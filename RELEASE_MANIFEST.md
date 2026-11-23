# AI 离线翻译插件 · 发布清单

- 生成时间：`2026-11-24T05:22:29+08:00`
- 工作区：仓库根目录
- 范围：**AI 离线翻译插件（ai_translate）**
- Git：`main` @ `a797e14`（生成时 HEAD；清单本身在后续提交入库）（有未提交改动）
- 已跟踪文件：91 个，23.09 MB

## 1. 版本与交付物

| 项 | 值 |
|---|---|
| 插件版本 | `1.9.24` |
| 预期版本 | `1.9.24` |
| 适用工具集 | `STALKER Localization Toolkit 1.1.0` |
| 历史 exe FileVersion | `1.1.0.0` |

| 角色 | 路径 | 大小 | SHA-256 | 说明 |
|---|---|---:|---|---|
| 插件分享副本 | `plugins\ai_translate.py` | 381.50 KB | `9df315bdfd463b714041ed44049569ed1e8b7a40aa99aa827a028cce8c0518cb` | 纯标准库，保留必要安全说明 |
| 插件发布 zip | `dist\ai_translate_plugin_v1.9.24.zip` | 937.41 KB | `84f708102d176ae6f3ba8da2398c6698c13816859e4c3da90c8ab1cd309d049f` | 当前发布包 |
| 术语库（随包，只读） | `plugins\ai_translate_assets\glossary_builtin.json` | 1.15 MB | `387a6ce17117b99cd9c03d5efe598a3c2e96851e222a83021affe2c185009d3a` | 运行时匹配的内置层 |
| 术语基表（分类） | `plugins\ai_translate_assets\stalker_termbase.json` | 1.16 MB | `9537e729411eaf04ac939c517974ceaf44514392360dcbeb83879a909880385b` | 导入用 |
| X-Ray 解析器 | `plugins\ai_translate_assets\xray_pkg.zip` | 7.38 KB | `2f3e1dcd4b2ce701e981d5c0f3e6c16cf17782692a5a892aa9cb1e159e63f8a6` | 解析 XML/LTX/脚本 |
| 第三方来源声明 | `plugins\ai_translate_assets\THIRD_PARTY_NOTICES.txt` | 2.08 KB | `338f62ede3e6253ce1e7b513023b8535b6f5d94c33a00311bf295d421d601ff2` | 随包分发（上游来源与许可状态） |
| 使用说明 | `使用说明.txt` | 30.91 KB | `278131eb7234c1e506103dccc3e37134f7af9ab8c68589adb381bc6938420614` | 含发布包 SHA-256 |
| 术语库处理记录 | `术语库_处理记录.md` | 17.80 KB | `d353739807c1613c292abe8833f44e7f62066f5e76dd4fe2787af540e446c8f8` | 术语口径与修剪记录 |
| 大小写冲突清单 | `术语库_大小写冲突清单.md` | 2.14 KB | `e2a491e9edd55be5a949778583d4bc29222ec9bdaf78d2783ab4183dc94f8279` | 术语大小写冲突处理 |
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

来源：llama.cpp `b11612` GitHub Release asset digest；HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、`tencent/Hy-MT2-1.8B-GGUF`）。下载后强制比对，不一致即删除并换源。

## 3. 一致性核对

| 检查 | 结果 | 说明 |
|---|---|---|
| 插件语法 | 一致 | compile() 通过 |
| 插件版本 == 预期 | 一致 | PLUGIN_INFO 1.9.24 / 预期 1.9.24 |
| zip 文件名含插件版本 | 一致 | ai_translate_plugin_v1.9.24.zip |
| zip 条目 == 5 个预期文件 | 一致 | 多出：无；缺少：无 |
| zip 不含空目录/运行库/模型/日志 | 一致 | 干净 |
| zip 内文件 == 分享副本 | 一致 | 全部一致 |
| 官方哈希：llama.cpp 6 项 | 一致 | 登记 6 项；不一致：无 |
| 官方哈希：模型 2 项 | 一致 | 登记 2 项；不一致：无 |
| 官方哈希：8 个合法 SHA-256 | 一致 | 8 个 |
| 官方哈希：llama.cpp 标签 b11612 | 一致 | 源码常量 |
| 分享包不含 bin/models/logs/downloads/config.json | 一致 | 干净 |
| 无绝对路径/用户名泄漏 | 一致 | 未发现 |
| 术语基表：_meta.total_entries == 实际分类合计 | 一致 | meta 24455 / 实际 24455 |
| 术语基表：_meta.categories == 实际分类条数 | 一致 | meta 分类数 13 / 实际分类数 13，差异：无 |
| tools 术语基表 == 随包术语基表 | 一致 | 一致 |
| 术语库：随包条目 >= 8000 | 一致 | 24455 条 |
| 术语库：随包 >= 分类基表 | 一致 | 24455 / 24455 |
| 使用说明含 exe SHA-256 | 一致 | 171ce0028151d06b… |
| 使用说明含当前 zip SHA-256 | 一致 | 84f708102d176ae6… |
| 使用说明含插件版本 | 一致 | ai_translate 1.9.24 |
| dist 只保留当前版本 zip | 一致 | 仅当前版本 |

## 4. 术语数据规模

| 文件 | 条目 |
|---|---:|
| `glossary_builtin.json`（随包，运行时匹配） | 24455 |
| `stalker_termbase.json`（分类导入） | 24455 |

分类明细：阵营与组织 107、人物与军衔 3250、地点与区域 397、变异生物 79、物品·武器·装备·神器 3693、任务 2453、百科与术语 3713、界面与提示 3263、新闻·对话·其他 5351、ANTHOLOGY 参考 148、技术缩写 18、人工对照 1932、高频硬词 51

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
Get-FileHash 'dist\ai_translate_plugin_v1.9.24.zip' -Algorithm SHA256

# 重新生成清单
python tools/gen_release_manifest.py
```

## 7. 备注

- 本清单只读文件，不修改插件；范围仅限 `plugins/`、根目录文档与插件 zip。
- 插件包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。
- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，需重新核对。
- 工具集源码/发行版已在清理中移除；历史版本仍可从 Git 历史取回。
