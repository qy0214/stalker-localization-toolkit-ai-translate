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
`glossary_user.json` 是运行时扁平词库，由发布流程单独维护。
