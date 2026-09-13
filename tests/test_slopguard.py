"""slopguard.py 的单元测试."""

import importlib.util
import io
import json
from pathlib import Path

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "slopguard.py"
_spec = importlib.util.spec_from_file_location("slopguard", _HOOK)
slopguard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slopguard)


# ---- yaml helpers ----

def test_as_str_list_skips_empty():
    assert slopguard.as_str_list(["等你拍", "  ", None, "钉住"]) == ["等你拍", "钉住"]


def test_as_calque_map_strips():
    assert slopguard.as_calque_map({" 闸门 ": " gate ", None: "x", "a": None}) == {
        "闸门": "gate"
    }


def test_load_yaml_bad_file(tmp_path):
    p = tmp_path / "missing.yaml"
    assert slopguard.load_yaml(p) == {}
    p.write_text("- just a list\n", encoding="utf-8")
    assert slopguard.load_yaml(p) == {}


# ---- find_matches ----

def test_find_matches_basic():
    assert slopguard.find_matches("快等你拍板", ["等你拍"]) == ["等你拍"]


def test_zhandezhu_matches_without_jiao():
    assert slopguard.find_matches("这个理由站得住", ["站得住(?!脚)"]) == ["站得住"]


def test_zhandezhu_jiao_is_good_chinese():
    # 「站得住脚」是正经中文,不该命中
    assert slopguard.find_matches("这个论点站得住脚", ["站得住(?!脚)"]) == []


def test_dengnipan_matches_without_duan():
    assert slopguard.find_matches("方案做好了,等你判", ["等你判(?!断)"]) == ["等你判"]


def test_dengnipan_duan_is_good_chinese():
    # 「等你判断」是正经中文,不该命中
    assert slopguard.find_matches("这事还要等你判断", ["等你判(?!断)"]) == []


def test_find_matches_dedup_keeps_order():
    assert slopguard.find_matches("先钉在再钉住又钉在", ["钉[在住]"]) == ["钉在", "钉住"]


def test_find_matches_skips_bad_regex():
    # 坏正则不能让整个扫描崩掉
    assert slopguard.find_matches("随便什么文字", ["(", "等你拍"]) == []


def test_find_matches_no_hit():
    assert slopguard.find_matches("一句正常的人话", ["等你拍"]) == []


# ---- build_reason ----

def test_build_reason_fills_placeholder():
    assert slopguard.build_reason(["说人话:{words}"], ["甲", "乙"]) == "说人话:甲, 乙"


def test_build_reason_empty_templates_has_fallback():
    assert "甲" in slopguard.build_reason([], ["甲"])


# ---- main(端到端) ----

def _run_main(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    code = slopguard.main()
    return code, out.getvalue(), err.getvalue()


def test_main_blocks_on_slop(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "这个设计稳稳托住了全场",
        "stop_hook_active": False,
    })
    # exit 0 + stdout JSON 阻断
    assert code == 0
    result = json.loads(out)
    assert result["decision"] == "block"
    # reason 是回注给 Claude 的提示词(模板带 {words},含命中词)
    assert "稳稳托住" in result["reason"]
    # systemMessage 是只给用户看的命中横幅
    assert "稳稳托住" in result["systemMessage"]


def test_main_passes_clean_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "这段话写得很正常,没有毛病。",
        "stop_hook_active": False,
    })
    assert code == 0
    assert out.strip() == ""


def test_main_stop_hook_active_passes(tmp_path, monkeypatch):
    # 已在重试中:就算有 slop 也直接放行,避免死循环
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "稳稳托住",
        "stop_hook_active": True,
    })
    assert code == 0
    assert out.strip() == ""


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
    assert (udir / "patterns.yaml").exists()
    assert (udir / "templates.yaml").exists()
    assert not (udir / "patterns.txt").exists()
    assert not (udir / "templates.txt").exists()


# ---- load_patterns / load_calques 分流 ----

