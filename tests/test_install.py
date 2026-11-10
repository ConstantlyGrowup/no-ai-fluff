import json

from no_ai_fluff import cli
from no_ai_fluff.install import MCP_ENTRY, install_claude_code, template
from no_ai_fluff.mcp_server import TOOLS


def test_agent_template_names_real_mcp_tools():
    agent = template("agent.md")
    assert agent.startswith("---\nname: no-ai-fluff\n")
    for t in TOOLS:  # 子智能体的 tools 白名单必须和 Server 实际提供的工具一致
        assert f"mcp__no-ai-fluff__{t['name']}" in agent
    assert "subagent_type: \"no-ai-fluff\"" in template("skill.md")


def test_project_install_writes_agent_skill_and_merges_mcp_json(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), "utf-8")
    rep = install_claude_code("project", tmp_path)
    assert (tmp_path / ".claude/agents/no-ai-fluff.md").read_text("utf-8") == template("agent.md")
    assert (tmp_path / ".claude/skills/no-ai-fluff/SKILL.md").read_text("utf-8") == template("skill.md")
    cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
    assert cfg["mcpServers"] == {"other": {"command": "x"}, "no-ai-fluff": MCP_ENTRY}
    assert len(rep.written) == 3
    again = install_claude_code("project", tmp_path)  # 幂等
    assert not again.written and len(again.unchanged) == 3


def test_install_does_not_clobber_user_edits_without_force(tmp_path):
    install_claude_code("project", tmp_path)
    agent = tmp_path / ".claude/agents/no-ai-fluff.md"
    agent.write_text("my edits", "utf-8")
    rep = install_claude_code("project", tmp_path)
    assert agent in rep.skipped and agent.read_text("utf-8") == "my edits"
    install_claude_code("project", tmp_path, force=True)
    assert agent.read_text("utf-8") == template("agent.md")


def test_user_scope_skips_mcp_json_and_prints_mcp_add(tmp_path):
    rep = install_claude_code("user", tmp_path)
    assert not (tmp_path / ".mcp.json").exists()
    assert any("claude mcp add -s user no-ai-fluff" in s for s in rep.next_steps)


def test_cli_install(tmp_path, capsys):
    assert cli.main(["install", "claude-code", "--dir", str(tmp_path)]) == 0
    assert "no-ai-fluff.md" in capsys.readouterr().out
    assert (tmp_path / ".mcp.json").exists()
