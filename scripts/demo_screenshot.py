"""无需 API Key 演示“截图 -> 抽取 -> 路由 -> 求解 -> 校验”端到端流程。

脚本注入一个假的视觉模型（只返回预置 JSON），用于本地验证截图链路与产物落盘；
真实使用时请配置 ``LLM_API_KEY`` 与 ``LLM_VISION_MODEL``。

运行：``python scripts/demo_screenshot.py``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.llm import ChatResponse  # noqa: E402
from assessment_agent.pipeline import Pipeline  # noqa: E402

EXTRACTION_PAYLOAD: Dict[str, Any] = {
    "stem": "数列 2, 4, 8, 16, ( ) 的下一项是？",
    "options": [
        {"label": "A", "text": "24"},
        {"label": "B", "text": "30"},
        {"label": "C", "text": "32"},
        {"label": "D", "text": "36"},
    ],
    "question_type": "single_choice",
    "skill_hint": "number_series",
    "has_diagram": False,
    "confidence": 0.93,
}


class ScriptedVisionModel:
    """满足 ChatModel 协议的脚本化模型（不发起网络请求）。"""

    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> ChatResponse:
        return ChatResponse(text="{}", model="scripted-vision", usage={})

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append({"image_path": image_path, "model": model})
        return dict(self.payload)


def make_screenshot(path: Path) -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("[跳过] 未安装 Pillow，无法生成示例截图")
        raise SystemExit(0)
    image = Image.new("RGB", (720, 220), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((24, 40), "number series: 2, 4, 8, 16, ( )", fill=(20, 20, 20))
    for index, text in enumerate(["A. 24", "B. 30", "C. 32", "D. 36"]):
        draw.text((24 + index * 140, 120), text, fill=(20, 20, 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def main() -> int:
    os.environ.setdefault("LLM_API_KEY", "demo-fake-key")
    os.environ.setdefault("LLM_VISION_MODEL", "demo-vision")
    os.environ["ASSESSMENT_OFFLINE"] = "1"

    screenshot = make_screenshot(PROJECT_ROOT / "runs" / "demo" / "sample_question.png")
    settings = Settings.from_env()
    pipeline = Pipeline(settings, llm=ScriptedVisionModel(EXTRACTION_PAYLOAD))
    result = pipeline.run(image_path=str(screenshot), run_id="demo_screenshot")

    print(f"截图：{screenshot}")
    print(f"抽取题干：{result.question.stem}")
    print(f"抽取选项：{[(option.label, option.text) for option in result.question.options]}")
    print(f"路由题型：{result.route.skill}（{result.route.source}）")
    print(f"求解状态：{result.solution.status}，答案：{result.solution.answer}")
    print(f"校验：{'通过' if result.verification.passed else '未通过'}（{result.verification.action}）")
    print(f"产物目录：{result.run_dir}")
    for name, relative in result.artifacts.items():
        print(f"  - {name}: {relative}")
    return 0 if result.verification.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
