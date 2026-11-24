# -*- coding: utf-8 -*-
"""1.9.19 回归：同步 2.0.22 修复（软层退役 / 子串替换加固 / 型号直取 /
藏宝点与 ogg 豁免 / 词库内容级修正）+ 本仓库专属的数据根兼容式闸门。

证据与量化结果在 2.x 姊妹仓库 `audit-reports/`（三轮随机文件测试 678 文件、
全 MOD 全文件测试 1600 文件 / 24 万条目、全量审计），本仓库只固化行为断言。
"""
import importlib.util
import json
import os
import sys

sys.dont_write_bytecode = True   # 别把 .pyc 写进随包插件目录（门禁 A6 会判为运行产物）

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate.py")
ASSETS = os.path.join(ROOT, "plugins", "ai_translate_assets")

spec = importlib.util.spec_from_file_location("m", PLUGIN)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

R = []
def check(name, ok, detail=""):
    R.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + str(detail)) if detail else ""))
    return ok

# ── 1 版本 ──────────────────────────────────────────────────────
check("version:1.9.19+",
      m.PLUGIN_INFO["version"] in ("1.9.19", "1.9.20", "1.9.21", "1.9.22", "1.9.23", "1.9.24", "1.9.25"),
      m.PLUGIN_INFO["version"])

# ── 2 软层退役 ──────────────────────────────────────────────────
tb = json.load(open(os.path.join(ASSETS, "stalker_termbase.json"),
                    encoding="utf-8"))
gb = json.load(open(os.path.join(ASSETS, "glossary_builtin.json"),
                    encoding="utf-8"))
soft = ("GAMMA 参考", "Gunslinger 参考")
check("软层分类已移出词库", not any(c in tb for c in soft),
      [c for c in soft if c in tb])
check("_meta.layers.reference 不含软层",
      not any(c in tb["_meta"]["layers"]["reference"] for c in soft),
      tb["_meta"]["layers"]["reference"])
check("_DEFAULT_LAYERS.reference 不含软层",
      not any(c in m._DEFAULT_LAYERS["reference"] for c in soft),
      m._DEFAULT_LAYERS["reference"])
for src in ("HK USP Match", "Вставить",
            ".45 Hydra-shock", "Остров валунов", "HK G36", "FN P90"):
    check("直取不再命中软层误译 %r" % src,
          m.termbase_lookup(src) is None and src not in gb,
          m.termbase_lookup(src))
# ★ 1.9.22（同步 2.0.25）：'Find %s for Cardan' 被 CHS 人工对照收割
# （译名"为卡丹找到%s"不丢信息，优于旧软层误译"寻找%s"）——从"必须
# None"清单移到人工对照命中断言。
check("人工对照命中 Find %s for Cardan",
      m.termbase_lookup("Find %s for Cardan") == "为卡丹找到%s",
      m.termbase_lookup("Find %s for Cardan"))
check("垃圾键 '.' 已不存在", m.termbase_lookup(".") is None and "." not in gb)
check("垃圾键 '...' 已删除", m.termbase_lookup("...") is None)

# ── 3 词库计数自洽 + 内容级修正 ─────────────────────────────────
n_cat = sum(len(v) for k, v in tb.items()
            if not k.startswith("_") and isinstance(v, dict))
check("_meta.total_entries == 分类求和",
      tb["_meta"]["total_entries"] == n_cat,
      "%s / %d" % (tb["_meta"]["total_entries"], n_cat))
# ★ 1.9.22（同步 2.0.25）：随包表允许混合结构（扁平条目 + 分类嵌套
# dict + _meta），条目数按插件 flatten_glossary 摊平语义统计。
check("扁平镜像条数 == 分类求和",
      len(m.flatten_glossary(gb)) == n_cat,
      "%d / %d" % (len(m.flatten_glossary(gb)), n_cat))
