# 交接文档：自动化评测与自驱动调优闭环（Autonomous Optimization Loop）

> 接手方：Claude Code + Opus 5.5（high）。写于 2026-11-10，交接时代码基线为 `main` 上的 v6（提交见文末）。
> 本文是唯一的权威交接说明；与旧文档冲突时以本文为准。用户的原始任务书全文见附录 A。

## 0. 一句话任务与必读顺序

**任务**：在预算与防过拟合约束下迭代流水线，让它在 Dev 集上稳定胜过 `op7418/Humanizer-zh`，最后在封存的 Holdout-Test 上一次性出分，产出 `docs/evaluation-report.md` 和一份人工盲测页面。

**必读顺序**：本文 → `docs/architecture.md`（系统设计）→ `docs/rtt-model-comparison.md` 第九至十二节（M3~v6 的实验证据）→ `src/no_ai_fluff/patcher.py` 里的 `AUDIT_INSTRUCTIONS_V6` → `benchmarks/README.md`。

**Phase 0（只读预检）已完成**，用户已逐项确认（见第 2 节）。你从 **Phase 1** 开始。

---

## 1. 现状快照

### 1.1 流水线（v6，当前默认）

1. **Masker**（`masker.py`）：按 Markdown/HTML/纯文本拆槽位，代码、路径、URL、公式、数字类标识等冻结成 `{{Pn}}`。
   - “软”占位符：整句英文（≥4 个连续英文词）、首字母大写英文词、PM/ROI 这类缩写（术语表里的 API、SDK 等除外）。宿主以 `中英混杂` 类型补丁把它们改成中文时允许去掉；其他占位符一律不准丢。
2. **RTT**（`rtt.py`）：OpenRouter `tencent/hy-mt2-30b-a3b`，中→俄→中。使用混元官方“上下文翻译”模板，附用户问题 + 紧邻前文 300 字（`rtt_context_chars`）；短段落带上下文偶发空响应时自动去掉上下文重试。
3. **Radar**（`radar.py`）：5 类表层疑点（长从句、数字一致、套话、散装英文、代词）。
4. **宿主审校**（`patcher.py`，规则 v6）：**补丁打在原文上**，往返稿只作“直白说法参考”；每条补丁 `type` 必须是九类问题之一（残句堆砌、隐喻黑话、自造代号、中英混杂、翻译腔、空洞大词、推理跳步、冗余、指代不清），否则拒收；find ≤400 字、不跨行、唯一；没有问题就输出 `[]`，原样交付。
5. **合并与守卫**：占位符 100% 校验、结构前缀剥离、表格逐格、行尾空白容错、`cleanup.py` 标点清理（只动被改过的槽位）。

v1~v5 规则仍在代码里，供复现历史实验；**v5 已淘汰**（见下表）。

### 1.2 历史结果（决定了现在的方向）

| 轮次 | 对比 | 结论 |
|---|---|---|
| M3 人工盲测（8 票） | 原文 / 法 / 日 / 俄往返 + v2 补丁 | 俄语 5/8 最好读；锁定 hy-mt2 + 俄语 |
| M4 人工盲测（10 票，新样本） | 原文 / v5 / Humanizer-zh | Humanizer-zh 6/10 最好读；v5 被选最难读 4/10 |
| M4 GPT-6 双轴评审（30 条） | 同上 | 保真：原文 5.0 / Humanizer-zh 4.7 / **v5 2.3（9/10 一票否决）**；易读：4.0 / 3.6 / 2.8 |
| v6smoke（5 篇，DeepSeek） | v6 | 往返错词（运维→运营）、半中半英句不再进定稿；自然文本原样返回；**偏保守且不稳定**（teaching-001 两次调用分别 4 条、0 条补丁） |
| sonnet1（1 篇，Sonnet 5.5 宿主） | v6 / Humanizer-zh | Sonnet 与 DeepSeek 在 v6 下表现接近（4 条补丁，2.3% vs 3%）→ 宿主强弱不是主因 |
| hard1 人工盲测（2 篇构造难例） | 原文 / v6 / Humanizer-zh | hard-001（黑话）v6 最好读；**hard-002（极端翻译腔）原文最好读、v6 最难读** |

