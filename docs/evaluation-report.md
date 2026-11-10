# no-ai-fluff 评测报告：自动化评测与自驱动调优闭环

> 2026-11-10。锁定配置为 git 标签 `v8-locked`：审校规则 v8 + 第 2 轮雷达，hy-mt2 俄语往返（上下文 300 字），宿主 DeepSeek `deepseek-flash` 高推理强度。
> 评委 `typesafe/jev-1.14`（快照 `jev-1.14-20261014`）。逐轮记录见 `benchmarks/optimization_ledger.jsonl`。

## 结论

1. **Holdout（30 篇，收敛前从未读取）上，no-ai-fluff 与 Humanizer-zh 打平**：位置去偏胜率 51.2%（95% CI 46.9%~55.6%），16/30 篇占优。
   Dev 上的 56.8% 在 Holdout 上回落了 5.6 个百分点，说明调参时有一部分收益是 Dev 特有的。
2. **两者都明显比原文好读**：我方对原文 63.7%（60.2%~67.1%，27/30 篇），Humanizer-zh 对原文 60.2%（56.2%~64.1%，24/30 篇）。
3. **保真不比 Humanizer-zh 差**：JEV 保真风险 0.553 对 0.570（差值的 CI 跨 0）。确定性指标上我方更稳：
   硬占位符（代码、路径、公式、URL）存活 100% 对 99.1%（Humanizer-zh 最差一篇 88.9%）；数字被改动的篇目 3.3% 对 6.7%。
4. **分场景**：我方在 coding、teaching、mixed（公式/代码混排）上领先，在 daily、discussion、writing 上落后（见第 3 节）。
5. **代价**：我方多一次往返翻译、宿主输出更长，端到端延迟中位数约 71 秒对 34 秒；宿主费用约为 Humanizer-zh 的 1.5 倍。

目标“保真不低于 Humanizer-zh 且去偏胜率稳定超过它”：保真达成；胜率在 Dev 上达成、在 Holdout 上没有显著超过（统计上打平）。

## 1. 评测设置

### 语料与切分（`benchmarks/splits/`）

| 集合 | 篇数 | 来源 | 用途 |
|---|---|---|---|
| Train | 22 | M3/M4 盲测用过的 20 篇 + 2 篇构造难例 | 允许逐篇读 bad case，用于诊断 |
| Dev | 24 | 12 篇未读旧样本 + 12 篇新样本 | 每轮只看聚合与分场景指标 |
| Holdout | 30 | 全新样本，每场景 5 篇 | 收敛后一次性出分 |

- 新样本：35 道新问题由 Claude Sonnet 5.5 / GPT-5.5 / Gemini 3.1 Pro / DeepSeek V3.2 轮流作答（每题只用一个模型，无截断），另 7 篇 GitHub 公开的 Claude Code 中文 PR。新增“公式/代码混排”（mixed）场景。
- Holdout 用固定种子 20261110 分层抽取，抽完即写 `holdout.lock.json`（id + sha256），正文移出 `samples.jsonl` 封存；只有声明 `split=holdout` 的轮次能读取，并逐篇核对 sha256。
- Holdout 篇幅 448~9,800 字，中位数 2,360 字。

### 对比对象

- **原文**：模型原始回复。
- **no-ai-fluff v8**：往返翻译作参考，宿主按 v8 规则在原文上开局部补丁，代码层守卫合并。
- **Humanizer-zh**：`op7418/Humanizer-zh`，SKILL.md 作系统提示，同一宿主（DeepSeek high）全文改写。

### 评委：JEV 与校准（`benchmarks/jev_judge.py`、`results/jev_calib/`）

- 保真风险：6 个是非题（数字、语气强弱、术语、删信息、加信息、主客体），`risk = 1 − Π(1 − p)`，越低越好。
- 易读度：5 级打分（1~5），只给候选文本，不给原文。
- 胜率：两两比较，A/B 正反各问一次取平均（位置去偏）。
- 综合奖励 = 均值（对原文胜率，对 Humanizer-zh 胜率，(易读度 − 1)/4）。

