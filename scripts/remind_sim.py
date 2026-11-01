"""模拟提醒到点时她组织的话：普通提醒、天气提醒、她自己记挂的事。

任务说明与插件相同（core/reminder.py 的 remind_task），天气直接用场景里写好的文字，不调用高德接口。

用法：
    uv run python scripts/remind_sim.py --case all
    uv run python scripts/remind_sim.py --case REM-004

场景在 scripts/remind_cases.json：now、name、text，可选 weather（查好的天气或没查到的说明）、
follow_up（她自己记挂的事）。API Key 从环境变量 DEEPSEEK_API_KEY 读取。
"""

import argparse
import os
import sys
from datetime import datetime

from persona_chat import chat, load_persona
from rmts.core.reminder import Reminder, remind_task
from sim_common import SCRIPTS, load_cases, print_expect

CASES = SCRIPTS / "remind_cases.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=[], help="编号或 all")
    parser.add_argument("--persona", default="persona/rosmontis.md")
    parser.add_argument(
        "--model", default=os.environ.get("RMTS_MODEL", "deepseek-v4-pro")
    )
    parser.add_argument(
        "--base", default=os.environ.get("RMTS_API_BASE", "https://api.deepseek.com")
    )
    args = parser.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        sys.exit("请先设置环境变量 DEEPSEEK_API_KEY")
    persona = load_persona(args.persona)

    for case in load_cases(CASES, args.case or ["all"]):
        r = Reminder(
            id=0,
            scope="",
            umo="",
            qq="",
            name=case["name"],
            at="",
            repeat="",
            text=case["text"],
            compose=True,
            follow_up=case.get("follow_up", False),
        )
        now = datetime.strptime(case["now"], "%Y-%m-%d %H:%M")
        prompt = remind_task(r, now, case.get("weather", ""))
        print(f"===== {case['id']} {case['category']}（现在 {now:%H:%M}）")
        print(f"  要说的：{case['text']}")
        if case.get("weather"):
            print(f"  天气：{case['weather']}")
        reply = chat(
            args.base,
            key,
            args.model,
            [
                {"role": "system", "content": persona},
                {"role": "user", "content": prompt},
            ],
        )
        print(f"  → 迷迭香：{reply}")
        print_expect(case)


if __name__ == "__main__":
    main()
