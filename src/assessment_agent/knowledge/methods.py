"""R7 阶段A/D：方法知识库（结构化方法卡 + 轻量可复现检索）。

与既有 ``KnowledgeBase``（skill→Markdown 说明注入）互补：
- ``knowledge/methods/*.json``：**可被 Solver 调用**的结构化方法卡（A3 字段规范）；
- ``knowledge/sources.yaml``：教材/资料来源登记（名称/URL/访问日期/许可/用途边界）；
- 检索先使用结构化标签（skill/problem_family/concepts/applicability 关键词）
  与 BM25-lite（词频+长度归一，纯 python 可复现，无外部依赖/无向量库）。

纪律（评审工单 §六）：
- 方法卡只含**通用方法与易错点**，不得含评测集题目答案或逐题错误报告；
- 教材学习资料 / 方法知识库 / 开发练习集 / 冻结评测集 四类目录严格分离；
- 每张卡必须有 source_references（无合法来源的方法不允许入库）。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# A3 规范：方法卡必填字段
REQUIRED_FIELDS = (
    "method_id", "skill", "problem_family", "concepts",
    "applicability_conditions", "required_quantities", "reasoning_operations",
    "solution_procedure", "verification_strategy", "common_pitfalls",
    "source_references", "version",
)


@dataclass
class MethodCard:
    method_id: str
    skill: str
    problem_family: str
    concepts: List[str]
    applicability_conditions: List[str]
    required_quantities: List[str]
    reasoning_operations: List[str]
    solution_procedure: List[str]
    verification_strategy: List[str]
    common_pitfalls: List[str]
    source_references: List[Dict[str, str]]
    version: str
    executable: Optional[Dict[str, Any]] = None   # 可选：可执行算子描述（模板/依赖图）
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.raw

    def prompt_block(self, max_chars: int = 900) -> str:
        """给 ReasoningPlanner 的紧凑方法提示（只含方法与边界，不含答案）。"""
        lines = [
            f"方法 {self.method_id}（{self.problem_family} v{self.version}）",
            "适用: " + "；".join(self.applicability_conditions[:4]),
            "需要量: " + "、".join(self.required_quantities[:6]),
            "步骤: " + " → ".join(self.solution_procedure[:6]),
            "验证: " + "；".join(self.verification_strategy[:3]),
            "易错: " + "；".join(self.common_pitfalls[:3]),
        ]
        return "\n".join(lines)[:max_chars]


def _tokenize(text: str) -> List[str]:
    """中英混合轻分词：CJK 单字 + bigram，西文按词。可复现、无依赖。"""
    toks: List[str] = []
    for seg in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text or ""):
        if re.match(r"[a-zA-Z0-9]", seg):
            toks.append(seg.lower())
        else:
            toks.extend(list(seg))
            toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


class MethodKB:
    def __init__(self, root: Optional[Path] = None):
        base = Path(root) if root is not None else Path(__file__).resolve().parents[2] / "knowledge"
        self.methods_dir = base / "methods"
        self.sources_file = base / "sources.yaml"
        self._cards: Optional[Dict[str, MethodCard]] = None

    # ---------- 加载与完整性 ----------
    def cards(self) -> Dict[str, MethodCard]:
        if self._cards is None:
            self._cards = {}
            if self.methods_dir.is_dir():
                for path in sorted(self.methods_dir.glob("*.json")):
                    card = self.load_card_file(path)
                    self._cards[card.method_id] = card
        return self._cards

    @staticmethod
    def load_card_file(path: Path) -> MethodCard:
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_FIELDS if k not in data or data[k] in (None, "", [])]
        if missing:
            raise ValueError(f"方法卡 {path.name} 缺必填字段: {missing}")
        refs = data["source_references"]
        if not all(isinstance(r, dict) and r.get("title") and r.get("url") for r in refs):
            raise ValueError(f"方法卡 {path.name} source_references 必须含 title+url（合法来源，无来源不入库）")
        return MethodCard(**{k: data[k] for k in (
            "method_id", "skill", "problem_family", "concepts", "applicability_conditions",
            "required_quantities", "reasoning_operations", "solution_procedure",
            "verification_strategy", "common_pitfalls", "source_references", "version")},
            executable=data.get("executable"), raw=data)

    def validate_all(self) -> List[str]:
        """返回错误列表（空=全部方法卡符合 A3 规范）。"""
        errors: List[str] = []
        if not self.methods_dir.is_dir():
            return ["methods/ 目录不存在"]
        for path in sorted(self.methods_dir.glob("*.json")):
            try:
                self.load_card_file(path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path.name}: {exc}")
        return errors

    # ---------- 检索（结构化标签 + BM25-lite）----------
    def _card_text(self, c: MethodCard) -> str:
        parts = [c.method_id, c.skill, c.problem_family]
        parts += c.concepts + c.applicability_conditions + c.required_quantities
        parts += c.reasoning_operations + c.common_pitfalls
        return " ".join(str(p) for p in parts)

    def search(self, query: str, skill: Optional[str] = None,
               top_k: int = 3) -> List[MethodCard]:
        cards = [c for c in self.cards().values() if skill is None or c.skill == skill]
        if not cards:
            return []
        q = Counter(_tokenize(query))
        docs = [Counter(_tokenize(self._card_text(c))) for c in cards]
        n = len(docs)
        mean_len = sum(sum(d.values()) for d in docs) / n
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        scores = []
        k1, b = 1.5, 0.75
        for c, d in zip(cards, docs):
            dl = sum(d.values())
            s = 0.0
            for term, qf in q.items():
                tf = d.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / mean_len))
            scores.append((s, c))
        scores.sort(key=lambda x: (-x[0], x[1].method_id))
        picked = [c for s, c in scores if s > 0][:top_k]
        if not picked and skill:  # 无命中时按 skill 兜底（规划层自行判断是否可用）
            picked = cards[:top_k]
        return picked

    def for_skill(self, skill: str) -> List[MethodCard]:
        return [c for c in self.cards().values() if c.skill == skill]
