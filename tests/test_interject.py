from datetime import datetime, timedelta

from rmts.core.diary import DiaryEntry, DiaryStore, LogEntry
from rmts.core.interject import Interjector, build_prompt, parse_reply
from rmts.core.memory import MemoryStore

T0 = datetime(2026, 11, 1, 12, 0)


def minutes(n):
    return T0 + timedelta(minutes=n)


def test_wakes_after_enough_messages_and_time():
    it = Interjector(min_messages=3, min_minutes=10, daily_limit=0)
    assert [it.on_message("g", minutes(0)) for _ in range(3)] == [False, False, True]
    # 条数够了但离上次判断不到 10 分钟
    assert [it.on_message("g", minutes(5)) for _ in range(3)] == [False] * 3
    assert it.on_message("g", minutes(11))
    # 各群分开计数
    assert not it.on_message("h", minutes(11))


def test_her_speaking_resets_and_blocks():
    it = Interjector(min_messages=2, min_minutes=10, daily_limit=0)
    it.on_message("g", minutes(0))
    it.spoke("g", minutes(1))
    assert it.spoke_since("g", minutes(0)) and not it.spoke_since("g", minutes(2))
    # 说话后重新计数，且 10 分钟内不唤醒
    assert not it.on_message("g", minutes(2))
    assert not it.on_message("g", minutes(5))
    assert it.on_message("g", minutes(12))


def test_daily_limit_resets_next_day():
    it = Interjector(min_messages=1, min_minutes=0, daily_limit=1)
    assert it.on_message("g", minutes(0))
    it.interjected("g", minutes(0))
    assert not it.on_message("g", minutes(30))
    assert it.on_message("g", T0 + timedelta(days=1))


def test_build_prompt_uses_visible_memory(tmp_path):
    import asyncio

    memory = MemoryStore(tmp_path, max_length=100)
    asyncio.run(
        memory.add_doctor_facts("42", ["在准备考试"], scope="123", today="2026-11-01")
    )
    asyncio.run(
        memory.add_doctor_facts(
            "42", ["私聊里的事"], scope="private_42", today="2026-11-01"
        )
    )
    asyncio.run(memory.add_group_facts("123", ["大家在玩卡池"], today="2026-11-01"))
    diaries = DiaryStore(tmp_path / "diaries")
    diaries.put("123", DiaryEntry("2026-10-31", "今天很热闹", "热闹", []))
    ts = int(T0.timestamp())
    entries = [
        LogEntry(ts, "42", "小明", "明天就考试了"),
        LogEntry(ts + 5, "bot", "迷迭香", "加油", is_self=True),
    ]
    prompt = build_prompt(memory, diaries, "123", entries, T0)
    for text in [
        "在准备考试",
        "大家在玩卡池",
        "今天很热闹",
        "小明(42)：明天就考试了",
        "你：加油",
    ]:
        assert text in prompt
    assert "私聊里的事" not in prompt


def test_parse_reply():
    entries = [
        LogEntry(1, "42", "小明", "明天考试", msg_id="m1"),
        LogEntry(2, "bot", "迷迭香", "加油", is_self=True),
        LogEntry(3, "43", "小红", "没有消息 ID"),
    ]
    for silent in ["不说", " 不说。", "", "[回复1] 不说"]:
        assert parse_reply(silent, entries) is None
    said = parse_reply("不说话的话，我就去晒太阳了。", entries)
    assert said.text.startswith("不说话") and not said.reply_to and not said.at

    said = parse_reply("[回复1][@43] 考试加油", entries)
    assert said.text == "考试加油"
    assert said.reply_to.msg_id == "m1" and said.at.name == "小红"
    # 编号越界、引用她自己或没有消息 ID 的、@ 不在群聊里的人，都只丢掉标记
    assert parse_reply("[@小红] 你好", entries).at.qq == "43"
    for marker in ["[回复9]", "[回复2]", "[回复3]", "[@99]", "[@不在群里的人]"]:
        said = parse_reply(f"{marker} 你好", entries)
        assert said.text == "你好" and not said.reply_to and not said.at
