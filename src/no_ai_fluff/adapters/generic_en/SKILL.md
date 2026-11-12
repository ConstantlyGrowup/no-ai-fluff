---
name: no-ai-fluff-en
description: Before delivering a long English reply or document meant for people (PR summaries, technical explanations, proposals, reports, READMEs), finalize it with the no-ai-fluff-en MCP tools (round-trip reference + local patches; code and formulas untouched); afterwards, rewrite only the paragraph the user names, using the built-in plain-English rules. Not for short chat.
---

# no-ai-fluff-en: a plain-English pass before delivery

Use this when the user says "run it through no-ai-fluff", "finalize this with no-ai-fluff" or "make this plain English", or when you have written a long English reply or a `.md` / `.html` file meant for people.

## Finalizing

1. Call the MCP tool `prepare_rtt_draft` with the full draft as `text`, the user's original question as `question` (if any), and `exit` (`file` if it will be written to disk, otherwise `terminal`).
2. If it returns `status=needs_domain_triage`, decide whether the listed sentences come from a technical or engineering context and call `prepare_rtt_draft` again with `draft_id` and `domain_triage` to get the editing rules.
3. Following the returned rules, compare the original with the round-trip reference and write local JSON patches only for hard-to-read sentences (`find` must be text from the original); pass `[]` if nothing needs changing.
4. Call `finalize_patched_delivery(draft_id, patches)`. In the result, the text before `---` is the final text; after `---` come patch counts and delivery notes for you, which must stay out of the deliverable.
5. Deliver the final text as is; do not polish it again yourself.

If your agent supports subagents, steps 1 to 4 can run in a subagent with its own context, but that subagent must also go through both MCP tools and must not rewrite the whole text by itself. Use high reasoning effort for the review.

## The note after delivery (chat only)

- Terminal reply (`exit=terminal`): send the final text as is, then a blank line and a horizontal rule, then this note:

  > {{NOTE}}

- File (`exit=file`): **never put this note in the target file**; the file holds only the clean final text. Report the file path in the chat, then add the note.

## When the user asks for a rewrite: the paragraph-rewrite fallback

When the user says "rewrite paragraph 2", "this part is still too dense, rewrite it", or pastes a few sentences to rewrite, rewrite them yourself without calling the MCP tools again. This is no longer a light edit, and you may reorder the whole paragraph, but keep these seven rules:

{{RULES}}

After rewriting, send only the rewritten paragraph (or sentences) and say which part you changed; if the deliverable is a file, replace just that paragraph in the file.

Requires the MCP server registered in your agent (stdio, command `no-ai-fluff-en`, argument `mcp`) and `OPENROUTER_API_KEY` in the environment or `.env` (`TENCENTCLOUD_API_KEY` is used if no OpenRouter key is found).
