# 归档：审校规则 v3~v6 冒烟记录

2026-11-10 从 `benchmarks/results/` 移到这里，不再作为发布报告的直接依据。`docs/rtt-model-comparison.md` 第十一、十二节引用的文件都在本目录：

- `v3smoke` / `v4smoke` / `v5smoke`：3 篇样本上 v3→v5 的对比；`v5smoke/patch_microv*.jsonl` 是词对微筛三档的记录。
- `v6smoke`：v6 冒烟，共 5 篇；`*_pre_soft.jsonl` 是加入软占位符之前的结果。

这些目录里 `rtt.jsonl` 的 `draft_id` 仍可在 `benchmarks/.cache/drafts` 找到。如需复现，把目录移回 `results/` 即可。
