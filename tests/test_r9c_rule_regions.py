# -*- coding: utf-8 -*-
"""R9-C：图形面数算子 rule_regions（封闭区域/holes 外推）五类样本验收。"""
from __future__ import annotations

import numpy as np
import pytest

from assessment_agent.tools.figure_rules import rule_regions
from assessment_agent.tools.figure_tools import cell_features


def _f(holes: int, shapes: int = 1):
    return {"holes": holes, "shape_count": shapes}


def _opts(pairs):
    return [(lab, _f(h)) for lab, h in pairs]


# 类1 正常：等差面数 1,2,3 → 4，唯一匹配
def test_normal_arithmetic_regions():
    hit = rule_regions([_f(1), _f(2), _f(3)], _opts([("A", 4), ("B", 2), ("C", 1), ("D", 5)]))
    assert hit is not None and hit.rule == "regions" and hit.label == "A"
    assert hit.predicted["holes"] == 4


# 类1b 政策边界：恒定面数**不主张**（与 figure_verify 口径一致：面数在叠加/
# 平移规律下非不变量，恒定外推属弱一致性，禁止借它自证答案）
def test_constant_regions_not_claimed():
    assert rule_regions([_f(2), _f(2), _f(2)], _opts([("A", 1), ("B", 2), ("C", 3), ("D", 0)])) is None


# 类2 反向边界：从零面起始的等差 0,1,2 → 3 合法
def test_reverse_boundary_zero_start():
    hit = rule_regions([_f(0), _f(1), _f(2)], _opts([("A", 3), ("B", 1), ("C", 2), ("D", 0)]))
    assert hit is not None and hit.label == "A" and hit.predicted["holes"] == 3


# 类3 典型错误：多选项同分必须报不可唯一，不得挑一个
def test_typical_wrong_multiple_match():
    hit = rule_regions([_f(1), _f(2), _f(3)], _opts([("A", 4), ("B", 4), ("C", 1), ("D", 5)]))
    assert hit is not None and hit.label is None
    assert "无法唯一确定" in hit.detail


# 类3b 非等差序列不适用（不外推噪声）
def test_typical_wrong_non_arithmetic():
    assert rule_regions([_f(1), _f(3), _f(2)], _opts([("A", 4), ("B", 1)])) is None


# 类4 不足条件：特征面缺 holes 键 → 规则弃权（None），不是崩溃也不是猜测
def test_insufficient_missing_feature_key():
    assert rule_regions([{"shape_count": 1}, {"shape_count": 2}],
                        [("A", {"shape_count": 3})]) is None


# 类5b advisory 政策回归（screens 教训）：面数碰巧等差的 count 题，
# 正解（满足 count、违反面数）不得被证伪——regions 约束必须是仅确证项
def test_regions_is_advisory_only():
    from assessment_agent.tools.figure_verify import _sequence_constraints

    def _full(holes, shapes):
        return {"holes": holes, "shape_count": shapes, "sides": 0,
                "ink_pixels": 0.5, "centroid": (0.5, 0.5),
                "vertical_symmetric": True, "horizontal_symmetric": True}

    stem = [_full(0, 1), _full(1, 2), _full(2, 3)]        # 个数与面数同为等差
    cons = _sequence_constraints(stem)
    kinds = {c["kind"]: c for c in cons}
    assert "count" in kinds and "regions" in kinds
    assert kinds["regions"].get("advisory") is True
    assert not kinds["count"].get("advisory")
    chosen = _full(0, 4)                                   # count 正解但面数不符
    violated = [c for c in cons if not c.get("advisory") and not c["test"](chosen)]
    assert violated == []
    assert not kinds["regions"]["test"](chosen)            # 若未豁免将被误证伪


# 类5c solver 主张 regions 时 verify 可独立确证（advisory 仅作确证用）
def test_regions_claim_confirmable():
    from assessment_agent.tools.figure_verify import _RULE_CONSTRAINT
    assert _RULE_CONSTRAINT.get("regions") == "regions"


# 类5 独立复核：几何掩膜 → cell_features.holes 与人工欧拉面数一致
def test_independent_euler_ring():
    m = np.zeros((70, 70), dtype=bool)
    m[10:60, 10:60] = True
    m[20:50, 20:50] = False          # 环形：内部一个封闭空腔
    feats = cell_features(m)
    assert feats["holes"] == 1


def test_independent_two_holes_shape():
    m = np.zeros((70, 90), dtype=bool)
    m[:, :] = False
    # “日”字：外框 + 中横 → 2 个封闭区域
    m[10:60, 10:80] = True
    m[13:57, 13:77] = False
    m[10:60, 10:13] = True; m[10:60, 77:80] = True
    m[10:13, 10:80] = True; m[57:60, 10:80] = True
    m[33:37, 10:80] = True
    feats = cell_features(m)
    assert feats["holes"] == 2
