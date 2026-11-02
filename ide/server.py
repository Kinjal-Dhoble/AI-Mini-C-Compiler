"""MiniC Studio: local web IDE server (Python standard library only).

Serves the single-page IDE from ide/static and a small JSON API:

  GET  /api/info                 toolchain (gcc/clang/tcc or VM), workspace, defaults
  GET  /api/files                .c files in the workspace
  GET  /api/file?name=x.c        read a file
  POST /api/file                 {name, content, backup}  save (backup -> x.c.bak once per session)
  POST /api/compile              {source, name}  full pipeline: tokens, AST, diagnostics, IR, CFG
  POST /api/build                {source, name, optimize}  IR -> C -> executable (+ assembly)
  POST /api/run                  {source, name}  build, then execute the executable
  POST /api/debug                {source}  run on the MiniC VM with a step-by-step trace
  GET  /api/ai/status?model=     is Ollama up, which models are installed
  POST /api/ai                   {feature: fix|optimize|docs|bugs|refactor|flow, source, corrected_from?, model, stdin}
  POST /api/parse_trace          {source}  LALR shift/reduce trace (PLY yacc), grammar, agreement with the hand parser
  POST /api/bison                {source}  build and run the flex + bison checker (grammar/minic.l, minic.y)
  compile/build/run/debug accept "frontend": "hand" | "yacc", and run/debug accept "stdin" (input for scanf)
"""
import dataclasses
import json
import mimetypes
import re
import shutil
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from compiler.pipeline import analyze
from compiler.diagnostics import enrich
from compiler import backend
from ai_assistant.ir_interpreter import run_program
from ai_assistant.llm_client import OllamaClient, LLMError

STATIC_DIR = Path(__file__).resolve().parent / "static"
ERROR_CATEGORIES = ("Semantic Error", "Type Error", "Syntax Error", "Lexical Error")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}\.c$")


class Config:
    workspace = Path("workspace")
    build_dir = Path("build")
    ollama_host = None
    default_model = None
    ai_timeout = 300


# Fixed types: on Windows, mimetypes reads the registry, which sometimes maps .js to text/plain
# and makes the browser refuse to start the editor's worker script.
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".ttf": "font/ttf",
                 ".svg": "image/svg+xml", ".png": "image/png", ".txt": "text/plain; charset=utf-8"}

_backed_up = set()
_lock = threading.Lock()


# --------------------------------------------------------------- pipeline
def ast_to_json(node):
    if dataclasses.is_dataclass(node):
        out = {"type": type(node).__name__.removesuffix("Node")}
        for f in dataclasses.fields(node):
            out[f.name] = ast_to_json(getattr(node, f.name))
        return out
    if isinstance(node, (list, tuple)):
        return [ast_to_json(x) for x in node]
    return node


def _diag(d, severity):
    e = enrich(d)
    return {"severity": severity, "category": d.get("category", "Error"), "message": d.get("message", ""),
            "line": d.get("line") or 1, "column": d.get("column") or 1, "explanation": e["explanation"],
            "suggestion": e.get("suggestion"), "recovery": d.get("recovery"), "rule": d.get("rule"),
            "expected": d.get("expected"), "found": d.get("found")}


def compile_source(source, frontend="hand"):
    """Run every compiler phase (compiler.pipeline) and shape the result for the browser."""
    r = analyze(source, frontend=frontend if frontend in ("hand", "yacc") else "hand")
    res = {"success": r["success"], "stage": r["stage"],
           "tokens": [dataclasses.asdict(t) for t in r["tokens"]],
           "ast": ast_to_json(r["ast"]) if r["ast"] is not None else None,
           "diagnostics": [_diag(d, "error") for d in r["errors"]]
                          + [_diag(d, "info" if d.get("category") == "Note" else "warning") for d in r["warnings"]]
                          + [_diag(d, "warning" if d["category"] == "Warning" else "info") for d in r["static"]],
           "ir": r["ir"], "line_map": r["line_map"], "cfg": r["cfg"], "call_graph": r["call_graph"],
           "functions": (r["call_graph"] or {}).get("functions", []), "loops": r["loops"],
           "ast_loops": r["ast_loops"], "complexity": r["complexity"], "unreachable": r["unreachable"],
           "frontend": r.get("frontend", "hand"), "reads_input": any(i.startswith(("READ_INT", "READ ")) for i in r["ir"])}
    if r["success"] and "main" not in res["functions"]:
        res["diagnostics"].append({"severity": "warning", "category": "Warning",
                                   "message": "No main function: the program compiles but cannot be run.",
                                   "line": 1, "column": 1, "explanation": "Execution starts at main().",
                                   "suggestion": "Add int main() { ... return 0; }"})
    return res


def _stem(name):
    return Path(name or "program.c").stem or "program"


