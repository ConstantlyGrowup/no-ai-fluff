"""English edition: masker, round-trip checks, radar, cleanup, pipeline, MCP server, CLI and installer."""

import json

from no_ai_fluff.en import cli as en_cli
from no_ai_fluff.en.cleanup import clean_punctuation_en
from no_ai_fluff.en.install import install_claude_code_en
from no_ai_fluff.en.masker import EnMasker
from no_ai_fluff.en.mcp_server import EnMCPServer
from no_ai_fluff.en.pipeline import EnPipeline, EnPipelineConfig
from no_ai_fluff.en.radar import scan, surface
from no_ai_fluff.en.rtt import EnRoundTripTranslator, build_prompt_en, split_long_en, validate_en
from no_ai_fluff.masker import parse
from no_ai_fluff.rtt import default_provider


def frozen(text):
    doc = parse(text, "markdown", masker_cls=EnMasker)
    return doc.placeholders


def test_masker_freezes_code_not_prose():
    ph = frozen(r"Call `invalidateOnWrite()` in CartService.java, see https://x.io/a and $\eta$; "
                "use utf-8 and v2.1.0 with p99_ms.")
    assert "`invalidateOnWrite()`" in ph and "CartService.java" in ph and "https://x.io/a" in ph
    assert r"$\eta$" in ph and "utf-8" in ph and "v2.1.0" in ph and "p99_ms" in ph


def test_masker_leaves_english_prose_alone():
    ph = frozen("This sprint, the Platform Team drove a north-star-aligned, flake-rate-driven win, e.g. for Redis.")
    assert not any(p in ph for p in ("north-star-aligned", "flake-rate-driven", "This sprint", "Platform", "Redis", "e.g"))


def test_masker_word_pairs_are_prose_but_paths_are_not():
    ph = frozen("Check emails/messages and Norway/Mexico, then open src/lib/x.py and a/b/c.")
    assert "emails/messages" not in ph and "Norway/Mexico" not in ph
    assert "src/lib/x.py" in ph


def test_cleanup_doubled_article():
    assert clean_punctuation_en("It stems from the a change.")[0] == "It stems from a change."
    assert clean_punctuation_en("the theory of a anchor")[0] == "the theory of a anchor"


def test_masker_has_no_soft_placeholders():
    doc = parse("The API returns JSON for the SLA report.", "text", masker_cls=EnMasker)
    assert doc.soft == []


def test_validate_and_prompt():
    assert validate_en("The value {{P0}} is large.", "The value {{P0}} is big.", [0]) == ""
    assert "not English" in validate_en("The value {{P0}} is large.", "値 {{P0}} は大きい。", [0])
    assert "placeholder" in validate_en("The value {{P0}} is large.", "The value is big.", [0])
    assert build_prompt_en("x", "ja").startswith("Translate the following segment into Japanese")
    assert "Referring to the information above" in build_prompt_en("x", "en", context="prev")


def test_split_long_en_keeps_text():
    text = "First sentence is here. Second one, with a clause, follows! Third? " * 20
    parts = split_long_en(text, 120)
    assert "".join(parts) == text and all(len(p) <= 120 for p in parts)


def test_radar_and_surface():
    t = ("We leverage a robust, seamless pipeline. The write-path invalidation ordering change regressed. "
         "Goal: 7:00 wake → sunlight.")
    rep = scan([t])
    assert rep.counts["jargon"] >= 3 and rep.counts["noun_stack"] >= 1 and rep.counts["symbol_for_word"] >= 1
    s = surface(t + " `code here` is ignored.")
    assert s["words"] > 10 and s["jargon_per_1kw"] > 0


def test_cleanup_respects_reference():
    assert clean_punctuation_en("It works.. Fine , ok.")[0] == "It works. Fine, ok."
    assert clean_punctuation_en("Wait.. what", "Wait.. what")[0] == "Wait.. what"
    assert clean_punctuation_en("And so...")[0] == "And so..."


def _pipe(tmp_path, fn=lambda s: s):
    from tests.conftest import FakeTranslator  # noqa: PLC0415

    cfg = EnPipelineConfig(store_dir=str(tmp_path / "d"), telemetry=False)
    return EnPipeline(cfg, translator_factory=lambda c: FakeTranslator(fn))


