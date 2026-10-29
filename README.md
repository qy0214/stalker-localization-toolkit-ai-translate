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
            glossary_builtin.json
            THIRD_PARTY_NOTICES.txt
```

重启工具集，顶部会出现「AI 翻译」栏目。详细步骤、档位说明和已知限制见
[`使用说明.txt`](使用说明.txt)。

插件包**不包含**模型、llama.cpp 运行库、配置和日志。首次运行按本机硬件
选择档位并下载；运行库/模型下载后会按发布方官方 SHA-256 校验，某个源给的
文件不对就删掉并**自动换下一个源**。安装到 Program Files 等只读位置时，
运行数据自动落到 `%LOCALAPPDATA%\STALKER Localization Toolkit\ai_translate`。

### 术语：随包层与用户层分开

- `glossary_builtin.json`：**随包只读**，升级时被新版本覆盖（正常的术语更新）。
- `glossary_user.json`：**用户层**，你在界面里新增/删除/清空的差异写到这里，
  升级不会被覆盖（旧版把两者放在同一个文件里，升级即丢自定义词条）。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `plugins/ai_translate.py` | 插件本体，纯 Python 标准库 |
| `plugins/ai_translate_assets/` | X-Ray 解析器 + 术语数据（随包只读） |
| `使用说明.txt` | 安装、使用、已知限制 |
| `THIRD_PARTY_NOTICES.md` | 第三方来源、许可状态与致谢 |
| `dist/ai_translate_plugin_v1.7.1.zip` | 可直接分发的插件包 |
| `tests/` | 116 项回归测试 + 分享包卫生测试 |
| `tools/` | 术语库构建脚本 + 原始语料/词典 + 发布清单生成器 |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `.github/workflows/ci.yml` | CI：跑两套测试 + 发布清单漂移门禁 |

## 版本与校验

- 插件版本：`ai_translate 1.7.1`
- 当前 zip SHA-256：
  `39dd38f17a230d558d346ea6d75a496f469cb6aa68fe7a4fa7a689801eb61dac`
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 3 个 HuggingFace 模型
- 术语规模：`glossary_builtin.json` 8246 条 / `stalker_termbase.json` 8246 条

```powershell
Get-FileHash 'dist\ai_translate_plugin_v1.7.1.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 1.6.0 – 1.7.1 变更摘要

- **附加提示词真正生效**：原先在工作线程里读 Tk 控件，必然抛
  `main thread is not in main loop` 并被吞成空串 —— 功能实际从未工作。
  现在由 UI 线程读好并缓存，工作线程只读缓存。
- **不再漏译**：输出"文件已存在"不再等于"已译完"。新增完成账本
  （输入 size+mtime + 输出 size），输入一变就重译；文本 XML 还会校验输出
  是否覆盖输入的全部 id。输出改为 tmp→replace 原子写，中断不留半截成品。
- **升级不丢用户术语**：随包术语与用户覆盖层分离（见上）。
- **切模型不再卡界面**：停止旧引擎（taskkill，最长约 15 秒）移出 UI 线程。
- **校验失败自动换源**：与文档描述一致（原实现只删文件让用户手动重试）。
- **CUDA 运行时完整性**：三组 DLL 齐全才算装好，并写安装标记；
  解压改为临时目录 + 原子移入，半套解压不再被误判为可用。
- **占位符安全切分**：长文本切分不会切断 `$$Tn$$`，避免回退到低质量切片路径。
- **1.6.1**：启动自检不再把小预算下的空输出误报成 tokenizer 不完整 ——
  推理型 GGUF（如 MiniCPM5）会把 16 token 全用在 `reasoning_content` 上、
  `content` 为空；现在先 16 token 快速探测，失败再用 256 token 复核，
  仍不收尾才判为 tokenizer 问题。
- **1.6.2**：补充第三方来源声明 —— X-Ray 解析器与翻译流程源自
  [wzyddg/stalker_auto-trans-tool_remake](https://github.com/wzyddg/stalker_auto-trans-tool_remake)
  （上游未声明许可证），随包附 `THIRD_PARTY_NOTICES.txt`，仓库根附
  `THIRD_PARTY_NOTICES.md`（含逐文件原样/修改说明）。
- **1.6.3**：补充实测可用的下载源 —— 运行库新增 4 个 GitHub 加速代理
  （gh-proxy.org / gh.nxnow / gh.catmak / gh.monlor），模型新增
  hf-mirror.net 与 ModelScope 的 download 端点；**原有源全部保留**，
  作为不同地区/运营商的备援，下载前逐个测速择优。
- **1.7.0**：新增 **CUDA 13.4** 可选运行库（官方 b11612 的 cuda-13.4 构建 +
  cudart 13.4），主包与 cudart 各 **23 个下载源**；硬件检测扩展为
  `(名称, 显存, 驱动版本, compute_cap)`，按「驱动 ≥ 580 且 CC ≥ 7.5」
  判断可尝试性；启动失败或未上卡会自动回退 CUDA 12.4。默认仍为 12.4（兼容面最广）。
- **1.7.1**：修翻译过程中界面卡死 —— 实测 6122 个文件的批次里局部日志达到
  **3000 行/秒**，而界面每条日志都整块重绘 400 行、UI 泵一次抽干队列，
  Tk 主线程被日志占满。现在：逐文件日志改为每 50 个文件一条进度
  （明细只进运行日志）、日志刷新合并为一个泵周期一次、UI 泵单次限 200 项/15ms。
- 发布清单改为**真校验**：术语基表按实际分类求和、新增"tools 副本 == 随包"
  与"dist 只保留当前版本 zip"检查；CI 用 `--check` 做漂移门禁。

## 开发与测试

```powershell
# 116 项回归测试
python tests/test_ai_translate_fixes.py

# 分享包卫生 / 冷启动 / 只读位置 / 发布 zip 一致性
python tests/test_share_package.py

# 重新生成发布清单（并可用 --check 只校验不写盘）
python tools/gen_release_manifest.py
python tools/gen_release_manifest.py --check
```

术语库构建链：

```powershell
cd tools
python build_glossary.py
python build_termbase.py
```

术语构建的全部输入（`raw_pairs.json`、三本词典、人工三语表）都随仓库分发，
位于 `tools/`，检出后可直接重建；文件清单与用法见
[`tools/README.md`](tools/README.md)。这些文件包含《S.T.A.L.K.E.R.》游戏/模组
原文，权利归原权利人，收录仅用于构建本项目术语库。生成后的
`stalker_termbase.json` 可复制到 `plugins/ai_translate_assets/`（发布清单会
校验 `tools/` 与随包副本一致，避免按文档重建时把线上修订回退）。

## 官方 SHA-256

下载地址由插件内置；镜像只负责传输，校验值来自发布方元数据：

- llama.cpp `b11612`：GitHub Release asset digest
- Hy-MT2 7B / 1.8B、HY-MT1.5 1.8B：HuggingFace LFS oid

完整哈希表见 `RELEASE_MANIFEST.md` 第 2 节。

## 许可

插件随 STALKER Localization Toolkit 分发，采用 **GPL-3.0**，见 [`LICENSE`](LICENSE)。

第三方来源与许可见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：

- X-Ray 解析器与翻译流程：源自
  [wzyddg/stalker_auto-trans-tool_remake](https://github.com/wzyddg/stalker_auto-trans-tool_remake)
  （上游**未声明许可证**，版权归原作者；本仓库保留来源说明，不主张额外授权）；
- llama.cpp：MIT；Hy-MT 模型：Apache-2.0；二者均不随仓库分发。
该 GPL-3.0 许可不改变上述第三方组件的版权与授权状态。
