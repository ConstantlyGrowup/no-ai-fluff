"""翻译小模型横评（hy-mt2 vs gemma × 法/日/俄桥接）：只用客观指标，不用大模型评委。

数据来源
1. results/m2/rtt.jsonl + .cache/drafts：32 篇语料 × 6 组合的整篇往返翻译；
2. probes/rtt_probes.jsonl：术语 / 条件 / 数字 / 日常 四类探针句，每句标注必须保留的关键词（含同义写法）。
   探针按生产路径处理（一行一句，走 Pipeline 掩码、合批与校验），只调用 OpenRouter 上的翻译小模型。

    python benchmarks/rtt_compare.py            # 跑探针（有缓存）并输出 results/m2/rtt_compare.json
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402
from no_ai_fluff.masker import PLACEHOLDER_RE, parse  # noqa: E402
from no_ai_fluff.pipeline import DraftStore, Pipeline, PipelineConfig  # noqa: E402
from no_ai_fluff.radar import number_mismatch, scan_single  # noqa: E402

PROBES = ra.BENCH / "probes" / "rtt_probes.jsonl"
OUT = ra.BENCH / "results" / "m2" / "rtt_compare.json"
COMBOS = [(m, b) for m in ra.SUPPORTED_MODELS for b in ra.SUPPORTED_BRIDGES]
_CJK = re.compile(r"[一-鿿]")
_SPACED_CODE = re.compile(r"[一-鿿] (?:\{\{P\d+\}\})|(?:\{\{P\d+\}\}) [一-鿿]")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def run_probes() -> dict:
    probes = ra.load_jsonl(PROBES)
    text = "\n".join(p["text"] for p in probes)
    out = {}
    for model, bridge in COMBOS:
        cfg = PipelineConfig(model=model, bridge=bridge, chat_timeout_s=180, doc_timeout_s=180,
                             cache_dir=str(ra.CACHE / "openrouter"), store_dir=str(ra.CACHE / "probe_drafts"),
                             telemetry=False)
        pipe = Pipeline(cfg)
        prep = pipe.prepare(text, fmt="text")
        draft = pipe.store.get(prep.draft_id)
        rows = []
        for p, slot, rtt in zip(probes, draft.doc.slots, draft.rtt_slots):
            back = _norm(PLACEHOLDER_RE.sub(lambda m: draft.doc.placeholders[int(m.group(1))], rtt))
            missing = [g for g in p["keep"] if not any(_norm(a) in back for a in g)]
            rows.append({"id": p["id"], "category": p["category"], "pass": not missing,
                         "missing": ["/".join(g) for g in missing], "unchanged": rtt == slot.masked,
                         "back": back})
        out[ra.combo_key(model, bridge)] = {"rows": rows, "fallback": prep.fallback_reason}
        n = sum(r["pass"] for r in rows)
        print(f"  probes {ra.combo_key(model, bridge):9} pass {n}/{len(rows)}", flush=True)
    return out


def doc_metrics() -> dict:
    """逐组合汇总整篇往返翻译的客观指标。"""
    smap = {s["id"]: s for s in ra.samples()}
    store = DraftStore(ra.CACHE / "drafts", ttl_s=1e12)
    per = defaultdict(lambda: defaultdict(list))
    for r in ra.load_jsonl(ra.BENCH / "results" / "m2" / "rtt.jsonl"):
        if r["sample_id"] not in smap:
            continue
        d = store.get(r["draft_id"])
        st = r["stats"]
        idx = d.translated
        orig = [d.doc.slots[i].masked for i in idx]
        back = [d.rtt_slots[i] for i in idx]
        chars = sum(len(x) for x in orig) or 1
        changed = [i for i, (a, b) in enumerate(zip(orig, back)) if a != b]
        miss, add = number_mismatch("\n".join(orig), "\n".join(back))
        ro, rb = scan_single(orig).counts, scan_single(back).counts
        spaced_o = sum(len(_SPACED_CODE.findall(x)) for x in orig)
        spaced_b = sum(len(_SPACED_CODE.findall(x)) for x in back)
        lens = [len(b) / len(a) for a, b in zip(orig, back) if len(a) >= 8 and a != b]
        rec = {
            "latency_s": st["rtt_latency_s"],
            "cost_per_kchar": st["cost_usd"] / chars * 1000,
            "segment_fallback": st["segment_fallbacks"] / max(1, st["translated_slots"]),
            "unchanged_rate": 1 - len(changed) / max(1, len(idx)),
            "number_mismatch_doc": 1.0 if (miss or add) else 0.0,
            "number_mismatch_count_per_kchar": (sum(miss.values()) + sum(add.values())) / chars * 1000,
            "loose_english_before": ro["loose_english"] / chars * 1000,
            "loose_english_after": rb["loose_english"] / chars * 1000,
            "long_clause_before": ro["long_clause"] / chars * 1000,
            "long_clause_after": rb["long_clause"] / chars * 1000,
            "cliche_before": ro["cliche"] / chars * 1000,
            "cliche_after": rb["cliche"] / chars * 1000,
            "spacing_kept": (spaced_b / spaced_o) if spaced_o else None,
            "length_ratio": statistics.median(lens) if lens else None,
        }
        for k, v in rec.items():
            per[r["combo"]]["all"].append((k, v))
            per[r["combo"]][r["scenario"]].append((k, v))

    def agg(pairs):
        by = defaultdict(list)
        for k, v in pairs:
            if v is not None:
                by[k].append(v)
        res = {k: round(statistics.mean(v), 4) for k, v in by.items()}
        if by.get("latency_s"):
            res["latency_p50"] = round(statistics.median(by["latency_s"]), 2)
            res["latency_p90"] = round(sorted(by["latency_s"])[int(0.9 * (len(by["latency_s"]) - 1))], 2)
        res["n"] = len(by.get("latency_s", []))
        return res

    return {c: {g: agg(p) for g, p in groups.items()} for c, groups in per.items()}


def main() -> None:
    ra.load_dotenv()
    probes = run_probes()
    docs = doc_metrics()
    summary = {}
    for c, v in probes.items():
        rows = v["rows"]
        cats = defaultdict(list)
        for r in rows:
            cats[r["category"]].append(r["pass"])
        summary[c] = {
            "probe_pass": round(sum(r["pass"] for r in rows) / len(rows), 4),
            "probe_pass_by_category": {k: round(sum(x) / len(x), 4) for k, x in cats.items()},
            "probe_unchanged": round(sum(r["unchanged"] for r in rows) / len(rows), 4),
            "probe_failures": [{"id": r["id"], "missing": r["missing"], "back": r["back"]} for r in rows if not r["pass"]],
        }
    OUT.write_text(json.dumps({"docs": docs, "probes": summary}, ensure_ascii=False, indent=2), "utf-8")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
