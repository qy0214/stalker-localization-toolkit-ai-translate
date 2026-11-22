# -*- coding: utf-8 -*-
"""用现有插件代码（Desktop\\249\\plugins\\ai_translate\\plugin.py，v2.0.5）
模拟测试翻译 C:\\Users\\Admin\\Desktop\\gamedata。

* 引擎用 FakeServer 顶替 LlamaServer：不下载模型、不拉 llama-server，
  逐行做确定性伪翻译（术语表命中优先替换，其余西里尔词→伪中文），
  保持行数/空行/非西里尔字符原样，让插件侧的掩码还原、逐行重建、
  术语强制、引用层、断点账本全部走真实代码路径。
* 输出写到工作区 sim_out（只读 gamedata，不写回桌面）。

用法：python sim_translate_gamedata.py [阶段]
  scan   只勘察（analyze_input_dir，只读）
  run    勘察 + 全量 auto 模拟翻译
"""
import importlib.util
import os
import re
import sys
import time
import threading
import traceback

PLUGIN_PATH = (os.environ.get("AIT_PLUGIN_PATH") or
               r"C:\Users\Admin\Desktop\249\plugins\ai_translate\plugin.py")
GAME_DIR = r"C:\Users\Admin\Desktop\gamedata"
OUT_DIR = os.environ.get("AIT_OUT_DIR") or r"C:\STALKER-HH-2x\sim_out\gamedata_auto"
RUNLOG = r"C:\STALKER-HH-2x\sim_out\sim_run.log"

os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)


