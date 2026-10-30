# -*- coding: utf-8 -*-
"""ai_translate.py 修复验证套件（D1~D32 对应断言）

用法:
    python test_ai_translate_fixes.py [plugin.py 路径]

约定:
    * 不依赖 pytest，自带极简断言器；退出码 0 = 全通过
    * 断言必须**先失败过**（对应旧实现的缺陷）才有意义，因此关键用例里
      同时跑一遍"旧实现"做对照（见 T1/T2）
"""

import atexit
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(ROOT, "plugins", "ai_translate.py"))
VENDOR_ZIP = os.path.join(ROOT, "plugins", "ai_translate_assets", "xray_pkg.zip")

# 在临时副本上跑，避免插件导入时在仓库里生成 bin/models/logs/config。
_ORIG_PLUGIN, _ORIG_ZIP = PLUGIN, VENDOR_ZIP
_TEST_ROOT = tempfile.mkdtemp(prefix="ait_plugin_")
os.makedirs(os.path.join(_TEST_ROOT, "ai_translate_assets"), exist_ok=True)
shutil.copy2(_ORIG_PLUGIN, os.path.join(_TEST_ROOT, "ai_translate.py"))
shutil.copy2(_ORIG_ZIP, os.path.join(_TEST_ROOT, "ai_translate_assets", "xray_pkg.zip"))
_ASSET_SRC = os.path.join(ROOT, "plugins", "ai_translate_assets")
for _name in ("stalker_termbase.json", "glossary_builtin.json"):
    _src = os.path.join(_ASSET_SRC, _name)
    if os.path.isfile(_src):
        shutil.copy2(_src, os.path.join(_TEST_ROOT, "ai_translate_assets", _name))
PLUGIN = os.path.join(_TEST_ROOT, "ai_translate.py")
atexit.register(shutil.rmtree, _TEST_ROOT, ignore_errors=True)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))
    return ok


