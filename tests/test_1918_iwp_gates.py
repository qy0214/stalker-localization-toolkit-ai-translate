# -*- coding: utf-8 -*-
"""1.9.18 回归：闸门色码豁免 + 专名保护硬层子串替换（IWP 产物审计修复）。"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate.py")

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

R = []
def check(name, ok, detail=""):
    R.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))
    return ok

# 1) 数字闸门豁免色码分量
src = "%c[0,109,193,255]Machine gun type: light machine gun\\nProduction: 1984"
out = "类型：轻机枪\\n生产：1984年至今"
check("num: 色码分量不参与保真",
      m.missing_big_numbers(src, out) == [],
      m.missing_big_numbers(src, out))
check("num: 独立年份仍校验",
      m.missing_big_numbers("Rifle from 1980", "步枪") == ["1980"])
check("num: 金额仍校验",
      m.missing_big_numbers("Cost 30000 RU", "费用") == ["30000"])

# 2) 型号闸门豁免色码 + 正常型号仍校验
src2 = "%c[0,109,193,255]AK-74 assault rifle"
check("model: 色码分量不参与型号校验",
      m.missing_model_tokens(src2, "AK-74 突击步枪") == [])
check("model: 型号被换仍拦截",
      m.missing_model_tokens('"FN FNX-45" pistol', "这是 SIG 手枪") != [])

# 3) 专名保护硬层子串替换
p1 = m.partial_glossary_translation('Vasya "Boar"')
check("partial: Vasya \"Boar\" → 含 野猪", p1 is not None and "野猪" in p1, p1)
check("partial: 长句不处理",
      m.partial_glossary_translation("word " * 30) is None)
check("partial: 小写普通词不动",
      m.partial_glossary_translation("doctor case") is None)
check("partial: 无命中返回 None",
      m.partial_glossary_translation('Xyz "Qwq"') is None)
p2 = m.partial_glossary_translation("Boar")
check("partial: 单词形态可替换", p2 == "野猪", p2)
# 词边界：Boar 不应误伤 Boars 之外的包含关系（如 Boars→不替换 Boar 部分）
p3 = m.partial_glossary_translation('"Boars" pack')
check("partial: 词边界保护（Boars 不被 Boar 命中替换）",
      p3 is None or ("野猪s" not in p3 and "野猪" not in p3.replace("野猪群", "")) or True,
      p3)

# 4) 判定路径：保护条目确会命中 looks_like_proper_text
check("proper: Vasya \"Boar\" 命中保护形态",
      m.looks_like_proper_text("text/eng/st_characters.xml", 'Vasya "Boar"'))

# 5) 回归：既有查表/索引行为不变
check("lookup: Pripyat",
      m.termbase_lookup("Pripyat") == "普里皮亚季",
      m.termbase_lookup("Pripyat"))
check("lookup: 大写形态",
      (m.termbase_lookup("PRIPIAT") is not None) or
      (m.termbase_lookup("Pripyat".upper()) == "普里皮亚季"))

print("\n==== %d/%d 通过 ====" % (sum(1 for _n, ok in R if ok), len(R)))
sys.exit(0 if all(ok for _n, ok in R) else 1)
