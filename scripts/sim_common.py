"""本地模拟脚本共用的部分：导入 core、拼记事块、读预设场景、打印期望。"""

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

# 和 tests/conftest.py 一样，把仓库根注册为包 rmts，只导入不依赖 AstrBot 的 core
_pkg = types.ModuleType("rmts")
_pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("rmts", _pkg)

from rmts.core.memory import Fact, Profile, render_memory_block


def facts_of(texts: list[str], date: str = "") -> list[Fact]:
    return [Fact(i, text, date, []) for i, text in enumerate(texts, 1)]


def memory_block(memory: dict, name: str, qq: str) -> str:
    """把场景里的 memory 拼成插件注入的记事块，当作正在说话的博士的记事。

    memory 可有：impression、facts（字符串列表）、group（本群记录）、
    diary（[日期, 正文]）、sent（[[时间, 她主动发过的话], ...]）。
    """
    if not memory:
        return ""
    return render_memory_block(
        speaker_qq=qq,
        speaker_name=name,
        speaker=Profile(name=name, impression=memory.get("impression", "")),
        speaker_facts=facts_of(memory.get("facts", [])),
        mentioned=[],
        group_facts=facts_of(memory.get("group", [])),
        sent=[tuple(item) for item in memory.get("sent", [])],
        diaries=[tuple(memory["diary"])] if memory.get("diary") else [],
    )


def load_cases(path: Path, wanted: list[str]) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if "all" not in wanted:
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            sys.exit(f"没有找到场景：{'、'.join(wanted)}")
    return cases


def print_expect(case: dict) -> None:
    print("期望：" + "；".join(case["expect"]))
    print("不应出现：" + "；".join(case["red_flags"]) + "\n")
