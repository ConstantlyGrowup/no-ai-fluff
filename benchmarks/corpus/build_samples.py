"""合并语料为 samples.jsonl（评测台的唯一输入）。

- generated_raw.jsonl：同一 id 只保留最新一条（重新采集会追加新记录）；
- coding_raw.jsonl：GitHub 真实 PR 总结；
- daily_raw.jsonl：日常对话（DeepSeek 原生作答），日常小写作天然短，最短长度放宽到 100 字；
- 被截断（finish_reason=length）或过短（其余场景 < 300 字）的样本会被剔除并打印警告。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = {"coding": 0, "teaching": 1, "writing": 2, "discussion": 3, "daily": 4}
MIN_CHARS = {"daily": 100}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def main() -> None:
    latest: dict[str, dict] = {}
    for rec in load(HERE / "generated_raw.jsonl"):
        latest[rec["id"]] = rec  # 后写入的覆盖先写入的
    for name in ("coding_raw.jsonl", "daily_raw.jsonl"):
        for rec in load(HERE / name):
            latest[rec["id"]] = rec
    samples, dropped = [], []
    for rec in latest.values():
        text = (rec.get("text") or "").strip()
        if rec.get("finish_reason") == "length" or len(text) < MIN_CHARS.get(rec["scenario"], 300):
            dropped.append(f"{rec['id']}（{rec.get('finish_reason')}，{len(text)} 字）")
            continue
        samples.append({
            "id": rec["id"], "scenario": rec["scenario"], "title": rec["title"], "source_model": rec["model"],
            "subscenario": rec.get("subscenario"), "source_url": rec.get("source_url"),
            "prompt": rec.get("prompt"), "text": text,
        })
    samples.sort(key=lambda r: (ORDER.get(r["scenario"], 9), r["id"]))
    out = HERE / "samples.jsonl"
    out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), "utf-8")
    by: dict[str, int] = {}
    for s in samples:
        by[s["scenario"]] = by.get(s["scenario"], 0) + 1
    print(f"samples={len(samples)} {by} -> {out}")
    for d in dropped:
        print(f"  剔除 {d}")


if __name__ == "__main__":
    main()
