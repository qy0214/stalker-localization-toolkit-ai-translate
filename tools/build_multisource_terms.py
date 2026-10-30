#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从多个 MOD 汉化补丁中挖掘高置信度术语，生成可复核的增量表。

与 build_glossary.py 的区别：
  * 按 <string id> 跨目录对齐（补丁的表路径/文件名与底版不同，不能按路径配对）；
  * 同时读 NS7 各版本、NS 2/19、OP、OLR25、疤脸、WOT；
  * 只产出“新增”和“人工确认的覆盖”，不直接改术语库；
  * 剩下所有冲突写进报告，交给人工判断。

用法（在仓库根目录）：
    python tools/build_multisource_terms.py
    python tools/build_multisource_terms.py --base "D:\\gamedata" --apply

产物：
    tools/multisource_terms.json     新增/覆盖候选（merge_multisource_terms.py 的输入）
    tools/multisource_report.md      新增、覆盖、冲突与来源明细
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_glossary as bg  # noqa: E402

OUT = os.path.join(HERE, "multisource_terms.json")
REPORT = os.path.join(HERE, "multisource_report.md")
TERMBASE = os.path.join(HERE, "stalker_termbase.json")

# 默认路径只用于本机重建；命令行可覆盖。
DEFAULT_BASE = r"C:\Users\Admin\Desktop\gamedata"
DEFAULT_PATCHES = [
    ("NS7_0906", r"C:\game\NS7\MODS\NS OGSR 2026年9月6日版本汉化补丁\gamedata", 100),
    ("NS7_0726", r"C:\game\NS7\MODS\NS OGSR 2026年7月26日版本汉化补丁\gamedata", 90),
    ("NS7_0706", r"C:\game\NS7\MODS\NS OGSR 2026年7月6日版本汉化补丁\gamedata", 70),
    ("NS7_0619", r"C:\game\NS7\MODS\NS OGSR 2026年6月19日版本汉化补丁\gamedata", 60),
    ("NS_0219", r"C:\game\NS\MODS\NS OGSR 2026年2月19日版本汉化补丁\gamedata", 50),
    ("OP", r"C:\game\OP\MODS\2.汉化", 80),
    ("OLR25", r"C:\game\STALKER-OLR25\MODS\2：大字体汉化（高分辨率屏幕用此汉化）", 80),
    ("SCAR", r"C:\game\S.T.A.L.K.E.R\MODS\疤脸归来2 机翻汉化补丁", 40),
    ("WOT", r"C:\game\WOT\MODS\汉化", 30),
]

CJK = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
STR_RE = re.compile(r'<string\s+id="([^"]+)"[^>]*>(.*?)</string>', re.S)
TAG_RE = re.compile(r"<(rus|eng|ukr|tur|text|chs)>(.*?)</\1>", re.S)

# 这些普通词即使出现在名词位置也不能当术语；否则“Нет→否”会被硬塞进句子。
RU_COMMON = set("""
нет да всего всё все это этот эта эти так тут там сейчас потом очень можно
нужно надо есть был была были быть кто что где когда почему как такой такая
такие он она они мы вы я ты его её ее их мне тебе нам вас себя сам сама само
сами уже ещё еще только даже ведь же ли бы не ни или и а но зато также тоже
""".split())

EN_STOP = set("""
the a an of to in on at for and or is are was were be been being with by from
this that these those it its as not no yes you your he she they we i me my our
their his her them us do does did can could will would should may might must
have has had so but if then than also very just there here what which who whom
whose when where why how all any some
""".split())

EN_IMP = set("""
deliver find search get take bring kill destroy defeat investigate show steal
offer help traverse mount cross reach meet talk speak collect locate scout
eliminate protect follow escape return repair activate deactivate sell buy
trade examine inspect measure prove solve rescue guard place install use equip
upgrade unlock fetch hunt clear defend accompany recover retrieve identify
track confront infiltrate warn convince question explore obtain disable secure
""".split())

# 任务/藏匿点/新闻里的短语不是名词术语；这些词一出现就跳过。
SKIP_WORDS = re.compile(
    r"\b(?:stash|ambush|captive|wounded|dead|possible|year|documents?|"
    r"notes?|list|group|squad|team|credits?)\b", re.I)

# 地图标注里混着“受伤的狐狸/被俘的机灵鬼/伏击”这类事件标签，不是地名。
LOCATION_STOP = re.compile(
    r"\b(?:wounded|injured|captive|ambush|scouts?|group|squad|team|report|"
    r"lieutenant|stalker|mercenar\w*|prisoner|information|data|route|access)\b",
    re.I)

