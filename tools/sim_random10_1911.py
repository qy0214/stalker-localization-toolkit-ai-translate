# -*- coding: utf-8 -*-
"""1.9.11 随机 10 文件端到端模拟测试。

* 从 Desktop\\gamedata 的 10 个文本表子目录各随机抽 1 个文件（seed 固定可复现），
  复制进隔离输入目录，用真实 TranslationJob + 真实 xRayXmlParser 走完整路径。
* FakeServer 故意模拟 1.8B 小模型两个实测毛病：
    1) 收到 context 就把「前一句：<上下文>」当正文吐出；
    2) 源文含 >=3 位数字时把金额/年份改写成 999。
  插件带上「逐字保留数字」重试、或去掉 context 重试时，FakeServer 变乖，
  因此能直接验证 1.9.11 两道闸门是否触发并成功纠正。
* 只读 gamedata，输出写到工作区 sim_out/random10，不写回桌面。

用法: python tools/sim_random10_1911.py [seed]
"""
import importlib.util
import os
import random
import re
import shutil
import sys
import threading
import traceback

PLUGIN = r"C:\STALKER-HH-2x\plugins\ai_translate.py"
GAME = r"C:\Users\Admin\Desktop\gamedata"
WORK = r"C:\STALKER-HH-2x\sim_out\random10"
IN_DIR = os.path.join(WORK, "in")
OUT_DIR = os.path.join(WORK, "out")
REPORT = r"C:\STALKER-HH-2x\sim_out\random10_report.md"
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 1911
N_FILES = 10

_CYR = re.compile(r"[А-Яа-яЁё]")
_CYR_WORD = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\-']*_?")
_BIG = re.compile(r"(?<![\d,.])(\d{3,})(?![\d,.])")
_SYL = "科托夫世界异变辐射警报兵器北方联邦契约区科学极暗恐惧者灵魂之音"
_CTX_MARK = ("前一句：", "后一句：", "前一句:", "后一句:")


