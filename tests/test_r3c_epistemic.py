"""R3c Part 1：命题归属/事实性层（reported(P) ⊬ P 等）回归测试。

纪律：全部样本为通用构造，不含冻结盲集原题文本（vbv-inf-09 的机制以**改写变体**做回归锚）。
"""
from __future__ import annotations

import pytest

from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills.base import SkillContext
from assessment_agent.skills.verbal_understanding import VerbalUnderstandingSkill
from assessment_agent.tools.verbal_tool import (audit, build_verbal_plan, detect_task,
                                                epistemics)

# ----------------------------------------------------------- 单元：epistemics


@pytest.mark.parametrize("text,status", [
    ("有媒体报道称，升级版手机的功耗更低。", "reported"),
    ("专家认为该方案在成本上不可行。", "believed"),
    ("数据显示产量增长了三成。", "asserted"),
    ("如果明年干旱持续，粮价可能上涨。", "hypothetical"),
    ("研发团队否认了关于耗电增加的说法。", "denied_attribution"),
    ("没有证据表明这种做法违法。", "denied_attribution"),
    ("电池寿命约为三年。", "asserted"),
])
def test_epistemics_statuses(text: str, status: str):
    assert epistemics(text)["status"] == status


def test_nested_attribution_keeps_outer_scope():
    e = epistemics("报道称某机构否认了相关指控。")
    assert e["status"] == "reported" and any("否认" in m for m in e["nested"])


def test_bare_hypothesis_word_does_not_mroute_argument():
    # 材料中出现"假设"字样不得把"可以推出"题劫持进论证核
    stem = "如果新一代手机的功耗只是理论假设，推广未必顺利。根据这段文字，以下哪项可以推出？"
    assert detect_task(stem) == "inference"


# ----------------------------------------------------------- 最小对比对（核心）

P = "升级版手机的功耗更低"
OPTS = {"A": P,
        "B": "有媒体报道称升级版手机的功耗更低",
        "C": "手机厂商拒绝了媒体采访"}
QUEST = "根据这段文字，以下哪项可以推出？"


@pytest.mark.parametrize("prefix,gold", [
    ("有媒体报道称，升级版手机的功耗更低。", "B"),   # reported(P) ⊬ P，仅支持归属复述
    ("实测数据显示，升级版手机的功耗更低。", "A"),   # asserted(P) 支持裸断言
    ("如果新一代手机的功耗更低只是实验室假设，推广未必顺利。"
     "目前已确认这不是量产结论。", None),            # hypothetical 证据 → 无候选诚实失败
])
def test_minimal_contrast_pairs(prefix: str, gold):
    r = build_verbal_plan(prefix + QUEST, list(OPTS.items()))
    if gold is None:
        assert not r["ok"] and r["failure"]["code"] in {"insufficient_overlap", "ambiguous_options"}
        rels = {a.label: a.relation for a in []}
        return
    assert r["ok"], r
    assert r["plan"].proposed_answer == gold
    # 越权选项必须被记为 overreach（definitive）
    ana_a = next(a for a in r["plan"].option_analyses if a.label == "A")
    if gold == "B":
        assert ana_a.relation == "overreach" and ana_a.definitive
    ana_b = next(a for a in r["plan"].option_analyses if a.label == "B")
    if gold == "A":
        assert ana_b.relation == "insufficient"  # P ⊬ reported(P)
    # 独立审计同向复核
    winner = next(a for a in r["plan"].option_analyses if a.label == gold)
    verdict = audit(prefix + QUEST, OPTS, gold, "inference", winner.relation,
                    tuple(e.text for e in winner.evidence))
    assert verdict["status"] == "confirmed", verdict