# 俄语地图标注里的同类事件/情报标签。
LOCATION_STOP_RU = re.compile(
    r"\b(?:ранен\w*|плен\w*|засад\w*|групп\w*|наёмник\w*|наемник\w*|"
    r"рапорт\w*|лейтенант\w*|данные|информаци\w*|маршрут\w*|отряд\w*)\b",
    re.I)

# 明确不采纳的补丁译法（补丁本身是机翻/错译，或当前译名更贴合语境）。
# 只影响“覆盖已有词”，不影响新增。
DENY_OVERRIDE = {
    "прапор": "补丁把“准尉”译成“旗帜”，语义错误",
    "волкодав": "补丁把绰号“狼狗”译成“沃夫”，偏离原意",
    "медведь": "补丁把“灰熊”译成“熊式”，语境不符",
    "холод": "补丁把“冷风”译成“寒冷”，丢失绰号感",
    "крюк": "补丁把“铁钩”译成“钩子”，丢失绰号感",
    "змей": "补丁把“毒蛇”译成“蛇”，丢失绰号感",
    "кастет": "补丁音译“卡斯特特”，当前“铜指环”更贴近原意",
    "гребень": "补丁译名不确定，保留现状",
    "глухарь": "补丁音译丢失绰号感，保留现状",
    "жека дуплет": "补丁译名不确定，保留现状",
    "тихий": "补丁把“Тихий”误译成“太平洋”",
}

# 人工确认的覆盖：source(小写) -> (新译名, 理由)
OVERRIDES = {
    "васильев": ("瓦西里耶夫", "去掉职务后缀，补丁人名表一致"),
    "бродяга": ("流浪汉", "补丁按词义，当前“恶棍”偏离"),
    "шуруп": ("螺丝", "补丁按词义，当前“拧巴”偏离"),
    "череп": ("头骨", "补丁按词义，去掉引号"),
    "упырь": ("吸血鬼", "补丁按词义，当前为音译"),
    "тиран": ("暴君", "补丁按词义，当前为音译"),
    "леший": ("林妖", "补丁按词义，当前为音译"),
    "лесник": ("守林员", "补丁用词，比“守林人”更统一"),
    "дух": ("幽灵", "补丁按词义，当前“神灵”偏离"),
    "шустрый": ("机灵鬼", "补丁按词义，当前“明眼”偏离"),
    "шило": ("希洛", "补丁音译统一"),
    "толик": ("托利克", "补丁音译统一"),
    "петруха": ("彼得鲁哈", "补丁音译统一"),
    "макс": ("马克斯", "补丁音译统一"),
    "лукаш": ("卢卡什", "补丁音译统一"),
    "валет": ("瓦列特", "补丁音译统一"),
    "боров": ("博罗夫", "补丁音译统一，当前“波诺夫”错"),
    "хребет": ("山脊", "补丁按词义"),
    "хог": ("霍格", "补丁音译统一"),
    "флинт": ("弗林特", "补丁音译统一"),
    "фантом": ("幻影", "补丁按词义"),
    "угрюмый": ("阴郁者", "补丁按词义，当前“黑暗”偏离"),
    "турист": ("游客", "补丁按词义"),
    "сумасшедший": ("疯子", "补丁按词义，当前带“潜行者”后缀"),
    "стрелок": ("史特列洛克", "补丁采用官方音译"),
    "серёга": ("谢廖加", "补丁音译统一"),
    "серый": ("灰色", "补丁按词义，当前“西里”是错误音译"),
    "рябой": ("麻脸", "补丁按词义，当前“斯巴迪”偏离"),
    "повар": ("厨师", "补丁按词义"),
    "павлик": ("帕夫利克", "补丁音译统一"),
    "осведомитель": ("线人", "补丁按词义，去掉引号"),
    "митяй": ("米佳", "补丁音译统一"),
    "лёлик": ("廖利克", "补丁音译统一"),
    "крот": ("鼹鼠", "补丁按词义，当前“莫尔”是错误音译"),
    "костоправ": ("正骨师", "补丁用词"),
    "капитан иванцов": ("伊万佐夫上尉", "补丁音译统一"),
    "жорик": ("乔里克", "补丁音译统一"),
    "данила": ("丹尼拉", "补丁音译统一"),
    "генка": ("根卡", "补丁音译统一"),
    "гарик": ("加里克", "补丁音译统一"),
    "вася кабан": ("瓦夏·野猪", "补丁音译统一"),
    "арни": ("阿尔尼", "补丁音译统一"),
    "ара": ("阿里", "补丁音译统一"),
    "гриф": ("格里芬", "补丁音译统一"),
    "зверобой": ("猎兽人", "补丁用词"),
    "доктор": ("医生", "补丁按词义"),
    "лис": ("狐狸", "去掉引号，避免替换时带引号"),
    "пузырь": ("气泡", "去掉引号"),
    "фраер": ("弗雷尔", "去掉引号"),
    "клюв": ("鸟喙", "去掉引号"),
    "барин": ("巴林", "去掉引号"),
    "вертухай": ("狱警", "去掉引号"),
    "шнырь": ("鼹鼠", "去掉引号"),
    "контроль монолита": ("黑石控制区", "去掉引号"),
    "эмбрион бюрера": ("狂暴侏儒胚胎", "去掉引号"),
    "лежбище разбойника": ("强盗栖身处", "去掉引号"),
    "записка сепатора": ("分离者便条", "去掉引号"),
    "группа призрака": ("幽灵小队", "去掉引号"),
    "армейские склады": ("军械库", "补丁用词，比“军用仓库”更像地名"),
}


