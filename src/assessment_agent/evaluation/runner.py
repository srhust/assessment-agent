"""评测执行器：跑 train/dev/test 数据集，输出准确率、耗时与失败原因。"""

from __future__ import annotations

import json
import re
import functools
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..pipeline import Pipeline, PipelineError
from ..schemas import QuestionOption
from .diagnosis import (
    classify_case_failure,
    attribute_error_chain,
    classify_exception,
    derive_root_cause,
    derive_verification_outcome,
)
from .metrics import CaseResult, MetricsAccumulator, answers_match
from .vision_metrics import aggregate_vision, compare_extraction_detail, render_region_overlay

SPLITS = ("train", "dev", "test", "screens", "bench_dev", "bench_holdout",
          "verbal_dev", "verbal_blind", "verbal_epistemic_dev",
          "language_dev", "language_blind", "verbal_blind2",
          "figtrans_dev", "figtrans_test",
          "figshots_dev", "figshots_test")

# split → 数据集目录（benchmark_real 独立泛化基准：dev 可调参、holdout 冻结）
_SPLIT_DIRS = {
    "bench_dev": "benchmark_real/dev",
    "bench_holdout": "benchmark_real/holdout",
    "verbal_dev": "benchmark_verbal/dev",
    "verbal_blind": "benchmark_verbal/blind",
    "verbal_epistemic_dev": "benchmark_epistemic/dev",
    "language_dev": "benchmark_language/dev",
    "language_blind": "benchmark_language/blind",
    "verbal_blind2": "benchmark_verbal/blind2",
    "figtrans_dev": "benchmark_figtrans/dev",
    "figtrans_test": "benchmark_figtrans/test",
    # R4b P0-1/P1-3：完整页面截图集（image_only 端到端专用；figtrans 转 Oracle 回归集）
    "figshots_dev": "benchmark_figshots/dev",
    "figshots_test": "benchmark_figshots/test",
}

EVAL_MODES = ("oracle_solver", "auto_router", "image_only")

# R2c：标注双维度（题目标注可显式给出 answerability/capability_status，缺省时按
# 客观事实派生——金标准答案是否存在决定 answerability，题型是否已实现决定 capability）
SUBJECTIVE_SKILLS = {"personality"}


@functools.lru_cache(maxsize=1)
def _registry_capability_ids() -> Tuple[str, ...]:
    """capability_status 从**实际注册的 Skill 声明**派生（R3a：不再维护固定名单）。

    依据 ``BaseSkill.supported``（注册表即版本化能力声明）；未注册或未支持 → unsupported。
    """
    from ..skills import default_skills

    return tuple(sorted(skill.id for skill in default_skills() if skill.supported))


def derive_answerability(answer: Optional[str], expected_status: str, skill: str) -> str:
    """answerability（客观属性，与系统能力无关）：
    - subjective：无客观标准答案（性格/自评量表类）。**优先级最高**：量表里出现的
      "答案"只是受试者的实际选择，不是唯一客观正确答案，绝不能因 `answer` 非空
      而被派生成 has_unique_answer（R3a 修复）；
    - has_unique_answer：存在唯一客观标准答案（哪怕当前 Skill 未实现该题型）；
    - undecidable：信息不足以唯一确定（含构造性歧义/合规弃权样本）；
    - objective_unlabeled：客观可答但本数据集未标注答案（不进答题分母，单独上报）。
    """
    if skill in SUBJECTIVE_SKILLS:
        return "subjective"
    if answer is not None:
        return "has_unique_answer"
    if expected_status in ("undecidable", "failed"):
        return "undecidable"
    return "objective_unlabeled"


def derive_capability_status(skill: str, capability_ids: Optional[Sequence[str]] = None) -> str:
    """capability_status（当前系统能力声明）：supported / unsupported。

    缺省从实际注册能力派生；评测运行时会传入 ``pipeline.registry`` 的 supported 集合，
    确保注册表变化（如 R3a 实现 data_analysis）自动反映到口径，无需改代码常量。
    """
    ids = tuple(capability_ids) if capability_ids is not None else _registry_capability_ids()
    return "supported" if skill in ids else "unsupported"


@dataclass
class Case:
    id: str
    skill: str
    stem: str
    options: List[QuestionOption] = field(default_factory=list)
    answer: Optional[str] = None
    expected_status: str = "solved"
    tags: List[str] = field(default_factory=list)
    layout: str = ""
    source_file: str = ""
    image: Optional[str] = None
    grid: Optional[List[int]] = None
    # 金标准区域（oracle region）：VisionParser v2 结构的 dict，crop_path 已解析为绝对路径
    regions: Optional[Dict[str, Any]] = None
    # R2c：标注双维度（显式标注优先，缺省派生）
    answerability: str = ""
    capability_status: str = ""


@dataclass
class EvaluationSummary:
    split: str
    total: int
    accuracy: float
    metrics: Dict[str, Any]
    output_dir: Optional[str]
    duration_s: float
    results: List[CaseResult] = field(default_factory=list)
    eval_mode: str = "oracle_solver"
    eval_batch: str = ""  # 批次唯一标识：image_only 的 runs 产物按批次隔离，不覆盖历史证据


def _load_options(raw: Any) -> List[QuestionOption]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [
            QuestionOption(label=str(label).upper(), text=str(text))
            for label, text in raw.items()
        ]
    if isinstance(raw, list):
        options: List[QuestionOption] = []
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                options.append(
                    QuestionOption(
                        label=str(item.get("label") or chr(ord("A") + index)).upper(),
                        text=str(item.get("text") or ""),
                    )
                )
            else:
                options.append(QuestionOption(label=chr(ord("A") + index), text=str(item)))
        return options
    return []


def _load_answer(payload: Dict[str, Any]) -> Optional[str]:
    """标注答案：支持 ``answer`` 与 ``expected_answer`` 两种写法。"""
    for key in ("answer", "expected_answer"):
        value = payload.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return None


