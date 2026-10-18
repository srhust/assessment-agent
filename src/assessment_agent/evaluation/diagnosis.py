"""失败归因（R2b）：把每道题的失败定位到具体环节，不再统一记 pipeline_error。

九个环节类别（按"最上游失效优先"归因，先坏的环节决定类别）：

| 类别 | 含义 |
| --- | --- |
| model_api_error | 视觉模型调用本身失败（网络 / 鉴权 / 未配置） |
| vision_parse_error | 模型返回了但无法结构化解析（JSON 非法 / 题干缺失） |
| extraction_text_error | 题干文本抽取与金标准不一致，且答案错 |
| region_missing_or_shifted | 图形区域缺失/越界/裁剪丢失（含 region_incomplete） |
| option_mapping_error | 选项标号/图形映射错误，且答案错 |
| routing_error | 题型路由错误（可答题被判 unknown 或路由到别的 Skill） |
| unsupported_skill | 可答题落到未实现题型（覆盖缺口） |
| solver_error | 抽取/区域/选项都正确（或不涉及），Solver 无解或错解 |
| verifier_false_reject | Solver 答案与金标准一致，但 Verifier 拒绝（误杀） |
| verifier_false_accept | Verifier 接受了错误答案（误放） |

真实模型未跑通时，extraction_* 类别在 oracle/auto 模式下不适用（无抽取环节）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .runner import Case

FAILURE_CLASSES = (
    "model_api_error",
    "vision_parse_error",
    "extraction_text_error",
    "region_missing_or_shifted",
    "option_mapping_error",
    "routing_error",
    "unsupported_skill",
    "solver_error",
    "verifier_false_reject",
    "verifier_false_accept",
    "pipeline_error",  # 兜底：无法归类的异常也必须显式可见，不允许吞掉
)

# 区域证据失效的错误前缀（Skill 返回的结构化错误）
_REGION_MARKERS = ("region_incomplete", "图形区域证据不完整", "区域适配失败")


def classify_exception(message: str) -> str:
    text = message or ""
    if "视觉解析调用失败" in text or "需要视觉模型" in text or "未配置 LLM_VISION_MODEL" in text:
        return "model_api_error"
    if "视觉结构化解析失败" in text or "截图抽取未得到题干文本" in text or "视觉抽取失败" in text:
        return "vision_parse_error"
    if "截图不存在" in text or "无法解码图像" in text or "缺少图像后端" in text:
        return "model_api_error"
    return "pipeline_error"


def classify_case_failure(
    *,
    case: "Case",
    mode: str,
    correct: bool,
    status: str,
    action: str,
    predicted: Optional[str],
    solution_answer: Optional[str],
    solution_error: Optional[str],
    solution_meta: Optional[dict],
    routed_skill: Optional[str],
    extraction_present: bool = False,
    extraction_stem_ok: Optional[bool] = None,
    option_map_ok: Optional[bool] = None,
) -> Optional[str]:
    """correct=True 时返回 None；否则给出最上游失效环节类别。"""
    if correct:
        return None

    error = solution_error or ""
    meta = solution_meta or {}
    gold = case.answer
    answerable_gold = case.expected_status == "solved" and bool(gold)

    # 1. 区域证据问题（结构化失败，优先于路由/求解归因——它就是设计要拦截的降级）
    if meta.get("reason") == "region_incomplete" or any(marker in error for marker in _REGION_MARKERS):
        return "region_missing_or_shifted"

    # 2. 抽取内容错误（image_only：先文本，再选项映射）
    if mode == "image_only" and extraction_present:
        if extraction_stem_ok is False:
            return "extraction_text_error"
        if option_map_ok is False:
            return "option_mapping_error"
    elif mode == "image_only" and not extraction_present:
        return "vision_parse_error"

    # 3. 路由/覆盖
    if status == "unsupported":
        return "unsupported_skill" if answerable_gold else "routing_error"
    if answerable_gold and routed_skill not in (None, case.skill):
        # 路由到了别的可答 Skill：错的答案多半源于此
        return "routing_error"
    if answerable_gold and routed_skill in (None, "", "unknown"):
        return "routing_error"

    # 4. Verifier 判定错误
    #    不可判定题（gold=None / expected=undecidable）被强行作答：最上游是 Solver
    #    本应弃权却给出了答案，归 solver_error（而非把它当成误放的 verifier 问题）。
    undecidable_gold = case.expected_status == "undecidable" or gold in (None, "")
    if undecidable_gold and predicted is not None and action in {"accept", "repair"}:
        return "solver_error"
    if answerable_gold and solution_answer and str(solution_answer) == str(gold) and action == "reject":
        return "verifier_false_reject"
    if action in {"accept", "repair"} and predicted is not None and str(predicted) != str(gold):
        return "verifier_false_accept"

    # 5. Solver 本身
    if status == "failed":
        return "solver_error"
    return "solver_error"


# ---------------------------------------------------------------------------
# R2c：归因拆分——verification_outcome（验证结果标签）与 root_cause（最初出错位置）
# ---------------------------------------------------------------------------

#: 逐题验证结果（对全部样本计算，不只失败样本）
VERIFICATION_OUTCOMES = (
    "correct_accept",       # 客观有答案：答案通过验证且与金标准一致
    "false_accept",         # 客观有答案：答案通过验证但与金标准不一致（误放）
    "false_reject",         # Solver 曾给出正确答案，却被 Verifier 拒绝（误杀）
    "correct_reject",       # 客观有答案：Solver 给了错答被拦下，Verifier 正确拒绝（拦截有效）
    "no_answer",            # 客观有答案：Solver 本身无解（诚实失败/能力缺口）
    "unverified_answerable",  # 独立复核无法确认（可答题）→ 受控弃权
    "correct_abstention",   # 不可判定/无客观答案：未输出通过校验的答案（诚实弃权）
    "unverified_abstention",  # 不可判定样本被 Solver 作答但复核未确认（仍属诚实处理）
    "over_answer",          # 不可判定/主观样本却输出了通过校验的答案（越权作答）
    "answered_unlabeled",   # 客观可答但数据集无金标注：作答不计分仅记录
    "abstained_unlabeled",  # 同上，未作答
)

#: root_cause：最初失效环节（谁先坏），与 verification_outcome（验证表现）正交
ROOT_CAUSES = (
    "none",                 # 正确作答
    "extraction_ocr",       # 视觉抽取文本错误
    "region_crop",          # 区域裁剪缺失/错位
    "visual_feature",       # 图形感知特征测量错误
    "option_mapping",       # 选项标号/图形映射错误
    "router",               # 题型路由错误
    "capability_gap",       # 题型未实现（有客观答案，安全拒答但仍是能力缺口）
    "word_problem_parse",   # 自然语言数量关系抽取失败
    "solver_coverage",      # 已支持题型但规律不覆盖（无解）
    "solver_rule_selection",  # 弱规律压过强规律 / 规律误选（答案错）
    "solver_arithmetic",    # 算术/数列求解错误
    "data_extraction",      # R3a：表格/统计图数据读取层失败（缺区域/缺单元格/OCR 歧义/单位冲突）
    "solver_data_reference",  # R3a：计算计划无法把题意唯一绑定到数据证据（目标/时间/引用歧义）
    "solver_overreach",     # 本应弃权却强行作答
    "verifier",             # 验证器误杀（false reject）
    "pipeline",             # 框架/接口异常
    "unresolved",           # 无法可靠定位（显式承认可见，不伪装归因）
)

_UNVERIFIED_MARK = "unverified"


def derive_verification_outcome(
    *,
    answerability: str,
    action: str,
    predicted: Optional[str],
    solution_answer: Optional[str],
    gold: Optional[str],
) -> str:
    """逐题验证结果标签（从 CaseResult 字段直接派生，纯函数、可单测）。"""
    answered = predicted is not None and action in {"accept", "repair"}
    if action == _UNVERIFIED_MARK:
        if answerability in {"undecidable", "subjective"}:
            return "unverified_abstention"
        return "unverified_answerable"
    if answered:
        if answerability == "has_unique_answer":
            from .metrics import answers_match

            return "correct_accept" if answers_match(predicted, gold) else "false_accept"
        if answerability == "objective_unlabeled":
            return "answered_unlabeled"
        return "over_answer"
    # 未输出通过校验的答案（reject / failed / unsupported / unverified 已在上分支）
    if answerability in {"undecidable", "subjective"}:
        return "correct_abstention"
    if answerability == "objective_unlabeled":
        return "abstained_unlabeled"
    if gold is not None and solution_answer is not None:
        return "false_reject" if str(solution_answer) == str(gold) else "correct_reject"
    return "no_answer"


def derive_root_cause(
    *,
    outcome: str,
    failure_class: Optional[str],
    capability_status: str = "",
    skill: str = "",
    solution_error: Optional[str] = None,
    solver_rule: Optional[str] = None,
    routed_skill: Optional[str] = None,
) -> str:
    """root_cause：最初失效环节。false_accept ≠ Verifier 的错——先问答案为什么错。"""
    if outcome in {"correct_accept", "correct_abstention", "unverified_abstention", "answered_unlabeled", "abstained_unlabeled"}:
        return "none"
    error = solution_error or ""
    if outcome == "false_reject":
        return "verifier"
    if outcome == "over_answer":
        return "solver_overreach"
    if "资料分析" in error:
        # R3a：资料分析的结构化失败码 → 区分"数据读取层"与"题意-数据引用层"
        m = re.search(r"失败[:：]\s*([a-z_]+)", error)
        code = m.group(1) if m else ""
        if code in {"cell_missing", "digit_ambiguous", "multi_number", "unit_conflict",
                     "cell_unusable", "no_table", "no_data_regions", "chart_values_insufficient",
                     "chart_value_conflict"} or "缺少表格/统计图" in error:
            return "data_extraction"
        if code in {"target_not_found", "ambiguous_reference", "time_pair_incomplete",
                     "operation_unsupported", "plan_build_error"} or "无法唯一匹配" in error:
            return "solver_data_reference"
        if code in {"plan_eval_error", "divide_by_zero"}:
            return "solver_arithmetic"
        return "solver_coverage"
    if outcome == "unverified_answerable":
        # 独立复核确认不了答案：错误在 Solver 的规则选择/感知特征，不在 Verifier
        if skill == "figure_reasoning" or solver_rule:
            return "solver_rule_selection"
        return "solver_coverage"
    if outcome == "no_answer" and capability_status == "unsupported":
        # 客观有答案但当前未支持该题型：安全拒答是合规行为，能力缺口是根因
        return "capability_gap"
    if outcome in {"correct_reject", "false_accept"}:
        # 答案本身错误（无论被拦还是被放）：根因在求解/感知侧，不在 Verifier。
        # 有解即规则选择错误；无解才是覆盖缺口。
        if skill == "figure_reasoning" or routed_skill == "figure_reasoning":
            return "solver_rule_selection" if solver_rule else "solver_coverage"
        if "解析出算术表达式" in error or "未能从题干" in error:
            return "word_problem_parse"
        if solver_rule:
            return "solver_rule_selection"
        return "solver_arithmetic"
    if failure_class == "extraction_text_error":
        return "extraction_ocr"
    if failure_class == "region_missing_or_shifted":
        return "region_crop"
    if failure_class == "option_mapping_error":
        return "option_mapping"
    if failure_class == "model_api_error":
        return "pipeline"
    if failure_class == "vision_parse_error":
        return "pipeline"
    if failure_class == "routing_error":
        return "router"
    if failure_class == "unsupported_skill":
        return "capability_gap" if capability_status != "supported" else "router"
    if failure_class == "verifier_false_accept":
        # 误放的根因在求解侧：图形题几乎总是弱规律（fill/centroid/symmetry）压过强规律
        if skill == "figure_reasoning":
            if solver_rule in {"fill", "centroid", "symmetry"}:
                return "solver_rule_selection"
            return "visual_feature" if solver_rule in {"count", "sides"} else "solver_rule_selection"
        if "解析出算术表达式" in error:
            return "word_problem_parse"
        return "solver_arithmetic"
    if failure_class == "solver_error":
        if "解析出算术表达式" in error or "未能从题干" in error:
            return "word_problem_parse"
        if skill == "figure_reasoning":
            return "solver_coverage" if solver_rule is None else "solver_rule_selection"
        return "solver_coverage"
    if failure_class == "pipeline_error":
        return "pipeline"
    return "unresolved"
