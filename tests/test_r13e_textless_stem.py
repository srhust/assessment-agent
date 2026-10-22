# -*- coding: utf-8 -*-
"""R13-E：无文字题干 + 可信图形区域 → 合法进入图推链路（零 API，MockVision）。

金标**不注入题干**：payload 故意不含 stem_text；答案必须由几何证据得出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.config import LLMSettings, Settings  # noqa: E402
from assessment_agent.evaluation.mock_vision import MockVisionClient  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402

IMG = ROOT / "data/benchmark_figshots/images/dev/ft-mirror-04.png"


def _payload(item):
    reg = item["regions"]
    opt_regions = []
    for o in reg.get("option_regions") or []:
        if not o.get("has_figure"):
            continue
        opt_regions.append({"label": o.get("label"), "bbox": o.get("bbox"),
                            "has_figure": True})
    sm = reg.get("stem_matrix_bbox")
    grid = reg.get("stem_matrix_grid") or [1, 3]
    return {
        "stem_text": "",                      # 关键：VLM 没给出文字题干
        "diagram_regions": [{"id": "d1", "role": "stem_matrix",
                             "bbox": list(sm), "grid": {"rows": int(grid[0]),
                                                        "cols": int(grid[1])}}],
        "option_regions": opt_regions,
        "image_size": reg.get("image_size"),
    }


def test_textless_stem_figure_page_goes_through_pipeline(tmp_path: Path):
    item = [json.loads(l) for l in
            (ROOT / "data/benchmark_figshots/dev/figshots.jsonl")
            .read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l)["id"] == "ft-mirror-04"][0]
    fake = MockVisionClient({"ft-mirror-04": _payload(item)})
    settings = Settings(offline=True, data_dir=ROOT / "data",
                        results_dir=tmp_path / "results",
                        llm=LLMSettings(api_key="sk-offline-mock",
                                        vision_model="mock-vlm"))
    pipe = Pipeline(settings, llm=fake)
    res = pipe.run(image_path=str(IMG), save=True)  # 不再抛 PipelineError
    assert not (res.question.stem or "").strip(), "金标/文字不得注入"
    assert res.route.skill == "figure_reasoning"
    sol = res.solution
    assert sol.status in ("solved", "failed", "abstained")
    if sol.status == "solved":
        assert sol.answer == item["answer"]
