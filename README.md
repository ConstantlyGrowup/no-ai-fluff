# no-ai-fluff

把大模型写给人看的中文长回复和文档，在交付前改成好读的大白话。

前沿大模型经过代码、数学类强化学习后，输出会出现连续名词堆叠、生造压缩代号、省略连接词等“高压缩方言”，中文里还叠加散装英文、翻译腔和互联网黑话。直接在主会话里塞文风规则会干扰模型的编码与推理，所以本项目把净化放在**交付层**：

1. 用一个便宜的翻译小模型把草稿做一次 **中文 → 桥接语 → 中文** 往返翻译（RTT）。翻译模型只会写常规句子，压缩句式在往返中被自然拆开。
2. 往返翻译可能把术语翻偏、把“仅在…时”弱化成“通常在…时”。宿主大模型在一个**独立的子上下文**里对照原文审校，只允许输出局部 JSON 补丁，不准重写全文。
3. 代码、公式、路径、URL 等内容全程冻结为占位符，任何一步出错都**无损回退**到原文。

理论依据见 Clark et al., 2026（[arXiv 2610.29162](https://arxiv.org/html/2610.29162v1)），完整设计见 [`docs/architecture.md`](docs/architecture.md)，人工审校的写作原则见 [`SKILL.md`](SKILL.md)。

## 流水线

| 阶段 | 模块 | 做什么 |
|---|---|---|
| Stage 0 | `masker.py` | 拆出 Markdown / HTML 的结构骨架；围栏代码块、公式块、表格分隔行等原样直通；行内代码、路径、标识符、工程缩写冻结为 `{{Pn}}`；日常散装英文（edge case、overhead）故意不冻结 |
| Stage 1 | `rtt.py` | 调 OpenRouter 翻译小模型往返翻译；多段合批，分界符对不上时二分重试；每段校验占位符、行内标记、长度比例，失败的段落退回原文 |
| Stage 2a | `radar.py` | 5 类确定性表层雷达：长句与定语堆积、数字不一致、套话黑话、散装英文、密集代词。只作提示，不据此放行 |
| Stage 2b | `patcher.py` | 生成给宿主模型的审校提示词（< 800 字） |
| Stage 3 | `patcher.py` + `pipeline.py` | 逐条合并补丁；切断、丢失、复制受保护内容或有歧义的补丁整条拒收；定稿再做一次全量校验，不通过则整体回退原文 |
| — | `telemetry.py` | 追加写 `~/.no-ai-fluff/events.jsonl`，只记统计量，不记正文 |

对外是**两次握手**：

```
prepare_rtt_draft(text)                 -> draft_id + 原文 + RTT 初稿 + 雷达提示 + 审校规则
finalize_patched_delivery(id, patches)  -> 定稿
```

## 安装

需要 Python ≥ 3.10，运行时零第三方依赖。

```bash
pip install -e .            # 提供 no-ai-fluff 命令
pip install -e ".[dev]"     # 加装 pytest
```

在仓库根目录或当前目录放一个 `.env`（已被 `.gitignore` 忽略）：

```
OPENROUTER_API_KEY=sk-or-...
```

### 可选环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NO_AI_FLUFF_PROVIDER` | `openrouter` | 翻译小模型服务商：`openrouter`，或 `tencent`（腾讯云 TokenHub，需 `TENCENTCLOUD_API_KEY`） |
| `NO_AI_FLUFF_MODEL` | 随服务商 | OpenRouter 默认 `tencent/hy-mt2-30b-a3b`；腾讯云默认 `hy-mt2-pro` |
| `NO_AI_FLUFF_BRIDGE` | `fr` | 桥接语：`fr` / `ja` / `ru` |
| `NO_AI_FLUFF_CHAT_TIMEOUT` | `10` | 终端回复的 RTT 整体时限（秒） |
| `NO_AI_FLUFF_DOC_TIMEOUT` | `60` | 落盘文档的 RTT 整体时限（秒） |
| `NO_AI_FLUFF_TERMS` | 空 | 额外冻结的术语，逗号分隔 |
| `NO_AI_FLUFF_CACHE_DIR` | 空 | OpenRouter 响应磁盘缓存目录，评测复现用 |
| `NO_AI_FLUFF_HOME` | `~/.no-ai-fluff` | 遥测与草稿目录 |
| `NO_AI_FLUFF_TELEMETRY` | `1` | 设为 `0` 关闭遥测 |

横评结论（[`docs/rtt-model-comparison.md`](docs/rtt-model-comparison.md)）：中文往返翻译选 hy-mt2，后续接入腾讯云 `hy-mt2-pro`；默认桥接语将在法语和日语之间由双轴评测与盲测决定。

## 命令行

```bash
no-ai-fluff prepare draft.md > prep.json          # 握手 1
no-ai-fluff finalize <draft_id> --patches p.json  # 握手 2，补丁可用文件、- 或内联 JSON
no-ai-fluff run draft.md -o out.md                # 不给补丁即纯 RTT
no-ai-fluff radar draft.md                        # 只看表层雷达统计
no-ai-fluff stats                                 # 遥测汇总表
no-ai-fluff mcp                                   # 启动 stdio MCP Server
```

## 作为 MCP Server 接入

```bash
claude mcp add no-ai-fluff -- no-ai-fluff mcp
```

Server 提供 `prepare_rtt_draft` 和 `finalize_patched_delivery` 两个工具。配套的 `context: fork` 审校子智能体、Codex CLI 与 DeepSeek Harness 适配放在 Milestone 4。

## 在 Python 里直接用

```python
from no_ai_fluff import Pipeline

pipe = Pipeline()
prep, fin = pipe.run(draft, reviewer=lambda prompt: my_llm(prompt))  # reviewer 返回 JSON 补丁
print(fin.final_text)
```

## 测试

```bash
python -m pytest
```

单元测试全部 mock 网络，不消耗 API 额度。

## 当前实测（Milestone 1，`hy-mt2-30b-a3b` + 法语，15 条语料）

- 占位符（代码、路径、公式等）丢失：0；代码围栏全部保持一致。
- 段级回退率约 2%：主要来自翻译模型合并相邻短行，以及上游服务商的内容过滤误伤。
- RTT 耗时：500~900 字的回复约 4~10 秒；2000~4200 字的文档约 8~15 秒。单篇翻译花费约 0.0003~0.002 美元。
- 已知问题：短回复的耗时贴近 10 秒对话时限，偶尔会整体回退到原文。

## 路线图

- [x] **Milestone 1**：核心四阶引擎、编排与回退、CLI、MCP Server 骨架、单元测试
- [ ] **Milestone 2**：四大场景真实语料，`2 模型 × 3 桥接语` 消融实验
- [ ] **Milestone 3**：本地盲测工作台，锁定默认模型与桥接语，产出评测报告
- [ ] **Milestone 4**：Claude Code / Codex CLI / DeepSeek Harness 多端适配与发布

## License

MIT
