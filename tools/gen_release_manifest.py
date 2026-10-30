#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 AI 离线翻译插件发布清单（只读，不改插件）。

输出：
  RELEASE_MANIFEST.md   人读版
  release_manifest.json 机读版

用法：
  python tools/gen_release_manifest.py

范围：只针对仓库根部的 plugins/ 插件目录与
dist/ai_translate_plugin_v1.9.4.zip，不扫描已清理的工具集源码/发行版。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_MD = ROOT / "RELEASE_MANIFEST.md"
OUT_JSON = ROOT / "release_manifest.json"

PLUGIN = ROOT / "plugins/ai_translate.py"
ASSETS = ROOT / "plugins/ai_translate_assets"
ZIP = ROOT / "dist/ai_translate_plugin_v1.9.4.zip"
USAGE = ROOT / "使用说明.txt"
TERM_DOC = ROOT / "术语库_处理记录.md"
CASE_DOC = ROOT / "术语库_大小写冲突清单.md"

EXPECTED_PLUGIN_VERSION = "1.9.4"

# 2026-10-23 从 llama.cpp b11612 GitHub Release asset digest 取得。
EXPECTED_LLAMA = {
    "cpu": ("llama-b11612-bin-win-cpu-x64.zip",
            "b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69"),
    "cuda-12.4": ("llama-b11612-bin-win-cuda-12.4-x64.zip",
                  "4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12"),
    "vulkan": ("llama-b11612-bin-win-vulkan-x64.zip",
               "e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9"),
    "cudart-12.4": ("cudart-llama-bin-win-cuda-12.4-x64.zip",
                    "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    "cuda-13.4": ("llama-b11612-bin-win-cuda-13.4-x64.zip",
                  "e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38"),
    "cudart-13.4": ("cudart-llama-bin-win-cuda-13.4-x64.zip",
                    "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668"),
}

# 2026-10-23 从 HuggingFace LFS oid 取得（hf-mirror API，官方仓库元数据）。
EXPECTED_MODELS = {
    "Hy-MT2-7B-Q4_K_M.gguf": (
        "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
        4624648896),
    "Hy-MT2-1.8B-Q8_0.gguf": (
        "5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4",
        1908528192),
}

# 清理前最后一次工具集发行版的哈希；exe 已从工作区移除，保留记录供发布说明引用。
HISTORICAL_EXE = {
    "path": "STALKER Local Toolkit 1.1.0/STALKER Localization Toolkit 1.1.0.exe",
    "file_version": "1.1.0.0",
    "size": 3342336,
    "sha256": "171ce0028151d06b9d28bab4b671d6df957451a3fa6241d0b6cb9543b22e6947",
    "note": "2026-10-23 清单记录；文件已在清理中移除",
}

ZIP_ENTRIES = {
    "plugins/ai_translate.py",
    "plugins/ai_translate_assets/THIRD_PARTY_NOTICES.txt",
    "plugins/ai_translate_assets/glossary_builtin.json",
    "plugins/ai_translate_assets/stalker_termbase.json",
    "plugins/ai_translate_assets/xray_pkg.zip",
}


def fmt_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%d %s" % (value, unit)) if unit == "B" else ("%.2f %s" % (value, unit))
        value /= 1024
    return "%d B" % n


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def dir_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
                count += 1
            except OSError:
                pass
    return total, count


def git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT)] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def git_info() -> dict[str, Any]:
    commit = git("rev-parse", "--short", "HEAD")
    branch = git("branch", "--show-current")
    status_lines = [
        line for line in git("status", "--porcelain").splitlines()
        if ("RELEASE_MANIFEST.md" not in line
                and "release_manifest.json" not in line
                and "gen_release_manifest.py" not in line)
    ]
    try:
        count = int(git("rev-list", "--count", "HEAD") or "0")
    except ValueError:
        count = 0
    return {
        "branch": branch or "（未提交/无分支）",
        "commit": commit or "（尚无提交）",
        "single_commit": count == 1,
        "dirty": bool(status_lines),
        "dirty_files": status_lines[:20],
    }


def tracked_stats() -> tuple[int, int]:
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout
    if not raw:
        return 0, 0
    total = 0
    count = 0
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        rel = entry.decode("utf-8", "surrogateescape")
        p = ROOT / rel
        try:
            total += p.stat().st_size
            count += 1
        except OSError:
            pass
    return count, total


