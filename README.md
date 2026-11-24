# stalker-localization-toolkit-ai-translate

面向 **STALKER Localization Toolkit 1.1.0** 的离线 AI 翻译插件（`ai_translate`）。
基于 Hy-MT2 / llama.cpp，在本地完成 X-Ray 文本、cfgxml、ltx 与脚本的翻译，
不依赖在线 API。

**当前版本 1.9.25（2026-11-25）**：小模型交付可靠性加固——零变化文本不落盘，
旧账本按 pipeline + SHA-256 强制迁移，取消/写盘失败/终检硬失败不再报“全部完成”，
生成 ID 定义表与正文保持事务一致，并补齐跨进程互斥与故障恢复。
完整变更见 [`CHANGELOG.md`](CHANGELOG.md)。

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
| `dist/ai_translate_plugin_v1.9.25.zip` | 可直接分发的插件包 |
| `CHANGELOG.md` | 1.6.0–1.9.25 各版本完整修复明细与证据 |
| `tests/` | 回归测试（契约/功能/各版本专项）+ 分享包卫生测试 |
| `tools/` | 术语库构建脚本 + 原始语料/词典 + 发布清单生成器 |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `.github/workflows/ci.yml` | CI：跑两套测试 + 发布清单漂移门禁 |

## 版本与校验

- 插件版本：`ai_translate 1.9.25`
- 当前 zip SHA-256：
  `7bc31563fd8477b21a687e01c06c046a8eb4c1df9d795dd5afa5cc914aa07d90`
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 2 个 HuggingFace 模型
- 术语规模：`glossary_builtin.json` 24455 条 / `stalker_termbase.json` 24455 条；
  13 个分类，两文件条目数一致（随包表允许扁平 + 分类段混合结构）；
  canon 4327（含 1.9.23 新增「高频硬词」51 条）/ names 5182（含「人工对照」
  1932 条）/ technical 18 / reference 14928。
  1.9.19 起 `Gunslinger 参考`/`GAMMA 参考` 两层（13946 条）退役，数据留档在
  2.x 仓库 `corpus/soft_reference_retired.json`，不参与直取与提示

```powershell
Get-FileHash 'dist\ai_translate_plugin_v1.9.25.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 版本迭代

完整明细（修复清单、证据与量化结果）见 [`CHANGELOG.md`](CHANGELOG.md)。

| 版本 | 摘要 |
|---|---|
| **1.9.25** | 小模型交付可靠性加固：零变化/原样回显不落盘；账本 pipeline + SHA-256 迁移；取消/异常/写盘失败/账本失败阻止完成；生成 ID 事务与损坏恢复；结构化终检、token 序列、语义/数字/上下文校验；跨进程互斥。新增 54 项交付闸门，11/11 测试文件全绿 |
| **1.9.24** | 同步 2.0.27（术语库交叉一致性审计，新方法）：15 组同源冲突统一、1087 条中文半角标点规范化、Мамины бусы 系列与英文对齐；交叉审计 S1/S4a 归零，测试 10/10 全绿 |
| 1.9.23 | 同步 2.0.26（术语库深度审查）：16 MOD / 1623 文件词频扫描 + 人工复核，新增「高频硬词」51 条挂 canon（地名格变补位、英文地名、CNPP/ЧАЭС 机组、Lab X-8/10/16/18、固定物品与 UI）；修正 9 处错译；词库 24456→24455（13 分类），测试 10/10 全绿 |
| 1.9.22 | 同步 2.0.25（aera 审计 + 人工对照包比对）：ident 假跳过修复（`_name/_descr/_text/_title` 等 id 后缀豁免，2206 条找回）；强制术语长文阈值（≥600 字符走分段链）；上下文串入切片级兜底；字面换行收敛 11 条；CHS 人工对照收割 1932 条入「人工对照」硬层（P1/P2/P4）；口语化提示词锚例；词库 22524→24456，测试 10/10 全绿（新增 1.9.22 专项 24 项） |
| 1.9.21 | 同步 2.0.24（IWP 全量深度审计驱动）：批量对话路径补齐占位符掩码/还原/数字/粘连/错位五重校验（P0，%c[default] 被译坏类运行期破坏的唯一来源）；「（原文数字/型号 …）」注记退役（中文纪年认可清零年代误报 + 定向重试 + 裸数字兜底 + 型号数字双注记修复）；粘连残词（克里门enko 类半音译）确定性闸门；短名回显定名指令兜底；汉字间空格收敛；未解析引用报告收紧。测试 9/9 全绿（新增 1.9.21 专项 27 项） |
| **1.9.20** | 同步 2.0.23 界面选项精简：引用层/完整补丁/TM-lite/会话词表收编固定开启（关闭只有坏处），保留 5 项各挂一行开/关后果说明；强制术语/保护专名出厂默认改开；引用层无文本表自动回退整目录模式 |
| **1.9.19** | 同步 2.0.22：GAMMA/Gunslinger 软层 13946 条整体退役（逐条语义抽检明确错率约 31%）、硬层子串替换加固（半中半英写出 112→0）、型号形态直取、藏宝点目录豁免、ogg/music 保护收窄、词库内容级修正 13 条、第三方署名补全；词库 22524 条，发布包 1.39→0.84 MB |
| 1.9.19 | 全库确定性错误清理 14 条 + 垃圾过滤三处口径修正 + 闸门豁免色码内数字 + 专名保护硬层子串替换 |
| 1.9.17 | 多源语料同步（Gunslinger / CoP 官中 / NS2016 / wiki / GAMMA），对齐 2.0.20 |
| 1.9.16 | 筛选器修复同步：术语直取提前、长句标题守卫、玩家可见目录豁免（对齐 2.0.21 前置项） |
| 1.9.15 及更早 | 见 [`CHANGELOG.md`](CHANGELOG.md) |

## 开发与测试

```powershell
# 348 项功能回归
python tests/test_ai_translate_fixes.py

# 54 项小模型交付闸门 / 中断恢复 / 跨进程互斥
python tests/test_1925_delivery_gates.py

# 分享包卫生 / 冷启动 / 只读位置 / 发布 zip 一致性
python tests/test_share_package.py

# 重新生成发布清单（并可用 --check 只校验不写盘）
python tools/gen_release_manifest.py
python tools/gen_release_manifest.py --check
```

术语库构建 / 审计 / 实测链：

```powershell
cd tools
python build_glossary.py
python build_termbase.py
python upgrade_termbase_v2.py   # 分层、变体、技术词、人工修正
python audit_termbase.py --strict
python ab_glossary_1000.py      # 1000 条开/关术语 A/B（需本机引擎）
```

复杂脚本 + 大文本表回归（复用已冻结的 30 个输入）：

```powershell
python tools/regress_complex_large.py
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
- Hy-MT2 7B / 1.8B：HuggingFace LFS oid

完整哈希表见 `RELEASE_MANIFEST.md` 第 2 节。

## 许可

插件随 STALKER Localization Toolkit 分发，采用 **GPL-3.0**，见 [`LICENSE`](LICENSE)。

第三方来源与许可见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：

- X-Ray 解析器与翻译流程：源自
  [wzyddg/stalker_auto-trans-tool_remake](https://github.com/wzyddg/stalker_auto-trans-tool_remake)
  （上游**未声明许可证**，版权归原作者；本仓库保留来源说明，不主张额外授权）；
- llama.cpp：MIT；Hy-MT 模型：Apache-2.0；二者均不随仓库分发。
该 GPL-3.0 许可不改变上述第三方组件的版权与授权状态。
