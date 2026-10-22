# -*- coding: utf-8 -*-
"""R10-D：页面级图形区域定位器（确定性像素通道，v2）。

工单：图形突破 = 区域定位与内容完整性（不扩 Solver 规则）。
* 图形区/选项区**分别**定位：行带分离 → 带内列投影分格 → gap 聚类为单元
* 标签字母（A/B/C/D 单字）按墨迹高度比剪除，不进单元格框
* 文本行识别（窄行+高分段密度）与图形行区分，题干图形取选项带上方相邻图形带
* 空白格定位：矩阵等分格墨迹显著缺失（grid_blank_cells）
* 完整性：check_option_crop 墨迹/尺寸 + 触边信号；置信度随行记录
* 三坐标关联：bbox_page（原图）/ bbox_local（相对所属带）/ pixel_sha（裁剪内容哈希）
* 与 VLM 声明区域 IoU 对账：vlm+pixel_confirmed / vlm_disagreed / pixel_localizer
定位正确性口径 = **slot 指派 + 包含率**（检测框落入金标格），不拿墨迹外接框
硬比整格 IoU；自洽（同图两跑一致）单列指标，绝不冒充真实定位正确。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MIN_BAND_INK = 60
BLANK_MAX_INK = 15
GAP_TOL = 2
MERGE_GAP = 25           # 单元内列间隙上限（像素）
LABEL_H_RATIO = 0.55     # 组内墨迹高度 < 0.55*最大 视为标签字母
TEXT_MIN_SEGS = 9        # 文本行：小 gap 聚类后段数下限
TEXT_MAX_H = 30          # 文本行：带高上限


def _iou(a: Sequence[int], b: Sequence[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if ix2 <= ix or iy2 <= iy:
        return 0.0
    inter = (ix2 - ix) * (iy2 - iy)
    return inter / max(1, aw * ah + bw * bh - inter)


def _containment(inner: Sequence[int], outer: Sequence[int]) -> float:
    ix, iy = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2 = min(inner[0] + inner[2], outer[0] + outer[2])
    iy2 = min(inner[1] + inner[3], outer[1] + outer[3])
    inter = max(0, ix2 - ix) * max(0, iy2 - iy)
    return inter / max(1, inner[2] * inner[3])


def _crop_hash(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> str:
    return hashlib.sha256(np.ascontiguousarray(
        mask[y0:y1, x0:x1].astype(np.uint8)).tobytes()).hexdigest()[:16]


def row_bands(mask: np.ndarray, min_ink: int = MIN_BAND_INK) -> List[Tuple[int, int]]:
    prof = mask.sum(axis=1)
    present = prof >= 2
    out, y, h = [], 0, len(prof)
    while y < h:
        if not present[y]:
            y += 1
            continue
        start, gap, last = y, 0, y
        while y < h and (present[y] or gap < GAP_TOL):
            if present[y]:
                last, gap = y, 0
            else:
                gap += 1
            y += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def col_segments(mask: np.ndarray, y0: int, y1: int, min_col_ink: int = 2,
                 gap: int = 3, min_ink: int = 40) -> List[Tuple[int, int]]:
    prof = mask[y0:y1, :].sum(axis=0)
    present = prof >= min_col_ink
    out, x, w = [], 0, len(prof)
    while x < w:
        if not present[x]:
            x += 1
            continue
        start, g, last = x, 0, x
        while x < w and (present[x] or g < gap):
            if present[x]:
                last, g = x, 0
            else:
                g += 1
            x += 1
        if int(prof[start:last + 1].sum()) >= min_ink:
            out.append((start, last + 1))
    return out


def _split_point(prof: np.ndarray, a: int, b: int) -> Optional[int]:
    """区间 [a,b) 内最长空白列段中点；无 ≥3px 空段返回 None。"""
    best_len, best_start = 0, -1
    run = 0
    for x in range(a + 4, b - 4):
        if prof[x] == 0:
            run += 1
            if run > best_len:
                best_len, best_start = run, x - run + 1
        else:
            run = 0
    if best_len < 3:
        return None
    return best_start + best_len // 2


def _solidity(mask: np.ndarray, y0: int, y1: int, a: int, b: int) -> float:
    """子段墨量 / 自身墨迹紧框面积。弧线/箭头碎片 <0.3，实体图形 ≥0.3。"""
    sub = mask[y0:y1, a:b] > 0
    n = int(sub.sum())
    if n == 0:
        return 0.0
    ys, xs = sub.nonzero()
    box = (int(xs.max()) - int(xs.min()) + 1) * (int(ys.max()) - int(ys.min()) + 1)
    return n / box if box else 0.0



def _narrow_gap_split_point(prof: np.ndarray, mask: np.ndarray, a: int, b: int,
                            y0: int, y1: int) -> Optional[int]:
    """[a,b) 内找 ≥2px 全白列隙且两侧墨量≥30、宽≥8 的分裂点；否则 None。"""
    if b - a < 26:
        return None
    best_len, best_mid = 0, None
    run = 0
    for x in range(a + 6, b - 5):          # 两侧各留 ≥6px 边距，避免啃笔画端部
        if prof[x] == 0:
            run += 1
        else:
            if run >= 2 and run > best_len:
                mid = x - run // 2
                if (mid - a >= 8 and b - mid >= 8
                        and int(mask[y0:y1, a:mid].sum()) >= 30
                        and int(mask[y0:y1, mid:b].sum()) >= 30):
                    w_l, w_r = mid - a, b - mid
                    i_l = int(mask[y0:y1, a:mid].sum())
                    i_r = int(mask[y0:y1, mid:b].sum())
                    # R12-C：真“两图并一格”= 两半近似均衡（宽度比≥0.8、墨量比≥0.6）；
                    # 图形内部缝隙（头/杆、双弧）两侧不均衡 → 不分裂
                    if min(w_l, w_r) / max(w_l, w_r) >= 0.8 \
                            and min(i_l, i_r) / max(i_l, i_r) >= 0.6:
                        best_len, best_mid = run, mid
            run = 0
    return best_mid



def _option_band_cells(mask, y0, y1, expect):
    """R14-E：标签锚点选项带切分（真实截图选项带恢复，零金标零 case-id）。

    失败模式实测（R13G 真实截图，零 API）：选项带是"字母段+图形段"交替且
    间距不等（trans-07 段隙 [33,20,23,73,71,10]），固定 merge_gap 列聚类
    无法恢复 n 个选项；把字母当分隔物剪掉又造成"选项裁剪近空白"（overlay-12
    C 被剪成只剩点号、ink 16px）与"标签丢失只剩图形"（tight 页 0 选项）。
    **版式内置的标签字母才是切分锚点**：锚点→下一锚点即一个选项单元，
    标签与其图形同单元（Solver 消费前按 label 域剥离，标签不污染图形哈希）。
    判据（页内像素导出）：
      ①域尺寸取 tight 外接框——行占用数在竖条笔画虚报（trans-07 C 竖条
        boxh=20 实高 8）；
      ②字母候选=连通域高 8-16 且宽 5-18；逐高度层尝试：恰 expect 个、
        不触左缘（≥2px）、等距（0.6-1.5×中位 pitch）、锚点间与末锚后
        带墨 ≥20、**每单元恰 1 字母 + ≥1 图形域**（面积≥24 或宽≥10）、
        字母与所属图形间隙 ≤1.9×字母高（overlay-12 2×2：D 字母与末图形
        间隙 18>1.9×13 被拒，防把邻列首字母当本列标签）；
      ③多层同时成立=歧义→回退旧路径（宁可不切，绝不造数）。
    真实截图 5 页：mirror-04/rot90-01/trans-07/overlay-12 命中；tight 页
    无字母版式按判据正确回退（旧路径即可，其 0 选项缺陷另有成因，见回归）。
    """
    W = mask.shape[1]
    if not (2 <= expect <= 6) or (y1 - y0) > 40:
        return None
    from scipy import ndimage as ndi
    # 页级互证（R14-E）：选项字母与页面上方**文本行**的字母同高（dev15 与
    # 真实截图实测：题面文字行含"…A…"字母高 13，与选项字母一致；图形碎片
    # 层——rot180 弧片 12、grid-11 圆片 11——不在文本行高频出现）。
    # support=上方窄带（高 ≤20，真文本行带）窄域高度中频次 ≥3 者；字母层
    # 必须落在 support 内（消歧），support 空（无文本行页）退回纯几何判据。
    support: set = set()
    for (by0, by1) in row_bands(mask):
        if by0 >= y0 or (by1 - by0) > 20:
            continue
        sub = mask[by0:by1, :]
        if int(sub.sum()) < 40:
            continue
        lt, nt = ndi.label(sub, structure=np.ones((3, 3), int))
        if nt > 120:
            continue
        hs = []
        for s2 in range(1, nt + 1):
            ys2, xs2 = np.nonzero(lt == s2)
            h2 = int(ys2.max() - ys2.min() + 1)
            w2 = int(xs2.max() - xs2.min() + 1)
            if 6 <= h2 <= 18 and w2 <= 18:
                hs.append(h2)
        for v in set(hs):
            if hs.count(v) >= 3:
                support.add(v)
    lab, nc = ndi.label(mask[y0:y1, :], structure=np.ones((3, 3), int))
    if nc > 60:
        return None                              # 文本行碎域爆炸：非选项带
    band_hmax = 0
    for s in range(1, nc + 1):
        ys0 = np.nonzero(lab == s)[0]
        if len(ys0):
            band_hmax = max(band_hmax, int(ys0.max() - ys0.min() + 1))
    # R14-E 排布密度门：选项带是**单元级**排布（字母—图形—间隙，段隙中位
    # ≈ 域高量级），说明文字行是**字距级**排布（段隙 ≤ 半域高）。实测：
    # 4 张真实截图选项带段隙中位 22-30（带内最高域 13-28）；screens 页脚
    # 文字行中位 5（最高域 13）→ 阈值两侧余量 ≥8px，页级不变量非逐例调参。
    segs_all = col_segments(mask, y0, y1, min_col_ink=1, gap=2, min_ink=1)
    # 密度门只在**碎域成行**时才有意义：3 个及以上连通域（字距级排布会把每
    # 个字/每条竖线各自成域）；≤2 域的带（如 2 图题干）中位段隙来自单元间
    # 距，不是字距（实测 tight 页题干带 2 域、段隙 28、域高 22 → 旧判据误杀）。
    if len(segs_all) >= 2:
        gaps = [segs_all[i + 1][0] - segs_all[i][1]
                for i in range(len(segs_all) - 1)]
        if float(np.median(gaps)) < max(4.0, 0.5 * band_hmax):
            return None                          # 字距级排布=文本行，非选项带
    doms: List[Tuple[int, int, int, int]] = []   # (x0,x1,h,w)
    for s in range(1, nc + 1):
        ys, xs = np.nonzero(lab == s)
        doms.append((int(xs.min()), int(xs.max()) + 1,
                     int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)))
    frag = [d for d in doms if d[2] <= 4]
    layers: Dict[int, List[Tuple[int, int, int]]] = {}
    for x0, x1, h, w in doms:
        if 8 <= h <= 16 and 5 <= w <= 18 and (not support or h in support):
            layers.setdefault(h, []).append((x0, x1, h))
    def _try_layer(dom, h_key):
        dom = sorted(dom)
        if len(dom) != expect or dom[0][0] < 2:
            return None
        units = []
        for a, b, h in dom:
            nb = [f for f in frag if f[0] >= b - 1 and f[0] - b <= 8]
            units.append((a, max([b] + [f[1] for f in nb]), h))
        if dom[0][0] < 2:
            return None                      # 字母域触左缘=页眉字
        astart = [u[0] for u in units]
        diffs = [astart[i + 1] - astart[i] for i in range(len(astart) - 1)]
        pitch = float(np.median(diffs))
        if pitch <= 0:
            return None
        keep = [astart[0]]
        for st_ in astart[1:]:
            if 0.6 * pitch <= st_ - keep[-1] <= 1.5 * pitch:
                keep.append(st_)
        if len(keep) != expect:
            return None
        anchors = [(u[0], u[1]) for u in units]
        prof = mask[y0:y1, :].sum(axis=0)
        for i in range(expect - 1):
            if int(prof[anchors[i][1]:anchors[i + 1][0]].sum()) < 20:
                return None
        if int(prof[anchors[-1][1]:].sum()) < 20:
            return None
        step = (anchors[-1][0] - anchors[0][0]) / max(1, expect - 1)
        end = min(W, anchors[-1][1] + int(round(1.05 * max(20.0, step))))
        # 末单元右界按**本单元图形紧框**收口：等距节距外扩（1.05×step）在
        # 2×2 选项带（overlay-12 实测末带第二行左缘 A 标签 x=28、D 图形止
        # 于 340）会把邻列首标签吞进末选项。取 [末锚右缘, end) 内最后一个
        # 图形域右缘 +3 为止界；触带右缘视为真贴边（tight/rot90 末图 x1=W-2）。
        last_span = mask[y0:y1, anchors[-1][1]:end]
        nz_cols = np.nonzero(last_span.any(axis=0))[0]
        if len(nz_cols):
            last_right = anchors[-1][1] + int(nz_cols[-1]) + 1
            if last_right < end and (end - last_right) >= 6:
                nxt = [d for d in layers[h_key] if d[0] >= last_right and d[0] < end]
                if not nxt:
                    end = min(W, last_right + 3)
        cells = [(anchors[i][0], anchors[i + 1][0] if i + 1 < expect else end)
                 for i in range(expect)]
        # 标签↔所属图形绑定：图形**紧框**（单元内非字母非碎片域的并集框）
        # 须与字母同条文本行线上（|垂直中心差| ≤ 0.6×字母高）。页级不变量：
        # 行测选项的字母与图形同行居中（15 页 dev15 + 4 张真实截图实测：
        # 字母 cy≈17、图形 cy≈17；rot180 弧片 cy16 / 竖条 cy10 同过）——
        # 文本行碎片的垂直散布大，靠它当字母会在行线上找不到同伴图形。
        # 首版用"间隙 ≤1.9×字母高"误杀 trans-07（A 与图形间隙 33）与
        # overlay-12（C 间隙 30）：版式间距本就大于字母高，行线判据才稳。
        # 标签↔所属图形绑定（一一对应的几何面）：单元内所有"图形域"
        # （非该层字母域、面积 ≥24 或宽 ≥10）的**并集紧框**与字母同条文本
        # 行线上（|垂直中心差| ≤ 0.6×字母高 + 4px 行对齐容差）。overlay-12
        # C 单元含 B 图（cy246）+ 本图（cy253）并集中心 249.5，字母 cy253
        # → 过；首版只取"字母后第一个域"让 B 图把门判歪（30>24.7 误杀）。
        # 版式间距本就大于字母高（trans-07 A 间隙 33、overlay C 间隙 30），
        # 纯间隙门在真实排版上不成立，行线判据才稳。
        for (xa, xb), u in zip(cells, units):
            in_unit = [d for d in doms if d[0] >= xa and d[1] <= xb]
            letter_n = sum(1 for d in in_unit if d[2] == h_key and 5 <= d[3] <= 18)
            figs = [d for d in in_unit if d[0] >= u[1]
                    and (d[2] * d[3] >= 24 or d[3] >= 10)
                    and d[2] <= 40]            # 触带域=题干续行混入，不计图形
            if letter_n != 1:
                return None
            if not figs:
                # 唯一例外（scale-09/overlay-12 C）：单元内只有一个**完整**
                # 图形域（四边距带缘 ≥2px 且 ≥14px 高）被字母域吃掉——此时
                # 字母实为图形组件，须与单元内全部非字母域同条行线。
                wide_in = [d for d in in_unit if d[0] >= u[1]]
                if len(wide_in) == 1 and wide_in[0][2] >= 14:
                    figs = wide_in
                else:
                    return None
            rows_all = np.zeros((y1 - y0,), dtype=bool)
            for d in figs:
                rows_all |= mask[y0:y1, d[0]:d[1]].any(axis=1)
            fz = np.nonzero(rows_all)[0]
            if int(mask[y0:y1, u[1]:min(xb, W)].sum()) < 20:
                return None                      # 标签后须有真实图形墨
            fy = (int(fz.min()) + int(fz.max()) + 1) / 2 + y0
            letter_rows = np.nonzero(mask[y0:y1, u[0]:u[1]].any(axis=1))[0]
            ly = (int(letter_rows.min()) + int(letter_rows.max()) + 1) / 2 + y0
            if abs(fy - ly) > 0.6 * h_key + 4:
                return None
        return {"cells": cells, "labels": [(u[0], u[1]) for u in units]}

    sols = []
    for h0 in sorted(layers):
        sl = _try_layer(layers[h0], h0)
        if sl is None:
            continue
        # 2×2 选项带（overlay-12）：末列右侧邻着**下一行首列标签**，其域高
        # 常为行间距（20px>字母 13）而漏进字母层。锚点集取"末锚右侧不再
        # 存在同高字母域"的最大前缀子集，且子集内部仍等距/一一对应。
        xs = sorted(d[0] for d in layers[h0])
        drop = [i for i in range(len(xs) - 1)
                if xs[i + 1] > sl["cells"][-1][1]]      # 单元右界外的同高层域
        if drop:
            cut = min(drop) + 1
            if cut >= expect:
                sub = _try_layer(layers[h0][:cut], h0)
                if sub is not None:
                    sl = sub
        sols.append(sl)
    if len(sols) != 1:
        return None                              # 无解或歧义：回退旧路径
    return sols[0]


def cluster_cells(mask: np.ndarray, y0: int, y1: int, merge_gap: int = MERGE_GAP,
                  prune_labels: bool = True,
                  expect: Optional[int] = None) -> List[Tuple[int, int]]:
    """列分段 → gap 聚类 → 剪标签字母 →（可选）校正到 expect 个单元。"""
    segs = col_segments(mask, y0, y1)
    if not segs:
        return []
    groups: List[List[Tuple[int, int]]] = [[segs[0]]]
    for s in segs[1:]:
        if s[0] - groups[-1][-1][1] < merge_gap:
            groups[-1].append(s)
        else:
            groups.append([s])
    out = []
    for g in groups:
        if prune_labels and len(g) > 1:
            heights = [(mask[y0:y1, a:b].any(axis=1)).sum() for a, b in g]
            mx = max(heights)
            # 标签字母：窄段（≤18px 宽）且明显矮于组内最高段
            keep = [s for s, hgt in zip(g, heights)
                    if not ((s[1] - s[0]) <= 18 and hgt < 0.75 * mx)]
            g = keep or g
        out.append((g[0][0], g[-1][1]))
    # R12-C narrow-gap：≥2px 纯白列隙 + 两侧墨量/宽度双门槛才分裂。
    # 防“两个紧贴图形并成一格”（幻影格根源），也防碎单图形笔画。
    prof = mask[y0:y1, :].sum(axis=0)
    split_out: List[Tuple[int, int]] = []
    for a, b in out:
        mid = _narrow_gap_split_point(prof, mask, a, b, y0, y1)
        if mid is None:
            split_out.append((a, b))
        else:
            split_out.extend([(a, mid), (mid, b)])
    out = sorted(split_out)
    if expect:
        prof = mask[y0:y1, :].sum(axis=0)
        guard = 0
        # 超宽单元先分裂（跨格粘连：宽度 >1.9× 中位）
        while guard < 8:
            guard += 1
            ws = sorted(c[1] - c[0] for c in out)
            med = ws[len(ws) // 2] or 1
            over = [c for c in out if (c[1] - c[0]) > 1.9 * med and
                    _split_point(prof, c[0], c[1]) is not None]
            if not over:
                break
            a, b = max(over, key=lambda c: c[1] - c[0])
            mid = _split_point(prof, a, b)
            out.remove((a, b))
            out.extend([(a, mid), (mid, b)])
            out.sort()
        while len(out) < expect and guard < 16:
            guard += 1
            # 分裂最宽单元：在其内部最长空白列段处切开
            a, b = max(out, key=lambda c: c[1] - c[0])
            inner = prof[a:b]
            run_best, run_cur, start = (0, -1, -1), (0, -1, -1), None
            for i, v in enumerate(inner):
                if v == 0:
                    if start is None:
                        start = i
                    run_cur = (i - start + 1, start, i)
                    if run_cur[0] > run_best[0]:
                        run_best = run_cur
                else:
                    start = None
            if run_best[0] < 2 or run_best[1] == 0 or run_best[2] >= (b - a) - 1:
                break
            mid = a + run_best[1] + run_best[0] // 2
            out.remove((a, b))
            out.extend([(a, mid), (mid, b)])
            out.sort()
        while len(out) > expect and guard < 16:
            guard += 1
            gaps = [(out[i + 1][0] - out[i][1], i) for i in range(len(out) - 1)]
            gmin, i = min(gaps)
            out[i:i + 2] = [(out[i][0], out[i + 1][1])]
    return out


def is_text_band(mask: np.ndarray, y0: int, y1: int) -> bool:
    """文本行判据：带矮（≤24px）且出现**词组级宽簇**（字距小、gap15 并成 ≥70px 连排）
    或极多细段（≥12 段且中位宽 ≤14）。图形带单元簇宽 20-64px，不满足。"""
    if (y1 - y0) > 24:
        return False
    segs = col_segments(mask, y0, y1, min_col_ink=1, gap=2, min_ink=8)
    widths = sorted(b - a for a, b in segs)
    if len(segs) >= 12 and widths and widths[len(widths) // 2] <= 14:
        return True
    clusters = cluster_cells(mask, y0, y1, merge_gap=15, prune_labels=False)
    return any((b - a) >= 70 for a, b in clusters)


def localize_page(mask: np.ndarray, n_options: int,
                  vlm_options: Optional[List[Dict[str, Any]]] = None,
                  vlm_stem: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    H, W = mask.shape
    bands = row_bands(mask)
    vlm_options = vlm_options or []
    notes: List[str] = []
    result: Dict[str, Any] = {"bands": [{"y0": a, "y1": b} for a, b in bands],
                              "stem_regions": [], "option_regions": [],
                              "blank_cells": [], "notes": notes,
                              "image_size": [int(W), int(H)]}
    if not bands:
        notes.append("空图")
        return result

    # ---- 选项带识别（R14-E 根修）：固定"最后一带"在真实排版上不成立——
    # 题干续行/页脚会排在选项带之后（trans-07 实测选项带是倒数第二带，
    # 末带是"？"行；--tight 页末带为空行）→ 旧路径直接 options_band_not_detected
    # → 0 选项。改为：从底部向上扫描**所有带**，逐带取首个"单元数恰 n_options"
    # 的切分（锚点优先，其次 natural/宽度校正），带簇（末两带拼 2×2）保留给
    # 双列版式。选择顺序仍是底部优先（最像选项带者先中），不引入金标。
    # ---- 选项带：候选集与 HEAD 完全一致（末带 / 末两带拼 2×2），
    #      R14-E 标签锚点只在同一候选集内**优先**替换切分方式。
    opt_cells: Optional[List[Tuple[Tuple[int, int], Tuple[int, int]]]] = None  # ((x0,x1),(y0,y1))
    opt_span: Optional[Tuple[int, int]] = None
    label_boxes: Optional[List[Tuple[int, int]]] = None   # R14-E 标签一一对应

    def try_band_pair(bidx: List[int]) -> Optional[List]:
        nonlocal label_boxes
        cells = []
        for bi in bidx:
            y0, y1 = bands[bi]
            natural = cluster_cells(mask, y0, y1)
            exp = n_options if len(bidx) == 1 else None
            got = None
            if exp is not None:
                # R14-E：标签锚点切分（字母域一一对应，自带 expect 校验）
                ab = _option_band_cells(mask, y0, y1, exp)
                if ab is not None:
                    label_boxes = ab["labels"]
                    for cx0, cx1 in ab["cells"]:
                        cells.append(((cx0, cx1), (y0, y1)))
                    continue
            if got is None:
                # 岛数下界**不采用**（对照实测：加界后紧页末带 3 簇→expect 4
                # 被拒，退到"末两带"候选 → 题干第二行被选成选项带 → 题干只剩
                # 1 行 → 1×4 假格阵且可释放；不加界时与 HEAD 完全一致
                # （opts band=(150,188)、stem 2×3、test_r13a 5 passed）。
                # 页级安全由锚点路径自身（字母↔图形一一对应）与格证据通道
                # 的行数互证承担，不在聚类数量上做手脚。
                got = natural if exp is None else cluster_cells(mask, y0, y1, expect=exp)
            if not got:
                return None                      # 本候选带无单元
            # 文本否决只在**自然聚类数不符**时生效（数都对上就不是文字行）
            if exp is not None and len(natural) != n_options and is_text_band(mask, y0, y1):
                return None
            if label_boxes is not None and exp is None:
                label_boxes = None            # 2×2 簇路径无锚点：不得沿用标签框
            for cx0, cx1 in got:
                cells.append(((cx0, cx1), (y0, y1)))
        return cells if len(cells) == n_options else None

    for bidx in ([len(bands) - 1], [len(bands) - 2, len(bands) - 1]):
        if len(bidx) > 1 and len(bands) < 2:
            continue
        got = try_band_pair(bidx)
        if got:
            opt_cells, opt_span = got, (bands[bidx[0]][0], bands[bidx[-1]][1])
            break
    if opt_cells is None:
        notes.append("options_band_not_detected")
    # ---- 图形带：选项带上方、非文本、与紧邻带 x 范围重叠者可拼多行矩阵
    hi = len(bands)
    if opt_cells:
        first_opt_band = min(bi for bi, b in enumerate(bands)
                             if any(bc[1] == b for bc in opt_cells))
        hi = first_opt_band
    fig_band_groups: List[List[int]] = []
    fig_band_groups_x: List[Tuple[int, int]] = []
    for bi in range(hi):
        y0, y1 = bands[bi]
        if is_text_band(mask, y0, y1):
            continue
        segs = cluster_cells(mask, y0, y1, merge_gap=15)
        if not segs:
            continue
        if fig_band_groups:
            py0, py1 = bands[fig_band_groups[-1][-1]]
            px0, px1 = fig_band_groups_x[-1]
            gx0, gx1 = segs[0][0], segs[-1][1]
            overlap = _iou([px0, 0, px1 - px0, 1], [gx0, 0, gx1 - gx0, 1])
            if (y0 - py1) <= 40 and overlap > 0.5:
                fig_band_groups[-1].append(bi)
                fig_band_groups_x[-1] = (min(px0, gx0), max(px1, gx1))
                continue
        fig_band_groups.append([bi])
        fig_band_groups_x.append((segs[0][0], segs[-1][1]))
    stem_regions = []
    for group, xr in zip(fig_band_groups, fig_band_groups_x):
        y0, y1 = bands[group[0]][0], bands[group[-1]][1]
        row_cells = []
        for gbi in group:
            b = bands[gbi]
            segs2 = col_segments(mask, b[0], b[1], min_ink=8)  # 低阈含"?"小墨
            merged: List[Tuple[int, int]] = [segs2[0]] if segs2 else []
            for s in segs2[1:]:
                if s[0] - merged[-1][1] < 15:
                    merged[-1] = (merged[-1][0], s[1])
                else:
                    merged.append(s)
            # R12-C：本路径同样应用窄隙保守分裂（mirror-05 实测 13px 白隙被
            # merge<15 吞并 → 过合并幻影格的根源）
            prof2 = mask[b[0]:b[1], :].sum(axis=0)
            split_merged: List[Tuple[int, int]] = []
            for a0, a1 in merged:
                mid = _narrow_gap_split_point(prof2, mask, a0, a1, b[0], b[1])
                if mid is None:
                    split_merged.append((a0, a1))
                else:
                    split_merged.extend([(a0, mid), (mid, a1)])
            row_cells.append(split_merged)
        cols = max(len(rc) for rc in row_cells) if row_cells else 0
        cells_out = []
        for ri, rc in enumerate(row_cells):
            for ci, (cx0, cx1) in enumerate(rc):
                b = bands[group[ri]]
                ink = int(mask[b[0]:b[1], cx0:cx1].sum())
                cells_out.append({"row": ri, "col": ci,
                                  "bbox_page": [cx0, b[0], cx1 - cx0, b[1] - b[0]],
                                  "ink": ink, "blank_candidate": ink <= BLANK_MAX_INK * 3,
                                  "pixel_sha": _crop_hash(mask, cx0, b[0], cx1, b[1])})
        bbox = [xr[0], y0, xr[1] - xr[0], y1 - y0]
        rec: Dict[str, Any] = {"bbox_page": bbox, "grid": [len(group), cols],
                               "cells": cells_out,
                               "pixel_sha": _crop_hash(mask, xr[0], y0, xr[1], y1),
                               "source": "pixel_localizer"}
        for c in cells_out:
            if c["blank_candidate"]:
                result["blank_cells"].append({"row": c["row"], "col": c["col"],
                                              "bbox_page": c["bbox_page"],
                                              "ink": c["ink"], "pixel_sha": c["pixel_sha"]})
        if vlm_stem:
            rec["vlm_iou"] = round(_iou(vlm_stem, bbox), 3)
            agree = rec["vlm_iou"] >= 0.5 or _containment(bbox, vlm_stem) >= 0.8
            rec["vlm_slot_agree"] = bool(agree)
            rec["source"] = ("vlm+pixel_confirmed" if agree else "vlm_disagreed")
        stem_regions.append(rec)
    result["stem_regions"] = stem_regions

    # ---- 选项区记录
    if opt_cells:
        from .diagram_layout import check_option_crop
        for i, ((x0, x1), (y0, y1)) in enumerate(opt_cells):
            crop = mask[y0:y1, x0:x1]
            content_only = label_boxes is not None
            if content_only:
                lx0, lx1 = label_boxes[i]
                crop = crop.copy()
                crop[:, max(0, lx0 - x0):max(0, lx0 - x0) + (lx1 - lx0)] = False
            comp = check_option_crop(crop, content_only=content_only)
            rec: Dict[str, Any] = {
                "index": i, "bbox_page": [x0, y0, x1 - x0, y1 - y0],
                "bbox_local": [x0, 0, x1 - x0, y1 - y0], "band": [y0, y1],
                "ink": int(crop.sum()), "completeness": comp,
                "touches_border": bool(x0 <= 1 or x1 >= W - 1 or y1 >= H - 1),
                "pixel_sha": _crop_hash(mask, x0, y0, x1, y1),
                "source": "pixel_localizer",
                "confidence": round(min(1.0, comp["ink_ratio"] * 12) *
                                    (1.0 if comp["passed"] else 0.4), 3)}
            if label_boxes is not None:
                # R14-E：锚点切分单元=标签+图形同单元；标签框单独随行，
                # 下游可把标签从内容里剥出（一一对应已由切分门控）。
                lx0, lx1 = label_boxes[i]
                rec["label_bbox"] = [lx0, y0, lx1 - lx0, y1 - y0]
                rec["cell_bbox"] = [x0, y0, x1 - x0, y1 - y0]
                rec["provenance"] = "localizer+label_anchor"
                rec["has_letter_labels"] = True
            elif len(opt_cells) > 1:
                # R14-E 选项带**标签存在性**页级实测（无锚点路径：只如实标注，
                # **不改几何、不新增否决**）。真选项带每个单元都是"标签在左 +
                # 真空隙 + 图形在右"（dev grid-10 实测首段 w 8-15、h 13、其后
                # gap 2-17；真实截图 4 页锚点同构）。若**任一**单元首段就是
                # 图形本体（首段宽 ≥0.9×其行高，即竖框/圆环类；或该单元只有
                # 一个域、左侧无独立小段），本带**不含标签**，标签↔图形一一
                # 对应无从建立 → has_letter_labels=False。tight 排版页实测
                # 3/3、4/4 单元首段宽=单元宽（图形本体），判 False 正确。
                widths = sorted(cx1 - cx0 for (cx0, cx1), _ in opt_cells)
                med_w = widths[len(widths) // 2] or 1
                no_label = False
                for (a0, a1), (_y0, _y1) in opt_cells:
                    seg = [g for g in col_segments(mask, _y0, _y1,
                                                   min_col_ink=1, gap=1,
                                                   min_ink=1)
                           if g[0] >= a0 and g[1] <= a1]
                    if not seg:
                        continue
                    g0 = seg[0]
                    ys3 = np.nonzero(mask[_y0:_y1, g0[0]:g0[1]].any(axis=1))[0]
                    h3 = int(ys3.max() - ys3.min() + 1) if len(ys3) else 0
                    w3 = g0[1] - g0[0]
                    if (w3 >= 0.9 * max(h3, 1) and w3 >= 0.6 * med_w) \
                            or len(seg) == 1:
                        no_label = True
                        break
                rec["has_letter_labels"] = not no_label
            bx, by, bw, bh = rec["bbox_page"]
            cx, cy = bx + bw / 2, by + bh / 2
            covering = [v for v in vlm_options
                        if v.get("bbox") and v["bbox"][0] <= cx < v["bbox"][0] + v["bbox"][2]
                        and v["bbox"][1] <= cy < v["bbox"][1] + v["bbox"][3]]
            if covering:
                best = covering[0]
                vb = best["bbox"]
                slot_agree = _containment(rec["bbox_page"], vb) >= 0.5
                iou = _iou(vb, rec["bbox_page"])
                rec["vlm_iou"] = round(iou, 3)
                rec["vlm_slot_agree"] = bool(slot_agree)
                rec["source"] = ("vlm+pixel_confirmed" if (iou >= 0.5 or slot_agree)
                                 else "vlm_disagreed")
            result["option_regions"].append(rec)
    return result


def grid_blank_cells(mask: np.ndarray, stem_bbox: Sequence[int],
                     rows: int, cols: int) -> List[Dict[str, Any]]:
    """矩阵等分格中的空白格（补全题的 "?" 位）：墨迹显著缺失。"""
    x, y, w, h = stem_bbox
    cw, ch = w / max(1, cols), h / max(1, rows)
    inks = []
    for r in range(rows):
        inks.append([int(mask[int(y + (r + .25) * ch):int(y + .85 * (r + 1) * ch),
                              int(x + (c + .15) * cw):int(x + (c + .9) * cw)].sum())
                     for c in range(cols)])
    med = float(np.median([v for row in inks for v in row if v > 0] or [1]))
    out = []
    for r in range(rows):
        for c in range(cols):
            if inks[r][c] <= max(BLANK_MAX_INK, 0.08 * med):
                out.append({"row": r, "col": c, "ink": inks[r][c],
                            "bbox_page": [int(x + c * cw), int(y + r * ch),
                                          int(cw), int(ch)],
                            "pixel_sha": _crop_hash(
                                mask, int(x + c * cw), int(y + r * ch),
                                int(x + (c + 1) * cw), int(y + (r + 1) * ch))})
    return out
