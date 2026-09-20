#!/usr/bin/env python3
"""Render a ListeningMind.AI-style HTML report from the pipeline JSON outputs.

Usage (WORKDIR = {project}/tmp/reports/listeningmind-query-opportunity-{category}-{timestamp}/):
    # query-opportunity (persona/customer-analysis card layout)
    python3 _shared/render/render_report.py \\
        --skill query-opportunity \\
        --groups  "$WORKDIR/lm_groups.json" \\
        --actions "$WORKDIR/lm_actions.json" \\
        --meta    "$WORKDIR/lm_query_result.json" \\
        --category "러닝화" --gl kr --date 2026-07-16 \\
        --out "$WORKDIR/report.html"

The output is a single self-contained HTML file with all CSS inlined and the
Google Fonts CDN as the only external dependency. No Chart.js — the report is
the zip customer-analysis card layout (persona-card sections + actions panel),
not chart-based. The ListeningMind.AI stylesheets are loaded as-is, so the
dashboard and A4 views match the web app pixel-for-pixel.

Standard library only — no third-party deps needed in Claude Desktop.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import components as ca   # customer-analysis card builders (t(), persona_card_html, ...)  # noqa: E402
import components_query as cq  # legacy chart builders — kept for a future opt-in chart view, unused on the default card path  # noqa: E402
from inline_styles import inline_styles  # noqa: E402
from style_order import SKILL_STYLES  # noqa: E402


PKG_ROOT = HERE.parent.parent
STYLES_DIR = PKG_ROOT / "_shared" / "styles"
LABELS_DIR = PKG_ROOT / "_shared" / "labels"
TEMPLATES_DIR = PKG_ROOT / "_shared" / "templates"
VENDOR_DIR = PKG_ROOT / "_shared" / "vendor"


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


# Common tab/view-toggle script. Trend additionally needs a Chart.js bootstrap
# appended when chart data is present (chart_script_html below).
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

  // 검색량 뱃지 — 클릭하면 키워드 목록(툴팁)을 토글. 마우스오버 시엔
  // native title("클릭시 키워드 목록 확인")만 뜬다. X·바깥클릭으로 닫는다.
  document.querySelectorAll('.persona-card__volbadge').forEach(function(badge){
    var tip = badge.querySelector('.volbadge-tip');
    var close = badge.querySelector('.volbadge-tip__close');
    badge.addEventListener('click', function(e){
      // 열린 목록 내부(행 등) 클릭은 유지 — 토글/전파 안 함
      if (tip && tip.contains(e.target)) { e.stopPropagation(); return; }
      e.stopPropagation();
      badge.classList.toggle('tip-open');
    });
    // 키보드 접근성: Enter/Space 로도 토글
    badge.addEventListener('keydown', function(e){
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault(); badge.classList.toggle('tip-open');
      }
    });
    if (close) {
      close.addEventListener('click', function(e){
        e.stopPropagation(); e.preventDefault();
        badge.classList.remove('tip-open');
      });
    }
  });
  // 검색어 번역 토글 — 원문 ↔ 리포트 언어. 번역이 없으면 버튼 자체가 없다.
  var trBtn = document.querySelector('.mini-toolbar__tr');
  if (trBtn) {
    trBtn.addEventListener('click', function(){
      var on = document.body.classList.toggle('kw-translated');
      trBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  // 바깥 아무 곳이나 클릭하면 열린 목록 모두 닫기
  document.addEventListener('click', function(){
    document.querySelectorAll('.persona-card__volbadge.tip-open').forEach(function(b){
      b.classList.remove('tip-open');
    });
  });
})();
"""