| 校准项 | 数据 | 结果 | 门槛 |
|---|---|---|---|
| 保真风险 vs GPT-6 一票否决 | Train 546 条 | AUC 0.94（非往返稿 0.84） | ≥ 0.8 ✓ |
| 易读度 vs GPT-6 | Train 546 条 | Spearman 0.49~0.72 | 参考 |
| 两两偏好 vs 人工（现役系统，calib2） | 23 对 | 两两 78%（CI 0.58~0.90），易读度分 87%（0.68~0.95） | ≥ 70% ✓（点估计） |
| 两两偏好 vs 人工（旧盲测，已淘汰系统） | 54 对 | 41%~51% | 未达标 |

旧盲测比较的是已淘汰的 v2/v5 重度改写，GPT-6 在其中 43 对上也只有 58%，因此补做了一轮现役系统的人工盲测（calib2，12 篇 Train）作校准。
问题集最终选中文、rubric 措辞（zh-rubric）。限制：calib2 只有 23 对，置信区间下限低于 70%。

## 2. Holdout 结果（30 篇）

| 指标 | no-ai-fluff v8 | Humanizer-zh | 原文 |
|---|---|---|---|
| 对 Humanizer-zh 去偏胜率 | **51.2%**（46.9%~55.6%） | — | — |
| 对原文去偏胜率 | **63.7%**（60.2%~67.1%） | 60.2%（56.2%~64.1%） | — |
| 易读度期望分（1~5） | **4.10** | 4.06 | 4.03 |
| 仍有 AI 腔的概率 | 0.426 | **0.397** | 0.451 |
| JEV 保真风险（越低越好） | **0.553** | 0.570 | — |
| 硬占位符存活率（最差一篇） | **100%（100%）** | 99.1%（88.9%） | 100% |
| 数字被改动的篇目 | **3.3%** | 6.7% | — |
| 每篇补丁数 / 零补丁篇数 | 12.8 / 1 | 全文改写 | — |

区间为按篇自助法 95% 置信区间。

## 3. 分场景（Holdout，每场景 5 篇）

| 场景 | 对 Humanizer-zh 胜率 | 我方对原文 | Humanizer-zh 对原文 | 保真风险 我方 / HZ |
|---|---|---|---|---|
| coding | **0.549** | 0.699 | 0.666 | 0.511 / 0.426 |
| teaching | **0.562** | 0.646 | 0.514 | 0.595 / 0.665 |
| mixed（公式/代码） | **0.611** | 0.600 | 0.485 | 0.622 / 0.432 |
| writing | 0.472 | 0.746 | 0.725 | 0.525 / 0.640 |
| discussion | 0.460 | 0.589 | 0.622 | 0.592 / 0.693 |
| daily | 0.419 | 0.540 | 0.598 | 0.471 / 0.565 |

- 技术密集场景（coding、teaching、mixed）我方占优：只改读着费劲的地方，代码、公式、术语原样保留；Humanizer-zh 在 mixed 上对原文只有 0.485，改写反而没让它更好读。
- 日常、讨论场景 Humanizer-zh 占优：它敢做全篇的小幅“解压”和删套话，我方补丁式改写更克制。
- 每场景只有 5 篇，分场景数字的波动很大，只能看方向。

## 4. 延迟与成本（Holdout）

| 项 | no-ai-fluff v8 | Humanizer-zh |
|---|---|---|
| 往返翻译（hy-mt2，OpenRouter） | P50 13.0s / P90 26.3s，约 0.002 美元/篇 | — |
| 宿主调用（DeepSeek high） | P50 58.0s / P90 91.9s | P50 34.4s / P90 73.8s |
| 宿主平均 token（输入 / 输出） | 4,328 / 11,699 | 6,238 / 7,818 |
| 宿主费用（按输出 token 分摊 Holdout 实扣 ¥2.76） | 约 ¥0.055/篇 | 约 ¥0.037/篇 |

生产环境里宿主就是用户自己的 Claude Code / Codex 子上下文，不产生额外 API 费用；上表是评测时用 DeepSeek 模拟宿主的花费。

## 5. 调优轨迹（Dev 24 篇）

