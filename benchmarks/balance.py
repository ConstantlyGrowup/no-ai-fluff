"""打印 DeepSeek 官方余额（人民币）与 OpenRouter 已用额度（美元），供账本记录每轮前后余额。不打印任何密钥。

    python benchmarks/balance.py
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from no_ai_fluff.config import load_dotenv  # noqa: E402


def deepseek_cny() -> float:
    key = os.environ.get("Deepseek_OFFICIAL_API_KEY") or os.environ["DEEPSEEK_API_KEY"]
    req = urllib.request.Request("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
    info = json.loads(urllib.request.urlopen(req, timeout=30).read())["balance_infos"]
    return float(next(b["total_balance"] for b in info if b["currency"] == "CNY"))


def openrouter_usage() -> float:
    req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                 headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
    return float(json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["usage"])


if __name__ == "__main__":
    load_dotenv()
    print(json.dumps({"deepseek_cny": deepseek_cny(), "openrouter_usage_usd": round(openrouter_usage(), 4)}))
