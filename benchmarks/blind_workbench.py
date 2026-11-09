"""M3 人工盲测工作台：生成离线 HTML，并统计导出的投票。

    python benchmarks/blind_workbench.py build              # -> benchmarks/blind_test_workbench.html
    python benchmarks/blind_workbench.py analyze votes.json # 解码投票，输出统计与 results/m3/blind_summary.md

盲测设计
- 每篇的 4 个版本（原文 + hy-mt2 法/日/俄往返 + 高推理宿主 v2 补丁）以 A~D 呈现，顺序逐篇随机；
- 页面里只有不透明代号，版本对应关系存在 benchmarks/.cache/blind/key-<run>.json，测完才解码；
- 不给参考原文，只凭阅读直觉选“最好读”（必选）和“最难读”（可选）。
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

OUT_HTML = ra.BENCH / "blind_test_workbench.html"
LABELS = {"original": "原文", "patched:hymt-fr": "法语往返 + 补丁", "patched:hymt-ja": "日语往返 + 补丁",
          "patched:hymt-ru": "俄语往返 + 补丁", "baseline:humanizer-zh": "Humanizer-zh"}
M3_ORDER = ["original", "patched:hymt-fr", "patched:hymt-ja", "patched:hymt-ru"]


def run_order(cfg: dict) -> list[str]:
    """盲测候选：run.json 写了 judge_variants 就按它（如 M4 的原文 / 俄语补丁 / Humanizer-zh），否则沿用 M3 的四个版本。"""
    return [v for v in cfg.get("judge_variants") or [] if not v.endswith(":")] or M3_ORDER


def key_path(run: str) -> Path:
    return ra.CACHE / "blind" / f"key-{run}.json"


def build(args) -> None:
    order = run_order(ra.load_run_config(args.dir))
    samples = ra.samples()
    patch = {(p["sample_id"], p["combo"]): p for p in ra.load_jsonl(args.dir / "patch.jsonl")}
    base = {(b["sample_id"], b["variant"]): b for b in ra.load_jsonl(args.dir / "baseline.jsonl")}
    rng = random.Random(args.seed)
    key, items = {}, []
    for s in samples:
        variants = []
        for v in order:
            if v == "original":
                variants.append((v, s["text"]))
            elif v.startswith("patched:") and (s["id"], v[8:]) in patch:
                variants.append((v, patch[(s["id"], v[8:])]["final_text"]))
            elif (s["id"], v) in base:
                variants.append((v, base[(s["id"], v)]["final_text"]))
        rng.shuffle(variants)
        cands = []
        for v, text in variants:
            code = secrets.token_hex(3)
            key[code] = {"sample_id": s["id"], "variant": v}
            cands.append({"code": code, "text": text})
        items.append({"sid": s["id"], "title": s["title"], "scenario": s["scenario"],
                      "prompt": s.get("prompt"), "cands": cands})
    rng.shuffle(items)  # 篇目顺序也打乱，避免按场景分组带来的疲劳偏差
    kp = key_path(args.run)
    if kp.exists() and not args.force:
        sys.exit(f"对照表已存在：{kp}\n重新生成会换掉全部代号，已导出的投票将无法解码。确认要重建请加 --force。")
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_text(json.dumps({"run": args.run, "seed": args.seed, "codes": key}, ensure_ascii=False, indent=2), "utf-8")
    data = json.dumps({"run": args.run, "items": items}, ensure_ascii=False).replace("</", "<\\/")
    tpl = (Path(__file__).resolve().parent / "blind_workbench_template.html").read_text("utf-8")
    OUT_HTML.write_text(tpl.replace("/*__DATA__*/null", data), "utf-8")
    print(f"{len(items)} 篇、{sum(len(i['cands']) for i in items)} 个版本 -> {OUT_HTML}\n对照表 -> {kp}（测完前不要打开）")


def analyze(args) -> None:
    key = json.loads(key_path(args.run).read_text("utf-8"))["codes"]
    votes = json.loads(Path(args.file).read_text("utf-8-sig"))
    rows = votes.get("votes", votes)
    best, worst, ties = Counter(), Counter(), 0
    by_scen = defaultdict(Counter)
    detail = []
    for sid, v in rows.items():
        for c in (v.get("best"), v.get("worst")):
            if c and c not in key:
                sys.exit(f"代号 {c}（{sid}）不在 {args.run} 的对照表里：投票文件和对照表不是同一轮")
        b = key.get(v.get("best") or "", {}).get("variant")
        w = key.get(v.get("worst") or "", {}).get("variant")
        if v.get("tie"):
            ties += 1
        if b:
            best[b] += 1
            by_scen[sid.split("-")[0]][b] += 1
        if w:
            worst[w] += 1
        detail.append({"sample_id": sid, "best": b, "worst": w, "tie": bool(v.get("tie")), "note": v.get("note", ""),
                       "seconds": v.get("seconds")})
    n = sum(best.values())
    cfg = ra.load_run_config(args.dir)
    order = run_order(cfg)
    LABELS.update(cfg.get("labels") or {})
    lines = [f"# {args.run} 人工盲测结果（{n} 篇有效投票）", "", "| 版本 | 最好读（票） | 占比 | 最难读（票） |", "|---|---|---|---|"]
    for v in order:
        lines.append(f"| {LABELS[v]} | {best[v]} | {best[v] / n:.0%} | {worst[v]} |" if n else f"| {LABELS[v]} | 0 | - | {worst[v]} |")
    lines += ["", "- " + "；".join(f"{LABELS[v]} 被选为最好读 {best[v]}/{n} 篇" for v in order),
              f"- 标记“差不多，选不出”：{ties} 篇", "", "## 分场景（最好读）", "",
              "| 场景 | " + " | ".join(LABELS[v] for v in order) + " |", "|---|" + "---|" * len(order)]
    for sc, c in sorted(by_scen.items()):
        lines.append(f"| {sc} | " + " | ".join(str(c[v]) for v in order) + " |")
    lines += ["", "## 逐篇", "", "| 样本 | 最好读 | 最难读 | 选不出 | 备注 |", "|---|---|---|---|---|"]
    for d in sorted(detail, key=lambda x: x["sample_id"]):
        lines.append(f"| {d['sample_id']} | {LABELS.get(d['best'], '-')} | {LABELS.get(d['worst'], '-')} | "
                     f"{'是' if d['tie'] else ''} | {d['note'].replace('|', '/').replace(chr(10), ' ')} |")
    out = args.dir / "blind_summary.md"
    out.write_text("\n".join(lines) + "\n", "utf-8")
    (args.dir / "blind_votes.json").write_text(json.dumps({"raw": votes, "decoded": detail}, ensure_ascii=False, indent=2), "utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["build", "analyze"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--run", help="build 必填；analyze 默认取投票文件里记录的轮次")
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument("--force", action="store_true", help="对照表已存在时仍然重建（会使已导出的投票无法解码）")
    args = ap.parse_args()
    if args.stage == "analyze":
        if not args.file:
            ap.error("analyze 需要导出的投票文件")
        voted = json.loads(Path(args.file).read_text("utf-8-sig")).get("run")
        if args.run and voted and args.run != voted:
            ap.error(f"投票文件属于 {voted}，与 --run {args.run} 不符")
        args.run = args.run or voted
        if not args.run:
            ap.error("投票文件里没有记录轮次，请用 --run 指定")
    elif not args.run:
        ap.error("build 需要 --run，例如 --run m4")
    args.dir = ra.BENCH / "results" / args.run
    {"build": build, "analyze": analyze}[args.stage](args)


if __name__ == "__main__":
    main()
