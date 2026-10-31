"""WebUI 页面用的只读数据汇总。"""

from dataclasses import asdict

from .diary import DailyLog, DiaryStore
from .memory import Fact, MemoryStore, total_length


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
