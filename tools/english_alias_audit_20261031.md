# 英文术语别名审计（2026-10-31）

## 结论

英文源 OLR 的 hard 术语命中从 **93.0%** 提升到 **98.1%**，
但不是靠继续加词，而是：

1. 补 12 条高置信英文物品别名；
2. 把 10 个英文同形普通词从 hard 降到 reference 软提示；
3. 给 `Duty/Freedom/Garbage` 加首字母大小写限制；
4. 修正 `izomorf→同形体`。

回放 OLR 英文随机测试的源/译文：hard 术语 493 项 / 33 项未命中
→ 413 项 / 8 项未命中，采纳率 **93.0% → 98.1%**。

## 补录的 12 条英文别名

| 英文 | 中文 |
|---|---|
| `activated bombmaker's briefcase` | 已激活的爆破手手提箱 |
| `professor kruglov's flash drive` | 克鲁格洛夫教授的U盘 |
| `insane stalker's flash drive` | 疯子的U盘 |
| `the prisoner's flash drive` | 俘虏的U盘 |
| `lucky's pda` | 幸运儿的PDA |
| `tyran's pda` | 暴君的PDA |
| `borov's key` | 博罗夫的钥匙 |
| `giant's younger brother` | 巨人之弟 |
| `great-grandma's beads` | 曾祖母的项链 |
| `grandma's beads` | 祖母的项链 |
| `izomorf` | 同形体 |
| `anomlaous stabilizer`（源文拼写） | 异常稳定器 |

## 降为软提示的英文同形词

这些词在英文里首先是普通词，不应该在正文里强制成物品名：

`Note→便条`、`Order→命令`、`Bread→面包`、`flash→闪光`、
`hide→表皮`、`Soul→“灵魂”`、`Military→军方`、`Flesh→豚兽`、
`Rat→变异鼠`、`crystal→水晶`。

它们仍会在精确命中时作为提示出现，只是不强制替换。

## 保留但加大小写限制

`Duty`、`Freedom`、`Garbage` 继续 hard，但只认首字母大写：
`guard duty`、`freedom`、`garbage` 这类普通语境交给模型。

## 剩余 8 项与运行时语义修复

回放剩下 8 项中，两处是明确语义错：

- `Controller → 脑波怪`：模型译成“控制器”；
- `Freedom faction`：模型译成“自由派团体/自由派势力”。

已在 1.9.10 增加英文源定点修复：

- 源文有 `controller/controllers` 时，把译文里的“控制器/控制者”改成“脑波怪”；
- 源文有首字母大写 `Freedom` 或 `Freedom faction` 时，把“自由派团体/势力/派别/阵营/组织”
  改成“自由团”；
- 只在英文源、且源文确实出现该词时生效；普通 lowercase `freedom` 不动。

1.8B 实测：

- `controller` → `脑波怪`
- `Eliminate the leader of Freedom faction` → `消灭“自由团”的头目`

其余 6 项：

- `Professor Kruglov's flash drive`：“闪存盘” vs “U盘”（同义）；
- `Garbage`：“垃圾场” vs “废料场”（同义）；
- `Tyran's PDA`：输出多了空格“暴君的 PDA”（格式）；
- `PP-4a sensor`：输出小写 a，期望 PP-4A（格式）。

这些不再通过扩表硬压。

## 边界

- 英文别名来自 NS7 英译和 OLR 英文源，仍可能有其它 MOD 的拼写变体；
- 单字英文普通词不做全局 hard，避免 `note/order/flash` 这类误伤；
- 下一步若要继续，应优先补 `Controller`、`Freedom faction` 等明确语义错，
  而不是把剩余 8 项都扩成 hard。
