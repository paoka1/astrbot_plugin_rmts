import asyncio
import json
from datetime import datetime

import pytest
from rmts.core.diary import DailyLog, DiaryEntry, DiaryStore, LogEntry
from rmts.core.memory import MemoryStore
from rmts.core.nightly import (
    diary_date_for,
    next_run,
    parse_json_reply,
    parse_ops,
    process_scope,
    run_nightly,
)

RUN_AT = datetime(2026, 11, 1, 4)
CUTOFF = int(RUN_AT.timestamp())
DIARY = json.dumps(
    {"text": "今天博士和我聊天了。", "summary": "和博士聊天", "tags": ["小明"]}
)


def test_schedule_and_json_parsing():
    assert next_run(datetime(2026, 10, 31, 3), "04:00") == datetime(2026, 10, 31, 4)
    assert next_run(datetime(2026, 10, 31, 4), "04:00") == RUN_AT
    assert diary_date_for(RUN_AT) == "2026-10-31"
    assert diary_date_for(datetime(2026, 10, 31, 23, 30)) == "2026-10-31"
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    for bad in ("不是 JSON", "[]"):
        with pytest.raises(ValueError):
            parse_json_reply(bad)


def test_parse_ops_tolerates_garbage():
    ops = parse_ops(
        {
            "add": ["x", 3, ""],
            "update": [{"id": "2", "text": "y"}, {"id": "a"}, None],
            "delete": [1, "z", False, 1.5],
            "merge": [
                {"ids": [1, 2], "text": "m"},
                {"ids": "bad"},
                {"ids": [1, "bad"], "text": "bad"},
            ],
        }
    )
    assert (
        ops.add == ["x"]
        and ops.update == {2: "y"}
        and ops.delete == [1]
        and ops.merge == [([1, 2], "m")]
    )
    assert parse_ops(
        {"add": None, "update": 3, "delete": "bad", "merge": None}
    ) == parse_ops(None)


