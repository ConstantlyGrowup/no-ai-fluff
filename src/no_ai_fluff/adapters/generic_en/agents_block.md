## no-ai-fluff-en: a plain-English pass before delivery

- When the user says "run it through no-ai-fluff" or "make this plain English", or before delivering a long English reply or `.md` / `.html` document meant for people: call the MCP tool `prepare_rtt_draft`, write only local JSON patches following the returned rules, then call `finalize_patched_delivery` for the final text. Do not bypass these tools and rewrite the whole text yourself. The `no-ai-fluff-en` skill has the detailed steps, if this agent supports skills.
- Deliver the final text as is. In the `finalize_patched_delivery` result, everything after `---` is for you only and stays out of the deliverable.
- After delivering, add this note in the chat (for a file, in the chat only, never in the file): {{NOTE}}
- If the user later names a paragraph to rewrite, rewrite just that part yourself without calling the MCP tools again, and keep these five rules:

{{RULES}}
