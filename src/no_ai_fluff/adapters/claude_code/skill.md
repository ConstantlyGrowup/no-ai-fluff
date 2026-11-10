---
name: no-ai-fluff
description: 把一篇要交给人读的中文长回复或文档交给 no-ai-fluff 审校子智能体定稿（往返翻译解压 + 局部补丁，代码与公式零改动）。适用于 PR 总结、技术讲解、方案、报告、README 等交付物；短闲聊不用。
---

# /no-ai-fluff：交付前过一遍“说人话”审校

当你写完一篇面向人类阅读的中文交付物（终端里的长回复，或要落盘的 `.md` / `.html`），在交给用户之前：

1. 用 Agent 工具启动 `no-ai-fluff` 子智能体（`subagent_type: "no-ai-fluff"`），在提示里给它：
   - 草稿全文，或草稿文件路径；
   - 用户的原始问题（有的话）；
   - 出口：要写文件就写明 `exit=file` 和目标路径，否则是 `exit=terminal`。
2. 子智能体会调用 `prepare_rtt_draft` 和 `finalize_patched_delivery` 两个 MCP 工具，在独立上下文里完成审校，只把定稿交回来。
3. 拿到定稿后原样交付给用户，不要再自己润色一遍。

如果用户在调用时附带了文字或文件路径（`/no-ai-fluff path/to/draft.md`），就把它当作要审校的草稿。

前提：已经注册 MCP Server（`claude mcp add no-ai-fluff -- no-ai-fluff mcp`，或项目根目录有 `no-ai-fluff install claude-code` 生成的 `.mcp.json`），并在环境变量或 `.env` 里配置了 `OPENROUTER_API_KEY`。
