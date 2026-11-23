# -*- coding: utf-8 -*-
"""1.9.11 移植回归：把新插件 v2.0.7/2.0.8/2.0.9 的修复回移到 1.X 单文件插件。

用法: python tests/test_1911_port.py [plugin.py 路径]
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
覆盖：上下文串入检测、数字保真、词库直取、专名保护修正、
      _translate_one 的串入重试/数字闸门，以及词库错误译名已纠正。
"""
import atexit
import importlib.util
import io
import json
import os
import shutil
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（发布门禁 A6 会判为运行产物）
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "ai_translate_assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait1911_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait1911_plugin_")
os.makedirs(os.path.join(_TMP, "ai_translate_assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_TMP, "ai_translate.py"))
for _f in ("stalker_termbase.json", "glossary_builtin.json"):
    _src = os.path.join(_ASSET_SRC, _f)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_TMP, "ai_translate_assets", _f))
# vendor zip（_load_vendor 需要）
for _f in os.listdir(_ASSET_SRC):
    if _f.lower().endswith(".zip"):
        shutil.copy2(os.path.join(_ASSET_SRC, _f),
                     os.path.join(_TMP, "ai_translate_assets", _f))
PLUGIN = os.path.join(_TMP, "ai_translate.py")
sys.path.insert(0, _TMP)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