def load_plugin():
    spec = importlib.util.spec_from_file_location("ai_translate_plugin",
                                                  PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- Fake 引擎
_CYR = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\-']*_?")
_SYL = "科托夫世界异变辐射警报兵器北方联邦契约区科学极暗恐惧者灵魂之音"
_CN = re.compile(r"[\u4e00-\u9fff]")


def _fake_line(line):
    """确定性伪翻译：术语命中替换；其余西里尔词→伪中文；行结构不动。"""
    if not line or not _CYR.search(line):
        return line
    return _CYR.sub(_fake_word, line)


def _fake_word(m):
    w = m.group(0)
    h = 0
    for i, ch in enumerate(w.lower()):
        h = (h * 31 + ord(ch)) & 0xFFFFFF
    n = min(max(len(w) // 2, 1), 3)
    return "".join(_SYL[(h >> (k * 5)) % len(_SYL)] for k in range(n))


class FakeServer(object):
    """顶替 LlamaServer：is_running / translate / translate_batch。"""

    ctx_size = 8192
    parallel = 1
    model = "fake-sim"
    variant = "sim"

    def __init__(self):
        self.calls_single = 0
        self.calls_batch = 0
        self.lines_in = 0

    def is_running(self):
        return True

    def translate(self, text, src_lang, tgt_lang, glossary_hits=None,
                  timeout_sec=180, max_tokens=None, use_extra=True,
                  extra_sys=None, temperature=None, context=None, style=None):
        self.calls_single += 1
        # 术语优先（模拟模型遵守术语块）
        hits = glossary_hits or []
        out = []
        for raw in text.split("\n"):
            line = raw
            for src, dst in hits:
                if src and src in line:
                    line = line.replace(src, dst)
            out.append(_fake_line(line))
        self.lines_in += len(out)
        return "\n".join(out)

    def translate_batch(self, lines, src_lang, tgt_lang, glossary_hits=None,
                        timeout_sec=180, max_tokens=None, temperature=None):
        self.calls_batch += 1
        hits = glossary_hits or []
        outs = []
        for raw in lines:
            line = raw
            for src, dst in hits:
                if src and src in line:
                    line = line.replace(src, dst)
            outs.append(_fake_line(line))
        self.lines_in += len(outs)
        return "\n".join("[%d] %s" % (i + 1, s) for i, s in enumerate(outs))


# ---------------------------------------------------------------- 输出回调
_log_fh = open(RUNLOG, "a", encoding="utf-8")
_log_lock = threading.Lock()
_t0 = time.time()


def log(m, tag="info"):
    with _log_lock:
        s = "[%6.1fs][%s] %s" % (time.time() - _t0, tag, m)
        print(s, flush=True)
        _log_fh.write(s + "\n")
        _log_fh.flush()


def detail(m, tag="info"):
    with _log_lock:
        _log_fh.write("[detail][%s] %s\n" % (tag, m))


def progress(d):
    with _log_lock:
        _log_fh.write("[prog] %s\n" % d)


# ---------------------------------------------------------------- 阶段
def do_scan(mod):
    log("勘察目录：%s（mode=auto，只读）" % GAME_DIR)
    st = mod.analyze_input_dir(GAME_DIR, mode="auto", log=log)
    log("勘察结果：%s" % {
        "文件数": st["files"], "总字节": st["bytes"],
        "候选字符串": st["n_strings"], "最长串": st["max_str"],
        "最长串文件": st["max_str_file"], "推荐档": st["recommend"],
        "超长分档": st["overlong"], "截断": st["truncated"]})


def do_run(mod):
    parser = mod._load_vendor()
    log("解析器就绪：%s" % sys.modules["xRayXmlParser"].__name__)

    srv = FakeServer()
    cancel = threading.Event()
    job = mod.TranslationJob(
        parser, srv, GAME_DIR, "auto", "ru", "chs",
        glossary={}, force_glossary=True, output_anyway=False,
        ctx_size=8192, out_dir=OUT_DIR,
        log=log, detail=detail, progress=progress,
        cancel_event=cancel,
        reference_mode=False, copy_reference=True, only_referenced=False,
        protect_proper=True, preview_reference=False,
        hard_glossary_keys=None, translate_literals=False,
        auto_session_terms=False, tm_lite=True, parallel=1,
        pending_files=0, freq_names=False)
    ok = job.run()
    log("run() 返回=%s 单条调用=%d 批量调用=%d 输入行=%d "
        "batch_stats=%s req=%d 长句分拆=%d"
        % (ok, srv.calls_single, srv.calls_batch, srv.lines_in,
           job.batch_stats, getattr(job, "req_count", "?"),
           getattr(job, "long_chunks", "?")))
    return ok


def do_verify(mod):
    import glob
    parser = mod._load_vendor()
    n_out = 0
    bad = []
    for path in glob.glob(os.path.join(OUT_DIR, "**", "*.xml"),
                          recursive=True):
        n_out += 1
        rel = os.path.relpath(path, OUT_DIR)
        src = os.path.join(GAME_DIR, rel)
        try:
            chain = mod.encoding_chain(open(path, "rb").read(1 << 20))
            ents_out = parser.parse_xray_text_xml(path, chain)
            chain_src = mod.encoding_chain(open(src, "rb").read(1 << 20))
            ents_in = parser.parse_xray_text_xml(src, chain_src)
        except Exception as e:
            bad.append((rel, "解析失败: %r" % e))
            continue
        ids_out = [getattr(x, "id", None) or (x.get("id") if isinstance(
            x, dict) else None) for x in ents_out]
        ids_in = [getattr(x, "id", None) or (x.get("id") if isinstance(
            x, dict) else None) for x in ents_in]
        if ids_out != ids_in:
            bad.append((rel, "条目 ID 不一致 %d→%d"
                        % (len(ids_in), len(ids_out))))
    log("输出文本XML %d 个，抽查异常 %d 个 %s"
        % (n_out, len(bad), bad[:5] if bad else ""))
    return n_out, bad


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "run"
    mod = load_plugin()
    log("插件版本：%s（%s）" % (mod.PLUGIN_INFO["version"], PLUGIN_PATH))
    if phase == "scan":
        do_scan(mod)
        return
    if phase in ("run", "all"):
        do_scan(mod)
        ok = do_run(mod)
        n_out, bad = do_verify(mod)
        log("总结：%s，输出文件抽查 %d 个，异常 %d"
            % ("成功" if ok else "失败", n_out, len(bad)))
        return
    if phase == "verify":
        do_verify(mod)
        return
    log("未知阶段 %s" % phase)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
