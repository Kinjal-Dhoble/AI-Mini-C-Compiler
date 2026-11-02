"""Measure how much faster the AI assistant's new settings are on YOUR computer.

    python tools/speedtest.py                      # uses $OLLAMA_MODEL or qwen2.5-coder:7b
    python tools/speedtest.py --model qwen3:8b
    python tools/speedtest.py --model qwen3:8b --file workspace/functions.c

It sends the same real request (beginner documentation of one program) twice:

  before   the old settings: thinking left on, the IR listing in the prompt, a fixed 8192-token
           context, and the model loaded fresh (as after Ollama's default 5-minute unload)
  after    the new settings: thinking off, no IR for documentation, a context sized to the prompt,
           an answer-length cap, and the model already loaded (keep_alive + warm-up)

and prints Ollama's own timing for each part: loading, reading the prompt, writing the answer.
A third line shows the cached repeat, which never reaches the model.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_assistant.ai_features import DOC_SCHEMA                    # noqa: E402
from ai_assistant.context_builder import build_context, check_source  # noqa: E402
from ai_assistant.llm_client import OllamaClient, LLMError, context_size, MAX_ANSWER_TOKENS, KEEP_ALIVE, THINKING_MODELS  # noqa: E402
from ai_assistant.skill_loader import load_skill                    # noqa: E402

S = 1e9   # Ollama reports nanoseconds


def run(client, payload):
    t0 = time.perf_counter()
    r = client._request("/api/chat", payload, timeout=1800)
    wall = time.perf_counter() - t0
    msg = r.get("message", {})
    return {"wall": wall, "load": r.get("load_duration", 0) / S,
            "prompt_tokens": r.get("prompt_eval_count", 0), "prompt_s": r.get("prompt_eval_duration", 0) / S,
            "answer_tokens": r.get("eval_count", 0), "answer_s": r.get("eval_duration", 0) / S,
            "thinking_chars": len(msg.get("thinking", "") or ""), "answer_chars": len(msg.get("content", ""))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--file", default=str(ROOT / "workspace" / "complexity.c"))
    args = ap.parse_args()
    client = OllamaClient(model=args.model, timeout=1800)
    try:
        client.health_check()
    except LLMError as exc:
        print(exc); return 1
    src = Path(args.file).read_text(encoding="utf-8")
    check = check_source(src)
    skill = load_skill("beginner_docs")
    header = "MODE: compiled. Add beginner-friendly comments to this program that compiled successfully.\n\n"
    old_prompt = header + build_context(src, check, include_ir=True)
    new_prompt = header + build_context(src, check, include_ir=False)
    msgs = lambda p: [{"role": "system", "content": skill.system_prompt}, {"role": "user", "content": p}]
    thinking_model = client.model.split("/")[-1].lower().startswith(THINKING_MODELS)

    print(f"Model {client.model} · program {Path(args.file).name} · this can take a few minutes on a laptop\n")
    print("Unloading the model, to measure a cold start as the old settings had after 5 idle minutes...")
    client._request("/api/generate", {"model": client.model, "keep_alive": 0})
    time.sleep(2)

    before = {"model": client.model, "messages": msgs(old_prompt), "stream": False, "format": DOC_SCHEMA,
              "options": {"temperature": skill.temperature, "num_ctx": 8192}}
    if thinking_model:
        before["think"] = True
    print("Running BEFORE (old settings)...")
    b = run(client, before)

    after = {"model": client.model, "messages": msgs(new_prompt), "stream": False, "format": DOC_SCHEMA, "keep_alive": KEEP_ALIVE,
             "options": {"temperature": skill.temperature, "num_ctx": context_size(skill.system_prompt, new_prompt),
                         "num_predict": MAX_ANSWER_TOKENS}}
    if thinking_model:
        after["think"] = False
    print("Running AFTER (new settings, model already loaded)...\n")
    a = run(client, after)

    rows = [("Total wait", "wall"), ("  loading the model", "load"), ("  reading the prompt", "prompt_s"), ("  writing the answer", "answer_s")]
    print(f"{'':24}{'before':>10}{'after':>10}{'saved':>9}")
    for label, k in rows:
        saved = f"{(1 - a[k] / b[k]) * 100:.0f}%" if b[k] > 0.05 else "-"
        print(f"{label:24}{b[k]:>9.1f}s{a[k]:>9.1f}s{saved:>9}")
    print(f"{'  prompt size':24}{b['prompt_tokens']:>8} tk{a['prompt_tokens']:>8} tk")
    print(f"{'  answer size':24}{b['answer_tokens']:>8} tk{a['answer_tokens']:>8} tk"
          + (f"   (before included {b['thinking_chars']:,} characters of hidden thinking)" if b["thinking_chars"] else ""))
    print(f"{'Same question again':24}{b['wall']:>9.1f}s{0:>9.1f}s{'100%':>9}   (cached in the IDE: no model call)")
    print(f"\nOverall: {b['wall']:.0f} s -> {a['wall']:.0f} s, {b['wall'] / max(a['wall'], 0.1):.1f}x faster for this request.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
