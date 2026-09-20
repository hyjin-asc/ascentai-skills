"""Stylesheet order for this skill's report slugs.

design-tokens.css first (CSS variables), overlay.css last (skill-only wrapper).
The concatenation itself lives in _core/render/inline_styles.py.
"""

SKILL_STYLES = {
    "query-opportunity": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "overlay.css",
    ],
}
