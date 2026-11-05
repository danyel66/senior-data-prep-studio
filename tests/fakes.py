"""Local fake Anthropic (SSE streaming) and Ollama (NDJSON) servers for offline tests.

Each fake serves a queue of canned JSON payloads (one per request) and records request bodies,
so tests can assert on exactly what the app sent.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _FakeServer:
    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[str] = []          # JSON texts returned in order (last one repeats)
        self.status = 200
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def next_text(self) -> str:
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeAnthropic(_FakeServer):
    model = "claude-opus-5-5"

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if fake.status != 200:
                    payload = json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                                      "message": "invalid x-api-key"}}).encode()
                    self.send_response(fake.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                text = fake.next_text()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def ev(name, data):
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()

                ev("message_start", {"type": "message_start", "message": {
                    "id": "msg_test", "type": "message", "role": "assistant", "model": fake.model, "content": [],
                    "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 120, "output_tokens": 1, "cache_creation_input_tokens": 5000,
                              "cache_read_input_tokens": 0}}})
                ev("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "thinking_delta", "thinking": "Picking a skewed table."}})
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "signature_delta", "signature": "sig"}})
                ev("content_block_stop", {"type": "content_block_stop", "index": 0})
                ev("content_block_start", {"type": "content_block_start", "index": 1,
                                           "content_block": {"type": "text", "text": ""}})
                for i in range(0, len(text), 200):
                    ev("content_block_delta", {"type": "content_block_delta", "index": 1,
                                               "delta": {"type": "text_delta", "text": text[i:i + 200]}})
                ev("content_block_stop", {"type": "content_block_stop", "index": 1})
                ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                     "usage": {"output_tokens": 1500}})
                ev("message_stop", {"type": "message_stop"})

        return H


class FakeOllama(_FakeServer):
    models = ["qwen2.5-coder:14b", "llama3.1:8b"]

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                payload = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                if self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m} for m in fake.models]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "body": body})
                if body.get("model") not in fake.models:
                    self._json(404, {"error": f"model '{body.get('model')}' not found"})
                    return
                text = fake.next_text()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for i in range(0, len(text), 150):
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": text[i:i + 150]},
                                                  "done": False}) + "\n").encode())
                self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                              "done_reason": "stop", "prompt_eval_count": 4000,
                                              "eval_count": 900}) + "\n").encode())

        return H


def sql_challenge(**overrides) -> str:
    data = {
        "title": "Territory momentum with skew-aware ranking",
        "category": "Advanced SQL", "difficulty": "Senior", "estimated_minutes": 25,
        "topics": ["window functions"],
        "scenario": "Finance needs monthly revenue momentum per territory.",
        "tasks": ["Compute monthly revenue per territory.", "Add month-over-month delta."],
        "deliverable": "One SQL query.",
        "tables_used": ["sales.sales_order_header", "sales.sales_territory"],
        "expected_columns": ["month", "territory", "revenue", "mom_delta"],
        "order_matters": True,
        "hints": ["Aggregate first.", "Use LAG.", "PARTITION BY territory ORDER BY month."],
        "reference_solution": (
            "WITH m AS (SELECT date_trunc('month', h.order_date) AS month, t.name AS territory, "
            "sum(h.total_due) AS revenue FROM sales.sales_order_header h "
            "JOIN sales.sales_territory t USING (territory_id) GROUP BY 1, 2) "
            "SELECT month, territory, revenue, revenue - lag(revenue) OVER (PARTITION BY territory ORDER BY month) "
            "AS mom_delta FROM m ORDER BY territory, month"),
        "solution_explanation": "Aggregate, then LAG over each territory.",
        "additional_artifacts": "",
        "follow_ups": ["How would you index this on Postgres?"],
    }
    data.update(overrides)
    return json.dumps(data)
