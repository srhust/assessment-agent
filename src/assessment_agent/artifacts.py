"""运行产物：原始题图、抽取结果、路由/求解/校验记录、可读报告。

目录结构（默认在 runs/<run_id>/ 下）::

    run.json                 # 汇总
    question.json            # 规范化题目
    extraction.json          # 视觉抽取原始结果（如有截图）
    route.json               # 路由决策
    solution.json            # 求解结果 + 工具调用轨迹
    verification.json        # 校验结果
    trace.jsonl              # 逐事件时间线（可直接用于蒸馏数据整理）
    report.md                # 人类可读报告
    image/original.<ext>     # 原始题图（不裁剪、不覆盖）
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .schemas import RunResult, utc_now


def default_run_id(prefix: str = "run") -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{uuid.uuid4().hex[:6]}_{prefix}"


class RunArtifacts:
    def __init__(self, root: Union[str, Path], run_id: Optional[str] = None):
        self.root = Path(root)
        self.run_id = run_id or default_run_id()
        self.dir = self.root / self.run_id
        self.image_dir = self.dir / "image"

    # ------------------------------------------------------------------ 路径
    def path(self, name: str) -> Path:
        return self.dir / name

    @property
    def report_path(self) -> Path:
        return self.path("report.md")

    # ------------------------------------------------------------------ 写入
    def ensure(self) -> Path:
        self.image_dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def save_image(self, source: Union[str, Path, bytes], suffix: str = "png") -> Path:
        self.ensure()
        target = self.image_dir / f"original.{suffix.lstrip('.')}"
        if isinstance(source, (bytes, bytearray)):
            target.write_bytes(bytes(source))
            return target
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"图片不存在: {source_path}")
        actual_suffix = source_path.suffix.lstrip(".") or suffix
        target = self.image_dir / f"original.{actual_suffix}"
        shutil.copyfile(source_path, target)
        return target

    def save_json(self, name: str, payload: Any) -> Path:
        self.ensure()
        target = self.path(name if name.endswith(".json") else f"{name}.json")
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return target

    def append_trace(self, event: str, **fields: Any) -> Path:
        self.ensure()
        target = self.path("trace.jsonl")
        record: Dict[str, Any] = {"ts": utc_now().isoformat(), "event": event}
        record.update({key: value for key, value in fields.items()})
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return target

    def write_report(self, result: RunResult) -> Path:
        self.ensure()
        target = self.report_path
        target.write_text(render_report(result), encoding="utf-8")
        return target

    def relative(self, path: Union[str, Path]) -> str:
        try:
            return Path(path).relative_to(self.dir).as_posix()
        except ValueError:
            return Path(path).as_posix()


def _bullets(items: List[str], empty: str = "（无）") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def render_report(result: RunResult) -> str:
    question = result.question
    solution = result.solution
    verification = result.verification
    option_lines = [f"{option.label}. {option.text}" for option in question.options]

    check_lines = [
        f"[{'PASS' if check.passed else 'FAIL'}] {check.name} — {check.detail}"
        for check in verification.checks
    ]
    tool_lines = [
        f"{call.tool}({json.dumps(call.args, ensure_ascii=False)}) -> "
        f"{'ok' if call.ok else 'error: ' + str(call.error)} [{call.duration_ms} ms]"
        for call in solution.tool_calls
    ]
    artifact_lines = [f"{name}: {path}" for name, path in result.artifacts.items()]
    latency_lines = [f"{stage}: {value:.1f} ms" for stage, value in result.latency_ms.items()]

    return "\n".join(
        [
            f"# 测评解题报告 · {result.run_id}",
            "",
            f"- 生成时间：{result.started_at.isoformat()}",
            f"- 题型：{result.route.skill}（路由来源 {result.route.source}，置信度 {result.route.confidence:.2f}）",
            f"- 求解状态：{solution.status}",
            f"- 最终答案：{verification.final_answer or solution.answer or '无'}",
            f"- 校验结论：{'通过' if verification.passed else '未通过'}（action={verification.action}）",
            "",
            "## 题目",
            "",
            question.stem or "（题干为空）",
            "",
            _bullets(option_lines),
            "",
            "## 解题步骤",
            "",
            _bullets(solution.steps),
            "",
            "## 工具调用",
            "",
            _bullets(tool_lines),
            "",
            "## 校验明细",
            "",
            _bullets(check_lines),
            "",
            "## 耗时",
            "",
            _bullets(latency_lines),
            "",
            "## 产物",
            "",
            _bullets(artifact_lines),
            "",
        ]
    )
