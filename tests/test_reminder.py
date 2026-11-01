from datetime import datetime

import pytest
from rmts.core.reminder import (
    MAX_PER_DOCTOR,
    ReminderError,
    ReminderStore,
    describe,
    first_time,
    next_time,
    normalize_repeat,
)

NOW = datetime(2026, 10, 31, 12, 0)  # 星期六


def add(store, at="2026-10-31 13:00", repeat="", qq="42", text="该喝水了", weather=""):
    return store.add(
        scope="123",
        umo="u",
        qq=qq,
        name="小明",
        at=at,
        repeat=repeat,
        text=text,
        weather=weather,
        compose=False,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", ""), ("none", ""), ("Daily", "daily"), ("2h", "2h"), ("2 h", "2h")],
)
def test_normalize_repeat(value, expected):
    assert normalize_repeat(value) == expected


@pytest.mark.parametrize("value", ["0h", "25h", "30m", "yearly"])
def test_normalize_repeat_rejects(value):
    with pytest.raises(ReminderError):
        normalize_repeat(value)


@pytest.mark.parametrize(
    ("at", "repeat", "expected"),
    [
        (datetime(2026, 10, 31, 9), "", None),
        (datetime(2026, 10, 31, 9), "daily", datetime(2026, 11, 1, 9)),
        (datetime(2026, 10, 30, 9), "weekdays", datetime(2026, 11, 2, 9)),
        (datetime(2026, 11, 1, 9), "weekends", datetime(2026, 11, 7, 9)),
        (datetime(2026, 10, 31, 9), "weekly", datetime(2026, 11, 7, 9)),
        (datetime(2026, 1, 31, 9), "monthly", datetime(2026, 2, 28, 9)),
        (datetime(2026, 12, 15, 9), "monthly", datetime(2027, 1, 15, 9)),
        (datetime(2026, 10, 31, 23), "2h", datetime(2026, 11, 1, 1)),
    ],
)
def test_next_time(at, repeat, expected):
    assert next_time(at, repeat) == expected


def test_first_time_moves_to_matching_day():
    assert first_time(datetime(2026, 10, 31, 9), "weekdays") == datetime(2026, 11, 2, 9)
    assert first_time(datetime(2026, 11, 2, 9), "weekends") == datetime(2026, 11, 7, 9)
    assert first_time(datetime(2026, 11, 2, 9), "daily") == datetime(2026, 11, 2, 9)


def test_add_validates(tmp_path):
    store = ReminderStore(tmp_path / "reminders.json")
    with pytest.raises(ReminderError, match="已经过去"):
        add(store, at="2026-10-31 11:00")
    with pytest.raises(ReminderError, match="格式"):
        add(store, at="明天九点")
    with pytest.raises(ReminderError, match="不能为空"):
        add(store, text="  ")
    for _ in range(MAX_PER_DOCTOR):
        add(store)
    with pytest.raises(ReminderError, match="已经有"):
        add(store)
    # 上限按人计算
    add(store, qq="43")


def test_weather_forces_compose_and_persists(tmp_path):
    path = tmp_path / "reminders.json"
    r = add(ReminderStore(path), repeat="daily", weather="西安市")
    assert r.compose is True and r.id == 1
    loaded = ReminderStore(path)
    assert loaded.all() == [r]
    assert add(loaded).id == 2
    assert "每天" in describe(r) and "西安市" in describe(r)


def test_cancel_only_own(tmp_path):
    store = ReminderStore(tmp_path / "reminders.json")
    mine = add(store)
    other = add(store, qq="43")
    assert store.cancel("123", "42", [mine.id, other.id]) == [mine.id]
    assert store.cancel("456", "43", [other.id]) == []
    assert [r.id for r in store.all()] == [other.id]


def test_pop_due(tmp_path):
    store = ReminderStore(tmp_path / "reminders.json")
    once = add(store, at="2026-10-31 13:00")
    daily = add(store, at="2026-10-31 13:00", repeat="daily")
    later = add(store, at="2026-10-31 18:00")

    assert store.pop_due(datetime(2026, 10, 31, 12, 59)) == ([], [])
    send, missed = store.pop_due(datetime(2026, 10, 31, 13, 0, 30))
    assert [r.id for r in send] == [once.id, daily.id] and missed == []
    assert [(r.id, r.at) for r in store.all()] == [
        (later.id, "2026-10-31 18:00"),
        (daily.id, "2026-11-01 13:00"),
    ]


def test_pop_due_skips_long_missed(tmp_path):
    store = ReminderStore(tmp_path / "reminders.json")
    once = add(store, at="2026-10-31 13:00")
    hourly = add(store, at="2026-10-31 13:00", repeat="1h")
    # 停机到 16:30 才恢复：都错过太久，不补发；重复提醒顺延到恢复之后
    send, missed = store.pop_due(datetime(2026, 10, 31, 16, 30))
    assert send == [] and [r.id for r in missed] == [once.id, hourly.id]
    assert [(r.id, r.at) for r in store.all()] == [(hourly.id, "2026-10-31 17:00")]


def test_follow_up_forces_compose(tmp_path):
    store = ReminderStore(tmp_path / "reminders.json")
    r = store.add(
        scope="123",
        umo="u",
        qq="42",
        name="小明",
        at="2026-11-02 18:00",
        repeat="",
        text="问问考试考得怎么样",
        weather="",
        compose=False,
        now=NOW,
        follow_up=True,
    )
    assert r.compose and r.follow_up and "你自己记挂的" in describe(r)
    assert ReminderStore(tmp_path / "reminders.json").all()[0].follow_up
