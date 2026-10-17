"""截图输入：视觉模型 -> 结构化题目字段（题干 / 选项 / 题型提示）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import ChatModel, LLMError
from .prompts import VISION_EXTRACT_SYSTEM, vision_user_prompt
from .schemas import ExtractionResult, QuestionOption

_VALID_TYPES = {"single_choice", "multi_choice", "fill_blank", "free_response"}


class VisionExtractionError(LLMError):
    """截图抽取失败。"""


def _normalize_options(raw: Any) -> List[QuestionOption]:
    options: List[QuestionOption] = []
    if not isinstance(raw, list):
        return options
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            label = str(item.get("label") or chr(ord("A") + index)).strip().upper()
            text = str(item.get("text") or "").strip()
        else:
            label = chr(ord("A") + index)
            text = str(item).strip()
        label = label.rstrip(".、．)）")
        if label:
            options.append(QuestionOption(label=label, text=text))
    return options


class VisionExtractor:
    """把题目截图转成 ``ExtractionResult``（只抄写可见内容，不做推理）。"""

    def __init__(self, client: ChatModel, model: Optional[str] = None):
        self.client = client
        self.model = model

    def extract(self, image_path: str | Path, hint: str = "") -> ExtractionResult:
        path = Path(image_path)
        if not path.is_file():
            raise VisionExtractionError(f"截图不存在: {path}")
        try:
            payload = self.client.complete_json(
                VISION_EXTRACT_SYSTEM,
                vision_user_prompt(hint),
                image_path=str(path),
                model=self.model,
            )
        except LLMError as exc:
            raise VisionExtractionError(f"截图抽取失败: {exc}") from exc
        return self.from_payload(payload, model=self.model)

    @staticmethod
    def from_payload(payload: Dict[str, Any], model: Optional[str] = None) -> ExtractionResult:
        stem = str(payload.get("stem") or "").strip()
        options = _normalize_options(payload.get("options"))
        question_type = str(payload.get("question_type") or "single_choice").strip()
        if question_type not in _VALID_TYPES:
            question_type = "single_choice"
        confidence = payload.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        confidence_value = max(0.0, min(1.0, confidence_value))
        warnings: List[str] = []
        if not stem:
            warnings.append("未能抽取到题干文本")
        if confidence_value < 0.6:
            warnings.append(f"抽取置信度偏低（{confidence_value:.2f}），建议人工核对原始截图")
        skill_hint = payload.get("skill_hint")
        if isinstance(skill_hint, str) and skill_hint.strip().lower() in {"null", "none", ""}:
            skill_hint = None
        return ExtractionResult(
            stem=stem,
            options=options,
            question_type=question_type,
            skill_hint=(str(skill_hint).strip() if skill_hint else None),
            has_diagram=bool(payload.get("has_diagram", False)),
            raw_text=str(payload.get("raw_text") or ""),
            model=model,
            confidence=confidence_value,
            warnings=warnings,
        )
