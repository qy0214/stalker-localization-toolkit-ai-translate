# -*- coding: utf-8 -*-
"""1.9.21 回归：同步 2.x 2.0.24（IWP 全量审计驱动的六项修复）。

覆盖：粘连残词判定口径、年代/型号豁免、注记退役与双注记、
CJK 词间空格收敛、未解析报告下划线判据、批量路径五态
（掩码丢弃/错位丢弃/粘连丢弃/数字丢弃/成功回填）、单条路径
粘连重试/定名兜底/型号重试与裸型号兜底。
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
"""
import importlib.util
import os
import shutil
import sys
sys.dont_write_bytecode = True
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate.py")
_ASSET_SRC = os.path.join(ROOT, "plugins", "ai_translate_assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait1921_local_")
_TMP = tempfile.mkdtemp(prefix="ait1921_plugin_")
_PDIR = os.path.join(_TMP, "plugins")
os.makedirs(_PDIR, exist_ok=True)
shutil.copy2(PLUGIN, os.path.join(_PDIR, "ai_translate.py"))
if os.path.isdir(_ASSET_SRC):
    shutil.copytree(_ASSET_SRC, os.path.join(_PDIR, "ai_translate_assets"),
                    dirs_exist_ok=True)
PLUGIN = os.path.join(_PDIR, "ai_translate.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait1921", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def __init__(self, script=None, batch_lines=None):
        self.script = script or {}
        self.batch_lines = batch_lines

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        note = kw.get("extra_sys") or ""
        for key, val in self.script.items():
            if key and key in note:
                return val() if callable(val) else val
        if "" in self.script:
            val = self.script[""]
            return val() if callable(val) else val
        return text

    def translate_batch(self, lines, src, tgt, **kw):
        if self.batch_lines is not None:
            return self.batch_lines
        return "\n".join("[%d] %s" % (i + 1, t)
                         for i, t in enumerate(lines))


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait1921_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind)
    os.makedirs(out)
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=False, copy_reference=False,
                    protect_proper=True, auto_session_terms=False,
                    tm_lite=False, force_glossary=False)
    defaults.update(kw)
    job = ait.TranslationJob(ait._load_vendor(), srv, ind, "auto",
                             "auto", "chs", **defaults)
    job._tmp_root = root
    return job


# ── 1 版本 ──────────────────────────────────────────────────────
check("version:1.9.21+",
      ait.PLUGIN_INFO["version"] in ("1.9.21", "1.9.22", "1.9.23"),
      ait.PLUGIN_INFO["version"])

# ── 2 粘连残词判定口径 ─────────────────────────────────────────
POS = ["克里门enko队长", "Tkachenko将军", "$clr_2We正看到一",
       "Gathering人前往地下墓穴", "箱子-kit在那儿",
       "Brain Scorcher已被关闭", "通往Dark Valley的道路",
       "只要不是Exo材料就行", "AS Val消音自动步枪"]
NEG = ["$target正在$map准备设下埋伏。", "AK-103可以安装多种瞄准具。",
       "这种NATO M855 5.56x45mm弹壳采用4克重的子弹。",
       "%c[255,216,186,140]“已启用”", "PC机、T-34坦克、AK-74M、SVD",
       "5.45x39mm弹、9x39mm SP-6弹", "125g药剂与200ml水",
       "Heckler & Koch USP", "S.C.H.O.L.A.R.协议",
       "Raging Bull%c[0,140,140,140]——一种转轮手枪",
       "%c[ui_orange]大家注意！$start $mid. $end."]
bad = [s for s in POS if not ait.find_glue_residue(s)]
fp = [s for s in NEG if ait.find_glue_residue(s)]
check("粘连-真阳性全命中", not bad, bad)
check("粘连-合法样本零误报", not fp, fp)

# ── 3 年代/型号豁免 ────────────────────────────────────────────
check("年代-「20世纪60年代」认可 1960s",
      ait.missing_big_numbers("It is a 1960s launcher.",
                              "这是一款20世纪60年代的发射器。") == [])
check("年代-「1990年代」认可 1990s",
      ait.missing_big_numbers("Developed in the 1990s.",
                              "研发于1990年代中期。") == [])
check("年代-真缺失仍拦",
      "1960" in ait.missing_big_numbers("A 1960s launcher.",
                                        "一款老式发射器。"))
check("型号-「mid-1990s」不算型号",
      "mid-1990s" not in ait.missing_model_tokens(
          "Designed in mid-1990s by the rifle team.",
          "由步枪团队在1990年代中期设计。"))
check("型号-引号型号缺失仍拦",
      "AK-100" in ait.missing_model_tokens(
          "A derivative of the \"AK-100\" series.",
          "这是某系列的衍生型。"))

# ── 4 注记退役与双注记 ─────────────────────────────────────────
patched = ait.patch_missing_numbers("The price is 30000 griva.",
                                    "报酬是 15000 卢布。")
check("注记-补裸数字", "（30000）" in (patched or ""), patched)
check("注记-不再出现「原文」字样",
      patched is None or "原文" not in patched, patched)