def load_module(path, name="ait"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("=" * 78)
print("加载被测插件:", PLUGIN)
ait = load_module(PLUGIN)
print("=" * 78)

TMP = tempfile.mkdtemp(prefix="ait_verify_")


# ----------------------------------------------------------------------
# T1 编码判定（D1）—— 关键：旧实现会把这些解成乱码
# ----------------------------------------------------------------------
def t1_encoding():
    print("\n--- T1 编码判定链 ---")
    zh = "潜行者，去酒吧找一个神器。"          # UTF-8 中文字节里含 0x98
    ru = "Привет, сталкер. Иди к Бару."        # 含 И(D0 98)
    wrong_decl = ('<?xml version="1.0" encoding="cp1251"?>\n'
                  '<string_table><string id="x"><rus>%s</rus></string>'
                  '</string_table>' % zh).encode("utf-8")
    bom = ("\ufeff" + zh).encode("utf-8")

    check("T1-1 UTF-8 中文 .ltx 首选 utf-8",
          ait.encoding_chain(zh.encode("utf-8"))[0] == "utf-8")
    check("T1-2 UTF-8 俄文（含 0x98）首选 utf-8",
          ait.encoding_chain(ru.encode("utf-8"))[0] == "utf-8")
    cp1251 = ru.encode("cp1251")
    chain = ait.encoding_chain(cp1251)
    check("T1-3 cp1251 文件不误判为 utf-8",
          chain[0] in ("cp1251", "windows-1251") and chain[-1] == "cp1251",
          str(chain))
    check("T1-4 BOM 文件首选 utf-8-sig",
          ait.encoding_chain(bom)[0] == "utf-8-sig")
    check("T1-5 声明与实际不符时以实际 UTF-8 为准",
          ait.encoding_chain(wrong_decl)[0] == "utf-8",
          str(ait.encoding_chain(wrong_decl)))
    check("T1-6 末位固定 cp1251（解析器强制兜底档）",
          ait.encoding_chain(cp1251)[-1] == "cp1251")
    c1252 = "Café résumé naïve — ÆØÅ".encode("cp1252")
    chain2 = ait.encoding_chain(
        ('<?xml version="1.0" encoding="windows-1252"?>' + c1252.decode()
         ).encode("ascii", "ignore") if False else c1252)
    check("T1-7 无声明 cp1252 数据也能给出可解码次序",
          chain2[0] in ("cp1251", "cp1252"), str(chain2))

    # 真正的回归点：.ltx 路径**没有** ElementTree 兜底，完全依赖候选次序
    p = os.path.join(TMP, "zh.ltx")
    with open(p, "wb") as f:
        f.write(("tip = \"%s\"\n" % zh).encode("utf-8"))
    parser = ait._load_vendor()
    chain_now = ait.encoding_chain(open(p, "rb").read())
    whole_now, _c, enc_now = parser.parse_xray_ltx_file(p, chain_now)
    check("T1-8 UTF-8 中文 .ltx 解析出原文（新次序）", zh in whole_now,
          "enc=%s text=%r" % (enc_now, whole_now[:30]))

    # 对照：旧候选次序（cp1251 优先）在同一文件上产出乱码
    whole_old, _c2, enc_old = parser.parse_xray_ltx_file(
        p, ["cp1251", "cp1252", "utf-8"])
    check("T1-9 对照：旧次序(cp1251 优先)确实产出乱码（证明缺陷真实存在）",
          zh not in whole_old,
          "enc=%s text=%r" % (enc_old, whole_old[:30]))


# ----------------------------------------------------------------------
# T2 术语索引等价性（D2 性能替换 + D27 CJK 边界修正）
# ----------------------------------------------------------------------
def old_glossary_find(text, glossary):
    """旧实现：巨型交替正则（长词优先 + \\w 词边界），逐字复刻。"""
    keys = sorted((k for k in glossary if k), key=len, reverse=True)
    if not keys:
        return []
    pat = re.compile("|".join(r"(?<!\w)" + re.escape(k) + r"(?!\w)"
                              for k in keys))
    return [(m.start(), m.end(), m.group(0)) for m in pat.finditer(text)]


def t2_glossary():
    print("\n--- T2 术语索引等价性 ---")
    glossary = {
        "Сталкер": "潜行者", "сталкер": "潜行者", "Зона": "禁区",
        "артефакт": "神器", "Артефакт": "神器", "Кот": "猫",
        "Сидорович": "西多罗维奇", "%s": "%s", "$$ACTION$$": "$$ACTION$$",
        "No": "否", "Now": "现在", "Nowhere": "无处",
        "Dark Valley": "黑暗山谷", "Бар": "酒吧", "  ": "空格",
    }
    corpus = [
        "Сталкер, возьми артефакт и иди к Бару.",
        "Артефакт «Медуза» лежит в Зоне. Кот спит.",
        "Который час? No, not Now. Nowhere to go.",
        "Sidorovich ждёт у выхода; Сталкер снова в Зоне (артефакт).",
        "xNo Now NowhereКот КоторыйКот",
        "Целый ряд слов: сталкер СТАЛКЕР Сталкер.",
        "Текст с %s и $$ACTION$$ внутри, Бар, Бар.",
        " артефакт артефакт артефакт ",
        "", "a", "Zona",
        "Dark Valley — DarkValley",
        "Зона:Зона,Зона-Зона/Зона",
        "«Сталкер» (Сталкер) [Сталкер]",
        "Сталкер!Сталкер? Сталкер. Сталкер;",
    ]
    idx = ait.GlossaryIndex(glossary)
    # 与旧实现的等价性改成"只增不减"的严格性质：
    #   每个旧命中都必须在新结果里以**同位置、同长度、同（忽略大小写的）术语**
    #   出现 —— 不允许丢命中或位移；新增命中只允许来自"原文大小写与词库不一致"
    #   （例如词库存 Сталкер，原文写 СТАЛКЕР），这是本版有意增加的能力。
    lost, added = [], []
    for text in corpus:
        a = old_glossary_find(text, glossary)
        b = idx.find(text)
        bset = {(s2, e2, k2.lower()) for s2, e2, k2 in b}
        for s1, e1, k1 in a:
            if (s1, e1, k1.lower()) not in bset:
                lost.append((text, (s1, e1, k1)))
        aset = {(s1, e1, k1.lower()) for s1, e1, k1 in a}
        for s2, e2, k2 in b:
            if (s2, e2, k2.lower()) not in aset:
                added.append((text, (s2, e2, k2)))
    check("T2-1 相对旧正则只增不减：%d 条语料无任何命中丢失/位移"
          % len(corpus), not lost, str(lost[:2]))
    check("T2-1b 新增命中全部来自原文大小写与词库不一致（有意为之）",
          all(
              # 原文该位置的片段与词库写法不同、但忽略大小写相同
              t[s:e].lower() == k.lower() and t[s:e] != k
              for t, (s, e, k) in added),
          str(added[:3]))

    hits_old = sorted((k, glossary[k])
                      for k in (m[2] for m in old_glossary_find(
                          "Сталкер и артефакт в Зоне", glossary)))
    hits_new = sorted(idx.hits("Сталкер и артефакт в Зоне"))
    check("T2-2 hits() 与旧 _glossary_hits 结果集一致", hits_old == hits_new,
          "%s vs %s" % (hits_old, hits_new))

    # D27：CJK 边界修正（有意与旧实现不同）
    zh_gloss = {"神器": "神器", "潜行者": "潜行者", "Now": "现在"}
    got = ait.GlossaryIndex(zh_gloss).find("一个神器装备和潜行者任务")
    keys = [k for _, _, k in got]
    old_keys = [k for _, _, k in old_glossary_find(
        "一个神器装备和潜行者任务", zh_gloss)]
    check("T2-3 CJK 术语在中文文本中可命中（旧实现做不到）",
          keys == ["神器", "潜行者"] and old_keys == [],
          "新=%s 旧=%s" % (keys, old_keys))

    # T2-4 / T2-5：本词库里大小写承载语义（实测 16 组同字母不同译文），
    # 所以匹配必须是"精确大小写优先"，而不是一律忽略大小写。
    conflict = {"GUARD": "哨兵", "Guard": "警卫",
                "OPTIONS": "选项", "Options": "设置"}
    ci = ait.GlossaryIndex(conflict)
    g1 = [(s, e, k, conflict[k]) for s, e, k in ci.find("GUARD и Guard")]
    check("T2-4 同字母不同大小写按原文精确匹配（GUARD→哨兵、Guard→警卫）",
          [x[3] for x in g1] == ["哨兵", "警卫"], str(g1))
    g2 = [(s, e, k, conflict[k]) for s, e, k in ci.find("guArd")]
    check("T2-5 词库写法大小写都不符时，小写兜底仍能命中（guArd→警卫）",
          [x[3] for x in g2] == ["警卫"], str(g2))


# ----------------------------------------------------------------------
# T3 flatten_glossary 混合格式（D4）
# ----------------------------------------------------------------------
def t3_flatten():
    print("\n--- T3 术语表摊平 ---")
    f = ait.flatten_glossary
    check("T3-1 扁平表", f({"a": "b"}) == {"a": "b"}, str(f({"a": "b"})))
    check("T3-2 分类表",
          f({"武器": {"ak": "AK"}, "怪物": {"x": "y"}})
          == {"ak": "AK", "x": "y"})
    mixed = f({"普通原文": "普通译文", "分类A": {"原文1": "译文1"}})
    check("T3-3 混合表两侧都保留（旧实现丢扁平条目）",
          mixed == {"普通原文": "普通译文", "原文1": "译文1"}, str(mixed))
    check("T3-4 下划线段跳过",
          f({"_meta": {"x": "y"}, "a": "b"}) == {"a": "b"})
    check("T3-5 多层嵌套",
          f({"L1": {"L2": {"k": "v"}}}) == {"k": "v"})
    check("T3-6 非字典输入", f([1, 2]) == {})
    check("T3-7 键两侧空白归一", f({" k ": "v"}) == {"k": "v"})


# ----------------------------------------------------------------------
# T4 下载产物校验（D3）
# ----------------------------------------------------------------------
def t4_artifact():
    print("\n--- T4 下载产物校验 ---")
    logs = []
    lg = lambda m, tag="info": logs.append(m)

    # 4-1 假 zip（无 marker）
    p = os.path.join(TMP, "fake_bin.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("readme.txt", "hi")
    try:
        ait.verify_artifact(p, {"kind": "zip", "filename": "llama-server.exe"}, lg)
        check("T4-1 无 llama-server.exe 的 zip 被拒", False)
    except ait.InvalidArtifact:
        check("T4-1 无 llama-server.exe 的 zip 被拒 且文件已删除",
              not os.path.exists(p))

    # 4-2 zip 里有 marker 但不是 PE
    p2 = os.path.join(TMP, "fake_bin2.zip")
    with zipfile.ZipFile(p2, "w") as z:
        z.writestr("llama-server.exe", "not a pe")
    try:
        ait.verify_artifact(p2, {"kind": "zip", "filename": "llama-server.exe"}, lg)
        check("T4-2 非 PE 的 llama-server.exe 被拒", False)
    except ait.InvalidArtifact:
        check("T4-2 非 PE 的 llama-server.exe 被拒 且文件已删除",
              not os.path.exists(p2))

    # 4-3 合法 PE zip → 通过并返回 sha256
    p3 = os.path.join(TMP, "ok_bin.zip")
    with zipfile.ZipFile(p3, "w") as z:
        z.writestr("llama-server.exe", b"MZ" + b"\x00" * 100)
    d3 = ait.verify_artifact(p3, {"kind": "zip", "filename": "llama-server.exe"}, lg)
    check("T4-3 合法 PE zip 通过并给出 sha256", len(d3) == 64, d3[:16])

    # 4-4 哈希不一致 → 拒绝
    p4 = os.path.join(TMP, "ok_bin4.zip")
    with zipfile.ZipFile(p4, "w") as z:
        z.writestr("llama-server.exe", b"MZ" + b"\x01" * 100)
    try:
        ait.verify_artifact(p4, {"kind": "zip", "filename": "llama-server.exe"},
                            lg, known_hashes={"llama-server.exe": d3})
        check("T4-4 与上次记录哈希不一致被拒", False)
    except ait.InvalidArtifact as e:
        check("T4-4 与上次记录哈希不一致被拒", "哈希不一致" in str(e))

    # 4-5 坏 GGUF
    p5 = os.path.join(TMP, "bad.gguf")
    with open(p5, "wb") as f:
        f.write(b"NOPE" + b"\x00" * 64)
    try:
        ait.verify_artifact(p5, {"kind": "model", "filename": "m.gguf"}, lg)
        check("T4-5 magic 非 GGUF 被拒", False)
    except ait.InvalidArtifact:
        check("T4-5 magic 非 GGUF 被拒", True)

    # 4-6 正常 GGUF
    p6 = os.path.join(TMP, "good.gguf")
    with open(p6, "wb") as f:
        f.write(b"GGUF" + (3).to_bytes(4, "little") + b"\x00" * (2 << 20))
    d6 = ait.verify_artifact(
        p6, {"kind": "model", "filename": "good.gguf", "size_hint": 0.002}, lg)
    check("T4-6 合法 GGUF 通过", len(d6) == 64)
    check("T4-7 cudart 前缀匹配分支可用",
          _prefix_branch_ok(ait, lg))


def _prefix_branch_ok(ait_mod, lg):
    p = os.path.join(TMP, "cudart.zip")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("build/cudart64_12.dll", b"\x00" * 32)
    try:
        ait_mod.verify_artifact(
            p, {"kind": "zip", "name_prefix": "cudart64"}, lg)
        return True
    except Exception as e:
        print("     cudart 分支异常:", e)
        return False


# ----------------------------------------------------------------------
# T5 翻译流水线：不误删译文（D8）、取消计数（D28）、单条失败隔离（D11）
# ----------------------------------------------------------------------
class FakeServer(object):
    def __init__(self, fail_words=(), truncate_once=()):
        self.calls = []
        self.fail_words = fail_words
        self.truncate_once = list(truncate_once)

    def translate(self, text, src, tgt, glossary_hits=None, timeout_sec=180,
                  max_tokens=None, extra_sys=None):
        self.calls.append(text)
        for w in self.fail_words:
            if w in text:
                raise IOError("模拟失败")
        if self.truncate_once and self.truncate_once[0] in text:
            self.truncate_once.pop(0)
            raise ait.OutputTruncated("模拟截断")
        return "译:" + text


def make_job(in_dir, mode="auto", out_dir=None, server=None, glossary=None,
             cancel=None, detail=None):
    parser = ait._load_vendor()
    return ait.TranslationJob(
        parser, server or FakeServer(), in_dir, mode, "rus", "chs",
        out_dir=out_dir, glossary=glossary or {}, log=lambda *a, **k: None,
        detail=detail, cancel_event=cancel)


def t5_pipeline():
    print("\n--- T5 流水线行为 ---")
    d = os.path.join(TMP, "t5")
    os.makedirs(os.path.join(d, "text"), exist_ok=True)

    # 5-1 非字符串表文件 + 已存在译文 → 不得删除
    src = os.path.join(d, "config.ltx")
    with open(src, "w", encoding="utf-8") as f:
        f.write('name = "Значение"')
    out = os.path.join(d, "config.ltx")
    with open(out, "w", encoding="utf-8") as f:
        f.write("上一轮译好的内容")
    job = make_job(d, mode="auto")
    # 用 text 模式并在 text 目录放一个"解析不出条目"的 XML 更能覆盖目标分支
    xml_src = os.path.join(d, "text", "ui_st.xml")
    with open(xml_src, "w", encoding="utf-8") as f:
        f.write("<?xml version='1.0'?><config><item>x</item></config>")
    out_xml = os.path.join(TMP, "t5out", "text", "ui_st.xml")
    os.makedirs(os.path.dirname(out_xml), exist_ok=True)
    with open(out_xml, "w", encoding="utf-8") as f:
        f.write("<string_table><string id='a'><chs>旧译文</chs></string>"
                "</string_table>")
    n = job._run_text_file("ui_st.xml", xml_src, out_xml,
                           os.path.join(TMP, "t5out", "__temp__", "text",
                                        "ui_st.xml"),
                           {}, 1, 1)
    check("T5-1 解析不出条目时保留既有译文（旧实现会删除）",
          n == 0 and os.path.exists(out_xml)
          and "旧译文" in open(out_xml, encoding="utf-8").read())

    # 5-2 空占位输出 → 清理
    empty_src = os.path.join(d, "text", "empty.xml")
    with open(empty_src, "w", encoding="utf-8") as f:
        f.write("<config/>")
    stale_out = os.path.join(TMP, "t5out2", "text", "empty.xml")
    os.makedirs(os.path.dirname(stale_out), exist_ok=True)
    with open(stale_out, "w", encoding="utf-8") as f:
        f.write("<string_table></string_table>")
    job._run_text_file("empty.xml", empty_src, stale_out,
                       os.path.join(TMP, "t5out2", "__temp__", "text",
                                    "empty.xml"), {}, 1, 1)
    check("T5-2 既有空占位输出被清理", not os.path.exists(stale_out))

    # 5-3 正常翻译 → 输出含译文
    ok_src = os.path.join(d, "text", "ok.xml")
    with open(ok_src, "w", encoding="utf-8") as f:
        f.write('<string_table><string id="s1"><rus>Привет</rus></string>'
                '</string_table>')
    ok_out = os.path.join(TMP, "t5out3", "text", "ok.xml")
    job3 = make_job(d, server=FakeServer())
    n3 = job3._run_text_file("ok.xml", ok_src, ok_out,
                             os.path.join(TMP, "t5out3", "__temp__", "text",
                                          "ok.xml"), {}, 1, 1)
    check("T5-3 正常文件写出译文", n3 == 1 and os.path.exists(ok_out)
          and "译:Привет" in open(ok_out, encoding="utf-8").read())

    # 5-4 取消：返回 -1 且半成品落盘
    cancel = threading.Event()
    cancel.set()
    tmp_t = os.path.join(TMP, "t5out4", "__temp__", "text", "ok.xml")
    job4 = make_job(d, server=FakeServer(), cancel=cancel)
    n4 = job4._run_text_file("ok.xml", ok_src,
                             os.path.join(TMP, "t5out4", "text", "ok.xml"),
                             tmp_t, {}, 1, 1)
    check("T5-4 取消返回 -1（不再伪装成已写出）", n4 == -1, "n=%s" % n4)

    # 5-5 单条失败隔离：一条抛错不影响其余条目
    multi_src = os.path.join(d, "text", "multi.xml")
    with open(multi_src, "w", encoding="utf-8") as f:
        f.write('<string_table>'
                '<string id="a"><rus>Один</rus></string>'
                '<string id="b"><rus>ВЗРЫВ тут</rus></string>'
                '<string id="c"><rus>Три</rus></string>'
                '</string_table>')
    multi_out = os.path.join(TMP, "t5out5", "text", "multi.xml")
    j5 = make_job(d, server=FakeServer(fail_words=("ВЗРЫВ",)))
    j5._run_text_file("multi.xml", multi_src, multi_out,
                      os.path.join(TMP, "t5out5", "__temp__", "text",
                                   "multi.xml"), {}, 1, 1)
    body = open(multi_out, encoding="utf-8").read()
    check("T5-5 单条翻译失败不影响其余条目（失败条保留原文）",
          "译:Один" in body and "译:Три" in body and j5.failed_strings == 1,
          "failed=%d" % j5.failed_strings)

    # 5-6 文件级异常隔离：坏文件不拖垮整批
    bad_src = os.path.join(d, "text", "bad.xml")
    with open(bad_src, "wb") as f:
        f.write(b"\xff\xfe\x00\x01 not xml at all \x00")
    d6 = os.path.join(TMP, "t5six")
    os.makedirs(os.path.join(d6, "text"), exist_ok=True)
    shutil.copy(ok_src, os.path.join(d6, "text", "ok.xml"))
    shutil.copy(bad_src, os.path.join(d6, "text", "bad.xml"))
    j6 = make_job(d6, mode="text")
    ok6 = j6.run()
    outs = []
    for root, _dd, ff in os.walk(os.path.join(d6, "translated_hy_mt2")):
        outs += [os.path.join(root, x) for x in ff]
    check("T5-6 坏文件被跳过、好文件照常产出",
          ok6 is True and any(x.endswith("ok.xml") for x in outs),
          "产物: %s" % [os.path.basename(x) for x in outs])


# ----------------------------------------------------------------------
# T6 输出截断保护（D12）
# ----------------------------------------------------------------------
def t6_truncation():
    print("\n--- T6 截断保护 ---")
    job = make_job(TMP, server=FakeServer(truncate_once=("ДЛИННЫЙ",)))
    job.safe_chars = 30
    text = "ДЛИННЫЙ текст для проверки. " * 3
    out = job._translate_chunk(text, "rus", None)
    check("T6-1 首次截断后自动切分重译并成功", out.startswith("译:"),
          out[:30])
    check("T6-2 触发过一次分句记录", job.long_chunks >= 1,
          "long_chunks=%d" % job.long_chunks)

    class AlwaysTrunc(FakeServer):
        def translate(self, text, src, tgt, glossary_hits=None,
                      timeout_sec=180, max_tokens=None, extra_sys=None):
            raise ait.OutputTruncated("总是截断")

    job2 = make_job(TMP, server=AlwaysTrunc())
    job2.safe_chars = 100
    try:
        job2._translate_chunk("Д" * 80, "rus", None)
        check("T6-3 持续截断时按深度上限终止（不无限递归）", False)
    except ait.OutputTruncated:
        check("T6-3 持续截断时按深度上限终止（不无限递归）", True)


# ----------------------------------------------------------------------
# T7 配置原子写 + 并发（D14）
# ----------------------------------------------------------------------
def t7_config():
    print("\n--- T7 配置写盘 ---")
    cfg_path = ait._CONFIG
    backup = None
    if os.path.exists(cfg_path):
        backup = open(cfg_path, "rb").read()

    def writer(i):
        for n in range(40):
            ait.save_config({"writer": i, "n": n,
                             "pad": "x" * (i * 7 + n)})

    ths = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    ok = False
    try:
        with open(cfg_path, encoding="utf-8") as f:
            json.load(f)
        ok = True
    except Exception as e:
        print("     解析失败:", e)
    check("T7-1 8 线程并发写 320 次后配置仍可解析（原子写+锁）", ok)
    check("T7-2 未残留 .tmp 中间文件",
          not os.path.exists(cfg_path + ".tmp"))
    if backup is not None:
        with open(cfg_path, "wb") as f:
            f.write(backup)
    else:
        os.remove(cfg_path)


# ----------------------------------------------------------------------
# T8 提示词语言名（D18）
# ----------------------------------------------------------------------
def t8_prompt():
    print("\n--- T8 提示词 ---")
    captured = {}

    class FakeResp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeReq(object):
        def __init__(self, url, data=None, headers=None, method=None):
            captured["url"] = url
            captured["data"] = data

    srv = ait.LlamaServer.__new__(ait.LlamaServer)
    srv.base = "http://127.0.0.1:1"
    srv.ctx_size = 8192
    srv.proc = type("P", (), {"poll": lambda self: None})()
    real_req, real_open = ait.urllib.request.Request, ait.urllib.request.urlopen
    ait.urllib.request.Request = FakeReq
    ait.urllib.request.urlopen = lambda req, timeout=None: FakeResp(
        json.dumps({"choices": [{"message": {"content": "好"},
                                 "finish_reason": "stop"}]}).encode())
    try:
        out = srv.translate("Привет", "rus", "chs")
    finally:
        ait.urllib.request.Request = real_req
        ait.urllib.request.urlopen = real_open
    body = json.loads(captured["data"].decode("utf-8"))
    user = body["messages"][1]["content"]
    head = user.split("：")[0]
    check("T8-1 提示词使用语言全名而非语言码",
          "俄语" in head and "下面的ru" not in head, head)
    check("T8-2 返回内容正确解析", out == "好")

    # 截断信号
    ait.urllib.request.Request = FakeReq
    ait.urllib.request.urlopen = lambda req, timeout=None: FakeResp(
        json.dumps({"choices": [{"message": {"content": "半句"},
                                 "finish_reason": "length"}]}).encode())
    try:
        srv.translate("Привет", "rus", "chs")
        check("T8-3 finish_reason=length 抛 OutputTruncated", False)
    except ait.OutputTruncated:
        check("T8-3 finish_reason=length 抛 OutputTruncated", True)
    except Exception as e:
        check("T8-3 finish_reason=length 抛 OutputTruncated", False, repr(e))
    finally:
        ait.urllib.request.Request = real_req
        ait.urllib.request.urlopen = real_open


# ----------------------------------------------------------------------
# T9 上下文决策（D19）
# ----------------------------------------------------------------------
def t9_ctx():
    print("\n--- T9 上下文决策 ---")
    info = {"max_str": 40}
    check("T9-1 自选档尊重用户设置（不被擅自缩小）",
          ait.decide_ctx("custom", info, cap=8192) == 8192)
    check("T9-2 内置档仍按需收缩", ait.decide_ctx("7b", info) <= 8192)
    check("T9-3 无勘察信息时取档位上限",
          ait.decide_ctx("mt2", None) == ait.TIER_PROFILES["mt2"]["ctx"])


# ----------------------------------------------------------------------
# T10 勘察与执行口径一致（D24）
# ----------------------------------------------------------------------
def t10_classify():
    print("\n--- T10 文件类型判定一致性 ---")
    d = os.path.join(TMP, "t10", "configs", "misc")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "strings.xml")
    with open(p, "w", encoding="utf-8") as f:
        f.write('<string_table><string id="a"><rus>Привет</rus></string>'
                '</string_table>')
    rel = os.path.join("configs", "misc", "strings.xml")
    kind = ait.classify_xray_file(p, rel)
    check("T10-1 非 text 目录但含 <string> 头 → 判为 text", kind == "text",
          str(kind))
    scan = ait.analyze_input_dir(os.path.join(TMP, "t10"), "auto")
    check("T10-2 勘察计入该文件的可译文本", scan["n_strings"] >= 1,
          "n_strings=%d files=%d" % (scan["n_strings"], scan["files"]))
    check("T10-3 勘察返回截断/取消标志位",
          "truncated" in scan and "cancelled" in scan)
    cancel = threading.Event()
    cancel.set()
    scan2 = ait.analyze_input_dir(os.path.join(TMP, "t10"), "auto",
                                  cancel=cancel)
    check("T10-4 勘察可取消", scan2["cancelled"] is True)


# ----------------------------------------------------------------------
# T11 下载：4xx 不重试 + 断点/换源（D26）
# ----------------------------------------------------------------------
def t11_download():
    print("\n--- T11 下载重试策略 ---")
    from http.server import BaseHTTPRequestHandler, HTTPServer
    hits = {"404": 0, "200": 0}
    payload = b"X" * (600 * 1024)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/missing":
                hits["404"] += 1
                self.send_response(404)
                self.end_headers()
                return
            hits["200"] += 1
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                body = payload[start:]
                self.send_response(206)
                self.send_header("Content-Range",
                                 "bytes %d-%d/%d" % (start, len(payload) - 1,
                                                     len(payload)))
            else:
                body = payload
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    target = os.path.join(TMP, "dl.bin")
    logs = []
    ok = ait.download_one(
        [("坏源", "http://127.0.0.1:%d/missing" % port),
         ("好源", "http://127.0.0.1:%d/file" % port)],
        target, log=logs.append)
    httpd.shutdown()
    check("T11-1 4xx 源只请求一次即换源（不做 3 次无谓重试）",
          hits["404"] == 1, "404 命中 %d 次" % hits["404"])
    check("T11-2 换源后下载成功且大小一致",
          ok and os.path.getsize(target) == len(payload))
    check("T11-3 成功后再无 .part 残留",
          not os.path.exists(target + ".part"))


# ----------------------------------------------------------------------
# T12 UI 封送（D32）
# ----------------------------------------------------------------------
def t12_ui_call():
    print("\n--- T12 UI 封送 ---")
    tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
    tab._q = __import__("queue").Queue()
    tab._tk = None
    tab._top = None
    calls = []
    tab._log_ui_error = lambda fn, e: calls.append(("err", str(e)))

    class FakeAPI(object):
        def log_detail(self, *a):
            calls.append(("detail", a[0]))

    tab.api = FakeAPI()
    tab.ui_call(lambda: calls.append(("ok", 1)))
    check("T12-1 未注册/已销毁时同步执行", ("ok", 1) in calls)
    tab.ui_call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    check("T12-2 同步执行异常仍留痕（不静默）",
          any(c[0] == "err" and "boom" in c[1] for c in calls))
    tab._tk = "registered"
    tab.ui_call(lambda: calls.append(("queued", 1)))
    check("T12-3 已注册时入队", tab._q.qsize() == 1 and ("queued", 1) not in calls)


# ----------------------------------------------------------------------
# T13 vendor 包等价性（语法告警修复不改变行为）
# ----------------------------------------------------------------------
def load_pkg_from_zip(zip_path, pkg_name, dir_name="xRayXmlParser"):
    """pkg_name 只作模块命名空间（用于同时加载新旧两份），
    档案内的路径固定是 dir_name（zip 里就是 xRayXmlParser/…）。"""
    import types
    z = zipfile.ZipFile(zip_path)
    order = ["entityDefinition", "xRayTextAnalyzer", "xmlFileGenerator",
             "xmlxReader"]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = []
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg
    srcs = {n: z.read("%s/%s.py" % (dir_name, n)).decode("utf-8")
            for n in order}
    init_src = z.read("%s/__init__.py" % dir_name).decode("utf-8")
    for name in order:
        mod = types.ModuleType("%s.%s" % (pkg_name, name))
        mod.__package__ = pkg_name
        sys.modules["%s.%s" % (pkg_name, name)] = mod
        exec(compile(srcs[name], "%s!%s" % (zip_path, name), "exec"),
             mod.__dict__)
        setattr(pkg, name, mod)
    exec(compile(init_src, "%s!__init__.py" % zip_path, "exec"), pkg.__dict__)
    return pkg


def t13_vendor():
    print("\n--- T13 vendor 包完整性 ---")
    import warnings
    with zipfile.ZipFile(VENDOR_ZIP) as z:
        names = z.namelist()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            for n in names:
                compile(z.read(n).decode("utf-8"), n, "exec")
        warns = sum(1 for x in w if issubclass(x.category, SyntaxWarning))
    check("T13-1 随包解析器可编译且无 SyntaxWarning", warns == 0,
          "%d 个文件 / %d 条告警" % (len(names), warns))
    pkg = load_pkg_from_zip(VENDOR_ZIP, "xRay")
    need = ("getLtxPotentialTexts", "getConfigXmlPotentialTexts",
            "getScriptPotentialTexts", "doesTextLookLikeId",
            "doesTextLookLikeScript", "getEncodingDeclaration",
            "normalize_xml_string")
    missing = [n for n in need if not hasattr(pkg, n)]
    check("T13-2 解析器关键函数齐全（提取/编码/ID 判定依赖）",
          not missing, str(missing))


# ----------------------------------------------------------------------
# T14 附加提示词 / 脏话保真口径
# ----------------------------------------------------------------------
def t14_prompt_extra():
    print("\n--- T14 附加提示词与脏话保真口径 ---")
    base = ait.TRANSLATION_SYS_PROMPT
    base_en = ait.TRANSLATION_SYS_PROMPT_EN

    # 1) 内置规则必须明确要求脏话按原意直译（否则模型会自行"文明化"）
    need = ("粗话", "不得弱化", "不得因内容而回避")
    miss = [k for k in need if k not in base]
    check("T14-1 内置提示词含「粗话按原文原意直译、不得弱化」条款",
          not miss, str(miss))

    # 2) 内置规则不得出现"跳过/忽略"类审查口径（会与本条自相矛盾）
    bad = [k for k in ("跳过粗", "忽略粗", "不予翻译", "省略粗", "避免粗俗",
                       "不要翻译粗") if k in base]
    check("T14-2 内置提示词无「跳过/忽略/避免」类限制口径", not bad, str(bad))

    # 3) 条款要带具体锚例，否则"力度"这种主观描述模型会自行解释
    check("T14-3 条款含具体锚例（дерьмо→屎／狗屎、заткнись→闭嘴）",
          "дерьмо" in base and "заткнись" in base)

    # 4~6) system 消息拼装：直接拦下 HTTP 载荷看真实提示词
    srv = ait.LlamaServer("x.exe", "y.gguf", "cpu", log=lambda *a, **k: None)
    srv.is_running = lambda: True
    srv.base = "http://127.0.0.1:1"      # 未启动引擎，只需 URL 能拼出来
    cap = {}

    class FakeResp(object):
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        cap["payload"] = json.loads(req.data.decode("utf-8"))
        body = {"choices": [{"message": {"content": "你好。"},
                             "finish_reason": "stop"}]}
        return FakeResp(json.dumps(body).encode("utf-8"))

    orig_urlopen = ait.urllib.request.urlopen
    ait.urllib.request.urlopen = fake_urlopen
    try:
        srv.prompt_extra = ""
        srv.translate("Hello.", "eng", "chs")
        sys_empty = cap["payload"]["messages"][0]["content"]

        srv.prompt_extra = "· 语气：冷峻简短的军事简报口吻。"
        srv.translate("Hello.", "eng", "chs")
        sys_full = cap["payload"]["messages"][0]["content"]

        srv.translate("Hello.", "eng", "chs", use_extra=False)
        sys_selfcheck = cap["payload"]["messages"][0]["content"]
    finally:
        ait.urllib.request.urlopen = orig_urlopen

    check("T14-4 附加提示词为空时，system 消息与英语基准提示词逐字相同",
          sys_empty == base_en, repr(sys_empty[-40:]))
    want = base_en + "\n补充要求（优先遵守）：\n· 语气：冷峻简短的军事简报口吻。"
    check("T14-5 附加提示词以「补充要求」整段追加在系统提示词末尾",
          sys_full == want, repr(sys_full[-50:]))
    check("T14-6 use_extra=False（启动自检通道）不受用户附加提示词影响",
          sys_selfcheck == base_en, repr(sys_selfcheck[-30:]))
    check("T14-13 英语提示词含英语粗口锚例与口语范例",
          all(k in base_en for k in ("fuck", "shit", "shut up",
                                     "Listen, I've got something"))
          and "дерьмо" not in base_en)
    check("T14-14 提示词回显校验覆盖中英两版锚点",
          ait.looks_like_prompt_echo(base_en[:80])
          and ait.looks_like_prompt_echo(base[:80])
          and not ait.looks_like_prompt_echo("这不是提示词，是正常译文。"))

    # 7) 多行输入框读写（Tk Text 语义 / Qt 语义都要兼容）
    class FakeTkText(object):
        def __init__(self):
            self.s = ""

        def delete(self, *a):
            self.s = ""

        def insert(self, *a):
            self.s = a[-1]

        def get(self, *a):
            return self.s

    class FakeQtText(object):
        def __init__(self):
            self.s = ""

        def toPlainText(self):
            return self.s

        def setPlainText(self, v):
            self.s = v

    t = FakeTkText()
    ait._multiline_set(t, "甲\n乙")
    tk_ok = ait._multiline_get(t) == "甲\n乙"
    q = FakeQtText()
    ait._multiline_set(q, "丙\n丁")
    qt_ok = ait._multiline_get(q) == "丙\n丁"
    check("T14-7 多行输入读写兼容 Tk（get/insert）与 Qt 语义",
          tk_ok and qt_ok, "tk=%s qt=%s" % (tk_ok, qt_ok))

    # 8) 配置项存在且默认留空（留空 = 只用内置规则，不夹带个人口径）
    check("T14-8 配置含 prompt_extra 且默认为空字符串",
          ait.DEFAULT_CONFIG.get("prompt_extra") == "")

    # 9) 示例模板本身就是"附加提示词能干什么"的自解释
    demo = ait.EXTRA_PROMPT_DEMO
    check("T14-9 示例模板覆盖语气语域/译名风格/世界观/粗口四类",
          all(k in demo for k in ("语气语域", "译名风格", "世界观", "粗口")))

    # 10~12) 口语地道性：抽象空话无效（实测），必须有条款 + 正反范例
    style_need = ("地道的中文口语", "翻译腔", "欧化", "语气词")
    miss2 = [k for k in style_need if k not in base]
    check("T14-10 内置提示词含「地道中文口语/去翻译腔」条款", not miss2,
          str(miss2))
    check("T14-11 旧的空泛表述「译文口语自然、语气贴合角色」已被替换",
          "译文口语自然、语气贴合角色" not in base)
    shot_need = ("口语对照示例", "生硬", "照右边的路子写")
    miss3 = [k for k in shot_need if k not in base]
    check("T14-12 含正反口语对照范例（实测：抽象规则对 1.8B 无效，范例才见效）",
          not miss3, str(miss3))


# ----------------------------------------------------------------------
# T15 术语提示块：上下文成本优化
# ----------------------------------------------------------------------
def t15_hint_block():
    print("\n--- T15 术语提示块（上下文优化） ---")
    pairs = [("Сидорович", "西多罗维奇"), ("Сидорович", "西多罗维奇"),
             ("Нет", "否"), ("Бар", "酒吧"), ("Зона", "禁区")]
    block, used = ait.glossary_hint_block(pairs)
    check("T15-1 大小写/重复项被去重（同一术语只出现一次）",
          block.count("Сидорович") == 1, block)
    check("T15-2 功能词（Нет）不进提示块",
          "Нет" not in block, block)
    check("T15-3 表头缩短且为紧凑 k=v 格式",
          block.startswith("术语(用给定译名)：") and " => " not in block,
          block)
    # used 是保守上界（含与正文间的空行、每行按含换行计），略大于块长
    check("T15-4 计费字符数为保守上界（块长 ≤ 计费 ≤ 块长+3）",
          0 <= used - len(block) <= 3, "%d vs %d" % (used, len(block)))

    # 预算：给一批超长术语，必须被截断在预算内
    big = [("ОченьДлинныйТермин%02d" % i, "非常长的译文编号%d" % i)
           for i in range(40)]
    blk2, used2 = ait.glossary_hint_block(big)
    check("T15-5 按字符预算截断（计费不超过 %d 字符，实际块更短）"
          % ait.GLOSSARY_HINT_BUDGET,
          used2 <= ait.GLOSSARY_HINT_BUDGET and len(blk2) <= used2,
          "%d 计费 / %d 实际 / %d 行"
          % (used2, len(blk2), blk2.count("\n")))
    check("T15-6 预算内装得下的条数远小于旧上限 15（长术语不再撑爆提示）",
          blk2.count("\n") <= 12, str(blk2.count("\n")))

    empty, u0 = ait.glossary_hint_block([("Нет", "否"), ("и", "和")])
    check("T15-7 全部被过滤时不产生空提示块（不浪费一次空注入）",
          empty == "" and u0 == 0, repr(empty))

    check("T15-8 新表头比旧表头短（每请求都要计费）",
          len(ait.GLOSSARY_HINT_HEADER) < len(
              "以下术语必须使用给定译名，不得另译或音译：") + 1,
          repr(ait.GLOSSARY_HINT_HEADER))


# ----------------------------------------------------------------------
# T16 附加提示词：工作线程只读缓存，不碰控件
# ----------------------------------------------------------------------
def t16_prompt_cache():
    print()
    print("--- T16 附加提示词（线程安全） ---")
    import queue as _queue
    tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
    tab._q = _queue.Queue()
    tab._tk = None
    tab._top = None
    tab.log_lines = []
    tab._log_ui_error = lambda fn, e: None
    tab.api = type("A", (), {"log_detail": lambda *a: None})()
    tab.log = lambda *a, **k: None

    class Srv(object):
        prompt_extra = ""

    class BoomWidget(object):
        def get(self, *a):
            raise AssertionError("工作线程不应读取 Tk 控件")

    tab.server = Srv()
    tab.extra_prompt = BoomWidget()
    tab._prompt_extra_text = "· 语气：冷峻。"
    tab._push_prompt_extra()
    check("T16-1 推送使用缓存文本（不触碰控件）",
          tab.server.prompt_extra == "· 语气：冷峻。",
          repr(tab.server.prompt_extra))
    tab._prompt_extra_text = ""
    tab._push_prompt_extra()
    check("T16-2 缓存为空时推送空串", tab.server.prompt_extra == "")
    import inspect
    src = inspect.getsource(ait.AiTranslateTab._push_prompt_extra)
    check("T16-3 实现里不再调用 _multiline_get（源码级回归）",
          "_multiline_get" not in src)


# ----------------------------------------------------------------------
# T17 完成账本：输出存在 ≠ 已译完
# ----------------------------------------------------------------------
def t17_ledger():
    print()
    print("--- T17 完成账本（防漏译/防残留） ---")
    d = os.path.join(TMP, "t17")
    src_dir = os.path.join(d, "in")
    out_dir = os.path.join(d, "out")
    os.makedirs(os.path.join(src_dir, "configs"), exist_ok=True)
    ltx = os.path.join(src_dir, "configs", "a.ltx")
    with open(ltx, "w", encoding="utf-8") as f:
        # 只用上游 ltx 提取器认可的键（description* / inv_name* / title / descr），
        # 且句子不含逗号（上游按分隔符切候选）。
        f.write('[dialogs]\ndescr = "Привет сталкер."\ntitle = "Пока."\n')
    out_ltx = os.path.join(out_dir, "configs", "a.ltx")

    s1 = FakeServer()
    make_job(src_dir, mode="auto", out_dir=out_dir, server=s1).run()
    body1 = open(out_ltx, encoding="utf-8").read() if os.path.isfile(out_ltx) else ""
    # ltx 走"提取"路径：输出里的取值被换成生成的文本 ID，译文落在
    # ___auto_put_this_to_text_folder.xml（上游设计）
    extract_xml = os.path.join(out_dir, "___auto_put_this_to_text_folder.xml")
    check("T17-1 首次翻译写出译文（ID 回填 + 译文 XML）",
          bool(body1) and "sgtat_gen_text_" in body1
          and os.path.isfile(extract_xml)
          and "译:Привет" in open(extract_xml, encoding="utf-8").read(),
          body1.strip()[:60])
    ledger = os.path.join(out_dir, ".ai_translate_done.json")
    check("T17-2 生成完成账本", os.path.isfile(ledger))

    s2 = FakeServer()
    make_job(src_dir, mode="auto", out_dir=out_dir, server=s2).run()
    check("T17-3 输入未变 → 账本命中，0 次模型调用",
          not s2.calls, "%d 次" % len(s2.calls))

    with open(ltx, "a", encoding="utf-8") as f:
        f.write('description1 = "Новая строка."\n')
    s3 = FakeServer()
    make_job(src_dir, mode="auto", out_dir=out_dir, server=s3).run()
    body = open(out_ltx, encoding="utf-8").read()
    extract_xml = os.path.join(out_dir, "___auto_put_this_to_text_folder.xml")
    check("T17-4 输入新增内容 → 重译且不漏新行",
          bool(s3.calls) and "Новая строка" in open(
              extract_xml, encoding="utf-8").read(),
          body.strip()[-60:])

    half = body[:max(1, len(body) // 2)]
    with open(out_ltx, "w", encoding="utf-8") as f:
        f.write(half)
    s4 = FakeServer()
    make_job(src_dir, mode="auto", out_dir=out_dir, server=s4).run()
    body4 = open(out_ltx, encoding="utf-8").read()
    check("T17-5 中断残留（半截输出）→ 重做而不是永久跳过",
          bool(s4.calls) and len(body4) > len(half)
          and body4.count("sgtat_gen_text_") >= 3,
          "%d 字 → %d 字" % (len(half), len(body4)))


# ----------------------------------------------------------------------
# T18 术语分层：随包只读 + 用户覆盖层
# ----------------------------------------------------------------------
def t18_glossary_layers():
    print()
    print("--- T18 术语分层（升级不丢用户词条） ---")
    d = os.path.join(TMP, "t18")
    os.makedirs(d, exist_ok=True)
    builtin = os.path.join(d, "glossary_builtin.json")
    legacy = os.path.join(d, "legacy_missing.json")
    user = os.path.join(d, "glossary_user.json")

    def write_json(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    write_json(builtin, {"A": "甲", "B": "乙"})
    old = (ait._GLOSS_BUILTIN, ait._GLOSS_BUILTIN_LEGACY, ait._GLOSS_USER)
    ait._GLOSS_BUILTIN, ait._GLOSS_BUILTIN_LEGACY, ait._GLOSS_USER = (
        builtin, legacy, user)
    try:
        merged, smap, dels, clr = ait.load_glossary_layers()
        check("T18-1 无用户层时 = 随包术语",
              merged == {"A": "甲", "B": "乙"}, str(merged))

        ait.save_user_glossary({"C": "丙"}, {"B"}, False)
        merged, smap, dels, clr = ait.load_glossary_layers()
        check("T18-2 用户新增/删除（墓碑）生效",
              merged == {"A": "甲", "C": "丙"}, str(merged))

        write_json(builtin, {"A": "甲", "B": "乙", "D": "丁"})
        merged, smap, dels, clr = ait.load_glossary_layers()
        check("T18-3 随包更新能生效（用户层不是全量快照）",
              merged == {"A": "甲", "C": "丙", "D": "丁"}, str(merged))

        write_json(user, {"A": "甲", "B": "乙改", "D": "丁"})
        merged, smap, dels, clr = ait.load_glossary_layers()
        check("T18-4 旧版扁平文件只把差异留作覆盖",
              smap == {"B": "乙改"} and merged.get("A") == "甲", str(smap))

        ait.save_user_glossary({}, set(), True)
        merged, smap, dels, clr = ait.load_glossary_layers()
        check("T18-5 clear_builtin 清空随包术语", merged == {}, str(merged))
    finally:
        (ait._GLOSS_BUILTIN, ait._GLOSS_BUILTIN_LEGACY,
         ait._GLOSS_USER) = old


# ----------------------------------------------------------------------
# T19 原子写 + 占位符安全切分
# ----------------------------------------------------------------------
def t19_atomic_split():
    print()
    print("--- T19 原子写 / 占位符切分 ---")
    d = os.path.join(TMP, "t19")
    job = make_job(os.path.join(d, "in"), mode="auto",
                   out_dir=os.path.join(d, "out"))
    out = os.path.join(d, "out", "x.txt")

    def full(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("完整成品")

    job._write_atomic(out, full)
    check("T19-1 原子写落地且无 .tmp 残留",
          open(out, encoding="utf-8").read() == "完整成品"
          and not os.path.exists(out + ".tmp"))

    def boom(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("半截")
        raise RuntimeError("模拟中断")

    try:
        job._write_atomic(out, boom)
        check("T19-2 写入失败应抛异常", False)
    except RuntimeError:
        check("T19-2 写入失败不破坏既有成品且不留 .tmp",
              open(out, encoding="utf-8").read() == "完整成品"
              and not os.path.exists(out + ".tmp"))

    text = "甲" * 120 + "$$T1$$" + "乙" * 120 + "$$T2$$" + "丙" * 120
    parts = ait.split_protected(text, 100, ait._PLACEHOLDER_RE)
    intact = sum(p.count("$$T1$$") + p.count("$$T2$$") for p in parts)
    broken = sum(p.count("$$T1") - p.count("$$T1$$")
                 + p.count("$$T2") - p.count("$$T2$$") for p in parts)
    check("T19-3 占位符不被切断（拼接无损、无半截标记）",
          "".join(parts) == text and intact == 2 and broken == 0,
          "intact=%d broken=%d" % (intact, broken))


# ----------------------------------------------------------------------
# T20 校验失败自动换源 + CUDA 运行时完整性
# ----------------------------------------------------------------------
def t20_verify_switch():
    print()
    print("--- T20 下载校验换源 / CUDA 完整性 ---")
    d = os.path.join(TMP, "t20")
    dest = os.path.join(d, "rt")
    os.makedirs(dest, exist_ok=True)

    with open(os.path.join(dest, "cudart64_12.dll"), "wb") as f:
        f.write(b"x")
    check("T20-1 只有 cudart 一个文件 → 判为不完整",
          not ait.AiTranslateTab._cudart_present(dest))
    for name in ("cublas64_12.dll", "nvrtc64_120_0.dll"):
        with open(os.path.join(dest, name), "wb") as f:
            f.write(b"x")
    check("T20-2 三组标记齐全 → 判为完整",
          ait.AiTranslateTab._cudart_present(dest))

    from http.server import BaseHTTPRequestHandler, HTTPServer
    good = b"GOOD" * 1024

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"BAD" if self.path == "/bad" else good
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    target = os.path.join(d, "payload.bin")

    def verify(path):
        data = open(path, "rb").read()
        if data != good:
            try:
                os.remove(path)
            except OSError:
                pass
            raise ait.InvalidArtifact("内容不符")
        return "digest-ok"

    try:
        got = ait.download_one(
            [("bad", "http://127.0.0.1:%d/bad" % port),
             ("good", "http://127.0.0.1:%d/good" % port)],
            target, log=lambda *a, **k: None, verify=verify,
            retries_per_source=1)
        ok = (got == "digest-ok" and os.path.isfile(target)
              and open(target, "rb").read() == good)
    finally:
        httpd.shutdown()
    check("T20-3 第一源校验失败 → 自动换下一源并成功", ok)


# ----------------------------------------------------------------------
# T21 启动自检：推理型模型不误报
# ----------------------------------------------------------------------
def t21_self_check():
    print()
    print("--- T21 启动自检误报 ---")

    def make_srv(behaviors):
        srv = ait.LlamaServer("x.exe", "y.gguf", "cpu",
                              log=lambda *a, **k: None)
        calls = []

        def fake(text, src, tgt, timeout_sec=180, max_tokens=None,
                 use_extra=True):
            calls.append(max_tokens)
            item = behaviors[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return item

        srv.translate = fake
        return srv, calls

    srv, calls = make_srv([OSError("翻译请求失败：空输出"), "你好。"])
    ok = srv.self_check()
    check("T21-1 小预算空输出 → 256 token 复核通过（不误报）",
          ok and srv.health_warn == "" and calls == [16, 256],
          "%s / %r" % (calls, srv.health_warn))

    srv, calls = make_srv([ait.OutputTruncated("撞满上限")])
    ok = srv.self_check()
    check("T21-2 真正停不下来 → 报 tokenizer/EOS 问题",
          (not ok) and "tokenizer" in srv.health_warn,
          srv.health_warn[:70])

    srv, calls = make_srv([OSError("空输出"), OSError("空输出")])
    ok = srv.self_check()
    check("T21-3 两次空输出 → 提示推理型模型可忽略，不诬告 tokenizer",
          (not ok) and "推理型" in srv.health_warn
          and "tokenizer" not in srv.health_warn,
          srv.health_warn[:80])


# ----------------------------------------------------------------------
# T22 下载源清单（多网络备援，不删除原有源）
# ----------------------------------------------------------------------
def t22_sources():
    print()
    print("--- T22 下载源清单 ---")
    gh = ait._gh_sources(ait._GH_BASE.format(variant="cpu"))
    check("T22-1 运行库源 >= 20 且全部 https",
          len(gh) >= 20 and all(u.startswith("https://") for _, u in gh),
          "%d 个" % len(gh))
    names = [n for n, _ in gh]
    check("T22-2 源名称唯一（下拉框可区分）",
          len(names) == len(set(names)))
    ms = ait._model_sources("Tencent-Hunyuan/X", "tencent/X", "x.gguf")
    check("T22-3 模型源含 ModelScope 与两个 hf-mirror 域名",
          any("modelscope.cn" in u for _, u in ms)
          and any("hf-mirror.com" in u for _, u in ms)
          and any("hf-mirror.net" in u for _, u in ms),
          "%d 个" % len(ms))


# ----------------------------------------------------------------------
# T23 GPU 检测四字段
# ----------------------------------------------------------------------
def t23_gpu_detect():
    print()
    print("--- T23 GPU 检测（驱动 / 计算能力） ---")

    class R4(object):
        returncode = 0
        stdout = "NVIDIA GeForce RTX 4090, 24564, 610.60, 8.9\n"

    class R2(object):
        returncode = 0
        stdout = "NVIDIA GeForce GTX 1050, 2048\n"

    class RFail(object):
        returncode = 1
        stdout = ""

    real = ait.subprocess.run
    calls = {"n": 0}

    def fake_fail_then_legacy(*a, **k):
        calls["n"] += 1
        return RFail() if calls["n"] == 1 else R2()

    try:
        ait.subprocess.run = lambda *a, **k: R4()
        got = ait.detect_nvidia_gpu()
        ait.subprocess.run = fake_fail_then_legacy
        got2 = ait.detect_nvidia_gpu()
    finally:
        ait.subprocess.run = real
    check("T23-1 nvidia-smi 四字段解析（名称/显存/驱动/CC）",
          got == ("NVIDIA GeForce RTX 4090", 24564, "610.60", 8.9), str(got))
    check("T23-2 老驱动不支持 compute_cap -> 退回两字段查询（不误判无卡）",
          got2 == ("NVIDIA GeForce GTX 1050", 2048, "", 0.0)
          and calls["n"] == 2, "%s / calls=%d" % (got2, calls["n"]))


# ----------------------------------------------------------------------
# T24 CUDA 13.4 门槛
# ----------------------------------------------------------------------
def t24_cuda13_gate():
    print()
    print("--- T24 CUDA 13.4 门槛 ---")
    cases = [("610.60", "8.6", True), ("580.65", "7.5", True),
             ("579.99", "8.6", False), ("610", "7.4", False),
             ("", "", False), (None, None, False)]
    bad = [(d, c, ait.cuda13_supported(d, c), w) for d, c, w in cases
           if ait.cuda13_supported(d, c) != w]
    check("T24-1 驱动 >=580 且 CC >=7.5 才判为可尝试", not bad, str(bad))


# ----------------------------------------------------------------------
# T25 CUDA 13.4 下载任务与镜像
# ----------------------------------------------------------------------
def t25_cuda13_task():
    print()
    print("--- T25 CUDA 13.4 下载源 ---")
    t = ait.DOWNLOAD_TASKS.get("bin_cuda13") or {}
    extra = t.get("extra_runtime") or {}
    check("T25-1 13.4 任务存在且哈希为官方值",
          t.get("variant") == "cuda13"
          and t.get("sha256") == "e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38"
          and extra.get("sha256") == "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
          "%s / %s" % (t.get("variant"), (t.get("sha256") or "")[:12]))
    n_main = len(t.get("sources") or [])
    n_rt = len(ait._gh_sources(extra.get("url") or ""))
    check("T25-2 13.4 主包与 cudart 各 >=20 个下载源（任意网络可换源）",
          n_main >= 20 and n_rt >= 20
          and "cuda-13.4" in (extra.get("url") or ""),
          "主包 %d / cudart %d" % (n_main, n_rt))


# ----------------------------------------------------------------------
# T26 CUDA 13.4 启动失败/未上卡 → 自动回退 12.4
# ----------------------------------------------------------------------
def t26_cuda13_fallback():
    print()
    print("--- T26 CUDA 13.4 回退 ---")
    import queue as _queue

    def make_tab():
        tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
        tab._q = _queue.Queue()
        tab._tk = None
        tab._top = None
        tab.log_lines = []
        tab._log_ui_error = lambda fn, e: None
        tab.api = type("A", (), {"log_detail": lambda *a: None})()
        tab.logs = []
        tab.log = lambda msg, tag="info": tab.logs.append(msg)
        tab._push_prompt_extra = lambda: None
        tab.find_exe = lambda v=None: (
            "C:/fake/cuda12/llama-server.exe" if v == "cuda" else None)
        return tab

    def fake_server_cls(raise_on_13, gpu_13=False):
        class FakeSrv(object):
            def __init__(self, exe, model, variant, log, ctx_size=8192,
                         tier="", gpu_layers=99):
                self.exe = exe
                self.model = model
                self.variant = variant
                self.ctx_size = ctx_size
                self.gpu_verified = (variant == "cuda")
                self.health_warn = ""

            def start(self, **kw):
                if self.variant == "cuda13":
                    if raise_on_13:
                        raise IOError("模拟 13.4 初始化失败")
                    self.gpu_verified = gpu_13
                return True

            def stop(self):
                pass

            def is_running(self):
                return True
        return FakeSrv

    real = ait.LlamaServer
    try:
        tab = make_tab()
        ait.LlamaServer = fake_server_cls(raise_on_13=True)
        srv = tab._spawn_server("exe13", "m.gguf", "cuda13", 4096, "custom")
        ok1 = (srv.variant == "cuda"
               and any("已回退到 CUDA 12.4" in x for x in tab.logs))
    finally:
        ait.LlamaServer = real
    check("T26-1 13.4 启动异常 → 自动回退 12.4", ok1,
          "%s / %s" % (getattr(srv, "variant", None), tab.logs[-2:]))

    try:
        tab2 = make_tab()
        ait.LlamaServer = fake_server_cls(raise_on_13=False, gpu_13=False)
        srv2 = tab2._spawn_server("exe13", "m.gguf", "cuda13", 4096, "custom")
        ok2 = (srv2.variant == "cuda"
               and any("未能在 GPU 上初始化" in x for x in tab2.logs))
    finally:
        ait.LlamaServer = real
    check("T26-2 13.4 未上卡 → 自动回退 12.4", ok2,
          "%s / %s" % (getattr(srv2, "variant", None), tab2.logs[-2:]))


# ----------------------------------------------------------------------
# T27 日志合并刷新（每条都整块重绘会卡死 UI）
# ----------------------------------------------------------------------
def t27_log_batch():
    print()
    print("--- T27 日志合并刷新 ---")
    import queue as _queue

    class View(object):
        def __init__(self):
            self.calls = 0
            self.text = ""

        def alive(self):
            return True

        def set_text(self, s):
            self.calls += 1
            self.text = s

    tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
    tab.log_lines = []
    tab._log_pending = False
    tab.log_view = View()
    tab.api = type("A", (), {"log": lambda *a, **k: None})()
    scheduled = []
    tab.ui_call = lambda fn, *a, **k: scheduled.append((fn, a, k))

    for i in range(100):
        tab.log("line %d" % i)
    check("T27-1 100 条日志只排队一次整块刷新", len(scheduled) == 1,
          str(len(scheduled)))
    fn, a, k = scheduled.pop(0)
    fn(*a, **k)
    check("T27-2 一次刷新写入全部内容（末行可见）",
          tab.log_view.calls == 1 and "line 99" in tab.log_view.text,
          "calls=%d" % tab.log_view.calls)

    for i in range(500):
        tab.log("more %d" % i)
    check("T27-3 环形缓冲上限 400 行", len(tab.log_lines) == 400,
          str(len(tab.log_lines)))


# ----------------------------------------------------------------------
# T28 UI 泵单次预算
# ----------------------------------------------------------------------
def t28_pump_budget():
    print()
    print("--- T28 UI 泵限流 ---")
    import queue as _queue

    class Top(object):
        def __init__(self):
            self.after_calls = []

        def after(self, ms, fn):
            self.after_calls.append(ms)

    tab = ait.AiTranslateTab.__new__(ait.AiTranslateTab)
    tab._q = _queue.Queue()
    tab._tk = "registered"
    tab._top = Top()
    tab._log_ui_error = lambda fn, e: None
    ran = []
    for i in range(1000):
        tab._q.put((ran.append, (i,), {}))
    tab._pump()
    check("T28-1 单次泵最多处理 200 项并重新排程（不饿死事件循环）",
          0 < len(ran) <= 200 and len(tab._top.after_calls) == 1,
          "ran=%d after=%d" % (len(ran), len(tab._top.after_calls)))


# ----------------------------------------------------------------------
# T29 批量文件处理不再逐文件刷 GUI 日志
# ----------------------------------------------------------------------
def t29_log_volume():
    print()
    print("--- T29 批量文件日志降量 ---")
    d = os.path.join(TMP, "t29")
    src = os.path.join(d, "in")
    out = os.path.join(d, "out")
    os.makedirs(os.path.join(src, "configs"), exist_ok=True)
    for i in range(120):
        with open(os.path.join(src, "configs", "empty_%03d.xml" % i),
                  "w", encoding="utf-8") as f:
            f.write("<config><item>x</item></config>")
    logs = []
    job = ait.TranslationJob(
        ait._load_vendor(), FakeServer(), src, "auto", "rus", "chs",
        glossary={}, force_glossary=False, ctx_size=4096, out_dir=out,
        log=lambda m, tag="info": logs.append(m))
    job.run()
    per_file = [m for m in logs if m.startswith("[")]
    check("T29-1 120 个无文本文件不产生逐文件 GUI 日志",
          not per_file and len(logs) <= 12,
          "logs=%d per_file=%d" % (len(logs), len(per_file)))
    check("T29-2 空文件计数进入收尾汇总", job.empty_files == 120,
          str(job.empty_files))


# ----------------------------------------------------------------------
# T30 llama-server 日志级别
# ----------------------------------------------------------------------
def t30_server_log_level():
    print()
    print("--- T30 服务端日志级别 ---")
    import inspect
    src = inspect.getsource(ait.LlamaServer._start_once)
    check("T30-1 使用 -lv 4 而非最大 verbose（25MB/次 -> 16KB/次）",
          '"-lv"' in src and '"4"' in src and '"--verbose"' not in src)

    log = os.path.join(TMP, "cuda_lv4.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("0.00 I llama_prepare_model_devices: using device CUDA0 "
                "(NVIDIA GeForce RTX 3060)\n"
                "0.00 I load_tensors: offloaded 43/43 layers to GPU\n")
    srv = ait.LlamaServer("x.exe", "y.gguf", "cuda",
                          log=lambda *a, **k: None)
    srv.log_file = log
    ok = srv._verify_gpu()
    check("T30-2 -lv 4 日志足以判定 GPU 上卡（43/43）",
          ok and srv.gpu_verified and srv.gpu_offloaded == (43, 43),
          str(getattr(srv, "gpu_offloaded", None)))


# ----------------------------------------------------------------------
# T31 条目级进度通道
# ----------------------------------------------------------------------
def t31_progress_channel():
    print()
    print("--- T31 条目进度通道 ---")
    import inspect
    import re
    src = inspect.getsource(ait.AiTranslateTab.on_run)
    gui_prog = re.search(r'ui_call\(\s*self\.log,\s*"  进度', src) is not None
    check("T31-1 条目进度只写运行日志，不再进界面日志（峰值 470 条/秒）",
          not gui_prog and "progress=lambda d: self.api.log_detail(" in src)


# ----------------------------------------------------------------------
# T32 cfgxml 正则灾难性回溯回归
# ----------------------------------------------------------------------
def t32_cfgxml_regex_dos():
    print()
    print("--- T32 cfgxml 正则回溯 ---")
    p = ait._load_vendor()
    bomb = os.path.join(TMP, "texture_bomb.xml")
    # 关键形态：大量自闭合 <texture .../>，没有 </text>。
    # 旧正则 (?:\s*|[^/>])*? 会为每个 <texture 前缀扫到文末，指数回溯
    # （实测同形态 ui_faction_patches.xml 58 秒，持有 GIL 冻结整个界面）。
    body = ['<?xml version="1.0" encoding="utf-8"?>', "<window>"]
    for i in range(800):
        body.append('<texture name="t%d" file="ui\\\\a/b%d.dds" '
                    'x="1" y="2" width="3" height="4"/>' % (i, i))
    body.append('<title><text font="letterica18">你好，潜行者</text></title>')
    body.append("</window>")
    with io.open(bomb, "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    t0 = time.time()
    whole, cands, enc = p.parse_xray_cfgxml_xml(bomb, ["utf-8"])
    elapsed = time.time() - t0
    check("T32-1 800 个 <texture/> 的 cfg 解析 < 2 秒（旧版 58 秒）",
          elapsed < 2.0, "%.3fs" % elapsed)
    check("T32-2 真正的 <text> 仍然提取到",
          any("你好" in c for c in cands), repr(sorted(cands)[:3]))
    # 自闭合 <text .../> 不应当作"开始标签"去吞后面的 </title>
    selfclose = os.path.join(TMP, "selfclose.xml")
    with io.open(selfclose, "w", encoding="utf-8") as f:
        f.write("<window><title>"
                '<text font="letterica16" r="255"/>\n</title></window>')
    _, cands2, _ = p.parse_xray_cfgxml_xml(selfclose, ["utf-8"])
    check("T32-3 自闭合 <text/> 不产生假候选",
          not [c for c in cands2 if c.strip()], repr(sorted(cands2)))


# ----------------------------------------------------------------------
# T33 引擎进程生命周期（Job Object / 孤儿清理）
# ----------------------------------------------------------------------
def t33_process_lifecycle():
    print()
    print("--- T33 引擎进程生命周期 ---")
    if os.name != "nt":
        check("T33-0 非 Windows：跳过 Job Object 实测", True)
        return
    job = ait._create_kill_job()
    check("T33-1 创建 kill-on-close 作业对象", bool(job))
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assigned = ait._assign_pid_to_job(job, child.pid)
        t0 = time.time()
        ait._close_handle(job)       # 关闭句柄 = 模拟宿主进程消失
        try:
            child.wait(timeout=10)
            gone = True
        except subprocess.TimeoutExpired:
            gone = False
        check("T33-2 关闭作业句柄后引擎被内核连带终止",
              assigned and gone, "assigned=%s gone=%s %.2fs"
              % (assigned, gone, time.time() - t0))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

    paths = ait._server_exe_paths()
    check("T33-3 只认本插件 bin 下的 llama-server 路径",
          any(p.endswith(os.path.join("bin", "cuda", "llama-server.exe"))
              for p in paths)
          and all(p.endswith("llama-server.exe") for p in paths),
          "n=%d" % len(paths))

    # 清理绝不能不打招呼就杀"别人的"引擎：路径不在白名单就一动不动
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        killed = ait._kill_orphan_servers(
            {"exe_paths": {"cuda": os.path.join(TMP, "other",
                                                "llama-server.exe")}})
        check("T33-4 白名单外的 llama-server 不会被误杀",
              not killed and other.poll() is None, "killed=%r" % (killed,))
    finally:
        if other.poll() is None:
            other.kill()
            other.wait()


# ----------------------------------------------------------------------
# T34 引擎启动参数与慢解析取证
# ----------------------------------------------------------------------
def t34_engine_mem_and_slow_log():
    print()
    print("--- T34 引擎参数 / 慢解析取证 ---")
    import inspect
    src = inspect.getsource(ait.LlamaServer._start_once)
    check("T34-1 单 slot 跑翻译（--parallel 1，少一份槽位开销）",
          '"--parallel"' in src and '"1"' in src)
    check("T34-2 子进程已接入 kill-on-close 作业",
          "_assign_server_to_kill_job(self.proc)" in src)
    src_tab = inspect.getsource(ait.AiTranslateTab.__init__)
    check("T34-3 启动时扫描遗留引擎",
          "_sweep_orphan_servers()" in src_tab)
    src_parse = inspect.getsource(ait.TranslationJob._parse)
    check("T34-4 单文件解析 >2 秒写详细日志、>10 秒告警",
          "elapsed >= 2.0" in src_parse and "elapsed >= 10.0" in src_parse)


# ----------------------------------------------------------------------
# T35 ID 引用层：自动解析、原地更新、引用层原样复制
# ----------------------------------------------------------------------
class _FakeServer:
    ctx_size = 16384

    def __init__(self, fn=None):
        self.calls = []
        self.fn = fn

    def is_running(self):
        return True

    def translate(self, text, src, tgt, glossary_hits=None,
                  timeout_sec=180, max_tokens=None, use_extra=True,
                  extra_sys=None):
        self.calls.append(text)
        if self.fn is not None:
            return self.fn(text)
        return "译[" + text + "]"


def _ref_tree():
    root = tempfile.mkdtemp(prefix="ait_ref_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    os.makedirs(os.path.join(ind, "configs", "gameplay"))
    os.makedirs(os.path.join(ind, "scripts"))

    def w(rel, s):
        path = os.path.join(ind, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(s)

    w(os.path.join("configs", "text", "rus", "table.xml"), """<string_table>
 <string id="dlg_1"><text>Привет, сталкер.</text></string>
 <string id="gen_1"><text>Ещё текст.</text></string>
 <string id="zh_1"><text>已经有中文</text></string>
 <string id="var_1"><text>$level $name 潜行者。</text></string>
 <string id="name_1"><text>M57</text></string>
 <string id="title_1"><text>Artist:Title</text></string>
 <string id="pct_1"><text>Привет, $name! Урон %s.</text></string>
</string_table>""")
    w(os.path.join("configs", "localization.ltx"),
      "[string_table]\nlanguage = rus\nfiles = table\n")
    w(os.path.join("configs", "gameplay", "test.xml"),
      "<game><text>dlg_1</text><text>gen_1</text></game>")
    w(os.path.join("scripts", "test.script"),
      'game.translate_string("dlg_1")\n')
    return root, ind, out


def _run_ref_job(ait, ind, out, server, **kw):
    logs = []
    preview = kw.pop("preview_reference", False)
    protect = kw.pop("protect_proper", True)
    job = ait.TranslationJob(
        ait._load_vendor(), server, ind, "auto", "auto", "chs",
        out_dir=out,
        log=lambda m, tag="info": logs.append(m),
        detail=lambda m, tag="info": None,
        reference_mode=True, copy_reference=True, only_referenced=False,
        protect_proper=protect, preview_reference=preview, **kw)
    ok = job.run()
    return job, ok, logs


def t35_reference_layer():
    print()
    print("--- T35 ID 引用层 ---")
    root, ind, out = _ref_tree()
    srv = _FakeServer()
    try:
        job, ok, logs = _run_ref_job(ait, ind, out, srv)
        text = io.open(os.path.join(out, "configs", "text", "rus",
                                    "table.xml"), encoding="utf-8").read()
        check("T35-1 引用层自动解析并完成", ok)
        check("T35-2 被引用的俄文条目译成中文",
              "译[Привет, сталкер.]" in text)
        check("T35-3 未被引用的俄文条目也译（默认不限制引用）",
              "译[Ещё текст.]" in text)
        check("T35-4 已中文条目不再送翻",
              "<text>已经有中文</text>" in text
              and "已经有中文" not in srv.calls)
        check("T35-5 含 $var 的中文条目不再送翻（修 100 条损坏）",
              "$level $name 潜行者。" in text
              and not any("$level" in c for c in srv.calls))
        check("T35-6 占位符 $name / %s 原样保留",
              "$name" in text and "%s" in text)
        check("T35-7 代号/歌名受保护，不送翻",
              "<text>M57</text>" in text and "<text>Artist:Title</text>" in text)
        check("T35-8 XML/脚本/注册表原样复制",
              io.open(os.path.join(out, "configs", "gameplay", "test.xml"),
                      encoding="utf-8").read()
              == io.open(os.path.join(ind, "configs", "gameplay", "test.xml"),
                         encoding="utf-8").read()
              and io.open(os.path.join(out, "scripts", "test.script"),
                          encoding="utf-8").read()
              == io.open(os.path.join(ind, "scripts", "test.script"),
                         encoding="utf-8").read()
              and os.path.isfile(os.path.join(out, "configs",
                                              "localization.ltx")))
        check("T35-9 不再生成未注册的 ___auto 表",
              not os.path.isfile(os.path.join(
                  out, "___auto_put_this_to_text_folder.xml")))
        check("T35-10 输出包含全部文本表（不漏文件）",
              os.path.isfile(os.path.join(out, "configs", "text", "rus",
                                          "table.xml")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T36 运行期占位符硬约束 + 目标语言判定
# ----------------------------------------------------------------------
def t36_runtime_tokens():
    print()
    print("--- T36 占位符/目标语言 ---")
    text = "$level $name %s %c[255,1,255,255] \\n"
    masked, mapping = ait.mask_runtime_tokens(text)
    check("T36-1 四类功能占位符全部 mask（换行不再严格占位）",
          len(mapping) == 4, str(mapping))
    check("T36-1b 字面 \\n 可软转换并写回",
          ait.escape_newlines(ait.unescape_newlines("第一行\\n第二行"))
          == "第一行\\n第二行")
    tech = "Оптимизация рендеринга травы — r2_details_opt"
    masked_tech, mapping_tech = ait.mask_runtime_tokens(tech)
    check("T36-1c 技术后缀被 mask，正文仍可译",
          "r2_details_opt" in mapping_tech.values()
          and "Оптимизация" in masked_tech
          and ait.restore_runtime_tokens(masked_tech, mapping_tech) == tech)
    check("T36-2 mask 后能原样回填",
          ait.restore_runtime_tokens(masked, mapping) == text)
    check("T36-3 模型丢失占位符时不回填", ait.restore_runtime_tokens(
        "坏译", mapping) is None)

    root, ind, out = _ref_tree()
    try:
        srv = _FakeServer(fn=lambda t: "坏译")       # 故意丢占位符
        job, ok, _logs = _run_ref_job(ait, ind, out, srv)
        text = io.open(os.path.join(out, "configs", "text", "rus",
                                    "table.xml"), encoding="utf-8").read()
        check("T36-4 占位符丢失时整条保留原文（不写坏译文）",
              "Привет, $name! Урон %s." in text)
        check("T36-5 已中文的 $level 条目不被重译",
              "$level $name 潜行者。" in text)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T37 专名/调试串保护
# ----------------------------------------------------------------------
def t37_protected_text():
    print()
    print("--- T37 噪声/专名保护 ---")
    noise = [
        ("configs/text/rus/table.xml", "M57"),
        ("configs/text/rus/table.xml", "AK-74M"),
        ("configs/text/rus/table.xml", "dead"),
        ("configs/text/rus/table.xml", "Copyright 2008 ReaL Studio"),
        ("configs/text/rus/table.xml", "~babah~ onItemDrop obj:"),
        ("configs/text/rus/table.xml", "https://example.com/a"),
    ]
    for rel, s in noise:
        check("T37-1 噪声跳过：%s" % s[:28],
              ait.looks_like_code_or_debug(rel, s))
    proper = [
        ("configs/text/rus/table.xml", "Artist:Title"),
        ("configs/text/rus/table.xml", "Sega Sound Team：The Dawn"),
        ("configs/text/rus/table.xml", "VSS“Vintorez”"),
        ("configs/text/rus/table.xml", "NVIDIA DLSS"),
    ]
    for rel, s in proper:
        check("T37-2 专名（可选保护）：%s" % s[:28],
              ait.looks_like_proper_text(rel, s))
    for s in ("John Smith", "Крот", "青灰色的"):
        check("T37-3 人名/正文不再整类跳过：%s" % s[:20],
              not ait.looks_like_protected_text(
                  "configs/text/character_name/table.xml", s))
    for s in ("Привет, сталкер. Как дела?", "Настройки",
              "Найди документы в подвале."):
        check("T37-4 正常正文不保护：%s" % s[:24],
              not ait.looks_like_protected_text(
                  "configs/text/rus/table.xml", s))
    for s in ("Набор 1", "Патроны 9х18мм ПМ", "30 х 5.45x39мм",
              "8 x 9x18mm", "3D-прицелы", "Частота 60Гц"):
        check("T37-5 数字正文不误判为型号：%s" % s[:24],
              not ait.looks_like_code_or_debug(
                  "configs/text/rus/table.xml", s))
    for s in ('Former digger who raids all the desolate laboratories.',
              'Stalker from the "Monolith" grouping. No details available.',
              'Sentinel of the "Monolith" grouping.'):
        check("T37-6 英语正文不被 f[A-Z] 误判：%s" % s[:24],
              not ait.looks_like_code_or_debug(
                  "configs/text/character_name/table.xml", s))


# ----------------------------------------------------------------------
# T38 无改动字节复制 + BOM + 注释保留 + 预览不写盘
# ----------------------------------------------------------------------
def t38_noop_bom_preview():
    print()
    print("--- T38 无改动/BOM/预览 ---")
    root = tempfile.mkdtemp(prefix="ait_bom_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    path = os.path.join(ind, "configs", "text", "rus", "table.xml")
    raw = ("\ufeff<string_table>\n  <!-- keep me -->\n"
           '  <string id="a"><text>中文</text></string>\n'
           "</string_table>\n").encode("utf-8")
    open(path, "wb").write(raw)
    srv = _FakeServer()
    try:
        job, ok, _logs = _run_ref_job(ait, ind, out, srv)
        got = open(os.path.join(out, "configs", "text", "rus",
                                "table.xml"), "rb").read()
        check("T38-1 无改动表逐字节复制（BOM/注释不变）", got == raw)
        check("T38-2 无改动不调用模型", not srv.calls, str(srv.calls))
        # 预览不写盘
        out2 = os.path.join(root, "out2")
        srv2 = _FakeServer()
        job2, ok2, _logs2 = _run_ref_job(ait, ind, out2, srv2,
                                        preview_reference=True)
        check("T38-3 预览模式不写盘",
              ok2 and not os.path.isdir(out2) or
              (not any(os.scandir(out2)) if os.path.isdir(out2) else True))
        check("T38-4 预览模式不调用模型", not srv2.calls)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T39 UI/流程接线
# ----------------------------------------------------------------------
def t39_reference_wiring():
    print()
    print("--- T39 引用层接线 ---")
    import inspect
    src_build = inspect.getsource(ait.AiTranslateTab._build_step_job)
    for key in ("reference_layer", "copy_reference", "only_referenced",
                "protect_proper", "preview_reference"):
        check("T39-1 界面选项存在：%s" % key, key in src_build)
    src_run = inspect.getsource(ait.AiTranslateTab.on_run)
    check("T39-2 任务传入引用层开关", "reference_mode=ref_mode" in src_run)
    src_job = inspect.getsource(ait.TranslationJob.run)
    check("T39-3 自动/文本模式走引用层", "_run_reference_layer" in src_job)
    src_tok = inspect.getsource(ait.TranslationJob._translate_one)
    check("T39-4 翻译入口强制 mask 占位符",
          "mask_runtime_tokens" in src_tok
          and "restore_runtime_tokens" in src_tok)


# ----------------------------------------------------------------------
# T40 源语言 rus 优先 + 语言标签保留 + 脚本/XML 字面文本层
# ----------------------------------------------------------------------
def t40_source_tags_literals():
    print()
    print("--- T40 源语言/标签/字面文本 ---")
    root = tempfile.mkdtemp(prefix="ait_lit_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "dialogs"))
    os.makedirs(os.path.join(ind, "configs", "ui"))
    os.makedirs(os.path.join(ind, "scripts"))

    def w(rel, s, enc="utf-8"):
        path = os.path.join(ind, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding=enc, newline="\n") as f:
            f.write(s)

    w(os.path.join("configs", "text", "dialogs", "table.xml"), """<?xml version="1.0" encoding="utf-8"?>
<string_table>
<string id="a"><rus>Привет, сталкер.</rus><eng>Hello, stalker.</eng></string>
</string_table>
""")
    w(os.path.join("configs", "ui", "test.xml"),
      "<ui><text>Привет из UI</text></ui>\n")
    w(os.path.join("scripts", "test.script"),
      'local news_text = "Привет из скрипта."\n')
    srv = _FakeServer(fn=lambda t: "译[" + t + "]")
    try:
        job, ok, logs = _run_ref_job(ait, ind, out, srv,
                                    translate_literals=True,
                                    protect_proper=False)
        text = io.open(os.path.join(out, "configs", "text", "dialogs",
                                    "table.xml"), encoding="utf-8").read()
        check("T40-1 源语言默认 rus，不翻 eng",
              "译[Привет, сталкер.]" in text and "Hello, stalker." in text
              and "译[Hello" not in text)
        check("T40-2 保留 <rus>/<eng> 标签（不再只剩 <text>）",
              "<rus>" in text and "<eng>" in text and "<text>" not in text)
        ui = io.open(os.path.join(out, "configs", "ui", "test.xml"),
                     encoding="utf-8").read()
        script = io.open(os.path.join(out, "scripts", "test.script"),
                         encoding="utf-8").read()
        check("T40-3 UI 字面文本被抽成新 ID",
              "sgtat_gen_text_" in ui and "Привет из UI" not in ui)
        check("T40-4 脚本字面文本被抽成新 ID 并包 game.translate_string",
              'game.translate_string("sgtat_gen_text_' in script
              and "Привет из скрипта." not in script)
        table = os.path.join(out, "configs", "text", "sgtat_auto_texts.xml")
        check("T40-5 生成字面文本表", os.path.isfile(table))
        if os.path.isfile(table):
            ts = io.open(table, encoding="utf-8").read()
            check("T40-6 表内有 UI/脚本中文",
                  "译[Привет из UI]" in ts
                  and "译[Привет из скрипта.]" in ts)
        loc = os.path.join(out, "configs", "localization.ltx")
        ls = io.open(loc, encoding="utf-8").read() if os.path.isfile(loc) else ""
        check("T40-7 新表已注册到 localization.ltx",
              "sgtat_auto_texts" in ls and "files" in ls)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T41 清理/硬术语分类/标识符过滤/cp1251 标签保留
# ----------------------------------------------------------------------
def t41_cleanup_and_cp1251():
    print()
    print("--- T41 清理/硬术语/cp1251 ---")
    check("T41-1 有有→有（功能词重复清理）",
          ait.clean_translation_output("有有用的信息吗？") == "有用的信息吗？")
    check("T41-2 正常叠词不动（看看）",
          ait.clean_translation_output("去看看情况") == "去看看情况")
    hard = ait.load_hard_glossary_keys()
    check("T41-3 对话/界面普通词不进硬术语（Информацию）",
          "Информацию" not in hard and "Здоровье" not in hard,
          "hard=%d" % len(hard))
    check("T41-4 专名类仍进硬术语（Псевдогигант）",
          "Псевдогигант" in hard)
    for s in ("resque information", "Courier document", "My map task"):
        check("T41-5 识别任务/段落名：%s" % s[:22],
              ait.looks_like_identifier_text(s))
    check("T41-6 真正俄文不误判为标识符",
          not ait.looks_like_identifier_text("Безумный снайпер"))

    # cp1251 → UTF-8 时保留 <rus>/<eng>
    root = tempfile.mkdtemp(prefix="ait_cp_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    raw = ("""<?xml version="1.0" encoding="windows-1251"?>
<string_table>
<string id="a"><rus>Привет</rus><eng>Hello</eng></string>
</string_table>
""").encode("cp1251")
    open(os.path.join(ind, "configs", "text", "rus", "cp.xml"), "wb").write(raw)
    srv = _FakeServer(fn=lambda t: "译[" + t + "]")
    try:
        job, ok, logs = _run_ref_job(ait, ind, out, srv)
        text = io.open(os.path.join(out, "configs", "text", "rus", "cp.xml"),
                       encoding="utf-8").read()
        check("T41-7 cp1251 转 UTF-8 后保留 <rus>/<eng>",
              "<rus>译[Привет]</rus>" in text and "<eng>Hello</eng>" in text
              and "windows-1251" not in text)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T42 脚本上下文/英语源文/提示词回显
# ----------------------------------------------------------------------
def t42_script_context_and_english():
    print()
    print("--- T42 脚本上下文/英语源文/提示词回显 ---")
    check("T42-1 俄文/英文内容检测",
          ait.detect_text_lang("Привет, сталкер.") == "rus"
          and ait.detect_text_lang("Hello, stalker.") == "eng"
          and ait.detect_text_lang("AK-74") == "")
    check("T42-2 语言全名用中文（不再把 English 塞进中文句）",
          ait.LANG_FULL.get("eng") == "英语"
          and ait.LANG_FULL.get("rus") == "俄语")
    check("T42-3 英语系统提示词含英语锚例",
          "fuck" in ait.TRANSLATION_SYS_PROMPT_EN
          and "Listen, I've got something" in ait.TRANSLATION_SYS_PROMPT_EN)

    root = tempfile.mkdtemp(prefix="ait_ctx_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    os.makedirs(os.path.join(ind, "scripts"))

    with io.open(os.path.join(ind, "scripts", "ctx.script"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(
            'local text = "Привет из скрипта."\n'
            'log1("debug text")\n'
            'if string_find(inv_name, "П:") == 1 then\n'
            '  return trim(string_gsub(inv_name, "П:", "X"))\n'
            'end\n'
            'local tag = "logic_tag"\n'
            'self.search:SetText("Кнопка поиска")\n'
            'amk.send_tip("Подсказка игроку")\n')
    with io.open(os.path.join(ind, "configs", "text", "rus", "eng.xml"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write('<string_table>\n'
                '<string id="en1"><rus>Hello, stalker.</rus></string>\n'
                '</string_table>\n')

    class LangServer(_FakeServer):
        def __init__(self):
            _FakeServer.__init__(self)
            self.langs = []

        def translate(self, text, src, tgt, glossary_hits=None,
                      timeout_sec=180, max_tokens=None, extra_sys=None):
            self.calls.append(text)
            self.langs.append((text, src))
            return "译[" + text + "]"

    srv = LangServer()
    try:
        job, ok, _logs = _run_ref_job(ait, ind, out, srv,
                                      translate_literals=True,
                                      protect_proper=False)
        script = io.open(os.path.join(out, "scripts", "ctx.script"),
                         encoding="utf-8").read()
        check("T42-4 脚本正常完成", ok)
        check("T42-5 显示类调用包 game.translate_string",
              'self.search:SetText(game.translate_string(' in script
              and 'amk.send_tip(game.translate_string(' in script
              and 'local text = game.translate_string(' in script)
        check("T42-6 log 调试串保持原样",
              'log1("debug text")' in script)
        check("T42-7 string_find/string_gsub 逻辑串保持原样",
              'string_find(inv_name, "П:")' in script
              and 'string_gsub(inv_name, "П:", "X")' in script)
        check("T42-8 非文本变量/标签串保持原样",
              'local tag = "logic_tag"' in script)
        check("T42-9 脚本里没有裸 sgtat 全局引用",
              script.count("sgtat_gen_text_")
              == script.count('game.translate_string("sgtat_gen_text_'))
        table = io.open(os.path.join(out, "configs", "text",
                                     "sgtat_auto_texts.xml"),
                        encoding="utf-8").read()
        check("T42-10 生成表含脚本显示文本译文",
              "译[Кнопка поиска]" in table
              and "译[Подсказка игроку]" in table
              and "译[Привет из скрипта.]" in table)
        check("T42-11 英语源文按 eng 送翻（字段是 rus 也不误导）",
              ("Hello, stalker.", "eng") in srv.langs
              and not any(t == "Hello, stalker." and s == "rus"
                          for t, s in srv.langs))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # 提示词回显必须整条回退原文
    root2 = tempfile.mkdtemp(prefix="ait_echo_")
    ind2 = os.path.join(root2, "in")
    out2 = os.path.join(root2, "out")
    os.makedirs(os.path.join(ind2, "configs", "text", "rus"))
    with io.open(os.path.join(ind2, "configs", "text", "rus", "echo.xml"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write('<string_table><string id="e1"><rus>Привет</rus></string>'
                '</string_table>')
    echo_srv = _FakeServer(fn=lambda t: ait.TRANSLATION_SYS_PROMPT_EN[:120])
    try:
        job2, ok2, _ = _run_ref_job(ait, ind2, out2, echo_srv,
                                    translate_literals=False,
                                    protect_proper=False)
        body = io.open(os.path.join(out2, "configs", "text", "rus",
                                    "echo.xml"), encoding="utf-8").read()
        check("T42-12 提示词回显被拦截并保留原文",
              ok2 and "<rus>Привет</rus>" in body
              and "你是《潜行者" not in body)
    finally:
        shutil.rmtree(root2, ignore_errors=True)


# ----------------------------------------------------------------------
# T43 多行/粗口重试
# ----------------------------------------------------------------------
def t43_multiline_profanity_retry():
    print()
    print("--- T43 多行/粗口重试 ---")
    root = tempfile.mkdtemp(prefix="ait_retry_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    with io.open(os.path.join(ind, "configs", "text", "rus", "r.xml"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write('<string_table>\n'
                '<string id="m1"><rus>Первая строка.\\nВторая строка.</rus></string>\n'
                '<string id="m2"><rus>Один.\\nДва.\\nТри.</rus></string>\n'
                '<string id="p1"><rus>Ну а на хрен тебе не пойти?</rus></string>\n'
                '</string_table>\n')

    calls = {"m1": 0, "m2": 0, "p1": 0}

    def fn(text):
        if "Первая" in text:
            calls["m1"] += 1
            return "合并成一行" if calls["m1"] == 1 else "第一行\n第二行"
        if "Один" in text:
            calls["m2"] += 1
            return "第一句。第二句。第三句。"
        if "хрен" in text:
            calls["p1"] += 1
            return "那你就别去了吧？" if calls["p1"] == 1 \
                else "你他妈怎么还不滚？"
        return "译[" + text + "]"

    srv = _FakeServer(fn=fn)
    try:
        job, ok, _logs = _run_ref_job(ait, ind, out, srv,
                                      protect_proper=False)
        body = io.open(os.path.join(out, "configs", "text", "rus",
                                    "r.xml"), encoding="utf-8").read()
        check("T43-1 多行合并被重试并写回字面 \\n",
              ok and "第一行\\n第二行" in body and calls["m1"] == 2)
        check("T43-3 重试仍合并时按句末标点补回换行",
              "第一句。\\n第二句。\\n第三句。" in body and calls["m2"] >= 2)
        check("T43-2 粗口软化被重试并保留力度",
              "你他妈怎么还不滚" in body and calls["p1"] == 2)
        check("T43-4 粗口判定不误伤普通词（操作/可靠）",
              not ait.has_zh_profanity("正在操作设备，这很可靠")
              and ait.has_zh_profanity("你他妈闭嘴"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T44 问题文件筛选/无中文重试
# ----------------------------------------------------------------------
def t44_problem_file_filters():
    print()
    print("--- T44 问题文件筛选/无中文重试 ---")
    cases = [
        ("configs/text/ui/stable_pc_text.xml", "Аномалии.html"),
        ("configs/text/ui/ui_sss.xml",
         "[ТРЕБУЕТСЯ ПЕРЕЗАПУСК] Включение/выключение коллизии"),
        ("configs/text/character_name/stable_sms_character_name.xml",
         "ГОЛЬФСТРИМ 7-2"),
        ("configs/text/encyclopedia/string_table_enc_zone.xml", "ЧАЭС-2"),
        ("configs/text/news/news.xml", "[источник неизвестен]"),
    ]
    for rel, s in cases:
        check("T44-1 可见文件名/标签/大写名不误跳：%s" % s[:26],
              not ait.looks_like_code_or_debug(rel, s))
    check("T44-2 纯型号仍跳过", ait.looks_like_code_or_debug(
        "configs/text/rus/x.xml", "AK-74M"))
    root = tempfile.mkdtemp(prefix="ait_complete_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    with io.open(os.path.join(ind, "configs", "text", "rus", "c.xml"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write('<string_table><string id="c1">'
                '<rus>Worldwide Publishing</rus></string></string_table>')
    calls = {"n": 0}

    def fn(text):
        calls["n"] += 1
        return ("Worldwide Publishing" if calls["n"] == 1
                else "全球发行")

    srv = _FakeServer(fn=fn)
    try:
        job, ok, _ = _run_ref_job(ait, ind, out, srv, protect_proper=False)
        body = io.open(os.path.join(out, "configs", "text", "rus", "c.xml"),
                       encoding="utf-8").read()
        check("T44-3 模型回显英文时按完整性重试并写中文",
              ok and "全球发行" in body and calls["n"] == 2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T45 通用硬词收敛/技术精确/会话词表缓存
# ----------------------------------------------------------------------
def t45_term_layers_session_cache():
    print()
    print("--- T45 硬词收敛/技术精确/会话词表 ---")
    check("T45-1 人物名不再全局硬词（Крот）", "Крот" not in ait.load_hard_glossary_keys())
    check("T45-2 宇宙概念仍是硬词（Псевдогигант）",
          "Псевдогигант" in ait.load_hard_glossary_keys())
    root = tempfile.mkdtemp(prefix="ait_sess_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    cache = os.path.join(root, "cache")
    os.makedirs(os.path.join(ind, "configs", "text", "character_name"))
    with io.open(os.path.join(ind, "configs", "text", "character_name",
                             "names.xml"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write('<string_table>'
                '<string id="n1_name"><rus>Иванов</rus></string>'
                '<string id="u1"><rus>ч.</rus></string>'
                '</string_table>')
    srv = _FakeServer()
    try:
        job = ait.TranslationJob(
            ait._load_vendor(), srv, ind, "auto", "auto", "chs",
            out_dir=out, glossary={}, force_glossary=False,
            hard_glossary_keys=set(), log=lambda *a, **k: None,
            reference_mode=False, copy_reference=False,
            protect_proper=False, translate_literals=False,
            auto_session_terms=True, session_cache_dir=cache)
        ok = job.run()
        body = io.open(os.path.join(out, "configs", "text",
                                    "character_name", "names.xml"),
                       encoding="utf-8").read()
        check("T45-3 会话词表翻译人名", ok and "译[Иванов]" in body)
        check("T45-4 技术精确表译单位", "小时" in body)
        check("T45-5 会话词表已落缓存",
              job.session_stats.get("candidates", 0) >= 1
              and any(x.endswith(".json") for x in os.listdir(cache)))
        job2 = ait.TranslationJob(
            ait._load_vendor(), _FakeServer(), ind, "auto", "auto", "chs",
            out_dir=os.path.join(root, "out2"), glossary={},
            force_glossary=False, hard_glossary_keys=set(),
            log=lambda *a, **k: None, reference_mode=False,
            copy_reference=False, protect_proper=False,
            translate_literals=False, auto_session_terms=True,
            session_cache_dir=cache)
        ok2 = job2.run()
        body2 = io.open(os.path.join(root, "out2", "configs", "text",
                                     "character_name", "names.xml"),
                        encoding="utf-8").read()
        check("T45-6 第二次运行命中会话缓存、不重复翻译名词",
              ok2 and "译[Иванов]" in body2 and "小时" in body2
              and job2.session_stats.get("cached", 0) >= 1
              and job2.session_requests == 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T46 ANTHOLOGY 2.1 A/B
# ----------------------------------------------------------------------
def t46_anthology_terms():
    print()
    print("--- T46 ANTHOLOGY 2.1 软词/纠错 ---")
    gloss, _set, _deleted, _clear = ait.load_glossary_layers()
    check("T46-1 新人物软词", gloss.get("Стычинский") == "斯季钦斯基")
    check("T46-2 新物品软词", gloss.get("кевларовые пластины") == "凯夫拉板")
    check("T46-3 软词不进硬词",
          "Стычинский" not in ait.load_hard_glossary_keys()
          and "кевларовые пластины" not in ait.load_hard_glossary_keys())
    check("T46-4 B 档纠错：Мутант→变异体，Мутанты→变种人，Седых→灰白",
          gloss.get("Мутант") == "变异体"
          and gloss.get("Мутанты") == "变种人"
          and gloss.get("Седых") == "灰白")
    check("T46-5 未确认冲突保持 NS 标准；Шрам=疤脸",
          gloss.get("Доктор") == "医生"
          and gloss.get("Леший") == "林妖"
          and gloss.get("Прапор") == "准尉"
          and gloss.get("Шрам") == "疤脸")


# ----------------------------------------------------------------------
# T47 术语库 v2：canon/names/technical + 变体/大小写
# ----------------------------------------------------------------------
def t47_termbase_v2():
    print()
    print("--- T47 术语库 v2 分层/变体 ---")
    data = ait.load_termbase_data()
    meta = data.get("_meta") or {}
    layers = ait.load_term_layers()
    check("T47-1 术语库 v2 元数据",
          meta.get("format") == "stalker-toolkit-termbase/2"
          and meta.get("layers", {}).get("canon")
          and meta.get("layers", {}).get("technical"))
    check("T47-2 四层规模：canon 1375 / names 3067 / technical 18",
          len(layers["canon"]) == 1375
          and len(layers["names"]) == 3067
          and len(layers["technical"]) == 18,
          "%d/%d/%d" % (len(layers["canon"]), len(layers["names"]),
                        len(layers["technical"])))
    check("T47-3 技术词数据化并进入术语库",
          layers["technical"].get("ч.") == "小时"
          and layers["technical"].get("RU") == "俄语"
          and ait.load_technical_exact().get("м/с") == "米/秒")
    check("T47-4 names 不进入硬词",
          "Кот" not in ait.load_hard_glossary_keys()
          and "Псевдогигант" in ait.load_hard_glossary_keys())
    gloss, _s, _d, _c = ait.load_glossary_layers()
    idx = ait.GlossaryIndex(gloss,
                            case_sensitive=layers["case_sensitive"],
                            variants=layers["variants"])
    got = idx.find("Гришачев и Гришачёв")
    check("T47-5 ё/е 变体归一到规范词条",
          got and all(k == "Гришачёв" for _, _, k in got)
          and idx.hits("Гришачев")[0][1] == "格里沙乔夫",
          str(got))
    got2 = idx.find("Санёк")
    check("T47-6 人物变体归一（Санёк→Санек）",
          got2 and got2[0][2] == "Санек"
          and idx.hits("Санёк")[0][1] == "萨涅克", str(got2))
    check("T47-7 小写拉丁专名不误伤正文，大写专名仍命中",
          idx.find("crystal") == [] and idx.find("bar") == []
          and [k for _, _, k in idx.find("Crystal")] == ["crystal"]
          and [k for _, _, k in idx.find("Bar")] == ["bar"])
    check("T47-8 人物名首字母大小写敏感",
          [k for _, _, k in idx.find("Кот спит")] == ["Кот"]
          and idx.find("кот спит") == [])
    check("T47-9 审计脚本无 blocking/high",
          subprocess.run([sys.executable,
                          os.path.join(ROOT, "tools", "audit_termbase.py"),
                          "--strict"],
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT).returncode == 0)


# ----------------------------------------------------------------------
# T48 TM-lite：高重复门控 + 精确复用
# ----------------------------------------------------------------------
def t48_tm_lite():
    print()
    print("--- T48 TM-lite 门控/复用 ---")

    def build(n_unique, n_dup):
        rows = ['<string_table>']
        for i in range(n_unique):
            rows.append('<string id="u%d"><text>Уникальная строка номер %d.'
                        '</text></string>' % (i, i))
        for i in range(n_dup):
            rows.append('<string id="d%d"><text>Повторяющийся текст задачи.'
                        '</text></string>' % i)
        rows.append('</string_table>')
        return "\n".join(rows)

    def run_case(n_unique, n_dup):
        root = tempfile.mkdtemp(prefix="ait_tm_")
        ind = os.path.join(root, "in")
        out = os.path.join(root, "out")
        os.makedirs(os.path.join(ind, "configs", "text", "rus"))
        with io.open(os.path.join(ind, "configs", "text", "rus", "t.xml"),
                     "w", encoding="utf-8", newline="\n") as f:
            f.write(build(n_unique, n_dup))
        with io.open(os.path.join(ind, "configs", "localization.ltx"),
                     "w", encoding="utf-8", newline="\n") as f:
            f.write("[string_table]\nlanguage = rus\nfiles = t\n")
        srv = _FakeServer()
        try:
            job, ok, _logs = _run_ref_job(
                ait, ind, out, srv, protect_proper=False, tm_lite=True)
            body = io.open(os.path.join(out, "configs", "text", "rus",
                                        "t.xml"), encoding="utf-8").read()
            return job, ok, srv, body
        finally:
            shutil.rmtree(root, ignore_errors=True)

    total = 50
    unique = 30
    dup = total - unique
    job, ok, srv, body = run_case(unique, dup)
    check("T48-1 高重复表启用 TM-lite（38%%）",
          ok and (job._tm_local_enabled or job._tm_global_enabled)
          and job.tm_stats.get("scope") in ("table", "global"),
          str(job.tm_stats))
    check("T48-2 重复句只请求一次并复用译文",
          len(srv.calls) == unique + 1
          and job.tm_stats.get("hits") == dup - 1
          and body.count("译[Повторяющийся текст задачи.]") == dup,
          "calls=%d hits=%d body=%d"
          % (len(srv.calls), job.tm_stats.get("hits", 0),
             body.count("译[Повторяющийся текст задачи.]")))

    job2, ok2, srv2, _body2 = run_case(48, 2)
    check("T48-3 低重复表不启用、请求数不变（4%%）",
          ok2 and not job2._tm_local_enabled
          and len(srv2.calls) == 50,
          "calls=%d" % len(srv2.calls))

    # 占位符参与缓存键：同句不同变量不会互相复用。
    check("T48-4 缓存键包含占位符与换行签名",
          job._tm_key("Привет $name", "rus")
          != job._tm_key("Привет $other", "rus")
          and job._tm_key("A\\nB", "rus") != job._tm_key("A B", "rus"))
    check("T48-5 全局门控阈值可判定",
          job._tm_should_enable({"total": 50, "unique": 30})
          and not job._tm_should_enable({"total": 50, "unique": 45})
          and not job._tm_should_enable({"total": 10, "unique": 5}))


# ----------------------------------------------------------------------
# T49 控制符丢失重试
# ----------------------------------------------------------------------
def t49_placeholder_retry():
    print()
    print("--- T49 控制符丢失重试 ---")
    root = tempfile.mkdtemp(prefix="ait_ph_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    with io.open(os.path.join(ind, "configs", "text", "rus", "p.xml"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write('<string_table><string id="p1">'
                '<rus>Привет, $name! Урон %s.</rus></string></string_table>')
    calls = {"n": 0}

    def fn(text):
        calls["n"] += 1
        if calls["n"] == 1:
            return "你好，伤害。"
        return "你好，$$R0$$！伤害 $$R1$$。"

    srv = _FakeServer(fn=fn)
    try:
        job, ok, _ = _run_ref_job(ait, ind, out, srv,
                                  protect_proper=False, tm_lite=False)
        body = io.open(os.path.join(out, "configs", "text", "rus", "p.xml"),
                       encoding="utf-8").read()
        check("T49-1 控制符丢失后重试并按原文回填",
              ok and calls["n"] == 2 and "$name" in body and "%s" in body,
              "calls=%d" % calls["n"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T50 移除 HY-MT1.5 旧档
# ----------------------------------------------------------------------
def t50_remove_mt15_tier():
    print()
    print("--- T50 移除 HY-MT1.5 旧档 ---")
    check("T50-1 旧档不再出现在任何档位/下载/哈希表",
          "mt15" not in ait.TIER_PROFILES
          and "mt15" not in ait.TIER_ORDER
          and "mt15" not in ait.BUILTIN_TIERS
          and "mt15" not in ait.TIER_FILE
          and "model_mt15" not in ait.DOWNLOAD_TASKS
          and "HY-MT1.5-1.8B-Q8_0.gguf" not in ait._MODEL_HASHES
          and all("神速" not in x for x in ait.TIER_LABELS))
    check("T50-2 旧配置 mt15 自动迁移到 mt2",
          ait.TIER_ALIAS.get("mt15") == "mt2")
    cfg_path = ait._CONFIG
    backup = open(cfg_path, "rb").read() if os.path.exists(cfg_path) else None
    try:
        with io.open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"tier": "mt15", "model_dir": ""}')
        cfg = ait.load_config()
        check("T50-3 load_config 迁移旧档", cfg.get("tier") == "mt2",
              str(cfg.get("tier")))
    finally:
        if backup is not None:
            open(cfg_path, "wb").write(backup)
        elif os.path.exists(cfg_path):
            os.remove(cfg_path)
    real_nv, real_gpu = ait.detect_nvidia_gpu, ait.detect_all_gpus
    try:
        ait.detect_nvidia_gpu = lambda: None
        ait.detect_all_gpus = lambda: []
        hw = ait.detect_hardware()
        check("T50-4 无独显推荐 mt2，不再回落到旧档",
              hw.get("tier") == "mt2" and "神速" not in hw.get("short", ""),
              str(hw))
    finally:
        ait.detect_nvidia_gpu, ait.detect_all_gpus = real_nv, real_gpu


# ----------------------------------------------------------------------
# T51 技术串/生成表/文件级专名保护
# ----------------------------------------------------------------------
def t51_output_filters():
    print()
    print("--- T51 技术串/生成表/专名保护 ---")
    tech = ("9x18 PM", "5.56x45 M995 AP", ".44 Magnum",
            "12.7x99mm", "100 x 5.56x45mm")
    check("T51-1 口径/弹药整串识别",
          all(ait.looks_like_technical_text(x) for x in tech)
          and not ait.looks_like_technical_text("Hello, world."))
    check("T51-2 旧生成表短 ASCII 噪声",
          ait.looks_like_generated_noise(
              "configs/text/rus/___scriptE__put_this_to_text_source.xml",
              "gulag:")
          and ait.looks_like_generated_noise(
              "configs/text/rus/___cfgxml__put_this_to_text_source.xml",
              "Card reader:")
          and not ait.looks_like_generated_noise(
              "configs/text/rus/stable_dialogs.xml", "Привет"))
    check("T51-3 纯拉丁型号名不进入会话候选",
          ait.looks_like_model_name("Browning High-Power")
          and ait.looks_like_model_name("IMI Uzi")
          and ait.looks_like_model_name("Colt M4A1")
          and ait.session_term_shape(
              "Browning High-Power", "物品·武器·装备·神器") == ""
          and ait.session_term_shape(
              "Ястреб", "地点与区域") == "Ястреб")
    check("T51-4 文件级专名保护",
          ait.looks_like_proper_text(
              "configs/text/rus/stable_game_credits.xml", "Jim Krenz")
          and ait.looks_like_proper_text(
              "configs/text/rus/string_table_enc_weapons.xml",
              "Browning High-Power")
          and not ait.looks_like_proper_text(
              "configs/text/rus/stable_dialogs.xml", "Jim Krenz"))
    check("T51-5 人物表短名仍可翻（不整表跳过）",
          not ait.looks_like_proper_text(
              "configs/text/character_name/table.xml", "Крот"))



# ----------------------------------------------------------------------
# T52 会话词条只做提示、不做精确替换
# ----------------------------------------------------------------------
def t52_session_hint_only():
    print()
    print("--- T52 会话词条只做提示 ---")
    root = tempfile.mkdtemp(prefix="ait_sess_hint_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    with io.open(os.path.join(ind, "configs", "text", "rus",
                              "string_table_enc_weapons.xml"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write('<string_table><string id="w"><rus>'
                'Browning High-Power</rus></string></string_table>')
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\n"
                "files = string_table_enc_weapons\n")
    srv = _FakeServer()
    try:
        job = ait.TranslationJob(
            ait._load_vendor(), srv, ind, "auto", "auto", "chs",
            out_dir=out, glossary={"Browning High-Power": "坏译"},
            force_glossary=False, hard_glossary_keys=set(),
            log=lambda *a, **k: None, reference_mode=True,
            copy_reference=True, protect_proper=True,
            translate_literals=False, auto_session_terms=False,
            tm_lite=False)
        job.session_only_keys = {"Browning High-Power"}
        ok = job.run()
        body = io.open(os.path.join(out, "configs", "text", "rus",
                                    "string_table_enc_weapons.xml"),
                       encoding="utf-8").read()
        check("T52-1 会话词条绕过保护专名时不再硬替换",
              ok and "Browning High-Power" in body
              and "坏译" not in body and not srv.calls)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T53 脚本显示调用补提取
# ----------------------------------------------------------------------
def t53_script_display_calls():
    print()
    print("--- T53 脚本显示调用补提取 ---")
    root = tempfile.mkdtemp(prefix="ait_extract_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text"))
    os.makedirs(os.path.join(ind, "scripts"))
    with io.open(os.path.join(ind, "configs", "text", "empty.xml"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write("<string_table></string_table>")
    with io.open(os.path.join(ind, "configs", "localization.ltx"),
                 "w", encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = empty\n")
    script = (
        "function test(obj, task, menu)\n"
        "  obj:set_level_changer_invitation("
        "\"Выброс - не время для путешествий!\")\n"
        "  task:set_description(\"Нужны два артефакта: \")\n"
        "  menu:AddItem(game.translate_string("
        "\"открыть панель управления\"), 1)\n"
        "end\n")
    with io.open(os.path.join(ind, "scripts", "test.script"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(script)
    srv = _FakeServer()
    try:
        job, ok, _logs = _run_ref_job(
            ait, ind, out, srv, protect_proper=False,
            translate_literals=True)
        body = io.open(os.path.join(out, "scripts", "test.script"),
                       encoding="utf-8").read()
        table = os.path.join(out, "configs", "text", "sgtat_auto_texts.xml")
        tbl = io.open(table, encoding="utf-8").read() if os.path.isfile(table) else ""
        check("T53-1 三个显示调用都被抽出并包 ID",
              ok and body.count("game.translate_string(\"sgtat_gen_text_") == 3
              and "game.translate_string(game.translate_string" not in body
              and "Выброс" not in body and "Нужны два" not in body
              and "открыть панель" not in body)
        check("T53-2 生成表包含 3 条中文",
              tbl.count("<string id=\"sgtat_gen_text_") == 3
              and "译[Выброс" in tbl and "译[Нужны" in tbl
              and "译[открыть" in tbl,
              tbl[:200])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T54 核心术语整条替换也要服从文件级专名保护
# ----------------------------------------------------------------------
def t54_credits_exact_protection():
    print()
    print("--- T54 制作名单不被核心词条硬替换 ---")
    root = tempfile.mkdtemp(prefix="ait_credit_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(os.path.join(ind, "configs", "text", "rus"))
    with io.open(os.path.join(ind, "configs", "text", "rus",
                              "stable_game_credits.xml"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write('<string_table><string id="c"><rus>Harris</rus>'
                '</string></string_table>')
    with io.open(os.path.join(ind, "configs", "localization.ltx"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\n"
                "files = stable_game_credits\n")
    srv = _FakeServer()
    try:
        job = ait.TranslationJob(
            ait._load_vendor(), srv, ind, "auto", "auto", "chs",
            out_dir=out, glossary={"Harris": "哈里斯"},
            force_glossary=True, hard_glossary_keys=set(),
            log=lambda *a, **k: None, reference_mode=True,
            copy_reference=True, protect_proper=True,
            translate_literals=False, auto_session_terms=False,
            tm_lite=False)
        ok = job.run()
        body = io.open(os.path.join(out, "configs", "text", "rus",
                                    "stable_game_credits.xml"),
                       encoding="utf-8").read()
        check("T54-1 credits 命中核心词条也不改写",
              ok and "<rus>Harris</rus>" in body and "哈里斯" not in body
              and not srv.calls)
        # 武器短名若有核心中文词条，仍应使用词条（不整表跳过）
        wdir = os.path.join(root, "weapon")
        wout = os.path.join(root, "wout")
        os.makedirs(os.path.join(wdir, "configs", "text", "rus"))
        with io.open(os.path.join(wdir, "configs", "text", "rus",
                                  "string_table_enc_weapons.xml"), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write('<string_table><string id="w"><rus>Browning High-Power'
                    '</rus></string></string_table>')
        job2 = ait.TranslationJob(
            ait._load_vendor(), srv, wdir, "auto", "auto", "chs",
            out_dir=wout, glossary={"Browning High-Power": "勃朗宁大威力手枪"},
            force_glossary=True, hard_glossary_keys=set(),
            log=lambda *a, **k: None, reference_mode=True,
            copy_reference=True, protect_proper=True,
            translate_literals=False, auto_session_terms=False,
            tm_lite=False)
        ok2 = job2.run()
        wbody = io.open(os.path.join(wout, "configs", "text", "rus",
                                     "string_table_enc_weapons.xml"),
                        encoding="utf-8").read()
        check("T54-2 武器核心词条仍可使用中文",
              ok2 and "勃朗宁大威力手枪" in wbody)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------------------------
# T55 英文物品/武器专项审计结果
# ----------------------------------------------------------------------
def t55_english_item_audit():
    print()
    print("--- T55 英文物品/武器审计 ---")
    d = ait.load_termbase_data()
    item = d.get("物品·武器·装备·神器", {})
    check("T55-1 Vintorez 错误硬词已清除",
          "Vintorez" not in item and "Винторез" not in item)
    check("T55-2 英文坏键/错配已清除",
          "Card" not in item and "Army uniform" not in item
          and "Rubick's Cube" not in item
          and "Anomlaous stabilizer" not in item)
    check("T55-3 高置信英文补录/修正",
          item.get("Browning High-Power") == "勃朗宁大威力手枪"
          and item.get("Metal Key") == "金属钥匙"
          and item.get("Circuit board") == "电路板"
          and item.get("Army body armor") == "军用防弹背心"
          and item.get("Anomalous stabilizer") == "异常稳定器")
    check("T55-4 俄文/英文对应实体修正",
          item.get("Mama's Beads") == "妈妈的项链"
          and item.get("Мамины бусы") == "妈妈的项链"
          and item.get("Monolith Shard") == "黑石碎片"
          and item.get("Осколок Монолита") == "黑石碎片"
          and item.get("Borov's trench coat") == "博罗夫的军大衣")


# ----------------------------------------------------------------------
def main():
    for fn in (t1_encoding, t2_glossary, t3_flatten, t4_artifact, t5_pipeline,
               t6_truncation, t7_config, t8_prompt, t9_ctx, t10_classify,
               t11_download, t12_ui_call, t13_vendor, t14_prompt_extra,
               t15_hint_block, t16_prompt_cache, t17_ledger,
               t18_glossary_layers, t19_atomic_split, t20_verify_switch,
               t21_self_check, t22_sources, t23_gpu_detect,
               t24_cuda13_gate, t25_cuda13_task, t26_cuda13_fallback,
               t27_log_batch, t28_pump_budget, t29_log_volume,
               t30_server_log_level, t31_progress_channel,
               t32_cfgxml_regex_dos, t33_process_lifecycle,
               t34_engine_mem_and_slow_log, t35_reference_layer,
               t36_runtime_tokens, t37_protected_text,
               t38_noop_bom_preview, t39_reference_wiring,
               t40_source_tags_literals, t41_cleanup_and_cp1251,
               t42_script_context_and_english,
               t43_multiline_profanity_retry, t44_problem_file_filters,
               t45_term_layers_session_cache, t46_anthology_terms,
               t47_termbase_v2, t48_tm_lite, t49_placeholder_retry, t50_remove_mt15_tier, t51_output_filters, t52_session_hint_only, t53_script_display_calls, t54_credits_exact_protection, t55_english_item_audit):
        try:
            fn()
        except Exception as e:
            import traceback
            check("%s 执行异常" % fn.__name__, False,
                  "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print("总计 %d 项：通过 %d，失败 %d" % (total, passed, total - passed))
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAIL: %s  %s" % (name, detail))
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
