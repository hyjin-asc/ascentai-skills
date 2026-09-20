## Check the prerequisites

`{SKILL_DIR}/_shared/render/{query_aggregate.py,render_report.py,components.py}` and
`{SKILL_DIR}/_shared/{styles,labels,templates}` must all be present.
Only python3 (standard library) is used. The report uses the same card format as the zip customer-analysis.

This report ports the analysis frame of ListeningMind **QueryFinder (Intent Finder)**'s real analysis
prompt (agent_query v0.4.7): it groups related queries by **search purpose** and by
**brand/non-brand** and presents them as cards.

<!-- NOTE: the snapshot below lives in the source repository only — it is not
     shipped inside this skill. It is diff material for maintainers, not runtime input.
     Snapshot: skills/queryfinder-report/prompts/agent_query.1958.kr.md (v0.4.7, 2026-07-01)
     + agent_system_prompt.1758.kr.md (the agent's system prompt)
     + agent_default_template.1726.kr.md (the wrapper that assembles context_csv +
       previous turn + the question around it)
     — all exported from the gpt_prompt DB (intent-finder-dev) on 2026-07-20.
     The Step 3 prompt is a JSON-output adaptation of that original's analysis frame (target/intent
     keyword decomposition · intent types · Top5 search purposes · Top5 brand/non-brand · volume_avg
     only · top 1,000 cap); diff against this snapshot when you need to verify that.
     The chat-only markup (:k[]/:::accordion/➊) and the "state exact numbers" rule are deliberately
     excluded (numbers are filled in by Python — decision 1).
     The kbf field is a skill-specific extension not in the original (derived from the zip
     persona-card schema).
     Re-check quarterly against the latest active KR row in gpt_prompt to see whether the
     production prompt has changed. -->

## Execution procedure

### Step 0 — Collect inputs + create the working folder

Data comes from the **ListeningMind MCP tools**. **Never ask for an API key** —
do not even look for a key in environment variables, `.env`, a DB or anywhere else.

You need three inputs, and **TARGET MARKET and REPORT LANGUAGE are independent of each other**
(analyzing the US market and writing the report in Japanese is a valid combination):

> Starting the query opportunity analysis.
> 1. **Seed keyword** — skip if you already gave it
> 2. **TARGET MARKET (`gl`)** — the search market to analyze: `kr` · `jp` · `us`
> 3. **REPORT LANGUAGE (`lang`)** — the language of the report: `kr` (Korean) · `jp` (Japanese) · `us` (English)

If the seed is already given, proceed with it. If the market or the report language is not explicit
in the user's request, **ask once, in a single short question, and do not guess.** There is no
default for either — never derive the report language from the market or the market from the report
language. **Fix both values before any tool call.**

From this point on, `<MARKET>` is the value of TARGET MARKET and `<REPORT_LANGUAGE>` is the value of
REPORT LANGUAGE. **Conduct the whole conversation with the user in REPORT LANGUAGE**
(`kr` → Korean, `jp` → Japanese, `us` → English).

**If the MCP connector is missing** · the tool call in Step 1 fails with "tool not found".
In that case, ask the user to **connect the ListeningMind MCP connector** and stop.
Do not invent data.

Once you have the inputs:

```bash
SAFE_SEED=$(echo "<SEED>" | tr ' /\\:*?"<>|' '_')
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
WORKDIR="$PWD/tmp/reports/listeningmind-query-opportunity-${SAFE_SEED}-${TIMESTAMP}"
mkdir -p "$WORKDIR"
echo "working folder: $WORKDIR"
```

From here on, substitute the absolute path above for `{WORKDIR}`.

### Step 1 — Collect the data (2 MCP calls)

The **list** of related keywords and the keyword **details** come from two different tools.
Get the list of related keyword strings with `intent_finder`, pass that list to `keyword_info` to get
the details containing search volume, intent and monthly trend, and save it as `lm_query.json`.

> **Every call takes 3 steps, without exception** (SKILL.md §Step 4):
> ① `mcp_cache.py lookup` → ② (on a miss) call MCP + dump + `store` → ③ `log_event.py --type tool_call`
>
> If ① returns **exit 0**, the file is already filled, so **do not call MCP** — go to ③
> (`--cached --used-credits-delta 0`). On **exit 2**, proceed to ②.

**1a. List of related keywords** — `intent_finder`

```bash
# ① check the cache
python3 {SKILL_DIR}/scripts/mcp_cache.py lookup intent_finder \
  --params '{"keywords":["<SEED>"],"gl":"<MARKET>","limit":1000,"sort":"volume_avg","order":"desc","volume_threshold":0}' \
  --out "{WORKDIR}/lm_keyword_list.json"
```

