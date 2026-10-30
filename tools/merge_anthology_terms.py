#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 ANTHOLOGY 2.1 的 A/B 档合并进通用术语库。

A 档：148 条高置信新词（53 人物 + 95 物品/装备），全部放在
      `ANTHOLOGY 参考` 软分类，不进入硬词；
B 档：只应用人工确认的三处纠错（Мутант/Мутанты→变异体，Седых→谢德赫）；
      其它冲突保留现状；Шрам 不并入通用库。

用法：
    python tools/merge_anthology_terms.py
"""
from __future__ import annotations

import json
import os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAYLOAD = os.path.join(HERE, "anthology_terms_soft.json")
TERMBASE = os.path.join(HERE, "stalker_termbase.json")
PLUGIN_TERMBASE = os.path.join(ROOT, "plugins", "ai_translate_assets",
                               "stalker_termbase.json")
PLUGIN_BUILTIN = os.path.join(ROOT, "plugins", "ai_translate_assets",
                              "glossary_builtin.json")
REPORT = os.path.join(HERE, "anthology_merge_report.md")
REF_CAT = "ANTHOLOGY 参考"


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    payload = load(PAYLOAD)
    tb = load(TERMBASE)
    cats = [k for k, v in tb.items()
            if not k.startswith("_") and isinstance(v, dict)]

    tb[REF_CAT] = {}
    index = {}
    for cat in cats:
        for key in tb[cat]:
            index.setdefault(key.lower(), (cat, key))

    added = []
    for src, zh in payload.get("terms", {}).items():
        if src.lower() in index:
            continue
        tb[REF_CAT][src] = zh
        index[src.lower()] = (REF_CAT, src)
        added.append((src, zh))

    extra_added = []
    for src, row in payload.get("additions", {}).items():
        cat = row.get("category")
        zh = row.get("zh")
        if not cat or not zh or cat not in tb:
            continue
        if src.lower() in index:
            continue
        tb[cat][src] = zh
        index[src.lower()] = (cat, src)
        extra_added.append((src, zh, cat))

    corrected = []
    old_map = payload.get("corrections_old", {})
    for src, zh in payload.get("corrections", {}).items():
        hit = index.get(src.lower())
        if not hit:
            continue
        cat, key = hit
        old = old_map.get(src, tb[cat].get(key))
        if tb[cat].get(key) != zh:
            tb[cat][key] = zh
        corrected.append((key, old, zh, cat))

    total = 0
    counts = {}
    for cat in list(tb):
        if cat.startswith("_") or not isinstance(tb[cat], dict):
            continue
        tb[cat] = dict(sorted(tb[cat].items(),
                              key=lambda kv: (-len(kv[0]), kv[0])))
        counts[cat] = len(tb[cat])
        total += counts[cat]

    meta = tb.setdefault("_meta", {})
    meta["built"] = date.today().isoformat()
    meta["total_entries"] = total
    meta["categories"] = counts
    layers = meta.setdefault("reference_layers", {})
    layers[REF_CAT] = {
        "source": "ANTHOLOGY2.1 汉化 2026-10-30",
        "entries": len(added),
        "hard": False,
        "note": "只做软参考；其它冲突未自动覆盖；Шрам 未并入",
    }
    sources = list(meta.get("sources") or [])
    line = ("tools/anthology_terms_soft.json (ANTHOLOGY2.1 A+B, "
            "148 soft terms + 3 corrections)")
    if line not in sources:
        sources.append(line)
    meta["sources"] = sources

    write(TERMBASE, tb)
    write(PLUGIN_TERMBASE, tb)
    flat = {}
    for cat in cats:
        flat.update(tb.get(cat, {}))
    flat.update(tb.get(REF_CAT, {}))
    write(PLUGIN_BUILTIN, dict(sorted(flat.items())))

    lines = [
        "# ANTHOLOGY 2.1 术语合并报告",
        "",
        "日期：%s" % date.today().isoformat(),
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
        "| A 档新增软词 | %d |" % len(added),
        "| B 档人工纠错 | %d |" % len(corrected),
        "| B 档补充标准名 | %d |" % len(extra_added),
        "| 术语库总数 | %d |" % total,
        "",
        "> `ANTHOLOGY 参考` 不在硬词分类中；只做精确/软提示，不跨 MOD 硬替换。",
        "> `Шрам` 未并入；留给会话词表/参考补丁。",
        "",
        "## B 档纠错",
        "",
        "| 原文 | 旧 | 新 | 分类 |",
        "|---|---|---|---|",
    ]
    for key, old, new, cat in corrected:
        lines.append("| %s | %s | %s | %s |" % (key, old, new, cat))
    if extra_added:
        lines += ["", "## B 档补充标准名", "",
                  "| 原文 | 译文 | 分类 |", "|---|---|---|"]
        for src, zh, cat in extra_added:
            lines.append("| %s | %s | %s |" % (src, zh, cat))
    lines += ["", "## A 档新增", "",
              "| 原文 | 译文 | 分类 |", "|---|---|---|"]
    for src, zh in added:
        lines.append("| %s | %s | %s |" % (
            src, zh, payload["categories"].get(src, "")))
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("added %d, corrected %d, extra %d, total %d"
          % (len(added), len(corrected), len(extra_added), total))
    print("->", TERMBASE)
    print("->", PLUGIN_TERMBASE)
    print("->", PLUGIN_BUILTIN)


if __name__ == "__main__":
    main()
