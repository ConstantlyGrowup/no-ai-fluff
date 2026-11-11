"""中文版接腾讯云 Key 的可用性核验（2026-11-11）：Dev 24 篇往返翻译客观指标，hy-mt2-pro（腾讯云）对照 hy-mt2-30b（OpenRouter）。

只跑 RTT（中 → 俄 → 中，rtt_context 300，与锁定配置 v8-locked 相同），不跑宿主、不请评委。

    python benchmarks/tencent_check.py            # 结果写 results/tencent-check/
"""
from __future__ import annotations

import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402
from no_ai_fluff.config import load_dotenv  # noqa: E402
from no_ai_fluff.pipeline import Pipeline, PipelineConfig  # noqa: E402
from no_ai_fluff.radar import number_mismatch, scan_single  # noqa: E402

OUT = ra.BENCH / "results" / "tencent-check"
COMBOS = {"tencent/hy-mt2-pro": ("tencent", "hy-mt2-pro"), "openrouter/hy-mt2-30b": ("openrouter", "tencent/hy-mt2-30b-a3b")}


def dev_samples() -> list[dict]:
    ids = json.loads((ra.BENCH / "splits" / "dev.json").read_text("utf-8"))["ids"]
    rows = {r["id"]: r for r in ra.load_jsonl(ra.SAMPLES)}
    return [rows[i] for i in ids]


def run(job):
    name, s = job
    provider, model = COMBOS[name]
    cfg = PipelineConfig(provider=provider, model=model, bridge="ru", chat_timeout_s=180, doc_timeout_s=180,
                         cache_dir=str(ra.CACHE / ("openrouter" if provider == "openrouter" else "tencent")),
                         store_dir=str(ra.CACHE / "drafts"), draft_ttl_s=365 * 86400, telemetry=False,
                         audit_rules="v8", rtt_context_chars=300)
    pipe = Pipeline(cfg)
    prep = pipe.prepare(s["text"], question=s.get("prompt"))
    d = pipe.store.get(prep.draft_id)
    idx = d.translated
    orig = [d.doc.slots[i].masked for i in idx]
    back = [d.rtt_slots[i] for i in idx]
    chars = sum(len(x) for x in orig) or 1
    miss, add = number_mismatch("\n".join(orig), "\n".join(back))
    ro, rb = scan_single(orig).counts, scan_single(back).counts
    st = prep.stats
    return {"combo": name, "id": s["id"], "scenario": s["scenario"], "fallback": prep.fallback_reason,
            "latency_s": st["rtt_latency_s"], "cost_usd": st["cost_usd"],
            "segment_fallback": st["segment_fallbacks"] / max(1, st["translated_slots"]),
            "unchanged_rate": sum(1 for a, b in zip(orig, back) if a == b) / max(1, len(idx)),
            "number_mismatch_doc": bool(miss or add), "number_detail": [dict(miss), dict(add)],
            **{f"{k}_before": ro[k] / chars * 1000 for k in ("loose_english", "long_clause", "cliche")},
            **{f"{k}_after": rb[k] / chars * 1000 for k in ("loose_english", "long_clause", "cliche")},
            "rtt_draft": prep.rtt_draft}


def main() -> None:
    load_dotenv()
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(c, s) for c in COMBOS for s in dev_samples()]
    with ThreadPoolExecutor(1) as ex:  # 逐篇串行：模拟单个用户；腾讯云默认每分钟 60 次
        rows = list(ex.map(run, jobs))
    with open(OUT / "rtt.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {}
    for c in COMBOS:
        rs = [r for r in rows if r["combo"] == c]
        lat = sorted(r["latency_s"] or 0 for r in rs)
        summary[c] = {
            "n": len(rs), "doc_fallback": sum(1 for r in rs if r["fallback"]),
            "latency_p50": round(statistics.median(lat), 2), "latency_p90": round(lat[int(0.9 * (len(lat) - 1))], 2),
            **{k: round(statistics.mean(r[k] for r in rs), 4) for k in
               ("segment_fallback", "unchanged_rate", "loose_english_before", "loose_english_after",
                "long_clause_before", "long_clause_after", "cliche_before", "cliche_after")},
            "number_mismatch_docs": sum(1 for r in rs if r["number_mismatch_doc"]),
            "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 5),
        }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
