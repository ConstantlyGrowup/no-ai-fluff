# Milestone 2 消融结果（10 篇样本，生成于 2026-11-08 11:53）

| 版本 | 保真度 | 易读度 | 一票否决率 | 占位符存活 | 数字不一致 | 雷达命中/千字 | RTT P50 | RTT 花费/篇 | 段回退 | 补丁/篇 | 补丁拒收 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| original | - | - | - | 100.0% | 0.0% | 3.26 | -s | $- | - | - | - |
| rtt:hymt-ru | - | - | - | 100.0% | 30.0% | 1.70 | 0.1s | $0.00197 | 1.1% | - | - |
| baseline:humanizer-zh | - | - | - | 97.8% | 0.0% | 3.11 | -s | $- | - | - | - |
| patched:hymt-ru | - | - | - | 100.0% | 10.0% | 1.74 | -s | $- | - | 37.7 | 13.0% |

## 分场景（保真度 / 易读度）

| 版本 | coding | daily | discussion | teaching | writing |
|---|---|---|---|---|---|
| original | - / - | - / - | - / - | - / - | - / - |
| rtt:hymt-ru | - / - | - / - | - / - | - / - | - / - |
| baseline:humanizer-zh | - / - | - / - | - / - | - / - | - / - |
| patched:hymt-ru | - / - | - / - | - / - | - / - | - / - |

## 雷达分项（命中/千字）

| 版本 | long_clause | number | cliche | loose_english | pronoun |
|---|---|---|---|---|---|
| original | 0.15 | 0.00 | 0.31 | 2.80 | 0.00 |
| rtt:hymt-ru | 0.11 | 0.00 | 0.15 | 1.45 | 0.00 |
| baseline:humanizer-zh | 0.15 | 0.00 | 0.21 | 2.72 | 0.03 |
| patched:hymt-ru | 0.05 | 0.00 | 0.01 | 1.67 | 0.00 |

## 评审来源审计

- 评审来源：无；完成 0/30
- 缺少 30 条评审，例如 [('coding-001', 'baseline:humanizer-zh'), ('coding-001', 'original'), ('coding-001', 'patched:hymt-ru')]
