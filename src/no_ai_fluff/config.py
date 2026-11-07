"""运行配置与 .env 读取（零依赖）。

读取顺序（已存在的环境变量永远优先，不会被 .env 覆盖）：
1. 环境变量 NO_AI_FLUFF_ENV 指向的文件
2. 当前工作目录下的 .env
3. 仓库根目录下的 .env（以本包位置向上查找，方便源码直接运行）
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_LOADED = False
_LOCK = threading.Lock()


def parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if key:
            out[key] = val
    return out


def candidate_env_files() -> list[Path]:
    paths: list[Path] = []
    if os.environ.get("NO_AI_FLUFF_ENV"):
        paths.append(Path(os.environ["NO_AI_FLUFF_ENV"]))
    paths.append(Path.cwd() / ".env")
    here = Path(__file__).resolve()
    for parent in list(here.parents)[1:4]:  # src/no_ai_fluff -> src -> 仓库根
        if (parent / "pyproject.toml").exists():
            paths.append(parent / ".env")
            break
    seen, uniq = set(), []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def load_dotenv(force: bool = False) -> list[Path]:
    """把 .env 里的变量补进 os.environ（不覆盖已有值）。返回实际读取的文件。"""
    global _LOADED
    with _LOCK:  # 并发首次调用时，后到的线程必须等文件读完，不能看到“已加载”就直接返回
        if _LOADED and not force:
            return []
        used: list[Path] = []
        for p in candidate_env_files():
            try:
                if not p.is_file():
                    continue
                for k, v in parse_dotenv(p.read_text("utf-8-sig")).items():
                    os.environ.setdefault(k, v)
                used.append(p)
            except OSError:
                continue
        _LOADED = True
        return used


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default