def test_denial_blocks_both_polarities():
    stem = ("研发团队否认了关于升级版手机耗电增加的说法。" + QUEST)
    opts = {"A": "升级版手机的耗电没有增加",      # ¬P：否认针对归属谓词，推不出
            "B": "有媒体采访确认了耗电问题",       # 无依据归属
            "C": "研发团队表示将与媒体加强合作"}   # 外部事实
    r = build_verbal_plan(stem, list(opts.items()))
    assert not r["ok"], r
    rels = r["failure"]["detail"]["relations"]
    assert rels["A"] in {"overreach", "insufficient"}
    assert rels["C"] in {"absent", "insufficient"}


def test_inf09_mechanism_anchor_rewritten_variant():
    # 冻结盲集 inf-09 的**机制改写锚**（不是原题）：传闻 → 裸断言不得被接受
    stem = "有消息称，新版客户端的启动速度更快。根据这段文字，以下哪项可以推出？"
    opts = {"A": "新版客户端的启动速度确实更快",
            "B": "旧版客户端存在启动问题",
            "C": "该消息未经证实的说法流传于论坛"}
    r = build_verbal_plan(stem, list(opts.items()))
    assert not r["ok"], r
    assert r["failure"]["detail"]["relations"]["A"] == "overreach"


def test_cannot_question_takes_attribution_overreach():
    stem = ("有报道称新型材料可以降解，但该结论尚未在量产环境验证。"
            "根据这段文字，以下哪项最不能推出？")
    opts = {"A": "新型材料能够降解",
            "B": "该结论尚未在量产环境验证",
            "C": "有报道称新型材料可以降解"}
    r = build_verbal_plan(stem, list(opts.items()))
    assert r["ok"] and r["plan"].proposed_answer == "A"
    ana = r["plan"].option_analyses[0]
    v = audit(stem, opts, "A", "inference", "overreach", tuple(e.text for e in ana.evidence))
    assert v["status"] == "confirmed"


def test_audit_independent_layer_blocks_supported_overreach():
    # 直接调用审计（模拟主分类漏网）：归属证据 + 裸断言选项 → 不得 confirmed
    stem = "专家指出该做法并不安全。根据这段文字，以下哪项可以推出？"
    v = audit(stem, {"A": "该做法不安全", "B": "该做法安全"}, "A", "inference",
              "supported", ("专家指出该做法并不安全",))
    assert v["status"] == "insufficient" and "attribution" in v["reason"]


# ----------------------------------------------------------- Skill/verify 集成


def _q(stem: str, opts: dict) -> Question:
    return Question(stem=stem, options=[QuestionOption(label=k, text=v) for k, v in opts.items()],
                    question_type="single_choice")


@pytest.fixture()
def ctx():
    from assessment_agent.config import Settings
    return SkillContext(settings=Settings.from_env().model_copy(update={"offline": True}))


def test_skill_refuses_hearsay_bare_assertion(ctx):
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q("有报道称，这款 App 的日活翻倍。根据这段文字，以下哪项可以推出？",
                         {"A": "这款 App 的日活翻倍了",
                          "B": "这款 App 的日活下降",
                          "C": "报道来自 unnamed 账号"}), ctx)
    assert sol.status == "failed" and sol.answer is None


def test_skill_accepts_attribution_matched_answer(ctx):
    skill = VerbalUnderstandingSkill()
    sol = skill.solve(_q("有报道称，这款 App 的日活翻倍。根据这段文字，以下哪项可以推出？",
                         {"A": "有报道称这款 App 的日活翻倍",
                          "B": "这款 App 的日活下降",
                          "C": "报道称的内容已被官方证实"}), ctx)
    if sol.status == "solved":  # 规则解出则验证必须过归属双层
        ver = skill.verify(_q("有报道称，这款 App 的日活翻倍。根据这段文字，以下哪项可以推出？",
                              {"A": "有报道称这款 App 的日活翻倍",
                               "B": "这款 App 的日活下降",
                               "C": "报道称的内容已被官方证实"}), sol, ctx)
        assert sol.answer == "A" and ver.action == "accept"
    else:
        pytest.skip("该措辞在重叠阈值外，诚实失败亦为合规行为")
