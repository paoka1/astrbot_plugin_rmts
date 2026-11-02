"""模拟群里插话：她在什么时候被唤醒、会不会开口、说什么。

两种用法：
1. 回放一段真实群聊记录。触发规则和给她看的内容与插件相同（core/interject.py）：
   新消息够条数、离上次判断和她上次说话都够久时唤醒，带上人格、终端上的记事
   （本群记录、最近说话的博士在本群可见的事实、最近的日记）和最近的群聊。
       uv run python scripts/interject_sim.py --data <插件数据目录> --log <插件数据目录>/logs/<群号>.jsonl
       uv run python scripts/interject_sim.py --data ... --log ... --messages 20 --minutes 15 --dry
       uv run python scripts/interject_sim.py --data ... --log ... --at 715 --at 747
   --data 下需要 memory.json 和 diaries/；--log 是插件的当天记录（JSONL，文件名为群号），
   从服务器拷到本地（例如 scratchpad），不要放进仓库。
   --dry 只列出唤醒时间点，不调用模型；--at 只在第几条消息处唤醒（可写多次），改了提示词后用它重跑同一批时刻。
   --config 读插件的配置文件（服务器上 config/astrbot_plugin_rmts_config.json），用其中插话的默认值、
   按时段的设置和每天上限；不给时用 --messages、--minutes、--threshold、--limit，不分时段。
   回放的是真实发生过的群聊：她模拟中插的话不会出现在之后的群聊里，只用于重新计数。

2. 跑 scripts/interject_cases.json 里的预设场景，每个场景唤醒一次，再列出期望和不应出现的表现：
       uv run python scripts/interject_sim.py --case all
       uv run python scripts/interject_sim.py --case INT-004 --case INT-005
   场景的 chat 每行是 [时:分, 名字, QQ, 内容]，QQ 写 self 表示她自己说的；
   memory 可有 group（本群记录）、people（{QQ: [事实]}）、diary（[日期, 正文]）。

API Key 从环境变量 DEEPSEEK_API_KEY 读取。
"""

import argparse
import json
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from persona_chat import chat, load_persona
from rmts.core.diary import DailyLog, DiaryStore, LogEntry
from rmts.core.interject import (
    Interjector,
    Rule,
    build_prompt,
    interject_prompt,
    parse_reply,
    parse_slots,
)
from rmts.core.memory import MemoryStore
from sim_common import SCRIPTS, facts_of, load_cases, print_expect

CASES = SCRIPTS / "interject_cases.json"
# 插件唤醒后先等 15 秒，期间她正常回复了就不插话；回放时看她这段时间里有没有真的说话
WAIT = timedelta(seconds=15)


def ask(
    args,
    key: str,
    persona: str,
    prompt: str,
    entries: list[LogEntry],
    threshold: float,
) -> bool:
    """调一次模型，打印她的意愿和想说的话；达到阈值、真的会发出去时返回 True。"""
    reply = chat(
        args.base,
        key,
        args.model,
        [
            {"role": "system", "content": persona},
            {"role": "user", "content": prompt},
        ],
    )
    said = parse_reply(reply, entries)
    sent = said.passes(threshold)
    verdict = "发出" if sent else "不发"
    print(f"  → 意愿 {said.score}（阈值 {threshold}），{verdict}")
    if said.reason:
        print(f"  → 理由：{said.reason}")
    if not said.text:
        print("  → 不说")
        return False
    if said.reply_to:
        print(f"  → 引用 {said.reply_to.name}「{said.reply_to.text}」")
    if said.at:
        print(f"  → @{said.at.name}")
    print(f"  → 迷迭香：{said.text}")
    return sent


def run_case(case: dict, args, key: str, persona: str) -> None:
    now = datetime.strptime(case["now"], "%Y-%m-%d %H:%M")
    entries = []
    for n, (hhmm, name, qq, text) in enumerate(case["chat"], 1):
        ts = int(datetime.combine(now.date(), time.fromisoformat(hhmm)).timestamp())
        mine = qq == "self"
        entries.append(
            LogEntry(ts, qq, name, text, is_self=mine, msg_id="" if mine else f"m{n}")
        )
    memory = case.get("memory", {})
    names = {e.qq: e.name for e in entries}
    prompt = interject_prompt(
        now=now,
        entries=entries,
        group_facts=facts_of(memory.get("group", [])),
        people=[
            (qq, names.get(qq, qq), facts_of(facts))
            for qq, facts in memory.get("people", {}).items()
        ],
        diary=tuple(memory["diary"]) if memory.get("diary") else None,
    )
    print(f"===== {case['id']} {case['category']}（现在 {now:%H:%M}）")
    for e in entries:
        print(f"  {datetime.fromtimestamp(e.ts):%H:%M} {e.name}：{e.text}")
    ask(args, key, persona, prompt, entries, args.interjector.rule(now).threshold)
    print_expect(case)


