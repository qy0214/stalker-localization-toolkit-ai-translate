# stalker-localization-toolkit-ai-translate

面向 **STALKER Localization Toolkit 1.1.0** 的离线 AI 翻译插件（`ai_translate`）。
基于 Hy-MT2 / llama.cpp，在本地完成 X-Ray 文本、cfgxml、ltx 与脚本的翻译，
不依赖在线 API。

**当前版本 1.9.4（2026-10-31）**：标准术语库 v2（canon/names/technical +
会话词表）、TM-lite 高重复精确复用；1000 条 A/B 术语采纳率 96.8% vs 35.7%；复杂文本表 800 条 +
大文本表 200 条回归 0 漏译；移除 HY-MT1.5 旧模型档。完整变更见下文。

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
| `dist/ai_translate_plugin_v1.9.4.zip` | 可直接分发的插件包 |
| `tests/` | 269 项回归测试 + 分享包卫生测试 |
| `tools/` | 术语库构建脚本 + 原始语料/词典 + 发布清单生成器 |
| `RELEASE_MANIFEST.md` / `release_manifest.json` | 发布清单（版本、哈希、一致性核对） |
| `.github/workflows/ci.yml` | CI：跑两套测试 + 发布清单漂移门禁 |

## 版本与校验

- 插件版本：`ai_translate 1.9.4`
- 当前 zip SHA-256：
  `7a3c9040793a1703cc0185ce5bfd50ffc071b9878bb8d0c86c36cbcc0cda3020`
- 下载链路官方哈希：llama.cpp `b11612` 的 6 个 Windows 包 + 2 个 HuggingFace 模型
- 术语规模：`glossary_builtin.json` 8850 条 / `stalker_termbase.json` 8850 条；
  canon 1375 / names 3067 / technical 18 / reference 148

```powershell
Get-FileHash 'dist\ai_translate_plugin_v1.9.4.zip' -Algorithm SHA256
```

完整核对结果见 [`RELEASE_MANIFEST.md`](RELEASE_MANIFEST.md)。

## 1.6.0 – 1.9.4 变更摘要

- **1.9.4**：俄文词条英文映射审计 —— 4,821 条俄文键逐条对照
  raw_pairs 英文候选：3,523 一致、61 译名冲突、135 有英文候选未入库、1,102 无候选；
  修正 Волкодав/Глухарь/Гребень/Кастет、风衣/水壶/U盘/手提箱等 20 余条；
  清理 `Lake→小飞机`、`Level→地点`、`the Discrete→太平洋`、`Hammer→悍马`
  等错误英文键；术语库 8850 条（canon 1375 / names 3067）。
- **1.9.3**：英文物品/武器专项审计 —— 474 条拉丁键完成分类，清理
  `Vintorez→螺纹切削`、`Metal Key→金属扳手`、`Card→电路板`、
  `Army uniform→军用防弹背心` 等错配；修正妈妈的项链/黑石碎片/博罗夫军大衣；
  补录 `Browning High-Power→勃朗宁大威力手枪`、`Circuit board`、
  `Army body armor`、`Anomalous stabilizer`；术语库 8853 条 / canon 1375。
- **1.9.2**：修正全量补漏时的误翻 —— 会话词条只做提示、不再整条硬替换；
  物品/武器跳过纯拉丁型号名；口径/弹药/`.44 Magnum`/`M995 AP` 加技术串保护；
  文本表补标识符过滤；跳过 `___scriptE__`/`___cfgxml__` 的调试串；
  credits/ogg/enc_weapons 的文件级专名保护生效；补提取
  `set_level_changer_invitation`/`set_description`/`AddItem(game.translate_string(...))`
  等脚本显示调用；清空会话词表缓存。
- **1.9.1**：移除 **HY-MT1.5-1.8B** 旧模型档（旧版对提示词/术语约束的指令遵循较弱，
  容易把术语提示和系统规则当成可选建议）。内置模型收敛为 7B 与 Hy-MT2 1.8B 两档，
  自选 GGUF 仍保留；旧配置里的 `mt15` 自动迁移到 `mt2`，无独显/低显存也推荐
  Hy-MT2 1.8B。
- **1.9.0**：标准术语库 v2 —— 随包库明确分成 canon（1377 硬词）/
  names（3068 人物软层）/ technical（18 条整条精确）/ reference（148 条参考软词）；
  运行时索引补 ё/е 变体归一、拼写变体映射、names 与小写拉丁 canon 的首字母
  大小写敏感（`crystal` 不再命中正文普通名词，`Crystal` 神器仍命中）。
  新增 `tools/audit_termbase.py` 质量审计（跨层冲突/屈折形/占位符/技术词/
  副本一致性）与 `tools/ab_glossary_1000.py` 1000 条 A/B。实测（1.8B、温度 0、
  同一提示词，只切换术语提示）：术语采纳率 **96.8% vs 35.7%**，整条全采纳
  **95.4% vs 24.9%**，A 更好 717 / B 更好 1 / 持平 282；canon 97.0% vs 33.9%，
  names 软层 96.3% vs 39.8%。新增 TM-lite：仅当同一批文本精确重复率 ≥20%
  且样本 ≥40 时启用同源复用，不做模糊匹配、不建跨 MOD 持久库。复杂脚本 +
  大文本表回归保持 0 漏译。
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
- **1.7.2**：继续修卡死 —— llama-server 日志从最大 verbose 降到 `-lv 4`
  （单次 25 MB / 28 万行 → 约 16 KB），GPU 上卡判定仍可用；条目级进度
  （峰值 470 条/秒）改为只写运行日志，界面只保留每 50 个文件一条进度。
