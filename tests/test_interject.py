from datetime import datetime, timedelta

import pytest
from rmts.core.diary import DiaryEntry, DiaryStore, LogEntry
from rmts.core.interject import (
    DecisionLog,
    Interjector,
    Rule,
    build_prompt,
    decision_of,
    parse_reply,
    parse_slots,
)
from rmts.core.memory import MemoryStore

T0 = datetime(2026, 11, 1, 12, 0)


def minutes(n):
    return T0 + timedelta(minutes=n)


def at(hhmm):
    return datetime.strptime(f"2026-11-01 {hhmm}", "%Y-%m-%d %H:%M")


def test_wakes_after_enough_messages_and_time():
    it = Interjector(Rule(3, 10, 0.6), [], daily_limit=0)
    assert [it.on_message("g", minutes(0)) for _ in range(3)] == [False, False, True]
    # 条数够了但离上次判断不到 10 分钟
    assert [it.on_message("g", minutes(5)) for _ in range(3)] == [False] * 3
    assert it.on_message("g", minutes(11))
    # 各群分开计数
    assert not it.on_message("h", minutes(11))


def test_her_speaking_resets_and_blocks():
    it = Interjector(Rule(2, 10, 0.6), [], daily_limit=0)
    it.on_message("g", minutes(0))
    it.spoke("g", minutes(1))
    assert it.spoke_since("g", minutes(0)) and not it.spoke_since("g", minutes(2))
    # 说话后重新计数，且 10 分钟内不唤醒
    assert not it.on_message("g", minutes(2))
    assert not it.on_message("g", minutes(5))
    assert it.on_message("g", minutes(12))


def test_daily_limit_resets_next_day():
    it = Interjector(Rule(1, 0, 0.6), [], daily_limit=1)
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
    for silent in ["不说", " 不说。", "", "[回复1] 不说", "0.1\n不说"]:
        assert parse_reply(silent, entries).text == ""
    said = parse_reply("不说话的话，我就去晒太阳了。", entries)
    assert said.text.startswith("不说话") and not said.reply_to and not said.at
    assert said.score is None and not said.passes(0)

    said = parse_reply("0.75\n[回复1] 考试加油", entries)
    assert said.score == 0.75 and said.reply_to.msg_id == "m1"
    assert said.passes(0.6) and not said.passes(0.8)
    # 最后一行的理由单独拿出来，不混进要发的话
    said = parse_reply("0.3\n考试加油\n理由：小明在紧张", entries)
    assert said.text == "考试加油" and said.reason == "小明在紧张"
    said = parse_reply("0.1\n不说\n理由: 没听懂", entries)
    assert said.text == "" and said.reason == "没听懂"
    assert parse_reply("意愿：1\n好", entries).score == 1.0
    assert parse_reply(".3 好", entries).score == 0.3
    # 「10 点了」不是分数
    said = parse_reply("10 点了", entries)
    assert said.score is None and said.text == "10 点了"

    said = parse_reply("[回复1][@43] 考试加油", entries)
    assert said.text == "考试加油"
    assert said.reply_to.msg_id == "m1" and said.at.name == "小红"
    # 编号越界、引用她自己或没有消息 ID 的、@ 不在群聊里的人，都只丢掉标记
    assert parse_reply("[@小红] 你好", entries).at.qq == "43"
    for marker in ["[回复9]", "[回复2]", "[回复3]", "[@99]", "[@不在群里的人]"]:
        said = parse_reply(f"{marker} 你好", entries)
        assert said.text == "你好" and not said.reply_to and not said.at


def test_decision_of(tmp_path):
    entries = [LogEntry(i, "41", "旧消息", "早") for i in range(50)] + [
        LogEntry(100, "42", "小明", "明天考试", msg_id="m1"),
        LogEntry(101, "bot", "迷迭香", "加油", is_self=True),
    ]
    said = parse_reply("0.7\n[回复39][@42] 考试别紧张\n理由：小明要考试", entries)
    d = decision_of(said, entries, 0.5, "发出")
    # 编号和提示词里一样，只算最近 40 条
    assert len(d.context) == 40 and d.context[38]["text"] == "明天考试"
    assert d.reply_to == 39 and d.at == "小明(42)"
    assert (d.score, d.text, d.reason) == (0.7, "考试别紧张", "小明要考试")
    assert d.context[39]["is_self"]

    log = DecisionLog(tmp_path)
    log.append("123", d)
    rows, truncated = log.query("123", None, None, 10)
    assert rows == [d] and not truncated
    assert log.query("456", None, None, 10) == ([], False)


def test_slots():
    default = Rule(30, 30, 0.6)
    slots = parse_slots(
        [
            {"__template_key": "slot", "time": "23:00-08:00", "threshold": 1.1},
            {"time": "12:00 - 14:00", "min_messages": 10, "min_minutes": 5},
            {"time": "13:00~15:00", "threshold": 0.9},
            {"time": "  "},
        ],
        default,
    )
    it = Interjector(default, slots, daily_limit=0)
    # 跨午夜；重叠时用排在前面的；没填的数值用默认；不在任何时段里用默认
    assert [it.rule(at(t)).threshold for t in ["23:30", "02:00", "07:59", "08:00"]] == [
        1.1,
        1.1,
        1.1,
        0.6,
    ]
    assert it.rule(at("13:30")) == Rule(10, 5, 0.6)
    assert it.rule(at("14:30")) == Rule(30, 30, 0.9)
    assert it.rule(at("20:00")) == default
    assert parse_slots([], default) == [] and parse_slots(None, default) == []
    for bad in ["8:00", "25:00-26:00", "08:61-09:00", "早上"]:
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            parse_slots([{"time": bad}], default)


def test_slot_changes_wake_rule():
    # 12:00-13:00 这段只要 2 条、不限间隔；其余时段要 5 条
    it = Interjector(
        Rule(5, 30, 0.6),
        parse_slots(
            [{"time": "12:00-13:00", "min_messages": 2, "min_minutes": 0}],
            Rule(5, 30, 0.6),
        ),
        0,
    )
    assert [it.on_message("g", at("12:10")) for _ in range(2)] == [False, True]
    assert [it.on_message("g", at("12:20")) for _ in range(2)] == [False, True]
    # 出了这个时段按默认：要 5 条
    assert [it.on_message("g", at("13:10")) for _ in range(5)] == [False] * 4 + [True]
