"""让 Agent（Codex / Antigravity 等）用宿主内置模型做双轴盲评。

和 run_ablation.py judge 的区别：这里没有“脚本调模型”，而是由 Agent 亲自逐份阅读、亲自写评分。
为了让 Agent 读得动、也为了能核实，评审被组织成「评审包」：

- 每个评审包 = 1 篇参考原文 + 若干匿名候选（A、B、C……，顺序随机，混有原文本身）；
  长文自动拆成多个包，每包总字数不超过 --max-chars。
- 候选与版本的对应关系存在 benchmarks/.cache/agent_judge/key.json，Agent 不应读取。
- Agent 把评分写进 answers/<包名>.json（已生成待填写的骨架）。
- 每条错误 / 易读性问题都必须附逐字引文（quote），导入时代码会核实引文确实出现在候选或原文里；
  分数缺失、越界、保真度 ≤2 却没写错误、引文对不上的候选会被拒收并给出原因，改好再导入即可。

    python benchmarks/agent_judge.py export          # 生成评审包与答卷骨架
    python benchmarks/agent_judge.py status          # 进度：哪些包未完成
    python benchmarks/agent_judge.py import          # 校验并导入答卷（可反复执行，已导入的跳过）
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

ANCHOR = "original@anchor"
RUN_CFG: dict = {}

RUBRIC = """## 评分规则（对每个候选独立打分）

**fidelity：语义与技术事实保真度（1–5）**
与【参考原文】相比，事实、数字、术语、因果方向、条件强弱（如“仅当”与“通常”）、代码/路径/标识符是否一致；
改写导致的信息遗漏也算错误。只要出现一处会误导读者的技术事实错误，fidelity 不得高于 2。

**readability：中文自然易读度（1–5）**
只看候选文本本身是否好读：句子主谓完整、不堆砌名词短语；因果直说，不用“打通、闭环、赋能”等空间隐喻和黑话；
不自造读者看不懂的压缩代号；非必要不夹英文；无翻译腔；推理不跳步；代词指代清楚。
5 = 像有经验的工程师写给同事的大白话；3 = 能读懂但有明显 AI 腔或翻译腔；1 = 很难读懂。

**证据要求（导入时会被代码核实）**
- `errors`：每条写 `{"quote": "逐字引文", "why": "一句话说明"}`。quote 从候选文本里逐字复制出错的片段，
  若是遗漏则从参考原文里复制被遗漏的片段；长度 4~80 字。fidelity ≤ 2 时至少写 1 条。
- `readability_issues`：最多 3 条，格式同上，quote 必须逐字摘自该候选。
- 没有问题就写空数组 `[]`。不要概括性地写“多处翻译腔”而不给引文。
"""

RUBRIC_V2 = """## 评分规则（对每个候选独立打分）

**fidelity：语义与技术事实保真度（1–5）**
与【参考原文】相比，事实、数字、术语、因果方向、条件强弱（如“仅当”与“通常”“以内”与“之间”）、
主体与客体、代码/路径/标识符是否一致；改写导致的信息遗漏或凭空添加也算错误。
只要出现一处会误导读者的事实错误，fidelity 不得高于 2。

**readability：读起来有没有“人味”、累不累（1–5）**
想象一位有经验的同事当面跟你讲这件事。他会用完整的句子，主语、动词齐全，该说的“的、了、会、因为、所以”不省；
会直接说谁做了什么、结果怎样，不用口号和黑话。readability 衡量的是候选读起来有多接近这种说话方式。

请特别注意：**好读不等于信息密度高，也不等于字数少。** 把句子压成名词短语、省掉连接词和主语，信息虽然一样多，但读者要自己脑补，读起来更累，应该扣分。
为了把话说清楚而多用几个字，不扣分。

下面的对照例子只用来说明标准，与本包内容无关：

| 读起来累（扣分） | 有人味（加分） | 差在哪里 |
|---|---|---|
| 支持事件签名校验，保障回调安全。 | 我们会校验事件签名，这样回调就不会被伪造。 | 前者把动作压成名词短语，没有主语，也没说清为什么 |
| 此举旨在纠正在实际模型评估中观察到的退化现象。 | 这样改，是因为真实模型评测里发现效果变差了。 | 公文腔、翻译腔 |
| 稳妥拆分、先观测后治理、渐进迁移、风险可控。 | 先拆一个风险小的服务，把监控搭好，确认没问题再拆下一个。 | 口号式排比，读者要自己猜具体做什么 |
| 本次重构打通了数据链路，形成闭环。 | 这次重构统一了上报格式，看板延迟从 15 分钟降到 10 秒。 | 黑话和空间隐喻，没有具体事实 |
| 开发者可以享受更安全的授权流程。 | 开发者改用 OAuth 2.0 授权，比原来的 API Key 更安全。 | 直译搭配“享受流程” |
| 该缓存失效会致其重建，从而引发其负载升高。 | 缓存失效后，服务会重建缓存，这段时间数据库的压力会变大。 | 代词“该、其”指代不清 |
| 这个 issue 很 critical，我们 sync 一下 fallback 方案。 | 这个问题很严重，我们对一下备用方案。 | 不必要的中英夹杂 |