**校准数据的两条脏票**：盲测页面渲染器曾把“空行隔开的编号列表”全显示为“1.”（已在 `blind_workbench_template.html` 修复）。受影响的只有 Humanizer-zh 的两篇：M4 `discussion-002`、hard1 `hard-001`，恰好都被选为“最难读”。校准时把这两票标为可疑（建议剔除或单独报告）。

---

## 2. 用户已确认的权限与决策（2026-11-10）

| # | 事项 | 决定 |
|---|---|---|
| 1 | JEV 当评委 | **明确同意**用 OpenRouter `typesafe/jev-1.13` 作评委（固定 1.13，不要用 `jev-latest`/1.14） |
| 2 | 补充语料 | 可用 `benchmarks/corpus/collect_generated.py` 生成；**一个问题只用一个模型作答**（按问题轮换 Claude / GPT / Gemini / DeepSeek，不要同一问题多模型） |
| 3 | 删除权限 | **同意**在项目目录内删除（清理、git 锁文件） |
| 4 | 云端运行 | 同意可用云端；但接手后在本机 Claude Code 里跑，没有 3 分钟命令时限，**不再需要云端**，密钥留在本机 `.env` |
| 5 | 保真度基线 | **不设严格要求**：JEV 保真度只作监控指标和报告项，不作为“不得劣于基线”的硬红线 |
| 6 | 审校提示词上限 | 可上调；建议上限 **1,600 字**（v6 现为 1,102 字） |

仍然有效的旧约束：
- **OpenRouter 只用于**：翻译小模型（hy-mt2）、JEV 评委、上面第 2 条批准的一次性语料生成（约 40 题，参考单价约 1.7 美元）。**不得**用 OpenRouter 跑宿主补丁或 Humanizer-zh（那会走大模型、额度消耗快）。sonnet1 的两次 Sonnet 调用是用户单独批准的一次性实验，不要重复。
- 宿主补丁和 Humanizer-zh 基线**只用 DeepSeek 官方 API**（`deepseek-flash`，高推理强度）。
- 不得伪造任何评审结果（项目曾发生过，证据在 `benchmarks/.cache/quarantine/`）。所有分数必须来自真实模型调用或真实人工投票，并保存原始返回。

硬约束（任一违背即判负）：
1. `python -m pytest -q` 100% 通过（交接时 198 个全部通过）；
2. 代码块 / 公式 / 路径等**硬**占位符无损率 100%（软占位符见 1.1）。

---

## 3. 环境、密钥与命令速查

- 项目根：`G:\AI-attempt\人话转化器`（远端 `https://github.com/ConstantlyGrowup/no-ai-fluff`，分支 `main`）。
- Python 3.10+，零第三方运行依赖；测试需要 `pytest`。
- `.env`（项目根，已配置，**不要打印、不要提交**）：
  - `OPENROUTER_API_KEY`
  - `Deepseek_OFFICIAL_API_KEY`（代码也认 `DEEPSEEK_API_KEY`）
  - `TENCENTCLOUD_API_KEY`（暂未用）
- 加载方式：`from no_ai_fluff.config import load_dotenv; load_dotenv()`。

常用命令（都在项目根执行）：

```bash
# 往返翻译（Stage 1）；--rtt-context 默认 300
python benchmarks/run_ablation.py rtt   --run <run> --models tencent/hy-mt2-30b-a3b --bridges ru --workers 5
# 宿主补丁（Stage 2b/3）；--rules 默认 v6；--patch-max-tokens 请显式设 24000
python benchmarks/run_ablation.py patch --run <run> --rules v6 --workers 5 --patch-max-tokens 24000
# 只用已存的宿主原始补丁重新合并（零模型调用）
python benchmarks/run_ablation.py refinalize --run <run> --rules v6
# 客观指标报告（占位符存活、数字一致、雷达、改写幅度…）
python benchmarks/run_ablation.py report --run <run>
# Humanizer-zh 基线（同一宿主 DeepSeek high）；请加 --max-tokens 32000
python benchmarks/run_baseline.py --run <run> --baseline humanizer-zh --workers 5 --max-tokens 32000
# 人工盲测页面 / 解码投票（analyze 自动按投票文件里的轮次解码）
python benchmarks/blind_workbench.py build --run <run> --out benchmarks/results/<run>/blind_test_<run>.html
python benchmarks/blind_workbench.py analyze <导出的投票文件>
```

