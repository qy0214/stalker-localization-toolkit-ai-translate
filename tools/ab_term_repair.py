#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""术语缺失定点修复 A/B。

A（基线）：当前 1.9.6 路径，只注入术语提示（hints），不强制。
B（修复）：A 的译文里，若本次提示过的 hard canon 术语没有出现，
只对这条回退到“占位符强制重译”一次；正常条目不增加请求。

指标：hard 术语采纳率、全部术语采纳率、整条全采纳、修复次数/成功率、
质量闸门（含中文/无残留西里尔/换行/无回显/非空）和额外耗时。

用法：
    python tools/ab_term_repair.py --n 300
    python tools/ab_term_repair.py --n 50 --no-server
"""
import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ab_glossary_1000 as base  # noqa: E402

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop",
                       "STALKER Localization Toolkit 1.1.0")
DEFAULT_EXE = os.path.join(DESKTOP, "plugins", "ai_translate_assets",
                           "bin", "cuda", "llama-server.exe")
DEFAULT_MODEL = os.path.join(DESKTOP, "plugins", "ai_translate_assets",
                             "models", "Hy-MT2-1.8B-Q8_0.gguf")
DEFAULT_SRC = os.path.join(os.path.expanduser("~"), "Desktop", "gamedata",
                           "configs", "text")
DEFAULT_OUT = os.path.join(HERE, "ab_term_repair_20261031")
QUOTES = "\"'«»“”‘’"
FORCE_NOTE = (
    "输入里的 $$T1$$、$$T2$$ 是术语占位符，必须逐字保留：数量、顺序、"
    "大小写和美元符号都不能改；只翻译占位符以外的正文。")
CYR = re.compile(r"[\u0400-\u04ff]")
CJK = re.compile(r"[\u3400-\u9fff]")


def tighten_cjk_spaces(text):
    """占位符回填后，清掉中文/中文引号之间的半角空格，避免“离开 禁区 异域”。"""
    text = re.sub(r"(?<=[\u4e00-\u9fff“”])[ \t]+"
                  r"(?=[\u4e00-\u9fff“”])", "", text)
    text = re.sub(r"[ \t]+(?=[，。！？；：、])", "", text)
    text = re.sub(r"(?<=[，。！？；：、])[ \t]+", "", text)
    return text


def quality_text(mod, item, text):
    text = text or ""
    return {
        "cjk": bool(CJK.search(text)),
        "no_cyr": not bool(CYR.search(text)),
        "newline": text.count("\n") == mod.unescape_newlines(
            item["src"]).count("\n"),
        "no_echo": not bool(mod.looks_like_prompt_echo(text)),
        "nonempty": bool(text.strip()),
        "len_ratio": (len(text) / float(len(item["src"]))
                      if item["src"] else 0.0),
    }


def context_ok(text, start, end, key, quote_required):
    if key not in quote_required:
        return True
    return (start > 0 and end < len(text)
            and text[start - 1] in QUOTES and text[end] in QUOTES)


def force_repair(mod, srv, item, gloss, idx, hard_pairs, quote_required,
                 temperature):
    """把本次提示过的 hard 术语占位符化后整条重译；失败返回 (None, 原因)。"""
    src = item["src"]
    normalized = mod.unescape_newlines(src)
    masked, runtime_map = mod.mask_runtime_tokens(normalized)
    wanted = {k for k, _v in hard_pairs}
    spans = [(s, e, k) for s, e, k in idx.find(masked)
             if k in wanted and context_ok(masked, s, e, k, quote_required)]
    if not spans:
        return None, "no-span"
    spans.sort()
    parts, term_map, last = [], {}, 0
    for i, (start, end, key) in enumerate(spans, 1):
        ph = "$$T%d$$" % i
        term_map[ph] = mod.TranslationJob._inline_term_value(
            masked, start, end, gloss[key])
        parts.append(masked[last:start])
        parts.append(ph)
        last = end
    parts.append(masked[last:])
    try:
        raw = srv.translate("".join(parts), item["lang"], "chs",
                            extra_sys=FORCE_NOTE, temperature=temperature)
    except Exception as e:
        return None, "request:%s" % type(e).__name__
    restored = (mod.restore_runtime_tokens(raw, runtime_map)
                if runtime_map else raw)
    if restored is None:
        return None, "runtime-token"
    for ph in term_map:
        if restored.count(ph) != 1:
            return None, "placeholder"
    for ph, val in term_map.items():
        restored = restored.replace(ph, val)
    return tighten_cjk_spaces(restored), ""


def valid_pairs(text, pairs, idx, quote_required):
    """按运行期规则过滤：quote_required 词条必须在引号语境里出现。"""
    out = []
    for k, v in pairs:
        if k not in quote_required:
            out.append((k, v))
            continue
        if any(key == k and context_ok(text, s, e, key, quote_required)
               for s, e, key in idx.find(text)):
            out.append((k, v))
    return out


def hit_count(pairs, text):
    return sum(1 for _k, v in pairs if base.term_present(v, text))


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--variant", default="cuda")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--no-server", action="store_true")
    args = ap.parse_args()

    mod, tmp = base.load_plugin()
    try:
        gloss, _set, _deleted, _clear = mod.load_glossary_layers()
        layers = mod.load_term_layers()
        idx = mod.GlossaryIndex(
            gloss, case_sensitive=layers["case_sensitive"],
            variants=layers["variants"])
        hard, names = layers["canon"], layers["names"]
        quote_required = layers.get("quote_required") or set()
        samples, pool = base.collect_samples(mod, idx, hard, names, args.src,
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
                    pairs = valid_pairs(item["src"], item["pairs"], idx,
                                        quote_required)
                    if not pairs:
                        continue
                    if pairs != item["pairs"]:
                        item = dict(item)
                        item["pairs"] = pairs
                    hard_pairs = [(k, v) for k, v in pairs if k in hard]
                    name_pairs = [(k, v) for k, v in pairs if k in names]
                    ta = time.time()
                    a_raw, a_res = base.translate_one(
                        mod, srv, item, item["pairs"], args.temperature)
                    ta = time.time() - ta
                    a_text = a_res if a_res is not None else a_raw
                    missed = [(k, v) for k, v in hard_pairs
                              if not base.term_present(v, a_text)]
                    tb = 0.0
                    reason = ""
                    repaired = bool(missed)
                    if repaired:
                        tb0 = time.time()
                        b_text, reason = force_repair(
                            mod, srv, item, gloss, idx, hard_pairs,
                            quote_required, args.temperature)
                        tb = time.time() - tb0
                        if b_text is None:
                            b_text = a_text
                    else:
                        b_text = a_text
                    times_a.append(ta)
                    if repaired:
                        times_b.append(tb)
                    row = {
                        "src": item["src"], "rel": item["rel"],
                        "id": item["id"], "lang": item["lang"],
                        "pairs": item["pairs"],
                        "hard_pairs": hard_pairs,
                        "name_pairs": name_pairs,
                        "a": a_text, "b": b_text,
                        "a_hit": hit_count(item["pairs"], a_text),
                        "b_hit": hit_count(item["pairs"], b_text),
                        "a_hard_hit": hit_count(hard_pairs, a_text),
                        "b_hard_hit": hit_count(hard_pairs, b_text),
                        "hard_total": len(hard_pairs),
                        "name_total": len(name_pairs),
                        "a_name_hit": hit_count(name_pairs, a_text),
                        "b_name_hit": hit_count(name_pairs, b_text),
                        "repaired": repaired,
                        "repair_ok": bool(
                            repaired and b_text != a_text
                            and hit_count(hard_pairs, b_text)
                            == len(hard_pairs)),
                        "repair_reason": reason,
                        "qa": quality_text(mod, item, a_text),
                        "qb": quality_text(mod, item, b_text),
                        "ta": ta, "tb": tb,
                    }
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fp.flush()
                    if i % 25 == 0 or i == len(samples):
                        rows = _load_rows(results_path)
                        print("  %d/%d  修复 %d 条  基线 hard %d/%d"
                              % (i, len(samples),
                                 sum(1 for x in rows if x["repaired"]),
                                 sum(x["a_hard_hit"] for x in rows),
                                 sum(x["hard_total"] for x in rows)))
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
    hard_total = sum(r["hard_total"] for r in rows)
    name_total = sum(r["name_total"] for r in rows)
    total_terms = sum(r["hard_total"] + r["name_total"] for r in rows)
    a_hard = sum(r["a_hard_hit"] for r in rows)
    b_hard = sum(r["b_hard_hit"] for r in rows)
    a_name = sum(r["a_name_hit"] for r in rows)
    b_name = sum(r["b_name_hit"] for r in rows)
    a_all = a_hard + a_name
    b_all = b_hard + b_name
    a_full = sum(1 for r in rows
                 if r["a_hit"] == r["hard_total"] + r["name_total"])
    b_full = sum(1 for r in rows
                 if r["b_hit"] == r["hard_total"] + r["name_total"])
    repaired = sum(1 for r in rows if r["repaired"])
    repaired_ok = sum(1 for r in rows if r["repair_ok"])
    improved = sum(1 for r in rows if r["b_hit"] > r["a_hit"])
    regressed = sum(1 for r in rows if r["b_hit"] < r["a_hit"])
    same = n - improved - regressed
    gates = {}
    for arm in ("qa", "qb"):
        gates[arm] = {
            "cjk": pct(sum(1 for r in rows if r[arm]["cjk"]), n),
            "no_cyr": pct(sum(1 for r in rows if r[arm]["no_cyr"]), n),
            "newline": pct(sum(1 for r in rows if r[arm]["newline"]), n),
            "no_echo": pct(sum(1 for r in rows if r[arm]["no_echo"]), n),
            "nonempty": pct(sum(1 for r in rows if r[arm]["nonempty"]), n),
        }
    per_term = {}
    for r in rows:
        for k, v in r["hard_pairs"]:
            x = per_term.setdefault(k, {"target": v, "n": 0, "a": 0, "b": 0})
            x["n"] += 1
            x["a"] += 1 if base.term_present(v, r["a"]) else 0
            x["b"] += 1 if base.term_present(v, r["b"]) else 0
    top = sorted(per_term.items(), key=lambda kv: kv[1]["n"], reverse=True)
    times = sorted(times or [])
    p50 = times[len(times) // 2] if times else 0
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0
    summary = {
        "n": n, "hard_total": hard_total, "name_total": name_total,
        "total_terms": total_terms,
        "a_hard": pct(a_hard, hard_total),
        "b_hard": pct(b_hard, hard_total),
        "a_name": pct(a_name, name_total),
        "b_name": pct(b_name, name_total),
        "a_all": pct(a_all, total_terms),
        "b_all": pct(b_all, total_terms),
        "a_full": pct(a_full, n), "b_full": pct(b_full, n),
        "repaired": repaired, "repaired_ok": repaired_ok,
        "improved": improved, "regressed": regressed, "same": same,
        "gates": gates, "elapsed": elapsed, "p50": p50, "p95": p95,
        "terms": [{"source": k, "target": v["target"], "n": v["n"],
                   "a": v["a"], "b": v["b"]}
                  for k, v in top[:60]],
        "temperature": args.temperature,
        "model": os.path.basename(args.model),
    }
    with io.open(os.path.join(args.out, "summary.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    lines = [
        "# 术语缺失定点修复 A/B", "",
        "- 模型：`%s`；上下文 `%d`；温度 `%.2f`"
        % (summary["model"], args.ctx, args.temperature),
        "- 样本：`%d` 条；hard 术语 `%d` 项，names 软术语 `%d` 项"
        % (n, hard_total, name_total),
        "- 耗时：`%.0f` 秒；单次请求 p50 `%.2fs` / p95 `%.2fs`"
        % (elapsed, p50, p95), "",
        "## 结论", "",
        "- **hard 术语采纳率：A（仅提示）%.1f%% → B（缺失时占位符重译）%.1f%%**"
        % (summary["a_hard"], summary["b_hard"]),
        "- 全部术语采纳率：A %.1f%% → B %.1f%%（names 软术语不强制，"
        "B 的 names 变化来自整句重译）"
        % (summary["a_all"], summary["b_all"]),
        "- 整条全采纳率：A %.1f%% → B %.1f%%"
        % (summary["a_full"], summary["b_full"]),
        "- 触发修复 `%d` 条；修复后缺失清零 `%d` 条（%.1f%%）"
        % (repaired, repaired_ok,
           pct(repaired_ok, repaired) if repaired else 0),
        "- B 更好 %d 条 / 更差 %d 条 / 持平 %d 条"
        % (improved, regressed, same), "",
        "## 格式完整性", "",
        "| 指标 | A | B |", "|---|---:|---:|",
    ]
    for key, label in (("cjk", "含中文"), ("no_cyr", "无残留西里尔"),
                       ("newline", "换行数一致"), ("no_echo", "无提示词回显"),
                       ("nonempty", "非空")):
        lines.append("| %s | %.1f%% | %.1f%% |"
                     % (label, gates["qa"][key], gates["qb"][key]))
    lines += ["", "## 高频 hard 术语", "",
              "| 原文 | 期望译名 | 样本 | A 采纳 | B 采纳 |",
              "|---|---|---:|---:|---:|"]
    for t in summary["terms"][:30]:
        lines.append("| `%s` | %s | %d | %.0f%% | %.0f%% |"
                     % (t["source"], t["target"], t["n"],
                        pct(t["a"], t["n"]), pct(t["b"], t["n"])))
    lines += ["", "## 示例：B 修复了 hard 术语", ""]
    shown = 0
    for r in rows:
        if r["b_hard_hit"] > r["a_hard_hit"] and shown < 12:
            lines += ["- 原文：%s" % r["src"],
                      "  - A：%s" % (r["a"] or "").replace("\n", " "),
                      "  - B：%s" % (r["b"] or "").replace("\n", " ")]
            shown += 1
    lines += ["", "## 示例：B 变差/回退", ""]
    shown = 0
    for r in rows:
        if (r["b_hit"] < r["a_hit"]
                or (r["repaired"] and not r["repair_ok"])) and shown < 12:
            lines += ["- 原文：%s" % r["src"],
                      "  - A：%s" % (r["a"] or "").replace("\n", " "),
                      "  - B：%s" % (r["b"] or "").replace("\n", " "),
                      "  - 修复：%s %s" % (r["repaired"], r["repair_reason"])]
            shown += 1
    lines += ["", "## 边界", "",
              "- 温度 0、单模型、单批源文，不能外推到所有模型；",
              "- 只对“本次提示过的 hard canon 术语”做缺失检测；"
              "names/soft 不强制，避免同名不同人；",
              "- B 只在缺失时增加一次请求，正常条目与 A 完全相同；",
              "- 术语采纳是客观指标，不等于人工质量评分。", ""]
    with io.open(os.path.join(args.out, "report.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("hard 采纳 A %.1f%% / B %.1f%%；修复 %d 条，清零 %d 条"
          % (summary["a_hard"], summary["b_hard"], repaired, repaired_ok))
    print("报告：%s" % os.path.join(args.out, "report.md"))


if __name__ == "__main__":
    sys.exit(main())
