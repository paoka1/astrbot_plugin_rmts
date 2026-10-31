import asyncio

from rmts.core.diary import DailyLog, DiaryEntry, DiaryStore, LogEntry
from rmts.core.memory import MemoryStore
from rmts.core.panel import build_overview, diary_payload

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
