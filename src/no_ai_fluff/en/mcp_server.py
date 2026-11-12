"""stdio MCP server for the English edition ("no-ai-fluff-en"). Same tool names and handshake as the Chinese edition."""

from __future__ import annotations

from ..delivery_note import host_note_en
from ..mcp_server import MCPServer
from ..mcp_server import main as _main
from .pipeline import EnPipeline

TOOLS_EN = [
    {
        "name": "prepare_rtt_draft",
        "title": "Prepare a round-trip draft (handshake 1)",
        "description": (
            "Takes an English draft meant for human readers (Markdown / HTML / plain text), freezes code, formulas and "
            "paths, round-trips the prose through another language, and returns draft_id, the original, the round-trip "
            "reference, radar hints and the editing rules. Then write JSON patches on the original and call "
            "finalize_patched_delivery. A few drafts first return a domain-triage request (status=needs_domain_triage): "
            "judge whether the listed sentences are technical, then call this tool again with draft_id and domain_triage "
            "(no text needed) to get the rules."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "the full draft (omit it in the domain-triage follow-up call)"},
                "format": {"type": "string", "enum": ["auto", "markdown", "html", "text"], "default": "auto"},
                "question": {"type": "string", "description": "optional: the user's original question"},
                "exit": {
                    "type": "string", "enum": ["terminal", "file"], "default": "terminal",
                    "description": "terminal = chat reply (10 s budget), file = document written to disk (60 s budget)",
                },
                "draft_id": {"type": "string", "description": "domain-triage follow-up: the draft_id from the first call"},
                "domain_triage": {
                    "description": ("domain-triage follow-up: whether the listed sentences come from a technical or "
                                    "engineering context. One boolean, an array of booleans, or "
                                    '[{"id": 1, "technical": true}] (or its JSON string)'),
                    "oneOf": [{"type": "boolean"},
                              {"type": "array", "items": {"oneOf": [{"type": "boolean"}, {"type": "object"}]}},
                              {"type": "string"}],
                },
            },
            "description": "Pass text on the first call; after a domain-triage request pass draft_id + domain_triage",
        },
    },
    {
        "name": "finalize_patched_delivery",
        "title": "Merge patches and return the final text (handshake 2)",
        "description": (
            'Submit patches [{"find": "exact span of the original", "replace": "new text", "type": "...", "reason": "..."}] '
            "or [] and get the final text. Patches that break frozen code, formulas or paths are rejected; if the final "
            "check fails, the original is returned unchanged."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string"},
                "patches": {
                    "description": "array of patches, or its JSON string",
                    "oneOf": [
                        {"type": "array", "items": {"type": "object", "properties": {
                            "find": {"type": "string"}, "replace": {"type": "string"}, "type": {"type": "string"},
                            "reason": {"type": "string"}}, "required": ["find", "replace"]}},
                        {"type": "string"},
                    ],
                },
            },
            "required": ["draft_id", "patches"],
        },
    },
]

MESSAGES_EN = {
    "text_empty": "text must not be empty",
    "text_or_draft": "pass text (first call), or draft_id + domain_triage (follow-up after a domain-triage request)",
    "no_draft_id": "draft_id is missing",
    "draft_not_found": "draft {draft_id} not found (already finalized or expired); call prepare_rtt_draft again",
    "unknown_tool": "unknown tool: {name}",
}

def format_prepare_en(res) -> str:
    head = f"draft_id: {res.draft_id}\n"
    if res.fallback:
        head += f"Note: the round trip did not run ({res.fallback_reason}); the reference equals the original. Review it anyway.\n"
    return (head + "\n" + res.audit_prompt
            + f'\n\nThen call finalize_patched_delivery(draft_id="{res.draft_id}", patches=<the JSON array>).')


def format_domain_triage_en(res) -> str:
    return (f"draft_id: {res.draft_id}\n"
            "The round trip is done and saved as a draft. One more step: the radar found a few sentences cut into short "
            "phrases. First decide whether they come from a technical or engineering context.\n\n"
            + res.triage_prompt
            + f'\n\nThen call prepare_rtt_draft(draft_id="{res.draft_id}", domain_triage=<the JSON array>) to get the rules.')


def format_finalize_en(res) -> str:
    lines = [res.final_text]
    meta = f"\n\n---\nPatches: {res.applied} applied, {res.rejected} rejected"
    if res.fallback_reason:
        meta += f"; {res.fallback_reason}"
    lines.append(meta)
    for p in [p for p in res.patches if not p["applied"]][:10]:
        lines.append(f"- rejected \"{p['find'][:30]}\": {p['reject_reason']}")
    lines.append("\n" + host_note_en())
    return "\n".join(lines)


class EnMCPServer(MCPServer):
    server_name = "no-ai-fluff-en"
    tools = TOOLS_EN
    instructions = (
        "Before delivering a long English reply or document meant for people: call prepare_rtt_draft, write local JSON "
        "patches on the original following the returned rules, then call finalize_patched_delivery for the final text. "
        "The final text is followed by delivery notes for the host: deliver the text as is, add the one-line note "
        "offering a paragraph rewrite in the chat, and if the user names a paragraph, rewrite only that part by the "
        "five rules given there."
    )
    messages = MESSAGES_EN
    pipeline_cls = EnPipeline

    def format_prepare(self, res) -> str:
        return format_prepare_en(res)

    def format_finalize(self, res) -> str:
        return format_finalize_en(res)

    def host_note(self) -> str:
        return host_note_en()

    def format_domain_triage(self, res) -> str:
        return format_domain_triage_en(res)


def main() -> None:
    _main(EnMCPServer)


if __name__ == "__main__":
    main()
