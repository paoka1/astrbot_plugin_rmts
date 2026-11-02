import asyncio
import json

import pytest
from rmts.core.memory import (
    Fact,
    FactOps,
    MemoryStore,
    Profile,
    cap_facts,
    render_memory_block,
    source_of,
    visible_facts,
)

TODAY = "2026-10-31"


@pytest.mark.parametrize(
    ("scope", "is_self", "expected"),
    [
        ("private_42", True, [1, 2, 3, 4]),
        ("private_42", False, []),
        ("A", True, [2, 3]),
        ("A", False, [2]),
        ("C", False, []),
    ],
)
def test_visibility(scope, is_self, expected):
    profile = Profile(
        facts=[
            Fact(1, "私", "", ["private"]),
            Fact(2, "A群", "", ["group:A"]),
            Fact(3, "B群", "", ["group:B"]),
            Fact(4, "混", "", ["group:A", "private"]),
        ]
    )
    assert [
        f.id for f in visible_facts(profile, scope=scope, is_self=is_self)
    ] == expected
    assert source_of(scope) == (
        "private" if scope.startswith("private_") else f"group:{scope}"
    )


def test_persistence_ops_and_stable_ids(tmp_path):
    async def scenario():
        store = MemoryStore(tmp_path, max_length=5)
        assert await store.add_doctor_facts(
            "42", ["住广州", "爱吃甜"], scope="A", today=TODAY, name="小明"
        ) == [1, 2]
        await store.add_doctor_facts("42", ["爱喝奶茶"], scope="B", today=TODAY)
        assert len(store.doctor("42").facts) == 3  # 超限不丢弃
        await store.apply_doctor_ops(
            "42",
            FactOps(
                add=["下周考试"],
                update={1: "住上海", 99: "无效"},
                merge=[([2, 3], "爱吃甜、爱喝奶茶"), ([99], "无效")],
            ),
            scope="B",
            today="2026-11-01",
            impression="很温柔",
        )
        profile = MemoryStore(tmp_path).doctor("42")
        assert profile.name == "小明" and profile.impression == "很温柔"
        assert [(f.id, f.text) for f in profile.facts] == [
            (1, "住上海"),
            (4, "爱吃甜、爱喝奶茶"),
            (5, "下周考试"),
        ]
        # 更新后的内容只在 B 群说过；A、B 两群事实合并后没有共同来源，只有本人可用
        assert profile.facts[0].sources == ["group:B"]
        assert profile.facts[1].sources == []
        assert profile.facts[2].sources == ["group:B"]
        assert [f.text for f in visible_facts(profile, scope="A", is_self=False)] == []
        assert "爱吃甜、爱喝奶茶" in [
            f.text for f in visible_facts(profile, scope="A", is_self=True)
        ]
        assert all(f.date == "2026-11-01" for f in profile.facts)
        profile.facts.clear()
        assert len(store.doctor("42").facts) == 3  # 读取不能改动内部存储
        assert await store.forget_doctor_facts("42", [1, 99]) == [1]
        assert await store.add_doctor_facts(
            "42", ["新事实"], scope="B", today=TODAY
        ) == [6]

    asyncio.run(scenario())


def test_private_ops_protection_and_merge(tmp_path):
    async def scenario():
        store = MemoryStore(tmp_path)
        await store.add_doctor_facts("42", ["失恋了"], scope="private_42", today=TODAY)
        await store.add_doctor_facts("42", ["喜欢甜食"], scope="A", today=TODAY)
        await store.apply_doctor_ops(
            "42",
            FactOps(update={1: "x"}, delete=[1], merge=[([1, 2], "y")]),
            scope="A",
            today=TODAY,
        )
        assert [f.text for f in store.doctor("42").facts] == ["失恋了", "喜欢甜食"]
        await store.apply_doctor_ops(
            "42",
            FactOps(merge=[([1, 2], "私聊里的合并")]),
            scope="private_42",
            today=TODAY,
        )
        assert store.doctor("42").facts == [Fact(3, "私聊里的合并", TODAY, ["private"])]
        await store.add_doctor_facts("42", ["a1", "a2"], scope="A", today=TODAY)
        await store.add_doctor_facts("42", ["b1"], scope="B", today=TODAY)
        await store.apply_doctor_ops(
            "42", FactOps(merge=[([4, 5], "a12")]), scope="B", today=TODAY
        )
        assert store.doctor("42").facts[-1] == Fact(7, "a12", TODAY, ["group:A"])

    asyncio.run(scenario())


