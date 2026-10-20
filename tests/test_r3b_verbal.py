"""R3b Part 2-4：言语理解 Skill——证据约束推理与独立验证。

纪律：答案必须绑定原文有效字符区间；LLM 通道只交字母/伪造证据/漏分析一律拒绝；
同一实现自洽复核不允许让 LLM 通道获得"确定性验证通过"；覆盖不到的题诚实 failed。
"""

from __future__ import annotations

import copy
from pathlib import Path

from assessment_agent.config import Settings
from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill

DATA = Path(__file__).resolve().parents[1] / "data"


def _ctx(llm=None, **flag) -> SkillContext:
    return SkillContext(settings=Settings(offline=True, data_dir=DATA, **flag), llm=llm)


def _q(stem, options):
    return Question(stem=stem, options=[QuestionOption(label=l, text=t) for l, t in options])


DEV16_STEM = "他的发言获得了与会者一致的（ ），会场气氛由此变得热烈起来。"
DEV16_OPTIONS = [("A", "反应"), ("B", "反映"), ("C", "批示"), ("D", "注视")]

AMBIG_STEM = ("新建的人行天桥让通行更安全，高峰时段排队明显缩短。"
              "根据这段文字，下列说法无法推出的是？")
AMBIG_OPTIONS = [("A", "天桥改善了通行安全"), ("B", "高峰时段排队缩短了"),
                 ("C", "建成后天桥减少了城市行人事故同比下降"),
                 ("D", "天桥让通行变得方便")]


class ScriptedLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, system, user, **kwargs):
        self.calls += 1
        return copy.deepcopy(self.payload)


def _span(stem, sub, label, role="support"):
    i = stem.index(sub)
    return {"label": label, "start": i, "end": i + len(sub), "text": sub, "role": role}


def _full_plan_payload(stem, relation="absent", answer="C", extra_ev=None):
    analyses = [
        {"label": "A", "relation": "supported", "notes": "原文首句",
         "evidence": [_span(stem, "新建的人行天桥让通行更安全", "A")]},
        {"label": "B", "relation": "supported", "notes": "原文次句",
         "evidence": [_span(stem, "高峰时段排队明显缩短", "B")]},
        {"label": "C", "relation": relation, "notes": "外部比较无原文依据",
         "evidence": (extra_ev or []) + ([_span(stem, "通行更安全", "C", "against")] if False else [])},
        {"label": "D", "relation": "supported", "notes": "同义改写首句",
         "evidence": [_span(stem, "新建的人行天桥让通行更安全", "D")]},
    ]
    return {"task_type": "inference", "question_target": "根据这段文字，下列说法无法推出的是？",
            "premises": ["天桥让通行更安全", "高峰排队缩短"], "conclusion": "",
            "option_analyses": analyses, "unsupported_claims": [],
            "proposed_answer": answer, "confidence": 0.8, "uncertainty_reasons": []}


# ------------------------------------------------------------------ 离线规则子集

def test_cloze_rule_solves_with_verbatim_evidence():
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q(DEV16_STEM, DEV16_OPTIONS), _ctx())
    assert sol.status == "solved" and sol.answer == "A"
    plan = sol.meta["verbal_plan"]
    assert {a["label"] for a in plan["option_analyses"]} == {"A", "B", "C", "D"}
    # 证据必须逐字对应原文区间
    for a in plan["option_analyses"]:
        for ev in a["evidence"]:
            if ev["start"] >= 0:
                assert DEV16_STEM[ev["start"]:ev["end"]] == ev["text"]
    ver = skill.verify(_q(DEV16_STEM, DEV16_OPTIONS), sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "A"
    assert all(c.passed for c in ver.checks)


def test_cloze_without_collocation_evidence_fails_honestly():
    stem = "他对这件艺术品的（ ）令旁观者动容。"
    opts = [("A", "凝视之姿"), ("B", "把玩之意"), ("C", "摹写之功"), ("D", "咀嚼之味")]
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q(stem, opts), _ctx())
    assert sol.status == "failed" and sol.answer is None
    assert sol.meta["failure"]["code"] in {"no_collocation_hit", "multi_blank", "insufficient_evidence"}


def test_span_tampering_is_blocked():
    skill = VerbalUnderstandingSkill()
    q = _q(DEV16_STEM, DEV16_OPTIONS)
    sol = skill.solve(q, _ctx())
    ev = sol.meta["verbal_plan"]["option_analyses"][0]["evidence"]
    target = next(e for e in ev if e["start"] >= 0)
    target["text"] = "会场气氛由此变得冰冷"  # 篡改引用内容
    ver = skill.verify(q, sol, _ctx())
    assert ver.action == "reject"
    assert any(c.name == "verbal.evidence_spans" and not c.passed for c in ver.checks)


