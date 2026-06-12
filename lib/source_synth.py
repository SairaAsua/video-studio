#!/usr/bin/env python3
"""
source_synth.py — Generic source-to-script synthesis.

Accepts diverse inputs (PDFs, markdown, plain text, URLs) and produces
a narrative spine (SPINE.md) or full script (script.md) by extracting
claims, clustering them into beats, and emitting structured output.

Usage:
    python lib/source_synth.py <source-path> [--slug <slug>] [--output script|spine]
    python lib/source_synth.py <url>          [--slug <slug>] [--output script|spine]

Architecture:
    1. Ingest: read source (pymupdf for PDF, requests for URL, plain read for text/md)
    2. Chunk: split into ~2000-token segments
    3. Map: per chunk, extract {claims, data_points, quotes, warnings}
    4. Reduce: cluster into narrative beats
    5. Emit: write SPINE.md or script.md to projects/<slug>/

The LLM calls are made by the agent (CompAII), not by this script directly.
This script provides the chunk/map/reduce plumbing. The agent handles the
actual synthesis prompt.
"""

import sys
import json
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_pdf(path: str) -> str:
    """Extract full text from a PDF using pymupdf."""
    import pymupdf
    doc = pymupdf.open(path)
    return "\n\n".join(page.get_text() for page in doc)

def ingest_text(path: str) -> str:
    """Read plain text or markdown file."""
    return Path(path).read_text()

def ingest_url(url: str) -> str:
    """Fetch URL content as text. For HTML, strip tags later."""
    import requests
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text

# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------

def chunk_text(text: str, max_tokens: int = 2000) -> list[str]:
    """Split text into chunks of ~max_tokens (rough char estimate: 1 token ≈ 4 chars)."""
    max_chars = max_tokens * 4
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) < max_chars:
            current += para + "\n\n"
        else:
            if current:
                chunks.append(current.strip())
            current = para + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks

# ---------------------------------------------------------------------------
# Map (extraction prompt template — agent fills)
# ---------------------------------------------------------------------------

MAP_PROMPT = """Extract from this chunk:

Claims (factual assertions, strategic recommendations):
Data points (numbers, prices, metrics, market sizes):
Directives (actionable "do this / don't do this"):
Quotable phrases (standalone, poetic, memorable):
Warnings (risks, pitfalls, things to avoid):

Chunk:
{chunk_text}"""

# ---------------------------------------------------------------------------
# Reduce (clustering prompt template — agent fills)
# ---------------------------------------------------------------------------

REDUCE_PROMPT = """You have {num_chunks} sets of extracted claims/data/directives/quotes/warnings
from a source document. Cluster them into {num_beats} narrative beats.
Each beat should feel distinct and build toward a conclusion.

Beats should follow this arc: {arc_description}

For each beat, output:
- Beat name (3-5 words)
- Time allocation
- What to say (2-4 sentences of narration)
- Visual direction (what should be on screen)
- What NOT to say

Extracted material:
{extractions}"""

# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

SPINE_TEMPLATE = """---
title: {title}
project: {slug}
status: draft → waiting approval
---

# SPINE — {title}

{beats_content}

---

*SPINE.md gates script.md and storyboard.json. Human approves before any image gen.*
"""

def emit_spine(slug: str, title: str, beats: list[dict], workspace: Path) -> Path:
    """Write SPINE.md from beat dicts."""
    beats_md = []
    for i, beat in enumerate(beats, 1):
        beats_md.append(f"## Beat {i} — {beat['name']} ({beat['time']})")
        beats_md.append(f"\n**Lo que se dice:**\n{beat.get('says', '')}")
        beats_md.append(f"\n**Visuales sugeridos:**\n{beat.get('visuals', '')}")
        beats_md.append(f"\n**Lo que NO se dice:**\n{beat.get('avoid', '')}")
        beats_md.append("")

    content = SPINE_TEMPLATE.format(
        title=title,
        slug=slug,
        beats_content="\n".join(beats_md)
    )

    spine_path = workspace / slug / "SPINE.md"
    spine_path.parent.mkdir(parents=True, exist_ok=True)
    spine_path.write_text(content)
    return spine_path

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: source_synth.py <path|url> [--slug <slug>] [--output spine|script]")
        sys.exit(1)

    source = sys.argv[1]
    slug = "default"
    output = "spine"

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--slug" and i+1 < len(args):
            slug = args[i+1]; i += 2
        elif args[i] == "--output" and i+1 < len(args):
            output = args[i+1]; i += 2
        else:
            i += 1

    # Ingest
    if source.endswith(".pdf"):
        text = ingest_pdf(source)
    elif source.startswith("http"):
        text = ingest_url(source)
    else:
        text = ingest_text(source)

    # Chunk
    chunks = chunk_text(text)
    print(f"Ingested: {len(text)} chars → {len(chunks)} chunks", file=sys.stderr)

    # Output chunks for the agent to process
    # (the agent reads these and runs MAP/REDUCE via LLM calls)
    workspace = Path("projects")
    chunks_path = workspace / slug / "chunks.json"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"source": source, "slug": slug, "chunks": chunks}, chunks_path.open("w"), indent=2)
    print(f"Chunks written to {chunks_path}", file=sys.stderr)
    print(chunks_path)

if __name__ == "__main__":
    main()