def test_group_ops(tmp_path):
    async def scenario():
        store = MemoryStore(tmp_path)
        await store.add_group_facts("A", ["a", "b", "c"], today=TODAY)
        await store.apply_group_ops(
            "A",
            FactOps(add=["d"], update={1: "A"}, delete=[3], merge=[([1, 2], "ab")]),
            today=TODAY,
        )
        assert store.group("A") == [Fact(4, "ab", TODAY, []), Fact(5, "d", TODAY, [])]
        assert store.group("B") == []

    asyncio.run(scenario())


def test_cap_facts_and_oversized(tmp_path):
    facts = [
        Fact(1, "生日五月一日", "", []),
        Fact(2, "住上海", "2026-10-31", []),
        Fact(3, "爱喝奶茶", "2026-10-01", []),
    ]
    # 超出上限时先舍弃日期最早（未知日期视为最早）的事实，其余保持原顺序
    assert [f.id for f in cap_facts(facts, 7)] == [2, 3]
    assert [f.id for f in cap_facts(facts, 100)] == [1, 2, 3]

    async def scenario():
        store = MemoryStore(tmp_path, max_length=4)
        await store.add_doctor_facts("42", ["一二三四五"], scope="A", today=TODAY)
        await store.add_doctor_facts("43", ["一二"], scope="A", today=TODAY)
        await store.add_group_facts("A", ["一二三四五"], today=TODAY)
        assert store.oversized() == ["博士 42", "群 A"]

    asyncio.run(scenario())


def test_migration_runs_once_and_empty_dir(tmp_path):
    assert MemoryStore(tmp_path).doctor("42") is None
    assert MemoryStore(tmp_path).group("123") == []
    for scope, data in {
        "123": {"42": {"memories": ["住广州"]}, "123": {"memories": ["是室友"]}},
        "private_42": {
            "42": {"memories": ["失恋了"]},
            "private_42": {"memories": ["怕黑"]},
        },
    }.items():
        (tmp_path / f"rosmontis_memory_{scope}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
    MemoryStore(tmp_path)
    store = MemoryStore(tmp_path)
    assert {f.text: f.sources for f in store.doctor("42").facts} == {
        "住广州": ["group:123"],
        "失恋了": ["private"],
        "怕黑": ["private"],
    }
    assert all(f.date == "" for f in store.doctor("42").facts)
    assert store.group("123")[0].text == "是室友"
    assert len(list(tmp_path.glob("*.json.bak"))) == 2
    assert not list(tmp_path.glob("rosmontis_memory_*.json"))


def test_render_memory_block():
    args = {
        "speaker_qq": "42",
        "speaker_name": "小明",
        "speaker": None,
        "speaker_facts": [],
        "mentioned": [],
        "group_facts": [],
        "sent": [],
        "diaries": [],
        "chat": [],
    }
    assert render_memory_block(**args) == ""
    # 有画像但没有可见的事实，也不单列一段
    assert render_memory_block(**{**args, "speaker": Profile(name="小明")}) == ""
    # 没有记事时只给群聊
    chat_only = render_memory_block(**{**args, "chat": ["14:20 小明(42)：在吗"]})
    assert chat_only == "【群里最近的聊天】（你被叫到之前）\n14:20 小明(42)：在吗"
    args.update(
        speaker=Profile(impression="很温柔"),
        speaker_facts=[Fact(1, "住广州", "", [])],
        mentioned=[("43", Profile(name="小红"), [])],
        group_facts=[Fact(1, "是室友", TODAY, [])],
        sent=[("2026-10-31 08:00", "早上好，今天晴")],
        diaries=[(TODAY, "今天很开心")],
        chat=["14:20 小红(43)：今天好累"],
    )
    block = render_memory_block(**args)
    assert "话里提到的博士" not in block
    assert block.endswith(
        "【群里最近的聊天】（你被叫到之前）\n14:20 小红(43)：今天好累"
    )
    assert all(
        text in block
        for text in [
            "迷迭香终端上的记事",
            "#1",
            "很久以前",
            "很温柔",
            "43",
            "是室友",
            TODAY,
            "今天很开心",
            "08:00：早上好，今天晴",
        ]
    )
