"""命令行入口：no-ai-fluff <command>

    prepare  [FILE|-]                 握手 1：输出 JSON（draft_id / 原文 / RTT 初稿 / 雷达 / 审校提示词）
    finalize DRAFT_ID [--patches P]   握手 2：P 可以是文件、- （stdin）或内联 JSON；输出定稿
    run      [FILE|-] [--patches P]   一次跑完：不给补丁即纯 RTT 输出
    radar    [FILE|-]                 只做表层雷达统计
    stats                             汇总 ~/.no-ai-fluff/events.jsonl
    mcp                               启动 stdio MCP Server
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, telemetry
from .config import load_dotenv


def _read(src: str | None) -> str:
    if src in (None, "-"):
        return sys.stdin.read()
    return Path(src).read_text("utf-8-sig")


def _read_patches(arg: str | None):
    if arg is None:
        return []
    if arg == "-":
        return sys.stdin.read()
    p = Path(arg)
    if len(arg) < 1024 and p.is_file():
        return p.read_text("utf-8-sig")
    return arg  # 内联 JSON


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text, "utf-8")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _pipeline(args):
    from .pipeline import Pipeline, PipelineConfig

    return Pipeline(PipelineConfig.from_env(
        provider=getattr(args, "provider", None), model=getattr(args, "model", None), bridge=getattr(args, "bridge", None)))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="no-ai-fluff", description="交付层中文去 AI 腔引擎")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("input", nargs="?", default="-", help="输入文件，- 表示 stdin")
        p.add_argument("--format", dest="fmt", default="auto", choices=["auto", "markdown", "html", "text"])
        p.add_argument("--exit", default="terminal", choices=["terminal", "file"])
        p.add_argument("--provider", choices=["openrouter", "tencent"], help="翻译小模型服务商（默认：有 OPENROUTER_API_KEY 用 openrouter，否则用腾讯云）")
        p.add_argument("--model")
        p.add_argument("--bridge", choices=["fr", "ja", "ru"], help="桥接语，默认 ru（M3 选型结果）")

    p = sub.add_parser("prepare", help="握手 1")
    common(p)
    p.add_argument("--prompt-only", action="store_true", help="只输出审校提示词（给人或别的模型看）")

    p = sub.add_parser("finalize", help="握手 2")
    p.add_argument("draft_id")
    p.add_argument("--patches", help="补丁文件 / - / 内联 JSON；省略表示 []")
    p.add_argument("-o", "--output")
    p.add_argument("--json", action="store_true", help="输出完整 JSON 结果")

    p = sub.add_parser("run", help="一次跑完")
    common(p)
    p.add_argument("--patches", help="补丁文件 / 内联 JSON（基于 RTT 初稿）；省略即纯 RTT")
    p.add_argument("-o", "--output")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("radar", help="表层雷达统计")
    p.add_argument("input", nargs="?", default="-")
    p.add_argument("--format", dest="fmt", default="auto")

    p = sub.add_parser("stats", help="遥测汇总")
    p.add_argument("--json", action="store_true")

    sub.add_parser("mcp", help="启动 stdio MCP Server")

    p = sub.add_parser("install", help="安装宿主适配（审校子智能体 + Skill + MCP 注册）")
    p.add_argument("host", choices=["claude-code"])
    p.add_argument("--scope", default="project", choices=["project", "user"])
    p.add_argument("--dir", help="安装到的根目录；默认 project=当前目录，user=用户主目录")
    p.add_argument("--force", action="store_true", help="覆盖已存在且内容不同的文件")
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    load_dotenv()

    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0

    if args.cmd == "install":
        from .install import install_claude_code

        print(install_claude_code(args.scope, Path(args.dir) if args.dir else None, args.force).render())
        return 0

    if args.cmd == "radar":
        from .radar import scan_document

        rep = scan_document(_read(args.input), args.fmt)
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "stats":
        s = telemetry.summarize(telemetry.read_events())
        print(json.dumps(s, ensure_ascii=False, indent=2) if args.json else telemetry.render_table(s))
        return 0

    if args.cmd == "prepare":
        res = _pipeline(args).prepare(_read(args.input), fmt=args.fmt, exit=args.exit)
        if args.prompt_only:
            print(f"draft_id: {res.draft_id}\n\n{res.audit_prompt}")
        else:
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        if res.fallback:
            print(f"[no-ai-fluff] RTT 回退：{res.fallback_reason}", file=sys.stderr)
        return 0

    if args.cmd == "finalize":
        from .pipeline import DraftNotFound

        try:
            res = _pipeline(args).finalize(args.draft_id, _read_patches(args.patches))
        except DraftNotFound:
            print(f"找不到草稿 {args.draft_id}（可能已定稿或过期）", file=sys.stderr)
            return 2
        _write(json.dumps(res.to_dict(), ensure_ascii=False, indent=2) if args.json else res.final_text, args.output)
        if res.rejected:
            print(f"[no-ai-fluff] 拒收补丁 {res.rejected} 条", file=sys.stderr)
        return 0

    if args.cmd == "run":
        pipe = _pipeline(args)
        prep = pipe.prepare(_read(args.input), fmt=args.fmt, exit=args.exit)
        fin = pipe.finalize(prep.draft_id, _read_patches(args.patches))
        if args.json:
            _write(json.dumps({"prepare": prep.to_dict(), "finalize": fin.to_dict()}, ensure_ascii=False, indent=2), args.output)
        else:
            _write(fin.final_text, args.output)
        if prep.fallback:
            print(f"[no-ai-fluff] RTT 回退：{prep.fallback_reason}", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
