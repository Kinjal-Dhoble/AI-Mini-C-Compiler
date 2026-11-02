"""Loads the Markdown "skill" files that tell the LLM how to do each job.

A skill file looks like this:

    ---
    name: fix-suggestions
    description: one line
    temperature: 0.1
    max_retries: 2
    include: _mini_c_language.md
    ---
    # Role ...            <- body becomes the system prompt

`include` lists other files in the skills folder (comma separated) whose bodies are
prepended to this skill's body, so the language rules live in one place.
"""
from dataclasses import dataclass, field
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass
class Skill:
    name: str
    description: str
    system_prompt: str
    temperature: float = 0.2
    max_retries: int = 1
    meta: dict = field(default_factory=dict)


def _split_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    _, header, body = text.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def load_skill(name, skills_dir=SKILLS_DIR):
    skills_dir = Path(skills_dir)
    path = skills_dir / (name if name.endswith(".md") else name + ".md")
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {path}")
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    parts = []
    for inc in filter(None, (s.strip() for s in meta.get("include", "").split(","))):
        _, inc_body = _split_frontmatter((skills_dir / inc).read_text(encoding="utf-8"))
        parts.append(inc_body)
    parts.append(body)
    return Skill(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        system_prompt="\n\n".join(parts),
        temperature=float(meta.get("temperature", 0.2)),
        max_retries=int(meta.get("max_retries", 1)),
        meta=meta,
    )
