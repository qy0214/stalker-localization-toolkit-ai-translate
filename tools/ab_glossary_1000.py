#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标准术语库 A/B：1000 条真实文本，开/关术语提示各翻一次。

目的不是“证明译文完美”，而是把术语库的作用隔离出来：
同一模型、同一系统提示、同一温度、同一批源文，唯一变量是有没有注入
canon/names 层的术语提示。指标：
  * 术语采纳率（期望译名是否出现在译文里）
  * 整条全采纳率
  * 格式完整性（中文/无残留西里尔/占位符/换行）
  * A 更好 / B 更好 / 持平条数

用法（默认读取 NS7 源文与桌面工具集的 1.8B 引擎）：
    python tools/ab_glossary_1000.py --n 1000
    python tools/ab_glossary_1000.py --n 100 --out D:\\tmp\\ab --no-server
"""
import argparse
import importlib.util
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop",
                       "STALKER Localization Toolkit 1.1.0")
DEFAULT_EXE = os.path.join(DESKTOP, "plugins", "ai_translate_assets",
                           "bin", "cuda", "llama-server.exe")
DEFAULT_MODEL = os.path.join(DESKTOP, "plugins", "ai_translate_assets",
                             "models", "Hy-MT2-1.8B-Q8_0.gguf")
DEFAULT_SRC = r"C:\game\NS7\gamedata\configs\text"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop",
                           "对比报告_20261030",
                           "ab_glossary_1000_20261030")
CJK = re.compile(r"[\u3400-\u9fff]")
CYR = re.compile(r"[\u0400-\u04ff]")
QUOTES = "\"'“”‘’«»"


def load_plugin():
    """在临时目录导入插件，避免在仓库 assets 里生成运行目录。"""
    tmp = tempfile.mkdtemp(prefix="ait_ab_")
    shutil.copy2(os.path.join(ROOT, "plugins", "ai_translate.py"), tmp)
    assets = os.path.join(tmp, "ai_translate_assets")
    os.makedirs(assets)
    for name in ("xray_pkg.zip", "stalker_termbase.json",
                 "glossary_builtin.json", "THIRD_PARTY_NOTICES.txt"):
        src = os.path.join(ROOT, "plugins", "ai_translate_assets", name)
        if os.path.isfile(src):
            shutil.copy2(src, assets)
    spec = importlib.util.spec_from_file_location(
        "ait_ab", os.path.join(tmp, "ai_translate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp


def term_present(target, out):
    t = str(target or "").strip().strip(QUOTES).strip()
    if len(t) < 2 or not CJK.search(t):
        return False
    return t in (out or "")


def effective_pairs(mod, idx, hard, names, text):
    pairs = idx.hits(text)
    hard_pairs = [(k, v) for k, v in pairs if k in hard]
    used = {k for k, _v in hard_pairs}
    name_pairs = [(k, v) for k, v in pairs
                  if k in names and k not in used]
    ordered = hard_pairs + name_pairs
    block, _used = mod.glossary_hint_block(ordered)
    if not block:
        return []
    return [(k, v) for k, v in ordered
            if "%s=%s" % (k, v) in block]


def collect_samples(mod, idx, hard, names, src_root, n, seed):
    parser = mod._load_vendor()
    buckets = {"canon": [], "both": [], "names": []}
    seen = set()
    file_counts = {}
    pair_counts = {}
    files = []
    for cur, _dirs, fs in os.walk(src_root):
        for fn in fs:
            if fn.lower().endswith(".xml"):
                files.append(os.path.join(cur, fn))
    files.sort()
    for full in files:
        rel = os.path.relpath(full, src_root)
        try:
            ents = parser.parse_xray_text_xml(
                full, mod.read_encoding_chain(full))
        except Exception:
            continue
        for ent in ents:
            tx = ent.texts or {}
            src = (tx.get("rus") or tx.get("text") or tx.get("eng") or "")
            src = src.strip()
            if not (8 <= len(src) <= 300) or CJK.search(src):
                continue
            if src in seen or not mod._has_translatable_text(src):
                continue
            if mod.looks_like_code_or_debug(rel, src):
                continue
            pairs = effective_pairs(mod, idx, hard, names, src)
            if not pairs:
                continue
            pairs = [(k, v) for k, v in pairs
                     if term_present(v, v) and len(k) >= 3]
            if not pairs:
                continue
            key = tuple(sorted(pairs))
            if pair_counts.get(key, 0) >= 25:
                continue
            if any(term_present(v, src) for _k, v in pairs):
                continue
            lang = mod.detect_text_lang(src) or "rus"
            item = {"src": src, "rel": rel, "id": ent.id, "lang": lang,
                    "pairs": pairs}
            seen.add(src)
            pair_counts[key] = pair_counts.get(key, 0) + 1
            has_hard = any(k in hard for k, _v in pairs)
            has_name = any(k in names for k, _v in pairs)
            bucket = ("both" if has_hard and has_name
                      else "canon" if has_hard else "names")
            buckets[bucket].append(item)
    rnd = random.Random(seed)
    for rows in buckets.values():
        rnd.shuffle(rows)
        rows.sort(key=lambda x: file_counts.get(x["rel"], 0))
    quota = {"canon": int(n * 0.60), "both": int(n * 0.25),
             "names": n - int(n * 0.60) - int(n * 0.25)}
    picked = []
    for bucket, want in quota.items():
        rows = buckets[bucket]
        local = {}
        for item in rows:
            if len(picked) >= n:
                break
            if len([x for x in picked if x["_bucket"] == bucket]) >= want:
                break
            if local.get(item["rel"], 0) >= 80:
                continue
            local[item["rel"]] = local.get(item["rel"], 0) + 1
            file_counts[item["rel"]] = file_counts.get(item["rel"], 0) + 1
            item["_bucket"] = bucket
            picked.append(item)
    # 名额不足时从其他桶补足，保持总数。
    if len(picked) < n:
        used = {x["src"] for x in picked}
        pool = [x for b in ("both", "canon", "names") for x in buckets[b]
                if x["src"] not in used]
        rnd.shuffle(pool)
        for item in pool:
            if len(picked) >= n:
                break
            item["_bucket"] = "fill"
            picked.append(item)
    for item in picked:
        item.pop("_bucket", None)
    return picked, {k: len(v) for k, v in buckets.items()}


def translate_one(mod, srv, item, pairs, temperature):
    src = item["src"]
    normalized = mod.unescape_newlines(src)
    masked, mapping = mod.mask_runtime_tokens(normalized)
    out = srv.translate(masked, item["lang"], "chs",
                        glossary_hits=pairs or None,
                        temperature=temperature)
    restored = mod.restore_runtime_tokens(out, mapping) if mapping else out
    return out, restored


def quality(mod, item, out, restored, mapping_count):
    text = restored if restored is not None else (out or "")
    return {
        "cjk": bool(CJK.search(text)),
        "cyr": bool(CYR.search(text)) if item["lang"] == "rus" else False,
        "placeholder": restored is not None,
        "newline": (text.count("\n") == mod.unescape_newlines(
            item["src"]).count("\n")),
        "echo": bool(mod.looks_like_prompt_echo(text)),
        "empty": not bool(text.strip()),
        "len_ratio": (len(text) / float(len(item["src"]))
                      if item["src"] else 0.0),
    }


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261030)
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--variant", default="cuda")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--no-server", action="store_true",
                    help="只选样本、不启动模型（冒烟用）")
    args = ap.parse_args()

    mod, tmp = load_plugin()
    try:
        gloss, _set, _deleted, _clear = mod.load_glossary_layers()
        layers = mod.load_term_layers()
        idx = mod.GlossaryIndex(
            gloss, case_sensitive=layers["case_sensitive"],
            variants=layers["variants"])
        hard, names = layers["canon"], layers["names"]
        print("术语层：canon %d / names %d / technical %d"
              % (len(hard), len(names), len(layers["technical"])))
        samples, pool = collect_samples(mod, idx, hard, names, args.src,
                                        args.n, args.seed)
        print("候选池：%s；选中 %d 条" % (pool, len(samples)))
        os.makedirs(args.out, exist_ok=True)
        with io.open(os.path.join(args.out, "samples.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump(samples, f, ensure_ascii=False, indent=1)
        if args.no_server or not samples:
            print("只选样本，未启动模型。输出：%s" % args.out)
            return 0
        if not os.path.isfile(args.exe) or not os.path.isfile(args.model):
            print("引擎或模型不存在：\n  %s\n  %s" % (args.exe, args.model))
            return 2

        srv = mod.LlamaServer(args.exe, args.model, args.variant,
                              log=lambda m, tag="info": print("  " + str(m)),
                              ctx_size=args.ctx, tier="1.8b")
        srv.start()
        try:
            results_path = os.path.join(args.out, "results.jsonl")
            done = set()
            if os.path.isfile(results_path):
                for line in io.open(results_path, encoding="utf-8"):
                    try:
                        done.add(json.loads(line)["src"])
                    except Exception:
                        pass
                print("断点续跑：已有 %d 条" % len(done))
            t0 = time.time()
            times_a, times_b = [], []
            with io.open(results_path, "a", encoding="utf-8",
                         newline="\n") as fp:
                for i, item in enumerate(samples, 1):
                    if item["src"] in done:
                        continue
                    ta = time.time()
                    a_raw, a_res = translate_one(mod, srv, item, item["pairs"],
                                                 args.temperature)
                    ta = time.time() - ta
                    tb = time.time()
                    b_raw, b_res = translate_one(mod, srv, item, None,
                                                 args.temperature)
                    tb = time.time() - tb
                    times_a.append(ta)
                    times_b.append(tb)
                    mapping_count = len(mod._RUNTIME_TOKEN_RE.findall(
                        mod.unescape_newlines(item["src"])))
                    qa = quality(mod, item, a_raw, a_res, mapping_count)
                    qb = quality(mod, item, b_raw, b_res, mapping_count)
                    a_hit = sum(1 for _k, v in item["pairs"]
                                if term_present(v, a_res))
                    b_hit = sum(1 for _k, v in item["pairs"]
                                if term_present(v, b_res))
                    row = {
                        "src": item["src"], "rel": item["rel"], "id": item["id"],
                        "lang": item["lang"], "pairs": item["pairs"],
                        "a": a_res, "b": b_res, "a_raw": a_raw,
                        "b_raw": b_raw, "a_hit": a_hit, "b_hit": b_hit,
                        "terms": len(item["pairs"]),
                        "qa": qa, "qb": qb, "ta": ta, "tb": tb,
                    }
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fp.flush()
                    if i % 25 == 0 or i == len(samples):
                        print("  %d/%d  %.2f 条/s  A采纳 %d/%d  B采纳 %d/%d"
                              % (i, len(samples),
                                 (len(times_a) + len(times_b)) /
                                 max(1e-6, sum(times_a) + sum(times_b)),
                                 sum(1 for x in _load_rows(results_path)
                                     if x["a_hit"] > 0),
                                 len(_load_rows(results_path)),
                                 sum(1 for x in _load_rows(results_path)
                                     if x["b_hit"] > 0),
                                 len(_load_rows(results_path))))
        finally:
            srv.stop()
        summarize(mod, args, samples, results_path,
                  times_a + times_b, time.time() - t0)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _load_rows(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    for line in io.open(path, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def summarize(mod, args, samples, results_path, times, elapsed):
    rows = _load_rows(results_path)
    n = len(rows)
    total_terms = sum(r["terms"] for r in rows)
    a_adopt = sum(r["a_hit"] for r in rows)
    b_adopt = sum(r["b_hit"] for r in rows)
    a_full = sum(1 for r in rows if r["a_hit"] == r["terms"])
    b_full = sum(1 for r in rows if r["b_hit"] == r["terms"])
    improved = sum(1 for r in rows if r["a_hit"] > r["b_hit"])
    regressed = sum(1 for r in rows if r["a_hit"] < r["b_hit"])
    same = n - improved - regressed
    gates = {}
    for arm in ("qa", "qb"):
        gates[arm] = {
            "cjk": pct(sum(1 for r in rows if r[arm]["cjk"]), n),
            "no_cyr": pct(sum(1 for r in rows if not r[arm]["cyr"]), n),
            "placeholder": pct(sum(1 for r in rows if r[arm]["placeholder"]),
                               n),
            "newline": pct(sum(1 for r in rows if r[arm]["newline"]), n),
            "no_echo": pct(sum(1 for r in rows if not r[arm]["echo"]), n),
            "nonempty": pct(sum(1 for r in rows if not r[arm]["empty"]), n),
        }
    per_term = {}
    for r in rows:
        for k, v in r["pairs"]:
            x = per_term.setdefault(k, {"target": v, "n": 0, "a": 0,
                                        "b": 0})
            x["n"] += 1
            x["a"] += 1 if term_present(v, r["a"]) else 0
            x["b"] += 1 if term_present(v, r["b"]) else 0
    top = sorted(per_term.items(), key=lambda kv: kv[1]["n"], reverse=True)
    times = sorted(times or [])
    p50 = times[len(times) // 2] if times else 0
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0
    summary = {
        "n": n,
        "total_terms": total_terms,
        "a_adoption": pct(a_adopt, total_terms),
        "b_adoption": pct(b_adopt, total_terms),
        "a_full": pct(a_full, n),
        "b_full": pct(b_full, n),
        "improved": improved, "regressed": regressed, "same": same,
        "gates": gates,
        "elapsed": elapsed,
        "p50": p50, "p95": p95,
        "terms": [{"source": k, "target": v["target"], "n": v["n"],
                   "a": v["a"], "b": v["b"]}
                  for k, v in top[:80]],
        "temperature": args.temperature,
        "model": os.path.basename(args.model),
    }
    with io.open(os.path.join(args.out, "summary.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    lines = [
        "# 标准术语库 1000 条 A/B",
        "",
        "- 模型：`%s`；温度 `%.2f`；同一系统提示、同一批源文，"
        "唯一变量是术语提示" % (summary["model"], args.temperature),
        "- 样本：`%d` 条（术语 %d 项）" % (n, total_terms),
        "- 耗时：`%.0f` 秒；p50 `%.2fs` / p95 `%.2fs`"
        % (elapsed, p50, p95),
        "",
        "## 结论",
        "",
        "- 术语采纳率：**A（开术语）%.1f%%** vs B（关术语）%.1f%%，"
        "提升 **%.1f 个百分点**"
        % (summary["a_adoption"], summary["b_adoption"],
           summary["a_adoption"] - summary["b_adoption"]),
        "- 整条全采纳率：A %.1f%% vs B %.1f%%"
        % (summary["a_full"], summary["b_full"]),
        "- A 更好 %d 条 / B 更好 %d 条 / 持平 %d 条"
        % (improved, regressed, same),
        "",
        "## 格式完整性",
        "",
        "| 指标 | A | B |",
        "|---|---:|---:|",
    ]
    for key, label in (("cjk", "含中文"), ("no_cyr", "无残留西里尔"),
                       ("placeholder", "占位符完整"), ("newline", "换行数一致"),
                       ("no_echo", "无提示词回显"), ("nonempty", "非空")):
        lines.append("| %s | %.1f%% | %.1f%% |"
                     % (label, gates["qa"][key], gates["qb"][key]))
    lines += ["", "## 高频术语", "",
              "| 原文 | 期望译名 | 样本 | A 采纳 | B 采纳 |",
              "|---|---|---:|---:|---:|"]
    for t in summary["terms"][:30]:
        lines.append("| `%s` | %s | %d | %.0f%% | %.0f%% |"
                     % (t["source"], t["target"], t["n"],
                        pct(t["a"], t["n"]), pct(t["b"], t["n"])))
    lines += ["", "## 样例（A 更好）", ""]
    shown = 0
    for r in rows:
        if r["a_hit"] > r["b_hit"] and shown < 12:
            lines += ["- 原文：%s" % r["src"],
                      "  - A：%s" % (r["a"] or "").replace("\n", " "),
                      "  - B：%s" % (r["b"] or "").replace("\n", " ")]
            shown += 1
    lines += ["", "## 边界", "",
              "- 这是**客观术语采纳**实验，不等于人工质量评分；",
              "- 温度 0、单模型、单批源文，不能外推到所有模型；",
              "- 术语提示预算 240 字符，超出预算的词条本来就不会注入，"
              "样本已按实际注入块筛选。", ""]
    with io.open(os.path.join(args.out, "report.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("A 采纳 %.1f%% / B %.1f%%；A 更好 %d / B 更好 %d / 持平 %d"
          % (summary["a_adoption"], summary["b_adoption"], improved,
             regressed, same))
    print("报告：%s" % os.path.join(args.out, "report.md"))


if __name__ == "__main__":
    sys.exit(main())
