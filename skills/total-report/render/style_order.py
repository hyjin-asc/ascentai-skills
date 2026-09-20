"""Stylesheet order for this skill's report slugs.

design-tokens.css first (CSS variables), overlay.css last (skill-only wrapper).
The concatenation itself lives in _core/render/inline_styles.py.
"""

SKILL_STYLES = {
    # ── individual finder reports (kept so the standalone render_<finder>
    #    adapters still work exactly as in the sibling skills) ──
    "query-opportunity": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "overlay.css",
    ],
    "path-opportunity": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "path-opportunity.css",
        "overlay.css",
    ],
    "cluster-landscape": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "cluster-landscape.css",  # .cl-* : hub table + From→To flow section
        "overlay.css",
    ],
    # ── total-insight umbrella: union of all finder styles + total-overview,
    #    tokens first, overlay LAST ──
    "total-insight": [
        "design-tokens.css",
        "report-shell.css",
        "audience-shared.css",
        "report-components.css",
        "customer-analysis.css",
        "query-opportunity.css",   # inert on card path but harmless (legacy chart view)
        "path-opportunity.css",    # .flow-* / .path-* / .hub-* journey styles
        "cluster-landscape.css",   # .cl-* hub table + flows
        "total-overview.css",      # .tot-* umbrella chrome: 4-tab nav + total cover + hub cross-view
        "overlay.css",
    ],
}
