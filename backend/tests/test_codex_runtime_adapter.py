"""Offline fake-process tests for the app-server boundary; no account/model access."""

import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tradingagents.web.domain.agent_research import LocalAgentReport
from tradingagents.web.infrastructure.agent_runtimes.codex import (
    CodexRuntimeAdapter,
    RuntimeAdapterError,
    _options,
)

FAKE = r"""#!/usr/bin/env python3
import json,os,sys,time,tomllib,subprocess
from pathlib import Path
MODE = __MODE__
TRACE = Path(__TRACE__)
if '--version' in sys.argv:
    print('codex-cli 0.164.0-alpha.11.1' if MODE != 'wrong_version' else 'codex-cli 0.163.0')
    raise SystemExit()
configuration={}
for i,arg in enumerate(sys.argv):
    if arg=='-c':
        key,value=sys.argv[i+1].split('=',1)
        value=tomllib.loads('value='+value)['value']
        dest=configuration
        parts=key.split('.')
        for part in parts[:-1]: dest=dest.setdefault(part,{})
        dest[parts[-1]]=value
def write(value):
    print(json.dumps(value),flush=True)
def respond(message,result):
    write({'id':message['id'],'result':result})
def finish():
    if MODE!='unknown_usage':
        write({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-fixture','turnId':'turn-fixture',
            'tokenUsage':{'total':{'inputTokens':31,'outputTokens':12,'totalTokens':43,'cachedInputTokens':3}}}})
    write({'method':'item/completed','params':{'threadId':'thread-fixture','turnId':'turn-fixture',
        'item':{'type':'agentMessage','phase':'final_answer','text':'{"summary":"offline research"}'}}})
    write({'method':'turn/completed','params':{'threadId':'thread-fixture','turn':{'id':'turn-fixture','status':'completed'}}})
for line in sys.stdin:
    m=json.loads(line)
    with TRACE.open('a') as f: f.write(json.dumps(m)+'\n')
    method=m.get('method')
    if method=='initialize': respond(m,{'userAgent':'test'})
    elif method=='config/read':
        if MODE=='wrong_policy': configuration['features']['shell_tool']=True
        if MODE in ('projection','wrong_origin'):
            actual=json.loads(json.dumps(configuration));actual['tools'].pop('experimental_request_user_input')
            origin={'name':{'type':'user' if MODE=='wrong_origin' else 'sessionFlags'},'version':'fixture-v1'}
            respond(m,{'config':actual,'origins':{'tools.experimental_request_user_input.enabled':origin},
                'layers':[{'config':configuration,**origin}]})
        else: respond(m,{'config':configuration})
    elif method=='account/read':
        respond(m,{'account':None if MODE=='signed_out' else {'type':'chatgpt','email':'fixture@example.invalid'}})
    elif method=='model/list':
        respond(m,{'data':[{'model':'fixture-model','displayName':'Fixture model',
            'supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]})
    elif method=='thread/start':
        catalog=json.loads(Path(configuration['model_catalog_json']).read_text())['models'][0]
        assert catalog['shell_type']=='disabled' and catalog['apply_patch_tool_type'] is None
        assert catalog['experimental_supported_tools']==[]
        assert not any(k in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','FRED_API_KEY'))
        respond(m,{'thread':{'id':'thread-fixture'},'model':'different' if MODE=='wrong_model' else m['params']['model'],
            'modelProvider':m['params']['modelProvider'],'approvalPolicy':'never','sandbox':{'type':'readOnly'},
            'runtimeWorkspaceRoots':[],'instructionSources':[]})
    elif method=='turn/start':
        if MODE=='timeout': time.sleep(30)
        if MODE in ('child','orphan'):
            p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
            Path(str(TRACE)+'.child').write_text(str(p.pid))
            if MODE=='orphan': raise SystemExit()
            time.sleep(30)
        respond(m,{'turn':{'id':'turn-fixture'}})
        if MODE=='invalid_json': print('not json',flush=True)
        elif MODE=='oversize': print('x'*1100000,flush=True)
        elif MODE=='approval':
            write({'id':'forbidden','method':'item/commandExecution/requestApproval','params':{}})
        elif MODE=='unknown_request':
            write({'id':'forbidden','method':'account/login/start','params':{}})
        elif MODE=='foreign_tool':
            write({'id':'tool-fixture','method':'item/tool/call','params':{'threadId':'other','turnId':'turn-fixture',
                'tool':'research_prices','arguments':{}}})
        elif MODE=='builtin_tool':
            write({'method':'item/completed','params':{'threadId':'thread-fixture','turnId':'turn-fixture',
                'item':{'type':'fileChange'}}})
        elif MODE=='foreign_event':
            write({'method':'item/completed','params':{'threadId':'other','turnId':'turn-fixture',
                'item':{'type':'agentMessage','text':'foreign'}}})
        elif MODE=='no_tools': finish()
        else:
            write({'id':'tool-fixture','method':'item/tool/call','params':{'threadId':'thread-fixture','turnId':'turn-fixture',
                'tool':'research_prices','arguments':{}}})
    elif m.get('id')=='tool-fixture': finish()
"""