On a miss, call the **`intent_finder` MCP tool** with these parameters:

```json
{"keywords": ["<SEED>"], "gl": "<MARKET>", "limit": 1000,
 "sort": "volume_avg", "order": "desc", "volume_threshold": 0,
 "user_query": "<the user's utterance, verbatim>"}
```

> **`volume_threshold` must be `0`** — this collects **all** related keywords, exactly like the
> ListeningMind web UI. Raising it cuts off every keyword whose monthly average search volume is
> below that value and distorts the landscape (e.g. `100` drops the entire long tail under 100
> searches). The quantity is already capped by `limit:1000` plus the later top-1,000 cap, so do not
> cut further with a lower bound.
>
> `user_query` is excluded from the cache key, so do not put it in `--params` (see the lookup above).

Dump the raw response verbatim and store it in the cache (`data` = an array of keyword strings,
sorted by search volume):

```bash
cat > "{WORKDIR}/lm_keyword_list.json" <<'DUMP_EOF'
<the entire raw intent_finder response JSON>
DUMP_EOF

# ② store in the cache · pass the length of the envelope's data array to --expect for verification
python3 {SKILL_DIR}/scripts/mcp_cache.py store intent_finder \
  --params '{"keywords":["<SEED>"],"gl":"<MARKET>","limit":1000,"sort":"volume_avg","order":"desc","volume_threshold":0}' \
  --file "{WORKDIR}/lm_keyword_list.json" --expect <length of the data array>

# ③ emit tool_call
python3 "$SKILL_DIR/scripts/log_event.py" --type tool_call --session-id "$SID" \
  --tool intent_finder \
  --request-body '{"keywords":["<SEED>"],"gl":"<MARKET>","limit":1000,"volume_threshold":0}' \
  --used-credits-delta <cost_detail.total_cost> \
  --used-credits-cumulative <used_credits> \
  --intent query_expansion
```

**1b. Keyword details** — `keyword_info` · the **top 1,000** of the list above
(the same cap as production QueryFinder — `ai-context-intent` size=1000 / `MAX_KEYWORDS=1000`)

First take the top 1,000 from the list and build a parameter file:

```bash
python3 - "{WORKDIR}/lm_keyword_list.json" <<'MKPARAM' > "{WORKDIR}/kw_params.json"
import json, sys
d = json.load(open(sys.argv[1]))
# Fetch details for all top 1,000, exactly like production (keyword_info maxItems=1000).
# Cutting to e.g. 200 under-counts total search volume and group sums and diverges from production.
kws = [k for k in d.get("data", []) if isinstance(k, str)][:1000]
print(json.dumps({"keywords": kws, "gl": "<MARKET>", "data_type": "all"}, ensure_ascii=False))
MKPARAM

# ① check the cache
python3 {SKILL_DIR}/scripts/mcp_cache.py lookup keyword_info \
  --params-file "{WORKDIR}/kw_params.json" \
  --out "{WORKDIR}/lm_query.json"
```

On a miss, call the **`keyword_info` MCP tool** with the contents of `kw_params.json` + `user_query`.
**`data_type` must be `all`**, and **all top 1,000 keywords must be included**.

> **Do not reduce the scale on your own.** Cutting to 300 keywords or lowering `data_type` to
> `ads_metrics` because the response is large under-counts total search volume and group sums and
> leaves the monthly trend chart empty, diverging from production. If the response is large, handle
> it with **Path A** below — there is no reason to narrow the query scope.

How you receive the response splits into two paths depending on its size.

#### Path A — the response was large and the host saved it to a file (1,000 keywords usually lands here)

The tool result sometimes arrives as this notice instead of a body:

```
Tool result too large for context, stored at
/mnt/user-data/tool_results/ListeningMind_keyword_info_<id>.json.
Use grep to search for specific content or head/tail to read portions.
```

**This is not a failure. The entire response is already in that file.**
There is no need to transcribe it — **just pass that path through**:

> The saved file may be wrapped in `[{"type":"text","text":"<JSON string>"}]` instead of being the
> envelope itself (observed · the claude.ai web container). `mcp_cache.py store` **unwraps and
> normalizes it automatically**, so leave it alone. The record count after unwrapping is what gets
> checked against `--expect`.