spec = importlib.util.spec_from_file_location("ait1911", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class ScriptedServer:
    """按调用次序返回预设译文；记录每次请求的 context 与 extra_sys。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.contexts = []
        self.notes = []
        self.n = 0

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        self.contexts.append(kw.get("context"))
        self.notes.append(kw.get("extra_sys"))
        i = min(self.n, len(self.replies) - 1)
        self.n += 1
        r = self.replies[i]
        if callable(r):
            return r(text, kw)
        return r


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait1911_job_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs"), exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\n"
                "files = stable_sms_character_name\n")
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=True, copy_reference=True,
                    protect_proper=True, auto_session_terms=False,
                    tm_lite=False, force_glossary=False)
    defaults.update(kw)
    return ait.TranslationJob(ait._load_vendor(), srv,
                              ind, "auto", "auto", "chs", **defaults), root


# ---------- T1 上下文串入检测 ----------
def t1_context_detectors():
    print("\n--- T1 上下文串入检测 ---")
    good = "我还没准备好。"
    bad_label = ("前一句：你终于接近了终点。你接下来要与一位熟练的潜行者"
                 "战斗。没错，他独自一人，但他穿着外骨骼装甲，还拿着 FN "
                 "F2000 步枪。如果你被击中，那你的下场就是死路一条。等待你"
                 "的将是一个惊喜，关于这个我无法告诉你，但我的")
    check("T1-正常译文不误判", not ait.looks_like_context_echo(good))
    check("T1-前一句标签命中", ait.looks_like_context_echo(bad_label))
    check("T1-后一句标签命中", ait.looks_like_context_echo("后一句：他来了。"))
    check("T1-上下文锚点命中",
          ait.looks_like_context_echo("上下文（仅供理解，不要翻译）：xxx"))
    check("T1-bleed 短源长译命中", ait.looks_like_context_bleed("Я не готов.", bad_label))
    check("T1-bleed 正常不命中", not ait.looks_like_context_bleed("Я не готов.", "我还没准备好。"))
    long_src = "你终于接近了终点，接下来要与一位熟练的潜行者战斗。" * 5
    clean_long = "你终于接近了终点，接下来要与一位穿戴外骨骼装甲的熟练潜行者展开战斗。" * 5
    check("T1-bleed 长源长译不命中",
          not ait.looks_like_context_bleed(long_src, clean_long))


# ---------- T2 数字保真 ----------
def t2_number_fidelity():
    print("\n--- T2 数字保真 ---")
    check("T2-金额腰斩被抓", "30000" in ait.missing_big_numbers(
        "30000 рублей", "15,000卢布"))
    check("T2-千分位保真放行", not ait.missing_big_numbers(
        "30000 рублей", "30,000卢布"))
    check("T2-中文3万放行", not ait.missing_big_numbers(
        "30000 рублей", "3万卢布"))
    check("T2-一万五千放行", not ait.missing_big_numbers(
        "15000 рублей", "一万五千卢布"))
    check("T2-1.5万放行", not ait.missing_big_numbers(
        "15000 рублей", "1.5万卢布"))
    check("T2-年份放行", not ait.missing_big_numbers(
        "в 2006 году", "在2006年"))
    check("T2-无大额数字放行", not ait.missing_big_numbers(
        "Я не готов.", "我还没准备好。"))
    check("T2-坐标Multi保真", not ait.missing_big_numbers(
        " patented 1987 and 2006 in sector 5", "1987年与2006年，5号区"))


# ---------- T3 词库直取 ----------
def t3_termbase_lookup():
    print("\n--- T3 词库直取与译名修正 ---")
    checks = {
        "Крысолов": "捕鼠人", "Борова": "博罗夫", "Гном": "格诺姆",
        "Корефан": "科雷凡", "Корефан Борова": "科雷凡·博罗夫",
        "Калугин": "卡卢金", "П.И. Калугин": "P.I.卡卢金",
        "Гусаров": "古萨罗夫", "Демон": "恶魔", "Тёмыч": "焦梅奇",
        "Цепа": "采帕", "100 Рентген": "100伦琴",
        "Юбилейный": "周年纪念", "Х-8": "X-8", "ХолодМаш": "冷机",
        "БАРМЕН": "酒保",
    }
    for w, want in checks.items():
        got = ait.termbase_lookup(w)
        check("T3-%s" % w, got == want, "got=%r want=%r" % (got, want))
    # exact 版本带首尾引号去重
    check("T3-exact 引号", ait.termbase_exact_translation('"БАРМЕН"') == "酒保",
          repr(ait.termbase_exact_translation('"БАРМЕН"')))


# ---------- T4 专名保护修正 ----------
def t4_proper_guard():
    print("\n--- T4 专名保护规则 ---")
    # 西里尔全大写短名不再自动保护
    check("T4-西里尔全大写不保护", not ait.looks_like_proper_text(
        "configs/text/dialogs/stable_dialogs.xml", "БАРМЕН"))
    check("T4-拉丁型号仍保护", ait.looks_like_proper_text(
        "configs/text/dialogs/stable_dialogs.xml", "NVIDIA DLSS"))
    check("T4-拉丁缩写仍保护", ait.looks_like_proper_text(
        "configs/text/dialogs/stable_dialogs.xml", "AK-74"))


# ---------- T5 _translate_one 串入重试 ----------
def t5_bleed_retry():
    print("\n--- T5 串入去上下文重试 ---")
    src = ("你终于接近了终点。你接下来要与一位熟练的潜行者战斗。"
           "没错，他独自一人，但他穿着外骨骼装甲，还拿着 FN F2000 步枪。")
    polluted = "前一句：" + src + "后续内容" * 30
    srv = ScriptedServer([polluted, "你终于接近了终点，接下来要与熟练的潜行者战斗。"])
    job, _ = make_job(srv)
    out = job._translate_one(src, "rus", context="前一句：某条源文", style="dialogue")
    check("T5-至少重试1次", srv.n >= 2, "n=%d" % srv.n)
    check("T5-首次带上下文", bool(srv.contexts[0]), repr(srv.contexts[0])[:40])
    check("T5-重试已清上下文", srv.contexts[1] in (None, ""), repr(srv.contexts[1]))
    check("T5-未串入", not ait.looks_like_context_echo(out), out[:50])

    # 重试仍污染 → 保留原文
    srv2 = ScriptedServer([polluted, "后一句：又是污染译文"])
    job2, _ = make_job(srv2)
    out2 = job2._translate_one(src, "rus", context="前一句：某条源文")
    check("T5-仍污染保留原文", out2 == src, out2[:50])


# ---------- T6 极短条目源头减险 ----------
def t6_short_no_context():
    print("\n--- T6 极短条目不注入上下文 ---")
    srv = ScriptedServer(["我还没准备好。"])
    job, _ = make_job(srv)
    job._translate_one("Я не готов.", "rus", context="前一句：某条源文")
    check("T6-极短条目无上下文", srv.contexts[0] in (None, ""), repr(srv.contexts[0]))

    srv2 = ScriptedServer(["译文。"])
    job2, _ = make_job(srv2)
    job2._translate_one("这是一段足够长的俄文源文本用于消歧目的。", "rus",
                        context="前一句：某条源文")
    check("T6-长条目保留上下文", bool(srv2.contexts[0]), repr(srv2.contexts[0]))


# ---------- T7 _translate_one 数字闸门 ----------
def t7_number_gate():
    print("\n--- T7 数字保真闸门 ---")
    src = "Здесь 30000 рублей."
    srv = ScriptedServer(["这里15,000卢布。", "这里30,000卢布。"])
    job, _ = make_job(srv)
    out = job._translate_one(src, "rus")
    check("T7-调用2次", srv.n == 2, "n=%d" % srv.n)
    check("T7-重试带逐字保留要求",
          any("逐字" in (n or "") for n in srv.notes),
          str(srv.notes)[:120])
    check("T7-数字已保真", "30000" in out or "30,000" in out, out)

    srv2 = ScriptedServer(["这里15,000卢布。", "这里15,000卢布。"])
    job2, _ = make_job(srv2)
    out2 = job2._translate_one(src, "rus")
    # ★ 1.9.12 行为变更：数字两次重试仍错时不再整段保留俄文原文，
    # 而是补缺失数字保留中文译文（patch_missing_numbers）。
    check("T7-补数字保留中文译文",
          "15,000" in out2 and "30000" in out2 and "这里" in out2, out2)


# ---------- T8 词库条目数一致性 ----------
def t8_meta():
    print("\n--- T8 词库 meta 一致性 ---")
    d = json.load(open(os.path.join(_ASSET_SRC, "stalker_termbase.json"),
                       encoding="utf-8"))
    actual = sum(len(v) for k, v in d.items()
                 if k != "_meta" and isinstance(v, dict))
    check("T8-total_entries 正确", d["_meta"]["total_entries"] == actual,
          "%d vs %d" % (d["_meta"]["total_entries"], actual))
    for cat, cnt in d["_meta"]["categories"].items():
        check("T8-分类计数 %s" % cat, cnt == len(d.get(cat, {})),
              "%d vs %d" % (cnt, len(d.get(cat, {}))))
    b = json.load(open(os.path.join(_ASSET_SRC, "glossary_builtin.json"),
                       encoding="utf-8"))
    check("T8-两表条目数相等", len(b) == actual, "%d vs %d" % (len(b), actual))


# ---------- T9 无术语强制路径的 note 透传 ----------
def t9_note_passthrough_no_term():
    print("\n--- T9 无术语强制路径 note 透传 ---")
    # 数字/占位符/换行闸门的重试指令全靠 note；强制术语模式若整条没命中
    # 术语，必须仍把 note 传到引擎，否则重试永远无效、条目被保留成原文。
    srv = ScriptedServer(["能给我一把 AK-103 吗？"])
    job, _ = make_job(srv, force_glossary=True,
                      glossary={"Крысолов": "捕鼠人"})
    note = "原文数字必须逐字保留"
    out = job._translate_plain("Can you get me an AK-103?", "eng", note=note)
    check("T9-无术语命中仍透传 note",
          any(note == x for x in srv.notes), str(srv.notes))
    check("T9-译文正常返回", bool(out), out)


def main():
    t1_context_detectors()
    t2_number_fidelity()
    t3_termbase_lookup()
    t4_proper_guard()
    t5_bleed_retry()
    t6_short_no_context()
    t7_number_gate()
    t8_meta()
    t9_note_passthrough_no_term()
    ok = sum(1 for _, r, _ in RESULTS if r)
    print("\n==== %d/%d 通过 ====" % (ok, len(RESULTS)))
    if ok != len(RESULTS):
        print("失败项：")
        for name, r, detail in RESULTS:
            if not r:
                print("  - %s | %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
