"""标准 stdio MCP Server（手写 JSON-RPC 2.0，零依赖）。

提供两次握手的原子工具：
    prepare_rtt_draft          Stage 0 + 1 + 2a：掩码、往返翻译、雷达，返回审校材料
    finalize_patched_delivery  Stage 3：合并宿主模型开具的 JSON 补丁，校验后返回定稿

传输：每行一条 JSON-RPC 消息（MCP stdio 规范）。stdout 只写协议消息，日志一律写 stderr。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, TextIO

from . import __version__
from .pipeline import DraftNotFound, Pipeline

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {
        "name": "prepare_rtt_draft",
        "title": "准备往返翻译初稿（握手 1）",
        "description": (
            "把一段面向人类阅读的中文交付草稿（Markdown / HTML / 纯文本）做往返翻译解压，"
            "返回 draft_id、原文、RTT 初稿、表层雷达提示与审校规则。"
            "调用方随后应对照原文全量审校 RTT 初稿，只输出局部 JSON 补丁，再调用 finalize_patched_delivery。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待净化的草稿全文"},
                "format": {"type": "string", "enum": ["auto", "markdown", "html", "text"], "default": "auto"},
                "question": {
                    "type": "string",
                    "description": "可选：用户的原始问题。审校时用来判断哪些内容是回答问题必不可少的、有没有跑题",
                },
                "exit": {
                    "type": "string",
                    "enum": ["terminal", "file"],
                    "default": "terminal",
                    "description": "交付出口：terminal=终端回复（10s 时限），file=落盘文档（60s 时限）",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "finalize_patched_delivery",
        "title": "合并补丁并输出定稿（握手 2）",
        "description": (
            "提交对 RTT 初稿的局部替换补丁，返回定稿。patches 为 "
            '[{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]，无需修改时传 []。'
            "破坏代码/公式/路径等受保护内容的补丁会被自动拒收；最终校验失败时整体回退原文。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string"},
                "patches": {
                    "description": "补丁数组，或其 JSON 字符串",
                    "oneOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "find": {"type": "string"},
                                    "replace": {"type": "string"},
                                    "type": {"type": "string"},
                                    "misread": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["find", "replace"],
                            },
                        },
                        {"type": "string"},
                    ],
                },
            },
            "required": ["draft_id", "patches"],
        },
    },
]


def format_prepare(res) -> str:
    head = f"draft_id: {res.draft_id}\n"
    if res.fallback:
        head += f"注意：往返翻译未生效（{res.fallback_reason}），初稿即原文，仍请按规则审校。\n"
    return (
        head
        + "\n"
        + res.audit_prompt
        + f"\n\n完成后调用 finalize_patched_delivery(draft_id=\"{res.draft_id}\", patches=上面的 JSON 数组)。"
    )


def format_finalize(res) -> str:
    lines = [res.final_text]
    rejected = [p for p in res.patches if not p["applied"]]
    meta = f"\n\n---\n补丁：生效 {res.applied} 条，拒收 {res.rejected} 条"
    if res.fallback_reason:
        meta += f"；{res.fallback_reason}"
    lines.append(meta)
    for p in rejected[:10]:
        lines.append(f"- 拒收「{p['find'][:30]}」：{p['reject_reason']}")
    return "\n".join(lines)


class MCPServer:
    def __init__(self, pipeline: Pipeline | None = None):
        self._pipeline = pipeline

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = Pipeline()
        return self._pipeline

    # -- 工具实现 ---------------------------------------------------------------
    def call_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "prepare_rtt_draft":
                text = args.get("text")
                if not isinstance(text, str) or not text.strip():
                    return _tool_error("text 不能为空")
                res = self.pipeline.prepare(text, fmt=args.get("format", "auto"), exit=args.get("exit", "terminal"),
                                            question=args.get("question"))
                payload = {k: v for k, v in res.to_dict().items() if k != "audit_prompt"}
                return {"content": [{"type": "text", "text": format_prepare(res)}], "structuredContent": payload}
            if name == "finalize_patched_delivery":
                draft_id = args.get("draft_id")
                if not isinstance(draft_id, str):
                    return _tool_error("缺少 draft_id")
                try:
                    res = self.pipeline.finalize(draft_id, args.get("patches", []))
                except DraftNotFound:
                    return _tool_error(f"找不到草稿 {draft_id}（可能已定稿或过期），请重新调用 prepare_rtt_draft")
                return {"content": [{"type": "text", "text": format_finalize(res)}], "structuredContent": res.to_dict()}
            return _tool_error(f"未知工具：{name}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return _tool_error(f"{type(e).__name__}: {e}")

    # -- JSON-RPC ---------------------------------------------------------------
    def handle(self, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "Invalid Request")
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method is None:  # 客户端回给我们的响应，忽略
            return None
        is_notification = "id" not in msg
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "no-ai-fluff", "version": __version__},
                    "instructions": (
                        "交付给人类阅读的中文长回复/文档在定稿前：先调 prepare_rtt_draft，"
                        "按返回的规则只开局部 JSON 补丁，再调 finalize_patched_delivery 取定稿。"
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call_tool(params.get("name", ""), params.get("arguments") or {})
            elif method.startswith("notifications/"):
                return None
            else:
                return None if is_notification else _rpc_error(mid, -32601, f"Method not found: {method}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return None if is_notification else _rpc_error(mid, -32603, str(e))
        return None if is_notification else {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp: Any = _rpc_error(None, -32700, "Parse error")
            else:
                if isinstance(msg, list):  # 批量请求
                    resp = [r for r in (self.handle(m) for m in msg) if r is not None] or None
                else:
                    resp = self.handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()


def _tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _rpc_error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
        except (AttributeError, ValueError):
            pass
    MCPServer().serve()


if __name__ == "__main__":
    main()
