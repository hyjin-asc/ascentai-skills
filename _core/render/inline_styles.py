"""Concat CSS files in deterministic order for inline <style> injection.

Order matters and is declared per skill in its own `style_order.py`:
  * design-tokens.css (CSS variables) MUST come first so later rules can
    reference them.
  * overlay.css (the skill-only single-page wrapper) MUST come last so it can
    override ListeningMind.AI defaults such as the .rpt-main sidebar offset.

The order itself is skill data, so it lives next to the skill; only this
concatenation is shared.
"""

from pathlib import Path
from typing import Dict, List


def inline_styles(styles_dir: Path, skill: str, style_order: Dict[str, List[str]]) -> str:
    chunks: List[str] = []
    for name in style_order[skill]:
        path = styles_dir / name
        chunks.append(f"/* === {name} === */\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)
