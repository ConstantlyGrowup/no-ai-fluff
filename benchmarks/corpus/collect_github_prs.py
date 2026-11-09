"""采集 Coding 场景语料：GitHub 公开仓库里 Claude Code 生成的真实中文 PR 总结（零生成开销）。

    python benchmarks/corpus/collect_github_prs.py            # 写入 coding_raw.jsonl
    python benchmarks/corpus/collect_github_prs.py --n 8      # 追加 8 条（已有条目保持原 id，新条目接着编号）

筛选规则（确定性，便于复现与审计）：
- 公开 PR，正文含 “Claude Code”（含 Generated with Claude Code 署名）；
- 简体中文：汉字 ≥ 400，繁体特征字占比 < 2%；
- 正文长度 800~5000 字符，代码块占比 < 40%（以叙述为主）；
- 每个仓库最多 1 条，按汉字数从多到少取前 N 条。
每条记录保留 html_url / repo / 作者 / 创建时间，用于出处追溯。未登录的 GitHub Search API
限速 10 次/分钟，脚本内置间隔。若设置了 GITHUB_TOKEN 会自动带上（只需公开读权限）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "coding_raw.jsonl"

QUERIES = [
    '"Generated with Claude Code" is:pr is:public 根因',
    '"Generated with Claude Code" is:pr is:public 修复 验证',
    '"Generated with Claude Code" is:pr is:public 问题 原因 方案',
    '"Generated with Claude Code" is:pr is:public 测试计划',
    '"Claude Code" is:pr is:public 改动 影响 测试',
]

_CJK = re.compile(r"[一-鿿]")
# 繁体特有字（简体中不会出现）的一个小样本，用于粗判繁简
_TRAD = set("這們為與發說開關麼後裡體點變個來時會對過還應讓現種實頁線連據數檢務處")
_FENCE = re.compile(r"```.*?```", re.S)


def gh_get(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "no-ai-fluff-corpus"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def qualifies(body: str) -> tuple[bool, dict]:
    cjk = _CJK.findall(body)
    n = len(cjk)
    trad = sum(1 for c in cjk if c in _TRAD) / n if n else 1
    code = sum(len(m) for m in _FENCE.findall(body)) / max(1, len(body))
    info = {"cjk": n, "trad_ratio": round(trad, 4), "code_ratio": round(code, 3), "chars": len(body)}
    ok = (
        "claude code" in body.lower()
        and n >= 400
        and trad < 0.02
        and 800 <= len(body) <= 5000
        and code < 0.4
    )
    return ok, info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    args = ap.parse_args()
    # 追加模式：已采集的条目保持原 id 不动，新条目接着编号；同一仓库、同一 PR 不重复采
    existing = [json.loads(l) for l in OUT.read_text("utf-8").splitlines() if l.strip()] if OUT.exists() else []
    seen_repos: set[str] = {r["repo"] for r in existing}
    seen_urls = {r["source_url"] for r in existing}
    next_no = max((int(r["id"].split("-")[1]) for r in existing), default=0) + 1
    pool: list[dict] = []
    for q in QUERIES:
        url = "https://api.github.com/search/issues?" + urllib.parse.urlencode({"q": q, "per_page": 50})
        try:
            data = gh_get(url)
        except Exception as e:  # noqa: BLE001
            print(f"search failed: {q}: {e}", file=sys.stderr)
            continue
        for it in data.get("items", []):
            body = (it.get("body") or "").replace("\r\n", "\n").strip()
            repo = it["repository_url"].split("/repos/", 1)[1]
            ok, info = qualifies(body)
            if (ok and repo not in seen_repos and it["html_url"] not in seen_urls
                    and all(p["source_url"] != it["html_url"] for p in pool)):
                pool.append({
                    "repo": repo, "title": it["title"], "source_url": it["html_url"],
                    "author": (it.get("user") or {}).get("login"), "created_at": it.get("created_at"),
                    "text": body, **info,
                })
        time.sleep(7)  # 未登录 Search API 限速 10 次/分钟
    pool.sort(key=lambda r: -r["cjk"])
    picked: list[dict] = []
    for r in pool:
        if r["repo"] in seen_repos:
            continue
        seen_repos.add(r["repo"])
        picked.append(r)
        if len(picked) >= args.n:
            break
    with OUT.open("a", encoding="utf-8") as f:
        for i, r in enumerate(picked, next_no):
            rec = {
                "id": f"coding-{i:03d}", "scenario": "coding", "model": "claude-code (in the wild)",
                "title": r["title"], "prompt": None, "text": r["text"], "source_url": r["source_url"],
                "repo": r["repo"], "author": r["author"], "created_at": r["created_at"],
                "collected_at": time.strftime("%Y-%m-%d"),
                "stats": {k: r[k] for k in ("cjk", "trad_ratio", "code_ratio", "chars")},
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # 不打印标题与正文：新样本可能落入 Holdout
            print(f"{rec['id']} {r['cjk']:5} cjk {r['chars']:5}ch {r['source_url']}")
    print(f"candidates={len(pool)} picked={len(picked)} -> {OUT}")


if __name__ == "__main__":
    main()
