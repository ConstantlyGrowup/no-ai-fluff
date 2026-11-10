"""从 benchmarks/results 生成宣传片数据 promo/data.js。

宣传片里出现的每个数字、每段原文和定稿都由本脚本从真实结果文件读出，不手抄：

    python promo/build.py

读取：
    results/showcase/   案例 0（演示段落）的往返初稿、宿主补丁、Humanizer-zh 对照
    results/holdout/    coding-012 补丁、JEV 汇总、硬占位符存活率、往返费用
    results/r3-dev/     mixed-006 补丁
    results/holdout-blind/blind_votes.json  人工盲测
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "results"
OUT = Path(__file__).resolve().parent / "data.js"


def jl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# 分词与差异：锁定内容（反引号代码、$公式$、指定术语）是不可拆的整体
# ---------------------------------------------------------------------------

def tokenize(text: str, terms: list[str] = ()) -> list[tuple[str, str]]:
    """返回 [(显示文本, 类型)]；类型：lock（代码/术语）、math（公式变量）、''。"""
    alts = [r"`[^`]+`", r"\$[^$]+\$"] + [re.escape(t) for t in sorted(terms, key=len, reverse=True)]
    pat = re.compile("|".join(alts) + r"|[A-Za-z0-9_.+\-]+|\s+|.", re.S)
    out = []
    for m in pat.finditer(text):
        s = m.group(0)
        if s.startswith("`") and s.endswith("`") and len(s) > 1:
            out.append((s[1:-1], "lock"))
        elif s.startswith("$") and s.endswith("$") and len(s) > 1:
            out.append((s[1:-1], "math"))
        elif s in terms:
            out.append((s, "lock"))
        else:
            out.append((s, ""))
    return out


def merge(segs: list[list]) -> list[list]:
    out: list[list] = []
    for s in segs:
        if out and out[-1][1] == s[1] and s[1] not in ("lock", "math"):
            out[-1][0] += s[0]
        else:
            out.append(list(s))
    return out


def diff(a: str, b: str, terms: list[str] = ()) -> dict:
    """before：删去的标 del；after：新增的标 ins；merged：两者合在一起（S4-4 用）。"""
    ta, tb = tokenize(a, terms), tokenize(b, terms)
    sm = difflib.SequenceMatcher(None, [t[0] for t in ta], [t[0] for t in tb], autojunk=False)
    before, after, merged = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for t in ta[i1:i2]:
                before.append(list(t)); after.append(list(t)); merged.append(list(t))
            continue
        for t in ta[i1:i2]:
            c = t[1] or "del"
            before.append([t[0], c]); merged.append([t[0], c])
        for t in tb[j1:j2]:
            c = t[1] or "ins"
            after.append([t[0], c]); merged.append([t[0], c])
    return {"before": merge(before), "after": merge(after), "merged": merge(merged)}


def mark(segs: list[list], words: list[str], cls: str) -> list[list]:
    """在普通片段里把 words 标成 cls（幕 1 的黑话高亮）。"""
    out = []
    pat = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)))
    for text, c in segs:
        if c:
            out.append([text, c]); continue
        pos = 0
        for m in pat.finditer(text):
            if m.start() > pos:
                out.append([text[pos:m.start()], ""])
            out.append([m.group(0), cls])
            pos = m.end()
        if pos < len(text):
            out.append([text[pos:], ""])
    return out


# ---------------------------------------------------------------------------
# 案例与数字
# ---------------------------------------------------------------------------

def patch_case(run: str, sample: str, find_prefix: str) -> tuple[str, str]:
    row = next(r for r in jl(RES / run / "patch.jsonl") if r["sample_id"] == sample and r["combo"] == "hymt-ru")
    p = next(p for p in row["patches"] if p["applied"] and p["find"].startswith(find_prefix))
    assert p["replace"] in row["final_text"], f"{run}/{sample} 定稿里找不到补丁"
    return p["find"], p["replace"]


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=。)", text.strip()) if s.strip()]


def showcase() -> dict:
    """幕 1：分布式缓存网关演示段落（showcase-004），扫描高亮 + 擦除线换成定稿。"""
    corpus = {r["id"]: r for r in jl(ROOT / "benchmarks" / "corpus" / "showcase.jsonl")}
    rtt = {r["sample_id"]: r for r in jl(RES / "showcase" / "rtt.jsonl")}
    pat = [r for r in jl(RES / "showcase" / "patch.jsonl") if r["sample_id"] == "showcase-004"][-1]
    orig, final = corpus["showcase-004"]["text"], pat["final_text"].strip()
    assert rtt["showcase-004"]["stats"]["placeholders"] == 3  # `SingleFlight` `L1/L2` `p99 < 15ms`
    for t in ["`SingleFlight`", "`L1/L2`", "`p99 < 15ms`"]:
        assert t in orig and t in final, t
    words = ["底座", "收口", "打通", "闭环", "门禁", "心智负担"]          # 定稿里一个不剩
    for w in words:
        assert w in orig and w not in final, w
    phrases = ["为底座收口", "打通", "失效闭环", "读请求 → 本地命中 → 远端兜底 → 回填", "契约门禁前置", "冒烟全绿", "显著降低调用方心智负担"]
    before = tokenize_plain(orig, [])
    before = mark(before, phrases, "mark")
    d = diff(orig, final)
    return {"before": before, "after": d["after"], "words": len(words), "words_left": sum(final.count(w) for w in words),
            "arrows": [orig.count("→"), final.count("→")], "locks": 3,
            "patches": len(pat["patches"]), "applied": pat["applied"], "rejected": pat["rejected"]}


def rtt_fragment() -> dict:
    """幕 3：同一篇演示段落（showcase-003）的真实往返片段。"""
    rtt = {r["sample_id"]: r for r in jl(RES / "showcase" / "rtt.jsonl")}
    corpus = {r["id"]: r for r in jl(ROOT / "benchmarks" / "corpus" / "showcase.jsonl")}
    frag_in = "blocking 已闭环，smoke 通过，赋能下游、降低心智负担。"
    rd = rtt["showcase-003"]["rtt_draft"]
    assert frag_in in corpus["showcase-003"]["text"]
    return {"in": frag_in, "out": rd[rd.index("阻塞问题"):]}


def jargon_stats() -> dict:
    """Dev 24 + Holdout 30 的 6 个高频黑话计数；Holdout 30 的箭头计数（原文 / v8 / Humanizer-zh）。"""
    sys.path.insert(0, str(ROOT / "benchmarks"))
    import run_ablation as ra  # noqa: E402  —— 已宣布收敛，按 lock 文件核对 sha256 后读取 Holdout
    src = {"holdout": {r["id"]: r for r in ra.sealed_samples()}, "r3-dev": {r["id"]: r for r in jl(ROOT / "benchmarks" / "corpus" / "samples.jsonl")}}
    W = ["沉淀", "闭环", "抓手", "底座", "打通", "心智"]
    tot, arr, n = [0, 0, 0], [0, 0, 0], {}
    for run in ("holdout", "r3-dev"):
        base = {r["sample_id"]: r for r in jl(RES / run / "baseline.jsonl")}
        rows = [r for r in jl(RES / run / "patch.jsonl") if r["combo"] == "hymt-ru"]
        n[run] = len(rows)
        for r in rows:
            texts = (src[run][r["sample_id"]]["text"], r["final_text"], base[r["sample_id"]]["final_text"])
            for i, t in enumerate(texts):
                tot[i] += sum(t.count(w) for w in W)
                if run == "holdout":
                    arr[i] += t.count("→")
    return {"words": W, "jargon": tot, "arrows": arr, "n": n}


def pair(run: str, sample: str, a: str, b: str) -> list[str]:
    """一对“原文片段 → 定稿片段”，逐字核对。"""
    row = next(r for r in jl(RES / run / "patch.jsonl") if r["sample_id"] == sample and r["combo"] == "hymt-ru")
    assert any(a in p["find"] for p in row["patches"] if p["applied"]) and b in row["final_text"], (sample, a)
    return [a, b, f"{'封存集' if run == 'holdout' else 'Dev'} · {sample}"]


def teaching_three() -> dict:
    run = "holdout"
    f, r = patch_case(run, "teaching-006", "简化流程")
    hz = next(x for x in jl(RES / run / "baseline.jsonl") if x["sample_id"] == "teaching-006")["final_text"]
    i = hz.index("简化流程"); h = hz[i:hz.index("。", i) + 1]
    return {"orig": f, "hz": h, "ours": r}


# 来自文档的数字（不在结果 jsonl 里）：逐条注明出处
DOC_FACTS = {
    "corpus_m2": 32,                        # docs/rtt-model-comparison.md 测评设置
    "ja_long": [0.09, 0.16],                # 同上 第一节：日语桥接后长句/千字
    "fr_eng": [2.65, 0.96],                 # 同上 第一节：法语桥接后散装英文/千字
    "blind_bridge": {"ru": "5/8", "fr": "2/8", "ja": "0/8"},   # 同上 第九节 M3 人工盲测
    "effort": {"veto": ["5/5", "1/5"], "fid": [2.0, 4.0], "read": [3.2, 3.8]},  # 同上 第八节 m2v2hi
    "drift": [["45 分钟以内", "45~60 分钟之间"], ["运维", "运营"], ["景区电瓶车", "自己骑电动车"]],  # 同上 第七、八节
    "ratchet": [["r0", "49.2%", "基线"], ["r1", "52.8%", "KEEP"], ["r2", "54.9%", "KEEP"], ["r3", "56.8%", "KEEP"],
                ["r4", "56.6%", "REVERT"], ["r5", "持平", "REVERT"], ["r6", "57.0%", "REVERT"]],  # docs/evaluation-report.md 第 5 节
    "hz_hard": 0.9912,                      # results/holdout/summary.json（下面直接读取并核对）
    "stars": {"hz": 22460, "ours": 0, "date": "2026-11-10"},  # GitHub API：op7418/Humanizer-zh；本仓库发布前为私有，0 星
    # 封存集唯一一处“数字报警”（teaching-012）：v8 删掉了重复的“（6 bits）”括注，“6 个比特位”仍在，没有改动任何数值
    "number_note": "唯一一处报警是删掉了重复的“6 bits”括注",
}


def tokenize_plain(text: str, locks: list[str]) -> list[list]:
    return merge([list(t) for t in tokenize(text, locks)])


def blind() -> dict:
    votes = json.loads((RES / "holdout-blind" / "blind_votes.json").read_text("utf-8"))["decoded"]
    ours, base = "patched:hymt-ru", "baseline:humanizer-zh"
    n = len(votes)
    first = {k: sum(v["best"] == k for v in votes) / n for k in (ours, base, "original")}
    win = lose = 0
    for v in votes:  # 两两对比：最好读 > 其余两篇；其余 > 最难读
        variants = {ours, base, "original"}
        pairs = [(v["best"], o) for o in variants - {v["best"]}]
        if v.get("worst"):
            pairs += [(o, v["worst"]) for o in variants - {v["worst"], v["best"]}]
        win += sum(p == (ours, base) for p in pairs)
        lose += sum(p == (base, ours) for p in pairs)
    return {"first_ours": first[ours], "first_base": first[base], "first_orig": first["original"],
            "pairwise_vs_base": win / (win + lose)}


def main() -> None:
    jev = json.loads((RES / "holdout" / "jev_summary.json").read_text("utf-8"))
    summ = json.loads((RES / "holdout" / "summary.json").read_text("utf-8"))["variants"]
    assert round(summ["baseline:humanizer-zh"]["hard_placeholder_survival"], 4) == DOC_FACTS["hz_hard"]
    by = jev["by_scenario"]
    cf, cr = patch_case("holdout", "coding-012", "严格解析")
    mf, mr = patch_case("r3-dev", "mixed-006", "假设 Query")
    tf, tr_ = patch_case("holdout", "teaching-006", "简化流程")
    splits = {k: json.loads((ROOT / "benchmarks" / "splits" / f"{k}.json").read_text("utf-8"))["n"] for k in ("train", "dev")}
    splits["holdout"] = json.loads((ROOT / "benchmarks" / "splits" / "holdout.lock.json").read_text("utf-8"))["n"]
    data = {
        "showcase": showcase(),
        "rtt": rtt_fragment(),
        "coding": {"src": "封存集 · coding-012", **diff(cf, cr)},
        "teaching": {"src": "封存集 · teaching-006", **diff(tf, tr_)},
        "mixed": {"src": "Dev · mixed-006", **diff(mf, mr)},
        "pairs": [pair("holdout", "writing-010", "构建提速的核心抓手", "构建提速的主要做法"),
                  pair("holdout", "writing-010", "将 CI 数据与研发效能看板打通", "把 CI 数据和研发效能看板接起来"),
                  pair("holdout", "discussion-010", "增加了后端的心智负担", "这也让后端理解和维护起来更费劲"),
                  pair("r3-dev", "writing-001", "基础底座建设", "基础设施搭建"),
                  pair("r3-dev", "discussion-007", "评审记录能沉淀设计决策", "评审记录能留下设计决策"),
                  pair("r3-dev", "discussion-007", "blocking 问题能闭环", "阻塞问题能跟到解决")],
        "three": teaching_three(),
        "stats": jargon_stats(),
        "splits": splits,
        "facts": DOC_FACTS,
        "blind": blind(),
        "jev": {"overall": jev["overall"]["win_vs_base"], "vs_orig": jev["overall"]["win_vs_orig"],
                "mixed": by["mixed"]["win_vs_base"], "teaching": by["teaching"]["win_vs_base"],
                "coding": by["coding"]["win_vs_base"]},
        "hard_survival": summ["patched:hymt-ru"]["hard_placeholder_survival"],
        "hz_hard": summ["baseline:humanizer-zh"]["hard_placeholder_survival"],
        "rtt_cost_usd": summ["rtt:hymt-ru"]["cost_usd_mean"],
        "hz_hard_min": summ["baseline:humanizer-zh"]["hard_placeholder_min"],
    }
    OUT.write_text("// 由 promo/build.py 从 benchmarks/results 生成，请勿手改\nwindow.PROMO_DATA = "
                   + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", "utf-8", newline="\n")
    st, b, j = data["stats"], data["blind"], data["jev"]
    print(f"wrote {OUT.name}: jargon {st['jargon']} arrows {st['arrows']} n {st['n']}; splits {splits}; "
          f"blind {b['first_ours']:.1%}; jev {j['overall']:.1%}/{j['vs_orig']:.1%}; hard {data['hard_survival']} vs {data['hz_hard']}; "
          f"showcase words {data['showcase']['words']}->{data['showcase']['words_left']} arrows {data['showcase']['arrows']}")


if __name__ == "__main__":
    main()
