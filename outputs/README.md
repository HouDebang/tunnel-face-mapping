# 成果目录索引

最终交付与实际验证：

- `real_RK4_970/`：修正像素中心缩放后的真实照片正式运行；10 个 unknown 区域、3035 个候选图路径段；整图回退，未验证 ROI。
- `synthetic_face/`：正式合成测试运行；4 个 unknown 区域、12 个候选图路径段，仅验证软件。
- `failure_missing_input/`：输入缺失的失败报告示范，不伪造统计。
- `test_results.xml`：最终 42 项测试通过记录。
- `verification_summary.json`：两组正式成果从磁盘重读、面积/几何/CSV/哈希独立复核记录。

`synthetic_default/` 和 `real_RK4_970_default/` 是早期开发检查目录，产生于像素中心缩放修正前，不属于最终交付。自动审批拒绝递归清理操作，故保留并明确标注。
