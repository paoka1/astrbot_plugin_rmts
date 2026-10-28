"""
个人 / 全局记忆，移植自 rmts/plugins/chat/functions/memory/memory_manager.py
与原版的区别：每次写入后立即落盘（原版只在 Bot 关闭时保存）
"""

import asyncio
import json
from pathlib import Path


class MemoryStore:
    """
    记忆存储：作用域（群号或私聊）-> 博士 ID（或作用域本身，表示全局）-> 记忆列表
    每个作用域一个 JSON 文件
    """

    def __init__(self, data_dir: Path, max_length: int = 300) -> None:
        self.data_dir = data_dir
        self.max_length = max_length
        self._lock = asyncio.Lock()
        self.memories: dict[str, dict[str, list[str]]] = {}
        self._load()

    def _file(self, scope: str) -> Path:
        return self.data_dir / f"rosmontis_memory_{scope}.json"

    def _load(self) -> None:
        for path in self.data_dir.glob("rosmontis_memory_*.json"):
            scope = path.stem.removeprefix("rosmontis_memory_")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.memories[scope] = {k: v.get("memories", []) for k, v in data.items()}

    def _save(self, scope: str) -> None:
        data = {
            k: {"max_length": self.max_length, "memories": v}
            for k, v in self.memories.get(scope, {}).items()
        }
        tmp = self._file(scope).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file(scope))

    async def add(self, scope: str, owner: str, items: list[str]) -> None:
        async with self._lock:
            unit = self.memories.setdefault(scope, {}).setdefault(owner, [])
            unit.extend(str(i) for i in items)
            # 确保记忆总长度不超过最大长度，超出时丢弃最早的
            while unit and sum(len(m) for m in unit) > self.max_length:
                unit.pop(0)
            self._save(scope)

    async def get(self, scope: str, owner: str) -> list[str]:
        async with self._lock:
            return list(self.memories.get(scope, {}).get(owner, []))
