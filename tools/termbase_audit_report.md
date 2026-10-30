# 标准术语库 v2 质量审计

- 生成：`tools/audit_termbase.py`
- 术语库：`8855` 条；canon `1377`、names `3068`、technical `18`、reference `148`
- 严重度：blocking 0 / high 0 / medium 2 / low 2 / ok 16

## 结论

无 blocking / high 问题；当前术语库可随包发布。medium/low 为已知边界，运行时已按层/大小写策略规避。

## 明细

| 严重度 | 检查 | 结果 |
|---|---|---|
| ok | `schema` | v2 元数据/分层 完整 |
| ok | `same_source_conflict` | 规范化同源、译文不同（不含仅引号差异）：0 组 |
| medium | `quoted_vs_bare` | 引号形态不同且译文不同（不同源串，运行时不合并）：5 组 |
| ok | `case_conflict` | 仅大小写/ё 不同且译文冲突：0 组 |
| ok | `empty` | 空原文/空译文：0 条 |
| ok | `cjk_source` | 原文含中文：0 条 |
| ok | `target_no_cjk` | 非技术层译文不含中文：0 条 |
| low | `identity` | 非技术层原文=译文：1 条 |
| ok | `placeholder` | 占位符集合不一致：0 条 |
| ok | `sentence_in_canon` | canon 层疑似长句/带句末标点：0 条 |
| ok | `inflection_conflict` | 同分类屈折形译名冲突：0 组 |
| medium | `inflection_cross_cat` | 跨分类同词干不同译名（可能是不同概念）：1 组 |
| low | `risky_latin_canon` | 小写单单词拉丁 canon（运行时仅认大写专名）：30 条 |
| ok | `technical_missing` | 插件 _TECHNICAL_EXACT 未登记进术语库：0 条 |
| ok | `technical_mismatch` | 技术词译文与插件常量不一致：0 条 |
| ok | `variant_target` | 变体映射指向不存在的词条：0 条 |
| ok | `variant_value` | 变体与规范词条译文不同：0 条 |
| ok | `case_policy` | 风险拉丁词未写入 case_sensitive：0 条 |
| ok | `copy_sync` | 工具库与随包副本：plugins\ai_translate_assets\stalker_termbase.json=一致 |
| ok | `builtin_sync` | glossary_builtin.json 与分类库摊平结果：一致 |

### quoted_vs_bare 样例

```text
'loners' -> [('阵营与组织', '"Loners"', '“中立者”'), ('百科与术语', 'Loners', '独行者潜行者')]
'army' -> [('阵营与组织', '"Army"', '“军事”'), ('百科与术语', 'Army', '军方')]
'battery' -> [('物品·武器·装备·神器', 'Battery', '蓄电池'), ('百科与术语', '"Battery"', '“电池”')]
'inferno' -> [('百科与术语', '"Inferno"', '“炼狱”'), ('百科与术语', 'Inferno', '地狱')]
'burner' -> [('百科与术语', '"Burner"', '“炙烤”'), ('百科与术语', 'Burner', '燃烧器')]
```

### inflection_cross_cat 样例

```text
地点与区域:Генераторы→焦脑器 / 物品·武器·装备·神器:Генератор→干扰器
```

## 已知边界（不是本轮能自动证明的）

- 语义误译（同名不同人、同词不同语境）只能靠人工/语境抽查；
- 带引号的 158 条词条与不带引号的同名词条是不同源串，审计不把它们合并；
- names 层 3068 条只做整条精确/首字母大写提示，不进入强制替换。