def test_pipeline_patches_original(tmp_path):
    text = "We leveraged `fooBar()` to unlock a robust step-function improvement: p99 went from 120ms to 45ms."
    pipe = _pipe(tmp_path, lambda s: s.replace("leveraged", "used"))
    prep = pipe.prepare(text, question="what changed?")
    assert "[Original]" in prep.audit_prompt and "used" in prep.rtt_draft and "[User question]" in prep.audit_prompt
    patches = [
        {"find": "We leveraged `fooBar()` to unlock a robust step-function improvement",
         "replace": "We used `fooBar()` to cut latency sharply", "type": "jargon", "reason": "metaphor"},
        {"find": "p99 went", "replace": "p99 dropped", "type": "made-up", "reason": "x"},
    ]
    fin = pipe.finalize(prep.draft_id, patches)
    assert fin.final_text.startswith("We used `fooBar()` to cut latency sharply: p99 went from 120ms to 45ms.")
    assert fin.applied == 1 and fin.rejected == 1
    assert fin.patches[1]["reject_reason"] == "patch has no valid type"


def test_pipeline_rejects_dropping_code_in_english(tmp_path):
    pipe = _pipe(tmp_path)
    prep = pipe.prepare("Run `make test` before you push the branch to origin.")
    fin = pipe.finalize(prep.draft_id, [{"find": "Run `make test` before", "replace": "Test before", "type": "redundant"}])
    assert fin.applied == 0 and "protected content" in fin.patches[0]["reject_reason"]


def test_config_provider_order(monkeypatch):
    assert default_provider() == "openrouter"  # no key at all: OpenRouter, so the error names its key
    monkeypatch.setenv("TENCENTCLOUD_API_KEY", "t")
    assert default_provider() == "tencent"
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert default_provider() == "openrouter"
    cfg = EnPipelineConfig.from_env()
    assert (cfg.provider, cfg.model, cfg.bridge, cfg.source_lang, cfg.audit_rules) == (
        "openrouter", "tencent/hy-mt2-30b-a3b", "ja", "en", "en2")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert EnPipelineConfig.from_env().model == "hy-mt2-pro"


def test_translator_defaults():
    tr = EnRoundTripTranslator(client=None, model="m")
    assert (tr.source, tr.bridge) == ("en", "ja")


def test_mcp_server(tmp_path):
    srv = EnMCPServer(_pipe(tmp_path))
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "no-ai-fluff-en"
    tools = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["prepare_rtt_draft", "finalize_patched_delivery"]
    r = srv.call_tool("prepare_rtt_draft", {"text": "We leverage synergy across the whole team every week."})
    did = r["structuredContent"]["draft_id"]
    assert "finalize_patched_delivery" in r["content"][0]["text"]
    f = srv.call_tool("finalize_patched_delivery", {"draft_id": did, "patches": "[]"})
    assert f["content"][0]["text"].startswith("We leverage synergy") and "Patches: 0 applied" in f["content"][0]["text"]
    assert "not found" in srv.call_tool("finalize_patched_delivery", {"draft_id": did, "patches": []})["content"][0]["text"]


def test_cli_without_key(tmp_path, capsys):
    f = tmp_path / "a.md"
    f.write_text("We leverage synergy to unlock value.", "utf-8")
    assert en_cli.main(["prepare", str(f)]) == 0
    cap = capsys.readouterr()
    prep = json.loads(cap.out)
    assert prep["fallback"] and "round trip skipped" in cap.err
    patches = '[{"find": "We leverage synergy to unlock value.", "replace": "We work together.", "type": "jargon"}]'
    assert en_cli.main(["finalize", prep["draft_id"], "--patches", patches]) == 0
    assert capsys.readouterr().out.strip() == "We work together."


def test_install(tmp_path):
    rep = install_claude_code_en("project", tmp_path)
    assert (tmp_path / ".claude" / "agents" / "no-ai-fluff-en.md").exists()
    assert (tmp_path / ".claude" / "skills" / "no-ai-fluff-en" / "SKILL.md").exists()
    cfg = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
    assert cfg["mcpServers"]["no-ai-fluff-en"] == {"command": "no-ai-fluff-en", "args": ["mcp"]}
    assert any("OPENROUTER_API_KEY" in s for s in rep.next_steps)
