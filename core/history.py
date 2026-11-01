"""长期保存的交互记录：别人对她说的话、她的回复和主动发出的消息，供 WebUI 翻阅。

按作用域、按月分文件（`<scope>/<YYYY-MM>.jsonl`），只追加，不随夜间整理清理。
"""

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .diary import LogEntry


def is_interaction(entry: LogEntry) -> bool:
    return entry.to_her or entry.is_self


def _month(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


class History:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def append(self, scope: str, entry: LogEntry) -> None:
        if not is_interaction(entry):
            return
        folder = self.root / scope
        folder.mkdir(exist_ok=True)
        with (folder / f"{_month(entry.ts)}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def scopes(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def query(
        self, scopes: list[str], start: date | None, end: date | None, limit: int
    ) -> tuple[list[tuple[str, LogEntry]], bool]:
        """按时间倒序返回 (作用域, 记录)，起止日期都包含在内；第二项表示是否被截断。"""
        first = start.strftime("%Y-%m") if start else ""
        last = end.strftime("%Y-%m") if end else "9999-99"
        rows: list[tuple[str, LogEntry]] = []
        for scope in scopes:
            for path in (self.root / scope).glob("*.jsonl"):
                if not first <= path.stem <= last:
                    continue
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = LogEntry(**json.loads(line))
                        except (json.JSONDecodeError, TypeError):
                            continue
                        day = datetime.fromtimestamp(entry.ts).date()
                        if (start and day < start) or (end and day > end):
                            continue
                        rows.append((scope, entry))
        rows.sort(key=lambda row: row[1].ts, reverse=True)
        return rows[:limit], len(rows) > limit