CHART_BOOTSTRAP_SCRIPT = r"""
(function(){
  var drawn = {};
  var CANVAS_IDS = ['dashTrendChart','a4TrendChart'];

  function draw(canvasId){
    if (drawn[canvasId]) return false;
    var canvas = document.getElementById(canvasId);
    var dataEl = document.getElementById(canvasId + '-data');
    if (!canvas || !dataEl) { drawn[canvasId] = true; return true; } // no data → don't retry
    if (typeof Chart === 'undefined') return false;
    if (!canvas.offsetParent || canvas.clientWidth === 0) return false; // hidden → retry later
    try {
      var d = JSON.parse(dataEl.textContent);
      var datasets = [
        {label: d.label1, data: d.data1, borderColor: '#AA18CC',
         backgroundColor: 'rgba(170,24,204,.08)', tension: 0.3, fill: true, pointRadius: 3, borderWidth: 2}
      ];
      if (d.data2 && d.data2.length) {
        datasets.push({label: d.label2, data: d.data2, borderColor: '#CD3197',
          backgroundColor: 'rgba(205,49,151,.08)', tension: 0.3, fill: true, pointRadius: 3, borderWidth: 2});
      }
      new Chart(canvas, {
        type: 'line', data: { labels: d.labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: 'top', labels: { font: { size: 12 }, boxWidth: 12 } } },
          scales: {
            x: { ticks: { font: { size: 11 }, color: '#9ca3af' }, grid: { color: '#f3f4f6' }, border: { display: false } },
            y: { ticks: { font: { size: 11 }, color: '#9ca3af' }, grid: { color: '#f3f4f6' }, border: { display: false } }
          }
        }
      });
      drawn[canvasId] = true;
      return true;
    } catch (e) { console.error('chart draw failed', canvasId, e); return false; }
  }

  function drawAll(){ CANVAS_IDS.forEach(draw); }

  function showChartUnavailableNotice(){
    CANVAS_IDS.forEach(function(canvasId){
      if (drawn[canvasId]) return;
      var canvas = document.getElementById(canvasId);
      if (!canvas) return;
      var wrap = canvas.parentElement;
      if (!wrap) return;
      wrap.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--gray-500);font-size:13px;text-align:center;padding:20px">차트를 그릴 수 없어요.<br>브라우저에서 자바스크립트가 비활성화되었거나 미리뷰가 스크립트 실행을 막고 있을 수 있어요. Chrome 같은 일반 브라우저에서 열어주세요.</div>';
      drawn[canvasId] = true;
    });
  }

  // Polling retry — Chart.js CDN may load after our inline script, or the
  // a4 canvas may become visible later via view toggle. Try every 200ms for
  // up to 5s, then fall back to a notice if Chart.js never showed up.
  var attempts = 0, MAX_ATTEMPTS = 25;
  var timer = setInterval(function(){
    attempts++;
    drawAll();
    var allDone = CANVAS_IDS.every(function(id){ return drawn[id]; });
    if (allDone || attempts >= MAX_ATTEMPTS) {
      clearInterval(timer);
      if (typeof Chart === 'undefined') showChartUnavailableNotice();
    }
  }, 200);

  // Also redraw immediately on view/tab change (covers post-5s view toggles)
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.mini-toolbar__view button, .dash-tab').forEach(function(el){
      el.addEventListener('click', function(){ setTimeout(drawAll, 60); });
    });
  });
})();
"""

QUERY_CHART_SCRIPT = r"""
(function(){
  var drawn = {};
  function canvases(){ return Array.prototype.slice.call(document.querySelectorAll('canvas[id]')); }
  function draw(cv){
    var id = cv.id;
    if (drawn[id]) return true;
    var dataEl = document.getElementById(id + '-data');
    if (!dataEl) { drawn[id] = true; return true; }
    if (typeof Chart === 'undefined') return false;
    if (!cv.offsetParent || cv.clientWidth === 0) return false;
    try {
      var cfg = JSON.parse(dataEl.textContent);
      // Bubble points carry {x, y, r, label: keyword} (see
      // _opportunity_map_config) but the JSON config stays function-free,
      // so the tooltip label callback that reads it back out is attached
      // here instead.
      if (cfg.type === 'bubble') {
        cfg.options = cfg.options || {};
        cfg.options.plugins = cfg.options.plugins || {};
        cfg.options.plugins.tooltip = cfg.options.plugins.tooltip || {};
        cfg.options.plugins.tooltip.callbacks = Object.assign({}, cfg.options.plugins.tooltip.callbacks, {
          label: function(ctx){
            var raw = ctx.raw || {};
            return (raw.label || '') + ': (' + raw.x + ', ' + raw.y + ')';
          }
        });
      }
      new Chart(cv, cfg);
      drawn[id] = true; return true;
    } catch (e) { console.error('chart draw failed', id, e); return false; }
  }
  function drawAll(){ canvases().forEach(draw); }
  var attempts = 0, MAX = 25;
  var timer = setInterval(function(){
    attempts++; drawAll();
    var done = canvases().every(function(cv){ return drawn[cv.id]; });
    if (done || attempts >= MAX) clearInterval(timer);
  }, 200);
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('.mini-toolbar__view button, .dash-tab').forEach(function(el){
      el.addEventListener('click', function(){ setTimeout(drawAll, 60); });
    });
  });

  // A4 canvases only get real layout (offsetParent/clientWidth) once the
  // A4 view is visible, so if the user prints straight from the default
  // dashboard view the A4 chart areas would print blank. Force the A4
  // view + a draw pass right before print, then restore whichever view
  // the user was on.
  var wasDashBeforePrint = false;
  window.addEventListener('beforeprint', function(){
    wasDashBeforePrint = document.body.classList.contains('view-mode-dash');
    if (wasDashBeforePrint) {
      document.body.classList.remove('view-mode-dash');
      document.body.classList.add('view-mode-a4');
    }
    drawAll();
  });
  window.addEventListener('afterprint', function(){
    if (wasDashBeforePrint) {
      document.body.classList.remove('view-mode-a4');
      document.body.classList.add('view-mode-dash');
    }
  });
})();
"""


