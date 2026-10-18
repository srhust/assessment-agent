"""生成 R3c 语言推理基准：benchmark_language/{dev,blind} + benchmark_verbal/blind2。

纪律（写入 manifest 的 independence_declaration）：
- 人工按"七类关系 × 方向 × 判别性"网格独立编写，期望答案在模型运行前按词典语言学
  标准确定；**禁止按 Solver 是否解对筛除失败样本**（多重匹配/词典外题干作为诚实
  failed 样本保留）；
- 每题干扰项被词典判定为不同关系/方向反转/未登录；唯一 match 才标 solved；
- blind 与 blind2 在 dev 全部工作完成后生成，生成即冻结（sha256 入 manifest），
  只允许**首次且唯一一次**评测运行；此后不得依据其表现修改规则或重生成。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_LANG = ROOT / "data" / "benchmark_language"
OUT_VBL2 = ROOT / "data" / "benchmark_verbal"
CREATED = "2026-10-18"


def opt(*pairs):
    return [dict(label=l, text=t) for l, t in pairs]


def item(id_, stem, options, answer, group, tags, expected="solved", skill="language_reasoning"):
    return dict(id=id_, skill=skill, question_type="single_choice", group=group,
                expected_status=expected, answer=answer, tags=tags, stem=stem, options=options,
                source=dict(provenance="human-written", created=CREATED))


V = "verbal_understanding"

DEV = [
    item("lng-gen-01", "苹果∶水果 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "芹菜∶蔬菜"), ("B", "轮胎∶汽车"), ("C", "桌子∶木头"), ("D", "厨师∶厨房")),
         "A", "genus", ["种属", "方向一致"]),
    item("lng-gen-02", "香蕉∶水果 逻辑关系与之相同的一组是",
         opt(("A", "书本∶页码"), ("B", "西瓜∶水果"), ("C", "木头∶桌子"), ("D", "医生∶医院")),
         "B", "genus", ["种属", "反序干扰"]),
    item("lng-part-03", "表盘∶手表 与下列关系最为相似的是",
         opt(("A", "手表∶表盘"), ("B", "页码∶书本"), ("C", "厨师∶炒锅"), ("D", "笔∶写字")),
         "B", "part", ["整体部分", "方向偷换=partial"]),
    item("lng-part-04", "电脑∶键盘 相当于",
         opt(("A", "键盘∶电脑"), ("B", "书本∶封面"), ("C", "苹果∶水果"), ("D", "面粉∶面包")),
         "B", "part", ["反向包含 HAS_PART"]),
    item("lng-mat-05", "桌子∶木头 关系最为相似的一组是",
         opt(("A", "木头∶桌子"), ("B", "面包∶面粉"), ("C", "医生∶医院"), ("D", "钟表∶计时")),
         "B", "material", ["材料 MADE_FROM", "反序=PRODUCES 干扰"]),
    item("lng-mat-06", "玻璃∶窗户 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "窗户∶玻璃"), ("B", "钢材∶桥梁"), ("C", "水果∶苹果"), ("D", "渔网∶捕鱼")),
         "B", "material", ["原料→成品 PRODUCES"]),
    item("lng-fun-07", "钟表∶计时 逻辑关系相同于",
         opt(("A", "电话∶通讯"), ("B", "画家∶颜料"), ("C", "司机∶汽车"), ("D", "轮胎∶汽车")),
         "A", "function", ["功能用途", "工具-人/场所干扰"]),
    item("lng-fun-08", "笔∶写字 相当于",
         opt(("A", "写字∶老师"), ("B", "渔网∶捕鱼"), ("C", "书本∶封面"), ("D", "水∶鱼")),
         "B", "function", ["功能", "反序/部分干扰"]),
    item("lng-plc-09", "演员∶剧院 与下列关系最为相似的是",
         opt(("A", "老师∶学校"), ("B", "学校∶老师"), ("C", "医生∶听诊器"), ("D", "木头∶家具")),
         "A", "place", ["地点对象", "反序未定义=未登录对"]),
    item("lng-plc-10", "鱼∶水 关系最为相似的一组是",
         opt(("A", "猎人∶森林"), ("B", "鸟∶天空"), ("C", "厨师∶厨房"), ("D", "司机∶汽车")),
         None, "place", ["多重匹配：B/C/D 均 PLACE", "诚实保留不可判题"], expected="failed"),
    item("lng-tl-11", "医生∶听诊器 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "听诊器∶医生"), ("B", "厨师∶炒锅"), ("C", "园丁∶花园"), ("D", "警察∶抓捕")),
         "B", "tool", ["工具关系", "反序未定义"]),
    item("lng-cau-12", "努力∶成功 逻辑关系与之相同的是",
         opt(("A", "摩擦∶生热"), ("B", "成功∶失败"), ("C", "医生∶医院"), ("D", "医生∶治疗")),
         "A", "cause", ["因果 CAUSE", "反义/场所/功能干扰"]),
    item("lng-cau-13", "感冒∶发烧 相当于",
         opt(("A", "发烧∶感冒"), ("B", "咳嗽∶感冒"), ("C", "学习∶进步"), ("D", "聪明∶愚笨")),
         "C", "cause", ["因果", "反序 EFFECT 干扰"]),
    item("lng-syn-14", "高兴∶喜悦 与下列关系最为相似的是",
         opt(("A", "愤怒∶气愤"), ("B", "犹豫∶果断"), ("C", "汽车∶轮胎"), ("D", "医生∶医院")),
         "A", "synonym", ["近义", "反义/部分/场所干扰"]),
    item("lng-ant-15", "犹豫∶果断 逻辑关系相同的一组是",
         opt(("A", "安静∶嘈杂"), ("B", "高兴∶喜悦"), ("C", "吝啬∶小气"), ("D", "勤奋∶刻苦")),
         "A", "antonym", ["反义", "三个近义干扰"]),
    item("lng-unk-16", "收集∶整理 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "苹果∶水果"), ("B", "医生∶医院"), ("C", "笔∶写字"), ("D", "鱼∶水")),
         None, "unknown_stem", ["题干词对无词典证据", "诚实 failed 样本"], expected="failed"),
    item("lng-en-17", "Fork is to eating as knife is to",
         opt(("A", "cooking"), ("B", "cutting"), ("C", "spoon"), ("D", "drinking")),
         "B", "en_analogy", ["英文 FUNCTION 类比", "未登录竞争项声明"]),
    item("lng-en-18", "Wheel is to car as page is to",
         opt(("A", "wheel"), ("B", "vehicle"), ("C", "book"), ("D", "read")),
         "C", "en_analogy", ["英文 PART_OF"]),
    item("lng-syn-19", "找出下列词语的近义词：吝啬",
         opt(("A", "节俭"), ("B", "小气"), ("C", "大方"), ("D", "慷慨")),
         "B", "synonym", ["近义词", "评价色彩干扰"]),
    item("lng-ant-20", "下列词语与“勤奋”意思相反的是",
         opt(("A", "勤勉"), ("B", "懒惰"), ("C", "刻苦"), ("D", "努力")),
         "B", "antonym", ["反义词", "近义干扰"]),
]

BLIND = [
    item("lngb-gen-01", "黄瓜∶蔬菜 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "轮胎∶汽车"), ("B", "大米∶食物"), ("C", "桌子∶木头"), ("D", "医生∶诊断")),
         None, "genus", ["题干黄瓜未登录", "诚实 failed 样本"], expected="failed"),
    item("lngb-mix-02", "键盘∶电脑 相当于",
         opt(("A", "电脑∶键盘"), ("B", "表带∶手表"), ("C", "画家∶颜料"), ("D", "木头∶桌子")),
         "B", "part", ["整体部分", "反序 partial"]),
    item("lngb-mat-03", "面粉∶面条 关系最为相似的一组是",
         opt(("A", "面条∶面粉"), ("B", "玻璃∶镜子"), ("C", "书本∶报纸"), ("D", "杯子∶桌子")),
         "B", "material", ["原料→成品", "MADE_FROM 反序干扰"]),
    item("lngb-fun-04", "电话∶通讯 与下列关系最为相似的是",
         opt(("A", "书籍∶纸张"), ("B", "文具∶钢笔"), ("C", "笔∶写字"), ("D", "杯子∶桌子")),
         "C", "function", ["功能用途", "材料/种属干扰"]),
    item("lngb-plc-05", "厨师∶厨房 逻辑关系相同的一组是",
         opt(("A", "厨房∶厨师"), ("B", "演员∶剧院"), ("C", "菜刀∶厨师"), ("D", "烹饪∶香味")),
         "B", "place", ["地点对象", "反序/工具未定义关系"]),
    item("lngb-tl-06", "画家∶画笔 相当于",
         opt(("A", "画笔∶画家"), ("B", "警察∶抓捕"), ("C", "园丁∶剪刀"), ("D", "学生∶学校")),
         "C", "tool", ["工具关系", "反序未定义"]),
    item("lngb-cau-07", "摩擦∶生热 与下列哪组词语逻辑关系最为相似？",
         opt(("A", "生热∶摩擦"), ("B", "懈怠∶失败"), ("C", "聪明∶愚笨"), ("D", "医生∶诊断")),
         "B", "cause", ["因果", "反序 EFFECT"]),
    item("lngb-ant-08", "慷慨∶吝啬 逻辑关系与之相同的是",
         opt(("A", "大方∶小气"), ("B", "安静∶寂静"), ("C", "高兴∶难过"), ("D", "聪明∶聪慧")),
         None, "antonym", ["多重反义对（A/C 均 ANT）", "诚实 failed 样本"], expected="failed"),
    item("lngb-unk-09", "春天∶花朵 与下列关系最为相似的是",
         opt(("A", "苹果∶水果"), ("B", "医生∶医院"), ("C", "努力∶成功"), ("D", "笔∶写字")),
         None, "unknown_stem", ["题干无词典证据", "诚实 failed 样本"], expected="failed"),
    item("lngb-syn-10", "找出下列词语的近义词：果断",
         opt(("A", "犹豫"), ("B", "干脆"), ("C", "安静"), ("D", "智慧")),
         "B", "synonym", ["近义词", "反义干扰"]),
    item("lngb-en-11", "Fish is to water as bird is to",
         opt(("A", "animal"), ("B", "sky"), ("C", "feather"), ("D", "reading")),
         "B", "en_analogy", ["英文 PLACE"]),
    item("lngb-en-12", "Happy is to glad as sad is to",
         opt(("A", "unhappy"), ("B", "big"), ("C", "glad"), ("D", "fast")),
         None, "en_analogy", ["sad 的反义项未以 syn/ant 登录选项词", "诚实 failed 样本"],
         expected="failed"),
]

VBLIND2 = [
    item("vb2-inf-01", "有消息称，某品牌新款轿车的百公里油耗降低了两升。测试机构尚未公布验证结果。"
                       "根据这段文字，以下哪项可以推出？",
         opt(("A", "新款轿车的百公里油耗降低了两升"),
             ("B", "有消息称新款轿车的百公里油耗降低了两升"),
             ("C", "测试机构拒绝了验证请求"), ("D", "该油耗数据来自官方发布")),
         "B", "inference", ["归属", "盲集2"], skill=V),
    item("vb2-inf-02", "监测数据显示，该湖区候鸟数量今年创下新高。根据这段文字，以下哪项可以推出？",
         opt(("A", "该湖区候鸟数量今年创下新高"),
             ("B", "有报道称该湖区候鸟数量创下新高"),
             ("C", "候鸟数量增长将持续十年")),
         "A", "inference", ["数据断言", "盲集2"], skill=V),
    item("vb2-inf-03", "安监部门否认了关于该工厂已发生爆炸事故的说法。根据这段文字，以下哪项可以推出？",
         opt(("A", "该工厂发生了爆炸事故"), ("B", "该工厂没有发生爆炸事故"),
             ("C", "安监部门曾通报过该工厂")),
         None, "inference", ["否认归属", "双向不可推", "盲集2"], expected="failed", skill=V),
    item("vb2-inf-04", "据行业分析师称，本季度芯片库存已经恢复正常水平。根据这段文字，以下哪项最不能推出？",
         opt(("A", "本季度芯片库存已经恢复正常"),
             ("B", "有分析师声称本季度芯片库存恢复正常"),
             ("C", "该说法来自行业分析师")),
         "A", "inference", ["归属", "cannot 型", "盲集2"], skill=V),
    item("vb2-inf-05", "实验组小鼠在干预后体重下降。研究人员认为该机制与肠道菌群有关，但该假说尚待更大样本验证。"
                       "根据这段文字，以下哪项可以推出？",
         opt(("A", "该机制与肠道菌群有关"),
             ("B", "干预导致所有实验动物体重下降"),
             ("C", "实验组小鼠在干预后体重下降")),
         "C", "inference", ["混合断言层", "盲集2"], skill=V),
    item("vb2-main-06", "许多城市试点潮汐车道，早晚高峰方向随流量调整，通行效率提升约一成。但也有批评者指出维护成本上升。"
                        "这段文字意在说明？",
         opt(("A", "潮汐车道有收益也伴随成本争议"), ("B", "潮汐车道应全面推广"),
             ("C", "通行效率提升约一成"), ("D", "维护成本压垮试点")),
         "A", "main_idea", ["主旨", "盲集2"], skill=V),
    item("vb2-clo-07", "依次填入划横线部分最恰当的一项是：他对规则的______并非出于无知，而是精心算计。",
         opt(("A", "漠视"), ("B", "忽视"), ("C", "无视"), ("D", "轻视")),
         None, "cloze", ["搭配", "盲集2", "近义辨析超出搭配表子集"], expected="failed", skill=V),
    item("vb2-arg-08", "某报告称降糖药 X 可显著减重。以下哪项如果为真，最能削弱上述结论？",
         opt(("A", "该报告样本仅十二人且无对照组"),
             ("B", "药 X 的价格不菲"), ("C", "医生常开药 X"), ("D", "降糖是该药的主要适应症")),
         "A", "argument", ["削弱", "盲集2", "证据质量攻击"], skill=V),
    item("vb2-inf-09", "民间流传着“艾灸可缓解近视”的说法，眼科医生对此表示无法证实。根据这段文字，以下哪项可以推出？",
         opt(("A", "艾灸可以缓解近视"), ("B", "艾灸不能缓解近视"),
             ("C", "眼科医生对流传的说法表示无法证实")),
         "C", "inference", ["传闻+无法证实", "盲集2"], skill=V),
    item("vb2-inf-10", "气象预报显示明天有暴雨，户外演出因此可能取消。根据这段文字，以下哪项可以推出？",
         opt(("A", "明天有暴雨"), ("B", "户外演出一定取消"),
             ("C", "所有演出都将改期")),
         "A", "inference", ["预报断言层", "情态", "盲集2"], skill=V),
]


def _rows(items):
    return "\n".join(json.dumps(it, ensure_ascii=False, sort_keys=True) for it in items) + "\n"


def _write_bytes(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))   # LF 行尾：sha 对账以文件字节为准


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(publish_blind: bool = False) -> None:
    d = OUT_LANG / "dev"
    d.mkdir(parents=True, exist_ok=True)
    _write_bytes(d / "language.jsonl", _rows(DEV))
    dev_sha = _sha(_rows(DEV))
    manifest = {
        "name": "benchmark_language",
        "data_class": "human-written（按七类关系×方向×判别性网格独立编写；期望在模型运行前确定）",
        "created_at": CREATED,
        "splits": {
            "dev": {"file": "dev/language.jsonl", "count": len(DEV), "sha256": dev_sha,
                    "policy": "R3c 迭代期间可调参；重生成即更新 sha256"},
        },
        "independence_declaration": (
            "题目由人工按关系网格独立编写，未按 Solver 是否解对筛除失败样本"
            "（多重匹配/词典外题干作为诚实 failed 样本保留）；"
            "blind 与 verbal_blind2 在 dev 工作完成后生成并冻结，只允许首次且唯一一次评测；"
            "此后不得依据盲集表现修改规则或重生成盲集。"),
    }
    (OUT_LANG / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[language] dev {len(DEV)} 题 sha={dev_sha[:16]} publish_blind={publish_blind}")
    if not publish_blind:
        return
    for d, items, fname, name in (
            (OUT_LANG / "blind", BLIND, "language.jsonl", "language_blind"),
            (OUT_VBL2 / "blind2", VBLIND2, "verbal.jsonl", "verbal_blind2"),
    ):
        target = d / fname
        if target.exists():
            raise SystemExit(f"盲集已存在，禁止重写：{target}")
        d.mkdir(parents=True, exist_ok=True)
        raw = _rows(items)
        _write_bytes(target, raw)
        print(f"[blind-freeze] {name} {len(items)} 题 sha256={_sha(raw)}")
    man = json.loads((OUT_LANG / "manifest.json").read_text(encoding="utf-8"))
    man["splits"]["blind"] = {"file": "blind/language.jsonl", "count": len(BLIND),
                              "sha256": _sha(_rows(BLIND)),
                              "policy": "冻结：首次且唯一一次评测；不得重生成或回写"}
    man["splits"]["verbal_blind2"] = {"file": "../benchmark_verbal/blind2/verbal.jsonl",
                                      "count": len(VBLIND2), "sha256": _sha(_rows(VBLIND2)),
                                      "policy": "冻结：首次且唯一一次评测；含归属机制新题"}
    (OUT_LANG / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish-blind", action="store_true")
    a = ap.parse_args()
    main(a.publish_blind)
