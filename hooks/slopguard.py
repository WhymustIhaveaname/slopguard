# /// script
# requires-python = ">=3.9"
# ///
"""slopguard —— Claude Code 的 Stop hook。

Claude 每说完一整轮,扫一遍它这一轮的回复;命中 AI 腔词库就返回
{"decision": "block"} —— CC 会拦住这次停止:reason 作为一条
"Stop hook feedback" 进 Claude 的 context(只此一条、不带命令前缀,
已用随机串实测确认)逼它用人话重说;systemMessage 只显示给用户,
不进 Claude 的 context。
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_ROOT / "data"
DEFAULT_PATTERNS_FILE = DATA_DIR / "default-patterns.txt"
DEFAULT_TEMPLATES_FILE = DATA_DIR / "default-templates.txt"

USER_PATTERNS_HEADER = """\
# slopguard 用户词库 —— 在这里追加你自己的 AI 腔正则。
# 一行一条正则(Python re 语法),# 开头为注释,空行忽略。
# 例: 站得住(?!脚)
"""

USER_TEMPLATES_HEADER = """\
# slopguard 用户模板 —— 在这里追加你自己的回注提示词。
# 一行一条,{words} 会被替换成命中的 AI 腔词。
"""


def user_dir() -> Path:
    """用户层配置目录。可用环境变量 SLOPGUARD_USER_DIR 覆盖(主要给测试用)。"""
    env = os.environ.get("SLOPGUARD_USER_DIR")
    return Path(env) if env else Path.home() / ".claude" / "slopguard"


def parse_lines(text: str) -> list[str]:
    """取出文件里的有效行:去掉空行和 # 注释。"""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def read_list_file(path: Path) -> list[str]:
    try:
        return parse_lines(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def ensure_user_file(path: Path, header: str) -> None:
    """用户层文件不存在时,创建一个带说明的空文件,方便用户编辑。"""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")
    except OSError:
        pass


def ensure_user_files() -> None:
    """确保两个用户层配置文件都存在,方便用户一次性发现和编辑。"""
    udir = user_dir()
    ensure_user_file(udir / "patterns.txt", USER_PATTERNS_HEADER)
    ensure_user_file(udir / "templates.txt", USER_TEMPLATES_HEADER)


def load_patterns() -> list[str]:
    """默认词库 + 用户词库。"""
    upath = user_dir() / "patterns.txt"
    ensure_user_file(upath, USER_PATTERNS_HEADER)
    return read_list_file(DEFAULT_PATTERNS_FILE) + read_list_file(upath)


def load_templates() -> list[str]:
    """默认模板 + 用户模板。"""
    upath = user_dir() / "templates.txt"
    ensure_user_file(upath, USER_TEMPLATES_HEADER)
    return read_list_file(DEFAULT_TEMPLATES_FILE) + read_list_file(upath)


def find_matches(text: str, patterns: list[str]) -> list[str]:
    """逐条正则扫描 text,返回去重后的命中片段(保持出现顺序)。坏正则跳过。"""
    found: dict[str, None] = {}
    for pat in patterns:
        try:
            regex = re.compile(pat)
        except re.error:
            continue
        for m in regex.finditer(text):
            frag = m.group(0)
            if frag:
                found.setdefault(frag, None)
    return list(found)


def build_reason(templates: list[str], words: list[str]) -> str:
    """随机挑一条模板,把 {words} 换成命中的词。"""
    joined = "、".join(words)
    if not templates:
        return f"检测到 AI 腔:{joined}。请用自然中文重写这部分。"
    return random.choice(templates).replace("{words}", joined)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    # 防死循环:已经在 Stop hook 重试中,直接放行(每轮最多打回一次)。
    if payload.get("stop_hook_active"):
        return 0

    # CC 在 Stop hook 的 stdin 里直接给了本轮最后一条助手消息。
    # 不去解析 transcript —— Stop 触发时那条消息可能还没落盘到文件。
    text = payload.get("last_assistant_message") or ""
    if not text:
        return 0

    ensure_user_files()
    matches = find_matches(text, load_patterns())
    if not matches:
        return 0

    # 命中:用 exit code 2 阻断这次停止。stderr 的内容会被 CC 回注给
    # Claude(也显示给用户),逼它用人话重说。只放"打回"那一句,让进
    # context 的东西尽量少。
    # 命中:用 JSON 阻断这次停止。
    # - reason: 回注给 Claude 的提示词。作为一条 "Stop hook feedback"
    #   消息进 context,只此一条、不带命令前缀。
    # - systemMessage: 只给用户看的命中横幅,不进 Claude 的 context。
    output = {
        "decision": "block",
        "reason": build_reason(load_templates(), matches),
        "systemMessage": "🛡 slopguard 命中 AI 腔:" + "、".join(matches),
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 任何意外都放行,绝不卡住用户会话
        print(f"slopguard hook error: {exc}", file=sys.stderr)
        sys.exit(0)
