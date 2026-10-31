"""跟随博士的画像、本群记录，以及旧记忆迁移。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Fact:
    id: int
    text: str
    date: str
    sources: list[str]


@dataclass
class Profile:
    name: str = ""
    impression: str = ""
    facts: list[Fact] = field(default_factory=list)
    next_id: int = 1


@dataclass
class FactOps:
    add: list[str] = field(default_factory=list)
    update: dict[int, str] = field(default_factory=dict)
    delete: list[int] = field(default_factory=list)
    merge: list[tuple[list[int], str]] = field(default_factory=list)


def source_of(scope: str) -> str:
    return "private" if scope.startswith("private_") else f"group:{scope}"


def is_private_fact(fact: Fact) -> bool:
    return "private" in fact.sources


def total_length(facts: list[Fact]) -> int:
    return sum(len(f.text) for f in facts)


def cap_facts(facts: list[Fact], limit: int) -> list[Fact]:
    """注入时的兜底：夜间整理没把字数压下来时，先舍弃日期最早的事实（未知日期最早）"""
    dropped = set()
    total = total_length(facts)
    for fact in sorted(facts, key=lambda f: (f.date, f.id)):
        if total <= limit:
            break
        dropped.add(fact.id)
        total -= len(fact.text)
    return [f for f in facts if f.id not in dropped]


def visible_facts(profile: Profile, *, scope: str, is_self: bool) -> list[Fact]:
    if scope.startswith("private_"):
        return deepcopy(profile.facts) if is_self else []
    return [
        deepcopy(f)
        for f in profile.facts
        if not is_private_fact(f) and (is_self or source_of(scope) in f.sources)
    ]


class MemoryStore:
    def __init__(self, data_dir: Path, max_length: int = 500) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.max_length = max_length
        self._lock = asyncio.Lock()
        self._path = data_dir / "memory.json"
        self._doctors: dict[str, Profile] = {}
        self._groups: dict[str, Profile] = {}
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for key, target in (("doctors", self._doctors), ("groups", self._groups)):
                for owner, raw in data.get(key, {}).items():
                    target[owner] = Profile(
                        name=raw.get("name", ""),
                        impression=raw.get("impression", ""),
                        next_id=raw["next_id"],
                        facts=[Fact(**f) for f in raw["facts"]],
                    )
        self._migrate_legacy()

    def _save(self) -> None:
        data = {
            "doctors": {qq: asdict(p) for qq, p in self._doctors.items()},
            "groups": {
                gid: {"next_id": p.next_id, "facts": [asdict(f) for f in p.facts]}
                for gid, p in self._groups.items()
            },
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _add(
        profile: Profile, texts: list[str], today: str, sources: list[str]
    ) -> list[int]:
        ids = []
        for text in texts:
            profile.facts.append(Fact(profile.next_id, text, today, list(sources)))
            ids.append(profile.next_id)
            profile.next_id += 1
        return ids

    def _migrate_legacy(self) -> None:
        for path in sorted(self.data_dir.glob("rosmontis_memory_*.json")):
            scope = path.stem.removeprefix("rosmontis_memory_")
            data = json.loads(path.read_text(encoding="utf-8"))
            for owner, raw in data.items():
                if scope.startswith("private_"):
                    profile = self._doctors.setdefault(
                        scope.removeprefix("private_"), Profile()
                    )
                    sources = ["private"]
                elif owner == scope:
                    profile = self._groups.setdefault(scope, Profile())
                    sources = []
                else:
                    profile = self._doctors.setdefault(owner, Profile())
                    sources = [source_of(scope)]
                self._add(profile, raw.get("memories", []), "", sources)
            self._save()
            path.rename(path.with_suffix(".json.bak"))

    def doctor(self, qq: str) -> Profile | None:
        return deepcopy(self._doctors.get(qq))

    def group(self, group_id: str) -> list[Fact]:
        profile = self._groups.get(group_id)
        return deepcopy(profile.facts) if profile else []

    def doctors(self) -> dict[str, Profile]:
        return deepcopy(self._doctors)

    def groups(self) -> dict[str, list[Fact]]:
        return {gid: deepcopy(p.facts) for gid, p in self._groups.items()}

    def oversized(self) -> list[str]:
        """字数超过上限的画像 / 群信息，供夜间整理后告警"""
        return [
            f"{kind} {key}"
            for kind, target in (("博士", self._doctors), ("群", self._groups))
            for key, profile in target.items()
            if total_length(profile.facts) > self.max_length
        ]

    async def add_doctor_facts(
        self, qq: str, texts: list[str], *, scope: str, today: str, name: str = ""
    ) -> list[int]:
        async with self._lock:
            profile = self._doctors.setdefault(qq, Profile())
            if name:
                profile.name = name
            ids = self._add(profile, texts, today, [source_of(scope)])
            self._save()
            return ids

    async def add_group_facts(
        self, group_id: str, texts: list[str], *, today: str
    ) -> list[int]:
        async with self._lock:
            profile = self._groups.setdefault(group_id, Profile())
            ids = self._add(profile, texts, today, [])
            self._save()
            return ids

    async def forget_doctor_facts(self, qq: str, ids: list[int]) -> list[int]:
        async with self._lock:
            profile = self._doctors.get(qq)
            if profile is None:
                return []
            removed = [f.id for f in profile.facts if f.id in ids]
            profile.facts = [f for f in profile.facts if f.id not in ids]
            self._save()
            return removed

    def _apply(
        self, profile: Profile, ops: FactOps, *, source: str, today: str
    ) -> None:
        # 只操作整理前已有的编号；群整理不能触碰任何私聊事实。
        allowed = {
            f.id: f
            for f in profile.facts
            if source == "private" or not is_private_fact(f)
        }
        sources = [source] if source else []
        for fact_id, text in ops.update.items():
            if fact_id in allowed:
                fact = allowed[fact_id]
                # 新内容只在当前作用域说过，来源换成当前作用域，不能扩散到旧来源的群
                fact.text, fact.date, fact.sources = text, today, list(sources)
        removed = set(ops.delete) & allowed.keys()
        for ids, text in ops.merge:
            if not ids or any(i not in allowed or i in removed for i in ids):
                continue
            # 合并后的内容混合了各条事实：含私聊就是私聊；否则只在各条都可见的群里可见
            groups = [set(allowed[i].sources) for i in ids]
            if source == "private" or any("private" in g for g in groups):
                merged_sources = ["private"]
            else:
                merged_sources = sorted(set.intersection(*groups))
            self._add(profile, [text], today, merged_sources)
            removed.update(ids)
        profile.facts = [f for f in profile.facts if f.id not in removed]
        self._add(profile, ops.add, today, sources)

    async def apply_doctor_ops(
        self,
        qq: str,
        ops: FactOps,
        *,
        scope: str,
        today: str,
        impression: str | None = None,
        name: str = "",
    ) -> None:
        async with self._lock:
            profile = self._doctors.setdefault(qq, Profile())
            self._apply(profile, ops, source=source_of(scope), today=today)
            if impression:
                profile.impression = impression
            if name:
                profile.name = name
            self._save()

    async def apply_group_ops(self, group_id: str, ops: FactOps, *, today: str) -> None:
        async with self._lock:
            profile = self._groups.setdefault(group_id, Profile())
            self._apply(profile, ops, source="", today=today)
            self._save()


def render_memory_block(
    *,
    speaker_qq: str,
    speaker_name: str,
    speaker: Profile | None,
    speaker_facts: list[Fact],
    mentioned: list[tuple[str, Profile, list[Fact]]],
    group_facts: list[Fact],
    diaries: list[tuple[str, str]],
) -> str:
    if speaker is None and not (speaker_facts or mentioned or group_facts or diaries):
        return ""

    def lines_for(profile: Profile | None, facts: list[Fact]) -> list[str]:
        lines = (
            [f"印象：{profile.impression}"] if profile and profile.impression else []
        )
        lines.extend(f"#{f.id} {f.text}（{f.date or '很久以前'}）" for f in facts)
        return lines or ["（还没有记录）"]

    lines = [
        "【迷迭香终端里的记录】",
        f"正在说话的博士 {speaker_name}({speaker_qq})：",
        *lines_for(speaker, speaker_facts),
    ]
    for qq, profile, facts in mentioned:
        lines.extend(
            [f"话里提到的博士 {profile.name}({qq})：", *lines_for(profile, facts)]
        )
    if group_facts:
        lines.extend(["本群记录：", *lines_for(None, group_facts)])
    if diaries:
        lines.extend(["最近的日记：", *(f"{day}：{text}" for day, text in diaries)])
    return "\n".join(lines)