def _resolve_image(data_dir: Path, raw: Any, source: Path, line_number: int) -> Optional[str]:
    """题图路径：相对路径按 data/ 目录解析；缺失时直接报错，而不是静默跳过。"""
    if not raw:
        return None
    candidate = Path(str(raw))
    resolved = candidate if candidate.is_absolute() else Path(data_dir) / candidate
    if not resolved.is_file():
        raise FileNotFoundError(f"{source.name}:{line_number} 题图不存在: {resolved}")
    return str(resolved)


def _load_regions(raw: Any, source: Path) -> Optional[Dict[str, Any]]:
    """金标准区域：crop 路径按 data/ 目录解析；引用缺失文件时直接报错（不静默降级）。"""
    if not isinstance(raw, dict):
        return None
    regions = json.loads(json.dumps(raw))  # deep copy

    def _resolve(path_value: Any) -> Optional[str]:
        if not path_value:
            return None
        candidate = Path(str(path_value))
        resolved = candidate if candidate.is_absolute() else Path(source).parent.parent / candidate
        return str(resolved)

    stem_crop = _resolve(regions.get("stem_matrix_crop"))
    if stem_crop and not Path(stem_crop).is_file():
        raise FileNotFoundError(f"{source.name} 区域裁剪不存在: {stem_crop}")
    regions["stem_matrix_crop"] = stem_crop
    for option in regions.get("option_regions") or []:
        crop = _resolve(option.get("crop_path"))
        if crop and not Path(crop).is_file():
            raise FileNotFoundError(f"{source.name} 选项裁剪不存在: {crop}")
        option["crop_path"] = crop
    # R3a：统计图金标区域（dev）的柱状图裁剪同样按 data/ 根解析并强制存在
    chart = regions.get("chart")
    if isinstance(chart, dict) and chart.get("crop_path"):
        crop = _resolve(chart["crop_path"])
        if crop and not Path(crop).is_file():
            raise FileNotFoundError(f"{source.name} 统计图裁剪不存在: {crop}")
        chart["crop_path"] = crop
    return regions


def _load_grid(raw: Any) -> Optional[List[int]]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            rows, cols = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return None
        if rows > 0 and cols > 0:
            return [rows, cols]
    return None


def load_cases(
    data_dir: Path,
    split: str,
    skills: Optional[Sequence[str]] = None,
    capability_ids: Optional[Sequence[str]] = None,
) -> List[Case]:
    """capability_ids：实际注册能力声明（评测运行器传入 registry 的 supported 集合）；
    缺省则按模块默认注册表派生，不再是写死的题型名单。"""
    if split not in SPLITS:
        raise ValueError(f"split 必须是 {SPLITS} 之一，收到 {split!r}")
    split_dir = Path(data_dir) / _SPLIT_DIRS.get(split, split)
    if not split_dir.is_dir():
        raise FileNotFoundError(f"数据集目录不存在: {split_dir}")
    cases: List[Case] = []
    for path in sorted(split_dir.glob("*.jsonl")):
        if skills and path.stem not in skills:
            continue
        # utf-8-sig：兼容 Windows PowerShell 写出的 BOM 文件
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 解析失败: {exc}") from exc
            answer = _load_answer(payload)
            skill = str(payload.get("skill") or path.stem)
            expected_status = str(payload.get("expected_status") or "solved")
            cases.append(
                Case(
                    id=str(payload.get("id") or f"{path.stem}-{line_number}"),
                    skill=skill,
                    stem=str(payload.get("stem") or ""),
                    options=_load_options(payload.get("options")),
                    answer=answer,
                    expected_status=expected_status,
                    tags=[str(tag) for tag in payload.get("tags") or []],
                    layout=str(payload.get("layout") or ""),
                    source_file=str(path.name),
                    image=_resolve_image(data_dir, payload.get("image"), path, line_number),
                    grid=_load_grid(payload.get("grid")),
                    regions=_load_regions(payload.get("regions"), path),
                    answerability=str(payload.get("answerability") or "")
                    or derive_answerability(answer, expected_status, skill),
                    capability_status=str(payload.get("capability_status") or "")
                    or derive_capability_status(skill, capability_ids),
                )
            )
    if not cases:
        raise ValueError(f"{split_dir} 中没有可用样本")
    return cases


def _failure_reason(status: str, action: str, error: Optional[str]) -> Optional[str]:
    if status == "unsupported":
        return "unsupported_skill"
    if status == "failed":
        return error or "solve_failed"
    if action == "reject":
        return "verification_rejected"
    return "wrong_answer"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _compare_extraction(case: Case, extraction: Any) -> Tuple[Optional[bool], Optional[float], Optional[bool]]:
    """image-only 模式模块级归因：题干解析 / 区域 IoU / 选项标号映射（vs 金标准标注）。"""
    if extraction is None:
        return False, None, False
    accurate = bool(case.stem) and _compact(extraction.stem) == _compact(case.stem)
    iou: Optional[float] = None
    gold_bbox = (case.regions or {}).get("stem_matrix_bbox") if isinstance(case.regions, dict) else None
    if gold_bbox and extraction.diagram_regions:
        from ..vision_parser import _bbox_iou

        best = max((_bbox_iou(gold_bbox, region.bbox) for region in extraction.diagram_regions), default=None)
        iou = round(float(best), 4) if best is not None else None
    gold_labels = [option.label for option in case.options]
    parsed_labels = [option.label for option in extraction.options]
    if gold_labels:
        option_map_ok = gold_labels == parsed_labels
    else:
        option_map_ok = not parsed_labels
    return accurate, iou, option_map_ok