def load_plugin():
    spec = importlib.util.spec_from_file_location("ait_1911", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_word(m):
    w = m.group(0)
    h = 0
    for ch in w.lower():
        h = (h * 31 + ord(ch)) & 0xFFFFFF
    n = min(max(len(w) // 2, 1), 3)
    return "".join(_SYL[(h >> (k * 5)) % len(_SYL)] for k in range(n))


class NaughtyServer(object):
    """模拟小模型：带上下文串入 + 大额数字改写；被点名要求时变乖。"""
    ctx_size = 8192
    parallel = 1
    model = "naughty-sim-1.8b"
    variant = "sim"

    def __init__(self):
        self.calls = 0
        self.poisoned = 0
        self.num_broken = 0
        self.clean_retry = 0
        self.num_retry = 0
        self.with_ctx_calls = 0
        self.notes = []

    def is_running(self):
        return True

    @staticmethod
    def _ok(line):
        return _CYR_WORD.sub(fake_word, line) if _CYR_WORD.search(line) else line

    def _answer(self, lines, kw):
        self.calls += 1
        note = str(kw.get("extra_sys") or "")
        ctx = str(kw.get("context") or "")
        self.notes.append(note[:80])
        obey_num = ("逐字保留" in note and ("数字" in note or "金额" in note))
        if ctx:
            self.with_ctx_calls += 1
        # 毛病 1：带上下文且未被要求忽略时，把上下文当正文输出
        if ctx and "不要输出上下文" not in note:
            self.poisoned += 1
            poison = "前一句：" + self._ok(ctx)[:240]
            out = [poison for _ in lines]
        else:
            if ctx:
                self.clean_retry += 1
            out = [self._ok(raw) for raw in lines]
        # 毛病 2：大额数字被模型改写；带数字保真要求时遵守
        if any(_BIG.search(line) for line in lines):
            if obey_num:
                self.num_retry += 1
            else:
                self.num_broken += 1
                out = [_BIG.sub("999", line) for line in out]
        return "\n".join(out)

    def translate(self, text, src_lang, tgt_lang, **kw):
        return self._answer(str(text).split("\n"), kw)

    def translate_batch(self, lines, src_lang, tgt_lang, **kw):
        return "\n".join("[%d] %s" % (i + 1, s)
                         for i, s in enumerate(self._answer(list(lines), kw)))


def pick_ten():
    """每个文本表子目录随机 1 个，共 10 个；不足则回落到全局随机。"""
    root = os.path.join(GAME, "configs", "text")
    groups = {}
    for cur, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith(".xml"):
                continue
            full = os.path.join(cur, fn)
            try:
                head = open(full, "rb").read(1 << 16)
            except OSError:
                continue
            decoded = head.decode("cp1251", "replace")
            if ("<rus" not in decoded.lower()
                    and "<text" not in decoded.lower()):
                continue
            if not _CYR.search(decoded):
                continue
            sub = os.path.basename(os.path.dirname(full)).lower()
            groups.setdefault(sub, []).append(os.path.relpath(full, GAME))
    rng = random.Random(SEED)
    picked = []
    for sub in sorted(groups):
        picked.append((sub, rng.choice(sorted(groups[sub]))))
    if len(picked) > N_FILES:
        picked = rng.sample(picked, N_FILES)
    if len(picked) < N_FILES:
        pool = [x for v in groups.values() for x in v if x not in
                [p[1] for p in picked]]
        rng.shuffle(pool)
        picked += [("extra", x) for x in pool[:N_FILES - len(picked)]]
    return sorted(picked, key=lambda x: x[1])


def prepare(picked):
    if os.path.isdir(IN_DIR):
        shutil.rmtree(IN_DIR)
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(IN_DIR)
    os.makedirs(OUT_DIR)
    names = []
    for _sub, rel in picked:
        src = os.path.join(GAME, rel)
        dst = os.path.join(IN_DIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        names.append(os.path.splitext(os.path.basename(rel))[0])
    ltx = os.path.join(IN_DIR, "configs", "localization.ltx")
    os.makedirs(os.path.dirname(ltx), exist_ok=True)
    with open(ltx, "w", encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfont_prefix =\nfiles = "
                + ", ".join(names) + "\n")


def ent_id(e):
    if isinstance(e, dict):
        return e.get("id")
    return getattr(e, "id", None)


def ent_text(e):
    if isinstance(e, dict):
        for k in ("text", "value", "content"):
            if e.get(k) is not None:
                return str(e[k])
    for k in ("text", "value", "content"):
        v = getattr(e, k, None)
        if v is not None:
            return str(v)
    return ""


def parse_entities(mod, parser, path):
    raw = open(path, "rb").read(1 << 22)
    ents = parser.parse_xray_text_xml(path, mod.encoding_chain(raw))
    return list(ents)


def verify_all(mod, parser, picked):
    rows = []
    for _sub, rel in picked:
        out = os.path.join(OUT_DIR, rel)
        src = os.path.join(GAME, rel)
        row = {"rel": rel, "out": os.path.isfile(out), "ids_ok": False,
               "bleed": 0, "num_miss": 0, "cyr_left": 0, "n": 0,
               "err": ""}
        if not row["out"]:
            row["err"] = "输出文件缺失"
            rows.append(row)
            continue
        try:
            a = parse_entities(mod, parser, src)
            b = parse_entities(mod, parser, out)
        except Exception as e:
            row["err"] = "解析失败: %r" % e
            rows.append(row)
            continue
        row["n"] = len(a)
        row["ids_ok"] = [ent_id(x) for x in a] == [ent_id(x) for x in b]
        for x, y in zip(a, b):
            s, t = ent_text(x), ent_text(y)
            if not t or t == s:
                continue
            if any(m in t for m in _CTX_MARK):
                row["bleed"] += 1
            row["num_miss"] += len(mod.missing_big_numbers(s, t))
            if _CYR.search(s) and _CYR.search(t):
                row["cyr_left"] += 1
        rows.append(row)
    return rows


def main():
    mod = load_plugin()
    picked = pick_ten()
    print("插件版本: %s  seed=%d  抽样 %d 个文件"
          % (mod.PLUGIN_INFO["version"], SEED, len(picked)))
    for sub, rel in picked:
        print("  [%s] %s" % (sub, rel))
    prepare(picked)

    srv = NaughtyServer()
    detail_lines = []
    log_lines = []

    def detail(m):
        detail_lines.append(str(m))

    def log(m, tag="info"):
        log_lines.append("[%s] %s" % (tag, m))

    job = mod.TranslationJob(
        mod._load_vendor(), srv, IN_DIR, "auto", "ru", "chs",
        glossary={}, force_glossary=True, output_anyway=False,
        ctx_size=8192, out_dir=OUT_DIR,
        log=log, detail=detail, progress=lambda *a, **k: None,
        cancel_event=threading.Event(),
        reference_mode=False, copy_reference=True, only_referenced=False,
        protect_proper=True, preview_reference=False,
        hard_glossary_keys=None, translate_literals=False,
        auto_session_terms=False, tm_lite=True)
    ok = job.run()

    parser = mod._load_vendor()
    rows = verify_all(mod, parser, picked)
    n_ctx_gate = sum("上下文串入译文" in x for x in detail_lines)
    n_ctx_fixed = sum("去上下文重译成功" in x for x in detail_lines)
    n_num_gate = sum("数字缺失" in x for x in detail_lines)
    n_num_fixed = sum("数字保真重试成功" in x for x in detail_lines)
    n_give_up = sum("保留原文" in x for x in detail_lines)

    print("\n=== 运行结果 ===")
    print("run=%s  引擎调用=%d  带上下文调用=%d  故意污染=%d  数字篡改=%d"
          % (ok, srv.calls, srv.with_ctx_calls, srv.poisoned, srv.num_broken))
    print("上下文闸门触发=%d / 纠正成功=%d ；数字闸门触发=%d / 纠正成功=%d"
          % (n_ctx_gate, n_ctx_fixed, n_num_gate, n_num_fixed))
    print("闸门放弃保留原文=%d" % n_give_up)
    print("带 note 调用=%d，note 样本=%s"
          % (sum(bool(x) for x in srv.notes),
             sorted({x for x in srv.notes if x})[:6]))
    print("%-11s %-62s %5s %6s %6s %6s %7s"
          % ("目录", "文件", "条目", "ID一致", "污染", "缺数字", "西语残留"))
    for r in rows:
        print("%-11s %-62s %5d %6s %6d %6d %7d"
              % (picked[rows.index(r)][0], r["rel"][:62], r["n"],
                 "是" if r["ids_ok"] else "否", r["bleed"],
                 r["num_miss"], r["cyr_left"]))

    bad = [r for r in rows if not r["out"] or not r["ids_ok"]
           or r["bleed"] or r["num_miss"]]
    print("\n结论: %s（异常 %d/%d 个文件）"
          % ("PASS" if ok and not bad else "FAIL", len(bad), len(rows)))

    md = ["# 1.9.11 随机 10 文件模拟测试报告", "",
          "- seed: `%d`" % SEED,
          "- 插件: `%s`（%s）" % (PLUGIN, mod.PLUGIN_INFO["version"]),
          "- 引擎: `NaughtyServer`（上下文串入 + 大额数字改写）",
          "- run: `%s`；结论: **%s**"
          % (ok, "PASS" if ok and not bad else "FAIL"), "",
          "## 闸门统计", "",
          "| 项目 | 次数 |", "| --- | ---: |",
          "| 引擎总调用 | %d |" % srv.calls,
          "| 带上下文调用 | %d |" % srv.with_ctx_calls,
          "| 故意污染上下文 | %d |" % srv.poisoned,
          "| 故意改写大额数字 | %d |" % srv.num_broken,
          "| 上下文闸门触发 / 纠正成功 | %d / %d |" % (n_ctx_gate, n_ctx_fixed),
          "| 数字闸门触发 / 纠正成功 | %d / %d |" % (n_num_gate, n_num_fixed),
          "| 闸门放弃并保留原文 | %d |" % n_give_up, "",
          "## 逐文件校验", "",
          "| 子目录 | 文件 | 条目 | ID一致 | 上下文污染 | 数字缺失 | 西语残留 | 备注 |",
          "| --- | --- | ---: | --- | ---: | ---: | ---: | --- |"]
    for r, (sub, _rel) in zip(rows, picked):
        md.append("| %s | %s | %d | %s | %d | %d | %d | %s |"
                  % (sub, r["rel"], r["n"], "是" if r["ids_ok"] else "否",
                     r["bleed"], r["num_miss"], r["cyr_left"], r["err"]))
    md += ["", "## 闸门日志（节选）", ""]
    md += ["- " + x.replace("\n", " ") for x in detail_lines[:80]]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("报告: %s" % REPORT)
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
