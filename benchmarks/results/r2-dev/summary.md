# Milestone 2 消融结果（24 篇样本，生成于 2026-11-10 14:34）

| 版本 | 保真度 | 易读度 | 一票否决率 | 占位符存活 | 数字不一致 | 雷达命中/千字 | RTT P50 | RTT 花费/篇 | 段回退 | 补丁/篇 | 补丁拒收 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| original | - | - | - | 100.0% | 0.0% | 3.41 | -s | $- | - | - | - |
| rtt:hymt-ru | - | - | - | 100.0% | 4.2% | 2.11 | 11.3s | $0.00196 | 2.0% | - | - |
| baseline:humanizer-zh | - | - | - | 98.7% | 8.3% | 3.23 | -s | $- | - | - | - |
| patched:hymt-ru | - | - | - | 98.4% | 0.0% | 2.88 | -s | $- | - | 10.5 | 2.4% |

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
| original | 0.76 | 0.00 | 0.11 | 2.53 | 0.01 |
| rtt:hymt-ru | 0.66 | 0.00 | 0.08 | 1.17 | 0.20 |
| baseline:humanizer-zh | 0.68 | 0.00 | 0.11 | 2.43 | 0.01 |
| patched:hymt-ru | 0.65 | 0.00 | 0.02 | 2.21 | 0.00 |

## 评审来源审计

- 评审来源：无；完成 0/72
- 缺少 72 条评审，例如 [('coding-004', 'baseline:humanizer-zh'), ('coding-004', 'original'), ('coding-004', 'patched:hymt-ru')]
