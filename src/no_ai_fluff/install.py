"""把 Claude Code 适配（审校子智能体 + /no-ai-fluff Skill + MCP 注册）装进项目或用户目录。

    no-ai-fluff install claude-code                # 当前项目：.claude/agents、.claude/skills、.mcp.json
    no-ai-fluff install claude-code --scope user   # 用户级：~/.claude/agents、~/.claude/skills（MCP 需另行 claude mcp add -s user）

已存在且内容不同的文件默认不覆盖，加 --force 才覆盖；.mcp.json 只合并 no-ai-fluff 这一项，不动其他 Server。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

SERVER_NAME = "no-ai-fluff"
MCP_ENTRY = {"command": "no-ai-fluff", "args": ["mcp"]}
MCP_ADD_USER = f"claude mcp add -s user {SERVER_NAME} -- no-ai-fluff mcp"


@dataclass
class InstallReport:
    written: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # 已存在且不同，未加 --force
    next_steps: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        for title, paths in (("写入", self.written), ("已是最新", self.unchanged), ("已存在且不同，未覆盖（加 --force 覆盖）", self.skipped)):
            lines += [f"{title}：{p}" for p in paths]
        if self.next_steps:
            lines.append("")
            lines += [f"下一步：{s}" for s in self.next_steps]
        return "\n".join(lines)


def template(name: str) -> str:
    return resources.files("no_ai_fluff.adapters.claude_code").joinpath(name).read_text("utf-8")


def _put(path: Path, text: str, force: bool, rep: InstallReport) -> None:
    if path.exists():
        if path.read_text("utf-8") == text:
            rep.unchanged.append(path)
            return
        if not force:
            rep.skipped.append(path)
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8", newline="\n")
    rep.written.append(path)


def _merge_mcp_json(path: Path, force: bool, rep: InstallReport, name: str = SERVER_NAME,
                     entry: dict | None = None) -> None:
    entry = entry or MCP_ENTRY
    cfg = json.loads(path.read_text("utf-8")) if path.exists() else {}
    servers = cfg.setdefault("mcpServers", {})
    current = servers.get(name)
    if current == entry:
        rep.unchanged.append(path)
        return
    if current is not None and not force:
        rep.skipped.append(path)
        return
    servers[name] = entry
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", "utf-8", newline="\n")
    rep.written.append(path)


def install_claude_code(scope: str = "project", root: Path | None = None, force: bool = False) -> InstallReport:
    """scope=project：root 默认当前目录；scope=user：root 默认用户主目录。"""
    if scope not in ("project", "user"):
        raise ValueError(f"未知 scope：{scope}")
    root = Path(root) if root else (Path.home() if scope == "user" else Path.cwd())
    claude = root / ".claude"
    rep = InstallReport()
    _put(claude / "agents" / f"{SERVER_NAME}.md", template("agent.md"), force, rep)
    _put(claude / "skills" / SERVER_NAME / "SKILL.md", template("skill.md"), force, rep)
    if scope == "project":
        _merge_mcp_json(root / ".mcp.json", force, rep)
        rep.next_steps.append("在项目根目录启动 claude，首次会询问是否信任 .mcp.json 里的 no-ai-fluff Server，选择同意")
    else:
        rep.next_steps.append(MCP_ADD_USER)
    rep.next_steps.append("确认环境变量或 .env 里有 OPENROUTER_API_KEY（只用于 hy-mt2 往返翻译）")
    rep.next_steps.append("交付长回复或文档前输入 /no-ai-fluff，或直接让 Claude 用 no-ai-fluff 子智能体定稿")
    return rep
