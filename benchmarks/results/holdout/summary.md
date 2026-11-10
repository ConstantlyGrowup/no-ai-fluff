# Milestone 2 消融结果（30 篇样本，生成于 2026-11-10 15:12）

| 版本 | 保真度 | 易读度 | 一票否决率 | 占位符存活 | 数字不一致 | 雷达命中/千字 | RTT P50 | RTT 花费/篇 | 段回退 | 补丁/篇 | 补丁拒收 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| original | - | - | - | 100.0% | 0.0% | 3.85 | -s | $- | - | - | - |
| rtt:hymt-ru | - | - | - | 100.0% | 30.0% | 2.38 | 12.7s | $0.00196 | 2.2% | - | - |
| baseline:humanizer-zh | - | - | - | 98.7% | 6.7% | 3.62 | -s | $- | - | - | - |
| patched:hymt-ru | - | - | - | 99.6% | 3.3% | 2.78 | -s | $- | - | 13.4 | 4.5% |

## 分场景（保真度 / 易读度）

| 版本 | coding | daily | discussion | mixed | teaching | writing |
|---|---|---|---|---|---|---|
| original | - / - | - / - | - / - | - / - | - / - | - / - |
| rtt:hymt-ru | - / - | - / - | - / - | - / - | - / - | - / - |
| baseline:humanizer-zh | - / - | - / - | - / - | - / - | - / - | - / - |
| patched:hymt-ru | - / - | - / - | - / - | - / - | - / - | - / - |

## 雷达分项（命中/千字）

| 版本 | long_clause | number | cliche | loose_english | pronoun |
|---|---|---|---|---|---|
| original | 0.79 | 0.00 | 0.17 | 2.83 | 0.05 |
| rtt:hymt-ru | 0.55 | 0.00 | 0.28 | 1.52 | 0.03 |
| baseline:humanizer-zh | 0.74 | 0.00 | 0.04 | 2.79 | 0.04 |
| patched:hymt-ru | 0.52 | 0.00 | 0.03 | 2.23 | 0.00 |

## 评审来源审计

- 评审来源：无；完成 0/90
- 缺少 90 条评审，例如 [('coding-006', 'baseline:humanizer-zh'), ('coding-006', 'original'), ('coding-006', 'patched:hymt-ru')]
