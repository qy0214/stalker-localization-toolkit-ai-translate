# -*- coding: utf-8 -*-
"""恶意注入 E2E：模拟 Hy-MT2 1.8B 把「前一句」上下文当正文翻出。

用真实 TranslationJob + 真实 gamedata 的 bar 对话表，FakeServer 在收到
context 时故意返回「前一句：」+ 上一条源文的污染译文，没 context 时返回
正常伪译文。验证：
  1) 修复后的插件能拦截污染并去上下文重试，输出表无「前一句/后一句」标签；
  2) bar_arena_man_job_172（短条目）译文正确，不再被上一条长译文覆盖。

用法: python sim_context_bleed_2808.py [plugin.py 路径]
"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import threading

PLUGIN = (sys.argv[1] if len(sys.argv) > 1 else
          r"C:\STALKER-AiTranslate-2x\plugins\ai_translate\plugin.py")
GAME = r"C:\Users\Admin\Desktop\gamedata"
BAR_SRC = os.path.join(GAME, r"configs\text\dialogs\stable_dialogs_bar.xml")

_CYR = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\-']*_?")
_SYL = "科托夫世界异变辐射警报兵器北方联邦契约区科学极暗恐惧者灵魂之音"


def fake_word(m):
    w = m.group(0)
    h = 0
    for ch in w.lower():
        h = (h * 31 + ord(ch)) & 0xFFFFFF
    n = min(max(len(w) // 2, 1), 3)
    return "".join(_SYL[(h >> (k * 5)) % len(_SYL)] for k in range(n))


class EvilServer(object):
    """带上下文 -> 吐「前一句：<翻后的上下文>」污染；无上下文 -> 正常伪译。"""
    ctx_size = 8192
    parallel = 1
    model = "evil-sim"
    variant = "sim"

    def __init__(self):
        self.with_ctx = 0
        self.no_ctx = 0

    def is_running(self):
        return True

    @staticmethod
    def _ok(line):
        return _CYR.sub(fake_word, line) if _CYR.search(line) else line

    def translate(self, text, src_lang, tgt_lang, **kw):
        ctx = kw.get("context")
        if ctx:
            self.with_ctx += 1
            # 模拟 1.8B 的毛病：把上下文当正文，连标签一起输出，截断到 ~240
            poisoned = "前一句：" + self._ok(str(ctx))[:240]
            return "\n".join(poisoned for _ in text.split("\n"))
        self.no_ctx += 1
        return "\n".join(self._ok(raw) for raw in text.split("\n"))

    def translate_batch(self, lines, src_lang, tgt_lang, **kw):
        return "\n".join("[%d] %s" % (i + 1, self._ok(s))
                         for i, s in enumerate(lines))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_one(mod, label):
    out = tempfile.mkdtemp(prefix="bleed_out_" + label + "_")
    # 只放 bar 一张表，缩小范围
    ind = tempfile.mkdtemp(prefix="bleed_in_" + label + "_")
    sub = os.path.join(ind, "configs", "text", "dialogs")
    os.makedirs(sub)
    shutil.copy2(BAR_SRC, os.path.join(sub, "stable_dialogs_bar.xml"))
    with open(os.path.join(ind, "configs", "localization.ltx"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("[string_table]\nlanguage = rus\nfiles = stable_dialogs_bar\n")

    srv = EvilServer()
    logs = []
    job = mod.TranslationJob(
        mod._load_vendor(), srv, ind, "auto", "rus", "chs",
        glossary={}, force_glossary=False, output_anyway=False,
        ctx_size=8192, out_dir=out,
        log=lambda m, tag="info": logs.append(str(m)),
        detail=lambda *a, **k: None, progress=lambda *a, **k: None,
        cancel_event=threading.Event(),
        reference_mode=False, copy_reference=True, only_referenced=False,
        protect_proper=False, tm_lite=False, parallel=1, pending_files=0,
        freq_names=False, auto_session_terms=False, preview_reference=False)
    ok = job.run()
    bar_out = os.path.join(out, "configs", "text", "dialogs",
                           "stable_dialogs_bar.xml")
    raw = open(bar_out, "rb").read().decode("utf-8", "replace") \
        if os.path.isfile(bar_out) else ""
    bleed_lines = [l for l in raw.splitlines()
                   if "前一句：" in l or "后一句：" in l]
    print("[%s] run=%s 带上下文调用=%d 无上下文调用=%d 污染残留行=%d"
          % (label, ok, srv.with_ctx, srv.no_ctx, len(bleed_lines)))
    # 看 172 条（输出为 text 单字段）
    m = re.search(r'id="bar_arena_man_job_172"[^>]*>\s*<text>(.*?)</text>',
                  raw, re.S)
    print("[%s] 172 条译文: %s" % (label, m.group(1)[:120] if m else "未找到"))
    return len(bleed_lines), (m.group(1) if m else "")


def main():
    print("=== 恶意注入 E2E：验证上下文串入修复 ===")
    m = load(PLUGIN, "bleed_plugin")
    bleed, zh172 = run_one(m, "fixed")
    ok = True
    if bleed != 0:
        print("FAIL: 输出仍有 %d 行上下文污染" % bleed)
        ok = False
    if "前一句" in zh172 or "后一句" in zh172:
        print("FAIL: 172 条译文仍被污染")
        ok = False
    if not zh172:
        print("WARN: 172 条未产出译文（可能走跳过逻辑）")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
