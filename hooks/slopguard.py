# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""slopguard -- Claude Code 的 Stop hook.

Claude 每说完一整轮, 扫一遍它这一轮的回复; 命中 AI 腔词库或翻译腔
词库就返回 {"decision": "block"} -- CC 会拦住这次停止: reason 作为
一条 "Stop hook feedback" 进 Claude 的 context (只此一条、不带命令
前缀, 已用随机串实测确认) 逼它用人话重说 / 把硬翻的专有名词改回
英文; systemMessage 只显示给用户, 不进 Claude 的 context.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_ROOT / "data"
DEFAULT_PATTERNS_FILE = DATA_DIR / "default-patterns.yaml"
DEFAULT_TEMPLATES_FILE = DATA_DIR / "default-templates.yaml"

USER_PATTERNS_HEADER = """\
# slop: AI 腔正则 (Python re)
# calque: 硬翻中文: 英文
slop: []
calque: {}
"""

USER_TEMPLATES_HEADER = """\
# slop 模板用 {words}
# calque 模板用 {words_cn} / {word_en}
slop: []
calque: []
"""

DEFAULT_CALQUE_TEMPLATE = (
    '说多少次了英文专有名词不硬翻为中文! "{words_cn}" '
    '在中文语境下从来不能表达这个意思! 就应该用 "{word_en}" 即便是在中文中!'
)


def user_dir() -> Path:
    """用户层配置目录. 可用环境变量 SLOPGUARD_USER_DIR 覆盖 (主要给测试用)."""
    env = os.environ.get("SLOPGUARD_USER_DIR")
    return Path(env) if env else Path.home() / ".claude" / "slopguard"


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def as_calque_map(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in value.items():
        if k is None or v is None:
            continue
        cn, en = str(k).strip(), str(v).strip()
        if cn and en:
            out[cn] = en
    return out


def ensure_user_file(path: Path, header: str) -> None:
    """用户层文件不存在时, 创建一个带说明的空文件, 方便用户编辑."""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")
    except OSError:
        pass


def ensure_user_files() -> None:
    """确保两个用户层配置文件都存在, 方便用户一次性发现和编辑."""
    udir = user_dir()
    ensure_user_file(udir / "patterns.yaml", USER_PATTERNS_HEADER)
    ensure_user_file(udir / "templates.yaml", USER_TEMPLATES_HEADER)


def _patterns_docs() -> list[dict]:
    upath = user_dir() / "patterns.yaml"
    ensure_user_file(upath, USER_PATTERNS_HEADER)
    return [load_yaml(DEFAULT_PATTERNS_FILE), load_yaml(upath)]


def _templates_docs() -> list[dict]:
    upath = user_dir() / "templates.yaml"
    ensure_user_file(upath, USER_TEMPLATES_HEADER)
    return [load_yaml(DEFAULT_TEMPLATES_FILE), load_yaml(upath)]


def load_patterns() -> list[str]:
    """AI 腔正则: 默认 + 用户, 按文件顺序拼接."""
    out: list[str] = []
    for doc in _patterns_docs():
        out.extend(as_str_list(doc.get("slop")))
    return out


def load_calques() -> list[tuple[str, str]]:
    """翻译腔词条. 后写的同中文覆盖先写的."""
    by_cn: dict[str, str] = {}
    for doc in _patterns_docs():
        by_cn.update(as_calque_map(doc.get("calque")))
    return list(by_cn.items())


def load_templates() -> list[str]:
    """AI 腔回注模板."""
    out: list[str] = []
    for doc in _templates_docs():
        out.extend(as_str_list(doc.get("slop")))
    return out


def load_calque_templates() -> list[str]:
    """翻译腔回注模板."""
    out: list[str] = []
    for doc in _templates_docs():
        out.extend(as_str_list(doc.get("calque")))
    return out


def find_matches(text: str, patterns: list[str]) -> list[str]:
    """逐条正则扫描 text, 返回去重后的命中片段 (保持出现顺序). 坏正则跳过."""
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


def find_calque_matches(
    text: str, calques: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """扫描 text, 返回 [(命中片段, 英文)] 去重保序. 坏正则跳过."""
    found: dict[tuple[str, str], None] = {}
    for pat, en in calques:
        try:
            regex = re.compile(pat)
        except re.error:
            continue
        for m in regex.finditer(text):
            frag = m.group(0)
            if frag:
                found.setdefault((frag, en), None)
    return list(found)


def build_reason(templates: list[str], words: list[str]) -> str:
    """随机挑一条模板, 把 {words} 换成命中的词."""
    joined = ", ".join(words)
    if not templates:
        return f"检测到 AI 腔:{joined}. 请用自然中文重写这部分."
    return random.choice(templates).replace("{words}", joined)


def build_calque_reason(
    templates: list[str], hits: list[tuple[str, str]]
) -> str:
    """随机挑一条翻译腔模板, 填 {words_cn}/{word_en} (也认单复数别名)."""
    words_cn = ", ".join(cn for cn, _ in hits)
    word_en = ", ".join(en for _, en in hits)
    template = random.choice(templates) if templates else DEFAULT_CALQUE_TEMPLATE
    return (
        template.replace("{words_cn}", words_cn)
        .replace("{word_cn}", words_cn)
        .replace("{words_en}", word_en)
        .replace("{word_en}", word_en)
    )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    # 防死循环: 已经在 Stop hook 重试中, 直接放行 (每轮最多打回一次).
    if payload.get("stop_hook_active"):
        return 0

    # CC 在 Stop hook 的 stdin 里直接给了本轮最后一条助手消息.
    # 不去解析 transcript -- Stop 触发时那条消息可能还没落盘到文件.
    text = payload.get("last_assistant_message") or ""
    if not text:
        return 0

    ensure_user_files()
    calque_hits = find_calque_matches(text, load_calques())
    matches = find_matches(text, load_patterns())
    if not calque_hits and not matches:
        return 0

    # 命中: 用 JSON 阻断这次停止.
    # - reason: 回注给 Claude 的提示词. 作为一条 "Stop hook feedback"
    #   消息进 context, 只此一条、不带命令前缀. 翻译腔和 AI 腔都中的话
    #   两句拼在一起 (Stop 每轮只打回一次, 漏一句就放过去了).
    # - systemMessage: 只给用户看的命中横幅, 不进 Claude 的 context.
    reasons: list[str] = []
    banners: list[str] = []
    if calque_hits:
        reasons.append(build_calque_reason(load_calque_templates(), calque_hits))
        banners.append(
            "翻译腔:" + ", ".join(f"{cn}->{en}" for cn, en in calque_hits)
        )
    if matches:
        reasons.append(build_reason(load_templates(), matches))
        banners.append("AI 腔:" + ", ".join(matches))
    output = {
        "decision": "block",
        "reason": "\n".join(reasons),
        "systemMessage": "🛡 slopguard 命中 " + "; ".join(banners),
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 任何意外都放行, 绝不卡住用户会话
        print(f"slopguard hook error: {exc}", file=sys.stderr)
        sys.exit(0)
