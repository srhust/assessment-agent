"""生成 R3c 命题归属/事实性对照组：benchmark_epistemic/dev/epistemic.jsonl + manifest.json。

纪律：
- 人工按"外层谓词最小对比"编写：同一内容命题 P，只更换报道/断言/假设/否认/量词等
  外层归属结构，**期望行为随之改变**；期望状态在模型运行前按语言学标准确定；
- 禁止按模型是否解对筛题；禁止针对冻结盲集（vbv-inf-09 等）原题文本特判——本组全部
  为改写变体；
- dev 组允许在 R3c 迭代中调整；正式盲测见 benchmark_language/blind 与 verbal_blind2。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "benchmark_epistemic"
CREATED = "2026-10-18"


def opt(*pairs):
    return [dict(label=l, text=t) for l, t in pairs]


DEV = [
    # 组1 电池：断言→支持裸断言；报道→只支持归属复述；纯传闻无归属选项→诚实 failed
    dict(id="ep-assert-01", skill="verbal_understanding", question_type="single_choice",
         group="asserted", expected_status="solved", answer="A",
         tags=["事实性对照", "最小对比对", "断言"],
         stem="实验测试表明，新型电池的续航时间更长。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "新型电池的续航时间更长"), ("B", "新型电池不存在任何续航问题"),
                     ("C", "续航测试仅由媒体完成")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-reported-02", skill="verbal_understanding", question_type="single_choice",
         group="reported", expected_status="solved", answer="B",
         tags=["事实性对照", "最小对比对", "报道", "锚=盲集inf-09机制改写"],
         stem="有报道称，新型电池的续航时间更长。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "新型电池的续航时间确实更长"), ("B", "有报道称新型电池的续航时间更长"),
                     ("C", "新型电池的续航问题已经解决")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-hearsay-03", skill="verbal_understanding", question_type="single_choice",
         group="reported", expected_status="failed", answer=None,
         tags=["事实性对照", "传闻", "无合法候选"],
         stem="据社区论坛流传，新型电池的续航时间更长。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "新型电池的续航时间更长"), ("B", "新型电池的续航没有提升"),
                     ("C", "该电池通过了官方认证")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组2 手机功耗：报道 vs 实测断言，同一选项集，gold 随外层谓词翻转
    dict(id="ep-reported-04", skill="verbal_understanding", question_type="single_choice",
         group="reported", expected_status="solved", answer="B",
         tags=["事实性对照", "最小对比对", "报道"],
         stem="有媒体报道称，升级版手机的功耗更低。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "升级版手机的功耗更低"), ("B", "有媒体报道称升级版手机的功耗更低"),
                     ("C", "手机厂商拒绝了媒体采访")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-assert-05", skill="verbal_understanding", question_type="single_choice",
         group="asserted", expected_status="solved", answer="A",
         tags=["事实性对照", "最小对比对", "数据断言"],
         stem="实测数据显示，升级版手机的功耗更低。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "升级版手机的功耗更低"), ("B", "有媒体报道称升级版手机的功耗更低"),
                     ("C", "手机厂商拒绝了媒体采访")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组3 否认：归属否认 → 两个方向的裸断言都推不出
    dict(id="ep-denied-06", skill="verbal_understanding", question_type="single_choice",
         group="denied", expected_status="failed", answer=None,
         tags=["事实性对照", "否认归属", "双向不可推"],
         stem="研发团队否认了关于升级版手机耗电增加的说法。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "升级版手机的耗电确实增加了"), ("B", "升级版手机的耗电没有增加"),
                     ("C", "用户投诉集中在耗电问题上")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-denied-07", skill="verbal_understanding", question_type="single_choice",
         group="denied", expected_status="solved", answer="A",
         tags=["事实性对照", "否认归属", "无法推出"],
         stem="研发团队否认了关于升级版手机耗电增加的说法。根据这段文字，以下哪项最不能推出？",
         options=opt(("A", "升级版手机的耗电确实增加了"),
                     ("B", "研发团队否认了关于耗电增加的说法"),
                     ("C", "关于耗电增加的说法被研发团队否认")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组4 嵌套归属：报道内嵌否认 → 只支持归属层面复述
    dict(id="ep-nested-08", skill="verbal_understanding", question_type="single_choice",
         group="nested", expected_status="solved", answer="A",
         tags=["事实性对照", "嵌套归属", "报道称+否认"],
         stem="有媒体报道称，监管机构已经否认了相关调查正在进行。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "有媒体报道称监管机构否认相关调查正在进行"),
                     ("B", "相关调查没有正在进行"),
                     ("C", "监管机构将启动正式调查")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组5 观点 vs 事实
    dict(id="ep-believed-09", skill="verbal_understanding", question_type="single_choice",
         group="believed", expected_status="solved", answer="A",
         tags=["事实性对照", "观点归属", "可以推出"],
         stem="部分专家认为，现行标准在成本上不可行。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "部分专家认为现行标准在成本上不可行"),
                     ("B", "现行标准在成本上确实不可行"),
                     ("C", "现行标准将被立即修订")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-believed-10", skill="verbal_understanding", question_type="single_choice",
         group="believed", expected_status="solved", answer="A",
         tags=["事实性对照", "观点归属", "无法推出"],
         stem="部分专家认为，现行标准在成本上不可行，并建议推迟实施。根据这段文字，以下哪项最不能推出？",
         options=opt(("A", "现行标准在成本上确实不可行"),
                     ("B", "部分专家认为现行标准在成本上不可行"),
                     ("C", "专家建议推迟实施")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组6 混合量词+归属：规则子集外，诚实 failed（保留困难样本，不按解对与否筛选）
    dict(id="ep-quant-11", skill="verbal_understanding", question_type="single_choice",
         group="quantifier", expected_status="failed", answer=None,
         tags=["事实性对照", "混合量词", "条件", "子集外"],
         stem="如果部分用户迁移成功，所有存量账户都会被注销。目前有用户迁移成功。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "所有存量账户都会被注销"), ("B", "部分存量账户不会被注销"),
                     ("C", "迁移用户可以撤回申请")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组7 双重否定归属
    dict(id="ep-doubleneg-12", skill="verbal_understanding", question_type="single_choice",
         group="asserted", expected_status="solved", answer="A",
         tags=["事实性对照", "双重否定", "断言"],
         stem="这份审计报告不是没有指出库存管理的问题。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "这份审计报告指出了库存管理的问题"),
                     ("B", "这份审计报告没有指出任何问题"),
                     ("C", "库存管理已被彻底整改")),
         source=dict(provenance="human-written", created=CREATED)),
    # 组8 假设
    # 组9 R4a 预报最小对比对：同一 P 用 断言|预报 两种外层，期望行为随之改变
    # （Forecast(P) ⊬ P，与 reported 同级；同词复述归属才可 supported）
    dict(id="ep-predicted-14", skill="verbal_understanding", question_type="single_choice",
         group="predicted", expected_status="solved", answer="B",
         tags=["事实性对照", "最小对比对", "预报", "R4a"],
         stem="气象预报显示，明天有暴雨，户外演出因此可能取消。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "明天有暴雨"),
                     ("B", "气象预报显示明天有暴雨"),
                     ("C", "户外演出将取消")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-predicted-15", skill="verbal_understanding", question_type="single_choice",
         group="predicted", expected_status="failed", answer=None,
         tags=["事实性对照", "预报", "无法推出双候选", "R4a"],
         stem="气象预报显示，明天有暴雨，户外演出因此可能取消。根据这段文字，以下哪项最不能推出？",
         options=opt(("A", "明天有暴雨"),
                     ("B", "气象预报显示明天有暴雨"),
                     ("C", "户外演出将取消")),
         source=dict(provenance="human-written", created=CREATED)),
    dict(id="ep-assert-16", skill="verbal_understanding", question_type="single_choice",
         group="asserted", expected_status="solved", answer="A",
         tags=["事实性对照", "最小对比对", "断言对照预报", "R4a"],
         stem="实测记录显示：明日将有暴雨，户外演出取消。根据这段文字，以下哪项可以推出？",
         options=opt(("A", "明日有暴雨"),
                     ("B", "气象预报显示明日有暴雨"),
                     ("C", "户外演出照常举行")),
         source=dict(provenance="human-written", created=CREATED)),
]


def _rows(items):
    lines = []
    for it in items:
        row = dict(it)
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + "\n"


def main() -> None:
    d = OUT / "dev"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "epistemic.jsonl"
    raw = _rows(DEV).encode("utf-8")
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    counts = {}
    for it in DEV:
        counts[it["expected_status"]] = counts.get(it["expected_status"], 0) + 1
    manifest = {
        "name": "benchmark_epistemic",
        "data_class": "human-written minimal-contrast pairs（同一命题 P 只换外层归属谓词；期望状态在模型运行前按语言学标准确定）",
        "created_at": CREATED,
        "splits": {
            "dev": {"file": "dev/epistemic.jsonl", "count": len(DEV), "sha256": digest,
                    "expected_status_counts": counts,
                    "policy": "R3c 迭代期间可调参；调整后重生成即更新本 sha256"},
        },
        "independence_declaration": (
            "题目由人工按事实性网格独立编写；不按模型是否解对筛题；"
            "冻结盲集（benchmark_verbal/blind）原题未出现在本组，vbv-inf-09 仅以机制改写变体作回归锚；"
            "禁止依据任何盲集表现修改本组之外的规则后回写盲集。"),
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[epistemic] dev {len(DEV)} 题 sha={digest[:16]} counts={counts}")


if __name__ == "__main__":
    main()
