# -*- coding: utf-8 -*-
"""IWP 译文深度扫描：串段污染/重复译文/截断/专名漂移/残留原文/格式。
只读扫描所有已产出译文，定量给样本。"""
import os
import re
import sys
import glob
from collections import defaultdict, Counter
import xml.etree.ElementTree as ET

SRC = r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng"
OUT = r"C:\Users\Admin\Desktop\IWP\gamedata\translated_hy_mt2\configs\text\eng"


def entries(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            s = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return []
    out = []
    for e in root.iter("string"):
        t = e.find("text")
        if t is None:
            continue
        out.append((e.get("id", ""), "".join(t.itertext())))
    return out


def zh(s):
    return "".join(c for c in s if "\u4e00" <= c <= "\u9fff")


def cyr(s):
    return "".join(c for c in s if "\u0400" <= c <= "\u04ff")


def main():
    src = {}
    for p in glob.glob(os.path.join(SRC, "*.xml")):
        for k, v in entries(p):
            src[k] = v

    allout = {}          # id -> (fn, val)
    fn_entries = defaultdict(list)
    for p in glob.glob(os.path.join(OUT, "*.xml")):
        fn = os.path.basename(p)
        for k, v in entries(p):
            allout[k] = (fn, v)
            fn_entries[fn].append((k, v))

    total = sum(1 for _, (fn, v) in allout.items() if zh(v))
    print("===== IWP 译文深度扫描 =====")
    print("译文文件 %d 个，已译条目 %d\n" % (len(fn_entries), total))

    # 1. 串段污染：译文含 \n\n 但源文无换行；或译文长度远超源文（>2.5x）
    bleed = []
    for k, (fn, v) in allout.items():
        s = src.get(k, "")
        if not s or not zh(v):
            continue
        s_nl = s.count("\\n")
        v_nl = v.count("\\n")
        if v_nl >= 2 and s_nl == 0:
            bleed.append((fn, k, "双\\n源无", v[:70]))
        elif len(v) > len(s) * 2.5 and len(s) > 30:
            bleed.append((fn, k, "长度%.1fx" % (len(v) / len(s)), v[:70]))
    print("--- 1. 疑似串段污染 %d ---" % len(bleed))
    for x in bleed[:12]:
        print("  %s|%s|%s|%s" % (x[0][:24], x[1][:30], x[2], repr(x[3][:50])))

    # 2. 重复译文：同一文件内多条不同 ID 译文完全相同（且长度>15）
    dup = []
    for fn, ents in fn_entries.items():
        m = defaultdict(list)
        for k, v in ents:
            if zh(v) and len(v) >= 15:
                m[v].append(k)
        for v, ids in m.items():
            if len(ids) > 1:
                # 排除源文本身相同的条目（模板句）
                srcvals = {src.get(i, "") for i in ids}
                if len(srcvals) > 1:
                    dup.append((fn, ids[:4], v[:60]))
    print("\n--- 2. 不同源→相同译文（疑似漂译/复用）%d ---" % len(dup))
    for x in dup[:12]:
        print("  %s|%s|%s" % (x[0][:24], ",".join(i[:20] for i in x[1]), repr(x[2][:45])))

    # 3. 截断：译文以标点/句子中途结束但源文完整（末字符为介词/连词或无收尾）
    trunc = []
    for k, (fn, v) in allout.items():
        s = src.get(k, "")
        if not s or not zh(v):
            continue
        # 源末尾有句末标点但译文末尾无任何收尾
        if s.rstrip()[-1:] in ".!?" and v.rstrip()[-1:] not in ".。!！?？…；;）)\"”':":
            trunc.append((fn, k, v[-30:]))
    print("\n--- 3. 疑似截断 %d ---" % len(trunc))
    for x in trunc[:12]:
        print("  %s|%s|%s" % (x[0][:24], x[1][:30], repr(x[2])))

    # 4. 残留原文：译文仍有大段西里尔或整句英文
    resid = []
    for k, (fn, v) in allout.items():
        s = src.get(k, "")
        if not s or not zh(v):
            continue
        cz = cyr(v)
        if len(cz) >= 8:
            resid.append((fn, k, "俄文", cz[:50]))
        # 英文残留：去掉专名后仍有长英文句
        eng = re.findall(r"[A-Za-z]{4,}", v)
        if len(eng) >= 6 and not any(c in v for c in "，。！？"):
            resid.append((fn, k, "英文句", v[:55]))
    print("\n--- 4. 残留原文 %d ---" % len(resid))
    for x in resid[:12]:
        print("  %s|%s|%s|%s" % (x[0][:24], x[1][:30], x[2], repr(x[3][:45])))

    # 5. 专名漂移：同一源词在译文中有多种译法
    names = defaultdict(Counter)
    for k, (fn, v) in allout.items():
        s = src.get(k, "")
        if not s or not zh(v):
            continue
        for w in re.findall(r"\b[A-Z][a-z]{2,}\b", s):
            if w in ("You", "The", "This", "That", "What", "When", "Where",
                     "With", "They", "Then", "But", "And", "For", "Are",
                     "Not", "His", "Her", "She", "Your", "From", "There"):
                continue
            # 找该词对应的中文片段（简单：整条译文作为候选）
            names[w][v] += 1
    drift = []
    for w, c in names.items():
        if len(c) >= 3:
            # 取译文中出现过该词上下文的不同译文数
            drift.append((w, len(c)))
    drift.sort(key=lambda x: -x[1])
    print("\n--- 5. 专名多译（出现≥3种译文的英文词，供人工核查）%d ---" % len(drift))
    for w, n in drift[:15]:
        print("  %-20s %d 种" % (w, n))

    # 6. 空译文/极短译文
    empty = [(fn, k, repr(v)) for k, (fn, v) in allout.items()
             if src.get(k) and len(src[k]) > 10 and not v.strip()]
    print("\n--- 6. 空译文（源>10字符）%d ---" % len(empty))
    for x in empty[:10]:
        print("  %s|%s" % (x[0][:24], x[1][:30]))

    # 7. 占位符完整性
    ph_bad = []
    for k, (fn, v) in allout.items():
        s = src.get(k, "")
        if not s or not zh(v):
            continue
        ps = re.findall(r"%c\[[^\]]+\]|$$T\d+$$|\$\$T\d+\$\$", s)
        po = re.findall(r"%c\[[^\]]+\]|\$\$T\d+\$\$", v)
        if ps and sorted(ps) != sorted(po):
            ph_bad.append((fn, k, ps[:4], po[:4]))
    print("\n--- 7. 占位符不一致 %d ---" % len(ph_bad))
    for x in ph_bad[:10]:
        print("  %s|%s|%s|%s" % (x[0][:24], x[1][:30], x[2], x[3]))


if __name__ == "__main__":
    main()
