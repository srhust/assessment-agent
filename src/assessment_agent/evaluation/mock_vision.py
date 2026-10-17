"""共享的 mock 视觉后端（仅离线测试/报告使用）。

⚠️ 语义边界：本模块产生的 payload **由金标准区域构造**——它只能验证
VisionParser→Router→Adapter→Verifier 的**链路机制**，不代表任何真实视觉模型的
识别能力。真实模型结果必须由配置了 `LLM_API_KEY`/`LLM_VISION_MODEL` 的
image_only 运行产出；两者在报告里永远分开标注（mock-vlm vs real-vlm）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def payload_from_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """金标准记录（screens/benchmark_real 的 JSONL 行形状）→ v2 视觉模型 payload。"""
    regions = record.get("regions") or {}
    option_regions: List[Dict[str, Any]] = []
    for entry in regions.get("option_regions") or []:
        item: Dict[str, Any] = {
            "label": entry["label"],
            "bbox": entry["bbox"],
            "text": entry.get("text", ""),
            "has_figure": bool(entry.get("has_figure")),
        }
        if entry.get("figure_bbox"):
            item["figure_bbox"] = entry["figure_bbox"]
        option_regions.append(item)

    payload: Dict[str, Any] = {
        "stem_text": record.get("stem", ""),
        "text_regions": record.get("text_regions") or [],
        "option_regions": option_regions,
        "diagram_regions": [],
        "reading_order": regions.get("reading_order") or [],
        "question_type": "single_choice",
        "confidence": 0.9,
    }
    if regions.get("stem_matrix_bbox"):
        diagram: Dict[str, Any] = {
            "id": "d1",
            "role": "stem_matrix",
            "bbox": regions["stem_matrix_bbox"],
        }
        grid = regions.get("stem_matrix_grid")
        if grid:
            diagram["grid"] = {"rows": grid[0], "cols": grid[1]}
        payload["diagram_regions"] = [diagram]
    table = regions.get("table")
    if table and table.get("cells"):
        payload["table_regions"] = [
            {
                "id": "tb1",
                "bbox": table.get("bbox"),
                "cells": table["cells"],
                "rows": len(table["cells"]),
                "cols": len(table["cells"][0]),
            }
        ]
    return payload


class MockVisionClient:
    """按截图文件名分发金标准 payload 的假视觉后端（chat 被调用即断言失败）。"""

    def __init__(self, payloads: Dict[str, Dict[str, Any]]):
        self.payloads = payloads
        self.calls: List[str] = []

    def chat(self, messages: Any, **kwargs: Any):
        raise AssertionError("mock 视觉评测不应触发聊天调用（离线规则路由应保持确定性）")

    def complete_json(self, system: str, user: str, image_path: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
        from .vision_metrics import _compact  # noqa: F401  - 保持依赖方向清晰

        name = Path(str(image_path)).stem
        self.calls.append(name)
        if name not in self.payloads:
            raise KeyError(f"mock 视觉后端没有为 {name} 准备金标准 payload")
        return json.loads(json.dumps(self.payloads[name]))


def payloads_for_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {Path(str(record["image"])).stem: payload_from_record(record) for record in records if record.get("image")}