for k, want in (("Лейтенант «Аспирин» Сострадов", "“阿司匹林”索斯特拉多夫中尉"),
                ("Осведомителю", "线人"), ("Енот", "浣熊"),
                ("Mangun", "曼冈"), ("Учёный «О-Сознания»", "“共同意识”科学家"),
                ("Установка экранирующего покрытия.", "施加屏蔽涂层"),
                ("Установка экранирующего покрытия", "施加屏蔽涂层")):
    check("词库修正 %r" % k[:28], m.termbase_lookup(k) == want,
          m.termbase_lookup(k))

# ── 4 硬层子串替换加固 ──────────────────────────────────────────
broken = [
    ("stable_game_credits.xml", "Executive Vice President"),
    ("stable_game_credits.xml", "Database Applications Engineer"),
    ("st_characters.xml", 'Senior Sergeant "Mangun" Potryko'),
    ("st_characters.xml", 'Lieutenant "Aspirin" Sostradov'),
    ("st_items_outfit.xml", 'Duty Sphere M12 "Red Line"'),
    ("string_table_general.xml",
     "SOS signal: Somebody help! I'm in a hut in the swamp..."),
    ("mlr_addon.xml", "Following the tracks of Clear Sky: talk with Forester"),
    ("ui_st_mm_mp.xml", "Max players"),
    ("ui_st_secrets.xml", "Case in the fountain"),
]
for rel, s in broken:
    check("破碎替换被拦 %r" % s[:34],
          m.partial_glossary_translation(s, rel) is None,
          m.partial_glossary_translation(s, rel))
for rel, s, want in (("st_characters.xml", 'Vasya "Boar"', "瓦夏"),
                     ("scripts.xml", "FRITZ：", "弗里茨"),
                     ("string_table_enc_weapons.xml", 'AS "Val" PSO-1', "维尔"),
                     ("string_table_enc_weapons.xml", "Browning High-Power",
                      "勃朗宁"),
                     ("string_table_enc_weapons.xml", "SIG SG-550 «Sniper»",
                      "狙击手")):
    got = m.partial_glossary_translation(s, rel)
    check("干净替换保留 %r" % s[:30], got is not None and want in got, got)
check("制作名单硬豁免（Harris 不替换）",
      m.partial_glossary_translation("Harris", "stable_game_credits.xml") is None)
check("非名单文件同串仍可替换",
      m.partial_glossary_translation("Harris", "string_table_enc_weapons.xml")
      == "哈里斯")

# ── 5 型号形态直取 ──────────────────────────────────────────────
for rel, s in (("st_items_weapons.xml", "PPSH-41"),
               ("st_items_weapons.xml", "BT MP9"),
               ("string_table_enc_weapons.xml", "AK-74")):
    check("型号形态判定 %r" % s, m._is_model_code_form(rel, s))
check("PPSH-41 直取有译文",
      m.termbase_exact_translation("PPSH-41") == "PPSH-41冲锋枪",
      m.termbase_exact_translation("PPSH-41"))
for s in ("True", "False", "None", "dead", "Dead", "ч",
          "http://example.com", "© GSC"):
    check("调试词/URL/单字符不属型号形态 %r" % s,
          not m._is_model_code_form("ui_st_pda.xml", s))
check("名字表不适用型号豁免",
      not m._is_model_code_form("character_name/stable_generate_snames.xml",
                                "AK-74"))

# ── 6 藏宝点目录 + ogg 收窄 ─────────────────────────────────────
check("treasure 目录属玩家可见", "treasure" in m._PLAYER_LABEL_DIRS)
check("secrets 目录属玩家可见", "secrets" in m._PLAYER_LABEL_DIRS)
for rel, s in (("st_treasures.xml", "Backpack under tree"),
               ("stable_treasure_manager.xml", "Chest on the roof"),
               ("ui_st_secrets.xml", "Safe under the bush")):
    check("藏宝点名放行送翻 %r" % s, m.is_player_facing_label(rel, s))
OGG = "string_table_ogg_player.xml"
check("ogg 物品长描述放行送翻", not m.looks_like_proper_text(
    OGG, "Влаго-ударостойкий MP3-плеер с радиоприёмником. Целая партия "
         "таких китайских подделок была недавно завезена в Зону."))
