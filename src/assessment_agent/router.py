"""题型路由：规则优先，LLM 兜底，结果始终结构化可审计。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .config import Settings
from .llm import ChatModel, LLMError
from .prompts import ROUTER_SYSTEM, router_user_prompt
from .schemas import Question, RouteDecision
from .tools.math_tool import option_value

Rule = Tuple[re.Pattern, float]

SKILL_RULES: Dict[str, Sequence[Rule]] = {
    "figure_reasoning": (
        (re.compile(r"图形|图案|九宫格|折叠|展开图|对称轴|对称性|阴影|旋转|翻转|透视"), 2.0),
        (re.compile(r"下列.{0,6}(图形|图案)|下一个图形|缺失的图形"), 1.0),
    ),
    "data_analysis": (
        (re.compile(r"资料分析|根据(以上|下述|下列)(材料|资料|表格|图表)|表中数据"), 2.5),
        (re.compile(r"同比|环比|增长率|增长量|占比|比重|百分点|条形图|折线图|饼图|柱状图"), 1.5),
        (re.compile(r"平均每年|年均增长|累计"), 0.8),
    ),
    "number_series": (
        (re.compile(r"数列|下一个数|下一项|填入.{0,4}括号|括号内应填|依次排列|规律"), 2.0),
        (re.compile(r"\d+\s*[,，、]\s*\d+\s*[,，、]\s*\d+"), 1.5),
        (re.compile(r"[（(]\s*[)）]"), 0.8),
    ),
    "logical_reasoning": (
        (re.compile(r"一定为真|必然(为真|成立|可以推出)|可以推出|由此可知|据此(可以)?判断"), 2.0),
        (re.compile(r"如果.*那么|只有.*才|除非|否则|充分条件|必要条件"), 1.5),
        (re.compile(r"[甲乙丙丁戊己].{0,4}[甲乙丙丁戊己]|说真话|说假话|撒谎|诚实者|排第"), 2.0),
        (re.compile(r"可能为真|不可能为真|最能(支持|削弱)"), 1.0),
    ),
    "numeric_calculation": (
        (re.compile(r"计算|求值|等于多少|多少(元|米|人|个|件|小时|分钟|千克|吨)|之和|之差|乘积|商"), 2.0),
        (re.compile(r"百分|%|折扣|打[几折]|比例|单价|总价|税率"), 1.5),
        (re.compile(r"\d+\s*[+\-*/×÷]\s*\d+"), 1.8),
    ),
    "verbal_understanding": (
        (re.compile(r"依次填入|填入划横线|划横线部分最恰当|最恰当的一项|成语|词语填空"), 2.5),
        (re.compile(r"主旨|概括|意在说明|作者(想|要)表达|文段|这段话|上述文字"), 2.0),
        (re.compile(r"排序|语序|顺序最恰当"), 1.0),
        # R3b：阅读推断/论证言语化表述（与逻辑判断的"由此推出"式问法区分开）
        (re.compile(r"最能?(?:削弱|质疑|反驳|支持|加强)|(?:以下|下列)哪项.{0,12}(?:必要)?前提|基于以下哪项假设|下列说法(?:正确|错误|不正确)"), 2.5),
        (re.compile(r"(?:这段|上述|以下|短材料)(?:文字|材料)[^？]{0,40}(?:可以|无法|不能)?推(?:出|断)|意在强调?"), 2.2),
    ),
    "language_reasoning": (
        (re.compile(r"类比|关系最为相似|最为相似|最为相近|最为接近|反义|近义|同义|词语关系"), 2.5),
        (re.compile(r"\b(analogy|verbal reasoning|synonym|antonym)\b", re.IGNORECASE), 2.5),
        (re.compile(r"[A-Za-z]{3,}\s*[:：]\s*[A-Za-z]{3,}\s*(?:as|::)\s*", re.IGNORECASE), 2.0),
    ),
    "personality": (
        (re.compile(r"性格|人格|职业倾向|量表|测评结果|维度得分|行为风格|MBTI|大五"), 2.5),
    ),
}

RULE_ORDER: List[str] = [
    "figure_reasoning",
    "data_analysis",
    "number_series",
    "logical_reasoning",
    "numeric_calculation",
    "verbal_understanding",
    "language_reasoning",
    "personality",
]


def score_rules(question: Question) -> Dict[str, float]:
    text = question.render()
    scores: Dict[str, float] = {}
    for skill_id, rules in SKILL_RULES.items():
        total = 0.0
        for pattern, weight in rules:
            if pattern.search(text):
                total += weight
        if total > 0:
            scores[skill_id] = round(total, 3)
    if question.image_path and "figure_reasoning" in scores:
        scores["figure_reasoning"] += 0.5
    return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))


class Router:
    def __init__(self, settings: Settings, llm: Optional[ChatModel] = None, registry=None):
        self.settings = settings
        self.llm = llm
        self.registry = registry

    # ------------------------------------------------------------------ 入口
    def route(self, question: Question, explicit_skill: Optional[str] = None, hint: Optional[str] = None) -> RouteDecision:
        requested = explicit_skill or question.skill or hint
        if requested:
            return RouteDecision(
                skill=requested,
                confidence=1.0,
                source="explicit",
                scores={requested: 1.0},
                notes="题型由调用方显式指定",
            )
        scores = score_rules(question)
        # R2c：结构证据优先于关键词——图形题的"按规律/问号处"话术会与数列规则撞车，
        # 但"2 个以上选项区域含图形 + 题干有矩阵裁剪"是更强的客观证据。
        if getattr(self.settings, "r2c_router_structure", True):
            structural = self._structure_route(question, scores)
            if structural is not None:
                return structural
        if scores:
            ranked = list(scores.items())
            top_skill, top_score = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            ambiguous = second_score >= top_score * 0.85 and top_score > 0
            confidence = min(0.95, 0.55 + 0.1 * top_score)
            if ambiguous:
                # 不确定分类不允许伪装成高置信度
                confidence = min(confidence, 0.6)
            if top_score >= self.settings.router_rule_threshold:
                return RouteDecision(
                    skill=top_skill,
                    confidence=round(confidence, 3),
                    source="rules",
                    scores=scores,
                    ambiguous=ambiguous,
                    notes=f"规则得分 {scores}" + ("（多规则冲突，置信度封顶 0.6）" if ambiguous else ""),
                )
        return self._llm_route(question, scores)

    # ------------------------------------------------------------ 结构证据路由
    def _structure_route(self, question: Question, scores: Dict[str, float]) -> Optional[RouteDecision]:
        vision = (question.meta or {}).get("vision") if question.meta else None
        if not isinstance(vision, dict):
            vision = {}
        option_regions = [r for r in (vision.get("option_regions") or []) if isinstance(r, dict)]
        figure_options = [r for r in option_regions if r.get("has_figure") or r.get("crop_path")]
        stem_matrix = bool(vision.get("stem_matrix_crop") or vision.get("stem_matrix_grid"))
        stem_diagram = bool((question.meta or {}).get("has_diagram"))
        if len(figure_options) >= 2 and (stem_matrix or stem_diagram):
            competing = sorted(k for k, v in scores.items() if k != "figure_reasoning" and v >= 2.0)
            ambiguous = bool(competing)
            return RouteDecision(
                skill="figure_reasoning",
                confidence=0.6 if ambiguous else 0.85,
                source="structure",
                scores=scores,
                ambiguous=ambiguous,
                notes=(
                    f"结构证据：{len(figure_options)} 个选项区域含独立图形裁剪"
                    + ("，题干有矩阵/图形区域" if stem_matrix or stem_diagram else "")
                    + (f"；与文本线索 {competing} 冲突，置信度封顶 0.6（不伪装高置信）" if ambiguous else "")
                ),
            )
        tables = [t for t in (vision.get("table_regions") or []) if isinstance(t, dict)]
        table = vision.get("table")
        if isinstance(table, dict) and table.get("cells"):
            tables = tables + [table]
        charts = []
        if isinstance(vision.get("chart"), dict):
            charts.append(vision["chart"])
        for region in vision.get("text_regions") or []:
            if isinstance(region, dict) and region.get("id") == "chart":
                charts.append(region)
            elif isinstance(region, dict) and re.search(r"组\s*\d+(?:\.\d+)?\s*[万件元人]?%", str(region.get("text") or "")):
                charts.append(region)
        if tables or charts:
            competing = sorted(k for k, v in scores.items() if k != "data_analysis" and v >= 2.0)
            evidence = f"表格 {len(tables)} 个" if tables else ""
            if charts:
                evidence += ("，" if evidence else "") + f"统计图 {len(charts)} 个"
            return RouteDecision(
                skill="data_analysis",
                confidence=0.6 if competing else 0.85,
                source="structure",
                scores=scores,
                ambiguous=bool(competing),
                notes=f"结构证据：检出 {evidence} 数据区域，路由到资料分析"
                + (f"；文本线索 {competing} 冲突，置信度封顶 0.6" if competing else ""),
            )
        # 反例守卫：空括号（ ）像数列占位符，但选项若是**词语**（≥2 个汉字、非数值），
        # 那是语言填空/阅读理解信号——不能仅凭括号判成 number_series（结构优先于弱关键词）。
        # 只在关键词规则未能明确定论时兜底，不压过明确的"数列 2,4,8"/"下列图形"题干信号。
        if re.search(r"[（(]\s*[)）]", question.render()) and figure_options == []:
            if max(scores.values(), default=0.0) >= self.settings.router_rule_threshold:
                return None
            texts = [str((r.get("text") or "").strip()) for r in option_regions if isinstance(r, dict)]
            if not texts:
                texts = [option.text.strip() for option in question.options]
            def _wordy(text: str) -> bool:
                return (
                    bool(re.search(r"[\u4e00-\u9fff]{2,}", text))
                    and option_value(text) is None
                    and not re.search(r"\d", text)
                )
            if texts and sum(1 for t in texts if _wordy(t)) >= 2:
                return RouteDecision(
                    skill="verbal_understanding",
                    confidence=0.6,
                    source="structure",
                    scores=scores,
                    ambiguous=True,
                    notes="空括号疑似数列占位，但选项为词语而非数值：路由言语理解，置信度 0.6（不伪装高置信）",
                )
        return None

    # ------------------------------------------------------------ LLM 兜底
    def _llm_route(self, question: Question, scores: Dict[str, float]) -> RouteDecision:
        best_guess = next(iter(scores)) if scores else "unknown"
        if self.llm is None or not self.settings.llm_enabled:
            return RouteDecision(
                skill=best_guess,
                confidence=0.35 if scores else 0.0,
                source="fallback",
                scores=scores,
                ambiguous=True,
                # unknown ≠ unsupported：未分类如实置 classified=False
                classified=best_guess != "unknown",
                notes="规则未达阈值且未启用模型分类，请用 --skill 显式指定题型"
                if scores else "无任何规则命中且未启用模型分类：判为 unknown（未分类，而非'不支持'）",
            )
        try:
            payload = self.llm.complete_json(
                ROUTER_SYSTEM,
                router_user_prompt(question.stem, [option.as_line() for option in question.options]),
            )
        except LLMError as exc:
            return RouteDecision(
                skill=best_guess,
                confidence=0.3,
                source="fallback",
                scores=scores,
                ambiguous=True,
                notes=f"模型分类失败: {exc}",
            )
        skill = str(payload.get("skill") or "").strip()
        valid_ids = set(self.registry.ids()) if self.registry is not None else set(SKILL_RULES)
        if skill not in valid_ids:
            return RouteDecision(
                skill=best_guess,
                confidence=0.3,
                source="fallback",
                scores=scores,
                ambiguous=True,
                notes=f"模型返回了未知技能 {skill!r}",
            )
        try:
            confidence = float(payload.get("confidence", 0.6))
        except (TypeError, ValueError):
            confidence = 0.6
        return RouteDecision(
            skill=skill,
            confidence=max(0.0, min(1.0, confidence)),
            source="llm",
            scores=scores,
            ambiguous=confidence < 0.6,
            notes=str(payload.get("reason") or ""),
        )
