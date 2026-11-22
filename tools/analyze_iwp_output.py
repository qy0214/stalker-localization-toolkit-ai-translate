# -*- coding: utf-8 -*-
"""只读分析 IWP 翻译产物的问题（不修改任何文件）。

用法: python tools/analyze_iwp_output.py [out_dir] [src_dir]
"""
import os
import re
import sys
import collections

OUT = (sys.argv[1] if len(sys.argv) > 1 else
       r"C:\Users\Admin\Desktop\IWP\gamedata\translated_hy_mt2")
SRC = (sys.argv[2] if len(sys.argv) > 2 else
       r"C:\Users\Admin\Desktop\IWP\gamedata")

CTX = ("上一句：", "下一句：", "前一句：", "后一句：",
       "上一句:", "下一句:", "前一句:", "后一句:",
       "上下文（仅供理解", "上下文(仅供理解")
_BIG = re.compile(r"(?<![\d,.])(\d{3,})(?![\d,.])")
_LATIN = re.compile(r"[A-Za-z]{3,}")
_ENTRY = re.compile(rb'<string\s+id="([^"]+)"[^>]*>(.*?)</string>', re.S)
_TEXT = re.compile(rb"<text>(.*?)</text>", re.S)
_DECL = re.compile(rb'encoding=["\']([^"\']+)["\']')


def read_any(path):
    b = open(path, "rb").read()
    try:
        return b.decode("utf-8"), "utf-8", b
    except UnicodeDecodeError:
        return b.decode("cp1251", "replace"), "cp1251", b


def entries(path):
    text, enc, raw = read_any(path)
    m = _DECL.search(raw[:200])
    decl = m.group(1).decode("ascii", "replace") if m else "?"
    out = []
    for mm in _ENTRY.finditer(text.encode(enc) if enc != "utf-8" else text.encode("utf-8")):
        eid = mm.group(1).decode(enc, "replace")
        tm = _TEXT.search(mm.group(2))
        val = tm.group(1).decode(enc, "replace") if tm else ""
        out.append((eid, val))
    return out, decl, enc


def main():
    files = []
    for cur, _d, fs in os.walk(OUT):
        for f in fs:
            if f.lower().endswith(".xml"):
                files.append(os.path.join(cur, f))
    files.sort()
    print("输出文本表：%d 个\n" % len(files))
    total = collections.Counter()
    samples = collections.defaultdict(list)
    for p in files:
        rel = os.path.relpath(p, OUT)
        sp = os.path.join(SRC, rel)
        if not os.path.isfile(sp):
            print("[%s] 源文件缺失" % rel)
            continue
        a, decl_s, enc_s = entries(sp)
        b, decl_o, enc_o = entries(p)
        ids_a = [x[0] for x in a]
        ids_b = [x[0] for x in b]
        c = collections.Counter()
        c["条目"] = len(a)
        c["条目差"] = len(b) - len(a)
        c["ID顺序不一致"] = int(ids_a != ids_b)
        c["编码声明不符"] = int(decl_o.lower().replace("-", "") != enc_o.lower().replace("-", ""))
        d = dict(a)
        for eid, val in b:
            src = d.get(eid, "")
            if not src or not val:
                c["空译文"] += 1
                continue
            if val == src:
                c["未翻译(同原文)"] += 1
                if len(samples["未翻译(同原文)"]) < 15:
                    samples["未翻译(同原文)"].append((rel, eid, src[:110]))
            hit = [m for m in CTX if m in val]
            if hit:
                c["上下文串入"] += 1
                if len(samples["上下文串入"]) < 8:
                    samples["上下文串入"].append((rel, eid, hit[0], val[:110]))
            if "\\n" in val:
                c["字面\\n"] += 1
                if len(samples["字面\\n"]) < 5:
                    samples["字面\\n"].append((rel, eid, val[:110]))
            # 未翻译：译文拉丁字母占比高且几乎没有中文
            n_zh = len(re.findall(r"[\u4e00-\u9fff]", val))
            n_lat = len(_LATIN.findall(val))
            if n_lat >= 3 and n_zh <= 1:
                c["疑似整段未译"] += 1
                if len(samples["疑似整段未译"]) < 8:
                    samples["疑似整段未译"].append((rel, eid, val[:110]))
            miss = [x for x in _BIG.findall(src) if x not in re.sub(r"[,\s]", "", val)]
            if miss:
                c["数字缺失"] += 1
                if len(samples["数字缺失"]) < 8:
                    samples["数字缺失"].append((rel, eid, ",".join(miss[:3]), val[:110]))
            if len(val) > len(src) * 2 + 200:
                c["异常膨胀"] += 1
                if len(samples["异常膨胀"]) < 5:
                    samples["异常膨胀"].append((rel, eid, "%d→%d" % (len(src), len(val)), val[:110]))
        total.update(c)
        print("[%s] src_decl=%s out_decl=%s out_actual=%s %s"
              % (rel, decl_s, decl_o, enc_o,
                 " ".join("%s=%d" % (k, v) for k, v in c.items() if v)))
    print("\n=== 汇总 ===")
    for k, v in total.most_common():
        print("  %-18s %d" % (k, v))
    for k, rows in samples.items():
        print("\n--- %s 样本 ---" % k)
        for row in rows:
            print("  " + " | ".join(str(x) for x in row))


if __name__ == "__main__":
    main()
