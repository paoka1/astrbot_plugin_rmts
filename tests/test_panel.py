import asyncio
from datetime import datetime

from rmts.core.diary import DailyLog, DiaryEntry, DiaryStore, LogEntry
from rmts.core.history import History
from rmts.core.interject import Decision, DecisionLog
from rmts.core.memory import MemoryStore
from rmts.core.panel import (
    build_overview,
    decisions_payload,
    diary_payload,
    history_payload,
    reminders_payload,
)
from rmts.core.reminder import ReminderStore

TODAY = "2026-10-31"


def test_build_overview(tmp_path):
    async def scenario():
        memory = MemoryStore(tmp_path, max_length=4)
        await memory.add_doctor_facts(
            "42", ["住上海"], scope="private_42", today=TODAY, name="小明"
        )
        await memory.add_doctor_facts("42", ["爱喝奶茶"], scope="123", today=TODAY)
        await memory.add_group_facts("123", ["大家在聊卡池"], today=TODAY)
        logs = DailyLog(tmp_path / "logs")
        logs.append("123", "u", LogEntry(1, "42", "小明", "迷迭香在吗", to_her=True))
        logs.append("123", "u", LogEntry(2, "43", "小红", "今天下雨"))
        diaries = DiaryStore(tmp_path / "diaries")
        diaries.put("private_42", DiaryEntry(TODAY, "和博士聊天", "聊天", ["博士"]))

        data = build_overview(memory, logs, diaries)
        assert data["max_length"] == 4
        doctor = data["doctors"][0]
        assert doctor["qq"] == "42" and doctor["name"] == "小明"
        assert doctor["length"] == 7 and doctor["over_limit"] is True
        assert [f["sources"] for f in doctor["facts"]] == [["private"], ["group:123"]]
        assert data["groups"] == [
            {
                "id": "123",
                "length": 6,
                "over_limit": True,
                "facts": [
                    {"id": 1, "text": "大家在聊卡池", "date": TODAY, "sources": []}
                ],
            }
        ]
        scopes = {s["scope"]: s for s in data["scopes"]}
        assert scopes["123"] == {
            "scope": "123",
            "label": "群 123",
            "pending": 2,
            "pending_to_her": 1,
            "diaries": 0,
            "latest": "",
        }
        assert scopes["private_42"]["label"] == "私聊 小明(42)"
        assert scopes["private_42"]["diaries"] == 1
        assert scopes["private_42"]["latest"] == TODAY

    asyncio.run(scenario())


def test_diary_payload_newest_first(tmp_path):
    diaries = DiaryStore(tmp_path)
    diaries.put("1", DiaryEntry("2026-10-01", "a", "sa", ["x"]))
    diaries.put("1", DiaryEntry("2026-10-02", "b", "sb", ["y"]))
    assert [d["date"] for d in diary_payload(diaries, "1")] == [
        "2026-10-02",
        "2026-10-01",
    ]
    assert diary_payload(diaries, "../etc") == []


def test_history_payload(tmp_path):
    memory = MemoryStore(tmp_path, max_length=100)
    history = History(tmp_path / "history")
    at = int(datetime(2026, 10, 31, 8).timestamp())
    history.append("123", LogEntry(at, "42", "小明", "在吗", to_her=True))
    history.append("private_42", LogEntry(at + 1, "", "迷迭香", "早", is_self=True))

    data = history_payload(history, memory, "", "", "")
    # 会话按最后一条消息的时间倒序；不指定作用域时不返回消息
    assert [(s["label"], s["last_text"]) for s in data["scopes"]] == [
        ("私聊 42", "早"),
        ("群 123", "在吗"),
    ]
    assert data["entries"] == []
    data = history_payload(history, memory, "private_42", "", "")
    assert [(m["text"], m["proactive"]) for m in data["entries"]] == [("早", True)]
    assert data["entries"][0]["time"] == "2026-10-31 08:00:01"
    assert [
        m["text"] for m in history_payload(history, memory, "123", "", "")["entries"]
    ] == ["在吗"]
    # 不认识的作用域、坏日期都不报错
    assert history_payload(history, memory, "../x", "", "")["entries"] == []
    assert (
        len(history_payload(history, memory, "123", "坏", "2026-10-31")["entries"]) == 1
    )
    assert history_payload(history, memory, "123", "2026-11-01", "")["entries"] == []


def test_decisions_payload(tmp_path):
    memory = MemoryStore(tmp_path, max_length=100)
    log = DecisionLog(tmp_path / "interjections")
    at = int(datetime(2026, 10, 31, 8).timestamp())
    log.append("123", Decision(at, 0.5, "不想说", score=0.1, reason="没听懂"))
    log.append("123", Decision(at + 60, 0.5, "发出", score=0.7, text="早"))

    data = decisions_payload(log, memory, "", "", "")
    assert data["scopes"] == [{"scope": "123", "label": "群 123"}]
    assert data["decisions"] == []
    rows = decisions_payload(log, memory, "123", "", "")["decisions"]
    # 新的在前，带格式化好的时间
    assert [(d["outcome"], d["time"]) for d in rows] == [
        ("发出", "2026-10-31 08:01:00"),
        ("不想说", "2026-10-31 08:00:00"),
    ]
    assert rows[1]["reason"] == "没听懂"
    assert decisions_payload(log, memory, "../x", "", "")["decisions"] == []
    assert decisions_payload(log, memory, "123", "2026-11-01", "")["decisions"] == []


def test_reminders_payload(tmp_path):
    memory = MemoryStore(tmp_path, max_length=100)
    store = ReminderStore(tmp_path / "reminders.json")
    store.add(
        scope="123",
        umo="u",
        qq="42",
        name="小明",
        at="2026-10-31 13:00",
        repeat="daily",
        text="喝水",
        weather="",
        compose=False,
        now=datetime(2026, 10, 31, 12),
    )
    [item] = reminders_payload(store, memory)
    assert item["label"] == "群 123" and item["repeat_label"] == "每天"
    assert item["text"] == "喝水" and item["at"] == "2026-10-31 13:00"
