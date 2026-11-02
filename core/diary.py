"""当天文本记录、均匀抽样，以及按作用域存储和检索的日记。"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

MIN_TEXT_LEN = 2


def keep_text(text: str, to_her: bool) -> bool:
    text = text.strip()
    return bool(text) and (to_her or len(text) >= MIN_TEXT_LEN)


def message_text(parts: list[tuple[str, str]], self_id: str) -> str:
    """把消息里的文字和 @ 按原顺序拼成一句话。

    parts 为 (QQ, 文字)：文字段 QQ 为空；@ 段的文字是对方的名字，可能为空。
    @ 别人写成「@名字(QQ号)」，她用工具时要用到 QQ 号；开头用来叫她的 @ 去掉，
    句中 @ 她的写成「@你」，免得听成在叫别人。
    """
    words = []
    for qq, text in parts:
        if not qq:
            words.append(text.strip())
        elif qq == self_id:
            if any(words):
                words.append("@你")
        else:
            words.append(f"@{text}({qq})" if text else f"@{qq}")
    return " ".join(w for w in words if w)


@dataclass
class LogEntry:
    ts: int
    qq: str
    name: str
    text: str
    to_her: bool = False
    is_self: bool = False
    msg_id: str = ""


def _entry(row: dict) -> LogEntry:
    return LogEntry(**{k: v for k, v in row.items() if k != "umo"})


def is_proactive(entry: LogEntry) -> bool:
    # 插件主动发出的消息（如到点提醒）不带 QQ 号，以此和普通回复区分
    return entry.is_self and not entry.qq


class DailyLog:
    def __init__(self, log_dir: Path, max_entries: int = 5000) -> None:
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._counts: dict[str, int] = {}
        self._proactive: dict[str, list[LogEntry]] = {}

    def _path(self, scope: str) -> Path:
        return self.log_dir / f"{scope}.jsonl"

    def _rows(self, scope: str) -> list[dict]:
        path = self._path(scope)
        if not path.exists():
            return []
        rows = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _rewrite(self, scope: str, rows: list[dict]) -> None:
        path = self._path(scope)
        if rows:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
        self._counts[scope] = len(rows)
        self._proactive.pop(scope, None)

    def append(self, scope: str, umo: str, entry: LogEntry) -> None:
        if scope not in self._counts:
            self._counts[scope] = len(self._rows(scope))
        with self._path(scope).open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"umo": umo, **asdict(entry)}, ensure_ascii=False) + "\n"
            )
        self._counts[scope] += 1
        if scope in self._proactive and is_proactive(entry):
            self._proactive[scope].append(entry)
        # 通常只追加一行；连续整理失败时，定期收紧文件大小，避免无限堆积。
        if self._counts[scope] > self.max_entries * 2:
            self._rewrite(scope, self._rows(scope)[-self.max_entries :])

    def scopes(self) -> list[str]:
        return sorted(p.stem for p in self.log_dir.glob("*.jsonl"))

    def load(self, scope: str, until_ts: int) -> tuple[str, list[LogEntry]]:
        entries = []
        by_id: dict[str, LogEntry] = {}
        umo = ""
        for row in self._rows(scope):
            if row["ts"] > until_ts:
                continue
            umo = row["umo"]
            entry = _entry(row)
            if entry.msg_id and entry.msg_id in by_id:
                by_id[entry.msg_id].to_her |= entry.to_her
            else:
                entries.append(entry)
                if entry.msg_id:
                    by_id[entry.msg_id] = entry
        entries.sort(key=lambda e: e.ts)
        return umo, entries[-self.max_entries :]

    def proactive(self, scope: str, since_ts: int, limit: int = 3) -> list[LogEntry]:
        """她在这里主动发出的消息。这些不经过对话历史，要单独告诉模型。"""
        if scope not in self._proactive:
            self._proactive[scope] = [
                e for e in map(_entry, self._rows(scope)) if is_proactive(e)
            ]
        return [e for e in self._proactive[scope] if e.ts >= since_ts][-limit:]

    def clear(self, scope: str, until_ts: int) -> None:
        self._rewrite(scope, [row for row in self._rows(scope) if row["ts"] > until_ts])


def recent_chat(
    entries: list[LogEntry], *, since_ts: int, skip_msg_id: str, limit: int
) -> list[str]:
    """群里最近的聊天，她被叫到时附给她看。跳过叫她的这条，她自己说的标成「你」。"""
    if limit <= 0:
        return []
    picked = [
        e
        for e in entries
        if e.ts >= since_ts and not (skip_msg_id and e.msg_id == skip_msg_id)
    ][-limit:]
    lines = []
    for e in picked:
        who = "你" if e.is_self else f"{e.name}({e.qq})"
        lines.append(f"{datetime.fromtimestamp(e.ts):%H:%M} {who}：{e.text}")
    return lines


def sample_for_diary(entries: list[LogEntry], budget: int) -> list[LogEntry]:
    entries = sorted(entries, key=lambda e: e.ts)
    if budget <= 0:
        return []
    if sum(len(e.text) for e in entries) <= budget:
        return entries
    her = [e for e in entries if e.to_her or e.is_self]
    remaining = budget - sum(len(e.text) for e in her)
    if remaining < 0:
        kept = []
        remaining = budget
        for entry in reversed(her):
            if len(entry.text) <= remaining:
                kept.append(entry)
                remaining -= len(entry.text)
        return list(reversed(kept))
    others = [e for e in entries if not (e.to_her or e.is_self)]
    if not others or remaining <= 0:
        return her
    average = sum(len(e.text) for e in others) / len(others)
    count = min(len(others), int(remaining / average))
    while count > 0:
        indices = (
            [len(others) // 2]
            if count == 1
            else [round(i * (len(others) - 1) / (count - 1)) for i in range(count)]
        )
        sampled = [others[i] for i in indices]
        if sum(len(e.text) for e in sampled) <= remaining:
            return sorted(her + sampled, key=lambda e: e.ts)
        count -= 1
    return her


@dataclass
class DiaryEntry:
    date: str
    text: str
    summary: str
    tags: list[str]


@dataclass
class SearchHit:
    entry: DiaryEntry
    snippet: str


class DiaryStore:
    def __init__(self, diary_dir: Path) -> None:
        self.diary_dir = diary_dir
        diary_dir.mkdir(parents=True, exist_ok=True)

    def _load(self, scope: str) -> list[DiaryEntry]:
        path = self.diary_dir / f"{scope}.json"
        if not path.exists():
            return []
        return [
            DiaryEntry(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))
        ]

    def put(self, scope: str, entry: DiaryEntry) -> None:
        entries = [e for e in self._load(scope) if e.date != entry.date] + [entry]
        entries.sort(key=lambda e: e.date)
        path = self.diary_dir / f"{scope}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([asdict(e) for e in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def latest(self, scope: str, n: int) -> list[DiaryEntry]:
        return self._load(scope)[-n:] if n > 0 else []

    def scopes(self) -> list[str]:
        return sorted(p.stem for p in self.diary_dir.glob("*.json"))

    def entries(self, scope: str) -> list[DiaryEntry]:
        return self._load(scope)

    def get(self, scope: str, date: str) -> DiaryEntry | None:
        return next((e for e in self._load(scope) if e.date == date), None)

    def search(
        self,
        scope: str,
        keywords: list[str],
        start: str = "",
        end: str = "",
        limit: int = 5,
    ) -> list[SearchHit]:
        words = {word.strip().casefold() for word in keywords if word.strip()}
        ranked = []
        for entry in self._load(scope):
            if (start and entry.date < start) or (end and entry.date > end):
                continue
            body = entry.text.casefold()
            tags = "\n".join(entry.tags).casefold()
            full = f"{body}\n{entry.summary.casefold()}\n{tags}"
            matches = sum(word in full for word in words)
            if not matches:
                continue
            positions = [(body.find(word), word) for word in words if word in body]
            if positions:
                pos, word = min(positions)
                snippet = entry.text[max(0, pos - 30) : pos + len(word) + 30]
            else:
                snippet = entry.summary
            rank = (matches, sum(word in tags for word in words), entry.date)
            ranked.append((rank, SearchHit(entry, snippet)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[: max(0, limit)]]
