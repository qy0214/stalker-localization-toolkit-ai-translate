# 第三方组件与来源声明

本仓库包含或参考了以下第三方成果，各组件的版权归其原作者所有。

## 1. wzyddg/stalker_auto-trans-tool_remake — X-Ray 解析器与翻译流程

- 仓库：<https://github.com/wzyddg/stalker_auto-trans-tool_remake>
- 作者：wzyddg
- 许可证：**上游仓库未声明开源许可证**（2026-10-29 通过 GitHub API 检查为
  `license: null`）。相关代码的版权归原作者；本仓库仅保留来源与作者信息，
  不对上游代码主张任何额外授权。
- 本仓库的引用方式：

  | 本仓库 `plugins/ai_translate_assets/xray_pkg.zip` 内的文件 | 相对上游 |
  |---|---|
  | `xRayXmlParser/__init__.py` | 原样收录 |
  | `xRayXmlParser/entityDefinition.py` | 原样收录 |
  | `xRayXmlParser/xmlFileGenerator.py` | 原样收录 |
  | `xRayXmlParser/xmlxReader.py` | 已修改：改用标准库 XML 解析、加入编码候选链与健壮性修复 |
  | `xRayXmlParser/xRayTextAnalyzer.py` | 已修改：正则转义修正、俄文字母表修正、文本字段兼容处理；2026-10-30 修复 cfgxml 标签正则的灾难性回溯（`ui_faction_patches.xml` 单文件 58 秒 → 2 毫秒） |

- `plugins/ai_translate.py` 的翻译流水线（文本 XML / cfgxml / ltx / script 的
  "提取—翻译—回填"思路）参考了上游 `xRayTrans.py`，但为独立实现：本地
  llama.cpp 推理、完成账本、原子写盘、术语分层、下载校验与断点续传等均由
  本项目实现。

> 如果你是上述代码的权利人，希望调整署名方式或移除相关代码，请在本仓库
> 开 issue 说明，我们会配合处理。

## 2. llama.cpp

- 仓库：<https://github.com/ggml-org/llama.cpp>
- 许可证：MIT
- 使用方式：不随本仓库分发，插件在用户机器上按官方 SHA-256 校验后下载
  （当前固定构建 `b11612`）。

## 3. Hy-MT 模型

- 模型：Hy-MT2-7B / Hy-MT2-1.8B（腾讯混元）
- 许可证：Apache-2.0
- 使用方式：不随本仓库分发，插件在用户机器上按 HuggingFace LFS oid 校验后下载。

## 4. 游戏原文语料

`tools/` 下的 `raw_pairs.json`、两本 NS-OGSR 词典、旧版人工词表与人工三语表
包含《S.T.A.L.K.E.R.》游戏/模组原文，权利归原权利人所有；收录在此仅用于
构建本项目的术语库，不代表对其再分发授权。

## 5. 本仓库自身许可

本仓库自身代码按 **GPL-3.0** 分发（见 [`LICENSE`](LICENSE)）。
该许可不改变上述第三方组件的版权与授权状态；引用上游未声明许可证的代码时，
请同时遵守上游仓库的要求。