def test_load_splits_calques_from_patterns(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    patterns = slopguard.load_patterns()
    calques = slopguard.load_calques()
    assert "稳稳托住" in patterns
    assert ("闸门", "gate") in calques
    templates = slopguard.load_templates()
    calque_templates = slopguard.load_calque_templates()
    assert all("{words}" in t for t in templates)
    assert all("{words_cn}" in t or "{word_en}" in t for t in calque_templates)
    assert templates
    assert calque_templates


# ---- find_calque_matches ----

_ARM = r"(?<!手)(?<!机械)臂"
_CALQUES = [("闸门", "gate"), (_ARM, "arm")]


def test_calque_zhamen_hits():
    assert slopguard.find_calque_matches("打开这个闸门再继续", _CALQUES) == [
        ("闸门", "gate")
    ]


def test_calque_arm_hits():
    assert slopguard.find_calque_matches("把这段接到执行臂上", _CALQUES) == [
        ("臂", "arm")
    ]
    assert slopguard.find_calque_matches("the 臂 is ready", _CALQUES) == [
        ("臂", "arm")
    ]
    # 摇臂不在白名单里, 该命中
    assert slopguard.find_calque_matches("摇臂松了", _CALQUES) == [
        ("臂", "arm")
    ]


def test_calque_arm_excludes_shoubi_and_jixiebi():
    # 只放过 手臂 / 机械臂
    assert slopguard.find_calque_matches("他抬起右手臂", _CALQUES) == []
    assert slopguard.find_calque_matches("机械臂已经就位", _CALQUES) == []


def test_calque_skips_bad_regex():
    assert slopguard.find_calque_matches("闸门", [("(", "x"), ("闸门", "gate")]) == [
        ("闸门", "gate")
    ]


# ---- build_calque_reason ----

def test_build_calque_reason_fills_placeholders():
    got = slopguard.build_calque_reason(
        ['cn={words_cn} en={word_en}'],
        [("闸门", "gate"), ("执行臂", "arm")],
    )
    assert got == "cn=闸门, 执行臂 en=gate, arm"


def test_build_calque_reason_empty_templates_has_fallback():
    got = slopguard.build_calque_reason([], [("闸门", "gate")])
    assert "闸门" in got
    assert "gate" in got


# ---- main: 翻译腔 ----

def test_main_blocks_on_calque(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "先把闸门打开",
        "stop_hook_active": False,
    })
    assert code == 0
    result = json.loads(out)
    assert result["decision"] == "block"
    assert "闸门" in result["reason"]
    assert "gate" in result["reason"]
    assert "闸门" in result["systemMessage"]
    assert "gate" in result["systemMessage"]


def test_main_passes_shoubi_and_jixiebi(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "机械臂已经就位, 右手臂也没问题.",
        "stop_hook_active": False,
    })
    assert code == 0
    assert out.strip() == ""


def test_main_blocks_other_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "把这段接到执行臂上.",
        "stop_hook_active": False,
    })
    assert code == 0
    result = json.loads(out)
    assert result["decision"] == "block"
    assert "臂" in result["reason"]
    assert "arm" in result["reason"]


def test_main_user_calque_in_patterns(tmp_path, monkeypatch):
    # 用户把翻译腔写进同一个 patterns.yaml 的 calque:
    udir = tmp_path / "userconf"
    udir.mkdir()
    (udir / "patterns.yaml").write_text(
        "slop: []\ncalque:\n  令牌: token\n", encoding="utf-8"
    )
    (udir / "templates.yaml").write_text("slop: []\ncalque: []\n", encoding="utf-8")
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(udir))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "把这个令牌传进去",
        "stop_hook_active": False,
    })
    assert code == 0
    result = json.loads(out)
    assert result["decision"] == "block"
    assert "令牌" in result["reason"]
    assert "token" in result["reason"]


def test_main_both_calque_and_slop(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOPGUARD_USER_DIR", str(tmp_path / "userconf"))
    code, out, err = _run_main(monkeypatch, {
        "last_assistant_message": "这个闸门稳稳托住了全场",
        "stop_hook_active": False,
    })
    assert code == 0
    result = json.loads(out)
    assert result["decision"] == "block"
    assert "闸门" in result["reason"]
    assert "gate" in result["reason"]
    assert "稳稳托住" in result["reason"]