- **1.8.2**：按你的确认修正 ANTHOLOGY B 档：Мутант→变异体、Мутанты→变种人、Седых→灰白，并补入 Шрам→疤脸（人物软词）。术语库 8838。
- **1.8.1**：合并 ANTHOLOGY 2.1 的 A+B 档 —— 从 62,118 条对齐语料中
  筛出 148 条高置信人物/装备软词，放入 `ANTHOLOGY 参考` 分类（不硬替换）；
  人工确认 3 条纠错（Мутант/Мутанты→变异体，Седых→谢德赫）；未确认冲突和
  `Шрам` 不并入。术语库 8689→8837，硬词仍 1377。
- **1.8.0**：术语体系分层 —— 通用术语库只保留宇宙稳定硬词（阵营/地点/变异体/
  神器，1377 条），人物名转精确/软提示；新增运行时**会话词表**：扫描当前 MOD
  的人名/物品/地点候选，批量翻译、校验、本机缓存，不要求每 MOD 手工建档；
  新增技术精确表（单位等）与可选 `tools/build_mod_terms.py` 参考补丁词表。
- **1.7.6**：正确性与多 MOD 适配 —— 修 1.7.5 字面文本层的裸 ID 缺陷：
  63 个脚本曾被写成 `sgtat_gen_text_*` 全局变量（nil），现统一改为
  `game.translate_string("...")`，并只改显示类调用/文本变量；log、
  `string_find`/`string_gsub`、比较串保持原样。新增英语源文识别与英语
  提示词（语言全名、英语粗口/口语锚例、英语功能词过滤）；数字/型号误判
  与 `f[A-Z]` 调试正则收窄（回收 579 条漏译）；多行文本改软换行 + 换行
  保真重试；提示词回显/粗口软化硬校验；术语库从 NS7 多版本、OP、OLR25、
  疤脸、WOT 多源重建到 8689 条（人名/物品/变异体 443 条新增、57 条纠错）。
- **1.7.5**：针对新 MOD 的错误率优化 —— 源语言默认 **rus**（不再把英语意译当原文，
  修掉 all right→“所有 向右”、rest→“休息”这类错误）；去掉“ogg 子串命中就整表
  跳过”和 name/credits/weapons 整类跳过（实测漏掉 2 万条对话/人名/百科）；
  cp1251 文本表转 UTF-8 时保留 `<rus>/<eng>` 标签；术语表只强制/提示
  **专名类**（阵营/地点/生物/物品/人物），对话/界面/任务类普通词不再注入
  （修 `Информацию→信息`、`Здоровье→生命值`、`Искренне→火花` 等语境错误）；
  新增脚本/UI 字面文本层：抽到新表、原文件换 ASCII ID、自动注册
  `localization.ltx`；新增功能词重复清理（有有→有）。
- **1.7.4**：识别 **ID 引用层**（跨 MOD 通用）。不少 MOD 的 XML/脚本只写条目 ID
  （`<text>some_id</text>`、`game.translate_string("some_id")`），正文在
  `configs/text/**` 的文本表里；旧筛选器把它们当“没有可译文本”全部跳过。
  现在自动建 4 万级 ID 索引、扫描 XML/脚本引用，**原地更新定义该 ID 的文本表**
  （不改写引用、不生成新 ID、不碰 `localization.ltx` 的加载顺序）；引用层文件
  原样复制，保证不漏文件。另加：`$var`/`%s`/`%c[...]` 等运行占位符 mask +
  译后校验（不一致保留原文）、已中文条目剔除占位符后再判定、专名/歌名/型号/
  调试串保护、无改动表字节复制（保留 BOM/注释/空格）、预览不写盘。
- **1.7.3**：修真正的卡死根因 —— 随包 cfgxml 解析器的正则
  `(?:\s*|[^/>])*?` 在 `<texture .../>` 密集的文件上会灾难性回溯
  （`ui_faction_patches.xml` 实测 **58 秒**、`ui_icons_actor_outfit.xml`
  超过 1 分钟），全程持有 GIL，Tk 主线程完全无法响应；两次运行都停在
  同一个位置（5050/6122）。正则改为有界写法并排除自闭合标签，293 个
  cfg 文件最慢 1 ms，输出候选不变。另修进程生命周期：llama-server 接入
  Windows Job Object（kill-on-close），工具集被任务管理器强杀时不再留下
  占显存的孤儿；启动时自动清理旧孤儿；用 `--parallel 1` 降低槽位开销。
- 发布清单改为**真校验**：术语基表按实际分类求和、新增"tools 副本 == 随包"
  与"dist 只保留当前版本 zip"检查；CI 用 `--check` 做漂移门禁。

## 开发与测试

```powershell
# 269 项回归测试
python tests/test_ai_translate_fixes.py

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
