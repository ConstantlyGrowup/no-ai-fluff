"""把 Claude Code 适配（审校子智能体 + /no-ai-fluff Skill + MCP 注册）装进项目或用户目录。

    no-ai-fluff install claude-code                # 当前项目：.claude/agents、.claude/skills、.mcp.json
    no-ai-fluff install claude-code --scope user   # 用户级：~/.claude/agents、~/.claude/skills（MCP 需另行 claude mcp add -s user）

已存在且内容不同的文件默认不覆盖，加 --force 才覆盖；.mcp.json 只合并 no-ai-fluff 这一项，不动其他 Server。

其他 agent（不认识 Claude Code 的子智能体）装通用 Skill，并在 AGENTS.md 里加一段说明：

    no-ai-fluff install codex | zcode | qoder | workbuddy | generic   [--scope user]

MCP Server 需要在对应 agent 里另行注册（命令 no-ai-fluff，参数 mcp），安装结果会给出提示。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .delivery_note import NOTE_EN, NOTE_ZH, RULES_EN, RULES_ZH

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


# -- 其他 agent：通用 Skill + AGENTS.md ---------------------------------------------
# 每个宿主：(项目级 Skill 目录, 用户级 Skill 目录, 项目级 AGENTS.md, 用户级 AGENTS.md, 只能装到用户级)
# 目录依据各家文档（2026-11）：Codex 与 ZCode 读 .agents/skills，Qoder 读 .qoder/skills，WorkBuddy 读
# ~/.workbuddy/skills；Codex、ZCode、Qoder、DeepSeek Harness 都会读项目根目录的 AGENTS.md。
HOSTS: dict[str, tuple[str | None, str | None, str | None, str | None, bool]] = {
    "codex": (".agents/skills", ".agents/skills", "AGENTS.md", ".codex/AGENTS.md", False),
    "zcode": (".agents/skills", ".agents/skills", "AGENTS.md", ".zcode/AGENTS.md", False),
    "qoder": (".qoder/skills", ".qoder/skills", "AGENTS.md", None, False),
    "workbuddy": (None, ".workbuddy/skills", None, None, True),
    "generic": (".agents/skills", ".agents/skills", "AGENTS.md", None, False),
}

LANG = {
    "zh": {"name": SERVER_NAME, "pkg": "no_ai_fluff.adapters.generic", "note": NOTE_ZH, "rules": RULES_ZH},
    "en": {"name": "no-ai-fluff-en", "pkg": "no_ai_fluff.adapters.generic_en", "note": NOTE_EN, "rules": RULES_EN},
}


def generic_template(name: str, lang: str = "zh") -> str:
    """通用 Skill / AGENTS.md 段落：提示语和七条规则从 delivery_note 填入，和 MCP 返回的交付说明同源。"""
    cfg = LANG[lang]
    text = resources.files(cfg["pkg"]).joinpath(name).read_text("utf-8")
    return text.replace("{{NOTE}}", cfg["note"]).replace("{{RULES}}", cfg["rules"])


def _merge_block(path: Path, name: str, block: str, force: bool, rep: InstallReport) -> None:
    """在 AGENTS.md 里放一段带标记的说明；只动标记之间的内容，文件其余部分原样保留。"""
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    body = f"{start}\n{block.strip()}\n{end}\n"
    old = path.read_text("utf-8") if path.exists() else ""
    if start in old and end in old:
        i, j = old.index(start), old.index(end) + len(end)
        if old[i:j] + "\n" == body:
            rep.unchanged.append(path)
            return
        if not force:
            rep.skipped.append(path)
            return
        new = old[:i] + body.rstrip("\n") + old[j:]
    else:
        new = (old.rstrip("\n") + "\n\n" if old.strip() else "") + body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, "utf-8", newline="\n")
    rep.written.append(path)


def install_generic(host: str, scope: str = "project", root: Path | None = None, force: bool = False,
                    lang: str = "zh") -> InstallReport:
    """给 Codex / ZCode / Qoder / WorkBuddy 等装通用 Skill，并在 AGENTS.md 里加一段说明。"""
    if host not in HOSTS:
        raise ValueError(f"未知宿主：{host}")
    if scope not in ("project", "user"):
        raise ValueError(f"未知 scope：{scope}")
    proj_skills, user_skills, proj_md, user_md, user_only = HOSTS[host]
    if user_only:
        scope = "user"
    root = Path(root) if root else (Path.home() if scope == "user" else Path.cwd())
    cfg = LANG[lang]
    name = cfg["name"]
    rep = InstallReport()
    skills_dir = user_skills if scope == "user" else proj_skills
    if skills_dir:
        _put(root / skills_dir / name / "SKILL.md", generic_template("SKILL.md", lang), force, rep)
    md = user_md if scope == "user" else proj_md
    if md:
        _merge_block(root / md, name, generic_template("agents_block.md", lang), force, rep)
    zh = lang == "zh"
    if host == "codex":
        rep.next_steps.append(f"codex mcp add {name} -- {name} mcp")
    else:
        rep.next_steps.append(
            (f"在 {host} 的 MCP 设置里添加 stdio Server：名称 {name}，命令 {name}，参数 mcp"
             f"（JSON 写法：{{\"mcpServers\": {{\"{name}\": {{\"command\": \"{name}\", \"args\": [\"mcp\"]}}}}}}）")
            if zh else
            (f"add a stdio MCP server in {host}'s MCP settings: name {name}, command {name}, argument mcp "
             f"(JSON: {{\"mcpServers\": {{\"{name}\": {{\"command\": \"{name}\", \"args\": [\"mcp\"]}}}}}})"))
    if user_only and zh:
        rep.next_steps.append(f"{host} 只支持用户级 Skill，已装到 {root / skills_dir}；重启 {host} 后生效")
    elif user_only:
        rep.next_steps.append(f"{host} only supports user-level skills; installed under {root / skills_dir}. Restart {host}")
    rep.next_steps.append("确认环境变量或 .env 里有 OPENROUTER_API_KEY（只用于 hy-mt2 往返翻译）" if zh else
                          "set OPENROUTER_API_KEY in the environment or .env (used only for the round-trip translation)")
    rep.next_steps.append(f"交付长回复或文档前说“用 {name} 过一遍”，或在支持 Skill 的 agent 里调用 {name} Skill" if zh else
                          f"before delivering a long reply or document, say \"run it through {name}\", or invoke the "
                          f"{name} skill if your agent supports skills")
    return rep
