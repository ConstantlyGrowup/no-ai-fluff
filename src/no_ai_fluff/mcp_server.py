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
            "若有补丁把初稿的直白说法改回了原文用词，第一次调用会返回一份词对复核清单：请判断后带上 term_review "
            "（要撤销的编号数组，可为 []）用同样的 patches 再调用一次。"
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
                "term_review": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "词对复核结果：要撤销的编号（1 起始）。第一次调用不传；收到复核清单后再传",
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


MESSAGES = {
    "text_empty": "text 不能为空",
    "no_draft_id": "缺少 draft_id",
    "draft_not_found": "找不到草稿 {draft_id}（可能已定稿或过期），请重新调用 prepare_rtt_draft",
    "term_review": ("还差一步：有 {n} 处补丁把初稿的说法改回了原文用词，请单独复核。\n\n{prompt}\n\n"
                    "判断后用同样的 patches 再调用 finalize_patched_delivery，并带上 term_review=上面的编号数组。"),
    "unknown_tool": "未知工具：{name}",
}
INSTRUCTIONS = (
    "交付给人类阅读的中文长回复/文档在定稿前：先调 prepare_rtt_draft，"
    "按返回的规则只开局部 JSON 补丁，再调 finalize_patched_delivery 取定稿。"
)


class MCPServer:
    """中文版 MCP Server。其他语言版本（no_ai_fluff.en）继承本类，替换下面几项类属性与两个格式化方法。"""

    server_name = "no-ai-fluff"
    tools = TOOLS
    instructions = INSTRUCTIONS
    messages = MESSAGES
    pipeline_cls = Pipeline

    def __init__(self, pipeline: Pipeline | None = None):
        self._pipeline = pipeline

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = self.pipeline_cls()
        return self._pipeline

    def format_prepare(self, res) -> str:
        return format_prepare(res)

    def format_finalize(self, res) -> str:
        return format_finalize(res)

    # -- 工具实现 ---------------------------------------------------------------
    def call_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "prepare_rtt_draft":
                text = args.get("text")
                if not isinstance(text, str) or not text.strip():
                    return _tool_error(self.messages["text_empty"])
                res = self.pipeline.prepare(text, fmt=args.get("format", "auto"), exit=args.get("exit", "terminal"),
                                            question=args.get("question"))
                payload = {k: v for k, v in res.to_dict().items() if k != "audit_prompt"}
                return {"content": [{"type": "text", "text": self.format_prepare(res)}], "structuredContent": payload}
            if name == "finalize_patched_delivery":
                draft_id = args.get("draft_id")
                if not isinstance(draft_id, str):
                    return _tool_error(self.messages["no_draft_id"])
                try:
                    if args.get("term_review") is None:
                        tr = self.pipeline.term_review(draft_id, args.get("patches", []))
                        if tr:
                            prompt, suspects = tr
                            text = self.messages["term_review"].format(n=len(suspects), prompt=prompt)
                            return {"content": [{"type": "text", "text": text}], "structuredContent": {
                                "status": "needs_term_review", "draft_id": draft_id,
                                "pairs": [{"no": i, "left": sp.left, "right": sp.right} for i, sp in enumerate(suspects, 1)]}}
                    res = self.pipeline.finalize(draft_id, args.get("patches", []), term_reject=args.get("term_review"))
                except DraftNotFound:
                    return _tool_error(self.messages["draft_not_found"].format(draft_id=draft_id))
                return {"content": [{"type": "text", "text": self.format_finalize(res)}], "structuredContent": res.to_dict()}
            return _tool_error(self.messages["unknown_tool"].format(name=name))
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
                    "serverInfo": {"name": self.server_name, "version": __version__},
                    "instructions": self.instructions,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools}
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


def main(server_cls: type | None = None) -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
        except (AttributeError, ValueError):
            pass
    (server_cls or MCPServer)().serve()


if __name__ == "__main__":
    main()
