"""Collect native model replies for the no-ai-fluff benchmark corpus.

Usage (OPENROUTER_API_KEY must be set in the environment; never hard-code it):
    python benchmarks/corpus/collect_generated.py            # call API, write generated_raw.jsonl
    python benchmarks/corpus/collect_generated.py --only teaching-001

Only the user task is sent (no system prompt, no style instructions), so the
replies keep each vendor's native "AI voice". Raw replies plus token usage /
cost are appended to generated_raw.jsonl next to this script; samples.jsonl is
assembled from that file by build_samples.py.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://openrouter.ai/api/v1/chat/completions"
HERE = Path(__file__).resolve().parent
RAW_PATH = HERE / "generated_raw.jsonl"

# Vendor rotation: Anthropic / OpenAI / Google / DeepSeek.
CLAUDE = "anthropic/claude-sonnet-5.5"
GPT = "openai/gpt-5.5"
GEMINI = "google/gemini-3.1-pro-preview"
DEEPSEEK = "deepseek/deepseek-v3.2"

# Reasoning models spend part of max_tokens on hidden reasoning, so they get a
# low reasoning effort and a larger ceiling; visible output stays ~1200 tokens.
REASONING_MODELS = {GPT, GEMINI}
MAX_TOKENS = 1200
MAX_TOKENS_REASONING = 2400

TASKS = [
    # ---- teaching: STEM concept explanations ----
    ("teaching-001", "teaching", CLAUDE, "流形 manifold",
     "流形（manifold）到底是什么？我在看 diffusion model 的论文，里面老说 data lies on a low-dimensional manifold，这句话具体该怎么理解？"),
    ("teaching-002", "teaching", GPT, "特征值与 PCA",
     "特征值和特征向量的几何意义是什么？为什么 PCA 要对协方差矩阵做 eigendecomposition？顺便说下为什么协方差矩阵一定是半正定的。"),
    ("teaching-003", "teaching", GEMINI, "协程与线程",
     "Python 的 coroutine 和 thread 本质区别是什么？asyncio 的 event loop 到底是怎么调度协程的？"),
    ("teaching-004", "teaching", DEEPSEEK, "内存屏障",
     "解释一下 memory barrier。为什么 x86 上很多时候不用显式加 fence，ARM 上却必须加？它跟 C++ 的 memory_order 是怎么对应的？"),
    ("teaching-005", "teaching", CLAUDE, "Go 逃逸分析",
     "Go 的逃逸分析（escape analysis）是怎么工作的？什么情况下变量会逃逸到堆上？对性能影响到底有多大？"),
    # ---- writing: plans, reports, external announcements ----
    ("writing-001", "writing", GPT, "微服务拆分技术规划",
     "帮我写一份团队 Q3 的技术规划方案：把现有的单体 Java 服务逐步拆成微服务，同时引入可观测性体系（tracing、metrics、logging）。这份是给技术总监汇报用的。"),
    ("writing-002", "writing", GEMINI, "数据平台半年汇报",
     "我们是一个 10 人的数据平台团队，帮我写一份上半年工作汇报，内容包括：数仓迁移到 lakehouse 架构、实时数仓搭建、数据质量监控平台上线。"),
    ("writing-003", "writing", DEEPSEEK, "对外故障说明",
     "我们的 SaaS 产品昨天出现了大约 2 小时的服务中断，原因是数据库主从切换失败。帮我写一份对外发布的故障说明（incident report），给客户看的。"),
    ("writing-004", "writing", CLAUDE, "AI 编程助手推广方案",
     "帮我写一份内部 AI 编程助手落地推广方案，目标是在研发团队推广 Cursor / Claude Code 这类工具，需要包含目标、实施路径、风险和衡量指标。"),
    ("writing-005", "writing", GPT, "开放平台 API v2 发布说明",
     "写一份开放平台 API v2 的对外发布说明。主要变化：鉴权从 API Key 改为 OAuth 2.0；新增 webhook 事件订阅；限流策略调整为按应用维度；v1 将在 6 个月后下线。"),
    # ---- discussion: architecture trade-offs, technical direction ----
    ("discussion-001", "discussion", GEMINI, "创业团队单体 vs 微服务",
     "单体 vs 微服务，对于一个 20 人左右的创业团队，你怎么看这个 trade-off？"),
    ("discussion-002", "discussion", DEEPSEEK, "RAG vs 长上下文",
     "做知识库问答的话，自己搭向量数据库 + 检索 pipeline（RAG），和直接用长上下文模型把文档全塞进去，哪条路线更有前途？"),
    ("discussion-003", "discussion", CLAUDE, "前端状态管理选型",
     "前端状态管理现在 Redux、Zustand、React Query、signals 一大堆，到底该怎么选？它们背后的设计哲学差异是什么？"),
    ("discussion-004", "discussion", GPT, "Rust 重写存储引擎",
     "我们是做存储引擎的，现有代码是 C++。用 Rust 重写值不值得？"),
    ("discussion-005", "discussion", GEMINI, "PostgreSQL 一把梭 vs 专用数据库",
     "数据库选型上，PostgreSQL 一把梭 vs 针对场景用专门的数据库（Elasticsearch、ClickHouse、Redis 等），你怎么看？"),
]


def call(model: str, prompt: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS_REASONING if model in REASONING_MODELS else MAX_TOKENS,
        "usage": {"include": True},
    }
    if model in REASONING_MODELS:
        body["reasoning"] = {"effort": "low", "exclude": True}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "X-Title": "no-ai-fluff corpus",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    total_cost = 0.0
    for tid, scenario, model, title, prompt in TASKS:
        if only and tid not in only:
            continue
        for attempt in range(3):
            try:
                data = call(model, prompt)
                break
            except urllib.error.HTTPError as e:
                print(f"{tid}: HTTP {e.code} {e.read()[:300]!r}", file=sys.stderr)
                time.sleep(5)
        else:
            continue
        choice = data["choices"][0]
        usage = data.get("usage", {})
        total_cost += usage.get("cost") or 0.0
        rec = {
            "id": tid, "scenario": scenario, "model": data.get("model", model),
            "requested_model": model, "title": title, "prompt": prompt,
            "text": choice["message"].get("content") or "",
            "finish_reason": choice.get("finish_reason"), "usage": usage,
            "collected_at": time.strftime("%Y-%m-%d"),
        }
        with RAW_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{tid} {model} chars={len(rec['text'])} finish={rec['finish_reason']} cost={usage.get('cost')}")
    print(f"total cost (USD): {total_cost:.4f}")


if __name__ == "__main__":
    main()
