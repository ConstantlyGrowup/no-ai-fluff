"""采集 daily 场景语料：普通人日常会问 AI、且 AI 腔明显影响阅读的场景。

检索结论（2026-11）：没有找到 2026 年以后、许可证允许、由当前一代模型作答的中文日常问答语料；
HC3-Chinese 等数据集是 2023 年的模型回答，文风与现在差异很大。因此由 DeepSeek 官方 API 原生作答：
只发用户问题，不加系统提示和任何文风要求，保留模型默认的推理强度。

边界（与 coding / teaching / writing / discussion 互不重叠，不含技术与学习类问题）：
- life      生活建议：租房、作息、出行，AI 容易堆砌“首先/其次/最后”清单
- emotion   情感倾诉的回应：AI 容易套话、说教、强行升华
- consumer  消费决策：AI 容易排比罗列、空泛的“根据您的需求”
- casual    日常小写作：请假消息、朋友圈、祝福语、和房东沟通，AI 容易辞藻堆砌、不像真人口吻

    python benchmarks/corpus/collect_daily.py              # 全部
    python benchmarks/corpus/collect_daily.py --only daily-004
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from no_ai_fluff.config import load_dotenv  # noqa: E402
from no_ai_fluff.rtt import ChatClient  # noqa: E402

RAW_PATH = HERE / "daily_raw.jsonl"
MODEL = "deepseek-flash"

TASKS = [
    ("daily-001", "life", "北京首次租房",
     "下个月要从杭州搬到北京工作，第一次在北京租房，预算 5000 左右，有什么要注意的？"),
    ("daily-002", "life", "调整作息",
     "最近总是晚上一两点才睡，早上起不来，怎么把作息调回来？"),
    ("daily-003", "life", "带父母出游",
     "五一想带爸妈出去玩三四天，他们六十多岁了，腿脚还行，去哪比较合适？"),
    ("daily-004", "emotion", "朋友借钱不还",
     "和处了五年的好朋友吵架了，因为她借钱一直不还，我提了一句她就说我小气。现在很难受，不知道该怎么办。"),
    ("daily-005", "emotion", "工作焦虑",
     "工作三年了感觉一直在原地踏步，看着同学都升职了，有点焦虑，是不是我太没用了？"),
    ("daily-006", "emotion", "宠物离世",
     "养了十二年的猫昨天走了，一整天都没缓过来。"),
    ("daily-007", "consumer", "选扫地机器人",
     "预算 3000 左右想买个扫地机器人，家里有猫，地板是木地板，怎么选？"),
    ("daily-008", "consumer", "要不要买车",
     "纠结要不要买车，我在上海上班，地铁通勤 40 分钟，周末偶尔想出去玩。"),
    ("daily-009", "casual", "请假消息",
     "帮我写一段微信消息，跟领导请三天假回老家参加奶奶的八十大寿。"),
    ("daily-010", "casual", "朋友圈文案",
     "帮我写一段朋友圈文案，今天带女儿第一次去海边。"),
    ("daily-011", "casual", "婚礼祝福",
     "大学室友下周结婚，我去不了，帮我写一段给他的祝福。"),
    ("daily-012", "casual", "和房东还价",
     "帮我回复一下房东，他说下个月起房租要涨 500，我想委婉地还价。"),
]


def collect(task: tuple) -> dict | None:
    tid, sub, title, prompt = task
    client = ChatClient(provider="deepseek", timeout=300, max_retries=2)
    try:
        res = client.chat(MODEL, [{"role": "user", "content": prompt}], temperature=1.0, max_tokens=8000)
    except Exception as e:  # noqa: BLE001
        print(f"{tid}: FAILED {e}", file=sys.stderr)
        return None
    return {
        "id": tid, "scenario": "daily", "subscenario": sub, "model": f"deepseek/{MODEL}",
        "requested_model": MODEL, "title": title, "prompt": prompt, "text": res.text,
        "finish_reason": "stop", "usage": res.usage, "latency_s": round(res.latency_s, 1),
        "collected_at": time.strftime("%Y-%m-%d"),
    }


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    tasks = [t for t in TASKS if not args.only or t[0] in args.only]
    with ThreadPoolExecutor(6) as pool:
        recs = [r for r in pool.map(collect, tasks) if r]
    with RAW_PATH.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            u = r["usage"]
            print(f"{r['id']} {r['subscenario']:8} chars={len(r['text']):5} "
                  f"tokens={u.get('completion_tokens')} (reasoning {(u.get('completion_tokens_details') or {}).get('reasoning_tokens')})")


if __name__ == "__main__":
    main()
