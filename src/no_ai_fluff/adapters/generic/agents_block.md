## no-ai-fluff：交付前的“说人话”审校

- 用户说“用 no-ai-fluff 过一遍 / 定稿 / 改成大白话”，或者要交付面向人类阅读的中文长回复、`.md` / `.html` 文档时：先调用 MCP 工具 `prepare_rtt_draft`，按返回的规则只写局部 JSON 补丁，再调用 `finalize_patched_delivery` 取定稿；不要绕过这两个工具自己重写全文。详细步骤见 `no-ai-fluff` Skill（如果当前 agent 支持 Skill）。
- 定稿原样交给用户。`finalize_patched_delivery` 返回内容里 `---` 之后的部分只给你看，不写进交付物。
- 交付后在聊天里附一句提示（写文件时只在聊天里说，不写进文件）：{{NOTE}}
- 用户之后点名某一段要整段重写时，你直接重写那一段，不再调用 MCP 工具，守住七条：

{{RULES}}
