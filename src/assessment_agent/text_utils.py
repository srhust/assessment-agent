"""文本工具：从粘贴的题目文本中拆分题干与选项，或解析命令行选项。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from .schemas import QuestionOption

OPTION_LABEL_RE = re.compile(r"(?:^|\n|\s|　)([A-E])\s*[.、．)）:：]\s*")
MAX_OPTIONS = 8


def parse_options_from_text(text: str) -> Tuple[str, List[QuestionOption]]:
    """从含“A. …/B、…”的整段文本中拆出题干与选项。"""
    matches = list(OPTION_LABEL_RE.finditer(text))
    if len(matches) < 2:
        return text.strip(), []
    labels = [match.group(1) for match in matches]
    expected = [chr(ord("A") + index) for index in range(len(labels))]
    if labels != expected:
        return text.strip(), []
    stem = text[: matches[0].start()].strip()
    options: List[QuestionOption] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip(" \t\n　;；")
        options.append(QuestionOption(label=match.group(1), text=value))
    return stem, options


def parse_options_spec(spec: Optional[str]) -> List[QuestionOption]:
    """解析命令行 ``--options``。

    支持：
    - ``A=12,B=13,C=16``（显式标号）
    - ``12|13|16`` / ``12,13,16``（自动编号 A/B/C）
    """
    if not spec:
        return []
    spec = spec.strip()
    if not spec:
        return []
    if "=" in spec:
        options: List[QuestionOption] = []
        for chunk in re.split(r"[,;；]", spec):
            if not chunk.strip():
                continue
            label, _, value = chunk.partition("=")
            options.append(QuestionOption(label=label.strip().upper(), text=value.strip()))
        return options
    parts = [part.strip() for part in re.split(r"[|,;；]", spec) if part.strip()]
    return [QuestionOption(label=chr(ord("A") + index), text=part) for index, part in enumerate(parts)]


def format_options(options: Sequence[QuestionOption]) -> str:
    return "\n".join(f"{option.label}. {option.text}" for option in options)


def truncate(text: str, limit: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
