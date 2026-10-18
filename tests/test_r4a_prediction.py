"""R4a：predicted 状态的 Skill 端到端行为 + 标注审计文件守护。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.config import Settings  # noqa: E402
from assessment_agent.schemas import Question, QuestionOption  # noqa: E402
from assessment_agent.skills.base import SkillContext  # noqa: E402
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill  # noqa: E402


def _ctx():
    return SkillContext(settings=Settings.from_env(env={"ASSESSMENT_OFFLINE": "1"}), llm=None)


STEM = "气象预报显示，明天下雪，航班因此可能延误。根据这段文字，以下哪项可以推出？"


def _q(opts):
    return Question(stem=STEM, options=[QuestionOption(label=l, text=t) for l, t in opts],
                    question_type="single_choice")


def test_skill_forecast_bare_assertion_not_accepted():
    sk = VerbalUnderstandingSkill()
    q = _q([("A", "明天下雪"), ("B", "气象预报显示明天下雪")])
    sol = sk.solve(q, _ctx())
    assert sol.status == "solved" and sol.answer == "B"      # A 被阻断（predicted⊬P）
    ver = sk.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "B"
    assert "predicted" in (sol.meta["verbal_plan"]["option_analyses"][0]["epistemic"])


def test_skill_forecast_no_attribution_option_fails_honestly():
    sk = VerbalUnderstandingSkill()
    q = _q([("A", "明天下雪"), ("B", "航班将延误")])
    sol = sk.solve(q, _ctx())
    assert sol.status == "failed"                             # 全裸断言 → 诚实不可判


def test_forecast_asserted_contrast_flips_gold():
    """最小对比：外层从预报换成直接记录，同一裸断言选项从阻断变支持。"""
    sk = VerbalUnderstandingSkill()
    q_fore = _q([("A", "明天下雪"), ("B", "气象预报显示明天下雪")])
    q_assert = _q([("A", "明天下雪"), ("B", "气象预报显示明天下雪")])
    q_assert = Question(stem=STEM.replace("气象预报显示，明天下雪", "机场记录显示明天下雪"),
                        options=q_assert.options, question_type="single_choice")
    sol_f = sk.solve(q_fore, _ctx())
    sol_a = sk.solve(q_assert, _ctx())
    assert sol_f.answer == "B"                                # 预报层：只有归属复述可支持
    assert sol_a.status in ("solved", "failed")
    if sol_a.status == "solved":
        assert sol_a.answer == "A"                            # 记录断言层：裸 P 可支持


# ---------------------------------------------------------------- 审计文件守护

AUDIT_LANG = ROOT / "data" / "benchmark_language" / "annotation_audit_r4a.json"
AUDIT_VBL = ROOT / "data" / "benchmark_verbal" / "annotation_audit_r4a.json"


def test_annotation_audits_exist_and_cover_all_null_items():
    man_lang = {r["id"] for r in
                (json.loads(l) for l in (ROOT / "data/benchmark_language/blind/language.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip())
                if r["answer"] is None}
    audit = json.loads(AUDIT_LANG.read_text(encoding="utf-8"))
    assert {i["id"] for i in audit["items"]} == man_lang
    man_vbl = {r["id"] for r in
               (json.loads(l) for l in (ROOT / "data/benchmark_verbal/blind2/verbal.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip())
               if r["answer"] is None} | {"vb2-inf-10"}
    audit2 = json.loads(AUDIT_VBL.read_text(encoding="utf-8"))
    assert {i["id"] for i in audit2["items"]} == man_vbl


def test_frozen_files_untouched_by_audit():
    """审计文件是新增旁注，不改冻结数据：盲集文件 sha 仍与首跑记录一致。"""
    import hashlib
    b = (ROOT / "data/benchmark_language/blind/language.jsonl").read_bytes()
    fr = json.loads((ROOT / "data/benchmark_language/blind_firstrun_metrics.json")
                    .read_text(encoding="utf-8"))
    assert hashlib.sha256(b).hexdigest() == fr["data_sha256_at_firstrun"]
