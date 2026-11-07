"""运行遥测：追加写入 ~/.no-ai-fluff/events.jsonl，并汇总成 stats 统计表。

记录字段：触发出口、模型/桥接语、RTT 耗时与花费、段级回退、雷达命中（原文 / RTT 初稿 / 定稿）、
宿主模型开具的补丁条数（收到 / 生效 / 拒收）、整体回退率。只记录统计量，不记录正文。
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from .radar import KIND_LABELS, KINDS


def home() -> Path:
    return Path(os.environ.get("NO_AI_FLUFF_HOME") or Path.home() / ".no-ai-fluff")


def events_path() -> Path:
    return home() / "events.jsonl"


def log_event(event: dict, path: Path | None = None) -> None:
    if os.environ.get("NO_AI_FLUFF_TELEMETRY", "1") == "0":
        return
    p = path or events_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 遥测失败绝不影响主流程


def read_events(path: Path | None = None) -> list[dict]:
    p = path or events_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


def summarize(events: list[dict]) -> dict:
    prep = [e for e in events if e.get("event") == "prepare"]
    fin = [e for e in events if e.get("event") == "finalize"]
    lat = [e["rtt_latency_s"] for e in prep if e.get("rtt_latency_s") is not None and not e.get("fallback")]
    by_combo: dict[str, list[float]] = defaultdict(list)
    for e in prep:
        if e.get("rtt_latency_s") is not None and not e.get("fallback"):
            by_combo[f"{e.get('model')} / {e.get('bridge')}"].append(e["rtt_latency_s"])
    radar_before: Counter = Counter()
    radar_draft: Counter = Counter()
    radar_after: Counter = Counter()
    for e in fin:
        radar_before.update(e.get("radar_original") or {})
        radar_draft.update(e.get("radar_rtt") or {})
        radar_after.update(e.get("radar_final") or {})
    return {
        "prepare_count": len(prep),
        "finalize_count": len(fin),
        "exits": dict(Counter(e.get("exit", "unknown") for e in prep)),
        "fallback_count": sum(1 for e in prep if e.get("fallback")),
        "fallback_reasons": dict(Counter(e.get("fallback_reason") for e in prep if e.get("fallback"))),
        "segment_fallbacks": sum(e.get("segment_fallbacks", 0) for e in prep),
        "segments_translated": sum(e.get("translated_slots", 0) for e in prep),
        "rtt_latency_p50": statistics.median(lat) if lat else None,
        "rtt_latency_max": max(lat) if lat else None,
        "rtt_latency_by_combo": {k: round(statistics.median(v), 2) for k, v in by_combo.items()},
        "cost_usd": round(sum(e.get("cost_usd") or 0 for e in prep), 6),
        "patches_received": sum(e.get("patches_received", 0) for e in fin),
        "patches_applied": sum(e.get("patches_applied", 0) for e in fin),
        "patches_rejected": sum(e.get("patches_rejected", 0) for e in fin),
        "empty_patch_rate": (sum(1 for e in fin if not e.get("patches_received")) / len(fin)) if fin else None,
        "radar_original": {k: radar_before.get(k, 0) for k in KINDS},
        "radar_rtt": {k: radar_draft.get(k, 0) for k in KINDS},
        "radar_final": {k: radar_after.get(k, 0) for k in KINDS},
    }


def render_table(s: dict) -> str:
    lines = [
        "| 指标 | 数值 |",
        "|---|---|",
        f"| prepare / finalize 次数 | {s['prepare_count']} / {s['finalize_count']} |",
        f"| 触发出口 | {', '.join(f'{k}={v}' for k, v in s['exits'].items()) or '-'} |",
        f"| 整体回退率 | {_pct(s['fallback_count'], s['prepare_count'])} ({s['fallback_count']}) |",
        f"| 段级回退率 | {_pct(s['segment_fallbacks'], s['segments_translated'])} |",
        f"| RTT 耗时 P50 / 最大 | {s['rtt_latency_p50'] and round(s['rtt_latency_p50'], 2)}s / "
        f"{s['rtt_latency_max'] and round(s['rtt_latency_max'], 2)}s |",
        f"| 累计翻译花费 | ${s['cost_usd']} |",
        f"| 补丁 收到 / 生效 / 拒收 | {s['patches_received']} / {s['patches_applied']} / {s['patches_rejected']} |",
        f"| 空补丁 [] 比例 | {s['empty_patch_rate'] is not None and _pct(s['empty_patch_rate'], 1) or '-'} |",
        "",
        "| 雷达类别 | 原文 | RTT 初稿 | 定稿 |",
        "|---|---|---|---|",
    ]
    for k in KINDS:
        lines.append(f"| {KIND_LABELS[k]} | {s['radar_original'][k]} | {s['radar_rtt'][k]} | {s['radar_final'][k]} |")
    if s["rtt_latency_by_combo"]:
        lines += ["", "| 模型 / 桥接语 | RTT 耗时 P50 |", "|---|---|"]
        lines += [f"| {k} | {v}s |" for k, v in s["rtt_latency_by_combo"].items()]
    return "\n".join(lines)
