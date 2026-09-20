#!/usr/bin/env python3
"""Unified render harness for the total-report skill.

This is the MERGED render_report.py: it carries the three vendored finder
adapters (render_query / render_path / render_cluster — byte-compatible with the
sibling skills so each still renders standalone) PLUS a new render_total() that
assembles all four reports into ONE 4-tab umbrella HTML.

Design of the merge
-------------------
Each finder's *dashboard body* (cover + inner sub-tabs + panels) and its *A4
pages* (cover + body) are produced by a reusable "pieces" builder
(`_query_pieces` / `_path_pieces` / `_cluster_pieces`) that is independent of the
standalone page wrapper (the finder's own template). Both callers reuse it:

  * render_<finder>()  fills the finder's standalone template tokens from the
    pieces (identical output to the sibling skill).
  * render_total()     concatenates each finder's pieces into one outer tab
    panel and drops them into the umbrella shell (templates/total-insight.html).

Numbers are computed in Python (finder aggregators); the LLM only groups/names/
interprets. Output is a single self-contained HTML file — all CSS inlined, the
Google Fonts CDN the only (optional) external dependency, no external JS.

Usage — standalone finder (unchanged from siblings):
    python3 render_report.py --skill query-opportunity \
        --groups lm_groups.json --actions lm_actions.json --meta lm_query_result.json \
        --category "러닝화" --gl kr --date 2026-07-23 --out report.html

Usage — total umbrella:
    python3 render_report.py --skill total-insight \
        --total          lm_total.json  --total-facts lm_total_facts.json \
        --query-groups   q/lm_groups.json  --query-actions   q/lm_actions.json  --query-meta   q/lm_query_result.json \
        --path-paths     p/lm_paths.json   --path-actions    p/lm_actions.json  --path-meta    p/lm_path_result.json \
        --cluster-groups c/lm_groups.json  --cluster-actions c/lm_actions.json  --cluster-meta c/lm_cluster_result.json \
        --category "무선청소기" --gl kr --date 2026-07-23 --out total-insight-report.html

Standard library only.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import components as ca            # shared customer-analysis card builders (t, persona_card, cover, actions)  # noqa: E402
import components_path as cp       # path-specific builders (flow tree, path/hub cards)  # noqa: E402
import components_cluster as cc    # cluster-specific hub table + flow builders  # noqa: E402
import components_total as ct      # NEW: umbrella shell (4-tab nav, total cover, hub cross-view)  # noqa: E402
from inline_styles import inline_styles  # noqa: E402
from style_order import SKILL_STYLES  # noqa: E402
# NOTE: components_query (legacy chart view) is vendored for provenance but NOT
# imported — the query card path emits no Chart.js and does not need it.


PKG_ROOT = HERE.parent.parent
STYLES_DIR = PKG_ROOT / "_shared" / "styles"
LABELS_DIR = PKG_ROOT / "_shared" / "labels"
TEMPLATES_DIR = PKG_ROOT / "_shared" / "templates"


FONT_HREF = {
    "kr": "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;900&display=swap",
    "jp": "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700;900&display=swap",
    "us": "https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700;900&display=swap",
}
HTML_LANG = {"kr": "ko", "jp": "ja", "us": "en"}

# 리포트를 쓸 수 있는 언어 · 시장(gl)과 독립이다 (미국 시장을 일본어로 쓸 수 있다)
REPORT_LANGS = ("kr", "jp", "us")
LOCALE_FONT_OVERRIDE = {
    "kr": "",
    "jp": "html { --font-family: 'Noto Sans JP', -apple-system, BlinkMacSystemFont, Sans-serif; }",
    "us": "html { --font-family: 'Noto Sans', -apple-system, BlinkMacSystemFont, Sans-serif; }",
}
TOOLBAR_DASH_BTN = {"kr": "대시보드", "jp": "ダッシュボード", "us": "Dashboard"}
TOOLBAR_PRINT_BTN = {"kr": "인쇄", "jp": "印刷", "us": "Print"}


# ─────────────────────────────────────────
# Scripts
# ─────────────────────────────────────────
#
# BASE_SCRIPT — used by the standalone finder reports. Global .dash-tab toggle +
# view toggle + volume-badge tooltip. Identical to the sibling skills.
BASE_SCRIPT = r"""
(function(){
  var tabs = document.querySelectorAll('.dash-tab');
  var panels = document.querySelectorAll('.dash-tab-panel');
  tabs.forEach(function(tab){
    tab.addEventListener('click', function(){
      var idx = tab.getAttribute('data-panel');
      tabs.forEach(function(t){
        var on = t.getAttribute('data-panel') === idx;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      panels.forEach(function(p){
        p.classList.toggle('active', p.getAttribute('data-panel') === idx);
      });
    });
  });
  var viewBtns = document.querySelectorAll('.mini-toolbar__view button');
  viewBtns.forEach(function(btn){
    btn.addEventListener('click', function(){
      var mode = btn.getAttribute('data-view');
      document.body.classList.toggle('view-mode-dash', mode === 'dash');
      document.body.classList.toggle('view-mode-a4',   mode === 'a4');
      viewBtns.forEach(function(b){ b.classList.toggle('active', b === btn); });
    });
  });

  // 검색어 번역 토글 — 원문 ↔ 리포트 언어. 번역이 없으면 버튼 자체가 없다.
  var trBtn = document.querySelector('.mini-toolbar__tr');
  if (trBtn) {
    trBtn.addEventListener('click', function(){
      var on = document.body.classList.toggle('kw-translated');
      trBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  // 검색량 뱃지 툴팁 — 클릭 토글, X·바깥클릭으로 닫기.
  document.querySelectorAll('.persona-card__volbadge').forEach(function(badge){
    var tip = badge.querySelector('.volbadge-tip');
    var close = badge.querySelector('.volbadge-tip__close');
    badge.addEventListener('click', function(e){
      if (tip && tip.contains(e.target)) { e.stopPropagation(); return; }
      e.stopPropagation();
      badge.classList.toggle('tip-open');
    });
    badge.addEventListener('keydown', function(e){
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); badge.classList.toggle('tip-open'); }
    });
    if (close) {
      close.addEventListener('click', function(e){
        e.stopPropagation(); e.preventDefault();
        badge.classList.remove('tip-open');
      });
    }
  });
  document.addEventListener('click', function(){
    document.querySelectorAll('.persona-card__volbadge.tip-open').forEach(function(b){
      b.classList.remove('tip-open');
    });
  });
})();
"""

# TOTAL_SCRIPT — the 4-tab umbrella. Critically, the finder sub-tab toggle is
# SCOPED per `.tot-panel` container so the four embedded reports' internal
# sub-tabs (all using the generic .dash-tab / .dash-tab-panel[data-panel="0|1"])
# don't cross-toggle. Also carries: outer 4-tab nav, view dash/a4 toggle, and the
# volume-badge tooltip handler.
TOTAL_SCRIPT = r"""
(function(){
  // ── outer 4-tab nav (통합요약 / 쿼리 / 여정 / 클러스터) ──
  var nav = document.querySelector('.tot-tabnav');
  if (nav) {
    var navBtns = nav.querySelectorAll('button[data-tab]');
    var outerPanels = document.querySelectorAll('.tot-panel');
    navBtns.forEach(function(btn){
      btn.addEventListener('click', function(){
        var key = btn.getAttribute('data-tab');
        navBtns.forEach(function(b){
          var on = b.getAttribute('data-tab') === key;
          b.classList.toggle('is-active', on);
          b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        outerPanels.forEach(function(p){
          p.classList.toggle('is-active', p.getAttribute('data-tab') === key);
        });
      });
    });
  }

  // ── inner finder sub-tabs — SCOPED to each .tot-panel so identical
  //    data-panel indices across finders never cross-toggle ──
  document.querySelectorAll('.tot-panel').forEach(function(root){
    var tabs = root.querySelectorAll('.dash-tab');
    var subPanels = root.querySelectorAll('.dash-tab-panel');
    tabs.forEach(function(tab){
      tab.addEventListener('click', function(){
        var idx = tab.getAttribute('data-panel');
        tabs.forEach(function(t){
          var on = t.getAttribute('data-panel') === idx;
          t.classList.toggle('active', on);
          t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        subPanels.forEach(function(p){
          p.classList.toggle('active', p.getAttribute('data-panel') === idx);
        });
      });
    });
  });

  // ── view dash/a4 toggle ──
  var viewBtns = document.querySelectorAll('.mini-toolbar__view button');
  viewBtns.forEach(function(btn){
    btn.addEventListener('click', function(){
      var mode = btn.getAttribute('data-view');
      document.body.classList.toggle('view-mode-dash', mode === 'dash');
      document.body.classList.toggle('view-mode-a4',   mode === 'a4');
      viewBtns.forEach(function(b){ b.classList.toggle('active', b === btn); });
    });
  });

  // 검색어 번역 토글 — 원문 ↔ 리포트 언어. 번역이 없으면 버튼 자체가 없다.
  var trBtn = document.querySelector('.mini-toolbar__tr');
  if (trBtn) {
    trBtn.addEventListener('click', function(){
      var on = document.body.classList.toggle('kw-translated');
      trBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  // ── 검색량 뱃지 툴팁 (per-badge toggle — global is fine) ──
  document.querySelectorAll('.persona-card__volbadge').forEach(function(badge){
    var tip = badge.querySelector('.volbadge-tip');
    var close = badge.querySelector('.volbadge-tip__close');
    badge.addEventListener('click', function(e){
      if (tip && tip.contains(e.target)) { e.stopPropagation(); return; }
      e.stopPropagation();
      badge.classList.toggle('tip-open');
    });
    badge.addEventListener('keydown', function(e){
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); badge.classList.toggle('tip-open'); }
    });
    if (close) {
      close.addEventListener('click', function(e){
        e.stopPropagation(); e.preventDefault();
        badge.classList.remove('tip-open');
      });
    }
  });
  document.addEventListener('click', function(){
    document.querySelectorAll('.persona-card__volbadge.tip-open').forEach(function(b){
      b.classList.remove('tip-open');
    });
  });
})();
"""


# ─────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────

def _load_labels(skill: str, lang: str) -> dict:
    """Label file is chosen by REPORT LANGUAGE, not by market.

    `--gl` (market) and `--lang` (report language) are independent: a US-market
    report can be written in Japanese. Only the market *name* on the cover comes
    from `--gl`, and that string is read out of the report-language label file
    (`country.US` / `report.marketLabel.US`), which every language ships."""
    return json.loads((LABELS_DIR / f"{skill}.{lang}.json").read_text(encoding="utf-8"))


def _read_json(path) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _common_blocks(skill: str, lang: str, category: str, labels: dict, *, title_key: str, dash_tab_key: str) -> dict:
    """`lang` = report language. Font, <html lang> and toolbar follow the text
    on screen, not the market being analysed."""
    inlined_css = inline_styles(STYLES_DIR, skill, SKILL_STYLES)
    if LOCALE_FONT_OVERRIDE.get(lang):
        inlined_css += "\n\n/* === locale font override === */\n" + LOCALE_FONT_OVERRIDE[lang]
    return {
        "{{LANG}}": HTML_LANG.get(lang, "en"),
        "{{FONT_HREF}}": FONT_HREF.get(lang, FONT_HREF["us"]),
        "{{CATEGORY}}": category,
        "{{REPORT_TITLE}}": ca.t(labels, title_key),
        "{{TOOLBAR_DASH_LABEL}}": ca.t(labels, dash_tab_key),
        "{{TOOLBAR_A4_LABEL}}": "A4",
        "{{TOOLBAR_DASH_BTN}}": TOOLBAR_DASH_BTN.get(lang, "Dashboard"),
        "{{TOOLBAR_PRINT_BTN}}": TOOLBAR_PRINT_BTN.get(lang, "Print"),
        "{{INLINED_CSS}}": inlined_css,
    }


def _apply(template: str, blocks: dict) -> str:
    # Single-pass substitution: re.sub's replacement text is never re-scanned,
    # so a keyword/actions field containing a literal "{{TOKEN}}" can't be
    # expanded a second time.
    pattern = re.compile("|".join(re.escape(k) for k in blocks))
    return pattern.sub(lambda m: blocks[m.group(0)], template)


# ─────────────────────────────────────────
# Finder "pieces" builders — the reusable dashboard-body + A4 seam. Each returns
# a dict with granular parts so BOTH the standalone finder template and the total
# umbrella can consume them without re-computing.
#   cover / tabs / panels(list, in order) / a4_cover / a4_body / meta(raw) / n_core
# `dash_body` = cover + tabs + "".join(panels) is the exact content the finder's
# own <div class="view-dash"><div class="dash-scroll"> wraps.
# ─────────────────────────────────────────

def _query_pieces(gl: str, lang: str, category: str, date: str, groups_path, actions_path, meta_path) -> dict:
    groups_doc = _read_json(groups_path)
    groups = groups_doc.get("groups", [])
    overview = groups_doc.get("overview") or ""
    actions = _read_json(actions_path)
    meta = _read_json(meta_path)

    core = [g for g in groups if g.get("relation") != "alternative"]
    alt = [g for g in groups if g.get("relation") == "alternative"]

    meta_view = {
        "date": date,
        "clusterCount": meta.get("keywordCount", 0),
        "keywordCount": meta.get("keywordCount", 0),
        "personaCount": len(core),
        "altCount": len(alt),
    }
    labels = _load_labels("query-opportunity", lang)
    gl_label = ca.t(labels, f"country.{gl.upper()}")
    market_label = ca.t(labels, f"report.marketLabel.{gl.upper()}")

    cover = ca.dash_cover_html(meta_view, category, gl_label, market_label, labels, overview=overview)
    tabs = ca.dash_tabs_html(labels)
    panel_personas = ca.dash_panel_personas_html(core, alt, category, labels)
    panel_actions = ca.dash_panel_actions_html(actions, labels)
    return {
        "labels": labels,
        "meta": meta,
        "n_core": len(core),
        "cover": cover,
        "tabs": tabs,
        "panels": [panel_personas, panel_actions],
        "dash_body": cover + tabs + panel_personas + panel_actions,
        "a4_cover": ca.a4_cover_page_html(meta_view, category, labels),
        "a4_body": ca.a4_body_page_html(groups, actions, category, market_label, labels, overview=overview),
    }


def _path_pieces(gl: str, lang: str, category: str, date: str, paths_path, actions_path, meta_path) -> dict:
    paths_doc = _read_json(paths_path)
    paths = paths_doc.get("paths", [])
    hubs = paths_doc.get("hubs", [])
    overview = paths_doc.get("overview") or ""
    actions = _read_json(actions_path)
    meta = _read_json(meta_path)
    flow_tree = meta.get("flowTree", [])

    meta_view = {
        "date": date,
        "pathCount": meta.get("pathCount", 0),
        "hubCount": meta.get("hubCount", len(hubs)),
        "nodeCount": meta.get("nodeCount", 0),
    }
    labels = _load_labels("path-opportunity", lang)
    gl_label = ca.t(labels, f"country.{gl.upper()}")
    market_label = ca.t(labels, f"report.marketLabel.{gl.upper()}")

    cover = cp.dash_cover_html(meta_view, category, gl_label, market_label, labels, overview=overview)
    tabs = cp.dash_tabs_html(labels)
    panel_journey = cp.dash_panel_journey_html(paths, hubs, flow_tree, category, labels)
    panel_actions = ca.dash_panel_actions_html(actions, labels)
    return {
        "labels": labels,
        "meta": meta,
        "cover": cover,
        "tabs": tabs,
        "panels": [panel_journey, panel_actions],
        "dash_body": cover + tabs + panel_journey + panel_actions,
        "a4_cover": cp.a4_cover_page_html(meta_view, category, labels),
        "a4_body": cp.a4_body_page_html(paths, hubs, flow_tree, actions, category, market_label, labels, overview=overview),
    }


def _cluster_pieces(gl: str, lang: str, category: str, date: str, groups_path, actions_path, meta_path) -> dict:
    groups_doc = _read_json(groups_path)
    groups = groups_doc.get("groups", [])
    hub_table = groups_doc.get("hubTable", [])
    flows = groups_doc.get("flows", [])
    overview = groups_doc.get("overview") or ""
    actions = _read_json(actions_path)
    meta = _read_json(meta_path)

    core = groups
    alt = []  # cluster has no brand/non-brand split
    meta_view = {
        "date": date,
        "clusterCount": meta.get("clusterCount", 0),
        "keywordCount": meta.get("keywordCount", 0),
        "edgeCount": meta.get("edgeCount", 0),
        "personaCount": len(core),
        "altCount": 0,
    }
    labels = _load_labels("cluster-landscape", lang)
    gl_label = ca.t(labels, f"country.{gl.upper()}")
    market_label = ca.t(labels, f"report.marketLabel.{gl.upper()}")

    cover = ca.dash_cover_html(meta_view, category, gl_label, market_label, labels, overview=overview)
    tabs = ca.dash_tabs_html(labels)
    panel_clusters = ca.dash_panel_personas_html(core, alt, category, labels)
    panel_hub = cc.dash_hub_panel_html(hub_table, labels)
    panel_flows = cc.dash_flows_panel_html(flows, labels)
    panel_actions = ca.dash_panel_actions_html(actions, labels)
    return {
        "labels": labels,
        "meta": meta,
        "n_core": len(core),
        "cover": cover,
        "tabs": tabs,
        "panels": [panel_clusters, panel_hub, panel_flows, panel_actions],
        "dash_body": cover + tabs + panel_clusters + panel_hub + panel_flows + panel_actions,
        "a4_cover": ca.a4_cover_page_html(meta_view, category, labels),
        "a4_body": cc.a4_body_page_html(groups, hub_table, flows, actions, category, market_label, labels, overview=overview),
    }


# ─────────────────────────────────────────
# Standalone finder renderers (unchanged output vs. sibling skills)
# ─────────────────────────────────────────


def _report_lang(args) -> str:
    """Report language. Defaults to the market so the single-language editions
    keep working with `--gl` alone; the English-prompt edition passes `--lang`
    explicitly and may pair any market with any report language."""
    if getattr(args, "lang", None):
        return args.lang.lower()
    return args.gl.lower()   # 생략하면 시장 언어로 쓴다 (kr·jp·us 모두 라벨이 있다)


def render_query(args) -> str:
    gl = args.gl.lower()        # 분석 대상 시장
    lang = _report_lang(args)   # 리포트에 쓰는 언어
    p = _query_pieces(gl, lang, args.category, args.date, args.groups, args.actions, args.meta)

    # 검색어 번역 — 시장 언어와 리포트 언어가 다를 때만 넘어온다
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8")) if args.translations else {}
    ca.set_translations(translations)
    tr_btn = (
        f'<button type="button" class="mini-toolbar__tr" aria-pressed="false">'
        f'{html.escape(ca.t(p["labels"], "customerAnalysis.dash.keywordTranslateBtn"))}</button>'
    ) if translations else ""
    blocks = _common_blocks(
        "query-opportunity", lang, html.escape(args.category), p["labels"],
        title_key="agent.customerAnalysis.name",
        dash_tab_key="customerAnalysis.dash.personaTab",
    )
    blocks.update({
        "{{DASH_COVER}}": p["cover"],
        "{{DASH_TABS}}": p["tabs"],
        "{{DASH_PANEL_PERSONAS}}": p["panels"][0],
        "{{DASH_PANEL_ACTIONS}}": p["panels"][1],
        "{{A4_COVER_PAGE}}": p["a4_cover"],
        "{{A4_BODY_PAGE}}": p["a4_body"],
        "{{INLINE_SCRIPT}}": BASE_SCRIPT,
        "{{TOOLBAR_TR_BTN}}": tr_btn,
    })
    template = (TEMPLATES_DIR / "query-opportunity.html").read_text(encoding="utf-8")
    return _apply(template, blocks)


def render_path(args) -> str:
    gl = args.gl.lower()        # 분석 대상 시장
    lang = _report_lang(args)   # 리포트에 쓰는 언어
    p = _path_pieces(gl, lang, args.category, args.date, args.paths, args.actions, args.meta)

    # 검색어 번역 — 시장 언어와 리포트 언어가 다를 때만 넘어온다
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8")) if args.translations else {}
    ca.set_translations(translations)
    tr_btn = (
        f'<button type="button" class="mini-toolbar__tr" aria-pressed="false">'
        f'{html.escape(ca.t(p["labels"], "customerAnalysis.dash.keywordTranslateBtn"))}</button>'
    ) if translations else ""
    blocks = _common_blocks(
        "path-opportunity", lang, html.escape(args.category), p["labels"],
        title_key="agent.pathAnalysis.name",
        dash_tab_key="pathAnalysis.dash.journeyTab",
    )
    blocks.update({
        "{{DASH_COVER}}": p["cover"],
        "{{DASH_TABS}}": p["tabs"],
        "{{DASH_PANEL_JOURNEY}}": p["panels"][0],
        "{{DASH_PANEL_ACTIONS}}": p["panels"][1],
        "{{A4_COVER_PAGE}}": p["a4_cover"],
        "{{A4_BODY_PAGE}}": p["a4_body"],
        "{{INLINE_SCRIPT}}": BASE_SCRIPT,
        "{{TOOLBAR_TR_BTN}}": tr_btn,
    })
    template = (TEMPLATES_DIR / "path-opportunity.html").read_text(encoding="utf-8")
    return _apply(template, blocks)


def render_cluster(args) -> str:
    gl = args.gl.lower()        # 분석 대상 시장
    lang = _report_lang(args)   # 리포트에 쓰는 언어
    p = _cluster_pieces(gl, lang, args.category, args.date, args.groups, args.actions, args.meta)

    # 검색어 번역 — 시장 언어와 리포트 언어가 다를 때만 넘어온다
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8")) if args.translations else {}
    ca.set_translations(translations)
    tr_btn = (
        f'<button type="button" class="mini-toolbar__tr" aria-pressed="false">'
        f'{html.escape(ca.t(p["labels"], "customerAnalysis.dash.keywordTranslateBtn"))}</button>'
    ) if translations else ""
    blocks = _common_blocks(
        "cluster-landscape", lang, html.escape(args.category), p["labels"],
        title_key="agent.customerAnalysis.name",
        dash_tab_key="customerAnalysis.dash.personaTab",
    )
    blocks.update({
        "{{DASH_COVER}}": p["cover"],
        "{{DASH_TABS}}": p["tabs"],
        "{{DASH_PANEL_CLUSTERS}}": p["panels"][0],
        "{{DASH_HUB_TABLE}}": p["panels"][1],
        "{{DASH_FLOWS}}": p["panels"][2],
        "{{DASH_PANEL_ACTIONS}}": p["panels"][3],
        "{{A4_COVER_PAGE}}": p["a4_cover"],
        "{{A4_BODY_PAGE}}": p["a4_body"],
        "{{INLINE_SCRIPT}}": BASE_SCRIPT,
        "{{TOOLBAR_TR_BTN}}": tr_btn,
    })
    template = (TEMPLATES_DIR / "cluster-landscape.html").read_text(encoding="utf-8")
    return _apply(template, blocks)


# ─────────────────────────────────────────
# Total umbrella renderer
# ─────────────────────────────────────────
#
# Assembles: total cover + 4-tab nav + [탭1 통합요약 (신규) · 탭2 query · 탭3 path
# · 탭4 cluster] dashboard bodies, plus a unified A4 that prints all four in order
# (통합 → query → path → cluster). Finders whose inputs are absent render as a
# no-data tab so a partial run (e.g. no cluster API plan) still produces a report.

def render_total(args) -> str:
    gl = args.gl.lower()        # 분석 대상 시장
    lang = _report_lang(args)   # 리포트에 쓰는 언어
    category = args.category
    date = args.date

    # notes(정성) = lm_total.json (id-keyed) ; facts(숫자·좌표) = lm_total_facts.json
    notes = _read_json(args.total)
    facts = _read_json(args.total_facts) if args.total_facts else {}
    overview = notes.get("overview") or ""

    tlabels = _load_labels("total-insight", lang)

    # 검색어 번역 — 시장 언어와 리포트 언어가 다를 때만 넘어온다
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8")) if args.translations else {}
    ca.set_translations(translations)
    tr_btn = (
        f'<button type="button" class="mini-toolbar__tr" aria-pressed="false">'
        f'{html.escape(ca.t(tlabels, "customerAnalysis.dash.keywordTranslateBtn"))}</button>'
    ) if translations else ""
    gl_label = ca.t(tlabels, f"country.{gl.upper()}")
    market_label = ca.t(tlabels, f"report.marketLabel.{gl.upper()}")

    # finder pieces — each present only if its inputs were supplied
    qp = _query_pieces(gl, lang, category, date, args.query_groups, args.query_actions, args.query_meta) if args.query_groups else None
    pp = _path_pieces(gl, lang, category, date, args.path_paths, args.path_actions, args.path_meta) if args.path_paths else None
    clp = _cluster_pieces(gl, lang, category, date, args.cluster_groups, args.cluster_actions, args.cluster_meta) if args.cluster_groups else None

    # cover meta counts drawn from finder metas (Python facts, never LLM)
    counts = {
        "date": date,
        "keywordCount": (qp and qp["meta"].get("keywordCount")) or (clp and clp["meta"].get("keywordCount")) or "",
        "clusterCount": (clp and clp["meta"].get("clusterCount")) or "",
        "personaCount": (qp and qp["n_core"]) or "",
        "pathCount": (pp and pp["meta"].get("pathCount")) or "",
    }

    # 탭 1 통합 요약 — 머리글 + 6개 교차 인사이트 모듈(facts 숫자 + id-키드 노트 머지)
    tab1_intro = ct.tab1_intro_html(facts, notes, tlabels)
    modules = ct.total_modules(facts, notes, tlabels, collapsible=True)

    # 탭 2~4 — 각 파인더 대시보드 본문(없으면 no-data)
    query_body = qp["dash_body"] if qp else ct.nodata_html(tlabels)
    path_body = pp["dash_body"] if pp else ct.nodata_html(tlabels)
    cluster_body = clp["dash_body"] if clp else ct.nodata_html(tlabels)

    tab_query = ct.finder_panel_html("q", query_body, tlabels)
    tab_path = ct.finder_panel_html("p", path_body, tlabels)
    tab_cluster = ct.finder_panel_html("c", cluster_body, tlabels)

    # unified A4 — 통합 A4 first, then each finder's A4 cover+body in order
    a4_parts = [ct.total_a4_pages_html(counts, facts, notes, category, market_label, tlabels, overview=overview)]
    if qp:
        a4_parts.append(qp["a4_cover"] + qp["a4_body"])
    if pp:
        a4_parts.append(pp["a4_cover"] + pp["a4_body"])
    if clp:
        a4_parts.append(clp["a4_cover"] + clp["a4_body"])

    blocks = _common_blocks(
        "total-insight", lang, html.escape(category), tlabels,
        title_key="totalInsight.reportTitle",
        dash_tab_key="totalInsight.tab.t.short",
    )
    blocks.update({
        "{{TOTAL_COVER}}": ct.total_cover_html(counts, category, gl_label, market_label, tlabels, overview=overview),
        "{{TOTAL_TABNAV}}": ct.tabnav_html(tlabels),
        "{{TAB1_INTRO}}": tab1_intro,
        "{{TAB1_M1_COVERAGE}}": modules["m1"],
        "{{TAB1_M2_HUB}}": modules["m2"],
        "{{TAB1_M3_MATRIX}}": modules["m3"],
        "{{TAB1_M4_LEAK}}": modules["m4"],
        "{{TAB1_M5_CORRIDOR}}": modules["m5"],
        "{{TAB1_M6_BACKLOG}}": modules["m6"],
        "{{TAB_QUERY}}": tab_query,
        "{{TAB_PATH}}": tab_path,
        "{{TAB_CLUSTER}}": tab_cluster,
        "{{A4_PAGES}}": "".join(a4_parts),
        "{{INLINE_SCRIPT}}": TOTAL_SCRIPT,
        "{{TOOLBAR_TR_BTN}}": tr_btn,
    })
    template = (TEMPLATES_DIR / "total-insight.html").read_text(encoding="utf-8")
    return _apply(template, blocks)


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Render ListeningMind.AI-style HTML report(s).")
    parser.add_argument("--skill", required=True,
                        choices=["query-opportunity", "path-opportunity", "cluster-landscape", "total-insight"])
    parser.add_argument("--category", required=True)
    # kr-only for now: label JSON only ships for "kr".
    # --gl = 분석 대상 시장 (MCP 조회와 표지의 시장명)
    # --lang = 리포트 언어 (라벨·폰트·본문). 생략하면 시장을 따른다.
    parser.add_argument("--gl", required=True, choices=["kr", "jp", "us"],
                        help="target market to analyse")
    parser.add_argument("--lang", choices=list(REPORT_LANGS),
                        help="report language (default: same as --gl)")
    parser.add_argument("--translations",
                        help="keyword translation map {keyword: translation} — adds the "
                             "keyword-translation toggle. Only when the market language "
                             "differs from --lang.")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output HTML path")

    # single-finder inputs (query / cluster use --groups; path uses --paths)
    parser.add_argument("--groups", help="query/cluster: lm_groups.json path")
    parser.add_argument("--paths", help="path: lm_paths.json path")
    parser.add_argument("--actions", help="lm_actions.json path")
    parser.add_argument("--meta", help="lm_*_result.json path")

    # total-insight inputs
    parser.add_argument("--total", help="total-insight: lm_total.json path (id-keyed LLM notes)")
    parser.add_argument("--total-facts", help="total-insight: lm_total_facts.json path (aggregator numbers/coords)")
    parser.add_argument("--query-groups", help="total-insight: query lm_groups.json")
    parser.add_argument("--query-actions", help="total-insight: query lm_actions.json")
    parser.add_argument("--query-meta", help="total-insight: query lm_query_result.json")
    parser.add_argument("--path-paths", help="total-insight: path lm_paths.json")
    parser.add_argument("--path-actions", help="total-insight: path lm_actions.json")
    parser.add_argument("--path-meta", help="total-insight: path lm_path_result.json")
    parser.add_argument("--cluster-groups", help="total-insight: cluster lm_groups.json")
    parser.add_argument("--cluster-actions", help="total-insight: cluster lm_actions.json")
    parser.add_argument("--cluster-meta", help="total-insight: cluster lm_cluster_result.json")

    args = parser.parse_args()

    if args.skill == "query-opportunity":
        for k in ("groups", "meta"):
            if not getattr(args, k):
                parser.error(f"query-opportunity requires --{k}")
        html_str = render_query(args)
    elif args.skill == "path-opportunity":
        for k in ("paths", "meta"):
            if not getattr(args, k):
                parser.error(f"path-opportunity requires --{k}")
        html_str = render_path(args)
    elif args.skill == "cluster-landscape":
        for k in ("groups", "meta"):
            if not getattr(args, k):
                parser.error(f"cluster-landscape requires --{k}")
        html_str = render_cluster(args)
    elif args.skill == "total-insight":
        if not args.total:
            parser.error("total-insight requires --total")
        if not args.total_facts:
            parser.error("total-insight requires --total-facts (lm_total_facts.json)")
        # at least one finder must be present (partial runs allowed)
        if not (args.query_groups or args.path_paths or args.cluster_groups):
            parser.error("total-insight requires at least one finder (--query-groups / --path-paths / --cluster-groups)")
        html_str = render_total(args)
    else:  # pragma: no cover
        parser.error(f"unknown skill {args.skill}")

    Path(args.out).write_text(html_str, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
