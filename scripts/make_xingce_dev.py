# -*- coding: utf-8 -*-
"""R6 任务C：xingce_dev —— R6 规则开发专用集（**不是** held-out，可反复使用）。

纪律：
- 与 R5 冻结测试集**零近重复**（构建时对含 benchmark_xingce 的全 corpus 做泄漏检查；
  场景/句式/数字全部独立设计，禁止 R5 换数字）；
- 金标 BY CONSTRUCTION：分数链在脚本内用 Fraction 复算断言，先于渲染固定；
- 本集用于规则开发与回归，允许当前失败题（tag=dev-target 标记本轮开发靶点）。

运行：python -X utf8 scripts/make_xingce_dev.py
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import numpy as np  # noqa: E402

import make_figtrans_benchmark as ft  # noqa: E402
from make_xingce_benchmark import (  # noqa: E402
    CELL, PAGE_W, font, render_text_page, render_figure_page, save_page, crop_save,
    wrap_text, opts4, opts_shuffled, label_of, norm,
)

DATA_DIR = ROOT / "data"
DEV_DIR = DATA_DIR / "benchmark_xingce_dev"
SPLIT = "dev"

# ------------------------------------------------------------------ 金标算式（复算断言）
F = Fraction
assert 100 * F(9, 10) * F(95, 100) == F(171, 2)                     # 85.5
assert 200 * F(5, 4) * F(9, 10) == 225                              # 提价25%后9折
assert 300 * F(12, 100) / 360 == F(1, 10)                           # 加水稀释 10%
assert (200 * F(15, 100) + 10) / 210 == F(4, 21)                    # ≈19.05%
assert F(240, 100) == F(12, 5)                                      # 相遇 2.4h
assert 40 * 10 / (120 - 40) == 5                                    # 追及 5min
assert 360 * F(3, 9) == 120                                         # 比例分配
assert 6 * 5000000 == 30000000                                      # 300km
assert F(100, 80) - 1 == F(1, 4)                                    # 25%
assert F(115, 100) * F(110, 100) - 1 == F(53, 200)                  # 26.5%
assert F(23, F(115, 100)) == 20                                     # 基期 20 亿


def mk_row(qid, skill, stem, options, answer, rule, why, expected="solved",
           answerability=None, tags_extra=()):
    opts = [{"label": l, "text": t} for (l, t) in options]
    row = {"id": qid, "skill": skill, "stem": stem, "options": opts,
           "answer": answer, "expected_status": expected,
           "group": f"g-dev-{rule}", "tags": ["xingce", "dev", skill, rule, "dev-target", *tags_extra],
           "rationale": why}
    if answerability:
        row["answerability"] = answerability
    return row


def nc_rows():
    def mk(qid, stem, gold, distr, order, rule, why):
        options = opts_shuffled(gold, distr, order)
        return mk_row(qid, "numeric_calculation", stem, options,
                      label_of(options, gold), rule, why)
    return [
        mk("xd-nc-01", "笔记本原价 100 元，先降价 10%，又在此基础上降价 5%，最终价格是多少元？",
           "85.5", ["90.5", "85", "94.5"], [0, 2, 1, 3], "multi-discount",
           "100×0.9×0.95=85.5（两段连续百分比，单段计划必漏数）"),
        mk("xd-nc-02", "某商品按成本 200 元加价 25% 定价，之后按定价打九折出售，实际售价是多少元？",
           "225", ["220", "250", "198"], [1, 0, 3, 2], "markup-discount",
           "200×1.25×0.9=225（先涨后折混合链）"),
        mk("xd-nc-03", "300 克浓度 12% 的盐水中加入 60 克水，浓度变为多少？",
           "10%", ["12%", "11%", "9%"], [0, 3, 1, 2], "dilution",
           "盐 300×12%=36 克；36÷360=10%（溶质守恒+总质量增加）"),
        mk("xd-nc-04", "200 克浓度 15% 的盐水中加入 10 克盐，浓度约为多少？",
           "19.0%", ["20%", "15%", "17.1%"], [3, 0, 1, 2], "concentration-add",
           "(200×15%+10)÷210=40÷210≈19.05%→约19.0%"),
        mk("xd-nc-05", "A、B 两地相距 240 千米，甲车以 60 千米/小时、乙车以 40 千米/小时同时相向出发，几小时后相遇？",
           "2.4", ["3", "2", "2.6"], [2, 0, 3, 1], "meeting",
           "240÷(60+40)=2.4 小时"),
        mk("xd-nc-06", "弟弟步行 40 米/分先出发 10 分钟，哥哥骑车 120 米/分沿同路追及，多少分钟追上？",
           "5", ["6", "4", "8"], [0, 1, 3, 2], "catch-up",
           "领先 400 米；400÷(120-40)=5 分钟"),
        mk("xd-nc-07", "把 360 本图书按 2∶3∶4 分给三个班级，中间那个班分得多少本？",
           "120", ["80", "160", "90"], [0, 2, 3, 1], "ratio-share",
           "360×3/9=120"),
        mk("xd-nc-08", "在 1∶5000000 的地图上，两城图上距离 6 厘米，实际距离是多少千米？",
           "300", ["120", "60", "500"], [1, 0, 3, 2], "scale",
           "6×5000000=30000000 厘米=300 千米"),
        mk("xd-nc-09", "某商品现价比原价降低了 20%。要恢复原价，需要提价多少？",
           "25%", ["20%", "30%", "16.7%"], [0, 1, 2, 3], "reverse-percent",
           "0.8×(1+r)=1 → r=25%（基数换到降价后）"),
        mk("xd-nc-10", "2022 年营业收入比 2021 年增长 15%，2023 年比 2022 年增长 10%。2023 年比 2021 年增长约：",
           "26.5%", ["25%", "27.5%", "24%"], [0, 3, 1, 2], "compound-growth",
           "1.15×1.10-1=26.5%"),
        # 正控制（误杀哨兵）：规则应能完整计算并释放
        mk("xd-nc-19", "一件商品原价 150 元，打八折后的现价是多少元？",
           "120", ["130", "125", "30"], [0, 3, 1, 2], "single-discount-control",
           "150×0.8=120：单步折扣、数量全消费、结果超出题干等值类——必须正常释放"),
        mk("xd-nc-20", "每箱零件 25 个，14 箱共有多少个零件？",
           "350", ["345", "360", "330"], [1, 0, 2, 3], "unit-count-control",
           "25×14=350：数量×份数型完整消费（误杀哨兵）"),
        mk("xd-nc-21", "车间原计划 8 天生产 960 个零件，实际每天比原计划多生产 20 个，实际每天生产多少个？",
           "140", ["120", "130", "160"], [2, 0, 3, 1], "two-step-dev",
           "960÷8=120→120+20=140（两步链靶点：当前单步能力会被拦下，属预期弃权）"),
    ]


def da_rows():
    mat_a = ["2023 年某区文化体育支出 23 亿元，比上年增长 15%。",
             "同年教育支出 41 亿元，增长 8%。"]
    mat_b = ["某景区 2023 年全年接待游客 90 万人次。其中四月份接待 21 万人次，",
             "五月份因天气原因接待量同比下降 20%。"]
    t = [["年份", "营业收入(万元)"], ["2021", "320"], ["2022", "400"]]
    opts11 = opts_shuffled("20", ["18", "21", "17.4"], [0, 2, 1, 3])
    opts12 = opts_shuffled("23.3%", ["21.0%", "25.0%", "20.0%"], [0, 2, 1, 3])
    return [
        {"id": "xd-da-11", "skill": "data_analysis",
         "stem": "根据以下资料回答问题。2022 年该区文化体育支出约为多少亿元？",
         "options": [{"label": l, "text": tx} for (l, tx) in opts11],
         "answer": label_of(opts11, "20"), "expected_status": "solved", "group": "g-dev-da-text",
         "tags": ["xingce", "dev", "data_analysis", "text-material", "dev-target"],
         "rationale": "23÷1.15=20 亿元（文字材料基期量；当前工具不支持无表材料——本轮开发靶点）",
         "_material": mat_a},
        {"id": "xd-da-12", "skill": "data_analysis",
         "stem": "根据以下资料回答问题。2023 年四月份接待量约占全年的：",
         "options": [{"label": l, "text": tx} for (l, tx) in opts12],
         "answer": label_of(opts12, "23.3%"), "expected_status": "solved", "group": "g-dev-da-text",
         "tags": ["xingce", "dev", "data_analysis", "text-material", "dev-target"],
         "rationale": "21÷90=23.33%→23.3%", "_material": mat_b},
        {"id": "xd-da-13", "skill": "data_analysis",
         "stem": "依据下表数据回答本题。2022 年营业收入比 2021 年增长多少万元？",
         "options": [{"label": l, "text": tx} for (l, tx) in opts4("80", ["20%", "25%", "72"])],
         "answer": "A", "expected_status": "solved", "group": "g-dev-da-tbl",
         "tags": ["xingce", "dev", "data_analysis", "growth-amount", "dev-target"],
         "rationale": "400-320=80 万元（增长量；R5 曾暴露 growth_amount 操作未支持）",
         "_table": t},
    ]


def lg_rows():
    return [
        mk_row("xd-lg-14", "logical_reasoning",
               "所有通过笔试的员工都参加了培训。张明没有参加培训。由此可以推出：",
               [("A", "张明通过了笔试"), ("B", "张明没有通过笔试"),
                ("C", "无法确定张明是否通过笔试"), ("D", "参加培训的都是通过笔试的")],
               "B", "translation-contrapositive",
               "逆否命题：未参加培训→未通过笔试（翻译推理规则族——当前 z3 核仅排序域，本轮靶点）"),
        mk_row("xd-lg-15", "logical_reasoning",
               "只有提前预约的人才能进入展馆。李华进入了展馆，那么以下哪项必然为真？",
               [("A", "李华提前预约了"), ("B", "李华没有提前预约"),
                ("C", "提前预约的人都能进馆"), ("D", "进馆不一定需要预约")],
               "A", "translation-necessary",
               "只有P才Q：Q→P。李华进馆(Q)→已预约(P)"),
        mk_row("xd-ana-16", "language_reasoning",
               "剑∶打斗 与下列哪组词语的逻辑关系最为相似？",
               [("A", "弓箭∶靶心"), ("B", "餐具∶吃饭"), ("C", "毛笔∶书法"), ("D", "相机∶拍照")],
               None, "analogy-undecidable",
               "A/B/C/D 与'工具∶用途'层多组相容（餐具/毛笔/相机皆可代入）——"
               "不同抽象层级多解，无唯一客观金标 → 诚实系统应报告不可判定（answer=None）",
               expected="failed", answerability="undecidable"),
    ]


def vb_rows():
    """R6.1 补样本：2 道言语理解 + 1 道唯一答案语言推理（原 23 题缺 verbal 族）。"""
    return [
        mk_row("xd-vu-22", "verbal_understanding",
               "许多写字楼更换了节能照明系统后，由于使用习惯没有改变，省电效果并不明显。"
               "只有把系统升级和用能行为管理结合起来，才能达到预期的节能目标。"
               "这段文字的主旨是？",
               [("A", "节能照明系统的省电效果值得怀疑"),
                ("B", "只有把系统升级和用能行为管理结合起来，才能达到节能目标"),
                ("C", "写字楼员工的使用习惯亟待纠正"),
                ("D", "系统升级比行为管理更为重要")],
               "B", "main-idea",
               "'只有…才…'对策句为主旨（结论覆盖核对族；场景为 dev 新写）"),
        mk_row("xd-vu-23", "verbal_understanding",
               "一些自媒体为了博取关注（ ），对事件原貌进行夸张改编，完全背离了新闻报道的真实性要求。",
               [("A", "挺身而出"), ("B", "耸人听闻"), ("C", "当仁不让"), ("D", "据实直书")],
               "B", "cloze-idiom",
               "'耸人听闻'=故意夸大使人震惊，与'夸张改编'语境一致（区别于'骇人听闻'=事实本身可怕，"
               "经典易混对；成语搭配族 dev-target）"),
        mk_row("xd-lang-24", "language_reasoning",
               "笔∶文具 与下列哪组词语逻辑关系最为相似？",
               [("A", "桌子∶家具"), ("B", "轮胎∶橡胶"), ("C", "文具∶笔"), ("D", "医生∶医院")],
               "A", "analogy-genus-down",
               "GENUS_DOWN 唯一解：笔是文具、桌子是家具（词库 hyper 证据）；B 材料关系，"
               "C 方向相反，D 人物∶场所（补 dev 唯一答案类比缺口）"),
    ]


def ns_rows():
    return [
        mk_row("xd-ns-17", "number_series",
               "1/2，3/4，5/8，7/16，（ ）",
               [("A", "9/32"), ("B", "9/24"), ("C", "8/32"), ("D", "7/32")],
               "A", "fraction-series",
               "分子奇数列 1/3/5/7/9，分母等比 ×2 → 9/32（分数列当前缺口）"),
        mk_row("xd-ns-18", "number_series",
               "2，3，5，7，11，（ ）",
               [("A", "13"), ("B", "12"), ("C", "14"), ("D", "15")],
               "A", "prime-series",
               "素数递增列 → 13（素数族当前缺口）"),
    ]


def fig_rows():
    r90 = ft.rot90cw
    arr = ft.shape_arrow()

    def dots_tri(n: int) -> np.ndarray:
        m = np.zeros((CELL, CELL), dtype=bool)
        yy, xx = np.mgrid[0:CELL, 0:CELL]
        pos = []
        for r in range(5):
            for c in range(r + 1):
                pos.append((8 + r * 9, 24 - r * 4.5 + c * 9))
        for cy, cx in pos[:n]:
            m |= ((yy - int(cy)) ** 2 + (xx - int(cx)) ** 2) <= 12
        return m

    rows_ = []
    o1 = {"A": dots_tri(10), "B": dots_tri(8), "C": dots_tri(9), "D": dots_tri(12)}
    assert len({m.tobytes() for m in o1.values()}) == 4, "fig-19 选项必须互异"
    rows_.append(({"id": "xd-fig-19", "skill": "figure_reasoning",
                    "stem": "元素个数递增（三角形数）。按规律，问号处应填入哪个图形？",
                    "options": [{"label": l, "text": "图形选项"} for l in sorted(o1)],
                    "answer": "A", "expected_status": "solved", "layout": "seq",
                    "group": "g-dev-fig-quantity",
                    "tags": ["xingce", "dev", "figure_reasoning", "quantity-count", "dev-target"],
                    "rationale": "1/3/6→三角形数第4项 10 个点（计数族当前缺口）",
                    "_fig": {"cells": [dots_tri(1), dots_tri(3), dots_tri(6), None],
                             "grid": [1, 4], "options": o1}}))

    exps = [[0, 1, 2], [2, 3, 4], [4, 5, 6]]
    def cell_at(i, j):
        e = exps[i][j] % 4
        m = arr
        for _ in range(e):
            m = r90(m)
        return m
    cells = [[cell_at(i, j) for j in range(3)] for i in range(3)]
    flat = [cells[i][j] for i in range(3) for j in range(3)]
    flat[8] = None
    gold = cells[2][2]
    o2 = {"A": cell_at(2, 1), "B": gold, "C": cell_at(0, 0), "D": cell_at(1, 1)}
    assert len({m.tobytes() for m in o2.values()}) == 4, "fig-20 选项必须互异"
    rows_.append({"id": "xd-fig-20", "skill": "figure_reasoning",
                  "stem": "每行按四分之一转递进，行间再叠半转偏移。按规律，问号处应填入哪个图形？",
                  "options": [{"label": l, "text": "图形选项"} for l in sorted(o2)],
                  "answer": "B", "expected_status": "solved", "layout": "grid9",
                  "group": "g-dev-fig-compound",
                  "tags": ["xingce", "dev", "figure_reasoning", "compound-rule", "dev-target"],
                  "rationale": "格(i,j)=旋转(2i+j)·90°；(2,2)→180°（双参数复合律当前缺口）",
                  "_fig": {"cells": flat, "grid": [3, 3], "options": o2}})
    return rows_


# ------------------------------------------------------------------ 装配（dev 根）
def assemble(rows):
    img_dir = DEV_DIR / "images" / SPLIT
    reg_dir = DEV_DIR / "regions"
    out = []
    for row in rows:
        qid = row["id"]
        internal = {k: v for k, v in list(row.items()) if k.startswith("_")}
        for k in internal:
            del row[k]
        row.setdefault("layout", "text")
        row["answerability"] = row.get("answerability",
                                       "has_unique_answer" if row.get("answer") else "undecidable")
        if "_fig" in internal:
            spec = internal["_fig"]
            cells, grid, options, labels = spec["cells"], spec["grid"], spec["options"], sorted(spec["options"])
            intro = wrap_text(row["stem"])[:2]
            ink, regions = render_figure_page(cells, grid[0], grid[1], options, labels, intro)
            save_page(img_dir / f"{qid}.png", ink)
            row["image"] = f"benchmark_xingce_dev/images/{SPLIT}/{qid}.png"
            x, y, w, h = regions["stem_matrix_bbox"]
            crop_save(ink, reg_dir / qid / "stem.png", [x, y, w, h])
            regions["stem_matrix_crop"] = f"regions/{qid}/stem.png"
            for op in regions["option_regions"]:
                bx, by, bw, bh = op["bbox"]
                crop_save(ink, reg_dir / qid / f"option_{op['label']}.png", [bx, by, bw, bh])
                op["crop_path"] = f"regions/{qid}/option_{op['label']}.png"
            row["regions"] = regions
            row["grid"] = grid
        else:
            material = internal.get("_material")
            table = internal.get("_table")
            stem_lines = wrap_text(row["stem"])
            opts_pairs = [(o["label"], o["text"]) for o in row["options"]]
            ink, regions = render_text_page(stem_lines, opts_pairs, material=material, table=table)
            save_page(img_dir / f"{qid}.png", ink)
            row["image"] = f"benchmark_xingce_dev/images/{SPLIT}/{qid}.png"
            row["regions"] = regions
            if table is not None:
                row["layout"] = "table"
            elif material is not None:
                row["layout"] = "text-material"
        row["provenance"] = {"kind": "project_authored", "note": "R6 开发集，不参与 held-out 纪律"}
        row["source"] = "benchmark_xingce_dev/dev"
        row["capability_status"] = "supported"
        out.append(row)
    return out


def leak_against_xingce(rows):
    """dev 不得从既有语料(含 R5 测试)换数字/换词复用：比较去除指令模板后的实质内容。

    指令模板（'按规律，问号处应填入哪个图形'等）是命题规程的固定句式，不构成内容重复；
    实质内容（规律描述/题问句/材料句）参与双向包含检查。
    """
    tmpl = {norm(x) for x in (
        "按规律，问号处应填入哪个图形", "问号处应填入哪个图形",
        "依据下表数据回答本题", "根据以下资料回答问题", "根据以下材料回答问题",
        "从所给四个选项中选出最合适的一个", "选择最合适的一个",
    )}

    def content(stem: str) -> str:
        parts = [seg.strip() for seg in stem.split("。")]
        keep = [norm(seg) for seg in parts if seg and norm(seg) not in tmpl]
        return "".join(keep)

    hits = []
    ours = [content(r["stem"]) for r in rows]
    for path in DATA_DIR.rglob("*.jsonl"):
        if "benchmark_xingce_dev" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            other = content(str(payload.get("stem") or ""))
            if not other:
                continue
            for n, qid in zip(ours, [r["id"] for r in rows]):
                if n and (n == other or (len(n) >= 14 and (n in other or other in n))):
                    hits.append(f"{qid} ~ {path.name}:{payload.get('id')}")
    return sorted(set(hits))


def main():
    rows = nc_rows() + da_rows() + lg_rows() + vb_rows() + ns_rows() + fig_rows()
    rows = assemble(rows)
    hits = leak_against_xingce(rows)
    if hits:
        print("[dev] 近重复命中（必须消除）：")
        for h in hits:
            print("   ", h)
        sys.exit(1)
    test_dir = DEV_DIR / SPLIT
    test_dir.mkdir(parents=True, exist_ok=True)
    out = test_dir / "xingce_dev.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    import hashlib
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"[dev] {len(rows)} 题 sha={digest[:16]}")
    by = {}
    for r in rows:
        by[r["skill"]] = by.get(r["skill"], 0) + 1
    print("[dev] by skill:", by)
    print("[dev] leak vs 全语料(含R5): 0 hits")
    (DEV_DIR / "dev_info.json").write_text(json.dumps(
        {"split": "dev", "count": len(rows), "sha256": digest, "by_skill": by,
         "note": "R6 开发集：可反复用于规则开发与回归，不属于 held-out"},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