def _load_labels(skill: str, lang: str) -> dict:
    """Label file is chosen by REPORT LANGUAGE, not by market.

    `--gl` (market) and `--lang` (report language) are independent: a US-market
    report can be written in Japanese. Only the market *name* on the cover comes
    from `--gl`, and that string is read out of the report-language label file
    (`country.US` / `report.marketLabel.US`), which every language ships."""
    return json.loads((LABELS_DIR / f"{skill}.{lang}.json").read_text(encoding="utf-8"))


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
    # Single-pass substitution: a sequential str.replace loop re-scans
    # already-substituted content, so a keyword/actions field that happens
    # to contain a literal "{{SOME_BLOCK}}" token gets expanded a second
    # time once that block's turn comes up (e.g. duplicating the actions
    # panel into a table cell). re.sub's replacement text is never
    # re-scanned, so this is immune to that.
    pattern = re.compile("|".join(re.escape(k) for k in blocks))
    return pattern.sub(lambda m: blocks[m.group(0)], template)


# ─────────────────────────────────────────
# Skill: query-opportunity (persona/customer-analysis card layout)
# ─────────────────────────────────────────
#
# Renders query-opportunity groups through the ported zip customer-analysis
# card renderer (components.py) instead of the chart-based components_query
# builders. Inputs are persona-shaped:
#   --groups {"groups": [{name, relation, stage, who, situation, needs,
#             painpoint, kbf: [{factor, evidence_keywords}], insight,
#             evidence: [{kw, volLabel}]}, ...]}
#   --actions {synthesis, insights: [{title, body}], now: [...], future: [...]}
#             (구버전 summary 필드는 무시됨 — synthesis 와 중복이라 폐지)
#   --meta lm_query_result.json ({keywordCount, groupCount/queryCount, ...})
# `relation == "alternative"` groups render in the 브랜드·논브랜드 section;
# everything else (including a missing `relation`) renders as a core
# 검색목적 group. See docs/superpowers/specs/2026-07-16-queryfinder-zip-redesign-blend.md.

def _report_lang(args) -> str:
    """Report language. Defaults to the market so the single-language editions
    keep working with `--gl` alone; the English-prompt edition passes `--lang`
    explicitly and may pair any market with any report language."""
    if getattr(args, "lang", None):
        return args.lang.lower()
    gl = args.gl.lower()
    return gl   # 생략하면 시장 언어로 쓴다 (kr·jp·us 모두 라벨이 있다)