def build_source(source, name, optimize=False, frontend="hand"):
    comp = compile_source(source, frontend)
    if not comp["success"]:
        return comp, {"ok": False, "log": "Fix the compile errors before building."}
    with _lock:
        b = backend.build(comp["ir"], comp["line_map"], Config.build_dir, _stem(name),
                          name or "program.c", optimize)
    b["assembly"] = backend.assembly(b["c_path"]) if b.get("mode") == "native" and b["ok"] else None
    return comp, b


# ------------------------------------------------------------------ files
def _safe(name):
    if not name or not NAME_RE.match(name):
        raise ValueError("File names may use letters, digits, _ . - and must end in .c")
    return Config.workspace / name


def list_files():
    Config.workspace.mkdir(parents=True, exist_ok=True)
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(Config.workspace.glob("*.c"))]


def save_file(name, content, backup=False):
    path = _safe(name)
    if backup and path.exists() and path not in _backed_up:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        _backed_up.add(path)
    path.write_text(content, encoding="utf-8")
    return {"saved": name, "backup": (path.name + ".bak") if path in _backed_up else None}


# --------------------------------------------------------------------- AI
# -------------------------------------------------------------- lex & yacc
def _hand_parse(source):
    from compiler.lexer import Lexer
    from compiler.parser import Parser
    try:
        return Parser(Lexer(source).tokenize()).parse()
    except SyntaxError:
        return None


def parse_trace(source):
    from compiler import yacc_frontend as Y
    y = Y.parse(source, trace=True, max_rows=4000)
    h = _hand_parse(source)
    if (h is not None) != y["accepted"]:
        agree = {"same": False, "detail": f"hand-written parser {'accepts' if h is not None else 'rejects'}, yacc {'accepts' if y['accepted'] else 'rejects'}"}
    elif h is not None:
        d = Y.same_ast(h, y["ast"])
        agree = {"same": d is None, "detail": "identical syntax trees" if d is None else "trees differ at " + d}
    else:
        agree = {"same": True, "detail": "both parsers reject this program"}
    info = Y.grammar_info()
    return {"trace": y["trace"], "truncated": y["trace_truncated"], "accepted": y["accepted"], "errors": y["errors"],
            "agreement": agree, "grammar": info, "bison": _bison_status()}


def _bison_status():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_yacc
    return build_yacc.status()


def bison_check(source):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_yacc
    from compiler import yacc_frontend as Y
    from compiler.parser import FunctionNode
    with _lock:
        r = build_yacc.check(source)
    if not r.get("ok"):
        return r
    h = _hand_parse(source)
    same = (h is not None) == r.get("accepted")
    if same and h is not None:
        same = (r.get("functions") == [s.name for s in h.statements if isinstance(s, FunctionNode)]
                and r.get("statements") == Y.statement_count(h))
    r["agrees"] = same
    return r


def ai_status(model=None):
    client = OllamaClient(model=model or Config.default_model, host=Config.ollama_host)
    try:
        models = client.list_models()
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "models": [], "model": client.model, "host": client.host}
    model_ok = client.model in models or client.model + ":latest" in models
    return {"ok": model_ok, "models": models, "model": client.model, "host": client.host,
            "error": None if model_ok else f"Model '{client.model}' is not installed. Run: ollama pull {client.model}"}


_AI_CACHE = {}                # (feature, model, source, ...) -> verified report; same question = instant answer
_WARMED = set()


def _cache_key(body):
    import hashlib
    parts = [body.get("feature"), body.get("model") or Config.default_model, body.get("source", ""),
             body.get("corrected_from") or "", body.get("stdin") or ""]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def warmup(model=None):
    """Load the model into Ollama's memory in the background (once per model)."""
    import threading
    model = model or Config.default_model
    if model in _WARMED:
        return {"ok": True, "already": True}
    _WARMED.add(model)
    client = OllamaClient(model=model, host=Config.ollama_host, timeout=Config.ai_timeout)

    def go():
        try:
            client.warmup()
        except LLMError:
            _WARMED.discard(model)
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


def ai_run(body, on_event=None):
    key = _cache_key(body)
    if not body.get("fresh") and key in _AI_CACHE:
        return dict(_AI_CACHE[key], cached=True)
    rep = _ai_run(body, on_event)
    v = rep.get("verification") if isinstance(rep, dict) else None
    if v and v.get("passed") and not rep.get("ai_error"):
        if len(_AI_CACHE) > 100:
            _AI_CACHE.pop(next(iter(_AI_CACHE)))
        _AI_CACHE[key] = rep
    return rep


