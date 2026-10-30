#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把现行分类术语库升级为 v2（canon / names / technical + 变体索引）。

v2 不改变“一张通用库随包分发”的定位，只补三样东西：

1. `_meta.layers` 明确四层边界：canon（硬词）、names（人物软层）、
   technical（整条精确技术词）、reference（参考软词）；
2. `_meta.variants` 记录少量拼写变体到规范词条的映射，运行时索引据此
   去重，避免 ё/е、大小写变体出现两个不同译名；
3. 新增 `技术缩写` 分类，把原先写死在插件里的 `_TECHNICAL_EXACT`
   数据化，插件、术语库、审计工具共用同一份数据。

脚本幂等：重复运行不会重复添加技术词或改坏已修正的条目。

用法：
    python tools/upgrade_termbase_v2.py
"""
import ast
import io
import json
import os
import re
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TERMBASE = os.path.join(HERE, "stalker_termbase.json")
PLUGIN_COPY = os.path.join(ROOT, "plugins", "ai_translate_assets",
                           "stalker_termbase.json")
BUILTIN = os.path.join(ROOT, "plugins", "ai_translate_assets",
                       "glossary_builtin.json")
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate.py")

# 高置信修正：规范化后同源，但旧库给了不同译名。目标统一到 NS 标准。
FIXES = {
    ("地点与区域", "Армейские склады"): "军械库",
    ("人物与军衔", "Санёк"): "萨涅克",
    ("ANTHOLOGY 参考", "Гришачев"): "格里沙乔夫",
    # 同一概念在引号形态/大小写不同的两条词条里给了两个译名：
    # 统一到语料中出现最多的 NS 口径。
    ("任务", "Flying island"): "飞行岛",
    ("任务", '"Flying Island"'): "“飞行岛”",
    ("百科与术语", "fog pocket"): "局地雾",
    ("百科与术语", "anomalous groza"): "异常雷暴",
    # 屈折形与规范词条译名不一致（同一地点/怪物/装置）。
    ("地点与区域", "Армейских складах"): "军械库",
    ("地点与区域", "Армейских складов"): "军械库",
    ("变异生物", "Крысы"): "变异鼠",
    ("物品·武器·装备·神器", "Деактиватор телепорта"): "传送器干扰装置",
}

# 变体 → 规范词条。运行时索引把变体视为同一词条，只保留一个命中。
VARIANTS = {
    "Армейские склады": "Армейские Склады",
    "Санёк": "Санек",
    "Гришачев": "Гришачёв",
}

# 1000 条 A/B 暴露出的错误软词：Короче=“总之/简而言之”，不是“国王”。
# 这类词典对齐噪声只保留在原始语料里，不进入随包标准库。
REMOVALS = {
    ("人物与军衔", "Короче"),
}

# 英文物品/武器专项审计的高置信修正。清掉把俄文“Плата/Плащ/Винторез”
# 错误映射成英文后的坏键；保留俄文正确条目，并补正确的英文别名。
EN_FIXES = {
    ("物品·武器·装备·神器", "Metal Key"): "金属钥匙",
    ("物品·武器·装备·神器", "Металлический ключ"): "金属钥匙",
    ("物品·武器·装备·神器", "Borov's trench coat"): "博罗夫的军大衣",
    ("物品·武器·装备·神器", "Плащ Борова"): "博罗夫的军大衣",
    ("物品·武器·装备·神器", "Leather trench coat"): "皮制风衣",
    ("物品·武器·装备·神器", "Black bandit trench coat"): "黑色强盗风衣",
    ("物品·武器·装备·神器", "Brown bandit trench coat"): "棕色强盗风衣",
    ("物品·武器·装备·神器", "Psychiatrist's trench coat"): "精神科医师风衣",
    ("物品·武器·装备·神器", "Mama's Beads"): "妈妈的项链",
    ("物品·武器·装备·神器", "Мамины бусы"): "妈妈的项链",
    ("物品·武器·装备·神器", "Monolith Shard"): "黑石碎片",
    ("物品·武器·装备·神器", "Осколок Монолита"): "黑石碎片",
    ("物品·武器·装备·神器", "Sviblov's case"): "斯维布洛夫的手提箱",
    ("物品·武器·装备·神器", "Petrenko's Flask"): "彼得连科的水壶",
    ("物品·武器·装备·神器", "Unknown flash drive"): "未知U盘",
    ("物品·武器·装备·神器", "M50Z gas mask"): "M50Z防毒面具",
    ("物品·武器·装备·神器", "Planchette"): "乩板",
    ("物品·武器·装备·神器", "Планшетка"): "乩板",
    ("物品·武器·装备·神器", "Teleport Deactivator"): "传送器干扰装置",
    ("人物与军衔", "the Knuckle-duster"): "指虎",
    ("百科与术语", "psychiatrists trench coat"): "精神科医师风衣",
    ("百科与术语", "borovs trench coat"): "博罗夫的军大衣",
    ("人物与军衔", "Elsa Sakharova"): "艾尔莎·萨哈罗娃",
    ("人物与军衔", "ELSA SAKHAROVA"): "艾尔莎·萨哈罗娃",
}
RU_EN_FIXES = {
    ("人物与军衔", "Волкодав"): "“沃夫”",
    ("人物与军衔", "Глухарь"): "格卢哈里",
    ("人物与军衔", "Гребень"): "格列边",
    ("人物与军衔", "Кастет"): "指虎",
    ("物品·武器·装备·神器", "Коричневый бандитский плащ"): "棕色强盗风衣",
    ("物品·武器·装备·神器", "Чёрный бандитский плащ"): "黑色强盗风衣",
    ("物品·武器·装备·神器", "Плащ психиатра"): "精神科医师风衣",
    ("物品·武器·装备·神器", "Кожанный плащ"): "皮制风衣",
    ("物品·武器·装备·神器", "Именная фляжка Петренко"): "彼得连科的水壶",
    ("物品·武器·装备·神器", "Неизвестная флеш-карта"): "未知U盘",
    ("物品·武器·装备·神器", "Курьерский спецкейс"): "斯维布洛夫的手提箱",
    ("物品·武器·装备·神器", "Противогаз M50з"): "M50Z防毒面具",
    ("物品·武器·装备·神器", "Деактиватор телепорта"): "传送器干扰装置",
    ("任务", "Летающий остров"): "飞行岛",
    ("任务", "Телепортёр"): "传送器",
    ("任务", "Восточная часть Припяти"): "普里皮亚季东区",
    ("任务", "Выполнено квестов"): "已完成任务",
    ("任务", "Убито мутантов"): "击杀变异体",
    ("百科与术语", "Осторожнее"): "小心点",
    ("百科与术语", "Тушканчики"): "变异鼠",
    ("百科与术语", "Броня"): "护甲",
    ("百科与术语", "ОПЦИИ"): "选项",
}
EN_REMOVALS = {
    ("物品·武器·装备·神器", "Vintorez"),
    ("物品·武器·装备·神器", "Винторез"),
    ("物品·武器·装备·神器", "Card"),
    ("物品·武器·装备·神器", "Army uniform"),
    ("物品·武器·装备·神器", "Rubick's Cube"),
    ("物品·武器·装备·神器", "Anomlaous stabilizer"),
    ("百科与术语", "Lake"),
    ("百科与术语", "Level"),
    ("人物与军衔", "the Discrete"),
    ("人物与军衔", "Hammer"),
}
EN_ADDITIONS = {
    ("物品·武器·装备·神器", "Browning High-Power"): "勃朗宁大威力手枪",
    ("物品·武器·装备·神器", "Circuit board"): "电路板",
    ("物品·武器·装备·神器", "Army body armor"): "军用防弹背心",
    ("物品·武器·装备·神器", "Anomalous stabilizer"): "异常稳定器",
    ("人物与军衔", "Hummer"): "悍马",
}

CANON_CATS = ["阵营与组织", "地点与区域", "变异生物",
              "物品·武器·装备·神器"]
NAME_CATS = ["人物与军衔"]
REF_CATS = ["ANTHOLOGY 参考"]
TECH_CAT = "技术缩写"

# 小写单单词拉丁硬词（bar/crystal/star/lift…）在英文正文里与普通名词
# 冲突；运行时改为“首字母大小写必须一致”，大写专名仍然命中。
RISKY_LATIN_RE = re.compile(r"^[a-z][a-z0-9._\-/&()]{1,18}$")


def read_technical_exact():
    """从插件源码读取 _TECHNICAL_EXACT，避免两处维护同一张表。"""
    tree = ast.parse(io.open(PLUGIN, encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(getattr(t, "id", "") == "_TECHNICAL_EXACT"
               for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return {str(k): str(v) for k, v in value.items()}
    raise SystemExit("插件里找不到 _TECHNICAL_EXACT")


def write_json(path, data):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def main():
    data = json.load(io.open(TERMBASE, encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("术语库格式不是对象")

    fixed = 0
    added = 0
    en_added = 0
    for (cat, key), want in FIXES.items():
        table = data.get(cat)
        if isinstance(table, dict) and key in table and table[key] != want:
            table[key] = want
            fixed += 1
    removed = 0
    for cat, key in REMOVALS:
        table = data.get(cat)
        if isinstance(table, dict) and key in table:
            del table[key]
            removed += 1
    for (cat, key), want in EN_FIXES.items():
        table = data.get(cat)
        if isinstance(table, dict) and key in table and table[key] != want:
            table[key] = want
            fixed += 1
    for (cat, key), want in RU_EN_FIXES.items():
        table = data.get(cat)
        if isinstance(table, dict) and key in table and table[key] != want:
            table[key] = want
            fixed += 1
    for cat, key in EN_REMOVALS:
        table = data.get(cat)
        if isinstance(table, dict) and key in table:
            del table[key]
            removed += 1
    for (cat, key), want in EN_ADDITIONS.items():
        table = data.get(cat)
        if isinstance(table, dict) and key not in table:
            table[key] = want
            en_added += 1

    technical = read_technical_exact()
    tech_table = data.get(TECH_CAT)
    if not isinstance(tech_table, dict):
        tech_table = {}
        data[TECH_CAT] = tech_table
    for key, value in technical.items():
        if key not in tech_table:
            tech_table[key] = value
            added += 1

    # 与词条数据同步：变体源和规范词条可以跨层，只要都存在就写映射。
    existing = set()
    for cat in CANON_CATS + NAME_CATS + REF_CATS:
        table = data.get(cat)
        if isinstance(table, dict):
            existing.update(str(k) for k in table)
    variants = {src: canon for src, canon in VARIANTS.items()
                if src in existing and canon in existing}

    # 统计 + 元数据 -----------------------------------------------------
    cats = {k: v for k, v in data.items()
            if isinstance(v, dict) and not k.startswith("_")}
    total = sum(len(t) for t in cats.values())
    risky = sorted(
        key for cat in CANON_CATS for key in cats.get(cat, {})
        if RISKY_LATIN_RE.match(key))
    meta = data.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
        data["_meta"] = meta
    sources = list(meta.get("sources") or [])
    v2_note = ("tools/upgrade_termbase_v2.py (canon/names/technical layers, "
               "variant map, data-driven technical exact table)")
    if v2_note not in sources:
        sources.append(v2_note)
    meta.update({
        "format": "stalker-toolkit-termbase/2",
        "built": date.today().isoformat(),
        "builder": "tools/build_termbase.py + tools/upgrade_termbase_v2.py",
        "total_entries": total,
        "categories": {k: len(v) for k, v in cats.items()},
        "sources": sources,
        "layers": {
            "canon": CANON_CATS,
            "names": NAME_CATS,
            "technical": [TECH_CAT],
            "reference": REF_CATS,
        },
        "variants": variants,
        "case_sensitive": risky,
        "case_sensitive_note": (
            "names 层与 technical 拉丁缩写默认首字母大小写敏感；"
            "case_sensitive 列出的 canon 小写单单词也要求首字母一致。"),
    })
    meta["note"] = (
        "插件「内置术语库 / 导入术语表 JSON」选择本文件即可；分类段落自动摊平"
        "合并，_meta 自动跳过。v2 的 canon/names/technical 是运行时分层，"
        "不改变导入格式。")

    write_json(TERMBASE, data)
    write_json(PLUGIN_COPY, data)

    flat = {}
    for cat, table in cats.items():
        for key, value in table.items():
            flat[str(key)] = str(value)
    # 随包扁平层保持历史格式：单行紧凑 + 排序，避免每次升级产生整文件 diff。
    tmp = BUILTIN + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(flat, f, ensure_ascii=False, separators=(",", ":"),
                  sort_keys=True)
    os.replace(tmp, BUILTIN)

    print("v2 升级完成：总 %d 条，技术词新增 %d，英文补录 %d，修正 %d，移除 %d，变体 %d"
          % (total, added, en_added, fixed, removed, len(variants)))
    print("canon %d / names %d / technical %d / reference %d"
          % (sum(len(cats.get(c, {})) for c in CANON_CATS),
             sum(len(cats.get(c, {})) for c in NAME_CATS),
             len(cats.get(TECH_CAT, {})),
             sum(len(cats.get(c, {})) for c in REF_CATS)))
    print("大小写敏感 canon：%d 条" % len(risky))
    print("变体：%s" % ", ".join("%s→%s" % kv for kv in variants.items()))


if __name__ == "__main__":
    main()