# ── 5 CJK 词间空格收敛 ─────────────────────────────────────────
n, _ = ait.normalize_cn_punctuation("在异常活动强烈的 禁区 环境中执行任务。")
check("标点-汉字间空格收敛", n == "在异常活动强烈的禁区环境中执行任务。", n)
n2, _ = ait.normalize_cn_punctuation("$start $mid. 距离 12.3 米。")
check("标点-变量/数字空格保留", n2 == "$start $mid. 距离 12.3 米。", n2)

# ── 6 未解析报告口径 ───────────────────────────────────────────
J = ait.TranslationJob
check("报告-ammo 不再算未解析引用", J._looks_like_ref_value("ammo") is False)
check("报告-OK 不再算未解析引用", J._looks_like_ref_value("OK") is False)
check("报告-GENERATE_NAME_stalker 仍报",
      J._looks_like_ref_value("GENERATE_NAME_stalker") is True)
check("报告-btn_credits 仍报",
      J._looks_like_ref_value("btn_credits") is True)

# ── 7 批量路径五态 ─────────────────────────────────────────────
B = [{"src": "Set the value %c[default]before start.", "lang": "eng",
      "id": "b1", "rel": "configs/text/eng/x.xml"},
     {"src": "A second distinct line.", "lang": "eng",
      "id": "b2", "rel": "configs/text/eng/x.xml"}]

srv = FakeServer(batch_lines="[1] 开始游戏前设置好[默认]数值。\n"
                             "[2] 第二行。")
job = make_job(srv)
check("批量-掩码被译坏→整批丢弃",
      job._translate_dialogue_batch(B) == {})
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(batch_lines="[1] 在开始新游戏之前设置所需的数值，"
                             "由于行为异常，该选项在游戏过程中不可用。\n"
                             "[2] 在开始新游戏之前设置所需的数值，"
                             "由于行为异常，该选项在游戏过程中不可用。")
job = make_job(srv)
check("批量-不同源文同长译文→判错位丢弃",
      job._translate_dialogue_batch(B) == {})
C = [{"src": "Yes, sir.", "lang": "eng", "id": "c1",
      "rel": "configs/text/eng/x.xml"},
     {"src": "Affirmative.", "lang": "eng", "id": "c2",
      "rel": "configs/text/eng/x.xml"}]
srv = FakeServer(batch_lines="[1] 是的。\n[2] 好的。")
job = make_job(srv)
check("批量-短应答同义不同词→不误杀",
      len(job._translate_dialogue_batch(C)) == 2)
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(batch_lines="[1] 克里门enko队长说。\n[2] 第二行。")
job = make_job(srv)
check("批量-粘连残词→丢弃交单条",
      job._translate_dialogue_batch(B) == {})
shutil.rmtree(job._tmp_root, ignore_errors=True)

B2 = [{"src": "Pay 30000 griva.", "lang": "eng", "id": "b1",
       "rel": "configs/text/eng/x.xml"},
      {"src": "A second distinct line.", "lang": "eng", "id": "b2",
       "rel": "configs/text/eng/x.xml"}]
srv = FakeServer(batch_lines="[1] 支付 15000 卢布。\n[2] 第二行。")
job = make_job(srv)
check("批量-大额数字被改→丢弃交单条",
      job._translate_dialogue_batch(B2) == {})
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(batch_lines="[1] 开始游戏前设置好$$R0$$数值。\n[2] 第二行。")
job = make_job(srv)
got = job._translate_dialogue_batch(B)
check("批量-掩码原样保留→回填成功",
      len(got) == 2 and "%c[default]" in got["b1"][1], got)
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 8 单条路径重试链 ───────────────────────────────────────────
srv = FakeServer(script={"": "克里门enko队长。", "粘连": "克里门科队长。"})
job = make_job(srv)
out = job._translate_one("Captain Kremenko spoke.", "eng",
                         style="dialogue")
check("单条-粘连→整词音译重试成功",
      out == "克里门科队长。" and "enko" not in out, out)
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(script={"中文译名": "破布与骨"})
job = make_job(srv)
out = job._translate_one("Rag and Bone", "eng", style="label")
check("单条-短名回显→定名指令兜底", out == "破布与骨", out)
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(script={"": "这是一款衍生步枪，做工一般。",
                         "型号": "这是一款 AK-103 衍生步枪。"})
job = make_job(srv)
out = job._translate_one('A derivative of the "AK-103" rifle.', "eng",
                         style="dialogue")
check("单条-型号被吞→定向重试找回",
      "AK-103" in out and "（AK-103）" not in out, out)
shutil.rmtree(job._tmp_root, ignore_errors=True)

srv = FakeServer(script={"": "这是一款无托步枪，做工精良。"})
job = make_job(srv)
out = job._translate_one('The "AK-103" rifle is good.', "eng",
                         style="dialogue")
check("单条-重试仍失败→裸型号兜底无「原文」字样",
      "（AK-103）" in out and "原文" not in out, out)
check("单条-型号数字不双注记（无裸「（103）」）", "（103）" not in out, out)
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 汇总 ──────────────────────────────────────────────────────
fails = [n for n, ok, _ in RESULTS if not ok]
print("\n%d/%d 通过" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("失败:", fails)
    sys.exit(1)
