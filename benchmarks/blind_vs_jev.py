"""人工盲测与 JEV 逐对比对：把盲测投票拆成两两偏好，和同一批样本上已有的 JEV 去偏偏好对照。

    python benchmarks/blind_vs_jev.py --blind holdout-blind --jev holdout

只读 results/<blind>/blind_votes.json（analyze 的解码结果）和 results/<jev>/jev.jsonl，零调用。
一篇投票能导出的两两偏好：最好读 > 其余两个版本；其余版本 > 最难读。
JEV 一侧用 jev.jsonl 里的 p_ours_vs_base / p_ours_vs_orig / p_base_vs_orig（> 0.5 即判前者更好）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

OURS, BASE, ORIG = "patched:hymt-ru", "baseline:humanizer-zh", "original"
# (胜者, 败者) -> (jev 字段, 字段为 P(胜者更好) 时取 True)
PAIR_FIELD = {(OURS, BASE): ("p_ours_vs_base", True), (BASE, OURS): ("p_ours_vs_base", False),
              (OURS, ORIG): ("p_ours_vs_orig", True), (ORIG, OURS): ("p_ours_vs_orig", False),
              (BASE, ORIG): ("p_base_vs_orig", True), (ORIG, BASE): ("p_base_vs_orig", False)}
NAMES = {OURS: "ours", BASE: "hz", ORIG: "orig"}


def human_pairs(decoded: list[dict]) -> list[tuple[str, str, str]]:
    out = []
    for d in decoded:
        if d.get("tie"):
            continue
        b, w = d.get("best"), d.get("worst")
        others = [v for v in (OURS, BASE, ORIG) if v != b]
        if b:
            out += [(d["sample_id"], b, o) for o in others]
        if w:
            out += [(d["sample_id"], o, w) for o in (OURS, BASE, ORIG) if o not in (w, b)]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blind", default="holdout-blind")
    ap.add_argument("--jev", default="holdout")
    args = ap.parse_args()
    decoded = json.loads((ra.BENCH / "results" / args.blind / "blind_votes.json").read_text("utf-8"))["decoded"]
    jev = {r["sample_id"]: r for r in ra.load_jsonl(ra.BENCH / "results" / args.jev / "jev.jsonl")}
    rows, agree, total = [], Counter(), Counter()
    for sid, win, lose in human_pairs(decoded):
        field, direct = PAIR_FIELD[(win, lose)]
        p = jev[sid][field]
        p_win = p if direct else 1 - p
        kind = "-".join(sorted((NAMES[win], NAMES[lose])))
        ok = p_win > 0.5
        agree[kind] += ok
        total[kind] += 1
        rows.append({"sample_id": sid, "human_winner": NAMES[win], "human_loser": NAMES[lose],
                     "jev_p_winner": round(p_win, 3), "agree": ok})
    summary = {k: f"{agree[k]}/{total[k]}" for k in sorted(total)}
    summary["all"] = f"{sum(agree.values())}/{sum(total.values())}"
    out = {"blind": args.blind, "jev": args.jev, "summary": summary, "pairs": rows}
    path = ra.BENCH / "results" / args.blind / "blind_vs_jev.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    for r in rows:
        print(f"  {r['sample_id']:16} {r['human_winner']:>4} > {r['human_loser']:<4} JEV P={r['jev_p_winner']:.3f} "
              f"{'✓' if r['agree'] else '✗'}")
    print(f"写入 {path}")


if __name__ == "__main__":
    main()