check("ogg 含曲目列表仍保护（歌名不可译）", m.looks_like_proper_text(
    OGG, "Флеш-карта с музыкальными треками для MP3-плеера. \\n \\n"
         "%c[255,1,255,255]ТРЕК 01:%c[255,1,255,1] Алиса — Веретено. \\n"
         "%c[255,1,255,255]ТРЕК 02:%c[255,1,255,1] Сплин — Мы сидели."))
check("_TRACK_LIST_RE 命中 ТРЕК/曲目",
      bool(m._TRACK_LIST_RE.search("ТРЕК 01: x"))
      and bool(m._TRACK_LIST_RE.search("曲目 01:x"))
      and not m._TRACK_LIST_RE.search("MP3-плеер с радиоприёмником."))
check("制作名单长句仍整表保护", m.looks_like_proper_text(
    "stable_game_credits.xml",
    "Мы хотели бы выразить огромную признательность следующим Open-source "
    "проектам."))

# ── 7 本仓库既有收口不回归（title 守卫自带 len<=60 + 句末标点）──
for s in ("Connection error: CD-Key already in use.",
          "Stash: blocked entrance to the underground, inside an L-shaped pipe.",
          "It's all simple: clear the farmstead of bandits."):
    check("正文不被判专名 %r" % s[:36],
          not m.looks_like_proper_text("ui_st_other.xml", s))
for rel, s in (("string_table_enc_weapons.xml", "Vano:"),
               ("string_table_enc_weapons.xml", "MP5 — Sanitizer"),
               ("binoc.xml", "NVIDIA DLSS")):
    check("真标题/型号仍受保护 %r" % s, m.looks_like_proper_text(rel, s))

# ── 8 数据根兼容式闸门（1.9.19 专属）────────────────────────────
check("分享包内无运行产物目录",
      not any(os.path.isdir(os.path.join(ASSETS, d))
              for d in ("bin", "models", "logs", "downloads")),
      [d for d in ("bin", "models", "logs", "downloads")
       if os.path.isdir(os.path.join(ASSETS, d))])
check("分享包内无 __pycache__",
      not any("__pycache__" in r for r, _d, _f in os.walk(ASSETS)))
check("无运行数据时数据根落 LOCALAPPDATA",
      m._DATA_ROOT == m._LOCAL_ROOT, m._DATA_ROOT)
check("portable.txt 标记路径在插件同目录",
      m._PORTABLE_MARK == os.path.join(m._HERE, "portable.txt"),
      m._PORTABLE_MARK)
check("_has_runtime_data 对空目录判 False",
      m._has_runtime_data(HERE) is False)

# ── 8b 首尾引号键可达（旧实现只剥输入引号，键自带引号则永远查不到）──
for k, want in (("Учёный «О-Сознания»", "“共同意识”科学家"),
                ("Представитель «О-Сознания»", "“共同意识”代表"),
                ('"Tears of Electra"', "“电之泪”"),
                ('"Monolith" Chief', "黑石首领"),
                ('"Agroprom" factory', "农业研究所")):
    check("带引号键可达 %r" % k[:26], m.termbase_lookup(k) == want,
          m.termbase_lookup(k))
for k, want in (('"Boar"', "野猪"), ("«Бармен»", "酒保")):
    check("剥引号匹配不回归 %r" % k, m.termbase_lookup(k) == want,
          m.termbase_lookup(k))

# ── 9 既有查表行为不回归 ────────────────────────────────────────
check("lookup: Pripyat", m.termbase_lookup("Pripyat") == "普里皮亚季",
      m.termbase_lookup("Pripyat"))
check("lookup: КПК → PDA", m.termbase_lookup("КПК") == "PDA")
check("lookup: 大小写不敏感",
      m.termbase_lookup("PRIPYAT") == "普里皮亚季"
      and m.termbase_lookup("pripyat") == "普里皮亚季")

print("\n==== %d/%d 通过 ====" % (sum(1 for _n, ok in R if ok), len(R)))
sys.exit(0 if all(ok for _n, ok in R) else 1)
