"""定稿后的“整段重写”提示：MCP 返回、Claude Code 斜杠命令、通用 Skill 与 AGENTS.md 段落必须同源、不走样。"""

import pytest

from no_ai_fluff import cli
from no_ai_fluff.delivery_note import NOTE_EN, NOTE_ZH, RULES_EN, RULES_ZH, host_note_en, host_note_zh
from no_ai_fluff.en import cli as en_cli
from no_ai_fluff.en.install import template as en_cc_template
from no_ai_fluff.en.mcp_server import EnMCPServer
from no_ai_fluff.install import HOSTS, generic_template, install_generic
from no_ai_fluff.install import template as cc_template
from no_ai_fluff.mcp_server import MCPServer
from no_ai_fluff.pipeline import Pipeline, PipelineConfig


def test_claude_code_skills_quote_the_same_note():
    assert NOTE_ZH in cc_template("skill.md") and RULES_ZH in cc_template("skill.md")
    assert NOTE_EN in en_cc_template("skill.md") and RULES_EN in en_cc_template("skill.md")
    # 七条规则：前两条是护栏，后五条对应“说人话”七原则（语法完整、大白话、不造代号、不用空洞大词、
    # 不省中间步骤、尽量简洁、写清主体和客体）
    assert len(RULES_ZH.splitlines()) == len(RULES_EN.splitlines()) == 7
    for word in ("语法完整", "大白话", "不发明晦涩代号", "空洞大词", "中间步骤", "尽量简洁", "主体和客体"):
        assert word in RULES_ZH, word
    for word in ("grammatically complete", "plain words", "obscure labels", "hollow big words", "steps needed",
                 "as short as", "who does what"):
        assert word in RULES_EN, word


@pytest.mark.parametrize("lang,note,rules", [("zh", NOTE_ZH, RULES_ZH), ("en", NOTE_EN, RULES_EN)])
def test_generic_templates_are_filled_from_delivery_note(lang, note, rules):
    for name in ("SKILL.md", "agents_block.md"):
        text = generic_template(name, lang)
        assert "{{" not in text and note in text and rules in text
    skill = generic_template("SKILL.md", lang)
    assert skill.startswith("---\nname: no-ai-fluff")  # Agent Skills 格式：frontmatter 带 name / description
    assert "prepare_rtt_draft" in skill and "finalize_patched_delivery" in skill
    assert "subagent_type" not in skill  # 不依赖 Claude Code 的子智能体


def test_host_note_carries_note_and_rules():
    assert NOTE_ZH in host_note_zh() and all(l in host_note_zh() for l in RULES_ZH.splitlines())
    assert NOTE_EN in host_note_en() and all(l in host_note_en() for l in RULES_EN.splitlines())
    assert "不要写进交付物" in host_note_zh() and "keep it out of the deliverable" in host_note_en()
    # Claude Code 的审校子智能体也会看到这段：它只交回定稿，提示由主会话说，避免重复
    assert "子智能体" in host_note_zh() and "不要转述本说明" in host_note_zh()
    assert "subagent" in host_note_en() and "do not relay these notes" in host_note_en()


def test_mcp_finalize_appends_host_note_after_final_text(fake_translator, tmp_path):
    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path / "d"), audit_rules="v8"),
                    translator_factory=lambda c: fake_translator(lambda s: s))
    srv = MCPServer(pipe)
    did = srv.call_tool("prepare_rtt_draft", {"text": "调用 `foo()` 的开销很大。"})["structuredContent"]["draft_id"]
    fin = srv.call_tool("finalize_patched_delivery", {"draft_id": did, "patches": []})
    text = fin["content"][0]["text"]
    final, _, rest = text.partition("\n\n---\n")
    final = final.rstrip("\n")
    assert final == "调用 `foo()` 的开销很大。"  # 定稿本身不含提示
    assert NOTE_ZH not in final and NOTE_ZH in rest
    assert fin["structuredContent"]["final_text"] == final and fin["structuredContent"]["host_note"] == host_note_zh()
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert "整段重写" in init["result"]["instructions"]


def test_en_mcp_finalize_appends_host_note(tmp_path):
    from tests.test_en import _pipe

    srv = EnMCPServer(_pipe(tmp_path))
    did = srv.call_tool("prepare_rtt_draft", {"text": "We leverage synergy across the whole team every week."})[
        "structuredContent"]["draft_id"]
    fin = srv.call_tool("finalize_patched_delivery", {"draft_id": did, "patches": []})
    final, _, rest = fin["content"][0]["text"].partition("\n\n---\n")
    assert NOTE_EN not in final and NOTE_EN in rest
    assert fin["structuredContent"]["host_note"] == host_note_en()


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_install_generic_each_host(host, tmp_path):
    proj_skills, user_skills, proj_md, _, user_only = HOSTS[host]
    rep = install_generic(host, "project", tmp_path)
    skills = user_skills if user_only else proj_skills
    skill = tmp_path / skills / "no-ai-fluff" / "SKILL.md"
    assert skill.read_text("utf-8") == generic_template("SKILL.md")
    if proj_md and not user_only:
        assert "<!-- no-ai-fluff:start -->" in (tmp_path / proj_md).read_text("utf-8")
    assert any("no-ai-fluff mcp" in s or "命令 no-ai-fluff" in s for s in rep.next_steps)
    assert not install_generic(host, "project", tmp_path).written  # 幂等


def test_agents_md_merge_keeps_user_text_and_respects_edits(tmp_path):
    md = tmp_path / "AGENTS.md"
    md.write_text("# 项目约定\n\n用 4 空格缩进。\n", "utf-8")
    install_generic("codex", "project", tmp_path)
    install_generic("codex", "project", tmp_path, lang="en")
    text = md.read_text("utf-8")
    assert text.startswith("# 项目约定\n\n用 4 空格缩进。\n\n<!-- no-ai-fluff:start -->")
    assert text.count("<!-- no-ai-fluff:end -->") == 1 and "<!-- no-ai-fluff-en:start -->" in text
    md.write_text(text.replace("守住七条", "守住我改过的七条"), "utf-8")  # 用户改了这段
    rep = install_generic("codex", "project", tmp_path)
    assert md in rep.skipped and "我改过的" in md.read_text("utf-8")
    rep = install_generic("codex", "project", tmp_path, force=True)
    after = md.read_text("utf-8")
    assert md in rep.written and "我改过的" not in after and after.startswith("# 项目约定")
    assert after.count("<!-- no-ai-fluff:start -->") == 1 and "<!-- no-ai-fluff-en:start -->" in after


def test_cli_install_other_hosts(tmp_path, capsys):
    assert cli.main(["install", "qoder", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".qoder/skills/no-ai-fluff/SKILL.md").exists()
    assert en_cli.main(["install", "codex", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".agents/skills/no-ai-fluff-en/SKILL.md").read_text("utf-8") == generic_template("SKILL.md", "en")
    assert "codex mcp add no-ai-fluff-en -- no-ai-fluff-en mcp" in capsys.readouterr().out