```bash
# use the path the host saved, as-is (no loss of the raw response)
SAVED="/mnt/user-data/tool_results/ListeningMind_keyword_info_<id>.json"

# check the record count (count only, without reading the body into your context)
python3 -c "import json;print(len(json.load(open('$SAVED')).get('data',[])))"

# copy into the working folder + store in the cache
cp "$SAVED" "{WORKDIR}/lm_query.json"
python3 {SKILL_DIR}/scripts/mcp_cache.py store keyword_info \
  --params-file "{WORKDIR}/kw_params.json" \
  --file "{WORKDIR}/lm_query.json" --expect <the count from above>
```

Read the credits from the envelope — even for a large file, you only need these two values:

```bash
python3 -c "
import json; d=json.load(open('$SAVED'))
print('delta=', (d.get('cost_detail') or {}).get('total_cost'))
print('cumulative=', d.get('used_credits'))"
```

#### Path B — the response came straight into the context (small payloads)

Dump the raw response **verbatim, with no processing**:

```bash
cat > "{WORKDIR}/lm_query.json" <<'DUMP_EOF'
<the entire raw keyword_info response JSON>
DUMP_EOF

python3 {SKILL_DIR}/scripts/mcp_cache.py store keyword_info \
  --params-file "{WORKDIR}/kw_params.json" \
  --file "{WORKDIR}/lm_query.json" --expect <length of the data array>
```

#### ③ Emit tool_call (same for Path A and Path B)

```bash
python3 "$SKILL_DIR/scripts/log_event.py" --type tool_call --session-id "$SID" \
  --tool keyword_info --request-body "$(cat "{WORKDIR}/kw_params.json")" \
  --used-credits-delta <cost_detail.total_cost> \
  --used-credits-cumulative <used_credits> \
  --intent query_expansion
```

> **Forbidden** · because the response is large, do not ① reduce the number of keywords,
> ② lower `data_type`, or ③ summarize/excerpt records when transcribing. All three silently corrupt
> the report's numbers. If it is large, use Path A and verify the count with `--expect`; on a
> mismatch, `store` rejects it. If you judge that reducing scope is unavoidable, **do not proceed —
> report the situation to the user.**

> Comparison with production: the Hubble chat QueryFinder (`ascentkorea-hubble-ai-api`) makes
> **a single call** to the internal API `ai-context-intent` and gets the top 1,000 with full metrics.
> That internal endpoint is not public, so with the public tools we reproduce the same top-1,000
> dataset with **2 calls**: `intent_finder` (top 1,000) + `keyword_info` (details for those 1,000).
> (Both are capped at the top 1,000, so the totals may differ from the all-keyword totals in the
> ListeningMind web UI.)

The `keyword_info` response carries `data` (an array of records) at the top level, so the aggregator
reads it directly (no transformation needed — each record contains `keyword` · `ads_metrics` ·
`intents` · `monthly_volume`).

- If either response has `result` = `FAILED`, or a tool call fails, stop and report (check the
  connector, the seed and gl). Do not invent data.
- If there are fewer than 200 related keywords, continue with however many there are.

### Step 2 — Aggregate the keyword context

```bash
python3 {SKILL_DIR}/_shared/render/query_aggregate.py context \
  --raw "{WORKDIR}/lm_query.json" --seed "<SEED>" --gl <MARKET> --date <YYYY-MM-DD> \
  --out "{WORKDIR}/lm_query_result.json"
```

`lm_query_result.json` contains `volume_avg` · `intents` per keyword, plus the `csv` text to feed
into the LLM analysis. Read `{WORKDIR}/lm_query_result.json` and use the `csv` value inside it as the
input for the Step 3 analysis.

### Step 3 — QueryFinder analysis (LLM) → lm_groups_raw.json

Following the rules below, analyze the `csv` (related query data) from `lm_query_result.json`
yourself and save the result **as JSON only** to `{WORKDIR}/lm_groups_raw.json`.

---

#### Analysis rules (Data Insight Analyst)

You are a search data insight analyst. Your goal is to analyze the related query data and derive the
**search intent (search purpose)**, the **brand/non-brand landscape**, and strategic insights.

**Input**: each row of `csv` = `keyword, volume_avg, i, n, c, t, trend`
- `volume_avg` = **monthly average search volume**. Always use this value for search volume
  (never an annual total, never ×12).
- `i/n/c/t` = the share of informational / navigational / commercial / transactional intent.
- `trend` = the rise/fall trend of search volume (positive = rising, negative = falling).
  **Rising keywords are emerging demand, so pay attention to them in grouping and insights**, but
  refer to the trend only in qualitative terms such as "rising" or "declining" — never with numbers
  or multipliers.