def test_process_scope_filters_inputs_and_ops_and_keeps_new_messages(tmp_path):
    async def scenario():
        memory = MemoryStore(tmp_path)
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        await memory.add_doctor_facts("42", ["住广州"], scope="A", today="2026-10-01")
        await memory.add_doctor_facts(
            "42", ["失恋了"], scope="private_42", today="2026-10-01"
        )
        logs.append(
            "123",
            "umo",
            LogEntry(CUTOFF - 100, "42", "小明", "我搬到上海了", to_her=True),
        )
        logs.append(
            "123",
            "umo",
            LogEntry(CUTOFF - 90, "bot", "迷迭香", "我记下了", is_self=True),
        )
        logs.append("123", "umo", LogEntry(CUTOFF - 80, "99", "小红", "小明住在北京"))
        calls = []

        async def llm(system, prompt):
            calls.append((system, prompt))
            if len(calls) == 1:
                logs.append("123", "umo", LogEntry(CUTOFF + 1, "42", "小明", "新消息"))
                return DIARY
            return json.dumps(
                {
                    "doctors": {
                        "42": {
                            "update": [
                                {"id": 1, "text": "住上海"},
                                {"id": 2, "text": "泄漏"},
                            ],
                            "delete": [2],
                            "impression": "很温柔",
                        },
                        "99": {"add": ["不该新增"]},
                    },
                    "group": {"add": ["大家聊卡池"]},
                }
            )

        await process_scope(
            "123",
            memory=memory,
            logs=logs,
            diaries=diaries,
            llm=llm,
            persona_prompt="测试人格",
            run_at=RUN_AT,
            budget=20000,
        )
        assert "测试人格" in calls[0][0] and "测试人格" not in calls[1][0]
        assert "小明住在北京" in calls[0][1] and "小明住在北京" not in calls[1][1]
        assert "失恋了" not in calls[1][1]
        profile = memory.doctor("42")
        assert profile.facts[0].text == "住上海" and profile.facts[0].sources == [
            "group:123"
        ]
        assert profile.facts[1].text == "失恋了" and memory.doctor("99") is None
        assert profile.name == "小明" and profile.impression == "很温柔"
        assert memory.group("123")[0].text == "大家聊卡池"
        assert diaries.get("123", "2026-10-31") is not None
        assert [e.text for e in logs.load("123", CUTOFF + 100)[1]] == ["新消息"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_step", [1, 2])
def test_bad_json_keeps_log(tmp_path, failure_step):
    async def scenario():
        memory = MemoryStore(tmp_path)
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        logs.append(
            "123", "umo", LogEntry(CUTOFF - 1, "42", "a", "我住上海", to_her=True)
        )
        calls = 0

        async def llm(system, prompt):
            nonlocal calls
            calls += 1
            return "坏 JSON" if calls == failure_step else DIARY

        with pytest.raises(ValueError):
            await process_scope(
                "123",
                memory=memory,
                logs=logs,
                diaries=diaries,
                llm=llm,
                persona_prompt="人格",
                run_at=RUN_AT,
                budget=20000,
            )
        assert len(logs.load("123", CUTOFF)[1]) == 1
        assert memory.doctor("42") is None
        assert bool(diaries.latest("123", 1)) == (failure_step == 2)

    asyncio.run(scenario())


def test_run_nightly_isolates_failures_and_skips_memory_without_to_her(tmp_path):
    async def scenario():
        memory = MemoryStore(tmp_path)
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        for scope in ("bad", "good"):
            logs.append(scope, scope, LogEntry(CUTOFF - 1, "42", "a", "普通群聊"))
        calls = []

        async def make_llm(umo):
            async def llm(system, prompt):
                calls.append(umo)
                if umo == "bad":
                    raise RuntimeError("模型不可用")
                return DIARY

            return llm

        async def persona_for(umo):
            return "人格"

        failures = await run_nightly(
            memory=memory,
            logs=logs,
            diaries=diaries,
            make_llm=make_llm,
            persona_for=persona_for,
            run_at=RUN_AT,
            budget=20000,
        )
        assert failures == {"bad": "模型不可用"} and calls == ["bad", "good"]
        assert logs.scopes() == ["bad"] and diaries.latest("good", 1)
        assert memory.doctor("42") is None

    asyncio.run(scenario())


def test_same_day_rerun_continues_diary(tmp_path):
    # 白天手动整理过一次，夜里再整理同一天时续写，不覆盖已写的部分
    async def scenario():
        memory = MemoryStore(tmp_path)
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        diaries.put("1", DiaryEntry("2026-10-30", "前天的日记", "前天", ["旧"]))
        diaries.put("1", DiaryEntry("2026-10-31", "下午和博士聊天", "下午", ["博士"]))
        logs.append("1", "umo", LogEntry(CUTOFF - 1, "42", "a", "晚上大家在聊卡池"))
        prompts = []

        async def llm(system, prompt):
            prompts.append(prompt)
            return DIARY

        await process_scope(
            "1",
            memory=memory,
            logs=logs,
            diaries=diaries,
            llm=llm,
            persona_prompt="人格",
            run_at=RUN_AT,
            budget=20000,
        )
        assert "下午和博士聊天" in prompts[0] and "前天的日记" in prompts[0]
        assert [d.date for d in diaries.entries("1")] == ["2026-10-30", "2026-10-31"]

    asyncio.run(scenario())


def test_cancel_keeps_log_and_completed_diary(tmp_path):
    async def scenario():
        memory = MemoryStore(tmp_path)
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        logs.append(
            "private_42",
            "umo",
            LogEntry(CUTOFF - 1, "42", "a", "我住上海", to_her=True),
        )
        started = asyncio.Event()

        async def llm(system, prompt):
            if "人格" in system:
                return DIARY
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(
            process_scope(
                "private_42",
                memory=memory,
                logs=logs,
                diaries=diaries,
                llm=llm,
                persona_prompt="人格",
                run_at=RUN_AT,
                budget=20000,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert logs.load("private_42", CUTOFF)[1] and diaries.latest("private_42", 1)
        assert not list(tmp_path.rglob("*.tmp"))

    asyncio.run(scenario())


def test_run_nightly_skips_scopes_not_allowed(tmp_path):
    async def scenario():
        logs, diaries = DailyLog(tmp_path / "logs"), DiaryStore(tmp_path / "diaries")
        for scope in ("in", "out"):
            logs.append(scope, scope, LogEntry(CUTOFF - 1, "42", "a", "普通群聊"))

        async def make_llm(umo):
            async def llm(system, prompt):
                return DIARY

            return llm

        async def persona_for(umo):
            return "人格"

        failures = await run_nightly(
            memory=MemoryStore(tmp_path),
            logs=logs,
            diaries=diaries,
            make_llm=make_llm,
            persona_for=persona_for,
            run_at=RUN_AT,
            budget=20000,
            allowed=lambda scope, umo: scope == "in",
        )
        assert failures == {} and logs.scopes() == ["out"]
        assert diaries.latest("in", 1) and not diaries.latest("out", 1)

    asyncio.run(scenario())
