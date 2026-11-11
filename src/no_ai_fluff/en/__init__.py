"""no-ai-fluff-en: the English edition.

Same two-handshake design as the Chinese edition (mask -> round-trip translation -> host patches on the
original -> guarded merge), with its own masker, round-trip prompts and checks, radar, audit rules and
punctuation cleanup. Defaults: OpenRouter, tencent/hy-mt2-30b-a3b, Japanese bridge (as in the paper).

Entry points: `no-ai-fluff-en` (CLI) and `no-ai-fluff-en mcp` (stdio MCP server "no-ai-fluff-en").
"""
