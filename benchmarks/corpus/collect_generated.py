"""Collect native model replies for the no-ai-fluff benchmark corpus.

Usage (OPENROUTER_API_KEY must be set in the environment; never hard-code it):
    python benchmarks/corpus/collect_generated.py            # call API, write generated_raw.jsonl
    python benchmarks/corpus/collect_generated.py --only teaching-001

Only the user task is sent (no system prompt, no style instructions), so the
replies keep each vendor's native "AI voice". Raw replies plus token usage /
cost are appended to generated_raw.jsonl next to this script; samples.jsonl is
assembled from that file (latest record per id) by build_samples.py.
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
# low reasoning effort and a larger ceiling. The first collection run (1200 / 2400)
# truncated 11 of 15 replies (finish_reason=length), so the ceilings are now high
# enough for a complete natural answer; build_samples.py keeps the latest record per id.
REASONING_MODELS = {GPT, GEMINI}
MAX_TOKENS = 8000  # Claude Sonnet 5.5 也会先花约 2k token 推理
MAX_TOKENS_REASONING = 8000

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

    # ==== 2026-11-10 补充语料（交接文档 6.2）：每题只用一个模型作答，按题轮换 Claude / GPT / Gemini / DeepSeek ====
    # 生成后由 benchmarks/splits/make_splits.py 按固定种子分层划入 Dev / Holdout。
    # 本脚本只打印长度与 finish_reason，不打印正文（Holdout 在宣布收敛前不得读取）。
    # ---- teaching ----
    ("teaching-006", "teaching", CLAUDE, "Raft 与 Paxos",
     "Raft 和 Paxos 的核心区别是什么？为什么大家都说 Raft 更好懂？leader election 和 log replication 分别解决什么问题？"),
    ("teaching-007", "teaching", GPT, "KL 散度",
     "什么是 KL 散度？它为什么不对称？VAE 里为什么用 KL divergence 而不是别的距离？"),
    ("teaching-008", "teaching", GEMINI, "HTTPS 握手",
     "简单讲讲 HTTPS 握手过程，TLS 1.3 比 1.2 快在哪里？"),
    ("teaching-009", "teaching", DEEPSEEK, "B+ 树与 LSM-tree",
     "B+ 树为什么适合做数据库索引？跟 LSM-tree 比各有什么优劣？"),
    ("teaching-010", "teaching", CLAUDE, "CAP 定理",
     "解释一下 CAP 定理。现实里大家说的 AP 系统、CP 系统到底是什么意思？"),
    ("teaching-011", "teaching", GPT, "Rust 所有权",
     "Rust 的所有权和借用检查器是怎么防止数据竞争的？用通俗的话讲讲 lifetime。"),
    ("teaching-012", "teaching", GEMINI, "HyperLogLog",
     "HyperLogLog 是怎么用很少的内存估算基数的？误差大概多大？"),
    # ---- writing ----
    ("writing-006", "writing", DEEPSEEK, "后端组长年终述职",
     "帮我写一份年终述职报告。我是后端组长，今年主要做了支付系统重构、稳定性治理（SLA 从 99.9% 提到 99.95%），还带了 3 个新人。"),
    ("writing-007", "writing", CLAUDE, "机房网络割接通知",
     "帮我写一封全员邮件，通知下周六凌晨 2 点到 6 点进行机房网络割接，期间内网系统不可用。"),
    ("writing-008", "writing", GPT, "以图搜商品 PRD 背景",
     "写一份产品需求文档（PRD）的“背景与目标”部分：我们要给电商 App 加一个“以图搜商品”功能。"),
    ("writing-009", "writing", GEMINI, "GPU 算力预算申请",
     "帮我写一份申请 GPU 算力预算的说明。团队要训练一个 7B 参数的领域模型，需要申请 64 张 H100，为期三个月。"),
    ("writing-010", "writing", DEEPSEEK, "CI 提速技术博客",
     "帮我写一篇技术博客的开头和大纲，主题是“我们如何把 CI 构建时间从 40 分钟降到 8 分钟”。"),
    ("writing-011", "writing", CLAUDE, "数据口径变更通知",
     "写一份给业务方的数据口径变更通知：“活跃用户”的定义从“当日登录”改为“当日有有效操作”，下个月 1 号生效。"),
    ("writing-012", "writing", GPT, "Go 限流库 README",
     "帮我写一个开源项目 README 的介绍部分。项目是一个轻量级的 Go 限流库，支持令牌桶和滑动窗口两种算法。"),
    # ---- discussion ----
    ("discussion-006", "discussion", GEMINI, "中小公司与平台工程",
     "大厂都在搞平台工程（platform engineering），中小公司有必要跟吗？"),
    ("discussion-007", "discussion", DEEPSEEK, "代码评审流于形式",
     "代码评审到底该看什么？很多团队的 code review 流于形式，你怎么看？"),
    ("discussion-008", "discussion", CLAUDE, "TDD 是否理想主义",
     "TDD 在实际项目里真的有用吗，还是理想主义？"),
    ("discussion-009", "discussion", GPT, "小团队上 Kubernetes",
     "只有十几个服务的小团队上 Kubernetes，是不是过度设计？"),
    ("discussion-010", "discussion", GEMINI, "GraphQL vs REST",
     "GraphQL 和 REST 怎么选？GraphQL 这几年是不是凉了？"),
    ("discussion-011", "discussion", DEEPSEEK, "AI 时代还刷算法题吗",
     "AI 编程工具普及以后，初级程序员还该花时间刷算法题吗？"),
    ("discussion-012", "discussion", CLAUDE, "Serverless 的适用边界",
     "Serverless 适合做什么、不适合做什么？冷启动问题现在解决了吗？"),
    # ---- mixed：公式 / 代码混排（新场景） ----
    ("mixed-001", "mixed", GPT, "逻辑回归梯度推导",
     "推导一下逻辑回归交叉熵损失对参数的梯度，再给一个 NumPy 实现。"),
    ("mixed-002", "mixed", GEMINI, "Adam 优化器",
     "解释一下 Adam 优化器的更新公式，偏差修正是干什么的？顺便说说 PyTorch 里对应的参数。"),
    ("mixed-003", "mixed", DEEPSEEK, "快速排序复杂度",
     "用公式说明为什么快速排序的平均复杂度是 O(n log n)、最坏是 O(n^2)，再给个 Python 实现。"),
    ("mixed-004", "mixed", CLAUDE, "softmax 数值稳定",
     "softmax 数值溢出怎么处理？给出 log-sum-exp 技巧的推导和代码。"),
    ("mixed-005", "mixed", GPT, "布隆过滤器参数",
     "布隆过滤器的误判率公式是怎么来的？给定元素数 n 和期望误判率，怎么算位数组大小 m 和哈希函数个数 k？给个 Go 示例。"),
    ("mixed-006", "mixed", GEMINI, "RoPE 位置编码",
     "解释一下 RoPE 旋转位置编码的数学原理，再写一段 PyTorch 实现。"),
    ("mixed-007", "mixed", DEEPSEEK, "一维卡尔曼滤波",
     "卡尔曼滤波的预测公式和更新公式分别是什么意思？用一维的例子配 Python 代码讲讲。"),
    ("mixed-008", "mixed", CLAUDE, "EWMA 监控告警",
     "指数加权移动平均（EWMA）在监控告警里怎么用？给出公式和一段 Python 或 SQL 实现。"),
    ("mixed-009", "mixed", GPT, "贝叶斯 A/B 测试",
     "用贝叶斯公式解释一下 A/B 测试里的贝叶斯方法，它跟频率学派的 p 值有什么区别？附代码示例。"),
    # ---- daily ----
    ("daily-013", "daily", GEMINI, "第一次养猫",
     "第一次养猫需要准备什么？"),
    ("daily-014", "daily", DEEPSEEK, "半马朋友圈",
     "帮我写一条朋友圈，庆祝自己跑完第一个半程马拉松。"),
    ("daily-015", "daily", CLAUDE, "室友卫生矛盾",
     "和室友因为卫生问题闹得有点僵，怎么开口谈比较好？"),
    ("daily-016", "daily", GPT, "通勤降噪耳机",
     "预算 3000 想买个降噪耳机，主要通勤用，怎么选？"),
    ("daily-017", "daily", GEMINI, "幼儿园毕业感谢老师",
     "孩子幼儿园毕业了，帮我写一段给老师的感谢话。"),
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


def collected_ok() -> set[str]:
    """已有完整（未截断、非空）记录的 id。默认跳过它们，避免重复花钱。"""
    ok: set[str] = set()
    if RAW_PATH.exists():
        for line in RAW_PATH.read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("finish_reason") != "length" and len((r.get("text") or "").strip()) >= 100:
                    ok.add(r["id"])
    return ok


def collect_one(task: tuple) -> dict | None:
    tid, scenario, model, title, prompt = task
    for _ in range(3):
        try:
            data = call(model, prompt)
            break
        except (urllib.error.URLError, TimeoutError) as e:
            detail = e.read()[:300] if isinstance(e, urllib.error.HTTPError) else e
            print(f"{tid}: {type(e).__name__} {detail!r}", file=sys.stderr)
            time.sleep(5)
    else:
        return None
    choice = data["choices"][0]
    return {
        "id": tid, "scenario": scenario, "model": data.get("model", model),
        "requested_model": model, "title": title, "prompt": prompt,
        "text": choice["message"].get("content") or "",
        "finish_reason": choice.get("finish_reason"), "usage": data.get("usage", {}),
        "collected_at": time.strftime("%Y-%m-%d"),
    }


def main() -> None:
    """默认只采还没有完整记录的题；--only 指定 id 时强制重采；--force 全部重采。"""
    from concurrent.futures import ThreadPoolExecutor

    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    done = set() if ("--force" in sys.argv or only) else collected_ok()
    todo = [t for t in TASKS if (not only or t[0] in only) and t[0] not in done]
    total_cost = 0.0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for rec in ex.map(collect_one, todo):
            if rec is None:
                continue
            total_cost += rec["usage"].get("cost") or 0.0
            with RAW_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # 只打印长度与结束原因，不打印正文（新样本可能落入 Holdout）
            print(f"{rec['id']} {rec['requested_model']} chars={len(rec['text'])} "
                  f"finish={rec['finish_reason']} cost={rec['usage'].get('cost')}", flush=True)
    print(f"total cost (USD): {total_cost:.4f}")


if __name__ == "__main__":
    main()
