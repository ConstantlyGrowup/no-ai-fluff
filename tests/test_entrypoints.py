import io
import json

from no_ai_fluff import cli, config
from no_ai_fluff.mcp_server import MCPServer
from no_ai_fluff.pipeline import Pipeline, PipelineConfig


def server(fake_translator, tmp_path, fn=lambda s: s.replace(" overhead ", "开销")):
    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path / "d")), translator_factory=lambda c: fake_translator(fn))
    return MCPServer(pipe)


def rpc(srv, *msgs):
    inp = io.StringIO("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs))
    out = io.StringIO()
    srv.serve(inp, out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_mcp_initialize_and_list(fake_translator, tmp_path):
    r = rpc(
        server(fake_translator, tmp_path),
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    )
    assert len(r) == 3  # 通知没有响应
    assert r[0]["result"]["protocolVersion"] == "2025-06-18"
    assert r[0]["result"]["serverInfo"]["name"] == "no-ai-fluff"
    assert {t["name"] for t in r[1]["result"]["tools"]} == {"prepare_rtt_draft", "finalize_patched_delivery"}
    assert r[2]["result"] == {}


def test_mcp_unknown_protocol_falls_back_to_latest(fake_translator, tmp_path):
    r = rpc(server(fake_translator, tmp_path),
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r[0]["result"]["protocolVersion"] == "2025-06-18"


def test_mcp_two_handshakes(fake_translator, tmp_path):
    srv = server(fake_translator, tmp_path)
    r1 = rpc(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "prepare_rtt_draft", "arguments": {"text": "调用 `foo()` 的 overhead 很大。"}}})
    res = r1[0]["result"]
    draft_id = res["structuredContent"]["draft_id"]
    assert "【RTT 初稿】" in res["content"][0]["text"] and draft_id in res["content"][0]["text"]
    assert "调用 `foo()` 的开销很大。" == res["structuredContent"]["rtt_draft"]
    r2 = rpc(srv, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "finalize_patched_delivery", "arguments": {
                       "draft_id": draft_id,
                       "patches": [{"find": "很大", "replace": "较大", "reason": "去夸张"},
                                   {"find": "`foo()`", "replace": "foo", "reason": "坏补丁"}]}}})
    fin = r2[0]["result"]
    assert fin["structuredContent"]["final_text"] == "调用 `foo()` 的开销较大。"
    assert fin["structuredContent"]["applied"] == 1 and fin["structuredContent"]["rejected"] == 1
    assert "拒收" in fin["content"][0]["text"]


def test_mcp_tool_errors(fake_translator, tmp_path):
    srv = server(fake_translator, tmp_path)
    r = rpc(
        srv,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "prepare_rtt_draft", "arguments": {"text": " "}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "finalize_patched_delivery", "arguments": {"draft_id": "nope", "patches": []}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "no/such"},
    )
    assert r[0]["result"]["isError"] and r[1]["result"]["isError"] and r[2]["result"]["isError"]
    assert "找不到草稿" in r[1]["result"]["content"][0]["text"]
    assert r[3]["error"]["code"] == -32601


def test_mcp_parse_error_and_batch(fake_translator, tmp_path):
    srv = server(fake_translator, tmp_path)
    out = io.StringIO()
    srv.serve(io.StringIO("不是json\n" + json.dumps([{"jsonrpc": "2.0", "id": 9, "method": "ping"}]) + "\n"), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1] == [{"jsonrpc": "2.0", "id": 9, "result": {}}]


def test_cli_radar_and_stats(tmp_path, capsys, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("值得注意的是，这个方案赋能了业务。", "utf-8")
    assert cli.main(["radar", str(f)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["cliche"] == 2
    assert cli.main(["stats"]) == 0
    assert "指标" in capsys.readouterr().out


def test_cli_prepare_finalize_without_key(tmp_path, capsys):
    f = tmp_path / "a.md"
    f.write_text("这个方案的 overhead 很大。", "utf-8")
    assert cli.main(["prepare", str(f)]) == 0
    cap = capsys.readouterr()
    prep = json.loads(cap.out)
    assert prep["fallback"] and "RTT 回退" in cap.err
    assert cli.main(["finalize", prep["draft_id"], "--patches", '[{"find":"overhead","replace":"开销"}]']) == 0
    assert capsys.readouterr().out.strip() == "这个方案的 开销 很大。"
    assert cli.main(["finalize", prep["draft_id"]]) == 2  # 已定稿


def test_cli_run_writes_output(tmp_path, capsys):
    f, o = tmp_path / "a.md", tmp_path / "out.md"
    f.write_text("一句话。", "utf-8")
    assert cli.main(["run", str(f), "-o", str(o)]) == 0
    assert o.read_text("utf-8") == "一句话。"


def test_dotenv_parse_and_load(tmp_path, monkeypatch):
    assert config.parse_dotenv('# c\nA=1\nexport B="two words"\nC=x # note\nbad line\n') == {
        "A": "1", "B": "two words", "C": "x"}
    env = tmp_path / "x.env"
    env.write_text("OPENROUTER_API_KEY=from-file\nNO_AI_FLUFF_BRIDGE=ja\n", "utf-8")
    monkeypatch.setenv("NO_AI_FLUFF_ENV", str(env))
    monkeypatch.setenv("NO_AI_FLUFF_BRIDGE", "ru")  # 已有环境变量优先
    monkeypatch.chdir(tmp_path)
    used = config.load_dotenv(force=True)
    import os

    assert env in used
    assert os.environ["OPENROUTER_API_KEY"] == "from-file"
    assert os.environ["NO_AI_FLUFF_BRIDGE"] == "ru"


def test_dotenv_concurrent_first_load(tmp_path, monkeypatch):
    import os
    import threading
    import time

    env = tmp_path / "c.env"
    env.write_text("NAF_CONCURRENT_TEST=yes\n", "utf-8")
    monkeypatch.setenv("NO_AI_FLUFF_ENV", str(env))
    monkeypatch.delenv("NAF_CONCURRENT_TEST", raising=False)
    monkeypatch.setattr(config, "_LOADED", False)
    real = config.parse_dotenv
    monkeypatch.setattr(config, "parse_dotenv", lambda t: (time.sleep(0.1), real(t))[1])
    seen = []

    def worker():
        config.load_dotenv()
        seen.append(os.environ.get("NAF_CONCURRENT_TEST"))

    ths = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert seen == ["yes"] * 4


def test_mcp_prepare_accepts_question(fake_translator, tmp_path):
    srv = server(fake_translator, tmp_path)
    r = rpc(srv, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "prepare_rtt_draft", "arguments": {
                "text": "这个方案的 overhead 很大。", "question": "这个方案值得用吗？"}}})
    tool = next(t for t in r[0]["result"]["tools"] if t["name"] == "prepare_rtt_draft")
    assert "question" in tool["inputSchema"]["properties"]
    assert "【用户问题】\n这个方案值得用吗？" in r[1]["result"]["content"][0]["text"]
