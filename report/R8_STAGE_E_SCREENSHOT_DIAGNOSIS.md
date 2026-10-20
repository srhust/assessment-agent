# R8 阶段 E · 真实排版截图 Dev 六段分层诊断

- 素材：`figshots_dev` 15 题整页排版截图（真实像素、合成题面；非真考扫描件，manifest 同口径）。**未用 R5 `xingce_test` 调视觉**。
- 配对：oracle_solver（金标区域注入）准确率 **80%** vs image_only（全链路） **13%** → 感知层净损 10 题；推理层缺口 3 题。
- 预算：预注册 ≤60 次调用；image 跑 15 次（HTTP 尝试含重试 16，成功 15），oracle 跑 0 次（零 API 规则通道）；**未触顶自然结束**。 模型回显 ['glm-5.3-flash']。

| 题 | img | oracle | 首错 | root_cause | 区域IoU | 净损类 |
|---|---|---|---|---|---|---|
| ft-rot90-01 | ✗ | ✓ | structure_grid:grid_parse_mismatch | region_crop | 0.7077 | vision_net_loss |
| ft-rot180-02 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.1672 | vision_net_loss |
| ft-rot270-03 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.1181 | vision_net_loss |
| ft-mirror-04 | ✗ | ✓ | vision_schema:json_invalid | pipeline | None | vision_net_loss |
| ft-mirror-05 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.4861 | vision_net_loss |
| ft-comp-06 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.4404 | vision_net_loss |
| ft-trans-07 | ✗ | ✗ | vision_structure:regions_unusable | solver_rule_selection | 0.3038 | solver_gap |
| ft-trans-08 | ✗ | ✗ | stem_text:stem_truncated_or_wrong | extraction_ocr | 0.6484 | solver_gap |
| ft-scale-09 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.4297 | vision_net_loss |
| ft-grid-10 | ✗ | ✓ | stem_text:stem_truncated_or_wrong | extraction_ocr | 0.8724 | vision_net_loss |
| ft-grid-11 | ✗ | ✓ | vision_structure:regions_unusable | region_crop | 0.2182 | vision_net_loss |
| ft-overlay-12 | ✗ | ✗ | stem_text:stem_truncated_or_wrong | extraction_ocr | 0.9195 | solver_gap |
| ft-overlay-13 | ✗ | ✓ | stem_text:stem_truncated_or_wrong | extraction_ocr | 0.8859 | vision_net_loss |
| ft-sym-14 | ✓ | ✓ | None | none | 0.3526 |  |
| ft-conflict-15 | ✓ | ✓ | None | none | 0.7837 |  |

## 六段分布（首次检出错误层）

- **1-vision**: 8 题
- **3-evidence**: 1 题
- **5-planner_exec**: 4 题
- **solved**: 2 题

## 结论（供 R8 终报引用）
1. 推理层（变换通道/规则匹配/验证）在金标区域下 80%——**主要瓶颈在感知→证据段**（结构网格解析、裁剪质量、题干图形数量），非知识或验证缺失。
2. image_only 的 45 类弃权签名全部可归因到具体段（无一例静默误放：false_accept=0）。
3. 修复方向按段排队：structure_grid 解析 > stem_cer>0.1 的题干重读 > 选项裁剪空检。