def render_query(args) -> str:
    gl = args.gl.lower()        # 분석 대상 시장
    lang = _report_lang(args)   # 리포트에 쓰는 언어
    groups_doc = json.loads(Path(args.groups).read_text(encoding="utf-8"))
    groups = groups_doc.get("groups", [])
    # 3단계 LLM 분석 개요 — postprocess 가 통과시킨 값. 커버 요약문으로 렌더.
    overview = groups_doc.get("overview") or ""
    actions = json.loads(Path(args.actions).read_text(encoding="utf-8")) if args.actions else {}
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if args.meta else {}

    core = [g for g in groups if g.get("relation") != "alternative"]
    alt = [g for g in groups if g.get("relation") == "alternative"]

    meta_view = {
        "date": args.date,
        # clusterCount is unused by the adapted label string but kept for
        # safety/forward-compat with the ported dash_cover_html signature.
        "clusterCount": meta.get("keywordCount", 0),
        "keywordCount": meta.get("keywordCount", 0),
        # 커버 메타의 '검색목적 N'은 core 그룹만 센다 — 브랜드/논브랜드(alt)는
        # altCount 로 분리 (감사 갭 ⑤-2).
        "personaCount": len(core),
        "altCount": len(alt),
    }

    labels = _load_labels("query-opportunity", lang)

    # 검색어 번역 — 시장 언어와 리포트 언어가 다를 때만 넘어온다
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8")) if args.translations else {}
    ca.set_translations(translations)
    tr_btn = (
        f'<button type="button" class="mini-toolbar__tr" aria-pressed="false">'
        f'{html.escape(ca.t(labels, "customerAnalysis.dash.keywordTranslateBtn"))}</button>'
    ) if translations else ""
    gl_label = ca.t(labels, f"country.{gl.upper()}")
    market_label = ca.t(labels, f"report.marketLabel.{gl.upper()}")

    # {{CATEGORY}} is injected raw into <title> by _common_blocks/_apply (a
    # plain string replace with no further escaping), so it must be escaped
    # here. The other args.category usages below go through ca builders that
    # already html-escape it internally, so they stay unescaped to avoid
    # double-escaping.
    blocks = _common_blocks(
        "query-opportunity", lang, html.escape(args.category), labels,
        title_key="agent.customerAnalysis.name",
        dash_tab_key="customerAnalysis.dash.personaTab",
    )
    blocks.update({
        "{{DASH_COVER}}": ca.dash_cover_html(meta_view, args.category, gl_label, market_label, labels, overview=overview),
        "{{DASH_TABS}}": ca.dash_tabs_html(labels),
        "{{DASH_PANEL_PERSONAS}}": ca.dash_panel_personas_html(core, alt, args.category, labels),
        "{{DASH_PANEL_ACTIONS}}": ca.dash_panel_actions_html(actions, labels),
        "{{A4_COVER_PAGE}}": ca.a4_cover_page_html(meta_view, args.category, labels),
        "{{A4_BODY_PAGE}}": ca.a4_body_page_html(groups, actions, args.category, market_label, labels, overview=overview),
        "{{INLINE_SCRIPT}}": BASE_SCRIPT,
        "{{TOOLBAR_TR_BTN}}": tr_btn,
    })
    template = (TEMPLATES_DIR / "query-opportunity.html").read_text(encoding="utf-8")
    return _apply(template, blocks)


# ─────────────────────────────────────────
# CLI
# ─────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Render ListeningMind.AI-style HTML report.")
    parser.add_argument("--skill", required=True, choices=["query-opportunity"])
    parser.add_argument("--category", required=True)
    # --gl = 분석 대상 시장 (MCP 조회와 표지의 시장명)
    # --lang = 리포트 언어 (라벨·폰트·본문). 생략하면 시장을 따른다.
    parser.add_argument("--gl", required=True, choices=["kr", "jp", "us"],
                        help="target market to analyse")
    parser.add_argument("--lang", choices=list(REPORT_LANGS),
                        help="report language (default: same as --gl)")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output HTML path")
    parser.add_argument("--groups", help="query-opportunity: lm_groups.json path ({\"groups\": [...]})")
    parser.add_argument("--actions", help="query-opportunity: lm_actions.json path")
    parser.add_argument("--meta", help="query-opportunity: lm_query_result.json path")
    parser.add_argument("--translations",
                        help="keyword translation map {keyword: translation} — adds the "
                             "keyword-translation toggle. Only when the market language "
                             "differs from --lang.")
    args = parser.parse_args()

    if args.skill == "query-opportunity":
        for k in ("groups", "meta"):
            if not getattr(args, k):
                parser.error(f"query-opportunity requires --{k}")
        html_str = render_query(args)

    Path(args.out).write_text(html_str, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
