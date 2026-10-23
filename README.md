# stalker-localization-toolkit-ai-translate

面向 **STALKER Localization Toolkit 1.1.0** 的离线 AI 翻译插件（`ai_translate`）。
基于 Hy-MT2 / llama.cpp，在本地完成 X-Ray 文本、cfgxml、ltx 与脚本的翻译，
不依赖在线 API。

仓库：<https://github.com/qy0214/stalker-localization-toolkit-ai-translate>

## 安装

把仓库里的 `plugins/` 整个覆盖到工具集目录下，与
`STALKER Localization Toolkit 1.1.0.exe` 同级：

```text
STALKER Localization Toolkit 1.1.0\
    STALKER Localization Toolkit 1.1.0.exe
    plugins\
        ai_translate.py
        ai_translate_assets\
            xray_pkg.zip
            stalker_termbase.json
            glossary_user.json
```

重启工具集，顶部会出现「AI 翻译」栏目。详细步骤、档位说明和已知限制见
[`使用说明.txt`](使用说明.txt)。

插件包**不包含**模型、llama.cpp 运行库、配置和日志。首次运行按本机硬件
选择档位并下载；运行库/模型下载后会按发布方官方 SHA-256 校验，不一致即
删除并换源。安装到 Program Files 等只读位置时，运行数据自动落到
`%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate`。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `plugins/ai_translate.py` | 插件本体，纯 Python 标准库 |
| `plugins/ai_translate_assets/` | X-Ray 解析器 + 术语数据 |
| `使用说明.txt` | 安装、使用、已知限制 |
| `dist/ai_translate_plugin_v1.5.1.zip` | 可直接分发的插件包 |
| `tests/` | 回归测试与分享包卫生测试 |
| `tools/` | 术语库构建脚本 + 发布清单生成器（原始语料不入公开仓库） |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |

## 版本与校验

- 插件版本：`ai_translate 1.5.1`
- 当前 zip SHA-256：
  `b8f3c1d1002e51ec4532729dad832b26f3f2b4708ded5aa17189936a6839c14a`
- 下载链路官方哈希：llama.cpp `b11612` 的 4 个 Windows 包 + 3 个 HuggingFace 模型
- 术语规模：`glossary_user.json` 8246 条 / `stalker_termbase.json` 8208 条

```powershell
Get-FileHash 'dist\ai_translate_plugin_v1.5.1.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 开发与测试

```powershell
# 78 项回归测试
python tests/test_ai_translate_fixes.py

# 分享包卫生 / 冷启动 / 只读位置测试
python tests/test_share_package.py

# 重新生成发布清单
python tools/gen_release_manifest.py
```

术语库构建链：

```powershell
cd tools
python build_glossary.py
python build_termbase.py
```

原始语料 `raw_pairs.json`、词典和人工词表**不随公开仓库分发**，只保留在本地；
需要重建时按 [`tools/README.md`](tools/README.md) 放回 `tools/`。生成后的
`stalker_termbase.json` 可复制到 `plugins/ai_translate_assets/`。

## 官方 SHA-256

下载地址由插件内置；镜像只负责传输，校验值来自发布方元数据：

- llama.cpp `b11612`：GitHub Release asset digest
- Hy-MT2 7B / 1.8B、HY-MT1.5 1.8B：HuggingFace LFS oid

完整哈希表见 `RELEASE_MANIFEST.md` 第 2 节。

## 许可

插件随 STALKER Localization Toolkit 分发，采用 **GPL-3.0**，见 [`LICENSE`](LICENSE)。
llama.cpp 为 MIT；Hy-MT 模型为 Apache-2.0，模型文件不随仓库分发。