- Analyze only the top 1,000 by descending `volume_avg` (already sorted and capped).

**Global rules**:
- **Evidence from the data only**: do not invent facts that are not in the csv.
- **Avoid asserting numbers (important)**: **do not state numbers in text** — search volumes, shares,
  rankings ("the largest", "OO%", "N times" are forbidden). The numbers are filled in as measured
  values by code in the report's badges and keyword chips, so you write **only the qualitative
  interpretation** (why people search this way, what the intent is, how to address it).
- **No markup**: never use special markup such as `:k[]`, `:c[]`, `:::accordion`, `➊➋➌`, code blocks
  or tables. Put plain text strings only into the JSON values.
- **Output language** = REPORT LANGUAGE (`<REPORT_LANGUAGE>`): `kr` → Korean, `jp` → Japanese,
  `us` → English. This is independent of the target market — if the market is `kr` and REPORT
  LANGUAGE is `us`, you analyze Korean keywords and write every analysis text in English.
- `memberKeywords` must use only the verbatim keyword strings that actually appear in the csv
  (no translation, no alteration) — even when they are in a different language from the report.

**① Analysis overview (overview)**
- 1–2 sentences (100–200 characters) of the core insight running through the whole search dataset.
  Write it as prose, with no subheading.
- This sentence goes straight onto the report cover, under "Background and purpose", as the
  **data-based summary**.

**② Top 5 search purposes (intentGroups)** — at most 5
- Decompose each keyword into a **target keyword** (product · brand · category noun) and an
  **intent keyword** (the word revealing the context or situation).
- **Group semantically similar intent keywords into a single search purpose group** (multiple
  keywords per group; do not list groups that contain a single keyword).
- Each group:
  - `title`: the search purpose name (e.g. "Price and retailer comparison", "Looking up usage and
    care information") — clear and specific
  - Do **not** write an intent badge — code computes it from the csv `i/n/c/t` shares,
    volume-weighted, and fills in the group's dominant intent and its share (same principle as
    every other number in the report: facts by code, interpretation by you).
  - `memberKeywords`: the csv keywords belonging to this purpose (ordered by importance)
  - `who`: 1–2 sentences on the context and mindset of the people making these searches (qualitative)
  - `insight`: 1–2 sentences of marketing/product implications for addressing them (qualitative)
  - `kbf`: (optional) 2–4 entries of
    `[{ "factor": "decision/interest factor", "evidence_keywords": ["evidence keyword", ...] }]`

**③ Top 5 brand/non-brand (brandGroups)** — at most 5
- Identify brand keywords (product · brand names) and non-brand keywords (generic categories) in the
  csv. Weight brands more heavily.
- Each group:
  - `name`: **a single real brand name** (e.g. "Nike") or a non-brand category name
  - `kind`: `"brand"` or `"nonbrand"`
  - `memberKeywords`: the csv keywords that group under this brand/category
  - `label`: a label describing the group's character (written in REPORT LANGUAGE, e.g.
    "leading performance running shoe brand")
  - `analysis`: 1–2 sentences of qualitative analysis/insight about this brand or non-brand group

---

#### Output format — JSON only (no Markdown, no markup)

```json
{
  "overview": "1-2 sentences of core insight",
  "intentGroups": [
    {
      "title": "search purpose name",
      "memberKeywords": ["keyword1", "keyword2"],
      "who": "1-2 sentences on the context of the people searching this",
      "insight": "1-2 sentences of implications",
      "kbf": [{"factor": "factor", "evidence_keywords": ["evidence keyword 1"]}]
    }
  ],
  "brandGroups": [
    {
      "name": "real brand name",
      "kind": "brand",
      "memberKeywords": ["keyword1"],
      "label": "group character label",
      "analysis": "1-2 sentences of qualitative analysis"
    }
  ]
}
```

Write all of those text values in REPORT LANGUAGE, except `memberKeywords` and
`kbf.evidence_keywords`, which stay verbatim as they appear in the csv.

Save the JSON above to `{WORKDIR}/lm_groups_raw.json`.

### Step 4 — Post-process the groups (volume sums · evidence keywords)

```bash
python3 {SKILL_DIR}/_shared/render/query_aggregate.py groups \
  --raw-groups "{WORKDIR}/lm_groups_raw.json" \
  --context "{WORKDIR}/lm_query_result.json" \
  --out "{WORKDIR}/lm_groups.json"
```