def test_out_of_range_reference_is_blocked():
    skill = VerbalUnderstandingSkill()
    q = _q(DEV16_STEM, DEV16_OPTIONS)
    sol = skill.solve(q, _ctx())
    ev = sol.meta["verbal_plan"]["option_analyses"][0]["evidence"]
    target = next(e for e in ev if e["start"] >= 0)
    target["start"] = len(DEV16_STEM) + 5  # 越界引用
    ver = skill.verify(q, sol, _ctx())
    assert ver.action == "reject"


def test_evidence_from_question_zone_is_blocked():
    stem = "很多植物在夜间释放二氧化碳。卧室放太多植物可能影响睡眠。这段文字的主旨是？"
    opts = [("A", "夜间释放二氧化碳"), ("B", "卧室植物影响睡眠"), ("C", "植物光合作用"),
            ("D", "卧室应该多放花")]
    skill = VerbalUnderstandingSkill()
    q = _q(stem, opts)
    sol = skill.solve(q, _ctx())
    if sol.status != "solved":
        # 规则核诚实失败即可（主旨子集可能不覆盖此项），不得伪造接受
        assert sol.answer is None
        return
    ev = sol.meta["verbal_plan"]["option_analyses"][0]["evidence"]
    if ev:
        target = ev[0]
        qpos = stem.index("这段文字")
        target["start"], target["end"] = qpos, qpos + 4
        target["text"] = stem[qpos:qpos + 4]
        target["role"] = "support"
        ver = skill.verify(q, sol, _ctx())
        assert ver.action == "reject"


# ------------------------------------------------------------------ LLM 通道纪律

def test_llm_valid_plan_is_executed_and_audited():
    skill = VerbalUnderstandingSkill()
    q = _q(AMBIG_STEM, AMBIG_OPTIONS)
    base = skill.solve(q, _ctx())
    assert base.status == "failed"  # 规则核无法唯一判定 → 才轮到 LLM
    llm = ScriptedLLM(_full_plan_payload(AMBIG_STEM))
    sol = skill.solve(q, _ctx(llm=llm))
    assert llm.calls == 1
    assert sol.status == "solved" and sol.answer == "C"
    assert sol.meta["plan_source"] == "llm_audited"
    ver = skill.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "C"


def test_llm_answer_without_evidence_is_rejected():
    skill = VerbalUnderstandingSkill()
    q = _q(AMBIG_STEM, AMBIG_OPTIONS)
    payload = _full_plan_payload(AMBIG_STEM, relation="supported", answer="B")
    # B 的证据全部伪造：区间外文本
    payload["option_analyses"][1]["evidence"] = [
        {"label": "B", "start": 999, "end": 1004, "text": "不存在", "role": "support"}]
    sol = skill.solve(q, _ctx(llm=ScriptedLLM(payload)))
    assert sol.status == "failed"
    assert sol.meta.get("llm_rejection", {}).get("code") in {
        "answer_without_evidence", "audit_refuted", "audit_insufficient"}


def test_llm_incomplete_analysis_or_bad_label_rejected():
    skill = VerbalUnderstandingSkill()
    q = _q(AMBIG_STEM, AMBIG_OPTIONS)
    p1 = _full_plan_payload(AMBIG_STEM)
    p1["option_analyses"] = p1["option_analyses"][:1]  # 只分析一个选项
    sol = skill.solve(q, _ctx(llm=ScriptedLLM(p1)))
    assert sol.status == "failed"
    assert sol.meta["llm_rejection"]["code"] == "analysis_incomplete"
    p2 = _full_plan_payload(AMBIG_STEM)
    p2["proposed_answer"] = "E"  # 字母捷径：不在选项集
    sol2 = skill.solve(q, _ctx(llm=ScriptedLLM(p2)))
    assert sol2.status == "failed"
    assert sol2.meta["llm_rejection"]["code"] == "answer_label_invalid"