| 轮 | 模块 | 假设 / 改动 | Dev 奖励 | 对 HZ 胜率 | 保真风险 | 决定 |
|---|---|---|---|---|---|---|
| 0 | — | v6 基线 | 0.622 | 49.2% | 0.388 | 基线 |
| 1 | patcher | v7：逐段检查、逐条核对雷达（v6 有 42% 的篇目零补丁） | 0.646 | 52.8% | 0.489 | Keep |
| 2 | radar | 新增“高密度句”“句首连列”，提示上限 12→30 | 0.658 | 54.9% | 0.508 | Keep |
| 3 | patcher | v8：新增 [省略压缩] 一类（补回省掉的字、符号换成词） | **0.672** | **56.8%** | 0.505 | Keep |
| 4 | 合并守卫 | 语气词表守卫：删缓和词 / 加绝对词就拒收 | 0.672 | 56.6% | 0.503 | Revert |
| 5 | 新增复核 | 宿主逐条复核补丁，撤销改变原意的（探针） | −0.003 | −0.1pp | −0.017 | Revert |
| 6 | rtt | 去掉往返参考稿 | 0.673 | 57.0% | 0.511 | Revert |

同期 Humanizer-zh 在 Dev 上的保真风险为 0.503。

收敛依据：第 4~6 轮来自三个不同模块，净收益都低于阈值（综合分 < 0.02 且胜率 < 1.5pp），已探索 6 个方向。

负向结论（同样有用）：

- **词表式语气守卫几乎不触发**（330 条补丁只拒 3 条）：JEV 认定的“语气强弱变了”多数不是显式删词造成的。
- **保真复核**能撤回约 11% 的补丁、风险降 0.017，但易读度同步下降；奖励不含保真度，所以没有保留。适合作为“保真优先”的可选开关（每篇约 ¥0.01）。
- **往返参考稿在“补丁打在原文上”的架构下没有可测贡献**（Dev 奖励 ±0.001）。按协议持平即回滚，所以锁定配置仍保留它；去掉它每篇可省约 13 秒和 0.002 美元。这与架构最初“往返翻译解压句法”的设想不同，值得在下一版架构里重新评估。

## 6. 局限

- 评委是单一的 JEV。人工校准只有 23 对（1 位测试者），置信区间下限低于 70%。
- Holdout 每场景 5 篇，分场景结论只能看方向。
- 宿主用 DeepSeek 模拟；Claude Code 等真实宿主下的表现未在 Holdout 上测。sonnet1 的单篇对照显示宿主强弱影响不大。
- Dev 对 Humanizer-zh 的胜率比 Holdout 高 5.6pp：每轮的 Keep/Revert 都看 Dev，存在一定的 Dev 过拟合。
- Holdout 的人工盲测（第 7 节）待用户投票。

## 7. 人工盲测（Holdout）

`benchmarks/results/holdout-blind/blind_test_holdout.html`：从 Holdout 中 ≤4,500 字的样本按场景各抽 1 篇、再随机 2 篇，共 8 篇（固定种子 20261110）。
每篇 3 个版本（原文 / no-ai-fluff v8 / Humanizer-zh），顺序随机，只显示代号。投票后：

```bash
python benchmarks/blind_workbench.py analyze <导出的 blind_votes.json>
```

## 8. 复现

```bash
python benchmarks/splits/make_splits.py --verify                     # 核对 Holdout 封存
python benchmarks/jev_judge.py calibrate                             # 评委校准（全部走缓存）
python benchmarks/run_ablation.py rtt   --run holdout --models tencent/hy-mt2-30b-a3b --bridges ru
python benchmarks/run_ablation.py patch --run holdout --rules v8 --patch-max-tokens 40000
python benchmarks/run_baseline.py --run holdout --baseline humanizer-zh --max-tokens 48000
python benchmarks/jev_judge.py score --run holdout                   # 写 jev.jsonl / jev_summary.json
python benchmarks/run_ablation.py report --run holdout               # 占位符、数字等客观指标
```

所有模型调用（宿主、往返翻译、JEV）的原始返回都按请求哈希缓存在 `benchmarks/.cache/`，重跑零成本。
