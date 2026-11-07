# 交给本地模型 / 本地 Agent 的评审任务说明

> 把这份文件原样交给执行者。执行者如果是一个 Agent（Codex、Antigravity、Cursor 等），
> 请特别注意第 2 节的禁止事项。

## 1. 任务是什么

`no-ai-fluff` 项目的 Milestone 2 实验需要对 **247 条**评审任务打分。每条任务是一段已经写好的中文提示词，
里面有一篇【参考原文】和一篇【候选文本】，要求评委模型输出一行 JSON：

```json
{"fidelity": 1-5, "readability": 1-5, "errors": ["..."], "readability_issues": ["..."]}
```

**执行者要做的事只有一件：把每条提示词原样发给评委模型，拿回它的原始回复，交给脚本记录。**
执行者自己不打分，也不改提示词。

## 2. 禁止事项（上一轮就是在这里出的问题）

- **禁止用代码、规则或启发式算法生成分数。** 例如根据文本长度、雷达指标、补丁数量、模型名、场景来给分，
  或给某类版本写死分数。这样得到的不是评审，结果会被作废。
- **禁止**复用补丁阶段（`patch.jsonl`）里的 `reason` / `find` / `replace` 来拼凑 `errors`。
- **禁止**在 `model` 字段里填写没有真正生成这条回复的模型名。
- 某条任务调用失败或输出无法解析时，**留空并报告**，不要补默认值。脚本支持断点续跑，之后重跑即可补齐。
- 如果评委模型不可用、太慢或上下文不够，**停下来告诉用户**，不要换成其他方式“近似完成”。

## 3. 推荐做法：一条命令跑完（脚本自己逐条调用模型）

前提：评委模型以 OpenAI 兼容接口提供服务。Ollama 默认地址是 `http://localhost:11434/v1`，
LM Studio 是 `http://localhost:1234/v1`，vLLM 一般是 `http://localhost:8000/v1`。

在项目根目录执行：

```bash
# 先用 1 篇样本试跑，确认模型能稳定输出 JSON
python benchmarks/run_ablation.py judge --base-url http://localhost:11434/v1 --judge <模型名> --only coding-002 --workers 1

# 没问题再跑全部（已完成的会自动跳过）
python benchmarks/run_ablation.py judge --base-url http://localhost:11434/v1 --judge <模型名> --workers 2

# 检查完整性与来源，然后出报告
python benchmarks/run_ablation.py audit
python benchmarks/run_ablation.py report
```

脚本会把每条评委原始输出存进 `judge.jsonl` 的 `raw` 字段，`audit` 会检查数量、来源，以及有没有模板化的输出。

## 4. 备选做法：导出 → 自己调用模型 → 导入

只在评委模型没有 OpenAI 兼容接口时使用。

```bash
python benchmarks/run_ablation.py export-judge       # -> benchmarks/results/m2/tasks_judge.jsonl（247 行）
```

对 `tasks_judge.jsonl` 的**每一行**：

1. 取出 `prompt`，**一字不改**地作为单轮 user 消息发给评委模型（建议 temperature=0，输出上限 4000 token）；
2. 把模型回复**原样**写成一行：`{"task_id": "<同一 task_id>", "response": "<模型原始回复>", "model": "<真实模型名>"}`；
3. 所有回复写进 `responses_judge.jsonl`。

```bash
python benchmarks/run_ablation.py import-judge responses_judge.jsonl
python benchmarks/run_ablation.py audit
python benchmarks/run_ablation.py report
```

## 5. 对评委模型的要求

- 上下文 ≥ 32K token：最长的提示词约 2.1 万字，中位数约 7 千字。
- 中文理解好，能稳定输出 JSON。推理模型的 `<think>…</think>` 会被自动去掉。
- 最好**不是** Claude 系列，因为补丁阶段用的是 Claude Sonnet 5.5，同一家模型当评委容易偏爱它的改写。
- 已完成的 13 条（`coding-001`）用的评委是 `google/gemini-3.1-pro-preview`。换评委会让这两部分不能直接比较，
  所以建议用新评委把 `coding-001` 也重评一遍：先把 `judge.jsonl` 改名备份，再跑上面的命令，就是全量 260 条。

## 6. 工作量参考

247 条 × 平均约 7 千字输入。本地 32B 级模型单卡大约每条 20~60 秒，全部约 2~4 小时。
如果太慢，可以先只跑一部分，例如 `--only coding teaching`。报告会按实际完成的条目统计，`audit` 会列出缺口。