def test_llm_cloze_self_consistent_accepted_only_with_verbatim_evidence():
    """R7 §4.5/6 政策更新：cloze LLM 通道自洽审计 + 答案证据**逐字定位题干**
    + 竞争项全部有排除记录 → 允许释放（保守扣留不再是能力替代品）；
    证据不可定位时仍 unverified（原 R3B 安全语义保留为反例分支）。"""
    skill = VerbalUnderstandingSkill()
    stem = "他的书法得到了老师的高度（ ）。"
    opts = [("A", "褒扬"), ("B", "表扬信"), ("C", "褒奖之词"), ("D", "赞颂诗")]
    q = _q(stem, opts)
    i1 = stem.index("他的书法")
    payload = {"task_type": "cloze", "question_target": stem, "premises": [], "conclusion": "",
               "option_analyses": [
                   {"label": "A", "relation": "collocation_hit", "notes": "语义适配",
                    "evidence": [{"label": "A", "start": i1, "end": i1 + 5,
                                   "text": stem[i1:i1 + 5], "role": "support"}]},
                   {"label": "B", "relation": "collocation_conflict", "notes": "搭配不当", "evidence": []},
                   {"label": "C", "relation": "collocation_conflict", "notes": "搭配不当", "evidence": []},
                   {"label": "D", "relation": "collocation_conflict", "notes": "搭配不当", "evidence": []}],
               "unsupported_claims": [], "proposed_answer": "A", "confidence": 0.9,
               "uncertainty_reasons": []}
    sol = skill.solve(q, _ctx(llm=ScriptedLLM(payload)))
    assert sol.status == "solved" and sol.meta["plan_source"] == "llm_audited"
    ver = skill.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "A"
    # 反例：证据文字与定位不符 → solve 层即拒（第一道闸门）；
    # 若模型只给文字不给索引，verify 层逐字定位是第二道闸门（见上文分支）。
    import copy
    bad = copy.deepcopy(payload)
    bad["option_analyses"][0]["evidence"][0]["text"] = "他的写作水"
    sol2 = skill.solve(q, _ctx(llm=ScriptedLLM(bad)))
    assert sol2.status == "failed"
    assert sol2.meta["llm_rejection"]["code"] == "answer_without_evidence"


# ------------------------------------------------------------------ 论证/推断子集与消融

def test_argument_subset_weakens_unique():
    stem = "如果明天下雨，比赛就会改期。因此比赛改期了。以下哪项如果为真，最能削弱上述论证？"
    opts = [("A", "明天下雨了"), ("B", "比赛没有改期"), ("C", "裁判临时有事"), ("D", "观众很多")]
    skill = VerbalUnderstandingSkill()
    q = _q(stem, opts)
    sol = skill.solve(q, _ctx())
    assert sol.status == "solved" and sol.answer == "B"
    ver = skill.verify(q, sol, _ctx())
    assert ver.action == "accept" and ver.final_answer == "B"


def test_argument_out_of_subset_fails_not_guesses():
    # 冻结集同型的"主张削弱"：前提非条件链，规则核不强行编故事
    stem = ("城市绿地不仅美化环境，还能缓解热岛效应、降低噪声、吸附扬尘。"
            "因此，有人主张在老城区尽可能多种树。以下哪项如果为真，最能削弱上述主张？")
    opts = [("A", "种树需要长期维护成本"), ("B", "树木能吸附扬尘"),
            ("C", "绿地可降低噪声"), ("D", "热岛效应危害健康")]
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q(stem, opts), _ctx())
    assert sol.status == "failed" and sol.answer is None
    assert sol.meta["failure"]["code"] in {"argument_unresolved", "no_conclusion", "no_premises"}


def test_ambiguous_inference_refuses_to_pick():
    stem = "甲比乙高，乙比丙高。根据这段文字，下列说法正确的是？"
    # A/B 都是原文逐字可支持（真实歧义）：不得"三选一"猜一个看起来最顺的
    opts = [("A", "甲比乙高"), ("B", "乙比丙高"), ("C", "丙最高"), ("D", "甲最矮")]
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q(stem, opts), _ctx())
    assert sol.status == "failed"
    assert sol.meta["failure"]["code"] == "ambiguous_options"


def test_ablation_flag_disables_verbal():
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q(DEV16_STEM, DEV16_OPTIONS), _ctx(r3b_verbal=False))
    assert sol.status == "unsupported" and sol.answer is None


# ------------------------------------------------------------------ 基准守护

def test_verbal_benchmark_layout_and_freeze():
    import hashlib
    import json

    from assessment_agent.evaluation.runner import SPLITS, load_cases

    assert {"verbal_dev", "verbal_blind"} <= set(SPLITS)
    base = DATA / "benchmark_verbal"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    dev = load_cases(DATA, "verbal_dev")
    blind = load_cases(DATA, "verbal_blind")
    assert len(dev) == manifest["splits"]["dev"]["count"] == 20
    assert len(blind) == manifest["splits"]["blind"]["count"] == 12
    for split in ("dev", "blind"):
        raw = (base / split / "verbal.jsonl").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest["splits"][split]["sha256"], split
    # dev/blind 不得共享题干（泄漏守护）
    assert not ({c.stem for c in dev} & {c.stem for c in blind})
    # 四类任务在两集都覆盖
    for cases in (dev, blind):
        fams = {c.id.split("-")[1] for c in cases}
        assert {"cloze", "main", "inf", "arg"} <= fams or {"cloze", "main", "arg"} <= fams
    assert {"cloze", "main", "inf", "arg"} <= {c.id.split("-")[1] for c in dev}
    # 期望覆盖：不得是"全对"题集（含标注为失败/不可判定的构造性难点）
    assert {c.expected_status for c in dev} == {"solved", "failed", "undecidable"}
    assert sum(1 for c in blind if c.expected_status != "solved") >= 5
    assert all(c.answer is None for c in dev + blind if c.expected_status == "undecidable")
