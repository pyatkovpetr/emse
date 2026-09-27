"""Serial research gateway. Keeps provider credentials out of Orion's environment.

Only the supplied endpoint is contacted. Request/response bodies contain task
data, never Authorization headers. Caps count actual provider requests, including
retry requests. A cap is an experimental budget outcome, not a provider failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENDPOINT = "https://api.neuraldeep.tech/v1"
MODEL = os.environ["F10_MODEL"]

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def request(key, route, payload=None, timeout=90):
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    req = urllib.request.Request(ENDPOINT + route,
        data=None if payload is None else json.dumps(payload).encode(), headers=headers)
    with _OPENER.open(req, timeout=timeout) as response:
        return response.status, response.read()


def preflight(key, output):
    start = time.monotonic()
    report = {"endpoint": ENDPOINT, "requested_model": MODEL, "model_calls": 0}
    try:
        status, body = request(key, "/models")
        models = json.loads(body).get("data", [])
        report.update(models_http=status, requested_model_listed=any(x.get("id") == MODEL for x in models))
        payload = {"model": MODEL, "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                   "max_tokens": 16, "temperature": 0, "stream": False,
                   "chat_template_kwargs": {"enable_thinking": False}}
        report["model_calls"] += 1
        status, body = request(key, "/chat/completions", payload)
        data = json.loads(body)
        report.update(chat_http=status, returned_model=data.get("model"), usage=data.get("usage"),
                      response=data.get("choices", [{}])[0].get("message", {}).get("content"),
                      status="PASS")
    except Exception as exc:
        # Never emit the HTTP request, headers or full error body.
        report.update(status="FAIL", error_type=type(exc).__name__, http_code=getattr(exc, "code", None))
    report["wall_sec"] = round(time.monotonic()-start, 3)
    write_json(output, report)
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return report


class Gateway:
    def __init__(self, key, root, max_requests=24, timeout=120, neutral_paths=None):
        self._key = key
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_requests, self.timeout = max_requests, timeout
        self.lock = threading.Lock()
        self.forward_lock = threading.Lock()
        self.requests = 0
        self.limit_reached = False
        self.receipts = []
        self.neutral_paths = dict(neutral_paths or {})
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, value):
                body = json.dumps(value).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Local client termination after a research deadline must
                    # not relabel an already complete paid provider response.
                    pass

            def do_GET(self):
                if self.path.endswith("/models"):
                    return self.send_json(200, {"data": [{"id": MODEL, "object": "model"}]})
                return self.send_json(404, {"error": "unknown route"})

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    return self.send_json(404, {"error": "unknown route"})
                with owner.lock:
                    if owner.max_requests is not None and owner.requests >= owner.max_requests:
                        owner.limit_reached = True
                        return self.send_json(429, {"error": {"message": "RESEARCH_REQUEST_BUDGET_EXHAUSTED", "type": "research_budget"}})
                    owner.requests += 1
                    index = owner.requests
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(raw)
                call_root = owner.root / f"request-{index:04d}"
                # Preserve exactly what Orion sent, then hide arm/run directory
                # names from the model. Both arms use the same virtual paths.
                write_json(call_root / "orion-request.json", payload)
                payload = neutralize(payload, owner.neutral_paths)
                # Orion talks only to loopback; this gateway pins the external model.
                payload["model"] = MODEL
                payload["stream"] = False
                payload.pop("stream_options", None)
                payload.pop("chat_template_kwargs", None)
                write_json(call_root / "request.json", payload)
                started = time.monotonic()
                receipt = {"index": index, "external_model_api_call": True,
                           "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
                try:
                    with owner.forward_lock:
                        status, body = request(owner._key, "/chat/completions", payload, owner.timeout)
                    data = json.loads(body)
                    write_json(call_root / "response.json", data)
                    receipt.update(status="COMPLETE", http_status=status, usage=data.get("usage", {}),
                                   model=data.get("model"), response_sha256=hashlib.sha256(body).hexdigest())
                    self.send_json(status, data)
                except Exception as exc:
                    receipt.update(status="PROVIDER_FAILURE", error_type=type(exc).__name__, http_status=getattr(exc, "code", None))
                    self.send_json(502, {"error": {"message": "recorded provider failure", "type": type(exc).__name__}})
                finally:
                    receipt["wall_sec"] = round(time.monotonic()-started, 6)
                    write_json(call_root / "receipt.json", receipt)
                    with owner.lock:
                        owner.receipts.append(receipt)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Drain the current request before another trial begins, including after
        # an Orion process deadline. Waiting is bounded by the provider timeout.
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def neutralize(value, paths):
    """Replace only explicit local path prefixes, including nested JSON strings.

    Evidence IDs and response bytes are unchanged. Orion must recognize the
    virtual workspace alias, so no unrecorded response rewriting is needed.
    """
    if isinstance(value, dict):
        return {key: neutralize(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [neutralize(item, paths) for item in value]
    if isinstance(value, str):
        for actual, virtual in sorted(paths.items(), key=lambda pair: -len(pair[0])):
            for spelling in sorted({actual, actual.replace('\\', '/'), actual.replace('\\', '\\\\')}, key=len, reverse=True):
                value = value.replace(spelling, virtual)
        return value
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = preflight(Path(args.key_file).read_text(encoding="utf-8").strip(), args.output)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
