from datetime import date, datetime

from rmts.core.diary import LogEntry
from rmts.core.history import History


def ts(day, hour=12):
    return int(datetime(2026, 10, day, hour).timestamp())


def test_append_only_interactions_and_query(tmp_path):
    history = History(tmp_path)
    history.append("123", LogEntry(ts(30), "42", "小明", "迷迭香在吗", to_her=True))
    history.append("123", LogEntry(ts(30, 13), "43", "小红", "今天下雨"))
    history.append("123", LogEntry(ts(31), "bot", "迷迭香", "在的", is_self=True))
    history.append("private_42", LogEntry(ts(31, 8), "", "迷迭香", "早", is_self=True))
    (tmp_path / "123" / "2026-10.jsonl").open("a", encoding="utf-8").write("坏行\n")
    assert history.scopes() == ["123", "private_42"]

    rows, truncated = history.query(history.scopes(), None, None, 10)
    assert [e.text for _, e in rows] == ["在的", "早", "迷迭香在吗"] and not truncated
    rows, _ = history.query(["123"], date(2026, 10, 31), date(2026, 10, 31), 10)
    assert [e.text for _, e in rows] == ["在的"]
    rows, truncated = history.query(history.scopes(), None, None, 1)
    assert [s for s, _ in rows] == ["123"] and truncated


def test_files_split_by_month(tmp_path):
    history = History(tmp_path)
    history.append("1", LogEntry(ts(31), "42", "a", "十月", to_her=True))
    history.append(
        "1",
        LogEntry(
            int(datetime(2026, 11, 1).timestamp()), "42", "a", "十一月", to_her=True
        ),
    )
    assert sorted(p.name for p in (tmp_path / "1").iterdir()) == [
        "2026-10.jsonl",
        "2026-11.jsonl",
    ]
    rows, _ = history.query(["1"], date(2026, 11, 1), None, 10)
    assert [e.text for _, e in rows] == ["十一月"]
