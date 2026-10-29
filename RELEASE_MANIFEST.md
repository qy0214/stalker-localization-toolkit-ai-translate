# AI 离线翻译插件 · 发布清单

- 生成时间：`2026-10-30T00:27:17+08:00`
- 工作区：仓库根目录
- 范围：**AI 离线翻译插件（ai_translate）**
- Git：`main` @ `91cd846`（生成时 HEAD；清单本身在后续提交入库）
- 已跟踪文件：29 个，17.06 MB

## 1. 版本与交付物

| 项 | 值 |
|---|---|
| 插件版本 | `1.7.1` |
| 预期版本 | `1.7.1` |
| 适用工具集 | `STALKER Localization Toolkit 1.1.0` |
| 历史 exe FileVersion | `1.1.0.0` |

| 角色 | 路径 | 大小 | SHA-256 | 说明 |
|---|---|---:|---|---|
| 插件分享副本 | `plugins\ai_translate.py` | 188.05 KB | `1024a42fcd0cf5aa314d1fdd1637f0b18755d7103eeefb2064010193c0a60e18` | 纯标准库，保留必要安全说明 |
| 插件发布 zip | `dist\ai_translate_plugin_v1.7.1.zip` | 270.34 KB | `39dd38f17a230d558d346ea6d75a496f469cb6aa68fe7a4fa7a689801eb61dac` | 当前发布包 |
| 术语库（随包，只读） | `plugins\ai_translate_assets\glossary_builtin.json` | 279.12 KB | `804d3814c5a84e27d2b3921aa72dabe38e66b351185358a8e63a3ba10e95cbd4` | 运行时匹配的内置层 |
| 术语基表（分类） | `plugins\ai_translate_assets\stalker_termbase.json` | 312.83 KB | `c076b28dcf1f7638538a5f47e464a98d6e213b7e87f95ab6ad61dc98698d08f9` | 导入用 |
| X-Ray 解析器 | `plugins\ai_translate_assets\xray_pkg.zip` | 7.15 KB | `b575b4935ddaf2dd4c59a77068731d686aaf26e1e546077e55e1bc0fcb451a30` | 解析 XML/LTX/脚本 |
| 第三方来源声明 | `plugins\ai_translate_assets\THIRD_PARTY_NOTICES.txt` | 1.09 KB | `d93b0e6776328fe5f6c7fb72355ab808822c3a95e111c4555fe94368b9668042` | 随包分发（上游来源与许可状态） |
| 使用说明 | `使用说明.txt` | 17.76 KB | `0c1867734fabc5fa8f876a05c2b3c2cd0a34c23f436d40e2a9f19d688456da5f` | 含发布包 SHA-256 |
| 术语库处理记录 | `术语库_处理记录.md` | 6.27 KB | `2a075f0711a04dda6a568313e11b5e5f758fc2f1bd7b094c4833ba2604df9269` | 术语口径与修剪记录 |
| 大小写冲突清单 | `术语库_大小写冲突清单.md` | 1.32 KB | `a58bffc1028675ee02f7acfc79c65412f161b7944024247643320e612486c885` | 术语大小写冲突处理 |
| 历史工具集 exe | `STALKER Local Toolkit 1.1.0/STALKER Localization Toolkit 1.1.0.exe` | 3.19 MB | `171ce0028151d06b9d28bab4b671d6df957451a3fa6241d0b6cb9543b22e6947` | 2026-10-23 清单记录；文件已在清理中移除 |

> 历史 exe 已从工作区清理，哈希保留自 2026-10-23 清理前的清单；当前插件包不包含 exe。

## 2. 下载链路官方 SHA-256

| 产物 | 文件 | 字节数 | SHA-256 |
|---|---|---:|---|
| llama.cpp `cpu` | `llama-b11612-bin-win-cpu-x64.zip` | — | `b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69` |
| llama.cpp `cuda-12.4` | `llama-b11612-bin-win-cuda-12.4-x64.zip` | — | `4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12` |
| llama.cpp `vulkan` | `llama-b11612-bin-win-vulkan-x64.zip` | — | `e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9` |
| llama.cpp `cudart-12.4` | `cudart-llama-bin-win-cuda-12.4-x64.zip` | — | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| llama.cpp `cuda-13.4` | `llama-b11612-bin-win-cuda-13.4-x64.zip` | — | `e1c59ee10ead069dee12bf5a1f5a3ed294b144ea3f9c5006aa89675c5306de38` |
| llama.cpp `cudart-13.4` | `cudart-llama-bin-win-cuda-13.4-x64.zip` | — | `738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668` |
| 模型 | `Hy-MT2-7B-Q4_K_M.gguf` | 4624648896 | `9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b` |
| 模型 | `Hy-MT2-1.8B-Q8_0.gguf` | 1908528192 | `5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4` |
| 模型 | `HY-MT1.5-1.8B-Q8_0.gguf` | 1908528288 | `6789b06d0902f2f5312c0e1703d56ccbddfcfb6c653d22519b7c720f7db9a98e` |