def fixture_runtime(tmp_path, mode="normal", **options):
    root = tmp_path.resolve()
    program, trace = root / "codex-fixture", root / "trace.jsonl"
    program.write_text(FAKE.replace("__MODE__", repr(mode)).replace("__TRACE__", repr(str(trace))))
    program.chmod(0o700)
    home, workdir = root / "auth", root / "work"
    home.mkdir(mode=0o700)
    workdir.mkdir(mode=0o700)
    adapter = CodexRuntimeAdapter(program, **options)
    return adapter, {"mode": "runtime_login", "runtime_home": str(home)}, workdir, trace


TOOLS = [
    {
        "name": "research_prices",
        "description": "Fetch frozen research prices",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
]


def run(adapter, auth, workdir, *, active=lambda: None, on_tool=lambda *_: '{"evidence_id":"e1"}', output_schema=None):
    events = []
    result = adapter.run(
        prompt="Offline fixture research",
        model_id="fixture-model",
        tools=TOOLS,
        on_tool=on_tool,
        output_schema=output_schema or {"type": "object"},
        emit=lambda kind, payload: events.append((kind, payload)),
        active=active,
        workdir=workdir,
        auth_options=auth,
    )
    return result, events


def test_probe_never_starts_research_and_returns_no_raw_identity(tmp_path):
    adapter, auth, _, trace = fixture_runtime(tmp_path)
    result = adapter.probe(auth_options=auth)
    methods = [json.loads(line).get("method") for line in trace.read_text().splitlines()]
    assert methods == ["initialize", "initialized", "config/read", "account/read", "model/list"]
    assert result["auth_status"] == "authorized" and result["models"][0]["id"] == "fixture-model"
    assert result["capabilities"]["resume"] is False
    assert len(result["auth_fingerprint"]) == 64 and "fixture@example.invalid" not in json.dumps(
        result
    )


def test_run_checks_policy_dispatches_scoped_tool_and_captures_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("FRED_API_KEY", "must-not-leak")
    adapter, auth, workdir, trace = fixture_runtime(tmp_path)
    called = []
    result, events = run(
        adapter,
        auth,
        workdir,
        on_tool=lambda name, args: called.append((name, args)) or '{"evidence":"e1"}',
    )
    assert called == [("research_prices", {})]
    assert result["report"] == '{"summary":"offline research"}'
    assert result["usage"]["total_tokens"] == 43
    assert result["effective_model"] == result["requested_model"] == "fixture-model"
    assert [kind for kind, _ in events] == [
        "session_started",
        "tool_started",
        "tool_completed",
        "usage",
        "message_completed",
        "completed",
    ]
    messages = [json.loads(line) for line in trace.read_text().splitlines()]
    start = next(item["params"] for item in messages if item.get("method") == "thread/start")
    assert start["ephemeral"] and start["environments"] == [] and start["sandbox"] == "read-only"
    assert start["dynamicTools"][0]["type"] == "function"
    assert next(item["params"] for item in messages if item.get("method") == "turn/start")[
        "outputSchema"
    ] == {"type": "object"}
    assert not list(workdir.glob("runtime-policy-*"))


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("wrong_policy", "runtime_policy_unconfirmed"),
        ("wrong_model", "runtime_effective_configuration_mismatch"),
        ("signed_out", "runtime_authentication_missing"),
        ("invalid_json", "runtime_invalid_message"),
        ("oversize", "runtime_output_limit"),
        ("approval", "runtime_operation_denied"),
        ("unknown_request", "runtime_operation_denied"),
        ("foreign_tool", "runtime_operation_denied"),
        ("builtin_tool", "runtime_builtin_tool_denied"),
        ("foreign_event", "runtime_event_scope_mismatch"),
        ("wrong_version", "runtime_version_unsupported"),
        ("wrong_origin", "runtime_policy_unconfirmed"),
    ],
)
def test_rejects_unsafe_protocol_or_configuration(tmp_path, mode, code):
    adapter, auth, workdir, _ = fixture_runtime(tmp_path, mode)
    with pytest.raises(RuntimeAdapterError) as error:
        run(adapter, auth, workdir)
    assert error.value.code == code


