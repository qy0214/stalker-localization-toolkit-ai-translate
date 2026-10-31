"""AI 离线翻译插件（Hy-MT2 / llama.cpp）

为 STALKER Localization Toolkit 增加一个「AI 翻译」栏目：
  * 两档本地离线模型：Hy-MT2-7B Q4_K_M（约 4.4 GB）/ Hy-MT2-1.8B Q8_0（约 1.8 GB）
  * 模型与 llama.cpp 运行库均不内置：插件内下载管理，自动探测网络环境、
    并发实测各镜像下载速度、自动选择最优源；支持断点续传 / 取消 / 失败换源
  * 本地拉起 llama-server（OpenAI 兼容 HTTP），子进程与主程序隔离
  * 翻译 X-Ray 文本 XML / cfgxml / ltx / script，支持术语表、译文复用、
    临时断点、冗余 ID 报告

纯标准库实现（urllib / threading / subprocess / zipfile），可在工具集的
PyInstaller 冻结环境（无第三方包）中运行；界面只用 api.ui，Tk / Qt 双后端。

第三方来源：X-Ray 解析器（ai_translate_assets/xray_pkg.zip）与
翻译流程参考 wzyddg/stalker_auto-trans-tool_remake（上游未声明
许可证）；详见仓库 THIRD_PARTY_NOTICES.md 与随包
THIRD_PARTY_NOTICES.txt。
"""

import os
import sys
import re
import json
import time
import io
import csv
import codecs
import hashlib
import socket
import queue
import threading
import subprocess
import zipfile
import tempfile
import shutil
import atexit
import urllib.request
import urllib.error
import urllib.parse

PLUGIN_INFO = {
    "id": "ai_translate",
    "name": "AI 离线翻译（Hy-MT2 / 自选模型）",
    "version": "1.9.10",
    "author": "local",
    "description": "本地离线模型翻译 X-Ray 文本；解压即用、零环境依赖；"
                   "混元两档内置模型自动测速下载；支持自选任意 GGUF 模型；"
                   "CPU / CUDA 12.4 / CUDA 13.4 / Vulkan 四后端并自动检测显卡；"
                   "内置无限制强制术语库",
}

APP_NAME = "ai_translate"


_HERE = os.path.dirname(os.path.abspath(__file__))
_ASSETS = os.path.join(_HERE, "ai_translate_assets")
_VENDOR_ZIP = os.path.join(_ASSETS, "xray_pkg.zip")
_SHIPPED_BIN_DIR = os.path.join(_ASSETS, "bin")
_SHIPPED_MODELS_DIR = os.path.join(_ASSETS, "models")


def _writable(path):
    """目录是否可写（Program Files 等只读安装位置会失败）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "wb") as f:
            f.write(b"1")
        os.remove(probe)
        return True
    except Exception:
        return False


if _writable(_ASSETS):
    _DATA_ROOT = _ASSETS
else:
    _DATA_ROOT = os.path.join(
        os.environ.get("LOCALAPPDATA")
        or os.path.expanduser("~"),
        "STALKER Localization Toolkit", "ai_translate")

_MODELS_DIR = os.path.join(_DATA_ROOT, "models")
_BIN_DIR = os.path.join(_DATA_ROOT, "bin")
_LOGS_DIR = os.path.join(_DATA_ROOT, "logs")
_CONFIG = os.path.join(_DATA_ROOT, "config.json")
_DOWNLOAD_DIR = os.path.join(_DATA_ROOT, "downloads")

# 术语分两层，避免"升级覆盖 plugins/ 把用户自定义术语一起冲掉"：
#   builtin（随包，只读）      ai_translate_assets/glossary_builtin.json
#   用户覆盖层（可变）          <_DATA_ROOT>/glossary_user.json
# 1.6.0 之前两者是同一个文件（assets/glossary_user.json），升级即丢用户词条；
# 这里保留对旧文件名/旧扁平格式的读取兼容，见 _read_user_layer()。
_GLOSS_BUILTIN = os.path.join(_ASSETS, "glossary_builtin.json")
_GLOSS_BUILTIN_LEGACY = os.path.join(_ASSETS, "glossary_user.json")
_GLOSS_USER = os.path.join(_DATA_ROOT, "glossary_user.json")
_GLOSS_LAYER_FORMAT = "ai-translate-glossary-user/1"

for _d in (_DATA_ROOT, _MODELS_DIR, _BIN_DIR, _LOGS_DIR, _DOWNLOAD_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass


def _bin_dirs():
    """查找运行库时同时看随包目录与用户数据目录。"""
    return [_SHIPPED_BIN_DIR, _BIN_DIR]


# ══════════════════════════════════════════════════════════════════
# Windows 进程生命周期：Job Object + 遗留引擎清理
#
# 背景（2026-10-30 实测）：工具集被"任务管理器结束任务"后，llama-server
# 不会收到任何通知（atexit 不执行），于是留在后台继续占着 ~3 GB 显存和
# 数 GB 内存。连续几次强杀后 6 GB 显卡被 4 个残留引擎塞满，驱动把模型
# 溢到内存，下一次翻译连窗口一起卡死 —— 用户看到的就是"又卡死了"。
#
# 这里做两件事：
#   1. 启动时扫一遍 llama-server.exe：只认本插件自己的 exe 路径，且父
#      进程已经不在的（真孤儿），杀掉并回收显存；
#   2. 新拉起的引擎放进 kill-on-close 的 Job Object：宿主进程无论怎么
#      死（含 TerminateProcess），内核都会连带终止它，不再产生新孤儿。
# ══════════════════════════════════════════════════════════════════

_WIN = False          # False=尚未初始化；None=初始化失败；dict=已就绪
_KILL_JOB = None      # kill-on-close Job 句柄，保持到进程结束
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WAIT_TIMEOUT = 0x00000102
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002


def _win():
    """Windows 进程/作业对象 API 的轻量封装；非 Windows 返回 None。"""
    global _WIN
    if os.name != "nt":
        return None
    if _WIN not in (False, None):
        return _WIN
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,
                                                 wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                    wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,
                                                 wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL

        _WIN = {
            "ctypes": ctypes,
            "wintypes": wintypes,
            "k32": k32,
            "EXTENDED_LIMIT": EXTENDED_LIMIT,
            "PROCESSENTRY32W": PROCESSENTRY32W,
            "INVALID_HANDLE": ctypes.c_void_p(-1).value,
        }
    except Exception:
        _WIN = None
    return _WIN


def _norm_path(p):
    try:
        return os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return ""


def _server_exe_paths(cfg=None):
    """本插件可能使用的 llama-server.exe 全路径集合。

    只认这些**精确路径**，避免误杀用户自己跑的 llama.cpp 或别的工具集。
    布局：<随包或用户数据>/bin/<变体>/llama-server.exe；外加手工指定的
    exe_paths（用户可选任意本地运行库）。
    """
    paths = set()
    for base in _bin_dirs():
        for variant in ("cpu", "cuda", "cuda13", "vulkan"):
            paths.add(_norm_path(os.path.join(base, variant,
                                              "llama-server.exe")))
        paths.add(_norm_path(os.path.join(base, "llama-server.exe")))
    for p in ((cfg or {}).get("exe_paths") or {}).values():
        if p:
            paths.add(_norm_path(p))
    paths.discard("")
    return paths


def _win_process_image(pid):
    """进程完整 exe 路径；取不到返回空串。"""
    w = _win()
    if not w:
        return ""
    k32, ctypes, wintypes = w["k32"], w["ctypes"], w["wintypes"]
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        pass
    finally:
        k32.CloseHandle(h)
    return ""


def _win_pid_alive(pid):
    """父进程是否还活着（只用于判孤儿；PID 复用会偏保守地算"活着"）。"""
    w = _win()
    if not w:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == _WAIT_TIMEOUT
    finally:
        k32.CloseHandle(h)


def _win_terminate_pid(pid):
    w = _win()
    if not w:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_PROCESS_TERMINATE, False, int(pid))
    if not h:
        return False
    try:
        return bool(k32.TerminateProcess(h, 1))
    finally:
        k32.CloseHandle(h)


def _win_llama_servers():
    """[(pid, ppid, exe_path)]，仅 llama-server.exe。"""
    w = _win()
    if not w:
        return []
    k32, ctypes, wintypes = w["k32"], w["ctypes"], w["wintypes"]
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == w["INVALID_HANDLE"]:
        return []
    out = []
    try:
        entry = w["PROCESSENTRY32W"]()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = (entry.szExeFile or "").lower()
            if name == "llama-server.exe":
                pid = int(entry.th32ProcessID)
                out.append((pid, int(entry.th32ParentProcessID),
                            _win_process_image(pid)))
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _kill_orphan_servers(cfg=None):
    """杀掉**本插件路径下**且父进程已退出的 llama-server，返回 PID 列表。"""
    if os.name != "nt":
        return []
    allowed = _server_exe_paths(cfg)
    killed = []
    for pid, ppid, exe in _win_llama_servers():
        if not exe or _norm_path(exe) not in allowed:
            continue
        if _win_pid_alive(ppid):
            continue                      # 还归某个活着的宿主，别动
        if _win_terminate_pid(pid):
            killed.append(pid)
    return killed


def _create_kill_job():
    """创建 kill-on-close 作业对象；失败返回 None（不影响翻译）。"""
    w = _win()
    if not w:
        return None
    k32, ctypes = w["k32"], w["ctypes"]
    job = k32.CreateJobObjectW(None, None)
    if not job or job == w["INVALID_HANDLE"]:
        return None
    info = w["EXTENDED_LIMIT"]()
    info.BasicLimitInformation.LimitFlags = \
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return job


def _assign_pid_to_job(job, pid):
    w = _win()
    if not w or not job:
        return False
    k32 = w["k32"]
    h = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                        False, int(pid))
    if not h:
        return False
    try:
        return bool(k32.AssignProcessToJobObject(job, h))
    finally:
        k32.CloseHandle(h)


def _close_handle(h):
    w = _win()
    if w and h:
        w["k32"].CloseHandle(h)


def _kill_job():
    """进程级共享的 Job（保持句柄到退出；宿主一死内核连带收掉引擎）。"""
    global _KILL_JOB
    if os.name != "nt":
        return None
    if _KILL_JOB is None:
        _KILL_JOB = _create_kill_job()
    return _KILL_JOB


def _assign_server_to_kill_job(proc):
    if proc is None:
        return False
    return _assign_pid_to_job(_kill_job(), proc.pid)


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) STALKER-Localization-Toolkit/ai-translate"

LANG_FULL = {"chs": "简体中文", "eng": "英语", "rus": "俄语", "ukr": "乌克兰语"}
LANG_CODE = {"chs": "zh", "eng": "en", "rus": "ru", "ukr": "uk"}

# 通用技术精确表：只做整条精确匹配，绝不子串替换。
# 这些短标签模型通常保留原文；不放进核心硬词，避免影响正文。
_TECHNICAL_EXACT = {
    "ч.": "小时", "ч": "小时", "час": "小时", "часа": "小时",
    "сек": "秒", "сек.": "秒", "м/с": "米/秒", "в/мин": "发/分钟",
    "шт": "个", "шт.": "个", "р.": "卢布", "руб.": "卢布",
    "дБм.": "分贝毫瓦", "дБм": "分贝毫瓦", "м.": "米", "м": "米",
    "RU": "俄语", "FX": "特效",
}

# 会话词表：当前 MOD 的人名/物品/地点候选，自动翻译并缓存。
# 它不是随包术语库，不需要为每个 MOD 手工建档；缓存只在本机。
_SESSION_TERM_NOTE = (
    "这是游戏名词表。逐行译成简体中文，保持行数与顺序；"
    "人名音译，绰号/物品/地点按游戏习惯意译；"
    "不要编号、不要解释、不要保留俄文或英文原文。")
_SESSION_QUOTES = "\"'«»“”‘’"
_SESSION_NAME_DIRS = ("character_name", "snames", "fnames", "bio")
_SESSION_ITEM_DIRS = ("item", "weapon", "equipment", "artefact",
                      "artifact", "arsenal")
_SESSION_LOC_DIRS = ("mapspot", "level", "place", "zone")
_SESSION_SKIP_DIRS = ("dialog", "news", "task", "tip", "stash", "secret",
                      "credits", "ui", "gameplay", "spawn", "npc")
_SESSION_LIMITS = {"人物与军衔": 1500, "物品·武器·装备·神器": 500,
                   "地点与区域": 400, "阵营与组织": 150, "变异生物": 100}

# TM-lite：只在“同一批文本里重复句比例高”时启用**精确同源**复用。
# 不做模糊匹配、不建跨 MOD 持久库、不把上一次任务的译文混进来。
# 实测 NS7 182 张表：全局重复率 7.4%，5 张表 ≥20%（1,482 条）；
# 阈值 20% 正好把“名字/任务/新闻模板”这类高重复表挑出来，普通对话表
# 不会因为零星重复而改变翻译路径。
TM_LITE_MIN_RATIO = 0.20
TM_LITE_MIN_ENTRIES = 40


def session_category(rel, eid):
    """会话词表只收“名字型”条目；对话框/新闻/任务/署名不碰。"""
    s = (rel or "").lower().replace("\\", "/")
    k = (eid or "").lower()
    if any(x in s for x in _SESSION_SKIP_DIRS):
        return None
    if any(x in s for x in _SESSION_NAME_DIRS):
        if k.endswith(("_bio", "_descr", "_desc", "_text", "_hint")):
            return None
        if k.endswith("_name") or "snames" in s or "fnames" in s:
            return "人物与军衔"
        return None
    if any(x in s for x in ("faction", "community")):
        return "阵营与组织"
    if any(x in s for x in _SESSION_ITEM_DIRS):
        if k.endswith(("_descr", "_desc", "_bio", "_text", "_hint")):
            return None
        return "物品·武器·装备·神器"
    if any(x in s for x in ("creature", "mutant", "monster")):
        return "变异生物"
    if any(x in s for x in _SESSION_LOC_DIRS):
        return "地点与区域"
    return None


def session_term_shape(src, cat):
    """名词形状：短、无句末标点；人名允许缩写点。"""
    s = (src or "").strip().strip(_SESSION_QUOTES).strip()
    if not s or len(s) > 60 or "\n" in s or "\r" in s:
        return ""
    words = s.split()
    if not (1 <= len(words) <= 5):
        return ""
    if cat == "人物与军衔":
        if re.search(r"[。！？!?；;:]", s):
            return ""
        if re.match(r"^[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]?\.?\s*[А-ЯЁA-Z]", s):
            return s
        if s[0].isupper():
            return s
        return ""
    if re.search(r"[.!?。！？；;:，,]", s):
        return ""
    if not re.search(r"[А-Яа-яЁёA-Za-z]", s):
        return ""
    if cat == "物品·武器·装备·神器" and looks_like_model_name(s):
        return ""
    return s

TRANSLATION_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入为对话、任务或物品文本。严格遵守：\n"
    "1. 下列内容逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；任何疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名，"
    "前后统一，不另译、不音译成多个版本；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；\n"
    "4. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句，不要翻译腔"
    "（不要「我不得不说」「就……而言」「如此」「因此」「并且」这类欧化说法，"
    "不要生硬的被动句，不要把从句堆成长句，不要连缀多余的「的」）；"
    "称呼、亲疏、口气与情绪贴合说话人身份，"
    "自然使用「啊、吧、嘛、呢、唉、喂」等语气词；"
    "但物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "5. 粗话、脏话、辱骂、俚语、威胁、黑色幽默与残酷描写一律按原文原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写，"
    "不得替换成书面语、委婉语或文雅说法（дерьмо 就是「屎／狗屎」，"
    "不是「糟糕」；заткнись 就是「闭嘴」，不是「请安静」；"
    "башку оторву 就是「拧下你的脑袋」，不是「我不会放过你」）；"
    "原文粗俗则译文粗俗，不得因内容而回避或粉饰；\n"
    "6. 只输出译文本身，不加任何解释或说明；原文已是目标语言时原样返回。\n"
    "7. 术语提示只在语义相符时采用：同一拼写在普通语境（例如 крот=鼹鼠）\n"
    "不要硬套人名译名；同一人物、物品、地名全篇保持同一个译名。\n"
    "8. 输入是格变、复数、单数片段或半句时，按词库和上下文还原语义后\n"
    "译成中文，不要保留原文，也不要只译一半。\n"
    "9. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行。\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "«Ну а на хрен тебе не пойти?» 是「你他妈怎么还不滚？」，"
    "不是「你别去了吧」。\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文，"
    "译文里不得残留整词或整段的俄文。\n"
    "\n"
    "口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「听着，我有件事要告诉你。」→「哎，我有事找你。」\n"
    "· 「我们无法再继续留在这里了。」→「这儿咱待不下去了。」\n"
    "· 「我不知道你该如何做到这一点。」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「你明白正在发生什么吗？」→「你懂现在发生啥了吗？」"
)

# 英语源文专用提示词。规则与俄语版相同，但把术语/粗口/口语锚例换成
# 英语里最常见的误译点：小模型容易把英语委婉化、书面化，或把习语逐词直译。
TRANSLATION_SYS_PROMPT_EN = (
    "你是《潜行者 S.T.A.L.K.E.R.》后末日游戏的专业译员，"
    "输入可能是英语的对话、任务、物品或界面文本。严格遵守：\n"
    "1. 下列内容逐字保留、数量与顺序不变：占位符（%s、%d、%1$s）、"
    "控制符（$$ACTION$$、$变量）、脚本与条目ID（如 zat_b101_x、"
    "sgtat_gen_text_1_x、a.b.c）、\\n；任何疑似代码或标记一律照抄勿译；\n"
    "2. 人名、阵营、地名、怪物、武器物品等专名使用游戏约定译名；"
    "有官方中文名的用官方名，没有的按常见音译，首次出现后保持统一；"
    "缩写、型号、代号（AK-74、PDA、SEVA）保留原文，不要硬造中文；\n"
    "3. 忠实完整，不增译、不漏译、不脑补剧情；英语的冠词、助动词、"
    "重复主语不要逐词搬进中文；\n"
    "4. 译文必须是地道的中文口语，读起来要像中国玩家自己在说话："
    "用中文的语序、短句和中文习惯的省略与断句，不要翻译腔"
    "（不要「我不得不说」「就……而言」「如此」「因此」「并且」这类欧化说法，"
    "不要生硬的被动句，不要把从句堆成长句，不要连缀多余的「的」）；"
    "英语习语按中文口语意译（例如 It's a piece of cake → 小菜一碟），"
    "不要逐词硬译；称呼、亲疏、口气与情绪贴合说话人身份，"
    "自然使用「啊、吧、嘛、呢、唉、喂」等语气词；"
    "但物品名称、按钮、菜单项这类短标签保持简洁直白，不口语化、不加语气词；\n"
    "5. 粗话、脏话、辱骂、俚语、威胁、黑色幽默与残酷描写一律按原文原意直译，"
    "保留同等粗鲁力度与情绪强度：不得弱化、省略、删除或改写，"
    "不得替换成书面语、委婉语或文雅说法（fuck 就是「操／妈的」，"
    "shit 就是「屎／狗屎」，不是「糟糕」；shut up 就是「闭嘴」，"
    "不是「请安静」；I'll rip your head off 就是「我拧下你的脑袋」，"
    "不是「我不会放过你」）；原文粗俗则译文粗俗，不得因内容而回避或粉饰；\n"
    "6. 只输出译文本身，不加任何解释或说明；原文已是中文时原样返回；"
    "不要把英文原文抄进译文，也不要把规则复述出来。\n"
    "7. 术语提示只在语义相符时采用；同一人物、物品、地名全篇保持同一个译名；"
    "普通语境里的同形词不要硬套专名译名。\n"
    "8. 输入是复数、缩写、单数片段或半句时，按词库和上下文还原语义后译成中文，"
    "不要保留英文原文。\n"
    "9. 保留原文的换行数量与段落数；不要把两段合并成一段，也不要多加空行。\n"
    "10. 反问、讽刺、威胁按说话人的意图译，不要逐字翻转否定："
    "「Why don't you just leave?」是「你他妈怎么还不滚？」，"
    "不是「你为什么不离开」。\n"
    "11. 引号不是保留原文的理由：引号内的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径才保留原文，"
    "译文里不得残留整词或整段的英文或俄文原文。\n"
    "\n"
    "英语口语对照示例（左为生硬译法，右为地道中文；照右边的路子写）：\n"
    "· 「Listen, I've got something to tell you.」→「哎，我有事找你。」\n"
    "· 「We can't stay here anymore.」→「这儿咱待不下去了。」\n"
    "· 「I don't know how you're going to do it.」→「我不知道你咋弄，反正东西我得要。」\n"
    "· 「Do you understand what's going on?」→「你懂现在发生啥了吗？」\n"
    "· 「Get out of my sight.」→「滚，别让我看见你。」\n"
    "· 「You're a dead man.」→「你死定了。」"
)

# 对话场景批量翻译：只用于连续短对话。1.8B 实测能保留 [n] 行号。
# 短标签/UI/物品名：不要套用对话口语规则。
TRANSLATION_SYS_PROMPT_LABEL = (
    "你是《潜行者 S.T.A.L.K.E.R.》的游戏本地化译者。输入是物品、按钮、"
    "菜单或界面短标签。译成简洁、直白、统一的简体中文；不加语气词，"
    "不扩写，不解释。占位符、控制符、代码/条目ID逐字保留。"
    "专名使用游戏约定译名；只输出译文本身。"
)

TRANSLATION_BATCH_SYS_PROMPT = (
    "你是《潜行者 S.T.A.L.K.E.R.》的场景对话译者。输入是多行连续对话，"
    "每行以 [n] 开头。逐行翻译成自然、口语化的简体中文；必须保留每行"
    "开头的 [n] 编号、行数和顺序，不合并、不拆分、不解释、不加角色名。"
    "上下文里前后句只帮助理解，不要翻译没有编号的内容。"
    "术语提示只在语义相符时采用；引号内的阵营、地名、物品、人名也要译成中文。"
    "只输出带编号的译文行。"
)

# 模型偶尔会把整段系统提示词当译文回显。用固定锚点做硬校验，
# 不依赖提示词本身（换提示词时只需保留这些锚点）。
_PROMPT_ECHO_MARKERS = (
    "你是《潜行者",
    "S.T.A.L.K.E.R.》后末日游戏的专业译员",
    "只输出译文本身",
    "口语对照示例",
    "术语(用给定译名)",
)


def looks_like_prompt_echo(text):
    s = text or ""
    return any(marker in s for marker in _PROMPT_ECHO_MARKERS)


_RU_PROFANITY_RE = re.compile(
    r"дерьм|заткн|хрен|чёрт|черт|бля|хуй|пизд|ебат|еб[ао]|говн|суч|"
    r"сдох|жоп|муд[ао]|уёб|уеб|нахуй|иди ты|пошёл ты|пошел ты", re.I)
# 中文译文里出现连续两个以上西里尔字母，基本就是模型漏译/原样保留。
_CYR_WORD_RE = re.compile(r"[А-Яа-яЁё]{2,}")
_ZH_PROFANITY_RE = re.compile(
    r"他妈|妈的|操(?:你|他|她|它|死|蛋|逼|屄)|我操|"
    r"靠[！!。.，,]|滚(?:蛋|开|！|!|。|，)|闭嘴|混蛋|畜生|王八|去死|"
    r"该死|见鬼|鸡巴|屌|日你|找死|活腻|屎|狗屎|放屁")
_PROFANITY_RETRY_NOTE = (
    "本句原文含粗口、辱骂或威胁，必须保留同等粗鲁力度：不得弱化、"
    "删除或改成书面语。按原文直译，例如 на хрен→他妈/滚，"
    "дерьмо→屎/狗屎，заткнись→闭嘴。")
_PLACEHOLDER_RETRY_NOTE = (
    "必须逐字保留输入里的 $$R0$$、$$R1$$ 等控制占位符，"
    "数量、顺序和写法都不能改；只翻译占位符以外的正文。")
_RESIDUAL_CYRILLIC_NOTE = (
    "译文里仍夹着俄文（%s）。引号里的阵营、组织、地名、物品、人名"
    "同样要译成中文；只有代码、型号、条目ID、文件路径保留原文，"
    "不要保留俄文单词。")


def has_source_profanity(text):
    return bool(_RU_PROFANITY_RE.search(text or ""))


def has_zh_profanity(text):
    return bool(_ZH_PROFANITY_RE.search(text or ""))


def has_cjk(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

GLOSSARY_HINT_HEADER = "术语(用给定译名)：\n"
GLOSSARY_HINT_BUDGET = 240
GLOSSARY_HINT_STOPWORDS = frozenset((
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "нет", "есть", "был", "была", "были", "быть", "это", "этот",
    "для", "или", "если", "уже", "ещё", "еще", "тут", "там", "тогда", "чем",
    "кто", "где", "когда", "почти", "всего", "всегда", "очень", "надо",
    "можно", "нужно", "теперь", "потом", "себя", "себе", "свой", "свою",
    "один", "два", "три", "без", "под", "над", "про", "при", "между",
)) | frozenset((
    # 英语源文同样要过滤功能词，否则术语提示预算会被 the/a/of 吃掉。
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "is", "are", "was", "were", "be", "been", "being", "with", "by",
    "from", "this", "that", "these", "those", "it", "its", "as", "not",
    "no", "yes", "you", "your", "yours", "he", "she", "they", "we", "i",
    "me", "my", "our", "their", "his", "her", "them", "us", "do", "does",
    "did", "can", "could", "will", "would", "should", "may", "might",
    "must", "have", "has", "had", "so", "but", "if", "then", "than",
    "also", "very", "just", "there", "here", "what", "which", "who",
    "whom", "whose", "when", "where", "why", "how", "all", "any", "some",
))


def glossary_hint_block(pairs, budget=GLOSSARY_HINT_BUDGET):
    """把命中术语压成尽可能小的提示块，返回 (文本, 计费字符数)。

    计费字符数是**保守上界**（每行按含换行计、另预留与正文之间的空行），
    略大于实际块长；预算判断因此偏安全，不会算少。

    规则：跳过功能词、大小写去重、按预算截断；超预算的长术语直接跳过而不是
    截半（半截术语反而会误导模型）。实测把原来的 15 条上限换成 240 字符预算
    后，每条请求少注入约 25% 字符，且长专名不再被短词挤掉。
    """
    seen = set()
    lines = []
    used = len(GLOSSARY_HINT_HEADER) + 2
    for k, v in pairs:
        k = str(k).strip()
        lk = k.lower()
        if not k or lk in seen or lk in GLOSSARY_HINT_STOPWORDS:
            continue
        v = str(v)
        cost = len(k) + len(v) + 2
        if used + cost > budget:
            continue
        seen.add(lk)
        lines.append("%s=%s" % (k, v))
        used += cost
    if not lines:
        return "", 0
    return GLOSSARY_HINT_HEADER + "\n".join(lines), used

EXTRA_PROMPT_DEMO = (
    "· 语气语域：译文用冷峻简短的军事简报口吻，不用敬语、不加句末语气词。\n"
    "· 译名风格：怪物与武器名称一律音译，不意译。\n"
    "· 世界观补充：Зона=禁区，аномалия=异常点，мутант=变异体。\n"
    "· 粗口：原文脏话按原意直译并保留同等力度，不要改写成书面语。"
)


_LLAMA_TAG = "b11612"
_GH_BASE = ("https://github.com/ggml-org/llama.cpp/releases/download/"
            + _LLAMA_TAG + "/llama-" + _LLAMA_TAG + "-bin-win-{variant}-x64.zip")
_GH_CUDART = ("https://github.com/ggml-org/llama.cpp/releases/download/"
              + _LLAMA_TAG + "/cudart-llama-bin-win-cuda-12.4-x64.zip")
_GH_CUDART13 = ("https://github.com/ggml-org/llama.cpp/releases/download/"
                + _LLAMA_TAG + "/cudart-llama-bin-win-cuda-13.4-x64.zip")

# 官方 SHA-256（2026-10-23 取自发布方元数据）：
# 运行库 = llama.cpp b11612 GitHub Release asset digest；
# 模型 = HuggingFace LFS oid（sha256），并核对官方字节数。
_LLAMA_SHA256 = {
    "cpu": "b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69",
    "cuda-12.4": "4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12",
    "vulkan": "e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9",
    "cudart-12.4": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    # CUDA 13.4（2026-10-29 取自同一 Release 的 asset digest）
    "cuda-13.4": "e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38",
    "cudart-13.4": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
}
_MODEL_HASHES = {
    "Hy-MT2-7B-Q4_K_M.gguf": (
        "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        4624648896),
    "Hy-MT2-1.8B-Q8_0.gguf": (
        "5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        1908528192),
}
_GH_PROXIES = [
    ("GitHub 直连", "{url}"),
    ("ghproxy.net 代理", "https://ghproxy.net/{url}"),
    ("gh.h233 代理", "https://gh.h233.eu.org/{url}"),
    ("ghfast 代理", "https://ghfast.top/{url}"),
    ("geekertao 代理", "https://gh.geekertao.top/{url}"),
    ("dpik 代理", "https://gh.dpik.top/{url}"),
    ("llkk 代理", "https://gh.llkk.cc/{url}"),
    ("moeyy 代理", "https://github.moeyy.xyz/{url}"),
    ("ghgo 代理", "https://ghgo.xyz/{url}"),
    ("gh-proxy 代理", "https://gh-proxy.com/{url}"),
    ("imciel 代理", "https://ghproxy.imciel.com/{url}"),
    ("ddlc 代理", "https://gh.ddlc.top/{url}"),
    ("idayer 代理", "https://gh.idayer.com/{url}"),
    ("nuaa 镜像", "https://download.nuaa.cf/{url}"),
    ("mirror.ghproxy 代理", "https://mirror.ghproxy.com/{url}"),
    ("ghproxy.cc 代理", "https://ghproxy.cc/{url}"),
    ("960316 代理", "https://gh.960316.xyz/{url}"),
    ("99988866 代理", "https://gh.api.99988866.xyz/{url}"),
    # 2026-10-29 实测可用（不同网络下可用性会变；插件逐个测速择优）
    ("gh-proxy.org 代理", "https://gh-proxy.org/{url}"),
    ("gh.nxnow 代理", "https://gh.nxnow.top/{url}"),
    ("catmak 代理", "https://gh.catmak.name/{url}"),
    ("monlor 代理", "https://gh.monlor.com/{url}"),
]
_GH_DOMAIN_MIRRORS = [
    ("kkgithub 域名镜像", "https://kkgithub.com/{path}"),
]


def _gh_sources(url):
    path = url.split("github.com/", 1)[-1]
    sources = [(n, t.format(url=url)) for n, t in _GH_PROXIES]
    sources += [(n, t.format(path=path)) for n, t in _GH_DOMAIN_MIRRORS]
    return sources

_CUDART_MARKS = ("cudart64", "cublas", "nvrtc")
_CUDART_MARK = ".cudart_installed.json"

def _model_sources(repo_ms, repo_hf, filename):
    return [
        ("ModelScope 主站",
         "https://modelscope.cn/models/%s/resolve/master/%s" % (repo_ms, filename)),
        ("ModelScope 主站(www)",
         "https://www.modelscope.cn/models/%s/resolve/master/%s" % (repo_ms, filename)),
        ("ModelScope CDN",
         "https://modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s" % (repo_ms, filename)),
        ("ModelScope CDN(www)",
         "https://www.modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s" % (repo_ms, filename)),
        ("ModelScope CDN(download)",
         "https://modelscope.cn/api/v1/models/%s/repo?Revision=master"
         "&FilePath=%s&download=true" % (repo_ms, filename)),
        ("hf-mirror（HF 国内镜像）",
         "https://hf-mirror.com/%s/resolve/main/%s" % (repo_hf, filename)),
        ("hf-mirror.net（HF 国内镜像 2）",
         "https://hf-mirror.net/%s/resolve/main/%s" % (repo_hf, filename)),
        ("HuggingFace 官方",
         "https://huggingface.co/%s/resolve/main/%s" % (repo_hf, filename)),
        ("HuggingFace 官方(hf.co)",
         "https://hf.co/%s/resolve/main/%s" % (repo_hf, filename)),
    ]


TIER_PROFILES = {
    "7b": {
        "task": "model_7b",
        "file": "Hy-MT2-7B-Q4_K_M.gguf",
        "repo_ms": "Tencent-Hunyuan/Hy-MT2-7B-GGUF",
        "repo_hf": "tencent/Hy-MT2-7B-GGUF",
        "size_gb": 4.4,
        "ram_gb": 8,
        "ctx": 8192,
        "combo": "精雕 · Hy-MT2 7B Q4_K_M｜最精确·质量首选｜速度中等｜上下文8K｜约8GB内存",
        "download": "① 精雕 Hy-MT2-7B Q4_K_M（4.4GB · 质量最高/最精确 · 速度中等）",
        "pos": ("质量：最高（复杂长难句、专业领域、多语言指令遵循最强）\n"
                "精度：最精确，专名/语气/占位符保持最好\n"
                "速度：中等（CPU 短句约数秒/条）\n"
                "上下文：8192 token 质量窗（约 3800 字符/段，超长自动分句）\n"
                "定位：终校级译文，NS7 这类超长日记/大段任务文本首选"),
    },
    "mt2": {
        "task": "model_mt2",
        "file": "Hy-MT2-1.8B-Q8_0.gguf",
        "repo_ms": "Tencent-Hunyuan/Hy-MT2-1.8B-GGUF",
        "repo_hf": "tencent/Hy-MT2-1.8B-GGUF",
        "size_gb": 1.8,
        "ram_gb": 4,
        "ctx": 16384,
        "combo": "精准 · Hy-MT2 1.8B Q8_0｜高质量·快速｜上下文16K｜约4GB内存",
        "download": "② 精准 Hy-MT2-1.8B Q8_0（1.8GB · 质量高/精确 · 速度快速）",
        "pos": ("质量：高（新一代小模型，官方称整体超越主流商业翻译 API）\n"
                "精度：精确，较上代小模型在真实场景与指令遵循上明显提升\n"
                "速度：快速（1.8B 轻量，CPU 也明显快于 7B）\n"
                "上下文：16384 token 质量窗（约 7600 字符/段，超长自动分句）\n"
                "定位：日常汉化主力，质量/速度最均衡的一档"),
    },
    "custom": {
        "task": None,
        "file": None,
        "size_gb": 0,
        "ram_gb": 0,
        "ctx": 8192,
        "combo": "自选 · 任意 GGUF 离线模型｜本机自选文件·自定上下文",
        "pos": ("用法：点「选择已有模型」指定本机任意 .gguf 文件（拖入亦可），\n"
                "并在右侧选择该模型要使用的上下文长度。\n"
                "要求：指令/对话微调的聊天模型（GGUF 内置 chat template）；\n"
                "非混元模型的翻译风格与术语遵循由模型本身决定。\n"
                "上下文：按所选长度生效，超长文本仍自动分句；请按模型大小与\n"
                "内存选择（经验：7B/Q4 约 8GB，1.8B/Q8 约 4GB，上下文越大越吃内存）"),
    },
}
TIER_ORDER = ("7b", "mt2", "custom")
BUILTIN_TIERS = ("7b", "mt2")
CUSTOM_CTX_CHOICES = (4096, 8192, 16384, 32768)
TIER_SHORT = {
    "7b": "精雕7B｜质量最高最精确｜速度中等｜8K上下文",
    "mt2": "精准1.8B｜高质量精确｜快速｜16K上下文",
    "custom": "自选模型｜本机任意 GGUF｜上下文自定",
}

MODE_LABELS = {
    "auto": "auto 自动识别全部",
    "text": "text 文本XML",
    "cfgxml": "cfgxml 配置XML",
    "ltx": "ltx 配置",
    "scriptL": "scriptL 脚本就地替换",
    "scriptE": "scriptE 脚本提取",
}
MODE_ORDER = ("auto", "text", "cfgxml", "ltx", "scriptL", "scriptE")


def _model_task(tier):
    p = TIER_PROFILES[tier]
    digest, size_bytes = _MODEL_HASHES[p["file"]]
    return {
        "label": p["download"],
        "kind": "model", "tier": tier,
        "filename": p["file"],
        "size_hint": p["size_gb"],
        "size_bytes": size_bytes,
        "sha256": digest,
        "sources": _model_sources(p["repo_ms"], p["repo_hf"], p["file"]),
    }


DOWNLOAD_TASKS = {
    TIER_PROFILES[t]["task"]: _model_task(t) for t in BUILTIN_TIERS
}
DOWNLOAD_TASKS.update({
    "bin_cpu": {
        "label": "llama.cpp CPU 运行库（约 19 MB，兼容性最好）",
        "kind": "zip", "variant": "cpu",
        "filename": "llama-server.exe",
        "size_hint": 0.019,
        "sha256": _LLAMA_SHA256["cpu"],
        "sources": _gh_sources(_GH_BASE.format(variant="cpu")),
    },
    "bin_cuda": {
        "label": "llama.cpp CUDA 12 运行库（约 630 MB，含运行时，NVIDIA 最快）",
        "kind": "zip", "variant": "cuda",
        "filename": "llama-server.exe",
        "size_hint": 0.63,
        "sha256": _LLAMA_SHA256["cuda-12.4"],
        "extra_runtime": {
            "url": _GH_CUDART,
            "marks": _CUDART_MARKS,
            "label": "CUDA 运行时（cudart/cublas）",
            "sha256": _LLAMA_SHA256["cudart-12.4"],
        },
        "sources": _gh_sources(_GH_BASE.format(variant="cuda-12.4")),
    },
    "bin_cuda13": {
        "label": "llama.cpp CUDA 13.4 运行库（约 580 MB，需 580+ 驱动 / Turing+）",
        "kind": "zip", "variant": "cuda13",
        "filename": "llama-server.exe",
        "size_hint": 0.58,
        "sha256": _LLAMA_SHA256["cuda-13.4"],
        "extra_runtime": {
            "url": _GH_CUDART13,
            "marks": _CUDART_MARKS,
            "label": "CUDA 13 运行时（cudart/cublas）",
            "sha256": _LLAMA_SHA256["cudart-13.4"],
        },
        "sources": _gh_sources(_GH_BASE.format(variant="cuda-13.4")),
    },
    "bin_vulkan": {
        "label": "llama.cpp Vulkan GPU 运行库（约 31 MB，A/N/I 显卡通用）",
        "kind": "zip", "variant": "vulkan",
        "filename": "llama-server.exe",
        "size_hint": 0.031,
        "sha256": _LLAMA_SHA256["vulkan"],
        "sources": _gh_sources(_GH_BASE.format(variant="vulkan")),
    },
})

VARIANT_LABELS = {
    "cpu": "CPU（兼容性好，不占显存）",
    "cuda": "CUDA 12.4（NVIDIA 显卡，兼容性最好）",
    "cuda13": "CUDA 13.4（需 580+ 驱动 / Turing+，可选）",
    "vulkan": "Vulkan（A/N/I 显卡通用加速）",
}
VARIANT_ORDER = ("cpu", "cuda", "cuda13", "vulkan")
GPU_VARIANTS = ("cuda", "cuda13", "vulkan")

TIER_LABELS = [TIER_SHORT[t] for t in TIER_ORDER]
_LABEL_TO_TIER = {TIER_SHORT[t]: t for t in TIER_ORDER}
_LABEL_TO_TIER.update({TIER_PROFILES[t]["combo"]: t for t in TIER_ORDER})
_MODEL_TASK_ORDER = [TIER_PROFILES[t]["task"] for t in BUILTIN_TIERS]
TASK_KEY_ORDER = _MODEL_TASK_ORDER + ["bin_cpu", "bin_cuda",
                                    "bin_cuda13", "bin_vulkan"]
TIER_BY_TASK = {TIER_PROFILES[t]["task"]: t for t in BUILTIN_TIERS}
TIER_FILE = {t: TIER_PROFILES[t]["file"] for t in BUILTIN_TIERS}
TIER_ALIAS = {"1b8": "mt2", "mt15": "mt2"}


def _fmt_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0


def _fmt_eta(sec):
    if sec is None or sec < 0 or sec > 3600 * 99:
        return "--:--:--"
    sec = int(sec)
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


def detect_nvidia_gpu():
    """通过 nvidia-smi 探测 NVIDIA 独显。

    返回 (名称, 显存MB, 驱动版本, compute_cap)；无 N 卡/取不到返回 None。
    纯本地、无网络；nvidia-smi 随驱动安装。旧版只取前两项，1.7.0 起带上
    驱动版本与计算能力，用于判断能否使用 CUDA 13.4 构建。
    """
    def query(fields):
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=" + fields,
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))

    try:
        r = query("name,memory.total,driver_version,compute_cap")
        legacy = False
        if r.returncode != 0:
            # 老驱动不认识 compute_cap：退回 1.x 的两字段查询，至少别把 N 卡
            # 误判成"没有独显"（那样会白白降到 Vulkan/CPU）。
            r = query("name,memory.total")
            legacy = True
        if r.returncode != 0:
            return None
        lines = (r.stdout or "").strip().splitlines()
        if not lines:
            return None
        parts = [x.strip() for x in lines[0].split(",")]
        name = parts[0] if parts else ""
        mem = "".join(ch for ch in (parts[1] if len(parts) > 1 else "")
                      if ch.isdigit())
        driver = "" if legacy or len(parts) <= 2 else parts[2]
        try:
            cc = 0.0 if legacy or len(parts) <= 3 else float(parts[3])
        except (TypeError, ValueError):
            cc = 0.0
        return (name, int(mem) if mem else 0, driver, cc)
    except Exception:
        return None


def cuda13_supported(driver, compute_cap):
    """CUDA 13.x 构建的门槛：驱动主版本 ≥ 580 且计算能力 ≥ 7.5（Turing+）。

    这是"可尝试"的判据，不是保证：OEM/笔记本驱动、Optimus、UMD 与 Toolkit
    版本差异都可能让实际初始化失败，所以调用方还要用真实启动 + GPU 层验证兜底。
    """
    try:
        major = int(str(driver).strip().split(".")[0])
    except (TypeError, ValueError):
        return False
    try:
        cc = float(compute_cap)
    except (TypeError, ValueError):
        return False
    return major >= 580 and cc >= 7.5


_IGPU_MARKERS = ("microsoft basic", "basic display", "remote", "citrix",
                 "parsec", "vmware", "virtual", "hyper-v", "meta display",
                 "oray", "sunlogin", "todesk", "gameviewer", "usb display")


def _gpu_registry_vram():
    """从注册表取真实显存（字节）：{驱动描述: 字节数}，取不到返回 {}。

    为什么不用 Win32_VideoController.AdapterRAM：它是 uint32，≥4GB 就回绕
    （本机 RTX 3060 6GB 报 4293918720 字节 → 被算成 4095MB 再截成 4096）。
    HardwareInformation.qwMemorySize 是 64 位，能拿到真实值（本机实测 6.0GiB）。
    """
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\"
          "{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue"
          " | ForEach-Object { $p=Get-ItemProperty $_.PSPath"
          " -ErrorAction SilentlyContinue;"
          " if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize')"
          " { \"$($p.DriverDesc)|$($p.'HardwareInformation.qwMemorySize')\" } }")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=12,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        out = r.stdout.decode("utf-8", "ignore")
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        name, _, val = line.strip().partition("|")
        val = val.strip()
        if name and val.isdigit() and int(val) > 0:
            table[name] = int(val)
    return table


def _is_integrated_gpu(low_name):
    """判断是不是集显/虚拟卡（入参已小写）。

    保守策略：拿不准就按集显处理 → 最终退回 CPU。相比"猜成独显去开
    Vulkan、结果比 CPU 还慢"，退回 CPU 是安全得多的错法。
    """
    if any(k in low_name for k in _IGPU_MARKERS):
        return True
    if "intel" in low_name and "arc" not in low_name:
        return True
    if "radeon" in low_name or "amd" in low_name:
        if ("rx " in low_name or " rx" in low_name or "pro " in low_name
                or "instinct" in low_name or "vii" in low_name):
            return False
        return True
    return False


def detect_all_gpus():
    """列出全部显卡（CIM 取名 + 注册表取真实显存），返回 [(名称, 显存MB)]；
    显存未知记为 0，不编造近似值。"""
    vram_table = _gpu_registry_vram()
    try:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-CimInstance Win32_VideoController | ForEach-Object {"
              "$n=$_.Name; $m=[int64]($_.AdapterRAM); \"$n|$m\"}")
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=12,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        out = r.stdout.decode("utf-8", "ignore")
        gpus = []
        for line in out.splitlines():
            name, _, mem = line.strip().partition("|")
            if not name:
                continue
            mb = 0
            real = vram_table.get(name)
            if real:
                mb = int(real // (1024 * 1024))
            elif mem.isdigit():
                mb = int(mem) // 1024 // 1024
                if mb > 4095:
                    mb = 0
            gpus.append((name, mb))
        return gpus
    except Exception:
        return []


def detect_hardware():
    """整机硬件勘察 → 推荐后端与模型档。返回 dict：
    variant / tier / gpu_name / vram / short（一句话）。"""
    nv = detect_nvidia_gpu()
    others = detect_all_gpus()
    discrete_other = None
    for name, vram in others:
        low = name.lower()
        if "nvidia" in low:
            continue
        if _is_integrated_gpu(low):
            continue
        discrete_other = (name, vram)
    if nv:
        name, vram, driver, cc = nv
        variant = "cuda"          # 默认 12.4；13.4 需用户显式选择
        cuda13_ok = cuda13_supported(driver, cc)
        tier = "7b" if vram >= 5120 else "mt2"
        short = "%s %dGB → CUDA 12.4 + %s%s" % (
            name.replace("NVIDIA GeForce ", ""), round(vram/1024, 1),
            {"7b": "精雕7B", "mt2": "精准1.8B"}[tier],
            ("（CUDA 13.4 可用）" if cuda13_ok else ""))
        return {"variant": variant, "tier": tier, "gpu_name": name,
                "vram": vram, "short": short, "driver": driver,
                "compute_cap": cc, "cuda13_ok": cuda13_ok}
    if discrete_other:
        name, vram = discrete_other
        tier = "mt2"
        return {"variant": "vulkan", "tier": tier, "gpu_name": name,
                "vram": vram,
                "short": "%s → Vulkan + 精准1.8B" % name}
    return {"variant": "cpu", "tier": "mt2", "gpu_name": "",
            "vram": 0, "short": "未检测到独显 → CPU + 精准1.8B"}



_ENC_FALLBACK = ("cp1251", "cp1252")
_ENC_FORCED = "cp1251"
_ENC_UTF8 = ("utf-8", "utf-8-sig")
_BOM_UTF8 = b"\xef\xbb\xbf"
_XML_DECL_ENC = re.compile(rb"<\?xml[^>]*?encoding\s*=\s*[\"']([\w.\-]+)", re.I)
_DECL_SCAN_BYTES = 2048


def _is_valid_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _declared_encoding(raw):
    """取 XML 声明里写明的编码名；未声明 / 名字不被 Python 认识时返回 None。"""
    m = _XML_DECL_ENC.search(raw[:_DECL_SCAN_BYTES])
    if not m:
        return None
    name = m.group(1).decode("ascii", "ignore").strip().lower()
    try:
        codecs.lookup(name)
    except (LookupError, ValueError):
        return None
    return name


def encoding_chain(raw):
    """给出该字节流的解码候选次序（首个能严格解码成功者胜出）。

    次序规则，按优先级：
      1. UTF-8 BOM          → utf-8-sig（唯一的权威信号）
      2. 字节流是合法 UTF-8 → utf-8
      3. XML 声明写明的编码 → 提到最前（声明与实际不符时严格解码会失败，
         自动落到下一档，不会误判）
      4. 其余按 cp1251 → cp1252（STALKER 俄版为主流），末位固定 _ENC_FORCED

    返回元组；末位是"尽力而为"档，供解析器的 errors="ignore" 兜底使用。

    已知限制：无 BOM、无声明、又非 UTF-8 的文件，若同时是合法 cp1251 与
    合法 cp1252 字节流（极常见），此处按 STALKER 主流取 cp1251；西文 ww
    版这类文件请把「源语言」选为 eng 或在文件头补 XML 声明。
    """
    if raw.startswith(_BOM_UTF8):
        cands = ["utf-8-sig"]
    elif _is_valid_utf8(raw):
        cands = ["utf-8"]
    else:
        cands = []
        decl = _declared_encoding(raw)
        if decl and decl not in _ENC_UTF8:
            cands.append(decl)
        cands.extend(n for n in _ENC_FALLBACK if n not in cands)
    if not cands or cands[-1] != _ENC_FORCED:
        cands.append(_ENC_FORCED)
    return tuple(cands)


def read_encoding_chain(path, max_bytes=64 * 1024 * 1024):
    """读取文件并给出其解码候选次序；读不了时返回兜底次序。
    仅供解析前判定编码用，不改变调用方自己的读取逻辑。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(max_bytes if size > max_bytes else size)
    except OSError:
        return (_ENC_FORCED,)
    if size > max_bytes:
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        if raw and raw[-1] >= 0x80:
            raw = raw[:-1]
    return encoding_chain(raw)


