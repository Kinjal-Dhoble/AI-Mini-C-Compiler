"""Start MiniC Studio, the browser-based IDE for the AI-assisted Mini C compiler.

    python ide.py                      # opens http://127.0.0.1:8765 in your browser
    python ide.py --port 9000 --workspace my_programs --model qwen3:8b
"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ide import server  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="MiniC Studio - web IDE for the Mini C compiler")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other devices on your network")
    ap.add_argument("--workspace", default=str(ROOT / "workspace"), help="folder with your .c files")
    ap.add_argument("--build-dir", default=str(ROOT / "build"), help="where executables are written")
    ap.add_argument("--model", default=None, help="default Ollama model (else $OLLAMA_MODEL or qwen2.5-coder:7b)")
    ap.add_argument("--ollama-host", default=None)
    ap.add_argument("--ai-timeout", type=int, default=300, help="seconds per LLM reply")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    server.Config.workspace = Path(args.workspace)
    server.Config.build_dir = Path(args.build_dir)
    server.Config.default_model = args.model
    server.Config.ollama_host = args.ollama_host
    server.Config.ai_timeout = args.ai_timeout
    server.Config.workspace.mkdir(parents=True, exist_ok=True)

    try:
        httpd = server.serve(args.host, args.port)
    except OSError as exc:
        print(f"Could not start on port {args.port}: {exc}\nTry: python ide.py --port {args.port + 1}")
        sys.exit(1)
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    tc = server.backend.toolchain_info()
    print("MiniC Studio running at", url)
    print("  workspace :", server.Config.workspace.resolve())
    print("  executables:", tc["version"] if tc["mode"] == "native" else
          "MiniC VM (no gcc/clang/tcc found - install MinGW-w64 for native .exe files)")
    print("  Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
