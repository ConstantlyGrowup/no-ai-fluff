"""no-ai-fluff-en evaluation: tuning on the tune set, then one final JEV + objective comparison on the locked test set.

    python benchmarks/en_eval.py run     --set tune --run en-r5 --rules en5   # pipeline + DeepSeek host, writes rows.jsonl
    python benchmarks/en_eval.py metrics --run en-r1                          # objective metrics, writes metrics.json
    python benchmarks/en_eval.py judge   --run en-test                        # JEV (typesafe/jev-1.14, en_text rubric)
    python benchmarks/en_eval.py judge   --run en-r5 --ref en-r2              # also head-to-head against another run

en3 / en4 send telegraphic-chain hints through the domain triage first; in benchmarks that triage is a DeepSeek
(effort=low) call injected here, cached under benchmarks/.cache/telegraph_router (same as the Chinese runs).

Translation goes through OpenRouter (hy-mt2), the host only through the official DeepSeek API, the judge only through
OpenRouter's JEV decisions endpoint — as in CLAUDE.md. Every raw response is cached under benchmarks/.cache/ and the
host's raw output is also stored in rows.jsonl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402
from no_ai_fluff.en.pipeline import EnPipeline, EnPipelineConfig  # noqa: E402
from no_ai_fluff.en.radar import surface  # noqa: E402
from no_ai_fluff.rtt import ChatClient  # noqa: E402

CORPUS = {"tune": ra.BENCH / "corpus" / "en_tune.jsonl", "test": ra.BENCH / "corpus" / "en_test.jsonl"}
LOCK = ra.BENCH / "splits" / "en_test.lock.json"
import re  # noqa: E402

_NUM = re.compile(r"\d+(?:[.,:]\d+)*")


def load_set(name: str) -> list[dict]:
    rows = ra.load_jsonl(CORPUS[name])
    if name == "test":
        lock = json.loads(LOCK.read_text("utf-8"))
        got = {r["id"]: hashlib.sha256(r["text"].encode("utf-8")).hexdigest() for r in rows}
        bad = [i for i in lock["ids"] if got.get(i) != lock["sha256"][i]]
        if bad or len(rows) != len(lock["ids"]):
            sys.exit(f"en_test does not match its lock: {bad or 'different count'}")
    return rows


def nums(t: str) -> Counter:
    return Counter(_NUM.findall(t))


def stage_run(args) -> None:
    d = ra.BENCH / "results" / args.run
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"set": args.set, "rules": args.rules, "model": args.model, "bridge": args.bridge,
                                            "host": "deepseek-flash", "effort": "high", "rtt_context": args.rtt_context,
                                            "date": time.strftime("%Y-%m-%d")}, indent=2), "utf-8")
    host = ChatClient(provider="deepseek", timeout=900, cache_dir=ra.CACHE / "deepseek")
    host.is_local = True
    from no_ai_fluff.en.rules import ROUTED_RULES

    router = ra.router(args.rules) if args.rules in ROUTED_RULES else None

    def one(s):
        cfg = EnPipelineConfig(provider="openrouter", model=args.model, bridge=args.bridge, audit_rules=args.rules,
                               cache_dir=str(ra.CACHE / "openrouter"), store_dir=str(ra.CACHE / "drafts-en"),
                               chat_timeout_s=180, doc_timeout_s=180, draft_ttl_s=365 * 86400, telemetry=False,
                               rtt_context_chars=args.rtt_context)
        pipe = EnPipeline(cfg, router=router)
        log = {}

        def reviewer(prompt: str):
            raw, usage, lat = ra.chat_json(host, "deepseek-flash", prompt, max_tokens=64000, effort="high")
            log.update(raw=raw, usage=usage, latency_s=round(lat, 2))
            return raw

        prep, fin = pipe.run(s["text"], reviewer=reviewer, exit="file", question=s.get("question"))
        return {"id": s["id"], "scenario": s["scenario"], "origin": s.get("origin"), "question": s.get("question"),
                "original": prep.original, "rtt": prep.rtt_draft, "rtt_fallback": prep.fallback_reason,
                "rtt_stats": prep.stats, "radar": prep.radar, "final": fin.final_text, "applied": fin.applied,
                "rules_used": "dense" if prep.stats.get("domain_dense") else "base",
                "rejected": fin.rejected, "patches": fin.patches, "fallback_reason": fin.fallback_reason,
                "host_raw": log.get("raw"), "host_usage": log.get("usage"), "host_latency_s": log.get("latency_s")}

    samples = load_set(args.set)
    if args.only:
        samples = [s for s in samples if s["id"] in args.only or s["scenario"] in args.only]
    with ThreadPoolExecutor(args.workers) as ex:
        rows = list(ex.map(one, samples))
    with open(d / "rows.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in rows:
        print(f"{r['id']:28} applied {r['applied']:2} rejected {r['rejected']} {r['fallback_reason'] or ''}")
    stage_metrics(args)


def stage_metrics(args) -> None:
    d = ra.BENCH / "results" / args.run
    rows = ra.load_jsonl(d / "rows.jsonl")
    keys = list(surface("x").keys())
    per = []
    for r in rows:
        b, a = surface(r["original"]), surface(r["final"])
        changed = sum(1 for x, y in zip(r["original"].split("\n"), r["final"].split("\n")) if x != y)
        per.append({
            "id": r["id"], "scenario": r["scenario"], "applied": r["applied"], "rejected": r["rejected"],
            "final_fallback": bool(r["fallback_reason"] and "final check" in r["fallback_reason"]),
            "numbers_changed": nums(r["original"]) != nums(r["final"]),
            "numbers_lost": dict(nums(r["original"]) - nums(r["final"])),
            "numbers_added": dict(nums(r["final"]) - nums(r["original"])),
            "lines_changed": changed,
            "before": b, "after": a,
        })
    summ = {
        "n": len(per),
        "patches_applied": sum(p["applied"] for p in per), "patches_rejected": sum(p["rejected"] for p in per),
        "docs_untouched": sum(1 for r in rows if r["final"] == r["original"]),
        "docs_numbers_changed": sum(1 for p in per if p["numbers_changed"]),
        "final_fallbacks": sum(1 for p in per if p["final_fallback"]),
        "before": {k: round(statistics.mean(p["before"][k] for p in per), 2) for k in keys},
        "after": {k: round(statistics.mean(p["after"][k] for p in per), 2) for k in keys},
    }
    (d / "metrics.json").write_text(json.dumps({"summary": summ, "per_doc": per}, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summ, indent=1))
    for p in per:
        if p["numbers_changed"]:
            print("  numbers", p["id"], "lost", p["numbers_lost"], "added", p["numbers_added"])


def stage_judge(args) -> None:
    import jev_judge as jj

    d = ra.BENCH / "results" / args.run
    rows = ra.load_jsonl(d / "rows.jsonl")
    lang = "en_text"
    ref = {r["id"]: r for r in ra.load_jsonl(ra.BENCH / "results" / args.ref / "rows.jsonl")} if args.ref else None

    def one(r):
        rec = {"id": r["id"], "scenario": r["scenario"], "origin": r["origin"]}
        rec["orig_read"] = jj.readability(r["original"], lang)
        rec["ours_read"] = jj.readability(r["final"], lang)
        rec["ours_fid"] = jj.fidelity(r["original"], r["final"], lang)
        rec["p_ours_vs_orig"] = jj.prefer(r["final"], r["original"], lang)
        if ref:
            other = ref[r["id"]]["final"]
            rec["p_ours_vs_ref"] = 0.5 if other == r["final"] else jj.prefer(r["final"], other, lang)
        return rec

    with ThreadPoolExecutor(args.workers) as ex:
        out = list(ex.map(one, rows))
    (d / (f"jev_vs_{args.ref}.jsonl" if args.ref else "jev.jsonl")).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), "utf-8")

    def m(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    def agg(rs):
        return {"n": len(rs),
                "win_vs_orig": m([x["p_ours_vs_orig"] for x in rs]),
                "docs_ours_preferred": sum(1 for x in rs if (x["p_ours_vs_orig"] or 0) > 0.5),
                "read_orig": m([x["orig_read"]["read"] for x in rs]), "read_ours": m([x["ours_read"]["read"] for x in rs]),
                "ai_tone_orig": m([x["orig_read"]["ai_tone"] for x in rs]),
                "ai_tone_ours": m([x["ours_read"]["ai_tone"] for x in rs]),
                "fidelity_risk_ours": m([x["ours_fid"]["risk"] for x in rs]),
                **({"win_vs_ref": m([x.get("p_ours_vs_ref") for x in rs]),
                    "docs_beat_ref": sum(1 for x in rs if (x.get("p_ours_vs_ref") or 0.5) > 0.5),
                    "docs_lose_ref": sum(1 for x in rs if (x.get("p_ours_vs_ref") or 0.5) < 0.5)} if ref else {}),
                **{f"p_{k}": m([x["ours_fid"].get(f"p_{k}") for x in rs]) for k in jj.FIDELITY_Q[lang]}}
    by: dict[str, list] = {}
    for x in out:
        grp = "real Claude Code PRs" if "GitHub" in (x["origin"] or "") else "assistant answers"
        by.setdefault(grp, []).append(x)
    summary = {"model": jj.JEV_MODEL, "lang": lang, "style": "rubric", "ref": args.ref, "overall": agg(out),
               "by_source": {k: agg(v) for k, v in by.items()},
               "by_scenario": {s: agg([x for x in out if x["scenario"] == s]) for s in sorted({x["scenario"] for x in out})},
               "spent": dict(jj.SPENT)}
    (d / (f"jev_summary_vs_{args.ref}.json" if args.ref else "jev_summary.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, indent=1))


def main() -> None:
    ra.load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["run", "metrics", "judge"])
    ap.add_argument("--set", default="tune", choices=["tune", "test"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--rules", default="en5", choices=["v1", "en2", "v2", "en5"])
    ap.add_argument("--model", default="tencent/hy-mt2-30b-a3b")
    ap.add_argument("--bridge", default="ja")
    ap.add_argument("--rtt-context", type=int, default=300)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ref", help="judge: also compare head-to-head with this run's final texts")
    args = ap.parse_args()
    from no_ai_fluff.en.rules import resolve_rules

    args.rules = resolve_rules(args.rules)
    {"run": stage_run, "metrics": stage_metrics, "judge": stage_judge}[args.stage](args)


if __name__ == "__main__":
    main()