def replay(args, key: str, persona: str) -> None:
    scope = args.log.stem
    memory = MemoryStore(args.data, 500)
    diaries = DiaryStore(args.data / "diaries")
    _, entries = DailyLog(args.log.parent).load(scope, 2**62)
    if not entries:
        sys.exit("这份记录是空的")
    interjector = args.interjector

    woke = spoke = 0
    for i, entry in enumerate(entries):
        now = datetime.fromtimestamp(entry.ts)
        if entry.is_self:
            interjector.spoke(scope, now)
            continue
        # 指定了 --at 时只在这些位置唤醒，便于改了提示词后重跑同一批时刻
        if args.at:
            if i + 1 not in args.at:
                continue
        elif not interjector.on_message(scope, now):
            continue
        woke += 1
        print(f"\n=== {now:%m-%d %H:%M:%S} 唤醒（第 {i + 1} 条消息）===")
        for e in entries[max(0, i + 1 - args.show) : i + 1]:
            who = "迷迭香" if e.is_self else e.name
            print(f"  {datetime.fromtimestamp(e.ts):%H:%M} {who}：{e.text}")
        if any(
            e.is_self and now <= datetime.fromtimestamp(e.ts) <= now + WAIT
            for e in entries[i + 1 :]
        ):
            print("  → 她正在正常回复，不插话")
            continue
        if args.dry:
            continue
        prompt = build_prompt(memory, diaries, scope, entries[: i + 1], now)
        threshold = interjector.rule(now).threshold
        if threshold > 1:
            print(f"  → 阈值 {threshold}，这个时段不插话")
            continue
        if ask(args, key, persona, prompt, entries[: i + 1], threshold):
            spoke += 1
            interjector.spoke(scope, now)
            interjector.interjected(scope, now)

    first = datetime.fromtimestamp(entries[0].ts)
    last = datetime.fromtimestamp(entries[-1].ts)
    print(
        f"\n共 {len(entries)} 条消息（{first:%m-%d %H:%M} ~ {last:%m-%d %H:%M}），"
        f"唤醒 {woke} 次" + ("" if args.dry else f"，插话 {spoke} 次")
    )


def main() -> None:
    # 按行刷新，重定向到文件时也能看到进度
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", help="跑预设场景，填编号或 all")
    parser.add_argument("--data", type=Path, help="插件数据目录")
    parser.add_argument("--log", type=Path, help="某个群的当天记录")
    parser.add_argument("--persona", default="persona/rosmontis.md")
    parser.add_argument("--messages", type=int, default=30, help="至少多少条新消息")
    parser.add_argument("--minutes", type=int, default=30, help="至少隔多少分钟")
    parser.add_argument("--limit", type=int, default=5, help="每天最多插话几次，0 不限")
    parser.add_argument(
        "--show", type=int, default=6, help="每次唤醒时显示最近几条消息"
    )
    parser.add_argument("--dry", action="store_true", help="只列唤醒时间点，不调用模型")
    parser.add_argument(
        "--at", type=int, action="append", help="只在第几条消息处唤醒，可写多次"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.6, help="意愿达到多少才发出（0～1）"
    )
    parser.add_argument("--config", type=Path, help="插件配置文件，用其中的插话设置")
    parser.add_argument(
        "--model", default=os.environ.get("RMTS_MODEL", "deepseek-v4-pro")
    )
    parser.add_argument(
        "--base", default=os.environ.get("RMTS_API_BASE", "https://api.deepseek.com")
    )
    args = parser.parse_args()
    conf = (
        json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
    )
    default = Rule(
        int(conf.get("interject_min_messages", args.messages)),
        int(conf.get("interject_min_minutes", args.minutes)),
        float(conf.get("interject_threshold", args.threshold)),
    )
    args.interjector = Interjector(
        default,
        parse_slots(conf.get("interject_slots", []), default),
        int(conf.get("interject_daily_limit", args.limit)),
    )
    if not (args.case or (args.data and args.log)):
        parser.error("要么给 --case，要么同时给 --data 和 --log")

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not (args.dry or key):
        sys.exit("请先设置环境变量 DEEPSEEK_API_KEY，或加 --dry")
    persona = load_persona(args.persona)
    if args.case:
        for case in load_cases(CASES, args.case):
            run_case(case, args, key, persona)
    else:
        replay(args, key, persona)


if __name__ == "__main__":
    main()
