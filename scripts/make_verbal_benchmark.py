"""生成 R3b 言语理解基准：benchmark_verbal/{dev,blind}/verbal.jsonl + manifest.json。

纪律（写入 manifest 的 independence_declaration）：
- 题目按"任务类型 × 语言特征"网格**独立人工编写**并标注答案/期望状态；
- expected_status 依据编写时的规则子集设计意图判定，**先于任何模型运行**固定；
- 禁止按模型是否解对筛题；blind 集生成一次后即冻结（manifest 记录 sha256），
  后续不得依据 blind 结果修改题目或规则后再重生成。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "benchmark_verbal"
CREATED = "2026-10-18"  # 固定日期：重生成字节级一致（R3a 同款纪律）


def opt(*pairs):
    return [{"label": l, "text": t} for l, t in pairs]


# ---------------------------------------------------------------- dev（可调参子集）
DEV = [
    # ---- 选词填空
    dict(id="vb-cloze-01", group="cloze", tags=["short", "collocation"],
         stem="这部纪录片播出后，观众的（ ）十分强烈，不少人致电询问拍摄地。",
         options=opt(("A", "反应"), ("B", "反映"), ("C", "批示"), ("D", "注视")),
         answer="A", expected_status="solved"),
    dict(id="vb-cloze-02", group="cloze", tags=["collocation"],
         stem="记者把基层群众的呼声通过内参（ ）到有关部门。",
         options=opt(("A", "反应"), ("B", "反映"), ("C", "批示"), ("D", "注视")),
         answer="B", expected_status="solved"),
    dict(id="vb-cloze-03", group="cloze", tags=["similar", "table-gap"],
         stem="我们要（ ）艰苦奋斗的优良作风。",
         options=opt(("A", "发扬"), ("B", "发挥"), ("C", "培育"), ("D", "弘扬")),
         answer="A", expected_status="failed"),  # 培育/弘扬 不在搭配表 → 非确定性排除
    dict(id="vb-cloze-04", group="cloze", tags=["multi_blank"],
         stem="依次填入横线处最恰当的词语是：他的讲演____，观众的情绪被____起来。",
         options=opt(("A", "引人入胜 调动"), ("B", "打动人心 激发"),
                      ("C", "感人肺腑 唤起"), ("D", "精彩绝伦 带动")),
         answer="B", expected_status="failed"),
    dict(id="vb-cloze-05", group="cloze", tags=["idiom", "color"],
         stem="一些账号为了流量（ ），完全没有底线。",
         options=opt(("A", "铤而走险"), ("B", "挺身而出"), ("C", "破釜沉舟"), ("D", "当仁不让")),
         answer="A", expected_status="failed"),  # 成语感情色彩不在子集
    # ---- 主旨概括
    dict(id="vb-main-06", group="main_idea", tags=["short", "cue-therefore"],
         stem="许多城市推广共享单车后，乱停乱放问题突出，影响交通秩序。因此必须加强共享单车的停放管理。这段文字的主旨是？",
         options=opt(("A", "共享单车影响交通秩序"), ("B", "必须加强共享单车的停放管理"),
                      ("C", "乱停乱放现象普遍存在"), ("D", "城市应当限制共享单车数量")),
         answer="B", expected_status="solved"),
    dict(id="vb-main-07", group="main_idea", tags=["cue-therefore"],
         stem="传统能源价格持续上涨，电网调峰压力增大。因此，发展分布式储能是缓解供电紧张的可行路径。这段文字的主旨是？",
         options=opt(("A", "传统能源价格不断上涨"), ("B", "发展分布式储能缓解供电紧张"),
                      ("C", "电网调峰源于结构缺陷"), ("D", "供电紧张将拖累经济增速")),
         answer="B", expected_status="solved"),
    dict(id="vb-main-08", group="main_idea", tags=["turn", "near-tie"],
         stem="网购确实便利了生活，然而退货难、质检弱、责任界定模糊等新问题也随之而来。这段文字意在强调？",
         options=opt(("A", "网购便利了生活"), ("B", "网购带来新问题"),
                      ("C", "退货难是最大问题"), ("D", "平台应明确责任")),
         answer="B", expected_status="failed"),  # 转折句 gram 覆盖近 tie，子集不保证
    dict(id="vb-main-09", group="main_idea", tags=["long", "paraphrase"],
         stem="古籍修复讲究'修旧如旧'，一位匠人处理一册虫蛀县志往往需要数月。年轻学徒从浆糊浓度练起，十年才能独立开页。而数字化扫描只能解决查阅便利，解决不了纸寿千年的问题。这段文字意在说明？",
         options=opt(("A", "古籍修复技艺需要长期传承积累"), ("B", "数字化扫描毫无用处"),
                      ("C", "学徒培养周期过长应当缩短"), ("D", "县志破损情况日益严重")),
         answer="A", expected_status="failed"),  # 重表述改写：字面覆盖规则不适用
    # ---- 阅读推断
    dict(id="vb-inf-10", group="inference", tags=["quant", "literal"],
         stem="所有参评项目都通过了初审。部分项目组受邀展示了成果。根据这段文字，可以推出？",
         options=opt(("A", "所有参评项目都通过了初审"), ("B", "通过初审的项目都受邀展示"),
                      ("C", "所有项目都受邀展示"), ("D", "受邀展示的不是参评项目")),
         answer="A", expected_status="solved"),
    dict(id="vb-inf-11", group="inference", tags=["quant", "modal"],
         stem="有些新员工参加了培训。李明是这批新员工中的一员。下列说法不正确的是？",
         options=opt(("A", "有些新员工参加了培训"), ("B", "李明可能参加了培训"),
                      ("C", "所有新员工都没有参加培训"), ("D", "有的新员工参加了培训")),
         answer="C", expected_status="solved"),
    dict(id="vb-inf-12", group="inference", tags=["double-negation"],
         stem="这次会议不是没有成果。有些提案通过了审议。根据这段文字，下列说法不正确的是？",
         options=opt(("A", "这次会议有成果"), ("B", "有些提案通过了审议"),
                      ("C", "有的提案未被当场表决"), ("D", "这次会议没有任何成果")),
         answer="D", expected_status="solved"),
    dict(id="vb-inf-13", group="inference", tags=["quant", "negation"],
         stem="所有参赛队员都通过了体能测试。有些队员来自青训营。根据这段文字，下列说法不正确的是？",
         options=opt(("A", "参赛队员都通过了体能测试"), ("B", "所有队员都没有通过体能测试"),
                      ("C", "有些队员来自青训营"), ("D", "来自青训营的队员都是参赛队员")),
         answer="B", expected_status="solved"),  # D 属过度引申但不等于必然错误：子集只认矛盾项
    dict(id="vb-inf-14", group="inference", tags=["ambiguity"],
         stem="甲比乙高，乙比丙高。根据这段文字，下列说法正确的是？",
         options=opt(("A", "甲比乙高"), ("B", "乙比丙高"), ("C", "丙最矮"), ("D", "甲最矮")),
         answer=None, expected_status="undecidable"),  # A/B 同时可推出：真歧义设计
    dict(id="vb-inf-15", group="inference", tags=["conditional", "fallacy"],
         stem="如果气温超过40度，体育馆将延长开放。昨天体育馆延长了开放。根据这段文字，可以推出？",
         options=opt(("A", "昨天气温可能没超过40度"), ("B", "昨天气温超过了40度"),
                      ("C", "体育馆明天不会开放"), ("D", "延长开放与气温无关")),
         answer="A", expected_status="failed"),  # 肯后式不确定性推断不在子集
    # ---- 加强/削弱/前提
    dict(id="vb-arg-16", group="argument", tags=["negation", "direct-rebuttal"],
         stem="如果明天下雨，比赛就会改期。因此比赛改期了。以下哪项如果为真，最能削弱上述论证？",
         options=opt(("A", "明天下雨了"), ("B", "比赛没有改期"),
                      ("C", "裁判临时有事"), ("D", "到场的观众很多")),
         answer="B", expected_status="solved"),
    dict(id="vb-arg-17", group="argument", tags=["gap", "conditional"],
         stem="如果坚持锻炼就能更健康。老王希望通过冬天改善体质。因此老王更健康了。以下哪项如果为真，最能支持上述论证？",
         options=opt(("A", "锻炼需要场地保障"), ("B", "医生认为规律作息更重要"),
                      ("C", "老王坚持锻炼了一个冬天"), ("D", "老王去年体重增加")),
         answer="C", expected_status="solved"),
    dict(id="vb-arg-18", group="argument", tags=["assumption", "only-if"],
         stem="只有年满18岁才能报名马拉松。因此小明不能报名马拉松。上述论证基于以下哪项假设？",
         options=opt(("A", "小明没有年满18岁"), ("B", "马拉松报名需要体检"),
                      ("C", "报名系统近期完成升级"), ("D", "马拉松历史悠久")),
         answer="A", expected_status="solved"),
    dict(id="vb-arg-19", group="argument", tags=["claim", "cost-benefit"],
         stem="城市绿地不仅美化环境，还能缓解热岛效应、降低噪声、吸附扬尘。因此，有人主张在老城区尽可能多种树。以下哪项如果为真，最能削弱上述主张？",
         options=opt(("A", "种树需要长期维护成本"), ("B", "树木能吸附扬尘"),
                      ("C", "绿地可降低噪声"), ("D", "热岛效应危害健康")),
         answer="A", expected_status="failed"),  # 主张-权衡型论证无法形式化为条件链
    dict(id="vb-arg-20", group="argument", tags=["gap", "consequent"],
         stem="如果摄入咖啡因，就会延长浅睡时间。因此，受试者就会延长浅睡时间。以下哪项如果为真，最能支持上述论证？",
         options=opt(("A", "受试者摄入了咖啡因"), ("B", "实验在双盲条件下进行"),
                      ("C", "浅睡时间用脑电波测量"), ("D", "咖啡因代谢约需6小时")),
         answer="A", expected_status="solved"),
]

# ---------------------------------------------------------------- blind（一次生成即冻结）
BLIND = [
    dict(id="vbv-cloze-01", group="cloze", tags=["short", "collocation"],
         stem="园区改革极大（ ）了企业的创新活力，技改项目明显增多。",
         options=opt(("A", "激发"), ("B", "发扬"), ("C", "批示"), ("D", "反映")),
         answer="A", expected_status="solved"),
    dict(id="vbv-cloze-02", group="cloze", tags=["similar", "table-gap"],
         stem="他对文物修复怀有（ ）的感情，每次开馆前都要独自走一遍展厅。",
         options=opt(("A", "深厚"), ("B", "深沉"), ("C", "深挚"), ("D", "浓烈")),
         answer="A", expected_status="failed"),
    dict(id="vbv-cloze-03", group="cloze", tags=["idiom"],
         stem="为赶工期，个别工地出现了（ ）式的粗放施工。",
         options=opt(("A", "急功近利"), ("B", "精益求精"), ("C", "稳扎稳打"), ("D", "循序渐进")),
         answer="A", expected_status="failed"),
    dict(id="vbv-main-04", group="main_idea", tags=["cue-therefore"],
         stem="高铁站周边通勤接驳不足，旅客换乘耗时普遍超过四十分钟。因此，完善'最后一公里'接驳是提升枢纽体验的关键。这段文字的主旨是？",
         options=opt(("A", "完善最后一公里接驳是提升枢纽体验的关键"), ("B", "高铁站旅客数量持续增长"),
                      ("C", "换乘耗时超过四十分钟"), ("D", "枢纽周边应当增设商业设施")),
         answer="A", expected_status="solved"),
    dict(id="vbv-main-05", group="main_idea", tags=["turn", "near-tie"],
         stem="电子书确实普及了阅读，但纸质书在深度阅读场景里的位置并没有被取代。这段文字意在强调？",
         options=opt(("A", "电子书普及了阅读"), ("B", "纸质书仍有不可替代的位置"),
                      ("C", "深度阅读依赖纸质书"), ("D", "两种书籍将长期并存")),
         answer="B", expected_status="failed"),
    dict(id="vbv-main-06", group="main_idea", tags=["long", "paraphrase"],
         stem="疫苗冷链运输要求全程2到8摄氏度，任何一个环节的温度波动都可能让整批药剂失效。乡村接种点的冰箱时常断电，配送员不得不往返补冰。有人建议把冷链标准放宽到15摄氏度以减少损耗。这段文字没有提及以下哪一点？",
         options=opt(("A", "温度波动可能使疫苗失效"), ("B", "接种点供电条件不稳定"),
                      ("C", "放宽标准会牺牲疫苗效力"), ("D", "乡村配送需要往返补冰")),
         answer="C", expected_status="failed"),
    dict(id="vbv-inf-07", group="inference", tags=["quant", "negation"],
         stem="本次抽检的全部批次均符合标准。部分批次来自本地工厂。下列说法不正确的是？",
         options=opt(("A", "本次抽检的批次符合标准"), ("B", "有的抽检批次不符合标准"),
                      ("C", "部分批次来自本地工厂"), ("D", "所有批次都来自本地工厂")),
         answer="B", expected_status="solved"),
    dict(id="vbv-inf-08", group="inference", tags=["double-negation", "modal"],
         stem="失败不是没有价值，它教会我们的往往比成功更多。根据这段文字，可以推出？",
         options=opt(("A", "失败具有价值"), ("B", "成功不能教给人东西"),
                      ("C", "失败一定比成功好"), ("D", "有价值的事物都是失败")),
         answer="A", expected_status="solved"),
    dict(id="vbv-inf-09", group="inference", tags=["insufficient", "hearsay"],
         stem="有报道称新型电池续航更长。根据这段文字，可以推出？",
         options=opt(("A", "新型电池续航确实更长"), ("B", "续航提升了百分之三十"),
                      ("C", "该报道已经过第三方核实"), ("D", "新型电池即将量产")),
         answer=None, expected_status="undecidable"),
    dict(id="vbv-arg-10", group="argument", tags=["modus-tollens", "email"],
         stem="如果报名成功，系统就会发送确认邮件。老张没有收到确认邮件，所以报名没有成功。以下哪项如果为真，最能削弱上述论证？",
         options=opt(("A", "确认邮件可能延迟或进入垃圾箱"), ("B", "报名需要实名认证"),
                      ("C", "老张使用了新邮箱"), ("D", "系统上线已超过半年")),
         answer="A", expected_status="failed"),  # 邮件系统的现实语义无法形式化
    dict(id="vbv-arg-11", group="argument", tags=["only-if", "gap"],
         stem="只要按时提交，申请就会被受理。项目组提交了材料。因此申请被受理了。以下哪项如果为真，最能支持上述论证？",
         options=opt(("A", "项目组按时提交了材料"), ("B", "受理标准近期有调整"),
                      ("C", "评审专家来自高校"), ("D", "项目组曾撤回过申请")),
         answer="A", expected_status="solved"),
    dict(id="vbv-arg-12", group="argument", tags=["claim", "correlation"],
         stem="不少家长认为课外班越多成绩越好，因此应不断给孩子加课。以下哪项如果为真，最能削弱上述观点？",
         options=opt(("A", "报班数量与成绩提升没有稳定相关"), ("B", "部分名师课时费上涨"),
                      ("C", "有学生喜欢自学"), ("D", "课外班需要接送成本")),
         answer="A", expected_status="failed"),
]


def _rows(items, split):
    out = []
    for it in items:
        row = {
            "id": it["id"],
            "skill": "verbal_understanding",
            "question_type": "single_choice",
            "stem": it["stem"],
            "options": it["options"],
            "answer": it["answer"],
            "expected_status": it["expected_status"],
            "group": it["group"],
            "tags": it["tags"],
            "source": f"benchmark_verbal/{split}",
        }
        out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(out) + "\n"


def main() -> None:
    for split, items in (("dev", DEV), ("blind", BLIND)):
        d = OUT / split
        d.mkdir(parents=True, exist_ok=True)
        path = d / "verbal.jsonl"
        path.write_text(_rows(items, split), encoding="utf-8")
    digests = {}
    for split in ("dev", "blind"):
        raw = (OUT / split / "verbal.jsonl").read_bytes()
        digests[split] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "name": "benchmark_verbal",
        "data_class": "independent-handwritten（人工按语言特征网格独立编写，非真题库）",
        "created_at": CREATED,
        "authorship": "assessment-agent 项目原创（题干、选项与标注均为人工编写并复核）",
        "license": "内部评估使用（自创内容，无第三方版权）",
        "contains_real_exam": False,
        "independence_declaration": (
            "样本按 任务类型×语言特征 网格独立编写并按语言学标准标注：期望状态（solved/failed/"
            "undecidable）在**任何模型运行之前**依据子集设计意图确定；禁止按模型是否解对筛选题目；"
            "blind 集生成一次即冻结（本清单记录 sha256），后续不得依据 blind 表现修改题目或规则后重生成。"
        ),
        "annotation_note": (
            "expected_status=failed/undecidable 的题目包含超出当前离线规则子集的构造性难点"
            "（成语色彩、近义并列、重表述改写、现实语义论证、肯后式推断）；answer 字段保留人工金标准，"
            "undecidable 样本不给唯一答案。"
        ),
        "splits": {
            "dev": {"file": "dev/verbal.jsonl", "count": len(DEV), "sha256": digests["dev"],
                     "tuning": "允许在此集上调试规则并报告指标"},
            "blind": {"file": "blind/verbal.jsonl", "count": len(BLIND), "sha256": digests["blind"],
                       "tuning": "冻结：首次运行后只报告，不再据结果改题改规则"},
        },
        "group_coverage": {
            split: {g: sum(1 for it in items if it["group"] == g)
                     for g in ("cloze", "main_idea", "inference", "argument")}
            for split, items in (("dev", DEV), ("blind", BLIND))
        },
        "expected_status_coverage": {
            split: {s: sum(1 for it in items if it["expected_status"] == s)
                     for s in ("solved", "failed", "undecidable")}
            for split, items in (("dev", DEV), ("blind", BLIND))
        },
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dev": len(DEV), "blind": len(BLIND), "sha": digests}, ensure_ascii=False))


if __name__ == "__main__":
    main()
