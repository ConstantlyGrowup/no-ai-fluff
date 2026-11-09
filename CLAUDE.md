# no-ai-fluff：给 Claude Code 的项目说明

当前任务是“自动化评测与自驱动调优闭环”。开始任何工作前，先完整阅读 **`docs/HANDOFF-optimization-loop.md`**。权限、预算、数据切分和闭环协议都以那份文档为准。

几条不能忘的硬规则（详见交接文档第 2、9 节）：

- `.env` 里有密钥：不要打印，不要提交，也不要写进日志或账本。
- 宿主补丁和 Humanizer-zh 基线只走 DeepSeek 官方 API。OpenRouter 只用于以下三项：
  - hy-mt2 往返翻译；
  - `typesafe/jev-1.13` 评委；
  - 已批准的一次性语料生成（每题只用一个模型）。
- 绝不删除以下内容：
  - `benchmarks/.cache/openrouter`、`benchmarks/.cache/deepseek`、`benchmarks/.cache/drafts`；
  - `benchmarks/.cache/blind/key-*.json`、`benchmarks/.cache/agent_judge/key-*.json`；
  - `benchmarks/.cache/quarantine/`。
- `benchmarks/splits/holdout.lock.json` 里的样本：在宣布收敛前，不读正文，不跑实验。
- 所有评分必须来自真实模型调用或真实人工投票，并保存原始返回。
- 每次提交前跑 `python -m pytest -q`，必须 100% 通过。
- 每一轮迭代都记入 `benchmarks/optimization_ledger.jsonl`。
