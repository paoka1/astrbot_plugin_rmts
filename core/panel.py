"""WebUI 页面用的只读数据汇总。"""

from dataclasses import asdict
from datetime import date, datetime

from .diary import DailyLog, DiaryStore, is_proactive
from .history import History
from .interject import DecisionLog
from .memory import Fact, MemoryStore, total_length
from .reminder import ReminderStore, repeat_label

# 交互记录一次最多返回多少条，更早的要缩小时间范围再查
HISTORY_LIMIT = 500
# 插话判断一次最多返回多少条，每条带着当时的群聊，比交互记录大得多
DECISION_LIMIT = 200


def _facts_payload(facts: list[Fact], max_length: int) -> dict:
    length = total_length(facts)
    return {
        "length": length,
        "over_limit": length > max_length,
        "facts": [asdict(f) for f in facts],
    }


def _scope_label(scope: str, memory: MemoryStore) -> str:
    if not scope.startswith("private_"):
        return f"群 {scope}"
    qq = scope.removeprefix("private_")
    profile = memory.doctor(qq)
    return f"私聊 {profile.name}({qq})" if profile and profile.name else f"私聊 {qq}"


def build_overview(memory: MemoryStore, logs: DailyLog, diaries: DiaryStore) -> dict:
    doctors = [
        {
            "qq": qq,
            "name": p.name,
            "impression": p.impression,
            **_facts_payload(p.facts, memory.max_length),
        }
        for qq, p in sorted(memory.doctors().items())
    ]
    groups = [
        {"id": gid, **_facts_payload(facts, memory.max_length)}
        for gid, facts in sorted(memory.groups().items())
    ]
    scopes = []
    for scope in sorted(set(logs.scopes()) | set(diaries.scopes())):
        _, pending = logs.load(scope, 2**62)
        entries = diaries.entries(scope)
        scopes.append(
            {
                "scope": scope,
                "label": _scope_label(scope, memory),
                "pending": len(pending),
                "pending_to_her": sum(e.to_her for e in pending),
                "diaries": len(entries),
                "latest": entries[-1].date if entries else "",
            }
        )
    return {
        "max_length": memory.max_length,
        "doctors": doctors,
        "groups": groups,
        "scopes": scopes,
    }


def diary_payload(diaries: DiaryStore, scope: str) -> list[dict]:
    # 只认已存在的作用域，避免查询参数拼出数据目录外的路径
    if scope not in diaries.scopes():
        return []
    return [asdict(e) for e in reversed(diaries.entries(scope))]


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def history_payload(
    history: History, memory: MemoryStore, scope: str, start: str, end: str
) -> dict:
    chats = []
    for s in history.scopes():
        last = history.latest(s)
        chats.append(
            {
                "scope": s,
                "label": _scope_label(s, memory),
                "last_time": _time(last.ts) if last else "",
                "last_text": last.text if last else "",
            }
        )
    chats.sort(key=lambda c: c["last_time"], reverse=True)
    # 只认已存在的作用域，避免查询参数拼出数据目录外的路径
    known = [c["scope"] for c in chats]
    rows, truncated = history.query(
        [scope] if scope in known else [],
        _parse_day(start),
        _parse_day(end),
        HISTORY_LIMIT,
    )
    return {
        "scopes": chats,
        "entries": [
            {
                "time": _time(e.ts),
                "qq": e.qq,
                "name": e.name,
                "text": e.text,
                "is_self": e.is_self,
                "proactive": is_proactive(e),
            }
            for _, e in rows
        ],
        "truncated": truncated,
        "limit": HISTORY_LIMIT,
    }


def decisions_payload(
    log: DecisionLog, memory: MemoryStore, scope: str, start: str, end: str
) -> dict:
    scopes = [{"scope": s, "label": _scope_label(s, memory)} for s in log.scopes()]
    # 只认已存在的作用域，避免查询参数拼出数据目录外的路径
    if scope in {s["scope"] for s in scopes}:
        rows, truncated = log.query(
            scope, _parse_day(start), _parse_day(end), DECISION_LIMIT
        )
    else:
        rows, truncated = [], False
    return {
        "scopes": scopes,
        "decisions": [{**asdict(d), "time": _time(d.ts)} for d in rows],
        "truncated": truncated,
        "limit": DECISION_LIMIT,
    }


def reminders_payload(reminders: ReminderStore, memory: MemoryStore) -> list[dict]:
    return [
        {
            **asdict(r),
            "label": _scope_label(r.scope, memory),
            "repeat_label": repeat_label(r.repeat),
        }
        for r in reminders.all()
    ]