def parse_plugin(text: str) -> dict[str, Any]:
    block = text[text.find("PLUGIN_INFO"):text.find("PLUGIN_INFO") + 1500]
    m = re.search(r'"version"\s*:\s*"([^"]+)"', block)
    version = m.group(1) if m else ""

    llama: dict[str, str] = {}
    models: dict[str, tuple[str, int]] = {}
    tree = ast.parse(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "_LLAMA_SHA256":
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                llama = {str(k): str(v) for k, v in value.items()}
        elif target.id == "_MODEL_HASHES":
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                models = {str(k): (str(v[0]), int(v[1])) for k, v in value.items()}
    return {"version": version, "llama": llama, "models": models}


def json_count(path: Path, meta_key: str | None = None) -> int | None:
    try:
        data = json.loads(read_text(path))
    except Exception:
        return None
    if meta_key and isinstance(data, dict):
        meta = data.get("_meta") or {}
        if isinstance(meta, dict) and isinstance(meta.get(meta_key), int):
            return int(meta[meta_key])
    if isinstance(data, dict):
        return len([k for k in data if k != "_meta"])
    if isinstance(data, list):
        return len(data)
    return None


def artifact(role: str, path: Path, note: str = "") -> dict[str, Any]:
    rel = str(path.relative_to(ROOT))
    if not path.exists():
        return {"role": role, "path": rel, "exists": False,
                "size": 0, "sha256": "", "note": note or "缺失"}
    if path.is_dir():
        total, count = dir_size(path)
        return {"role": role, "path": rel, "exists": True,
                "size": total, "sha256": "", "note": "%d 个文件（目录）" % count}
    return {"role": role, "path": rel, "exists": True,
            "size": path.stat().st_size, "sha256": sha256(path), "note": note}


def check(name: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def build_manifest() -> dict[str, Any]:
    plugin_text = read_text(PLUGIN) if PLUGIN.is_file() else ""
    meta = parse_plugin(plugin_text) if plugin_text else {"version": "", "llama": {}, "models": {}}
    version = meta["version"]

    artifacts = [
        artifact("插件分享副本", PLUGIN, "纯标准库，保留必要安全说明"),
        artifact("插件发布 zip", ZIP, "当前发布包"),
        artifact("术语库（随包，只读）", ASSETS / "glossary_builtin.json",
             "运行时匹配的内置层"),
        artifact("术语基表（分类）", ASSETS / "stalker_termbase.json", "导入用"),
        artifact("X-Ray 解析器", ASSETS / "xray_pkg.zip", "解析 XML/LTX/脚本"),
        artifact("第三方来源声明", ASSETS / "THIRD_PARTY_NOTICES.txt",
                 "随包分发（上游来源与许可状态）"),
        artifact("使用说明", USAGE, "含发布包 SHA-256"),
        artifact("术语库处理记录", TERM_DOC, "术语口径与修剪记录"),
        artifact("大小写冲突清单", CASE_DOC, "术语大小写冲突处理"),
    ]

    # 历史 exe 记录（文件已不在工作区）
    artifacts.append({
        "role": "历史工具集 exe",
        "path": HISTORICAL_EXE["path"],
        "exists": False,
        "size": HISTORICAL_EXE["size"],
        "sha256": HISTORICAL_EXE["sha256"],
        "note": HISTORICAL_EXE["note"],
    })

    zip_sha = ""
    zip_entries: list[str] = []
    zip_notes: list[str] = []
    if ZIP.is_file():
        import zipfile
        zip_sha = sha256(ZIP)
        with zipfile.ZipFile(ZIP) as z:
            zip_entries = z.namelist()
            zip_notes = [n for n in zip_entries
                         if n.endswith("/") or n.startswith(("bin/", "models/", "logs/", "downloads/"))]

    checks: list[dict[str, Any]] = []

    # 插件语法与版本
    try:
        compile(plugin_text, str(PLUGIN), "exec")
        checks.append(check("插件语法", True, "compile() 通过"))
    except Exception as e:
        checks.append(check("插件语法", False, "compile() 失败：%s" % e))

    checks.append(check(
        "插件版本 == 预期", version == EXPECTED_PLUGIN_VERSION,
        "PLUGIN_INFO %s / 预期 %s" % (version or "未读到", EXPECTED_PLUGIN_VERSION)))
    checks.append(check(
        "zip 文件名含插件版本", version and ("v%s" % version) in ZIP.name,
        ZIP.name))

    # zip 内容
    checks.append(check(
        "zip 条目 == 5 个预期文件", set(zip_entries) == ZIP_ENTRIES,
        "多出：%s；缺少：%s" % (
            sorted(set(zip_entries) - ZIP_ENTRIES) or "无",
            sorted(ZIP_ENTRIES - set(zip_entries)) or "无")))
    checks.append(check(
        "zip 不含空目录/运行库/模型/日志", not zip_notes,
        "发现：%s" % zip_notes if zip_notes else "干净"))

    # zip 内文件与分享副本逐字节一致
    if ZIP.is_file() and PLUGIN.is_file():
        import zipfile
        with zipfile.ZipFile(ZIP) as z:
            same = True
            details = []
            for rel in ("plugins/ai_translate.py",
                        "plugins/ai_translate_assets/THIRD_PARTY_NOTICES.txt",
                        "plugins/ai_translate_assets/glossary_builtin.json",
                        "plugins/ai_translate_assets/stalker_termbase.json",
                        "plugins/ai_translate_assets/xray_pkg.zip"):
                try:
                    data = z.read(rel)
                except KeyError:
                    same = False
                    details.append("%s 缺失" % rel)
                    continue
                disk = ROOT / rel
                if not disk.is_file() or hashlib.md5(data).hexdigest() != md5(disk):
                    same = False
                    details.append("%s 不同" % rel)
            checks.append(check("zip 内文件 == 分享副本", same,
                                "全部一致" if same else "；".join(details)))

    # 官方哈希表
    expected_llama = {k: v[1] for k, v in EXPECTED_LLAMA.items()}
    checks.append(check(
        "官方哈希：llama.cpp 6 项", meta["llama"] == expected_llama,
        "登记 %d 项；不一致：%s" % (
            len(meta["llama"]),
            sorted(set(expected_llama.items()) ^ set(meta["llama"].items())) or "无")))
    checks.append(check(
        "官方哈希：模型 2 项", meta["models"] == EXPECTED_MODELS,
        "登记 %d 项；不一致：%s" % (
            len(meta["models"]),
            sorted(set(EXPECTED_MODELS.items()) ^ set(meta["models"].items())) or "无")))
    all_hashes = list(meta["llama"].values()) + [v[0] for v in meta["models"].values()]
    checks.append(check(
        "官方哈希：8 个合法 SHA-256", len(all_hashes) == 8 and
        all(re.fullmatch(r"[0-9a-f]{64}", h or "") for h in all_hashes),
        "%d 个" % len(all_hashes)))
    checks.append(check(
        "官方哈希：llama.cpp 标签 b11612", '_LLAMA_TAG = "b11612"' in plugin_text,
        "源码常量"))

    # 分享包不含运行库/模型/配置/日志
    forbidden = []
    if ASSETS.is_dir():
        for name in os.listdir(ASSETS):
            if name in ("bin", "models", "logs", "downloads", "config.json"):
                forbidden.append(name)
    checks.append(check(
        "分享包不含 bin/models/logs/downloads/config.json", not forbidden,
        "发现：%s" % forbidden if forbidden else "干净"))

    # 绝对路径 / 本机路径泄漏
    leak = []
    patterns = (r"[A-Za-z]:\\STALKER-HH", r"[A-Za-z]:/STALKER-HH",
                r"C:\\Users\\", r"C:/Users/")
    for label, text in (("ai_translate.py", plugin_text), ("使用说明.txt",
                        read_text(USAGE) if USAGE.is_file() else "")):
        for pat in patterns:
            if re.search(pat, text):
                leak.append("%s: %s" % (label, pat))
    checks.append(check("无绝对路径/用户名泄漏", not leak,
                        "发现：%s" % leak if leak else "未发现"))

    # 术语规模：**必须按实际分类求和**再和 _meta 比。
    # 旧实现两边都取自同一个 _meta 块，等于自己和自己比，永远绿 ——
    # 线上文件实际 8246 条、_meta 停在 8208 时，19 项检查照样全过。
    glossary_count = json_count(ASSETS / "glossary_builtin.json")
    termbase_path = ASSETS / "stalker_termbase.json"
    termbase = {}
    try:
        termbase = json.loads(read_text(termbase_path))
    except Exception:
        pass
    actual_cats = {str(k): len(v) for k, v in termbase.items()
                   if isinstance(v, dict) and not str(k).startswith("_")}
    actual_sum = sum(actual_cats.values())
    meta = termbase.get("_meta") if isinstance(termbase.get("_meta"), dict) else {}
    termbase_count = meta.get("total_entries")
    checks.append(check(
        "术语基表：_meta.total_entries == 实际分类合计",
        isinstance(termbase_count, int) and termbase_count == actual_sum,
        "meta %s / 实际 %s" % (termbase_count, actual_sum)))
    meta_cats = meta.get("categories") if isinstance(meta.get("categories"), dict) else {}
    checks.append(check(
        "术语基表：_meta.categories == 实际分类条数",
        meta_cats == actual_cats,
        "meta 分类数 %d / 实际分类数 %d，差异：%s" % (
            len(meta_cats), len(actual_cats),
            sorted(set(meta_cats.items()) ^ set(actual_cats.items()))[:4] or "无")))
    # README 教用户"重建后复制到 plugins/" —— 若 tools 副本与随包不一致，
    # 照文档操作会把线上人工修订（补录/定稿译名/PDA 口径）整批回退。
    tools_tb = ROOT / "tools/stalker_termbase.json"
    same_tools = tools_tb.is_file() and md5(tools_tb) == md5(termbase_path)
    checks.append(check(
        "tools 术语基表 == 随包术语基表", same_tools,
        "一致" if same_tools else "不一致：按 README 重建会覆盖线上数据"))
    checks.append(check(
        "术语库：随包条目 >= 8000", bool(glossary_count and glossary_count >= 8000),
        "%s 条" % glossary_count))
    checks.append(check(
        "术语库：随包 >= 分类基表", bool(glossary_count and actual_sum and
                                          glossary_count >= actual_sum),
        "%s / %s" % (glossary_count, actual_sum)))

    # 发布说明包含 exe / zip 哈希
    usage_text = read_text(USAGE) if USAGE.is_file() else ""
    checks.append(check(
        "使用说明含 exe SHA-256", HISTORICAL_EXE["sha256"] in usage_text,
        HISTORICAL_EXE["sha256"][:16] + "…"))
    checks.append(check(
        "使用说明含当前 zip SHA-256", bool(zip_sha) and zip_sha in usage_text,
        (zip_sha[:16] + "…") if zip_sha else "zip 缺失"))
    checks.append(check(
        "使用说明含插件版本", ("ai_translate %s" % version) in usage_text,
        "ai_translate %s" % version))

    # dist 里只能有当前版本的 zip
    stale_zips = sorted(p.name for p in (ROOT / "dist").glob(
        "ai_translate_plugin_*.zip") if p != ZIP)
    checks.append(check(
        "dist 只保留当前版本 zip", not stale_zips,
        ("多余：%s" % stale_zips) if stale_zips else "仅当前版本"))

    # 存储相关（只列插件资产，不扫描整个仓库）
    storage_specs = [
        ("运行时备份",
         ROOT / ".workbuddy/backups/ai_translate_cleanup_20261015_154625/bin",
         "已删除；插件按官方哈希重新下载"),
        ("旧插件包 1.4.0", ROOT / "ai_translate_plugin_1.4.0.zip",
         "已删除（2026-10-23）"),
        ("旧解析器压缩包", ROOT / "plugins.rar",
         "已删除（核对无独有文件）"),
        ("陈旧 assets 副本", ROOT / "STALKER Localization Toolkit 1.1.0",
         "已删除（仅剩旧 xray_pkg.zip）"),
    ]
    storage = []
    for role, path, note in storage_specs:
        rel = str(path.relative_to(ROOT))
        if not path.exists():
            storage.append({"role": role, "path": rel, "exists": False,
                            "size": 0, "files": 0, "note": note})
            continue
        if path.is_dir():
            total, count = dir_size(path)
            storage.append({"role": role, "path": rel, "exists": True,
                            "size": total, "files": count, "note": note})
        else:
            storage.append({"role": role, "path": rel, "exists": True,
                            "size": path.stat().st_size, "files": 1, "note": note})

    tracked_count, tracked_bytes = tracked_stats()

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workspace": ".",
        "scope": "AI 离线翻译插件（ai_translate）",
        "git": git_info(),
        "versions": {
            "plugin_version": version,
            "expected_plugin_version": EXPECTED_PLUGIN_VERSION,
            "target_toolkit": "STALKER Localization Toolkit 1.1.0",
            "historical_exe_version": HISTORICAL_EXE["file_version"],
        },
        "artifacts": artifacts,
        "zip": {"path": str(ZIP.relative_to(ROOT)), "sha256": zip_sha,
                "entries": zip_entries},
        "official_hashes": {
            "llama": {k: {"file": v[0], "sha256": v[1]}
                      for k, v in EXPECTED_LLAMA.items()},
            "models": {k: {"sha256": v[0], "bytes": v[1]}
                       for k, v in EXPECTED_MODELS.items()},
        },
        "consistency": checks,
        "term_data": {
            "glossary_entries": glossary_count,
            "termbase_entries": actual_sum,
            "termbase_categories": actual_cats,
            "termbase_meta_total": termbase_count,
        },
        "storage": storage,
        "historical_exe": HISTORICAL_EXE,
        "tracked": {"files": tracked_count, "bytes": tracked_bytes},
    }


def render_markdown(data: dict[str, Any]) -> str:
    def yes(ok: bool | None) -> str:
        return "一致" if ok is True else ("不一致" if ok is False else "无法比对")

    lines: list[str] = []
    lines.append("# AI 离线翻译插件 · 发布清单")
    lines.append("")
    lines.append("- 生成时间：`%s`" % data["generated_at"])
    lines.append("- 工作区：仓库根目录")
    lines.append("- 范围：**%s**" % data["scope"])
    if data["git"].get("single_commit"):
        lines.append("- Git：`%s`（单提交仓库；清单与代码同一提交）%s" % (
            data["git"]["branch"],
            "（有未提交改动）" if data["git"]["dirty"] else ""))
    else:
        lines.append("- Git：`%s` @ `%s`（生成时 HEAD；清单本身在后续提交入库）%s" % (
            data["git"]["branch"], data["git"]["commit"],
            "（有未提交改动）" if data["git"]["dirty"] else ""))
    lines.append("- 已跟踪文件：%d 个，%s" % (
        data["tracked"]["files"], fmt_size(data["tracked"]["bytes"])))
    lines.append("")
    lines.append("## 1. 版本与交付物")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 插件版本 | `%s` |" % (data["versions"]["plugin_version"] or "未读到"))
    lines.append("| 预期版本 | `%s` |" % data["versions"]["expected_plugin_version"])
    lines.append("| 适用工具集 | `%s` |" % data["versions"]["target_toolkit"])
    lines.append("| 历史 exe FileVersion | `%s` |" % data["versions"]["historical_exe_version"])
    lines.append("")
    lines.append("| 角色 | 路径 | 大小 | SHA-256 | 说明 |")
    lines.append("|---|---|---:|---|---|")
    for a in data["artifacts"]:
        if not a["exists"]:
            digest = ("`%s`" % a["sha256"]) if a.get("sha256") else "—"
            lines.append("| %s | `%s` | %s | %s | %s |" % (
                a["role"], a["path"], fmt_size(a["size"]), digest, a["note"]))
            continue
        digest = ("`%s`" % a["sha256"]) if a["sha256"] else "—"
        lines.append("| %s | `%s` | %s | %s | %s |" % (
            a["role"], a["path"], fmt_size(a["size"]), digest, a["note"]))
    lines.append("")
    lines.append("> 历史 exe 已从工作区清理，哈希保留自 2026-10-23 清理前的清单；"
                 "当前插件包不包含 exe。")
    lines.append("")
    lines.append("## 2. 下载链路官方 SHA-256")
    lines.append("")
    lines.append("| 产物 | 文件 | 字节数 | SHA-256 |")
    lines.append("|---|---|---:|---|")
    for key, item in data["official_hashes"]["llama"].items():
        lines.append("| llama.cpp `%s` | `%s` | — | `%s` |" % (
            key, item["file"], item["sha256"]))
    for key, item in data["official_hashes"]["models"].items():
        lines.append("| 模型 | `%s` | %s | `%s` |" % (
            key, item["bytes"], item["sha256"]))
    lines.append("")
    lines.append("来源：llama.cpp `b11612` GitHub Release asset digest；"
                 "HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、"
                 "`tencent/Hy-MT2-1.8B-GGUF`）。"
                 "下载后强制比对，不一致即删除并换源。")
    lines.append("")
    lines.append("## 3. 一致性核对")
    lines.append("")
    lines.append("| 检查 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for c in data["consistency"]:
        lines.append("| %s | %s | %s |" % (c["check"], yes(c["ok"]), c["detail"]))
    lines.append("")
    lines.append("## 4. 术语数据规模")
    lines.append("")
    lines.append("| 文件 | 条目 |")
    lines.append("|---|---:|")
    lines.append("| `glossary_builtin.json`（随包，运行时匹配） | %s |" % (
        data["term_data"]["glossary_entries"] or "—"))
    lines.append("| `stalker_termbase.json`（分类导入） | %s |" % (
        data["term_data"]["termbase_entries"] or "—"))
    lines.append("")
    cats = data["term_data"]["termbase_categories"]
    if cats:
        lines.append("分类明细：" + "、".join(
            "%s %s" % (k, v) for k, v in cats.items()))
        lines.append("")
    lines.append("## 5. 存储相关（仅插件资产）")
    lines.append("")
    lines.append("| 角色 | 路径 | 大小 | 文件数 | 说明 |")
    lines.append("|---|---|---:|---:|---|")
    for s in data["storage"]:
        if not s["exists"]:
            lines.append("| %s | `%s` | — | — | %s |" % (
                s["role"], s["path"], s["note"]))
            continue
        lines.append("| %s | `%s` | %s | %d | %s |" % (
            s["role"], s["path"], fmt_size(s["size"]), s["files"], s["note"]))
    lines.append("")
    lines.append("> 运行库/模型不入库、不随插件包分发；当前未保留本地缓存，"
                 "首次使用由插件按官方 SHA-256 下载。")
    lines.append("")
    lines.append("## 6. 校验方式")
    lines.append("")
    lines.append("```powershell")
    lines.append("# 校验当前插件 zip")
    lines.append("Get-FileHash 'dist\\%s' -Algorithm SHA256" % ZIP.name)
    lines.append("")
    lines.append("# 重新生成清单")
    lines.append("python tools/gen_release_manifest.py")
    lines.append("```")
    lines.append("")
    lines.append("## 7. 备注")
    lines.append("")
    lines.append("- 本清单只读文件，不修改插件；范围仅限 `plugins/`、根目录文档与插件 zip。")
    lines.append("- 插件包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。")
    lines.append("- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，需重新核对。")
    lines.append("- 工具集源码/发行版已在清理中移除；历史版本仍可从 Git 历史取回。")
    lines.append("")
    return "\n".join(lines)


def compare_with_committed(data: dict[str, Any]) -> list[str]:
    """与已提交的 release_manifest.json 比对（忽略生成时间与 git 元信息）。"""
    if not OUT_JSON.is_file():
        return ["release_manifest.json 不存在"]
    try:
        old = json.loads(read_text(OUT_JSON))
    except Exception as e:
        return ["release_manifest.json 无法解析：%s" % e]
    keys = ("versions", "artifacts", "zip", "official_hashes", "term_data")
    return [k for k in keys if data.get(k) != old.get(k)]


def main() -> int:
    if not PLUGIN.is_file():
        print("找不到插件：%s" % PLUGIN, file=sys.stderr)
        return 2
    if "--check" in sys.argv:
        # CI 门禁：只校验、不写文件。生成时间每次不同，因此不做整文件 diff；
        # 只比对实质内容（版本/产物哈希/zip/官哈希/术语规模）。
        data = build_manifest()
        bad = [c for c in data["consistency"] if c["ok"] is False]
        diffs = compare_with_committed(data)
        print("一致性：%d 项检查，%d 项不一致"
              % (len(data["consistency"]), len(bad)))
        for c in bad:
            print("  - %s：%s" % (c["check"], c["detail"]))
        if diffs:
            print("与已提交清单不一致：%s（请重新生成并提交）"
                  % ", ".join(diffs))
        ok = not bad and not diffs
        print("check:", "OK" if ok else "FAIL")
        return 0 if ok else 1
    data = build_manifest()
    OUT_MD.write_text(render_markdown(data), encoding="utf-8", newline="\n")
    OUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    print("已生成：%s" % OUT_MD)
    print("已生成：%s" % OUT_JSON)
    print("插件版本：%s" % data["versions"]["plugin_version"])
    print("插件 zip：%s" % data["zip"]["sha256"])
    bad = [c for c in data["consistency"] if c["ok"] is False]
    print("一致性：%d 项检查，%d 项不一致" % (len(data["consistency"]), len(bad)))
    for c in bad:
        print("  - %s：%s" % (c["check"], c["detail"]))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