评分参考：
- 5 = 像有经验的人当面讲给你听，一遍读懂，不需要回头重读；
- 4 = 大体自然，偶有生硬的地方；
- 3 = 能读懂，但有明显的 AI 腔、翻译腔或压缩感，读着累；
- 2 = 多处需要回头重读或自己补主语、补逻辑；
- 1 = 很难读懂。
同一包里几个候选可能很接近，按实际感受打分，可以同分，不必强行拉开差距。

**证据要求（导入时会被代码核实）**
- `errors`：每条写 `{"quote": "逐字引文", "why": "一句话说明"}`。quote 从候选文本里逐字复制出错的片段，
  若是遗漏则从参考原文里复制被遗漏的片段；长度 4~80 字。fidelity ≤ 2 时至少写 1 条。
- `readability_issues`：最多 3 条，格式同上，quote 必须逐字摘自该候选。
- 没有问题就写空数组 `[]`。不要概括性地写“多处翻译腔”而不给引文。
"""

# v3：fidelity 与宿主审校规则 v4 同口径——以读者是否被误导为准，不以措辞是否与原文一致为准
RUBRIC_V3 = """## 评分规则（对每个候选独立打分）

**fidelity：读者会不会被误导（1–5）**
判断标准：读者读了候选，会不会得出与【参考原文】不同的错误认识，或者据此做错事。
会误导读者的情况：数字 / 单位 / 量级变了；语气强弱变了（“尽量、建议”变成硬性要求，或反过来）；条件边界变了（“以内”与“之间”）；
因果或主客体颠倒；专业术语或工程专有名词变成了另一个东西；关键信息丢失或凭空添加。
**不算**错误：用词、语体、句子结构与原文不同；书面词换成口语词；句子拆短或调整语序；删掉原文的套话、口号和空洞修饰。
只要出现一处会误导读者的错误，fidelity 不得高于 2；errors 里的 why 要写清楚“读者会误以为什么”。

**readability：读起来有没有“人味”、累不累（1–5）**
想象一位有经验的同事当面跟你讲这件事。他会用完整的句子，主语、动词齐全，该说的“的、了、会、因为、所以”不省；
会直接说谁做了什么、结果怎样，不用口号和黑话。readability 衡量的是候选读起来有多接近这种说话方式。

请特别注意：**好读不等于信息密度高，也不等于字数少。** 把句子压成名词短语、省掉连接词和主语，信息虽然一样多，但读者要自己脑补，读起来更累，应该扣分。
为了把话说清楚而多用几个字，不扣分。

下面的对照例子只用来说明标准，与本包内容无关：

| 读起来累（扣分） | 有人味（加分） | 差在哪里 |
|---|---|---|
| 支持事件签名校验，保障回调安全。 | 我们会校验事件签名，这样回调就不会被伪造。 | 前者把动作压成名词短语，没有主语，也没说清为什么 |
| 此举旨在纠正在实际模型评估中观察到的退化现象。 | 这样改，是因为真实模型评测里发现效果变差了。 | 公文腔、翻译腔 |
| 稳妥拆分、先观测后治理、渐进迁移、风险可控。 | 先拆一个风险小的服务，把监控搭好，确认没问题再拆下一个。 | 口号式排比，读者要自己猜具体做什么 |
| 本次重构打通了数据链路，形成闭环。 | 这次重构统一了上报格式，看板延迟从 15 分钟降到 10 秒。 | 黑话和空间隐喻，没有具体事实 |
| 开发者可以享受更安全的授权流程。 | 开发者改用 OAuth 2.0 授权，比原来的 API Key 更安全。 | 直译搭配“享受流程” |
| 该缓存失效会致其重建，从而引发其负载升高。 | 缓存失效后，服务会重建缓存，这段时间数据库的压力会变大。 | 代词“该、其”指代不清 |
| 这个 issue 很 critical，我们 sync 一下 fallback 方案。 | 这个问题很严重，我们对一下备用方案。 | 不必要的中英夹杂 |

评分参考：
- 5 = 像有经验的人当面讲给你听，一遍读懂，不需要回头重读；
- 4 = 大体自然，偶有生硬的地方；
- 3 = 能读懂，但有明显的 AI 腔、翻译腔或压缩感，读着累；
- 2 = 多处需要回头重读或自己补主语、补逻辑；
- 1 = 很难读懂。
同一包里几个候选可能很接近，按实际感受打分，可以同分，不必强行拉开差距。

