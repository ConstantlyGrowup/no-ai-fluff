"""宣传片演示段落：用锁定配置 v8-locked 跑一遍，不属于评测语料，也不参与任何指标。

    python benchmarks/showcase.py rtt   --run showcase --models tencent/hy-mt2-30b-a3b --bridges ru
    python benchmarks/showcase.py patch --run showcase --host deepseek-flash --effort high --rules v8
    python benchmarks/showcase.py baseline --run showcase      # Humanizer-zh 对照（同一宿主）

样本来自 benchmarks/corpus/showcase.jsonl（人工撰写的高密度黑话段落），与 Train/Dev/Holdout 完全隔离。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

SHOWCASE = ra.BENCH / "corpus" / "showcase.jsonl"
ra.all_samples = lambda: ra.load_jsonl(SHOWCASE)  # 只换样本来源，流水线与规则原样复用

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "baseline":
        import run_baseline
        run_baseline.ra.all_samples = ra.all_samples
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        run_baseline.main()
    else:
        ra.main()
