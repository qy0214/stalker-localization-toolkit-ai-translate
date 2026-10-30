#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 multisource_terms.json 合并进术语库与随包 glossary_builtin.json。

只做两件事：
  * overrides：人工确认过的旧词纠错（按小写匹配，保留原分类）；
  * additions：新词（按分类插入）。

不自动覆盖未确认的冲突；冲突只在 build_multisource_terms.py 的报告里。

用法：
    python tools/merge_multisource_terms.py
    python tools/merge_multisource_terms.py --payload tools/multisource_terms.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TERMBASE = os.path.join(HERE, "stalker_termbase.json")
PLUGIN_TERMBASE = os.path.join(ROOT, "plugins", "ai_translate_assets",
                               "stalker_termbase.json")
PLUGIN_BUILTIN = os.path.join(ROOT, "plugins", "ai_translate_assets",
                              "glossary_builtin.json")
DEFAULT_PAYLOAD = os.path.join(HERE, "multisource_terms.json")
MERGE_REPORT = os.path.join(HERE, "multisource_merge_report.md")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def apply(payload):
    tb = load_json(TERMBASE)
    cats = [k for k, v in tb.items()
            if not k.startswith("_") and isinstance(v, dict)]

    # 建立小写索引：同一个词可能只差大小写
    index = {}
    for cat in cats:
        for key in list(tb[cat]):
            index.setdefault(key.lower(), (cat, key))

    applied_over = []
    for src, row in payload.get("overrides", {}).items():
        hit = index.get(src)
        if not hit:
            continue
        cat, key = hit
        old = tb[cat].get(key)
        new = row.get("zh")
        if not new or new == old:
            continue
        tb[cat][key] = new
        applied_over.append((key, old, new, cat, row.get("reason", "")))

    applied_moves = []
    for src, cat in payload.get("moves", {}).items():
        hit = index.get(src.lower())
        if not hit or cat not in tb:
            continue
        old_cat, key = hit
        if old_cat == cat:
            continue
        zh = tb[old_cat].pop(key, None)
        if zh is None:
            continue
        tb[cat][key] = zh
        index[key.lower()] = (cat, key)
        applied_moves.append((key, old_cat, cat))

    applied_add = []
    for src, row in payload.get("additions", {}).items():
        cat = row.get("category")
        zh = row.get("zh")
        if not cat or not zh or cat not in tb:
            continue
        hit = index.get(src.lower())
        if hit:
            old_cat, old_key = hit
            if old_cat != cat and tb[old_cat].get(old_key) == zh:
                tb[old_cat].pop(old_key, None)
                tb[cat][old_key] = zh
                index[old_key.lower()] = (cat, old_key)
                applied_moves.append((old_key, old_cat, cat))
            continue
        tb[cat][src] = zh
        index[src.lower()] = (cat, src)
        applied_add.append((src, zh, cat))

    # 重新排序并更新元信息
    total = 0
    cat_counts = {}
    for cat in cats:
        ordered = dict(sorted(tb[cat].items(), key=lambda kv: (-len(kv[0]),
                                                               kv[0])))
        tb[cat] = ordered
        cat_counts[cat] = len(ordered)
        total += len(ordered)

    meta = tb.setdefault("_meta", {})
    meta["built"] = date.today().isoformat()
    meta["total_entries"] = total
    meta["categories"] = cat_counts
    srcs = list(meta.get("sources") or [])
    extra = ("tools/multisource_terms.json (NS7 9/6, 7/26, 7/6, 6/19; "
             "NS 2/19; OP; OLR25; scar; WOT; high-confidence noun terms)")
    if extra not in srcs:
        srcs.append(extra)
    meta["sources"] = srcs
    meta["multisource"] = {
        "additions": len(applied_add),
        "overrides": len(applied_over),
        "payload": payload.get("_meta", {}).get("built", ""),
    }

    write_json(TERMBASE, tb)
    write_json(PLUGIN_TERMBASE, tb)

    flat = {}
    for cat in cats:
        flat.update(tb[cat])
    write_json(PLUGIN_BUILTIN, dict(sorted(flat.items())))

    lines = [
        "# 多源术语合并报告",
        "",
        "日期：%s" % date.today().isoformat(),
        "",
        "| 项 | 数量 |",
        "|---|---:|",
        "| 新增 | %d |" % len(applied_add),
        "| 覆盖 | %d |" % len(applied_over),
        "| 移动分类 | %d |" % len(applied_moves),
        "| 术语库总数 | %d |" % total,
        "",
        "## 覆盖明细",
        "",
        "| 原文 | 旧译名 | 新译名 | 分类 | 理由 |",
        "|---|---|---|---|---|",
    ]
    for key, old, new, cat, reason in applied_over:
        lines.append("| %s | %s | %s | %s | %s |" % (
            key, old, new, cat, reason))
    lines += ["", "## 移动分类", ""]
    for key, old_cat, cat in applied_moves:
        lines.append("| %s | %s → %s |" % (key, old_cat, cat))
    lines += ["", "## 新增明细", "",
              "| 原文 | 译名 | 分类 |", "|---|---|---|"]
    for src, zh, cat in applied_add:
        lines.append("| %s | %s | %s |" % (src, zh, cat))
    with open(MERGE_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("applied additions %d, overrides %d, total %d"
          % (len(applied_add), len(applied_over), total))
    print(" ->", TERMBASE)
    print(" ->", PLUGIN_TERMBASE)
    print(" ->", PLUGIN_BUILTIN)
    return {
        "additions": len(applied_add),
        "overrides": len(applied_over),
        "moves": len(applied_moves),
        "total": total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=DEFAULT_PAYLOAD)
    args = ap.parse_args()
    payload = load_json(args.payload)
    apply(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