class EvaluationRunner:
    def __init__(self, pipeline: Pipeline, data_dir: Optional[Path] = None, results_dir: Optional[Path] = None):
        self.pipeline = pipeline
        self.data_dir = Path(data_dir or pipeline.settings.data_dir)
        self.results_dir = Path(results_dir or pipeline.settings.results_dir)

    def run(
        self,
        split: str = "test",
        *,
        save: bool = True,
        run_name: Optional[str] = None,
        skills: Optional[Sequence[str]] = None,
        route_mode: str = "oracle",
        eval_mode: Optional[str] = None,
        eval_batch: Optional[str] = None,
        case_ids: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
        no_overwrite: bool = False,
    ) -> EvaluationSummary:
        # 三种评测输入模式（R2a）：
        #   oracle_solver：标准题干/区域/题型 → 只测 Solver
        #   auto_router ：标准题干/区域、题型未知 → 测 Router + Solver
        #   image_only  ：仅原始截图 → 测 Vision + Router + Solver + Verifier
        mode = eval_mode or ("oracle_solver" if route_mode == "oracle" else "auto_router")
        if mode not in EVAL_MODES:
            raise ValueError(f"eval_mode 必须是 {EVAL_MODES} 之一，收到 {mode!r}")
        # R2b：批次唯一标识——image_only 的 runs 产物按批次隔离，重复评测不覆盖历史证据
        batch = eval_batch or run_name or datetime.now().strftime("%Y%m%dT%H%M%S")
        # R4d P1：no_overwrite=True 时，已存在且非空的输出目录直接报错——
        # 静默重跑会覆盖上一份调用凭证，实验报告之间必须可区分。
        if no_overwrite and save:
            _probe = self.results_dir / split / (run_name or batch)
            if _probe.is_dir() and any(_probe.iterdir()):
                raise FileExistsError(
                    f"输出目录已存在且非空，拒绝静默覆盖（用唯一 run_name 或去掉 --no-overwrite）: {_probe}")
        cases = load_cases(
            self.data_dir, split, skills=skills,
            capability_ids=[s.id for s in self.pipeline.registry.supported()],
        )
        # R4c：冒烟子集选择（显式 case_ids/limit 只影响本次运行范围，不改数据、
        # 不按结果筛题——选择标准必须在运行前声明并写入报告）
        if case_ids:
            wanted = list(case_ids)
            cases = [c for c in cases if c.id in wanted]
            missing = [q for q in wanted if q not in {c.id for c in cases}]
            if missing:
                raise ValueError(f"case_ids 不存在于 {split}: {missing}")
        if limit is not None:
            cases = cases[: int(limit)]
        accumulator = MetricsAccumulator()
        started = time.perf_counter()
        for case in cases:
            result = self._run_case(case, mode, eval_batch=batch)
            accumulator.add(result)
        duration = time.perf_counter() - started

        metrics = accumulator.summary(route_mode=mode)
        metrics["eval_batch"] = batch
        if isinstance(metrics.get("capability"), dict):
            # R3a：能力声明溯源——capability_status 来自实际注册表（版本化声明），非硬编码名单
            metrics["capability"]["capability_source"] = "skill_registry"
            metrics["capability"]["registered_supported_skills"] = sorted(
                s.id for s in self.pipeline.registry.supported())
        vision_agg = aggregate_vision([item.vision for item in accumulator.results if item.vision])
        if vision_agg:
            metrics["vision"] = vision_agg
        output_dir: Optional[Path] = None
        if save:
            output_dir = self.results_dir / split / (run_name or datetime.now().strftime("%Y%m%dT%H%M%S"))
            output_dir.mkdir(parents=True, exist_ok=True)
            self._write_outputs(output_dir, split, cases, accumulator.results, metrics, duration)
        return EvaluationSummary(
            split=split,
            total=metrics["total"],
            accuracy=metrics["accuracy"],
            metrics=metrics,
            output_dir=str(output_dir) if output_dir else None,
            duration_s=round(duration, 3),
            results=accumulator.results,
            eval_mode=mode,
            eval_batch=batch,
        )

    # ------------------------------------------------------------------ 内部
    def _usage(self) -> Dict[str, Any]:
        """真实模型用量（客户端可获取时），否则空 dict。"""
        fn = getattr(self.pipeline.llm, "usage_snapshot", None)
        return fn() if callable(fn) else {}

    def _audit(self) -> Dict[str, Any]:
        fn = getattr(self.pipeline.llm, "audit_snapshot", None)
        return fn() if callable(fn) else {}

    @staticmethod
    def _vision_stages(audit_before: Dict[str, Any], audit_after: Dict[str, Any],
                       extraction: Any, vision_detail: Dict[str, Any],
                       image_path: Optional[str] = None) -> Dict[str, bool]:
        """每题视觉链路三段（R4d）：HTTP 2xx → JSON/schema 合法 → 结构化产物可用；
        再加证据完整（必需区域全命中）。差分口径避免把 router/solver 请求混计。

        ``VisionParser.parse`` 把 HTTP/JSON 失败也抛成 VisionParseError（被
        pipeline 转为 PipelineError），因此**不能只看 parse_failed**——那会把
        "根本没发出请求"与"JSON 不合法"混为一谈。三段分别取：客户端分阶段
        审计差分（HTTP/schema 层级）与**区域证据**（结构化层级）。
        结构化不能只看 `parser` 字段——schemas.ExtractionResult 的 parser
        默认值就是 vision_parser，v1 纯文本抽取（VISION_PARSER=0）产物同样
        带该值；因此要求 v2 区域数组**真实非空**（全被 bbox 校验丢弃 = 无
        证据，不算结构化成功）。
        """
        def _p(a: Dict[str, Any], key: str) -> int:
            return int(((a.get("by_phase") or {}).get("vision_parse") or {}).get(key, 0))
        d_http = _p(audit_after, "http_succeeded") - _p(audit_before, "http_succeeded")
        d_json = _p(audit_after, "json_output_valid") - _p(audit_before, "json_output_valid")
        parser = getattr(extraction, "parser", None) if extraction is not None else None
        has_regions = bool(extraction is not None and any(
            getattr(extraction, attr, None) for attr in
            ("text_regions", "diagram_regions", "option_regions", "table_regions")))
        http_ok = d_http > 0
        schema_ok = d_json > 0
        extraction_valid = bool(extraction is not None and parser == "vision_parser"
                                and has_regions)
        # --- R4e P1-1 三档口径（评审裁决：旧 evidence_complete 过宽）-----------
        # ① region_evidence_complete：区域与选项标签齐（= 旧 evidence_complete 定义，
        #    原键保留原语义，首跑记录口径不追改）。
        # ② structural_evidence_complete：区域齐 **且** 结构可核对——图形题必须
        #    网格解析正确（VLM 1×3 vs 金标 1×4 这类不算）、题干+选项裁剪完整落盘、
        #    选项映射成立；纯文本题没有矩阵结构，结构档=区域档。
        # ③ solver_ready：结构齐 **且** 求解器实际拿得到的裁剪文件都存在（审计用
        #    磁盘事实，而非只信 flags）。
        region_evidence = bool(http_ok and extraction_valid
                               and not vision_detail.get("region_detection_failure", True))
        is_matrix = vision_detail.get("grid_parse_correct") is not None
        structural = bool(region_evidence
                          and (not is_matrix or vision_detail.get("grid_parse_correct") is True)
                          and (not is_matrix or vision_detail.get("crops_complete") is True))
        crop_paths: list = []
        stem_crop = getattr(extraction, "stem_matrix_crop", None) if extraction is not None else None
        if stem_crop:
            crop_paths.append(str(stem_crop))
        if extraction is not None:
            for reg in (getattr(extraction, "option_regions", None) or []):
                cp = (reg.get("crop_path") if isinstance(reg, dict)
                      else getattr(reg, "crop_path", None))
                if cp and (reg.get("has_figure") if isinstance(reg, dict)
                           else getattr(reg, "has_figure", False)):
                    crop_paths.append(str(cp))
        solver_ready = bool(structural and crop_paths
                            and all(Path(cp).is_file() for cp in crop_paths))
        # --- R4g P1-1 运行时自检字段（金标无关，可用原图+模型输出+几何证据复算）---
        # 旧键（grid_parse_correct 比对出的 structural/solver_ready）保留**离线评测**
        # 语义不追改；在线决策与部署重试必须改用 runtime_* 组——尤其不得拿
        # gold 比对结果去控制推理。
        runtime: Dict[str, Any] = {
            "runtime_layout_consistent": None, "layout_source": None,
            "layout_hypotheses": None, "layout_ambiguity_reason": None,
            "crop_content_integrity": None, "crops_cover_all_detected_units": None,
            "solver_ready_runtime": None,
        }
        stem_regions: list = []
        option_payload: list = []
        if extraction is not None:
            for r in (getattr(extraction, "diagram_regions", None) or []):
                d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r or {})
                if str(d.get("role")) in ("stem_figure", "stem_matrix"):
                    stem_regions.append(d)
            for r in (getattr(extraction, "option_regions", None) or []):
                option_payload.append(r.model_dump(mode="json")
                                      if hasattr(r, "model_dump") else dict(r or {}))
        if (image_path and stem_regions and extraction is not None
                and extraction_valid):
            try:
                from ..tools.diagram_layout import assemble_stem_layout, check_option_crop
                from ..tools.figure_tools import load_mask

                mask = load_mask(str(image_path))
                res = assemble_stem_layout(mask, stem_regions, option_payload)
                res.pop("_canvas", None)
                runtime["runtime_layout_consistent"] = bool(res.get("verified"))
                runtime["layout_source"] = res.get("layout_source")
                runtime["layout_hypotheses"] = len(res.get("hypotheses") or [])
                runtime["layout_ambiguity_reason"] = res.get("ambiguity_reason")
                crops_ok = []
                for reg in option_payload:
                    cp = reg.get("crop_path")
                    if reg.get("has_figure") and cp and Path(str(cp)).is_file():
                        crops_ok.append(check_option_crop(load_mask(str(cp)))["passed"])
                runtime["crop_content_integrity"] = bool(crops_ok and all(crops_ok))
                und = next((c["detail"].get("undeclared_units") for c in res["checks"]
                            if c["name"] == "undeclared_units"), None)
                # 原图可见但 VLM 未声明的单元存在 → 裁剪未覆盖全部必需图形（如实）；
                # 矩阵直通档 units 语义不适用 → None（不冒充判定）
                runtime["crops_cover_all_detected_units"] = (
                    None if res.get("layout_source") == "matrix_passthrough"
                    else bool(und is not None and len(und) == 0))
                runtime["solver_ready_runtime"] = bool(
                    region_evidence and runtime["runtime_layout_consistent"]
                    and runtime["crop_content_integrity"] and crop_paths
                    and all(Path(cp).is_file() for cp in crop_paths))
            except Exception as exc:  # noqa: BLE001 - fail-closed：复算失败不冒充证据
                runtime["layout_ambiguity_reason"] = f"runtime_recompute_error:{type(exc).__name__}"
        return {
            "vision_http_success": http_ok,
            "vision_schema_valid": schema_ok,
            "vision_extraction_valid": extraction_valid,
            "vision_payload_source": parser,
            "legacy_payload_fallback": bool(extraction is not None and d_json > 0
                                            and not extraction_valid),
            "evidence_complete": region_evidence,
            "region_evidence_complete": region_evidence,
            "structural_evidence_complete": structural,
            "solver_ready": solver_ready,
            **runtime,
        }

    @staticmethod
    def _gold_payload(case: Case) -> Dict[str, Any]:
        """把 Case 金标准转成 VisionParser v2 payload 形状，供逐内容项比较。"""
        gold: Dict[str, Any] = {}
        if isinstance(case.regions, dict):
            gold.update(json.loads(json.dumps(case.regions)))  # deep copy
        gold["stem_text"] = case.stem
        if case.options:
            gold.setdefault("option_regions", [])
            known = {str(o.get("label", "")).upper() for o in gold["option_regions"]}
            for option in case.options:
                if option.label.upper() not in known:
                    gold["option_regions"].append({"label": option.label, "text": option.text})
        if case.grid:
            gold.setdefault("stem_matrix_grid", list(case.grid))
        return gold

    def _run_case(self, case: Case, mode: str = "oracle_solver", *, eval_batch: str = "") -> CaseResult:
        started = time.perf_counter()
        extraction_accurate: Optional[bool] = None
        region_iou: Optional[float] = None
        option_map_ok: Optional[bool] = None
        run_ref: Optional[str] = None
        vision_detail: Dict[str, Any] = {}
        usage: Dict[str, Any] = {}
        _pre_audit = self._audit()  # R4d：题前阶段审计快照（异常路径也要差分）
        gold_payload = self._gold_payload(case)
        try:
            if mode == "image_only":
                # 端到端：只给原始截图，禁止注入 stem / options / skill / grid / 区域标注
                if not case.image:
                    latency = (time.perf_counter() - started) * 1000.0
                    miss_outcome = derive_verification_outcome(
                        answerability=case.answerability, action="reject",
                        predicted=None, solution_answer=None, gold=case.answer,
                    )
                    return CaseResult(
                        case_id=case.id,
                        skill=case.skill,
                        expected=case.answer,
                        predicted=None,
                        correct=False,
                        status="failed",
                        verification_action="reject",
                        latency_ms=round(latency, 3),
                        expected_status=case.expected_status,
                        failure_reason="missing_image_for_e2e",
                        failure_class="vision_parse_error",
                        tags=list(case.tags),
                        input_mode=mode,
                        vision=compare_extraction_detail(gold_payload, None)
                        | self._vision_stages(_pre_audit, self._audit(), None,
                                              compare_extraction_detail(gold_payload, None)),
                        answerability=case.answerability,
                        capability_status=case.capability_status,
                        verification_outcome=miss_outcome,
                        root_cause=derive_root_cause(
                            outcome=miss_outcome, failure_class="vision_parse_error",
                            capability_status=case.capability_status, skill=case.skill,
                        ),
                    )
                usage_before = self._usage()
                audit_before = self._audit()
                run_id = f"e2e-{eval_batch}-{case.id}" if eval_batch else f"e2e-{case.id}"
                # image_only 输入契约：仅原始整页图片进求解链路
                run = self.pipeline.run(image_path=case.image, save=True, run_id=run_id)
                usage = self._usage_delta(usage_before, self._usage())
                extraction_accurate, region_iou, option_map_ok = _compare_extraction(case, run.extraction)
                vision_detail = compare_extraction_detail(gold_payload, run.extraction)
                # 三段判定以 VisionParser **协议来源字段**为准：合法 payload 走
                # vision_parser；旧版纯文本降级为 legacy_payload（不算结构化成功）；
                # 由 client 分阶段审计补 HTTP/schema 层级
                stages = self._vision_stages(_pre_audit, self._audit(),
                                             run.extraction, vision_detail,
                                             image_path=case.image)
                vision_detail.update(stages)
                run_ref = run.run_id
            else:
                skill_for_pipeline = case.skill if mode == "oracle_solver" else None
                meta: Dict[str, Any] = {}
                if case.grid:
                    meta["grid"] = case.grid
                if getattr(case, "layout", None):
                    meta["layout"] = case.layout
                if case.regions:
                    meta["vision"] = case.regions
                usage_before = self._usage()
                run = self.pipeline.run(
                    text=case.stem,
                    image_path=case.image,
                    options=case.options,
                    skill=skill_for_pipeline,
                    save=False,
                    meta=meta or None,
                )
                usage = self._usage_delta(usage_before, self._usage())
            solution = run.solution
            verification = run.verification
            route = run.route
            # 只在 Verifier 接受或修复答案时使用 final_answer，拒绝时不输出未验证答案
            predicted = (
                verification.final_answer
                if verification.action in {"accept", "repair"}
                else None
            )
            if case.expected_status == "unsupported":
                if case.capability_status == "supported":
                    # R3a：该样本生成时"未实现"，但当前注册表声明已实现。
                    # 有金标准 → 按真实答题判定；无金标准（数据区域缺失类样本）→
                    # 只要不给出通过校验的答案就是合规受控弃权（不强求 status 字面值）。
                    if case.answer is not None:
                        correct = solution.status == "solved" and answers_match(predicted, case.answer)
                    else:
                        correct = predicted is None
                else:
                    correct = solution.status == "unsupported"
            elif case.expected_status == "failed":
                # 合规性样本：必须严格匹配 failed 状态（不猜答案）才算正确
                correct = solution.status == "failed"
            elif case.expected_status == "undecidable":
                # R2b 独立基准：信息不足以唯一确定答案（构造时即如此），
                # 系统必须不给通过校验的答案——含 Solver 作答但被独立复核拦截
                # （reject/unverified）的受控弃权（R2c：与注释语义一致）
                correct = predicted is None
            else:
                correct = solution.status == "solved" and answers_match(predicted, case.answer)
            latency = (time.perf_counter() - started) * 1000.0
            # R4g P1-1：候选答案是否有足够证据获准释放（金标无关的在线语义）
            vision_detail["answer_evidence_sufficient"] = verification.action in (
                "accept", "repair")
            # R4e P1-2：先构建错误链，再让 failure_class 看到结构/几何阻塞（不再
            # 被题干文本截断的早期判定遮蔽）
            transform_meta = (solution.meta or {}).get("transform") or {}
            chain = attribute_error_chain(
                mode=mode, correct=correct, answered=predicted is not None,
                vision=vision_detail, extraction_stem_ok=extraction_accurate,
                option_map_ok=option_map_ok,
                routed_ok=(route.skill == case.skill) if route.skill else None,
                transform=transform_meta,
                verification_action=verification.action,
                solution_answer=solution.answer,
            )
            failure_class = classify_case_failure(
                case=case,
                mode=mode,
                correct=correct,
                status=solution.status,
                action=verification.action,
                predicted=predicted,
                solution_answer=solution.answer,
                solution_error=solution.error,
                solution_meta=solution.meta,
                routed_skill=route.skill,
                extraction_present=run.extraction is not None,
                extraction_stem_ok=extraction_accurate,
                option_map_ok=option_map_ok,
                vision=vision_detail,
                transform_blockers=[str(b.get("code")) for b in
                                    (transform_meta.get("blockers") or [])
                                    if isinstance(b, dict)],
            )
            # R2c：verification_outcome（验证表现）与 root_cause（最初失效环节）分开记录
            outcome = derive_verification_outcome(
                answerability=case.answerability,
                action=verification.action,
                predicted=predicted,
                solution_answer=solution.answer,
                gold=case.answer,
            )
            root_cause = derive_root_cause(
                outcome=outcome,
                failure_class=failure_class,
                capability_status=case.capability_status,
                skill=case.skill,
                solution_error=solution.error,
                solver_rule=(solution.meta or {}).get("rule"),
                routed_skill=route.skill,
            )
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=predicted,
                correct=correct,
                status=solution.status,
                verification_action=verification.action,
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=None if correct else _failure_reason(solution.status, verification.action, solution.error),
                failure_class=failure_class,
                predicted_option=solution.answer,
                tags=list(case.tags),
                routed_skill=route.skill,
                route_source=route.source,
                route_confidence=route.confidence,
                input_mode=mode,
                extraction_accurate=extraction_accurate,
                region_iou=region_iou,
                option_map_ok=option_map_ok,
                run_ref=run_ref,
                vision=vision_detail,
                usage=usage,
                answerability=case.answerability,
                capability_status=case.capability_status,
                root_cause=root_cause,
                verification_outcome=outcome,
                first_detected_error=chain["first_detected_error"],
                blocking_failure=chain["blocking_failure"],
                secondary_errors=chain["secondary_errors"],
            )
        except PipelineError as exc:
            latency = (time.perf_counter() - started) * 1000.0
            detail = compare_extraction_detail(gold_payload, None) if mode == "image_only" else {}
            if mode == "image_only":
                # R4d：异常路径也要写真实三段（HTTP 可能成功但解析抛错——
                # 层级区分正是 EXECUTED 判定的原料），缺省初值防未定义
                detail.update(self._vision_stages(_pre_audit, self._audit(), None, detail))
            perr_outcome = derive_verification_outcome(
                answerability=case.answerability, action="reject",
                predicted=None, solution_answer=None, gold=case.answer,
            )
            perr_chain = attribute_error_chain(
                mode=mode, correct=False, answered=False, vision=detail,
                extraction_stem_ok=False if mode == "image_only" else None,
                transform={}, verification_action="reject", solution_answer=None,
            )
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=None,
                correct=False,
                status="failed",
                verification_action="reject",
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=f"pipeline_error: {exc}",
                failure_class=classify_exception(str(exc)),
                tags=list(case.tags),
                input_mode=mode,
                extraction_accurate=False if mode == "image_only" else None,
                vision=detail,
                answerability=case.answerability,
                capability_status=case.capability_status,
                verification_outcome=perr_outcome,
                root_cause=derive_root_cause(
                    outcome=perr_outcome, failure_class=classify_exception(str(exc)),
                    capability_status=case.capability_status, skill=case.skill, solution_error=str(exc),
                ),
                **perr_chain,
            )
        except Exception as exc:  # noqa: BLE001 - 评测需要记录单题崩溃而不是中断
            latency = (time.perf_counter() - started) * 1000.0
            xerr_class = "pipeline_error"
            xerr_outcome = derive_verification_outcome(
                answerability=case.answerability, action="reject",
                predicted=None, solution_answer=None, gold=case.answer,
            )
            xerr_chain = attribute_error_chain(
                mode=mode, correct=False, answered=False, vision={},
                transform={}, verification_action="reject", solution_answer=None,
            )
            return CaseResult(
                case_id=case.id,
                skill=case.skill,
                expected=case.answer,
                predicted=None,
                correct=False,
                status="failed",
                verification_action="reject",
                latency_ms=round(latency, 3),
                expected_status=case.expected_status,
                failure_reason=f"exception: {type(exc).__name__}: {exc}",
                failure_class=xerr_class,
                tags=list(case.tags),
                input_mode=mode,
                answerability=case.answerability,
                capability_status=case.capability_status,
                verification_outcome=xerr_outcome,
                root_cause=derive_root_cause(
                    outcome=xerr_outcome, failure_class=xerr_class,
                    capability_status=case.capability_status, skill=case.skill, solution_error=str(exc),
                ),
                **xerr_chain,
            )

    @staticmethod
    def _usage_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        if not before or not after:
            return {}
        delta = {key: after.get(key, 0) - before.get(key, 0) for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens")}
        if after.get("model"):
            delta["model"] = after["model"]
        return delta

    def _write_outputs(
        self,
        output_dir: Path,
        split: str,
        cases: List[Case],
        results: List[CaseResult],
        metrics: Dict[str, Any],
        duration: float,
    ) -> None:
        (output_dir / "predictions.jsonl").write_text(
            "\n".join(json.dumps(result.as_dict(), ensure_ascii=False) for result in results) + "\n",
            encoding="utf-8",
        )
        (output_dir / "metrics.json").write_text(
            json.dumps(
                {
                    "split": split,
                    "sample_count": len(cases),
                    "duration_s": round(duration, 3),
                    "settings": self.pipeline.settings.describe(),
                    **metrics,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # R2b：每题独立诊断记录 + 区域叠加可视化（仅 image_only 且有 run 产物）
        self._write_case_records(output_dir, cases, results)
        # R4c：真实调用审计凭证（EXECUTED 判定唯一依据；键与响应正文不落盘）
        audit_fn = getattr(self.pipeline.llm, "audit_snapshot", None)
        if callable(audit_fn):
            audit = audit_fn()
        elif self.pipeline.llm is None:
            audit = {"requests_sent": 0, "requests_succeeded": 0, "http_status_counts": {},
                     "error_types": {}, "echoed_models": [], "base_url_host": None,
                     "vision_model_configured": self.pipeline.settings.llm.vision_model,
                     "mock_or_injected_transport": False, "offline_mode": True,
                     "real_client_present": False,
                     "note": "pipeline 无模型客户端（离线或未配置）：任何 EXECUTED 判定都不成立"}
        else:
            audit = {"requests_sent": 0, "requests_succeeded": 0, "error_types": {},
                     "mock_or_injected_transport": True, "real_client_present": False,
                     "offline_mode": self.pipeline.settings.offline,
                     "injected_client_type": type(self.pipeline.llm).__name__,
                     "note": "注入的是替身客户端（测试 Fake 等），不构成真实调用证据"}
        (output_dir / "llm_evidence.json").write_text(
            json.dumps({
                "audit": audit,
                "usage": self._usage(),
                "eval_mode": metrics.get("eval_mode"),
                "split": split,
                "sample_count": len(cases),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output_dir / "summary.md").write_text(
            self._render_summary(split, len(cases), metrics, duration),
            encoding="utf-8",
        )

    def _write_case_records(self, output_dir: Path, cases: List[Case], results: List[CaseResult]) -> None:
        by_id = {case.id: case for case in cases}
        records_dir = output_dir / "cases"
        overlays_dir = output_dir / "overlays"
        for result in results:
            case = by_id.get(result.case_id)
            if case is None or result.input_mode != "image_only":
                continue
            records_dir.mkdir(parents=True, exist_ok=True)
            record = result.as_dict()
            record["gold"] = {
                "stem": case.stem,
                "options": {option.label: option.text for option in case.options},
                "answer": case.answer,
                "regions": case.regions,
            }
            if result.run_ref:
                record["run_dir"] = str(self.pipeline.settings.runs_dir / result.run_ref)
            (records_dir / f"{case.id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            extraction_path = self.pipeline.settings.runs_dir / (result.run_ref or "") / "extraction.json"
            predicted = None
            if extraction_path.is_file():
                try:
                    predicted = json.loads(extraction_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    predicted = None
            overlays_dir.mkdir(parents=True, exist_ok=True)
            rendered = render_region_overlay(
                str(case.image or ""),
                self._gold_payload(case),
                predicted,
                str(overlays_dir / f"{case.id}.png"),
            )
            if rendered is None:
                (overlays_dir / f"{case.id}.txt").write_text("可视化叠加图生成失败（不影响指标）", encoding="utf-8")


    @staticmethod
    def _render_summary(split: str, sample_count: int, metrics: Dict[str, Any], duration: float) -> str:
        route_mode = metrics.get("route_mode", "unknown")
        oracle_route = route_mode in {"oracle", "oracle_solver"}
        lines = [
            f"# 评测报告 · split={split} · eval_mode={route_mode}",
            "",
            f"- 样本数：{sample_count}",
            f"- 输入模式：{route_mode}（oracle_solver=标注题型+标准题干；auto_router=标准题干+自动路由；image_only=仅原始截图）",
            "",
            "## 核心指标（legacy · R2b expected_status 口径，仅供历史对照；主指标见下方 answerability 口径块）",
            "",
            f"- **答案准确率（legacy）**：{metrics['answer_accuracy']:.2%}（{metrics['answerable']} 道可答题中答对 {int(metrics['answer_accuracy'] * metrics['answerable'])} 道）",
            f"- **可答题覆盖率（legacy）**：{metrics['answerable_coverage']:.2%}（{metrics['covered_count']}/{metrics['answerable']}）",
            f"- **选择性准确率**：{metrics['selective_accuracy']:.2%}（通过验证的答案中正确的比例）",
        ]

        # 路由准确率：oracle 系模式下由标注题型直接给定，标注为 N/A
        if oracle_route:
            lines.append("- **路由准确率**：N/A（oracle_solver 模式直接使用标注题型）")
        else:
            lines.append(f"- **路由准确率**：{metrics['route_accuracy']:.2%}（{metrics['route_correct']}/{metrics['route_total']}）")

        lines.extend([
            "",
            f"- **拒答准确率**：{metrics['abstention_accuracy']:.2%}（{metrics['abstention_correct']}/{metrics['abstention_total']}）",
            f"- 耗时：总 {duration:.2f}s，平均 {metrics['mean_latency_ms']:.1f} ms，"
            f"p50 {metrics['p50_latency_ms']:.1f} ms，p95 {metrics['p95_latency_ms']:.1f} ms",
        ])

        capability = metrics.get("capability") or {}
        if capability:
            def _pct(entry: Any) -> str:
                if not isinstance(entry, dict) or entry.get("value") is None:
                    return "N/A"
                return f"{entry['value']:.2%}（{entry['hit']}/{entry['denominator']}）"

            unl = capability.get("objective_unlabeled") or {}
            lines.extend(
                [
                    "",
                    "## 核心指标（主口径 · answerability 双维度）——客观可答性与能力口径（R2c/R3a，分母=所有有唯一客观答案样本，含未实现题型）",
                    "",
                    f"- **总体答题准确率 overall_answer_accuracy**：{_pct(capability.get('overall_answer_accuracy'))}"
                    "；分母含当前 Skill 未实现但客观有答案的题型",
                    f"- **已支持题型准确率 supported_answer_accuracy**：{_pct(capability.get('supported_answer_accuracy'))}",
                    f"- **能力覆盖 capability_coverage**：{_pct(capability.get('capability_coverage'))}"
                    "（静态：客观可答样本中当前已支持题型的比例）",
                    f"- **作答覆盖 answered_coverage**：{_pct(capability.get('overall_answered_coverage'))}"
                    "（客观可答样本中实际给出通过校验答案的比例）",
                    f"- **诚实弃权（不可判定+主观）**：{_pct(capability.get('honest_abstention'))}；"
                    f"不可判定子集 {_pct(capability.get('undecidable_abstention'))}，"
                    f"主观子集 {_pct(capability.get('subjective_abstention'))}",
                    f"- **有答案未实现而安全拒答（能力缺口，不计入诚实弃权）**："
                    f"{capability.get('unsupported_answerable_refusals', 0)} 题",
                    f"- **无标注客观可答样本（不计入答题分母，单独上报）**："
                    f"{unl.get('total', 0)} 题（其中作答 {unl.get('answered', 0)}）",
                    f"- **验证结果计数**：误放 false_accept={capability.get('false_accept_total', 0)}，"
                    f"误杀 false_reject={capability.get('false_reject_total', 0)}，"
                    f"未验证受控弃权 unverified={capability.get('unverified_answerable_total', 0)}，"
                    f"越权作答 over_answer={capability.get('over_answer_total', 0)}",
                    "",
                    "注：旧 `answer_accuracy`/`abstention_accuracy` 为 R2b 兼容口径"
                    "（分母只含标注 solved；弃权集混入有答案未实现样本），仅供历史对照；"
                    "JSON 输出中 `primary_*` / `capability` 字段为 R3a 主口径，报告一律优先读取。",
                ]
            )

        e2e = metrics.get("e2e") or {}
        if e2e:
            def _fmt(value: Any) -> str:
                if value is None:
                    return "N/A"
                return f"{value:.2%}" if value <= 1.0 else str(value)

            def _scored(key: str) -> str:
                value = e2e.get(key)
                n = e2e.get(f"{key}_scored")
                return f"{_fmt(value)}" + (f"（分母 {n}）" if isinstance(n, int) else "")

            lines.extend(
                [
                    "",
                    "## E2E（image_only）模块级归因指标",
                    "",
                    f"- 仅截图样本数：{e2e.get('cases', 0)}",
                    f"- **Extraction Accuracy**（题干精确解析率）：{_scored('extraction_accuracy')}；题干 CER 见 vision 段",
                    f"- **Region IoU**（题干矩阵，漏检记 0）：{_fmt(e2e.get('region_iou_mean'))}（分母 {e2e.get('region_iou_scored', 0)}）",
                    f"- **Option Mapping Accuracy**（标号序列完全一致）：{_fmt(e2e.get('option_mapping_accuracy'))}",
                    f"- **Image-only Answer Accuracy（可答题口径，R2b）**：{_scored('answer_accuracy')}；分母=有标准答案样本",
                    f"- **Image-only Answerable Coverage**：{_scored('answerable_coverage')}",
                    f"- **Abstention Accuracy（应拒答口径）**：{_scored('abstention_accuracy')}；分母=标注 unsupported/failed 样本",
                    f"- 旧混合口径 E2E（含拒答样本，仅历史对照）：{_fmt(e2e.get('end_to_end_accuracy_mixed'))}",
                ]
            )

        vision = metrics.get("vision") or {}
        if vision:
            lines.extend(["", "## 视觉内容指标（image_only，全部注明分母）", ""])
            for key, entry in vision.items():
                if not isinstance(entry, dict) or "value" not in entry:
                    continue
                value = entry["value"]
                note = f"，{entry['note']}" if entry.get("note") else ""
                shown = f"{value:.4f}" if isinstance(value, float) else "N/A"
                lines.append(f"- **{key}**：{shown}（分母 {entry['denominator']}）{note}")

        failure_classes = metrics.get("failure_classes") or {}
        if failure_classes:
            lines.extend(["", "## 失败归因分布（按最上游失效环节）", ""])
            for key, value in failure_classes.items():
                lines.append(f"- {key}：{value}")

        if capability:
            outcomes = capability.get("verification_outcomes") or {}
            roots = capability.get("root_causes") or {}
            if outcomes:
                lines.extend(["", "## 逐题验证结果（verification_outcome，与 root_cause 正交）", ""])
                for key, value in outcomes.items():
                    lines.append(f"- {key}：{value}")
            if roots:
                lines.extend(["", "## 根因分布（root_cause：最初失效位置）", ""])
                for key, value in roots.items():
                    lines.append(f"- {key}：{value}")

        lines.extend(
            [
                "",
                "## 各题型详情",
                "",
                "| 题型 | 答案准确率 | 可答题覆盖率 | 选择性准确率 | 路由准确率 | 平均耗时(ms) | p95(ms) | unsupported | failed | 主要失败原因 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for skill, stats in metrics["per_skill"].items():
            reasons = ", ".join(f"{key}×{value}" for key, value in stats["failure_reasons"].items()) or "-"
            # 路由准确率：oracle 系模式标注 N/A
            route_acc = "N/A" if oracle_route else f"{stats['route_accuracy']:.2%}"
            lines.append(
                f"| {skill} | {stats['answer_accuracy']:.2%} | {stats['answerable_coverage']:.2%} | "
                f"{stats['selective_accuracy']:.2%} | {route_acc} | "
                f"{stats['mean_latency_ms']:.1f} | {stats['p95_latency_ms']:.1f} | "
                f"{stats['unsupported']} | {stats['failed']} | {reasons} |"
            )
        lines.append("")
        return "\n".join(lines)
