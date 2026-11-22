# IWP 已翻译产物深度检查报告（2026-11-23）

范围：`Desktop\IWP\gamedata\translated_hy_mt2\configs\text\eng`（80 文件 / 9320 条目）
工具：`tools/verify_iwp_output.py`（源↔译逐条对照）+ `tools/audit_iwp_deep.py`（旧扫描）
核验口径：源文件自带非法注释（`<!-----` 横线数不符，X-Ray 引擎容忍）已归一后解析，
故此前被判"解析失败"的 5 个大文件（st_dialog_manager / st_dialogs_escape / marsh /
yantar / st_dynamic_news，合计 3085 条）本轮全部纳入。

## 修复明细（共 342 处，全部落盘）

### 1. 专名译名统一（322 处，词库 canon 权威口径）
- 跟踪者 → 潜行者（195）、追踪者 → 潜行者（118）—— stalker 三种译名并存是最大面积问题
- 斯特雷洛克 → 史特列洛克（10）—— Strelok 权威译名
- 普里皮亚特 → 普里皮亚季（8）
- OT-14-1A → OTs-14-1A 型突击步枪（1，原译还把 Groza 写成"手枪"）

### 2. 串段污染（4 处）
- st_dialogs|about_quests_dialog_stalkers_55：混入 Vano 段（相邻条目译文），已删；机翻残留 "kissinger" 改"嘴脸"
- st_dialog_manager|dm_anomalies_12：混入电液潭段（太空异常条目），按源文改写 Forester 句
- st_dialog_manager|dm_anomalies_36：混入辐射段，已删
- st_dialog_manager|dm_information_9：混入焦脑器/佣兵段（Generators 条目），已删并修正地名

### 3. 格式保真（2 处）
- ui_cryray_hints|ui_hint_slot_hud：颜色码内字面 \n（%c[255,\n216,186,140]）已修 + 补回缺失空行
- （stalkers_55 的 \n\n 随串段删除一并消除）

### 4. 语义串入整条重译（1 处）
- st_dialogs_escape|esc_2_12_stalker_nimble_get_order_131：**正文整段被换成 SIG-Sauer P220 的描述**（源为 FN FNX-45）。
  翻译插件的 missing_model_tokens 闸门当时已拦截并补注记（"（原文型号 FNX-45）"），但正文未还原——本次按源文手工重译，
  含 FNX 家族/2007-2009 研发背景/Joint Combat Pistol Program/2800 价格全部信息点。

### 5. 闸门误伤注记清理（1 处）
- ui_st_encyclopedia_weapons|encyclopedia_weapons_m249_text：`（原文数字 109）（原文数字 193）` 为 %c 色码分量被
  数字保真闸门误抓产生的注记，已清除。**该误伤模式建议在插件闸门中豁免色码内数字**（2.0.20 现行为仍会再产生此类注记）。

## 已定性误报（无需处理）
- "疑似截断 548 条"：中文/英文信息密度比 0.25~0.35 属正常压缩（句数复核+语义抽查确认完整），非截断。
  句数比值脚本首版有正则缺陷（中文句号误计），已修正后残留 4 条亦为省略号形态误报。
- 数字缺失 2 条：dolg_28 "10th squad"→"第十小队"、mosin "500,000/3,800,000"→"50万/380万" 为数字合理变形。
- 西里尔残留 1 条：`Sample №8с` 源串自带西里尔编号字母，保留正确。
- 占位符 0 / 漏条目 0 / 残留原文 0。

## 遗留建议
1. 50 条闸门注记条目（年份缺失类为主，如"（原文数字 1980）"）建议下一轮重译时收敛——年份信息缺失属轻微问题，
   当前不影响语义；其中型号类注记（FNX-45 已修、MP-153/IZh-81/OTs-12/StG-58/SOK-94 共 6 条）建议人工复核正文。
2. 插件闸门两处改进建议：色码内数字豁免（消除 109/193 类误伤注记）；专名保护在词库有译名时应取词库译名
   而非保留英文（Boar/Sidorovich 类人物 bio 条目实测保留英文）。
3. "Monolith/Duty/Freedom" 在译文中以裸"黑石/纪律团/自由团"出现（不带引号）与词库带引号形态并存——
   属正常对话变体，未强制统一。