每个 `<run>` 是 `benchmarks/results/<run>/`，靠 `run.json` 限定样本与候选：

```json
{"samples": ["coding-001", "..."], "judge_variants": ["original", "patched:hymt-ru", "baseline:humanizer-zh"],
 "labels": {"patched:hymt-ru": "no-ai-fluff"}, "rules": "v6"}
```

结果文件：`rtt.jsonl`（往返稿 + draft_id）、`patch.jsonl`（宿主原始补丁 `raw_patches`、逐条去留、定稿、用量）、`baseline.jsonl`（Humanizer-zh 定稿）。

---

## 4. 预算

- **DeepSeek 余额：¥34.40**（2026-11-10 实测，`GET https://api.deepseek.com/user/balance`）。
- **OpenRouter**：账户额度上限 50 美元，已用 5.19 美元，剩余 44.81 美元（只用于第 2 节允许的用途）。

单价估算（按历史 token 数与余额变化推算，**第一轮请用余额差实测校正后写进账本**）：

| 调用 | 典型输出 token | 估算单价 |
|---|---|---|
| v6 宿主补丁（high） | 5k~12k | ¥0.03~0.08 / 篇 |
| Humanizer-zh（high） | 6k~20k | ¥0.03~0.08 / 篇 |
| JEV（每候选约 5 个问题） | 只计输入 | < 0.0001 美元 / 次 |
| hy-mt2 往返 | — | < 0.01 美元 / 篇 |

配额计划：

| 阶段 | 配额 |
|---|---|
| Dev 集 Humanizer-zh 基线（每篇只跑一次，之后读缓存） | ≈ ¥1.5 |
| v6 Dev 基线 | ≈ ¥2 |
| 探索期（每轮探针 ≈¥0.5，通过后 Dev ≈¥1.5；预计 8~10 轮） | ≤ ¥18 |
| **Holdout 终测（我方 + Humanizer-zh 各一次全量）——不可挪用** | **¥8** |
| 安全垫 | ≈ ¥5 |

纪律：每次调用前收紧 `max_tokens`（DeepSeek 会按它预扣额度）；每轮在账本里记录调用前后余额。

---

## 5. JEV 评委（Reward Model）

### 5.1 接口（已实测）

- `POST https://openrouter.ai/api/alpha/decisions`，`Authorization: Bearer $OPENROUTER_API_KEY`。**不是**对话接口，chat SDK 不能用。
- 请求：`{"model": "typesafe/jev-1.13", "state": <字符串/对象/数组>, "questions": {<名字>: <问题>}}`。
- 问题类型：
  - `noul`：`instructions` + 可选 `criteria: {"true": "...", "false": "..."}` → 返回 `noul`（“是”的概率，0~1）；
  - `choice`：`criteria` 为“选项名 → 说明” → 返回 `choice`、`confidence`、`probabilities`；
  - `score`：`criteria` 为有序数组 → 返回 `score`（加权位置）、`probabilities`（按下标）、`legend`。
- 同一请求内的问题并行作答、互相看不到答案；可能返回 `refusal`。
- 响应带 `usage.cost`（美元）。探针实测：3 个问题、529 输入 token、0.000022 美元、0.77 秒。服务版本为 `typesafe/jev-1.13-20260917`。
- 上下文：文档写 32K token，端点元数据写 262K。**按 32K 设计**，长文拆段评。
- JEV 不给理由、只给概率，所以不能再沿用 `agent_judge.py` 的“逐字引文”证据制。

### 5.2 评测设计（Phase 0 方案，用户已同意用 JEV）

`state = {"original": 原文, "candidate": 候选}`（长文按段落对齐拆分）：

