# AI 离线翻译插件 · 发布清单

- 生成时间：`2026-10-23T16:53:56+08:00`
- 工作区：仓库根目录
- 范围：**AI 离线翻译插件（ai_translate）**
- Git：`main`（单提交仓库；清单与代码同一提交）
- 已跟踪文件：22 个，1.51 MB

## 1. 版本与交付物

| 项 | 值 |
|---|---|
| 插件版本 | `1.5.1` |
| 预期版本 | `1.5.1` |
| 适用工具集 | `STALKER Localization Toolkit 1.1.0` |
| 历史 exe FileVersion | `1.1.0.0` |

| 角色 | 路径 | 大小 | SHA-256 | 说明 |
|---|---|---:|---|---|
| 插件分享副本 | `plugins\ai_translate.py` | 164.58 KB | `42b261cd680ab0dbfc858d36b0877fbf526ad8088907dfcaef550869b40725e7` | 纯标准库，保留必要安全说明 |
| 插件发布 zip | `dist\ai_translate_plugin_v1.5.1.zip` | 261.68 KB | `b8f3c1d1002e51ec4532729dad832b26f3f2b4708ded5aa17189936a6839c14a` | 当前发布包 |
| 术语库（扁平） | `plugins\ai_translate_assets\glossary_user.json` | 279.12 KB | `804d3814c5a84e27d2b3921aa72dabe38e66b351185358a8e63a3ba10e95cbd4` | 运行时匹配 |
| 术语基表（分类） | `plugins\ai_translate_assets\stalker_termbase.json` | 312.80 KB | `1e0f59668ab509e5eb000e560321acfe99827a74ec989103ad03178549ba864b` | 导入用 |
| X-Ray 解析器 | `plugins\ai_translate_assets\xray_pkg.zip` | 7.15 KB | `b575b4935ddaf2dd4c59a77068731d686aaf26e1e546077e55e1bc0fcb451a30` | 解析 XML/LTX/脚本 |
| 使用说明 | `使用说明.txt` | 13.05 KB | `c071ddf3edb5166436f63afccab56c25bed0e8e9454d0b4609fcdd7ff5467373` | 含发布包 SHA-256 |
| 术语库处理记录 | `术语库_处理记录.md` | 6.01 KB | `a45481a602698bd9361214b0e3a0eb8e53dde343faa4c0b17ad6aee1d120fdc9` | 术语口径与修剪记录 |
| 大小写冲突清单 | `术语库_大小写冲突清单.md` | 11.44 KB | `74ed765010f284411e081c6031c53a00cb22121d547d1e27aa9915645be8d31b` | 术语大小写冲突处理 |
| 历史工具集 exe | `STALKER Local Toolkit 1.1.0/STALKER Localization Toolkit 1.1.0.exe` | 3.19 MB | `171ce0028151d06b9d28bab4b671d6df957451a3fa6241d0b6cb9543b22e6947` | 2026-10-23 清单记录；文件已在清理中移除 |

> 历史 exe 已从工作区清理，哈希保留自 2026-10-23 清理前的清单；当前插件包不包含 exe。

## 2. 下载链路官方 SHA-256

| 产物 | 文件 | 字节数 | SHA-256 |
|---|---|---:|---|
| llama.cpp `cpu` | `llama-b11612-bin-win-cpu-x64.zip` | — | `b8456ccb1be98365a5a2227efa5c495508a6a3ebce881c6189a1dbeaaa170b69` |
| llama.cpp `cuda-12.4` | `llama-b11612-bin-win-cuda-12.4-x64.zip` | — | `4c6d8afd23475f6eded74c4a3019a6746aab83183a3158f3fc4de1cb7c4f5f12` |
| llama.cpp `vulkan` | `llama-b11612-bin-win-vulkan-x64.zip` | — | `e62f0674970a70b007fa3c425d191ceacabbf02406d6c9ac8b6f27cd23abe9e9` |
| llama.cpp `cudart-12.4` | `cudart-llama-bin-win-cuda-12.4-x64.zip` | — | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| 模型 | `Hy-MT2-7B-Q4_K_M.gguf` | 4624648896 | `9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b` |
| 模型 | `Hy-MT2-1.8B-Q8_0.gguf` | 1908528192 | `5c3fe0b1408a5ceb0143184ef247b11b579c525f4b02b060e6c851bb76fef1a4` |
| 模型 | `HY-MT1.5-1.8B-Q8_0.gguf` | 1908528288 | `6789b06d0902f2f5312c0e1703d56ccbddfcfb6c653d22519b7c720f7db9a98e` |

来源：llama.cpp `b11612` GitHub Release asset digest；HuggingFace LFS oid（`tencent/Hy-MT2-7B-GGUF`、`tencent/Hy-MT2-1.8B-GGUF`、`tencent/HY-MT1.5-1.8B-GGUF`）。下载后强制比对，不一致即删除并换源。

## 3. 一致性核对

| 检查 | 结果 | 说明 |
|---|---|---|
| 插件语法 | 一致 | compile() 通过 |
| 插件版本 == 预期 | 一致 | PLUGIN_INFO 1.5.1 / 预期 1.5.1 |
| zip 文件名含插件版本 | 一致 | ai_translate_plugin_v1.5.1.zip |
| zip 条目 == 4 个预期文件 | 一致 | 多出：无；缺少：无 |
| zip 不含空目录/运行库/模型/日志 | 一致 | 干净 |
| zip 内文件 == 分享副本 | 一致 | 全部一致 |
| 官方哈希：llama.cpp 4 项 | 一致 | 登记 4 项；不一致：无 |
| 官方哈希：模型 3 项 | 一致 | 登记 3 项；不一致：无 |
| 官方哈希：7 个合法 SHA-256 | 一致 | 7 个 |
| 官方哈希：llama.cpp 标签 b11612 | 一致 | 源码常量 |
| 分享包不含 bin/models/logs/downloads/config.json | 一致 | 干净 |
| 无绝对路径/用户名泄漏 | 一致 | 未发现 |
| 术语基表：total_entries == 分类合计 | 一致 | 8208 / 8208 |
| 术语库：扁平条目 >= 8000 | 一致 | 8246 条 |
| 术语库：扁平 >= 分类基表 | 一致 | 8246 / 8208 |
| 使用说明含 exe SHA-256 | 一致 | 171ce0028151d06b… |
| 使用说明含当前 zip SHA-256 | 一致 | b8f3c1d1002e51ec… |
| 使用说明含插件版本 | 一致 | ai_translate 1.5.1 |
| dist 不含旧版 v1.5.0 zip | 一致 | 已移除 |

## 4. 术语数据规模

| 文件 | 条目 |
|---|---:|
| `glossary_user.json`（运行时匹配） | 8246 |
| `stalker_termbase.json`（分类导入） | 8208 |

分类明细：阵营与组织 34、人物与军衔 2952、地点与区域 358、变异生物 42、物品·武器·装备·神器 680、任务 908、百科与术语 2862、界面与提示 344、新闻·对话·其他 28

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
Get-FileHash 'dist\ai_translate_plugin_v1.5.1.zip' -Algorithm SHA256

# 重新生成清单
python tools/gen_release_manifest.py
```

## 7. 备注

- 本清单只读文件，不修改插件；范围仅限 `plugins/`、根目录文档与插件 zip。
- 插件包只含代码 + 解析器 + 术语数据；运行库、模型、配置、日志均不随包。
- 官方哈希取自发布方元数据（2026-10-23）；若上游重新上传同标签产物，需重新核对。
- 工具集源码/发行版已在清理中移除；历史版本仍可从 Git 历史取回。
