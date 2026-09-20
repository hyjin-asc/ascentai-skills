"""Stylesheet order for this skill's report slugs.

design-tokens.css first (CSS variables), overlay.css last (skill-only wrapper).
The concatenation itself lives in _core/render/inline_styles.py.
"""

SKILL_STYLES = {
    "cluster-landscape": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "cluster-landscape.css",  # .cl-* : hub table + From→To flow section
        "overlay.css",
    ],
}
