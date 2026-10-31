"""插件自带的人格：启动时同步到 AstrBot，用户改过就不覆盖。"""

import hashlib
import json
from pathlib import Path

PERSONA_ID = "迷迭香（rmts）"


def digest(prompt: str) -> str:
    # WebUI 保存时可能改动换行符和首尾空白，统一后再比较
    text = prompt.replace("\r\n", "\n").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_action(shipped: str, current: str | None, synced: str) -> str:
    """决定怎么同步。synced 是插件上次写入内容的摘要，没有写过为空。

    返回 create（还没有这个人格）、keep（内容已是最新）、
    update（用户没改过，换成新版）、edited（用户改过，不动）。
    """
    if current is None:
        return "create"
    if digest(current) == digest(shipped):
        return "keep"
    if digest(current) == synced:
        return "update"
    return "edited"


class PersonaState:
    """记下插件上次写入人格的摘要，用来判断用户有没有改过。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def synced(self) -> str:
        if not self._path.exists():
            return ""
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return data.get("synced", "")

    def save(self, prompt: str) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"synced": digest(prompt)}), encoding="utf-8")
        tmp.replace(self._path)
