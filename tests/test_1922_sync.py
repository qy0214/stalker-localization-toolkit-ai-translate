# -*- coding: utf-8 -*-
"""1.9.22 回归：同步 2.x 2.0.25（aera/CHS 全量对照驱动的判定链与数据修复）。

覆盖：_id_player_visible 豁免（ident 假跳过 2206 条）、reconcile 0→≤2
收敛（lit_nl 11 条）、_translate_force 长文跳过占位符法（D12 签名①）、
上下文串入切片级兜底（D12 签名②）、人工对照层词库命中（神器英文键/
弹药状态/单名）、口语化提示词锚例。
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
夹具注意：长文/串入用例按 2.0.25 测试同款修正——长文源文必须含词库
命中（否则 spans=0 根本不进 _translate_force，断言假通过）；串入用例
按请求形态分派（带上下文主请求/整条无上下文重译/切片片段），否则
主翻译直接拿到好译文、三级路径没有执行。
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

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait1922_local_")
_TMP = tempfile.mkdtemp(prefix="ait1922_plugin_")
_PDIR = os.path.join(_TMP, "plugins")
os.makedirs(_PDIR, exist_ok=True)
shutil.copy2(PLUGIN, os.path.join(_PDIR, "ai_translate.py"))
if os.path.isdir(_ASSET_SRC):
    shutil.copytree(_ASSET_SRC, os.path.join(_PDIR, "ai_translate_assets"),
                    dirs_exist_ok=True)
PLUGIN = os.path.join(_PDIR, "ai_translate.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait1922", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def __init__(self, script=None):
        self.script = script or {}

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        for key, val in self.script.items():
            if key and key in str(text):
                return val() if callable(val) else val
        if "" in self.script:
            val = self.script[""]
            return val() if callable(val) else val
        return text

    def translate_batch(self, lines, src, tgt, **kw):
        return "\n".join("[%d] %s" % (i + 1, t)
                         for i, t in enumerate(lines))


def make_job(srv, **kw):
    root = tempfile.mkdtemp(prefix="ait1922_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind)
    os.makedirs(out)
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=False, copy_reference=False,
                    protect_proper=True, auto_session_terms=False,
                    tm_lite=False, force_glossary=False, glossary={})
    defaults.update(kw)
    job = ait.TranslationJob(ait._load_vendor(), srv, ind, "auto",
                             "auto", "chs", **defaults)
    job._tmp_root = root
    return job


# ── 1 版本 ──────────────────────────────────────────────────────
check("version:1.9.22+", ait.PLUGIN_INFO["version"] in ("1.9.22", "1.9.23"),
      ait.PLUGIN_INFO["version"])

# ── 2 id 形态豁免（P3/P5）──────────────────────────────────────
check("豁免-_name 后缀", ait._id_player_visible("st_itm_upgr_flash_disk_alloy_name"))
check("豁免-_descr 后缀", ait._id_player_visible("st_af_camelbak_descr"))
check("豁免-_text 后缀", ait._id_player_visible("encyclopedia_locations_Bar_text"))
check("豁免-task_name 后缀", ait._id_player_visible("drx_sl_baraholka_trader_meet_task_1_name"))
check("不豁免-纯标识 id", not ait._id_player_visible("st_dyn_news_comm_dolg_5"))
check("不豁免-弹药状态 id", not ait._id_player_visible("ammo-12x70-buck_bad"))
check("不豁免-空 id", not ait._id_player_visible(""))

# ── 3 reconcile 0→≤2 收敛（lit_nl）────────────────────────────
extra, fixed = ait.reconcile_literal_newlines("源无换行", "译A\\n译B")
check("收敛-两个多余\\n变空格",
      fixed and ait.count_literal_newlines(extra) == 0 and "译A 译B" == extra,
      repr(extra))
extra2, fixed2 = ait.reconcile_literal_newlines("源无换行", "译A\\n译B\\n译C\\n译D")
check("收敛-超过两个不处理", not fixed2)
same, fixed3 = ait.reconcile_literal_newlines("带\\n源文", "带\\n源文译")
check("收敛-数量一致不动", not fixed3)

# ── 4 人工对照层词库命中（P1/P2/P4）───────────────────────────
for src, want in (
        ("«Spike»", "尖刺"),
        ("«Cocoon»", "茧"),
        ("12x70 Buckshot (wet)", "12x70 霰弹（受潮的）"),
        ("12.7x108mm FMJ Rounds (bad)", "12.7x108mm FMJ 弹药（劣质的）"),
        ("Flash drive with alloys data", "载有合金数据的闪存盘"),
        ("Magpie", "喜鹊"),
        ("Vicarius", "维卡里乌斯"),
        ("Eugene Paton", "尤金·帕通"),
):
    got = ait.termbase_exact_translation(src)
    check("词库 %r" % src, got == want, got)
check("词库-人工对照层挂 names",
      "人工对照" in ait.load_termbase_data()["_meta"]["layers"]["names"])

# ── 5 长文多术语跳过占位符法（D12 签名①）──────────────────────
long_text = ("General information about the Whirligig anomaly. " * 18)
seen_calls = []


class CapServer(FakeServer):
    def translate(self, text, src, tgt, **kw):
        seen_calls.append(str(text))
        return "（普通整译产出）" + str(text)[:20]


job = make_job(CapServer(), force_glossary=True,
               glossary={"Whirligig": "旋转木马"})
check("长文-词库命中就位", bool(job._glossary_spans(long_text)),
      str(len(job._glossary_spans(long_text))))
out = job._translate_plain(long_text, "eng")
check("长文-不走 $$T$$ 占位符",
      seen_calls and all("$$T" not in c for c in seen_calls),
      str(seen_calls[0])[:60] if seen_calls else "无请求")
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 6 上下文串入→切片兜底（D12 签名②）────────────────────────
SRC6 = ("Short clean sentence about the zone and its anomalies. " * 4)


class BleedServer(FakeServer):
    def translate(self, text, src, tgt, **kw):
        if kw.get("context"):
            return "前一句：上一条对话的串入译文"          # 主请求
        if str(text).strip() == SRC6.strip():
            return "前一句：去上下文重译仍串入"            # 整条无上下文
        return "正常的好译文。"                            # 切片片段


job = make_job(BleedServer())
out = job._translate_one(SRC6, "eng",
                         context="前一句：上一条对话", style="dialogue")
check("上下文-串入后切片救回",
      "前一句" not in str(out) and "正常的好译文" in str(out),
      str(out)[:60])
shutil.rmtree(job._tmp_root, ignore_errors=True)

# ── 7 提示词锚例（P6）─────────────────────────────────────────
check("提示词-描述文本锚例存在",
      "低剂量辐射" in ait.TRANSLATION_SYS_PROMPT_EN
      and "概不负责" in ait.TRANSLATION_SYS_PROMPT)

# ── 汇总 ──────────────────────────────────────────────────────
fails = [n for n, ok, _ in RESULTS if not ok]
print("\n%d/%d 通过" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("失败:", fails)
    sys.exit(1)