def read_text(path):
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", "replace")


def parse_table(path):
    result = {}
    for m in STR_RE.finditer(read_text(path)):
        result.setdefault(m.group(1), {
            t.group(1): t.group(2) for t in TAG_RE.finditer(m.group(2))})
    return result


def clean_value(value):
    s = html.unescape(value or "").strip()
    s = s.replace('"', "").replace("“", "").replace("”", "")
    s = s.replace("«", "").replace("»", "").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[.。…；;:：,，]+$", "", s).strip()
    return s


def name_shape(s):
    """人名/绰号：允许 1-5 个词、缩写、连字符、中黑点；排除普通词和句尾标点。"""
    if not s or len(s) > 48 or s.lower() in RU_COMMON:
        return False
    if re.search(r"[.!?。！？；;:：,，]$", s):
        return False
    if re.match(r"^[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]?\.?\s*[А-ЯЁA-Z][а-яёa-z-]+$", s):
        return True
    words = s.split()
    return 1 <= len(words) <= 5 and s[0].isupper()


def english_shape(s):
    words = s.split()
    if not (1 <= len(words) <= 4 and len(s) <= 40 and s[0].isupper()):
        return False
    if re.search(r"[.!?。！？；;:：,，]$", s):
        return False
    low = [w.lower().strip(".,") for w in words]
    if any(w in EN_STOP for w in low):
        return False
    if low[0] in EN_IMP or low[0].endswith(("ing", "ed")):
        return False
    return True


def category_of(rel, key=""):
    """按补丁里的真实目录/ID 判断条目类别；任务/对话/新闻/藏匿点等一律不收。"""
    s = rel.lower().replace(os.sep, "/")
    k = (key or "").lower()
    if any(x in s for x in (
            "task", "dialog", "news", "tip", "stash", "secret", "treasure",
            "ui", "map_desc", "gameplay", "spawn", "npc", "credits")):
        return None
    # 说明/提示/简介不是术语；_name 保留（人名、物品名）。
    if any(x in k for x in ("_descr", "_desc", "_bio", "_text", "_hint")):
        return None
    if any(x in s for x in ("character_name", "snames", "fnames", "bio")):
        return "人物与军衔"
    if any(x in s for x in ("faction", "community", "communities")):
        return "阵营与组织"
    # 神器表里混着地名（Bar / Monolith control）；按 ID/名字优先判地点。
    if k in ("bar", "l05_bar", "control-monolith", "l12u_control_monolith"):
        return "地点与区域"
    if any(x in s for x in ("string_table_levels", "levels", "mapspots")):
        return "地点与区域"
    if k.startswith(("l0", "l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8",
                     "l9", "way", "to_")):
        return "地点与区域"
    if k.startswith(("af-", "af_", "art-")):
        return "物品·武器·装备·神器"
    if any(x in s for x in ("enc_zone", "enc_arts", "artefact", "artifact",
                            "amk_arts", "arts_desc")):
        return "物品·武器·装备·神器"
    if any(x in s for x in ("enc_weapons", "weapon", "items", "equipment")):
        return "物品·武器·装备·神器"
    if any(x in s for x in ("enc_mutants", "creature", "mutant", "monster")):
        return "变异生物"
    if any(x in s for x in ("zone", "map", "level", "place")):
        return "地点与区域"
    return None


def walk_tables(root):
    for cur, _dirs, files in os.walk(root):
        for fn in files:
            if fn.lower().endswith(".xml"):
                yield os.path.join(cur, fn)


def load_base(base_root):
    base = {}
    for path in walk_tables(base_root):
        if "translated_hy_mt2_" in path.replace(os.sep, "/"):
            continue
        for key, tags in parse_table(path).items():
            base.setdefault(key, tags)
    return base


