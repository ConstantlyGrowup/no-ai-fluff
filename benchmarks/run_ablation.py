"""Milestone 2 消融实验台：2 模型 × 3 桥接语 × {纯 RTT, RTT + 宿主 JSON 补丁}。

分阶段运行，每阶段结果追加写入 benchmarks/results/<run>/，已完成的条目自动跳过（可断点续跑）：

    python benchmarks/run_ablation.py rtt                    # 阶段 1：往返翻译初稿
    python benchmarks/run_ablation.py patch                  # 阶段 2：宿主模型开局部 JSON 补丁并定稿
    python benchmarks/run_ablation.py judge                  # 阶段 3：LLM 双轴盲评（保真度 × 易读度）
    python benchmarks/run_ablation.py report                 # 汇总：summary.json + summary.md

接本地模型（任何 OpenAI 兼容接口：Ollama / LM Studio / vLLM ……）：
    python benchmarks/run_ablation.py patch --base-url http://localhost:11434/v1 --host qwen3:32b
    python benchmarks/run_ablation.py judge --base-url http://localhost:11434/v1 --judge qwen3:32b

或离线导出任务、自行跑完再导回（每行 {"task_id", "prompt"} -> {"task_id", "response"}）：
    python benchmarks/run_ablation.py export-patch           # -> results/m2/tasks_patch.jsonl
    python benchmarks/run_ablation.py import-patch responses_patch.jsonl
    python benchmarks/run_ablation.py export-judge           # 必须在补丁导入之后
    python benchmarks/run_ablation.py import-judge responses_judge.jsonl

说明
- 生产环境里补丁由宿主模型（如 Claude Code）在独立子上下文中开具，不产生额外 API 费用；
  评测时用 OpenRouter 上的同类模型（默认 anthropic/claude-sonnet-5.5）模拟宿主。
- 评委默认用另一家的模型（google/gemini-3.1-pro-preview），避免评委偏好自家模型的改写。
- OpenRouter 响应缓存在 benchmarks/.cache/（已 gitignore），重跑不重复花钱。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from no_ai_fluff.config import load_dotenv  # noqa: E402
from no_ai_fluff.masker import parse  # noqa: E402
from no_ai_fluff.pipeline import DraftStore, Pipeline, PipelineConfig  # noqa: E402
from no_ai_fluff.radar import KINDS, number_mismatch, scan, scan_document  # noqa: E402
from no_ai_fluff.rtt import SUPPORTED_BRIDGES, SUPPORTED_MODELS, OpenRouterClient  # noqa: E402

BENCH = ROOT / "benchmarks"
CACHE = BENCH / ".cache"
SAMPLES = BENCH / "corpus" / "samples.jsonl"
DEFAULT_HOST = "anthropic/claude-sonnet-5.5"
DEFAULT_JUDGE = "google/gemini-3.1-pro-preview"
SHORT = {"tencent/hy-mt2-30b-a3b": "hymt", "google/gemma-3-4b-it": "gemma"}

_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def append_jsonl(path: Path, rec: dict) -> None:
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def combo_key(model: str, bridge: str) -> str:
    return f"{SHORT.get(model, model)}-{bridge}"


def samples(only: list[str] | None = None) -> list[dict]:
    rows = load_jsonl(SAMPLES)
    return [r for r in rows if not only or r["id"] in only or r["scenario"] in only]


def make_client(args, timeout: float = 300) -> OpenRouterClient:
    """默认走 OpenRouter；给了 --base-url 就走任意 OpenAI 兼容接口（本地模型不需要真实 Key）。"""
    if args.base_url:
        url = args.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        client = OpenRouterClient(api_key=args.api_key or "local", url=url, timeout=timeout,
                                  cache_dir=CACHE / "local")
        client.is_local = True
    else:
        client = OpenRouterClient(cache_dir=CACHE / "openrouter", timeout=timeout)
        client.is_local = False
    return client


def chat_json(client: OpenRouterClient, model: str, prompt: str, max_tokens: int = 6000) -> tuple[str, dict, float]:
    # OpenRouter 专有字段（用量回传、推理预算）不发给本地服务，避免严格校验的服务端报错
    extra = None if getattr(client, "is_local", False) else {
        "usage": {"include": True}, "reasoning": {"effort": "low", "exclude": True}}
    res = client.chat(
        model,
        [{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=max_tokens,
        timeout=client.timeout,
        extra=extra,
    )
    text = re.sub(r"<think>.*?</think>", "", res.text, flags=re.S).strip()  # 去掉推理模型的思考段
    return text, res.usage or {}, res.latency_s


def parse_json_obj(text: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        return json.loads(s[a : b + 1])


# ---------------------------------------------------------------------------
# 阶段 1：RTT
# ---------------------------------------------------------------------------


def stage_rtt(args) -> None:
    out = args.dir / "rtt.jsonl"
    done = {(r["sample_id"], r["combo"]) for r in load_jsonl(out)}
    jobs = [
        (s, m, b)
        for s in samples(args.only)
        for m in args.models
        for b in args.bridges
        if (s["id"], combo_key(m, b)) not in done
    ]
    print(f"rtt jobs: {len(jobs)} (skip {len(done)} done)")

    def run(job):
        s, model, bridge = job
        cfg = PipelineConfig(
            model=model, bridge=bridge, chat_timeout_s=args.timeout, doc_timeout_s=args.timeout,
            cache_dir=str(CACHE / "openrouter"), store_dir=str(CACHE / "drafts"),
            draft_ttl_s=365 * 86400, telemetry=False,
        )
        prep = Pipeline(cfg).prepare(s["text"])
        rec = {
            "sample_id": s["id"], "scenario": s["scenario"], "combo": combo_key(model, bridge),
            "model": model, "bridge": bridge, "draft_id": prep.draft_id, "rtt_draft": prep.rtt_draft,
            "fallback": prep.fallback, "fallback_reason": prep.fallback_reason, "stats": prep.stats,
            "radar": prep.radar["counts"], "radar_hints": prep.radar_hints,
        }
        append_jsonl(out, rec)
        st = prep.stats
        print(f"  {s['id']:15} {rec['combo']:9} {st['rtt_latency_s']}s seg_fb={st['segment_fallbacks']}/{st['translated_slots']}"
              f" ${st['cost_usd']:.5f}{'  FALLBACK ' + str(prep.fallback_reason) if prep.fallback else ''}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(run, jobs))


# ---------------------------------------------------------------------------
# 阶段 2：宿主模型 JSON 补丁
# ---------------------------------------------------------------------------


def _patch_env(args):
    rtt = load_jsonl(args.dir / "rtt.jsonl")
    out = args.dir / "patch.jsonl"
    done = {(r["sample_id"], r["combo"]) for r in load_jsonl(out)}
    only_ids = {s["id"] for s in samples(args.only)}
    jobs = [r for r in rtt if r["sample_id"] in only_ids and (r["sample_id"], r["combo"]) not in done]
    pipe = Pipeline(
        PipelineConfig(telemetry=False, store_dir=str(CACHE / "drafts"), draft_ttl_s=365 * 86400),
        store=DraftStore(CACHE / "drafts", ttl_s=365 * 86400),
    )
    return jobs, out, pipe


def patch_prompt(pipe: Pipeline, r: dict) -> str:
    from no_ai_fluff.patcher import build_audit_prompt

    draft = pipe.store.get(r["draft_id"])
    # 用当前版本的雷达重新生成提示（阶段 1 记录的提示可能来自旧口径）
    hints = scan([s.masked for s in draft.doc.slots], draft.rtt_slots, only=draft.translated).to_prompt()
    return build_audit_prompt(draft.original_text, draft.rtt_text(), hints)


def record_patch(pipe: Pipeline, out: Path, r: dict, host: str, raw: str, usage: dict, lat: float, prompt: str) -> None:
    fin = pipe.finalize(r["draft_id"], raw, keep_draft=True)
    rec = {
        "sample_id": r["sample_id"], "scenario": r["scenario"], "combo": r["combo"], "host": host,
        "raw_patches": raw, "patches": fin.patches, "applied": fin.applied, "rejected": fin.rejected,
        "final_text": fin.final_text, "fallback": fin.fallback, "fallback_reason": fin.fallback_reason,
        "host_latency_s": round(lat, 2), "host_cost_usd": usage.get("cost"), "host_usage": usage,
        "prompt_chars": len(prompt),
    }
    append_jsonl(out, rec)
    print(f"  {r['sample_id']:15} {r['combo']:9} patches={len(fin.patches)} applied={fin.applied} "
          f"rejected={fin.rejected} {lat:.1f}s{' ' + fin.fallback_reason if fin.fallback_reason else ''}", flush=True)


def stage_patch(args) -> None:
    jobs, out, pipe = _patch_env(args)
    print(f"patch jobs: {len(jobs)} host={args.host} via={args.base_url or 'openrouter'}")
    client = make_client(args)

    def run(r):
        prompt = patch_prompt(pipe, r)
        try:
            raw, usage, lat = chat_json(client, args.host, prompt)
        except Exception as e:  # noqa: BLE001 —— 调用失败不落盘，下次重跑会补上
            print(f"  {r['sample_id']} {r['combo']} HOST FAILED {e}", flush=True)
            return
        record_patch(pipe, out, r, args.host, raw, usage, lat, prompt)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(run, jobs))


def stage_export_patch(args) -> None:
    jobs, _, pipe = _patch_env(args)
    path = args.dir / "tasks_patch.jsonl"
    path.write_text("".join(
        json.dumps({"task_id": f"{r['sample_id']}|{r['combo']}", "prompt": patch_prompt(pipe, r)}, ensure_ascii=False) + "\n"
        for r in jobs), "utf-8")
    print(f"exported {len(jobs)} patch tasks -> {path}")


def stage_import_patch(args) -> None:
    jobs, out, pipe = _patch_env(args)
    resp = {x["task_id"]: x for x in load_jsonl(Path(args.file))}
    n = 0
    for r in jobs:
        x = resp.get(f"{r['sample_id']}|{r['combo']}")
        if x is None:
            continue
        record_patch(pipe, out, r, x.get("model") or args.host, x["response"], {}, float(x.get("latency_s") or 0),
                     patch_prompt(pipe, r))
        n += 1
    print(f"imported {n}/{len(jobs)} patch responses")


# ---------------------------------------------------------------------------
# 阶段 3：LLM 双轴盲评
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """你是中文技术写作评审。下面给出【参考原文】和一份【候选文本】。候选文本可能是原文本身，也可能是改写版本，你不知道它的来源，请独立评价，只输出 JSON。

