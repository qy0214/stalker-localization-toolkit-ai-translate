#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a rus->chs term glossary (名词库) from raw_pairs.json.

Only context-independent noun phrases are kept, because the translator applies
the glossary both as an exact whole-segment match and as a post-translation
substring replace -- a wrong or sentence-like key does damage silently.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_pairs.json")
LEGACY = os.path.join(HERE, "dict_current_2026-06-21.json")
OUT_JSON = os.path.join(HERE, "dict_ns_ogsr_rus-chs.json")
OUT_EN = os.path.join(HERE, "dict_ns_ogsr_eng-chs.json")
OUT_MD = os.path.join(HERE, "glossary_report.md")

CAT_LABEL = {
    "name": "人名 / 绰号",
    "mapspot": "地名 / 标注点",
    "item": "物品 / 装备",
    "encyclopedia": "百科词条 / 术语",
    "other": "其他字符串表",
    "task": "任务名",
    "tip": "提示",
    "news": "新闻",
    "dialog": "对话",
    "ui": "界面",
    "legacy": "旧词表独有",
}

CJK = re.compile(r"[一-鿿]")
CYR = re.compile(r"[Ѐ-ӿ]")
RU_BAD = re.compile(r"[.,!?;:«»\"'()\[\]—–/\\]")
ZH_BAD = re.compile(r"[。！？；，、：…—–\"\"''()\[\]《》（）「」【】]")
TESTISH = re.compile(r"test|тест|demo|示例|测试|占位|todo|fixme", re.I)

# Function words: any hit means the source is a phrase, not a name.
RU_STOP = {
    "в", "во", "на", "над", "с", "со", "у", "об", "о", "от", "до", "для", "из",
    "из-за", "к", "ко", "по", "при", "через", "между", "без", "за", "про",
    "и", "а", "но", "да", "или", "как", "что", "чем", "это", "этот", "эта",
    "это", "эти", "мой", "мои", "твой", "наш", "ваш", "его", "её", "ее", "их",
    "мне", "тебе", "нам", "вас", "него", "неё", "нее", "них", "себя", "сам",
    "сама", "само", "сами", "там", "тут", "здесь", "где", "когда", "если",
    "только", "ещё", "еще", "уже", "ведь", "же", "ли", "бы", "не", "ни", "даже",
    "очень", "совсем", "просто", "все", "всё", "многие", "каждый", "другой",
    "другие", "такой", "такая", "такие", "какой", "которые", "кого", "чему",
    "том", "том", "об этом", "оном", "ём", "ней", "них", "него",
}
# Infinitives / imperatives that open task titles and dialog lines. A curated
# list, because suffix guessing eats nouns like "Припять" and "Память".
RU_VERB_WORDS = {
    "иди", "идём", "идем", "пойти", "сходи", "найди", "найти", "осмотри",
    "осмотреть", "доложи", "доложить", "поговори", "поговорить", "беседовать", "расскажи", "рассказать",
    "побеседовать", "принеси", "принести", "принесите", "принесём", "принесем",
    "взять", "возьми", "забери", "забрать", "сделать", "сделай", "установить",
    "починить", "отдать", "отдать", "передать", "передай", "обменять",
    "навестить", "согласиться", "присесть", "лечь", "сесть", "встать", "выйти",
    "зайти", "прийти", "добыть", "уничтожить", "убить", "достать", "открыть",
    "закрыть", "активировать", "деактивировать", "отключить", "подключить",
    "завершить", "начать", "начни", "остановить", "выключить", "включить",
    "использовать", "помочь", "помоги", "спасти", "собрать", "собери",
    "разобраться", "узнать", "выяснить", "вернуть", "отнести", "прикрыть",
    "удержать", "отбить", "поджечь", "взломать", "изготовить", "продать",
    "купить", "получить", "отправить", "вызвать", "отвести", "сопровождать",
    "накормить", "вылечить", "оживить", "прочитать", "прочесть", "изучить",
    "отсканировать", "просканировать", "записывать", "записать", "спрятать",
    "разместить", "закрепить", "обеспечить", "просить", "попросить", "ждать",
    "дождаться", "подождать", "позволить", "заставить", "научить", "починить",
    "привести", "приведите", "увести", "убрать", "убери", "очистить",
    "зачистить", "проследовать", "проникнуть", "превратить", "трансформировать",
    "проверить", "проверь", "сверить", "восстановить", "запустить", "стартовать",
    "стрелять", "застрелить", "ударить", "атаковать", "напасть", "защитить",
    "схватить", "поймать", "освободить", "выжить", "выживаеть", "сбежать",
    "убежать", "убегать", "спрятаться", "прятаться", "искать", "ищи",
    "следить", "наблюдать", "слушать", "подслушать", "смотреть", "видеть",
    "посмотреть", "посетить", "принять", "отдать", "сдать", "сдать", "получи",
    "попробовать", "испытать", "сварить", "готовить", "состыковать", "чинить",
    # finite / past forms that leak in from news and dialog fragments
    "сообщил", "сообщила", "сообщает", "рассказал", "рассказала",
    "рассказывает", "спросил", "спросила", "спрашивает", "просил",
    "просила", "просит", "просят", "предложил", "предлагает", "попросил",
    "пришёл", "пришла", "пришел", "пришла", "ушёл", "ушел", "вошёл", "вошел",
    "вышел", "вышла", "вернулся", "вернулась", "нашёл", "нашел", "нашла",
    "потерял", "потеряла", "увидел", "увидела", "заметил", "почуял", "услышал",
    "понял", "поняла", "знает", "знаю", "знают", "думает", "думаю", "надеется",
    "надеюсь", "хочет", "хочу", "хотел", "мог", "могла", "может", "должен",
    "должна", "обязан", "нужен", "нужна", "нужно", "надо", "остался", "осталась",
    "слышал", "слышала", "наблюдал", "наблюдала", "обнаружил", "обнаружил",
    "высадили", "грохнули", "поджарило", "задели", "повезло", "непраздник",
}