def _ai_run(body, on_event=None):
    from ai_assistant.ai_features import AIFeatures
    client = OllamaClient(model=body.get("model") or Config.default_model, host=Config.ollama_host,
                          timeout=Config.ai_timeout)
    ai = AIFeatures(client, stdin=body.get("stdin") or None, on_event=on_event)
    feature, source = body.get("feature"), body.get("source", "")
    if feature == "fix":
        return ai.suggest_fixes(source)
    if feature == "optimize":
        return ai.suggest_optimizations(source)
    if feature == "docs":
        return ai.document_code(source, corrected_from=body.get("corrected_from"))
    if feature == "bugs":
        return ai.detect_bugs(source)
    if feature == "bugfix":
        return ai.detect_bugs(source, full_fix=True)
    if feature == "refactor":
        return ai.refactor(source)
    if feature == "flow":
        return ai.explain_flow(source)
    raise ValueError(f"unknown AI feature: {feature}")


def referee(body, action):
    """The compiler as referee, without Ollama: judge one answer, or generate wrong answers from the program."""
    from ai_assistant import referee as R
    source, stdin = body.get("source", ""), body.get("stdin") or None
    if action == "challenge":
        res = R.challenge(source, stdin)
        for c in res["cases"]:
            c.pop("code_full", None)
        return res
    return R.judge(source, body.get("feature", "optimize"), body.get("candidate", ""), stdin)


# ------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MiniCStudio/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            print("  " + fmt % args)

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _stream_ai(self, body):
        """Run an AI feature and send newline-delimited JSON: progress events, then the result."""
        import queue
        import threading
        q = queue.Queue()

        def work():
            try:
                q.put({"type": "result", "report": ai_run(body, on_event=q.put)})
            except LLMError as exc:
                q.put({"type": "error", "status": 502, "error": str(exc)})
            except Exception as exc:                      # report, keep serving
                traceback.print_exc()
                q.put({"type": "error", "status": 500, "error": f"{type(exc).__name__}: {exc}"})
        threading.Thread(target=work, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        while True:
            e = q.get()
            try:
                self.wfile.write((json.dumps(e) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return                                    # the browser went away; the work finishes quietly
            if e.get("type") in ("result", "error"):
                return

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/info":
                return self._send(200, {"toolchain": backend.toolchain_info(),
                                        "workspace": str(Config.workspace.resolve()),
                                        "build_dir": str(Config.build_dir.resolve()),
                                        "default_model": OllamaClient(model=Config.default_model).model})
            if url.path == "/api/files":
                return self._send(200, list_files())
            if url.path == "/api/file":
                return self._send(200, {"name": q["name"], "content": _safe(q["name"]).read_text(encoding="utf-8")})
            if url.path == "/api/ai/status":
                return self._send(200, ai_status(q.get("model")))
            rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            path = (STATIC_DIR / rel).resolve()
            if STATIC_DIR in path.parents and path.is_file():
                ctype = CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                return self._send(200, path.read_bytes(), ctype)
            self._send(404, {"error": "not found"})
        except (KeyError, ValueError, OSError) as exc:
            self._send(400, {"error": str(exc)})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/file":
                return self._send(200, save_file(body["name"], body.get("content", ""), body.get("backup", False)))
            fe = body.get("frontend", "hand")
            if url.path == "/api/compile":
                return self._send(200, compile_source(body.get("source", ""), fe))
            if url.path == "/api/build":
                comp, b = build_source(body.get("source", ""), body.get("name"), body.get("optimize", False), fe)
                return self._send(200, {"compile": comp, "build": b})
            if url.path == "/api/run":
                comp, b = build_source(body.get("source", ""), body.get("name"), body.get("optimize", False), fe)
                run = backend.run_executable(b, timeout=body.get("timeout", 5), stdin=body.get("stdin", "")) if b.get("ok") else None
                return self._send(200, {"compile": comp, "build": b, "run": run})
            if url.path == "/api/debug":
                comp = compile_source(body.get("source", ""), fe)
                vm = run_program(comp["ir"], comp["line_map"], trace=True, max_steps=200_000,
                                 stdin=body.get("stdin", "")) if comp["success"] else None
                return self._send(200, {"compile": comp, "vm": vm})
            if url.path == "/api/parse_trace":
                return self._send(200, parse_trace(body.get("source", "")))
            if url.path == "/api/bison":
                return self._send(200, bison_check(body.get("source", "")))
            if url.path == "/api/referee/challenge":
                return self._send(200, referee(body, "challenge"))
            if url.path == "/api/referee/judge":
                return self._send(200, referee(body, "judge"))
            if url.path == "/api/ai/warmup":
                return self._send(200, warmup(body.get("model")))
            if url.path == "/api/ai" and body.get("stream"):
                return self._stream_ai(body)
            if url.path == "/api/ai":
                return self._send(200, ai_run(body))
            self._send(404, {"error": "not found"})
        except LLMError as exc:
            self._send(502, {"error": str(exc)})
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:                         # report, keep serving
            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def serve(host="127.0.0.1", port=8765):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