This script computes, in code, each group's `volume_avg` sum and ordering, the evidence keywords
(evidence, including search volume labels), and `volumeLabel` (summed search volume) and
`memberCount` (number of keywords) for the group header badges, saves them in card form, and passes
Step 3's `overview` through as the cover summary.
(The numbers are filled in here as measured values.)
It also **blocks hallucinations**: any keyword in `memberKeywords` or `kbf.evidence_keywords` that is
not in the csv is removed here, and any group or kbf row left without a single real keyword is
discarded entirely.
If this filter empties the Step 3 result (0 groups), the script stops with an error — redo Step 3.

### Step 5 — Insights and action proposals (LLM) → lm_actions.json

Based on the groups in `{WORKDIR}/lm_groups.json`, save the JSON below to `{WORKDIR}/lm_actions.json`.
The output language is REPORT LANGUAGE (`kr` → Korean, `jp` → Japanese, `us` → English). **No asserting numbers**
(same as Step 3), no markup.

- `synthesis`: 2–3 sentences of overall assessment cutting across the search purposes and the brand
  landscape
- `insights`: **exactly 3** detailed insights `{"title","body"}` — logical conclusions grounded in
  the user behavior found in the data. Keep the title short and based on the core keyword; the body
  is 1–2 sentences. (No plain enumeration — derive them from the group analysis results.)
- `now`: 2–3 action proposals to try right away `{"title","body"}` (responding to specific search
  purposes/brands)
- `future`: 2–3 opportunities to watch going forward `{"title","body"}`

```json
{
  "synthesis": "2-3 sentences of overall assessment",
  "insights": [{"title": "headline based on the core keyword", "body": "behavior-based conclusion, 1-2 sentences"}],
  "now": [{"title": "action proposal headline", "body": "what to do, 1-2 sentences"}],
  "future": [{"title": "opportunity headline", "body": "description of the opportunity, 1-2 sentences"}]
}
```

(The `summary` field is retired — it duplicated synthesis and is not rendered. It is ignored if present.)

### Step 5.5 — Translate the keywords (only when MARKET language ≠ REPORT LANGUAGE)

The report shows keywords exactly as they are searched in the market. When the report language
differs from the market's language, the reader cannot read them, so build the translations that
back the **"Translate keywords" toolbar button** here. Skip this step when the market language and
REPORT LANGUAGE are the same — the button then does not appear at all.

First extract only the keywords that actually appear on screen:

```bash
python3 {SKILL_DIR}/_shared/render/query_aggregate.py keywords \
  --groups "{WORKDIR}/lm_groups.json" --category "<SEED>" \
  --out "{WORKDIR}/kw_to_translate.json"
```

Read the `keywords` array in that file and translate **every** keyword into REPORT LANGUAGE,
saving `{WORKDIR}/lm_keyword_tr.json` as `{"original": "translation", ...}`.

- Use the **original string as the key**, byte for byte (same spacing, same spelling variants).
  A key that does not match leaves that chip untranslated.
- Do **not** add keywords that are not in the list, and do not drop any.
- **Brand and product names take the form commonly used in REPORT LANGUAGE**
  (e.g. `다이소` → `Daiso` / `ダイソー`). If there is no common form, keep the original.
- These are search terms, not sentences. Keep them short, in the shape someone would type.
- Translate only — never append a gloss or an explanation.

### Step 6 — Render the HTML

```bash
python3 {SKILL_DIR}/_shared/render/render_report.py --skill query-opportunity \
  --groups "{WORKDIR}/lm_groups.json" \
  --actions "{WORKDIR}/lm_actions.json" \
  --meta "{WORKDIR}/lm_query_result.json" \
  --category "<SEED>" --gl <MARKET> --lang <REPORT_LANGUAGE> --date <YYYY-MM-DD> \
  --out "{WORKDIR}/query-opportunity-report.html"
# If you did Step 5.5, append --translations "{WORKDIR}/lm_keyword_tr.json"
```

`--gl` is the market that was analyzed; `--lang` is the language the report is rendered in. Pass both
— they are set independently in Step 0.

### Step 7 — Tell the user

Write this message to the user in REPORT LANGUAGE (`kr` → Korean, `jp` → Japanese, `us` → English), keeping the
paths and the command exactly as they are:

```
✅ Query opportunity analysis report created: {WORKDIR}/query-opportunity-report.html
Open it in a browser to see the search purpose and brand cards plus the insights in dashboard or A4
view, and press Cmd+P to save it as an A4 PDF.
Open it immediately on macOS: open {WORKDIR}/query-opportunity-report.html
```
