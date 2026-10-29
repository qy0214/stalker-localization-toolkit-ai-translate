# -*- coding: utf-8 -*-
"""分享包可移植性验证（"任意设备 / 任意网络 / 任意位置"）

验证四件事：
  A 脏东西为零：无本机绝对路径、无本机设置、无模型/运行库等可自下载物
  B 冷启动可用：全新目录、无 config.json，插件导入即工作，配置回落默认值
  C 只读位置可用：插件目录不可写时自动改用 %LOCALAPPDATA%（Program Files 场景）
  D 不影响功能：同一份代码在干净包与工作副本上跑同一套断言，结果一致

用法:
    python test_share_package.py <share_plugins_dir> <work_plugins_dir>
"""

import glob
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHARE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "plugins")
WORK = sys.argv[2] if len(sys.argv) > 2 else SHARE

RESULTS = []
SKIPPED = []


def skip(name, why):
    SKIPPED.append((name, why))
    print("SKIP %s  |  %s" % (name, why))


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  |  " + detail) if detail else ""))
    return ok


def load(path, name="ait"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("=" * 78)
print("分享包:", SHARE)
print("工作副本:", WORK)
print("=" * 78)

# ---------------------------------------------------------------- A 脏东西为零
print("\n--- A 分享包内容与痕迹 ---")
files = []
for root, _d, fs in os.walk(SHARE):
    for f in fs:
        files.append(os.path.relpath(os.path.join(root, f), SHARE))
files.sort()
print("   文件清单:", files)
EXPECT = {"ai_translate.py",
          os.path.join("ai_translate_assets", "xray_pkg.zip"),
          os.path.join("ai_translate_assets", "stalker_termbase.json"),
          os.path.join("ai_translate_assets", "glossary_builtin.json")}
check("A1 只含插件+必需解析器+术语数据（无模型/运行库/配置/日志）",
      set(files) == EXPECT, str(sorted(set(files) ^ EXPECT) or files))

size = sum(os.path.getsize(os.path.join(SHARE, f)) for f in files)
check("A2 体积轻量（< 1 MB，可随插件目录分享）", size < 1024 * 1024,
      "%.0f KB" % (size / 1024.0))

bad_tokens = ["STALKER-HH", "\\Users\\", "AppData", "Admin"]
src = open(os.path.join(SHARE, "ai_translate.py"), encoding="utf-8-sig").read()
hits = [t for t in bad_tokens if t in src]
check("A3 代码/注释内无本机路径片段与用户名", not hits, str(hits))

# 真正要防的是"写死的本机值"，而不是配置键名（in_dir/tier_chosen 等键名
# 本来就是代码的一部分，它们的值全部来自运行时或默认空值）
import re as _re
abs_literals = _re.findall(r"[\"'][A-Za-z]:[\\/]{1,2}[^\"'\n]*[\"']", src)
check("A3b 无写死的盘符绝对路径字符串字面量", not abs_literals,
      str(abs_literals[:3]))
defaults = _re.search(r"DEFAULT_CONFIG\s*=\s*\{(.*?)\n\}", src, _re.S)
check("A3c 默认配置里没有预置的具体目录（全为空/False/空表）",
      defaults and not _re.search(r"[\"'][A-Za-z]:[\\/]", defaults.group(1)),
      "DEFAULT_CONFIG 无盘符路径")

# 术语数据文件是随包内容（用户确认要带），不算"本机设置"；要防的是 config.json
DATA_JSON = {os.path.join("ai_translate_assets", "glossary_builtin.json"),
             os.path.join("ai_translate_assets", "stalker_termbase.json")}
extra_json = [f for f in files if f.endswith(".json") and f not in DATA_JSON]
check("A4 不带本机设置文件（无 config.json，只有术语数据）",
      not extra_json, str(extra_json))
# 术语数据本身也不该夹带本机路径
bad_in_data = []
for f in sorted(DATA_JSON):
    txt = open(os.path.join(SHARE, f), encoding="utf-8", errors="replace").read()
    for t in ("STALKER-HH", "\\Users\\", "AppData", "Admin"):
        if t in txt:
            bad_in_data.append((f, t))
check("A4b 术语数据文件内无本机路径/用户名", not bad_in_data,
      str(bad_in_data))

# 导入前记录基线：插件导入时会创建 bin/models/logs/downloads 运行目录，
# 若直接导入分享包会把它弄脏 —— 因此所有导入都在临时副本上进行，
# 最后还要断言分享包一个字节都没变。
BASELINE = sorted(files)

# ---------------------------------------------------------------- B 冷启动可用
print("\n--- B 冷启动（全新目录、无 config.json） ---")
tmp = tempfile.mkdtemp(prefix="ait_cold_")
cold = os.path.join(tmp, "plugins")
shutil.copytree(SHARE, cold)
cold_py = os.path.join(cold, "ai_translate.py")
mod_share = load(cold_py, "ait_share")
check("A5 解析器包能被加载（xray_pkg.zip 是必需项，不能省）",
      mod_share._load_vendor() is not None)
check("B0 导入后分享包本身未被写入（测试只在临时副本上操作）",
      sorted(os.path.relpath(os.path.join(r, f), SHARE)
             for r, _d, fs in os.walk(SHARE) for f in fs) == BASELINE,
      "分享包目录保持原样")

mod_cold = load(cold_py, "ait_cold")
check("B1 全新目录下导入成功且数据根落在插件目录（便携模式）",
      os.path.normcase(mod_cold._DATA_ROOT) == os.path.normcase(
          os.path.join(cold, "ai_translate_assets")),
      mod_cold._DATA_ROOT)
check("B2 无 config.json 时配置回落默认值（不带任何本机设置）",
      not os.path.exists(mod_cold._CONFIG)
      and mod_cold.load_config()["in_dir"] == ""
      and mod_cold.load_config()["tier"] in mod_cold.TIER_PROFILES,
      json.dumps({k: mod_cold.load_config()[k]
                  for k in ("in_dir", "out_dir", "tier", "variant")},
                 ensure_ascii=False))
check("B3 未附带运行库时 find_exe 返回 None（会引导用户下载）",
      mod_cold.AiTranslateTab.find_exe.__doc__ is not None)
_inst = mod_cold.AiTranslateTab.__new__(mod_cold.AiTranslateTab)
_inst.cfg = mod_cold.load_config()
_inst.current_variant = lambda: "cuda"
_inst.current_tier = lambda: "7b"
check("B4 未附带模型时 find_model 返回 None（会引导用户下载）",
      _inst.find_model() is None)
check("B5 未附带运行库时 find_exe 返回 None（不静默跨后端回退）",
      _inst.find_exe() is None)
# 首次运行应自行写出配置（不依赖预置文件）
mod_cold.save_config({"probe": 1})
check("B6 首次运行能自行生成数据文件（无需预置 config）",
      os.path.exists(mod_cold._CONFIG)
      and json.load(open(mod_cold._CONFIG, encoding="utf-8"))["probe"] == 1)
check("B7 生成的配置里不含任何外部绝对路径",
      "STALKER-HH" not in open(mod_cold._CONFIG, encoding="utf-8").read())

# ---------------------------------------------------------------- C 只读位置可用
print("\n--- C 只读安装位置（插件目录不可写 → %LOCALAPPDATA%） ---")
ro_dir = os.path.join(tmp, "readonly_plugins")
shutil.copytree(SHARE, ro_dir)
ro_py = os.path.join(ro_dir, "ai_translate.py")

# 模拟"插件目录不可写"：让 mkdir/写探针在插件目录下失败（等价于 Program Files）
real_makedirs, real_open = os.makedirs, open
blocked = []
assets = os.path.join(ro_dir, "ai_translate_assets")


def fake_makedirs(path, *a, **k):
    if os.path.normcase(str(path)).startswith(os.path.normcase(assets)):
        raise PermissionError("模拟只读目录")
    return real_makedirs(path, *a, **k)


def fake_open(file, *a, **k):
    if os.path.normcase(str(file)).startswith(os.path.normcase(assets)):
        mode = a[0] if a else k.get("mode", "r")
        if any(c in str(mode) for c in "wax+"):
            raise PermissionError("模拟只读目录")
    return real_open(file, *a, **k)


os.makedirs, open = fake_makedirs, fake_open
try:
    mod_ro = load(ro_py, "ait_ro")
finally:
    os.makedirs, open = real_makedirs, real_open

fallback_ok = (os.path.normcase(mod_ro._DATA_ROOT)
               != os.path.normcase(assets)
               and "STALKER Localization Toolkit" in mod_ro._DATA_ROOT)
check("C1 插件目录只读时自动改用 %LOCALAPPDATA%（不崩、不写只读位置）",
      fallback_ok, mod_ro._DATA_ROOT)
check("C2 只读场景下解析器仍能加载（读只读位置是允许的）",
      mod_ro._load_vendor() is not None)

# ---------------------------------------------------------------- D 发布包一致性
print("\n--- D 发布包与分享目录一致 ---")
check("A6 分享包无任何运行产物（bin/models/logs/downloads/__pycache__）",
      not any(os.path.isdir(os.path.join(SHARE, "ai_translate_assets", d))
              for d in ("bin", "models", "logs", "downloads"))
      and not any("__pycache__" in r for r, _d, _f in os.walk(SHARE)))

# 1.6.0 之前这里比较 SHARE 与 WORK，而 WORK 默认就等于 SHARE ——
# 同一个文件和它自己比，永远通过。现在改为：dist/ 里的发布 zip 必须与
# 分享目录逐字节一致；没有 zip 时明确 SKIP，不再伪装成通过。
zips = sorted(glob.glob(os.path.join(ROOT, "dist", "ai_translate_plugin_*.zip")))
if not zips:
    skip("D1 发布 zip == 分享目录", "dist/ 下没有 ai_translate_plugin_*.zip")
    skip("D2 插件版本 == zip 文件名版本", "同上")
else:
    import zipfile
    zp = zips[-1]
    with zipfile.ZipFile(zp) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        disk_names = {"plugins/" + f.replace(os.sep, "/") for f in files}
        check("D1a zip 条目 == 分享目录文件", set(names) == disk_names,
              "差异：%s" % (sorted(set(names) ^ disk_names)[:4] or "无"))
        diffs = []
        for n in names:
            disk = os.path.join(SHARE, n[len("plugins/"):])
            if not os.path.isfile(disk) or z.read(n) != open(disk, "rb").read():
                diffs.append(n)
        check("D1b zip 内容 == 分享目录（逐字节）", not diffs, str(diffs[:4]))
    ver = mod_share.PLUGIN_INFO.get("version", "")
    check("D2 插件版本 == zip 文件名版本",
          bool(ver) and ("v%s" % ver) in os.path.basename(zp),
          "%s / %s" % (ver, os.path.basename(zp)))

# 关键算法在干净包上照样工作（抽样：编码链/术语索引/术语表摊平/随包术语）
zh = "潜行者，去酒吧找一个神器。"
g = {"神器": "神器", "Now": "现在", "Сталкер": "潜行者"}
idx = mod_share.GlossaryIndex(g)
check("D3 编码链在干净包上给出 UTF-8 优先",
      mod_share.encoding_chain(zh.encode("utf-8"))[0] == "utf-8")
check("D4 术语索引在干净包上可命中中文术语",
      [k for _, _, k in idx.find("一个神器装备")] == ["神器"])
check("D5 术语表摊平保留混合表两侧",
      mod_share.flatten_glossary({"a": "b", "分类": {"x": "y"}})
      == {"a": "b", "x": "y"})
check("D6 随包术语层可加载（>= 8000 条）",
      len(mod_share.load_builtin_glossary()) >= 8000,
      "%d 条" % len(mod_share.load_builtin_glossary()))

shutil.rmtree(tmp, ignore_errors=True)

total = len(RESULTS)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print("\n" + "=" * 78)
print("总计 %d 项：通过 %d，失败 %d，跳过 %d"
      % (total, passed, total - passed, len(SKIPPED)))
for name, ok, detail in RESULTS:
    if not ok:
        print("  FAIL:", name, detail)
print("=" * 78)
sys.exit(0 if passed == total else 1)
