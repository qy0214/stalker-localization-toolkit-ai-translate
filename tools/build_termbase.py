#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 tools / V1.1 目录里所有术语来源汇总成一个分类名词库。

产物 stalker_termbase.json 供 STALKER Localization Toolkit 的
AI 翻译插件直接导入：顶层按分类组织（值为 {原文: 译文} 的分类即被自动摊平），
"_meta" 段以 "_" 开头，导入时跳过。

来源（全部位于本机，零网络/零依赖）：
  raw_pairs.json                 30079 条 ru/en/zh 对齐语料（带 cat 分类投票）
  dict_ns_ogsr_rus-chs.json      4405 条已过滤俄语名词表（已并入旧词表+变格）
  dict_ns_ogsr_eng-chs.json      3721 条英文键对照表
  dict_current_2026-06-21.json   248 条人工旧词表（名词部分已在俄语表中）
  新建 文字文件.txt                人工三语分类表（阵营/地点/怪物/物品/术语）
"""
import codecs
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY = os.path.join(HERE, "dict_current_2026-06-21.json")
OUT = os.path.join(HERE, "stalker_termbase.json")
TXT = os.path.join(HERE, "新建 文字文件.txt")

QUOTES = "\xab\xbb\"\u201c\u201d\u2018\u2019'"
RANK_RE = re.compile(
    r"^(Подполковник|Полковник|Майор|Капитан|Лейтенант|Прапорщик|Старшина|"
    r"Сержант|Генерал|Профессор|Доцент|Полковник|Ст\.|Мл\.)\b", re.I)

# 语料中已证实的错误对齐（常见词/动词被强制成错误译名），整词命中也会误伤，弃用。
GARBAGE = {
    "ru": {"Поговори", "Погоня", "Принеси", "Приноси", "Плати", "Видели",
           "Выходи", "Установлен"},
    "en": {"Suit", "UI", "Main", "Mean", "Cool", "the Shiv"},
}

# 投票资格过滤：只有“定义性”行（短名词译名）才有资格定类，
# 对话/新闻里提到某人某派的长句不参与（与 build_glossary 的判据一致）。
CJK = re.compile(r"[一-鿿]")
CYR = re.compile(r"[Ѐ-ӿ]")
RU_BAD = re.compile(r"[.,!?;:«»\"'()\[\]—–/\\]")
ZH_BAD = re.compile(r"[。！？；，、：…—–\"\"''()\[\]《》（）「」【】]")
TESTISH = re.compile(r"test|тест|demo|示例|测试|占位|todo|fixme", re.I)


def zh_term_like(zh):
    if ZH_BAD.search(zh) or TESTISH.search(zh) or not CJK.search(zh):
        return False
    if len(zh) > 12:
        return False
    return not re.search(r"[的了是在和就都也很更被把让给过着吗呢吧啊哦嗯]", zh)


def ru_term_like(ru):
    if RU_BAD.search(ru) or TESTISH.search(ru) or not CYR.search(ru):
        return False
    words = ru.split()
    if not (1 <= len(words) <= 4) or len(ru) > 40 or not ru[0].isupper():
        return False
    return True


def en_term_like(en):
    return bool(en and len(en) <= 48 and 1 <= len(en.split()) <= 5
                and not re.search(r"[<>|]|\$\(|[!?]|\.\s|\.$", en)
                and re.search(r"[A-Za-z]", en))


# 英文超常用词：即便人工表列出也不强制（语料中译名与“神器简称”冲突时弃新增）
TXT_EXTRA_SKIP_EN = {"Art"}

# 最终分类（输出顺序即整理顺序）
CATS = [
    ("faction", "阵营与组织"),
    ("name", "人物与军衔"),
    ("loc", "地点与区域"),
    ("mutant", "变异生物"),
    ("item", "物品·武器·装备·神器"),
    ("task", "任务"),
    ("term", "百科与术语"),
    ("ui", "界面与提示"),
    ("misc", "新闻·对话·其他"),
]
CAT_TITLE = dict(CATS)

RAW_FOLD = {
    "name": "name", "mapspot": "loc", "item": "item", "task": "task",
    "encyclopedia": "term", "ui": "ui", "tip": "ui",
    "news": "misc", "dialog": "misc", "other": "misc",
}
TXT_FOLD = {
    "阵营与组织": "faction", "地点与区域": "loc", "怪物与变异体": "mutant",
    "人物与绰号": "name", "物品与神器": "item", "武器与装备": "item",
    "术语与俚语": "term",
}


def variants(s):
    b = s.strip(QUOTES)
    return [s] if b == s or not b else [s, b]


def read_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_txt_table():
    """人工三语表：返回 [{en, ru, zh, cat}]，只取分类小节内的数据行。"""
    rows = []
    cur = None
    for line in codecs.open(TXT, encoding="utf-8"):
        line = line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        if re.match(r"^\d+\.\s", line):          # “1. 阵营与组织”
            cur = re.sub(r"^\d+\.\s*", "", line).strip()
            continue
        if line.startswith("英文\t"):
            continue
        parts = line.split("\t")
        if cur and len(parts) >= 3:
            en, ru, zh = parts[0].strip(), parts[1].strip(), parts[2].strip()
            zh = zh.split("/")[0].strip()         # “神射手/斯特列洛克”取主译名
            cat = TXT_FOLD.get(cur)
            if cat and en and ru and zh:
                rows.append({"en": en, "ru": ru, "zh": zh, "cat": cat})
    return rows


def main():
    rows = read_json(os.path.join(HERE, "raw_pairs.json"))
    ru_tab = read_json(os.path.join(HERE, "dict_ns_ogsr_rus-chs.json"))
    en_tab = read_json(os.path.join(HERE, "dict_ns_ogsr_eng-chs.json"))
    legacy = read_json(LEGACY) if os.path.isfile(LEGACY) else {}

    # 1) 语料分类投票（只认短名词译名的“定义性”行）----------------------
    votes = defaultdict(Counter)
    zh_freq = defaultdict(Counter)
    for r in rows:
        if not zh_term_like(r["zh"]):
            continue
        c = RAW_FOLD.get(r["cat"], "misc")
        for v in variants(r["ru"]):
            if ru_term_like(v):
                votes[v][c] += 1
                zh_freq[v][r["zh"]] += 1
        e = r.get("en", "").strip()
        if e and en_term_like(e):
            votes[e][c] += 1
            zh_freq[e][r["zh"]] += 1

    # 2) 人工三语表：分类覆盖 + 缺失补录 ----------------------------------
    curated = {}
    extras = []
    extra_keys = set()
    for t in parse_txt_table():
        curated[t["ru"]] = t["cat"]
        curated[t["en"]] = t["cat"]
        ru_has = t["ru"] in ru_tab
        en_has = t["en"] in en_tab
        for lang, key, tab, has, other_tab, other_key in (
                ("ru", t["ru"], ru_tab, ru_has, en_tab, t["en"]),
                ("en", t["en"], en_tab, en_has, ru_tab, t["ru"])):
            if has or key in GARBAGE[lang] or key in extra_keys:
                continue
            if lang == "en" and key in TXT_EXTRA_SKIP_EN:
                continue
            # 同一概念的英/俄键共用一个译名：优先补丁中已有的一侧
            if other_key in other_tab:
                zh = other_tab[other_key]
            elif zh_freq[key]:
                zh = zh_freq[key].most_common(1)[0][0]
            else:
                zh = t["zh"]
            tab[key] = zh
            extras.append((key, zh))
            extra_keys.add(key)

    # 3) 变格形式继承原形分类（同 build_glossary 的配对思路）---------------
    vocab = Counter()
    for r in rows:
        for w in re.findall(r"[А-ЯЁ][а-яё]+", r["ru"]):
            vocab[w] += 1
    case_ends = ("а", "я", "у", "ю", "е", "ы", "и", "ом", "ем", "ам", "ях",
                 "ах", "ов", "ев", "ами", "ями")
    cand = [w for w, n in vocab.items()
            if n >= 2 and len(w) >= 5 and w.endswith(case_ends)]
    by_head = defaultdict(list)
    for w in cand:
        by_head[w[:4]].append(w)

    def lcp(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    inflect_base = {}
    for key in ru_tab:
        if len(key) < 4 or " " in key:
            continue
        need = max(4, len(key) - 2)
        for w in by_head.get(key[:4], ()):
            if w in inflect_base:
                continue
            if len(key) <= len(w) <= len(key) + 3 and lcp(w, key) >= need:
                inflect_base[w] = key

    legacy_nouns = {k for k in legacy if 1 <= len(k.split()) <= 4 and len(k) <= 40}

    # 4) 逐条定类：先俄语（变格可链式继承原形），再英语（挂靠同概念俄语键）----
    ru_cat = {}

    def cat_of_ru(key, depth=0):
        if key in ru_cat:
            return ru_cat[key]
        base_cat = None
        if key in inflect_base and depth < 4:
            base_cat = cat_of_ru(inflect_base[key], depth + 1)
        if key in curated:
            c = curated[key]
        elif base_cat and base_cat != "misc":
            c = base_cat          # 变格形式跟随原形（Долга→阵营，Псевдогиганта→怪物）
        elif votes[key]:
            c = votes[key].most_common(1)[0][0]
        elif base_cat:
            c = base_cat
        elif key in legacy_nouns or RANK_RE.match(key):
            c = "name"
        else:
            c = "misc"
        ru_cat[key] = c
        return c

    for key in ru_tab:
        cat_of_ru(key)

    # 英文键：能对齐到某条俄语键时，直接继承该键的最终分类
    en_link = defaultdict(Counter)
    for r in rows:
        e = r.get("en", "").strip()
        if not e or e not in en_tab:
            continue
        for v in variants(r["ru"]):
            if v in ru_cat:
                en_link[e][ru_cat[v]] += 1

    en_cat = {}
    for key in en_tab:
        if key in curated:
            en_cat[key] = curated[key]
        elif en_link[key]:
            en_cat[key] = en_link[key].most_common(1)[0][0]
        elif votes[key]:
            en_cat[key] = votes[key].most_common(1)[0][0]
        else:
            en_cat[key] = "misc"

    assigned, valued = {}, {}
    for key, zh in ru_tab.items():
        if key not in GARBAGE["ru"] and key.strip():
            assigned[key] = ru_cat[key]
            valued[key] = zh
    for key, zh in en_tab.items():
        if key in GARBAGE["en"] or not key.strip() or key in valued:
            continue
        assigned[key] = en_cat[key]
        valued[key] = zh

    # 4b) “其他”桶二次整理：按短语形状区分任务/界面/名词短语 -------------
    UI_VOCAB_RU = re.compile(
        r"настройк|интенсивн|скорост|качеств|расстояни|дистанци|эффект|"
        r"глубин|цветност|отражени|преломлен|прозрачност|переключен|"
        r"отображен|разрешен|текстур|анимаци|загрузк|рассеивани|звук|"
        r"категори|сортировк|ремонт|сервер|симулятор|громкост|вариативн|"
        r"срабатыван|скрыти|группировк|перенос|предыдущ|режим\b|восстановл|"
        r"вынослив|здоровь|просмотр|прорисовк|пауза|границ|фонар|подсветк|"
        r"колес|контакт|рейтинг|задани|сообщени|слот|предмет|интерфейс|"
        r"прорисов|детальн|кол-во|количеств", re.I)
    UI_VOCAB_EN = re.compile(
        r"intensity|quality|speed|distance|effect|depth|colou?r|enable|disable|"
        r"switch|toggle|display|resolution|texture|animation|loading|mouse|"
        r"droplet|rain|sorting|category|option|setting|slider|volume|scatter|"
        r"reflection|refraction|transparency|focus|thread|server|simulator|"
        r"patch|wheel|interaction|respawn|graphics|shadow|lighting|sound|"
        r"vertical|horizontal|field of view|fps|cpu|gpu|variation|footstep|"
        r"qty|quantity|element|hide|group|carry|previous|mode|stamina|health|"
        r"viewer|detailed|draw |pause|boundary|trigger|flashlight|"
        r"illumination|scope|tactical|contact|rating|history|message|slot|"
        r"interface|collision|grass|save\b", re.I)
    RU_INF_RE = re.compile(r"(ать|ять|еть|ить|ыть|оть|уть|зть|чь)$")
    RU_INF_EXTRA = {"класть", "пасть", "упасть", "сесть", "попасть",
                    "пропасть", "учесть", "счесть"}
    RU_IMP_WORDS = {
        "иди", "найди", "принеси", "узнай", "проверь", "помоги", "убей",
        "уничтожь", "сходи", "доложи", "забери", "отдай", "передай", "верни",
        "спаси", "беги", "уходи", "собери", "осмотри", "выясни", "разведай",
        "приведи", "открой", "закрой", "включи", "выключи", "нажми", "возьми",
        "купи", "продай", "почини", "установи", "подключи", "отключи",
        "зачисти", "обыщи", "догони", "следуй", "жди", "встреть", "поговори",
        "расскажи", "спроси", "доставь", "раздобудь", "разыщи", "обследуй",
        "исследуй", "захвати", "выкради", "предложи", "покажи", "оформи",
        "изгони", "разрушь", "разверни", "пересеки", "посети", "прими",
    }
    EN_IMP = {
        "deliver", "find", "search", "get", "take", "bring", "kill", "destroy",
        "defeat", "investigate", "show", "steal", "offer", "help", "traverse",
        "mount", "cross", "reach", "meet", "talk", "speak", "collect",
        "locate", "scout", "eliminate", "protect", "follow", "escape",
        "return", "repair", "activate", "deactivate", "sell", "buy", "trade",
        "examine", "inspect", "measure", "prove", "solve", "rescue", "guard",
        "place", "install", "use", "equip", "upgrade", "unlock", "fetch",
        "hunt", "clear", "defend", "accompany", "recover", "retrieve",
        "identify", "track", "confront", "infiltrate", "warn", "convince",
        "question", "explore", "obtain", "disable", "secure",
    }

    def refine(key):
        words = key.split()
        if CYR.search(key):
            head = words[0].lower()
            if RU_INF_RE.search(words[0]) or head in RU_INF_EXTRA \
                    or head in RU_IMP_WORDS:
                return "task"
            if UI_VOCAB_RU.search(key):
                return "ui"
            if len(words) >= 2:
                return "term"
            return "term" if len(words[0]) >= 4 else "misc"
        head = re.sub(r"[^A-Za-z'-]", "", words[0]).lower()
        if head in EN_IMP:
            return "task"
        if UI_VOCAB_EN.search(key) or key.startswith(("SERVER:", "CLIENT:")):
            return "ui"
        if len(words) >= 2:
            return "term"
        return "term" if len(head) >= 4 else "misc"

    for key in list(assigned):
        if assigned[key] == "misc":
            assigned[key] = refine(key)

    # 5) 组装输出 ----------------------------------------------------------
    buckets = {c: {} for c, _ in CATS}
    for key, zh in valued.items():
        buckets[assigned[key]][key] = zh

    total = 0
    out_cats = {}
    for c, title in CATS:
        d = buckets[c]
        ordered = {k: d[k] for k in sorted(d, key=lambda x: (-len(x), x))}
        out_cats[title] = ordered
        total += len(ordered)

    out = {
        "_meta": {
            "format": "stalker-toolkit-termbase/1",
            "name": "STALKER NS OGSR 俄英→简中 名词库",
            "built": date.today().isoformat(),
            "builder": "tools/build_termbase.py",
            "sources": [
                "raw_pairs.json (30079 aligned pairs with category votes)",
                "dict_ns_ogsr_rus-chs.json",
                "dict_ns_ogsr_eng-chs.json",
                "dict_current_2026-06-21.json (legacy curated, folded in)",
                "新建 文字文件.txt (manual en/ru/zh category table)",
            ],
            "languages": "ru,en -> zh-Hans (mixed source keys, auto-detected)",
            "total_entries": total,
            "categories": {title: len(out_cats[title]) for _, title in CATS},
            "dropped_misaligned": {
                "ru": sorted(GARBAGE["ru"]), "en": sorted(GARBAGE["en"])},
            "note": "插件「内置术语库 / 导入术语表 JSON」选择本文件即可；"
                    "分类段落自动摊平合并，_meta 自动跳过。",
        },
    }
    out.update(out_cats)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("total %d -> %s" % (total, OUT))
    for _, title in CATS:
        print("  %-12s %4d" % (title, len(out_cats[title])))
    print("txt extras added:", len(extras))
    for k, v in extras:
        print("   + %s => %s" % (k, v))


if __name__ == "__main__":
    sys.exit(main())
