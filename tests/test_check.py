"""slopguard check.py 的单元测试。"""

import importlib.util
import io
import json
from pathlib import Path

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "check.py"
_spec = importlib.util.spec_from_file_location("check", _HOOK)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


# ---- parse_lines ----

def test_parse_lines_skips_comments_and_blanks():
    text = "# 注释\n\n等你拍\n  钉住  \n# 又一条\n"
    assert check.parse_lines(text) == ["等你拍", "钉住"]


# ---- find_matches ----

def test_find_matches_basic():
    assert check.find_matches("快等你拍板", ["等你拍"]) == ["等你拍"]


def test_zhandezhu_matches_without_jiao():
    assert check.find_matches("这个理由站得住", ["站得住(?!脚)"]) == ["站得住"]


def test_zhandezhu_jiao_is_good_chinese():
    # 「站得住脚」是正经中文,不该命中
    assert check.find_matches("这个论点站得住脚", ["站得住(?!脚)"]) == []


def test_dengnipan_matches_without_duan():
    assert check.find_matches("方案做好了,等你判", ["等你判(?!断)"]) == ["等你判"]


def test_dengnipan_duan_is_good_chinese():
    # 「等你判断」是正经中文,不该命中
    assert check.find_matches("这事还要等你判断", ["等你判(?!断)"]) == []


def test_find_matches_dedup_keeps_order():
    assert check.find_matches("先钉在再钉住又钉在", ["钉[在住]"]) == ["钉在", "钉住"]


def test_find_matches_skips_bad_regex():
    # 坏正则不能让整个扫描崩掉
    assert check.find_matches("随便什么文字", ["(", "等你拍"]) == []


def test_find_matches_no_hit():
    assert check.find_matches("一句正常的人话", ["等你拍"]) == []


# ---- build_reason ----

def test_build_reason_fills_placeholder():
    assert check.build_reason(["说人话:{words}"], ["甲", "乙"]) == "说人话:甲、乙"


def test_build_reason_empty_templates_has_fallback():
    assert "甲" in check.build_reason([], ["甲"])


# ---- main(端到端) ----

def _run_main(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = check.main()
    return code, out.getvalue(), err.getvalue()


def test_main_blocks_on_slop(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "这个设计稳稳托住了全场",
        "stop_hook_active": False,
    })
    # exit code 2 = 阻断;命中词与提示词都写在 stderr,回注给 Claude
    assert code == 2
    assert "稳稳托住" in err
    assert "🛡" in err


def test_main_passes_clean_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "这段话写得很正常,没有毛病。",
        "stop_hook_active": False,
    })
    assert code == 0
    assert err.strip() == ""


def test_main_stop_hook_active_passes(tmp_path, monkeypatch):
    # 已在重试中:就算有 slop 也直接放行,避免死循环
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "稳稳托住",
        "stop_hook_active": True,
    })
    assert code == 0
    assert err.strip() == ""


def test_main_no_message_passes(tmp_path, monkeypatch):
    # stdin 里没有 last_assistant_message(比如本轮只调了工具),放行
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {"stop_hook_active": False})
    assert code == 0


def test_main_creates_user_files(tmp_path, monkeypatch):
    udir = tmp_path / "userconf"
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(udir))
    _run_main(monkeypatch, {
        "last_assistant_message": "正常的回答",
        "stop_hook_active": False,
    })
    assert (udir / "patterns.txt").exists()
    assert (udir / "templates.txt").exists()
