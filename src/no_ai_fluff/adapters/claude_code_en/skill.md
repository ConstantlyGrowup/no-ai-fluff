---
name: no-ai-fluff-en
description: Hand a long English reply or document meant for people to the no-ai-fluff-en editor subagent before delivering it (round-trip reference + local patches; code and formulas untouched). For PR summaries, technical explanations, proposals, reports, READMEs; not for short chat.
---

# /no-ai-fluff-en: a plain-English pass before delivery

When you have finished an English deliverable meant for people (a long terminal reply, or a `.md` / `.html` file to write), before handing it to the user:

1. Start the `no-ai-fluff-en` subagent with the Agent tool (`subagent_type: "no-ai-fluff-en"`) and give it:
   - the full draft, or the draft's file path;
   - the user's original question, if any;
   - the exit: `exit=file` plus the target path if it will be written to disk, otherwise `exit=terminal`.
2. The subagent calls the `prepare_rtt_draft` and `finalize_patched_delivery` MCP tools in its own context and returns only the final text.
3. Deliver that final text as is; do not polish it again yourself.

If the user passes text or a file path with the command (`/no-ai-fluff-en path/to/draft.md`), treat it as the draft to edit.

Requires the MCP server (`claude mcp add no-ai-fluff-en -- no-ai-fluff-en mcp`, or a `.mcp.json` written by `no-ai-fluff-en install claude-code`) and `OPENROUTER_API_KEY` in the environment or `.env` (`TENCENTCLOUD_API_KEY` is used if no OpenRouter key is found).