- **保真风险**（监控项，非硬红线）：5~6 个 `noul`，分别问“数字/单位/量级变了”“条件或语气强弱变了”“术语变成了另一个东西”“删掉了信息”“添加了原文没有的信息”“主客体或指代弄混了”。合成：`risk = 1 − Π(1 − p_i)`。
- **易读度**：一个 5 级 `score`（等级沿用 `agent_judge.py` 里 RUBRIC_V3 的“人味”分级），加一个“仍有明显 AI 腔/翻译腔”的 `noul`。
- **对 Humanizer-zh 胜率**：`choice`（A/B），每对**正反各问一次**（A=我方/B=HZ 与反过来），取期望概率，消除位置偏置。
- 中文原文配英文提问、还是全中文提问，在校准时两种都试，选一致率高的。

### 5.3 校准（闭环启动前提）

数据：
- 人工：`results/m3/blind_votes.json`（8 票、4 个版本）、`results/m4/blind_votes.json`（10 票、3 个版本）、`results/hard1/blind_votes.json`（2 票）。展开成两两偏好：最好读 > 其余版本 > 最难读，约 40~50 对。剔除或单独报告第 1.2 节提到的两张脏票。
- 高阶模型：GPT-6 双轴评审 `results/{m2,m2v2,m2v2hi,m3judge,m4}/judge.jsonl`（共 843 条，含 `fidelity`/`readability` 1~5 分与引文）。
- 解码密钥：`.cache/blind/key-*.json`、`.cache/agent_judge/key-*.json`（**绝不能删**）。

门槛（未达标就先拆细或改写问题，**不许用未校准的信号当奖励**）：
- 与人工两两偏好一致率 ≥ 70%（报告置信区间，样本小）；
- 保真风险对 GPT-6 一票否决（fidelity ≤ 2）的 AUC ≥ 0.8；
- 易读度 `score` 与 GPT-6 readability 的 Spearman 相关系数报告即可（参考值）。

建议把 JEV 封装成 `benchmarks/jev_judge.py`：带磁盘缓存（键为请求体哈希），保存原始返回，子命令 `calibrate` / `score --run <run>`。

---

## 6. 数据：防泄漏切分

### 6.1 现有语料（`benchmarks/corpus/samples.jsonl`，34 篇）

代码 PR 5、技术教学 5、长文写作 5、深度讨论 5、日常 12、构造难例 2（hard-001/002）。

**已被逐篇读过、调过参的**（只能进 Train）：
- M3 的 10 篇：coding-003, coding-005, teaching-002, teaching-004, writing-002, writing-003, discussion-004, discussion-005, daily-002, daily-007；
- M4 的 10 篇：coding-001, coding-002, teaching-001, teaching-003, writing-004, writing-005, discussion-001, discussion-002, daily-001, daily-012；
- hard-001、hard-002。

**尚未被逐篇阅读的旧样本**（12 篇，可进 Dev）：coding-004, teaching-005, writing-001, discussion-003, daily-003, daily-004, daily-005, daily-006, daily-008, daily-009, daily-010, daily-011。

### 6.2 补充生成（用户已批准）

- 在 `collect_generated.py` 的 `TASKS` 里追加约 40 道**新问题**（`id` 前缀用新编号，如 `teaching-006`…）。每题只指定**一个**模型，按题轮换 Claude / GPT / Gemini / DeepSeek（脚本里的常量）。
- 场景覆盖：代码 PR（也可用 `collect_github_prs.py` 抓公开 PR，免费）、技术教学、长文写作、深度讨论、日常问答，并**新增“公式/代码混排”场景**；长度要有梯度。
- 生成后用 `build_samples.py` 并入 `samples.jsonl`。
- **原始回复可能被截断**（看 `finish_reason`），截断的样本要重生成或剔除。

### 6.3 切分（固定种子、分层）

- `Train`：第 6.1 节已读过的 22 篇。只有这里允许逐篇读 bad case。
- `Dev`：12 篇未读旧样本 + 约 12 篇新样本（≈24 篇，各场景尽量均衡）。只看聚合指标与分场景分，**禁止针对单篇硬编码**。
- `Holdout-Test`：约 30 篇全新样本（每场景约 5 篇），抽完立刻写入 `benchmarks/splits/holdout.lock.json`（只含 id 列表 + 每篇文本的 sha256 + 种子 + 时间）。宣布收敛前**不读正文、不跑任何实验**。

