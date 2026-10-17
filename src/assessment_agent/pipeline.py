"""端到端流水线：文本/截图输入 -> 抽取 -> 路由 -> 求解 -> 校验 -> 产物落盘。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .artifacts import RunArtifacts, default_run_id
from .config import Settings
from .knowledge import KnowledgeBase
from .llm import ChatModel, OpenAICompatibleClient
from .router import Router
from .schemas import ExtractionResult, Question, QuestionOption, RunResult, utc_now
from .skills import SkillContext, SkillRegistry
from .solver import Solver
from .text_utils import parse_options_from_text, parse_options_spec
from .tools import ToolBox, default_toolbox
from .verifier import Verifier
from .vision import VisionExtractor, VisionExtractionError

OptionInput = Union[None, str, Sequence[Any]]


class PipelineError(RuntimeError):
    """流水线配置或输入错误（可直接展示给用户）。"""


def coerce_options(options: OptionInput) -> List[QuestionOption]:
    if options is None:
        return []
    if isinstance(options, str):
        return parse_options_spec(options)
    if isinstance(options, dict):
        return [
            QuestionOption(label=str(label).upper(), text=str(text))
            for label, text in options.items()
        ]
    result: List[QuestionOption] = []
    for item in options:
        if isinstance(item, QuestionOption):
            result.append(item)
        elif isinstance(item, dict):
            label = str(item.get("label") or chr(ord("A") + len(result))).upper()
            result.append(QuestionOption(label=label, text=str(item.get("text") or "")))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append(QuestionOption(label=str(item[0]).upper(), text=str(item[1])))
        else:
            result.append(QuestionOption(label=chr(ord("A") + len(result)), text=str(item)))
    return result


class Pipeline:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        registry: Optional[SkillRegistry] = None,
        toolbox: Optional[ToolBox] = None,
        knowledge: Optional[KnowledgeBase] = None,
        llm: Optional[ChatModel] = None,
    ):
        self.settings = settings or Settings.from_env()
        self.registry = registry or SkillRegistry()
        self.toolbox = toolbox or default_toolbox()
        self.knowledge = knowledge or KnowledgeBase(self.settings.knowledge_dir)
        if llm is not None:
            self.llm: Optional[ChatModel] = llm
        elif self.settings.llm_enabled:
            self.llm = OpenAICompatibleClient(self.settings.llm)
        else:
            self.llm = None
        self.router = Router(self.settings, llm=self.llm, registry=self.registry)
        self.solver = Solver(self.registry)
        self.verifier = Verifier(self.settings, registry=self.registry, llm=self.llm)

    # ------------------------------------------------------------------ 主流程
    def run(
        self,
        *,
        text: Optional[str] = None,
        image_path: Optional[str] = None,
        options: OptionInput = None,
        skill: Optional[str] = None,
        run_id: Optional[str] = None,
        save: bool = True,
        hint: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        if not (text and text.strip()) and not image_path:
            raise PipelineError("需要提供题干文本（--text）或题目截图（--image）")
        if skill is not None and self.registry.get(skill) is None:
            raise PipelineError(f"未知题型 {skill!r}，可用题型：{self.registry.ids()}")

        started_at = utc_now()
        latency: Dict[str, float] = {}
        artifacts = RunArtifacts(self.settings.runs_dir, run_id or default_run_id())
        if save:
            artifacts.ensure()
            artifacts.append_trace("run_start", text=text, image_path=image_path, skill=skill)

        given_options = coerce_options(options)
        extraction: Optional[ExtractionResult] = None
        image_saved: Optional[Path] = None
        if image_path:
            if save:
                image_saved = artifacts.save_image(image_path)
        stem = (text or "").strip()
        question_options: List[QuestionOption] = list(given_options)

        if image_path and not stem:
            extraction_started = time.perf_counter()
            extraction = self._extract(image_path, hint, artifacts=artifacts if save else None)
            latency["extraction_ms"] = round((time.perf_counter() - extraction_started) * 1000.0, 3)
            stem = extraction.stem
            if not question_options:
                question_options = list(extraction.options)
            if save:
                artifacts.save_json("extraction", extraction.model_dump(mode="json"))
                artifacts.append_trace(
                    "extraction",
                    confidence=extraction.confidence,
                    warnings=extraction.warnings,
                    schema_version=extraction.schema_version,
                    regions={
                        "text": len(extraction.text_regions),
                        "diagram": len(extraction.diagram_regions),
                        "option": len(extraction.option_regions),
                        "table": len(extraction.table_regions),
                    },
                )
            if not stem:
                raise PipelineError("截图抽取未得到题干文本；请改用 --text 显式提供题目")
        elif stem and not question_options:
            parsed_stem, parsed_options = parse_options_from_text(stem)
            if parsed_options:
                stem, question_options = parsed_stem, parsed_options

        vision_meta = extraction.vision_meta() if extraction is not None else {}
        question = Question(
            stem=stem,
            options=question_options,
            skill=skill,
            image_path=str(image_saved) if image_saved else image_path,
            source="image" if image_path else "text",
            meta={
                "extraction_confidence": extraction.confidence if extraction else None,
                "has_diagram": extraction.has_diagram if extraction else False,
                **({"vision": vision_meta} if vision_meta else {}),
                **({"grid": vision_meta["stem_matrix_grid"]} if vision_meta.get("stem_matrix_grid") else {}),
                **(meta or {}),
            },
        )

        # ---------------------------------------------------------- 路由
        started = time.perf_counter()
        route = self.router.route(
            question,
            explicit_skill=skill,
            hint=extraction.skill_hint if extraction else None,
        )
        latency["route_ms"] = round((time.perf_counter() - started) * 1000.0, 3)

        # ---------------------------------------------------------- 求解
        ctx = SkillContext(
            settings=self.settings,
            llm=self.llm,
            toolbox=self.toolbox,
            knowledge=self.knowledge,
            artifacts=artifacts if save else None,
        )
        started = time.perf_counter()
        solution = self.solver.solve(question, route.skill, ctx)
        latency["solve_ms"] = round((time.perf_counter() - started) * 1000.0, 3)

        # ---------------------------------------------------------- 校验
        started = time.perf_counter()
        verification = self.verifier.verify(question, solution, ctx)
        latency["verify_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        latency["total_ms"] = round(sum(value for value in latency.values()), 3)

        finished_at = utc_now()
        artifacts_map: Dict[str, str] = {}
        if save:
            artifacts.save_json("question", question.model_dump(mode="json"))
            artifacts.save_json("route", route.model_dump(mode="json"))
            artifacts.save_json("solution", solution.model_dump(mode="json"))
            artifacts.save_json("verification", verification.model_dump(mode="json"))
            artifacts.append_trace(
                "solution",
                status=solution.status,
                answer=solution.answer,
                skill=solution.skill,
                confidence=solution.confidence,
                tool_calls=[call.model_dump() for call in solution.tool_calls],
            )
            artifacts.append_trace(
                "verification",
                passed=verification.passed,
                action=verification.action,
                final_answer=verification.final_answer,
            )

        result = RunResult(
            run_id=artifacts.run_id,
            run_dir=str(artifacts.dir),
            question=question,
            route=route,
            solution=solution,
            verification=verification,
            extraction=extraction,
            latency_ms=latency,
            started_at=started_at,
            finished_at=finished_at,
        )

        if save:
            report_path = artifacts.report_path
            artifacts_map = {
                "run": artifacts.relative(artifacts.path("run.json")),
                "question": artifacts.relative(artifacts.path("question.json")),
                "route": artifacts.relative(artifacts.path("route.json")),
                "solution": artifacts.relative(artifacts.path("solution.json")),
                "verification": artifacts.relative(artifacts.path("verification.json")),
                "trace": artifacts.relative(artifacts.path("trace.jsonl")),
                "report": artifacts.relative(report_path),
            }
            if extraction is not None:
                artifacts_map["extraction"] = artifacts.relative(artifacts.path("extraction.json"))
            if image_saved is not None:
                artifacts_map["image"] = artifacts.relative(image_saved)
            result.artifacts = artifacts_map
            artifacts.write_report(result)
            summary_path = artifacts.save_json(
                "run",
                {
                    "run_id": result.run_id,
                    "started_at": result.started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "skill": route.skill,
                    "route_source": route.source,
                    "status": solution.status,
                    "answer": (
                        verification.final_answer
                        if verification.action in {"accept", "repair"}
                        else None
                    ),
                    "solver_answer": solution.answer,
                    "verification": {
                        "passed": verification.passed,
                        "action": verification.action,
                    },
                    "latency_ms": latency,
                    "settings": self.settings.describe(),
                },
            )
            artifacts_map["run"] = artifacts.relative(summary_path)
            result.artifacts = artifacts_map
            artifacts.append_trace(
                "run_end",
                answer=(
                    verification.final_answer
                    if verification.action in {"accept", "repair"}
                    else None
                ),
                artifacts=artifacts_map,
            )

        return result

    # ------------------------------------------------------------------ 辅助
    def _extract(self, image_path: str, hint: Optional[str], *, artifacts: Optional[RunArtifacts] = None) -> ExtractionResult:
        if self.llm is None:
            raise PipelineError(
                "截图输入需要视觉模型：请配置 LLM_API_KEY 与 LLM_VISION_MODEL（见 .env.example）"
            )
        if not self.settings.llm.vision_configured:
            raise PipelineError("未配置 LLM_VISION_MODEL，无法进行截图抽取")
        if self.settings.vision_parser:
            from .vision_parser import VisionParseError, VisionParser

            parser = VisionParser(
                self.llm,
                self.settings.llm.vision_model,
                crops_dir=(artifacts.dir / "regions") if artifacts is not None else None,
            )
            try:
                return parser.parse(image_path, hint=hint or "")
            except VisionParseError as exc:
                raise PipelineError(f"视觉结构化解析失败：{exc}") from exc
        extractor = VisionExtractor(self.llm, self.settings.llm.vision_model)
        try:
            return extractor.extract(image_path, hint=hint or "")
        except VisionExtractionError as exc:
            raise PipelineError(str(exc)) from exc

    def capabilities(self) -> Dict[str, Any]:
        return {
            "settings": self.settings.describe(),
            "skills": self.registry.describe(),
            "tools": self.toolbox.describe(),
            "knowledge": self.knowledge.titles(),
        }
