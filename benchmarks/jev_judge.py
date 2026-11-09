"""JEV 评委（Reward Model）：OpenRouter `typesafe/jev-1.14` 决策接口的封装、校准与打分。

    python benchmarks/jev_judge.py calibrate                 # 用人工盲测与 GPT-6 双轴评审回测，写 results/jev_calib/
    python benchmarks/jev_judge.py score --run <run>         # 给某轮的候选打分，写 results/<run>/jev.jsonl 与 jev_summary.json

设计（交接文档第 5 节）
- 接口：POST https://openrouter.ai/api/alpha/decisions，不是对话接口。模型名固定写 `typesafe/jev-1.14`，不用别名，
  避免中途升级导致分数尺度漂移。
- 每个请求体以 sha256 为键缓存到 benchmarks/.cache/jev/，缓存文件里同时保存请求与原始返回（审计用）。
  同一请求再跑零成本；改了问题措辞就会产生新请求。
- 三组问题：
  1. 保真风险（监控项）：state = {original, candidate}，6 个 noul，risk = 1 − Π(1 − p_i)；
  2. 易读度：state = {text: candidate}，不给原文（与人工盲测一致），一个 5 级 score + 一个“仍有 AI 腔”noul；
  3. 两两偏好：state = {A, B}，choice，正反各问一次取平均，消除位置偏置。
- 问题措辞有英文、中文两套（--lang en|zh），校准时都试，选与人工一致率高的那套。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.14"
JEV_CACHE = ra.CACHE / "jev"
MAX_STATE_CHARS = 40000  # 约 3 万 token 以内（交接文档 5.1：单次请求尽量控制在 32K）

_cost_lock = threading.Lock()
SPENT = {"usd": 0.0, "calls": 0, "cached": 0}

# ---------------------------------------------------------------------------
# 问题集
# ---------------------------------------------------------------------------

FIDELITY_Q = {
    "en": {
        "number": ("Compare `candidate` with `original`. Does `candidate` change any number, unit, quantity, magnitude, "
                   "percentage, date or version (including dropping or adding one)?",
                   "Yes: at least one number, unit or magnitude differs from `original`.",
                   "No: all numbers and units are the same; rewording or moving them is fine."),
        "hedge": ("Compare `candidate` with `original`. Does `candidate` change how strong a claim, recommendation or "
                  "condition is? Examples: 'try to' / 'usually' / 'recommended' becoming a hard rule or the reverse; "
                  "'only when' becoming 'usually when'; a boundary such as 'within' becoming 'between'.",
                  "Yes: the strength of at least one statement or condition changed.",
                  "No: every claim keeps the same strength and the same conditions."),
        "term": ("Compare `candidate` with `original`. Does `candidate` turn a technical term, product name or proper "
                 "noun into something that refers to a different thing (for example 'O&M cost' becoming 'operating "
                 "cost', or 'zero-copy parsing' becoming 'zero-copy analysis')? Translating a term into a correct "
                 "equivalent, or keeping it in English, does not count.",
                 "Yes: at least one term now refers to a different thing.",
                 "No: every term still refers to the same thing."),
        "drop": ("Compare `candidate` with `original`. Does `candidate` drop information a reader needs — a fact, "
                 "step, condition, caveat, example, item or heading that carries content? Removing filler, slogans, "
                 "repeated sentences and empty phrases does not count.",
                 "Yes: some needed information from `original` is missing.",
                 "No: all needed information is still there."),
        "add": ("Compare `candidate` with `original`. Does `candidate` add a claim, fact, number, recommendation or "
                "conclusion that `original` does not contain?",
                "Yes: `candidate` asserts something new.",
                "No: `candidate` only says what `original` says."),
        "role": ("Compare `candidate` with `original`. Does `candidate` swap who does what to whom, reverse a cause and "
                 "its effect, or make a reference ambiguous in a way that would mislead the reader?",
                 "Yes: an actor, object, cause or reference is now wrong or misleading.",
                 "No: actors, objects, causes and references are the same."),
    },
    "zh": {
        "number": ("对照 `original` 看 `candidate`：数字、单位、数量、量级、百分比、日期或版本号有没有被改动（包括漏掉或多出）？",
                   "有：至少一处数字、单位或量级与原文不同。", "没有：数字和单位都一致；换说法或挪位置不算。"),
        "hedge": ("对照 `original` 看 `candidate`：说法、建议或条件的强弱有没有变？例如“尽量 / 通常 / 建议”变成硬性要求或反过来，"
                  "“仅在……时”变成“通常在……时”，“以内”变成“之间”。",
                  "有：至少一处说法或条件的强弱变了。", "没有：每句话的强弱和条件都与原文一致。"),
        "term": ("对照 `original` 看 `candidate`：有没有把技术术语、产品名或专有名词改成了指另一种东西的词"
                 "（例如“运维成本”变成“运营成本”，“零拷贝解析”变成“零拷贝分析”）？译成意思相同的正确中文、或保留英文，都不算。",
                 "有：至少一个术语现在指的是另一种东西。", "没有：所有术语指的还是同一个东西。"),
        "drop": ("对照 `original` 看 `candidate`：有没有丢掉读者需要的信息——事实、步骤、条件、注意事项、例子、条目或有内容的小标题？"
                 "删掉套话、口号、重复的句子和空洞修饰不算。",
                 "有：原文里读者需要的某些信息不见了。", "没有：需要的信息都还在。"),
        "add": ("对照 `original` 看 `candidate`：有没有添加原文里没有的说法、事实、数字、建议或结论？",
                "有：`candidate` 说了原文没说的东西。", "没有：`candidate` 只说了原文说过的内容。"),
        "role": ("对照 `original` 看 `candidate`：有没有把谁对谁做了什么弄反、把因果弄反，或者让指代变得会误导读者？",
                 "有：动作主体、对象、因果或指代错了或会误导。", "没有：主体、对象、因果和指代都与原文一致。"),
    },
}

READ_LEVELS = {
    "en": ["1 = very hard to understand.",
           "2 = in many places the reader must reread or fill in the subject or the logic.",
           "3 = understandable, but with obvious AI tone, translationese or compressed phrasing; tiring to read.",
           "4 = mostly natural, occasionally stiff.",
           "5 = reads like an experienced colleague explaining it in person; clear on first read."],
    "zh": ["1 = 很难读懂。",
           "2 = 多处需要回头重读，或自己补主语、补逻辑。",
           "3 = 能读懂，但有明显的 AI 腔、翻译腔或压缩感，读着累。",
           "4 = 大体自然，偶有生硬的地方。",
           "5 = 像有经验的人当面讲给你听，一遍读懂，不需要回头重读。"],
}
READ_INSTR = {
    "en": ("`text` is a Chinese passage. How easy and natural is it to read? Imagine an experienced colleague explaining "
           "this in person: complete sentences with subjects and verbs, connectives such as 'because' and 'so' kept, "
           "saying directly who did what, no slogans or buzzwords. Reading well is NOT the same as being dense or "
           "short: sentences compressed into noun phrases that make the reader fill in the gaps read worse. Code, "
           "formulas and technical terms are fine and should not lower the score."),
    "zh": ("`text` 是一段中文。它读起来有多自然、多省力？想象一位有经验的同事当面跟你讲这件事：句子完整，主语动词齐全，"
           "“因为、所以”这类连接词不省，直接说谁做了什么，不用口号和黑话。好读不等于信息密度高或字数少："
           "把句子压成名词短语、让读者自己脑补的写法更累。代码、公式和技术术语本身不扣分。"),
}
AI_TONE = {
    "en": ("`text` is a Chinese passage. Does it still have obvious AI tone or translationese? Signs: sentences "
           "compressed into noun phrases, slogans or buzzwords (such as 赋能, 闭环, 抓手), empty hype words, "
           "unnecessary English mixed into Chinese, long chains of modifiers, stiff literal-translation phrasing, "
           "filler openings and lofty closing lines.",
           "Yes: a reader would clearly notice AI tone or translationese.",
           "No: it reads like natural writing by a person."),
    "zh": ("`text` 是一段中文。它是否仍有明显的 AI 腔或翻译腔？表现包括：把句子压成名词短语，口号和黑话（赋能、闭环、抓手），"
           "空洞的夸张修饰，不必要的中英夹杂，长串定语，生硬的直译搭配，开头铺垫和结尾升华。",
           "是：读者能明显感到 AI 腔或翻译腔。", "否：读起来像人自然写的。"),
}
PAIR_INSTR = {
    "en": ("`A` and `B` are two Chinese versions of the same content. Which one is easier and more natural to read — "
           "closer to how an experienced colleague would explain it in person, with complete sentences and no AI tone, "
           "buzzwords or translationese? Judge readability only, not length or level of detail."),
    "zh": ("`A` 和 `B` 是同一内容的两个中文版本。哪一个读起来更自然、更省力——更接近有经验的同事当面讲话的方式，"
           "句子完整，没有 AI 腔、黑话和翻译腔？只比较好不好读，不比较长短或详略。"),
}
PAIR_CRIT = {"en": {"A": "A reads better.", "B": "B reads better."},
             "zh": {"A": "A 更好读。", "B": "B 更好读。"}}

# 校准第 2 版（style="human"）：照搬人工盲测页面给测试者的指令，并像盲测页面一样附上当时的提问。
# 第 1 版（style="rubric"）的问题措辞带着“AI 腔 / 翻译腔”清单，与人工两两偏好一致率只有 41%~50%。
HUMAN_PAIR_INSTR = {
    "en": ("`question` is what the user asked; `A` and `B` are two versions of the answer. Go by how they feel to "
           "read: which version sounds more like a real person talking and is less tiring to read? Do not hunt for "
           "errors word by word."),
    "zh": ("`question` 是用户当时的提问，`A` 和 `B` 是回答的两个版本。凭读起来的感觉选：哪一版更像人话、读着更不累？"
           "不用逐字找错。"),
}
HUMAN_READ_INSTR = {
    "en": ("`question` is what the user asked; `text` is the answer. Go by how it feels to read: how much does it "
           "sound like a real person talking, and how tiring is it to read? Do not hunt for errors word by word."),
    "zh": ("`question` 是用户当时的提问，`text` 是回答。凭读起来的感觉打分：它有多像人话、读着累不累？不用逐字找错。"),
}
HUMAN_READ_LEVELS = {
    "en": ["1 = very tiring; does not sound like a person at all.", "2 = tiring in many places.",
           "3 = readable but clearly machine-like or stiff.", "4 = mostly sounds like a person, a few stiff spots.",
           "5 = sounds like a real person talking; effortless to read."],
    "zh": ["1 = 读着很累，完全不像人话。", "2 = 很多地方读着累。", "3 = 能读，但明显像机器写的或很生硬。",
           "4 = 大体像人话，偶有生硬。", "5 = 就像真人在说话，读着毫不费力。"],
}


def fidelity_questions(lang: str) -> dict:
    return {k: {"type": "noul", "instructions": q, "criteria": {"true": t, "false": f}}
            for k, (q, t, f) in FIDELITY_Q[lang].items()}


def read_questions(lang: str) -> dict:
    q, t, f = AI_TONE[lang]
    return {"read": {"type": "score", "instructions": READ_INSTR[lang], "criteria": READ_LEVELS[lang]},
            "ai_tone": {"type": "noul", "instructions": q, "criteria": {"true": t, "false": f}}}


def pair_questions(lang: str) -> dict:
    return {"better": {"type": "choice", "instructions": PAIR_INSTR[lang], "criteria": PAIR_CRIT[lang]}}


# ---------------------------------------------------------------------------
# 接口调用（带缓存）
# ---------------------------------------------------------------------------


def _key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def jev(state, questions: dict) -> dict:
    """调用 JEV；返回原始响应。缓存命中不花钱。"""
    body = {"model": JEV_MODEL, "state": state, "questions": questions}
    size = len(json.dumps(state, ensure_ascii=False))
    if size > MAX_STATE_CHARS:
        raise ValueError(f"state 过长（{size} 字符），请拆段评")
    k = _key(body)
    path = JEV_CACHE / k[:2] / f"{k}.json"
    if path.exists():
        with _cost_lock:
            SPENT["cached"] += 1
        return json.loads(path.read_text("utf-8"))["response"]
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(6):
        req = urllib.request.Request(JEV_URL, data=data, headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:300]!r}"
            if e.code not in (408, 409, 429, 500, 502, 503, 504):
                raise RuntimeError(last) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = repr(e)
        time.sleep(min(60, 2 ** attempt + random.random()))
    else:
        raise RuntimeError(f"JEV 调用失败：{last}")
    if resp.get("model", "").split("-2026")[0] != JEV_MODEL:
        raise RuntimeError(f"JEV 返回的模型不是 {JEV_MODEL}：{resp.get('model')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"request": body, "response": resp, "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                               ensure_ascii=False), "utf-8")
    with _cost_lock:
        SPENT["usd"] += (resp.get("usage") or {}).get("cost") or 0.0
        SPENT["calls"] += 1
    return resp


def _ans(resp: dict, name: str) -> dict:
    a = (resp.get("answers") or {}).get(name) or {}
    if "refusal" in a or a.get("type") == "refusal":
        return {}
    return a


def fidelity(original: str, candidate: str, lang: str) -> dict:
    resp = jev({"original": original, "candidate": candidate}, fidelity_questions(lang))
    ps = {k: _ans(resp, k).get("noul") for k in FIDELITY_Q[lang]}
    vals = [p for p in ps.values() if p is not None]
    risk = 1 - math.prod(1 - p for p in vals) if vals else None
    return {"risk": risk, **{f"p_{k}": v for k, v in ps.items()}}


def readability(text: str, lang: str, style: str = "rubric", question: str | None = None) -> dict:
    """易读度期望分（1~5）与 AI 腔概率。ai_tone 一律用 rubric 版问题（监控项）；read 按 style 选问题。"""
    resp = jev({"text": text}, read_questions(lang))
    sc = _ans(resp, "read").get("score")
    if style == "human":
        hq = {"read": {"type": "score", "instructions": HUMAN_READ_INSTR[lang], "criteria": HUMAN_READ_LEVELS[lang]}}
        sc = _ans(jev({"question": question or "", "text": text}, hq), "read").get("score")
    return {"read": None if sc is None else sc + 1, "ai_tone": _ans(resp, "ai_tone").get("noul")}


def prefer(a: str, b: str, lang: str, style: str = "rubric", question: str | None = None) -> float | None:
    """P(a 比 b 好读)，正反各问一次取平均。"""
    if style == "human":
        qs = {"better": {"type": "choice", "instructions": HUMAN_PAIR_INSTR[lang], "criteria": PAIR_CRIT[lang]}}
        s1, s2 = {"question": question or "", "A": a, "B": b}, {"question": question or "", "A": b, "B": a}
    else:
        qs = pair_questions(lang)
        s1, s2 = {"A": a, "B": b}, {"A": b, "B": a}
    r1 = _ans(jev(s1, qs), "better").get("probabilities") or {}
    r2 = _ans(jev(s2, qs), "better").get("probabilities") or {}
    if "A" not in r1 or "B" not in r2:
        return None
    return (r1["A"] + r2["B"]) / 2


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def auc(scores_pos: list[float], scores_neg: list[float]) -> float | None:
    if not scores_pos or not scores_neg:
        return None
    win = 0.0
    for p in scores_pos:
        for n in scores_neg:
            win += 1.0 if p > n else 0.5 if p == n else 0.0
    return win / (len(scores_pos) * len(scores_neg))


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def pmap(fn, items, workers: int = 8) -> list:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


# ---------------------------------------------------------------------------
# 数据：各轮候选文本
# ---------------------------------------------------------------------------

SPLITS = ra.BENCH / "splits"


def train_ids() -> set[str]:
    return set(json.loads((SPLITS / "train.json").read_text("utf-8"))["ids"])


def run_texts(run: str) -> dict[tuple[str, str], str]:
    """{(sample_id, variant): 文本}，与当时评审 / 盲测所用的文件一致（经 run_ablation.variants_for 解析）。"""
    d = ra.BENCH / "results" / run
    ra.load_run_config(d)
    ra.JUDGE_VARIANTS = None  # 解析全部版本，由调用方按需要挑
    rtt, patch = defaultdict(list), defaultdict(list)
    for r in ra.load_jsonl(d / "rtt.jsonl"):
        rtt[r["sample_id"]].append(r)
    for r in ra.load_jsonl(d / "patch.jsonl"):
        patch[r["sample_id"]].append(r)
    base = ra.load_baselines(d)
    out = {}
    ra.RUN_SAMPLES = None  # 取全部样本；再按需要过滤
    smap = {s["id"]: s for s in ra.load_jsonl(ra.SAMPLES)}
    for sid, s in smap.items():
        for name, text in ra.variants_for(s, rtt[sid], patch[sid], base[sid]):
            out[(sid, name)] = text
        out[(sid, "original@anchor")] = s["text"]
    ra.JUDGE_VARIANTS = None
    return out


# 第 1.2 节：盲测页面渲染器曾把“空行隔开的编号列表”全显示为“1.”，受影响的两张票都把 Humanizer-zh 选为最难读
DIRTY = {("m4", "discussion-002"), ("hard1", "hard-001")}


# 盲测页面里实际展示给测试者的文本（页面内嵌数据 + 对照表解码）。m3 的页面后来被 m4 覆盖，从 git 历史取回。
BLIND_HTML = {"m3": ("023b2f5", "benchmarks/blind_test_workbench.html"),
              "m4": ("e9abac6", "benchmarks/blind_test_workbench.html"),
              "hard1": (None, "benchmarks/results/hard1/blind_test_hard1.html"),
              "calib2": (None, "benchmarks/results/calib2/blind_test_calib2.html")}
HUMAN_RUNS = ("m3", "m4", "hard1", "calib2")


def blind_texts(run: str) -> dict[tuple[str, str], str]:
    import subprocess
    commit, rel = BLIND_HTML[run]
    if commit:
        html = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=ra.ROOT, capture_output=True,
                              check=True).stdout.decode("utf-8")
    else:
        html = (ra.ROOT / rel).read_text("utf-8")
    data = json.JSONDecoder().raw_decode(html[html.index('{"run"'):])[0]
    assert data["run"] == run, (data["run"], run)
    key = json.loads((ra.CACHE / "blind" / f"key-{run}.json").read_text("utf-8"))["codes"]
    out = {}
    for it in data["items"]:
        for c in it["cands"]:
            k = key[c["code"]]
            for var in [k["variant"]] + k.get("same_as", []):
                out[(k["sample_id"], var)] = c["text"].replace("<\\/", "</")
    return out


def human_pairs() -> list[dict]:
    """把人工盲测展开成两两偏好：最好读 > 其余版本 > 最难读。文本取自盲测页面本身。"""
    pairs = []
    for run in HUMAN_RUNS:
        vp = ra.BENCH / "results" / run / "blind_votes.json"
        if not vp.exists():
            continue
        votes = json.loads(vp.read_text("utf-8"))["decoded"]
        texts = blind_texts(run)
        shown = defaultdict(list)
        for sid, var in texts:
            shown[sid].append(var)
        for v in votes:
            if v.get("tie") or not v.get("best"):
                continue
            sid = v["sample_id"]
            order = sorted(shown[sid])
            missing = [x for x in order if (sid, x) not in texts]
            if missing:
                print(f"  警告：{run} {sid} 缺少文本 {missing}", file=sys.stderr)
            # 逐字相同的版本（same_as）共用一个代号：它们之间不构成偏好对
            best_g = {v["best"], *v.get("best_same_as", [])}
            worst_g = {v["worst"], *v.get("worst_same_as", [])} if v.get("worst") else set()
            seen = set()
            for b in best_g:
                for o in order:
                    if o not in best_g and (sid, o) in texts:
                        seen.add((b, o))
            for wv in worst_g:
                for o in order:
                    if o not in best_g | worst_g and (sid, o) in texts:
                        seen.add((o, wv))
            for w, l in sorted(seen):
                pairs.append({"run": run, "sample_id": sid, "winner": w, "loser": l,
                              "winner_text": texts[(sid, w)], "loser_text": texts[(sid, l)],
                              "kind": "best" if w in best_g else "worst",
                              "dirty": (run, sid) in DIRTY and "baseline:humanizer-zh" == l and l == v.get("worst")})
    return pairs


def gpt6_rows(train: set[str]) -> list[dict]:
    rows = []
    for run in ("m2", "m2v2", "m2v2hi", "m3judge", "m4"):
        texts = run_texts(run)
        for r in ra.load_jsonl(ra.BENCH / "results" / run / "judge.jsonl"):
            if r["sample_id"] not in train:
                continue
            t = texts.get((r["sample_id"], r["variant"]))
            if t is None:
                continue
            rows.append({"run": run, "sample_id": r["sample_id"], "variant": r["variant"],
                         "fidelity": int(r["fidelity"]), "readability": int(r["readability"]), "text": t})
    return rows


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


def calibrate(args) -> None:
    train = train_ids()
    smap = {s["id"]: s for s in ra.load_jsonl(ra.SAMPLES)}
    out_dir = ra.BENCH / "results" / "jev_calib"
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = human_pairs()
    g6 = gpt6_rows(train)
    print(f"人工两两偏好 {len(pairs)} 对（可疑 {sum(p['dirty'] for p in pairs)}）；GPT-6 评审 {len(g6)} 条（仅 Train）", flush=True)
    report: dict = {"model": JEV_MODEL, "date": time.strftime("%Y-%m-%d"), "n_pairs": len(pairs),
                    "n_gpt6": len(g6), "langs": {}}
    for lang, style in [(lg, st) for lg in args.langs for st in args.styles]:
        tag = f"{lang}-{style}"
        print(f"== {tag}", flush=True)

        # 1) 人工两两偏好
        def do_pair(p):
            q = smap[p["sample_id"]].get("prompt")
            pw = prefer(p["winner_text"], p["loser_text"], lang, style, q)
            rw = readability(p["winner_text"], lang, style, q)
            rl = readability(p["loser_text"], lang, style, q)
            return {**{k: p[k] for k in ("run", "sample_id", "winner", "loser", "kind", "dirty")},
                    "p_pair": pw, "read_w": rw["read"], "read_l": rl["read"],
                    "ai_w": rw["ai_tone"], "ai_l": rl["ai_tone"]}
        pr = pmap(do_pair, pairs, args.workers)
        res: dict = {}
        for subset, rows in (("all", pr), ("clean", [r for r in pr if not r["dirty"]]),
                             ("calib2", [r for r in pr if r["run"] == "calib2"]),
                             ("legacy_clean", [r for r in pr if r["run"] != "calib2" and not r["dirty"]])):
            if not rows:
                continue
            n = len(rows)
            agree_pair = sum(1.0 if r["p_pair"] > 0.5 else 0.5 if r["p_pair"] == 0.5 else 0 for r in rows)
            agree_read = sum(1.0 if r["read_w"] > r["read_l"] else 0.5 if r["read_w"] == r["read_l"] else 0 for r in rows)
            agree_ai = sum(1.0 if r["ai_w"] < r["ai_l"] else 0.5 if r["ai_w"] == r["ai_l"] else 0 for r in rows)
            # 组合：易读期望分差 + AI 腔差 + 两两偏好，各自标准化后同号投票的简单平均
            agree_combo = sum(
                1.0 if (r["p_pair"] - 0.5) + (r["read_w"] - r["read_l"]) / 4 - (r["ai_w"] - r["ai_l"]) / 2 > 0 else 0
                for r in rows)
            res[subset] = {
                "n": n,
                **{name: {"rate": k / n if n else None, "ci95": wilson(k, n)}
                   for name, k in (("pair_choice", agree_pair), ("read_score", agree_read),
                                   ("ai_tone", agree_ai), ("combo", agree_combo))},
            }
        # 2) GPT-6：保真风险 AUC、易读度 Spearman
        def do_g6(r):
            f = fidelity(smap[r["sample_id"]]["text"], r["text"], lang)
            rd = readability(r["text"], lang, style, smap[r["sample_id"]].get("prompt"))
            return {**{k: r[k] for k in ("run", "sample_id", "variant", "fidelity", "readability")}, **f, **rd}
        gr = pmap(do_g6, g6, args.workers)
        prod = [r for r in gr if not r["variant"].startswith("rtt:")]
        aucs = {}
        for name, rows in (("all", gr), ("non_rtt", prod)):
            pos = [r["risk"] for r in rows if r["fidelity"] <= 2 and r["risk"] is not None]
            neg = [r["risk"] for r in rows if r["fidelity"] > 2 and r["risk"] is not None]
            aucs[name] = {"auc": auc(pos, neg), "n_pos": len(pos), "n_neg": len(neg)}
            for k in FIDELITY_Q[lang]:
                pk = [r[f"p_{k}"] for r in rows if r["fidelity"] <= 2 and r[f"p_{k}"] is not None]
                nk = [r[f"p_{k}"] for r in rows if r["fidelity"] > 2 and r[f"p_{k}"] is not None]
                aucs[name][f"auc_{k}"] = auc(pk, nk)
        sp = {}
        for run in ("m2", "m2v2", "m3judge", "m4"):
            rows = [r for r in gr if r["run"] == run and r["read"] is not None]
            sp[run] = {"read": spearman([r["read"] for r in rows], [r["readability"] for r in rows]),
                       "ai_tone": spearman([-r["ai_tone"] for r in rows], [r["readability"] for r in rows]),
                       "n": len(rows)}
        report["langs"][tag] = {"human": res, "fidelity_auc": aucs, "readability_spearman": sp}
        (out_dir / f"pairs_{tag}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in pr), "utf-8")
        (out_dir / f"gpt6_{tag}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in gr), "utf-8")
        print(json.dumps(report["langs"][tag]["human"], ensure_ascii=False), flush=True)
    report["spent"] = dict(SPENT)
    (out_dir / "calib.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"JEV 花费 {SPENT['usd']:.4f} 美元，新调用 {SPENT['calls']} 次，缓存命中 {SPENT['cached']} 次 -> {out_dir}")


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def score(args) -> None:
    d = ra.BENCH / "results" / args.run
    cfg = ra.load_run_config(d)
    texts = run_texts(args.run)
    sids = cfg.get("samples") or sorted({k[0] for k in texts})
    smap = {s["id"]: s for s in ra.load_jsonl(ra.SAMPLES)}
    ours, base = args.ours, args.baseline
    lang, style = args.lang, args.style

    def one(sid):
        s = smap[sid]
        q = s.get("prompt")
        rec = {"sample_id": sid, "scenario": s["scenario"]}
        for name in ("original", ours, base):
            t = texts.get((sid, name))
            if t is None:
                continue
            tag = name.split(":")[0] if name != ours else "ours"
            tag = {"original": "orig", "baseline": "base"}.get(tag, tag)
            rec[f"{tag}_read"] = readability(t, lang, style, q)
            if name != "original":
                rec[f"{tag}_fid"] = fidelity(s["text"], t, lang)
        if (sid, ours) in texts:
            rec["p_ours_vs_orig"] = prefer(texts[(sid, ours)], s["text"], lang, style, q)
            if (sid, base) in texts:
                rec["p_ours_vs_base"] = prefer(texts[(sid, ours)], texts[(sid, base)], lang, style, q)
        if (sid, base) in texts:
            rec["p_base_vs_orig"] = prefer(texts[(sid, base)], s["text"], lang, style, q)
        return rec

    rows = pmap(one, sids, args.workers)
    out = d / f"jev{args.suffix}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    summary = summarize(rows)
    summary.update({"run": args.run, "ours": ours, "baseline": base, "lang": lang, "style": style, "model": JEV_MODEL,
                    "spent": dict(SPENT)})
    (d / f"jev_summary{args.suffix}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def summarize(rows: list[dict]) -> dict:
    def agg(rs):
        m = {
            "n": len(rs),
            "win_vs_base": _mean([r.get("p_ours_vs_base") for r in rs]),
            "win_vs_orig": _mean([r.get("p_ours_vs_orig") for r in rs]),
            "base_win_vs_orig": _mean([r.get("p_base_vs_orig") for r in rs]),
            "read_ours": _mean([(r.get("ours_read") or {}).get("read") for r in rs]),
            "read_base": _mean([(r.get("base_read") or {}).get("read") for r in rs]),
            "read_orig": _mean([(r.get("orig_read") or {}).get("read") for r in rs]),
            "ai_ours": _mean([(r.get("ours_read") or {}).get("ai_tone") for r in rs]),
            "ai_base": _mean([(r.get("base_read") or {}).get("ai_tone") for r in rs]),
            "ai_orig": _mean([(r.get("orig_read") or {}).get("ai_tone") for r in rs]),
            "risk_ours": _mean([(r.get("ours_fid") or {}).get("risk") for r in rs]),
            "risk_base": _mean([(r.get("base_fid") or {}).get("risk") for r in rs]),
        }
        m["reward"] = reward(m)
        return m
    by = defaultdict(list)
    for r in rows:
        by[r["scenario"]].append(r)
    return {"overall": agg(rows), "by_scenario": {k: agg(v) for k, v in sorted(by.items())}}


def reward(m: dict) -> float | None:
    """综合奖励（第 7 节）：相对原文的去 AI 腔提升 + 相对 Humanizer-zh 的去偏胜率 + 易读度期望分。

    三项都缩放到 0~1 附近后等权平均：
    - 去 AI 腔：P(ours 比原文好读)（两两、正反换位）；
    - 胜率：P(ours 比 Humanizer-zh 好读)；
    - 易读度：(read_ours − 1) / 4。
    """
    parts = [m.get("win_vs_orig"), m.get("win_vs_base"),
             None if m.get("read_ours") is None else (m["read_ours"] - 1) / 4]
    parts = [p for p in parts if p is not None]
    return round(sum(parts) / len(parts), 4) if parts else None


def main() -> None:
    ra.load_dotenv()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--langs", nargs="*", default=["en", "zh"])
    c.add_argument("--styles", nargs="*", default=["rubric", "human"], choices=["rubric", "human"])
    c.add_argument("--workers", type=int, default=8)
    s = sub.add_parser("score")
    s.add_argument("--run", required=True)
    s.add_argument("--ours", default="patched:hymt-ru")
    s.add_argument("--baseline", default="baseline:humanizer-zh")
    s.add_argument("--lang", default="zh", choices=["en", "zh"])
    s.add_argument("--style", default="rubric", choices=["rubric", "human"])
    s.add_argument("--suffix", default="")
    s.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    {"calibrate": calibrate, "score": score}[args.cmd](args)


if __name__ == "__main__":
    main()
