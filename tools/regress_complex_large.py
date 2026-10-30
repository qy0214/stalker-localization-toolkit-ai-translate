#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复杂脚本 + 大文本表回归（v2 / TM-lite 实测）。

复用 2026-10-30 已冻结的 30 个输入：
  S01..S20  20 个复杂脚本（含 89 条内联俄文字面文本）
  Q01..Q10  10 个大文本表（各 20 条最长条目，带 NS/SCAR 中文参考）

每个用例单独跑一次真实 1.8B 引擎，输出到临时目录，统计：
  * 是否完成、请求数、耗时；
  * 大文本表与中文参考的相似度（并对比旧版基线 results.json）；
  * 术语采纳、中文覆盖、残留西里尔、占位符；
  * 脚本字面层：包装数、生成表条目、中文条目数。

用法：
    python tools/regress_complex_large.py
"""
import argparse
import difflib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from xml.sax.saxutils import escape as xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE = os.path.join(DESKTOP, "对比报告_20261030",
                    "rounds_random_final_20261030")
OUT = os.path.join(DESKTOP, "对比报告_20261030",
                   "regress_complex_large_v2_20261030")
DEFAULT_EXE = os.path.join(DESKTOP, "STALKER Localization Toolkit 1.1.0",
                           "plugins", "ai_translate_assets", "bin", "cuda",
                           "llama-server.exe")
DEFAULT_MODEL = os.path.join(DESKTOP, "STALKER Localization Toolkit 1.1.0",
                             "plugins", "ai_translate_assets", "models",
                             "Hy-MT2-1.8B-Q8_0.gguf")
DEFAULT_SRC = r"C:\game\NS7\gamedata\configs\text"
DEFAULT_PATCH = (r"C:\game\NS7\MODS"
                 r"\NS OGSR 2026年9月6日版本汉化补丁\gamedata\configs\text")
CJK = re.compile(r"[\u3400-\u9fff]")
CYR = re.compile(r"[\u0400-\u04ff]")
QUOTES = "\"'“”‘’«»"
TOKEN = re.compile(r"\$\$[A-Za-z0-9_]+\$\$|\$[A-Za-z_]\w*|"
                   r"%c\[[^\]]*\]|%[-+ #0-9.*$]*[A-Za-z]")


def load_plugin():
    tmp = tempfile.mkdtemp(prefix="ait_reg_")
    shutil.copy2(os.path.join(ROOT, "plugins", "ai_translate.py"), tmp)
    assets = os.path.join(tmp, "ai_translate_assets")
    os.makedirs(assets)
    for name in ("xray_pkg.zip", "stalker_termbase.json",
                 "glossary_builtin.json", "THIRD_PARTY_NOTICES.txt"):
        p = os.path.join(ROOT, "plugins", "ai_translate_assets", name)
        if os.path.isfile(p):
            shutil.copy2(p, assets)
    spec = importlib.util.spec_from_file_location(
        "ait_reg", os.path.join(tmp, "ai_translate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp


def present(target, out):
    t = str(target or "").strip().strip(QUOTES).strip()
    return bool(t) and CJK.search(t) and t in (out or "")


def table_map(mod, path):
    p = mod._load_vendor()
    try:
        ents = p.parse_xray_text_xml(path, mod.read_encoding_chain(path))
    except Exception:
        return {}
    out = {}
    for ent in ents:
        # 直接取 rus/text/eng，和插件源语言优先级一致。
        tx = ent.texts or {}
        val = tx.get("rus") or tx.get("text") or tx.get("eng") or ""
        out[str(ent.id)] = val
    return out


def run_job(mod, srv, in_dir, out_dir, translate_literals, gloss, hard,
            cache):
    logs = []
    job = mod.TranslationJob(
        mod._load_vendor(), srv, in_dir, "auto", "auto", "chs",
        glossary=gloss, force_glossary=False, out_dir=out_dir,
        reference_mode=True, copy_reference=True, only_referenced=False,
        protect_proper=False, translate_literals=translate_literals,
        auto_session_terms=False, session_cache_dir=cache, tm_lite=True,
        log=lambda m, tag="info": logs.append(str(m)),
        detail=lambda m, tag="info": None)
    ok = job.run()
    return job, ok, logs


def script_stats(out_dir):
    scripts = []
    for cur, _dirs, files in os.walk(out_dir):
        for fn in files:
            if fn.lower().endswith(".script"):
                scripts.append(os.path.join(cur, fn))
    wrapped = bare = 0
    for p in scripts:
        s = io.open(p, encoding="utf-8", errors="ignore").read()
        wrapped += s.count('game.translate_string("sgtat_gen_text_')
        # 粗筛：未包装的带西里尔引号串（注释里的也算，报告只用于趋势）
        for m in re.finditer(r'(["\'])([^"\'\n]*[А-Яа-яЁё][^"\'\n]*)\1',
                             s):
            start = max(0, m.start() - 24)
            if "translate_string" not in s[start:m.start()]:
                bare += 1
    tables = []
    for cur, _dirs, files in os.walk(out_dir):
        for fn in files:
            if "sgtat_gen_text_" in fn and fn.endswith(".xml"):
                tables.append(os.path.join(cur, fn))
    terms = {}
    for p in tables:
        pmod = None
        try:
            # parse via the same vendor is done by caller; use simple XML
            import xml.etree.ElementTree as ET
            root = ET.parse(p).getroot()
            for node in root:
                if node.tag != "string":
                    continue
                for lang in node:
                    if lang.tag in ("rus", "text") and lang.text:
                        terms[str(node.attrib.get("id"))] = lang.text
        except Exception:
            pass
    return {"scripts": len(scripts), "wrapped": wrapped, "bare": bare,
            "tables": len(tables), "terms": terms}


def q_metrics(mod, src_dir, out_dir, reference, gloss, idx, hard, names):
    refs = reference or {}
    out = {}
    for cur, _dirs, files in os.walk(os.path.join(out_dir, "configs",
                                                  "text")):
        for fn in files:
            if not fn.endswith(".xml"):
                continue
            p = os.path.join(cur, fn)
            rel = os.path.relpath(p, out_dir).replace("\\", "/")
            for eid, val in table_map(mod, p).items():
                out["%s#%s" % (rel, eid)] = val
    rows = []
    for key, exp in refs.items():
        got = out.get(key)
        if got is None:
            rows.append({"id": key, "status": "miss", "ratio": 0.0})
            continue
        ratio = difflib.SequenceMatcher(None, exp, got).ratio()
        status = ("exact" if exp.strip() == (got or "").strip()
                  else "sim" if ratio >= 0.60 else "low")
        rows.append({"id": key, "status": status, "ratio": ratio,
                     "actual": got, "expected": exp})
    # 术语采纳：从源文算 v2 提示，再看中文输出是否出现。
    pairs = []
    for cur, _dirs, files in os.walk(src_dir):
        for fn in files:
            if not fn.endswith(".xml"):
                continue
            p = os.path.join(cur, fn)
            rel = os.path.relpath(p, src_dir).replace("\\", "/")
            for eid, val in table_map(mod, p).items():
                if not val or CJK.search(val):
                    continue
                got = out.get("configs/text/%s#%s" % (rel, eid)) or ""
                hp = [(k, v) for k, v in idx.hits(val) if k in hard]
                used = {k for k, _v in hp}
                hp += [(k, v) for k, v in idx.hits(val)
                       if k in names and k not in used]
                for k, v in hp:
                    if present(v, v):
                        pairs.append((v, present(v, got)))
    adopted = sum(1 for _v, ok in pairs if ok)
    return {
        "refs": len(refs),
        "exact": sum(1 for r in rows if r["status"] == "exact"),
        "sim": sum(1 for r in rows if r["status"] == "sim"),
        "low": sum(1 for r in rows if r["status"] == "low"),
        "miss": sum(1 for r in rows if r["status"] == "miss"),
        "avg": (sum(r["ratio"] for r in rows) / len(rows)) if rows else 0.0,
        "terms": len(pairs), "adopted": adopted,
        "rows": rows,
    }


def build_complex_tables(mod, idx, hard, names, src_root, patch_root,
                         dest, max_files=20, per_file=40):
    """从 NS7 源文挑 20 个复杂表，每表抽 40 条边界条目，并带上汉化参考。

    复杂度评分优先：换行、占位符、长句、术语命中、引号/破折号。目录最多
    4 个文件，避免 20 个都来自 dialogs。
    """
    parser = mod._load_vendor()
    files = []
    for cur, _dirs, fs in os.walk(src_root):
        for fn in fs:
            if fn.lower().endswith(".xml"):
                files.append(os.path.join(cur, fn))
    files.sort()
    scored_files = []
    for full in files:
        rel = os.path.relpath(full, src_root)
        try:
            ents = parser.parse_xray_text_xml(
                full, mod.read_encoding_chain(full))
        except Exception:
            continue
        rows = []
        for ent in ents:
            tx = ent.texts or {}
            src = (tx.get("rus") or tx.get("text") or tx.get("eng") or "")
            src = src.strip()
            if not (5 <= len(src) <= 2000) or CJK.search(src):
                continue
            if not mod._has_translatable_text(src):
                continue
            if mod.looks_like_code_or_debug(rel, src):
                continue
            score = 0
            if "\\n" in src or "\n" in src:
                score += 6
            if TOKEN.search(src):
                score += 4
            if len(src) >= 200:
                score += 3
            if len(src) >= 500:
                score += 3
            if any(q in src for q in "«»“”"):
                score += 2
            hp = [k for k, _v in idx.hits(src)
                  if k in hard or k in names]
            score += min(4, len(hp)) * 2
            rows.append((score, ent.id, src))
        if rows:
            scored_files.append((sum(r[0] for r in rows), rel, full, rows))
    scored_files.sort(reverse=True)
    chosen, dir_count = [], {}
    for _score, rel, full, rows in scored_files:
        top = rel.replace("\\", "/").split("/")[0]
        if dir_count.get(top, 0) >= 4:
            continue
        if len(chosen) >= max_files:
            break
        dir_count[top] = dir_count.get(top, 0) + 1
        chosen.append((rel, full, rows))

    os.makedirs(dest, exist_ok=True)
    refs = {}
    total = 0
    for rel, full, rows in chosen:
        rows.sort(key=lambda x: (-x[0], x[1]))
        take = rows[:per_file]
        # 恢复文件内原顺序，便于阅读与对比。
        take.sort(key=lambda x: x[1])
        patch = os.path.join(patch_root, rel)
        patch_map = table_map(mod, patch) if os.path.isfile(patch) else {}
        out_rel = os.path.join("configs", "text", rel)
        out_path = os.path.join(dest, out_rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        body = ['<?xml version="1.0" encoding="utf-8"?>',
                "<string_table>"]
        for _score, eid, src in take:
            body.append(' <string id="%s"><rus>%s</rus></string>'
                        % (xml_escape(str(eid), {'"': "&quot;"}),
                           xml_escape(src)))
            ref = patch_map.get(str(eid))
            if ref and CJK.search(ref):
                refs["%s#%s" % (out_rel.replace("\\", "/"), eid)] = ref
            total += 1
        body.append("</string_table>")
        with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(body) + "\n")
    ltx = os.path.join(dest, "configs", "localization.ltx")
    os.makedirs(os.path.dirname(ltx), exist_ok=True)
    with io.open(ltx, "w", encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles =\n")
    return os.path.join(dest, "configs", "text"), refs, total, len(chosen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--variant", default="cuda")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--only", default="", help="只跑名称包含该子串的用例")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--patch", default=DEFAULT_PATCH)
    ap.add_argument("--skip-complex", action="store_true")
    args = ap.parse_args()
    if not os.path.isfile(args.exe) or not os.path.isfile(args.model):
        print("引擎或模型不存在")
        return 2
    mod, tmp = load_plugin()
    os.makedirs(args.out, exist_ok=True)
    gloss, _s, _d, _c = mod.load_glossary_layers()
    layers = mod.load_term_layers()
    idx = mod.GlossaryIndex(gloss,
                            case_sensitive=layers["case_sensitive"],
                            variants=layers["variants"])
    srv = mod.LlamaServer(args.exe, args.model, args.variant,
                          log=lambda m, tag="info": print("  " + str(m)),
                          ctx_size=args.ctx, tier="1.8b")
    cache = os.path.join(tmp, "session")
    results = []
    try:
        srv.start()
        # 20 复杂脚本
        for name in sorted(os.listdir(BASE)):
            if not name.startswith("S"):
                continue
            if args.only and args.only not in name:
                continue
            case = os.path.join(BASE, name)
            ind = os.path.join(case, "input")
            if not os.path.isdir(ind):
                continue
            outd = os.path.join(args.out, name)
            shutil.rmtree(outd, ignore_errors=True)
            t0 = time.time()
            job, ok, _logs = run_job(
                mod, srv, ind, outd, True, gloss,
                layers["canon"], cache)
            st = script_stats(outd)
            terms = list(st.pop("terms").values())
            zh = sum(1 for x in terms if CJK.search(x))
            results.append({
                "case": name, "kind": "script", "ok": bool(ok),
                "elapsed": time.time() - t0, "requests": job.req_count,
                "wrapped": st["wrapped"], "bare": st["bare"],
                "tables": st["tables"], "terms": len(terms),
                "zh_terms": zh, "tm": job.tm_stats,
            })
            print("%s ok=%s req=%d wrapped=%d terms=%d zh=%d"
                  % (name, ok, job.req_count, st["wrapped"], len(terms), zh))
        # 20 个复杂文本表（从 NS7 源文抽边界条目，带汉化参考）
        if not args.skip_complex and not (args.only and "complex" not in args.only):
            cdest = os.path.join(args.out, "_complex_input")
            shutil.rmtree(cdest, ignore_errors=True)
            csrc, crefs, ctotal, cfiles = build_complex_tables(
                mod, idx, layers["canon"], layers["names"],
                args.src, args.patch, cdest)
            coutd = os.path.join(args.out, "complex_tables")
            shutil.rmtree(coutd, ignore_errors=True)
            t0 = time.time()
            job, ok, _logs = run_job(
                mod, srv, cdest, coutd, False, gloss,
                layers["canon"], cache)
            met = q_metrics(mod, csrc, coutd, crefs, gloss, idx,
                            layers["canon"], layers["names"])
            results.append({
                "case": "complex_tables", "kind": "complex_table",
                "ok": bool(ok), "elapsed": time.time() - t0,
                "requests": job.req_count, "files": cfiles,
                "entries": ctotal, "refs": met["refs"],
                "exact": met["exact"], "sim": met["sim"],
                "low": met["low"], "miss": met["miss"], "avg": met["avg"],
                "terms": met["terms"], "adopted": met["adopted"],
                "rows": met["rows"], "tm": job.tm_stats,
            })
            print("complex_tables ok=%s files=%d entries=%d avg=%.3f "
                  "terms=%d/%d" % (ok, cfiles, ctotal, met["avg"],
                                   met["adopted"], met["terms"]))
        # 10 大文本表
        baseline = {}
        bp = os.path.join(BASE, "results.json")
        if os.path.isfile(bp):
            for x in json.load(io.open(bp, encoding="utf-8")):
                baseline[x.get("round")] = x
        for name in sorted(os.listdir(BASE)):
            if not name.startswith("Q"):
                continue
            if args.only and args.only not in name:
                continue
            case = os.path.join(BASE, name)
            ind = os.path.join(case, "input")
            if not os.path.isdir(ind):
                continue
            outd = os.path.join(args.out, name)
            shutil.rmtree(outd, ignore_errors=True)
            refp = os.path.join(case, "reference.json")
            refs = json.load(io.open(refp, encoding="utf-8")) \
                if os.path.isfile(refp) else {}
            t0 = time.time()
            job, ok, _logs = run_job(
                mod, srv, ind, outd, False, gloss,
                layers["canon"], cache)
            met = q_metrics(mod, os.path.join(case, "input", "configs",
                                              "text"),
                            outd, refs, gloss, idx,
                            layers["canon"], layers["names"])
            old = baseline.get(name, {})
            results.append({
                "case": name, "kind": "table", "ok": bool(ok),
                "elapsed": time.time() - t0, "requests": job.req_count,
                "refs": met["refs"], "exact": met["exact"],
                "sim": met["sim"], "low": met["low"], "miss": met["miss"],
                "avg": met["avg"], "terms": met["terms"],
                "adopted": met["adopted"], "rows": met["rows"],
                "baseline": {k: old.get(k) for k in
                             ("exact", "sim", "low", "miss", "avg")},
                "tm": job.tm_stats,
            })
            print("%s ok=%s req=%d avg=%.3f (old %.3f) terms=%d/%d"
                  % (name, ok, job.req_count, met["avg"],
                     old.get("avg") or 0, met["adopted"], met["terms"]))
    finally:
        try:
            srv.stop()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    with io.open(os.path.join(args.out, "results.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    script = [r for r in results if r["kind"] == "script"]
    complex_rows = [r for r in results if r["kind"] == "complex_table"]
    table = [r for r in results if r["kind"] == "table"]
    lines = [
        "# 复杂脚本 + 大文本表回归（v2 / TM-lite）",
        "",
        "- 复杂脚本：%d 个；全部完成 %s；包装 `%d` 处；残留裸俄文串 `%d` 处"
        % (len(script), all(r["ok"] for r in script),
           sum(r["wrapped"] for r in script),
           sum(r["bare"] for r in script)),
        "- 脚本字面表：`%d` 张 / `%d` 条，含中文 `%d` 条"
        % (sum(r["tables"] for r in script),
           sum(r["terms"] for r in script),
           sum(r["zh_terms"] for r in script)),
        "- 复杂文本表：`%d` 个 / `%d` 条；miss `%d`；平均相似度 `%.3f`；"
        "术语采纳 `%d/%d`"
        % (sum(r["files"] for r in complex_rows),
           sum(r["entries"] for r in complex_rows),
           sum(r["miss"] for r in complex_rows),
           (sum(r["avg"] for r in complex_rows) / len(complex_rows))
           if complex_rows else 0,
           sum(r["adopted"] for r in complex_rows),
           sum(r["terms"] for r in complex_rows)),
        "- 大文本表：%d 个；全部完成 %s；miss `%d`；平均相似度 `%.3f`"
        % (len(table), all(r["ok"] for r in table),
           sum(r["miss"] for r in table),
           (sum(r["avg"] for r in table) / len(table)) if table else 0),
        "- 大表术语采纳：`%d/%d`（%.1f%%）"
        % (sum(r["adopted"] for r in table), sum(r["terms"] for r in table),
           100.0 * sum(r["adopted"] for r in table)
           / max(1, sum(r["terms"] for r in table))),
        "",
        "## 复杂文本表",
        "",
        "| 用例 | 完成 | 文件 | 条目 | 参考 | exact | sim | low | miss | avg |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in complex_rows:
        lines.append("| %s | %s | %d | %d | %d | %d | %d | %d | %d | %.3f |"
                     % (r["case"], "是" if r["ok"] else "否", r["files"],
                        r["entries"], r["refs"], r["exact"], r["sim"],
                        r["low"], r["miss"], r["avg"]))
    lines += [
        "",
        "## 大表与旧版基线",
        "",
        "| 用例 | 新版 avg | 旧版 avg | exact | sim | low | miss |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in table:
        lines.append("| %s | %.3f | %.3f | %d | %d | %d | %d |"
                     % (r["case"], r["avg"], r["baseline"].get("avg") or 0,
                        r["exact"], r["sim"], r["low"], r["miss"]))
    lines += ["", "## 复杂脚本", "",
              "| 用例 | 完成 | 请求 | 包装 | 裸串 | 字面条目 | 中文 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for r in script:
        lines.append("| %s | %s | %d | %d | %d | %d | %d |"
                     % (r["case"], "是" if r["ok"] else "否",
                        r["requests"], r["wrapped"], r["bare"],
                        r["terms"], r["zh_terms"]))
    lines += ["", "## 边界", "",
              "- 相似度只相对现有中文补丁，不等于人工质量分；",
              "- 脚本裸串统计包含注释/日志里的俄文，作为趋势指标；",
              "- 20 个脚本与 10 个大表复用 2026-10-30 旧版冻结输入；"
              "20 个复杂文本表从 NS7 源文按边界特征重新抽取（带汉化参考）。", ""]
    with io.open(os.path.join(args.out, "report.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("报告：%s" % os.path.join(args.out, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
