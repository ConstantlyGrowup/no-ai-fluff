"""English second generation (en5 = v2): telegraphic-chain radar, domain-triage handshake, fallback to the first
generation (en2 = v1) prompt, no runtime jargon list."""
import io
import json

from no_ai_fluff.en.mcp_server import EnMCPServer
from no_ai_fluff.en.pipeline import EnPipeline, EnPipelineConfig
from no_ai_fluff.en.radar import scan, surface, telegraph_sentences, telegraph_stats
from no_ai_fluff.en.rules import AUDIT_EN2, AUDIT_EN5_DENSE, DEFAULT_RULES, build_audit_prompt, effective_rules
from no_ai_fluff.telegraph_router import DOMAIN_SPEC_EN, TelegraphRouter

CHAIN = ("Post-merge, stale-read windows the old flow never exposed now surface under concurrent checkout load, "
         "which the integration suite, running 3 parallel workers, reliably reproduces.")
PLAIN = "We moved the cache invalidation ahead of the database commit so readers never see stale cart data."
JARGON_LINE = "This release will unlock a seamless, robust workflow for every team."
TEXT = f"{CHAIN}\n\n{PLAIN}\n\n{JARGON_LINE}"


def make(fake_translator, tmp_path, rules="en5", router=None, calls=None):
    calls = calls if calls is not None else []

    def factory(cfg):
        calls.append(1)
        return fake_translator()

    cfg = EnPipelineConfig(store_dir=str(tmp_path / "d"), telemetry=False, audit_rules=rules)
    return EnPipeline(cfg, translator_factory=factory, router=router), calls


def test_detector_is_statistical():
    assert telegraph_stats(CHAIN)
    assert telegraph_stats(PLAIN) is None
    assert telegraph_sentences([PLAIN, CHAIN]) == [(1, CHAIN)]
    assert scan([CHAIN]).counts["telegraph"] == 0 and scan([CHAIN], dense=True).counts["telegraph"] == 1
    assert scan([JARGON_LINE]).counts["jargon"] >= 3 and scan([JARGON_LINE], jargon=False).counts["jargon"] == 0
    assert surface(JARGON_LINE)["jargon_per_1kw"] > 0  # benchmark metric keeps the word list


def test_gate_and_default():
    assert DEFAULT_RULES == "en5"
    for gen2, gen1 in (("en5", "en2"), ("v2", "v1")):
        assert effective_rules(gen2, '- [long sentence] "x"') == gen1
        assert effective_rules(gen2, '- [telegraphic sentence chain] "x"') == gen2
        assert build_audit_prompt("o", "r", '- [noun stack] "x"', gen2) == build_audit_prompt("o", "r", '- [noun stack] "x"', "en2")
        assert build_audit_prompt("o", "r", '- [telegraphic sentence chain] "x"', gen2).startswith(AUDIT_EN5_DENSE)
    assert "12. [telegraph-stack]" in AUDIT_EN5_DENSE and "12." not in AUDIT_EN2


def test_handshake_dense_without_rerunning_rtt(fake_translator, tmp_path):
    pipe, calls = make(fake_translator, tmp_path)
    prep = pipe.prepare(TEXT)
    assert prep.needs_domain_triage and prep.triage_sentences == [CHAIN]
    assert '"technical"' in prep.triage_prompt and "technical or engineering context" in prep.triage_prompt
    done = pipe.complete_prepare(prep.draft_id, True)
    assert len(calls) == 1 and done.audit_prompt.startswith(AUDIT_EN5_DENSE)
    assert "[jargon / hype word]" not in done.radar_hints  # en5: no runtime word list
    again = pipe.prepare(TEXT)
    assert not again.needs_domain_triage and again.audit_prompt.startswith(AUDIT_EN5_DENSE)


def test_non_technical_falls_back_to_en2_and_en2_keeps_word_list(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    prep = pipe.prepare(TEXT)
    done = pipe.complete_prepare(prep.draft_id, [{"id": 1, "technical": False}])
    assert done.audit_prompt.startswith(AUDIT_EN2) and "[telegraphic sentence chain]" not in done.radar_hints
    assert "[jargon / hype word]" not in done.radar_hints
    first, _ = make(fake_translator, tmp_path / "v1", rules="v1")  # first generation: single channel, word list on
    p1 = first.prepare(TEXT)
    assert not p1.needs_domain_triage and p1.audit_prompt.startswith(AUDIT_EN2)
    assert "[jargon / hype word]" in p1.radar_hints and "[telegraphic sentence chain]" not in p1.radar_hints


def test_run_with_reviewer_and_injected_router(fake_translator, tmp_path):
    pipe, calls = make(fake_translator, tmp_path)
    seen = []

    def reviewer(prompt):
        seen.append(prompt)
        return '[{"id": 1, "technical": true}]' if '"technical"' in prompt else "[]"

    prep, fin = pipe.run(TEXT, reviewer=reviewer)
    assert len(seen) == 2 and seen[1].startswith(AUDIT_EN5_DENSE) and fin.final_text == TEXT and len(calls) == 1
    asked = []
    router = TelegraphRouter(lambda p: (asked.append(p) or json.dumps([{"id": 1, "technical": False}]), {}),
                             tmp_path / "r", spec=DOMAIN_SPEC_EN)
    pipe2, _ = make(fake_translator, tmp_path / "b", router=router)
    p2 = pipe2.prepare(TEXT)
    assert not p2.needs_domain_triage and len(asked) == 1 and p2.audit_prompt.startswith(AUDIT_EN2)


def test_en_mcp_handshake(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    srv = EnMCPServer(pipe)

    def call(args):
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "prepare_rtt_draft", "arguments": args}}
        out = io.StringIO()
        srv.serve(io.StringIO(json.dumps(msg) + "\n"), out)
        return json.loads(out.getvalue())["result"]

    r1 = call({"text": TEXT})
    assert r1["structuredContent"]["status"] == "needs_domain_triage"
    assert "One more step" in r1["content"][0]["text"]
    r2 = call({"draft_id": r1["structuredContent"]["draft_id"], "domain_triage": "false"})
    assert not r2.get("isError") and "finalize_patched_delivery" in r2["content"][0]["text"]
    assert call({"draft_id": "x"})["isError"]


def test_en5_tables_and_aliases():
    from no_ai_fluff.en.rules import NO_JARGON_RADAR_RULES, ROUTED_RULES, RULES, GATED_RULES, resolve_rules

    assert "still type 1" in AUDIT_EN5_DENSE and "can stay; a chain" not in AUDIT_EN5_DENSE
    assert set(RULES) == {"v1", "en2", "v2", "en5"} and RULES["v2"] == AUDIT_EN5_DENSE and RULES["v1"] == AUDIT_EN2
    assert set(ROUTED_RULES) == set(NO_JARGON_RADAR_RULES) == set(GATED_RULES) == {"en5", "v2"}
    assert resolve_rules("v2") == "en5" and resolve_rules("en2") == "en2"