def read_text_any(path):
    """按 UTF-8（含 BOM）→ cp1251 顺序读取脚本/XML 原文，读不出时替换。"""
    try:
        raw = open(path, "rb").read()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _files_equal(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x = fa.read(1024 * 1024)
                y = fb.read(1024 * 1024)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


_MOJIBAKE_CHARS = "ÐÑÃÂâ€™Ð„ÑŽ"


def looks_like_mojibake(text):
    """粗判"UTF-8 被单字节码表解出来的乱码"，只用于告警、不自动改判。

    正常俄文/中文文本里不会成片出现 Ð/Ñ/Ã/Â；而 UTF-8 的俄文（Д→D0 94）
    或中文被 cp1251 解码后，特征就是成片的拉丁扩充字符。
    """
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    bad = sum(1 for c in letters if c in _MOJIBAKE_CHARS)
    return bad / len(letters) > 0.3


_VENDOR_IO_LOCK = threading.Lock()


class capture_stdout(object):
    """把 vendor 解析器的 print() 诊断收进插件日志。

    vendor 包（wzyddg fork）用 print 报告"某文件用哪个编码解码"这类诊断，
    而冻结后的 exe 是 GUI 子系统（PE Subsystem=2），stdout 为 None —— 这些
    诊断在用户侧完全不可见，编码猜错时没有任何线索。这里把它收进
    api.log_detail（只落盘），让"到底用了哪个编码"事后可复查。

    stdout 是进程级全局对象，故重定向期间加锁串行；解析只在作业线程发生，
    且按钮互斥保证同时只有一个作业，冲突面很小。拿不到锁则退化为不重定向
    （宁可少一行日志，也不阻塞翻译主流程）。
    """

    def __init__(self):
        self._buf = io.StringIO()
        self._old = None
        self._held = False

    def __enter__(self):
        self._held = _VENDOR_IO_LOCK.acquire(timeout=30)
        if self._held:
            self._old = sys.stdout
            sys.stdout = self._buf
        return self

    def __exit__(self, *exc_info):
        if self._held:
            sys.stdout = self._old
            _VENDOR_IO_LOCK.release()
        return False

    def lines(self):
        return [ln.strip() for ln in self._buf.getvalue().splitlines()
                if ln.strip()]



DEFAULT_CONFIG = {
    "model_paths": {},
    "model_dir": _MODELS_DIR,
    "exe_paths": {},
    "custom_model_path": "",
    "custom_ctx": 8192,
    "tier": "7b",
    "variant": "cpu",
    "in_dir": "",
    "out_dir": "",
    "reuse_dir": "",
    "glossary": "",
    "force_glossary": True,
    "glossary_force": False,
    "protect_proper_v2": False,
    "src_lang": "auto",
    "mode": "auto",
    "session_glossary": True,
    "tm_lite": True,
    "output_anyway": False,
    "prompt_extra": "",
    "artifact_hashes": {},
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(_CONFIG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    if cfg.get("tier") in TIER_ALIAS:
        cfg["tier"] = TIER_ALIAS[cfg["tier"]]
    if cfg.get("tier") not in TIER_PROFILES:
        cfg["tier"] = "7b"
    mp = cfg.get("model_paths")
    if isinstance(mp, dict) and "1b8" in mp and "mt2" not in mp:
        mp["mt2"] = mp.pop("1b8")
    for k in ("in_dir", "out_dir", "reuse_dir", "glossary"):
        v = cfg.get(k)
        if isinstance(v, str) and v and not os.path.exists(v):
            cfg[k] = ""
    md = cfg.get("model_dir")
    if not (isinstance(md, str) and md and os.path.isdir(md)):
        cfg["model_dir"] = _MODELS_DIR
    if isinstance(mp, dict):
        cfg["model_paths"] = {
            k: v for k, v in mp.items()
            if isinstance(v, str) and v and os.path.isfile(v)}
    ep = cfg.get("exe_paths")
    if isinstance(ep, dict):
        cfg["exe_paths"] = {
            k: v for k, v in ep.items()
            if isinstance(v, str) and v and os.path.isfile(v)}
    cp = cfg.get("custom_model_path")
    if cp and (not isinstance(cp, str) or not os.path.isfile(cp)):
        cfg["custom_model_path"] = ""
    return cfg


_JSON_WRITE_LOCK = threading.Lock()


_LAST_CONFIG_ERROR = ""


def save_config(cfg):
    """原子写配置（tmp → os.replace），并发调用串行化。返回是否写成功。

    写失败**不再静默**：调用方据此提示用户（只读目录/磁盘满时，设置只在内存
    生效）。并发修改 dict 可能让 json.dumps 抛 RuntimeError，同样要捕获。
    """
    global _LAST_CONFIG_ERROR
    try:
        payload = json.dumps(cfg, ensure_ascii=False, indent=1)
    except (TypeError, ValueError, RuntimeError) as e:
        _LAST_CONFIG_ERROR = "%s: %s" % (type(e).__name__, e)
        return False
    with _JSON_WRITE_LOCK:
        try:
            tmp = _CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, _CONFIG)
            _LAST_CONFIG_ERROR = ""
            return True
        except Exception as e:
            _LAST_CONFIG_ERROR = "%s: %s" % (type(e).__name__, e)
            return False



def load_builtin_glossary():
    """随包术语（只读）。优先 glossary_builtin.json，兼容旧版文件名。"""
    for path in (_GLOSS_BUILTIN, _GLOSS_BUILTIN_LEGACY):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return flatten_glossary(json.load(f))
        except Exception:
            continue
    return {}


def _read_user_layer(builtin=None):
    """读用户覆盖层，返回 (set_map, deleted, clear_builtin)。

    兼容 1.6.0 之前的扁平格式：那份文件其实是"整张术语库的快照"，若直接当
    覆盖层会把 builtin 的后续更新全部遮蔽。这里按**与 builtin 的差异**提取，
    只保留用户真正改过/新增的条目。
    """
    try:
        with open(_GLOSS_USER, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, set(), False
    if isinstance(data, dict) and data.get("_format") == _GLOSS_LAYER_FORMAT:
        raw_set = data.get("set") if isinstance(data.get("set"), dict) else {}
        set_map = {str(k): str(v) for k, v in raw_set.items()
                   if str(k).strip() and not str(k).startswith("_")}
        try:
            deleted = {str(k) for k in (data.get("del") or [])
                       if str(k).strip()}
        except TypeError:
            deleted = set()
        return set_map, deleted, bool(data.get("clear_builtin"))
    flat = flatten_glossary(data) if isinstance(data, dict) else {}
    base = load_builtin_glossary() if builtin is None else builtin
    return {k: v for k, v in flat.items() if base.get(k) != v}, set(), False


def load_glossary_layers():
    """返回 (合并后的术语表, 用户 set 层, 用户删除集, 是否清空 builtin)。"""
    builtin = load_builtin_glossary()
    set_map, deleted, clear = _read_user_layer(builtin)
    merged = {} if clear else dict(builtin)
    for key in deleted:
        merged.pop(key, None)
    merged.update(set_map)
    return merged, set_map, deleted, clear


# 只把真正跨语境稳定的专名当硬词。界面与提示（Здоровье→生命值）、
# 任务、对话/百科类会随语境变化，一律只做软参考或不注入，避免
# “Здоровье не шалит?”被硬塞成“生命值”这类语境错误。
_HARD_TERM_CATEGORIES = (
    # 人物/军衔不再全局硬替换：不同 MOD 的同名人物可能不是同一人。
    # 人物名由“当前 MOD 会话词表 + 人物表精确命中”处理，见 _build_session_glossary。
    "阵营与组织", "地点与区域", "变异生物", "物品·武器·装备·神器")


_TERMBASE_PATH = os.path.join(_ASSETS, "stalker_termbase.json")
_TERMBASE_CACHE = {"key": None, "data": {}}
_DEFAULT_LAYERS = {
    "canon": list(_HARD_TERM_CATEGORIES),
    "names": ["人物与军衔"],
    "technical": ["技术缩写"],
    "reference": ["ANTHOLOGY 参考"],
}
_RISKY_LATIN_RE = re.compile(r"^[a-z][a-z0-9._\-/&()]{1,18}$")


def load_termbase_data():
    """读随包分类术语库（带 mtime 缓存，避免每次命中都重读 350 KB）。"""
    try:
        st = os.stat(_TERMBASE_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _TERMBASE_CACHE.get("key") == key:
        return _TERMBASE_CACHE.get("data") or {}
    try:
        with open(_TERMBASE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    _TERMBASE_CACHE["key"] = key
    _TERMBASE_CACHE["data"] = data
    return data


def _layer_categories(meta, name):
    layers = meta.get("layers") if isinstance(meta, dict) else None
    cats = layers.get(name) if isinstance(layers, dict) else None
    if isinstance(cats, str):
        cats = [cats]
    if not isinstance(cats, list):
        cats = _DEFAULT_LAYERS.get(name, [])
    return [str(c) for c in cats if str(c).strip()]


def load_term_layers():
    """返回 v2 四层索引：canon/names/technical/reference + 变体/大小写策略。

    旧版术语库没有 `_meta.layers` 时按原分类回退，导入别人的分类词库也能用。
    """
    data = load_termbase_data()
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    canon_cats = _layer_categories(meta, "canon")
    name_cats = _layer_categories(meta, "names")
    tech_cats = _layer_categories(meta, "technical")
    ref_cats = _layer_categories(meta, "reference")

    def collect(cats):
        keys, table = set(), {}
        for cat in cats:
            t = data.get(cat)
            if not isinstance(t, dict):
                continue
            for k, v in t.items():
                name = str(k).strip()
                if not name or name.startswith("_"):
                    continue
                keys.add(name)
                table[name] = str(v)
        return keys, table

    canon, _ = collect(canon_cats)
    names, _ = collect(name_cats)
    _, technical = collect(tech_cats)
    _, reference = collect(ref_cats)
    variants = meta.get("variants") if isinstance(meta.get("variants"),
                                                 dict) else {}
    variants = {str(k): str(v) for k, v in variants.items()
                if str(k).strip() and str(v).strip()}
    explicit = meta.get("case_sensitive")
    case_sensitive = ({str(k) for k in explicit if str(k).strip()}
                      if isinstance(explicit, list) else set())
    # 人物名与内容型专名按“首字母大小写一致”匹配：英文正文里的
    # doctor/bar/crystal 这类普通词不会被当成专名。
    case_sensitive |= names
    case_sensitive |= {k for k in technical if re.search(r"[A-Za-z]", k)}
    case_sensitive |= {k for k in canon if _RISKY_LATIN_RE.match(k)}
    quote_required = meta.get("quote_required")
    quote_required = ({str(k) for k in quote_required if str(k).strip()}
                      if isinstance(quote_required, list) else set())
    return {"canon": canon, "names": names, "technical": technical,
            "reference": reference, "variants": variants,
            "case_sensitive": case_sensitive,
            "quote_required": quote_required}


def load_hard_glossary_keys():
    """只把 canon 层（阵营/地点/变异体/物品）当硬词；人物名与对话类只做软参考。

    builtin 术语表是扁平表，混入了大量新闻/对话里的普通词和屈折形
    （例如 Информацию→信息、Искренне→火花）。这些词强制替换会把
    句子改坏；按 v2 的 canon 层筛出硬词，普通词不再注入/强制。
    """
    return set(load_term_layers()["canon"])


def load_technical_exact():
    """数据化技术精确表；随包术语库优先，旧包回退到内置常量。"""
    out = dict(_TECHNICAL_EXACT)
    out.update(load_term_layers()["technical"])
    return out


def save_user_glossary(set_map, deleted=None, clear_builtin=False):
    """只写用户覆盖层；builtin 永远是随包只读文件。"""
    payload = json.dumps({
        "_format": _GLOSS_LAYER_FORMAT,
        "clear_builtin": bool(clear_builtin),
        "set": dict(set_map or {}),
        "del": sorted(deleted or ()),
    }, ensure_ascii=False, indent=1, sort_keys=True)
    with _JSON_WRITE_LOCK:
        tmp = _GLOSS_USER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, _GLOSS_USER)


def flatten_glossary(data):
    """把术语表归一成 {原文: 译文}。

    兼容三种文件形态并允许**混合**：
    1) 扁平 {"原文": "译文"}；
    2) 分类名词库 {"分类名": {"原文": "译文"}, ...}（可多层嵌套）；
    3) 上面两种混在一张表里（如 {"a": "b", "武器": {...}}）。

    以 "_" 开头的段（如 _meta）整段跳过。

    注意：旧实现只要表里出现**任何**一个 dict 值，就把整张表当分类表解析，
    于是同一张表里的扁平条目被静默丢弃 —— 实测
    {"普通原文":"普通译文","分类A":{"原文1":"译文1"}} 只剩 {"原文1":...}，
    导入后无提示少条目。这里改成逐键判定，两侧都保留。
    """
    out = {}

    def walk(table, depth=0):
        for key, value in table.items():
            name = str(key).strip()
            if not name or name.startswith("_"):
                continue
            if isinstance(value, dict) and depth < 8:
                walk(value, depth + 1)
            elif not isinstance(value, (dict, list, tuple)):
                out[name] = str(value)

    if isinstance(data, dict):
        walk(data)
    return out



_VENDOR_READY = False


def _load_vendor():
    global _VENDOR_READY
    import types
    if _VENDOR_READY:
        return sys.modules["xRayXmlParser"]
    if not os.path.isfile(_VENDOR_ZIP):
        raise FileNotFoundError("缺少解析器包：%s" % _VENDOR_ZIP)

    pkg_name = "xRayXmlParser"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = []
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg

    order = ["entityDefinition", "xRayTextAnalyzer",
             "xmlFileGenerator", "xmlxReader"]
    with zipfile.ZipFile(_VENDOR_ZIP) as z:
        sources = {n: z.read("%s/%s.py" % (pkg_name, n)).decode("utf-8")
                   for n in order}
        init_src = z.read("%s/__init__.py" % pkg_name).decode("utf-8")

    for name in order:
        mod = types.ModuleType("%s.%s" % (pkg_name, name))
        mod.__package__ = pkg_name
        sys.modules["%s.%s" % (pkg_name, name)] = mod
        code = compile(sources[name], "%s!%s.py" % (_VENDOR_ZIP, name), "exec")
        exec(code, mod.__dict__)
        setattr(pkg, name, mod)

    code = compile(init_src, "%s!__init__.py" % _VENDOR_ZIP, "exec")
    exec(code, pkg.__dict__)
    _VENDOR_READY = True
    return pkg



class ProbeResult(object):
    def __init__(self, name, url):
        self.name = name
        self.url = url
        self.ok = False
        self.speed = 0.0
        self.got = 0
        self.secs = 0.0
        self.range_ok = False
        self.note = ""
        self.kind = ""


def _host_kind(url):
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("huggingface.co", "hf.co", "github.com"):
        return "直连"
    return "镜像"


def probe_source(name, url, max_bytes=2 * 1024 * 1024, max_secs=8.0):
    """对单个源做 Range 小样本测速。失败不抛异常，全部塞进 ProbeResult。"""
    res = ProbeResult(name, url)
    res.kind = _host_kind(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Range": "bytes=0-%d" % (max_bytes - 1)})
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        with resp:
            res.range_ok = (resp.status == 206)
            ctype = resp.headers.get("Content-Type", "")
            if "html" in ctype.lower():
                res.note = "返回网页而非文件"
                return res
            while res.got < max_bytes and (time.time() - t0) < max_secs:
                chunk = resp.read(65536)
                if not chunk:
                    break
                res.got += len(chunk)
        res.secs = max(time.time() - t0, 0.001)
        res.speed = (res.got / 1048576.0) / res.secs
        res.ok = res.got > 256 * 1024
        if not res.ok:
            res.note = "样本过小（%s）" % _fmt_size(res.got)
    except urllib.error.HTTPError as e:
        res.note = "HTTP %s" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        res.note = "连接失败：%s" % getattr(e, "reason", e)
    except Exception as e:
        res.note = "%s: %s" % (type(e).__name__, e)
    finally:
        if res.secs == 0.0:
            res.secs = max(time.time() - t0, 0.001)
    return res


def run_probe(sources, log=None, max_workers=12):
    """sources: [(name, url)]；并发测速（上限 max_workers，
    源很多时避免一次性打开几十条连接互相抢带宽），按速度降序返回。"""
    if log:
        log("正在并发实测 %d 个下载源（每源取样 2 MB / 8 秒，最多 %d 并发）…"
            % (len(sources), max_workers))
    results = [None] * len(sources)
    sem = threading.Semaphore(max_workers)

    def worker(i, name, url):
        with sem:
            results[i] = probe_source(name, url)

    threads = []
    for i, (name, url) in enumerate(sources):
        t = threading.Thread(target=worker, args=(i, name, url), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    ok = [r for r in results if r and r.ok]
    bad = [r for r in results if r and not r.ok]
    ok.sort(key=lambda r: r.speed, reverse=True)
    if log:
        for r in ok:
            tag = "直连" if r.kind == "直连" else "镜像"
            log("  ✓ [%s] %s：%.2f MB/s（%s）" % (tag, r.name, r.speed,
                "支持断点" if r.range_ok else "不支持 Range"))
        for r in bad:
            log("  ✗ %s：%s" % (r.name, r.note))
    return ok + bad


def detect_environment(results):
    """根据测速结果归纳网络环境。"""
    official_ok = any(r.ok and r.kind == "直连" for r in results)
    mirror_ok = any(r.ok and r.kind == "镜像" for r in results)
    if official_ok and not mirror_ok:
        return "直连网络：官方源可达，已按实测速度选源"
    if official_ok and mirror_ok:
        return "混合网络：官方源与镜像均可达，已自动选择最快源"
    if mirror_ok:
        return "受限网络：官方源不可达，已自动切换国内镜像"
    return "所有下载源均不可达，请检查网络或代理设置"



class DownloadCancelled(Exception):
    pass


class OutputTruncated(Exception):
    """模型输出撞到 max_tokens 被截断。

    单独成类而不是并进 IOError：重试同一个请求没有意义（同样的输入、同样的
    上限，结果只会再截一次），必须由上层把源文切得更细再译，否则半句译文会
    被静默写进汉化包。
    """
    pass


def download_one(sources, target, log, progress=None, cancel_event=None,
                 retries_per_source=3, verify=None):
    """按 sources 顺序尝试；.part 断点续传；全部源失败才抛异常。

    progress(done, total, speed_mbps, eta_sec, src_name)
    verify(part_path) -> digest：**每个源下载完成后**立即校验，失败即删文件并
    换下一个源（文档一直这么写，1.6.0 之前实际只是删文件让用户手动重试）。
    返回校验得到的 digest；未提供 verify 时返回 True。
    """
    part = target + ".part"
    total = None
    last_err = None
    last_invalid = None

    for name, url in sources:
        for attempt in range(1, retries_per_source + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            try:
                headers = {"User-Agent": UA}
                if have:
                    headers["Range"] = "bytes=%d-" % have
                req = urllib.request.Request(url, headers=headers)
                resp = urllib.request.urlopen(req, timeout=30)
                with resp:
                    ctype = resp.headers.get("Content-Type", "")
                    if "html" in ctype.lower():
                        raise IOError("%s 返回了网页而非文件" % name)
                    if have and resp.status == 206:
                        cr = resp.headers.get("Content-Range", "")
                        if "/" in cr:
                            total = int(cr.rsplit("/", 1)[1])
                        mode = "ab"
                    else:
                        if have:
                            log("  %s 不支持断点，重新开始下载" % name)
                        total = int(resp.headers.get("Content-Length", 0)) or None
                        have = 0
                        mode = "wb"
                    done = have
                    t0 = time.time()
                    last_ui = 0.0
                    with open(part, mode) as f:
                        while True:
                            if cancel_event is not None and cancel_event.is_set():
                                raise DownloadCancelled()
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            now = time.time()
                            if progress and (now - last_ui) > 0.4:
                                speed = (done - have) / 1048576.0 / max(now - t0, 0.001)
                                eta = ((total - done) / 1048576.0 / speed
                                       if total and speed > 0 else None)
                                progress(done, total, speed, eta, name)
                                last_ui = now
                    if progress:
                        progress(done, total, 0, 0, name)
                final_size = os.path.getsize(part)
                if total and final_size != total:
                    raise IOError("下载不完整：%d / %d 字节" % (final_size, total))
                digest = None
                if verify is not None:
                    digest = verify(part)
                os.replace(part, target)
                return digest if verify is not None else True
            except DownloadCancelled:
                raise
            except InvalidArtifact as e:
                # verify 内部已删除坏文件；本源的断点不再有意义，换下一个源
                last_invalid = "%s（第 %d 次）：校验未通过：%s" % (
                    name, attempt, e)
                log("  下载产物校验未通过（%s），已删除，切换下一个源…" % e)
                break
            except Exception as e:
                last_err = "%s（第 %d 次）：%s" % (name, attempt, e)
                log("  下载中断，%s" % last_err)
                if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500:
                    log("  该源返回 HTTP %s，不再重试，直接换源" % e.code)
                    break
                if attempt < retries_per_source:
                    time.sleep(min(2 * attempt, 5))
        log("  切换下一个下载源（已保留断点 %s）…" % _fmt_size(
            os.path.getsize(part) if os.path.isfile(part) else 0))
    if last_invalid:
        raise InvalidArtifact("所有下载源均未通过校验；最后错误：%s"
                              % last_invalid)
    raise IOError("所有下载源均失败；最后错误：%s" % last_err)


def _extract_zip_into(zip_path, dest_dir):
    """解压到临时目录，再逐项移入 dest_dir（同名项先删）。

    为什么不在 dest_dir 上直接 extractall：中断会留下"半套文件"，而按文件名
    前缀做的存在性检查会把它当成已安装（CUDA 运行时就是这么坏掉的）。
    """
    os.makedirs(dest_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ai_translate_zip_", dir=dest_dir)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        for item in sorted(os.listdir(tmp)):
            src = os.path.join(tmp, item)
            dst = os.path.join(dest_dir, item)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_zip(zip_path, dest_dir, marker, log):
    """解压运行库 zip，返回 marker 文件（llama-server.exe）的完整路径。"""
    if os.path.isdir(dest_dir):
        for root, _dirs, files in os.walk(dest_dir):
            if marker in files:
                return os.path.join(root, marker)
    _extract_zip_into(zip_path, dest_dir)
    marker_path = None
    for root, _dirs, files in os.walk(dest_dir):
        if marker in files:
            marker_path = os.path.join(root, marker)
            break
    if not marker_path:
        raise IOError("压缩包中找不到 %s" % marker)
    log("  解压完成：%s" % marker_path)
    return marker_path



class InvalidArtifact(Exception):
    """下载产物校验未通过（不是预期文件，或与上次记录的内容不一致）。"""
    pass


_GGUF_TYPES = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
               4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
               10: ("Q", 8), 11: ("q", 8), 12: ("d", 8)}


def read_gguf_meta(path, want=("tokenizer.chat_template",
                               "tokenizer.ggml.eos_token_id",
                               "general.name", "general.architecture"),
                   max_elements=4000000, head_bytes=96 * 1024 * 1024):
    """读 GGUF 头部 KV 元数据（只读头部，不加载张量），返回 dict。

    为什么要读它：某些 GGUF 的 tokenizer 元数据不完整 —— 实测
    `hy-mt2-1.8b-q8_0.gguf` 既没有 `tokenizer.chat_template`，EOS 又是 3
    这个不在 EOG 集合里的值，llama.cpp 会打
    `special_eos_id is not in special_eog_ids`，于是模型**永远不吐 EOS**：
    一句 "Hello." 也能连生成 1285 个 token（CPU 上 178 秒）直到客户端超时。
    这种模型拿去翻几千条文本，结果就是每条都超时、整批白跑；启动时读一眼
    元数据就能提前告警。

    实现要点（都踩过坑）：
      * KV 必须**从头顺序走**（值是变长的），无法靠偏移量直接跳到某个键；
        而 `tokenizer.chat_template` 通常排在词表数组之后，所以要把头部
        整块读进内存再解析 —— 逐字段读文件在 10 万级词表上会明显变慢；
      * 读到的结果带一个 `"__partial__"` 标记：解析中途失败/缓冲区不够时
        置 True。**调用方必须据此区分"确实没有模板"与"没解析到"** ——
        早先版本预算耗尽后静默返回部分结果，导致有模板的 7B 被误报
        "未内置对话模板"。
    """
    import struct
    out = {}

    def rd_str(f):
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 * 1024 * 1024:
            raise ValueError("KV 字符串长度异常")
        return f.read(n).decode("utf-8", "replace")

    budget = [max_elements]

    def rd_val(f, t):
        if t == 8:
            return rd_str(f)
        if t == 9:
            et = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<Q", f.read(8))[0]
            if budget[0] - n < 0:
                raise ValueError("KV 数组元素数超出预算")
            budget[0] -= n
            if n > 8:
                for _ in range(n):
                    rd_val(f, et)
                return "<array %d>" % n
            return [rd_val(f, et) for _ in range(n)]
        fmt, size = _GGUF_TYPES[t]
        return struct.unpack("<" + fmt, f.read(size))[0]

    complete = False
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(min(size, head_bytes))
        f = io.BytesIO(head)
        if f.read(4) != b"GGUF":
            return out
        struct.unpack("<I", f.read(4))[0]
        struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]
        for _ in range(n_kv):
            key = rd_str(f)
            t = struct.unpack("<I", f.read(4))[0]
            if key in want:
                out[key] = rd_val(f, t)
            else:
                rd_val(f, t)
        complete = True
    except Exception:
        pass
    if not complete:
        out["__partial__"] = True
    return out




def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(1 << 20)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def verify_artifact(path, task, log, known_hashes=None):
    """校验刚下载完的产物，并把它的 sha256 返回（由调用方记进配置）。

    校验顺序与依据：
      * 官方 SHA-256：任务里登记了 sha256 时，必须逐字节一致。运行库哈希来自
        llama.cpp b11612 GitHub Release 的 asset digest，模型哈希来自 HuggingFace
        LFS oid；镜像只负责传输，不能改变校验值。
      * GGUF：magic 必须是 b"GGUF"、版本字段合理、体积与官方字节数一致；
      * 运行库 zip：必须是合法 zip、内部含 llama-server.exe、且那是个 PE(MZ)；
      * 跨次一致性：把 sha256 记进 config；配置里的旧哈希只作提示，官方哈希优先。

    没有登记官方哈希的产物退化为结构校验 + 跨次一致性，并在日志里明确写出
    这一边界。校验失败的文件一律删除并抛 InvalidArtifact。
    """

    def fail(msg):
        try:
            os.remove(path)
        except OSError:
            pass
        raise InvalidArtifact(msg)

    try:
        size = os.path.getsize(path)
    except OSError as e:
        fail("文件读不到：%s" % e)
    if size <= 0:
        fail("文件为空")

    if task.get("kind") == "zip":
        marker = task.get("filename") or ""
        prefix = (task.get("name_prefix") or "").lower()
        problem = None
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = [n.replace("\\", "/") for n in z.namelist()]
                if marker:
                    hits = [n for n in names if n.endswith(marker)]
                else:
                    hits = [n for n in names
                            if os.path.basename(n).lower().startswith(prefix)]
                if not hits:
                    problem = "压缩包内找不到 %s" % (marker or prefix + "*")
                else:
                    with z.open(hits[0]) as fp:
                        head = fp.read(2)
                    if marker and head != b"MZ":
                        problem = "%s 不是可执行文件（PE 头缺失）" % marker
        else:
            problem = "不是有效的 zip 文件"
        if problem:
            fail(problem)
    elif task.get("kind") == "model":
        try:
            with open(path, "rb") as f:
                head = f.read(8)
        except OSError as e:
            fail("文件读不到：%s" % e)
        if head[:4] != b"GGUF":
            fail("不是 GGUF 模型（magic=%r）" % head[:4])
        version = int.from_bytes(head[4:8], "little")
        if version not in (1, 2, 3):
            fail("GGUF 版本号异常：%d" % version)
        official_size = int(task.get("size_bytes") or 0)
        if official_size and size != official_size:
            fail("体积与官方记录不一致（官方 %d B，实际 %d B）"
                 % (official_size, size))
        hint = float(task.get("size_hint") or 0)
        if hint and not (0.5 * hint <= size / float(1 << 30) <= 2.5 * hint):
            log("  注意：%s 体积 %.2f GB 与预期 %.2f GB 差异较大，"
                "请确认下载的是同一版本" % (os.path.basename(path),
                                    size / float(1 << 30), hint), "warn")

    try:
        digest = sha256_file(path)
    except OSError as e:
        fail("计算 sha256 失败：%s" % e)
    key = task.get("filename") or os.path.basename(path)
    prev = (known_hashes or {}).get(key)
    official = (task.get("sha256") or "").strip().lower()
    if official:
        if digest != official:
            fail("官方 SHA-256 不匹配（期望 %s，实际 %s）"
                 % (official, digest))
        if prev and prev != digest:
            log("  配置里的旧哈希与官方不一致，已按官方值更新", "warn")
        log("  官方 SHA-256 校验通过：%s · %s…"
            % (os.path.basename(path), digest[:16]))
    else:
        if prev and prev != digest:
            fail("与上次记录的哈希不一致（上次 %s…，本次 %s…）"
                 % (prev[:12], digest[:12]))
        log("  未登记官方哈希：仅做结构校验与跨次一致性", "warn")
    return digest



class LlamaServer(object):
    def __init__(self, exe, model, variant, log, ctx_size=8192, tier="",
                 gpu_layers=99, parallel=1):
        self.exe = exe
        self.model = model
        self.variant = variant
        self.log = log
        self.ctx_size = int(ctx_size)
        self.parallel = max(1, min(4, int(parallel)))
        self.tier = tier
        self.gpu_layers = 0 if variant == "cpu" else int(gpu_layers)
        self.proc = None
        self.port = None
        self.base = None
        self._atexit = False
        self.log_file = None
        self._log_fp = None
        self.gpu_verified = False
        self.health_warn = ""
        self.prompt_extra = ""

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def _verify_gpu(self):
        """从运行日志核实 GPU 是否真的在工作（offload 层数 / 后端名）。"""
        if self.variant == "cpu" or not self.log_file:
            return True
        try:
            with open(self.log_file, "rb") as f:
                f.seek(max(0, os.path.getsize(self.log_file) - 1048576))
                tail = f.read().decode("utf-8", "ignore")
        except OSError:
            return False
        low = tail.lower()
        self.gpu_offloaded = None
        on_gpu = total = None
        m = re.search(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+gpu", low)
        if m:
            on_gpu, total = int(m.group(1)), int(m.group(2))
        else:
            dev_pat = {"cuda": r"assigned to device cuda\d+",
                       "cuda13": r"assigned to device cuda\d+",
                       "vulkan": r"assigned to device vulkan"}.get(self.variant)
            if dev_pat:
                on_gpu = len(re.findall(dev_pat, low))
                total = on_gpu + len(re.findall(
                    r"assigned to device cpu", low))
        dev_ok = False
        if self.variant in ("cuda", "cuda13"):
            dev_ok = ("using device cuda" in low
                      or "assigned to device cuda" in low)
        elif self.variant == "vulkan":
            dev_ok = ("vulkan" in low and "device" in low)
        self.gpu_verified = dev_ok and (on_gpu or 0) > 0
        if self.gpu_verified:
            if total:
                self.gpu_offloaded = (on_gpu, total)
            self.log("GPU 加速已生效（%s：%s 层在显卡上）"
                     % (self.variant,
                        ("%d/%d" % (on_gpu, total)) if total else str(on_gpu)))
        else:
            self.log("警告：选择了 %s 后端，但未确认层已上 GPU；"
                     "若推理仍很慢，通常是缺 CUDA 运行时或驱动问题，"
                     "请改选 CPU/Vulkan 或在下载管理补装运行库"
                     % self.variant, "warn")
        return self.gpu_verified

    def _pick_port(self):
        """取一个本机空闲端口。

        做法是"绑定 0 → 读回端口 → 立刻关闭"，随后交由子进程监听，两步之间
        存在端口被别的进程抢走的窗口（TOCTOU）。这里不做复杂规避，而是让
        start() 在子进程提前退出时换端口重试一次 —— 比信号量/句柄继承方案
        简单且足够。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
        finally:
            s.close()

    def start(self, ready_event_log=None, timeout_sec=900):
        """启动引擎并等 /health 就绪。

        失败路径**必须**把已拉起的子进程收掉：否则健康检查超时/提前退出后
        会留下一个占着显存和内存的孤儿 llama-server，"再点一次启动"就变成
        两个进程抢显存。端口被抢占属可恢复失败，换端口重试一次。
        """
        if self.is_running():
            return True
        last_err = None
        for attempt in (1, 2):
            try:
                return self._start_once(timeout_sec, attempt)
            except IOError as e:
                last_err = e
                self._kill_proc()
                if attempt == 1 and "提前退出" in str(e):
                    self.log("端口可能被占用，换端口重试一次…", "warn")
                    continue
                raise
        raise last_err

    def _start_once(self, timeout_sec, attempt):
        self.port = self._pick_port()
        self.base = "http://127.0.0.1:%d" % self.port

        meta = read_gguf_meta(self.model)
        if meta:
            name = meta.get("general.name") or os.path.basename(self.model)
            if meta.get("__partial__"):
                self.log("模型元数据未完整读出（%s），跳过对话模板检查" % name,
                         "warn")
            elif not meta.get("tokenizer.chat_template"):
                self.log("警告：该 GGUF 未内置对话模板（tokenizer.chat_template），"
                         "翻译请求会退化为纯文本续写，可能出现停不下来/串话；"
                         "建议改用官方发布的 GGUF", "warn")
            else:
                self.log("模型 %s：内置对话模板 %d 字符，EOS=%s"
                         % (name, len(meta["tokenizer.chat_template"]),
                            meta.get("tokenizer.ggml.eos_token_id")), "dim")

        args = [self.exe,
                "--model", self.model,
                "--host", "127.0.0.1",
                "--port", str(self.port),
                "--ctx-size", str(self.ctx_size),
                "--n-gpu-layers", str(self.gpu_layers),
                # 翻译请求是串行的：1 个槽位足够。新版 llama-server 的
                # auto 会给 4 个槽位（每槽一套 16K 上下文），白占显存/内存。
                "--parallel", str(self.parallel),
                "--temp", "0.1"]
        if self.variant in GPU_VARIANTS:
            # 只要 GPU 识别所需的 I 级行（using device / offloaded N/M layers）。
            # 原来的 --verbose 把 verbosity 开到最大：实测一次运行 25 MB / 28 万行，
            # 请求级 debug 还持续写盘，拖慢整机。
            args += ["-lv", "4"]
        log_file = os.path.join(_LOGS_DIR, "server_%d.log" % int(time.time()))
        self.log_file = log_file
        self._log_fp = open(log_file, "wb")
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        backend_name = {"cpu": "CPU", "cuda": "CUDA 12.4/GPU",
                        "cuda13": "CUDA 13.4/GPU",
                        "vulkan": "Vulkan/GPU"}.get(self.variant, self.variant)
        self.log("启动 llama-server（%s，%s 档，%d 层 GPU offload，"
                 "上下文 %d token），端口 %d"
                 % (backend_name, self.tier or "-", self.gpu_layers,
                    self.ctx_size, self.port))
        self.log("运行日志：%s" % log_file)
        self.proc = subprocess.Popen(
            args, cwd=os.path.dirname(self.exe), stdout=self._log_fp,
            stderr=subprocess.STDOUT, creationflags=flags)
        # 把引擎放进 kill-on-close 作业：工具集被任务管理器强杀时，
        # 内核会连带终止它，不再留下占着显存的孤儿（见 _win 注释）。
        if os.name == "nt" and not _assign_server_to_kill_job(self.proc):
            self.log("提示：未能把引擎纳入系统作业托管；若工具集被强杀，"
                     "引擎可能残留（下次启动会自动清理）", "dim")
        if not self._atexit:
            atexit.register(self.stop)
            self._atexit = True

        t0 = time.time()
        health_url = self.base + "/health"
        last_err = ""
        while time.time() - t0 < timeout_sec:
            if self.proc.poll() is not None:
                raise IOError("llama-server 提前退出（返回码 %s），详见 %s"
                              % (self.proc.returncode, log_file))
            try:
                with urllib.request.urlopen(health_url, timeout=5) as r:
                    if r.status == 200:
                        self.log("引擎就绪（用时 %.0f 秒）" % (time.time() - t0))
                        self._verify_gpu()
                        self.self_check()
                        return True
                    try:
                        data = json.loads(r.read().decode("utf-8", "ignore"))
                        last_err = str(data.get("error", ""))[:120]
                    except Exception:
                        pass
            except Exception as e:
                last_err = str(e)
            time.sleep(1.5)
        raise IOError("等待引擎就绪超时（%s）" % (last_err or "health 无响应"))

    def translate(self, text, src_lang, tgt_lang, glossary_hits=None,
                  timeout_sec=180, max_tokens=None, use_extra=True,
                  extra_sys=None, temperature=None, context=None,
                  style=None):
        """单条翻译请求。被 max_tokens 截断时抛 OutputTruncated（不重试）。

        提示词里的语言用**全名**（"俄语"）而不是语言码（"ru"）：小模型对
        "将下面的ru文本翻译为简体中文"这类混排会偶发把 ru 当成待译内容。
        """
        if not self.is_running():
            raise IOError("引擎未运行")
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(text) or src_lang
        src = LANG_FULL.get(src_lang)
        tgt = LANG_FULL.get(tgt_lang, tgt_lang)
        sys_msg = (TRANSLATION_SYS_PROMPT_EN if src_lang == "eng"
                   else TRANSLATION_SYS_PROMPT)
        extra = (getattr(self, "prompt_extra", "") or "").strip() \
            if use_extra else ""
        if extra:
            sys_msg = sys_msg + "\n补充要求（优先遵守）：\n" + extra
        if extra_sys:
            sys_msg = sys_msg + "\n补充要求（优先遵守）：\n" + extra_sys
        if src and src_lang not in ("auto", "text"):
            user_msg = "将下面的%s文本翻译为%s：\n%s" % (src, tgt, text)
        else:
            user_msg = "将下面文本翻译为%s：\n%s" % (tgt, text)
        if context:
            user_msg = ("上下文（仅供理解，不要翻译）：" + chr(10)
                        + str(context) + chr(10) + chr(10) + user_msg)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user_msg = block + "\n\n" + user_msg
        if max_tokens is None:
            max_tokens = max(1024, int(self.ctx_size * 0.6))
        payload = json.dumps({
            "model": "hy-mt2",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": (0.1 if temperature is None
                            else float(temperature)),
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        last_err = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.base + "/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": UA},
                    method="POST")
                with urllib.request.urlopen(req, timeout=timeout_sec) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(1.0)
                continue
            choices = data.get("choices") or []
            if not choices:
                last_err = "响应缺少 choices"
                time.sleep(1.0)
                continue
            choice = choices[0]
            out = (choice.get("message") or {}).get("content", "").strip()
            if not out:
                last_err = "空输出"
                time.sleep(1.0)
                continue
            if choice.get("finish_reason") == "length":
                raise OutputTruncated(
                    "输出达到 max_tokens(%d) 被截断" % max_tokens)
            return out
        raise IOError("翻译请求失败：%s" % last_err)

    def self_check(self):
        """启动自检：真跑一条极短翻译，确认模型能正常收尾。

        实测背景：tokenizer 元数据不完整的 GGUF 永远不吐 EOS，一句 "Hello."
        能连生成上千 token 直到请求超时；真拿它去翻几千条文本，就是全部
        超时、白跑一整轮。这里用 16 个 token 的上限提前把它试出来，并把
        原因讲清楚。

        **不阻断启动**（只告警）：避免误伤行为特殊但可用的模型；用户看到
        告警后可以自行换模型或改用其他后端。
        """
        tokenizer_warn = (
            "模型无法正常收尾（输出撞满上限仍未结束）：该 GGUF 的 tokenizer "
            "元数据可能不完整（缺 EOS/EOG 或对话模板），直接翻译会让每条"
            "文本都超时")

        def probe(max_tokens, timeout):
            return self.translate("Hello.", "eng", "chs",
                                  timeout_sec=timeout, max_tokens=max_tokens,
                                  use_extra=False)

        try:
            out = probe(16, 120)
        except OutputTruncated:
            self.health_warn = tokenizer_warn
        except Exception:
            # 16 token 的"空输出"不等于模型坏了：推理型 GGUF 会把预算全花在
            # reasoning_content 上，content 为空（MiniCPM5-2B 实测如此，
            # 256 token 时能正常返回"你好。"）。用更大预算复核一次再下结论。
            try:
                out = probe(256, 180)
            except OutputTruncated:
                self.health_warn = tokenizer_warn
            except Exception as e:
                self.health_warn = (
                    "启动自检请求失败：%s: %s（不影响启动；推理型模型常把小预算"
                    "耗在思考上而返回空内容，可忽略此告警）"
                    % (type(e).__name__, e))
            else:
                self.health_warn = ""
                self.log("启动自检通过（16 token 不足，256 token 复核成功）：%r"
                         % (out[:24] or "(空)"))
                return True
        else:
            self.health_warn = ""
            self.log("启动自检通过：%r" % (out[:24] or "(空)"))
            return True
        self.log("启动自检未通过：" + self.health_warn, "warn")
        return False

    def _kill_proc(self):
        """杀掉子进程（含其子进程树）并关闭日志句柄。可重复调用。"""
        if self.proc is not None and self.proc.poll() is None:
            pid = self.proc.pid
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
            self.proc = None
        fp, self._log_fp = self._log_fp, None
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass

    def translate_batch(self, lines, src_lang, tgt_lang, glossary_hits=None,
                        timeout_sec=180, max_tokens=None,
                        temperature=None):
        """连续对话批量翻译：输入若干源文行，输出带 [n] 编号的译文。"""
        if not self.is_running():
            raise IOError("引擎未运行")
        if src_lang in ("auto", "text"):
            src_lang = detect_text_lang(lines[0] if lines else "") or src_lang
        tgt = LANG_FULL.get(tgt_lang, tgt_lang)
        numbered = chr(10).join("[%d] %s" % (i + 1, line)
                                for i, line in enumerate(lines))
        sys_msg = TRANSLATION_BATCH_SYS_PROMPT
        extra = (getattr(self, "prompt_extra", "") or "").strip()
        if extra:
            sys_msg += chr(10) + "补充要求（优先遵守）：" + chr(10) + extra
        user_msg = ("把下面的连续对话逐行翻译为%s：" % tgt
                    + chr(10) + numbered)
        if glossary_hits:
            block, _used = glossary_hint_block(glossary_hits)
            if block:
                user_msg = block + chr(10) + chr(10) + user_msg
        if max_tokens is None:
            max_tokens = max(1024, int(self.ctx_size * 0.6))
        payload = json.dumps({
            "model": "hy-mt2",
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            "temperature": (0.1 if temperature is None
                            else float(temperature)),
            "max_tokens": max_tokens,
            "stream": False,
        }).encode("utf-8")
        last_err = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    self.base + "/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": UA},
                    method="POST")
                with urllib.request.urlopen(req, timeout=timeout_sec) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, e)
                time.sleep(1.0)
                continue
            choices = data.get("choices") or []
            if not choices:
                last_err = str(data.get("error", ""))[:120]
                time.sleep(1.0)
                continue
            choice = choices[0]
            content = ((choice.get("message") or {}).get("content") or "").strip()
            if choice.get("finish_reason") == "length":
                raise OutputTruncated("批量输出被截断")
            return content
        raise IOError("批量翻译请求失败（%s）" % (last_err or "无响应"))

    def stop(self):
        if self.proc is None and self._log_fp is None:
            return
        self._kill_proc()
        self.log("引擎已停止")



def estimate_tokens(text):
    """保守的多语言 token 估算（混元 12 万词表）：
    汉字/假名/谚文约 1 token/字；拉丁/西里尔字母文本约 0.45 token/字符，
    按 0.55 取偏安全上界；标点空白计入长度。"""
    if not text:
        return 0
    cjk = 0
    for c in text:
        o = ord(c)
        if (0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF
                or 0xAC00 <= o <= 0xD7AF):
            cjk += 1
    other = len(text) - cjk
    return int(cjk * 1.0 + other * 0.55) + 1


def safe_chunk_chars(ctx_size):
    """单次请求可送的最大源文字符数（与画像中的"约 N 字符/段"一致）：
    8192 → 约 3768；16384 → 约 7536。"""
    return int(ctx_size * 0.46)


_SENT_SPLIT = re.compile(r"([。！？!?…\n；;])")
_CLAUSE_SPLIT = re.compile(r"([，,：:])")


_PLACEHOLDER_RE = re.compile(r"\$\$[TR]\d+\$\$")
_ANY_PLACEHOLDER_RE = re.compile(r"\$\$[A-Za-z0-9_]+\$\$")

# 运行期占位符：脚本变量/格式符/控制符。**必须先 mask 再送模型**：
# 提示词要求保留并不可靠，实测模型会把 $level/$name 当成普通英文词译成
# $地点/$名称，导致脚本变量替换失效（news_data.script 等直接引用这些条目）。
_RUNTIME_TOKEN_RE = re.compile(
    r"(\$\$[A-Za-z0-9_]+\$\$"          # $$ACTION$$
    r"|\$[A-Za-z_][A-Za-z0-9_]*"        # $level / $name
    r"|%c\[[^\]]*\]"                    # %c[255,1,255,255]
    r"|%[-+ #0-9.*$]*[A-Za-z]"          # %s / %d / %1$s
    r"|\b[rsg][0-9]?_[a-z0-9_]+\b)")    # r2_details_opt / s_light 等技术后缀
# 换行/制表不再当“严格占位符”：模型少写一个 \n 就把整条退回原文，
# 实测 138 条多行描述因此漏译。送翻前换成真实换行，译后统一写回 \n。


def mask_runtime_tokens(text):
    """把运行期占位符换成 $$R0$$…，返回 (masked, mapping)。"""
    mapping = {}

    def repl(m):
        ph = "$$R%d$$" % len(mapping)
        mapping[ph] = m.group(0)
        return ph

    return _RUNTIME_TOKEN_RE.sub(repl, text), mapping


def restore_runtime_tokens(text, mapping):
    """回填占位符；任何一个不是恰好出现一次就返回 None（调用方保留原文）。"""
    for ph in mapping:
        if text.count(ph) != 1:
            return None
    for ph, tok in mapping.items():
        text = text.replace(ph, tok)
    return text


def unescape_newlines(text):
    """送翻前把字面 \\n/\\t 变成真实换行，让模型看到段落结构。"""
    return (text or "").replace("\\r\\n", "\n").replace("\\n", "\n") \
        .replace("\\t", "\t")


def escape_newlines(text):
    """译后统一写回字面 \\n/\\t，保持 X-Ray 文本表的存储约定。"""
    s = (text or "").replace("\r\n", "\n")
    return s.replace("\n", "\\n").replace("\t", "\\t")


def restore_linebreaks(text, count):
    """模型把多行合并成一行时的确定性兜底：按句末标点补回换行。

    只在输出**一个换行都没有**且句子数足够时生效；不猜测原文行内位置，
    但至少让每段独立成行，避免整段挤成一行。
    """
    if count <= 0 or not text or text.count("\n") > 0:
        return text
    parts = [p for p in re.split(r"(?<=[。！？!?…])", text) if p]
    if len(parts) < count + 1:
        # 句子不够就退到逗号/破折号，宁可断得碎一点，也不要整段挤一行。
        parts = [p for p in re.split(r"(?<=[。！？!?…；;，,、])", text) if p]
    if len(parts) < 2:
        return text
    count = min(count, len(parts) - 1)
    out = []
    for i, part in enumerate(parts[:-1]):
        out.append(part + ("\n" if i < count else ""))
    out.append(parts[-1])
    return "".join(out)


# 专名/歌名/型号保护：只按**结构**判断，不写死任何 MOD 文件名。
# 文件名的 credits/ogg/music/songs/enc_weapons 只是通用提示；用户可关闭。
# ID 引用扫描（跨 MOD 通用；只认结构，不认具体文件名）
_XML_REF_TAG_RE = re.compile(
    r"<(?:text|name|bio|title)\b[^>]*>\s*([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})\s*</",
    re.I)
_XML_REF_ATTR_RE = re.compile(
    r"\b(?:name|hint|bio|caption|descr|title)\s*=\s*[\"']"
    r"([A-Za-z0-9_][A-Za-z0-9_.\-]{0,100})[\"']",
    re.I)
_SCRIPT_TRANSLATE_RE = re.compile(
    r"translate_string\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_SCRIPT_QUOTED_RE = re.compile(r"[\"']([A-Za-z0-9_][A-Za-z0-9_.\-]{1,100})[\"']")

_PROTECT_FILE_HINTS = ("credits", "ogg", "music", "songs", "enc_weapons",
                       "snames", "fnames", "character_name")
_TITLE_LIKE_RE = re.compile(
    r"^[A-Za-z0-9][^\n:：]{0,80}[:：]\s*[A-Za-z0-9]")
_TITLE_DASH_RE = re.compile(
    r"^[A-Za-z0-9][^\n]{0,80}[—–]\s*[A-Za-z0-9]")
_MODEL_LIKE_RE = re.compile(
    r'^[A-Z0-9][A-Za-z0-9 .\-/]*["“][^"”]{1,80}["”]')
_CODE_LIKE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .\-/_]{1,30}$")
_COPYRIGHT_RE = re.compile(r"^(?:©|\(c\)|copyright\b)", re.I)
_DEBUG_LIKE_RE = re.compile(
    r"(?:onItemDrop|npcObj|FAILED\b|sect\s*-|obj\s*:|gvid\s*:|"
    r"^(?:~|\[[a-z0-9_:.\- ]+\]$)|"
    r"\b(?:teleport\w*|reinit|kamps|npcItems|isVisual\w*)\b|"
    r"content\s*:)", re.I)
_DEBUG_PHRASES = {"dead", "none", "null", "true", "false",
                  "avail end", "start here", "reinit timer"}

# 口径/型号不是“正文”：9x18 PM、5.56x45 M995 AP、12.7x99mm、.44 Magnum
# 这类短技术串必须整串保留，不能被模型当时间/自动步枪翻译。
_TECH_SPEC_RE = re.compile(
    r"^\s*(?:\d+\s*[x×]\s*)?\d+(?:[.,]\d+)?\s*[x×]\s*"
    r"\d+(?:[.,]\d+)?(?:\s*[A-Za-z0-9.+/\-]{0,12})?\s*$", re.I)
_TECH_CALIBER_RE = re.compile(
    r"^\s*\.\d{2,3}\s+[A-Za-z][A-Za-z0-9 .\-/]{0,24}$", re.I)


def looks_like_technical_text(text):
    """口径/弹药/尺寸类整串：只做技术串保护，不当普通正文。"""
    s = str(text or "").strip()
    if not s or len(s) > 60 or re.search(r"[。！？!?；;]", s):
        return False
    if _TECH_SPEC_RE.match(s) or _TECH_CALIBER_RE.match(s):
        return True
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?", s))


def looks_like_generated_noise(rel, text):
    """旧的 ___scriptE__ / ___cfgxml__ 生成表：短 ASCII 多是日志/键名。"""
    low = (rel or "").lower().replace("\\", "/")
    if "___scripte__" not in low and "___cfgxml__" not in low:
        return False
    s = str(text or "").strip()
    if not s or len(s) > 80 or re.search(r"[。！？!?；;]", s):
        return False
    if s.isascii():
        return True
    return bool(re.search(
        r"^(?:gulag|ignored|selected|actor\s+(?:start|end)|spawn|id):",
        s, re.I))


def looks_like_model_name(text):
    """纯拉丁武器/物品型号名：会话词表不应把它们当人名/普通物品翻译。"""
    s = str(text or "").strip()
    if not s or not re.search(r"[A-Za-z]", s):
        return False
    if re.search(r"\d", s) or re.search(r"[“”\"'«»/]", s):
        return True
    if re.search(r"[A-Z]{2,}", s):
        return True
    if "-" in s and len(s.split()) >= 2:
        return True
    caps = sum(1 for w in s.split() if w[:1].isupper())
    return s.isascii() and caps >= 2


def _has_translatable_text(text):
    """剔除运行期占位符后仍有 2 个以上连续字母，才值得送翻。"""
    clean = _RUNTIME_TOKEN_RE.sub("", text or "")
    return bool(re.search(r"[A-Za-zА-Яа-яЁё]{2,}", clean))


def detect_text_lang(text):
    """按字母构成判断单条源文是俄语还是英语。

    X-Ray 的 `<rus>` 字段在不少 MOD 里实际放的是英语；只按字段名选源语言
    会把英语提示词成俄语。这里只做粗判：西里尔字母占优→rus，拉丁占优→eng；
    混合专名/缩写不改变主语言；太短（<2 个字母）返回空，交给文件级提示。
    """
    s = text or ""
    cyr = len(re.findall(r"[А-Яа-яЁё]", s))
    lat = len(re.findall(r"[A-Za-z]", s))
    if cyr >= 2 and cyr >= lat:
        return "rus"
    if lat >= 3 and lat > cyr:
        return "eng"
    return ""


def looks_like_code_or_debug(rel, text):
    """结构性噪声：调试日志/路径/URL/代号。**总是**跳过，不占“专名保护”。"""
    s = (text or "").strip()
    if not s or len(s) <= 1:
        return True
    if s.lower() in _DEBUG_PHRASES or _COPYRIGHT_RE.match(s):
        return True
    if _DEBUG_LIKE_RE.search(s):
        return True
    if s.lower().startswith(("http://", "https://", "www.")):
        return True
    # 短代号/弹药规格：只有“全大写且没有任何小写字母”才跳过。
    # 旧规则把“Набор 1 / Патроны 9х18мм ПМ / 30 х 5.45x39мм”都当型号，
    # 实测 459 条正文因此漏译；含小写词的串一律按正文送翻。
    name_file = any(x in (rel or "").lower() for x in (
        "character_name", "snames", "fnames", "bio"))
    zone_file = any(x in (rel or "").lower() for x in (
        "string_table_enc_zone", "string_table_levels", "mapspots"))
    if not (name_file or zone_file):
        if (any(c.isdigit() for c in s)
                and re.fullmatch(
                    r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9 .\-/_]{0,40}", s)):
            return True
        if _CODE_LIKE_RE.match(s) and any(c.isdigit() for c in s):
            return True
    return False


def looks_like_identifier_text(text):
    """纯小写 ASCII 的“段落/任务/节名”不是用户正文（如 resque information）。"""
    s = (text or "").strip()
    if not s:
        return True
    if re.fullmatch(r"[a-z0-9_.\- /]{1,90}", s) and not re.search(
            r"[.!?,;:()\[\]{}]", s):
        return True
    # 纯 ASCII、无句末标点、<=6 个词：多为任务/段落/角色/资源名
    # （Courier document / My map task / Gans Wiberg / Karta Name），
    # 不是用户正文；但「Disable Info」这类多个首字母大写的界面标签
    # 是给玩家看的，不能当标识符跳过。
    if (s.isascii() and len(s.split()) <= 6
            and not re.search(r"[.!?。！？]", s)):
        caps = sum(1 for w in s.split() if w[:1].isupper())
        if caps <= 1:
            return True
    return False


def looks_like_hard_protected_file(rel):
    """制作名单/音乐列表：连核心术语也不替换；武器短名只保护非词库型号。"""
    low = (rel or "").lower().replace("\\", "/")
    return any(x in low for x in ("credits", "ogg", "music", "songs"))


def looks_like_proper_text(rel, text):
    """专名/歌名/型号：只在用户勾选“保护专名”时跳过。"""
    s = (text or "").strip()
    if not s:
        return False
    low = (rel or "").lower().replace("\\", "/")
    # 文件级强保护：制作名单、音乐/歌曲列表整张表都不翻；人物/武器/名字表
    # 只保护短名，长描述仍交给模型（例如武器说明）。
    hint = next((h for h in _PROTECT_FILE_HINTS if h in low), "")
    if looks_like_hard_protected_file(rel):
        return True
    if hint == "enc_weapons" and len(s) <= 40 and len(s.split()) <= 6:
        return True
    if (_TITLE_LIKE_RE.match(s) or _TITLE_DASH_RE.match(s)
            or _MODEL_LIKE_RE.match(s)):
        return True
    if len(s) <= 20 and s.isupper() and any(c.isalpha() for c in s):
        return True
    return False


def looks_like_protected_text(rel, text):
    """兼容旧接口：结构性噪声或专名。"""
    return (looks_like_code_or_debug(rel, text)
            or looks_like_proper_text(rel, text))


# ----------------------------------------------------------------------
# 脚本字面文本上下文过滤（1.7.6）
#
# 上游 getScriptPotentialTexts 会把脚本里**所有**带字母的引号串都抽出来。
# 1.7.5 实测：63 个脚本、1890 处赋值、46 处 string_find、11 处 string_gsub
# 被换成裸 ID，逻辑串变成 nil。这里按“这一行在干什么”做保守白名单：
#   * log/print/debug/error/assert/trace 一律不动；
#   * string_find/string_gsub/比较运算一律不动（逻辑串）；
#   * 只改显示类调用和明确的文本变量赋值。
# 宁可漏几条界面文本，也不能把脚本逻辑改坏。
# ----------------------------------------------------------------------
_SCRIPT_LOG_CALL_RE = re.compile(
    r"\b(?:log1?|log2|log3|log_message|printf|print|debug|error|assert|"
    r"trace|console|exec)\s*\(", re.I)
_SCRIPT_LOGIC_CALL_RE = re.compile(
    r"\b(?:string_find|string_gsub|string_match|string_gmatch|"
    r"string\.find|string\.gsub|string\.match|string\.gmatch)\s*\(",
    re.I)
_SCRIPT_UI_CALL_RE = re.compile(
    r"\b(?:SetText|SetName|SetTextTip|InitStatic|InitButton|AddText|AddLine|"
    r"send_tip|hud_msg|add_spot_on_map|give_talk_message|map_add_user_spot|"
    r"show_message|show_tip|talk_message|send_message|add_news|add_tip|"
    r"message_box|set_level_changer_invitation)\s*\(", re.I)
_SCRIPT_ASSIGN_RE = re.compile(
    r"^\s*(?:local\s+)?(?:self\.|this\.|obj\.)?([A-Za-z_]\w*)\s*=\s*[^=]")
_SCRIPT_TEXT_VAR_RE = re.compile(
    r"(?:^|_)(?:text|txt|msg|message|hint|caption|title|descr|description|"
    r"name|news|label|tip)(?:_|$)", re.I)


def strip_script_comment(line):
    """去掉 -- 行注释，保留引号内的 --（如 "a--b"）。"""
    out = []
    quote = None
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in "\"'":
                quote = ch
                out.append(ch)
            elif ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


_SCRIPT_EXTRA_DISPLAY_RES = (
    re.compile(
        r"(?:set_level_changer_invitation|set_description)\s*\(\s*"
        r"([\"'])(.*?)\1", re.S),
    re.compile(
        r"AddItem\s*\(\s*game\.translate_string\s*\(\s*([\"'])(.*?)\1",
        re.S),
)
_SCRIPT_TRANSLATE_ARG_RE = re.compile(
    r"game\.translate_string\s*\(\s*([\"'])(.*?)\1", re.S)


def extra_script_display_literals(whole):
    """补上游漏掉的显示调用字面串（只收西里尔文本，避免碰 ID/代码）。"""
    out = set()
    for pattern in _SCRIPT_EXTRA_DISPLAY_RES:
        for m in pattern.finditer(whole or ""):
            inner = m.group(2)
            if re.search(r"[А-Яа-яЁё]", inner):
                out.add(m.group(1) + inner + m.group(1))
    return out


def script_translate_literal_args(whole):
    """已包在 game.translate_string(...) 里的字面串：替换时不能再套一层。"""
    return {m.group(1) + m.group(2) + m.group(1)
            for m in _SCRIPT_TRANSLATE_ARG_RE.finditer(whole or "")}


def script_literal_allowed(line, literal):
    """这一行的这个字面串是否允许改写/送翻。"""
    code = strip_script_comment(line)
    if not code.strip():
        return False
    if _SCRIPT_LOG_CALL_RE.search(code):
        return False
    if _SCRIPT_LOGIC_CALL_RE.search(code):
        return False
    # 比较/连接判断里的字符串通常是标签、档位、状态名，不能换。
    if re.search(r"==|~=|<=|>=", code):
        return False
    if _SCRIPT_UI_CALL_RE.search(code):
        return True
    m = _SCRIPT_ASSIGN_RE.match(code)
    if m and _SCRIPT_TEXT_VAR_RE.search(m.group(1)):
        return True
    return False


def script_literal_allowed_in_file(whole, cand):
    """候选串在脚本里的**每一次**出现都必须是安全上下文，才允许改写。

    任何一次出现在 log/string_find/比较里就整条跳过；全局替换无法区分
    同一字面串的不同出现位置，保守跳过比改坏逻辑好。
    """
    if len(cand) < 2 or cand[0] not in "\"'" or cand[-1] != cand[0]:
        return False
    inner = cand[1:-1]
    if not inner.strip():
        return False
    for needle in (cand, inner):
        found = False
        start = 0
        while True:
            idx = whole.find(needle, start)
            if idx < 0:
                break
            found = True
            line_start = whole.rfind("\n", 0, idx) + 1
            line_end = whole.find("\n", idx)
            if line_end < 0:
                line_end = len(whole)
            if not script_literal_allowed(whole[line_start:line_end], inner):
                return False
            start = idx + len(needle)
        if found:
            return True
    return False


def split_protected(text, max_chars, pattern):
    """按 split_long_text 切分，但**绝不切断 pattern 的匹配**。

    强制术语模式下原文被换成 $$T1$$ 这类占位符；按字符硬切若切在占位符中间，
    模型会看到半截标记，回填必然失败并退回旧的切片做法（质量下降且难排查）。
    做法：先把每个匹配替换成一个私有区单字符，切分后再还原 —— 匹配永远完整。
    """
    spans = list(pattern.finditer(text))
    if not spans or len(spans) > 5000:
        return split_long_text(text, max_chars)
    if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
        # 源文本已含私有区字符时不能用 PUA 做哨兵（会被误还原）；
        # 退回普通切分：占位符可能被切断，但 _translate_force 会校验并回退，
        # 不会产出坏译文。
        return split_long_text(text, max_chars)
    mapping = {}

    def repl(m):
        ch = chr(0xE000 + len(mapping))
        mapping[ch] = m.group(0)
        return ch

    protected = pattern.sub(repl, text)
    parts = split_long_text(protected, max_chars)
    if not mapping:
        return parts
    out = []
    for part in parts:
        for ch, orig in mapping.items():
            if ch in part:
                part = part.replace(ch, orig)
        out.append(part)
    return out


def split_long_text(text, max_chars):
    """把超长文本切成 ≤ max_chars 的段：先按句末标点（。！？!?…换行；分号），
    再按从句标点（逗号等），最后硬切。分隔符随前文保留，绝不丢字、绝不截断。"""
    if len(text) <= max_chars:
        return [text]

    def split_keep(pattern, s):
        """按捕获组切分，分隔符并回前一片段。"""
        parts = pattern.split(s)
        units, buf = [], ""
        for i, piece in enumerate(parts):
            buf += piece
            if i % 2 == 1:
                if buf:
                    units.append(buf)
                buf = ""
        if buf:
            units.append(buf)
        return units

    def hard_wrap(s, limit):
        return [s[i:i + limit] for i in range(0, len(s), limit)]

    units = split_keep(_SENT_SPLIT, text)
    fine = []
    for u in units:
        if len(u) <= max_chars:
            fine.append(u)
            continue
        for clause in split_keep(_CLAUSE_SPLIT, u):
            fine.extend(hard_wrap(clause, max_chars))

    chunks, cur = [], ""
    for u in fine:
        if len(cur) + len(u) <= max_chars:
            cur += u
        else:
            if cur:
                chunks.append(cur)
            cur = u
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


_TAG_TEXT = re.compile(
    r"<(?:text|rus|eng|ukr|ger|fra)\b[^>]*>(.*?)</(?:text|rus|eng|ukr|ger|fra)>",
    re.I | re.S)

_SCAN_KIND_MAP = {"text": "text", "cfg": "cfgxml", "ltx": "ltx",
                  "script": "scriptL"}


def _iter_candidate_strings(txt, kind):
    """按文件类型粗提"可能被翻译的字符串"，只用于规模估计与分档推荐。"""
    if kind in ("ltx", "cfgxml", "scriptL", "scriptE"):
        for m in re.finditer(r'"([^"\n]{2,})"', txt):
            s = m.group(1).strip()
            if s:
                yield s
    else:
        for m in _TAG_TEXT.finditer(txt):
            s = re.sub(r"\s+", " ", m.group(1)).strip()
            if s:
                yield s


def classify_xray_file(full, rel):
    """判定 X-Ray 文件类型：'text' / 'cfg' / 'ltx' / 'script' / None。

    勘察（扫描选档）与执行（真正翻哪些文件）**必须共用这一处判定**，
    否则"扫描说有 3000 条"和"实际翻了哪些文件"不是同一批 —— 旧实现两条
    口径各写一套（勘察只看路径里有没有 text 目录，执行还会嗅探 <string>
    头），统计与行为会分歧。
    """
    low = full.lower()
    parts = [x.lower() for x in rel.replace("\\", "/").split("/")]
    if low.endswith(".ltx"):
        return "ltx"
    if low.endswith(".script"):
        return "script"
    if low.endswith(".xml"):
        if "text" in parts[:-1]:
            return "text"
        try:
            with open(full, "rb") as f:
                head = f.read(65536).lower()
        except OSError:
            return "cfg"
        if b"<string" in head and b"id=" in head and (
                b"<text" in head or b"<rus" in head
                or b"<eng" in head or b"<ukr" in head):
            return "text"
        return "cfg"
    return None


def analyze_input_dir(in_dir, mode="text", log=None, cancel=None,
                      max_files=200000):
    """勘察待翻译目录：文件数 / 体积 / 最大文件 / 最长单条文本，
    并按内置档位给出超长条数与推荐档位。只读，不修改任何文件。

    要逐文件读内容才能统计"最长可译文本"，所以超大目录会明显耗时：
      * 每 500 个文件写一条进度，用户知道它在动；
      * 超过 max_files 停止统计并在结果里标 truncated（不再默默卡住）；
      * cancel 为 threading.Event 时支持中途取消。
    长度统计只保留"直方图 + 最长项"，不再把所有 (长度, 文件名) 元组堆在
    内存里（数万条文本就是几十万个元组）。
    """
    suffixes = {
        "text": (".xml",), "cfgxml": (".xml",),
        "ltx": (".ltx",), "scriptL": (".script",), "scriptE": (".script",),
        "auto": (".xml", ".ltx", ".script"),
    }
    want = suffixes.get(mode, (".xml",))
    n_files = 0
    total_bytes = 0
    max_file = (0, "")
    hist = {}
    n_strings = 0
    max_str = 0
    max_str_file = ""
    truncated = False
    cancelled = False
    for root0, dirs, files in os.walk(in_dir):
        dirs[:] = [d for d in dirs
                   if d not in ("translated_hy_mt2", "__temp__")
                   and not d.endswith(".git")]
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        parent_name = os.path.basename(root0).lower()
        if mode == "cfgxml" and parent_name == "text":
            continue
        for fn in files:
            if not fn.lower().endswith(want):
                continue
            full = os.path.join(root0, fn)
            rel = os.path.relpath(full, in_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > 20 * 1024 * 1024:
                continue
            n_files += 1
            total_bytes += size
            if size > max_file[0]:
                max_file = (size, full)
            if n_files > max_files:
                truncated = True
                break
            try:
                with open(full, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            txt = None
            for enc in encoding_chain(raw):
                try:
                    txt = raw.decode(enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if txt is None:
                continue
            kind = mode
            if mode == "auto":
                kind = _SCAN_KIND_MAP.get(
                    classify_xray_file(full, rel), "cfgxml")
            for s in _iter_candidate_strings(txt, kind):
                length = len(s)
                hist[length] = hist.get(length, 0) + 1
                n_strings += 1
                if length > max_str:
                    max_str, max_str_file = length, full
            if log and n_files % 500 == 0:
                log("  勘察中…已读 %d 个文件" % n_files)
        if truncated:
            if log:
                log("  文件数超过 %d，勘察提前结束（统计口径见结果说明）"
                    % max_files)
            break
    per_tier = {}
    for t in BUILTIN_TIERS:
        cap = safe_chunk_chars(TIER_PROFILES[t]["ctx"])
        per_tier[t] = sum(c for L, c in hist.items() if L > cap)
    if max_str > safe_chunk_chars(TIER_PROFILES["mt2"]["ctx"]):
        recommend = "7b"
    else:
        recommend = "mt2"
    return {
        "files": n_files,
        "bytes": total_bytes,
        "max_file": max_file,
        "max_str": max_str,
        "max_str_file": max_str_file,
        "n_strings": n_strings,
        "overlong": per_tier,
        "recommend": recommend,
        "truncated": truncated,
        "cancelled": cancelled,
    }


def decide_ctx(tier, info=None, cap=None):
    """按实测文本长度自动设定服务上下文：
    够短就用 4096 省内存/提速；按需要升档，但不超过该档上限。
    cap 可显式传入（自选档用用户设置）；否则取画像中的档位窗。

    自选档（custom）例外：上下文是用户自己选的，这里**不做收缩** ——
    用户选 8192 却被自动降到 4096，等于工具替用户改了他明确指定过的参数。
    """
    if cap is None:
        cap = TIER_PROFILES[tier]["ctx"]
    if tier == "custom":
        return cap
    if not info or not info.get("max_str"):
        return cap
    need_tokens = int(estimate_tokens("x" * info["max_str"]) * 2.2) + 512
    for ctx in (4096, 8192, 16384, 32768):
        if ctx >= cap:
            return cap
        if need_tokens <= ctx:
            return min(ctx, cap)
    return cap



def _is_word_char(ch):
    """术语两侧的"词字符"判定：西里尔/拉丁按词边界锚定，CJK 视为边界。

    为什么要区分（原实现只用正则 \\w，两者都没分开）：
      * 西里尔/拉丁文字有词的概念，术语两侧必须锚定词边界，否则
        "No" 会误伤 "Now"、"Кот" 会误伤 "Который"；
      * 中文没有词间空格，而 Python 的 \\w 把汉字也算词字符 —— 于是术语表里
        任何中文条目在中文文本里都会因为"左右都是词字符"而永不命中。
        把 CJK 当边界即可命中（如术语「神器」在「一个神器装备」中）。
    """
    if not (ch.isalnum() or ch == "_"):
        return False
    o = ord(ch)
    if (0x3400 <= o <= 0x9FFF
            or 0x3040 <= o <= 0x30FF
            or 0xAC00 <= o <= 0xD7AF):
        return False
    return True


class GlossaryIndex(object):
    """术语匹配索引：首字符分桶 + 同位置长词优先 + 非重叠单向扫描。

    为什么不用 "|".join(...) 的巨型交替正则：实测 8208 条术语拼出的
    204 KB 正则单条 150 字符文本要 43.70 ms，而同样的数据用朴素子串
    扫描只要 1.22 ms（慢 36 倍）；两万条量级的任务上就是几十分钟纯 CPU
    空转，GPU 全程在等。分桶后每个字符只与"以该字符开头"的词条比较，
    比较次数降两个数量级，匹配语义与原正则逐字符一致（有等价性属性测试）。

    v2 增补：
    * `variants`：拼写变体映射到规范词条，避免 ё/е 两个译名打架；
    * `case_sensitive`：人物名/技术缩写/小写单单词拉丁硬词要求首字母
      大小写一致（大写专名仍命中，句子里的普通名词不再误伤）；
    * 查找时统一做 ё→е 折叠，俄文里漏写两点的名字也能命中规范词条。
    """

    def __init__(self, mapping, case_sensitive=None, variants=None):
        raw = {}
        for k, v in (mapping or {}).items():
            name = str(k)
            if name.strip():
                raw[name] = str(v)
        self._case_sensitive = {str(k) for k in (case_sensitive or ())}
        var = {str(k): str(v) for k, v in (variants or {}).items()
               if str(k).strip() and str(v).strip()}

        # 变体只做别名，不单独成为规范条目：否则硬词过滤会拿到变体键，
        # 把“当前 MOD 专名”误判成软词。
        self._map = {}
        # 规范词条自身的译文优先；变体只在规范词条缺席时补位。
        for key, value in raw.items():
            if key not in var or var[key] == key:
                self._map[key] = value
        for key, value in raw.items():
            canon = var.get(key, key)
            if canon in raw:
                self._map.setdefault(canon, value)
            else:
                self._map.setdefault(key, value)
        self._alias = {}
        for key in raw:
            canon = var.get(key, key)
            if canon not in self._map:
                canon = key
            self._alias.setdefault(self._fold(key), set()).add(canon)

        self._by2 = {}
        self._by1 = {}
        for key in self._map:
            kl = self._fold(key)
            if len(key) == 1:
                t = self._by1.setdefault(kl, self._new_table())
            else:
                t = self._by2.setdefault(kl[:2], self._new_table())
            L = len(key)
            t["e"].setdefault(L, set()).add(key)
            for alias in self._alias.get(kl, (key,)):
                if len(alias) != L:
                    continue
                t["c"].setdefault(L, {}).setdefault(
                    self._fold(alias), []).append(key)
        for t in list(self._by1.values()) + list(self._by2.values()):
            t["lens"] = sorted(t["e"].keys(), reverse=True)

    @staticmethod
    def _new_table():
        return {"e": {}, "c": {}, "lens": []}

    @staticmethod
    def _fold(s):
        return s.casefold().replace("ё", "е")

    def _case_ok(self, seg, key):
        """大小写敏感词条：全大写缩写必须逐字一致，其余只要求首字母一致。

        首字母一致而不是全等，是为了让 `Гришачев`/`Гришачёв`、
        `Санёк`/`Санек` 这类拼写变体仍能命中；`crystal` 与 `Crystal`
        的首字母不同，正好被挡在正文普通名词之外。
        """
        if key not in self._case_sensitive:
            return True
        if key.isupper():
            return seg == key
        if key[:1].islower():
            # 小写单单词 canon（bar/crystal/star）只认大写专名：
            # 正文里的普通名词保持原样，交给模型按语境处理。
            return bool(seg) and seg[:1].isupper()
        return bool(seg) and bool(key) and seg[0] == key[0]

    def __len__(self):
        return len(self._map)

    def _probe(self, table, text, i, low):
        """按"位置升序 + 长词优先 + 同长度精确优先"取命中，返回 key 或 None。"""
        if not table:
            return None
        n = len(text)
        for L in table["lens"]:
            end = i + L
            if end > n:
                continue
            seg = text[i:end]
            key = None
            pool = table["e"].get(L)
            if pool and seg in pool:
                key = seg
            else:
                segl = low[i:end] if low is not None else self._fold(seg)
                cand = table["c"].get(L, {}).get(segl)
                if cand:
                    cand = list(dict.fromkeys(cand))
                    exact = [k for k in cand if k[:1] == seg[:1]]
                    if exact:
                        cand = exact
                    key = max(cand, key=lambda k: sum(
                        1 for a, b in zip(seg, k) if a == b))
            if key is None:
                continue
            if not self._case_ok(seg, key):
                continue
            if i and _is_word_char(text[i - 1]):
                continue
            if end < n and _is_word_char(text[end]):
                continue
            return key
        return None

    def find(self, text):
        """返回 [(start, end, key)]：按位置升序、同位置最长优先、互不重叠。"""
        out = []
        if not text or not self._map:
            return out
        n = len(text)
        low = self._fold(text)
        if len(low) != n:
            # 极少数字符 casefold 后长度变化（如 ß→ss）：退回逐段折叠，
            # 保证位置与原文一一对应。
            low = None
        i = 0
        while i < n:
            p2 = low[i:i + 2] if low is not None else self._fold(
                text[i:i + 2])
            key = self._probe(self._by2.get(p2), text, i, low)
            if key is None and self._by1:
                key = self._probe(self._by1.get(p2[:1]), text, i, low)
            if key is None:
                i += 1
                continue
            end = i + len(key)
            out.append((i, end, key))
            i = end
        return out

    def hits(self, text):
        """命中的 (原文, 译文)，长词优先 —— 与旧 _glossary_hits 同序。"""
        pairs = [(key, self._map[key]) for _, _, key in self.find(text)]
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        return pairs



_DUP_FUNCTION_RE = re.compile(r"(有有|的的|了了|是是|在在)")


def clean_translation_output(text):
    """清掉模型常见的功能词重复（有有/的的/了了…），不动正常叠词。"""
    if not text:
        return text
    return _DUP_FUNCTION_RE.sub(lambda m: m.group(1)[0], text)

_STYLE_REPLACEMENTS = (
    ("我不得不说", "说实话"),
    ("就目前而言", "现在"),
    ("就目前来看", "现在看"),
    ("并且", "而且"),
    ("此外", "还有"),
    ("然而", "可"),
    ("因此", "所以"),
    ("进行了一次", "做了一次"),
    ("作出了", "做了"),
    ("给予了", "给了"),
)


def style_cleanup_cn(text):
    """对话专用：保守去掉翻译腔词和高频空格，不改术语、数字、代码。"""
    if not text:
        return text
    out = text
    for old, new in _STYLE_REPLACEMENTS:
        out = out.replace(old, new)
    # 中文之间的半角空格；中文与中文标点之间的空格。
    out = re.sub(r"(?<=[一-鿿])[ 	]+(?=[一-鿿])", "", out)
    out = re.sub(r"[ 	]+(?=[，。！？；：、])", "", out)
    out = re.sub(r"(?<=[，。！？；：、])[ 	]+", "", out)
    # 口语里常见的三个点统一成中文省略号（代码/占位符已在上层排除）。
    out = out.replace("...", "……")
    return out


class TranslationJob(object):
    def __init__(self, parser, server, in_dir, mode, src_lang, tgt_lang,
                 reuse_dir=None, glossary=None, output_anyway=False,
                 force_glossary=True, ctx_size=8192, out_dir=None,
                 log=None, detail=None, progress=None, cancel_event=None,
                 reference_mode=False, copy_reference=True,
                 only_referenced=False, protect_proper=True,
                 preview_reference=False, hard_glossary_keys=None,
                 translate_literals=False, auto_session_terms=False,
                 session_cache_dir=None, tm_lite=True):
        self.p = parser
        self.srv = server
        self.in_dir = in_dir
        self.mode = mode
        self.src_lang = src_lang
        self.tgt = tgt_lang
        self.reuse_dir = reuse_dir
        self.glossary = dict(glossary or {})
        term_layers = load_term_layers()
        self.gloss_names = set(term_layers["names"])
        # 百科/任务/界面/新闻只做软提示：精确命中才注入，不强制替换。
        self.gloss_soft = set(term_layers.get("reference") or ())
        self.gloss_variants = dict(term_layers["variants"])
        self.gloss_case_sensitive = set(term_layers["case_sensitive"])
        self.gloss_quote_required = set(
            term_layers.get("quote_required") or ())
        # 技术精确表只做整条命中；不进入硬词/提示层。
        for _k, _v in load_technical_exact().items():
            self.glossary.setdefault(_k, _v)
        self.output_anyway = output_anyway
        self.force_glossary = force_glossary
        self._ctx_hint = ""
        self._style_hint = "dialogue"
        self.dialogue_temp = 0.1
        self.batch_stats = {"batches": 0, "lines": 0, "failed": 0}
        self.ctx_size = int(ctx_size)
        self.safe_chars = safe_chunk_chars(self.ctx_size)
        self.long_chunks = 0
        # ID 引用层模式：文本正文在 configs/text 的条目里，XML/脚本只放 ID。
        # 这里只更新条目正文、原样复制引用层，不改写任何 <text>ID</text>。
        self.reference_mode = bool(reference_mode)
        self.copy_reference = bool(copy_reference)
        self.only_referenced = bool(only_referenced)
        self.protect_proper = bool(protect_proper)
        self.preview_reference = bool(preview_reference)
        self._table_entries = {}     # 绝对路径 -> {id: entity}
        self._id_files = {}          # id -> [绝对路径, ...]
        self._ref_counts = {}        # id -> 引用次数
        self._ref_files = set()      # 含可解析引用的 XML/脚本
        self._unresolved = {}        # id -> 次数（索引里找不到）
        self._localization = {"language": "", "files": []}
        self._lang_hint = ""
        self.log = log or (lambda m: None)
        self.detail = detail or (lambda m: None)
        self.progress = progress or (lambda d: None)
        self.cancel = cancel_event
        self.req_count = 0
        self.trans_chars = 0
        self.redundant = []
        self.failed_strings = 0
        self.empty_files = 0
        self._past = set()
        self._temp_clock = 0
        self.out_dir = out_dir or os.path.join(in_dir, "translated_hy_mt2")
        self._excluded_paths = set()
        self.gloss = self._new_gloss_index()
        # None = 兼容旧 API/测试：所有术语都可用；set = 只在这些硬词上
        # 强制/提示（UI 传入 termbase 的 canon 层 + 用户自定义层）。
        self.gloss_hard = (set(hard_glossary_keys)
                           if hard_glossary_keys is not None else None)
        self.translate_literals = bool(translate_literals)
        self.auto_session_terms = bool(auto_session_terms)
        self.session_cache_dir = session_cache_dir or os.path.join(
            _DATA_ROOT, "session_terms")
        self.session_keys = set()
        # 会话词条只做提示，不参与“整条精确替换”；否则模型生成的坏译名
        # 会绕过 protect_proper 直接写进术语表。
        self.session_only_keys = set()
        self.session_stats = {}
        self.session_requests = 0
        # TM-lite：高重复批次内精确复用；缓存只在本次任务内存里。
        self.tm_lite = bool(tm_lite)
        self.tm_stats = {"scope": "off", "total": 0, "unique": 0,
                         "ratio": 0.0, "hits": 0, "saved_chars": 0}
        self._tm_cache = {}
        self._tm_global_enabled = False
        self._tm_local_enabled = False
        # 完成账本：记录"每个输入文件的指纹 + 输出大小"，取代"输出文件存在就算
        # 译完"。旧做法在两种真实场景下会静默漏译：①游戏更新给 XML 加了新条目，
        # ②ltx/cfg 上次中断留下半截输出 —— 两者都会被"存在即跳过"永久保留。
        self._ledger_path = None
        self._ledger = {}
        self._ledger_present = False
        self._ledger_saved = 0.0

    def _log_progress(self, idx, total, rel):
        """每 50 个文件记一条 GUI 进度；逐文件明细不再进界面日志。

        实测日志：6122 个文件时逐文件 log 产生近 5000 行、局部 3000 行/秒，
        而界面每条日志整块重绘 400 行 —— Tk 主线程被占满，窗口像卡死。
        明细保留在运行日志里（detail），界面只留可读的进度。
        """
        if idx == 1 or idx == total or idx % 50 == 0:
            self.log("进度 %d/%d（%.0f%%）：%s"
                     % (idx, total, 100.0 * idx / max(total, 1), rel))

    def _cancelled(self):
        return self.cancel is not None and self.cancel.is_set()

    def _parse(self, fn, *args):
        """调用 vendor 解析器，并把它的 print 诊断转进详细日志。

        解析器会逐文件打印"用了哪个编码解码"—— 那正是编码猜错时唯一的
        线索，而 GUI 子系统下 stdout 为 None，不接过来就等于没有。

        同时记录单文件解析耗时：vendor 的 cfgxml 正则曾在一类 <texture>
        文件上灾难性回溯，一卡就是几十秒且持有 GIL（整个窗口无响应）。
        修好后仍保留这条"慢文件"证据，下次换别的语料再遇到就能直接定位。
        """
        t0 = time.time()
        with capture_stdout() as cap:
            result = fn(*args)
        elapsed = time.time() - t0
        for line in cap.lines():
            self.detail("  " + line)
        if elapsed >= 2.0:
            name = os.path.basename(str(args[0])) if args else "?"
            self.detail("  解析耗时 %.1f 秒：%s" % (elapsed, name))
            if elapsed >= 10.0:
                self.log("解析耗时异常（%.0f 秒）：%s；如再次出现请反馈该文件"
                         % (elapsed, name), "warn")
        return result

    def _warn_mojibake(self, rel, samples):
        """单字节解码结果疑似乱码时告警（不自动改判，交人工决定）。"""
        for text in samples:
            if looks_like_mojibake(text):
                self.log("⚠ %s 疑为编码误判（乱码特征）：请用「编码转换」页"
                         "统一转成 UTF-8 后再翻译，否则译文不可用" % rel, "warn")
                return

    def _read_existing(self, path):
        """递归读取目录内全部文本 XML 的已有译文（复用/断点）。"""
        reuse = {}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                full = os.path.join(root, fn)
                try:
                    ents = self.p.parse_xray_text_xml(
                        full, read_encoding_chain(full))
                except Exception as e:
                    self.detail("  复用文件跳过：%s（%s: %s）"
                                % (fn, type(e).__name__, e))
                    continue
                for ent in ents:
                    rec = self.p.getRecommendLangText(ent, self.tgt)[1]
                    if rec:
                        reuse[ent.id] = rec
        return reuse

    def _looks_like_target(self, text):
        """目标语言为中文时，汉字占**正文**比例高即认为已经是中文。

        不能把 `$level`、`%s`、`$$T1$$` 里的拉丁字母算进分母：实测
        `$level $kill_seen_z，如同一个潜行者...` 的汉字占比会被压到 0.3
        以下，已汉化条目被再次送翻，模型把变量名也译成 `$地点`。
        先剔除占位符、XML 标签，再按剩下的字母计算。
        """
        if self.tgt != "chs":
            return False
        clean = _RUNTIME_TOKEN_RE.sub("", text or "")
        clean = re.sub(r"<[^>]+>", "", clean)
        letters = [c for c in clean if c.isalpha()]
        if not letters:
            return False
        han = sum(1 for c in letters if "\u4e00" <= c <= "\u9fff")
        # 0.1 而不是 0.3：`测试Taurus Raging Bull左轮手枪` 这类“中文 + 品牌”
        # 已经是译文，0.3 会把品牌名那点拉丁字母算成“不是中文”，又翻一遍。
        return han / len(letters) > 0.1

    def _new_gloss_index(self):
        if not self.glossary:
            return None
        return GlossaryIndex(
            self.glossary, case_sensitive=self.gloss_case_sensitive,
            variants=self.gloss_variants)

    def _span_context_ok(self, text, start, end, key):
        """阵营常见词（долг/свобода/монолит）只认引号内的专名用法。

        `долг` 本义是债务/责任，`свобода` 是自由，`монолит` 是整块石料；
        只有 `"Долга"` 这种引号内的组织名才强制替换，避免把普通名词
        硬改成“纪律团/自由团/黑石”。
        """
        if key not in self.gloss_quote_required:
            return True
        return self._inside_quotes(text, start, end)

    @staticmethod
    def _inside_quotes(text, start, end):
        """词条是否位于任意引号对内，而不是只贴着引号。

        原文常见 `"Броня Долга"`、`"база Свободы"` 这种引号包住的多词
        名称：`Долга/Свободы` 不紧贴引号，但仍在引号语境里，应算专名。
        """
        for op, cl in (("«", "»"), ("“", "”"), ("‘", "’")):
            if (text.rfind(op, 0, start) > text.rfind(cl, 0, start)
                    and text.find(cl, end) != -1):
                return True
        for q in ('"', "'"):
            if text.count(q, 0, start) % 2 == 1 and text.find(q, end) != -1:
                return True
        return False

    def _glossary_hits(self, text):
        """命中的硬术语 + 人物软提示，用于提示块；普通词不注入。

        canon 层与本次 MOD 会话词在前，人物层只作末尾软提示（不强制替换）。
        人物层按首字母大小写匹配，正文里的 доктор/bar 不会误命中专名。
        """
        if self.gloss is None:
            return []
        spans = self.gloss.find(text)
        pairs = [(key, self.gloss._map[key]) for start, end, key in spans
                 if self._span_context_ok(text, start, end, key)]
        if self.gloss_hard is None:
            return pairs
        hard = [(k, v) for k, v in pairs
                if k in self.gloss_hard or k in self.session_keys]
        used = {k for k, _v in hard}
        names = [(k, v) for k, v in pairs
                 if k in self.gloss_names and k not in used]
        used |= {k for k, _v in names}
        # 软层只提示不强制；长句/过长的百科条目会挤占 240 字符预算，
        # 直接跳过，让硬词和短专名优先。
        soft = [(k, v) for k, v in pairs
                if k in self.gloss_soft and k not in used
                and len(k) <= 32]
        return hard + names + soft

    def _glossary_spans(self, text):
        """命中的硬术语 (start, end, key)，用于强制/切片。"""
        if self.gloss is None:
            return []
        spans = self.gloss.find(text)
        spans = [h for h in spans
                 if self._span_context_ok(text, h[0], h[1], h[2])]
        if self.gloss_hard is None:
            return spans
        return [h for h in spans if h[2] in self.gloss_hard]

    # ── TM-lite：高重复批次内的精确复用 ────────────────────────────
    @staticmethod
    def _tm_norm(text):
        """归一化只做空白/换行整理，保留大小写与标点——精确复用不猜语义。"""
        s = unescape_newlines(str(text or "")).strip()
        return re.sub(r"[ \t]+", " ", s)

    def _tm_key(self, text, lang):
        src = self._tm_norm(text)
        sig = (tuple(sorted(_RUNTIME_TOKEN_RE.findall(src))),
               src.count("\n"))
        return (str(lang), len(src), sig, src)

    def _tm_measure(self, values):
        seen = set()
        total = 0
        for value in values:
            src = self._tm_norm(value)
            if len(src) < 4:
                continue
            total += 1
            seen.add(src)
        unique = len(seen)
        return {"total": total, "unique": unique,
                "ratio": (1.0 - float(unique) / total) if total else 0.0}

    def _tm_should_enable(self, stats):
        total = int(stats.get("total") or 0)
        unique = int(stats.get("unique") or 0)
        ratio = stats.get("ratio")
        if ratio is None:
            ratio = (1.0 - float(unique) / total) if total else 0.0
        return (self.tm_lite and total >= TM_LITE_MIN_ENTRIES
                and ratio >= TM_LITE_MIN_RATIO)

    def _tm_enable_global(self, values):
        """任务级门控：重复率高才跨表复用；否则完全走原路径。"""
        if not self.tm_lite:
            return False
        stats = self._tm_measure(values)
        self.tm_stats.update(stats)
        if self._tm_should_enable(stats):
            self._tm_global_enabled = True
            self.tm_stats["scope"] = "global"
            self.log("TM-lite：本批重复率 %.1f%%（%d/%d），启用跨表精确复用"
                     % (stats["ratio"] * 100, stats["unique"],
                        stats["total"]), "dim")
            return True
        self.log("TM-lite：未启用（重复率 %.1f%%，门槛 %.0f%%；"
                 "不改变翻译路径）"
                 % (stats["ratio"] * 100, TM_LITE_MIN_RATIO * 100), "dim")
        return False

    def _tm_enable_table(self, values, rel):
        """表级门控：任务级没启用时，只在高重复表内复用。"""
        self._tm_local_enabled = False
        if not self.tm_lite or self._tm_global_enabled:
            return
        stats = self._tm_measure(values)
        if not self._tm_should_enable(stats):
            return
        self._tm_local_enabled = True
        self._tm_cache = {}
        self.tm_stats.update(stats)
        self.tm_stats["scope"] = "table"
        self.log("TM-lite：%s 重复率 %.1f%%（%d/%d），表内精确复用"
                 % (rel, stats["ratio"] * 100, stats["unique"],
                    stats["total"]), "dim")

    def _tm_lookup(self, text, lang):
        if not (self._tm_global_enabled or self._tm_local_enabled):
            return None
        return self._tm_cache.get(self._tm_key(text, lang))

    def _tm_store(self, text, lang, out):
        if not (self._tm_global_enabled or self._tm_local_enabled):
            return
        src = self._tm_norm(text)
        if len(src) < 4 or not out or out == text:
            return
        if self.tgt == "chs" and not has_cjk(out):
            return
        self._tm_cache.setdefault(self._tm_key(text, lang), out)

    def _translate_chunk(self, content, lang, hints, depth=0, note=""):
        """翻译单个句段：超出该档安全长度时按句切分逐段翻译并拼回，
        保证不超长、不截断（长文质量保护）。

        depth 用于处理"模型输出被 max_tokens 截断"：收到 OutputTruncated
        就把该段一分为二重译，最多递归 3 层（宁可多切几刀，也不能把半句
        译文写进汉化包）。"""
        if len(content) <= self.safe_chars:
            try:
                return self.srv.translate(
                    content, lang, self.tgt, glossary_hits=hints,
                    extra_sys=note or None,
                    context=getattr(self, "_ctx_hint", "") or None,
                    style=getattr(self, "_style_hint", "dialogue"),
                    temperature=self._temp_for_style())
            except OutputTruncated:
                if depth >= 3 or len(content) < 8:
                    self.log("  输出被截断且已无法再切分（%d 字符），"
                             "建议改用更大上下文档位" % len(content), "warn")
                    raise
                self.log("  输出被截断，按句再切一次重译（%d 字符）"
                         % len(content), "warn")
                return "".join(
                    self._translate_chunk(seg, lang, hints, depth + 1, note)
                    for seg in split_long_text(
                        content, max(4, len(content) // 2)))
        parts = split_protected(content, self.safe_chars, _ANY_PLACEHOLDER_RE)
        if len(parts) > 1:
            self.long_chunks += max(0, len(parts) - 1)
            self.log("  长文本 %d 字符 → 自动切为 %d 段翻译（%s 上下文窗）"
                     % (len(content), len(parts), self.ctx_size))
        return "".join(
            self._translate_chunk(seg, lang, hints, depth + 1, note)
            for seg in parts)

    def _translate_segments(self, text, lang, note=""):
        """整条翻译；占位符在 `_translate_one` 里已 mask，不能在这里按
        换行/`$$ACTION$$` 再切碎。

        旧实现调用 `cutTextWithSeparator`：它把 `\\n` 当分隔符，多行描述被
        拆成一段段单独翻译，实测 8 条藏匿点描述发了 56 次请求，行与行之间
        失去上下文。这里只保留“整条精确命中术语”的快捷路径，其余交给
        `_translate_chunk`（超长时仍按句切分，保留占位符）。
        """
        if not text.strip():
            return text
        stripped = text.strip()
        if (not self.force_glossary and stripped in self.glossary
                and stripped not in self.session_only_keys):
            self.req_count += 1
            return text.replace(stripped, self.glossary[stripped])
        self.req_count += 1
        self.trans_chars += len(text)
        hints = None if self.force_glossary else self._glossary_hits(text)
        return self._translate_chunk(text, lang, hints, note=note)

    def _temp_for_style(self):
        if self._style_hint == "label":
            return 0.0
        if self._style_hint == "formal":
            return 0.1
        return self.dialogue_temp

    def _translate_one(self, text, lang, context="", style="dialogue"):
        """翻译一个文本串；运行期占位符先 mask，译后校验并回填。

        校验失败（模型改了/丢了 `$name`、`%s`、`\n` 等）时**保留原文**，
        绝不把坏译文写进文本表。这是硬约束，不依赖提示词。
        """
        self._ctx_hint = context or ""
        self._style_hint = style or "dialogue"
        if lang in ("auto", "text"):
            lang = detect_text_lang(text) or self._lang_hint or "rus"
        elif lang in ("rus", "eng"):
            det = detect_text_lang(text)
            if det and det != lang:
                lang = det
        if lang == self.tgt:
            return text
        hit = self._tm_lookup(text, lang)
        if hit is not None:
            self.tm_stats["hits"] += 1
            self.tm_stats["saved_chars"] += len(text)
            self.detail("  TM-lite 精确复用：%s" % str(text)[:60])
            return hit
        normalized = unescape_newlines(text)
        masked, mapping = mask_runtime_tokens(normalized)
        if not mapping:
            out = self._translate_plain(normalized, lang)
        else:
            out = self._translate_plain(masked, lang)
            restored = restore_runtime_tokens(out, mapping)
            if restored is None:
                # 颜色控制符（%c[...]）在带术语提示的请求里偶发被模型
                # 吞掉；先按“逐字保留控制符”重试一次，仍失败才保留原文。
                try:
                    retry_raw = self._translate_plain(
                        masked, lang, note=_PLACEHOLDER_RETRY_NOTE)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                except Exception:
                    retry = None
                if retry is None:
                    self.detail("  运行占位符未原样保留，保留原文：%s"
                                % str(text)[:60])
                    return text
                self.detail("  控制符未保留，已按逐字保留要求重试：%s"
                            % str(text)[:50])
                out = retry
            else:
                out = restored
        if looks_like_prompt_echo(out):
            self.detail("  模型回显提示词，保留原文：%s" % str(text)[:60])
            return text
        # 残留源词确定性替换：模型把专名原样保留时，直接按词库换掉；
        # 不调用模型，也不改动句子其它部分。替换后再看是否还残留俄文。
        out = self._repair_residual_source_terms(normalized, out)
        out = self._repair_known_semantic_terms(normalized, out, lang)
        if self._style_hint == "dialogue" and has_cjk(out):
            out = style_cleanup_cn(out)
        # 多行保真：模型偶尔把整段合并成一行；带明确数量再试一次。
        source_nl = normalized.count("\n")
        if source_nl and out.count("\n") != source_nl:
            note = ("原文有 %d 个换行，必须输出 %d 个换行，逐行对应；"
                    "不得合并段落，也不得多加空行。" % (source_nl, source_nl))
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry) \
                    and retry.count("\n") == source_nl:
                self.detail("  换行数不符，已按原文段数重试：%s"
                            % str(text)[:50])
                out = retry
            else:
                fixed = restore_linebreaks(out, source_nl)
                if fixed != out:
                    self.detail("  换行数不符（%d→%d），按句末标点补回"
                                % (source_nl, out.count("\n")))
                    out = fixed
                else:
                    self.detail("  换行数与原文不一致（%d→%d），保留首次译文"
                                % (source_nl, out.count("\n")))
        # 粗口保真闸门：原文有脏话/辱骂/威胁，译文却完全软化时，带
        # “保留同等力度”的补充要求重试一次；仍软化则保留第一次结果。
        if has_source_profanity(normalized) and not has_zh_profanity(out):
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=_PROFANITY_RETRY_NOTE)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=_PROFANITY_RETRY_NOTE)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry) \
                    and has_zh_profanity(retry):
                self.detail("  粗口被软化，已按原文力度重试：%s"
                            % str(text)[:50])
                out = retry
        # 完整性闸门：源文有可译内容，模型却原样回显/输出英文时重试一次。
        # 不处理纯型号（FX / ППШ-41）和无空格缩写，避免无意义请求。
        if (not has_cjk(out) and not has_cjk(text)
                and (re.search(r"[a-zа-яё]{2,}", text)
                     or " " in text.strip())):
            note = ("必须译成简体中文，不要保留英文或俄文原文；"
                    "人名音译，物品/任务按上下文意译。")
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry) \
                    and has_cjk(retry):
                self.detail("  译文无中文，已按完整性要求重试：%s"
                            % str(text)[:50])
                out = retry
        # 残留俄文闸门：中文译文里还夹着西里尔词，通常是引号内的格变专名
        # 被模型当成不可译名原样保留。带“引号内也要译”要求重试一次；
        # 仍残留则保留首次译文，不强行音译未知专名。
        residual = _CYR_WORD_RE.findall(out)
        if has_cjk(out) and residual:
            note = _RESIDUAL_CYRILLIC_NOTE % "、".join(
                list(dict.fromkeys(residual))[:3])
            try:
                if mapping:
                    retry_raw = self._translate_plain(
                        masked, lang, note=note)
                    retry = restore_runtime_tokens(retry_raw, mapping)
                else:
                    retry = self._translate_plain(
                        normalized, lang, note=note)
            except Exception:
                retry = None
            if retry and not looks_like_prompt_echo(retry) \
                    and has_cjk(retry) and not _CYR_WORD_RE.search(retry):
                self.detail("  译文残留俄文，已按引号内也译重试：%s"
                            % str(text)[:50])
                out = retry
        final = escape_newlines(out)
        self._tm_store(normalized, lang, final)
        return final

    def _translate_plain(self, text, lang, note=""):
        """翻译一个文本串；强制术语模式下整体翻译后回填指定译名。"""
        if lang == self.tgt:
            return text
        if not self.glossary:
            return self._translate_segments(text, lang, note=note)
        if self.force_glossary and self.gloss is not None:
            return self._translate_force(text, lang, note=note)
        return self._translate_segments(text, lang, note=note)

    @staticmethod
    def _inline_term_value(text, start, end, value):
        """源文本身在引号内时，去掉词条目标的外层引号，避免““纪律团””。"""
        v = str(value)
        quotes = "\"'«»“”‘’"
        if (start > 0 and end < len(text)
                and text[start - 1] in quotes and text[end] in quotes
                and len(v) >= 2 and v[0] in quotes and v[-1] in quotes):
            return v[1:-1]
        return v

    def _translate_sliced(self, text, lang, note=""):
        """旧的强制术语做法：把术语之间的片段**各自**送翻，术语直接拼接。

        保留是为了兜底（占位符法校验不过时回退到这里）。实测这条路径在
        一句含多个术语时句子质量差：片段之间没有上下文，会产出
        「去那里西德洛维奇，拿去吧战利品，然后——神器并且进入区域。」这种
        拼接感很强的译文，引号与标点也会被打散。
        """
        out = []
        last = 0
        for start, end, key in self._glossary_spans(text):
            if start > last:
                out.append(self._translate_segments(
                    text[last:start], lang, note=note))
            out.append(self._inline_term_value(
                text, start, end, self.glossary[key]))
            last = end
        if last < len(text):
            out.append(self._translate_segments(
                text[last:], lang, note=note))
        return "".join(out) if out else text

    def _repair_residual_source_terms(self, src, out):
        """A 译文里仍原样留着源文术语时，按词库直接替换，不调用模型。

        只处理 hard canon 且通过引号/大小写策略的 span；替换不改动其它
        文字、标点或换行。比整条重译零风格风险，专门兜住 `"Автомобилист"`
        这类模型当成专名保留的情况。
        """
        if not src or not out or self.gloss is None:
            return out
        spans = self._glossary_spans(src)
        if not spans:
            return out
        quotes = "\"'«»“”‘’"
        for start, end, key in spans:
            term = src[start:end]
            value = self.glossary.get(key)
            if not term or value is None:
                continue
            # 只处理真正的俄文词；拉丁型号（damper.sys、AK-74）留在代码/文件名里，
            # 不能因为词库里有 damper→阻尼器 就把 damper.sys 改成阻尼器.sys。
            if not _CYR_WORD_RE.search(term):
                continue
            if re.search(r"[\\/]", term) or re.search(
                    r"\.[A-Za-z0-9]{1,5}$", term):
                continue
            v = str(value)
            if (self._inside_quotes(src, start, end) and len(v) >= 2
                    and v[0] in quotes and v[-1] in quotes):
                v = v[1:-1]
            if not v or v == term or term not in out:
                continue
            pattern = re.compile(
                r"(?<![А-Яа-яЁё])%s(?![А-Яа-яЁё])" % re.escape(term))
            out2 = pattern.sub(lambda _m: v, out)
            if out2 != out:
                self.detail("  残留源词按词库替换：%s → %s"
                            % (term, v))
                out = out2
        return out

    def _repair_known_semantic_terms(self, src, out, lang=""):
        """英文源里两个已知语义错译的定点修复。

        Controller 在 STALKER 里是脑波怪，不是“控制器”；Freedom 是
        自由团，不是“自由派”。只在英文源、且源文确实出现该词时修，
        不改中文里正常的“控制器/自由”用法。
        """
        if not src or not out or lang != "eng":
            return out
        low = src.lower()
        if re.search(r"(?<![A-Za-z])controllers?(?![A-Za-z])", low):
            out = re.sub(r"控制者|控制器", "脑波怪", out)
        if re.search(r"(?<![A-Za-z])Freedom(?![A-Za-z])", src) or "freedom faction" in low:
            out = re.sub(r"自由派(?:团体|势力|派别|阵营|组织)?",
                         "“自由团”", out)
        return out

    def _translate_force(self, text, lang, note=""):
        """强制术语：先把术语换成占位符，**整串一次翻译**，再把占位符回填成
        词库指定译名。

        为什么不用旧做法：旧做法按术语切片，术语之间的每段各自翻译 —— 一句里
        出现 3 个术语就是 4 次请求，片段之间没有上下文，译文会有拼接感、标点与
        引号被打散（用户实测「非常差」）。改成占位符后整串只翻一次，语序、
        标点、语气完整；占位符由系统提示词第 1 条要求逐字保留（两档模型实测
        都能守住），最后回填，术语仍然是 100% 确定。
        校验不过（占位符丢失/控制符被改）时自动回退旧做法，不会产出坏译文。
        """
        hits = self._glossary_spans(text)
        if not hits:
            return self._translate_segments(text, lang)

        mapping = {}
        parts = []
        last = 0
        for i, (start, end, key) in enumerate(hits, 1):
            ph = "$$T%d$$" % i
            mapping[ph] = self._inline_term_value(
                text, start, end, self.glossary[key])
            parts.append(text[last:start])
            parts.append(ph)
            last = end
        parts.append(text[last:])
        masked = "".join(parts)

        hard = [pc["content"] for pc in self.p.cutTextWithSeparator(text)
                if not pc["needTrans"] and pc["content"].strip()]

        try:
            out = self._translate_masked(masked, lang, note=note)
        except Exception as e:
            self.detail("  占位符整译失败（%s: %s），回退到切片方式"
                        % (type(e).__name__, e))
            return self._translate_sliced(text, lang, note=note)

        bad = [ph for ph in mapping if out.count(ph) != 1]
        if bad:
            self.detail("  占位符未原样保留（%s），回退到切片方式"
                        % "、".join(bad[:3]))
            return self._translate_sliced(text, lang, note=note)
        lost = [h for h in hard if h not in out]
        if lost:
            self.detail("  控制符被改动（%s），回退到切片方式"
                        % "、".join(repr(h) for h in lost[:3]))
            return self._translate_sliced(text, lang, note=note)

        for ph, val in mapping.items():
            out = out.replace(ph, val)
        return out

    def _translate_masked(self, masked, lang, note=""):
        """把已占位符化的整串交给模型翻一次（超长时按句切分，不丢占位符）。"""
        self.req_count += 1
        self.trans_chars += len(masked)
        if len(masked) <= self.safe_chars:
            return self.srv.translate(
                masked, lang, self.tgt, extra_sys=note or None,
                context=getattr(self, "_ctx_hint", "") or None,
                style=getattr(self, "_style_hint", "dialogue"),
                temperature=self._temp_for_style())
        parts = split_protected(masked, self.safe_chars, _PLACEHOLDER_RE)
        self.long_chunks += max(0, len(parts) - 1)
        self.log("  长文本 %d 字符 → 自动切为 %d 段翻译（%s 上下文窗）"
                 % (len(masked), len(parts), self.ctx_size))
        return "".join(self._translate_masked(seg, lang, note)
                       for seg in parts)


    def _fallback_done_dir(self):
        """输入目录不可写（Program Files/只读盘/游戏目录受保护）时，
        把译文落到本机可变数据目录下，按输入路径派生唯一名。"""
        safe = re.sub(r'[<>:"/\\|?*]+', "_",
                      os.path.abspath(self.in_dir)).strip("_")
        return os.path.join(_DATA_ROOT, "translated_output", safe[-120:])

    def _resolve_done_dir(self, preferred):
        """确定实际输出目录：首选可写就用；不可写自动回退并明确告知。"""
        try:
            if _writable(preferred):
                return preferred
        except Exception:
            pass
        fb = self._fallback_done_dir()
        os.makedirs(fb, exist_ok=True)
        self.log("输出目录不可写（%s），译文改存到：%s"
                 % (preferred, fb), "warn")
        return fb

    def _session_tables(self):
        """当前 MOD 的文本表（兼容 configs/text 与 config/text 两种布局）。"""
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), self.in_dir)
                low = rel.lower().replace(os.sep, "/")
                if not (low.startswith("configs/text/")
                        or low.startswith("config/text/")):
                    continue
                if "sgtat_auto_texts" in low or "/___" in low:
                    continue
                yield rel, os.path.join(root, fn)

    def _session_candidates(self):
        """抽取名字型候选；返回 {lower: (source, category, lang, rel, id)}。"""
        core = {k.lower() for k in self.glossary}
        stop = set(GLOSSARY_HINT_STOPWORDS) | {
            "нет", "да", "все", "всё", "это", "так", "там", "тут", "очень",
            "можно", "нужно", "надо", "есть", "был", "была", "были",
        }
        cand = {}
        self._session_core_hits = set()
        for rel, full in self._session_tables():
            if self._cancelled():
                return cand
            try:
                ents = self._parse(self.p.parse_xray_text_xml, full,
                                   read_encoding_chain(full))
            except Exception:
                continue
            for ent in ents:
                cat = session_category(rel, ent.id)
                if not cat:
                    continue
                lang, src = self._pick_source(ent)
                if not src or has_cjk(src):
                    continue
                bare = session_term_shape(src, cat)
                if not bare:
                    continue
                low = bare.lower()
                if low in core:
                    # 核心词已在通用库；只把它登记为“本次 MOD 见过”，
                    # 让对话里也能作为软提示，不重新翻译、不硬替换。
                    self._session_core_hits.add(bare)
                    continue
                if (low in stop
                        or looks_like_code_or_debug(rel, bare)
                        or looks_like_identifier_text(bare)):
                    continue
                if low not in cand:
                    cand[low] = (bare, cat,
                                 detect_text_lang(bare) or lang or "rus",
                                 rel, ent.id)
        order = {"人物与军衔": 0, "阵营与组织": 1, "地点与区域": 2,
                 "变异生物": 3, "物品·武器·装备·神器": 4}
        rows = sorted(cand.values(),
                      key=lambda x: (order.get(x[1], 9), len(x[0]), x[0]))
        kept = {}
        used = {k: 0 for k in _SESSION_LIMITS}
        for src, cat, lang, rel, eid in rows:
            if used.get(cat, 0) >= _SESSION_LIMITS.get(cat, 0):
                continue
            used[cat] += 1
            kept[src.lower()] = (src, cat, lang, rel, eid)
        return kept

    @staticmethod
    def _clean_term_line(line):
        s = (line or "").strip()
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", s)
        return s.strip().strip("\"'“”‘’` ").strip()

    def _translate_session_batch(self, terms, lang):
        """批量翻译名词；行数不符时返回空，交回单条重试。"""
        self.session_requests += 1
        try:
            out = self.srv.translate(
                "\n".join(terms), lang, self.tgt,
                extra_sys=_SESSION_TERM_NOTE)
        except Exception:
            return {}
        lines = [self._clean_term_line(x) for x in (out or "").splitlines()
                 if x.strip()]
        if len(lines) != len(terms):
            return {}
        result = {}
        for src, dst in zip(terms, lines):
            if (dst and dst != src and has_cjk(dst)
                    and not looks_like_prompt_echo(dst) and len(dst) <= 60):
                result[src] = dst
        return result

    def _build_session_glossary(self):
        """扫描当前 MOD，补齐名字型术语；缓存只在本机，不随包。"""
        if not self.srv or not self.srv.is_running():
            return
        cand = self._session_candidates()
        if not cand:
            self.session_keys = set(getattr(self, "_session_core_hits", set()))
            self.session_stats = {"candidates": 0, "cached": 0,
                                  "translated": 0, "failed": 0}
            return
        raw = "\n".join("%s|%s|%s" % (v[3], v[4], v[0])
                        for v in sorted(cand.values()))
        fp = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
        cache_path = os.path.join(self.session_cache_dir, fp + ".json")
        cached = {}
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            if isinstance(data.get("terms"), dict):
                cached = {str(k): str(v) for k, v in data["terms"].items()}
        except Exception:
            cached = {}
        cache_low = {k.lower(): v for k, v in cached.items()}
        translated = {}
        missing = [(v[0], v[2]) for low, v in cand.items()
                   if low not in cache_low]
        by_lang = {}
        for src, lang in missing:
            by_lang.setdefault(lang, []).append(src)
        for lang, items in by_lang.items():
            for i in range(0, len(items), 20):
                batch = items[i:i + 20]
                got = self._translate_session_batch(batch, lang)
                translated.update(got)
                for src in batch:
                    if src in got:
                        continue
                    self.session_requests += 1
                    try:
                        out = self.srv.translate(
                            src, lang, self.tgt,
                            extra_sys=_SESSION_TERM_NOTE)
                    except Exception:
                        continue
                    dst = self._clean_term_line(out)
                    if (dst and dst != src and has_cjk(dst)
                            and not looks_like_prompt_echo(dst)
                            and len(dst) <= 60):
                        translated[src] = dst
        merged = dict(cached)
        merged.update(translated)
        self.session_keys = set(merged) | set(
            getattr(self, "_session_core_hits", set()))
        self.session_only_keys = set(merged)
        for src, dst in merged.items():
            self.glossary[src] = dst
        self.gloss = self._new_gloss_index()
        try:
            os.makedirs(self.session_cache_dir, exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"_format": "ai-translate-session-terms/1",
                           "fingerprint": fp, "terms": merged},
                          f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, cache_path)
        except OSError:
            pass
        self.session_stats = {
            "candidates": len(cand),
            "cached": len(cache_low),
            "translated": len(translated),
            "failed": max(0, len(missing) - len(translated)),
        }
        self.log("会话词表：候选 %d 条，缓存 %d，新译 %d，失败 %d（本机缓存，不随包）"
                 % (self.session_stats["candidates"],
                    self.session_stats["cached"],
                    self.session_stats["translated"],
                    self.session_stats["failed"]), "dim")

    def _is_stale_empty_output(self, out_path):
        """旧版缺陷会给不含文本条目的文件生成空 XML 占位，
        这类“译文”必须能被重跑覆盖；有真实条目的输出一律保留。"""
        try:
            ents = self.p.parse_xray_text_xml(
                out_path, read_encoding_chain(out_path))
        except Exception:
            return False
        return len(ents) == 0

    def _load_ledger(self):
        try:
            with open(self._ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_ledger(self):
        if not self._ledger_path:
            return
        try:
            tmp = self._ledger_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._ledger, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self._ledger_path)
            self._ledger_saved = time.time()
        except OSError:
            pass

    def _record_done(self, rel, full, out_path):
        try:
            ist = os.stat(full)
            ost = os.stat(out_path)
        except OSError:
            return
        self._ledger[rel.replace("\\", "/")] = {
            "in_size": ist.st_size,
            "in_mtime_ns": ist.st_mtime_ns,
            "out_size": ost.st_size,
        }
        # 每文件全量重写是 O(n²)（几千文件时写放大明显）；5 秒节流，
        # 崩溃最多让最近几秒的文件下次重译（不会漏、不会错）。
        if time.time() - self._ledger_saved > 5:
            self._save_ledger()

    def _output_covers_input(self, full, out_path):
        """输出 XML 是否包含输入的全部 string id（用于旧输出版本迁移）。"""
        try:
            in_ents = self._parse(self.p.parse_xray_text_xml, full,
                                  read_encoding_chain(full))
            out_ents = self.p.parse_xray_text_xml(
                out_path, read_encoding_chain(out_path))
        except Exception:
            return False
        if not in_ents:
            # 输入里没有条目：交给 _run_text_file 走"清理空占位输出"的既有路径
            return False
        return {e.id for e in in_ents} <= {e.id for e in out_ents}

    def _should_skip(self, rel, full, out_path, kind):
        """该文件是否可以跳过（= 输入未变且输出完整）。"""
        if not os.path.isfile(out_path):
            return False
        try:
            ist = os.stat(full)
            ost = os.stat(out_path)
        except OSError:
            return False
        ent = self._ledger.get(rel.replace("\\", "/"))
        if (ent
                and ent.get("in_size") == ist.st_size
                and ent.get("in_mtime_ns") == ist.st_mtime_ns
                and ent.get("out_size") == ost.st_size):
            return True
        if self._ledger_present:
            # 有账本 = 严格模式：没记录过的输出一律重做（可能是中断残留，
            # 也可能是旧版本产物；重做一次后即入账）。
            return False
        # 首次升级到 1.6.0：旧输出没有账本。文本按"输出是否覆盖输入 id"
        # 判断（能识别缺新条目/半截 XML），其余格式退回 mtime 判断。
        if kind == "text":
            ok = self._output_covers_input(full, out_path)
        else:
            ok = ost.st_mtime >= ist.st_mtime and ost.st_size > 0
        if ok:
            self._record_done(rel, full, out_path)   # 补账，下次走严格模式
        return ok

    def _write_atomic(self, out_path, writer):
        """先写 <path>.tmp 再 os.replace：中断不会留下半截"成品"。"""
        parent = os.path.dirname(out_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = out_path + ".tmp"
        try:
            writer(tmp)
            os.replace(tmp, out_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def run(self):
        done_dir = self._resolve_done_dir(self.out_dir)
        self.out_dir = done_dir
        self.done_dir = done_dir
        self._excluded_paths = {os.path.normcase(os.path.abspath(done_dir))}
        os.makedirs(done_dir, exist_ok=True)
        self._ledger_path = os.path.join(done_dir, ".ai_translate_done.json")
        self._ledger_present = os.path.isfile(self._ledger_path)
        self._ledger = self._load_ledger()

        if self.auto_session_terms and not self.preview_reference:
            try:
                self._build_session_glossary()
                self.req_count += self.session_requests
            except Exception as e:
                self.log("会话词表生成失败，继续使用通用术语库：%s：%s"
                         % (type(e).__name__, e), "warn")

        reuse = {}
        if (self.reuse_dir and os.path.isdir(self.reuse_dir)
                and not self.reference_mode):
            reuse = self._read_existing(self.reuse_dir)
            self.log("已载入复用译文 %d 条" % len(reuse))

        try:
            if self.reference_mode and self.mode in ("auto", "text"):
                ok = self._run_reference_layer(done_dir, reuse)
            elif self.mode == "auto":
                ok = self._run_auto(done_dir, reuse)
            elif self.mode == "text":
                temp_dir = os.path.join(done_dir, "__temp__")
                os.makedirs(temp_dir, exist_ok=True)
                reuse.update(self._read_existing(temp_dir))
                targets = self._collect_text_targets()
                total = len(targets)
                self.log("待翻译文本 XML %d 个（递归子目录，已排除输出目录）"
                         % total)
                if total == 0:
                    self.log("未发现任何 .xml 文件。请把目录选到 configs"
                             "（文本 XML 在其 text\\ 与 ui\\ 子目录内）", "warn")
                skipped_empty = 0
                failed = 0
                for idx, (rel, full) in enumerate(targets, 1):
                    if self._cancelled():
                        self.log("已取消")
                        ok = False
                        break
                    out_path = os.path.join(done_dir, rel)
                    if self._should_skip(rel, full, out_path, "text"):
                        continue
                    self._log_progress(idx, total, os.path.basename(full))
                    try:
                        n_texts = self._run_text_file(
                            os.path.basename(full), full, out_path,
                            os.path.join(temp_dir, rel), reuse, idx, total)
                    except Exception as e:
                        failed += 1
                        self.log("文件处理失败，已跳过：%s（%s: %s）"
                                 % (rel, type(e).__name__, e), "err")
                        continue
                    if n_texts > 0:
                        self._record_done(rel, full, out_path)
                    if n_texts == 0:
                        skipped_empty += 1
                else:
                    ok = True
                if skipped_empty:
                    self.log("跳过 %d 个不含 <string> 文本条目的 XML"
                             % skipped_empty)
                if failed:
                    self.log("有 %d 个文件处理失败（已跳过，其余照常完成），"
                             "详见日志" % failed, "warn")
            else:
                ok = self._run_recursive(done_dir, reuse)
        finally:
            # 结束时强制落盘（节流写可能还没写过）。但**空账本不写**：
            # 用户一开跑就取消时若落一个空账本，下次会进入严格模式，
            # 把旧版本遗留的输出全部重译一遍（安全但白白耗时）。
            if not (self.reference_mode and self.preview_reference):
                if self._ledger or self._ledger_present:
                    self._save_ledger()
                removed = self._prune_empty_dirs(done_dir)
                if removed:
                    self.log("已清理 %d 个空目录" % removed)

        self.log("全部完成：请求 %d 段，%s 字符%s" %
                 (self.req_count, _fmt_size(self.trans_chars),
                  ("，超长文本自动分句 %d 次" % self.long_chunks)
                  if self.long_chunks else ""))
        if self.tm_stats.get("scope") != "off" or self.tm_stats.get("hits"):
            self.log("TM-lite：%s，精确复用 %d 条，节省 %s 字符"
                     % ("跨表" if self.tm_stats.get("scope") == "global"
                        else "表内", self.tm_stats.get("hits", 0),
                        _fmt_size(self.tm_stats.get("saved_chars", 0))),
                     "dim")
        if self.empty_files:
            self.log("有 %d 个文件未提取到可译文本（前 20 个见详细日志）"
                     % self.empty_files)
        if self.failed_strings:
            self.log("有 %d 条文本翻译失败（已保留原文，详见详细日志）"
                     % self.failed_strings, "warn")
        if self.redundant:
            self.log("以下 ID 在多个文件重复，请人工核对：%s"
                     % ", ".join(self.redundant[:50]))
        self.log("译文目录：%s" % done_dir, "ok")
        return ok

    # ── ID 引用层模式（跨 MOD 通用）────────────────────────────────
    # 补丁/模组的正文在 configs/text 的条目里，XML/脚本只放条目 ID。
    # 这里不猜新 ID、不改写引用，只把“定义该 ID 的文本表条目”译成中文；
    # 引用层文件原样复制。这样 295 个引用层文件不会漏，也不会被改坏。
    def _read_localization(self):
        info = {"language": "", "files": []}
        path = os.path.join(self.in_dir, "configs", "localization.ltx")
        if not os.path.isfile(path):
            return info
        for line in read_text_any(path).splitlines():
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lower()
            if key == "language":
                info["language"] = value.strip().lower()
            elif key == "files":
                info["files"] = [x.strip() for x in value.split(",")
                                 if x.strip()]
        return info

    def _is_excluded(self, path):
        ap = os.path.normcase(os.path.abspath(path))
        return any(ap == ex or ap.startswith(ex + os.sep)
                   for ex in self._excluded_paths)

    def _iter_text_tables(self):
        root = os.path.join(self.in_dir, "configs", "text")
        out = []
        if not os.path.isdir(root):
            return out
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs
                       if not self._is_excluded(os.path.join(cur, d))]
            for fn in files:
                full = os.path.join(cur, fn)
                if fn.lower().endswith(".xml") and not self._is_excluded(full):
                    out.append(full)
        out.sort()
        return out

    def _build_text_index(self):
        self._table_entries = {}
        self._id_files = {}
        self._localization = self._read_localization()
        self._lang_hint = (self._localization.get("language") or "").lower()
        if not self._lang_hint:
            # 没写 localization.ltx 时，从 configs/text/<lang>/ 推断
            for full in self._iter_text_tables():
                parts = full.replace("\\", "/").lower().split("/configs/text/")
                if len(parts) == 2 and "/" in parts[1]:
                    self._lang_hint = parts[1].split("/")[0]
                    break
        for full in self._iter_text_tables():
            try:
                ents = self._parse(self.p.parse_xray_text_xml, full,
                                   read_encoding_chain(full))
            except Exception as e:
                self.detail("  文本表解析失败，跳过：%s（%s）"
                            % (os.path.basename(full), e))
                continue
            table = {}
            for ent in ents:
                if ent.id:
                    table[ent.id] = ent
            self._table_entries[full] = table
            for eid in table:
                self._id_files.setdefault(eid, []).append(full)
        self.log("文本表：%d 个文件 / %d 个 ID（语言 %s，注册表 %d 张）"
                 % (len(self._table_entries), len(self._id_files),
                    self._localization.get("language") or "未声明",
                    len(self._localization.get("files") or [])))

    @staticmethod
    def _looks_like_ref_value(value):
        v = (value or "").strip()
        if not (2 <= len(v) <= 120):
            return False
        if re.search(r"\s", v):
            return False
        if v.lower().endswith((".xml", ".ltx", ".script", ".dds", ".ogg",
                               ".wav", ".txt", ".ini")):
            return False
        if "/" in v or "\\" in v or ":" in v:
            return False
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_.\-]*$", v))

    def _scan_references(self):
        refs = {}
        ref_files = set()
        unresolved = {}
        text_root = os.path.abspath(
            os.path.join(self.in_dir, "configs", "text"))
        configs_root = os.path.join(self.in_dir, "configs")

        def add_ref(value, full, count_unresolved=True):
            v = (value or "").strip()
            if not v:
                return False
            if v in self._id_files:
                refs[v] = refs.get(v, 0) + 1
                ref_files.add(full)
                return True
            if count_unresolved and self._looks_like_ref_value(v):
                unresolved[v] = unresolved.get(v, 0) + 1
            return False

        for cur, dirs, files in os.walk(configs_root):
            ap = os.path.abspath(cur)
            if ap == text_root or ap.startswith(text_root + os.sep):
                continue
            if self._is_excluded(cur):
                dirs[:] = []
                continue
            for fn in files:
                if not fn.lower().endswith(".xml"):
                    continue
                full = os.path.join(cur, fn)
                text = read_text_any(full)
                for v in _XML_REF_TAG_RE.findall(text):
                    add_ref(v, full)
                for v in _XML_REF_ATTR_RE.findall(text):
                    add_ref(v, full)

        scripts_root = os.path.join(self.in_dir, "scripts")
        if os.path.isdir(scripts_root):
            for cur, dirs, files in os.walk(scripts_root):
                if self._is_excluded(cur):
                    dirs[:] = []
                    continue
                for fn in files:
                    if not fn.lower().endswith(".script"):
                        continue
                    full = os.path.join(cur, fn)
                    text = read_text_any(full)
                    ids = list(_SCRIPT_TRANSLATE_RE.findall(text))
                    for v in _SCRIPT_QUOTED_RE.findall(text):
                        if v in self._id_files and v not in ids:
                            ids.append(v)
                    for v in ids:
                        add_ref(v, full, count_unresolved=False)

        self._ref_counts = refs
        self._ref_files = ref_files
        self._unresolved = unresolved
        self.log("引用层：%d 个 XML/脚本，%d 条引用（%d 个不同 ID）；"
                 "未解析 %d 个"
                 % (len(ref_files), sum(refs.values()), len(refs),
                    len(unresolved)))
        if unresolved:
            top = sorted(unresolved.items(), key=lambda kv: -kv[1])[:20]
            self.log("未解析引用（保留原文，前 20）：%s"
                     % "、".join("%s×%d" % (k, v) for k, v in top), "warn")
        return refs, ref_files, unresolved

    def _pick_source(self, ent):
        """按游戏语言取源文：显式源语言 → localization → rus 优先。

        STALKER 系 MOD 的主语言基本是俄语。旧逻辑用 vendor 的
        getRecommendLangText 会优先 eng，把英语意译当原文（例如
        “all right”被翻成“所有 向右”、“rest”被翻成“休息”），
        俄文原文反而没被使用。这里默认 rus 优先，只有缺 rus 才用 eng。
        """
        texts = ent.texts or {}
        if not isinstance(texts, dict):
            return self.p.getRecommendLangText(ent, self.tgt)
        order = []
        if self.src_lang in ("rus", "eng", "ukr"):
            order.append(self.src_lang)
        if self._lang_hint:
            order.append(self._lang_hint)
        order += ["rus", "text", "eng", "ukr", "tur"]
        seen = set()
        for lang in order:
            if lang in seen:
                continue
            seen.add(lang)
            value = texts.get(lang, "")
            if value and value.strip():
                return lang, value
        return self.p.getRecommendLangText(ent, self.tgt)

    def _reference_candidates(self):
        total = todo = protected = 0
        tm_values = []
        for full, table in self._table_entries.items():
            rel = os.path.relpath(full, self.in_dir)
            for ent in table.values():
                _lang, src = self._pick_source(ent)
                if not src or not src.strip():
                    continue
                if self._looks_like_target(src):
                    continue
                if not _has_translatable_text(src):
                    continue
                if looks_like_code_or_debug(rel, src):
                    continue
                total += 1
                tm_values.append(src)
                if self.only_referenced and ent.id not in self._ref_counts:
                    continue
                if self.protect_proper and looks_like_proper_text(rel, src):
                    protected += 1
                    continue
                todo += 1
        self._tm_enable_global(tm_values)
        return total, todo, protected

    def _run_reference_layer(self, done_dir, reuse):
        self._build_text_index()
        if not self._table_entries:
            self.log("没有找到 configs/text 文本表；引用层模式无法继续",
                     "warn")
            return False
        self._scan_references()
        total, todo, protected = self._reference_candidates()
        self.log("引用层预览：可译条目 %d 条，其中待译 %d 条，保护专名 %d 条"
                 % (total, todo, protected))
        if self.preview_reference:
            self.log("预览完成（未写盘）。取消勾选「先预览」后再开始即执行。",
                     "ok")
            return True

        # 1.7.3 及更早版本生成的根目录表没有注册进 localization.ltx，
        # 属于旧机制残留；引用层模式不再产出它，顺手清掉避免误部署。
        for stale in ("___auto_put_this_to_text_folder.xml",
                      "待确认_未解析引用.csv"):
            try:
                os.remove(os.path.join(done_dir, stale))
            except OSError:
                pass

        tables = sorted(self._table_entries)
        n_tables = len(tables)
        changed = copied = translated = skipped = failed = 0
        for idx, full in enumerate(tables, 1):
            if self._cancelled():
                self.log("已取消，已处理的文本表保留在输出目录")
                return False
            rel = os.path.relpath(full, self.in_dir)
            out_path = os.path.join(done_dir, rel)
            st = self._translate_table(full, out_path, rel)
            translated += st["translated"]
            skipped += st["skipped"]
            failed += st["failed"]
            changed += 1 if st["changed"] else 0
            copied += 1 if st["copied"] else 0
            if idx == 1 or idx == n_tables or idx % 20 == 0:
                self.log("文本表 %d/%d（%d%%）：%s"
                         % (idx, n_tables, 100 * idx // n_tables, rel))
        copied_refs = self._copy_reference_layer(done_dir)
        lit = {"files": 0, "entries": 0}
        if self.translate_literals:
            lit = self._run_literal_layer(done_dir)
        self.empty_files = sum(1 for t in self._table_entries.values()
                               if not t)
        self.log("引用层完成：改写文本表 %d 个，原样复制 %d 个；"
                 "复制引用/配置 %d 个；译 %d 条，跳过噪声 %d 条，失败 %d 条；"
                 "脚本/XML 字面文本：改写 %d 个文件 / %d 条"
                 % (changed, copied, copied_refs, translated, skipped, failed,
                    lit["files"], lit["entries"]), "ok")
        return True

    def _run_literal_layer(self, done_dir):
        """把脚本/XML 里**不是 ID** 的字面文本抽到新表并注册。

        文本表只解决“ID → 正文”这一层；脚本和 UI XML 里仍有直接写着的
        俄文/英文（补丁的 scripts.xml / ui.xml / gameplay.xml 就是这些）。
        不处理它们，游戏里就会看到俄文或 ID。这里复用旧的提取器：
        字面文本 → 新 ID，原文件只换 ASCII ID，新表 UTF-8。
        """
        prefix = "sgtat_gen_text_%d_" % int(time.time())
        gen = [0]
        extract = {}
        written = 0
        total = 0
        targets = []
        for full in self._iter_files():
            rel = os.path.relpath(full, self.in_dir)
            kind = self._classify_file(full, rel)
            if kind in ("cfg", "script"):
                targets.append((kind, rel, full))
        targets.sort(key=lambda x: x[1])
        for kind, rel, full in targets:
            if self._cancelled():
                self.log("已取消：字面文本层已处理的文件保留在输出目录")
                break
            out_path = os.path.join(done_dir, rel)
            old_mode = self.mode
            self.mode = "cfgxml" if kind == "cfg" else "scriptE"
            try:
                rep = self._process_non_text(full, {}, extract, prefix, gen)
            except Exception as e:
                self.failed_strings += 1
                self.detail("  字面文本提取失败：%s（%s: %s）"
                            % (rel, type(e).__name__, e))
                rep = None
            finally:
                self.mode = old_mode
            if rep is None:
                continue
            whole, rep_dict, enc = rep
            if not rep_dict:
                continue
            try:
                self._write_atomic(
                    out_path,
                    lambda p: self.p.generateOutputFileFromString(
                        p, whole, needXmlHeader=False,
                        encoding=enc or "utf-8"))
            except Exception as e:
                self.failed_strings += 1
                self.detail("  字面文本回写失败：%s（%s: %s）"
                            % (rel, type(e).__name__, e))
                continue
            written += 1
            total += len(rep_dict)
            self._record_done(rel, full, out_path)
            if written % 20 == 0:
                self.log("字面文本：已改写 %d 个文件 / %d 条…"
                         % (written, total))
        if extract:
            name = "sgtat_auto_texts"
            table_path = os.path.join(done_dir, "configs", "text",
                                      name + ".xml")
            self._write_atomic(
                table_path,
                lambda p: self.p.generateOutputXml(p, extract,
                                                   splitLength=1000))
            self._register_localization(done_dir, name)
            self.log("字面文本表：%s（%d 条），已写入 localization.ltx"
                     % (table_path, len(extract)), "ok")
        return {"files": written, "entries": total}

    def _register_localization(self, done_dir, table_name):
        """把生成的文本表名追加到 configs/localization.ltx 的 files 行。"""
        src = os.path.join(self.in_dir, "configs", "localization.ltx")
        out = os.path.join(done_dir, "configs", "localization.ltx")
        if os.path.isfile(src):
            text = read_text_any(src)
        else:
            text = ("[string_table]\nlanguage = %s\nfont_prefix =\n"
                    % (self._lang_hint or "rus"))
        lines = text.splitlines()
        found = False
        for i, line in enumerate(lines):
            s = line.strip()
            if s.lower().startswith("files") and "=" in s:
                key, value = line.split("=", 1)
                names = [x.strip() for x in value.split(",") if x.strip()]
                if table_name not in names:
                    names.append(table_name)
                lines[i] = key + "= " + ", ".join(names)
                found = True
                break
        if not found:
            lines.extend(["", "files = " + table_name])
        parent = os.path.dirname(out) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, out)

    @staticmethod
    def _entry_style(rel, src):
        low = (rel or "").lower().replace(os.sep, "/")
        if "dialog" in low:
            return "dialogue"
        if ("/ui/" in low or "ui_" in low or "items" in low):
            return "label"
        s = (src or "").strip()
        if len(s) <= 48 and not re.search(r"[.!?。！？；;:]", s):
            return "label"
        if ("encyclopedia" in low or "news" in low
                or "enc_" in low):
            return "formal"
        return "dialogue"

    def _scene_context(self, metas, idx):
        """取同表前后各 2 条源文作场景上下文（最多 240 字符）。"""
        parts = []
        for j in range(idx - 1, max(-1, idx - 3), -1):
            src = (metas[j].get("src") or "").strip()
            if src:
                parts.insert(0, "前一句：" + src)
        for j in range(idx + 1, min(len(metas), idx + 3)):
            src = (metas[j].get("src") or "").strip()
            if src:
                parts.append("后一句：" + src)
        text = "\\n".join(parts)
        return text[:240]

    def _parse_numbered_batch(self, out, n):
        found = {}
        for line in (out or "").splitlines():
            m = re.match(r"^\s*\[(\d+)\]\s*(.*)$", line)
            if not m:
                continue
            i = int(m.group(1))
            if 1 <= i <= n and i not in found:
                found[i] = m.group(2).strip()
        if len(found) != n or any(not found.get(i) for i in range(1, n + 1)):
            return None
        return [found[i] for i in range(1, n + 1)]

    def _translate_dialogue_batch(self, batch):
        """批量翻译 2～4 条连续短对话；失败返回 {}，交由逐条路径。"""
        lines = [x["src"] for x in batch]
        pairs = []
        seen = set()
        for x in batch:
            for k, v in self._glossary_hits(x["src"]):
                if k not in seen:
                    seen.add(k)
                    pairs.append((k, v))
        self.batch_stats["batches"] += 1
        self.req_count += 1
        self.trans_chars += sum(len(x["src"]) for x in batch)
        try:
            out = self.srv.translate_batch(
                lines, batch[0]["lang"], self.tgt,
                glossary_hits=pairs or None,
                temperature=self.dialogue_temp)
        except Exception:
            self.batch_stats["failed"] += 1
            return {}
        parsed = self._parse_numbered_batch(out, len(batch))
        if not parsed:
            self.batch_stats["failed"] += 1
            return {}
        result = {}
        for x, text in zip(batch, parsed):
            text = clean_translation_output(text)
            text = self._repair_residual_source_terms(x["src"], text)
            xlang = x["lang"]
            if xlang in ("auto", "text", "rus", "eng"):
                det = detect_text_lang(x["src"])
                if det:
                    xlang = det
            text = self._repair_known_semantic_terms(
                x["src"], text, xlang)
            if self._entry_style(x["rel"], x["src"]) == "dialogue":
                text = style_cleanup_cn(text)
            if (not text or not has_cjk(text)
                    or text.strip() == x["src"].strip()
                    or looks_like_prompt_echo(text)
                    or (has_source_profanity(x["src"])
                        and not has_zh_profanity(text))):
                self.batch_stats["failed"] += 1
                return {}
            lead = x["src"][:len(x["src"]) - len(x["src"].lstrip())]
            trail = x["src"][len(x["src"].rstrip()):]
            result[x["id"]] = (x["lang"], lead + text.strip() + trail)
        self.batch_stats["lines"] += len(batch)
        return result

    def _translate_table(self, full, out_path, rel):
        st = {"translated": 0, "skipped": 0, "failed": 0,
              "changed": False, "copied": False}
        try:
            ents = self._parse(self.p.parse_xray_text_xml, full,
                               read_encoding_chain(full))
        except Exception as e:
            st["failed"] += 1
            self.detail("  文本表解析失败：%s（%s）" % (rel, e))
            return st
        if not ents:
            self._copy_file(full, out_path)
            st["copied"] = True
            self._record_done(rel, full, out_path)
            return st
        if self.tm_lite and not self._tm_global_enabled:
            tm_values = []
            for ent in ents:
                _lang, src = self._pick_source(ent)
                if not src or not src.strip():
                    continue
                if self._looks_like_target(src) or not _has_translatable_text(src):
                    continue
                if (looks_like_code_or_debug(rel, src)
                        or looks_like_generated_noise(rel, src)
                        or looks_like_technical_text(src)
                        or looks_like_identifier_text(src)):
                    continue
                if self.protect_proper and looks_like_proper_text(rel, src):
                    continue
                tm_values.append(src)
            self._tm_enable_table(tm_values, rel)
        changes = {}
        metas = []
        for ent in ents:
            lang0, src0 = self._pick_source(ent)
            metas.append({
                "id": ent.id, "ent": ent, "lang": lang0, "src": src0,
                "rel": rel, "style": self._entry_style(rel, src0 or ""),
            })
        batched_ids = set()
        if "dialog" in rel.lower() and not self.force_glossary:
            def _ok(meta):
                src0 = meta["src"]
                if not src0 or not src0.strip():
                    return False
                if len(src0) < 4 or len(src0) > 220 or chr(10) in src0:
                    return False
                if _RUNTIME_TOKEN_RE.search(src0):
                    return False
                # 含 hard 术语的句子不进批量：批量只用提示，容易丢术语；
                # 交给逐条路径（占位符/校验更完整）。
                if (self.gloss_hard is not None
                       and any(k in self.gloss_hard
                               for k, _v in self._glossary_hits(src0))):
                    return False
                if self._looks_like_target(src0) or not _has_translatable_text(src0):
                    return False
                if src0.strip() in self.glossary:
                    return False
                if (looks_like_code_or_debug(rel, src0)
                        or looks_like_generated_noise(rel, src0)
                        or looks_like_technical_text(src0)
                        or looks_like_identifier_text(src0)):
                    return False
                if self.only_referenced and meta["id"] not in self._ref_counts:
                    return False
                if self.protect_proper and looks_like_proper_text(rel, src0):
                    return False
                return True
            i = 0
            while i < len(metas):
                if not _ok(metas[i]):
                    i += 1
                    continue
                batch = [metas[i]]
                total = len(metas[i]["src"])
                j = i + 1
                while (j < len(metas) and len(batch) < 4
                       and _ok(metas[j])
                       and metas[j]["lang"] == metas[i]["lang"]
                       and total + len(metas[j]["src"]) <= 700):
                    batch.append(metas[j])
                    total += len(metas[j]["src"])
                    j += 1
                if len(batch) >= 2:
                    got = self._translate_dialogue_batch(batch)
                    if got:
                        changes.update(got)
                        batched_ids.update(got)
                        st["translated"] += len(got)
                        i = j
                        continue
                i += 1
        for meta_idx, meta in enumerate(metas):
            ent = meta["ent"]
            if ent.id in batched_ids:
                continue
            if self._cancelled():
                break
            lang, src = meta["lang"], meta["src"]
            if not src or not src.strip():
                continue
            if self._looks_like_target(src):
                continue
            exact = src.strip()
            if (exact in self.glossary
                    and exact not in self.session_only_keys):
                # 制作名单/音乐/武器短名即使命中了核心词条，也要服从
                # “保护专名”；否则 credits 里的 Harris 会被改写成中文。
                if not (self.protect_proper
                        and looks_like_hard_protected_file(rel)):
                    lead = src[:len(src) - len(src.lstrip())]
                    trail = src[len(src.rstrip()):]
                    new_exact = lead + self.glossary[exact] + trail
                    if new_exact != src:
                        changes[ent.id] = (lang, new_exact)
                        st["translated"] += 1
                    continue
            if not _has_translatable_text(src):
                continue
            if looks_like_code_or_debug(rel, src):
                st["skipped"] += 1
                continue
            if (looks_like_generated_noise(rel, src)
                    or looks_like_technical_text(src)
                    or looks_like_identifier_text(src)):
                st["skipped"] += 1
                continue
            if self.only_referenced and ent.id not in self._ref_counts:
                continue
            if self.protect_proper and looks_like_proper_text(rel, src):
                st["skipped"] += 1
                continue
            use_lang = self.src_lang if self.src_lang != "auto" else lang
            # 字段名不可靠：不少 MOD 把英语放进 <rus>，或把俄语放进 <text>。
            # 即使显式选了 rus/eng，正文明显是另一种语言时也按正文判断；
            # ukr 不覆盖（俄/乌都是西里尔，粗判分不出来）。
            if use_lang in ("auto", "text", "rus", "eng"):
                det = detect_text_lang(src)
                if det and det != use_lang:
                    use_lang = det
            ctx = ""
            if meta["style"] == "dialogue" and "dialog" in rel.lower():
                ctx = self._scene_context(metas, meta_idx)
            try:
                out = self._translate_one(
                    src, use_lang, context=ctx, style=meta["style"])
            except Exception as e:
                st["failed"] += 1
                self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                            % (ent.id, type(e).__name__, e))
                continue
            if not out or not out.strip():
                continue
            out = clean_translation_output(out)
            lead = src[:len(src) - len(src.lstrip())]
            trail = src[len(src.rstrip()):]
            new = lead + out.strip() + trail
            if new == src:
                continue
            changes[ent.id] = (lang, new)
            st["translated"] += 1
        if not changes:
            self._copy_file(full, out_path)
            st["copied"] = True
            self._record_done(rel, full, out_path)
            return st
        try:
            open(full, "rb").read().decode("utf-8-sig")
            utf8_table = True
        except (OSError, UnicodeDecodeError):
            utf8_table = False
        if utf8_table:
            wrote = self._write_table_changes(full, out_path, changes, rel)
        else:
            # 老 MOD 常见 cp1251 表；中文无法用 cp1251 写回，转成 UTF-8
            # 并重写声明。会丢注释/多语言标签，但不会漏译。
            wrote = self._write_table_as_utf8(full, out_path, ents,
                                             changes, rel)
        if wrote:
            st["changed"] = True
            self._record_done(rel, full, out_path)
        else:
            st["copied"] = True
        return st

    def _write_table_as_utf8(self, full, out_path, ents, changes, rel):
        """非 UTF-8 文本表：保持 <rus>/<eng>/<tur> 结构转 UTF-8。

        不能走 generateOutputXml：它只写 <text>，会把原文的 <rus>/<eng>
        标签全部丢掉（实测这个 MOD 的 stable_dialogs 从 52 KB 缩到 24 KB，
        引擎按 rus 读时就可能读不到中文）。这里逐条改 <rus> 内容，
        只把 XML 声明的编码换成 utf-8。
        """
        try:
            raw = open(full, "rb").read()
        except OSError:
            return False
        text = None
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")
        applied = missed = 0
        for eid, (lang, new_text) in changes.items():
            text, ok = self._patch_table_text(text, eid, lang, new_text)
            if ok:
                applied += 1
            else:
                missed += 1
        if applied == 0:
            self.log("文本表条目定位失败，保留原文：%s" % rel, "warn")
            self._copy_file(full, out_path)
            return False
        if missed:
            self.detail("  %s：%d 条已改写，%d 条定位失败（保留原文）"
                        % (rel, applied, missed))
        text = re.sub(
            r'(<\?xml[^>]*?\bencoding\s*=\s*["\'])[^"\']+(["\'])',
            r"\1utf-8\2", text, count=1, flags=re.I)
        if not text.lstrip().startswith("<?xml"):
            text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
        data = (b"\xef\xbb\xbf" if had_bom else b"") + text.encode("utf-8")
        parent = os.path.dirname(out_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = out_path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out_path)
        except OSError as e:
            self.log("文本表写入失败（保留原文）：%s（%s）" % (rel, e), "err")
            self._copy_file(full, out_path)
            return False
        self.log("文本表转 UTF-8 并保留语言标签：%s" % rel, "warn")
        return True

    @staticmethod
    def _patch_table_text(text, eid, lang, new_text):
        tag = {"text": "text", "rus": "rus", "eng": "eng",
               "ukr": "ukr", "tur": "tur"}.get(lang, "text")
        block_re = re.compile(
            r"(<string\b[^>]*\bid\s*=\s*[\"']%s[\"'][^>]*>)(.*?)"
            r"(</string>)" % re.escape(eid), re.S)
        m = block_re.search(text)
        if not m:
            return text, False
        inner = m.group(2)
        tag_re = re.compile(r"(<%s\b[^>]*>)(.*?)(</%s>)" % (tag, tag), re.S)
        tm = tag_re.search(inner)
        esc = (new_text.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;"))
        if tm:
            inner2 = inner[:tm.start(2)] + esc + inner[tm.end(2):]
        else:
            inner2 = (inner.rstrip() + "\n\t\t<%s>%s</%s>\n\t"
                      % (tag, esc, tag))
        return text[:m.start(2)] + inner2 + text[m.end(2):], True

    def _write_table_changes(self, full, out_path, changes, rel):
        try:
            raw = open(full, "rb").read()
        except OSError:
            return False
        had_bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            self.log("非 UTF-8 文本表暂不原地改写（保留原文）：%s" % rel,
                     "warn")
            self._copy_file(full, out_path)
            return False
        applied = missed = 0
        for eid, (lang, new_text) in changes.items():
            text, ok = self._patch_table_text(text, eid, lang, new_text)
            if ok:
                applied += 1
            else:
                missed += 1
        if applied == 0:
            self.log("文本表条目定位失败，保留原文：%s" % rel, "warn")
            self._copy_file(full, out_path)
            return False
        if missed:
            self.detail("  %s：%d 条已改写，%d 条定位失败（保留原文）"
                        % (rel, applied, missed))
        data = (b"\xef\xbb\xbf" if had_bom else b"") + text.encode("utf-8")
        parent = os.path.dirname(out_path) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = out_path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out_path)
        except OSError as e:
            self.log("文本表写入失败（保留原文）：%s（%s）" % (rel, e), "err")
            self._copy_file(full, out_path)
            return False
        return True

    def _copy_reference_layer(self, done_dir):
        if not self.copy_reference:
            return 0
        copied = 0
        for full in sorted(self._ref_files):
            rel = os.path.relpath(full, self.in_dir)
            out = os.path.join(done_dir, rel)
            if os.path.isfile(out) and _files_equal(full, out):
                continue
            try:
                self._copy_file(full, out)
                copied += 1
            except Exception as e:
                self.detail("  复制引用层失败：%s（%s）" % (rel, e))
        # 顶层 configs/*.ltx：localization.ltx 是文本表注册表，
        # external.ltx/fonts.ltx 等是解析/字体配置；补丁把它们算作有用文件。
        # 原样复制（不改内容），保证“不漏文件”。
        cfg = os.path.join(self.in_dir, "configs")
        if os.path.isdir(cfg):
            for fn in sorted(os.listdir(cfg)):
                if not fn.lower().endswith(".ltx"):
                    continue
                full = os.path.join(cfg, fn)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, self.in_dir)
                out = os.path.join(done_dir, rel)
                if os.path.isfile(out) and _files_equal(full, out):
                    continue
                try:
                    self._copy_file(full, out)
                    copied += 1
                except Exception:
                    pass
        if self._unresolved:
            try:
                path = os.path.join(done_dir, "待确认_未解析引用.csv")
                with open(path, "w", encoding="utf-8-sig",
                          newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["id", "count"])
                    for key, count in sorted(
                            self._unresolved.items(),
                            key=lambda kv: (-kv[1], kv[0])):
                        writer.writerow([key, count])
            except Exception:
                pass
        return copied

    def _copy_file(self, src, dst):
        parent = os.path.dirname(dst) or "."
        os.makedirs(parent, exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)

    @staticmethod
    def _prune_empty_dirs(root):
        """自底向上删除不含任何文件的空目录；保留根目录本身。
        返回删除的目录数。"""
        removed = 0
        if not os.path.isdir(root):
            return 0
        for cur, dirs, files in os.walk(root, topdown=False):
            if os.path.abspath(cur) == os.path.abspath(root):
                continue
            try:
                if not os.listdir(cur):
                    os.rmdir(cur)
                    removed += 1
            except OSError:
                pass
        return removed

    def _classify_file(self, full, rel):
        """文件类型判定（转发到模块级 classify_xray_file，与勘察共用一处口径）。"""
        return classify_xray_file(full, rel)

    def _run_auto(self, done_dir, reuse):
        temp_dir = os.path.join(done_dir, "__temp__")
        os.makedirs(temp_dir, exist_ok=True)
        reuse.update(self._read_existing(temp_dir))

        targets = []
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, self.in_dir)
                kind = self._classify_file(full, rel)
                if kind:
                    targets.append((kind, rel, full))
        targets.sort(key=lambda x: x[1])
        total = len(targets)
        counts = {}
        for kind, _, _ in targets:
            counts[kind] = counts.get(kind, 0) + 1
        self.log("识别到可翻译文件 %d 个：文本XML %d / 配置XML %d / ltx %d / 脚本 %d"
                 % (total, counts.get("text", 0), counts.get("cfg", 0),
                    counts.get("ltx", 0), counts.get("script", 0)))

        extract = {}
        prefix = "sgtat_gen_text_%d_" % int(time.time())
        gen = [0]
        chars_all = set()
        written = 0
        failed = 0
        for idx, (kind, rel, full) in enumerate(targets, 1):
            if self._cancelled():
                self.log("已取消，已完成文件保留在输出目录")
                return False
            out_path = os.path.join(done_dir, rel)
            if self._should_skip(rel, full, out_path, kind):
                continue
            self._log_progress(idx, total, rel)
            if kind == "text":
                tmp = os.path.join(temp_dir, rel)
                try:
                    n_texts = self._run_text_file(
                        os.path.basename(full), full, out_path,
                        tmp, reuse, idx, total)
                except Exception as e:
                    failed += 1
                    self.log("文件处理失败，已跳过：%s（%s: %s）"
                             % (rel, type(e).__name__, e), "err")
                    continue
                if n_texts > 0:
                    written += 1
                    self._record_done(rel, full, out_path)
            else:
                mode = {"cfg": "cfgxml", "ltx": "ltx",
                        "script": "scriptL"}[kind]
                old_mode = self.mode
                self.mode = mode
                try:
                    rep = self._process_non_text(
                        full, reuse, extract, prefix, gen)
                except Exception as e:
                    failed += 1
                    self.log("文件处理失败，已跳过：%s（%s: %s）"
                             % (rel, type(e).__name__, e), "err")
                    continue
                finally:
                    self.mode = old_mode
                if rep is None:
                    continue
                whole_text, rep_dict, enc = rep
                if kind == "script":
                    if "chars_all_in" in extract:
                        chars_all |= extract["chars_all_in"]
                if rep_dict or self.output_anyway:
                    if kind in ("cfg", "ltx"):
                        self._write_atomic(
                            out_path,
                            lambda p: self.p.generateOutputFileFromString(
                                p, whole_text, needXmlHeader=False,
                                encoding=enc or "utf-8"))
                    else:
                        self._write_atomic(
                            out_path,
                            lambda p: self.p.generateOutputFileFromString(
                                p, whole_text, needXmlHeader=False))
                    written += 1
                    self._record_done(rel, full, out_path)

        if "chars_all_in" in extract:
            del extract["chars_all_in"]
        if chars_all:
            chars = "".join(chars_all)
            for i, pie in enumerate(self.p.splitTextToPiecesAtLength(chars, 500)):
                extract["text_gen_for_font_%d" % i] = pie
        if extract:
            self._write_atomic(
                os.path.join(done_dir, "___auto_put_this_to_text_folder.xml"),
                lambda p: self.p.generateOutputXml(p, extract, splitLength=1000))
        self.log("已写出文件 %d 个（镜像原目录结构，格式保持不变）" % written)
        if failed:
            self.log("有 %d 个文件处理失败（已跳过，其余照常完成），详见日志"
                     % failed, "warn")
        return True

    def _flush_temp(self, temp_path, done):
        """把当前进度写进 __temp__：取消/中断后重跑可直接复用已译部分。"""
        if not done:
            return
        try:
            self._write_atomic(
                temp_path,
                lambda p: self.p.generateOutputXml(p, done, splitLength=1000))
            self._temp_clock = time.time()
        except Exception as e:
            self.detail("  临时进度写入失败：%s: %s" % (type(e).__name__, e))

    def _run_text_file(self, fn, full, out_path, temp_path, reuse, idx, total):
        """处理单个文本 XML；返回条目数，取消时返回 -1（区别于"空文件"的 0）。"""
        chain = read_encoding_chain(full)
        texts = self._parse(self.p.parse_xray_text_xml, full, chain)
        self.detail("  共 %d 条文本" % len(texts))
        if not texts:
            if os.path.exists(out_path):
                if self._is_stale_empty_output(out_path):
                    try:
                        os.remove(out_path)
                        self.detail("  已清理空占位输出：%s" % out_path)
                    except OSError:
                        pass
                else:
                    self.log("  未解析出条目，但既有译文有内容，已保留：%s"
                             "（确认是空文件请手动删除）" % out_path, "warn")
            return 0
        if chain and chain[0] not in _ENC_UTF8:
            samples = []
            for ent in texts[:20]:
                samples.extend((ent.texts or {}).values())
            self._warn_mojibake(fn, samples)
        done = {}
        self._temp_clock = time.time()
        metas = []
        for ent in texts:
            lang0, src0 = self.p.getRecommendLangText(ent, self.tgt)
            metas.append({
                "id": ent.id, "ent": ent, "lang": lang0,
                "src": src0 if isinstance(src0, str) else "",
                "rel": full,
                "style": self._entry_style(full, src0 or ""),
            })
        batched = set()
        if "dialog" in full.lower() and not self.force_glossary:
            def _ok(meta):
                src0 = meta["src"]
                if not src0 or not src0.strip() or meta["id"] in reuse:
                    return False
                if len(src0) < 4 or len(src0) > 220 or chr(10) in src0:
                    return False
                if _RUNTIME_TOKEN_RE.search(src0):
                    return False
                # 含 hard 术语的句子不进批量：批量只用提示，容易丢术语；
                # 交给逐条路径（占位符/校验更完整）。
                if (self.gloss_hard is not None
                       and any(k in self.gloss_hard
                               for k, _v in self._glossary_hits(src0))):
                    return False
                if self._looks_like_target(src0) or not _has_translatable_text(src0):
                    return False
                if (looks_like_code_or_debug(full, src0)
                        or looks_like_generated_noise(full, src0)
                        or looks_like_technical_text(src0)
                        or looks_like_identifier_text(src0)):
                    return False
                if self.only_referenced and meta["id"] not in self._ref_counts:
                    return False
                if self.protect_proper and looks_like_proper_text(full, src0):
                    return False
                return True
            i = 0
            while i < len(metas):
                if not _ok(metas[i]):
                    i += 1
                    continue
                batch = [metas[i]]
                total = len(metas[i]["src"])
                j = i + 1
                while (j < len(metas) and len(batch) < 4
                       and _ok(metas[j])
                       and metas[j]["lang"] == metas[i]["lang"]
                       and total + len(metas[j]["src"]) <= 700):
                    batch.append(metas[j])
                    total += len(metas[j]["src"])
                    j += 1
                if len(batch) >= 2:
                    got = self._translate_dialogue_batch(batch)
                    if got:
                        for eid, (_lang, new) in got.items():
                            done[eid] = new
                        batched.update(got)
                        i = j
                        continue
                i += 1
        for n, ent in enumerate(texts, 1):
            if self._cancelled():
                self._flush_temp(temp_path, done)
                self.log("已取消：本文件已完成 %d/%d 条（已存入 __temp__，"
                         "重跑可复用）" % (len(done), len(texts)))
                return -1
            if ent.id in self._past:
                if ent.id not in self.redundant:
                    self.redundant.append(ent.id)
            else:
                self._past.add(ent.id)
            if ent.id in batched:
                continue
            if ent.id in reuse:
                done[ent.id] = reuse[ent.id]
                continue
            lang, src = self.p.getRecommendLangText(ent, self.tgt)
            if self.tgt == lang:
                done[ent.id] = src
            elif src and lang == "text" and self.src_lang == "auto" \
                    and self._looks_like_target(src):
                done[ent.id] = src
            elif src:
                use_lang = self.src_lang if self.src_lang != "auto" else lang
                # 字段名不可靠：不少 MOD 把英语放进 <rus>，或把俄语放进 <text>。
                # 自动模式下按本条正文的字母构成覆盖字段判断。
                if use_lang in ("auto", "text", "rus", "eng"):
                    det = detect_text_lang(src)
                    if det and det != use_lang:
                        use_lang = det
                style = self._entry_style(full, src)
                ctx = ""
                if style == "dialogue" and "dialog" in full.lower():
                    ctx = self._scene_context(metas, n - 1)
                try:
                    done[ent.id] = self._translate_one(
                        src, use_lang, context=ctx, style=style)
                except Exception as e:
                    self.failed_strings += 1
                    self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                                % (ent.id, type(e).__name__, e))
                    done[ent.id] = src
            else:
                done[ent.id] = ""
            if time.time() - self._temp_clock > 120:
                self._flush_temp(temp_path, done)
            if n % 20 == 0:
                self.progress({"file": fn, "ids": n, "total_ids": len(texts),
                               "file_idx": idx, "files": total,
                               "reqs": self.req_count})
        if not done:
            try:
                if os.path.exists(out_path) and \
                        self._is_stale_empty_output(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return 0
        self._write_atomic(
            out_path,
            lambda p: self.p.generateOutputXml(p, done, splitLength=1000))
        self.progress({"file": fn, "ids": len(texts), "total_ids": len(texts),
                       "file_idx": idx, "files": total, "reqs": self.req_count})
        return len(texts)

    def _pruned_walk(self):
        """遍历输入树，统一排除：输出目录（按绝对路径，即使它在输入树内或
        不叫 translated_hy_mt2）、__temp__、.git，避免把自己的产物再翻一遍。"""
        for root, dirs, files in os.walk(self.in_dir):
            kept = []
            for d in dirs:
                if d.endswith(".git") or d in ("__temp__", "translated_hy_mt2"):
                    continue
                ap = os.path.normcase(os.path.abspath(os.path.join(root, d)))
                if any(ap == ex or ap.startswith(ex + os.sep)
                       for ex in self._excluded_paths):
                    continue
                kept.append(d)
            dirs[:] = kept
            yield root, dirs, files

    def _collect_text_targets(self):
        """递归收集文本 XML（相对路径, 绝对路径），排除输出目录。"""
        targets = []
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                if fn.lower().endswith(".xml"):
                    full = os.path.join(root, fn)
                    targets.append((os.path.relpath(full, self.in_dir), full))
        targets.sort()
        return targets

    def _iter_files(self):
        for root, _dirs, files in self._pruned_walk():
            for fn in files:
                yield os.path.join(root, fn)

    def _run_recursive(self, done_dir, reuse):
        suffix = {
            "cfgxml": ".xml", "ltx": ".ltx",
            "scriptL": ".script", "scriptE": ".script",
        }.get(self.mode)
        if not suffix:
            self.log("未知文件类型：%r（应为 auto/text/cfgxml/ltx/scriptE/scriptL）"
                     % self.mode, "err")
            return False
        targets = [p for p in self._iter_files() if p.lower().endswith(suffix)]
        if self.mode == "cfgxml":
            targets = [p for p in targets
                       if os.path.basename(os.path.dirname(p)).lower() != "text"]
        total = len(targets)
        extract = {}
        text_id_prefix = "sgtat_gen_text_%d_" % int(time.time())
        gen = [0]
        self.log("待处理 %s 文件 %d 个" % (suffix, total))
        failed = 0
        for idx, full in enumerate(targets, 1):
            if self._cancelled():
                self.log("已取消")
                return False
            rel = os.path.relpath(full, self.in_dir)
            out_path = os.path.join(done_dir, rel)
            if self._should_skip(rel, full, out_path, self.mode):
                continue
            self._log_progress(idx, total, rel)
            try:
                rep = self._process_non_text(full, reuse, extract,
                                             text_id_prefix, gen)
            except Exception as e:
                failed += 1
                self.log("文件处理失败，已跳过：%s（%s: %s）"
                         % (rel, type(e).__name__, e), "err")
                continue
            if rep is None:
                continue
            whole_text, rep_dict, enc = rep
            if rep_dict or self.output_anyway:
                if self.mode in ("ltx", "cfgxml"):
                    self._write_atomic(
                        out_path,
                        lambda p: self.p.generateOutputFileFromString(
                            p, whole_text, needXmlHeader=False,
                            encoding=enc or "utf-8"))
                else:
                    self._write_atomic(
                        out_path,
                        lambda p: self.p.generateOutputFileFromString(
                            p, whole_text, needXmlHeader=False))
                self._record_done(rel, full, out_path)
        if self.mode == "scriptL" and "chars_all_in" in extract:
            chars = "".join(extract["chars_all_in"])
            extract.clear()
            for i, pie in enumerate(self.p.splitTextToPiecesAtLength(chars, 500)):
                extract["text_gen_for_font_%d" % i] = pie
        if extract:
            self._write_atomic(
                os.path.join(done_dir, "___%s__put_this_to_text_folder.xml"
                             % self.mode),
                lambda p: self.p.generateOutputXml(p, extract, splitLength=1000))
        if failed:
            self.log("有 %d 个文件处理失败（已跳过，其余照常完成），详见日志"
                     % failed, "warn")
        return True

    def _process_non_text(self, full, reuse, extract, prefix, gen):
        chain = read_encoding_chain(full)
        if self.mode == "cfgxml":
            whole, cands, enc = self._parse(
                self.p.parse_xray_cfgxml_xml, full, chain)
        elif self.mode == "ltx":
            whole, cands, enc = self._parse(
                self.p.parse_xray_ltx_file, full, chain)
        else:
            whole, cands = self._parse(
                self.p.parse_xray_script_file, full, chain)
            # 脚本按原编码写回（只把字面文本换成 ASCII 新 ID，不改编码）
            enc = "utf-8"
            for candidate_enc in chain:
                try:
                    whole.encode(candidate_enc)
                    enc = candidate_enc
                    break
                except (UnicodeEncodeError, LookupError):
                    continue
        if chain and chain[0] not in _ENC_UTF8 and looks_like_mojibake(whole):
            self.log("⚠ %s 疑为编码误判（乱码特征），请先用「编码转换」页"
                     "统一转成 UTF-8" % os.path.basename(full), "warn")
        if not cands:
            self.empty_files += 1
            if self.empty_files <= 20:
                self.detail("  %s 未提取到可译文本（%s 模式的提取规则见上游）"
                            % (os.path.basename(full), self.mode))
        prewrapped = set()
        if self.mode in ("scriptE", "scriptL"):
            # 上游提取器不认识上下文；这里只保留显示类调用/文本变量赋值，
            # 跳过 log、string_find/string_gsub、比较运算。见 script_literal_allowed。
            # 上游还会漏掉 set_level_changer_invitation / set_description /
            # AddItem(game.translate_string(...))，这里按显示调用补回。
            extra = extra_script_display_literals(whole)
            prewrapped = script_translate_literal_args(whole)
            cands = list(dict.fromkeys(list(cands) + sorted(extra)))
            cands = [c for c in cands
                     if c in extra or script_literal_allowed_in_file(whole, c)]
        if self._cancelled():
            return None
        rep = {}
        for cand in cands:
            # 这里不能按 reuse 跳过：reuse 的键是文本 XML 的 string id，
            # 与 ltx/cfg/script 的候选串不是同一命名空间；真命中了也拿不到
            # 译文，只会把原文留在输出里。非文本模式的复用由账本负责。
            if self.mode == "cfgxml":
                if cand.strip() == "" or self.p.doesTextLookLikeId(cand):
                    continue
                send = cand
                quoted = False
            elif self.mode == "ltx":
                if self.p.doesTextLookLikeId(cand):
                    continue
                quoted = len(cand) > 1 and cand.startswith('"') and cand.endswith('"')
                send = cand[1:-1] if quoted else cand
            else:
                if len(cand) < 2:
                    continue
                qch = cand[0]
                send = cand[1:-1]
                if (self.p.doesTextLookLikeId(send)
                        or self.p.doesTextLookLikeScript(send)):
                    continue
            rel = os.path.relpath(full, self.in_dir)
            if looks_like_code_or_debug(rel, send):
                continue
            if looks_like_identifier_text(send):
                continue
            if self.protect_proper and looks_like_proper_text(rel, send):
                continue
            if self._cancelled():
                return None
            try:
                lang = self.src_lang
                if lang in ("auto", "text"):
                    lang = (detect_text_lang(send) or self._lang_hint
                            or "rus")
                transed = self._translate_one(send, lang)
            except Exception as e:
                self.failed_strings += 1
                self.detail("  条目翻译失败，保留原文：%s（%s: %s）"
                            % (str(send)[:40], type(e).__name__, e))
                continue
            if self.mode == "ltx" and quoted:
                transed = '"%s"' % transed
            if self.mode in ("cfgxml", "ltx", "scriptE"):
                key = prefix + str(gen[0])
                gen[0] += 1
                extract[key] = transed
                if self.mode == "scriptE":
                    # 脚本里裸 ID 是未定义全局变量（nil）；补丁统一用
                    # game.translate_string("id")。已经在 translate_string
                    # 里的字面串只换 ID，不能再套一层。
                    if cand in prewrapped:
                        # 外层已有 game.translate_string(...)，只把引号内
                        # 的字面串换成带引号的新 ID，不能退化成裸变量。
                        rep[cand] = '"%s"' % key
                    else:
                        rep[cand] = 'game.translate_string("%s")' % key
                else:
                    rep[cand] = key
            else:
                if "chars_all_in" not in extract:
                    extract["chars_all_in"] = set()
                for ch in transed:
                    extract["chars_all_in"].add(ch)
                inline = unescape_newlines(transed)
                rep[qch + self.p.escapeLiteralText(send, quote=qch) + qch] = (
                    qch + self.p.escapeLiteralText(inline, quote=qch) + qch)
        if not rep:
            return (whole, {}, enc)
        whole = self.p.replaceFromText(whole, rep)
        return (whole, rep, enc)



def _combo_get(w):
    try:
        return w.currentText()
    except Exception:
        try:
            return w.get()
        except Exception:
            return ""


def _combo_set(w, values, idx=0):
    try:
        w.configure(values=values)
        w.current(idx)
        return
    except Exception:
        pass
    try:
        w.clear()
        w.addItems(values)
        if values:
            w.setCurrentIndex(idx)
    except Exception:
        pass


def _entry_get(w):
    """跨后端读单行输入框（Tk: ttk.Entry.get / Qt: QLineEdit.text）。"""
    for name in ("get", "text"):
        fn = getattr(w, name, None)
        if callable(fn):
            try:
                return fn() or ""
            except Exception:
                continue
    return ""


def _entry_set(w, value):
    try:
        w.delete(0, "end")
        w.insert(0, value)
        return
    except Exception:
        pass
    try:
        w.setText(value)
    except Exception:
        pass


def _multiline_get(w):
    """跨后端读多行输入框（Tk: tk.Text / Qt: QPlainTextEdit）。"""
    fn = getattr(w, "get", None)
    if callable(fn):
        try:
            return fn("1.0", "end-1c") or ""
        except Exception:
            pass
    fn = getattr(w, "toPlainText", None) or getattr(w, "get_text", None)
    if callable(fn):
        try:
            return fn() or ""
        except Exception:
            pass
    return ""


def _multiline_set(w, value):
    try:
        w.delete("1.0", "end")
        w.insert("1.0", value)
        return
    except Exception:
        pass
    for name in ("setPlainText", "set_text", "setText"):
        fn = getattr(w, name, None)
        if callable(fn):
            try:
                fn(value)
                return
            except Exception:
                continue


def _chk_set(w, on):
    try:
        w.state(["selected" if on else "!selected"])
        return
    except Exception:
        pass
    try:
        w.setChecked(bool(on))
    except Exception:
        pass


def _chk_get(w):
    try:
        return bool(w.instate(["selected"]))
    except Exception:
        pass
    try:
        return bool(w.isChecked())
    except Exception:
        return False


class AiTranslateTab(object):
    def __init__(self, api, parent):
        self.api = api
        self.ui = api.ui
        self.parent = parent
        self.cfg = load_config()
        self.server = None
        self.parser = None
        self.dl_cancel = None
        self.tr_cancel = None
        self.scan_cancel = None
        self.probe_cache = {}
        self._last_ui_byte = -1
        self.log_lines = []
        # 术语分两层：builtin（随包只读）+ 用户覆盖层（可变），见 load_glossary_layers
        (self.gloss_user, self._gloss_set, self._gloss_deleted,
         self._gloss_clear) = load_glossary_layers()
        # 附加提示词必须由 **UI 线程**读控件后缓存到这里；工作线程只读这个
        # 字符串（1.6.0 之前工作线程直接读 tk.Text，异常被吞成空串，
        # 导致该功能实际从未生效）。
        self._prompt_extra_text = str(self.cfg.get("prompt_extra") or "")
        self._starting = False
        self._cfg_save_warned = False
        self._hw_cuda13_ok = False
        self._log_pending = False
        self.scan_info = None
        self.scan_dir = None
        self._mode_plain = self.cfg.get("mode", "auto")
        self._q = queue.Queue()
        self._tk = None

        try:
            self.parser = _load_vendor()
        except Exception:
            self.parser = None

        self._build()
        self.refresh_gloss_view()
        self.refresh_local_status()
        self.on_hw_check(silent=True)
        self._sweep_orphan_servers()

    def _sweep_orphan_servers(self):
        """后台清理上次异常退出遗留的引擎（只认本插件 exe + 父进程已死）。

        不阻塞启动：Toolhelp32 枚举通常只要几毫秒；即使失败也绝不影响
        正常流程。清到东西才写一行日志，避免每次启动都刷屏。
        """
        def work():
            try:
                killed = _kill_orphan_servers(self.cfg)
            except Exception:
                return
            if killed:
                self.ui_call(
                    self.log,
                    "已清理 %d 个上次异常退出遗留的引擎进程（%s），"
                    "显存/内存已回收"
                    % (len(killed), ", ".join("PID %d" % p for p in killed)),
                    "ok")
        threading.Thread(target=work, daemon=True).start()

    def on_hw_check(self, silent=False):
        """① 硬件检测：后台执行，给出一句话推荐并预选后端/档位。"""
        def work():
            try:
                hw = detect_hardware()
            except Exception as e:
                self.ui_call(self.log, "硬件检测失败：%s" % e, "err")
                return
            self.ui_call(self._apply_hw, hw, silent)
        threading.Thread(target=work, daemon=True).start()

    def _apply_hw(self, hw, silent=False):
        self._set_label(self.hw_label, hw["short"])
        self._hw_cuda13_ok = bool(hw.get("cuda13_ok"))
        if not self.cfg.get("variant_chosen"):
            vals = [VARIANT_LABELS[v] for v in VARIANT_ORDER]
            vi = VARIANT_ORDER.index(hw["variant"])
            _combo_set(self.variant_combo, vals, vi)
            self._on_variant(save_chosen=False)
        if not self.cfg.get("tier_chosen"):
            idx = TIER_ORDER.index(hw["tier"]) if hw["tier"] in TIER_ORDER else 0
            _combo_set(self.tier_combo, [TIER_SHORT[t] for t in TIER_ORDER], idx)
            self._on_tier(save_chosen=False)
        if not silent:
            self.log("硬件：%s" % hw["short"], "ok")
        self.refresh_local_status()

    def ui_call(self, fn, *a, **k):
        if self._tk is None:
            try:
                fn(*a, **k)
            except Exception as e:
                self._log_ui_error(fn, e)
        else:
            self._q.put((fn, a, k))

    def _log_ui_error(self, fn, exc):
        """UI 回调异常写详细日志：否则只表现为"点了没反应"。"""
        try:
            self.api.log_detail("UI 回调异常: %s — %s: %s"
                                % (getattr(fn, "__name__", fn),
                                   type(exc).__name__, exc), "err")
        except Exception:
            pass

    def _pump(self):
        # 一次把队列抽干会让主线程长时间回不到事件循环 —— 几千条日志排队时
        # 窗口表现为"卡死"。这里限制单次预算（200 项 / 15ms），剩下的下个
        # tick 继续，保证重绘与输入事件始终有机会执行。
        t0 = time.time()
        for _ in range(200):
            try:
                fn, a, k = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*a, **k)
            except Exception as e:
                self._log_ui_error(fn, e)
            if time.time() - t0 > 0.015:
                break
        top = getattr(self, "_top", None)
        if top is None:
            return
        try:
            top.after(30, self._pump)
        except Exception:
            self._tk = None
            self._top = None

    def _reg(self, w):
        """登记首个真实控件，用它拿到主窗口句柄以便启动 UI 更新泵。"""
        if self._tk is None:
            native = getattr(w, "widget", w)
            if hasattr(native, "winfo_toplevel"):
                self._tk = native
                try:
                    self._top = native.winfo_toplevel()
                    self._top.after(30, self._pump)
                except Exception:
                    pass
        return w

    def log(self, msg, tag="info"):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
        self.log_lines.append(line)
        del self.log_lines[:-400]
        self.api.log(msg, tag)
        # 每条日志都整块 set_text（最多 400 行）在批量文件处理时会把主线程
        # 压死；这里只置脏标记，一个泵周期内合并成一次重绘。
        if not self._log_pending:
            self._log_pending = True
            self.ui_call(self._flush_log)

    def _flush_log(self):
        self._log_pending = False
        try:
            if self.log_view.alive():
                self.log_view.set_text("\n".join(self.log_lines))
        except Exception:
            pass

    def dl_log(self, msg):
        self.log(msg)

    def _build(self):
        ui = self.ui
        p = self.parent

        head = self._reg(ui.label(
            p, "AI 离线翻译（本地推理，不联网翻译）· 按①→⑤操作"))
        ui.pack(head, fill="x", pady=(2, 4))

        self._build_step_hw(p)
        self._build_step_runtime(p)
        self._build_step_model(p)
        self._build_step_job(p)

        self.log_view = self._reg(ui.text_view(p, height=9, mono=True))
        ui.pack(self.log_view.widget, fill="both", expand=True, pady=(4, 0))

        if self.parser is None:
            self.log("XML 解析模块加载失败（xray_pkg.zip 缺失？）", "err")

    def _section(self, p, text):
        lab = self._reg(self.ui.label(p, text))
        self.ui.pack(lab, fill="x", pady=(6, 1))
        return lab

    def _build_step_hw(self, p):
        ui = self.ui
        self._section(p, "① 硬件检测")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        self.btn_hw = self._reg(ui.button(r, "检测硬件", on_click=self.on_hw_check))
        ui.pack(self.btn_hw, side="left", padx=(0, 6))
        self.hw_label = self._reg(ui.label(r, "尚未检测"))
        ui.pack(self.hw_label, side="left")

    def _build_step_runtime(self, p):
        ui = self.ui
        self._section(p, "② 运行环境（显卡驱动 / CPU）")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        ui.pack(ui.label(r, "后端："), side="left")
        variant_vals = [VARIANT_LABELS[v] for v in VARIANT_ORDER]
        self.variant_combo = self._reg(ui.combo(
            r, values=variant_vals, width=46,
            on_change=lambda v: self._on_variant()))
        ui.pack(self.variant_combo, side="left", padx=(0, 6))
        vi = VARIANT_ORDER.index(self.cfg["variant"]) \
            if self.cfg["variant"] in VARIANT_ORDER else 0
        _combo_set(self.variant_combo, variant_vals, vi)
        self.btn_probe_rt = self._reg(
            ui.button(r, "测速选源", on_click=lambda: self.on_probe("rt")))
        ui.pack(self.btn_probe_rt, side="left", padx=(0, 4))
        self.btn_dl_rt = self._reg(
            ui.button(r, "下载运行库", on_click=lambda: self.on_download("rt")))
        ui.pack(self.btn_dl_rt, side="left", padx=(0, 4))
        self.btn_dl_cancel = self._reg(ui.button(
            r, "取消", on_click=self.on_cancel_download, state="disabled"))
        ui.pack(self.btn_dl_cancel, side="left")

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        ui.pack(ui.label(r2, "源："), side="left")
        self.source_combo = self._reg(ui.combo(
            r2, values=["先测速，自动选最快"], width=44))
        ui.pack(self.source_combo, side="left", padx=(0, 6))
        self.env_label = self._reg(ui.label(r2, ""))
        ui.pack(self.env_label, side="left")

    def _build_step_model(self, p):
        ui = self.ui
        self._section(p, "③ 离线模型（四选一）")
        r = self._reg(ui.row(p))
        ui.pack(r, fill="x", pady=1)
        self.tier_combo = self._reg(ui.combo(
            r, values=[TIER_SHORT[t] for t in TIER_ORDER], width=46,
            on_change=lambda v: self._on_tier()))
        ui.pack(self.tier_combo, side="left", padx=(0, 6))
        idx = TIER_ORDER.index(self.cfg["tier"]) if \
            self.cfg["tier"] in TIER_ORDER else 0
        _combo_set(self.tier_combo, [TIER_SHORT[t] for t in TIER_ORDER], idx)
        self.btn_probe_model = self._reg(ui.button(
            r, "测速选源", on_click=lambda: self.on_probe("model")))
        ui.pack(self.btn_probe_model, side="left", padx=(0, 4))
        self.btn_dl_model = self._reg(ui.button(
            r, "下载模型", on_click=lambda: self.on_download("model")))
        ui.pack(self.btn_dl_model, side="left", padx=(0, 6))
        ui.pack(ui.label(r, "自选上下文："), side="left")
        self.ctx_combo = self._reg(ui.combo(
            r, values=["4096", "8192", "16384", "32768"], width=8,
            on_change=lambda v: self._on_custom_ctx()))
        ui.pack(self.ctx_combo, side="left")
        cur_ctx = str(self.cfg.get("custom_ctx", 8192))
        _combo_set(self.ctx_combo, ["4096", "8192", "16384", "32768"],
                   ["4096", "8192", "16384", "32768"].index(cur_ctx)
                   if cur_ctx in ("4096", "8192", "16384", "32768") else 1)

        rs = self._reg(ui.row(p))
        ui.pack(rs, fill="x", pady=1)
        ui.pack(ui.label(rs, "源："), side="left")
        self.model_source_combo = self._reg(ui.combo(
            rs, values=["先测速，自动选最快"], width=44))
        ui.pack(self.model_source_combo, side="left", padx=(0, 6))
        self.model_env_label = self._reg(ui.label(rs, ""))
        ui.pack(self.model_env_label, side="left")

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        self.model_box = self._reg(ui.drop_box(
            r2, on_file=self._pick_model,
            placeholder="模型 .gguf（可拖入）"))
        ui.pack(self.model_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_choose_model = self._reg(ui.button(
            r2, "选择模型", on_click=self.on_choose_model))
        ui.pack(self.btn_choose_model, side="left", padx=(0, 6))
        self.btn_choose_exe = self._reg(ui.button(
            r2, "自选exe", on_click=self.on_choose_exe))
        ui.pack(self.btn_choose_exe, side="left")

        self._section(p, "④ 启动引擎")
        r3 = self._reg(ui.row(p))
        ui.pack(r3, fill="x", pady=1)
        self.btn_start = self._reg(
            ui.button(r3, "启动引擎", on_click=self.on_start))
        ui.pack(self.btn_start, side="left", padx=(0, 6))
        self.btn_stop = self._reg(ui.button(
            r3, "停止", on_click=self.on_stop, state="disabled"))
        ui.pack(self.btn_stop, side="left", padx=(0, 8))
        self.engine_status = self._reg(ui.label(r3, "引擎未启动"))
        ui.pack(self.engine_status, side="left")
        self._on_tier()

    def _build_step_job(self, p):
        ui = self.ui
        self._section(p, "⑤ 翻译（自动识别全部格式，输出保持原目录结构）")

        r1 = self._reg(ui.row(p))
        ui.pack(r1, fill="x", pady=1)
        ui.pack(ui.label(r1, "输入："), side="left")
        self.in_box = self._reg(ui.drop_box(
            r1, on_file=self._pick_in, placeholder="待翻译目录（如 gamedata）"))
        ui.pack(self.in_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_in = self._reg(ui.button(r1, "选择", on_click=self.on_choose_in))
        ui.pack(self.btn_in, side="left", padx=(0, 4))
        self.btn_scan = self._reg(ui.button(r1, "扫描选档", on_click=self.on_scan_dir))
        ui.pack(self.btn_scan, side="left")
        if self.cfg.get("in_dir"):
            self.in_box.set_text(self.cfg["in_dir"])

        r2 = self._reg(ui.row(p))
        ui.pack(r2, fill="x", pady=1)
        ui.pack(ui.label(r2, "输出："), side="left")
        self.out_box = self._reg(ui.drop_box(
            r2, on_file=self._pick_out,
            placeholder="输出目录（默认 输入目录/translated_hy_mt2）"))
        ui.pack(self.out_box.widget, side="left", fill="x", expand=True,
                padx=(0, 6))
        self.btn_out = self._reg(ui.button(r2, "选择", on_click=self.on_choose_out))
        ui.pack(self.btn_out, side="left")
        if self.cfg.get("out_dir"):
            self.out_box.set_text(self.cfg["out_dir"])

        r3 = self._reg(ui.row(p))
        ui.pack(r3, fill="x", pady=1)
        ui.pack(ui.label(r3, "类型："), side="left")
        mode_vals = [MODE_LABELS[m] for m in MODE_ORDER]
        self.mode_combo = self._reg(ui.combo(r3, values=mode_vals, width=22))
        ui.pack(self.mode_combo, side="left", padx=(0, 8))
        mi = MODE_ORDER.index(self.cfg["mode"]) if \
            self.cfg["mode"] in MODE_ORDER else 0
        _combo_set(self.mode_combo, mode_vals, mi)
        ui.pack(ui.label(r3, "源语言："), side="left")
        lang_vals = ["auto（按文件语言）", "eng", "rus", "ukr"]
        self.lang_combo = self._reg(ui.combo(r3, values=lang_vals, width=18))
        ui.pack(self.lang_combo, side="left", padx=(0, 6))
        li = {"auto": 0, "eng": 1, "rus": 2, "ukr": 3}.get(
            self.cfg.get("src_lang", "auto"), 0)
        _combo_set(self.lang_combo, lang_vals, li)
        self.force_chk = self._reg(ui.checkbox(r3, "强制术语", on_toggle=None))
        ui.pack(self.force_chk, side="left")
        _chk_set(self.force_chk, self.cfg.get("glossary_force", False))

        # 引用层选项：默认自动识别 ID 引用、输出完整补丁、保护专名。
        # 不写死任何 MOD 路径；开关都可在界面改，并写入 config.json。
        r4 = self._reg(ui.row(p))
        ui.pack(r4, fill="x", pady=1)
        ui.pack(ui.label(r4, "引用层："), side="left")
        self.ref_chk = self._reg(ui.checkbox(
            r4, "识别 ID 引用层（推荐）", on_toggle=None))
        ui.pack(self.ref_chk, side="left", padx=(0, 6))
        _chk_set(self.ref_chk, self.cfg.get("reference_layer", True))
        self.copy_ref_chk = self._reg(ui.checkbox(
            r4, "输出完整补丁（复制引用层）", on_toggle=None))
        ui.pack(self.copy_ref_chk, side="left", padx=(0, 6))
        _chk_set(self.copy_ref_chk, self.cfg.get("copy_reference", True))
        self.only_ref_chk = self._reg(ui.checkbox(
            r4, "只译被引用条目", on_toggle=None))
        ui.pack(self.only_ref_chk, side="left")
        _chk_set(self.only_ref_chk, self.cfg.get("only_referenced", False))

        r5 = self._reg(ui.row(p))
        ui.pack(r5, fill="x", pady=(0, 1))
        self.protect_chk = self._reg(ui.checkbox(
            r5, "保护专名/歌名/型号", on_toggle=None))
        ui.pack(self.protect_chk, side="left", padx=(0, 6))
        _chk_set(self.protect_chk, self.cfg.get("protect_proper_v2", False))
        self.preview_chk = self._reg(ui.checkbox(
            r5, "先预览（不写盘）", on_toggle=None))
        ui.pack(self.preview_chk, side="left")
        _chk_set(self.preview_chk, self.cfg.get("preview_reference", False))
        self.literal_chk = self._reg(ui.checkbox(
            r5, "翻译脚本/XML 字面文本", on_toggle=None))
        ui.pack(self.literal_chk, side="left", padx=(6, 0))
        _chk_set(self.literal_chk, self.cfg.get("translate_literals", True))

        r6 = self._reg(ui.row(p))
        ui.pack(r6, fill="x", pady=(1, 0))
        self.session_chk = self._reg(ui.checkbox(
            r6, "自动生成当前MOD会话词表（名字/物品/地点，本机缓存）",
            on_toggle=None))
        ui.pack(self.session_chk, side="left")
        _chk_set(self.session_chk, self.cfg.get("session_glossary", True))
        self.tm_chk = self._reg(ui.checkbox(
            r6, "重复句精确复用（TM-lite，高重复时自动）",
            on_toggle=None))
        ui.pack(self.tm_chk, side="left", padx=(10, 0))
        _chk_set(self.tm_chk, self.cfg.get("tm_lite", True))

        ex1 = self._reg(ui.row(p))
        ui.pack(ex1, fill="x", pady=(3, 0))
        ui.pack(ui.label(ex1, "附加提示词/约束（可选，追加到内置规则后）："),
                side="left")
        self.btn_extra_demo = self._reg(ui.button(
            ex1, "填入示例", on_click=self.on_extra_demo))
        ui.pack(self.btn_extra_demo, side="left", padx=(6, 0))
        ex2 = self._reg(ui.row(p))
        ui.pack(ex2, fill="x", pady=(0, 1))
        self.extra_prompt = self._reg(ui.text(ex2, height=2, wrap="word"))
        ui.pack(self.extra_prompt, side="left", fill="x", expand=True)
        _multiline_set(self.extra_prompt, self.cfg.get("prompt_extra", ""))

        g1 = self._reg(ui.row(p))
        ui.pack(g1, fill="x", pady=(3, 1))
        ui.pack(ui.label(g1, "术语："), side="left")
        self.gk_entry = self._reg(ui.entry(g1, width=16))
        ui.pack(self.gk_entry, side="left", padx=(0, 3))
        ui.pack(ui.label(g1, "→"), side="left", padx=(0, 3))
        self.gv_entry = self._reg(ui.entry(g1, width=16))
        ui.pack(self.gv_entry, side="left", padx=(0, 4))
        self.btn_gloss_add = self._reg(ui.button(
            g1, "添加", on_click=self.on_gloss_add))
        ui.pack(self.btn_gloss_add, side="left", padx=(0, 3))
        self.btn_gloss_del = self._reg(ui.button(
            g1, "删除", on_click=self.on_gloss_del))
        ui.pack(self.btn_gloss_del, side="left", padx=(0, 4))
        self.gloss_count = self._reg(ui.label(g1, "共 0 条"))
        ui.pack(self.gloss_count, side="left")

        g2 = self._reg(ui.row(p))
        ui.pack(g2, fill="x", pady=1)
        self.btn_gloss_import = self._reg(ui.button(
            g2, "导入JSON", on_click=self.on_gloss_import))
        ui.pack(self.btn_gloss_import, side="left", padx=(0, 4))
        self.btn_gloss_export = self._reg(ui.button(
            g2, "导出JSON", on_click=self.on_gloss_export))
        ui.pack(self.btn_gloss_export, side="left", padx=(0, 4))
        self.btn_gloss_clear = self._reg(ui.button(
            g2, "清空", on_click=self.on_gloss_clear))
        ui.pack(self.btn_gloss_clear, side="left")

        r5 = self._reg(ui.row(p))
        ui.pack(r5, fill="x", pady=(4, 1))
        self.btn_run = self._reg(
            ui.button(r5, "开始翻译", on_click=self.on_run))
        ui.pack(self.btn_run, side="left", padx=(0, 6))
        self.btn_run_cancel = self._reg(ui.button(
            r5, "取消", on_click=self.on_cancel_run, state="disabled"))
        ui.pack(self.btn_run_cancel, side="left")

    def current_tier(self):
        label = _combo_get(self.tier_combo)
        return _LABEL_TO_TIER.get(label, "7b")

    def current_variant(self):
        label = _combo_get(self.variant_combo)
        if label.startswith("CUDA 13"):
            return "cuda13"
        if label.startswith("CUDA"):
            return "cuda"
        if label.startswith("Vulkan"):
            return "vulkan"
        return "cpu"

    def _on_variant(self, save_chosen=True):
        v = self.current_variant()
        if v == "cuda13" and not getattr(self, "_hw_cuda13_ok", False):
            self.log("当前驱动/显卡可能不支持 CUDA 13.4（需 580+ 驱动 / "
                     "Turing+）；仍可尝试，启动失败会自动回退 CUDA 12.4", "warn")
        self.cfg["variant"] = v
        if save_chosen:
            self.cfg["variant_chosen"] = True
        save_config(self.cfg)
        if getattr(self, "source_combo", None) is not None:
            _combo_set(self.source_combo, ["先测速，自动选最快"], 0)
            try:
                self.env_label.configure(text="")
            except Exception:
                pass
        self.refresh_local_status()
        if v in GPU_VARIANTS and not self.find_exe(v):
            self.log("%s 运行库未安装，点②「下载运行库」"
                     % VARIANT_LABELS[v].split("（")[0], "warn")

    def model_candidates(self, tier=None):
        tier = tier or self.current_tier()
        cands = []
        if tier == "custom":
            p = self.cfg.get("custom_model_path", "")
            return [p] if p else []
        cfg_path = self.cfg.get("model_paths", {}).get(tier)
        if cfg_path:
            cands.append(cfg_path)
        cands.append(os.path.join(self.cfg.get("model_dir", _MODELS_DIR),
                                  TIER_FILE[tier]))
        cands.append(os.path.join(_MODELS_DIR, TIER_FILE[tier]))
        cands.append(os.path.join(_SHIPPED_MODELS_DIR, TIER_FILE[tier]))
        return cands

    def find_model(self, tier=None):
        for c in self.model_candidates(tier):
            if c and os.path.isfile(c):
                return c
        return None

    def find_exe(self, variant=None):
        """严格按 variant 查找，**绝不跨后端回退**：
        否则选了 CUDA/Vulkan 却静默跑 CPU（GPU 不工作且难察觉）。"""
        variant = variant or self.current_variant()
        manual = self.cfg.get("exe_paths", {}).get(variant)
        if manual and os.path.isfile(manual):
            if self._exe_matches_variant(manual, variant):
                return manual
        for base in _bin_dirs():
            direct = os.path.join(base, variant, "llama-server.exe")
            if not os.path.isfile(direct):
                continue
            if variant in ("cuda", "cuda13") and not self._cudart_present(
                    os.path.dirname(direct)):
                continue
            return direct
        return None

    @staticmethod
    def _cudart_present(d):
        """CUDA 运行时是否**完整**安装。

        1.6.0 之前只要任意一个文件以 cudart/cublas/nvrtc 开头就算装好 ——
        半套解压也会被判为可用，随后 ggml-cuda 初始化失败、静默回退 CPU。
        现在要求 3 组标记**全部**命中；安装成功时会额外写一个 marker。
        """
        try:
            files = [f.lower() for f in os.listdir(d)]
        except OSError:
            return False
        has_core = any(f.startswith("cudart64") for f in files)
        if _CUDART_MARK in files:
            return has_core          # marker 在但核心 DLL 被删 → 仍需重装
        return all(any(f.startswith(m) for f in files) for m in _CUDART_MARKS)

    @staticmethod
    def _exe_matches_variant(exe_path, variant):
        """校验手动指定的 llama-server.exe 所在目录确实是该后端构建。"""
        d = os.path.dirname(exe_path)
        if os.path.basename(os.path.dirname(d)).lower() == variant:
            return True
        try:
            files = {f.lower() for f in os.listdir(d)}
        except OSError:
            return False
        if variant in ("cuda", "cuda13") and any(
                f.startswith("ggml-cuda") for f in files):
            return True
        if variant == "vulkan" and any("vulkan" in f for f in files):
            return True
        if variant == "cpu":
            has_gpu_backend = any(
                f.startswith("ggml-cuda") or "vulkan" in f
                for f in files)
            return not has_gpu_backend
        return False

    def refresh_local_status(self):
        model = self.find_model()
        exe = self.find_exe()

        def upd():
            if getattr(self, "model_box", None) is not None:
                self.model_box.set_text(model or "")
            if self.server and self.server.is_running():
                self._set_label(self.engine_status, "运行中（端口 %d）"
                                % self.server.port)
            else:
                bits = ["模型就绪" if model else "模型缺失",
                        "运行库就绪" if exe else "运行库缺失"]
                self._set_label(self.engine_status,
                                "未启动（%s）" % " · ".join(bits))
            env = ("运行库已装 · %s" % _DATA_ROOT) if exe else "运行库未安装"
            self._set_label(self.env_label, env)
        self.ui_call(upd)
        try:
            self._persist_cfg()
        except Exception:
            pass

    def _set_label(self, w, text):
        try:
            w.configure(text=text)
            return
        except Exception:
            pass
        try:
            w.setText(text)
        except Exception:
            pass

    def _push_prompt_extra(self):
        """把「附加提示词/约束」推给引擎对象，并留痕一次。

        **只读 `_prompt_extra_text` 缓存，不碰控件**：本方法由 on_start/on_run
        的**工作线程**调用；tk.Text 在非主线程读取会抛
        `RuntimeError: main thread is not in main loop`（1.6.0 之前就是这样被
        吞成空串，功能实际从未生效）。缓存由 UI 线程的 _persist_cfg 更新。
        """
        if self.server is None:
            return
        txt = str(getattr(self, "_prompt_extra_text", "") or "").strip()
        self.server.prompt_extra = txt
        if txt:
            self.ui_call(
                self.log, "已启用附加提示词（%d 字符），作为「补充要求」"
                          "追加在内置规则后" % len(txt))

    def _persist_cfg(self):
        self.cfg["tier"] = self.current_tier()
        self.cfg["variant"] = self.current_variant()
        try:
            self.cfg["in_dir"] = self.in_box.get_text()
            self.cfg["out_dir"] = self.out_box.get_text()
        except Exception:
            pass
        lang = _combo_get(self.lang_combo)
        self.cfg["src_lang"] = re.split(r"[\s（(]", lang, 1)[0]
        for k in MODE_ORDER:
            if _combo_get(self.mode_combo).startswith(k):
                self.cfg["mode"] = k
                break
        self._mode_plain = self.cfg["mode"]
        try:
            self.cfg["glossary_force"] = _chk_get(self.force_chk)
        except Exception:
            pass
        for key, widget in (
                ("reference_layer", self.ref_chk),
                ("copy_reference", self.copy_ref_chk),
                ("only_referenced", self.only_ref_chk),
                ("protect_proper_v2", self.protect_chk),
                ("preview_reference", self.preview_chk),
                ("translate_literals", self.literal_chk),
                ("session_glossary", self.session_chk),
                ("tm_lite", self.tm_chk)):
            try:
                self.cfg[key] = _chk_get(widget)
            except Exception:
                pass
        try:
            self.cfg["prompt_extra"] = _multiline_get(
                self.extra_prompt).strip()
            # UI 线程读好 → 缓存给工作线程用（见 _push_prompt_extra）
            self._prompt_extra_text = self.cfg["prompt_extra"]
        except Exception:
            pass
        if not save_config(self.cfg) and not self._cfg_save_warned:
            self._cfg_save_warned = True
            self.log("配置保存失败（%s）：本次设置只在内存生效，重启后会丢失"
                     % (_LAST_CONFIG_ERROR or "未知原因"), "warn")
        elif _LAST_CONFIG_ERROR == "":
            self._cfg_save_warned = False

    def on_extra_demo(self):
        """填入示例模板：说明这个框是用来放哪类要求的（可整段删改）。"""
        _multiline_set(self.extra_prompt, EXTRA_PROMPT_DEMO)
        self.cfg["prompt_extra"] = EXTRA_PROMPT_DEMO
        self._prompt_extra_text = EXTRA_PROMPT_DEMO
        self.log("已填入示例模板（可整段删改）。注意此处是软引导："
                 "模型多数情况会遵守，但不保证 100%；"
                 "必须强制统一的译名请用「强制术语」放进术语库。", "warn")

    def refresh_gloss_view(self):
        n = len(self.gloss_user)

        def upd():
            self._set_label(self.gloss_count, "共 %d 条" % n)
        self.ui_call(upd)

    def on_gloss_add(self):
        k = _entry_get(self.gk_entry).strip()
        v = _entry_get(self.gv_entry).strip()
        if not k or not v:
            self.log("请填写原文与强制译文两项", "warn")
            return
        self.gloss_user[k] = v
        self._gloss_set[k] = v
        self._gloss_deleted.discard(k)
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        _entry_set(self.gk_entry, "")
        _entry_set(self.gv_entry, "")
        self.refresh_gloss_view()
        self.log("已保存术语：%s => %s（共 %d 条）"
                 % (k, v, len(self.gloss_user)), "ok")

    def on_gloss_del(self):
        k = _entry_get(self.gk_entry).strip()
        if not k:
            self.log("在左侧输入框填写要删除的原文", "warn")
            return
        if k in self.gloss_user:
            del self.gloss_user[k]
            if k in self._gloss_set:
                del self._gloss_set[k]          # 删的是自己加的
            else:
                self._gloss_deleted.add(k)      # 删的是内置词条 → 记墓碑
            try:
                save_user_glossary(self._gloss_set, self._gloss_deleted,
                                   self._gloss_clear)
                self.log("已删除术语：%s" % k)
            except Exception as e:
                self.log("术语库保存失败：%s" % e, "err")
        else:
            self.log("术语库中没有该原文：%s" % k, "warn")
        _entry_set(self.gk_entry, "")
        _entry_set(self.gv_entry, "")
        self.refresh_gloss_view()

    def on_gloss_clear(self):
        if not self.gloss_user:
            return
        ok = self.ui.ask_yes_no(
            self._tk, "清空术语库",
            "确定清空内置术语库的全部 %d 条记录吗？" % len(self.gloss_user))
        if not ok:
            return
        self.gloss_user = {}
        self._gloss_set = {}
        self._gloss_deleted = set()
        self._gloss_clear = True               # 连内置词条一起隐藏
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        self.refresh_gloss_view()
        self.log("术语库已清空（内置词条被用户层隐藏；重新导入即可恢复）")

    def on_gloss_import(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "导入术语表 JSON（合并，同名词条以导入文件为准）",
                filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            self.log("术语表读取失败：%s" % e, "err")
            return
        if not isinstance(data, dict):
            self.log("术语表格式应为 {\"原文\": \"译文\"} 的 JSON 对象", "err")
            return
        incoming = flatten_glossary(data)
        if not incoming:
            self.log("术语表为空或格式无法识别（支持扁平表与 {\"分类\": {...}} 分类表）",
                     "err")
            return
        before = len(self.gloss_user)
        self.gloss_user.update(incoming)
        self._gloss_set.update(incoming)
        # 导入的条目优先生效：把它们从"墓碑"里移除
        self._gloss_deleted.difference_update(incoming)
        try:
            save_user_glossary(self._gloss_set, self._gloss_deleted,
                               self._gloss_clear)
        except Exception as e:
            self.log("术语库保存失败：%s" % e, "err")
            return
        self.refresh_gloss_view()
        self.log("已从 %s 合并 %d 条（术语库 %d → %d 条）"
                 % (os.path.basename(f), len(incoming), before,
                    len(self.gloss_user)), "ok")

    def on_gloss_export(self):
        try:
            f = self.ui.ask_save_file(
                self._tk, "导出术语表 JSON",
                initialfile="glossary_export.json",
                filetypes=[("JSON", "*.json")])
        except Exception:
            f = None
        if not f:
            return
        if not f.lower().endswith(".json"):
            f += ".json"
        try:
            tmp = f + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.gloss_user, fh, ensure_ascii=False,
                          indent=1, sort_keys=True)
            os.replace(tmp, f)
        except Exception as e:
            self.log("导出失败：%s" % e, "err")
            return
        self.log("已导出 %d 条术语：%s" % (len(self.gloss_user), f), "ok")

    def _pick_model(self, path):
        if path.lower().endswith(".gguf"):
            tier = self.current_tier()
            if tier == "custom":
                self.cfg["custom_model_path"] = path
            else:
                self.cfg.setdefault("model_paths", {})[tier] = path
            save_config(self.cfg)
            self.refresh_local_status()

    def _pick_in(self, path):
        if os.path.isdir(path):
            self.cfg["in_dir"] = path
            self._persist_cfg()
            self._default_out(path)

    def _default_out(self, in_dir):
        """输出目录默认 输入目录/translated_hy_mt2（用户可改）。"""
        cur = self.out_box.get_text() if getattr(self, "out_box", None) else ""
        if not cur:
            d = os.path.join(in_dir, "translated_hy_mt2")
            self.out_box.set_text(d)
            self.cfg["out_dir"] = d
            save_config(self.cfg)

    def _pick_out(self, path):
        if os.path.isdir(path):
            self.cfg["out_dir"] = path
            self._persist_cfg()

    def _ask_dir(self, title):
        try:
            from tkinter import filedialog
            d = filedialog.askdirectory(title=title, parent=self._tk)
            return d or None
        except Exception:
            self.log("无法弹出目录选择框，请把文件夹直接拖入输入框", "warn")
            return None

    def on_choose_model(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "选择 GGUF 模型",
                filetypes=[("GGUF 模型", "*.gguf"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if f:
            self._pick_model(f)

    def on_choose_exe(self):
        try:
            f = self.ui.ask_open_file(
                self._tk, "选择 llama-server.exe",
                filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        except Exception:
            f = None
        if f and os.path.basename(f).lower() == "llama-server.exe":
            self.cfg.setdefault("exe_paths", {})[self.current_variant()] = f
            save_config(self.cfg)
            self.refresh_local_status()

    def on_choose_in(self):
        d = self._ask_dir("选择待翻译目录")
        if d:
            self.in_box.set_text(d)
            self._pick_in(d)

    def on_choose_out(self):
        d = self._ask_dir("选择译文输出目录")
        if d:
            self.out_box.set_text(d)
            self._pick_out(d)

    def _on_custom_ctx(self):
        try:
            v = int(_combo_get(self.ctx_combo))
        except Exception:
            return
        if v in CUSTOM_CTX_CHOICES:
            self.cfg["custom_ctx"] = v
            save_config(self.cfg)
            if self.current_tier() == "custom":
                self.refresh_local_status()

    def _on_tier(self, save_chosen=True):
        """切档：更新状态；引擎运行中切档提示需重启。"""
        tier = self.current_tier()
        self.cfg["tier"] = tier
        if save_chosen:
            self.cfg["tier_chosen"] = True
        save_config(self.cfg)
        try:
            self.ui.set_state(self.ctx_combo,
                              "normal" if tier == "custom" else "disabled")
        except Exception:
            pass
        if getattr(self, "model_box", None) is not None:
            self.refresh_local_status()
        if getattr(self, "model_source_combo", None) is not None:
            _combo_set(self.model_source_combo,
                       ["先测速，自动选最快"], 0)
            try:
                self.model_env_label.configure(text="")
            except Exception:
                pass
        if self.server and self.server.is_running():
            self.log("已切换模型档，运行中的引擎仍是旧模型，重启引擎生效",
                     "warn")

    def _probe_task_key(self, kind):
        if kind == "rt":
            return "bin_" + self.current_variant()
        tier = self.current_tier()
        return TIER_PROFILES[tier]["task"] or \
            TIER_PROFILES["mt2"]["task"]

    def _sources_for(self, kind):
        key = self._probe_task_key(kind)
        return DOWNLOAD_TASKS[key], key

    def _source_combo(self, kind):
        return self.source_combo if kind == "rt" else self.model_source_combo

    def _populate_sources(self, kind, results):
        """把测速结果填入对应区的“源”下拉框：可达在前（按速度），
        不可达在后供人工尝试。"""
        combo = self._source_combo(kind)
        labels = []
        for r in results:
            if r.ok:
                labels.append("%s · %.2f MB/s" % (r.name, r.speed))
            else:
                labels.append("%s · 不可达" % r.name)
        if not labels:
            labels = ["测速失败，请重试"]
        _combo_set(combo, labels, 0)

    def on_probe(self, kind):
        """测速：kind='rt' 当前后端运行库；kind='model' 当前模型档。
        未测速过时，下载会自动先测速再拉取。"""
        if kind == "model" and self.current_tier() == "custom":
            self.log("自选档使用本机 .gguf，无需测速下载", "warn")
            return
        task, key = self._sources_for(kind)
        btn = self.btn_probe_rt if kind == "rt" else self.btn_probe_model
        self.ui.set_state(btn, "disabled")
        self.log("测速：%s" % task["label"])

        def work():
            try:
                results = run_probe(
                    task["sources"],
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag))
                self.probe_cache[key] = results
                env = detect_environment(results)

                def done():
                    self._populate_sources(kind, results)
                    label = self.model_env_label if kind == "model" \
                        else self.env_label
                    self._set_label(label, env)
                    self.log(env)
                    self.ui.set_state(btn, "normal")
                self.ui_call(done)
            except Exception as e:
                self.ui_call(self.log, "测速失败：%s" % e, "err")
                self.ui_call(self.ui.set_state, btn, "normal")
        threading.Thread(target=work, daemon=True).start()

    def _resolve_download_sources(self, kind, key, task, chosen_label=""):
        """按用户在“源”下拉框的选择决定下载顺序。
        返回 (ordered[(name,url)], auto_probed, chosen_result|None)。
        选中源排第一，其余可达源按测速排序依次备援，失败自动切换。

        chosen_label 由 **UI 线程**读好再传进来：本方法跑在下载工作线程，
        直接去读 Tk 控件（_combo_get）属于跨线程访问 GUI，会出现偶发异常或
        读到半更新状态（文件自己的 _plan_engine 就立了"控件值必须由 UI 线程
        预先传入"的规矩，这里是唯一漏网点）。
        """
        results = self.probe_cache.get(key)
        auto = False
        if results is None:
            results = run_probe(
                task["sources"],
                log=lambda m, tag="info": self.ui_call(self.log, m, tag))
            self.probe_cache[key] = results
            auto = True
            self.ui_call(lambda: self._populate_sources(kind, results))
        ok = sorted((r for r in results if r.ok),
                    key=lambda r: r.speed, reverse=True)
        chosen = None
        if chosen_label and " · " in chosen_label:
            sel_name = chosen_label.split(" · ", 1)[0]
            chosen = next((r for r in results if r.name == sel_name), None)
        ordered = []
        if chosen is not None:
            ordered.append((chosen.name, chosen.url))
        for r in ok:
            if chosen is None or r.name != chosen.name:
                ordered.append((r.name, r.url))
        seen = {n for n, _ in ordered}
        for n, u in task["sources"]:
            if n not in seen:
                ordered.append((n, u))
        return ordered, auto, chosen

    def on_download(self, kind):
        if self.dl_cancel is not None:
            self.log("已有下载任务在进行", "warn")
            return
        task, key = self._sources_for(kind)
        if kind == "model" and self.current_tier() == "custom":
            self.log("自选档请点「选择模型」指定本机 .gguf，无需下载", "warn")
            return
        self.dl_cancel = threading.Event()
        chosen_label = _combo_get(self._source_combo(kind))

        def progress(done, total, speed, eta, name):
            if total and done - getattr(self, "_last_ui_byte", -1) > 8*1024*1024:
                self._last_ui_byte = done
                self.ui_call(self.log,
                             "  %.0f%%（%s/%s）%.1f MB/s 剩余 %s"
                             % (100.0*done/total, _fmt_size(done),
                                _fmt_size(total), speed, _fmt_eta(eta)))

        def work():
            try:
                ordered, auto, chosen = self._resolve_download_sources(
                    kind, key, task, chosen_label)
                if auto:
                    self.ui_call(self.log, "自动测速完成，可在“源”下拉框改选")
                if chosen is not None and not chosen.ok:
                    self.ui_call(self.log,
                                 "你选择的 %s 测速不可达，将先尝试它、"
                                 "失败后自动切换其他源" % chosen.name, "warn")
                if task["kind"] == "model":
                    model_dir = self.cfg.get("model_dir") or _MODELS_DIR
                    os.makedirs(model_dir, exist_ok=True)
                    target = os.path.join(model_dir, task["filename"])
                else:
                    target = os.path.join(
                        _DOWNLOAD_DIR, task["variant"] + "_runtime.zip")
                if os.path.isfile(target) and task["kind"] == "model":
                    self.ui_call(self.log, "模型已存在：%s" % target, "warn")
                    return
                if task["kind"] == "zip":
                    self.ui_call(self.log,
                                 "提示：运行库将从 GitHub 及其公共镜像下载，"
                                 "下载后会按官方 SHA-256 校验；原始包保留在 %s"
                                 % _DOWNLOAD_DIR, "warn")
                self.ui_call(self.log, "下载：%s（首选 %s，共 %d 个源备援）"
                             % (task["label"], ordered[0][0], len(ordered) - 1))
                # 校验放在 download_one 内部逐个源进行：某个镜像给了坏文件时
                # 删掉它并自动换下一个源（1.6.0 之前只是删文件让用户手动重试）。
                digest = download_one(
                    ordered, target,
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                    progress=progress,
                    cancel_event=self.dl_cancel,
                    verify=lambda p: verify_artifact(
                        p, task,
                        log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                        known_hashes=self.cfg.get("artifact_hashes")))
                self.cfg.setdefault("artifact_hashes", {})[
                    os.path.basename(target)] = digest
                save_config(self.cfg)
                if task["kind"] == "zip":
                    dest = os.path.join(_BIN_DIR, task["variant"])
                    exe = extract_zip(
                        target, dest, "llama-server.exe",
                        log=lambda m, tag="info": self.ui_call(self.log, m, tag))
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                    extra = task.get("extra_runtime")
                    if extra:
                        self._install_extra_runtime(extra, dest)
                    self.ui_call(self.log, "运行库就绪：%s" % exe, "ok")
                else:
                    tier = TIER_BY_TASK[key]
                    self.cfg.setdefault("model_paths", {})[tier] = target
                    save_config(self.cfg)
                    self.ui_call(self.log, "模型下载完成：%s" % target, "ok")
            except DownloadCancelled:
                self.ui_call(self.log, "已取消，断点已保留")
            except InvalidArtifact as e:
                self.ui_call(self.log,
                             "下载产物校验未通过（已依次尝试所有源）：%s" % e,
                             "err")
            except Exception as e:
                self.ui_call(self.log, "下载失败：%s" % e, "err")
            finally:
                self.ui_call(self._after_download)
        threading.Thread(target=work, daemon=True).start()
        self.ui.set_state(self.btn_dl_rt, "disabled")
        self.ui.set_state(self.btn_dl_model, "disabled")
        self.ui.set_state(self.btn_dl_cancel, "normal")

    def _after_download(self):
        self.dl_cancel = None
        self._last_ui_byte = -1
        self.ui.set_state(self.btn_dl_rt, "normal")
        self.ui.set_state(self.btn_dl_model, "normal")
        self.ui.set_state(self.btn_dl_cancel, "disabled")
        self.refresh_local_status()

    def _install_extra_runtime(self, extra, dest):
        """CUDA 后端第二步：下载并解压 cudart 运行时；完整安装则跳过。"""
        if self._cudart_present(dest):
            self.ui_call(self.dl_log,
                         "%s 已存在，跳过" % extra["label"])
            return
        self.ui_call(self.dl_log,
                     "继续下载%s（CUDA 必需，否则会回退 CPU）…"
                     % extra["label"])
        sources = _gh_sources(extra["url"])
        rt_zip = os.path.join(_DOWNLOAD_DIR, "cudart_runtime.zip")
        results = run_probe(sources)
        ok = [(r.name, r.url) for r in results if r.ok]
        if not ok:
            raise IOError("无法下载 %s：所有源不可达" % extra["label"])

        def rt_progress(done, total, speed, eta, name):
            if done % (20 * 1024 * 1024) >= 1048576:
                return
            self.ui_call(self.dl_log, "  cudart：%.0f%% %.1f MB/s"
                         % (100.0 * done / total if total else 0, speed))

        rt_task = {"kind": "zip", "name_prefix": "cudart64",
                   "size_hint": 0.37, "sha256": extra.get("sha256", "")}
        digest = download_one(
            ok, rt_zip,
            log=lambda m: self.ui_call(self.dl_log, m),
            progress=rt_progress,
            cancel_event=self.dl_cancel,
            verify=lambda p: verify_artifact(
                p, rt_task,
                log=lambda m, tag="info": self.ui_call(self.dl_log, m),
                known_hashes=self.cfg.get("artifact_hashes")))
        self.cfg.setdefault("artifact_hashes", {})["cudart_runtime.zip"] = digest
        save_config(self.cfg)
        _extract_zip_into(rt_zip, dest)
        try:
            with open(os.path.join(dest, _CUDART_MARK), "w",
                      encoding="utf-8") as f:
                json.dump({"sha256": digest, "installed": time.strftime(
                    "%Y-%m-%d %H:%M:%S")}, f)
        except OSError:
            pass
        try:
            os.remove(rt_zip)
        except OSError:
            pass
        self.ui_call(self.dl_log, "%s 安装完成" % extra["label"])

    def on_cancel_download(self):
        if self.dl_cancel:
            self.dl_cancel.set()
            self.dl_log("正在取消…")

    def _selected_mode(self):
        for k in ("text", "cfgxml", "ltx", "scriptE", "scriptL"):
            if _combo_get(self.mode_combo).startswith(k):
                return k
        return "text"

    def on_scan_dir(self):
        if self.scan_cancel is not None:
            self.scan_cancel.set()
            self.log("正在取消勘察…", "warn")
            return
        d = self.in_box.get_text()
        if not d or not os.path.isdir(d):
            self.log("请先选择有效的待翻译目录", "warn")
            return
        if os.path.dirname(os.path.abspath(d)) == os.path.abspath(d):
            try:
                ok = self.ui.ask_yes_no(
                    self._tk, "目录看起来是磁盘根目录",
                    "「%s」是磁盘根目录，勘察会遍历整盘、耗时可能很长。\n"
                    "通常应选游戏或模组的 gamedata / configs 目录。\n"
                    "仍要继续吗？" % d)
            except Exception:
                ok = False
            if not ok:
                self.log("已取消勘察（建议改选 gamedata 或 configs 目录）")
                return
        mode = self._selected_mode()
        self._mode_plain = mode
        self.scan_cancel = threading.Event()
        self._set_label(self.btn_scan, "取消勘察")
        self.log("正在勘察目录（文件/最长文本/上下文需求）…再点一次可取消")

        def work():
            try:
                info = analyze_input_dir(
                    d, mode,
                    log=lambda m: self.ui_call(self.log, m),
                    cancel=self.scan_cancel)
                if not info.get("cancelled"):
                    self.scan_info = info
                    self.scan_dir = d
                self.ui_call(self._report_scan, info, mode)
            except Exception as e:
                self.ui_call(self.log, "勘察失败：%s" % e, "err")
            finally:
                self.scan_cancel = None
                self.ui_call(self._set_label, self.btn_scan, "扫描选档")
        threading.Thread(target=work, daemon=True).start()

    def _report_scan(self, info, mode):
        if info.get("cancelled"):
            self.log("已取消勘察（统计不完整，档位保持不变）", "warn")
            return
        rec_tier = info["recommend"]
        if info.get("truncated"):
            self.log("勘察达到文件数上限提前结束，结果只覆盖已读部分，"
                     "仅供参考", "warn")
        ready = [t for t in TIER_ORDER if self.find_model(t)]
        if rec_tier not in ready:
            pref = [t for t in (rec_tier, "mt2", "7b", "custom")
                    if t in ready]
            if pref:
                self.log("推荐的 %s 档模型尚未下载，已改选本机就绪的 %s 档"
                         "（下载对应模型后可手动切回）"
                         % (TIER_PROFILES[rec_tier]["combo"][:2],
                            TIER_PROFILES[pref[0]]["combo"][:2]), "warn")
                rec_tier = pref[0]
        rec = TIER_PROFILES[rec_tier]
        self.log("勘察结果：文件 %d 个 / %.1f MB；最大单文件 %.0f KB；"
                 "可翻译文本约 %d 条，最长 %d 字符（%s）"
                 % (info["files"], info["bytes"] / 1048576.0,
                    info["max_file"][0] / 1024.0, info["n_strings"],
                    info["max_str"],
                    os.path.basename(info["max_str_file"]) or "-"))
        for t in BUILTIN_TIERS:
            n = info["overlong"][t]
            if n:
                self.log("  %s 档将对 %d 条超长文本自动分句翻译"
                         % (TIER_PROFILES[t]["combo"][:2], n))
        self.log("推荐档位：%s" % rec["combo"])
        idx = TIER_ORDER.index(rec_tier)
        _combo_set(self.tier_combo, TIER_LABELS, idx)
        self._on_tier()
        ctx = decide_ctx(rec_tier, info,
                         cap=(int(self.cfg.get("custom_ctx", 8192))
                              if rec_tier == "custom" else None))
        self.log("该目录在推荐档的自动上下文：%d token（引擎启动时生效）" % ctx)

    def _plan_engine(self, allow_scan=False, tier=None, variant=None,
                     in_dir=None, interactive=False):
        """返回 (model, exe, tier, ctx)；缺组件时返回 None。
        所有 Tk 控件值必须由调用方在 UI 线程预先传入，本函数其余部分
        只用 os/已缓存数据，可安全在工作线程运行。
        interactive=True（UI 线程调用）才弹缺组件对话框；工作线程只返回 None。
        allow_scan=False（UI 线程）只复用已勘察结果，不做大目录扫描。"""
        tier = tier or self.current_tier()
        variant = variant or self.current_variant()
        if in_dir is None:
            in_dir = self.in_box.get_text()
        model = self.find_model(tier)
        exe = self.find_exe(variant)
        if not model or not exe:
            if interactive:
                missing = "模型文件" if not model else "llama-server 运行库"
                self.ui.ask_yes_no(
                    self._tk, "缺少组件",
                    "%s未就绪。请在下方「下载管理」中下载（可先测速选源）。" % missing)
            return None
        info = None
        if in_dir and os.path.isdir(in_dir):
            if self.scan_dir == in_dir and self.scan_info is not None:
                info = self.scan_info
            elif allow_scan:
                info = analyze_input_dir(
                    in_dir, self._mode_plain,
                    log=lambda m: self.ui_call(self.log, m),
                    cancel=self.tr_cancel)
                self.scan_info, self.scan_dir = info, in_dir
        cap = (int(self.cfg.get("custom_ctx", 8192))
               if tier == "custom" else None)
        ctx = decide_ctx(tier, info, cap=cap)
        return model, exe, tier, ctx

    def on_start(self):
        self._persist_cfg()
        plan = self._plan_engine(allow_scan=False, interactive=True)
        if not plan:
            return
        model, exe, tier, ctx = plan
        variant = self.current_variant()
        if self._starting:
            self.log("正在启动引擎，请稍候", "warn")
            return
        need_stop = False
        if self.server and self.server.is_running():
            sig = (self.server.model, self.server.variant, self.server.ctx_size)
            if sig == (model, variant, ctx):
                self.log("引擎已在运行（相同模型/上下文）")
                return
            need_stop = True
        self._starting = True
        self.ui.set_state(self.btn_start, "disabled")
        self.log("正在加载 %s，上下文 %d token，首次加载较慢，请稍候…"
                 % (TIER_PROFILES[tier]["combo"][:2], ctx))

        def work():
            try:
                if need_stop:
                    # 停旧引擎会 taskkill + wait（最长约 15 秒）——必须在工作线程，
                    # 否则切模型时整个界面冻结（1.6.0 之前就是在 Tk 回调里同步停的）。
                    self.ui_call(self.log, "模型或上下文已变更，先停止旧引擎…")
                    old = self.server
                    if old is not None:
                        old.stop()
                    self.server = None
                self._spawn_server(exe, model, variant, ctx, tier)
                self.ui_call(self._engine_ui, True)
            except Exception as e:
                self.ui_call(self.log, "引擎启动失败：%s" % e, "err")
                self.ui_call(self._engine_ui, False)
            finally:
                self._starting = False
                self.ui_call(self._engine_ui,
                             bool(self.server and self.server.is_running()))
        threading.Thread(target=work, daemon=True).start()

    def _spawn_server(self, exe, model, variant, ctx, tier):
        """创建并启动引擎；CUDA 13.4 起不来或没上卡时自动回退 CUDA 12.4。

        13.4 的可用性只有"驱动 ≥ 580 / 计算能力 ≥ 7.5"这个门槛判据，
        实际能否初始化还受 OEM 驱动、Optimus、cudart DLL 等影响，所以这里
        用真实启动 + gpu_verified 兜底，失败就退回兼容面更广的 12.4。
        """
        def build(v, e):
            return LlamaServer(
                e, model, v,
                log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                ctx_size=ctx, tier=tier)

        def fallback(reason):
            self.ui_call(self.log, "CUDA 13.4 %s，尝试回退 CUDA 12.4…"
                         % reason, "warn")
            try:
                srv.stop()
            except Exception:
                pass
            exe12 = self.find_exe("cuda")
            if not exe12:
                self.ui_call(self.log,
                             "未安装 CUDA 12.4 运行库，无法回退；"
                             "请下载运行库或改用 Vulkan/CPU", "err")
                return None
            srv12 = build("cuda", exe12)
            self.server = srv12
            self._push_prompt_extra()
            srv12.start()
            self.ui_call(self.log, "已回退到 CUDA 12.4", "ok")
            return srv12

        srv = build(variant, exe)
        self.server = srv
        self._push_prompt_extra()
        try:
            srv.start()
        except Exception:
            if variant != "cuda13":
                raise
            fb = fallback("启动失败")
            if fb is not None:
                return fb
            raise
        if variant == "cuda13" and not getattr(srv, "gpu_verified", False):
            fb = fallback("未能在 GPU 上初始化")
            if fb is not None:
                return fb
        return srv

    def _engine_ui(self, on):
        self.ui.set_state(self.btn_start, "normal" if not on else "disabled")
        self.ui.set_state(self.btn_stop, "normal" if on else "disabled")
        self.refresh_local_status()

    def on_stop(self):
        if self.server:
            def work():
                self.server.stop()
                self.ui_call(self._engine_ui, False)
            threading.Thread(target=work, daemon=True).start()

    def on_run(self):
        self._persist_cfg()
        if self.tr_cancel is not None:
            self.log("已有翻译任务在进行", "warn")
            return
        if self._starting:
            self.log("引擎正在启动，请稍候再开始翻译", "warn")
            return
        in_dir = self.in_box.get_text()
        if not in_dir or not os.path.isdir(in_dir):
            self.log("请先选择有效的待翻译目录", "warn")
            return
        if self.parser is None:
            self.log("XML 解析模块未就绪", "err")
            return
        out_dir = self.out_box.get_text() or \
            os.path.join(in_dir, "translated_hy_mt2")
        self.out_box.set_text(out_dir)
        self.cfg["out_dir"] = out_dir
        save_config(self.cfg)
        glossary = dict(self.gloss_user)
        force_glossary = _chk_get(self.force_chk)
        # 只给模型/强制路径提供专名类硬术语；对话/百科类普通词不注入。
        hard_glossary = load_hard_glossary_keys() | set(self._gloss_set)
        hard_glossary = hard_glossary or None
        ref_mode = False
        copy_ref = True
        only_ref = False
        protect_proper = False
        preview_ref = False
        translate_literals = True
        session_glossary = True
        tm_lite = True
        try:
            ref_mode = bool(_chk_get(self.ref_chk))
            copy_ref = bool(_chk_get(self.copy_ref_chk))
            only_ref = bool(_chk_get(self.only_ref_chk))
            protect_proper = bool(_chk_get(self.protect_chk))
            preview_ref = bool(_chk_get(self.preview_chk))
            translate_literals = bool(_chk_get(self.literal_chk))
            session_glossary = bool(_chk_get(self.session_chk))
            tm_lite = bool(_chk_get(self.tm_chk))
        except Exception:
            pass

        self.tr_cancel = threading.Event()
        self.ui.set_state(self.btn_run, "disabled")
        self.ui.set_state(self.btn_run_cancel, "normal")

        mode = self.cfg["mode"]
        src_lang = self.cfg["src_lang"]
        if ref_mode and mode not in ("auto", "text"):
            self.log("引用层模式只在「自动」或「文本XML」类型下生效；"
                     "当前类型按原流程执行", "warn")
        cap_tier = self.current_tier()
        cap_variant = self.current_variant()

        def work():
            try:
                plan = self._plan_engine(
                    allow_scan=True, tier=cap_tier,
                    variant=cap_variant, in_dir=in_dir)
                if not plan:
                    if not self.find_model(cap_tier):
                        prof = TIER_PROFILES[cap_tier]
                        want = self.cfg.get("model_paths", {}).get(cap_tier)
                        if not want:
                            fname = TIER_FILE.get(cap_tier, "*.gguf")
                            want = os.path.join(
                                self.cfg.get("model_dir", _MODELS_DIR), fname)
                        self.ui_call(self.log,
                            "「%s」档模型未就绪：%s。请下载该档模型或点「选择已有模型」"
                            "指定文件；不要直接用其他档位的引擎代替。"
                            % (prof["combo"][:2], want), "err")
                    if not self.find_exe(cap_variant):
                        self.ui_call(self.log,
                            "%s运行库未就绪，请在「下载管理」安装"
                            % VARIANT_LABELS.get(cap_variant, cap_variant),
                            "err")
                    self.ui_call(self._run_done)
                    return
                model, exe, tier, ctx = plan
                running = bool(self.server and self.server.is_running())
                reusable = running and self.server.model == model and (
                    self.server.variant == cap_variant
                    or (cap_variant == "cuda13"
                        and self.server.variant == "cuda"))
                if running and not reusable:
                    self.ui_call(self.log,
                        "正在切换到 %s 档（停止旧引擎并以新模型重启）…"
                        % TIER_PROFILES[tier]["combo"][:2], "warn")
                    self.server.stop()
                    self.server = None
                if not reusable:
                    self._spawn_server(exe, model, cap_variant, ctx, tier)
                    if getattr(self.server, "health_warn", ""):
                        self.ui_call(self.log,
                                     "注意：" + self.server.health_warn, "warn")
                    self.ui_call(self._engine_ui, True)
                self._push_prompt_extra()
                if self.scan_info and self.scan_dir == in_dir:
                    self.ui_call(self.log,
                                 "上下文 %d token；最长文本 %d 字符，超长自动分句"
                                 % (self.server.ctx_size,
                                    self.scan_info["max_str"]))

                job = TranslationJob(
                    self.parser, self.server, in_dir, mode, src_lang, "chs",
                    glossary=glossary,
                    force_glossary=force_glossary,
                    output_anyway=False,
                    ctx_size=self.server.ctx_size,
                    out_dir=out_dir,
                    log=lambda m, tag="info": self.ui_call(self.log, m, tag),
                    detail=lambda m, tag="info": self.api.log_detail(m, tag),
                    # 条目级进度只写运行日志：实测 6122 个文件时这一路
                    # 会达到 470 条/秒并全部进界面日志，是 1.7.1 后仍卡死的
                    # 主因。界面只保留每 50 个文件一条的文件进度。
                    progress=lambda d: self.api.log_detail(
                        "  进度：[%d/%d] %s（%d/%d 条，累计 %d 段）"
                        % (d.get("file_idx", 0), d.get("files", 0),
                           d.get("file", ""), d.get("ids", 0),
                           d.get("total_ids", 0), d.get("reqs", 0))),
                    cancel_event=self.tr_cancel,
                    reference_mode=ref_mode,
                    copy_reference=copy_ref,
                    only_referenced=only_ref,
                    protect_proper=protect_proper,
                    preview_reference=preview_ref,
                    hard_glossary_keys=hard_glossary,
                    translate_literals=translate_literals,
                    auto_session_terms=session_glossary,
                    tm_lite=tm_lite)
                job.run()
            except Exception as e:
                self.ui_call(self.log, "翻译失败：%s" % e, "err")
            finally:
                self.ui_call(self._run_done)
        threading.Thread(target=work, daemon=True).start()

    def _run_done(self):
        self.tr_cancel = None
        self.ui.set_state(self.btn_run, "normal")
        self.ui.set_state(self.btn_run_cancel, "disabled")

    def on_cancel_run(self):
        if self.tr_cancel:
            self.tr_cancel.set()
            self.log("正在取消（当前段落翻译完后停止）…", "warn")



def register(api):
    api.register_tool("AI 翻译", lambda parent: AiTranslateTab(api, parent),
                      order=6)