来源：llama.cpp `b11612` GitHub Release asset digest；HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、`tencent/Hy-MT2-1.8B-GGUF`、`tencent/HY-MT1.5-1.8B-GGUF`）。下载后强制比对，不一致即删除并换源。

## 3. 一致性核对

| 检查 | 结果 | 说明 |
|---|---|---|
| 插件语法 | 一致 | compile() 通过 |
| 插件版本 == 预期 | 一致 | PLUGIN_INFO 1.7.1 / 预期 1.7.1 |
| zip 文件名含插件版本 | 一致 | ai_translate_plugin_v1.7.1.zip |
| zip 条目 == 5 个预期文件 | 一致 | 多出：无；缺少：无 |
| zip 不含空目录/运行库/模型/日志 | 一致 | 干净 |
| zip 内文件 == 分享副本 | 一致 | 全部一致 |
| 官方哈希：llama.cpp 6 项 | 一致 | 登记 6 项；不一致：无 |
| 官方哈希：模型 3 项 | 一致 | 登记 3 项；不一致：无 |
| 官方哈希：9 个合法 SHA-256 | 一致 | 9 个 |
| 官方哈希：llama.cpp 标签 b11612 | 一致 | 源码常量 |
| 分享包不含 bin/models/logs/downloads/config.json | 一致 | 干净 |
| 无绝对路径/用户名泄漏 | 一致 | 未发现 |
| 术语基表：_meta.total_entries == 实际分类合计 | 一致 | meta 8246 / 实际 8246 |
| 术语基表：_meta.categories == 实际分类条数 | 一致 | meta 分类数 9 / 实际分类数 9，差异：无 |
| tools 术语基表 == 随包术语基表 | 一致 | 一致 |
| 术语库：随包条目 >= 8000 | 一致 | 8246 条 |
| 术语库：随包 >= 分类基表 | 一致 | 8246 / 8246 |
| 使用说明含 exe SHA-256 | 一致 | 171ce0028151d06b… |
| 使用说明含当前 zip SHA-256 | 一致 | 39dd38f17a230d55… |
| 使用说明含插件版本 | 一致 | ai_translate 1.7.1 |
| dist 只保留当前版本 zip | 一致 | 仅当前版本 |

## 4. 术语数据规模

| 文件 | 条目 |
|---|---:|
| `glossary_builtin.json`（随包，运行时匹配） | 8246 |
| `stalker_termbase.json`（分类导入） | 8246 |

分类明细：阵营与组织 34、人物与军衔 2952、地点与区域 386、变异生物 52、物品·武器·装备·神器 683、任务 908、百科与术语 2859、界面与提示 345、新闻·对话·其他 27

## 5. 存储相关（仅插件资产）

| 角色 | 路径 | 大小 | 文件数 | 说明 |
|---|---|---:|---:|---|
| 运行时备份 | `.workbuddy\backups\ai_translate_cleanup_20261015_154625\bin` | — | — | 已删除；插件按官方哈希重新下载 |
| 旧插件包 1.4.0 | `ai_translate_plugin_1.4.0.zip` | — | — | 已删除（2026-10-23） |
| 旧解析器压缩包 | `plugins.rar` | — | — | 已删除（核对无独有文件） |
| 陈旧 assets 副本 | `STALKER Localization Toolkit 1.1.0` | — | — | 已删除（仅剩旧 xray_pkg.zip） |

> 运行库/模型不入库、不随插件包分发；当前未保留本地缓存，首次使用由插件按官方 SHA-256 下载。

## 6. 校验方式

```powershell
# 校验当前插件 zip
Get-FileHash 'dist\ai_translate_plugin_v1.7.1.zip' -Algorithm SHA256

# 重新生成清单
python tools/gen_release_manifest.py
```

## 7. 备注

- 本清单只读文件，不修改插件；范围仅限 `plugins/`、根目录文档与插件 zip。
- 插件包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。
- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，需重新核对。
- 工具集源码/发行版已在清理中移除；历史版本仍可从 Git 历史取回。
