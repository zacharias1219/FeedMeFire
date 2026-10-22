"""A stand-in for Sarvam's chat-completions API. It behaves like a model: it reads the real request, writes a report from
the items it was given (so different input gives different output), wraps it in <think>, and records every request.
Run alone:  python tests/e2e/mock_sarvam.py 8199   then  SARVAM_BASE_URL=http://127.0.0.1:8199 SARVAM_API_KEY=x uvicorn app.main:app
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def build_report(user: dict) -> dict:
    items = user["sample_items"]
    n = len(items)
    hi = user["output_language"].startswith("Hindi")
    t = lambda k, w=34: items[k % n]["text"][:w]  # noqa: E731
    obs = [{"title": f"Verdict {k + 1}", "joke": f"MOCKMODEL on “{t(4 * k)}”, “{t(4 * k + 1)}”, “{t(4 * k + 2)}” and “{t(4 * k + 3)}”: a very specific roast.",
            "evidence_item_ids": [items[(4 * k + j) % n]["id"] for j in range(4)]} for k in range(6 if n >= 12 else 4)]
    return {
        "schema_version": "1.0", "headline": ("नमस्ते MOCKMODEL: " if hi else "MOCKMODEL: ") + t(0, 50),
        "persona": {"label": f"The {t(1, 20)} Devotee", "description": "A fictional viewing persona."},
        "opening_roast": f"MOCKMODEL looked at all {n} items, starting with “{t(0)}”, and has thoughts.",
        "observations": obs,
        "contradictions": [{"joke": f"“{t(20)}” versus “{t(21)}”: pick a lane.", "evidence_item_ids": [items[20 % n]["id"], items[21 % n]["id"]]},
                           {"joke": f"“{t(22)}” and “{t(23)}” cannot both be a priority.", "evidence_item_ids": [items[22 % n]["id"], items[23 % n]["id"]]}],
        "affectionate_closer": f"MOCKMODEL's last word is about “{t(2)}”.", "share_quote": f"MOCKMODEL says {t(3, 40)} is my whole personality.",
        "disclaimer": "replaced by the app",
    }


class Mock:
    def __init__(self, port: int = 0):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                system, user = body["messages"][0]["content"], body["messages"][1]["content"]
                if system.startswith("You are a strict content-safety reviewer"):
                    content = "<think>checking</think>SAFE"
                else:
                    content = "<think>I will now plan the jokes {not json}</think>```json\n" + json.dumps(build_report(json.loads(user)), ensure_ascii=False) + "\n```"
                out = {"id": "mock", "object": "chat.completion", "created": 1, "model": body["model"],
                       "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content, "reasoning_content": "secret reasoning"}}],
                       "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(content) // 4, "total_tokens": 0}}
                data = json.dumps(out).encode()
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


if __name__ == "__main__":
    m = Mock(int(sys.argv[1]) if len(sys.argv) > 1 else 8199)
    print("mock Sarvam on", m.url, flush=True)
    threading.Event().wait()
