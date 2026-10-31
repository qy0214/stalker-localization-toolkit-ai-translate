#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三轮不同 MOD 的场景批处理随机测试（复杂 + 大文件，截断采样）。

用法：
    python tools/run_scene_batch_rounds.py --mod NS --out tools/round_ns
每个 MOD 选 20 个复杂文件（截前 50 条）+ 10 个大文件（截前 100 条），
用真实 1.8B 引擎跑正常翻译路径，统计残留俄文/术语/占位符/批处理。
"""
import argparse
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
sys.path.insert(0, os.path.join(ROOT, "plugins"))
import ai_translate as ait  # noqa: E402

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop",
                       "STALKER Localization Toolkit 1.1.0")
EXE = os.path.join(DESKTOP, "plugins", "ai_translate_assets", "bin",
                   "cuda", "llama-server.exe")
MODEL = os.path.join(DESKTOP, "plugins", "ai_translate_assets", "models",
                     "Hy-MT2-1.8B-Q8_0.gguf")
MODS = {
    "NS7": r"C:\game\NS7\gamedata\configs\text",
    "STALKER": r"C:\game\S.T.A.L.K.E.R\gamedata\config\text",
    "OLR-ENG": r"C:\game\STALKER-OLR25\gamedata\config\text\eng",
}
CYR = re.compile(r"[А-Яа-яЁё]{2,}")
CJK = re.compile(r"[\u3400-\u9fff]")
PLACE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)|%\d*\$?[sd]|%c\[[^\]]*\]|"
                   r"\\n|\$\$[A-Za-z0-9_]+\$\$")


def load(p):
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "ignore"), "utf-8"


def entries_of(text):
    return list(re.finditer(r'<string\b[^>]*id="([^"]+)"[^>]*>(.*?)</string>',
                            text, re.S))


def pick_files(root, n_complex, n_large, seed):
    files = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if not f.lower().endswith(".xml"):
                continue
            p = os.path.join(dp, f)
            if any(x in f.lower() for x in
                   ("___scripte__", "___cfgxml__", "credits")):
                continue
            text, _enc = load(p)
            ents = entries_of(text)
            if not ents:
                continue
            files.append((len(ents), os.path.getsize(p), p))
    files.sort(key=lambda x: (x[0], x[1]))
    # 复杂：中大型、条目数 40 以上；大：条目数/体积最大。
    complex_pool = [x for x in files if x[0] >= 40]
    random.Random(seed).shuffle(complex_pool)
    complex_pick = complex_pool[:n_complex]
    large_pick = files[-n_large:]
    return complex_pick, large_pick


def make_input(mod_root, out_in, picks, caps, seed):
    os.makedirs(os.path.join(out_in, "configs", "text", "rus"),
                exist_ok=True)
    names = []
    manifest = []
    for kind, rows in picks:
        cap = caps[kind]
        for _n, _sz, p in rows:
            text, _enc = load(p)
            ents = entries_of(text)
            ents = ents[:cap]
            base = "%s_%s" % (kind, os.path.basename(p).replace(".xml", ""))
            name = base + "_%d" % len(names)
            names.append(name)
            dst = os.path.join(out_in, "configs", "text", "rus",
                               name + ".xml")
            with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write("<string_table>\n")
                for m in ents:
                    f.write(m.group(0) + "\n")
                f.write("</string_table>\n")
            manifest.append({"kind": kind, "src": p, "name": name,
                             "entries": len(ents)})
    with io.open(os.path.join(out_in, "configs", "localization.ltx"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = "
                + ", ".join(names) + "\n")
    return manifest


def parse_output(path):
    text, _enc = load(path)
    out = {}
    for m in entries_of(text):
        t = re.search(r"<text>(.*?)</text>", m.group(2), re.S)
        r = re.search(r"<rus>(.*?)</rus>", m.group(2), re.S)
        out[m.group(1)] = (t.group(1) if t else (r.group(1) if r else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", choices=sorted(MODS), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--ctx", type=int, default=8192)
    args = ap.parse_args()
    src_root = MODS[args.mod]
    root = tempfile.mkdtemp(prefix="ait_round_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    try:
        complex_pick, large_pick = pick_files(
            src_root, 20, 10, args.seed)
        manifest = make_input(
            src_root, ind,
            [("complex", complex_pick), ("large", large_pick)],
            {"complex": 30, "large": 60}, args.seed)
        gloss, _s, _d, _c = ait.load_glossary_layers()
        layers = ait.load_term_layers()
        idx = ait.GlossaryIndex(
            gloss, case_sensitive=layers["case_sensitive"],
            variants=layers["variants"])
        hard = layers["canon"]
        srv = ait.LlamaServer(EXE, MODEL, "cuda",
                              log=lambda m, tag="info": None,
                              ctx_size=args.ctx, tier="1.8b")
        srv.start()
        t0 = time.time()
        try:
            job = ait.TranslationJob(
                ait._load_vendor(), srv, ind, "text", "auto", "chs",
                out_dir=out, glossary=gloss, hard_glossary_keys=hard,
                force_glossary=False, reference_mode=False,
                copy_reference=False, protect_proper=False,
                translate_literals=False, auto_session_terms=False,
                tm_lite=False, ctx_size=args.ctx,
                log=lambda m, tag="info": None)
            ok = job.run()
        finally:
            srv.stop()
        elapsed = time.time() - t0
        keep = os.path.join(args.out, "output")
        if os.path.isdir(keep):
            shutil.rmtree(keep)
        shutil.copytree(out, keep)
        # 统计
        src_entries = {}
        for item in manifest:
            p = os.path.join(ind, "configs", "text", "rus",
                             item["name"] + ".xml")
            text, _enc = load(p)
            for m in entries_of(text):
                r = re.search(r"<rus>(.*?)</rus>", m.group(2), re.S)
                if not r:
                    r = re.search(r"<text>(.*?)</text>", m.group(2), re.S)
                src_entries[(item["name"], m.group(1))] = (
                    r.group(1) if r else "")
        out_entries = {}
        for item in manifest:
            p = os.path.join(out, "configs", "text", "rus",
                             item["name"] + ".xml")
            if os.path.isfile(p):
                for k, v in parse_output(p).items():
                    out_entries[(item["name"], k)] = v
        total = len(src_entries)
        translated = sum(1 for k, v in out_entries.items()
                         if v and v != src_entries.get(k))
        residual = 0; no_cjk = 0; placeholder = 0; hard_total = 0
        hard_hit = 0; samples = []
        for key, src in src_entries.items():
            dst = out_entries.get(key, "")
            if not dst:
                continue
            if CJK.search(dst) and CYR.search(dst):
                residual += 1
                if len(samples) < 12:
                    samples.append(("residual", src[:100], dst[:100]))
            if not CJK.search(dst):
                no_cjk += 1
            if PLACE.findall(src) != PLACE.findall(dst):
                placeholder += 1
            spans = [(s, e, k) for s, e, k in idx.find(src)
                     if k in hard]
            spans = [(s, e, k) for s, e, k in spans
                     if k not in layers["quote_required"]
                     or ait.TranslationJob._inside_quotes(src, s, e)]
            seen = {}
            for _s, _e, k in spans:
                seen[k] = gloss[k]
            for _k, v in seen.items():
                hard_total += 1
                if v.strip("“”") in dst:
                    hard_hit += 1
        report = {
            "mod": args.mod, "ok": ok, "elapsed": round(elapsed, 1),
            "files": len(manifest), "source_entries": total,
            "output_entries": len(out_entries),
            "translated": translated, "residual_cyr": residual,
            "no_cjk": no_cjk, "placeholder_mismatch": placeholder,
            "hard_terms": hard_total, "hard_hits": hard_hit,
            "hard_adoption": (100.0 * hard_hit / hard_total
                              if hard_total else 0),
            "batch": job.batch_stats,
            "requests": job.req_count,
            "manifest": manifest,
            "samples": samples,
        }
        os.makedirs(args.out, exist_ok=True)
        with io.open(os.path.join(args.out, "report.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        lines = [
            "# 场景批处理随机测试：%s" % args.mod, "",
            "- 结果：`%s`；耗时 `%.1f` 秒" % (ok, elapsed),
            "- 文件：%d（复杂 20 + 大 10，复杂前 30 条/大前 60 条）"
            % len(manifest),
            "- 源条目：%d；输出条目：%d；已译：%d"
            % (total, len(out_entries), translated),
            "- 残留俄文：%d；无中文：%d；占位符不一致：%d"
            % (residual, no_cjk, placeholder),
            "- hard 术语：%d 项，命中 %d（%.1f%%）"
            % (hard_total, hard_hit,
               100.0 * hard_hit / hard_total if hard_total else 0),
            "- 批处理：%s；请求数：%d" % (job.batch_stats, job.req_count),
            "", "## 样例", "",
        ]
        for kind, src, dst in samples:
            lines += ["- %s：%s" % (kind, src),
                      "  - 译：%s" % dst]
        with io.open(os.path.join(args.out, "report.md"), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        print(json.dumps({k: report[k] for k in
                          ("mod", "ok", "elapsed", "files",
                           "source_entries", "translated", "residual_cyr",
                           "no_cjk", "placeholder_mismatch", "hard_terms",
                           "hard_hits", "hard_adoption", "batch",
                           "requests")}, ensure_ascii=False, indent=1))
        print("报告：", os.path.join(args.out, "report.md"))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