def load_existing(path=None):
    data = json.load(open(path or TERMBASE, encoding="utf-8"))
    flat = {}
    for cat, table in data.items():
        if cat.startswith("_") or not isinstance(table, dict):
            continue
        for key, value in table.items():
            flat[key.lower()] = (cat, key, value)
    return flat


def collect(base, patches):
    votes = collections.defaultdict(collections.Counter)
    cats = collections.defaultdict(collections.Counter)
    where = collections.defaultdict(set)
    for name, root, _prio in patches:
        if not os.path.isdir(root):
            continue
        for path in walk_tables(root):
            rel = os.path.relpath(path, root)
            for key, tags in parse_table(path).items():
                cat = category_of(rel, key)
                if not cat:
                    continue
                target = ""
                for field in ("rus", "text", "chs"):
                    val = clean_value(tags.get(field, ""))
                    if val and CJK.search(val) and not CYR.search(val):
                        target = val
                        break
                if not target:
                    continue
                if re.search(r"[。！？；\n]", target):
                    continue
                if len(target) > (24 if cat == "人物与军衔" else 14):
                    continue
                bt = base.get(key, {})
                sources = []
                for field in ("rus", "eng"):
                    val = (bt.get(field) or "").strip()
                    if val and not CJK.search(val):
                        sources.append((field, clean_value(val)))
                if not sources:
                    for field in ("rus", "eng"):
                        val = (tags.get(field) or "").strip()
                        if val and not CJK.search(val):
                            sources.append((field, clean_value(val)))
                for field, source in sources:
                    if not source or SKIP_WORDS.search(source):
                        continue
                    if cat == "地点与区域" and (
                            LOCATION_STOP.search(source)
                            or LOCATION_STOP_RU.search(source)):
                        continue
                    if re.search(r"\.(?:txt|xml|ltx|script)\b", source, re.I):
                        continue
                    if "\n" in source or "\r" in source:
                        continue
                    if re.search(r"\.\s+[А-Яа-яЁёA-Za-z]", source):
                        continue
                    if field == "rus":
                        ok = (name_shape(source) if cat == "人物与军衔"
                              else bg.ru_is_term(source))
                    else:
                        ok = english_shape(source)
                    if not ok:
                        continue
                    low = source.lower()
                    votes[low][target] += 1
                    cats[low][cat] += 1
                    where[low].add((name, rel, key))
    return votes, cats, where


# 人工补录：语料里出现但原有词表缺的常用俚语/简称（只做软参考）。
MANUAL_ADDITIONS = {
    "верт": ("直升机", "物品·武器·装备·神器"),
    "верта": ("直升机", "物品·武器·装备·神器"),
    "вертушка": ("直升机", "物品·武器·装备·神器"),
}