评分项：
A. fidelity（语义与技术事实保真度，1–5）：与参考原文相比，事实、数字、术语、因果方向、条件强弱（如“仅当”与“通常”）、代码/路径/标识符是否一致；改写导致的信息遗漏也算错误。
   只要出现一处会误导读者的技术事实错误，fidelity 不得高于 2，并把错误写进 errors。
B. readability（中文自然易读度，1–5）：只看候选文本本身是否好读——句子主谓完整、不堆砌名词短语；因果直说，不用“打通、闭环、赋能”等空间隐喻和黑话；不自造读者看不懂的压缩代号；非必要不夹英文；无翻译腔；推理不跳步；代词指代清楚。
   5=像有经验的工程师写给同事的大白话；3=能读懂但有明显 AI 腔或翻译腔；1=很难读懂。

输出格式（不要输出其他内容）：
{{"fidelity": 整数, "readability": 整数, "errors": ["每条一句话，引用出错片段"], "readability_issues": ["最多 3 条"]}}

【参考原文】
{original}

【候选文本】
{candidate}
"""


def variants_for(sample: dict, rtt_rows: list[dict], patch_rows: list[dict]) -> list[tuple[str, str]]:
    vs = [("original", sample["text"])]
    for r in rtt_rows:
        vs.append((f"rtt:{r['combo']}", r["rtt_draft"]))
    for r in patch_rows:
        vs.append((f"patched:{r['combo']}", r["final_text"]))
    return vs


def _judge_env(args):
    rtt = defaultdict(list)
    for r in load_jsonl(args.dir / "rtt.jsonl"):
        rtt[r["sample_id"]].append(r)
    patch = defaultdict(list)
    for r in load_jsonl(args.dir / "patch.jsonl"):
        patch[r["sample_id"]].append(r)
    out = args.dir / "judge.jsonl"
    done = {(r["sample_id"], r["variant"]) for r in load_jsonl(out)}
    jobs = []
    for s in samples(args.only):
        for name, text in variants_for(s, rtt[s["id"]], patch[s["id"]]):
            if (s["id"], name) not in done:
                jobs.append((s, name, text))
    return jobs, out


def record_judge(out: Path, s: dict, name: str, judge: str, raw: str, usage: dict) -> bool:
    try:
        obj = parse_json_obj(raw)
        fid, rd = int(obj["fidelity"]), int(obj["readability"])
    except Exception as e:  # noqa: BLE001
        print(f"  {s['id']} {name} 评审输出无法解析：{e}", flush=True)
        return False
    rec = {
        "sample_id": s["id"], "scenario": s["scenario"], "variant": name, "judge": judge,
        "fidelity": fid, "readability": rd, "errors": obj.get("errors", []),
        "readability_issues": obj.get("readability_issues", []), "judge_cost_usd": usage.get("cost"),
    }
    append_jsonl(out, rec)
    print(f"  {s['id']:15} {name:16} F={fid} R={rd} errors={len(rec['errors'])}", flush=True)
    return True


def stage_judge(args) -> None:
    jobs, out = _judge_env(args)
    print(f"judge jobs: {len(jobs)} judge={args.judge} via={args.base_url or 'openrouter'}")
    client = make_client(args)

    def run(job):
        s, name, text = job
        prompt = JUDGE_PROMPT.format(original=s["text"], candidate=text)
        for _ in range(2):
            try:
                raw, usage, _lat = chat_json(client, args.judge, prompt, max_tokens=4000)
            except Exception as e:  # noqa: BLE001
                print(f"  {s['id']} {name} JUDGE FAILED {e}", flush=True)
                return
            if record_judge(out, s, name, args.judge, raw, usage):
                return

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(run, jobs))


def stage_export_judge(args) -> None:
    jobs, _ = _judge_env(args)
    path = args.dir / "tasks_judge.jsonl"
    path.write_text("".join(
        json.dumps({"task_id": f"{s['id']}|{name}", "prompt": JUDGE_PROMPT.format(original=s["text"], candidate=text)},
                   ensure_ascii=False) + "\n"
        for s, name, text in jobs), "utf-8")
    print(f"exported {len(jobs)} judge tasks -> {path}")


def stage_import_judge(args) -> None:
    jobs, out = _judge_env(args)
    resp = {x["task_id"]: x for x in load_jsonl(Path(args.file))}
    n = sum(
        record_judge(out, s, name, resp[f"{s['id']}|{name}"].get("model") or args.judge,
                     resp[f"{s['id']}|{name}"]["response"], {})
        for s, name, _ in jobs if f"{s['id']}|{name}" in resp
    )
    print(f"imported {n}/{len(jobs)} judge responses")


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def text_metrics(original: str, text: str) -> dict:
    doc = parse(original)
    frozen = doc.placeholders
    survived = sum(1 for p in frozen if p in text) / len(frozen) if frozen else 1.0
    cand = parse(text)
    missing, added = number_mismatch("\n".join(s.masked for s in doc.slots), "\n".join(s.masked for s in cand.slots))
    num_diff = sum(missing.values()) + sum(added.values())
    radar = scan_document(text).counts
    kchars = max(1, len(text)) / 1000
    return {
        "placeholder_survival": survived,
        "number_mismatch": num_diff,
        "fence_ok": original.count("```") == text.count("```"),
        "radar_per_kchar": {k: radar[k] / kchars for k in KINDS},
        "radar_total_per_kchar": sum(radar.values()) / kchars,
        "chars": len(text),
    }


def _mean(xs, nd: int = 4):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), nd) if xs else None


def stage_report(args) -> None:
    smap = {s["id"]: s for s in samples()}
    rtt = load_jsonl(args.dir / "rtt.jsonl")
    patch = load_jsonl(args.dir / "patch.jsonl")
    judge = load_jsonl(args.dir / "judge.jsonl")
    jmap = {(j["sample_id"], j["variant"]): j for j in judge}

    rows: list[dict] = []  # 每个 (样本, 版本) 一行
    for s in smap.values():
        m = text_metrics(s["text"], s["text"])
        rows.append({"sample_id": s["id"], "scenario": s["scenario"], "variant": "original", **m})
    for r in rtt:
        if r["sample_id"] not in smap:
            continue
        m = text_metrics(smap[r["sample_id"]]["text"], r["rtt_draft"])
        st = r["stats"]
        rows.append({
            "sample_id": r["sample_id"], "scenario": r["scenario"], "variant": f"rtt:{r['combo']}", **m,
            "latency_s": st["rtt_latency_s"], "cost_usd": st["cost_usd"], "fallback": r["fallback"],
            "segment_fallback_rate": st["segment_fallbacks"] / st["translated_slots"] if st["translated_slots"] else 0,
        })
    for r in patch:
        if r["sample_id"] not in smap:
            continue
        m = text_metrics(smap[r["sample_id"]]["text"], r["final_text"])
        rows.append({
            "sample_id": r["sample_id"], "scenario": r["scenario"], "variant": f"patched:{r['combo']}", **m,
            "patches": len(r["patches"]), "applied": r["applied"], "rejected": r["rejected"],
            "host_cost_usd": r["host_cost_usd"], "host_latency_s": r["host_latency_s"],
        })
    for row in rows:
        j = jmap.get((row["sample_id"], row["variant"]))
        row["fidelity"] = j["fidelity"] if j else None
        row["readability"] = j["readability"] if j else None
        row["veto"] = (j["fidelity"] <= 2) if j else None
        row["errors"] = j["errors"] if j else None

    def agg(group: list[dict]) -> dict:
        lat = [r.get("latency_s") for r in group if r.get("latency_s") is not None]
        return {
            "n": len(group),
            "fidelity": _mean([r["fidelity"] for r in group]),
            "readability": _mean([r["readability"] for r in group]),
            "veto_rate": _mean([1.0 if r["veto"] else 0.0 for r in group if r["veto"] is not None]),
            "placeholder_survival": _mean([r["placeholder_survival"] for r in group]),
            "number_mismatch_rate": _mean([1.0 if r["number_mismatch"] else 0.0 for r in group]),
            "fence_ok_rate": _mean([1.0 if r["fence_ok"] else 0.0 for r in group]),
            "radar_per_kchar": _mean([r["radar_total_per_kchar"] for r in group]),
            "radar_kinds_per_kchar": {k: _mean([r["radar_per_kchar"][k] for r in group]) for k in KINDS},
            "latency_p50_s": round(statistics.median(lat), 2) if lat else None,
            "cost_usd_mean": _mean([r.get("cost_usd") for r in group], 6),
            "segment_fallback_rate": _mean([r.get("segment_fallback_rate") for r in group]),
            "fallback_rate": _mean([1.0 if r.get("fallback") else 0.0 for r in group if "fallback" in r]),
            "patches_mean": _mean([r.get("patches") for r in group]),
            "patch_reject_rate": (
                round(sum(r.get("rejected", 0) for r in group) / max(1, sum(r.get("patches", 0) for r in group)), 3)
                if any("patches" in r for r in group) else None
            ),
            "host_cost_usd_mean": _mean([r.get("host_cost_usd") for r in group], 6),
        }

    by_variant: dict[str, list] = defaultdict(list)
    by_scen: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_variant[r["variant"]].append(r)
        by_scen[(r["variant"], r["scenario"])].append(r)
    order = sorted(by_variant, key=lambda v: (0 if v == "original" else 1 if v.startswith("rtt") else 2, v))
    summary = {
        "variants": {v: agg(by_variant[v]) for v in order},
        "by_scenario": {f"{v}|{sc}": agg(g) for (v, sc), g in sorted(by_scen.items())},
        "samples": len(smap),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    (args.dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    (args.dir / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")

    def f(x, nd=2, pct=False):
        if x is None:
            return "-"
        return f"{x * 100:.1f}%" if pct else f"{x:.{nd}f}"

    lines = [
        f"# Milestone 2 消融结果（{len(smap)} 篇样本，生成于 {summary['generated_at']}）",
        "",
        "| 版本 | 保真度 | 易读度 | 一票否决率 | 占位符存活 | 数字不一致 | 雷达命中/千字 | RTT P50 | RTT 花费/篇 | 段回退 | 补丁/篇 | 补丁拒收 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in order:
        a = summary["variants"][v]
        lines.append(
            f"| {v} | {f(a['fidelity'])} | {f(a['readability'])} | {f(a['veto_rate'], pct=True)} | "
            f"{f(a['placeholder_survival'], pct=True)} | {f(a['number_mismatch_rate'], pct=True)} | "
            f"{f(a['radar_per_kchar'])} | {f(a['latency_p50_s'], 1)}s | ${f(a['cost_usd_mean'], 5)} | "
            f"{f(a['segment_fallback_rate'], pct=True)} | {f(a['patches_mean'], 1)} | {f(a['patch_reject_rate'], pct=True)} |"
        )
    scen_names = sorted({r["scenario"] for r in rows})
    lines += ["", "## 分场景（保真度 / 易读度）", "", "| 版本 | " + " | ".join(scen_names) + " |",
              "|---|" + "---|" * len(scen_names)]
    for v in order:
        cells = []
        for sc in scen_names:
            a = summary["by_scenario"].get(f"{v}|{sc}")
            cells.append(f"{f(a['fidelity'], 1)} / {f(a['readability'], 1)}" if a else "-")
        lines.append(f"| {v} | " + " | ".join(cells) + " |")
    lines += ["", "## 雷达分项（命中/千字）", "", "| 版本 | " + " | ".join(KINDS) + " |", "|---|" + "---|" * len(KINDS)]
    for v in order:
        k = summary["variants"][v]["radar_kinds_per_kchar"]
        lines.append(f"| {v} | " + " | ".join(f(k[x]) for x in KINDS) + " |")
    (args.dir / "summary.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["rtt", "patch", "judge", "report",
                                      "export-patch", "import-patch", "export-judge", "import-judge"])
    ap.add_argument("file", nargs="?", help="import-* 阶段的回复文件")
    ap.add_argument("--run", default="m2")
    ap.add_argument("--only", nargs="*", help="样本 id 或场景名")
    ap.add_argument("--models", nargs="*", default=list(SUPPORTED_MODELS))
    ap.add_argument("--bridges", nargs="*", default=list(SUPPORTED_BRIDGES))
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--judge", default=DEFAULT_JUDGE)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--base-url", help="OpenAI 兼容接口地址，如 http://localhost:11434/v1；不填走 OpenRouter")
    ap.add_argument("--api-key", help="--base-url 对应服务的 Key；本地服务通常不需要")
    ap.add_argument("--timeout", type=float, default=180.0, help="RTT 整体时限；MVP 阶段不设硬约束，只记录耗时")
    args = ap.parse_args()
    args.dir = BENCH / "results" / args.run
    args.dir.mkdir(parents=True, exist_ok=True)
    if args.stage.startswith("import-") and not args.file:
        ap.error(f"{args.stage} 需要回复文件路径")
    {
        "rtt": stage_rtt, "patch": stage_patch, "judge": stage_judge, "report": stage_report,
        "export-patch": stage_export_patch, "import-patch": stage_import_patch,
        "export-judge": stage_export_judge, "import-judge": stage_import_judge,
    }[args.stage](args)


if __name__ == "__main__":
    main()
