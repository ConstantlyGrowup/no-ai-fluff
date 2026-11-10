# Milestone 2 消融结果（10 篇样本，生成于 2026-11-10 14:21）

| 版本 | 保真度 | 易读度 | 一票否决率 | 占位符存活 | 数字不一致 | 雷达命中/千字 | RTT P50 | RTT 花费/篇 | 段回退 | 补丁/篇 | 补丁拒收 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| original | - | - | - | 100.0% | 0.0% | 2.42 | -s | $- | - | - | - |
| rtt:hymt-ru | - | - | - | 100.0% | 0.0% | 1.50 | 11.6s | $0.00203 | 3.5% | - | - |
| baseline:humanizer-zh | - | - | - | 99.4% | 10.0% | 2.34 | -s | $- | - | - | - |
| patched:hymt-ru | - | - | - | 99.4% | 0.0% | 2.05 | -s | $- | - | 9.8 | 2.0% |

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
| original | 0.04 | 0.00 | 0.04 | 2.31 | 0.03 |
| rtt:hymt-ru | 0.09 | 0.00 | 0.02 | 0.96 | 0.44 |
| baseline:humanizer-zh | 0.04 | 0.00 | 0.04 | 2.26 | 0.00 |
| patched:hymt-ru | 0.00 | 0.00 | 0.00 | 2.05 | 0.00 |

## 评审来源审计

- 评审来源：无；完成 0/30
- 缺少 30 条评审，例如 [('coding-004', 'baseline:humanizer-zh'), ('coding-004', 'original'), ('coding-004', 'patched:hymt-ru')]
