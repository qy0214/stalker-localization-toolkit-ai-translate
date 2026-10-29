# 术语库构建工具

本目录的脚本用于从原始对齐语料重建术语表。**原始语料与词典不随公开仓库分发**，
只保留在本地，以避免再分发游戏文本。

公开仓库包含：

- `build_glossary.py`：从 `raw_pairs.json` 生成俄/英词典
- `build_termbase.py`：合并语料、词典与人工词表，生成分类术语库
- `stalker_termbase.json`：已生成的分类术语库（同时随插件分发）

本地重建时，请把以下文件放回 `tools/`（这些文件名已加入 `.gitignore`）：

```text
raw_pairs.json
dict_ns_ogsr_rus-chs.json
dict_ns_ogsr_eng-chs.json
dict_current_2026-06-21.json
新建 文字文件.txt
```

然后运行：

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