def build():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--apply", action="store_true",
                    help="同时写入术语库（否则只生成候选文件）")
    ap.add_argument("--baseline", default="",
                    help="用于判断“新增/覆盖”的旧术语库快照；默认当前术语库")
    args = ap.parse_args()

    base = load_base(args.base)
    if not args.baseline:
        print("提示：未指定 --baseline；合并后重跑只会看到尚未入库的条目。")
    existing = load_existing(args.baseline or None)
    votes, cats, where = collect(base, DEFAULT_PATCHES)

    additions = {}
    overrides = {}
    conflicts = {}

    for source, tally in votes.items():
        chosen, count = tally.most_common(1)[0]
        cat = cats[source].most_common(1)[0][0]
        total = sum(tally.values())
        old = existing.get(source)
        if old and old[2] != chosen:
            if source in OVERRIDES and source not in DENY_OVERRIDE:
                overrides[source] = {
                    "old": old[2], "zh": OVERRIDES[source][0],
                    "reason": OVERRIDES[source][1],
                    "category": cat, "old_category": old[0],
                    "votes": total,
                    "variants": [[v, n] for v, n in tally.most_common(5)],
                    "sources": sorted({x[0] for x in where[source]}),
                }
            if len(tally) > 1:
                conflicts[source] = {
                    "category": cat, "current": old[2],
                    "variants": [[v, n] for v, n in tally.most_common(8)],
                    "votes": total,
                    "sources": sorted({x[0] for x in where[source]}),
                    "files": sorted({"%s#%s" % (x[1], x[2])
                                     for x in where[source]})[:6],
                }
            continue
        if old or source in DENY_OVERRIDE:
            continue
        # 新增：同一译名至少被两个独立补丁/文件看到，且占多数；分歧大的
        # （例如 shell 同时有“甲壳/空弹”）只进冲突报告，不硬塞。
        if total < 2 or count / total < 0.6:
            continue
        add_cat = cat
        # 物品/神器名最容易被机翻带偏：低票/低一致的只放软参考（百科与术语），
        # 不进入强制替换层，避免“甲壳/空弹”这类词把正文改坏。
        if cat == "物品·武器·装备·神器" and not (
                total >= 5 and count / total >= 0.75):
            add_cat = "百科与术语"
        additions[source] = {
            "zh": chosen, "category": add_cat,
            "original_category": cat,
            "votes": total,
            "variants": [[v, n] for v, n in tally.most_common(5)],
            "sources": sorted({x[0] for x in where[source]}),
            "files": sorted({"%s#%s" % (x[1], x[2])
                             for x in where[source]})[:4],
        }
        if len(tally) > 1:
            conflicts[source] = {
                "category": cat, "current": "",
                "variants": [[v, n] for v, n in tally.most_common(8)],
                "votes": total,
                "sources": sorted({x[0] for x in where[source]}),
                "files": sorted({"%s#%s" % (x[1], x[2])
                                 for x in where[source]})[:6],
            }

    for source, (zh, cat) in MANUAL_ADDITIONS.items():
        low = source.lower()
        if low in existing or low in additions:
            continue
        additions[source] = {
            "zh": zh, "category": cat, "original_category": cat,
            "votes": 0, "variants": [[zh, 0]],
            "sources": ["manual"], "files": ["manual"],
        }

    moves = {}
    for source, (_zh, cat) in MANUAL_ADDITIONS.items():
        hit = existing.get(source.lower())
        if hit and hit[0] != cat:
            moves[source] = cat

    payload = {
        "_meta": {
            "format": "stalker-toolkit-multisource-terms/1",
            "built": date.today().isoformat(),
            "builder": "tools/build_multisource_terms.py",
            "base": args.base,
            "patches": [{"name": n, "path": p, "priority": prio}
                        for n, p, prio in DEFAULT_PATCHES],
            "additions": len(additions),
            "overrides": len(overrides),
            "conflicts": len(conflicts),
            "moves": len(moves),
        },
        "additions": dict(sorted(additions.items())),
        "overrides": dict(sorted(overrides.items())),
        "conflicts": dict(sorted(conflicts.items())),
        "moves": dict(sorted(moves.items())),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    write_report(payload)
    print("additions %d, overrides %d, conflicts %d -> %s"
          % (len(additions), len(overrides), len(conflicts), OUT))
    if args.apply:
        import merge_multisource_terms
        merge_multisource_terms.apply(payload)
    return 0


def write_report(payload):
    lines = [
        "# 多 MOD 术语挖掘报告",
        "",
        "日期：%s" % payload["_meta"]["built"],
        "",
        "来源：NS7 9/6、7/26、7/6、6/19，NS 2/19，OP，OLR25，疤脸，WOT。",
        "只统计名词形；任务/对话/新闻/藏匿点/界面短语不进入术语库。",
        "",
        "## 汇总",
        "",
        "| 项 | 数量 |",
        "|---|---:|",
        "| 新增术语 | %d |" % len(payload["additions"]),
        "| 人工确认覆盖 | %d |" % len(payload["overrides"]),
        "| 冲突（仅报告，不自动改） | %d |" % len(payload["conflicts"]),
        "",
        "## 人工确认覆盖",
        "",
        "| 原文 | 当前 | 新译名 | 类别 | 理由 |",
        "|---|---|---|---|---|",
    ]
    for src, row in payload["overrides"].items():
        lines.append("| %s | %s | %s | %s | %s |" % (
            src, row["old"], row["zh"], row["category"], row["reason"]))
    lines += ["", "## 新增术语", "",
              "| 原文 | 译名 | 类别 | 票数 | 来源 |",
              "|---|---|---|---:|---|"]
    for src, row in payload["additions"].items():
        lines.append("| %s | %s | %s | %d | %s |" % (
            src, row["zh"], row["category"], row["votes"],
            ", ".join(row["sources"])))
    lines += ["", "## 冲突（按票数排序，仅报告）", "",
              "| 原文 | 当前 | 候选 | 类别 | 票数 |",
              "|---|---|---|---|---:|"]
    for src, row in sorted(payload["conflicts"].items(),
                           key=lambda kv: -kv[1]["votes"])[:300]:
        variants = " / ".join("%s(%d)" % (v, n)
                              for v, n in row["variants"][:4])
        lines.append("| %s | %s | %s | %s | %d |" % (
            src, row["current"] or "-", variants,
            row["category"], row["votes"]))
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(build())
