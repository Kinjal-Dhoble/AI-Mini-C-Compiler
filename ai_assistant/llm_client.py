"""Minimal Ollama HTTP client (standard library only).

Talks to a locally running Ollama server through its REST API:
  GET  /api/tags  -> list installed models (used as a health check)
  POST /api/chat  -> chat completion (non-streaming)

Configuration comes from arguments or environment variables:
  OLLAMA_HOST   default http://localhost:11434
  OLLAMA_MODEL  default qwen2.5-coder:7b
"""
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:7b"


THINKING_MODELS = ("qwen3", "deepseek-r1")
KEEP_ALIVE = "30m"          # keep the model in memory between requests (Ollama's default is 5 minutes)
MAX_ANSWER_TOKENS = 3072    # a hard stop for runaway answers; our longest (documented code) fits easily


def context_size(*texts, answer=MAX_ANSWER_TOKENS):
    """Smallest context window that fits the prompt and the answer. Code is about 3 characters per
    token, so this errs on the large side. A smaller window needs less memory, so the model is more
    likely to fit on the GPU, which is the biggest single speed factor."""
    need = sum(len(t) for t in texts) / 3 + answer + 256
    for size in (4096, 8192, 12288, 16384):
        if need <= size:
            return size
    return 16384


class LLMError(RuntimeError):
    """Raised when Ollama is unreachable or returns an unusable response."""


class OllamaClient:
    streams = True              # chat() can report progress while the model writes

    def __init__(self, model=None, host=None, timeout=180, num_ctx=8192, think=None):
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        # think=True keeps a reasoning model's thinking on (slower); default off, or set MINIC_THINK=1
        self.think = think if think is not None else os.environ.get("MINIC_THINK", "") == "1"
        host = host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
        if not host.startswith("http"):
            host = "http://" + host          # OLLAMA_HOST is often set as "0.0.0.0:11434"
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx

    # ------------------------------------------------------------------ HTTP
    def _request(self, path, payload=None, timeout=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.host + path, data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET")
        # A local Ollama must never be routed through HTTP(S)_PROXY settings.
        local = any(h in self.host for h in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({})) if local
                  else urllib.request.build_opener())
        try:
            with opener.open(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LLMError(f"Ollama returned HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise LLMError(
                f"Could not reach Ollama at {self.host} ({exc}). "
                "Start it with `ollama serve` and check OLLAMA_HOST.") from exc

    def _stream(self, path, payload, on_progress):
        """Like _request, but reads Ollama's line-by-line stream and reports progress.
        Returns the same shape as a non-streamed reply: {"message": {"content": ...}}."""
        import time
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.host + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
        local = any(h in self.host for h in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
        content, thinking, last = [], 0, 0.0
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                for raw in resp:
                    if not raw.strip():
                        continue
                    part = json.loads(raw.decode("utf-8"))
                    if part.get("error"):
                        raise LLMError(f"Ollama error: {part['error']}")
                    msg = part.get("message", {})
                    content.append(msg.get("content", ""))
                    thinking += len(msg.get("thinking", "") or "")
                    now = time.monotonic()
                    if now - last > 0.25 or part.get("done"):
                        last = now
                        try:
                            on_progress(sum(len(c) for c in content), thinking)
                        except Exception:
                            pass                 # progress display must never break a request
                    if part.get("done"):
                        break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LLMError(f"Ollama returned HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise LLMError(f"Could not reach Ollama at {self.host} ({exc}). "
                           "Start it with `ollama serve` and check OLLAMA_HOST.") from exc
        return {"message": {"content": "".join(content)}}

    # ------------------------------------------------------------ public API
    def list_models(self):
        tags = self._request("/api/tags", timeout=5)
        return sorted(m.get("name", "") for m in tags.get("models", []))

    def health_check(self):
        """Return None if the server is up and the model is installed, else raise LLMError."""
        tags = self._request("/api/tags", timeout=5)
        names = {m.get("name", "") for m in tags.get("models", [])}
        wanted = self.model if ":" in self.model else self.model + ":latest"
        if wanted not in names and self.model not in names:
            raise LLMError(
                f"Model '{self.model}' is not installed. Run `ollama pull {self.model}`. "
                f"Installed: {', '.join(sorted(names)) or '(none)'}")

    def warmup(self):
        """Load the model into memory now, so the student's first request does not wait for it."""
        self._request("/api/generate", {"model": self.model, "keep_alive": KEEP_ALIVE}, timeout=300)

    def chat(self, system, user, schema=None, temperature=0.2, history=None, on_progress=None):
        """Send one chat turn. If `schema` is given, Ollama constrains output to that JSON schema
        and the parsed dict is returned; otherwise the raw text is returned.
        on_progress(characters_written, thinking) is called while the model writes."""
        messages = [{"role": "system", "content": system}]
        messages += history or []
        messages.append({"role": "user", "content": user})
        num_ctx = min(self.num_ctx, context_size(system, user, *(m["content"] for m in history or [])))
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": on_progress is not None,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": temperature, "num_ctx": num_ctx, "num_predict": MAX_ANSWER_TOKENS},
        }
        if schema is not None:
            payload["format"] = schema
        thinking = self.model.split("/")[-1].lower().startswith(THINKING_MODELS)
        if thinking and not self.think:
            # Reasoning models write a long hidden "thinking" section before every answer, which
            # can make each request several times slower. Our prompts already contain the
            # compiler's facts, so ask Ollama (0.9+) to skip it.
            payload["think"] = False
        send = (lambda: self._stream("/api/chat", payload, on_progress)) if on_progress else (lambda: self._request("/api/chat", payload))
        try:
            reply = send()
        except LLMError as exc:
            if "think" not in payload or "think" not in str(exc).lower():
                raise
            payload.pop("think")                 # older Ollama: retry without the option
            reply = send()
        content = reply.get("message", {}).get("content", "")
        content = strip_thinking(content)
        if schema is None:
            return content
        return parse_json(content)


def strip_thinking(text):
    """Remove <think>...</think> blocks emitted by reasoning models (qwen3, deepseek-r1)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def parse_json(text):
    """Parse a JSON object, tolerating ```json fences or leading chatter."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Model did not return valid JSON. First 300 chars:\n{text[:300]}")
