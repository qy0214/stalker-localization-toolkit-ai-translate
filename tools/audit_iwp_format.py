# -*- coding: utf-8 -*-
"""细查 IWP 译文的格式类问题：吞空格/标点/换行/占位符/数字/专名一致性。
对比源文与译文，定量给出每类问题条数与样本。只读，不改任何文件。"""
import os
import re
import sys
import glob

SRC = r"C:\Users\Admin\Desktop\IWP\gamedata\configs\text\eng"
OUT = r"C:\Users\Admin\Desktop\IWP\gamedata\translated_hy_mt2\configs\text\eng"

sys.path.insert(0, r"C:\STALKER-HH-2x\tools")


def entries(path):
    """返回 [(id, text)]，按 X-Ray <text> 解析。"""
    import xml.etree.ElementTree as ET
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
        eid = e.get("id", "")
        t = e.find("text")
        if t is None:
            continue
        txt = "".join(t.itertext())
        out.append((eid, txt))
    return out


def zh(s):
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def main():
    src_map = {}
    for p in glob.glob(os.path.join(SRC, "*.xml")):
        for eid, txt in entries(p):
            src_map[eid] = txt

    stats = {k: 0 for k in ("吞空格", "多空格", "标点丢失", "换行数不符",
                            "占位符缺失", "占位符增生", "数字缺失",
                            "尾部空白变化", "未译英文", "中英混排无空格")}
    samples = {k: [] for k in stats}
    total = 0

    for p in glob.glob(os.path.join(OUT, "*.xml")):
        fn = os.path.basename(p)
        for eid, out in entries(p):
            src = src_map.get(eid, "")
            if not src or not out or zh(out) == 0:
                continue
            total += 1

            def hit(k, detail):
                stats[k] += 1
                if len(samples[k]) < 8:
                    samples[k].append((fn, eid, detail))

            # 1. 源文有空格分隔的拉丁词，译文把空格吞掉（如 "FN FNX" -> "FNFNX"）
            lat_src = re.findall(r"[A-Za-z]+(?:[ -][A-Za-z0-9]+)*", src)
            for grp in lat_src:
                if " " in grp:
                    compact = grp.replace(" ", "")
                    if compact in out and grp not in out and len(compact) >= 4:
                        hit("吞空格", "源'%s' 译文含'%s'" % (grp, compact))
                        break

            # 2. 译文连续两个及以上空格（源文无）
            if re.search(r"[^\s] {2,}[^\s]", out) and not re.search(r" {2,}", src):
                hit("多空格", repr(out[:70]))

            # 3. 句末标点：源有 .!? 译无句号类收尾
            if src.rstrip().endswith((".", "!", "?")) and \
                    out.rstrip()[-1:] not in ".。!！?？…":
                hit("标点丢失", "尾='%s'" % out.rstrip()[-12:])

            # 4. 换行数
            s_nl = src.count("\\n") + src.count("\n")
            o_nl = out.count("\\n") + out.count("\n")
            if s_nl != o_nl:
                hit("换行数不符", "源%d 译%d" % (s_nl, o_nl))

            # 5/6. 占位符 $$Tn$$
            ph_s = re.findall(r"\$\$T\d+\$\$", src)
            ph_o = re.findall(r"\$\$T\d+\$\$", out)
            if ph_s and not set(ph_s) <= set(ph_o):
                hit("占位符缺失", "源%s 译%s" % (ph_s, ph_o))
            if ph_o and not set(ph_o) <= set(ph_s):
                hit("占位符增生", "源%s 译%s" % (ph_s, ph_o))

            # 7. 数字保真（3位以上独立数字）
            nums = re.findall(r"(?<![\d,.])(\d{3,})(?![\d,.])", src)
            missing = [n for n in nums if n not in out]
            if missing:
                hit("数字缺失", "缺%s" % missing)

            # 8. 尾部空白
            if (src.endswith(" ") != out.endswith(" ")) and zh(out) > 3:
                hit("尾部空白变化", repr(out[-8:]))

            # 9. 整条仍是英文（无中文）
            if not any("\u4e00" <= c <= "\u9fff" for c in out):
                hit("未译英文", repr(out[:60]))

            # 10. 中文紧跟拉丁字母无分隔（可能吞空格，仅提示）
            if re.search(r"[\u4e00-\u9fff][A-Za-z]{2,}", out) and \
                    re.search(r"[A-Za-z]\s[A-Za-z]", src):
                hit("中英混排无空格", repr(out[:70]))

    print("===== IWP 译文格式问题扫描 =====")
    print("已译条目 %d" % total)
    for k in stats:
        print("  %-16s %d" % (k, stats[k]))
    print()
    for k in stats:
        if not samples[k]:
            continue
        print("--- %s 样本 ---" % k)
        for fn, eid, detail in samples[k]:
            print("  %s | %s | %s" % (fn[:28], eid[:26], detail[:90]))
        print()


if __name__ == "__main__":
    main()
