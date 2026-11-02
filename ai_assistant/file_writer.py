"""Writes LLM-proposed code back into the source file, only after the user confirms.

Safety measures:
  - a unified diff of the exact change is shown before asking
  - unverified output defaults to "no" and needs a typed "yes"
  - the original is copied to <file>.bak (first backup is never overwritten: .bak, .bak1, ...)
  - the write is refused if the file changed on disk since it was read
  - the new content is written to a temp file and swapped in atomically
"""
import difflib
import os
import shutil
import sys
import tempfile
from pathlib import Path


def show_diff(old, new, name):
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     fromfile=f"{name} (current)", tofile=f"{name} (proposed)",
                                     lineterm="", n=2))
    if not diff:
        print("  (no changes)")
        return False
    color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    for line in diff:
        if color and line.startswith("+") and not line.startswith("+++"):
            line = f"\033[32m{line}\033[0m"
        elif color and line.startswith("-") and not line.startswith("---"):
            line = f"\033[31m{line}\033[0m"
        print("  " + line)
    return True


def confirm(question, verified, input_fn=input):
    """Ask y/n. Verified changes accept 'y'; unverified ones need the full word 'yes'."""
    if verified:
        prompt = f"\n  {question} [y/N]: "
        ok = {"y", "yes"}
    else:
        print("\n  WARNING: this change did NOT pass the compiler's verification.")
        prompt = f"  {question} Type 'yes' to apply anyway [no]: "
        ok = {"yes"}
    try:
        return input_fn(prompt).strip().lower() in ok
    except EOFError:            # not running in an interactive terminal
        print("\n  (no interactive input available; file left unchanged)")
        return False


def _backup_path(path):
    candidate = path.with_name(path.name + ".bak")
    i = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak{i}")
        i += 1
    return candidate


def offer_new_file(path, text, verified, what, input_fn=input, auto_yes=False):
    """Offer to save `text` as a separate file (used when the original is left untouched)."""
    path = Path(path)
    exists = " (it already exists and will be replaced)" if path.exists() else ""
    if auto_yes and verified:
        print(f"  --yes given: saving {what} to {path.name}.")
    elif not confirm(f"Save the {what} as {path.name}{exists}?", verified, input_fn):
        print("  Not saved.")
        return False
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    print(f"  Saved to {path}")
    return True


class SourceFile:
    """Tracks one source file on disk so later writes can detect outside edits."""

    def __init__(self, path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.newline = "\r\n" if b"\r\n" in raw else "\n"
        self.text = raw.decode("utf-8").replace("\r\n", "\n")
        self.mtime = self.path.stat().st_mtime_ns
        self.backup = None

    def write(self, new_text):
        if self.path.stat().st_mtime_ns != self.mtime:
            raise RuntimeError(f"{self.path} was changed by another program since it was read; "
                               "not overwriting it. Re-run the compiler.")
        if self.backup is None:                        # one backup per run: the true original
            self.backup = _backup_path(self.path)
            shutil.copy2(self.path, self.backup)
        if not new_text.endswith("\n"):
            new_text += "\n"
        data = new_text.replace("\n", self.newline).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self.text = new_text
        self.mtime = self.path.stat().st_mtime_ns

    def offer(self, new_text, verified, what, input_fn=input, auto_yes=False):
        """Show the diff, ask, and write. Returns True if the file was changed."""
        print(f"\n  Proposed change to {self.path.name} ({what}):")
        if not show_diff(self.text.rstrip("\n"), new_text.rstrip("\n"), self.path.name):
            return False
        if auto_yes and verified:
            print("  --yes given: applying verified change.")
        elif not confirm(f"Write the {what} into {self.path.name}?", verified, input_fn):
            print("  Skipped; file unchanged.")
            return False
        self.write(new_text)
        print(f"  Written to {self.path}  (original saved as {self.backup.name})")
        return True