def test_authentication_fingerprint_prevents_switching_queued_identity(tmp_path):
    adapter, auth, workdir, _ = fixture_runtime(tmp_path)
    auth["expected_auth_fingerprint"] = "a" * 64
    with pytest.raises(RuntimeAdapterError, match="身份或模型凭据已改变"):
        run(adapter, auth, workdir)


def test_unknown_usage_is_not_zero(tmp_path):
    adapter, auth, workdir, _ = fixture_runtime(tmp_path, "unknown_usage")
    result, _ = run(adapter, auth, workdir)
    assert result["usage"] is None


def test_omitted_config_projection_requires_matching_session_origin(tmp_path):
    adapter, auth, workdir, _ = fixture_runtime(tmp_path, "projection")
    result, _ = run(adapter, auth, workdir)
    assert result["effective_model"] == "fixture-model"


def test_timeout_cleans_up_owned_process(tmp_path):
    adapter, auth, workdir, _ = fixture_runtime(tmp_path, "timeout", timeout_seconds=0.5)
    started = time.monotonic()
    with pytest.raises(RuntimeAdapterError) as error:
        run(adapter, auth, workdir)
    assert error.value.code == "runtime_timeout"
    assert time.monotonic() - started < 4


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group semantics")
def test_cancel_terminates_only_its_owned_process_group(tmp_path):
    adapter, auth, workdir, trace = fixture_runtime(tmp_path, "child")
    child_path = Path(str(trace) + ".child")

    class Cancelled(Exception):
        pass

    def active():
        if child_path.exists():
            raise Cancelled()

    with pytest.raises(Cancelled):
        run(adapter, auth, workdir, active=active)
    pid = int(child_path.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        # A reparented zombie has exited too; Darwin's launchd may reap later.
        import subprocess

        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        assert result.returncode or result.stdout.strip().startswith("Z")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group semantics")
def test_reaps_descendant_pipes_when_runtime_leader_exits(tmp_path):
    adapter, auth, workdir, trace = fixture_runtime(tmp_path, "orphan", timeout_seconds=0.7)
    started = time.monotonic()
    with pytest.raises(RuntimeAdapterError):
        run(adapter, auth, workdir)
    assert time.monotonic() - started < 4
    assert Path(str(trace) + ".child").exists()


@pytest.mark.parametrize("auth_type", ["api_key", "custom_headers", "none"])
def test_explicit_model_credentials_never_fall_back_to_ambient_auth(tmp_path, auth_type):
    home = tmp_path.resolve() / "auth"
    home.mkdir(mode=0o700)
    auth = {
        "runtime_home": str(home),
        "mode": "model_connection",
        "endpoint": "http://127.0.0.1:9876/v1",
        "auth_type": auth_type,
        "api_key": "fake-api-key",
        "headers": {"X-Test-Key": "fake-header"},
    }
    env, config, _ = _options(auth)
    assert config["model_providers.tradingagents_research.requires_openai_auth"] is False
    if auth_type == "api_key":
        assert env["TRADINGAGENTS_RUNTIME_API_KEY"] == "fake-api-key"
    else:
        assert "TRADINGAGENTS_RUNTIME_API_KEY" not in env
        assert "model_providers.tradingagents_research.env_key" not in config
    assert config["cli_auth_credentials_store"] == "keyring"
    assert config["features.hooks"] is False


def test_requires_dedicated_private_home_and_rejects_symlinks(tmp_path):
    adapter, auth, _, _ = fixture_runtime(tmp_path)
    with pytest.raises(RuntimeAdapterError, match="独立认证目录"):
        adapter.probe()
    target = tmp_path / "alias"
    target.symlink_to(auth["runtime_home"], target_is_directory=True)
    with pytest.raises(RuntimeAdapterError, match="符号链接"):
        adapter.probe(auth_options={**auth, "runtime_home": str(target)})


@pytest.mark.integration
@pytest.mark.parametrize("auth_type", ["none", "custom_headers", "api_key"])
def test_installed_codex_restricts_actual_responses_request_to_research_tools(tmp_path, auth_type):
    """Real protocol, fake localhost inference: never contact a model vendor."""
    executable = shutil.which("codex")
    if not executable:
        pytest.skip("optional Codex CLI is not installed")
    received = []
    request_headers = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(json.loads(body))
            request_headers.append({key.lower(): value for key, value in self.headers.items()})
            text = json.dumps({"ticker": "NVDA", "analysis_date": "2024-01-02",
                "summary": "offline response fixture", "analysis": "offline analysis",
                "bull_case": "offline bull case", "bear_case": "offline bear case",
                "risks": "offline risks", "conclusion": "offline conclusion",
                "evidence_refs": [], "source_coverage": "unavailable"})
            item = {
                "type": "message",
                "id": "msg_offline",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
            response = {
                "id": "resp_offline",
                "object": "response",
                "status": "completed",
                "model": "fixture-model",
                "output": [item],
                "usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "content": [], "status": "in_progress"},
                },
                {
                    "type": "response.content_part.added",
                    "item_id": "msg_offline",
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_offline",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                },
                {
                    "type": "response.output_text.done",
                    "item_id": "msg_offline",
                    "output_index": 0,
                    "content_index": 0,
                    "text": text,
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                self.wfile.write(
                    ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()
                )
            self.wfile.flush()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path.resolve()
    home, workdir = root / "auth", root / "work"
    home.mkdir(mode=0o700)
    workdir.mkdir(mode=0o700)
    try:
        adapter = CodexRuntimeAdapter(executable, timeout_seconds=20)
        auth = {
            "mode": "model_connection",
            "runtime_home": str(home),
            "auth_type": auth_type,
            "endpoint": f"http://127.0.0.1:{server.server_port}/v1",
        }
        if auth_type == "api_key":
            auth["api_key"] = "offline-fixture-key"
        if auth_type == "custom_headers":
            auth["headers"] = {"X-Offline-Key": "offline-header-fixture"}
        report_schema = LocalAgentReport.model_json_schema()
        assert set(report_schema["required"]) == set(report_schema["properties"])
        result, _ = run(adapter, auth, workdir, output_schema=report_schema)
        assert json.loads(result["report"])["summary"] == "offline response fixture"
        assert len(received) == 1
        assert received[0]["text"]["format"]["schema"] == report_schema
        names = []
        for tool in received[0]["tools"]:
            if tool.get("type") == "namespace":
                names.extend(item["name"] for item in tool["tools"])
            else:
                names.append(tool["name"])
        assert names == ["research_prices"], names
        if auth_type == "api_key":
            assert request_headers[0].get("authorization") == "Bearer offline-fixture-key"
        else:
            assert "authorization" not in {key.lower() for key in request_headers[0]}
        if auth_type == "custom_headers":
            assert request_headers[0].get("x-offline-key") == "offline-header-fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
