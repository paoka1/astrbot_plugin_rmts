"""在本地写日记，不落盘，用来看她写出来的效果、对比不同的任务说明。

写法与夜间整理相同（core/nightly.py 的 write_diary）：人格 + 日记任务说明，当天记录按字数预算抽样。

两种用法：
1. 用一段真实的当天记录（从服务器拷到本地，例如 scratchpad，不要放进仓库）：
       uv run python scripts/diary_sim.py --data <插件数据目录> --log <插件数据目录>/logs/<群号>.jsonl
2. 跑 scripts/diary_cases.json 里的预设场景，写完列出期望和不应出现的表现：
       uv run python scripts/diary_sim.py --case all
   场景的 log 每行是 [时:分, 名字, QQ, 内容]，第五项写 to_her 表示对她说的，QQ 写 self 表示她自己说的；
   previous 是前一篇日记 [日期, 正文]，可省略。

--task 用文件里的任务说明代替当前的 DIARY_TASK，可写多次，同一份记录依次各写一篇。
API Key 从环境变量 DEEPSEEK_API_KEY 读取。
"""

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path

from persona_chat import chat, load_persona
from rmts.core.diary import DailyLog, DiaryEntry, DiaryStore, LogEntry
from rmts.core.nightly import DIARY_TASK, write_diary
from sim_common import SCRIPTS, load_cases, print_expect

CASES = SCRIPTS / "diary_cases.json"


def case_entries(case: dict) -> list[LogEntry]:
    day = datetime.strptime(case["date"], "%Y-%m-%d").date()
    entries = []
    for row in case["log"]:
        hhmm, name, qq, text = row[:4]
        ts = int(datetime.combine(day, time.fromisoformat(hhmm)).timestamp())
        mine = qq == "self"
        entries.append(
            LogEntry(
                ts,
                "" if mine else qq,
                name,
                text,
                to_her="to_her" in row[4:],
                is_self=mine,
            )
        )
    return entries


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", help="跑预设场景，填编号或 all")
    parser.add_argument("--data", type=Path, help="插件数据目录")
    parser.add_argument("--log", type=Path, help="某个群或私聊的当天记录")
    parser.add_argument("--persona", default="persona/rosmontis.md")
    parser.add_argument("--task", action="append", type=Path, help="任务说明文件")
    parser.add_argument("--budget", type=int, default=20000, help="读取记录的字数预算")
    parser.add_argument(
        "--model", default=os.environ.get("RMTS_MODEL", "deepseek-v4-pro")
    )
    parser.add_argument(
        "--base", default=os.environ.get("RMTS_API_BASE", "https://api.deepseek.com")
    )
    args = parser.parse_args()
    if not (args.case or (args.data and args.log)):
        parser.error("要么给 --case，要么同时给 --data 和 --log")

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        sys.exit("请先设置环境变量 DEEPSEEK_API_KEY")
    persona = load_persona(args.persona)
    tasks = [(str(p), p.read_text(encoding="utf-8")) for p in args.task or []]
    tasks = tasks or [("当前 DIARY_TASK", DIARY_TASK)]

    async def llm(system: str, prompt: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return await asyncio.to_thread(chat, args.base, key, args.model, messages)

    def write(scope: str, entries: list[LogEntry], diaries: DiaryStore, today: str):
        for name, task in tasks:
            entry = asyncio.run(
                write_diary(
                    scope,
                    entries,
                    diaries=diaries,
                    llm=llm,
                    persona_prompt=persona,
                    today=today,
                    budget=args.budget,
                    task=task,
                )
            )
            print(f"\n=== {name}：{len(entry.text)} 字 ===")
            print(entry.text)
            print(f"摘要：{entry.summary}")
            print(f"标签：{'、'.join(entry.tags)}")

    if args.case:
        for case in load_cases(CASES, args.case):
            print(f"===== {case['id']} {case['category']}（{len(case['log'])} 条记录）")
            with tempfile.TemporaryDirectory() as tmp:
                diaries = DiaryStore(Path(tmp))
                if case.get("previous"):
                    date, text = case["previous"]
                    diaries.put(case["scope"], DiaryEntry(date, text, "", []))
                write(case["scope"], case_entries(case), diaries, case["date"])
            print()
            print_expect(case)
        return

    scope = args.log.stem
    _, entries = DailyLog(args.log.parent).load(scope, 2**62)
    if not entries:
        sys.exit("这份记录是空的")
    today = datetime.fromtimestamp(entries[-1].ts).date().isoformat()
    write(scope, entries, DiaryStore(args.data / "diaries"), today)
    print(f"\n记录 {len(entries)} 条，日期 {today}")


if __name__ == "__main__":
    main()
