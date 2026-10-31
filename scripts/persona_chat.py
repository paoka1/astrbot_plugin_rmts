"""在本地终端里和人格提示词聊天，用来对比不同版本人格的效果。

不经过 AstrBot：没有工具调用、终端记事和日记，只有人格提示词和对话历史。
消息会像插件一样拼上「博士（TA的名字是：…，TA的ID是…），对你说：」。

用法：
    uv run python scripts/persona_chat.py
    uv run python scripts/persona_chat.py HEAD:persona/rosmontis.md persona/rosmontis.md
    uv run python scripts/persona_chat.py HEAD:persona/rosmontis.md persona/rosmontis.md -m "香香在吗" -m "今天有人欺负我"

人格可以是文件路径，也可以是 git 的「提交:路径」。给多个人格时，每句话会分别发给每个人格。
API Key 从环境变量 DEEPSEEK_API_KEY 读取；对话中输入 /reset 清空历史，/quit 退出。
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_persona(source: str) -> str:
    path = ROOT / source
    if path.exists():
        return path.read_text(encoding="utf-8")
    # 不是现有文件时按「提交:路径」从 git 读取
    return subprocess.run(
        ["git", "show", source],
        cwd=ROOT,
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout


def chat(base: str, key: str, model: str, messages: list[dict]) -> str:
    request = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=json.dumps({"model": model, "messages": messages}).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    # 网络偶尔断开握手，重试两次
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read())
            return body["choices"][0]["message"]["content"].strip()
        except urllib.error.URLError:
            if attempt == 2:
                raise


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("personas", nargs="*", default=["persona/rosmontis.md"])
    parser.add_argument("-m", "--message", action="append", help="依次发送这些话后退出")
    parser.add_argument("--name", default="博士", help="说话的博士的名字")
    parser.add_argument("--qq", default="10000", help="说话的博士的 QQ 号")
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
    prompts = {source: load_persona(source) for source in args.personas}
    histories = {source: [] for source in prompts}

    def send(text: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        prompt = f"博士（TA的名字是：{args.name}，TA的ID是{args.qq}），对你说：{text}\n（当前时间：{now}）"
        for source, system in prompts.items():
            history = histories[source]
            history.append({"role": "user", "content": prompt})
            reply = chat(
                args.base,
                key,
                args.model,
                [{"role": "system", "content": system}, *history],
            )
            history.append({"role": "assistant", "content": reply})
            label = f"[{source}] " if len(prompts) > 1 else ""
            print(f"{label}迷迭香：{reply}\n")

    if args.message:
        for text in args.message:
            print(f"{args.name}：{text}")
            send(text)
        return
    while True:
        try:
            text = input(f"{args.name}：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/quit":
            break
        if text == "/reset":
            for history in histories.values():
                history.clear()
            print("（已清空对话历史）\n")
        elif text:
            send(text)


if __name__ == "__main__":
    main()
