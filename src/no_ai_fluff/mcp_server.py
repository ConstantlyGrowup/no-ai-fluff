"""标准 stdio MCP Server（手写 JSON-RPC 2.0，零依赖）。

提供两次握手的原子工具：
    prepare_rtt_draft          Stage 0 + 1 + 2a：掩码、往返翻译、雷达，返回审校材料
                               （雷达扫出疑似电报句且本地没有分诊缓存时，先返回领域分诊请求；
                                宿主判断后带 draft_id + domain_triage 再调一次，取回审校规则，往返翻译不重跑）
    finalize_patched_delivery  Stage 3：合并宿主模型开具的 JSON 补丁，校验后返回定稿

传输：每行一条 JSON-RPC 消息（MCP stdio 规范）。stdout 只写协议消息，日志一律写 stderr。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, TextIO

from . import __version__
from .delivery_note import host_note_zh
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
            "少数稿子会先返回领域分诊请求（status=needs_domain_triage）：判断列出的几句是否出自技术/工程语境，"
            "再带 draft_id 和 domain_triage 调用一次本工具（此时不用再传 text），即可取回审校规则。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待净化的草稿全文（第二步领域分诊时可省略）"},
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
                "draft_id": {
                    "type": "string",
                    "description": "第二步领域分诊：回传第一次调用返回的 draft_id（此时不用再传 text）",
                },
                "domain_triage": {
                    "description": (
                        "第二步领域分诊：列出的句子是否出自技术/工程语境。可传单个布尔（整篇同一判断）、"
                        "布尔数组（按编号顺序），或 [{\"id\": 1, \"technical\": true}] 数组（或其 JSON 字符串）"
                    ),
                    "oneOf": [
                        {"type": "boolean"},
                        {"type": "array", "items": {"oneOf": [{"type": "boolean"}, {"type": "object"}]}},
                        {"type": "string"},
                    ],
                },
            },
            "description": "text 与 draft_id 二选一：第一次调用传 text；收到领域分诊请求后传 draft_id + domain_triage",
        },
    },
    {
        "name": "finalize_patched_delivery",
        "title": "合并补丁并输出定稿（握手 2）",
        "description": (
            "提交对原文的局部替换补丁，返回定稿。patches 为 "
            '[{"find": "原文片段", "replace": "修改后片段", "type": "问题类型", "reason": "原因"}]，无需修改时传 []。'
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
    lines.append("\n" + host_note_zh())
    return "\n".join(lines)


def format_domain_triage(res) -> str:
    return (
        f"draft_id: {res.draft_id}\n"
        "往返翻译已完成并存为草稿。还差一步：雷达在稿子里扫到几句被切得很碎的短句，请先判断它们是否出自技术/工程语境。\n\n"
        + res.triage_prompt
        + f"\n\n判断后调用 prepare_rtt_draft(draft_id=\"{res.draft_id}\", domain_triage=上面的 JSON 数组) 取回审校规则。"
    )


MESSAGES = {
    "text_empty": "text 不能为空",
    "text_or_draft": "需要 text（第一次调用），或 draft_id + domain_triage（领域分诊后的第二次调用）",
    "no_draft_id": "缺少 draft_id",
    "draft_not_found": "找不到草稿 {draft_id}（可能已定稿或过期），请重新调用 prepare_rtt_draft",
    "unknown_tool": "未知工具：{name}",
}
INSTRUCTIONS = (
    "交付给人类阅读的中文长回复/文档在定稿前：先调 prepare_rtt_draft，"
    "按返回的规则只开局部 JSON 补丁，再调 finalize_patched_delivery 取定稿。"
    "定稿后面附有给宿主的交付说明：照它把定稿交给用户、在聊天里附一句可以点名整段重写的提示，"
    "用户点名后按其中七条规则只重写那一段。"
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

    def host_note(self) -> str:
        return host_note_zh()

    def format_domain_triage(self, res) -> str:
        return format_domain_triage(res)

    def _prepare_reply(self, res) -> dict:
        if getattr(res, "needs_domain_triage", False):
            return {"content": [{"type": "text", "text": self.format_domain_triage(res)}], "structuredContent": {
                "status": "needs_domain_triage", "draft_id": res.draft_id,
                "sentences": [{"id": i, "text": x} for i, x in enumerate(res.triage_sentences, 1)]}}
        payload = {k: v for k, v in res.to_dict().items() if k != "audit_prompt"}
        return {"content": [{"type": "text", "text": self.format_prepare(res)}], "structuredContent": payload}

    # -- 工具实现 ---------------------------------------------------------------
    def call_tool(self, name: str, args: dict) -> dict:
        try:
            if name == "prepare_rtt_draft":
                draft_id = args.get("draft_id")
                if isinstance(draft_id, str) and draft_id and "domain_triage" in args:
                    try:
                        res = self.pipeline.complete_prepare(draft_id, args.get("domain_triage"))
                    except DraftNotFound:
                        return _tool_error(self.messages["draft_not_found"].format(draft_id=draft_id))
                    return self._prepare_reply(res)
                text = args.get("text")
                if not isinstance(text, str) or not text.strip():
                    return _tool_error(self.messages.get("text_or_draft", self.messages["text_empty"]) if draft_id else self.messages["text_empty"])
                res = self.pipeline.prepare(text, fmt=args.get("format", "auto"), exit=args.get("exit", "terminal"),
                                            question=args.get("question"))
                return self._prepare_reply(res)
            if name == "finalize_patched_delivery":
                draft_id = args.get("draft_id")
                if not isinstance(draft_id, str):
                    return _tool_error(self.messages["no_draft_id"])
                try:
                    res = self.pipeline.finalize(draft_id, args.get("patches", []))
                except DraftNotFound:
                    return _tool_error(self.messages["draft_not_found"].format(draft_id=draft_id))
                return {"content": [{"type": "text", "text": self.format_finalize(res)}],
                        "structuredContent": {**res.to_dict(), "host_note": self.host_note()}}
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
