---
name: no-ai-fluff-en
description: Hand a long English reply or document meant for people to the no-ai-fluff-en editor subagent before delivering it (round-trip reference + local patches; code and formulas untouched); afterwards, rewrite only the paragraph the user names, using the built-in plain-English rules. For PR summaries, technical explanations, proposals, reports, READMEs; not for short chat.
---

# /no-ai-fluff-en: a plain-English pass before delivery

When you have finished an English deliverable meant for people (a long terminal reply, or a `.md` / `.html` file to write), before handing it to the user:

1. Start the `no-ai-fluff-en` subagent with the Agent tool (`subagent_type: "no-ai-fluff-en"`) and give it:
   - the full draft, or the draft's file path;
   - the user's original question, if any;
   - the exit: `exit=file` plus the target path if it will be written to disk, otherwise `exit=terminal`.
2. The subagent calls the `prepare_rtt_draft` and `finalize_patched_delivery` MCP tools in its own context and returns only the final text.
3. Deliver that final text as is; do not polish it again yourself. Add one short note in the chat when you deliver it (see the next section).

If the user passes text or a file path with the command (`/no-ai-fluff-en path/to/draft.md`), treat it as the draft to edit.

## The note after delivery (chat only)

- `exit=terminal`: send the final text as is, then a blank line and a horizontal rule, then this note:

  > If any paragraph still feels dense or jargon-heavy and needs a full rewrite instead of light edits, just tell me which part to rewrite in plain English (or paste those sentences). I will rewrite only that part from scratch and keep all code, paths, formulas and numbers untouched.

- `exit=file`: **never put this note in the target file**; the file holds only the clean final text. Report the file path in the chat, then add the note.

## When the user asks for a rewrite: the paragraph-rewrite fallback

When the user says "rewrite paragraph 2", "this part is still too dense, rewrite it", or pastes a few sentences to rewrite, you (the main session) do the rewrite yourself; do not call the subagent again. This is no longer a light edit, and you may reorder the whole paragraph, but keep these five rules:

1. **Touch only what was named**: rewrite only the paragraph or sentences the user pointed to; leave every other paragraph exactly as it is.
2. **Keep hard content intact and invent nothing**: code blocks, inline code, commands, file paths, URLs, LaTeX formulas, version numbers and every number stay character for character. Add no mechanism, consequence or condition the original did not state; keep signs, order, direction and sequence as written, even if the original looks wrong.
3. **Keep genuine terms, unpack packaging jargon**: if a plain substitute would make an expert misunderstand, it is a genuine term (for example `P99`, idempotent, deadlock, SHA256) and stays. Otherwise it is packaging jargon (for example drive alignment, operationalize, holistic, de-risk): say the action it actually stands for.
4. **Break up compressed and long sentences**: replace `→`, `+` and `/` standing in for words, and chains of fragments with no subject or linking words, with complete sentences that use "first", "then", "because", "so". Split sentences over about 25 words, put back a dropped "that" in relative clauses, and unpack noun stacks.
5. **Say it plainly from scratch**: do not patch the old sentence word by word; stay faithful to the meaning and make it readable in one pass. Do not pad, add background or end on a slogan.

After rewriting, send only the rewritten paragraph (or sentences) and say which part you changed; if the deliverable is a file, use an edit tool to replace just that paragraph in the file.

Requires the MCP server (`claude mcp add no-ai-fluff-en -- no-ai-fluff-en mcp`, or a `.mcp.json` written by `no-ai-fluff-en install claude-code`) and `OPENROUTER_API_KEY` in the environment or `.env` (`TENCENTCLOUD_API_KEY` is used if no OpenRouter key is found).
