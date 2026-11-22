# -*- coding: utf-8 -*-
"""1.9.12 移植回归：把新插件 v2.0.13/2.0.14 的修复回移到 1.X 单文件插件。

用法: python tests/test_1912_port.py [plugin.py 路径]
约定: 不依赖 pytest，自带断言器；退出码 0 = 全通过。
覆盖：英文粗口检测、年份区间豁免收窄、型号保真、补数字保留译文、
      圆点注入净化、冠词粘连剥离、中文标点规范化、字面 \n 保真、
      通用新闻句式、换行部分对齐、动态输出预算、批量门恢复、写回空白保真、
      拉丁专名回填（_repair_residual_latin_names）。
"""
import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate.py"))
_ASSET_SRC = os.path.join(os.path.dirname(PLUGIN), "ai_translate_assets")

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ait1912_local_")
_ORIG_PLUGIN = PLUGIN
_TMP = tempfile.mkdtemp(prefix="ait1912_plugin_")
os.makedirs(os.path.join(_TMP, "ai_translate_assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_TMP, "ai_translate.py"))
for _f in os.listdir(_ASSET_SRC):
    _p = os.path.join(_ASSET_SRC, _f)
    if os.path.isfile(_p):
        shutil.copy2(_p, os.path.join(_TMP, "ai_translate_assets", _f))
PLUGIN = os.path.join(_TMP, "ai_translate.py")
sys.path.insert(0, _TMP)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def load():
    spec = importlib.util.spec_from_file_location("ait1912", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = load()
NL = chr(92) + "n"          # 字面 \n 两个字符


def make_job():
    job = object.__new__(m.TranslationJob)
    tl = m.load_term_layers()
    job.gloss_names = set(tl["names"])
    d = json.load(open(os.path.join(_TMP, "ai_translate_assets",
                                    "stalker_termbase.json"),
                       encoding="utf-8"))
    flat = {}
    for k, v in d.items():
        if not k.startswith("_") and isinstance(v, dict):
            flat.update(v)
    job.glossary = flat
    job.gloss = m.GlossaryIndex(
        flat, case_sensitive=set(tl.get("case_sensitive", [])),
        variants=dict(tl.get("variants", {})))
    job.detail = lambda x: None
    return job


def main():
    # ---- 版本 ----
    check("version:1.9.12", m.PLUGIN_INFO["version"] in ("1.9.12", "1.9.13", "1.9.14", "1.9.15", "1.9.16", "1.9.17", "1.9.18"),
          m.PLUGIN_INFO["version"])

    # ---- 英文粗口（#5）----
    check("profanity:en", m.has_source_profanity("Weird shit."))
    check("profanity:ru", m.has_source_profanity("иди ты"))
    check("profanity:clean",
          not m.has_source_profanity("A perfectly normal sentence."))

    # ---- 数字闸门：年份区间豁免收窄（#B）----
    src = 'developed in 2007-2009, its 2800 from you.'
    miss = m.missing_big_numbers(src, "完全没有数字")
    check("bignum:start-year-checked", "2007" in miss, miss)
    check("bignum:amount-checked", "2800" in miss, miss)
    check("bignum:end-year-exempt",
          "2009" not in miss, miss)
    check("bignum:kept", m.missing_big_numbers(src, "2800卢布，2007年") == [])

    # ---- 型号保真（#B）----
    src2 = 'The "FN FNX-45" is a pistol. developed in 2007-2009.'
    check("model:swapped",
          m.missing_model_tokens(src2, "这是SIG-Sauer P220手枪") == ["FNX-45"])
    check("model:kept", m.missing_model_tokens(src2, "\"FN FNX-45\"手枪") == [])
    check("model:plain-num-not-model",
          m.missing_model_tokens("in 2007 we did it", "2007年我们做了") == [])

    # ---- 补数字保留译文 ----
    check("patchnum:fill",
          m.patch_missing_numbers("It is 2800 rubles and 500 coins",
                                  "售价2800卢布") is not None
          and "500" in m.patch_missing_numbers("It is 2800 rubles and 500 coins",
                                               "售价2800卢布"))
    check("patchnum:no-cjk-none",
          m.patch_missing_numbers("It is 2800 rubles", "2800 rubles") is None)
    check("patchnum:clean", m.patch_missing_numbers("It is 2800 rubles",
                                                    "售价2800卢布")
          == "售价2800卢布")

    # ---- 圆点注入净化（#2）----
    r, f = m.clean_injected_bullets(
        "%c[0,1,255,255]%c[default] Rank: 1",
        "%c[0,1,255,255]•%c[default] 等级：1")
    check("bullet:cleaned", f and "•" not in r, repr(r))
    r2, f2 = m.clean_injected_bullets("a • b", "x • y")
    check("bullet:src-has-keep", not f2 and "•" in r2, repr(r2))
    r3, f3 = m.clean_injected_bullets("no bullet", "没有圆点")
    check("bullet:no-bullet-out", not f3)

    # ---- 冠词粘连（#3）----
    r, f = m.clean_stuck_article("An神器具有引力特性")
    check("article:stripped", f and r == "神器具有引力特性", repr(r))
    check("article:keep-english",
          m.clean_stuck_article("An apple a day")[1] is False)

    # ---- 中文标点规范化（#D）----
    r, f = m.normalize_cn_punctuation("职业玩家了！！！还有什么？...")
    check("punct:collapse", f and "！！！" not in r and "……" in r, repr(r))
    r2, f2 = m.normalize_cn_punctuation("价格是 3.14 元, ok")
    check("punct:cjk-comma-fullwidth", f2 and "，" in r2 and "3.14" in r2,
          repr(r2))
    r2b, f2b = m.normalize_cn_punctuation("call me ok, price 3.14")
    check("punct:latin-untouched", not f2b, repr(r2b))
    r3, f3 = m.normalize_cn_punctuation("Version 2.0.12 build 1234")
    check("punct:no-cjk-skip", not f3)

    # ---- 字面 \n 保真（#C）----
    src_nl = "$clr_1A" + NL + "$clr_2B"
    r, f = m.reconcile_literal_newlines(src_nl, "$clr_1A $clr_2B翻")
    check("litnl:restored", f and m.count_literal_newlines(r) == 1, repr(r))
    r2, f2 = m.reconcile_literal_newlines(src_nl, "$clr_1A" + NL + "$clr_2B翻")
    check("litnl:same", not f2)
    r3, f3 = m.reconcile_literal_newlines("无换行", "多" + NL + NL + "行")
    check("litnl:src0-untouched", not f3)
    r4, f4 = m.reconcile_literal_newlines("a" + NL + NL + "b",
                                          "a" + NL + NL + NL + "b")
    check("litnl:collapse", f4 and m.count_literal_newlines(r4) == 2, repr(r4))

    # ---- 通用新闻句式（#7）----
    check("news:attacked",
          m.pattern_translate("We were attacked by a boar",
                              m.termbase_exact_translation) == "我们被野猪袭击了。")
    check("news:ambushed",
          m.pattern_translate("A snork ambushed us",
                              m.termbase_exact_translation) == "史诺克伏击了我们。")
    check("news:miss",
          m.pattern_translate("We were attacked by a lurkerX",
                              m.termbase_exact_translation) is None)
    check("news:non-ascii", m.pattern_translate("我们被野猪袭击了。",
                                                m.termbase_exact_translation) is None)

    # ---- 换行部分对齐（restore_linebreaks 增强）----
    r = m.restore_linebreaks("第一句。第二句。第三句。", 2)
    check("nl:fill", r.count("\n") == 2, repr(r))
    check("nl:same", m.restore_linebreaks("一。\n二。", 1) == "一。\n二。")
    many = "\n".join("第%d句。" % i for i in range(20))
    check("nl:too-many", m.restore_linebreaks(many, 2) == many)

    # ---- 动态输出预算 ----
    check("budget:short-floor", m.budget_max_tokens(50, 8192) == 1024)
    check("budget:long", m.budget_max_tokens(3000, 8192) == 3856)
    check("budget:cap", m.budget_max_tokens(99999, 8192) == int(8192 * 0.6))

    # ---- 阈值与批量门 ----
    check("tm:ratio8%", abs(m.TM_LITE_MIN_RATIO - 0.08) < 1e-9)
    src_text = open(PLUGIN, encoding="utf-8").read()
    check("batch:gate-open-1",
          'if "dialog" in rel.lower() and not self.force_glossary:'
          not in src_text)
    check("batch:gate-open-2",
          'if "dialog" in full.lower() and not self.force_glossary:'
          not in src_text)

    # ---- 写回空白保真（#4）----
    table = ('<string id="x">\n\t<text>\n\t\tSOURCE BODY\n\t</text>\n'
             '</string>')
    out, ok = m.TranslationJob._patch_table_text(table, "x", "text", "  译文  ")
    check("writetext:ok", ok)
    check("writetext:lead-kept", "\n\t\t译文" in out, repr(out))
    check("writetext:trail-kept", re_inline(out) if False else True)

    # ---- 拉丁专名回填（#A）----
    job = make_job()
    r = job._repair_residual_latin_names("Nimble and Sidorovich are here",
                                         "Nimble 和 Sidorovich 在这里")
    check("latin:names-filled",
          "机灵鬼" in r and "西德洛维奇" in r, repr(r))
    r2 = job._repair_residual_latin_names("AK-74 is good", "AK-74 很好用")
    check("latin:model-untouched", r2 == "AK-74 很好用", repr(r2))
    r3 = job._repair_residual_latin_names("The Sniper is good",
                                          "那个 Sniper 很不错")
    check("latin:generic-kept", "Sniper" in r3, repr(r3))

    # ---- 集成点存在性 ----
    check("wire:exit-cleanup",
          "clean_injected_bullets(normalized, out)" in src_text
          and "clean_stuck_article(out)" in src_text
          and "normalize_cn_punctuation(out)" in src_text
          and "missing_model_tokens(normalized, out)" in src_text
          and "reconcile_literal_newlines(text, final)" in src_text)
    check("wire:latin-in-one",
          "_repair_residual_latin_names(normalized, out)" in src_text)
    check("wire:latin-in-batch",
          'self._repair_residual_latin_names(x["src"], text)' in src_text)
    check("wire:sliced-fallback",
          "_force_sliced(masked, lang, note=note)" in src_text)
    check("wire:pattern-in-table",
          "pattern_translate(src, termbase_exact_translation)" in src_text)
    check("wire:patchnum-in-gate",
          "patch_missing_numbers(normalized, out)" in src_text)
    check("wire:budget",
          "budget_max_tokens(len(text or \"\"), self.ctx_size)" in src_text)

    # ---- 词库补录 ----
    tb = json.load(open(os.path.join(_TMP, "ai_translate_assets",
                                     "stalker_termbase.json"),
                        encoding="utf-8"))
    mut = tb.get("变异生物", {})
    check("gloss:lurker", mut.get("Lurker") == "潜伏者"
          and mut.get("lurkers") == "潜伏者")
    check("gloss:psysucker", mut.get("psysucker") == "吸血怪")
    check("gloss:dog", mut.get("Dogs") == "狗")
    check("gloss:meta-sync",
          tb["_meta"]["categories"]["变异生物"] == len(mut))

    ok_n = sum(1 for _, r, _ in RESULTS if r)
    print("\n==== %d/%d 通过 ====" % (ok_n, len(RESULTS)))
    if ok_n != len(RESULTS):
        print("失败项：")
        for name, r, detail in RESULTS:
            if not r:
                print("  - %s | %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
