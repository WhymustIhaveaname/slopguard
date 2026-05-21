# /// script
# requires-python = ">=3.13"
# ///
"""slopguard —— Claude Code 的 Stop hook。

Claude 每说完一整轮,扫一遍它说的中文;命中 AI 腔词库就返回
{"decision": "block"},让 Claude 用人话重说这一轮。
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


def _message_content(entry: dict):
    return entry.get("message", {}).get("content")


def is_real_user_message(entry: dict) -> bool:
    """真人输入返回 True;工具结果回填(tool_result)返回 False。"""
    content = _message_content(entry)
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def assistant_text(entry: dict) -> str:
    """取一条 assistant 记录里的纯文本(忽略 tool_use 等块)。"""
    content = _message_content(entry)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def extract_last_turn_text(transcript_path: str) -> str:
    """取最近一轮里 Claude 说的全部散文:从末尾回溯,直到上一条真人输入。"""
    try:
        raw = Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return ""

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    chunks = []
    for entry in reversed(entries):
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        etype = entry.get("type")
        if etype == "assistant":
            text = assistant_text(entry)
            if text:
                chunks.append(text)
        elif etype == "user" and is_real_user_message(entry):
            break
    chunks.reverse()
    return "\n".join(chunks)


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

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    text = extract_last_turn_text(transcript_path)
    if not text:
        return 0

    ensure_user_files()
    matches = find_matches(text, load_patterns())
    if not matches:
        return 0

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
