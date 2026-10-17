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
