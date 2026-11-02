"""Tests for the speed features: streaming with progress, context sizing, thinking off, caching."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ai_assistant.llm_client import OllamaClient, context_size, MAX_ANSWER_TOKENS


class _Ollama(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Ollama.requests.append((self.path, req))
        answer = json.dumps({"summary": "ok", "fixed_code": "int main() { return 0; }"})
        self.send_response(200)
        self.end_headers()
        if req.get("stream"):
            for i in range(0, len(answer), 10):
                self.wfile.write((json.dumps({"message": {"content": answer[i:i + 10]}, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())
        else:
            self.wfile.write(json.dumps({"message": {"content": answer}, "done": True}).encode())


class SpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.host = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        _Ollama.requests.clear()

    def test_streaming_reports_progress_and_returns_the_same_answer(self):
        seen = []
        c = OllamaClient(model="qwen2.5-coder:7b", host=self.host)
        out = c.chat("sys", "user", schema={"type": "object"}, on_progress=lambda chars, thinking: seen.append(chars))
        self.assertEqual(out["summary"], "ok")
        self.assertTrue(seen and seen[-1] > 0)
        req = _Ollama.requests[-1][1]
        self.assertTrue(req["stream"])
        self.assertEqual(req["keep_alive"], "30m")
        self.assertEqual(req["options"]["num_predict"], MAX_ANSWER_TOKENS)
        self.assertNotIn("think", req)                       # not a thinking model

    def test_thinking_is_switched_off_for_qwen3(self):
        OllamaClient(model="qwen3:8b", host=self.host).chat("s", "u", schema={"type": "object"})
        self.assertIs(_Ollama.requests[-1][1]["think"], False)

    def test_context_window_fits_the_prompt(self):
        self.assertEqual(context_size("x" * 300), 4096)
        self.assertEqual(context_size("x" * 20000), 12288)
        c = OllamaClient(model="m", host=self.host)
        c.chat("s", "short prompt", schema={"type": "object"})
        self.assertEqual(_Ollama.requests[-1][1]["options"]["num_ctx"], 4096)

    def test_warmup_loads_the_model(self):
        OllamaClient(model="m", host=self.host).warmup()
        self.assertEqual(_Ollama.requests[-1][0], "/api/generate")

    def test_ide_caches_verified_answers(self):
        from ide import server
        calls = []

        class Fake:
            streams = False
            def __init__(self, *a, **k): pass
            def chat(self, system, user, schema=None, temperature=0.2, history=None):
                calls.append(1)
                return {"overview": "x", "documented_code": "// doc\nint main() {\n    return 0;\n}\n"}
        old = server.OllamaClient
        server.OllamaClient = Fake
        try:
            body = {"feature": "docs", "source": "int main() {\n    return 0;\n}\n", "model": "m"}
            first = server.ai_run(dict(body))
            second = server.ai_run(dict(body))
            third = server.ai_run(dict(body, fresh=True))
        finally:
            server.OllamaClient = old
        self.assertTrue(first["verification"]["passed"])
        self.assertTrue(second.get("cached"))
        self.assertFalse(third.get("cached", False))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
