from datetime import datetime

from rmts.core.diary import (
    DailyLog,
    DiaryEntry,
    DiaryStore,
    LogEntry,
    keep_text,
    message_text,
    recent_chat,
    sample_for_diary,
)


def test_log_merge_cutoff_and_clear(tmp_path):
    logs = DailyLog(tmp_path)
    logs.append("123", "umo:g", LogEntry(10, "42", "小明", "迷迭香在吗", msg_id="m1"))
    logs.append(
        "123",
        "umo:g",
        LogEntry(11, "42", "小明", "迷迭香在吗", to_her=True, msg_id="m1"),
    )
    logs.append("123", "umo:g", LogEntry(12, "bot", "迷迭香", "在的……", is_self=True))
    with (tmp_path / "123.jsonl").open("a", encoding="utf-8") as f:
        f.write("坏行\n")
    umo, entries = logs.load("123", 11)
    assert (
        umo == "umo:g"
        and len(entries) == 1
        and entries[0].to_her
        and entries[0].ts == 10
    )
    logs.clear("123", 11)
    assert logs.load("123", 100)[1][0].is_self
    logs.clear("123", 100)
    assert logs.scopes() == [] and logs.load("123", 100) == ("", [])


def test_proactive(tmp_path):
    logs = DailyLog(tmp_path)
    logs.append("1", "umo", LogEntry(10, "", "迷迭香", "八点的提醒", is_self=True))
    logs.append("1", "umo", LogEntry(11, "bot", "迷迭香", "普通回复", is_self=True))
    logs.append("1", "umo", LogEntry(12, "42", "小明", "谢谢"))
    assert [e.text for e in logs.proactive("1", 0)] == ["八点的提醒"]
    # 读过一次后新追加的也能拿到，早于起点的不给
    logs.append("1", "umo", LogEntry(20, "", "迷迭香", "新提醒", is_self=True))
    assert [e.text for e in logs.proactive("1", 15)] == ["新提醒"]
    assert [e.text for e in DailyLog(tmp_path).proactive("1", 0)] == [
        "八点的提醒",
        "新提醒",
    ]
    logs.clear("1", 15)
    assert [e.text for e in logs.proactive("1", 0)] == ["新提醒"]
    assert logs.proactive("2", 0) == []


def test_log_cap(tmp_path):
    logs = DailyLog(tmp_path, max_entries=3)
    for ts in range(1, 6):
        logs.append("1", "umo", LogEntry(ts, "42", "a", "内容"))
    assert [e.ts for e in logs.load("1", 4)[1]] == [2, 3, 4]
    for ts in range(6, 30):
        logs.append("1", "umo", LogEntry(ts, "42", "a", "内容"))
    assert len((tmp_path / "1.jsonl").read_text(encoding="utf-8").splitlines()) <= 6


def test_message_text():
    me = "999"
    # 开头叫她的 @ 去掉，句中 @ 她写成「@你」，@ 别人保留名字
    parts = [("888", "小鲸鱼"), ("", "去和"), (me, "迷迭香"), ("", "交个朋友")]
    assert message_text(parts, me) == "@小鲸鱼(888) 去和 @你 交个朋友"
    assert message_text([(me, "迷迭香"), (me, "迷迭香"), ("", " 在吗 ")], me) == "在吗"
    assert message_text([("", "问问"), ("777", "")], me) == "问问 @777"
    assert message_text([(me, "迷迭香")], me) == ""


def test_recent_chat():
    t = int(datetime(2026, 11, 2, 14, 20).timestamp())
    entries = [
        LogEntry(t - 7200, "10001", "小明", "太早了"),
        LogEntry(t, "10001", "小明", "有人打危机合约吗"),
        LogEntry(t + 60, "bot", "迷迭香", "合约……是什么", is_self=True),
        LogEntry(t + 120, "", "迷迭香", "八点的提醒", is_self=True),
        LogEntry(t + 180, "20002", "阿蓝", "第三天好难"),
        LogEntry(t + 360, "30003", "小红", "你会打吗", to_her=True, msg_id="m9"),
    ]
    assert recent_chat(entries, since_ts=t, skip_msg_id="m9", limit=30) == [
        "14:20 小明(10001)：有人打危机合约吗",
        "14:21 你：合约……是什么",
        "14:22 你：八点的提醒",
        "14:23 阿蓝(20002)：第三天好难",
    ]
    assert recent_chat(entries, since_ts=t, skip_msg_id="m9", limit=1) == [
        "14:23 阿蓝(20002)：第三天好难"
    ]
    # 没有消息 ID 时不跳过任何一条
    assert len(recent_chat(entries, since_ts=0, skip_msg_id="", limit=30)) == 6
    assert recent_chat(entries, since_ts=0, skip_msg_id="m9", limit=0) == []


def test_keep_text_and_sample():
    assert not keep_text("  ", True) and not keep_text("1", False)
    assert keep_text("1", True) and keep_text("哈哈", False)
    her = [LogEntry(i * 100, "42", "a", "对她说" * 5, to_her=True) for i in range(3)]
    others = [LogEntry(i, "43", "b", "群聊消息内容") for i in range(1000)]
    out = sample_for_diary(her + others, 2000)
    assert all(e in out for e in her)
    assert sum(len(e.text) for e in out) <= 2000
    kept = [e.ts for e in out if not e.to_her]
    assert kept[0] < 100 and kept[-1] > 900
    assert [e.ts for e in out] == sorted(e.ts for e in out)
    assert sample_for_diary(her, 1000) == her
    assert sample_for_diary(her, 20) == [her[-1]]
    assert sample_for_diary(her, 0) == []


def test_diary_storage_and_search(tmp_path):
    store = DiaryStore(tmp_path)
    store.put(
        "1", DiaryEntry("2026-10-09", "小明说考试很累", "小明考试", ["小明", "考试"])
    )
    store.put(
        "1",
        DiaryEntry("2026-10-01", "小明抽卡出了新干员", "小明抽卡", ["小明", "抽卡"]),
    )
    store.put("1", DiaryEntry("2026-10-05", "今天下雨", "下雨", ["天气"]))
    assert [d.date for d in store.latest("1", 2)] == ["2026-10-05", "2026-10-09"]
    assert [h.entry.date for h in store.search("1", ["小明", "抽卡"])] == [
        "2026-10-01",
        "2026-10-09",
    ]
    assert "抽卡" in store.search("1", ["抽卡"])[0].snippet
    assert store.search("1", ["小明"], start="2026-10-05")[0].entry.date == "2026-10-09"
    assert store.search("1", ["小明"], end="2026-10-05")[0].entry.date == "2026-10-01"
    assert store.search("1", ["不存在", " "]) == store.search("2", ["小明"]) == []
    store.put("1", DiaryEntry("2026-10-05", "DeepSeek 聊天", "模型", ["AI"]))
    assert DiaryStore(tmp_path).get("1", "2026-10-05").text == "DeepSeek 聊天"
    assert store.get("2", "2026-10-05") is None and store.latest("1", 0) == []
    assert len(store.search("1", ["DEEPSEEK", "deepseek", "AI"], limit=1)) == 1
    assert store.scopes() == ["1"]
    assert [d.date for d in store.entries("1")] == [
        "2026-10-01",
        "2026-10-05",
        "2026-10-09",
    ]
