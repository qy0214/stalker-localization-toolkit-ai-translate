#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从当前 MOD 的底版 + 汉化补丁抽一份“参考词表”（可选，不写入通用库）。

用途：用户手上有某个 MOD 的汉化补丁时，先抽它的高置信名词，作为本次任务的
参考词表导入插件（术语 -> 导入JSON）。它不是随包术语库，不会污染通用层。

用法：
    python tools/build_mod_terms.py --base "D:\\MOD\\gamedata" \
        --patch "D:\\MOD\\汉化\\gamedata" --out "D:\\MOD\\mod_terms.json"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_glossary as bg  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
CYR = re.compile(r"[А-Яа-яЁё]")
STR = re.compile(r'<string\s+id="([^"]+)"[^>]*>(.*?)</string>', re.S)
TAG = re.compile(r"<(rus|eng|ukr|tur|text)>(.*?)</\1>", re.S)


def read(path):
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", "replace")


def parse(path):
    out = {}
    for m in STR.finditer(read(path)):
        out.setdefault(m.group(1), {
            t.group(1): t.group(2) for t in TAG.finditer(m.group(2))})
    return out


def value(tags):
    for key in ("rus", "text", "eng"):
        v = tags.get(key, "")
        if v.strip() and not CJK.search(v):
            return v.strip()
    return ""


def target(tags):
    for key in ("rus", "text"):
        v = tags.get(key, "")
        if v.strip() and CJK.search(v):
            return v.strip()
    return ""


def category(rel):
    s = rel.lower().replace(os.sep, "/")
    if any(x in s for x in ("character_name", "snames", "fnames", "bio")):
        return "人物与军衔"
    if any(x in s for x in ("faction", "community")):
        return "阵营与组织"
    if any(x in s for x in ("item", "weapon", "equipment", "artefact",
                            "artifact")):
        return "物品·武器·装备·神器"
    if any(x in s for x in ("creature", "mutant", "monster")):
        return "变异生物"
    if any(x in s for x in ("mapspot", "level", "place", "zone")):
        return "地点与区域"
    return "百科与术语"


def term_ok(src, cat):
    s = src.strip().strip("\"'«»“”‘’").strip()
    if not s or len(s) > 60 or not (CYR.search(s) or re.search(r"[A-Za-z]", s)):
        return ""
    if "\n" in s or "\r" in s:
        return ""
    if cat == "人物与军衔":
        if re.search(r"[。！？!?；;:，,]", s):
            return ""
        return s if s[0].isupper() else ""
    if not bg.ru_is_term(s):
        return ""
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--patch", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    result = {}
    for base_root, _dirs, files in os.walk(args.base):
        for fn in files:
            if not fn.lower().endswith(".xml"):
                continue
            bp = os.path.join(base_root, fn)
            rel = os.path.relpath(bp, args.base)
            # SCAR 等补丁同时有 config/text/rus 与 chs；优先 chs。
            alt = rel.replace(os.sep + "rus" + os.sep, os.sep + "chs" + os.sep)
            alt2 = rel.replace("/rus/", "/chs/")
            pp = ""
            for cand in (os.path.join(args.patch, alt),
                         os.path.join(args.patch, alt2),
                         os.path.join(args.patch, rel)):
                if os.path.isfile(cand):
                    pp = cand
                    break
            if not pp:
                continue
            b = parse(bp)
            p = parse(pp)
            cat = category(rel)
            for eid, tags in b.items():
                src = term_ok(value(tags), cat)
                dst = target(p.get(eid, {}))
                if src and dst and len(dst) <= 30:
                    result.setdefault(src, dst)

    payload = {
        "_meta": {
            "format": "stalker-toolkit-mod-terms/1",
            "built": date.today().isoformat(),
            "base": os.path.abspath(args.base),
            "patch": os.path.abspath(args.patch),
            "note": "仅作当前 MOD 参考，不写入通用术语库；补丁可能有误。",
            "entries": len(result),
        },
        "参考补丁": dict(sorted(result.items())),
    }
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("entries", len(result), "->", args.out)


if __name__ == "__main__":
    main()