切分清单放 `benchmarks/splits/{train,dev,holdout.lock}.json` 并提交。

---

## 7. 闭环协议（用户任务书的执行要点）

每轮严格五步，记入 `benchmarks/optimization_ledger.jsonl`（一行一轮）：

```json
{"round": 3, "date": "…", "state": "Train 诊断出的最大瓶颈及证据", "module": "patcher|rtt|masker|radar|cleanup",
 "hypothesis": "单变量假设", "change": "改了什么（提交号/差异摘要）",
 "probe": {"ids": [...], "metrics": {...}, "passed": true},
 "dev": {"metrics": {...}, "by_scenario": {...}},
 "reward": 0.0, "decision": "keep|revert", "reason": "…",
 "cost": {"deepseek_cny": 0.0, "openrouter_usd": 0.0, "balance_after_cny": 0.0}}
```

1. **诊断**：只看 Train 的低分样本与雷达/补丁拒收统计，定位瓶颈模块。
2. **单变量假设**：一次只动一个模块或一组强耦合参数。
3. **防过拟合**：不加只命中特定话题的词表/特判正则；改动要能跨五大场景解释；审校提示词 ≤ 1,600 字。
4. **两级评估**：
   - 先跑 8~10 篇跨场景探针（只改 Stage 2b/3 时直接复用 RTT 缓存）；无正向提升或触发红线就中止本轮。
   - 探针正向再跑全量 Dev，记录本轮花费与剩余额度。
5. **棘轮裁决**：Dev 综合奖励净提升、且无单场景灾难性退化 → `git commit`（Keep）；持平或下降 → `git checkout` 回滚（Revert），把失败原因记为负向先验。

**奖励**：满足硬约束后，最大化：
- Dev 上相对原文的去 AI 腔提升；
- 相对 Humanizer-zh 的位置去偏胜率；
- 易读度期望分。

保真风险作为监控项写进账本，大幅恶化时在报告里说明（第 2 节第 5 条）。

**停机**：
- 至少探索 3 个正交方向后才可宣布收敛；
- 满足以下任一组条件即停：
  - 目标达成：Dev 上保真不低于 Humanizer-zh，且去偏胜率稳定超过它；
  - 边际收益过小：连续 3 轮不同模块的合理假设净收益都低于阈值（胜率 < 1.5 个百分点或综合分 < 0.02）；
  - 触及探索期预算上限。

**终局**：
- 锁定代码与参数，解封 Holdout 一次性跑“我方 vs Humanizer-zh vs 原文”；
- 写 `docs/evaluation-report.md`，包含分场景胜率、保真失误率、延迟、单篇成本；
- 用 `blind_workbench.py` 从 Holdout 分层抽 6~10 篇生成盲测 HTML，交给用户。

---

## 8. 诊断起点（Train 侧已知线索）

1. **v6 偏保守、结果不稳定**：同一提示两次调用补丁数从 4 到 0（teaching-001）；原文 11 处散装英文、2 处套话却 0 补丁。可能方向：要求逐条核对雷达疑点；或同一篇多次采样取并集再去重。注意成本翻倍。
2. **极端翻译腔（hard-002）v6 输给原文**：先读 `results/hard1/patch.jsonl` 里 hard-002 的补丁，看是改得不够、改坏了，还是选择性改写反而让文风不统一。
3. **宿主强弱不是主因**（sonnet1），优先改提示与流程，而不是换宿主。
4. **Humanizer-zh 的强项**：敢删整句套话和收尾口号（hard-001 里删掉“战斗力”“谱写新篇章”整段），v6 往往只改写不删除。
5. **Humanizer-zh 的弱项**：会丢行内代码的反引号、删掉带信息的过渡句和加粗小标题（sonnet1、M4 discussion-002）。我方守卫在这点上占优，报告里应量化。

---

