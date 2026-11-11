"""Build promo/en/data.js for the English promo from the saved English results (nothing is copied by hand).

    python promo/build_en.py

Reads:
    results/en_probe/    translator comparison (EN -> JA -> EN), round-trip drift examples
    results/en-r1/, en-r2/   tuning rounds on the tune set (en2 = shipped rules); en-r2 also holds the showcase paragraph
    results/en-test/     the locked 12-document test set: rows, metrics, JEV summary
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import diff, jl, mark, tokenize  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "results"
OUT = Path(__file__).resolve().parent / "en" / "data.js"
sys.path.insert(0, str(ROOT / "src"))
from no_ai_fluff.en.radar import surface  # noqa: E402


def rows(run: str) -> dict:
    return {r["id"]: r for r in jl(RES / run / "rows.jsonl")}


def patch(run: str, doc: str, find_prefix: str) -> tuple[str, str]:
    r = rows(run)[doc]
    p = next(p for p in r["patches"] if p["applied"] and p["find"].startswith(find_prefix))
    assert p["find"] in r["original"] and p["replace"] in r["final"], (run, doc, find_prefix)
    return p["find"], p["replace"]


def strip_md(s: str) -> str:
    return s.replace("**", "")


def showcase() -> dict:
    r = rows("en-r2")["en-tune-probe-showcase-001"]
    orig, final = r["original"], r["final"]
    jargon = ["drove alignment", "holistic", "consolidation", "unlocking", "step-function", "posture", "Leveraging",
              "de-risks", "runway", "double-click", "Net-net", "north-star-aligned", "meaningfully", "moves the needle"]
    for w in jargon:
        assert w in orig, w
    d = diff(orig, final)
    before = mark(tokenize(orig), jargon, "mark")
    left = [w for w in jargon if w.lower() in final.lower()]
    sb, sa = surface(orig), surface(final)
    for keep in ("`auth-gateway`", "p99 moved from 120ms to 45ms", "2 regions"):
        assert keep in orig and keep in final, keep
    return {"before": before, "after": d["after"], "words": len(jargon), "words_left": len(left),
            "wps": [round(sb["words_per_sentence"], 1), round(sa["words_per_sentence"], 1)],
            "applied": r["applied"], "rejected": r["rejected"]}


def case(run: str, doc: str, prefix: str, src: str) -> dict:
    f, rpl = patch(run, doc, prefix)
    d = diff(strip_md(f), strip_md(rpl))
    return {"before": d["before"], "after": d["after"], "src": src}


def pairs() -> list[list[str]]:
    """Jargon -> plain wording, each pair checked against a real applied patch."""
    spec = [
        ("en-r2", "en-tune-probe-showcase-001", "drove alignment", "aligned work", "tune · showcase"),
        ("en-test", "en-test-teaching-01", "surfaces conceptually related results", "returns conceptually related results", "test · teaching"),
        ("en-test", "en-test-discussion-01", "won't meaningfully change", "won't change your p99 much", "test · discussion"),
        ("en-test", "en-test-discussion-02", "imposes a steep tax on it", "takes a large share of it", "test · discussion"),
        ("en-test", "en-test-writing-01", "more meaningful opportunity", "chance", "test · writing"),
        ("en-r2", "en-tune-teaching-01", "leverage", "use", "tune · teaching"),
    ]
    out = []
    for run, doc, a, b, src in spec:
        r = rows(run)[doc]
        ok = [p for p in r["patches"] if p["applied"] and a in p["find"] and b in p["replace"]]
        assert ok, (doc, a, b)
        if a == "leverage":  # the patch replaced just the verb; show it with its object, which is in both texts
            assert "often leverage multiprocessing" in r["original"] and "often use multiprocessing" in r["final"]
            a, b = "leverage multiprocessing", "use multiprocessing"
        out.append([a, b, src])
    return out


def two_col() -> dict:
    """Chapter 5 side by side: the most rewritten long paragraph of en-test-discussion-02 (Kubernetes on day one)."""
    import difflib
    r = rows("en-test")["en-test-discussion-02"]
    pairs = [(o, f) for o, f in zip(r["original"].split("\n"), r["final"].split("\n")) if o != f and len(o.split()) >= 40]
    o, f = min(pairs, key=lambda p: difflib.SequenceMatcher(None, p[0].split(), p[1].split()).ratio())
    assert o.startswith("That said, there are legitimate exceptions") and f.startswith("That said, there are legitimate exceptions.")
    d = diff(o, f)
    return {"orig": d["before"], "ours": d["after"], "src": "assistant answer · Kubernetes on day one · test set"}


def main() -> None:
    probe = json.loads((RES / "en_probe" / "rtt_report.json").read_text("utf-8"))
    prt = jl(RES / "en_probe" / "rtt.jsonl")

    def rtt_text(model, doc):
        return next(x["rtt"] for x in prt if x["model"] == model and x["id"] == doc)
    drift = [
        ["amortized O(1)", "average O(1)", "hy-mt2"],
        ["flake-rate-driven CI", "flake-rate-driven and CI", "hy-mt2"],
        ["## Fix", "## After the cache change, the checkout tests started failing.", "gemma-3-4b"],
    ]
    assert "average O(1)" in rtt_text("tencent/hy-mt2-30b-a3b", "en-teaching-001")
    assert "flake-rate-driven and CI" in rtt_text("tencent/hy-mt2-30b-a3b", "en-coding-001")
    assert "## After the cache change, the checkout tests started failing." in rtt_text("google/gemma-3-4b-it", "en-coding-001")

    m1 = json.loads((RES / "en-r1" / "metrics.json").read_text("utf-8"))["summary"]
    m2 = json.loads((RES / "en-r2" / "metrics.json").read_text("utf-8"))["summary"]
    mt = json.loads((RES / "en-test" / "metrics.json").read_text("utf-8"))["summary"]
    jv = json.loads((RES / "en-test" / "jev_summary.json").read_text("utf-8"))
    test_rows = list(rows("en-test").values())
    assert mt["docs_numbers_changed"] == 0 and mt["final_fallbacks"] == 0
    corpus = jl(ROOT / "benchmarks" / "corpus" / "en_test.jsonl")
    repos = [re.sub(r"^https://github.com/([^/]+/[^/]+)/pull/\d+$", r"\1", c["source"]) for c in corpus if c.get("source")]
    answers = {}
    for c in corpus:
        if not c.get("source"):
            answers[c["scenario"]] = answers.get(c["scenario"], 0) + 1
    rejected = [p for r in test_rows for p in r["patches"] if not p["applied"]]
    assert len(rejected) == 1 and "duplicates protected content" in rejected[0]["reject_reason"]

    o = jv["overall"]
    data = {
        "showcase": showcase(),
        "translators": {k: {kk: probe[k][kk] for kk in ("latency_p50_s", "seg_fallback_pct", "nominal_per100", "buzz_per1k")}
                        for k in probe if k != "original"},
        "orig_nominal": probe["original"]["nominal_per100"], "orig_buzz": probe["original"]["buzz_per1k"],
        "drift": drift,
        "tuning": {
            "wps": [m1["before"]["words_per_sentence"], m1["after"]["words_per_sentence"], m2["after"]["words_per_sentence"]],
            "long": [m1["before"]["long_sentence_pct"], m1["after"]["long_sentence_pct"], m2["after"]["long_sentence_pct"]],
            "jargon": [m1["before"]["jargon_per_1kw"], m1["after"]["jargon_per_1kw"], m2["after"]["jargon_per_1kw"]],
            "n": m2["n"],
        },
        "test": {"n": mt["n"], "repos": repos, "answers": answers,
                 "long": [mt["before"]["long_sentence_pct"], mt["after"]["long_sentence_pct"]],
                 "wps": [mt["before"]["words_per_sentence"], mt["after"]["words_per_sentence"]],
                 "applied": mt["patches_applied"], "rejected": mt["patches_rejected"], "numbers_changed": mt["docs_numbers_changed"]},
        "jev": {"win": o["win_vs_orig"], "pref_share": o["docs_ours_preferred"] / o["n"],
                "read": [o["read_orig"], o["read_ours"]], "ai": [o["ai_tone_orig"], o["ai_tone_ours"]],
                "pr_win": jv["by_source"]["real Claude Code PRs"]["win_vs_orig"],
                "pr_share": jv["by_source"]["real Claude Code PRs"]["docs_ours_preferred"] / jv["by_source"]["real Claude Code PRs"]["n"],
                "by": {k: v["win_vs_orig"] for k, v in jv["by_scenario"].items()}},
        "cases": [
            case("en-test", "en-test-coding-01", "Callers of `getResolvedAddress`", "real PR · apache/hadoop · test set"),
            case("en-test", "en-test-teaching-02", "Modern variants like CUBIC", "assistant answer · TCP congestion control · test set"),
            case("en-test", "en-test-discussion-02", "In the early phase, your scarcest", "assistant answer · Kubernetes on day one · test set")
            if any(p["find"].startswith("In the early phase, your scarcest") for p in rows("en-test")["en-test-discussion-02"]["patches"])
            else case("en-test", "en-test-discussion-01", "**Rewrites carry substantial", "assistant answer · Python to Rust? · test set"),
        ],
        "pairs": pairs(),
        "two_col": two_col(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("// generated by promo/build_en.py from benchmarks/results; do not edit by hand\n"
                   f"window.PROMO_DATA = {json.dumps(data, ensure_ascii=False, indent=1)};\n", "utf-8")
    print(f"wrote {OUT}  win {o['win_vs_orig']:.3f}  share {data['jev']['pref_share']:.3f}  PR {data['jev']['pr_win']:.3f}  "
          f"showcase jargon {data['showcase']['words']}->{data['showcase']['words_left']}")


if __name__ == "__main__":
    main()
