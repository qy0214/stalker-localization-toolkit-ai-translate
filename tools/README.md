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
