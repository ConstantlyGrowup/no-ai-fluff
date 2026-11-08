"""M4 对照基线：用与 no-ai-fluff 相同的宿主模型执行第三方润色 Skill，产出 baseline.jsonl。

    python benchmarks/run_baseline.py --run m4 --baseline humanizer-zh

每行：{sample_id, scenario, variant: "baseline:<name>", final_text, usage, latency_s, ...}；已完成的样本自动跳过。
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

BASELINES = Path(__file__).resolve().parent / "baselines"

# 沿用 Humanizer-zh README 的调用方式；只补一句“只输出改写稿”，便于直接取用结果
USER_TEMPLATES = {
    "humanizer-zh": "请用 humanizer-zh 润色下面的文章。直接输出最终改写稿全文，不要附说明。\n\n{text}",
}


def strip_wrapper(text: str) -> str:
    """去掉推理段和整体包裹的代码围栏（模型偶尔把全文放进 ```markdown 里）。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.fullmatch(r"```(?:markdown|md|text)?\n(.*)\n```", text, flags=re.S)
    return m.group(1).strip() if m else text


def main() -> None:
    ra.load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--baseline", default="humanizer-zh", choices=sorted(USER_TEMPLATES))
    ap.add_argument("--host", default=ra.DEFAULT_HOST)
    ap.add_argument("--provider", default="deepseek", choices=["deepseek", "openrouter"])
    ap.add_argument("--base-url")
    ap.add_argument("--api-key")
    ap.add_argument("--effort", default="high", choices=["low", "high", "max"])
    ap.add_argument("--max-tokens", type=int, default=64000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    args.dir = ra.BENCH / "results" / args.run
    ra.load_run_config(args.dir)
    skill = (BASELINES / args.baseline / "SKILL.md").read_text("utf-8")
    variant = f"baseline:{args.baseline}"
    out = args.dir / "baseline.jsonl"
    done = {(r["sample_id"], r["variant"]) for r in ra.load_jsonl(out)}
    jobs = [s for s in ra.samples(args.only) if (s["id"], variant) not in done]
    client = ra.make_client(args)
    print(f"baseline jobs: {len(jobs)} {variant} host={args.host} effort={args.effort}")

    def run(s: dict) -> None:
        if getattr(client, "provider", None) == "deepseek":
            extra = {"reasoning_effort": args.effort}
        elif getattr(client, "is_local", False):
            extra = None
        else:
            extra = {"usage": {"include": True}, "reasoning": {"effort": args.effort, "exclude": True}}
        user = USER_TEMPLATES[args.baseline].format(text=s["text"])
        try:
            res = client.chat(args.host, [{"role": "system", "content": skill}, {"role": "user", "content": user}],
                              temperature=0.0, max_tokens=args.max_tokens, timeout=client.timeout, extra=extra)
        except Exception as e:  # noqa: BLE001 —— 失败不落盘，重跑时补上
            print(f"  {s['id']} FAILED {e}", flush=True)
            return
        final = strip_wrapper(res.text)
        ra.append_jsonl(out, {
            "sample_id": s["id"], "scenario": s["scenario"], "variant": variant, "baseline": args.baseline,
            "host": args.host, "effort": args.effort, "final_text": final, "raw": res.text,
            "usage": res.usage or {}, "latency_s": round(res.latency_s, 2),
            "chars_in": len(s["text"]), "chars_out": len(final),
        })
        print(f"  {s['id']:15} {len(s['text'])} -> {len(final)} chars {res.latency_s:.1f}s", flush=True)

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(run, jobs))


if __name__ == "__main__":
    main()
