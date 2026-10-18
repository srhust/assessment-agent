"""R3b 言语理解：确定性规则核 + 证据引用审计。

纪律（与 data_plan 同一套哲学）：
- 每个结论必须绑定原文**字符区间证据**（VerbalEvidence），不存在/越界/截断改义的引用一律拒绝；
- 否定、双重否定、量词、情态词的"强度只降不升"是硬约束：选项比原文更强即为 overreach；
- 规则核只覆盖可形式化子集，覆盖不到就返回结构化失败（ambiguous/no_passage/multi_blank/…），
  绝不给出猜测答案；LLM 计划必须过同一审计函数（audit）才能被接受；
- 审计函数与主分类器使用**不同的实现**（重叠度量、否定扫描各自独立），
  降低"同一 bug 自我确认"的风险，但仍不宣称能证明完整语义正确性。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..schemas import VerbalEvidence, VerbalOptionAnalysis, VerbalReasoningPlan

# ------------------------------------------------------------------ 基础文本工具

_SENT_END = "。！？；"
_PUNCT = re.compile(r"[，。！？；：、“”‘’（）()《》〈〉【】\[\]□＿_\s,.;:!?'\"~—…-]")


def norm(text: str) -> str:
    return _PUNCT.sub("", text or "")


def split_sentences(text: str) -> List[Tuple[int, int, str]]:
    """带原文偏移的句子切分（供证据区间核对）。"""
    out: List[Tuple[int, int, str]] = []
    start = 0
    for m in re.finditer(f"[{_SENT_END}]", text):
        seg = text[start:m.end()]
        if norm(seg):
            out.append((start, m.end(), seg))
        start = m.end()
    if norm(text[start:]):
        out.append((start, len(text), text[start:]))
    return out


_FUNC_CHARS = set("的了是在和与或而于把被让都也就才又却其之以着过吗呢很更最些这那有对于")


def content_bigrams(text: str) -> Set[str]:
    """CJK 二元组内容指纹（剔除虚词 Crossing 噪声）。"""
    t = norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)
            if all("\u4e00" <= c <= "\u9fff" and c not in _FUNC_CHARS for c in t[i:i + 2])}


# ------------------------------------------------------------------ 线索词抽取（否定/情态/量词）

NEG_DOUBLE = ["不是没有", "不是不", "没有不", "不无", "并非不", "未尝不是", "未尝不",
              "无不", "不可否认", "不能否认", "不会不", "非…不"]
NEG_SINGLE = ["不是", "没有", "没", "未", "非", "无", "不", "别", "莫", "勿"]
MODALITY = {"必然": 3, "一定": 3, "必定": 3, "肯定": 3, "必须": 3, "势必": 3, "定能": 3,
            "可能": 1, "也许": 1, "或许": 1, "大概": 1, "似乎": 1, "往往": 1, "通常": 1,
            "未必": 0, "不一定": 0, "不太可能": 0, "难说": 0}
QUANTIFIER = {"所有": 3, "每一": 3, "每": 3, "任何": 3, "任意": 3, "全部": 3, "都": 3, "均": 3,
              "大多数": 2.5, "多数": 2.5, "大量": 2.5, "普遍": 2.5,
              "有些": 1, "有的": 1, "部分": 1, "某些": 1, "有的": 1,
              "个别": 0.5, "少数": 0.5, "只有": 0.8, "仅": 0.8, "只": 0.8}

_CUE_WORDS = sorted(set(NEG_DOUBLE) | set(NEG_SINGLE) | set(MODALITY) | set(QUANTIFIER),
                    key=len, reverse=True)


def extract_cues(text: str) -> Dict[str, Any]:
    """按最长优先扫描线索词；双重否定先移除再数单次否定（奇偶性）。"""
    t = norm(text)
    doubles = 0
    for d in NEG_DOUBLE:
        if d == "非…不":
            continue
        c = t.count(d)
        if c:
            doubles += c
            t = t.replace(d, "◎")
    t2 = t.replace("未必", "◣").replace("不一定", "◤")  # 已计入情态 0 级
    singles = 0
    for s in sorted(NEG_SINGLE, key=len, reverse=True):
        c = t2.count(s)
        if c:
            singles += c
            t2 = t2.replace(s, "○")
    modal_vals = [v for w, v in MODALITY.items() if w in norm(text)]
    quant_vals = [v for w, v in QUANTIFIER.items() if w in norm(text)]
    return {
        "neg_parity": (doubles * 2 + singles) % 2,
        "neg_count": doubles * 2 + singles,
        "modal": max(modal_vals) if modal_vals else 2,   # 2=无标记事实层
        # 选项量词只在**显式出现**时参与比较（None=未声明全称，不算扩大）
        "quant": max(quant_vals) if quant_vals else None,
    }


# ------------------------------------------------------------------ 任务识别

TASK_PATTERNS: List[Tuple[str, str]] = [
    ("argument", r"最能?(?:削弱|质疑|反驳|支持|加强)|(?:以下|下列)哪项(?:是|为)?.{0,6}(?:必要)?前提|假设"),
    ("cloze", r"依次填入|填入.{0,6}(?:横线|空格|括号)|最恰当的一?项?"),
    ("inference", r"(?:可以|无法|不能|最不能|不可能)(?:推出|得出)|推不出|由此可知|下列说法(?:正确|错误|不正确)|根据(?:这段|上述|以上)(?:文字|材料)"),
    ("main_idea", r"主旨|意在|强调|概括|适合做(?:的)?标题|中心(?:思想|论点)|主要(?:观点|说明|论述)"),
]

NEGATIVE_Q = re.compile(r"(?:无法|不能|最不能|不可能)推出|推不出|下列说法(?:错误|不正确)|不恰当|例外|削弱|质疑|反驳")
FALSE_Q = re.compile(r"下列说法(?:错误|不正确)|不正确的一项|不恰当|（错误）")
CANNOT_Q = re.compile(r"(?:无法|不能|最不能|不可能)(?:推出|得出)|推不出")


def detect_task(stem: str) -> Optional[str]:
    for task, pat in TASK_PATTERNS:
        if re.search(pat, stem):
            return task
    # 无提问词的填空句：题干含空括号/下划线占位 + 词语型选项 → 选词填空（如真实卷面把
    # 指令放在题组说明里的情况）；指令缺失时按句子本身处理，仍要求证据全部来自原文
    if re.search(r"（\s*）|\(\s*\)|_{2,}|____", stem):
        return "cloze"
    return None


def _question_tail(stem: str) -> Tuple[str, int]:
    """提问句 = 最后一个句号级标点之后的句子；返回 (提问文本, 材料结束偏移)。"""
    marks = [m.end() for m in re.finditer(r"[?？]", stem)]
    if not marks:
        return "", len(stem)
    tail_start = marks[-1]
    # 回溯到该问句的起点
    prev = max([0] + [m.end() for m in re.finditer(f"[{_SENT_END}]", stem[:tail_start]) if m.end() <= tail_start])
    q = stem[prev:].strip()
    m0 = re.search(r"[?？]", stem[prev:])
    end = prev + (m0.end() if m0 else len(q))
    return stem[prev:end].strip(), prev


def _ev(option: str, span: Tuple[int, int, str], role: str) -> VerbalEvidence:
    return VerbalEvidence(option=option, start=span[0], end=span[1], text=span[2], role=role)


def _verb_fail(code: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"ok": False, "failure": {"code": code, "detail": detail or {}}}


# ------------------------------------------------------------------ 阅读推断核

def _overlap(a: Set[str], b: Set[str]) -> float:
    if not a:
        return 0.0
    return len(a & b) / len(a)


def classify_inference_option(label: str, text: str, sentences: List[Tuple[int, int, str]],
                              stem: str) -> VerbalOptionAnalysis:
    ob = content_bigrams(text)
    best_span, best_ratio = None, 0.0
    for span in sentences:
        r = _overlap(ob, content_bigrams(span[2]))
        if r > best_ratio:
            best_span, best_ratio = span, r
    new_terms = sorted({g for g in ob if g not in content_bigrams(stem)})[:8]
    ana = VerbalOptionAnalysis(label=label, option_text=text, definitive=False)
    if best_ratio < 0.34 or best_span is None:
        ana.relation = "absent"
        ana.definitive = True
        ana.notes = f"与材料内容重叠 {best_ratio:.2f}，无对应陈述"
        ana.new_terms = new_terms
        return ana
    ana.evidence = [_ev(label, best_span, "support")]
    oc, sc = extract_cues(text), extract_cues(best_span[2])
    diffs: List[str] = []
    if oc["neg_parity"] != sc["neg_parity"]:
        diffs.append(f"否定极性不一致（选项 {oc['neg_parity']} vs 原文 {sc['neg_parity']}）")
    if oc["modal"] > sc["modal"]:
        diffs.append(f"情态增强（{sc['modal']}→{oc['modal']}）")
    if oc["quant"] is not None and sc["quant"] is not None and oc["quant"] > sc["quant"]:
        diffs.append(f"量词扩大（{sc['quant']}→{oc['quant']}）")
    ana.cue_diffs = diffs
    ana.new_terms = new_terms
    if diffs and best_ratio >= 0.4:
        # 极性翻转与"肯定性断言"视为矛盾；单纯增强视为过度推断
        ana.relation = "contradicted" if any("否定" in d for d in diffs) else "overreach"
        ana.definitive = True
        return ana
    if best_ratio >= 0.62 and len(new_terms) <= 2:
        ana.relation = "supported"
        ana.definitive = True
        return ana
    ana.relation = "insufficient"
    return ana


def core_inference(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    question, passage_end = _question_tail(stem)
    sentences = [s for s in split_sentences(stem[:passage_end] or stem) if norm(s[2])]
    if not sentences:
        return _verb_fail("no_passage")
    question_target = question or stem
    negative = bool(NEGATIVE_Q.search(question_target))
    if CANNOT_Q.search(question_target):
        mode = "cannot"          # 无法推出：非被支持候选（矛盾/过度/缺席）
    elif FALSE_Q.search(question_target):
        mode = "false"          # 说法错误：只有"与原文矛盾"才算候选
    else:
        mode = "true"           # 可以推出：必须被原文支持
    analyses = [classify_inference_option(l, t, sentences, stem[:passage_end] or stem)
                for l, t in options]
    CANDIDATE_RELATIONS = {
        "cannot": {"contradicted", "overreach", "absent"},
        "false": {"contradicted"},
        "true": {"supported"},
    }[mode]
    cands = [a for a in analyses if a.relation in CANDIDATE_RELATIONS]
    weak_others = any(a.relation == "insufficient" for a in analyses if a not in cands)
    plan = VerbalReasoningPlan(task_type="inference", question_target=question,
                               negative_target=negative,
                               passage_spans=[e for a in analyses for e in a.evidence])
    plan.option_analyses = analyses
    plan.unsupported_claims = sorted({t for a in analyses for t in a.new_terms
                                       if a.label == (cands[0].label if len(cands) == 1 else "")})
    if len(cands) == 1:
        plan.proposed_answer = cands[0].label
        plan.confidence = 0.8 if not weak_others else 0.6
        if weak_others:
            plan.uncertainty_reasons.append(
                "部分干扰项规则不可判定（未被积极排除）；接受依赖唯一命中+验证层独立重叠复核")
        plan.assumptions = ["规则子集：内容重叠+否定/情态/量词强度核对；非完整语义证明"]
        return {"ok": True, "plan": plan}
    if len(cands) > 1:
        plan.uncertainty_reasons.append(f"多个选项满足{mode}判据: {[a.label for a in cands]}")
        return _verb_fail("ambiguous_options", {"candidates": [a.label for a in cands], "mode": mode})
    plan.uncertainty_reasons.append(f"没有选项满足{mode}判据")
    return _verb_fail("insufficient_overlap",
                      {"relations": {a.label: a.relation for a in analyses}, "mode": mode})


# ------------------------------------------------------------------ 主旨概括核

CONCLUSION_CUES = ["因此", "所以", "可见", "总之", "综上", "因而", "由此看来", "显然",
                   "关键在于", "问题是", "需要", "应该", "应当", "必须", "旨在", "核心", "真正"]


def core_main_idea(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    question, passage_end = _question_tail(stem)
    passage = stem[:passage_end] or stem
    sentences = [s for s in split_sentences(passage) if norm(s[2])]
    if len(sentences) < 2:
        return _verb_fail("no_passage", {"sentences": len(sentences)})
    anchor = None
    for span in reversed(sentences):
        if any(cue in span[2] for cue in CONCLUSION_CUES):
            anchor = span
            break
    if anchor is None:
        anchor = sentences[-1]
    cb = content_bigrams(anchor[2])
    stem_bg = content_bigrams(passage)
    scored: List[Tuple[str, float, float, VerbalOptionAnalysis]] = []
    for label, text in options:
        ob = content_bigrams(text)
        hit = cb & ob
        cov = len(hit) / len(cb) if cb else 0.0
        novel = len(ob - stem_bg) / len(ob) if ob else 1.0
        ana = VerbalOptionAnalysis(label=label, option_text=text, definitive=True,
                                   evidence=[_ev(label, anchor, "conclusion_anchor")],
                                   new_terms=sorted(ob - stem_bg)[:8])
        if novel > 0.45:
            ana.relation = "overreach"
            ana.notes = "选项引入大量原文之外的内容词（过度引申）"
        elif cov >= 0.3 and len(hit) >= 2:
            ana.relation = "covers"
            ana.notes = f"结论句内容覆盖率 {cov:.2f}（命中 {sorted(hit)[:4]}）"
        else:
            ana.relation = "misses"
            ana.notes = f"结论句内容覆盖率 {cov:.2f}（过低）"
        scored.append((label, cov, novel, ana))
    ranked = sorted([s for s in scored if s[3].relation == "covers"], key=lambda x: -x[1])
    plan = VerbalReasoningPlan(task_type="main_idea", question_target=question,
                               conclusion=anchor[2],
                               passage_spans=[_ev("", anchor, "conclusion_anchor")],
                               premises=[s[2] for s in sentences if s is not anchor][:6],
                               option_analyses=[s[3] for s in scored])
    plan.unsupported_claims = sorted({t for s in scored for t in s[3].new_terms})
    if len(ranked) == 1:
        second = max([s[1] for s in scored if s[3].relation != "covers"] + [0.0])
        if ranked[0][1] - second < 0.12:
            plan.uncertainty_reasons.append("与其他选项覆盖率差距过小")
            return _verb_fail("near_tie", {"top": ranked[0][0], "gap": ranked[0][1] - second})
        plan.proposed_answer = ranked[0][0]
        plan.confidence = 0.65
        plan.assumptions = ["规则近似：结论句线索+内容覆盖；不能证明完整主旨正确性"]
        return {"ok": True, "plan": plan}
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.12:
        plan.uncertainty_reasons.append(f"多选项覆盖结论且差距不显著: {[r[0] for r in ranked]}")
        return _verb_fail("near_tie", {"candidates": [r[0] for r in ranked]})
    plan.uncertainty_reasons.append("没有选项可靠覆盖结论句")
    return _verb_fail("no_covering_option", {"coverage": {s[0]: round(s[1], 2) for s in scored}})


# ------------------------------------------------------------------ 选词填空核（搭配表子集）

COLLOCATIONS: Dict[str, Set[str]] = {
    "发挥": {"作用", "优势", "潜力", "水平", "效能", "价值"},
    "发扬": {"精神", "传统", "作风", "民主", "美德"},
    "履行": {"职责", "义务", "承诺", "公约", "手续", "职能"},
    "执行": {"任务", "命令", "计划", "判决", "方案", "操作"},
    "遵守": {"纪律", "规则", "法规", "约定", "承诺"},
    "恪守": {"职责", "承诺", "底线", "原则", "本分"},
    "践行": {"诺言", "理念", "宗旨", "价值观", "承诺"},
    "承载": {"梦想", "希望", "功能", "容量", "记忆", "使命"},
    "承担": {"责任", "义务", "风险", "成本", "后果"},
    "承接": {"业务", "订单", "产业", "转移", "外包"},
    "汇集": {"力量", "信息", "人才", "资金", "于一处", "汇聚"},
    "汇聚": {"力量", "人才", "能量", "资源", "合力"},
    "凝聚": {"共识", "力量", "人心", "心血", "合力"},
    "积淀": {"文化", "底蕴", "历史", "经验", "学术"},
    "涵养": {"气质", "品德", "水源", "生态", "心性"},
    "激发": {"活力", "兴趣", "潜能", "热情", "动力", "创新"},
    "启迪": {"智慧", "思想", "心智", "后人"},
    "砥砺": {"意志", "品质", "品格", "奋进"},
    "磨炼": {"意志", "心智", "技能", "品格"},
    "锤炼": {"意志", "本领", "作风", "队伍"},
    "增强": {"意识", "体质", "信心", "互信", "黏性", "效果"},
    "增进": {"了解", "友谊", "感情", "共识", "福祉", "互信"},
    "改善": {"生活", "条件", "环境", "关系", "服务", "民生", "体验"},
    "改进": {"方法", "技术", "工作", "工艺", "流程", "方式"},
    "提高": {"水平", "效率", "能力", "质量", "素养", "意识", "速度"},
    "提升": {"品质", "层级", "形象", "效能", "能级", "空间", "价值"},
    "优化": {"结构", "流程", "布局", "环境", "供给", "配置", "体验"},
    "重塑": {"格局", "形象", "流程", "优势", "认知"},
    "契合": {"时代", "需求", "实际", "主旨", "规律", "身份"},
    "吻合": {"实际", "事实", "特征", "标准", "预期"},
    "符合": {"规定", "要求", "标准", "国情", "实际", "潮流", "条件"},
    "适应": {"环境", "需要", "变化", "市场", "时代", "发展"},
    "关注": {"焦点", "问题", "动向", "健康", "发展", "民生", "趋势"},
    "重视": {"人才", "教育", "安全", "基础", "领域", "细节"},
    "致敬": {"先辈", "英雄", "时代", "对手", "坚守者"},
    "摒弃": {"陋习", "偏见", "杂念", "做法", "惰性"},
    "抛弃": {"幻想", "包袱", "成见", "杂念"},
    "杜绝": {"现象", "隐患", "漏洞", "浪费", "腐败"},
    "遏制": {"势头", "增长", "蔓延", "冲动", "上升"},
    "抑制": {"冲动", "增长", "情绪", "消费", "膨胀"},
    "控制": {"局面", "成本", "规模", "节奏", "情绪", "速度"},
    "反应": {"发言", "讲话", "与会", "会场", "气氛", "热烈", "市场", "观众", "公众", "快速", "强烈", "一致"},
    "反映": {"情况", "问题", "群众", "基层", "向上级", "现实", "诉求", "呼声"},
    "批示": {"上级", "领导", "文件", "报告"},
    "注视": {"目光", "凝视", "远远", "深情"},
    "艰巨": {"任务", "使命", "工程", "岁月", "目标"},
    "艰苦": {"奋斗", "条件", "环境", "岁月", "生活", "朴素"},
    "艰难": {"抉择", "旅途", "时刻", "推进", "转型", "跋涉"},
    "坚固": {"防线", "堡垒", "堤坝", "结构", "城墙"},
    "牢固": {"基础", "观念", "印象", "记忆", "根基"},
    "扎实": {"功底", "基础", "作风", "推进", "开展"},
    "深厚": {"功底", "情谊", "积淀", "底蕴", "基础", "感情"},
    "精湛": {"技艺", "技术", "工艺", "医术", "表演"},
    "精深": {"学问", "理论", "研究", "造诣", "博大"},
    "高明": {"医术", "手段", "见解", "做法", "招数"},
    "明智": {"选择", "决定", "举措", "做法"},
    "正确": {"选择", "认识", "方向", "道路", "方法", "判断", "处理"},
    "准确": {"判断", "描述", "定位", "识别", "再现", "传达"},
    "精确": {"计算", "测量", "数据", "制导", "匹配"},
    "确切": {"消息", "信息", "含义", "回答", "答案"},
    "切实": {"落实", "可行", "措施", "有效", "执行", "解决"},
}


def core_cloze(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    question, passage_end = _question_tail(stem)
    passage = stem[:passage_end] or stem
    blanks = list(re.finditer(r"（\s*）|\(\s*\)|_{2,}|＿{2,}|□", passage))
    if not blanks:
        return _verb_fail("no_blank")
    if len(blanks) > 1:
        return _verb_fail("multi_blank", {"n": len(blanks)})
    blank_at = blanks[0].start()
    sentences = split_sentences(passage)
    target = next((s for s in sentences if s[0] <= blank_at < s[1]), sentences[-1] if sentences else None)
    if target is None:
        return _verb_fail("no_passage")
    ctx = norm(passage)
    results: List[Tuple[str, List[Tuple[str, str]], VerbalOptionAnalysis]] = []
    for label, text in options:
        words = [w for w in re.split(r"[、,，;；/]|\s+", norm(text)) if w]
        hits: List[Tuple[str, str]] = []
        miss: List[str] = []
        unanalyzed: List[str] = []
        for w in words:
            table = COLLOCATIONS.get(w)
            if not table:
                unanalyzed.append(w)
                continue
            hit = None
            for t in sorted(table, key=len, reverse=True):
                if t in ctx:
                    hit = t
                    break
            if hit:
                hits.append((w, hit))
            else:
                miss.append(w)
        ana = VerbalOptionAnalysis(label=label, option_text=text)
        if unanalyzed:
            ana.relation = "insufficient"
            ana.definitive = False
            ana.notes = "搭配表未收录: " + "、".join(unanalyzed)
        elif not miss:
            ana.relation = "collocation_hit"
            ana.definitive = True
            ana.evidence = [_ev(label, target, "support")]
            ana.notes = "搭配命中: " + "；".join(f"{w}—{t}" for w, t in hits)
        else:
            ana.relation = "collocation_conflict"
            ana.definitive = True
            ana.evidence = [_ev(label, target, "against")]
            ana.notes = "无搭配依据: " + "、".join(miss)
        results.append((label, hits, ana))
    winners = [r for r in results if r[2].relation == "collocation_hit"]
    others_clean = all(r[2].relation != "insufficient" for r in results if r not in winners)
    plan = VerbalReasoningPlan(task_type="cloze", question_target=question,
                               passage_spans=[_ev("", target, "support")],
                               option_analyses=[r[2] for r in results])
    if len(winners) == 1 and others_clean:
        plan.proposed_answer = winners[0][0]
        plan.confidence = 0.75
        plan.assumptions = ["规则子集：固定搭配表命中；一词多义/语境微妙差异不在覆盖范围"]
        return {"ok": True, "plan": plan}
    plan.uncertainty_reasons.append(
        f"命中数 {len(winners)}；其余选项存在规则不可判定项: {not others_clean}")
    return _verb_fail("ambiguous_collocation" if winners else "no_collocation_hit",
                      {"winners": [w[0] for w in winners]})


# ------------------------------------------------------------------ 论证加强/削弱核（命题链子集）

_IMPL = re.compile(r"(?:如果|假如|倘若|要是|只要|若|一旦)(.{1,30}?)[,，]?(?:就|则|那么|便)(.{1,30}?)(?=[。；！？]|$)")
_ONLY = re.compile(r"只有(.{1,30}?)才(.{1,30}?)(?=[。；！？]|$)")
_EXCEPT = re.compile(r"除非(.{1,30}?)否则不(.{1,30}?)(?=[。；！？]|$)")
_EXCEPT2 = re.compile(r"除非(.{1,30}?)否则(.{1,30}?)(?=[。；！？]|$)")
_ALLS = re.compile(r"(?:所有|任何|每一)(.{1,20}?)(?:都|均)(不|没)?(.{1,20}?)(?=[。；！？]|$)")
_CONC = re.compile(r"(?:因此|所以|可见|由此|从而|据此)(.{4,60}?)(?=[。；！？]|$)")
_ATOM_STRIP = re.compile(r"^[，。；：、的]+|[，。；：、的了]+$")


def _atom(text: str) -> str:
    s = _ATOM_STRIP.sub("", norm(text))
    s = re.sub(r"^[能会可就应该]", "", s)   # 谓词助动词前缀不参与原子身份
    s = re.sub(r"[的了会就]", "", s)        # 子集纪律：动态助词/助动词不区分命题（已记入限制）
    s = re.sub(r"[的了]$", "", s)
    return s


def _clauses(text: str) -> List[Tuple[Any, ...]]:
    """从句子抽取可形式化命题：('imp', A, B) | ('lit', A) | ('nlit', A)。"""
    out: List[Tuple[Any, ...]] = []
    for m in _IMPL.finditer(text):
        a, b = _atom(m.group(1)), _atom(m.group(2))
        if a and b:
            out.append(("imp", a, b, m.span()))
    for m in _ONLY.finditer(text):
        a, b = _atom(m.group(1)), _atom(m.group(2))
        if a and b:
            out.append(("imp", b, a, m.span()))
    for m in _EXCEPT.finditer(text):
        a, b = _atom(m.group(1)), _atom(m.group(2))
        if a and b:
            out.append(("imp", b, a, m.span()))
    for m in _ALLS.finditer(text):
        a, neg, b = _atom(m.group(1)), m.group(2), _atom(m.group(3))
        if a and b:
            out.append(("imp", a, ("not" + b) if neg else b, m.span()))
    return out


def _z3_env():
    import z3

    env: Dict[str, Any] = {"z3": z3, "vars": {}}

    def atom_expr(name: str):
        if name.startswith("not"):
            return z3.Not(atom_expr(name[3:]))
        if name not in env["vars"]:
            env["vars"][name] = z3.Bool(f"a{len(env['vars'])}")
        return env["vars"][name]
    env["atom"] = atom_expr
    return env


def _entails(F: List[Any], e: Any, neg: bool = False) -> bool:
    env = _z3_env()
    z3 = env["z3"]
    exprs: List[Any] = []
    def to_expr(cl: Tuple[Any, ...]):
        if cl[0] == "imp":
            return z3.Implies(env["atom"](cl[1]), env["atom"](cl[2]))
        if cl[0] == "lit":
            return env["atom"](cl[1])
        return z3.Not(env["atom"](cl[1]))
    conj = [c for c in F if isinstance(c, tuple)]
    plain = [c for c in F if not isinstance(c, tuple)]
    for cl in conj:
        exprs.append(to_expr(cl))
    exprs.extend(plain)
    target = e if isinstance(e, tuple) else e
    if isinstance(target, tuple):
        target_e = to_expr(target)
    else:
        target_e = target
    s = z3.Solver()
    s.add(exprs)
    s.add(z3.Not(target_e) if not neg else target_e)
    return s.check() == z3.unsat


def _consistent(F: List[Any]) -> bool:
    env = _z3_env()
    z3 = env["z3"]
    s = z3.Solver()
    conj = [c for c in F if isinstance(c, tuple)]
    plain = [c for c in F if not isinstance(c, tuple)]
    def to_expr(cl):
        if cl[0] == "imp":
            return z3.Implies(env["atom"](cl[1]), env["atom"](cl[2]))
        if cl[0] == "lit":
            return env["atom"](cl[1])
        return z3.Not(env["atom"](cl[1]))
    for cl in conj:
        s.add(to_expr(cl))
    s.add(plain)
    return s.check() != z3.unsat


def _atom_vocab(F: List[Tuple[Any, ...]], C: Tuple[Any, ...]) -> List[str]:
    atoms: List[str] = []
    for c in F:
        atoms += [c[1], c[2]] if c[0] == "imp" else [c[1]]
    atoms += [C[1], C[2]] if C[0] == "imp" else [C[1]]
    return [a for a in dict.fromkeys(atoms) if len(a) >= 2]


def _map_atom(name: str, vocab: List[str]) -> str:
    """把新短语映射到已有命题词表（子集：同词/包含关系视为同一原子）。"""
    best = ""
    for v in vocab:
        if v in name or name in v:
            if len(v) > len(best):
                best = v
    return best or name


def core_argument(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    question, passage_end = _question_tail(stem)
    passage = stem[:passage_end] or stem
    negative = bool(re.search(r"削弱|质疑|反驳|不能支持|除外", question or stem))
    assumption = bool(re.search(r"假设|前提", question or stem))
    sentences = split_sentences(passage)
    # 结论句：第一个含"因此/所以/可见…"的句子；其余句提供前提
    conclusion: Optional[Tuple[Any, ...]] = None
    conc_span = None
    rest: List[Tuple[int, int, str]] = []
    for span in sentences:
        if conclusion is None:
            cm = _CONC.search(span[2])
            if cm:
                inner = _clauses(cm.group(1))
                a = _atom(cm.group(1))
                if inner:
                    conclusion, conc_span = inner[0][:3], span
                    continue
                if a:
                    mn = re.search(r"(不是|没有|不|没|未|非)", a)
                    if mn:
                        core = _atom(a[:mn.start()] + a[mn.end():])
                        conclusion = ("nlit", core) if core else ("lit", a)
                    else:
                        conclusion = ("lit", a)
                    conc_span = span
                    continue
        rest.append(span)
    premise_clauses = [c[:3] for s in rest for c in _clauses(s[2])]
    premise_spans = [s for s in rest if _clauses(s[2])]
    if conclusion is None:
        return _verb_fail("no_conclusion", {"clauses": len(premise_clauses)})
    F = premise_clauses
    C = conclusion

    def _atoms_of(clauses):
        out: List[str] = []
        for c in clauses:
            out += [c[1], c[2]] if c[0] == "imp" else [c[1]]
        return [a for a in out if len(a) >= 2]

    def _map(name: str, vocab: List[str]) -> str:
        best = ""
        for v in vocab:
            if v and (v in name or name in v) and len(v) > len(best):
                best = v
        return best or name

    vf = _atoms_of(F)
    C = ((C[0], _map(C[1], vf)) if C[0] in ("lit", "nlit")
         else ("imp", _map(C[1], vf), _map(C[2], vf)))
    v_all = vf + ([C[1]] if C[0] in ("lit", "nlit") else [C[1], C[2]])

    def map_atom(name: str) -> str:
        return name if name in v_all else _map(name, v_all)

    # 陈述句事实前提：仅当句子字面命中某条条件链的**前件**时才入 F（防结论自证）
    antecedents = [c[1] for c in F if c[0] == "imp"]
    for sp in rest:
        if _clauses(sp[2]):
            continue
        a0 = _atom(sp[2])
        if not a0 or len(a0) < 3:
            continue
        hit = next((v for v in antecedents if v and (v in a0 or a0 in v)), None)
        if hit:
            F = F + [("lit", hit)]
    if not _consistent(F):
        return _verb_fail("inconsistent_premises")
    already = _entails(F, C)
    analyses: List[VerbalOptionAnalysis] = []
    outcomes: List[Tuple[str, str]] = []
    c_need = [C[1]] if C[0] in ("lit", "nlit") else [C[1], C[2]]
    for label, text in options:
        o_cl = [("imp", map_atom(c[1]), map_atom(c[2])) if c[0] == "imp" else
                (c[0], map_atom(c[1])) for c in _clauses(text)]
        o_cl = list(o_cl)
        ana = VerbalOptionAnalysis(label=label, option_text=text, definitive=True)
        ana.evidence = [VerbalEvidence(option=label, start=-1, end=-1, text=text, role="against")]
        for sp in premise_spans:
            if all(n in norm(sp[2]) for n in c_need):
                ana.evidence.append(_ev(label, sp, "support"))
                break
        if not o_cl:
            a0 = _atom(text)
            m = re.search(r"(不是|没有|不|没|未|非)", a0) if a0 else None
            if m:
                core = _atom(a0[:m.start()] + a0[m.end():])
                if core:
                    o_cl = [("nlit", map_atom(core))]
            elif a0:
                o_cl = [("lit", map_atom(a0))]
        if not o_cl:
            ana.relation = "not_formalizable"
            ana.definitive = False
            analyses.append(ana)
            outcomes.append((label, "not_formalizable"))
            continue
        O = o_cl[0]
        rel = "neutral"
        direct_rebut = (
            (O[0] == "nlit" and C[0] == "lit" and O[1] == C[1])
            or (O[0] == "nlit" and C[0] == "imp" and O[1] == C[2])
            or (O[0] == "lit" and C[0] == "nlit" and O[1] == C[1])
        )
        try:
            if negative and not assumption:
                if direct_rebut or not _consistent(F + [O]) or _entails(F + [O], C, neg=True):
                    rel = "weakens"
            elif assumption:
                if already:
                    nO = (("nlit", O[1]) if O[0] == "lit" else
                          ("lit", O[1]) if O[0] == "nlit" else None)
                    if nO is not None and not _entails(F + [nO], C):
                        rel = "assumption_candidate"
                elif O[0] != "imp" and _entails(F + [O], C):
                    # 缺口型论证：必要前提=补上后论证成立的选项
                    rel = "assumption_candidate"
            if rel == "neutral" and not negative and not assumption:
                if not already and _entails(F + [O], C):
                    rel = "strengthens"
        except Exception:
            rel = "not_formalizable"
            ana.definitive = False
        ana.relation = rel
        ana.notes = ("前提已蕴含结论" if already else "前提不蕴含结论（存在论证缺口）") + f"；选项判定 {rel}"
        analyses.append(ana)
        outcomes.append((label, rel))
    want = "assumption_candidate" if assumption else ("weakens" if negative else "strengthens")
    winners = [l for l, r in outcomes if r == want]
    gap_needed = (not already) if (not negative or assumption) else True
    plan = VerbalReasoningPlan(task_type="argument", question_target=question,
                               negative_target=negative,
                               premises=[f"{c[1]}→{c[2]}" for c in F if c[0] == "imp"],
                               conclusion=("非" + C[1] if C[0] == "nlit" else
                                           C[1] if C[0] == "lit" else f"{C[1]}→{C[2]}"),
                               passage_spans=([_ev("", conc_span, "conclusion_anchor")]
                                               if conc_span else [])
                               + [_ev("", sp, "support") for sp in premise_spans],
                               option_analyses=analyses)
    plan.assumptions = ["规则子集：字面条件链（如果/只有/除非/所有）+ Z3 命题蕴含检验；"
                        "隐含前提与语义近似不在覆盖范围"]
    if len(winners) == 1 and gap_needed:
        plan.proposed_answer = winners[0]
        plan.confidence = 0.8
        return {"ok": True, "plan": plan}
    plan.uncertainty_reasons.append(f"唯一性不成立（winners={winners}, 缺口={'无' if already else '有'}）")
    return _verb_fail("argument_unresolved", {"outcomes": dict(outcomes), "entailed": already})


# ------------------------------------------------------------------ 主入口 & 独立审计

CORES = {"inference": core_inference, "main_idea": core_main_idea,
         "cloze": core_cloze, "argument": core_argument}


def build_verbal_plan(stem: str, options: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    task = detect_task(stem)
    if task is None:
        return _verb_fail("unrecognized_task", {"question": stem[:80]})
    return CORES[task](stem, options)


def spans_valid(plan: Dict[str, Any] | VerbalReasoningPlan, stem: str) -> List[str]:
    """所有证据引用必须是原文有效字符区间：逐字核对，返回问题列表（空=通过）。"""
    p = plan if isinstance(plan, VerbalReasoningPlan) else plan.get("plan")
    problems: List[str] = []
    if p is None:
        return ["plan_missing"]
    for ev in [e for a in p.option_analyses for e in a.evidence] + list(p.passage_spans):
        if ev.start < 0 or ev.end <= ev.start or ev.end > len(stem):
            if ev.role != "against" or ev.start != -1:
                problems.append(f"越界引用 {ev.option}:{ev.start}-{ev.end}")
            continue
        if stem[ev.start:ev.end] != ev.text:
            problems.append(f"引用与原文不一致 {ev.option}:{ev.text[:20]!r}")
    return problems


def audit(stem: str, options: Sequence[Tuple[str, str]], answer: str, task_type: str,
          claim_relation: str = "", span_texts: Sequence[str] = ()) -> Dict[str, Any]:
    """独立于求解器的最小重算（不同实现：包含度度量 + 独立否定奇偶扫描 + 强度词表核对）。
    返回 status ∈ {confirmed, refuted, insufficient, self_consistent}；
    语义任务不存在真正独立的算术式反证——cloze/argument 只能自洽复核，
    这一点在验证层 notes 与文档里如实声明，绝不冒充"独立证明"。"""
    opts = dict(options)
    if answer not in opts:
        return {"status": "refuted", "reason": "answer_label_unknown"}

    def grams(s: str) -> Set[str]:
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def neg_scan(s: str) -> int:
        """与 extract_cues 同语义、不同实现：先折叠双否模式再数单否。"""
        s = norm(s)
        n = 0
        for d in NEG_DOUBLE:
            if d == "非…不":
                continue
            c = s.count(d)
            if c:
                n += 2 * c
                s = s.replace(d, "◎")
        for ch in ("不是", "没有", "没", "不", "未", "非", "无", "勿", "莫", "别"):
            c = s.count(ch)
            if c:
                n += c
                s = s.replace(ch, "○")
        return n % 2

    t = norm(stem)
    o = norm(opts[answer])
    o_g = grams(o)
    if not o_g:
        return {"status": "insufficient", "reason": "empty_option"}
    whole = len(o_g & grams(t)) / len(o_g)
    if task_type == "inference":
        if claim_relation == "supported":
            par_ok = all(neg_scan(norm(sp)) == neg_scan(o) for sp in span_texts if sp)
            if whole >= 0.5 and par_ok:
                return {"status": "confirmed", "metric": round(whole, 3)}
            return {"status": "refuted" if whole < 0.3 else "insufficient",
                    "metric": round(whole, 3), "reason": "包含度/否定极性复核"}
        if claim_relation in {"contradicted", "overreach"}:
            strength = ("一定", "必然", "所有", "都", "任何", "首次", "唯一", "必须")
            for sp in span_texts:
                spn = norm(sp)
                if neg_scan(spn) != neg_scan(o):
                    return {"status": "confirmed", "reason": "独立否定扫描极性不一致"}
                if any(w in o and w not in spn for w in strength):
                    return {"status": "confirmed", "reason": "强度词无原文依据"}
            if not span_texts and whole < 0.25:
                return {"status": "confirmed", "reason": "全题包含度过低"}
            return {"status": "insufficient", "metric": round(whole, 3)}
        if claim_relation == "absent":
            return {"status": "confirmed" if whole < 0.25 else "refuted", "metric": round(whole, 3)}
        return {"status": "insufficient", "reason": f"claim relation {claim_relation!r} 不可审计"}
    if task_type == "main_idea":
        anchor = "".join(norm(sp) for sp in span_texts)
        common = len(o_g & grams(anchor))
        return {"status": "confirmed" if common >= 2 else "insufficient",
                "metric": round(whole, 3)}
    if task_type == "cloze":
        return {"status": "self_consistent", "reason": "搭配表复核与求解共用同一词表（非独立实现）"}
    if task_type == "argument":
        return {"status": "self_consistent", "reason": "Z3 蕴含重放：同一命题抽取实现（非独立语义证明）"}
    return {"status": "insufficient", "reason": f"unknown task {task_type}"}
