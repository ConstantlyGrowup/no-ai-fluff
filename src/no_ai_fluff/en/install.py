"""Install the English Claude Code adapter (subagent + /no-ai-fluff-en skill + MCP entry).

    no-ai-fluff-en install claude-code                # this project: .claude/agents, .claude/skills, .mcp.json
    no-ai-fluff-en install claude-code --scope user   # user level; register MCP with `claude mcp add -s user ...`
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from ..install import InstallReport, _merge_mcp_json, _put

SERVER_NAME = "no-ai-fluff-en"
MCP_ENTRY = {"command": "no-ai-fluff-en", "args": ["mcp"]}
MCP_ADD_USER = f"claude mcp add -s user {SERVER_NAME} -- no-ai-fluff-en mcp"


def template(name: str) -> str:
    return resources.files("no_ai_fluff.adapters.claude_code_en").joinpath(name).read_text("utf-8")


def install_claude_code_en(scope: str = "project", root: Path | None = None, force: bool = False) -> InstallReport:
    if scope not in ("project", "user"):
        raise ValueError(f"unknown scope: {scope}")
    root = Path(root) if root else (Path.home() if scope == "user" else Path.cwd())
    claude = root / ".claude"
    rep = InstallReport()
    _put(claude / "agents" / f"{SERVER_NAME}.md", template("agent.md"), force, rep)
    _put(claude / "skills" / SERVER_NAME / "SKILL.md", template("skill.md"), force, rep)
    if scope == "project":
        _merge_mcp_json(root / ".mcp.json", force, rep, SERVER_NAME, MCP_ENTRY)
        rep.next_steps.append("start claude in the project root and approve the no-ai-fluff-en server from .mcp.json")
    else:
        rep.next_steps.append(MCP_ADD_USER)
    rep.next_steps.append("set OPENROUTER_API_KEY in the environment or .env (used only for the round-trip translation; "
                          "TENCENTCLOUD_API_KEY is used if no OpenRouter key is found)")
    rep.next_steps.append("before delivering a long reply or document, type /no-ai-fluff-en or ask Claude to use the "
                          "no-ai-fluff-en subagent")
    return rep
