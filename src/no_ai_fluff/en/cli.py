"""Command line for the English edition: no-ai-fluff-en <command>

    prepare  [FILE|-]                 handshake 1: JSON with draft_id / original / round-trip reference / radar / rules
    finalize DRAFT_ID [--patches P]   handshake 2: P is a file, - (stdin) or inline JSON; prints the final text
    run      [FILE|-] [--patches P]   both steps at once
    radar    [FILE|-]                 surface radar only
    mcp                               start the stdio MCP server "no-ai-fluff-en"
    install  claude-code              install the Claude Code subagent, skill and MCP entry
    install  codex|zcode|qoder|workbuddy|generic   install the portable skill and an AGENTS.md section
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import __version__
from ..cli import _read, _read_patches, _write
from ..config import load_dotenv


def _pipeline(args):
    from .pipeline import EnPipeline, EnPipelineConfig

    return EnPipeline(EnPipelineConfig.from_env(provider=getattr(args, "provider", None),
                                                model=getattr(args, "model", None), bridge=getattr(args, "bridge", None)))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="no-ai-fluff-en", description="De-fluff English text before it reaches people")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("input", nargs="?", default="-", help="input file, - for stdin")
        p.add_argument("--format", dest="fmt", default="auto", choices=["auto", "markdown", "html", "text"])
        p.add_argument("--exit", default="terminal", choices=["terminal", "file"])
        p.add_argument("--provider", choices=["openrouter", "tencent"],
                       help="translation provider (default: OpenRouter if OPENROUTER_API_KEY is set, else Tencent Cloud)")
        p.add_argument("--model")
        p.add_argument("--bridge", help="bridge language, default ja")

    p = sub.add_parser("prepare", help="handshake 1")
    common(p)
    p.add_argument("--prompt-only", action="store_true", help="print only the editing prompt")
    p.add_argument("--draft-id", help="domain-triage follow-up: the draft_id from the first prepare")
    p.add_argument("--domain-triage", help='domain-triage follow-up: true / false / [true,false] / [{"id":1,"technical":true}]')
    p = sub.add_parser("finalize", help="handshake 2")
    p.add_argument("draft_id")
    p.add_argument("--patches", help="patch file / - / inline JSON; omitted means []")
    p.add_argument("-o", "--output")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("run", help="both steps")
    common(p)
    p.add_argument("--patches")
    p.add_argument("-o", "--output")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("radar", help="surface radar")
    p.add_argument("input", nargs="?", default="-")
    sub.add_parser("mcp", help="start the stdio MCP server")
    p = sub.add_parser("install", help="install the agent adapter (Claude Code: subagent + skill + MCP entry; "
                                        "other agents: portable skill + AGENTS.md section)")
    p.add_argument("host", choices=["claude-code", "codex", "zcode", "qoder", "workbuddy", "generic"])
    p.add_argument("--scope", default="project", choices=["project", "user"])
    p.add_argument("--dir")
    p.add_argument("--force", action="store_true")
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
        from ..install import install_generic
        from .install import install_claude_code_en

        root = Path(args.dir) if args.dir else None
        if args.host == "claude-code":
            print(install_claude_code_en(args.scope, root, args.force).render())
        else:
            print(install_generic(args.host, args.scope, root, args.force, lang="en").render())
        return 0
    if args.cmd == "radar":
        from .radar import scan, surface

        text = _read(args.input)
        print(json.dumps({"counts": scan(text.split("\n")).counts, "surface": surface(text)}, indent=2))
        return 0
    if args.cmd == "prepare":
        if args.draft_id:
            from ..pipeline import DraftNotFound

            try:
                res = _pipeline(args).complete_prepare(args.draft_id, args.domain_triage)
            except DraftNotFound:
                print(f"draft {args.draft_id} not found (already finalized or expired)", file=sys.stderr)
                return 2
        else:
            res = _pipeline(args).prepare(_read(args.input), fmt=args.fmt, exit=args.exit)
        if res.needs_domain_triage:
            print(f"draft_id: {res.draft_id}\n\n{res.triage_prompt}")
            print(f"[no-ai-fluff-en] domain triage needed: answer it, then run prepare --draft-id {res.draft_id} "
                  "--domain-triage '<JSON>'", file=sys.stderr)
            return 0
        if args.prompt_only:
            print(f"draft_id: {res.draft_id}\n\n{res.audit_prompt}")
        else:
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        if res.fallback:
            print(f"[no-ai-fluff-en] round trip skipped: {res.fallback_reason}", file=sys.stderr)
        return 0
    if args.cmd == "finalize":
        from ..pipeline import DraftNotFound

        try:
            res = _pipeline(args).finalize(args.draft_id, _read_patches(args.patches))
        except DraftNotFound:
            print(f"draft {args.draft_id} not found (already finalized or expired)", file=sys.stderr)
            return 2
        _write(json.dumps(res.to_dict(), ensure_ascii=False, indent=2) if args.json else res.final_text, args.output)
        if res.rejected:
            print(f"[no-ai-fluff-en] {res.rejected} patch(es) rejected", file=sys.stderr)
        return 0
    if args.cmd == "run":
        pipe = _pipeline(args)
        prep = pipe.prepare(_read(args.input), fmt=args.fmt, exit=args.exit)
        if prep.needs_domain_triage:  # nobody to ask in one-shot mode: treat as technical (affects only the rules)
            prep = pipe.complete_prepare(prep.draft_id, None)
        fin = pipe.finalize(prep.draft_id, _read_patches(args.patches))
        if args.json:
            _write(json.dumps({"prepare": prep.to_dict(), "finalize": fin.to_dict()}, ensure_ascii=False, indent=2), args.output)
        else:
            _write(fin.final_text, args.output)
        if prep.fallback:
            print(f"[no-ai-fluff-en] round trip skipped: {prep.fallback_reason}", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
