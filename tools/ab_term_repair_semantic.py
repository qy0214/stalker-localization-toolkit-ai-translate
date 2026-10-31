#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""语义型术语缺失：29 条失败样本的定点修复验证。

只把“源句 + A 译文 + 缺失术语列表”发给模型，要求只改术语、不改其它。
不启动全量 A/B；这是上线前的小样本验证。
"""
import argparse
import difflib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request

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
DEFAULT_RESULTS = os.path.join(HERE, "ab_term_repair_1000_20261031_v2",
                               "results.jsonl")
DEFAULT_OUT = os.path.join(HERE, "ab_term_repair_semantic_20261031")
CJK = re.compile(r"[\u3400-\u9fff]")
CYR = re.compile(r"[\u0400-\u04ff]")
SYS = (
    "你是《潜行者 S.T.A.L.K.E.R.》游戏本地化的中文校对员。"
    "只修正下面中文译文里漏用或译错的术语，其他文字、标点、换行、"
    "控制符、占位符一律保持原样。只输出修正后的完整中文译文，"
    "不要解释，不要加引号，不要重写其他句子。")


def term_present(target, text):
    t = str(target or "").strip().strip("\"'“”‘’«»").strip()
    return bool(t) and t in (text or "")


def repair_chat(srv, src, draft, missed, temperature=0.0):
    items = "\n".join("- %s = %s" % (k, v) for k, v in missed)
    user = ("原文：\n%s\n\n现有译文：\n%s\n\n"
            "必须使用的术语（只改这些对应部分）：\n%s\n\n"
            "如果现有译文已经用了目标译名，就不要改；"
            "不要改动其他句子、标点或换行。" % (src, draft, items))
    payload = json.dumps({
        "model": "hy-mt2",
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": user}],
        "temperature": float(temperature),
        "max_tokens": max(512, min(int(srv.ctx_size * 0.6),
                                   len(draft) * 2 + 128)),
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        srv.base + "/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "User-Agent": "ait-semantic-repair"},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    choices = data.get("choices") or []
    if not choices:
        raise IOError("no choices")
    return (choices[0].get("message") or {}).get("content", "") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--variant", default="cuda")
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    mod, tmp = base.load_plugin()
    try:
        gloss, _s, _d, _c = mod.load_glossary_layers()
        layers = mod.load_term_layers()
        hard = layers["canon"]
        idx = mod.GlossaryIndex(
            gloss, case_sensitive=layers["case_sensitive"],
            variants=layers["variants"])
        rows = []
        for line in io.open(args.results, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("repaired"):
                continue
            rows.append(r)
        os.makedirs(args.out, exist_ok=True)

        # 用当前术语库重新计算 hard 命中，过滤掉已降级/不再命中的词条。
        cases = []
        for r in rows:
            spans = [(s, e, k) for s, e, k in idx.find(r["src"])
                     if k in hard]
            # 按运行期 quote/case 规则过滤
            spans = [(s, e, k) for s, e, k in spans
                     if k not in layers["quote_required"]
                     or mod.TranslationJob._inside_quotes(
                         r["src"], s, e)]
            seen = {}
            for _s, _e, k in spans:
                seen[k] = gloss[k]
            missed = [(k, v) for k, v in seen.items()
                      if not term_present(v, r["a"])]
            if not missed:
                continue
            cases.append({"src": r["src"], "a": r["a"],
                          "missed": missed, "rel": r.get("rel", ""),
                          "id": r.get("id", "")})
        print("旧失败样本 %d；当前术语库下仍有语义缺失 %d 条"
              % (len(rows), len(cases)))
        with io.open(os.path.join(args.out, "cases.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump(cases, f, ensure_ascii=False, indent=1)
        if not cases:
            return 0
        if not os.path.isfile(args.exe) or not os.path.isfile(args.model):
            print("引擎或模型不存在")
            return 2
        srv = mod.LlamaServer(args.exe, args.model, args.variant,
                              log=lambda m, tag="info": print("  " + str(m)),
                              ctx_size=args.ctx, tier="1.8b")
        srv.start()
        out_rows = []
        try:
            for i, c in enumerate(cases, 1):
                t0 = time.time()
                try:
                    c_text = repair_chat(srv, c["src"], c["a"],
                                         c["missed"], args.temperature)
                except Exception as e:
                    c_text = c["a"]
                    err = "%s: %s" % (type(e).__name__, e)
                else:
                    err = ""
                c_text = (c_text or "").strip()
                if not c_text:
                    c_text = c["a"]
                ratio = difflib.SequenceMatcher(
                    None, c["a"], c_text).ratio()
                hits = sum(1 for _k, v in c["missed"]
                           if term_present(v, c_text))
                out_rows.append({
                    "src": c["src"], "id": c["id"], "rel": c["rel"],
                    "a": c["a"], "c": c_text,
                    "missed": c["missed"],
                    "hits_before": 0, "hits_after": hits,
                    "ratio": ratio,
                    "rewrite": ratio < 0.80,
                    "cjk": bool(CJK.search(c_text)),
                    "no_cyr": not bool(CYR.search(c_text)),
                    "newline_same": c_text.count("\n") == c["a"].count("\n"),
                    "error": err, "sec": time.time() - t0,
                })
                print("  %d/%d hits=%d/%d ratio=%.3f%s"
                      % (i, len(cases), hits, len(c["missed"]), ratio,
                         " REWRITE" if ratio < 0.80 else ""))
        finally:
            srv.stop()

        import csv
        csv_path = os.path.join(args.out, "cases.csv")
        with io.open(csv_path, "w", encoding="utf-8-sig",
                     newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "file", "source", "A_before", "C_repair",
                        "missed", "hits_after", "seq_ratio", "rewrite",
                        "cjk", "no_cyr", "newline_same", "error"])
            for r in out_rows:
                w.writerow([r["id"], r["rel"], r["src"], r["a"], r["c"],
                            "; ".join("%s=%s" % (k, v)
                                      for k, v in r["missed"]),
                            r["hits_after"], "%.3f" % r["ratio"],
                            r["rewrite"], r["cjk"], r["no_cyr"],
                            r["newline_same"], r["error"]])
        total = sum(len(r["missed"]) for r in out_rows)
        hits = sum(r["hits_after"] for r in out_rows)
        full = sum(1 for r in out_rows
                   if r["hits_after"] == len(r["missed"]))
        rewrites = sum(1 for r in out_rows if r["rewrite"])
        lines = [
            "# 语义型术语缺失：29 条失败样本修复验证", "",
            "- 模型：`%s`；上下文 `%d`；温度 `%.2f`"
            % (os.path.basename(args.model), args.ctx, args.temperature),
            "- 旧失败样本：`%d` 条；当前术语库下仍有语义缺失：`%d` 条"
            % (len(rows), len(out_rows)),
            "- 缺失术语：`%d` 项；修复后命中：`%d` 项（%.1f%%）"
            % (total, hits, 100.0 * hits / total if total else 0),
            "- 条目级全部命中：`%d/%d`（%.1f%%）"
            % (full, len(out_rows), 100.0 * full / len(out_rows)
               if out_rows else 0),
            "- 相似度 <0.80 的“重写”条目：`%d` 条" % rewrites,
            "", "## 结果", "",
            "| 指标 | 数值 |", "|---|---:|",
            "| 含中文 | %.1f%% |"
            % (100.0 * sum(1 for r in out_rows if r["cjk"])
               / len(out_rows) if out_rows else 0),
            "| 无残留西里尔 | %.1f%% |"
            % (100.0 * sum(1 for r in out_rows if r["no_cyr"])
               / len(out_rows) if out_rows else 0),
            "| 换行一致 | %.1f%% |"
            % (100.0 * sum(1 for r in out_rows if r["newline_same"])
               / len(out_rows) if out_rows else 0),
            "", "## 示例", "",
        ]
        for r in out_rows:
            lines += ["- 原文：%s" % r["src"],
                      "  - A：%s" % (r["a"] or "").replace("\n", " "),
                      "  - C：%s" % (r["c"] or "").replace("\n", " "),
                      "  - 术语：%s；hits %d/%d；相似度 %.3f"
                      % ("; ".join("%s=%s" % (k, v)
                                   for k, v in r["missed"]),
                         r["hits_after"], len(r["missed"]), r["ratio"])]
        with io.open(os.path.join(args.out, "report.md"), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        print("语义修复 %d/%d 项；整条全命中 %d/%d；报告：%s"
              % (hits, total, full, len(out_rows),
                 os.path.join(args.out, "report.md")))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
