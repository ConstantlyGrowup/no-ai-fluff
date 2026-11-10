"""优化闭环的单轮工具：建轮次目录、与基准轮次对比。

    python benchmarks/loop_round.py setup   --run r1-probe --split probe --rules v7
    python benchmarks/run_ablation.py patch --run r1-probe --rules v7 --workers 5 --patch-max-tokens 24000
    python benchmarks/jev_judge.py score    --run r1-probe
    python benchmarks/loop_round.py compare --run r1-probe --ref dev

setup：按 splits/<split>.json 写 run.json；往返稿（rtt.jsonl）与 Humanizer-zh 定稿（baseline.jsonl）从 --source 轮次
（默认 dev）逐行复制，零调用、零成本。只改 Stage 2b/3 时这样复用往返翻译缓存（交接文档第 7 节）。
compare：在同一批样本上对比本轮与基准轮次的 JEV 指标（奖励、胜率、易读度、AI 腔、保真风险）与客观指标
（零补丁率、改动幅度、补丁拒收率、回退）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_judge as jj  # noqa: E402
import run_ablation as ra  # noqa: E402


def setup(args) -> None:
    ids = json.loads((ra.BENCH / "splits" / f"{args.split}.json").read_text("utf-8"))["ids"]
    d = ra.BENCH / "results" / args.run
    if (d / "patch.jsonl").exists():
        sys.exit(f"{d} 已有 patch.jsonl，换一个轮次名")
    d.mkdir(parents=True, exist_ok=True)
    src = ra.BENCH / "results" / args.source
    cfg = {"purpose": args.purpose or f"优化闭环 {args.run}", "samples": ids, "split": args.split,
           "judge_variants": ["original", "patched:hymt-ru", "baseline:humanizer-zh"],
           "labels": {"patched:hymt-ru": f"no-ai-fluff {args.rules}"}, "rules": args.rules, "bridge": "ru",
           "rtt_context": 300, "host": "deepseek-flash", "effort": "high", "rtt_and_baseline_from": args.source}
    (d / "run.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    for name in ("rtt.jsonl", "baseline.jsonl"):
        rows = [r for r in ra.load_jsonl(src / name) if r["sample_id"] in ids]
        (d / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
        print(f"{name}: 从 {args.source} 复制 {len(rows)} 行")


def objective(run: str, ids: list[str]) -> dict:
    smap = {s["id"]: s for s in ra.all_samples()}
    rows = [r for r in ra.load_jsonl(ra.BENCH / "results" / run / "patch.jsonl") if r["sample_id"] in ids]
    n = len(rows)
    if not n:
        return {}
    change = [1 - difflib.SequenceMatcher(None, smap[r["sample_id"]]["text"], r["final_text"], autojunk=False).ratio()
              for r in rows]
    applied = sum(int(r["applied"]) for r in rows)
    rejected = sum(int(r["rejected"]) for r in rows)
    return {"n": n, "zero_patch_rate": round(sum(int(r["applied"]) == 0 for r in rows) / n, 3),
            "change_ratio_mean": round(sum(change) / n, 4), "change_ratio_median": round(sorted(change)[n // 2], 4),
            "patches_applied": applied, "patch_reject_rate": round(rejected / max(1, applied + rejected), 3),
            "fallbacks": sum(1 for r in rows if str(r.get("fallback")) == "True")}


def compare(args) -> None:
    cur = {r["sample_id"]: r for r in ra.load_jsonl(ra.BENCH / "results" / args.run / f"jev{args.suffix}.jsonl")}
    ref = {r["sample_id"]: r for r in ra.load_jsonl(ra.BENCH / "results" / args.ref / "jev.jsonl")}
    ids = sorted(set(cur) & set(ref))
    a = jj.summarize([cur[i] for i in ids])
    b = jj.summarize([ref[i] for i in ids])
    keys = ["reward", "win_vs_base", "win_vs_orig", "read_ours", "ai_ours", "risk_ours", "risk_base"]
    out = {"run": args.run, "ref": args.ref, "n": len(ids), "ids": ids,
           "overall": {k: {"cur": a["overall"][k], "ref": b["overall"][k],
                           "delta": None if a["overall"][k] is None or b["overall"][k] is None
                           else round(a["overall"][k] - b["overall"][k], 4)} for k in keys},
           "by_scenario": {sc: {k: [a["by_scenario"][sc][k], b["by_scenario"][sc][k]] for k in ("reward", "win_vs_base", "risk_ours")}
                           for sc in a["by_scenario"]},
           "objective": {"cur": objective(args.run, ids), "ref": objective(args.ref, ids)}}
    (ra.BENCH / "results" / args.run / f"compare_{args.ref}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    fmt = lambda x: "-" if x is None else f"{x:.3f}"
    print(f"{args.run} vs {args.ref}（{len(ids)} 篇）")
    for k in keys:
        o = out["overall"][k]
        print(f"  {k:12} cur={fmt(o['cur'])} ref={fmt(o['ref'])} Δ={fmt(o['delta'])}")
    print("  按场景 [cur, ref]：", json.dumps({sc: {k: [round(x, 3) if x is not None else None for x in v]
                                                 for k, v in d.items()} for sc, d in out["by_scenario"].items()},
                                           ensure_ascii=False))
    print("  客观指标 cur：", out["objective"]["cur"])
    print("  客观指标 ref：", out["objective"]["ref"])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--run", required=True)
    s.add_argument("--split", required=True, choices=["probe", "dev"])
    s.add_argument("--rules", required=True)
    s.add_argument("--source", default="dev")
    s.add_argument("--purpose")
    c = sub.add_parser("compare")
    c.add_argument("--run", required=True)
    c.add_argument("--ref", default="dev")
    c.add_argument("--suffix", default="")
    args = ap.parse_args()
    {"setup": setup, "compare": compare}[args.cmd](args)


if __name__ == "__main__":
    main()
