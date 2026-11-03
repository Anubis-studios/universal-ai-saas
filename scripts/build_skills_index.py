#!/usr/bin/env python3
"""Build backend/app/data/skills_index.json.gz from an Agentic Awesome Skills checkout.

Usage:
    python scripts/build_skills_index.py /path/to/agentic-awesome-skills-main

Reads every ``skills/*/SKILL.md``, parses its YAML front matter, and writes the
compact JSON index the API loads at startup. No third-party dependencies, so it
runs anywhere Python does.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "data" / "skills_index.json.gz"
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
LIST_KEYS = {"tags", "tools", "allowed-tools"}
ACRONYMS = {"seo", "aws", "api", "ai", "ml", "sql", "css", "html", "ux", "ui", "ci", "cd",
            "csv", "pdf", "llm", "rag", "gcp", "k8s", "sdk", "cli", "json", "yaml", "http",
            "rest", "grpc", "cron", "ddd", "tdd", "etl", "nlp", "ocr", "vpn", "sso", "iac"}


def parse_scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return raw


def parse_front_matter(text: str) -> tuple[dict, str]:
    match = FRONT.match(text.lstrip("\ufeff"))
    if not match:
        return {}, text
    meta: dict = {}
    key = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and key:          # nested block / list item
            item = line.strip()
            if item.startswith("- "):
                meta.setdefault(key, [])
                if isinstance(meta[key], list):
                    meta[key].append(parse_scalar(item[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        meta[key] = parse_scalar(value)
    return meta, match.group(2)


def as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [v for v in re.split(r"[,\s]+", value) if v]
    return []


def load_catalogue_index(root: Path) -> dict[str, dict]:
    """The upstream repo ships a classified index; trust it over guesswork."""
    for candidate in (root / "skills_index.json", root / "data" / "skills_index.json"):
        if candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else data.get("skills", [])
            return {e.get("path", "").rstrip("/").split("/")[-1] or e["name"]: e for e in entries}
    return {}


def build(root: Path) -> list[dict]:
    skills_dir = root / "skills" if (root / "skills").is_dir() else root
    catalogue = load_catalogue_index(root)
    rows, seen = [], set()
    for md in sorted(skills_dir.glob("*/SKILL.md")):
        meta, body = parse_front_matter(md.read_text(encoding="utf-8", errors="replace"))
        classified = catalogue.get(md.parent.name, {})
        name = str(meta.get("name") or classified.get("name") or md.parent.name).strip()
        if not name or md.parent.name in seen:
            continue
        seen.add(md.parent.name)
        if not body.strip():
            body = md.read_text(encoding="utf-8", errors="replace")
        description = " ".join(str(classified.get("description") or meta.get("description", "")).split())
        # Upstream often omits tags; name tokens make a usable substitute.
        tags = as_list(meta.get("tags")) or as_list(classified.get("keywords"))
        if not tags:
            tags = [t for t in re.split(r"[-_\s]+", name.lower()) if len(t) > 2]
        rows.append(
            {
                "name": name,
                "title": " ".join(
                    w.upper() if w.lower() in ACRONYMS else w.capitalize()
                    for w in re.split(r"[-_\s]+", name)
                    if w
                ).strip(),
                "category": str(
                    classified.get("category") or meta.get("category") or "general"
                ).strip().lower(),
                "description": description,
                "tags": tags,  # stored as a real list; the API joins on load
                "risk": str(classified.get("risk") or meta.get("risk") or "safe").strip().lower(),
                "source": str(classified.get("source") or meta.get("source") or "community").strip().lower(),
                "path": str(md.relative_to(root)),
                "instructions": body.strip()[:2000],
            }
        )
    return rows


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    rows = build(Path(sys.argv[1]).expanduser().resolve())
    if not rows:
        print("No SKILL.md files found.", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode(), 9))
    cats = {r["category"] for r in rows}
    print(f"wrote {OUT} — {len(rows)} skills, {len(cats)} categories, {OUT.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
