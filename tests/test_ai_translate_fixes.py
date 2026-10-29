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
import sys
import tempfile
import threading
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
                  max_tokens=None):
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
                      timeout_sec=180, max_tokens=None):
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
          "Русский" in head and "下面的ru" not in head, head)
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

    check("T14-4 附加提示词为空时，system 消息与基准提示词逐字相同",
          sys_empty == base, repr(sys_empty[-40:]))
    want = base + "\n补充要求（优先遵守）：\n· 语气：冷峻简短的军事简报口吻。"
    check("T14-5 附加提示词以「补充要求」整段追加在系统提示词末尾",
          sys_full == want, repr(sys_full[-50:]))
    check("T14-6 use_extra=False（启动自检通道）不受用户附加提示词影响",
          sys_selfcheck == base, repr(sys_selfcheck[-30:]))

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
def main():
    for fn in (t1_encoding, t2_glossary, t3_flatten, t4_artifact, t5_pipeline,
               t6_truncation, t7_config, t8_prompt, t9_ctx, t10_classify,
               t11_download, t12_ui_call, t13_vendor, t14_prompt_extra,
               t15_hint_block, t16_prompt_cache, t17_ledger,
               t18_glossary_layers, t19_atomic_split, t20_verify_switch,
               t21_self_check):
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
