#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标准术语库质量审计（v2）。

检查的不是“译文好不好”（那需要人工/语境），而是**能自动证明的结构问题**：
跨层同源冲突、大小写/ё 变体打架、空值、占位符、CJK 缺失、长句误入硬层、
技术词漏登记、随包副本不一致等。输出：
    tools/termbase_audit_report.md
    tools/termbase_audit.json

用法：
    python tools/audit_termbase.py [--strict]
"""
import ast
import collections
import io
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TERMBASE = os.path.join(HERE, "stalker_termbase.json")
PLUGIN_COPY = os.path.join(ROOT, "plugins", "ai_translate_assets",
                           "stalker_termbase.json")
BUILTIN = os.path.join(ROOT, "plugins", "ai_translate_assets",
                       "glossary_builtin.json")
PLUGIN = os.path.join(ROOT, "plugins", "ai_translate.py")
REPORT_MD = os.path.join(HERE, "termbase_audit_report.md")
REPORT_JSON = os.path.join(HERE, "termbase_audit.json")

CJK = re.compile(r"[\u3400-\u9fff]")
CYR = re.compile(r"[\u0400-\u04ff]")
LAT = re.compile(r"[A-Za-z]")
PH = re.compile(r"\$[A-Za-z_]\w*|%[sdif]|%c\[[^\]]+\]|\\[nt]")
QUOTES = "\"'«»“”‘’"
TERMINAL = re.compile(r"[.!?;:]$")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.casefold().replace("ё", "е")
    s = s.replace("«", '"').replace("»", '"').replace("“", '"') \
         .replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().strip(QUOTES).strip()
    return s


def read_tech():
    tree = ast.parse(io.open(PLUGIN, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_TECHNICAL_EXACT"
                for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def add(rows, severity, code, detail, samples=()):
    rows.append({"severity": severity, "code": code, "detail": detail,
                 "samples": list(samples)[:20]})


def main():
    data = json.load(io.open(TERMBASE, encoding="utf-8"))
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    cats = {k: v for k, v in data.items()
            if isinstance(v, dict) and not k.startswith("_")}
    layers = meta.get("layers") if isinstance(meta.get("layers"), dict) else {}
    canon_cats = layers.get("canon") or ["阵营与组织", "地点与区域",
                                        "变异生物", "物品·武器·装备·神器"]
    name_cats = layers.get("names") or ["人物与军衔"]
    tech_cats = layers.get("technical") or ["技术缩写"]
    ref_cats = layers.get("reference") or ["ANTHOLOGY 参考"]
    canon = set(k for c in canon_cats for k in cats.get(c, {}))
    names = set(k for c in name_cats for k in cats.get(c, {}))
    tech = set(k for c in tech_cats for k in cats.get(c, {}))
    refs = set(k for c in ref_cats for k in cats.get(c, {}))

    items = [(c, str(k), str(v)) for c, t in cats.items()
             for k, v in t.items()]
    total = len(items)
    rows = []
    summary = {}

    # 1) 元数据与结构 ----------------------------------------------------
    bad_meta = []
    if meta.get("format") != "stalker-toolkit-termbase/2":
        bad_meta.append("format=%r" % meta.get("format"))
    if int(meta.get("total_entries") or -1) != total:
        bad_meta.append("total_entries=%r != %d"
                        % (meta.get("total_entries"), total))
    if meta.get("categories") != {k: len(v) for k, v in cats.items()}:
        bad_meta.append("categories 统计不一致")
    if not layers or not all(isinstance(layers.get(x), list)
                             for x in ("canon", "names", "technical")):
        bad_meta.append("layers 缺少 canon/names/technical")
    add(rows, "blocking" if bad_meta else "ok", "schema",
        "v2 元数据/分层" + ("：" + "；".join(bad_meta) if bad_meta else " 完整"))

    # 2) 跨层同源冲突 ----------------------------------------------------
    by = collections.defaultdict(list)
    for c, k, v in items:
        by[norm(k)].append((c, k, v))
    conflicts = [(x, y) for x, y in by.items()
                 if len({norm(v) for _, _, v in y}) > 1]
    # 引号形态不同（"Loners" vs Loners）在游戏表里是不同源串：
    # 前者是阵营名，后者可能是普通名词/百科词。它们不能自动合并，
    # 归为 medium 提示，交给人工按语境确认。
    quoted_only, real_conflicts = [], []
    for x, y in conflicts:
        has_q = [bool(k[:1] in QUOTES or k[-1:] in QUOTES) for _, k, _ in y]
        (quoted_only if any(has_q) and not all(has_q)
         else real_conflicts).append((x, y))
    add(rows, "high" if real_conflicts else "ok", "same_source_conflict",
        "规范化同源、译文不同（不含仅引号差异）：%d 组"
        % len(real_conflicts),
        ["%r -> %s" % (x, [(c, k, v) for c, k, v in y])
         for x, y in real_conflicts])
    add(rows, "medium" if quoted_only else "ok", "quoted_vs_bare",
        "引号形态不同且译文不同（不同源串，运行时不合并）：%d 组"
        % len(quoted_only),
        ["%r -> %s" % (x, [(c, k, v) for c, k, v in y])
         for x, y in quoted_only])

    # 大小写冲突（不折叠引号/变体，只看 casefold）
    by_case = collections.defaultdict(list)
    for c, k, v in items:
        by_case[unicodedata.normalize("NFKC", k).casefold()].append(
            (c, k, v))
    case_conf = [(x, y) for x, y in by_case.items()
                 if len({v for _, _, v in y}) > 1]
    # 引号形态不同导致的“同字母不同译名”不阻塞：源串本身不同。
    case_conf = [(x, y) for x, y in case_conf
                 if len({k.strip(QUOTES).casefold() for _, k, _ in y}) == 1]
    add(rows, "high" if case_conf else "ok", "case_conflict",
        "仅大小写/ё 不同且译文冲突：%d 组" % len(case_conf),
        ["%r -> %s" % (x, [(c, k, v) for c, k, v in y])
         for x, y in case_conf])

    # 3) 空值/异常字符 --------------------------------------------------
    empty = [(c, k, v) for c, k, v in items if not k.strip() or not v.strip()]
    add(rows, "blocking" if empty else "ok", "empty",
        "空原文/空译文：%d 条" % len(empty), empty)
    cjk_src = [(c, k, v) for c, k, v in items if CJK.search(k)]
    add(rows, "high" if cjk_src else "ok", "cjk_source",
        "原文含中文：%d 条" % len(cjk_src), cjk_src)
    no_cjk = [(c, k, v) for c, k, v in items
              if not CJK.search(v) and c not in tech_cats]
    # PDA 是游戏内通用缩写，保留英文是正确口径；不算缺陷。
    no_cjk = [x for x in no_cjk if x[1].upper() not in ("PDA", "КПК", "ПДА")]
    add(rows, "medium" if no_cjk else "ok", "target_no_cjk",
        "非技术层译文不含中文：%d 条" % len(no_cjk), no_cjk)
    identity = [(c, k, v) for c, k, v in items
                if norm(k) == norm(v) and c not in tech_cats]
    add(rows, "low" if identity else "ok", "identity",
        "非技术层原文=译文：%d 条" % len(identity), identity)

    # 4) 占位符/长句/硬层形状 -------------------------------------------
    ph_bad = []
    for c, k, v in items:
        if sorted(PH.findall(k)) != sorted(PH.findall(v)):
            ph_bad.append((c, k, v))
    add(rows, "high" if ph_bad else "ok", "placeholder",
        "占位符集合不一致：%d 条" % len(ph_bad), ph_bad)

    long_canon = [(c, k, v) for c, k in [(c, k) for c, k, _ in items
                                        if c in canon_cats]
                  for v in [next(x[2] for x in items
                                 if x[0] == c and x[1] == k)]
                  if len(k.split()) > 8 or TERMINAL.search(k.strip())]
    add(rows, "medium" if long_canon else "ok", "sentence_in_canon",
        "canon 层疑似长句/带句末标点：%d 条" % len(long_canon), long_canon)

    # 屈折形冲突：同一词干（俄语常见词尾归一）在 canon 里给了不同译名。
    # 同一分类内视为 high；跨分类（如 Генератор 装置 / Генераторы 地点）
    # 只提示，可能是两个真实概念。
    suffixes = ("ыми", "ими", "ого", "его", "ому", "ему", "ами", "ями",
                "ах", "ях", "их", "ых", "ов", "ев", "ый", "ий", "ой",
                "ая", "ое", "ые", "ие", "ую", "юю", "ом", "ем", "ам",
                "ям", "ы", "и", "а", "я", "у", "ю", "е", "о", "ь")

    def stem_word(w):
        w = w.casefold().replace("ё", "е")
        for suf in sorted(suffixes, key=len, reverse=True):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                return w[:-len(suf)]
        return w

    groups = collections.defaultdict(list)
    for c, k, v in items:
        if c not in canon_cats:
            continue
        words = [stem_word(x) for x in re.findall(
            r"[А-Яа-яЁёA-Za-z0-9-]+", k.strip(QUOTES))]
        if words:
            groups[tuple(words)].append((c, k, v))
    infl_same, infl_cross = [], []
    for _stem, group in groups.items():
        vals = {norm(v) for _c, _k, v in group}
        if len(vals) < 2:
            continue
        group_cats = {c for c, _k, _v in group}
        (infl_same if len(group_cats) == 1 else infl_cross).append(group)
    add(rows, "high" if infl_same else "ok", "inflection_conflict",
        "同分类屈折形译名冲突：%d 组" % len(infl_same),
        [" / ".join("%s:%s→%s" % (c, k, v) for c, k, v in g)
         for g in infl_same])
    add(rows, "medium" if infl_cross else "ok", "inflection_cross_cat",
        "跨分类同词干不同译名（可能是不同概念）：%d 组" % len(infl_cross),
        [" / ".join("%s:%s→%s" % (c, k, v) for c, k, v in g)
         for g in infl_cross])

    risky = sorted(k for k in canon if re.fullmatch(
        r"[a-z][a-z0-9._\-/&()]{1,18}", k))
    add(rows, "low", "risky_latin_canon",
        "小写单单词拉丁 canon（运行时仅认大写专名）：%d 条" % len(risky),
        risky)

    # 5) 技术词数据化 ----------------------------------------------------
    tech_plugin = read_tech()
    missing_tech = sorted(set(tech_plugin) - tech)
    add(rows, "high" if missing_tech else "ok", "technical_missing",
        "插件 _TECHNICAL_EXACT 未登记进术语库：%d 条" % len(missing_tech),
        missing_tech)
    tech_diff = [(k, tech_plugin[k], data.get(tech_cats[0], {}).get(k))
                 for k in tech_plugin
                 if data.get(tech_cats[0], {}).get(k) not in (None,
                                                              tech_plugin[k])]
    add(rows, "high" if tech_diff else "ok", "technical_mismatch",
        "技术词译文与插件常量不一致：%d 条" % len(tech_diff), tech_diff)

    # 6) 变体/大小写策略一致性 ------------------------------------------
    variants = meta.get("variants") if isinstance(meta.get("variants"),
                                                  dict) else {}
    all_map = {k: v for _c, k, v in items}
    bad_var = [(s, c) for s, c in variants.items()
               if s not in all_map or c not in all_map]
    add(rows, "high" if bad_var else "ok", "variant_target",
        "变体映射指向不存在的词条：%d 条" % len(bad_var), bad_var)
    diff_var = [(s, c, all_map.get(s), all_map.get(c))
                for s, c in variants.items()
                if s in all_map and c in all_map
                and norm(all_map[s]) != norm(all_map[c])]
    add(rows, "high" if diff_var else "ok", "variant_value",
        "变体与规范词条译文不同：%d 条" % len(diff_var), diff_var)
    case_list = meta.get("case_sensitive") if isinstance(
        meta.get("case_sensitive"), list) else []
    missing_case = [k for k in risky if k not in set(case_list)]
    add(rows, "medium" if missing_case else "ok", "case_policy",
        "风险拉丁词未写入 case_sensitive：%d 条" % len(missing_case),
        missing_case)

    # 7) 副本一致性 ------------------------------------------------------
    copies = []
    for path in (PLUGIN_COPY,):
        try:
            same = io.open(TERMBASE, "rb").read() == io.open(path, "rb").read()
        except OSError:
            same = False
        copies.append((path, same))
    flat = {}
    for c, t in cats.items():
        for k, v in t.items():
            flat[str(k)] = str(v)
    builtin = {}
    try:
        builtin = json.load(io.open(BUILTIN, encoding="utf-8"))
    except Exception:
        pass
    add(rows, "blocking" if not all(x[1] for x in copies) else "ok",
        "copy_sync", "工具库与随包副本：" + "；".join(
            "%s=%s" % (os.path.relpath(p, ROOT), "一致" if ok else "不一致")
            for p, ok in copies))
    add(rows, "blocking" if builtin != flat else "ok", "builtin_sync",
        "glossary_builtin.json 与分类库摊平结果：%s"
        % ("一致" if builtin == flat else "不一致"))

    # 8) 统计 ------------------------------------------------------------
    summary = {
        "format": meta.get("format"),
        "total": total,
        "layers": {
            "canon": len(canon), "names": len(names),
            "technical": len(tech), "reference": len(refs),
        },
        "categories": {k: len(v) for k, v in cats.items()},
        "variants": len(variants),
        "case_sensitive": len(case_list),
        "risky_latin_canon": len(risky),
        "quoted_keys": sum(1 for _, k, _ in items
                           if k[:1] in QUOTES or k[-1:] in QUOTES),
        "cross_layer_conflicts": len(conflicts),
        "case_conflicts": len(case_conf),
        "checks": rows,
        "severity": {
            s: sum(1 for r in rows if r["severity"] == s)
            for s in ("blocking", "high", "medium", "low", "ok")
        },
    }
    io.open(REPORT_JSON, "w", encoding="utf-8", newline="\n").write(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    lines = [
        "# 标准术语库 v2 质量审计",
        "",
        "- 生成：`tools/audit_termbase.py`",
        "- 术语库：`%d` 条；canon `%d`、names `%d`、technical `%d`、"
        "reference `%d`" % (total, len(canon), len(names), len(tech),
                            len(refs)),
        "- 严重度：blocking %d / high %d / medium %d / low %d / ok %d"
        % (summary["severity"]["blocking"], summary["severity"]["high"],
           summary["severity"]["medium"], summary["severity"]["low"],
           summary["severity"]["ok"]),
        "",
        "## 结论",
        "",
    ]
    blocking = summary["severity"]["blocking"]
    high = summary["severity"]["high"]
    if blocking:
        lines.append("存在 **blocking** 问题，术语库不应发布。")
    elif high:
        lines.append("存在 **high** 问题，需修复后再发布；"
                     "其余为可接受的结构性风险。")
    else:
        lines.append("无 blocking / high 问题；当前术语库可随包发布。"
                     "medium/low 为已知边界，运行时已按层/大小写策略规避。")
    lines += ["", "## 明细", "",
              "| 严重度 | 检查 | 结果 |", "|---|---|---|"]
    for r in rows:
        detail = r["detail"].replace("|", "\\|")
        lines.append("| %s | `%s` | %s |"
                     % (r["severity"], r["code"], detail))
    for r in rows:
        if r["samples"] and r["severity"] in ("high", "medium", "blocking"):
            lines += ["", "### %s 样例" % r["code"], "", "```text"]
            lines += [str(x) for x in r["samples"][:20]]
            lines += ["```"]
    lines += ["", "## 已知边界（不是本轮能自动证明的）", "",
              "- 语义误译（同名不同人、同词不同语境）只能靠人工/语境抽查；",
              "- 带引号的 158 条词条与不带引号的同名词条是不同源串，"
              "审计不把它们合并；",
              "- names 层 %d 条只做整条精确/首字母大写提示，"
              "不进入强制替换。" % len(names), ""]
    io.open(REPORT_MD, "w", encoding="utf-8", newline="\n").write(
        "\n".join(lines))

    print("审计完成：%d 条；blocking %d / high %d / medium %d / low %d"
          % (total, blocking, high, summary["severity"]["medium"],
             summary["severity"]["low"]))
    print("报告：%s" % REPORT_MD)
    if "--strict" in sys.argv and (blocking or high):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