def ru_is_term(ru):
    if RU_BAD.search(ru) or TESTISH.search(ru) or not CYR.search(ru):
        return False
    words = ru.split()
    if not (1 <= len(words) <= 4) or len(ru) > 40:
        return False
    lowered = [w.lower() for w in words]
    if any(w in RU_STOP for w in lowered):
        return False
    if not ru[0].isupper():
        return False
    # Curated verb list only: suffix guessing kills nouns like "Припять".
    return not any(w in RU_VERB_WORDS for w in lowered)


QUOTES = "«»\"'“”‘’"


def ru_variants(ru):
    """A label stored as "«Долг»" must also answer to bare "Долг"."""
    bare = ru.strip(QUOTES)
    return [ru] if bare == ru or not bare else [ru, bare]


def ru_is_term_loose(ru):
    """Old glossary keys are curated, so only drop the sentence-length ones."""
    return 1 <= len(ru.split()) <= 4 and len(ru) <= 40


def zh_is_term(zh):
    if ZH_BAD.search(zh) or TESTISH.search(zh) or not CJK.search(zh):
        return False
    if len(zh) > 12:
        return False
    return not re.search(r"[的了是在和就都也很更被把让给过着吗呢吧啊哦嗯]", zh)


def main():
    rows = json.load(open(RAW, encoding="utf-8"))
    votes = defaultdict(Counter)
    cats = defaultdict(Counter)
    for r in rows:
        if not zh_is_term(r["zh"]):
            continue
        for variant in ru_variants(r["ru"]):
            if not ru_is_term(variant):
                continue
            votes[variant][r["zh"]] += 1
            cats[variant][r["cat"]] += 1

    # Case-insensitive grouping: the tables spell the same nickname as
    # "Архара" and "АРХАРА"; one name has to get one translation.
    by_lower = defaultdict(Counter)
    for ru, counter in votes.items():
        by_lower[ru.lower()].update(counter)
    chosen = {low: c.most_common(1)[0][0] for low, c in by_lower.items()}
    glossary = {ru: chosen[ru.lower()] for ru in votes}
    glossary = dict(sorted(glossary.items()))
    ambiguous = []
    for low, tally in by_lower.items():
        if len(tally) < 2:
            continue
        best, n = tally.most_common(1)[0]
        spellings = sorted(r for r in votes if r.lower() == low)
        ambiguous.append((" / ".join(spellings), best, n, sum(tally.values()),
                          tally.most_common(4)))
    ambiguous.sort(key=lambda x: -x[3])

    legacy = json.load(open(LEGACY, encoding="utf-8")) if os.path.isfile(LEGACY) else {}
    legacy_terms = {k: v for k, v in legacy.items() if ru_is_term_loose(k)}
    legacy_sentences = {k: v for k, v in legacy.items() if k not in legacy_terms}
    disagree = {k: (legacy[k], chosen[k.lower()]) for k in legacy_terms
                if k.lower() in chosen and chosen[k.lower()] != legacy[k]}

    # The curated legacy table wins over corpus variants; every disagreement is
    # listed in the report so it can be flipped by hand.
    merged = dict(legacy_terms)
    legacy_lower = {k.lower() for k in legacy_terms}
    for k, v in glossary.items():
        if k.lower() in legacy_lower:
            continue
        merged.setdefault(k, v)

    # Russian names decline, and the translator's post-pass replaces bare
    # source forms. "Радар" alone never matches "Радара" in the output, and a
    # blind stem replace would leave "雷达а" behind, so ship the observed
    # inflected forms too, mapped to the same translation.
    vocab = Counter()
    for r in rows:
        for w in re.findall(r"[А-ЯЁ][а-яё]+", r["ru"]):
            vocab[w] += 1
    case_ends = ("а", "я", "у", "ю", "е", "ы", "и", "ом", "ем", "ам", "ях",
                 "ах", "ов", "ев", "ами", "ями")
    cand = [w for w, n in vocab.items()
            if n >= 2 and len(w) >= 5 and w.endswith(case_ends)]
    by_head = defaultdict(list)
    for w in cand:
        by_head[w[:4]].append(w)
    covered = {k.lower() for k in merged}

    def lcp(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    best = {}
    for key, zh in merged.items():
        if len(key) < 4:
            continue
        need = max(4, len(key) - 2)
        for word in by_head.get(key[:4], ()):
            if word.lower() in covered or not (len(key) <= len(word) <= len(key) + 3):
                continue
            if lcp(word, key) < need:
                continue
            score = (lcp(word, key), len(key))
            won = best.get(word)
            if won is None or score > won[0]:
                best[word] = (score, key, zh)
    inflected = {w: zh for w, (_score, _key, zh) in best.items()}
    merged.update(inflected)

    # English-keyed companion table: 启动.bat runs the engine with -f eng, and
    # the same alignment carries the <eng> side of every kept term.
    en_votes = defaultdict(Counter)
    for r in rows:
        for variant in ru_variants(r["ru"]):
            if ru_is_term(variant) and zh_is_term(r["zh"]) and variant in glossary:
                en = r["en"].strip()
                if (en and len(en) <= 48 and 1 <= len(en.split()) <= 5
                        and not re.search(r"[<>|]|\$\(|[!?]|\.\s|\.$", en)
                        and re.search(r"[A-Za-z]", en)):
                    en_votes[en][r["zh"]] += 1
    en_table = {en: c.most_common(1)[0][0] for en, c in en_votes.items()}
    en_table = dict(sorted(en_table.items(), key=lambda kv: (-len(kv[0]), kv[0])))
    with open(OUT_EN, "w", encoding="utf-8") as f:
        json.dump(en_table, f, ensure_ascii=False, indent=1)
    merged = dict(sorted(merged.items(), key=lambda kv: (-len(kv[0]), kv[0])))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)

    by_cat = defaultdict(list)
    for ru, zh in merged.items():
        stem = best.get(ru, (0, None, zh))[1]
        cat = "legacy"
        if stem and cats[stem]:
            cat = cats[stem].most_common(1)[0][0]
        elif cats[ru]:
            cat = cats[ru].most_common(1)[0][0]
        by_cat[cat].append((ru, zh))

    # Candidate names mined from source text: capitalised words that are NOT at
    # position 0 (those are just sentence starts) and not already in the table.
    tokens = Counter()
    for r in rows:
        for m in re.finditer(r"[А-ЯЁ][а-яё]+", r["ru"]):
            if m.start() == 0:
                continue
            prev = r["ru"][m.start() - 1]
            if prev not in " ,;:":
                continue
            before = r["ru"][:m.start()].rstrip()
            if before == "" or before.endswith((".", "!", "?", "…", ":", ";")):
                continue
            tokens[m.group()] += 1
    covered = {k.lower() for k in merged}
    gaps = [(t, n) for t, n in tokens.most_common(3000)
            if n >= 15 and t.lower() not in covered and 4 < len(t) <= 24][:40]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        w = f.write
        w("# NS OGSR 汉化名词库整理报告\n\n")
        w("**产物**：`%s`（`俄语原文: 中文` 扁平字典，已按 key 长度降序排列，"
          "可直接作为 `xRayTrans.py --term-file` 使用）；另有英文键对照表 `%s`。\n\n"
          % (OUT_JSON, OUT_EN))
        w("## 用法与生效范围\n\n"
          "```bat\n"
          "python xRayTrans.py -e hy_mt2 -p <文本目录> -t chs -f rus "
          "--term-file <本目录>\\dict_ns_ogsr_rus-chs.json\n"
          "```\n\n"
          "- 术语表有两条生效路径：`TranslatorImpl.doTranslate` 的**整段精确命中**"
          "（任何源语言都生效，直接返回译名），以及 `xRayTrans.py:274` 的**译后词形替换**"
          "（只在 `-f rus` 且目标为 chs 时执行）。\n"
          "- 因此 **`-f eng` 跑英文原文时，俄语键的表不会起作用**；那种流程请改用同目录的"
          "英文键表 `dict_ns_ogsr_eng-chs.json`（仅整段精确命中生效）。\n"
          "- 变格：替换按字面量匹配，故已把语料中出现过 ≥2 次的宾格/属格等形式"
          "（如 `Долга`、`Агропроме`）一并收入并映射到同一译名；多词词组（如 "
          "`Тёмная Долина`）的变格形式无法安全推断，仍只收录原形。\n\n")
        w("## 来源与方法\n\n")
        w("- 中文侧：`C:\\game\\NS7\\MODS\\NS OGSR 2026年9月6日版本汉化补丁\\gamedata\\configs`"
          "（汉化覆盖层，`<rus>` 已被改写为中文）。\n")
        w("- 俄语侧：`C:\\game\\CCCC\\gamedata.sq_base_configs\\configs`（未翻译底版）。\n")
        w("- 两侧同路径 XML 按 `<string id>` 对齐，取 `rus` 字段成对；"
          "补丁独有、底版缺失的文件与未对齐 id 一律丢弃。\n")
        w("- 收录判据（名词性硬门槛）：俄语 1~4 词、≤40 字符、首字母大写、"
          "任一动词或虚词开头即弃、句读与括号一律不收；中文 ≤12 字、不含句读与"
          "「的了是在…」等虚词。目的是保证每条都能脱离上下文独立替换。\n")
        w("- 「«Долг»」这类带引号的派系名同时收录带引号与去引号两种写法；"
          "同名异形（`Архара` / `АРХАРА`）按不区分大小写归并，取语料最高频译名。\n\n")
        w("## 规模\n\n")
        w("| 指标 | 数值 |\n|---|---|\n")
        w("| 对齐后俄中配对 | %d |\n" % len(rows))
        w("| 通过名词判据 | %d |\n" % len(glossary))
        w("| 合并旧词表后总计 | %d（含 %d 条自动补出的变格形式） |\n"
          % (len(merged), len(inflected)))
        w("| 旧词表条目 | %d（并入 %d 条名词，另有 %d 条整句简介未并入） |\n"
          % (len(legacy), len(legacy_terms), len(legacy_sentences)))
        w("| 与旧词表译名冲突 | %d |\n" % len(disagree))
        w("| 语料内部译名不一致（取最高频） | %d |\n" % len(ambiguous))
        w("| 英文键对照表 | %d 条（`%s`，供 `-f eng` 参考）|\n\n"
          % (len(en_table), OUT_EN))
        w("## 分类统计\n\n| 类别 | 条数 |\n|---|---|\n")
        for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
            w("| %s | %d |\n" % (CAT_LABEL.get(cat, cat), len(by_cat[cat])))
        for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
            w("\n## %s（%d 条）\n\n" % (CAT_LABEL.get(cat, cat), len(by_cat[cat])))
            w("| 俄语 | 中文 |\n|---|---|\n")
            for ru, zh in sorted(by_cat[cat])[:60]:
                w("| %s | %s |\n" % (ru, zh))
        if ambiguous:
            w("\n## 译名不一致（取最高频，其余候选列出待人工裁定）\n\n")
            w("| 俄语 | 采用 | 采用/总 | 其他候选 |\n|---|---|---|---|\n")
            for ru, zh, n, total, options in sorted(ambiguous, key=lambda x: -x[3])[:40]:
                others = ", ".join("%s(%d)" % (o, c) for o, c in options[1:])
                w("| %s | %s | %d/%d | %s |\n" % (ru, zh, n, total, others))
        if disagree:
            w("\n## 与旧词表译名不同（旧值已保留优先，此处仅列出）\n\n")
            w("| 俄语 | 旧词表 | 本语料 |\n|---|---|---|\n")
            for ru, (old, new) in sorted(disagree.items()):
                w("| %s | %s | %s |\n" % (ru, old, new))
        w("\n## 高频但未收录的名称（源文本出现 ≥15 次，建议人工补录）\n\n")
        for t, n in gaps:
            w("- %s（%d 次）\n" % (t, n))

    print("pairs %d -> terms %d, merged %d, ambiguous %d, legacy-disagree %d, gaps %d"
          % (len(rows), len(glossary), len(merged), len(ambiguous), len(disagree),
             len(gaps)))
    print("wrote %s (%d), %s (%d), %s"
          % (OUT_JSON, len(merged), OUT_EN, len(en_table), OUT_MD))


if __name__ == "__main__":
    sys.exit(main())
