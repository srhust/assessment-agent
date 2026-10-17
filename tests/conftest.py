"""pytest 公共夹具：离线设置、假模型、示例截图。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import pytest

from assessment_agent.config import Settings
from assessment_agent.llm import ChatResponse


def make_settings(tmp_path: Path, extra: Optional[Mapping[str, str]] = None) -> Settings:
    env: Dict[str, str] = {
        "ASSESSMENT_OFFLINE": "1",
        "ASSESSMENT_RUNS_DIR": str(tmp_path / "runs"),
        "ASSESSMENT_RESULTS_DIR": str(tmp_path / "results"),
        "ASSESSMENT_DATA_DIR": str(tmp_path / "data"),
    }
    env.update(dict(extra or {}))
    return Settings.from_env(env=env)


@dataclass
class FakeChatModel:
    """满足 ``ChatModel`` 协议的假模型：记录调用，返回预置 JSON。"""

    payload: Dict[str, Any] = field(default_factory=dict)
    text_response: str = "{}"
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def chat(self, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> ChatResponse:
        self.calls.append({"kind": "chat", "messages": list(messages), **kwargs})
        return ChatResponse(text=self.text_response, model="fake-model", usage={"total_tokens": 1})

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append(
            {"kind": "json", "system": system, "user": user, "image_path": image_path, "model": model}
        )
        return dict(self.payload)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def fake_llm() -> FakeChatModel:
    return FakeChatModel()


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    pillow = pytest.importorskip("PIL.Image")
    target = tmp_path / "question.png"
    image = pillow.new("RGB", (120, 80), color=(255, 255, 255))
    image.save(target)
    return target


@pytest.fixture
def screenshot_llm() -> FakeChatModel:
    """视觉抽取用的假模型：返回一道数字推理题。"""

    payload = {
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
        "confidence": 0.92,
    }
    return FakeChatModel(payload=payload)


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path
