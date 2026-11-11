"""stdio MCP server for the English edition ("no-ai-fluff-en"). Same tool names and handshake as the Chinese edition."""

from __future__ import annotations

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
            "finalize_patched_delivery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "the full draft"},
                "format": {"type": "string", "enum": ["auto", "markdown", "html", "text"], "default": "auto"},
                "question": {"type": "string", "description": "optional: the user's original question"},
                "exit": {
                    "type": "string", "enum": ["terminal", "file"], "default": "terminal",
                    "description": "terminal = chat reply (10 s budget), file = document written to disk (60 s budget)",
                },
            },
            "required": ["text"],
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
    "no_draft_id": "draft_id is missing",
    "draft_not_found": "draft {draft_id} not found (already finalized or expired); call prepare_rtt_draft again",
    "term_review": "{prompt}",
    "unknown_tool": "unknown tool: {name}",
}

def format_prepare_en(res) -> str:
    head = f"draft_id: {res.draft_id}\n"
    if res.fallback:
        head += f"Note: the round trip did not run ({res.fallback_reason}); the reference equals the original. Review it anyway.\n"
    return (head + "\n" + res.audit_prompt
            + f'\n\nThen call finalize_patched_delivery(draft_id="{res.draft_id}", patches=<the JSON array>).')


def format_finalize_en(res) -> str:
    lines = [res.final_text]
    meta = f"\n\n---\nPatches: {res.applied} applied, {res.rejected} rejected"
    if res.fallback_reason:
        meta += f"; {res.fallback_reason}"
    lines.append(meta)
    for p in [p for p in res.patches if not p["applied"]][:10]:
        lines.append(f"- rejected \"{p['find'][:30]}\": {p['reject_reason']}")
    return "\n".join(lines)


class EnMCPServer(MCPServer):
    server_name = "no-ai-fluff-en"
    tools = TOOLS_EN
    instructions = (
        "Before delivering a long English reply or document meant for people: call prepare_rtt_draft, write local JSON "
        "patches on the original following the returned rules, then call finalize_patched_delivery for the final text."
    )
    messages = MESSAGES_EN
    pipeline_cls = EnPipeline

    def format_prepare(self, res) -> str:
        return format_prepare_en(res)

    def format_finalize(self, res) -> str:
        return format_finalize_en(res)


def main() -> None:
    _main(EnMCPServer)


if __name__ == "__main__":
    main()