## 9. 坑与约定

- **缓存**：
  - `.cache/openrouter`、`.cache/deepseek` 以请求体为键，同一请求再跑零成本；改提示词或模板就会产生新请求。**绝不删**这两个目录和 `.cache/drafts`。
  - 改了 masker 后，旧草稿的软占位符列表不会更新。需要重跑 `rtt`（相同请求走缓存）重建草稿，再跑 `patch`（相同宿主提示也走缓存）。
- **refinalize**：只改合并/守卫逻辑时，用 `refinalize` 零成本重算。
- **测试替身**：`tests/conftest.py` 的 `FakeTranslator.round_trip` 没有 `contexts` 参数，`pipeline.prepare` 用 `inspect.signature` 判断是否传上下文，不要破坏这个兼容。
- **换行符**：Windows 编辑器会把一些文件改成 CRLF，`git diff --ignore-cr-at-eol` 为空的改动不要提交，用 `git checkout -- <file>` 还原。
- **git push**：之前的会话无法从本机直接 push，是经云端中转的。若你在本机 push 失败，请让用户 push。
- **提交署名**：提交说明末尾按 Claude Code 自己的署名规范添加；不要把 `.env` 或任何密钥写进仓库、日志或账本。
- **归档**：`benchmarks/archive/` 里是 v3~v6 的冒烟记录（从 `results/` 移过来的），`docs/rtt-model-comparison.md` 第十一、十二节引用的文件现在在这里。
- **盲测投票**：页面导出的 JSON 里有 `run` 字段，`analyze` 会据此找对照表；`--run` 与文件不符会报错。
- **其他可复用资产**：`results/m4/agent_judge/` 是 GPT-6 当评委的评审包与答卷，可用作 JEV 校准的高阶参照；`benchmarks/AGENT_JUDGE_PROMPT.md` 是让外部 Agent 当评委的提示。

---

## 附录 A：用户原始任务书（2026-11-10，摘录要点）

- 角色：接管“自动化评测与自驱动工程调优闭环”，视为受预算与泛化约束的策略优化问题。目标：在不损失技术保真度的前提下，泛化效果稳定超越 `humanizer-zh`，产出最终评测报告。
- 一、环境与数据层：清理归档 `benchmarks/`，只保留支撑发布报告的里程碑（**已完成**，见第 9 节归档说明）；最大化复用 `.cache`；扩充语料并做 Train / Dev / Holdout-Test 三级物理隔离（固定种子分层抽样；Holdout 在收敛前严禁读取或调参）。
- 二、奖励函数层：JEV 评委先用历史人类盲评与 m3/m4 高置信评审数据回测校准；两两比较必须正反换位求期望；校准不达标先拆分维度，不用未校准信号作奖励。硬约束：pytest 100%、占位符无损 100%（保真度基线已按第 2 节第 5 条放宽为监控项）。主目标：Dev 上去 AI 腔提升、相对 Humanizer-zh 的去偏胜率与易读度期望分。
- 三、策略迭代层：账本记录状态-动作-奖励；五步棘轮（诊断 → 单变量假设 → 防过拟合 → 探针 / 全量两级评估 → Keep / Revert）。
- 四、预算与停机：总预算为 DeepSeek 余额（交接时 ¥34.40），为 Holdout 预留 6~8 元；收紧 `max_tokens`；至少探索 3 个正交方向；停机判据与终局交付见第 7 节。

## 附录 B：交接时状态

- 代码基线：`main` 上的 v6，以及本次交接提交（盲测渲染器修复、`results/` 归档、交接文档）。
- `python -m pytest -q`：198 个全部通过。
- 本次会话已执行的清理：
  - 删除：`.cache/*.bundle`、`.cache/apply_final_2.py`、`.cache/scratch`、`.cache/probe_drafts`、`.cache/xfer`、`.git/stale-*`；
  - 移动：`results/v3smoke~v6smoke` → `benchmarks/archive/`；项目根 `blind_votes.json` → `results/hard1/blind_votes_raw.json`；
  - 还原：只有换行符改动的 `LICENSE` 与 `results/m4/blind_*`。