**证据要求（导入时会被代码核实）**
- `errors`：每条写 `{"quote": "逐字引文", "why": "一句话说明"}`。quote 从候选文本里逐字复制出错的片段，
  若是遗漏则从参考原文里复制被遗漏的片段；长度 4~80 字。fidelity ≤ 2 时至少写 1 条。
- `readability_issues`：最多 3 条，格式同上，quote 必须逐字摘自该候选。
- 没有问题就写空数组 `[]`。不要概括性地写“多处翻译腔”而不给引文。
"""

RUBRICS = {"v1": RUBRIC, "v2": RUBRIC_V2, "v3": RUBRIC_V3}


_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub("", s or "")


def paths(args):
    base = args.dir / "agent_judge"
    return base / "packets", base / "answers", ra.CACHE / "agent_judge" / f"key-{args.run}.json"


def stage_export(args) -> None:
    packets_dir, answers_dir, key_path = paths(args)
    packets_dir.mkdir(parents=True, exist_ok=True)
    answers_dir.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = json.loads(key_path.read_text("utf-8")) if key_path.exists() else {}
    assigned = {(v["sample_id"], v["variant"]) for p in key.values() for v in p["candidates"].values()}

    jobs, _ = ra._judge_env(args)  # 未评审的 (样本, 版本名, 文本)
    by_sample: dict[str, list] = {}
    for s, name, text in jobs:
        if (s["id"], name) not in assigned:
            by_sample.setdefault(s["id"], []).append((s, name, text))
    if args.anchor:
        # 每篇加一个匿名的原文作锚点（记为 original@anchor），让新候选与原文在同一包内比较，避免跨轮次尺度漂移
        smap = {s["id"]: s for s in ra.samples()}
        for sid, items in by_sample.items():
            if (sid, ANCHOR) not in assigned and all(n != ANCHOR for _, n, _ in items):
                items.append((smap[sid], ANCHOR, smap[sid]["text"]))
    rng = random.Random(args.seed)
    made = 0
    for sid, items in by_sample.items():
        rng.shuffle(items)
        s = items[0][0]
        budget = max(1, args.max_chars - len(s["text"]))
        groups, cur, size = [], [], 0
        for it in items:
            if cur and size + len(it[2]) > budget:
                groups.append(cur)
                cur, size = [], 0
            cur.append(it)
            size += len(it[2])
        if cur:
            groups.append(cur)
        n_existing = sum(1 for k in key if k.startswith(sid + "-"))
        for gi, group in enumerate(groups, n_existing + 1):
            pid = f"{sid}-{gi}"
            letters = [chr(ord("A") + i) for i in range(len(group))]
            key[pid] = {"candidates": {L: {"sample_id": sid, "variant": name} for L, (_, name, _) in zip(letters, group)}}
            body = [f"# 评审包 {pid}", "",
                    f"本包有 {len(group)} 个候选（{'、'.join(letters)}）。候选可能是原文本身，也可能是改写版本，来源已隐去。",
                    f"读完后把评分写进 `answers/{pid}.json`（骨架已生成），不要修改本文件。", "",
                    RUBRICS[RUN_CFG.get("rubric", "v1")],
                    "## 【参考原文】", "", "````text", s["text"], "````", ""]
            for L, (_, _, text) in zip(letters, group):
                body += [f"## 【候选 {L}】", "", "````text", text, "````", ""]
            (packets_dir / f"{pid}.md").write_text("\n".join(body), "utf-8")
            skeleton = {
                "packet": pid,
                "judge_model": None,
                "candidates": {L: {"fidelity": None, "readability": None, "errors": [], "readability_issues": []}
                               for L in letters},
            }
            (answers_dir / f"{pid}.json").write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), "utf-8")
            made += 1
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2), "utf-8")
    print(f"exported {made} packets ({sum(len(v) for v in by_sample.values())} candidates) -> {packets_dir}")


def _validate(cand: dict, text: str, original: str) -> list[str]:
    errs = []
    f, r = cand.get("fidelity"), cand.get("readability")
    if not isinstance(f, int) or not 1 <= f <= 5:
        errs.append("fidelity 必须是 1~5 的整数")
    if not isinstance(r, int) or not 1 <= r <= 5:
        errs.append("readability 必须是 1~5 的整数")
    e_list, r_list = cand.get("errors"), cand.get("readability_issues")
    if not isinstance(e_list, list) or not isinstance(r_list, list):
        return errs + ["errors / readability_issues 必须是数组"]
    if isinstance(f, int) and f <= 2 and not e_list:
        errs.append("fidelity ≤ 2 时 errors 至少要有 1 条")
    if len(r_list) > 3:
        errs.append("readability_issues 最多 3 条")
    nt, no = norm(text), norm(original)
    for kind, items, allow_original in (("errors", e_list, True), ("readability_issues", r_list, False)):
        for i, it in enumerate(items):
            if not isinstance(it, dict) or not isinstance(it.get("quote"), str) or not isinstance(it.get("why"), str):
                errs.append(f"{kind}[{i}] 必须是 {{\"quote\": ..., \"why\": ...}}")
                continue
            q = norm(it["quote"])
            if not 4 <= len(q) <= 120:
                errs.append(f"{kind}[{i}] 的 quote 长度应为 4~80 字")
            elif q not in nt and not (allow_original and q in no):
                where = "候选或原文" if allow_original else "该候选"
                errs.append(f"{kind}[{i}] 的 quote 在{where}里找不到：「{it['quote'][:30]}」")
    return errs


def _texts(args) -> dict:
    smap = {s["id"]: s for s in ra.samples()}
    out = {}
    for s in smap.values():
        out[(s["id"], "original")] = s["text"]
        out[(s["id"], ANCHOR)] = s["text"]
    for r in ra.load_jsonl(args.dir / "rtt.jsonl"):
        out[(r["sample_id"], f"rtt:{r['combo']}")] = r["rtt_draft"]
    for r in ra.load_jsonl(args.dir / "patch.jsonl"):
        out[(r["sample_id"], f"patched:{r['combo']}")] = r["final_text"]
    return out, smap


def stage_import(args) -> None:
    _, answers_dir, key_path = paths(args)
    key = json.loads(key_path.read_text("utf-8"))
    texts, smap = _texts(args)
    out = args.dir / "judge.jsonl"
    done = {(j["sample_id"], j["variant"]) for j in ra.load_jsonl(out)}
    ok = bad = pending = 0
    for pid, info in sorted(key.items()):
        f = answers_dir / f"{pid}.json"
        try:
            ans = json.loads(f.read_text("utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"✗ {pid}: 答卷无法读取（{e}）")
            bad += 1
            continue
        model = ans.get("judge_model")
        cands = ans.get("candidates") or {}
        for L, m in info["candidates"].items():
            sid, variant = m["sample_id"], m["variant"]
            if (sid, variant) in done:
                continue
            c = cands.get(L) or {}
            if c.get("fidelity") is None and c.get("readability") is None:
                pending += 1
                continue
            if not model or not isinstance(model, str):
                print(f"✗ {pid}: 请在 judge_model 填写实际打分的模型名")
                bad += 1
                break
            problems = _validate(c, texts[(sid, variant)], smap[sid]["text"])
            if problems:
                bad += 1
                print(f"✗ {pid} 候选 {L}：" + "；".join(problems))
                continue
            raw = json.dumps(c, ensure_ascii=False)
            obj = {"fidelity": c["fidelity"], "readability": c["readability"],
                   "errors": [f"「{x['quote']}」{x['why']}" for x in c["errors"]],
                   "readability_issues": [f"「{x['quote']}」{x['why']}" for x in c["readability_issues"]]}
            ra.append_jsonl(out, {
                "sample_id": sid, "scenario": smap[sid]["scenario"], "variant": variant, "judge": model,
                "fidelity": obj["fidelity"], "readability": obj["readability"], "errors": obj["errors"],
                "readability_issues": obj["readability_issues"], "judge_cost_usd": None,
                "source": f"agent:{pid}:{L}", "raw": raw,
            })
            done.add((sid, variant))
            ok += 1
    print(f"导入 {ok} 条；待填写 {pending} 条；不合格 {bad} 条（改好答卷后重新执行 import 即可）")


def stage_status(args) -> None:
    _, answers_dir, key_path = paths(args)
    if not key_path.exists():
        print("还没有评审包，先执行 export")
        return
    key = json.loads(key_path.read_text("utf-8"))
    done = {(j["sample_id"], j["variant"]) for j in ra.load_jsonl(args.dir / "judge.jsonl")}
    todo = []
    for pid, info in sorted(key.items()):
        left = [L for L, m in info["candidates"].items() if (m["sample_id"], m["variant"]) not in done]
        if left:
            todo.append(f"{pid}（剩 {len(left)} 个候选）")
    print(f"评审包 {len(key)} 个，未完成 {len(todo)} 个")
    for t in todo[: args.limit]:
        print("  " + t)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["export", "import", "status"])
    ap.add_argument("--run", default="m2")
    ap.add_argument("--max-chars", type=int, default=45000, help="每个评审包的总字数上限（原文 + 候选）")
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--anchor", action="store_true", help="每篇加一个匿名原文作锚点候选（original@anchor）")
    args = ap.parse_args()
    args.dir = ra.BENCH / "results" / args.run
    args.only = None
    global RUN_CFG
    RUN_CFG = ra.load_run_config(args.dir)
    {"export": stage_export, "import": stage_import, "status": stage_status}[args.stage](args)


if __name__ == "__main__":
    main()
