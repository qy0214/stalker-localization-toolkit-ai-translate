# -*- coding: utf-8 -*-
"""1.9.25 小模型交付闸门与生命周期回归。"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEST_ROOT = tempfile.mkdtemp(prefix="ait1925_")
PLUGIN_DIR = os.path.join(TEST_ROOT, "plugins")
os.makedirs(PLUGIN_DIR)
shutil.copy2(os.path.join(ROOT, "plugins", "ai_translate.py"),
             os.path.join(PLUGIN_DIR, "ai_translate.py"))
shutil.copytree(os.path.join(ROOT, "plugins", "ai_translate_assets"),
                os.path.join(PLUGIN_DIR, "ai_translate_assets"))
os.environ["LOCALAPPDATA"] = os.path.join(TEST_ROOT, "localappdata")

spec = importlib.util.spec_from_file_location(
    "ait1925", os.path.join(PLUGIN_DIR, "ai_translate.py"))
ait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ait)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))


class FakeServer(object):
    def __init__(self, fn=None, batch_fn=None, on_call=None):
        self.calls = []
        self.lang_calls = []
        self.fn = fn or (lambda text: "译：" + text)
        self.batch_fn = batch_fn
        self.on_call = on_call

    def is_running(self):
        return True

    def translate(self, text, src, tgt, **kwargs):
        self.calls.append(text)
        self.lang_calls.append((text, src))
        result = self.fn(text)
        if self.on_call:
            self.on_call(text)
        return result

    def translate_batch(self, lines, src, tgt, **kwargs):
        self.calls.extend(lines)
        if self.batch_fn:
            return self.batch_fn(lines)
        return "\n".join("[%d] %s" % (i, self.fn(line))
                         for i, line in enumerate(lines, 1))


def make_job(root, server=None, mode="auto", **kwargs):
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    os.makedirs(ind, exist_ok=True)
    logs = []
    params = dict(
        parser=ait._load_vendor(), server=server or FakeServer(),
        in_dir=ind, mode=mode, src_lang="auto", tgt_lang="chs",
        out_dir=out, log=lambda m, tag="info": logs.append((tag, m)),
        detail=lambda m, tag="info": None,
        reference_mode=False, copy_reference=False, only_referenced=False,
        protect_proper=False, translate_literals=False,
        auto_session_terms=False, tm_lite=False, force_glossary=False,
        hard_glossary_keys=set())
    params.update(kwargs)
    return ait.TranslationJob(**params), logs


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def table(path, rows):
    body = "".join('<string id="%s"><rus>%s</rus></string>'
                   % (eid, text) for eid, text in rows)
    write(path, "<string_table>%s</string_table>" % body)


def read_map(path):
    if not os.path.isfile(path):
        return {}
    pkg = ait._load_vendor()
    ents = pkg.parse_xray_text_xml(path, ["utf-8"]) or []
    return {e.id: pkg.getRecommendLangText(e, "chs")[1] for e in ents}


def ledger(job):
    return job._load_ledger() if os.path.isfile(job._ledger_path) else {}


def t1_primitives():
    check("T1-1 token 序列必须完全一致",
          ait.runtime_tokens_match("$name %s", "$name %s")
          and not ait.runtime_tokens_match("$name %s", "%s $name"))
    check("T1-2 付款方向反转可识别",
          ait.semantic_role_issues("I will pay you.", "你必须付给我。", "eng"))
    check("T1-3 正常付款方向不误报",
          not ait.semantic_role_issues("I will pay you.", "我会付给你。", "eng"))
    check("T1-4 源文自身含提示词锚点不误报",
          not ait.looks_like_prompt_echo("只输出译文本身", "只输出译文本身"))
    check("T1-5 模型回显提示词可识别",
          ait.looks_like_prompt_echo("你是《潜行者》的译员", "Open the door."))
    check("T1-6 上下文串入可识别",
          ait.looks_like_context_bleed("Open the door.", "前一句：你好\n打开门。"))
    check("T1-7 千分位不误报",
          not ait.missing_big_numbers("Pay 30000 rubles.", "支付30,000卢布。"))
    check("T1-8 金额腰斩可识别",
          ait.missing_big_numbers("Pay 30000 rubles.", "支付15,000卢布。"))
    check("T1-9 源文千分位也参与校验",
          ait.missing_big_numbers("Pay 30,000 rubles.", "支付15卢布。"))
    check("T1-10 重复数字按次数校验",
          ait.missing_big_numbers("Pay 30000 and 30000.", "支付30000卢布。"))


def t2_zero_change():
    root = tempfile.mkdtemp(prefix="ait1925_zero_")
    job, _ = make_job(root)
    src = os.path.join(root, "in", "configs", "text", "symbols.xml")
    out = os.path.join(root, "out", "configs", "text", "symbols.xml")
    table(src, [("s", "$name %s %c[default]")])
    ok = job.run()
    check("T2-1 纯符号表不生成输出", ok and not os.path.exists(out))
    check("T2-2 纯符号表不写账本", not ledger(job))

    root2 = tempfile.mkdtemp(prefix="ait1925_echo_")
    srv = FakeServer(fn=lambda text: text)
    job2, logs = make_job(root2, srv)
    src2 = os.path.join(root2, "in", "configs", "text", "echo.xml")
    out2 = os.path.join(root2, "out", "configs", "text", "echo.xml")
    table(src2, [("e", "Open the door.")])
    ok2 = job2.run()
    check("T2-3 模型原样回显不生成输出",
          not ok2 and not os.path.exists(out2) and job2.failed_strings >= 1)
    check("T2-4 回显失败不显示全部完成",
          not any(msg.startswith("全部完成") for _tag, msg in logs))


def t3_ledger_and_exact_terms():
    root = tempfile.mkdtemp(prefix="ait1925_ledger_")
    srv = FakeServer()
    job, _ = make_job(root, srv)
    rel = "configs/text/a.xml"
    src = os.path.join(root, "in", rel)
    out = os.path.join(root, "out", rel)
    table(src, [("a", "Open the door.")])
    ok = job.run()
    check("T3-1 首次翻译建立哈希账本",
          ok and ledger(job).get(rel, {}).get("pipeline") == "1.9.25"
          and len(ledger(job).get(rel, {}).get("out_hash", "")) == 64)
    calls = len(srv.calls)
    job2, _ = make_job(root, srv)
    ok2 = job2.run()
    check("T3-2 当前 pipeline 与哈希一致可跳过",
          ok2 and len(srv.calls) == calls)
    old = ledger(job2)
    old[rel].pop("pipeline", None)
    job2._ledger = old
    job2._ledger_present = True
    check("T3-3 无 pipeline 的旧账本不能跳过",
          not job2._should_skip(rel, src, out, "text"))
    data = open(out, "rb").read()
    st = os.stat(out)
    open(out, "wb").write(data.replace(b"\xe6\x89\x93\xe5\xbc\x80", b"XXXX"))
    os.utime(out, ns=(st.st_atime_ns, st.st_mtime_ns))
    check("T3-4 同尺寸同时间输出篡改仍重译",
          not job2._should_skip(rel, src, out, "text"))

    for mode in ("auto", "text"):
        root3 = tempfile.mkdtemp(prefix="ait1925_exact_")
        srv3 = FakeServer()
        job3, _ = make_job(root3, srv3, mode=mode)
        src3 = os.path.join(root3, "in", "configs", "text", "unit.xml")
        out3 = os.path.join(root3, "out", "configs", "text", "unit.xml")
        table(src3, [("u", "ч.")])
        ok3 = job3.run()
        check("T3-5 %s 短技术词零请求直取" % mode,
              ok3 and read_map(out3).get("u") == "小时" and not srv3.calls,
              "calls=%d" % len(srv3.calls))


def t4_multilingual_and_batch():
    root = tempfile.mkdtemp(prefix="ait1925_lang_")
    srv = FakeServer(fn=lambda text: "你好。")
    job, _ = make_job(root, srv, src_lang="fra")
    src = os.path.join(root, "in", "configs", "text", "fra.xml")
    out = os.path.join(root, "out", "configs", "text", "fra.xml")
    write(src, '<string_table><string id="a"><fra>Bonjour.</fra>'
               '<eng>Hello.</eng></string></string_table>')
    ok = job.run()
    raw = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
    check("T4-1 显式 fra 只译 fra 且保留 eng",
          ok and "<fra>你好。</fra>" in raw and "<eng>Hello.</eng>" in raw
          and "<text>" not in raw)
    check("T4-2 批量解析拒绝附加说明",
          job._parse_numbered_batch("[1] 甲\n说明", 1) is None)
    check("T4-3 批量解析拒绝重复编号",
          job._parse_numbered_batch("[1] 甲\n[1] 乙", 1) is None)
    check("T4-4 批量解析拒绝越界编号",
          job._parse_numbered_batch("[2] 甲", 1) is None)
    batch = [{"id": "a", "src": "Use $name.", "lang": "eng",
              "rel": "configs/text/dialogs/a.xml"},
             {"id": "b", "src": "Open the door.", "lang": "eng",
              "rel": "configs/text/dialogs/a.xml"}]
    bad = FakeServer(batch_fn=lambda lines: "[1] 使用 %s。\n[2] 打开门。")
    job._srv = bad
    check("T4-5 批量 token 损坏整批回退",
          job._translate_dialogue_batch(batch) == {})


def t5_final_audit():
    root = tempfile.mkdtemp(prefix="ait1925_audit_")
    job, _ = make_job(root)
    rel = "configs/text/a.xml"
    src = os.path.join(root, "in", rel)
    out = os.path.join(root, "out", rel)
    table(src, [("a", "Open the door.")])
    ok = job.run()
    before = open(out, "rb").read()
    table(out, [("a", "Open the door.")])
    job._audit_tables = {rel}
    result = job._final_audit(os.path.join(root, "out"))
    check("T5-1 终检发现零变化表并撤销账本",
          not result["ok"] and rel not in job._ledger
          and os.path.isfile(out))
    check("T5-2 终检不删除问题输出", os.path.isfile(out))

    root2 = tempfile.mkdtemp(prefix="ait1925_dangling_")
    job2, _ = make_job(root2)
    script = os.path.join(root2, "out", "scripts", "a.script")
    write(script, 'game.translate_string("sgtat_gen_text_999_0")\n')
    result2 = job2._final_audit(os.path.join(root2, "out"))
    check("T5-3 悬空生成 ID 是硬失败",
          not result2["ok"] and result2["details"]["dangling_generated_ids"])


def t6_generated_transactions():
    root = tempfile.mkdtemp(prefix="ait1925_gen_")
    srv = FakeServer(fn=lambda text: "打开门。")
    job, _ = make_job(root, srv)
    src = os.path.join(root, "in", "configs", "misc", "a.ltx")
    out = os.path.join(root, "out", "configs", "misc", "a.ltx")
    gen = os.path.join(root, "out", "___auto_put_this_to_text_folder.xml")
    write(src, '[dialogs]\ndescr = "Open the door."\n')
    ok = job.run()
    body = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
    ids = [eid for eid in read_map(gen) if eid.startswith("sgtat_gen_text_")]
    check("T6-1 正文与生成定义表同时存在",
          ok and ids and ids[0] in body and os.path.isfile(gen))
    check("T6-2 生成表记录 generated 哈希账本",
          ledger(job).get("___auto_put_this_to_text_folder.xml", {}).get(
              "generated") is True)
    calls = len(srv.calls)
    job2, _ = make_job(root, srv)
    ok2 = job2.run()
    check("T6-3 有效生成表与正文可无损跳过",
          ok2 and len(srv.calls) == calls)
    write(gen, "broken")
    job3, _ = make_job(root, srv)
    ok3 = job3.run()
    body3 = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
    new_ids = [eid for eid in read_map(gen) if eid.startswith("sgtat_gen_text_")]
    check("T6-4 损坏生成表触发正文重译与新定义",
          ok3 and new_ids and new_ids[0] in body3 and new_ids[0] != ids[0])

    root2 = tempfile.mkdtemp(prefix="ait1925_cancel_")
    cancel = threading.Event()
    srv2 = FakeServer(fn=lambda text: "打开门。", on_call=lambda text: cancel.set())
    job4, _ = make_job(root2, srv2, cancel_event=cancel)
    src2 = os.path.join(root2, "in", "configs", "misc", "a.ltx")
    out2 = os.path.join(root2, "out", "configs", "misc", "a.ltx")
    gen2 = os.path.join(root2, "out", "___auto_put_this_to_text_folder.xml")
    write(src2, '[dialogs]\ndescr = "Open the door."\n')
    ok4 = job4.run()
    body4 = open(out2, encoding="utf-8").read() if os.path.isfile(out2) else ""
    check("T6-5 取消后已写正文仍有生成定义",
          not ok4 and os.path.isfile(gen2)
          and any(eid in body4 for eid in read_map(gen2)))

    root3 = tempfile.mkdtemp(prefix="ait1925_writefail_")
    job5, _ = make_job(root3, FakeServer(fn=lambda text: "打开门。"))
    src3 = os.path.join(root3, "in", "configs", "misc", "a.ltx")
    out3 = os.path.join(root3, "out", "configs", "misc", "a.ltx")
    gen3 = os.path.join(root3, "out", "___auto_put_this_to_text_folder.xml")
    write(src3, '[dialogs]\ndescr = "Open the door."\n')
    orig_write = job5._write_atomic

    def fail_body(path, writer):
        if path.endswith(".ltx"):
            raise OSError("受控写盘失败")
        return orig_write(path, writer)

    job5._write_atomic = fail_body
    ok5 = job5.run()
    check("T6-6 正文写盘失败不留孤儿 ID",
          not ok5 and not os.path.exists(out3) and not os.path.exists(gen3))

    root4 = tempfile.mkdtemp(prefix="ait1925_hardkill_")
    srv4 = FakeServer(fn=lambda text: "打开门。")
    job6, _ = make_job(root4, srv4)
    src4 = os.path.join(root4, "in", "configs", "misc", "a.ltx")
    out4 = os.path.join(root4, "out", "configs", "misc", "a.ltx")
    gen4 = os.path.join(root4, "out", "___auto_put_this_to_text_folder.xml")
    write(src4, '[dialogs]\ndescr = "Open the door."\n')
    ok6 = job6.run()
    os.remove(gen4)
    saved = ledger(job6)
    saved.pop("___auto_put_this_to_text_folder.xml", None)
    write(job6._ledger_path, json.dumps(saved, ensure_ascii=False))
    calls_before = len(srv4.calls)
    job7, _ = make_job(root4, srv4)
    ok7 = job7.run()
    check("T6-7 无生成表账本的悬空正文首次重跑即修复",
          ok6 and ok7 and len(srv4.calls) > calls_before
          and os.path.isfile(gen4))


def t7_text_write_failure():
    root = tempfile.mkdtemp(prefix="ait1925_textfail_")
    srv = FakeServer()
    job, _ = make_job(root, srv)
    rel = "configs/text/a.xml"
    src = os.path.join(root, "in", rel)
    out = os.path.join(root, "out", rel)
    table(src, [("a", "Open the door.")])
    ok = job.run()
    before = open(out, "rb").read()
    table(src, [("a", "Close the window.")])
    job2, _ = make_job(root, FakeServer(fn=lambda text: "关闭窗户。"))
    job2._write_table_changes = lambda *args: False
    ok2 = job2.run()
    check("T7-1 文本写盘失败保留旧成品但撤销账本",
          ok and not ok2 and open(out, "rb").read() == before
          and rel not in ledger(job2))


def t8_reference_copy_failure():
    root = tempfile.mkdtemp(prefix="ait1925_ref_")
    ind = os.path.join(root, "in")
    out = os.path.join(root, "out")
    table(os.path.join(ind, "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    write(os.path.join(ind, "configs", "localization.ltx"),
          "[string_table]\nlanguage = rus\nfiles = a\n")
    write(os.path.join(ind, "configs", "gameplay", "ref.xml"),
          "<game><text>a</text></game>")
    job, _ = make_job(root, FakeServer(), reference_mode=True,
                      copy_reference=True)
    orig_copy = job._copy_file

    def fail_ref(src, dst):
        if "gameplay" in src:
            raise OSError("受控复制失败")
        return orig_copy(src, dst)

    job._copy_file = fail_ref
    ok = job.run()
    check("T8-1 引用层复制失败不能判完成",
          not ok and job.failed_strings >= 1)


def t9_lock_and_ledger_failure():
    root = tempfile.mkdtemp(prefix="ait1925_lock_")
    entered = threading.Event()
    release = threading.Event()

    def blocking(text):
        entered.set()
        release.wait(5)
        return "打开门。"

    srv1 = FakeServer(fn=blocking)
    job1, _ = make_job(root, srv1)
    table(os.path.join(root, "in", "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    result = []

    def worker():
        result.append(job1.run())

    th = threading.Thread(target=worker)
    th.start()
    check("T9-1 首任务已进入模型边界", entered.wait(5))
    job2, _ = make_job(root, FakeServer())
    ok2 = job2.run()
    release.set()
    th.join(5)
    check("T9-2 同一输出目录并发任务被拒绝",
          not ok2 and result == [True])

    root2 = tempfile.mkdtemp(prefix="ait1925_savefail_")
    job3, _ = make_job(root2, FakeServer())
    table(os.path.join(root2, "in", "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    orig_write = job3._write_atomic

    def fail_ledger(path, writer):
        if path.endswith(".ai_translate_done.json"):
            raise OSError("受控账本失败")
        return orig_write(path, writer)

    job3._write_atomic = fail_ledger
    ok3 = job3.run()
    check("T9-3 账本保存失败不能判全部完成",
          not ok3 and job3._ledger_save_failed)

    root3 = tempfile.mkdtemp(prefix="ait1925_proclock_")
    table(os.path.join(root3, "in", "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    marker = os.path.join(root3, "entered")
    release = os.path.join(root3, "release")
    result_path = os.path.join(root3, "result")
    child_code = r'''
import importlib.util, os, sys, time
plugin, root, marker, release, result = sys.argv[1:]
spec = importlib.util.spec_from_file_location("ait_child", plugin)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class Server:
    def translate(self, text, *args, **kwargs):
        open(marker, "w").write("1")
        while not os.path.exists(release):
            time.sleep(0.05)
        return "打开门。"
job = mod.TranslationJob(
    mod._load_vendor(), Server(), os.path.join(root, "in"),
    "auto", "auto", "chs", out_dir=os.path.join(root, "out"),
    log=lambda *a, **k: None, reference_mode=False,
    copy_reference=False, protect_proper=False,
    auto_session_terms=False, tm_lite=False, force_glossary=False,
    hard_glossary_keys=set())
open(result, "w").write(str(job.run()))
'''
    child = subprocess.Popen(
        [sys.executable, "-c", child_code,
         os.path.join(PLUGIN_DIR, "ai_translate.py"), root3,
         marker, release, result_path])
    deadline = time.time() + 5
    while not os.path.isfile(marker) and time.time() < deadline:
        time.sleep(0.05)
    job4, _ = make_job(root3, FakeServer())
    ok4 = job4.run()
    open(release, "w").write("1")
    child.wait(timeout=10)
    check("T9-4 跨进程同输出目录互斥",
          os.path.isfile(marker) and not ok4 and child.returncode == 0
          and open(result_path).read() == "True")


def t10_atomic_writes():
    root = tempfile.mkdtemp(prefix="ait1925_atomic_")
    job, _ = make_job(root)
    target = os.path.join(root, "out", "same.bin")
    errors = []

    def worker(i):
        try:
            job._write_atomic(
                target, lambda tmp: open(tmp, "wb").write((str(i) * 1000).encode()))
        except Exception as e:
            errors.append(type(e).__name__)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5)
    data = open(target, "rb").read() if os.path.isfile(target) else b""
    leftovers = [name for name in os.listdir(os.path.dirname(target))
                 if name.startswith(".ait_")]
    check("T10-1 并发原子写无半文件或临时文件冲突",
          not errors and len(data) == 1000 and len(set(data)) == 1
          and not leftovers, "errors=%r" % errors)


def t11_adversarial_edges():
    root = tempfile.mkdtemp(prefix="ait1925_noopgen_")
    srv = FakeServer(fn=lambda text: text)
    job, _ = make_job(root, srv)
    src = os.path.join(root, "in", "configs", "misc", "a.ltx")
    out = os.path.join(root, "out", "configs", "misc", "a.ltx")
    gen = os.path.join(root, "out", "___auto_put_this_to_text_folder.xml")
    write(src, '[dialogs]\ndescr = "Open the door."\n')
    ok = job.run()
    check("T11-1 非文本原样回显不生成 ID 或定义表",
          not ok and not os.path.exists(out) and not os.path.exists(gen))

    root2 = tempfile.mkdtemp(prefix="ait1925_textcancel_")
    cancel = threading.Event()
    srv2 = FakeServer(on_call=lambda text: cancel.set())
    job2, _ = make_job(root2, srv2, cancel_event=cancel)
    src2 = os.path.join(root2, "in", "configs", "text", "a.xml")
    out2 = os.path.join(root2, "out", "configs", "text", "a.xml")
    table(src2, [("a", "Open the door."), ("b", "Close the window.")])
    ok2 = job2.run()
    check("T11-2 文本翻译取消不写半成品且不记完成",
          not ok2 and not os.path.exists(out2) and not ledger(job2))

    root3 = tempfile.mkdtemp(prefix="ait1925_auditgate_")
    job3, logs = make_job(root3, FakeServer())
    table(os.path.join(root3, "in", "configs", "text", "a.xml"),
          [("a", "Open the door.")])
    job3._final_audit = lambda done: {"ok": False, "hard_problems": 1}
    ok3 = job3.run()
    check("T11-3 终检硬失败阻止全部完成日志",
          not ok3 and not any(msg.startswith("全部完成") for _t, msg in logs))

    root4 = tempfile.mkdtemp(prefix="ait1925_badout_")
    job4, _ = make_job(root4, FakeServer(), out_dir=os.path.join(root4, "in"))
    check("T11-4 输出等于输入时拒绝启动", not job4.run())

    root5 = tempfile.mkdtemp(prefix="ait1925_tokens_")
    job5, _ = make_job(root5, FakeServer())
    rel = "configs/text/a.xml"
    src5 = os.path.join(root5, "in", rel)
    out5 = os.path.join(root5, "out", rel)
    table(src5, [("a", "Use $name.")])
    ok5 = job5.run()
    table(out5, [("a", "使用 %c[默认]。")])
    job5._audit_tables = {rel}
    result5 = job5._final_audit(os.path.join(root5, "out"))
    check("T11-5 终检识别色码损坏",
          ok5 and not result5["ok"]
          and result5["details"]["runtime_token_damage"])

    root6 = tempfile.mkdtemp(prefix="ait1925_terms_")
    job6, _ = make_job(root6, FakeServer(fn=lambda text: "区域。"),
                       glossary={"Zone": "禁区"},
                       hard_glossary_keys={"Zone"})
    rel6 = "configs/text/a.xml"
    src6 = os.path.join(root6, "in", rel6)
    out6 = os.path.join(root6, "out", rel6)
    table(src6, [("a", "Zone")])
    ok6 = job6.run()
    table(out6, [("a", "区域。")])
    job6._audit_tables = {rel6}
    result6 = job6._final_audit(os.path.join(root6, "out"))
    check("T11-6 终检识别硬术语译名缺失",
          ok6 and not result6["ok"]
          and result6["details"]["glossary_conflicts"])

    job7, _ = make_job(tempfile.mkdtemp(prefix="ait1925_ctx_"),
                       FakeServer(fn=lambda text: "前一句：你好\n打开门。"))
    value = job7._translate_one("Open the door.", "eng", context="前一句：你好")
    check("T11-7 上下文串入结果整条回退并计失败",
          value == "Open the door." and job7.failed_strings >= 1)
    batch = [{"id": "a", "src": "I will pay you.", "lang": "eng",
              "rel": "configs/text/dialogs/a.xml"},
             {"id": "b", "src": "Open the door.", "lang": "eng",
              "rel": "configs/text/dialogs/a.xml"}]
    job7._srv = FakeServer(batch_fn=lambda lines:
                           "[1] 你必须付给我。\n[2] 打开门。")
    check("T11-8 批量语义方向反转整批回退",
          job7._translate_dialogue_batch(batch) == {})
    job8, _ = make_job(tempfile.mkdtemp(prefix="ait1925_num_"),
                       FakeServer(fn=lambda text: "支付15,000卢布。"))
    value8 = job8._translate_one("Pay 30000 rubles.", "eng")
    check("T11-9 单条大额数字被篡改时补回原值",
          "30000" in value8 and "15,000" in value8)
    job9, _ = make_job(tempfile.mkdtemp(prefix="ait1925_ph_"),
                       FakeServer(fn=lambda text: "打开门。"))
    value9 = job9._translate_one("Use $name.", "eng")
    check("T11-10 占位符恢复失败计失败而非静默完成",
          value9 == "Use $name." and job9.failed_strings >= 1)

    root10 = tempfile.mkdtemp(prefix="ait1925_delete_")
    job10, _ = make_job(root10, FakeServer())
    src10 = os.path.join(root10, "in", "configs", "text", "a.xml")
    out10 = os.path.join(root10, "out", "configs", "text", "a.xml")
    table(src10, [("a", "$name %s")])
    write(out10, "<string_table><string id=\"a\"><text>$name %s</text>"
                 "</string></string_table>")
    orig_remove = os.remove

    def fail_remove(path):
        if os.path.normcase(str(path)) == os.path.normcase(out10):
            raise OSError("受控删除失败")
        return orig_remove(path)

    os.remove = fail_remove
    try:
        ok10 = job10.run()
    finally:
        os.remove = orig_remove
    check("T11-11 零变化输出删除失败不能判完成",
          not ok10 and os.path.exists(out10) and job10.failed_strings >= 1)

    root11 = tempfile.mkdtemp(prefix="ait1925_fallback_")
    srv11 = FakeServer(fn=lambda text: "你好。")
    job11, _ = make_job(root11, srv11, src_lang="fra")
    src11 = os.path.join(root11, "in", "configs", "text", "a.xml")
    write(src11, '<string_table><string id="a"><eng>Hello.</eng></string>'
                 '</string_table>')
    ok11 = job11.run()
    check("T11-12 显式语言缺失时按实际回退语言请求",
          ok11 and any(src == "eng" for _text, src in srv11.lang_calls),
          str(srv11.lang_calls))


def main():
    t1_primitives()
    t2_zero_change()
    t3_ledger_and_exact_terms()
    t4_multilingual_and_batch()
    t5_final_audit()
    t6_generated_transactions()
    t7_text_write_failure()
    t8_reference_copy_failure()
    t9_lock_and_ledger_failure()
    t10_atomic_writes()
    t11_adversarial_edges()
    passed = sum(1 for _name, ok in RESULTS if ok)
    print("\n%d/%d passed" % (passed, len(RESULTS)))
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
