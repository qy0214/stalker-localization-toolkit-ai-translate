# -*- coding: utf-8 -*-
"""1.9.20 回归：选项收编后的两个关键行为（对齐 2.x 2.0.23）。

  T1 引用层无文本表自动回退：reference_mode 开、MOD 里没有 configs/text
     （纯脚本/ltx MOD）时，run() 不再「警告后终止」，而是回退整目录
     直接翻译模式并正常完成——引用层收编为固定模式后的配套安全网。
  T2 回退时 only_referenced 强制关闭：引用索引为空，若开着「只译被引用
     条目」会把所有条目过滤成 0，回退必须自动关闭并点名。

用法: python tests/test_1920_options_ui.py [插件路径]
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
"""
import importlib.util
import os
import shutil
import sys
sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate.py"))
_ASSET_SRC = os.path.join(ROOT, "plugins", "ai_translate_assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait1920_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait1920_plugin_")
_PDIR = os.path.join(_TMP, "plugins")
os.makedirs(_PDIR, exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_PDIR, "ai_translate.py"))
if os.path.isdir(_ASSET_SRC):
    shutil.copytree(_ASSET_SRC, os.path.join(_PDIR, "ai_translate_assets"),
                    dirs_exist_ok=True)
PLUGIN = os.path.join(_PDIR, "ai_translate.py")
sys.path.insert(0, _PDIR)

spec = importlib.util.spec_from_file_location("ait1920", PLUGIN)
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))


class FakeServer:
    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kw):
        return "[译]" + text[:20]

    def translate_batch(self, lines, src, tgt, **kw):
        return "\n".join("[%d] [译]%s" % (i + 1, t)
                         for i, t in enumerate(lines))


def make_job(root, **kw):
    """in_dir 只有非文本表文件（无 configs/text），触发引用层回退。"""
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(ind, "strings.ltx"), "w",
              encoding="cp1251", newline="\n") as f:
        f.write("[description]\ndescription = Survival handbook for stalkers\n")
    defaults = dict(out_dir=out, log=lambda *a, **k: None,
                    reference_mode=True, copy_reference=True,
                    protect_proper=True, auto_session_terms=False,
                    tm_lite=False, force_glossary=False)
    defaults.update(kw)
    return ait.TranslationJob(ait._load_vendor(), FakeServer(),
                              ind, "auto", "auto", "chs", **defaults)


def t1_fallback():
    print("\n--- T1 引用层无文本表 → 回退整目录模式 ---")
    root = tempfile.mkdtemp(prefix="ait1920_fb_")
    try:
        logs = []
        job = make_job(root, log=lambda m, tag="info": logs.append(str(m)))
        ok = job.run()
        joined = "\n".join(logs)
        check("T1-run 正常完成", ok is True, joined[:200])
        check("T1-回退日志出现", "回退：按整目录直接翻译模式继续" in joined,
              joined[:300])
        check("T1-不再出现「无法继续」终止口径",
              "引用层模式无法继续" not in joined, joined[:300])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def t2_fallback_disables_only_ref():
    print("\n--- T2 回退时 only_referenced 自动关闭 ---")
    root = tempfile.mkdtemp(prefix="ait1920_or_")
    try:
        logs = []
        job = make_job(root, only_referenced=True,
                       log=lambda m, tag="info": logs.append(str(m)))
        ok = job.run()
        joined = "\n".join(logs)
        check("T2-run 正常完成", ok is True, joined[:200])
        check("T2-点名「只译被引用条目」不生效",
              "「只译被引用条目」在回退模式下不生效" in joined, joined[:300])
        check("T2-实例标志被复位", job.only_referenced is False,
              str(job.only_referenced))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    t1_fallback()
    t2_fallback_disables_only_ref()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n总计 %d 项：通过 %d，失败 %d" %
          (len(RESULTS), passed, len(RESULTS) - passed))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
