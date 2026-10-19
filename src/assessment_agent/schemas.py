"""全局结构化数据模型（所有组件间传递的都是这些 Pydantic 对象）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "q") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class QuestionOption(BaseModel):
    label: str
    text: str = ""

    def as_line(self) -> str:
        return f"{self.label}. {self.text}".strip()


class Question(BaseModel):
    """一道题目的规范化表示。"""

    id: str = Field(default_factory=lambda: new_id("q"))
    stem: str = ""
    options: List[QuestionOption] = Field(default_factory=list)
    skill: Optional[str] = None
    question_type: str = "single_choice"
    image_path: Optional[str] = None
    source: str = "text"
    meta: Dict[str, Any] = Field(default_factory=dict)

    def option_labels(self) -> List[str]:
        return [option.label for option in self.options]

    def option_text(self, label: str) -> Optional[str]:
        for option in self.options:
            if option.label == label:
                return option.text
        return None

    def render(self) -> str:
        lines = [self.stem.strip()]
        lines.extend(option.as_line() for option in self.options)
        return "\n".join(line for line in lines if line)


class ToolInvocation(BaseModel):
    """一次工具调用的可追溯记录。"""

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    result: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


class Solution(BaseModel):
    status: Literal["solved", "unsupported", "failed"] = "failed"
    skill: str = "unknown"
    answer: Optional[str] = None
    answer_text: Optional[str] = None
    steps: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = ""
    tool_calls: List[ToolInvocation] = Field(default_factory=list)
    error: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

    def to_llm_payload(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "skill": self.skill,
            "answer": self.answer,
            "answer_text": self.answer_text,
            "steps": self.steps,
            "confidence": self.confidence,
            "method": self.method,
        }


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["info", "warning", "blocking"] = "blocking"


class Verification(BaseModel):
    passed: bool = False
    action: Literal["accept", "repair", "reject", "unverified"] = "reject"
    checks: List[VerificationCheck] = Field(default_factory=list)
    final_answer: Optional[str] = None
    repaired_answer: Optional[str] = None
    notes: str = ""
    duration_ms: float = 0.0

    def failing(self) -> List[VerificationCheck]:
        return [check for check in self.checks if not check.passed]


class RouteDecision(BaseModel):
    skill: str
    confidence: float = 0.0
    source: Literal["explicit", "rules", "structure", "llm", "fallback"] = "rules"
    scores: Dict[str, float] = Field(default_factory=dict)
    ambiguous: bool = False
    # R2c：区分“无法归类”（classified=False → unknown）与“归类到当前未实现题型”
    classified: bool = True
    notes: str = ""


class DiagramGrid(BaseModel):
    """图形矩阵的行列标注（VLM 给出的布局提示，可空，由适配器自动推断兜底）。"""

    rows: int = Field(ge=1, le=12)
    cols: int = Field(ge=1, le=12)


class TextRegion(BaseModel):
    """题干文字区域。bbox 为原图像素坐标 [x, y, w, h]，左上角原点。"""

    region_id: str
    bbox: List[int] = Field(min_length=4, max_length=4)
    text: str = ""


class DiagramRegion(BaseModel):
    """题干图形区域（矩阵 / 单图）。crop_path 由 VisionParser 裁剪后回填。"""

    region_id: str
    role: Literal["stem_matrix", "stem_figure", "option_figure", "other"] = "stem_figure"
    bbox: List[int] = Field(min_length=4, max_length=4)
    grid: Optional[DiagramGrid] = None
    crop_path: Optional[str] = None


class OptionRegion(BaseModel):
    """选项区域：标号 + 文本 + 可选图形子区域（不含 A. 标号文字）。"""

    label: str
    bbox: List[int] = Field(min_length=4, max_length=4)
    text: str = ""
    has_figure: bool = False
    figure_bbox: Optional[List[int]] = None
    crop_path: Optional[str] = None


class TableRegion(BaseModel):
    """表格 / 图表区域（本轮只记录结构与文本，不做求解）。"""

    region_id: str
    bbox: List[int] = Field(min_length=4, max_length=4)
    rows: int = 0
    cols: int = 0
    cells: List[List[str]] = Field(default_factory=list)


class DataOperand(BaseModel):
    """数据计算计划的一个操作数：必须携带来源单元格的原始证据（R3a 资料分析）。"""

    name: str = "O1"                 # 计划内引用名
    table_ref: int = 0               # 来源表序号
    row: Optional[int] = None        # 归一化后的源行
    col: Optional[int] = None        # 归一化后的源列
    row_label: str = ""              # 行头（统计对象/时间）
    col_label: str = ""              # 列头（指标+时间）
    raw_text: str = ""               # 源单元格原始文本（图像证据保留）
    value: str = ""                  # 归一化数值（可被 Fraction 重解析的字符串）
    unit: str = ""                   # 源单元格单位（万元/亿元/%/百分点/…）
    kind: str = "number"             # money | quantity | percent | ppt | number | label
    source_bbox: Optional[List[int]] = None


class DataCalculationPlan(BaseModel):
    """可复用的数据计算计划：LLM 只允许输出该结构，计算由确定性求值器执行。"""

    operation: str                   # sum/mean/max/min/rank/growth_yoy/growth_mom/…
    operands: List[DataOperand] = Field(default_factory=list)
    expression: str = ""             # 以操作数名组合的表达式，如 (O2 - O1) / O1 * 100
    intermediate_values: List[Dict[str, str]] = Field(default_factory=list)
    result_unit: str = ""
    result_kind: str = "number"      # number | percent | ppt
    answer_kind: str = "value"       # value | label（排序/排位类返回行列标签）
    assumptions: List[str] = Field(default_factory=list)
    question_focus: str = ""         # 计划实际解析到的题干片段（题意核对用）


class VerbalEvidence(BaseModel):
    """言语推理的证据引用：必须对应原文（题干/材料）中的有效字符区间。"""

    option: str = ""                     # 关联选项标号（空=材料级证据）
    start: int = -1                      # 原文字符偏移（含）
    end: int = -1                        # 原文字符偏移（不含）
    text: str = ""                       # 引用片段原文（verify 逐字核对 stem[start:end]）
    role: str = "support"                # support | against | absence | conclusion_anchor


class PropositionRecord(BaseModel):
    """R3c 命题级中间表示：把"谁以何种态度陈述 P"与 P 本身分开。

    核心纪律：``reported(P) ⊬ P``、``believed(P) ⊬ P``、``hypothetical(P) ⊬ P``；
    否认的是归属谓词时，既不得推出 P，也不得推出 ¬P。
    """

    proposition: str = ""                       # 归属谓词作用的核心内容（P，归一化文本）
    attribution: str = ""                       # 归属主体/谓词证据（"有报道称"…），空=原文直接陈述
    epistemic_status: str = "asserted"          # asserted|reported|believed|hypothetical|uncertain|denied_attribution
    polarity: int = 0                           # 内容否定奇偶（0 肯定 / 1 否定；双否折叠≈0）
    modality: int = 2                           # 1=可能级 2=中性断言 3=必然级
    quantifier: Optional[float] = None          # 显式量词强度（None=无显式量词）
    evidence_span: Optional[VerbalEvidence] = None
    nested_markers: List[str] = Field(default_factory=list)   # 嵌套归属/否认标记（如"报道称…否认…"内层）


class VerbalOptionAnalysis(BaseModel):
    """逐选项分析（LLM 与规则核共用同一结构；缺分析的选项不允许被接受）。"""

    label: str
    option_text: str = ""
    relation: str = "unanalyzable"       # supported/contradicted/overreach/absent/insufficient/
                                         # covers/misses/collocation_hit/collocation_conflict/
                                         # strengthens/weakens/neutral/not_formalizable
    definitive: bool = False             # 该判断是否由确定性检查得出
    evidence: List[VerbalEvidence] = Field(default_factory=list)
    cue_diffs: List[str] = Field(default_factory=list)   # 否定/量词/情态差异记录
    new_terms: List[str] = Field(default_factory=list)   # 选项中无法在原文定位的内容词
    epistemic: str = ""                  # R3c: 证据status→选项status（归属核对结果）
    notes: str = ""


class VerbalReasoningPlan(BaseModel):
    """R3b 言语理解计划：答案只能由证据+规则/审计得出，模型不得只交字母。"""

    task_type: str                       # cloze | main_idea | inference | argument
    question_target: str = ""            # 解析到的提问句（题意锚点）
    negative_target: bool = False        # "无法推出/削弱/不恰当" 类反向提问
    passage_spans: List[VerbalEvidence] = Field(default_factory=list)
    premises: List[str] = Field(default_factory=list)
    conclusion: str = ""
    propositions: List[PropositionRecord] = Field(default_factory=list)   # R3c 证据句命题层
    option_analyses: List[VerbalOptionAnalysis] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    proposed_answer: str = ""
    confidence: float = 0.0
    uncertainty_reasons: List[str] = Field(default_factory=list)
    resolution: str = "rule"             # rule | llm_audited
    assumptions: List[str] = Field(default_factory=list)


class RelationEvidence(BaseModel):
    """R3c 关系词库证据：一次关系判定所用的词典条目引用。"""

    word_a: str = ""
    word_b: str = ""
    field: str = ""                       # hyper / part_of / made_of / function / place / tool / act / cause / syn / ant / def
    source_text: str = ""                 # 人工释义原文（verify 对账词典现存内容）
    direction: str = ""                   # a->b | b->a


class LanguageRelationOption(BaseModel):
    label: str
    pair: List[str] = Field(default_factory=list)          # [c1, c2]
    matched: List[str] = Field(default_factory=list)       # 与题干签名同向一致的关系标签
    own_relations: List[str] = Field(default_factory=list) # 该词对全部可判定关系
    missing: List[str] = Field(default_factory=list)       # 题干签名中该选项缺失的关系
    verdict: str = "unknown"                # match | partial | contradicted | mismatch | unknown_pair
    notes: str = ""


class LanguageRelationPlan(BaseModel):
    """R3c 语言推理计划：关系标签必须逐条绑定词典证据，方向显式。"""

    task_type: str                       # analogy | synonym | antonym | en_analogy
    question_target: str = ""
    stem_pair: List[str] = Field(default_factory=list)
    relation_signature: List[str] = Field(default_factory=list)   # 含方向的关系标签集
    evidence: List[RelationEvidence] = Field(default_factory=list)
    option_analyses: List[LanguageRelationOption] = Field(default_factory=list)
    unresolved_competitors: List[str] = Field(default_factory=list)  # R4a: 词典无法排除的选项
    conflicts: List[str] = Field(default_factory=list)
    proposed_answer: str = ""
    confidence: float = 0.0
    uncertainty_reasons: List[str] = Field(default_factory=list)
    resolution: str = "rule"             # rule | llm_audited
    assumptions: List[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """题图抽取结果（视觉模型 -> 结构化字段）。

    ``schema_version=1``：旧版纯文本抽取（stem/options/confidence），保持兼容；
    ``schema_version=2``：VisionParser 区域化抽取，附带像素坐标区域、阅读顺序与裁剪证据。
    bbox 统一为原图像素坐标 ``[x, y, w, h]``（左上角原点，x+w ≤ 图像宽，y+h ≤ 图像高）。
    """

    stem: str = ""
    options: List[QuestionOption] = Field(default_factory=list)
    question_type: str = "single_choice"
    skill_hint: Optional[str] = None
    has_diagram: bool = False
    raw_text: str = ""
    model: Optional[str] = None
    confidence: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    # ---- schema v2：视觉结构化区域（全部可空，兼容旧 JSON）----
    schema_version: int = 1
    bbox_format: str = "pixel_xywh"
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    text_regions: List[TextRegion] = Field(default_factory=list)
    diagram_regions: List[DiagramRegion] = Field(default_factory=list)
    option_regions: List[OptionRegion] = Field(default_factory=list)
    table_regions: List[TableRegion] = Field(default_factory=list)
    reading_order: List[str] = Field(default_factory=list)
    parser: Optional[str] = None

    @property
    def has_regions(self) -> bool:
        return self.schema_version >= 2 and bool(
            self.diagram_regions or self.option_regions or self.table_regions
        )

    def vision_meta(self) -> Dict[str, Any]:
        """注入 ``Question.meta["vision"]`` 的精简结构（供 Skill / 适配器消费）。

        R4g 注：单数 ``stem_matrix_*`` 字段只是**默认兼容视图**（首个矩阵或
        区域）；完整的 ``diagram_regions`` 列表同样在此输出中保留。适配器
        （tools/diagram_layout）必须消费完整列表——多 ``stem_figure`` 序列题
        只取第一区域会丢弃已检测图形，这是 R4f 定位的 P0 接口缺陷。
        """
        if not self.has_regions:
            return {}
        stem_matrix = next(
            (region for region in self.diagram_regions if region.role == "stem_matrix"),
            self.diagram_regions[0] if self.diagram_regions else None,
        )
        return {
            "bbox_format": self.bbox_format,
            "image_size": [self.image_width, self.image_height],
            "stem_matrix_bbox": stem_matrix.bbox if stem_matrix else None,
            "stem_matrix_grid": (
                [stem_matrix.grid.rows, stem_matrix.grid.cols] if stem_matrix and stem_matrix.grid else None
            ),
            "stem_matrix_crop": stem_matrix.crop_path if stem_matrix else None,
            "diagram_regions": [region.model_dump(mode="json") for region in self.diagram_regions],
            "option_regions": [region.model_dump(mode="json") for region in self.option_regions],
            "table_regions": [region.model_dump(mode="json") for region in self.table_regions],
            "text_regions": [region.model_dump(mode="json") for region in self.text_regions],
            "reading_order": self.reading_order,
            "parser": self.parser,
            "confidence": self.confidence,
        }


class RunResult(BaseModel):
    """一次端到端运行的完整结果与产物路径。"""

    run_id: str
    run_dir: str
    question: Question
    route: RouteDecision
    solution: Solution
    verification: Verification
    extraction: Optional[ExtractionResult] = None
    latency_ms: Dict[str, float] = Field(default_factory=dict)
    artifacts: Dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
