# 术语库构建工具

本目录的脚本用于从原始对齐语料重建术语表。

## 输入（随仓库分发）

```text
raw_pairs.json                 俄/英↔简中 对齐语料（约 15.5 MB）
dict_ns_ogsr_rus-chs.json      俄→简中词典
dict_ns_ogsr_eng-chs.json      英→简中词典
dict_current_2026-06-21.json   旧版人工词表（legacy）
新建 文字文件.txt              人工三语分类表
```

> 这些文件包含《S.T.A.L.K.E.R.》游戏/模组原文，权利归原权利人所有；
> 收录在此仅用于构建本项目术语库，不代表对其再分发授权。

## 脚本

- `build_glossary.py`：从 `raw_pairs.json` 生成俄/英词典
- `build_termbase.py`：合并语料、词典与人工词表，生成分类术语库
- `stalker_termbase.json`：已生成的分类术语库（同时随插件分发）

## 重建

在仓库根目录运行：

```powershell
cd tools
python build_glossary.py
python build_termbase.py
```

生成的 `stalker_termbase.json` 复制到 `plugins/ai_translate_assets/` 即可随插件分发。
发布清单会校验 `tools/stalker_termbase.json` 与随包副本**逐字节一致**，避免按本文
重建时把线上的人工修订（补录/定稿译名/PDA 口径）整批回退。

运行时词库分两层：

- `plugins/ai_translate_assets/glossary_builtin.json`：**随包只读层**，由发布流程维护；
  当前没有一键生成脚本（它由插件端的展开结果固化），改动请直接编辑后重跑清单生成器。
- `<用户数据目录>/glossary_user.json`：**用户覆盖层**，插件界面里的新增/删除/清空写在这里，
  格式为 `{"_format": "ai-translate-glossary-user/1", "set": {...}, "del": [...],
  "clear_builtin": false}`；旧版扁平文件会被自动识别为"与内置的差异"并迁移。

## 多 MOD 术语挖掘（1.7.6）

`build_multisource_terms.py` 按 `<string id>` 跨目录对齐 NS7 各版本、NS 2/19、
OP、OLR25、疤脸、WOT 的汉化补丁，只产出高置信度名词术语（人名、地点、变异体、
物品/神器），并把冲突写进 `multisource_report.md`；不会自动覆盖未确认的旧词。

```powershell
# 1) 以“合并前的术语库”作为 baseline（否则词已在新库里，会被当成已存在）
git show HEAD:tools/stalker_termbase.json > "$env:TEMP\termbase_base.json"
python tools/build_multisource_terms.py --baseline "$env:TEMP\termbase_base.json"
python tools/merge_multisource_terms.py
```

- `multisource_terms.json`：新增/人工确认覆盖/冲突/分类移动，是合并的输入；
- `merge_multisource_terms.py`：写入 `tools/stalker_termbase.json`、插件随包副本和
  `glossary_builtin.json`；
- 运行前请确认 `C:\game` 下的补丁路径仍存在；路径在脚本顶部 `DEFAULT_PATCHES`。

> 术语库仍是“随包只读”；用户覆盖层在 `%LOCALAPPDATA%`，不会被本流程清掉。

## 术语库 v2 / 质量审计 / A-B（1.9.0）

```powershell
python tools/upgrade_termbase_v2.py   # 分层、变体、技术词、人工修正（幂等）
python tools/audit_termbase.py --strict
python tools/ab_glossary_1000.py      # 需要本机 llama-server + 1.8B 模型
python tools/regress_complex_large.py # 20 个复杂脚本 + 10 个大文本表
```

- `upgrade_termbase_v2.py`：把分类库升级到 `stalker-toolkit-termbase/2`，
  写 `_meta.layers`（canon/names/technical/reference）、`_meta.variants`、
  `_meta.case_sensitive`，并把插件里的 `_TECHNICAL_EXACT` 数据化；
  同时应用高置信修正（引号形态/屈折形/交叉层冲突）与移除项。重复运行安全。
- `audit_termbase.py`：检查跨层同源冲突、同分类屈折冲突、空值、占位符、
  CJK 缺失、长句误入 canon、技术词漏登记、变体指向、副本一致性。
  产物 `termbase_audit_report.md` / `.json`；`--strict` 在 blocking/high 时退出 1。
- `ab_glossary_1000.py`：固定模型/温度/提示词，只切换术语提示，统计
  术语采纳率、整条全采纳率、格式完整性；默认读 NS7 源文，结果写桌面报告目录。
- `regress_complex_large.py`：复用冻结的 S01–S20（脚本字面层）与
  Q01–Q10（大表，带中文参考）输入，和新版/旧版基线对比。

## 可选：从当前 MOD 的补丁抽参考词表（不写入通用库）

用户手上有某个 MOD 的汉化补丁时，可先抽一份该 MOD 的高置信名词，导入插件的
“术语 → 导入JSON”，作为本次任务的参考层：

```powershell
python tools/build_mod_terms.py `
  --base "D:\MOD\gamedata" `
  --patch "D:\MOD\汉化\gamedata" `
  --out "D:\MOD\mod_terms.json"
```

- 输出分类 `参考补丁`，插件会自动摊平；
- 只抽名词形，补丁可能有误，工具不保证正确性；
- 不写 `stalker_termbase.json`，不影响其它 MOD；
- 通用库仍是随包的一份，角色名/物品名由运行时“会话词表”处理。

## ANTHOLOGY 2.1 补充词（1.8.1）

`tools/anthology_terms_soft.json` 收录从 ANTHOLOGY 2.1 汉化包筛出的
148 条高置信新词（53 人物 + 95 物品/装备），全部放在 `ANTHOLOGY 参考`
软分类；另含 3 条人工纠错（Мутант/Мутанты→变异体，Седых→谢德赫）。
`Шрам` 不并入通用库，留给会话词表/参考补丁。

```powershell
python tools/merge_anthology_terms.py
```

重建顺序：

```text
build_glossary -> build_termbase -> build_multisource_terms --apply
-> merge_anthology_terms
```

`ANTHOLOGY 参考` 不在 `_HARD_TERM_CATEGORIES` 中，只做精确/软提示，
不会跨 MOD 硬替换。

## 英文物品/武器审计（1.9.3）

- `english_item_audit.csv`：474 条拉丁物品/武器键的逐条决策
  （源文 / 原译 / 保留英文或译成中文 / 建议译名 / 置信度）；
- `english_item_audit.md`：高置信修正、坏英文键与补录清单；
- 数据修正由 `upgrade_termbase_v2.py` 的 `EN_FIXES` / `EN_REMOVALS` /
  `EN_ADDITIONS` 幂等应用，重建术语库后不会回退。

## 俄文词条英文映射审计（1.9.4）

- `ru_en_mapping_audit.csv`：4,821 条俄文键逐条对照 raw_pairs 英文候选，
  标注 `一致 / 译名冲突 / 英语未入库 / 无英语候选` 与优先级；
- `ru_en_mapping_audit.md`：统计、已修正项与仍需人工确认的 2 条物品冲突；
- 数据修正由 `upgrade_termbase_v2.py` 的 `RU_EN_FIXES` 幂等应用。
