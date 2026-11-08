# 评测台（Milestone 2）

## 语料

`corpus/samples.jsonl`：共 32 篇，coding / teaching / writing / discussion 各 5 篇，daily 12 篇，由 `corpus/build_samples.py` 合并生成。

| 场景 | 来源 | 采集脚本 |
|---|---|---|
| coding | GitHub 公开仓库里 Claude Code 生成的真实中文 PR 总结，记录了 `source_url` | `corpus/collect_github_prs.py` |
| teaching / writing / discussion | 只发用户问题、不加任何文风要求，轮流请 Claude / GPT / Gemini / DeepSeek 原生作答 | `corpus/collect_generated.py` |
| daily | 生活建议、情感倾诉、消费决策、日常小写作四类日常问题，DeepSeek 官方 API（`deepseek-flash`）原生作答。没有找到 2026 年以后的合适现成语料 | `corpus/collect_daily.py` |

被截断（`finish_reason=length`）或短于 300 字的样本会被 `build_samples.py` 剔除。

## 服务商约定

- OpenRouter **只用于**翻译小模型（`hy-mt2-30b-a3b`、`gemma-3-4b-it`），不调用大模型。
- 宿主补丁用 DeepSeek 官方 API（`--provider deepseek`，Key 读 `.env` 里的 `Deepseek_OFFICIAL_API_KEY`）。DeepSeek 会按 `--patch-max-tokens` 预扣额度，余额低时需要调小。
- 双轴评审由 Agent 宿主模型完成，见 `AGENT_JUDGE_PROMPT.md`。
- 横评报告：`python benchmarks/rtt_compare.py` → `docs/rtt-model-comparison.md`。

## 实验阶段

```bash
python benchmarks/run_ablation.py rtt      # 阶段 1：2 模型 × 3 桥接语往返翻译（已完成，results/m2/rtt.jsonl）
python benchmarks/run_ablation.py patch    # 阶段 2：宿主模型（默认 DeepSeek 官方 API）对照原文审校 RTT 初稿，只开局部 JSON 补丁
python benchmarks/run_ablation.py judge    # 阶段 3：评委模型双轴盲评（保真度 × 易读度）
python benchmarks/run_ablation.py report   # 汇总为 results/m2/summary.md
```

每个阶段都可以断点续跑，已完成的条目会自动跳过。OpenRouter 响应缓存在 `.cache/`。阶段 2 依赖阶段 1 留在 `.cache/drafts/` 里的草稿，所以要在同一个目录里运行。

## 用本地模型跑阶段 2、3

> 评委是**能被接口调用的本地模型**（Ollama / LM Studio / vLLM）时，把 [`LOCAL_JUDGE_TASK.md`](LOCAL_JUDGE_TASK.md) 交给执行者。
> 评委是**Agent 的宿主内置模型**（Codex、Antigravity 等）时，用 [`agent_judge.py`](agent_judge.py) 的评审包流程，把 [`AGENT_JUDGE_PROMPT.md`](AGENT_JUDGE_PROMPT.md) 里的提示词粘贴给 Agent。
> 跑完后用 `python benchmarks/run_ablation.py audit` 检查评审的来源与完整性。

本地模型要做两件事，提示词都已写好，模型只需按要求输出 JSON。

### 任务 A：补丁审校（阶段 2，共 192 条）

- **角色**：模拟生产环境里的宿主模型（Claude Code）。
- **输入**：原文、往返翻译初稿、雷达提示和审校规则，见 `src/no_ai_fluff/patcher.py` 的 `AUDIT_INSTRUCTIONS`。
- **输出**：只输出一个 JSON 数组 `[{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]`；初稿没问题就输出 `[]`。
- **要点**：`find` 必须逐字摘自初稿且唯一；只修术语翻偏、逻辑失真、指代不明；不准重写全文。不合格的补丁会被代码自动拒收，拒收率本身就是评测指标。

### 任务 B：双轴盲评（阶段 3，补丁全部完成后共 416 条）

- **角色**：评委。每条任务只给参考原文和一个匿名候选版本，候选可能是原文、纯 RTT 版或补丁版。
- **输出**：`{"fidelity": 1-5, "readability": 1-5, "errors": [...], "readability_issues": [...]}`。
- **要点**：只要出现一处会误导读者的技术事实错误，fidelity 不得高于 2。提示词见 `run_ablation.py` 的 `JUDGE_PROMPT`。

### 对模型的要求

- 上下文至少 32K：最长的提示词约 2.1 万字，中位数约 7 千字。
- 中文理解好、能稳定输出 JSON。推理模型的 `<think>…</think>` 会被自动去掉。
- 任务 A 和任务 B 最好用**不同**的模型，避免评委偏爱同一个模型的改写。

### 接法一：直连 OpenAI 兼容接口（Ollama / LM Studio / vLLM）

```bash
python benchmarks/run_ablation.py patch --base-url http://localhost:11434/v1 --host <补丁模型名> --workers 2
python benchmarks/run_ablation.py judge --base-url http://localhost:11434/v1 --judge <评委模型名> --workers 2
python benchmarks/run_ablation.py report
```

本地服务一般不需要 Key，需要时加 `--api-key`。`--workers` 是并发数，按显存调整。

### 接法二：导出任务，自行跑完再导回

```bash
python benchmarks/run_ablation.py export-patch                  # -> results/m2/tasks_patch.jsonl
#   逐行读 {"task_id", "prompt"}，把 prompt 原样发给模型，
#   写出 {"task_id", "response", "model"} 到 responses_patch.jsonl（model 可选）
python benchmarks/run_ablation.py import-patch responses_patch.jsonl
python benchmarks/run_ablation.py export-judge                  # 必须在补丁导入之后，才会包含补丁版
python benchmarks/run_ablation.py import-judge responses_judge.jsonl
python benchmarks/run_ablation.py report
```

建议 `temperature=0`，单条输出上限 4000 token。导入时解析失败的条目会被跳过并打印出来，修好后再导入一次即可，已导入的不会重复。

## M3 人工盲测

```bash
python benchmarks/blind_workbench.py build                       # 生成 blind_test_workbench.html（已生成）
# 用浏览器直接打开 benchmarks/blind_test_workbench.html，读完投票后点“导出结果”
python benchmarks/blind_workbench.py analyze blind_votes.json    # 解码并统计 -> results/m3/blind_summary.md
```

- 样本与候选见 `results/m3/run.json`：10 篇（每场景 2 篇，≤4500 字），每篇 4 个版本——原文，以及 hy-mt2 法 / 日 / 俄往返后由 DeepSeek 高推理强度宿主按 v2 规则开补丁、经标点清理的定稿。
- 页面里只有随机代号，A~D 顺序和篇目顺序都已打乱，不显示参考原文。代号与版本的对照表在 `.cache/blind/key-m3.json`，**测完之前不要打开，也不要删除**：删掉后投票就无法解码了。
- 投票自动保存在浏览器里，也可以中途“导出结果”，换浏览器后“载入进度”继续。
- `build` 在对照表已存在时会拒绝覆盖。确实要重建请加 `--force`，但这会让已导出的投票作废。
