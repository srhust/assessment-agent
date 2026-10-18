"""R3c 语言推理确定性核：词典关系证据 + 方向签名 + 类比/近义/反义判别。

纪律（与 verbal_tool 同一哲学）：
- 关系判定只能来自 `LEXICON`/`EN_LEXICON` 中**人工编写的词条**（def + 关系字段）；
  未登录词、未标注词对一律 unknown_pair，绝不凭"模型直觉"补世界观；
- 关系标签自带方向（GENUS_UP ≠ GENUS_DOWN，PART_OF ≠ HAS_PART…），"方向/粒度偷换"
  即 mismatch，不算同关系；
- 多重关系词对必须**覆盖题干全部签名关系**才算候选（判别性不足 → ambiguous）；
- audit_relation 独立重推导并对账词典 source_text（篡改词条 → mismatch）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..schemas import (LanguageRelationOption, LanguageRelationPlan, RelationEvidence)

# ------------------------------------------------------------------ 词条数据
# LEXICON 数据块见本文件末尾（人工编写，2026-10-18 R3c）。字段语义：
#   def 人工释义；hyper 上位（a 是 b 的一种）；parts b 的组成部分清单；part_of a 是 b 的部分；
#   made_of a 由 b 制成；products a 可加工成 b；function a 的用途/功能行为；
#   place a 的典型场所；tools a 使用的工具；cause a 引起 b；syn/ant 近义/反义。
_LEX_PATH = Path(__file__).with_name("lexicon_r3c.json")


def load_lexicon() -> Dict[str, Dict[str, Any]]:
    data = json.loads(_LEX_PATH.read_text(encoding="utf-8"))
    return data.get("zh", {}), data.get("en", {})


_LEX_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}


def _lex(lang: str = "zh") -> Dict[str, Dict[str, Any]]:
    if lang not in _LEX_CACHE:
        zh, en = load_lexicon()
        _LEX_CACHE["zh"], _LEX_CACHE["en"] = zh, en
    return _LEX_CACHE[lang]


def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# ------------------------------------------------------------------ 关系推导

def infer_relations(a: str, b: str, lang: str = "zh") -> List[Tuple[str, str, str]]:
    """返回 [(tag, field, source_text)]：a-b 词对的全部可判定关系（含方向）。

    字段单值/多值均可（str 或 list），列表字段语义为"a 与列表中每个词都存在该关系"；
    SYN/ANT 按语言学的对称性做双向闭包（来源词条与字段保留在 field@source 中）。
    """
    L = _lex(lang)
    out: List[Tuple[str, str, str]] = []
    ea, eb = L.get(a, {}), L.get(b, {})
    if not ea or not eb:
        return out

    def add(tag: str, field: str, holder: Dict[str, Any]) -> None:
        out.append((tag, field, holder.get("def", "")))

    if b in _as_list(ea.get("hyper")) or b in _as_list(ea.get("hypers")):
        add("GENUS_UP", "hyper", ea)
    if a in _as_list(eb.get("hyper")) or a in _as_list(eb.get("hypers")):
        add("GENUS_DOWN", "hyper", eb)
    if (b in _as_list(ea.get("part_of")) or b in _as_list(ea.get("parts_of"))
            or a in _as_list(eb.get("parts"))):
        add("PART_OF", "part_of", ea)
    if (b in _as_list(ea.get("parts")) or a in _as_list(eb.get("part_of"))
            or b in _as_list(eb.get("parts_of"))):
        add("HAS_PART", "parts", ea)
    if b in _as_list(ea.get("made_of")):
        add("MADE_FROM", "made_of", ea)
    if b in _as_list(ea.get("products")):
        add("PRODUCES", "products", ea)
    if b in _as_list(ea.get("function")):
        add("FUNCTION", "function", ea)
    if b in _as_list(ea.get("place")) or a in _as_list(eb.get("place")):
        # PLACE 语义=活动/事物与其典型场所：场所对活动是反向同一关系（老师↔学校），
        # 不视为方向偷换（PLACE 无对偶标签）
        add("PLACE", "place", ea if b in _as_list(ea.get("place")) else eb)
    if b in _as_list(ea.get("tools")):
        add("TOOL", "tools", ea)
    if b in _as_list(ea.get("cause")):
        add("CAUSE", "cause", ea)
    if a in _as_list(eb.get("cause")):
        add("EFFECT", "cause", eb)
    # R4a：SYN/ANT 语义上对称——双向闭包，来源词条与字段保留在 field@source 与 def 里
    if b in _as_list(ea.get("syn")):
        add("SYN", "syn@a", ea)
    elif a in _as_list(eb.get("syn")):
        add("SYN", "syn@b", eb)
    if b in _as_list(ea.get("ant")):
        add("ANT", "ant@a", ea)
    elif a in _as_list(eb.get("ant")):
        add("ANT", "ant@b", eb)
    return out


# ------------------------------------------------------------------ 题面解析

_PAIR_ZH = re.compile(r"[“\"『「《]?([\u4e00-\u9fa5]{1,10})[：:∶]\s*"
                      r"([\u4e00-\u9fa5]{1,10})[”\"』」》]?")
_EN_ANALOGY = re.compile(r"(\w+)\s+is\s+to\s+(\w+)\s+as\s+(\w+)\s+is\s+to", re.I)
_ANALOGY_Q = re.compile(r"关系最(?:为)?相似|与.{0,14}(?:关系|类似|相似)|类比|相当于|与之相同|一致|关系相同|相同于")
_SYN_Q = re.compile(r"近义词|意思(?:最)?相近")
_ANT_Q = re.compile(r"反义词|意思(?:最)?相反|相对")

_LEX_PAIR_SPLIT = re.compile(r"[：:∶]")


def parse_pair(option_text: str) -> Optional[Tuple[str, str]]:
    """把 'A：B' 类选项拆成词对；'的' 字尾等噪声剥离。"""
    m = _PAIR_ZH.search(option_text) or _EN_ANALOGY.search(option_text)
    if m and m.lastindex and m.lastindex >= 2:
        return m.group(1), m.group(2)
    if "：" in option_text or ":" in option_text:
        parts = _LEX_PAIR_SPLIT.split(option_text.strip().strip("“”\"'"))
        if len(parts) == 2 and all(p.strip() for p in parts):
            return parts[0].strip(), parts[1].strip()
    return None


def detect_lang(text: str) -> str:
    return "en" if not re.search(r"[\u4e00-\u9fa5]", text) else "zh"


def _fail(code: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": False, "failure": {"code": code, "detail": detail or {}}}


# ------------------------------------------------------------------ 类比核

def core_analogy(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    m_en = re.search(r"(\w+)\s+is\s+to\s+(\w+)\s+as\s+(\w+)\s+is\s+to\s*\??", stem, re.I)
    if m_en:
        w1, w2, w3 = m_en.group(1).lower(), m_en.group(2).lower(), m_en.group(3).lower()
        opt_pairs: List[Tuple[str, Sequence[str]]] = []
        for label, text in options:
            mm = re.search(r"[A-Za-z]+", text)
            if not mm:
                return _fail("bad_option_pair", {"label": label})
            opt_pairs.append((label, (w3, mm.group(0).lower())))
        return _analogy_common(stem, (w1, w2), opt_pairs, "en", "en_analogy")
    lang = detect_lang(stem)
    m = _PAIR_ZH.search(stem)
    if not m:
        return _fail("no_stem_pair")
    pair = (m.group(1), m.group(2))
    zh_pairs: List[Tuple[str, Tuple[str, str]]] = []
    for label, text in options:
        p = parse_pair(text)
        if p is None:
            return _fail("bad_option_pair", {"label": label})
        zh_pairs.append((label, p))
    return _analogy_common(stem, pair, zh_pairs, lang, "analogy")


def _analogy_common(stem: str, pair: Tuple[str, str],
                    opt_pairs: Sequence[Tuple[str, Sequence[str]]],
                    lang: str, task: str) -> Dict[str, Any]:
    rels_stem = infer_relations(pair[0], pair[1], lang)
    plan = LanguageRelationPlan(task_type=task, question_target=stem[-40:],
                                stem_pair=[pair[0], pair[1]])
    if not rels_stem:
        plan.conflicts.append(f"题干词对 {pair} 无词典关系证据（unknown pair）")
        return _fail("no_lexicon_evidence", {"stem_pair": list(pair)})
    sig = sorted({t for t, _, _ in rels_stem})
    plan.relation_signature = sig
    plan.evidence = [RelationEvidence(word_a=pair[0], word_b=pair[1], field=f,
                                      source_text=s, direction="a->b")
                     for _, f, s in rels_stem]
    cands: List[str] = []
    for label, (c1, c2) in opt_pairs:
        rels = infer_relations(c1, c2, lang)
        tags = sorted({t for t, _, _ in rels})
        ao = LanguageRelationOption(label=label, pair=[c1, c2], own_relations=tags)
        if not tags:
            ao.verdict = "unknown_pair"
            ao.notes = "词对未在关系词典登录，无法确定同/异关系"
        elif set(sig) <= set(tags):
            ao.verdict = "match"
            ao.matched = sig
            cands.append(label)
        elif set(tags) & set(sig):
            ao.verdict = "partial"
            ao.matched = sorted(set(tags) & set(sig))
            ao.missing = sorted(set(sig) - set(tags))
            ao.notes = "部分满足题干关系，判别性不足"
        else:
            rev_map = {"GENUS_UP": "GENUS_DOWN", "GENUS_DOWN": "GENUS_UP", "PART_OF": "HAS_PART",
                       "HAS_PART": "PART_OF", "MADE_FROM": "PRODUCES", "PRODUCES": "MADE_FROM",
                       "CAUSE": "EFFECT", "EFFECT": "CAUSE"}
            swaps = sorted({rev_map[s] for s in sig if s in rev_map and rev_map[s] in tags})
            ao.missing = sorted(set(sig) - set(tags))
            if swaps:
                # R4a contradicted：有可靠证据显示方向不一致（方向偷换）——积极排除
                ao.verdict = "contradicted"
                ao.notes = f"仅方向反转关系成立（{swaps}）——方向偷换，不算满足"
            else:
                ao.verdict = "mismatch"
                ao.notes = f"关系不一致（选项关系 {tags}）"
        plan.option_analyses.append(ao)
    plan.unresolved_competitors = [a.label for a in plan.option_analyses
                                    if a.verdict == "unknown_pair"]
    if len(cands) == 1:
        plan.proposed_answer = cands[0]
        unknowns = plan.unresolved_competitors
        plan.confidence = 0.85 if not unknowns else 0.6
        if unknowns:
            plan.uncertainty_reasons.append(
                f"开放词典：竞争项 {unknowns} 未登录、无法排除；唯一已知匹配≠唯一正确答案，"
                "仅提案，接受降级 unverified（R4a）")
        plan.assumptions = ["关系判定以人工词典为唯一世界观来源；词典外关系不可见（未知≠不存在）"]
        return {"ok": True, "plan": plan}
    if len(cands) > 1:
        plan.conflicts.append(f"多个选项满足题干关系签名: {cands}")
        return _fail("ambiguous_relation", {"candidates": cands, "signature": sig})
    return _fail("no_matching_relation",
                 {"signature": sig,
                  "verdicts": {a.label: a.verdict for a in plan.option_analyses}})


# ------------------------------------------------------------------ 近义/反义核

def core_word_relation(stem: str, options: Sequence[Tuple[str, str]],
                       want: str) -> Dict[str, Any]:
    """want ∈ {"syn", "ant"}：找出与题干词近义/反义的选项。"""
    m = re.search(r"[“\"『「]([\u4e00-\u9fa5]{1,8})[”\"』」]", stem) or \
        re.search(r"[:：]\s*([\u4e00-\u9fa5]{1,8})\s*[。]?$", stem)
    if not m:
        return _fail("no_target_word")
    target = m.group(1)
    lang = "zh"
    L = _lex(lang)
    plan = LanguageRelationPlan(task_type="synonym" if want == "syn" else "antonym",
                                question_target=stem[-40:], stem_pair=[target, ""])
    if target not in L:
        return _fail("target_not_in_lexicon", {"word": target})
    key = "syn" if want == "syn" else "ant"
    rels = _as_list(L[target].get(key))
    plan.evidence.append(RelationEvidence(word_a=target, word_b="", field=key,
                                          source_text=L[target].get("def", ""),
                                          direction="a->b"))
    cands: List[str] = []
    for label, text in options:
        w = re.sub(r"[^一-龥A-Za-z]", "", text)
        ao = LanguageRelationOption(label=label, pair=[target, w])
        own = sorted({t for t, _, _ in infer_relations(target, w, lang)})
        own2 = sorted({t for t, _, _ in infer_relations(w, target, lang)})
        ao.own_relations = sorted(set(own) | set(own2))
        if want.upper() in ao.own_relations:
            # R4a：对称闭包判定——目标关系可由任一侧词条字段证实（来源保留在 evidence/field）
            ao.verdict = "match"
            ao.matched = [want.upper()]
            cands.append(label)
        elif not ao.own_relations:
            ao.verdict = "unknown_pair"
            ao.notes = "选项词未登录词典（未知不等于不存在，不能排除）"
        else:
            ao.verdict = "mismatch"
            ao.notes = f"可判定关系 {ao.own_relations} 不含目标 {want.upper()}"
        plan.option_analyses.append(ao)
    plan.unresolved_competitors = [a.label for a in plan.option_analyses
                                    if a.verdict == "unknown_pair" and a.label not in cands]
    if len(cands) == 1:
        plan.proposed_answer = cands[0]
        plan.confidence = 0.85 if not plan.unresolved_competitors else 0.6
        if plan.unresolved_competitors:
            plan.uncertainty_reasons.append(
                f"开放词典：{plan.unresolved_competitors} 无法排除，接受降级 unverified（R4a）")
        plan.assumptions = ["近义/反义判定以人工词典 syn/ant 字段为唯一证据（对称闭包）"]
        return {"ok": True, "plan": plan}
    if len(cands) > 1:
        return _fail("ambiguous_word_relation", {"candidates": cands})
    return _fail("no_matching_word_relation",
                 {"verdicts": {a.label: a.verdict for a in plan.option_analyses}})


# ------------------------------------------------------------------ 独立审计

def audit_relation(stem: str, options: Sequence[Tuple[str, str]], answer: str,
                   plan_dict: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """独立复核：重推导关系签名 + 词典 source_text 对账 + 竞争项排除 + 唯一性。

    与 core_* 共享 LEXICON 数据（世界观必须同源，否则无法判定），但**推导路径独立**
    （逐选项重新推导标签集合、显式方向对账，不复用求解中间结果）。
    返回 {status, problems}；status ∈ {confirmed, refuted, insufficient}。
    """
    problems: List[str] = []
    if answer not in dict(options):
        return {"status": "refuted", "problems": [f"答案标号 {answer!r} 不在选项集"]}
    # ① 题干签名重推导
    m_en = re.search(r"(\w+)\s+is\s+to\s+(\w+)\s+as\s+(\w+)\s+is\s+to\s*\??", stem, re.I)
    if m_en:
        w1, w2, w3 = (m_en.group(i).lower() for i in (1, 2, 3))
        lang = "en"
        sig_now = {t for t, _, _ in infer_relations(w1, w2, "en")}
        mm = re.search(r"[A-Za-z]+", dict(options)[answer])
        c_pair = (w3, mm.group(0).lower()) if mm else None
        ans_rels = ({t for t, _, _ in infer_relations(c_pair[0], c_pair[1], "en")}
                    if c_pair else set())
    else:
        m = _PAIR_ZH.search(stem)
        if not m:
            return {"status": "insufficient", "problems": ["题干词对不可解析"]}
        pair = (m.group(1), m.group(2))
        lang = detect_lang(stem)
        sig_now = {t for t, _, _ in infer_relations(pair[0], pair[1], lang)}
        c = parse_pair(dict(options)[answer])
        if not c:
            return {"status": "refuted", "problems": ["答案选项不含可解析词对"]}
        ans_rels = {t for t, _, _ in infer_relations(c[0], c[1], lang)}
    if not sig_now:
        return {"status": "insufficient", "problems": ["题干词对在词典中无关系证据"]}
    if not (sig_now <= ans_rels):
        problems.append(f"方向/签名不一致：题干 {sorted(sig_now)}，答案 {sorted(ans_rels)}")
    # ② source_text 对账（词条被篡改 → 不一致）
    L = _lex(lang)
    if plan_dict:
        for ev in plan_dict.get("evidence", []):
            entry = L.get(ev.get("word_a"), {})
            if entry and ev.get("source_text") != entry.get("def", ""):
                problems.append(f"词条释义证据与当前词典不一致: {ev.get('word_a')}")
    # ③ 竞争选项：其他选项必须可判定且不覆盖签名
    for label, text in options:
        if label == answer:
            continue
        if m_en:
            mm = re.search(r"[A-Za-z]+", text)
            if not mm:
                continue
            others = {t for t, _, _ in infer_relations(w3, mm.group(0).lower(), "en")}
        else:
            c = parse_pair(text)
            if not c:
                continue
            others = {t for t, _, _ in infer_relations(c[0], c[1], lang)}
        if sig_now and sig_now <= others:
            problems.append(f"竞争选项 {label} 同样满足全部题干关系（唯一性失败）")
    # ④ 未登录竞争项：不能积极排除——记录 unresolved（规则通道如实声明后可接受，
    # LLM 通道由 skill verify 拒绝；确定性来自唯一 match，不来自"排除证明"）
    unresolved: List[str] = []
    if plan_dict:
        unresolved = [a.get("label") for a in plan_dict.get("option_analyses", [])
                      if a.get("verdict") == "unknown_pair" and a.get("label") != answer]
    if problems:
        return {"status": "refuted" if any("竞争选项" in p or "签名不一致" in p for p in problems)
                else "insufficient", "problems": problems, "unresolved": unresolved}
    return {"status": "confirmed", "signature": sorted(sig_now), "unresolved": unresolved}
