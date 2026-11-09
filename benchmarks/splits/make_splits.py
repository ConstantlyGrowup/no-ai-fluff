"""Train / Dev / Holdout-Test 三级切分（交接文档第 6.3 节）。

    python benchmarks/splits/make_splits.py            # 首次切分：写 train.json / dev.json / holdout.lock.json
    python benchmarks/splits/make_splits.py --verify   # 核对 holdout 的 sha256 与封存文件

规则
- Train：已被逐篇读过、调过参的 22 篇（M3 10 篇、M4 10 篇、hard-001/002）。只有这里允许逐篇读 bad case。
- Dev：12 篇未读旧样本 + 新样本里没抽进 Holdout 的部分。只看聚合指标与分场景分。
- Holdout：新样本按场景分层，固定种子每个场景抽 5 篇（不足 5 篇的场景全部进 Holdout）。
  抽完立刻写 holdout.lock.json（只含 id、每篇文本的 sha256、种子、时间），正文移出 samples.jsonl，
  封存到 corpus/holdout_sealed.jsonl。宣布收敛前不读正文、不跑任何实验。
- holdout.lock.json 已存在时拒绝重切，保证 Holdout 不会因为重跑而换人。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "corpus"
SAMPLES = CORPUS / "samples.jsonl"
SEALED = CORPUS / "holdout_sealed.jsonl"
LOCK = HERE / "holdout.lock.json"
SEED = 20261110
PER_SCENARIO = 5

TRAIN = [
    # M3（10）
    "coding-003", "coding-005", "teaching-002", "teaching-004", "writing-002", "writing-003",
    "discussion-004", "discussion-005", "daily-002", "daily-007",
    # M4（10）
    "coding-001", "coding-002", "teaching-001", "teaching-003", "writing-004", "writing-005",
    "discussion-001", "discussion-002", "daily-001", "daily-012",
    # 构造难例
    "hard-001", "hard-002",
]
DEV_OLD = ["coding-004", "teaching-005", "writing-001", "discussion-003", "daily-003", "daily-004",
           "daily-005", "daily-006", "daily-008", "daily-009", "daily-010", "daily-011"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()] if path.exists() else []


def dump(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def make() -> None:
    if LOCK.exists():
        sys.exit(f"{LOCK} 已存在，Holdout 已封存，拒绝重切。")
    rows = load(SAMPLES)
    ids = {r["id"] for r in rows}
    missing = [i for i in TRAIN + DEV_OLD if i not in ids]
    if missing:
        sys.exit(f"samples.jsonl 缺少 {missing}")
    new = [r for r in rows if r["id"] not in set(TRAIN) | set(DEV_OLD)]
    by = defaultdict(list)
    for r in new:
        by[r["scenario"]].append(r["id"])
    rng = random.Random(SEED)
    holdout, dev_new = [], []
    for sc in sorted(by):
        pool = sorted(by[sc])
        rng.shuffle(pool)
        holdout += sorted(pool[:PER_SCENARIO])
        dev_new += sorted(pool[PER_SCENARIO:])
    text = {r["id"]: r["text"] for r in rows}
    scen = {r["id"]: r["scenario"] for r in rows}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    def manifest(name, id_list, note):
        counts = defaultdict(int)
        for i in id_list:
            counts[scen[i]] += 1
        return {"split": name, "seed": SEED, "created_at": now, "note": note, "n": len(id_list),
                "by_scenario": dict(sorted(counts.items())), "ids": id_list,
                "sha256": {i: sha(text[i]) for i in id_list}}

    (HERE / "train.json").write_text(json.dumps(manifest(
        "train", TRAIN, "已被逐篇读过、调过参的样本；只有这里允许逐篇读 bad case"), ensure_ascii=False, indent=2), "utf-8")
    (HERE / "dev.json").write_text(json.dumps(manifest(
        "dev", DEV_OLD + dev_new, "未读旧样本 + 新样本中未进 Holdout 的部分；只看聚合指标与分场景分，禁止针对单篇硬编码"),
        ensure_ascii=False, indent=2), "utf-8")
    lock = {"split": "holdout", "seed": SEED, "created_at": now, "per_scenario": PER_SCENARIO,
            "method": "新样本按场景分层：每个场景的 id 排序后用 random.Random(seed) 依场景字母序依次 shuffle，取前 5 篇",
            "sealed_file": "benchmarks/corpus/holdout_sealed.jsonl",
            "n": len(holdout), "ids": holdout, "sha256": {i: sha(text[i]) for i in holdout}}
    LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2), "utf-8")
    # 物理隔离：Holdout 正文移出 samples.jsonl
    hs = set(holdout)
    dump(SEALED, [r for r in rows if r["id"] in hs])
    dump(SAMPLES, [r for r in rows if r["id"] not in hs])
    print(f"train={len(TRAIN)} dev={len(DEV_OLD) + len(dev_new)}（新 {len(dev_new)}） holdout={len(holdout)}")
    print("holdout 按场景：", lock_counts(holdout, scen))
    print("dev 按场景：", lock_counts(DEV_OLD + dev_new, scen))


def lock_counts(ids, scen):
    c = defaultdict(int)
    for i in ids:
        c[scen[i]] += 1
    return dict(sorted(c.items()))


def verify() -> None:
    lock = json.loads(LOCK.read_text("utf-8"))
    sealed = {r["id"]: r for r in load(SEALED)}
    bad = [i for i in lock["ids"] if i not in sealed or sha(sealed[i]["text"]) != lock["sha256"][i]]
    leaked = [r["id"] for r in load(SAMPLES) if r["id"] in set(lock["ids"])]
    print(f"holdout {len(lock['ids'])} 篇；sha256 不符 {bad}；混入 samples.jsonl {leaked}")
    if bad or leaked:
        sys.exit(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    verify() if a.verify else make()
