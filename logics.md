# FollowUp Dashboard — Project & Business Logic Reference

This file is the onboarding document for this project. If you are a fresh
Claude Code session picking this up on a new machine with no memory of how
it got this way, read this file before touching any code — it explains
what each file does, exactly how every number on the dashboard is computed
(verified line-by-line against the current code, not against an old spec),
and a list of known problems that were found but deliberately left
unfixed pending a decision. `app.py` renders; it has no business logic of
its own. All the actual math lives in `metrics.py`.

Several definitions below were corrected mid-project; deltas from an
earlier version are marked **[FIX]** so you know not to "fix" them back.
A separate **Known issues** section near the end lists things that are
*still* wrong or inconsistent, found during review but not yet changed —
check there before assuming something is a bug worth silently patching.

---

## Contents

0. [Project map](#0-project-map)
0. [Running it locally](#running-it-locally)
1. [Global filters](#1-global-filters)
2. [Overview (Card 1)](#2-overview-card-1)
3. [Audio Index](#3-audio-index)
4. [Due Today](#4-due-today)
5. [Overdue](#5-overdue)
6. [Funnel Quality](#6-funnel-quality)
7. [Follow-up funnel](#7-follow-up-funnel)
8. [Data source & dates](#8-data-source--dates)
9. [Shared conventions](#9-shared-conventions)
10. [Known issues — found, not yet fixed](#10-known-issues--found-not-yet-fixed)
11. [How this project has been worked on](#11-how-this-project-has-been-worked-on)

---

## 0. Project map

| File | Role |
|---|---|
| `followup_dashboard_query.sql` | The Metabase card's SQL (Card ID 5360). Defines every raw column the dashboard ever sees. Lives outside this repo in Metabase itself; this file is a copy for reference. Editing it does nothing unless it's pasted back into the actual Metabase card. |
| `data_pull.py` | Fetches the card from Metabase's `/api/card/{id}/query/json` export endpoint (not the interactive endpoint — that one caps at 2000 rows), normalises dtypes (dates/datetimes/bools/numerics/strings), slugifies column names to snake_case, and can write a CSV. Also exposes `load_followup_dashboard_data()` for reuse, and `apply_end_date_flags()` (see §8). Runnable standalone: `python data_pull.py --output followup_dashboard.csv`. |
| `metrics.py` | Every business-logic function. Pure pandas, **no Streamlit import** — it's deliberately reusable/testable independent of the UI. This file is what §1–§9 below describe. |
| `app.py` | The Streamlit UI. Loads `followup_dashboard.csv` (static — no live fetch), renders the sidebar (city nav), and renders the six dashboard sections by calling into `metrics.py` and formatting the results. No business logic here beyond display formatting (rounding, `%` suffixes, table styling). |
| `config.toml` | **Local only, gitignored.** Metabase URL / API key / card ID, plus default filter values for `data_pull.py`'s CLI. Only needed to regenerate the CSV; the deployed dashboard never reads it. **Contains a real, live API key in plaintext** — see Known issues. |
| `followup_dashboard.csv` | The dashboard's only data source: a snapshot produced by `data_pull.py`. Loaded once per server process and cached; replaced by regenerating the file and redeploying. |
| `followup_dashboard_column_labels.json` | `{slug_name: original_metabase_label}` mapping produced by `data_pull.py --output ...` alongside the CSV. Not read by `app.py` at runtime — informational only, for tracing a slugified column back to the SQL's own `SELECT ... AS "..."` alias. |

## Running it locally

1. `pip install -r requirements.txt` (and `tomli` only if Python < 3.11).
2. `streamlit run app.py` — it renders `followup_dashboard.csv` as-is. No
   Metabase key or network access is needed to view the dashboard.
3. To refresh the data (optional, separate from the dashboard): put a real
   Metabase API key in `config.toml`'s `[metabase]` block (or set the
   `MB_API_KEY` environment variable — also `MB_URL` / `MB_CARD_ID`), run
   `python data_pull.py --output followup_dashboard.csv`, then commit the
   new CSV and push; Streamlit Cloud redeploys and picks it up.

---

## 1. Global filters

Applied to the WHOLE dashboard, before any section-specific logic — every
section below starts from this already-filtered `city_df`, not from the
raw pulled data:

1. `cluster` == the selected city (case-insensitive), or no filter at all
   in Pan-India mode.
2. `sc_channel` == `"Field Sale SC"` (case-insensitive). Applied globally,
   not just for Card 1's "Meetings done" count.

---

## 2. Overview (Card 1)

Every row is classified into exactly ONE of four mutually exclusive
buckets (`metrics._followup_buckets`), by priority (first match wins —
the raw conditions overlap on their own, so priority ordering is what
makes the subtraction identity below exact rather than approximate):

1. **Closed on spot** — `is_on_spot == True`
2. **Closed on Follow-Up** — (not bucket 1) AND `is_on_spot == False` AND
   `order_closure_datetime_ist` NOT blank
3. **No audio notes** — (not 1/2) AND `audio_present` is NOT true (`False`
   or missing) AND `order_closure_datetime_ist` blank. **[FIX]** — this
   used to be `notes_submitted_in_range == 0`, which a blank/missing value
   never satisfies (`NaN == 0` is always `False` in pandas). A lead with no
   `meeting_metrics_history` row inside the pull's `[start_date, end_date]`
   has both `notes_submitted_in_range` and `audio_present` missing, so the
   old condition silently let those leads fall through to bucket 4
   (Eligible) instead of bucket 3. Checking `audio_present` directly
   (missing treated the same as `False`) catches them correctly.
4. **Eligible for follow-ups** — everything else in Meetings done

- **Meetings done** = count of all rows (post global filters, i.e.
  `sc_channel == "Field Sale SC"`).
- Identity: `Eligible = Meetings done − Closed on Follow-Up − No audio notes − Closed on spot` (exact, by construction).
- Displays 5 numbers: Meetings done, Closed on spot, Closed on Follow-Up,
  No audio notes, Eligible for follow-ups — each of the last 4 with a
  "% of Meetings done" caption.
- City breakdown table (Pan-India view): same 5 counts, one row per city.
- `eligible_for_followups_pool()` is this bucket-4 population as a
  DataFrame — it's shared by three other sections (Audio Index, Overdue,
  and nothing else currently), so all mentions of "the Eligible pool"
  below refer back to this exact same definition.

---

## 3. Audio Index

**Base pool = `eligible_for_followups_pool`** — the same "Eligible for
follow-ups" bucket as Overview's bucket 4. (This used to be its own,
separately-defined `audio_present==True AND is_on_spot==False` pool; it
now shares Overview's pool so the two sections never disagree about which
leads are "eligible.")

- **Coverage %** = count(eligible pool) ÷ count(Meetings done AND
  `order_closure_datetime_ist` blank) × 100 — irrespective of Closed on
  spot / Closed on Follow-Up bucket membership; the lead's own
  `order_closure_datetime_ist` value is checked directly. **[FIX]** — the
  denominator used to be count(`is_on_spot == False`), which still
  included Closed on Follow-Up leads (not on-spot, but
  `order_closure_datetime_ist` IS filled for them) and so overcounted.
  Every Closed-on-spot lead also has `order_closure_datetime_ist` filled
  (confirmed against the data — 0 exceptions), so the new denominator is
  algebraically identical to "No audio notes" count + "Eligible for
  follow-ups" count, and cleanly excludes both closed buckets.
- **Completeness %** = `(denom_count × 11 − Σ dispositions_missing over that same wider denominator population) / (denom_count × 11) × 100`.
  **[FIX]** — this used to be computed over the eligible pool only; now it
  uses the SAME wider population as Coverage %'s denominator (No audio
  notes + Eligible). A "No audio notes" lead's `dispositions_missing`
  defaults to 11 (fully missing) in the source SQL, so folding those leads
  in pulls Completeness % down substantially versus the old
  eligible-only calculation — intentional, not a regression. Divisor is
  **11**, not 12 — `Remarks` is deliberately excluded from the 12
  disposition questions (it's free-text, not a structured disposition), so
  only 11 columns count toward missing/total.
- **Audio Index** = `(Coverage % × Completeness %) / 1000`, clipped to
  `[0, 10]`, rounded to 1 decimal. Shown as `"X.X / 10"`.
- **"From Meeting's Today" toggle**: when ON, restricts everything in this
  section to leads whose `first_meeting_done_date == as_of` (the
  dashboard's single "today" — see §8), then the eligible pool is
  recomputed on that narrowed set. OFF = no restriction.
  **[FIX, twice]** — this checked `fu_completedat_today == 1` originally
  (a follow-up-task flag, unrelated to when the meeting happened), then
  briefly `meeting_done_date` (that lead's most recent meeting activity,
  which can differ from their very first meeting), before landing on
  `first_meeting_done_date` specifically.
- Pan-India city breakdown table: same 3 numbers per city.

### Split (TL → SC → Lead)

TL/SC columns: **TL/SC name, Index, Leads, % leads with a disposition
missing, Dispositions missing (average per lead)**.
- **Index** = same formula as the headline Audio Index, computed for that
  TL/SC's own slice: needs BOTH the wider "Meeting Done" population
  (for Coverage %'s denominator AND Completeness %'s own population — the
  `order_closure_datetime_ist` blank check, per §3's headline formula) AND
  the eligible-pool slice (for Coverage %'s numerator only).
- **Leads** = count of eligible-pool rows under that TL/SC (unchanged —
  Leads/% missing/avg missing still describe the eligible-only
  population, distinct from the wider one Index's Completeness % uses).
- **% leads with a disposition missing** = share of those leads with
  `dispositions_missing > 0`.
- **Dispositions missing (average per lead)** = mean `dispositions_missing`
  over that TL/SC's leads.

Lead-level columns (`audio_lead_table`): **Lead ID, Category, Meeting
done, Priority, Audio Duration (in sec), Missing, FUs done, Last FU,
Due**.
- Category = the lead's own raw value (`category_display`): its raw
  `last_follow_up_status` if agreed_to_meet/another_follow_up_required,
  else "P1+P2" if priority is P1/P2, else "Others".
- **Meeting done = `first_meeting_done_date`. Last FU = `last_follow_up_date`.**
  Same definitions as Due Today's `lead_table` (§4). **[FIX]** — this used
  to be `last_follow_up_date` / `latest_status` (the latter blank for
  essentially every lead except the rare ones with more than 6 follow-up
  tasks — see §8's FU1..FU6 note); this table was missed when Due Today's
  version got the same fix, then corrected afterward.
- Audio Duration (in sec) = `audio_duration_sec`. Missing =
  `dispositions_missing`. FUs done = `total_followup_done`. Due =
  `followup_due_date`.
- Sorted by Category: agreed_to_meet first, then
  another_follow_up_required, then P1+P2, then everything else (Others).

### Disposition Breakdown

One row per disposition question — only **11** questions, `Remarks` is
excluded entirely (not fetched, not scored): Quoted Price, Offering,
Buffer/Margin Left, SC's Reading, Site Visit Done?, Bill Value, Competitor
Quote, Next Follow up (Voice Note), Timeline to go Solar, Decision Maker,
Main Objection.

Columns: **Leads** (count of eligible-pool rows blank for that
disposition), **Share** (÷ total eligible pool, %), **Of those, <45s** (of
the blank ones, how many have `audio_duration_sec < 45`).

---

## 4. Due Today

**Overall corpus = `due_today_corpus_pool`, which is
`eligible_for_followups_pool` again** — the same pool as Overview's Card 1,
Audio Index, and Overdue. **[FIX, major]** — this used to be the raw
cluster-filtered data with no Eligible restriction at all; `came_due_pool`
was applied directly to `city_df`. Every mention of "TL/SC roster" below
means every TL/SC present in THIS corpus, not just those with a due-today
lead.

Base pool for the actual Due Today numbers = `came_due_pool`, now always
called on the Eligible corpus above: rows (within it) where
`fu_due_date_today == 1`. **Not** a union with `fu_completedat_today==1` —
a lead with `fu_completedat_today=1` but `fu_due_date_today=0` is excluded
from this section entirely. (`fu_due_date_today`/`fu_completedat_today`
are recomputed against `as_of` in Python, not read as-is from the SQL —
see §8; this matters for exactly which leads land in this pool.)

### Summary boxes

Each of the 4 boxes shows a **Due / Worked / Booked** triple, all computed
on that box's own subset of `came_due_pool`:
- **Due** = count in that subset (already `fu_due_date_today==1` for all).
- **Worked** = of Due, `fu_completedat_today == 1`.
- **Booked** = of Due (same population as Worked, NOT of Worked) —
  `order_closure_datetime_ist` NOT blank. **[FIX]** — this used to be "of
  Worked, `order_closure_datetime_ist` NOT blank" (AND'd with the Worked
  condition); a lead whose order closed without today's specific
  follow-up task being marked complete was invisible to Booked even
  though the order genuinely was booked. Booked is now independent of
  Worked — same relationship Agreed+Another/P1+P2/Others already have to
  each other, not a subset-of-a-subset chain.
- **Worth knowing**: in the current data, Booked reads **0 everywhere** in
  this section (and in Overdue's, below) — not because of this
  independent-vs-nested distinction, but because `order_closure_datetime_ist`
  is *never* filled in for any lead in the Eligible-for-follow-ups corpus
  at all (confirmed directly: 0 of 5,793). That's structural, not a bug
  here: a lead with that column filled in always falls into "Closed on
  spot" or "Closed on Follow-Up" in `_followup_buckets` (§2), never
  "Eligible" — so no subset of the Eligible pool can ever show a non-zero
  Booked count, regardless of how Booked itself is defined.

| Box | Subset |
|---|---|
| Due Today / Worked / Booked | whole `came_due_pool` |
| Agreed to Meet + Another follow-up | `last_follow_up_status` in {agreed_to_meet, another_follow_up_required} |
| P1+P2 | `priority` in {P1, P2} |
| Others | ~Agreed+Another AND ~P1+P2 (checked against status first, then priority for what's left) |

All four boxes are **independent per-lead conditions**, including
Others. **[FIX, twice]** — Others was originally `total − Agreed+Another`
only, then briefly `total − Agreed+Another − P1+P2` (a residual), before
landing on the current direct condition. Because all three are
independent, **Agreed+Another + P1+P2 + Others can add up to MORE than
the top box's own Due/Worked/Booked** whenever a lead is in both
Agreed+Another and P1+P2 — that is expected, not a bug. The % under each
box = that box's own Worked ÷ Due.

Also shown: pending count (`Due − Worked` for the whole pool) and the TL
with the highest pending count.

### TL → SC → Lead drill-down (`breakdown_by` / `tl_breakdown` / `sc_breakdown`)

Every TL (and, within a TL, every SC) present anywhere in the Eligible
corpus gets a row — including one with zero due-today leads, shown as a
row of zeros rather than being absent. **[FIX]** — this used to seed rows
only from the already-`came_due`-filtered pool, so a TL/SC with no lead
due today never appeared in this table at all, even though they might
have plenty of other Eligible-for-follow-ups leads just not due today.

TL/SC view columns: **Due Today, Worked, Pending, Worked %** (Worked ÷
Due Today for that row, `0%` when Due Today is 0; column literally
labelled "Worked %" with a `%` suffix on the values), plus **Agreed to
Meet + Another follow-up / P1+P2 / Others** — the SAME independent-condition
convention as the summary boxes above, not a mutually-exclusive per-lead
bucket. **[FIX]** — this used to precedence-bucket each lead into exactly
one of the three columns (Agreed/Another > P1/P2 > Others via
`due_today_categorize`, now removed from the codebase entirely since
nothing calls it anymore), which made a column's sum across every TL (or
every SC) disagree with the summary card's own total for that column. Now
every column's sum across a whole level (every TL, or every SC) ties
exactly to the card, because `group_col` always partitions `came_due`
exactly (every row has exactly one tl and one sc) — a group-by sum of an
independent boolean condition can never double-count or drop a row (the
zero-count rows the roster fix adds don't change this identity, they just
add zeros to the sum). A single SC's numbers need not resemble its own
TL's row; only the SUM across a whole level is guaranteed to match.

Lead-level columns (`lead_table`): **Lead ID, Category, Meeting done,
Priority, Audio Duration (in sec), Missing, FUs done, Last FU, Due, State**.
`lead_table` itself is still scoped to `came_due_pool`, not the wider
Eligible corpus — drilling into a TL/SC with 0 Due Today correctly shows
an empty lead table, not an error.
- Category = the lead's own raw value (`category_display`) — same rule as
  Audio Index's Category above.
- **Meeting done = `first_meeting_done_date`. Audio Duration (in sec) =
  `audio_duration_sec`. Missing =
  `dispositions_missing`. FUs done = `total_followup_done`. Last FU =
  `last_follow_up_date`. Due = `followup_due_date`.** State = constant
  `"Due Today"`.
- Due isn't always today even though every row here is "due today" by
  `fu_due_date_today` — that flag now also checks `followup_due_date ==
  as_of` directly (see §8's `[FIX]`), so the two agree far more often than
  before, but not always: a lead can still reach `came_due_pool` via its
  FU1..FU6 due dates matching `as_of` while its *current* open task
  (`followup_due_date`) has since moved to a different date (e.g. the
  matching follow-up was rescheduled after the fact). This column shows
  `followup_due_date` as-is, so that narrower remaining gap can still show
  up here.
- Sort order: agreed_to_meet first, then another_follow_up_required, then
  P1+P2, then everything else (Others).

---

## 5. Overdue

**Base pool = `overdue_corpus_pool`, which is `eligible_for_followups_pool`
again** — the same pool as Overview's bucket 4 and Audio Index's base
pool. **[FIX, major]** — this used to be its own, separately-defined
population (every `funnel_bucket` except
booked/terminal/lost/later/customer_unreachable); it now shares the exact
same eligible-for-follow-ups pool that Card 1 and Audio Index use, so the
three sections never disagree about which leads are in scope.

- **`overdue_pool(df)`** = the corpus narrowed to `funnel_bucket ==
  'overdue'` — this is what feeds the headline card, the ageing split, the
  Agreed+Another/P1+P2/Others cards, and the lead-level drill-down.
- **Headline card: Overdue (as on today) / Worked today / Booked today** —
  a **Due / Worked / Booked** triple, same pattern as Due Today's cards
  (§4). **[FIX, major]** — this used to be a single "Overdue %" metric
  (overdue count ÷ corpus count) plus a separate "Total overdue leads"
  count; the % reading and the now-redundant separate count are both
  gone, replaced by this one triple:
  - **Overdue (as on today)** = count of `overdue_pool` (same number the
    old "Total overdue leads" metric showed).
  - **Worked today** = of those, `last_follow_up_date == as_of` (the
    dashboard's single "today" — see §8). **[FIX, three times]** — this
    was `fu_completedat_today == 1` originally (Due Today's own
    definition of "worked"), then briefly `last_follow_up_date` merely
    not blank (any date at all, which over-counts — a lead last followed
    up weeks ago still has a non-blank date, so that wasn't really "worked
    TODAY"). It's now an exact date match, which is the correct
    definition — **but it currently reads 0 everywhere anyway**, for a
    reason structurally identical to the very first `fu_completedat_today`
    problem: confirmed directly against the raw data, `funnel_bucket ==
    'overdue'` and `last_follow_up_date == as_of` never co-occur, city by
    city or dashboard-wide. The exact-match condition itself is not
    broken — 97 leads dashboard-wide DO have `last_follow_up_date ==
    as_of` — they're just never also `overdue`: every one of them falls
    into `this_week`, `tomorrow`, `today`, `terminal`, or
    `customer_unreachable` instead. Contacting a lead today appears to
    always move it out of the `overdue` bucket within the same snapshot
    (a new/rescheduled task pushes the due date forward, or the lead
    exits the funnel entirely) — so "an overdue lead that was also worked
    today" may not be something this data can ever represent, regardless
    of how Worked is defined.
  - **Booked today** = of Overdue (as on today) — independent of Worked,
    not nested inside it — `order_closure_datetime_ist` is not blank.
    **[FIX]** — same nesting fix as Due Today's Booked (§4): this used to
    be "of Worked today, `order_closure_datetime_ist` not blank." As with
    Due Today, Booked reads 0 regardless, for the structural reason
    explained in §4's note (the Eligible corpus never has
    `order_closure_datetime_ist` filled in for any lead).
  `pct_overdue` (the old overdue ÷ corpus ratio) is still computed and
  returned by `overdue_summary` — it's just not shown as its own card
  metric any more; the TL/SC table's own "Overdue %" column (below) still
  uses that same ratio, scoped per row.
- Also shown: TL with the most overdue leads (plain count).
- **Ageing split** (over `overdue_pool`, by `overdue_days`): Under 3 days
  (`<3`), 3-5 days (`3 ≤ x ≤ 5`), Over 5 days (`>5`).
- **Agreed to Meet + Another follow-up / P1+P2 / Others**: now ALSO
  Due/Worked/Booked triples (own Worked/Booked counts within that
  category), same as Due Today's per-category boxes. The independent-
  condition convention is unchanged — Agreed+Another and P1+P2 can
  overlap (a lead counted in both), and Others = ~Agreed+Another AND
  ~P1+P2, checked directly rather than as a residual — so on any of the
  three numbers, Agreed+Another + P1+P2 + Others can add up to more than
  the top box's own total whenever a lead is in both — expected, not a
  bug.
- **Worth knowing**: in the current data, `funnel_bucket == 'overdue'` and
  `fu_completedat_today == 1` never co-occur for any lead, dashboard-wide
  — confirmed directly against the raw data, not just this pool. So
  Worked today / Booked today read 0 across every city right now. This
  looks structural, not a bug: completing an overdue lead's follow-up
  today likely changes which task is "open" for it, which is what
  `funnel_bucket` itself is computed from in the SQL — by the time the
  snapshot is taken, that lead has probably already moved out of the
  `overdue` bucket entirely. Due Today's own Worked/Booked numbers don't
  have this problem, because `fu_due_date_today` is a fixed FU-task flag,
  independent of `funnel_bucket`.

### TL → SC → Lead drill-down (`overdue_breakdown_by`)

Every TL (and, within a TL, every SC) present anywhere in the Eligible
corpus gets a row — including one with zero overdue leads, shown as a row
of zeros rather than being absent. **[FIX]** — same bug, and same fix, as
Due Today's `breakdown_by` (§4): this used to seed rows only from the
overdue-only subset, so a TL/SC with no overdue lead never appeared in
this table at all, even though they might have plenty of other
Eligible-for-follow-ups leads just not overdue. Confirmed against the
data — e.g. Delhi's "Unassigned" TL has 0 overdue leads and now correctly
shows a 0 row instead of being missing.

TL/SC columns: **Overdue, Overdue %** (this group's own overdue count ÷
this group's own corpus count — same ratio as the section card, just
scoped, `0%` when Overdue is 0), the **Agreed+Another / P1+P2 / Others**
split (same independent-condition convention as the card above —
**[FIX]**, this used to precedence-bucket per lead, then briefly subtract
both from the group's Overdue count as a residual, before landing on the
current direct condition), and 3 more columns under an "Ageing" header:
**Under 3 days, 3-5 days, Over 5 days**. `cluster_df` passed in must be
the *full* cluster-filtered data (not just the overdue subset), so each
group's own corpus count can be computed.

Lead-level columns (`overdue_lead_table`): **Lead ID, Category, Meeting
done, Priority, Audio Duration (in sec), Missing, FUs done, Last FU, Due,
Ageing**.
- Category = same rule as Due Today.
- **Meeting done = `first_meeting_done_date`. Last FU = `last_follow_up_date`.**
  Same definitions as Due Today's lead table (§4). **[FIX]** — this used
  to be `last_follow_up_date` / `latest_status` (blank for ~all leads);
  this table was missed when Due Today's version got the same fix, then
  corrected afterward.
- Due = the lead's own `followup_due_date` (NOT today's date — different
  from Due Today's lead table, which also uses `followup_due_date` but
  for a different reason: every Due Today row genuinely came due today by
  the FU-flag definition, while an overdue lead's `followup_due_date` is
  in the past by definition).
- Ageing = `overdue_days` rendered as text, e.g. `"8 days"` (this column
  is named "State" in Due Today's lead table output, but "Ageing" here).
- Sort order: same as Due Today.

---

## 6. Funnel Quality

**Base pool = `funnel_quality_pool`, which is `funnel_pool` (§7) again --
and `funnel_pool` is now the same "Eligible for follow-ups" pool as
Overview/Audio Index/Overdue/Due Today** — see
`eligible_for_followups_pool`. **[FIX, major]** — both this section and
Follow-up funnel used to share their own, separately-defined
`audio_present`/`is_on_spot`/`order_closure_datetime_ist` pool; they now
share the exact same eligible-for-follow-ups pool every other section
uses. No separate Booked carve-out here; this section has no Booked box.

Three categories, each with its own threshold and denominator:

| Key | Condition | Threshold | Denominator |
|---|---|---|---|
| Did not pick up (dnp) | `last_follow_up_status == 'dnp'` | 25% | **"With an outcome"** — `last_follow_up_status` not blank |
| Did not pick up + Lost + Nurture | dnp OR lost_to_competitor OR **Nurture** | 45% | same "With an outcome" count |
| Another follow-up dated beyond 5 days | status in {agreed_to_meet, another_follow_up_required} AND `(next_follow_up_date − last_follow_up_date) > 5` | 50% | only leads with status in {agreed_to_meet, another_follow_up_required} (unchanged — kept including `agreed_to_meet`, not narrowed to `another_follow_up_required` alone) |

**[FIX]** — the first two categories' denominator used to be the whole
base pool (`len(pool)`), including leads with a blank
`last_follow_up_status` — even though the Basis column's own label text
already said "of leads with an outcome". The label and the number now
actually agree: `_fq_with_outcome(pool)` = `last_follow_up_status != ""`.

Since `dnp`, `lost_to_competitor`, and the three Nurture statuses are all
distinct `last_follow_up_status` values, no lead can match more than one
arm of "Did not pick up + Lost + Nurture" — that row's count is a clean
union, no double-counting.

**Nurture** = `last_follow_up_status` in {will_go_later, dropped_plan,
not_qualified_not_serviceable} — unconditional, no day-count on
`next_follow_up_date`. **[FIX]** — `will_go_later` used to only count as
Nurture past 60 days out from `last_follow_up_date`; every `will_go_later`
lead counts now, same as the other two statuses. **[FIX]** —
`not_qualified_not_serviceable` was previously misspelled
`"not_qualified_not_servicable"` in the code (missing the middle "e"),
which never matched the real data value, so those leads were silently
never counted as Nurture at all. Both are corrected now — check
`_NURTURE_STATUSES` in `metrics.py` if this list ever needs to change
again, and be careful with the spelling (the real column value is
"serviceable", standard English spelling, not "servicable").

Per-category row: Measure, Now % (matched ÷ denominator), Threshold %
(hardcoded above), Basis (denominator's label), State = **"Breaching"** if
Now % is strictly greater than Threshold, else **"Within"** (exactly on
threshold counts as Within).

Per-category SC expander: **SC, Leads** (total pool count for that SC),
**[category's matched-count column]**, plus **DNP%** (`matched ÷ leads ×
100`, 2 decimals with `%`) — this 4th column only appears on the "Did not
pick up" category's table, not the other two.

TL-wise cut (no SC drill): **TL, With an outcome** (count of non-blank
`last_follow_up_status`), then each category's % — DNP and
DNP+Lost+Nurture are now % of that TL's own "With an outcome" count
(**[FIX]** — used to be % of that TL's whole pool; now matches the Overall
cut's own Basis, so the two cuts agree on the same category's denominator);
Beyond-5-days is % of that TL's agreed_to_meet/another_follow_up_required
leads only, unchanged. A 0-denominator category renders as a dash, not 0%.

---

## 7. Follow-up funnel

**Base pool (`funnel_pool`, applies to every box except Booked) = the same
"Eligible for follow-ups" pool as Overview/Audio Index/Overdue/Due Today**
— see `eligible_for_followups_pool` and §6's note. **[FIX, major]** — this
used to be its own, separately-defined pool (`audio_present == True` AND
`is_on_spot == False` AND `order_closure_datetime_ist` blank); every
section now shares the same corpus.

**Booked box exception** (`booked_box_pool`): its own pool is
`order_closure_datetime_ist` NOT blank AND `total_followup_done > 0` —
ignores the base pool filter entirely. Mutually exclusive with `funnel_pool`
by construction (one requires the column blank, the other requires it
filled).

Boxes = one per distinct `last_follow_up_status` value present in the
base pool (dynamically discovered, not a fixed list), plus the fixed
Booked box. Display label: `another_follow_up_required` → "Another Follow
up needed"; `dnp` → "DNP"; anything else → title-cased raw value (e.g.
`dropped_plan` → "Dropped Plan"). A raw status of `'booked'`, if it ever
appeared inside `funnel_pool`, would be folded into the dedicated Booked
box rather than creating a second box with the same label — in practice
this shouldn't happen, since a booked lead normally has
`order_closure_datetime_ist` filled in.

### Another Follow up needed (`another_follow_up_required`)

**When / Leads / Share** cut, by `next_follow_up_date − as_of`: Overdue
(`<0`), Due Today (`==0`), 1-3 days (`1–3`), 4-7 days (open-ended, `>3`
days out — not capped at 7, it's "4 or more" so every parseable lead
lands somewhere).

TL/SC columns: **Leads, Overdue, Due today, Upcoming** (1-3 + 4-7
combined), **Avg days out**.

Lead columns (`another_fu_lead_table`): **Lead ID, City, Priority,
Scheduled** (`next_follow_up_date`), **Days from Today** (signed, e.g.
"+3 days"), **Last FU** (`last_follow_up_date`). Sorted most-overdue-first
(no Category column here, unlike Due Today/Overdue/Audio Index). **City =
`cluster`** (the same column the sidebar's own city filter uses — see §1).
**[FIX]** — City used to read a `subset.get("city")` column that doesn't
exist in the CSV, so it was always blank.

### DNP (`dnp`)

**Consecutive DNPs / Leads / Share**: rows 1/2/3 only, counted by
`tl_is_dnp_tasks == n`. Despite the label, `tl_is_dnp_tasks` is a
**lifetime count** of TL/IS-owned DNP tasks (from the SQL:
`count(*) FILTER (WHERE outcome='dnp' AND owner_role IN ('TL','IS'))`),
not a count of literally consecutive DNPs — see Known issues.

TL/SC columns: **DNP leads** (count), **3+ in a row**
(`tl_is_dnp_tasks > 3`), **Avg DNPs** (mean `tl_is_dnp_tasks`).

Lead columns (`dnp_lead_table`): **Lead ID, City, Priority, Consecutive
DNPs** (`tl_is_dnp_tasks`), **Last FU** (`last_follow_up_date`), **Due**
(`next_follow_up_date`). Sorted by Consecutive DNPs descending. **City =
`cluster`**, same fix as Another Follow up's lead table.

### No action possible / Will go later / Lost to Competitor / Booked

Shared generic template (`funnel_generic_breakdown`) — **TL, Leads, Share,
Avg FUs** (mean `total_followup_done`), **Avg days to last FU** (mean of
`as_of − last_follow_up_date`), **P1+P2** (count, priority P1 or P2 —
independent, unrelated to the Others-convention discussion elsewhere;
this box has no "Others"/"Agreed" concept at all). Pan-India view groups
by `cluster` instead of `tl`.

### Agreed to Meet (`agreed_to_meet`)

No drill-down. 3 numbers by `next_follow_up_date` vs `as_of`: **Already
slipped** (`<0`), **Due today** (`==0`), **Still ahead** (`>0`).

### TL-wise cut (fixed 7 boxes, no dynamic discovery)

Groups: **Still in play** = another_follow_up_required + dnp +
will_go_later + agreed_to_meet. **Closed out** = no_action_possible +
lost_to_competitor + booked. Any OTHER status value present in the data is
excluded from this cut entirely (unlike the Overall cut above, which is
fully dynamic and shows every status that exists).

Per TL/SC row: `followed_up_once` (count with `total_followup_done > 0`,
over the union of that group's leads across all 7 boxes), each box's
count + its % of the 7-box grand total (**rounded to whole percent**, not
1 decimal — an intentional exception to the rest of the dashboard).

---

## 8. Data source & dates

- **The dashboard is static: it only ever shows `followup_dashboard.csv`.**
  There is no live Metabase pull, no date pickers and no background
  refresh. `load_data` (`@st.cache_data`) reads the file once per server
  process and every session shares that one cached DataFrame; the city /
  Pan-India nav is a pure in-memory pandas filter on it. To change the
  numbers, regenerate the CSV (`data_pull.py`), commit, and push — the
  redeploy restarts the process and clears the cache.
- **One "today" = the data's end date, never the server clock.** It is the
  CSV's `snapshot_date`. Every section that says "today" (Due Today's
  whole pool, Overdue's `funnel_bucket`, the funnel's "Days from Today",
  every lead table's Due column) uses this `as_of` value. The server clock
  is not used anywhere.
- **`fu_due_date_today` / `fu_completedat_today` are recomputed in Python**
  (`data_pull.apply_end_date_flags`), replacing the SQL's original
  `now()`-based values.
  - `fu_completedat_today` = 1 if ANY of FU1..FU6's completion date equals
    `as_of`. A lead's 7th+ follow-up is never looked at (same limit as the
    SQL). The SQL card itself now also computes this against `{{end_date}}`
    directly (no longer `now()`), so this recomputation is belt-and-braces
    here, not load-bearing — kept so the dashboard doesn't depend on the
    Metabase card always being current.
  - `fu_due_date_today` = 1 if ANY of FU1..FU6's due date equals `as_of`,
    **OR `followup_due_date == as_of`**. **[FIX]** — this used to check
    only FU1..FU6, same limit as `fu_completedat_today`; but a lead with
    zero real follow-up tasks (`followup_tasks == 0`) has no FU1..FU6 due
    date at all, so it could never be flagged as due today regardless of
    which end date was picked — even though `followup_due_date` (that
    lead's own fallback to `first_meeting_done_date + 2`) clearly places
    it there. Confirmed on the current data: **425 leads** (out of 18,945)
    were being silently excluded from Due Today this way; the corrected
    rule picks all of them up (164 → 589 dashboard-wide, whole-CSV count).
    Mirrors the same OR-condition added to the SQL card's own
    `fu_due_date_today` (see `followup_dashboard_query.sql`'s `fu_pivot`
    CTE) — the SQL and Python sides now agree.
  - Because of this, `followup_due_date` and `fu_due_date_today` agree far
    more often now than before, but still aren't identical: a lead deep
    into its follow-up history (past FU6) whose *current* open task
    happens to be due today is still only caught via the
    `followup_due_date` term above, not via any FU-numbered column — see
    §4's Due column note for the general gap this leaves.
- **`cohort_from` is fixed at 2026-07-31** and is baked into the CSV by
  the pull that produced it; it is not a user-facing filter (there are
  no date filters in the dashboard).

---

## 9. Shared conventions

- `AGREED_OUTCOMES = {agreed_to_meet, another_follow_up_required}`;
  `HIGH_PRIORITY = {P1, P2}` (priority compared case-insensitively,
  uppercased).
- **"Others" means an independent condition (~Agreed+Another AND
  ~P1+P2), never a subtraction**, everywhere it appears with an
  Agreed+Another/P1+P2 pair (Due Today's cards and TL/SC table, Overdue's
  cards and TL/SC table). This was iterated on more than once during
  development (see the [FIX] notes in §4/§5) — if a future change ever
  reintroduces a residual-subtraction formula for Others, that's a
  regression, not a simplification: it silently double-subtracts leads
  that are in both Agreed+Another and P1+P2, undercounting Others.
- "(unattributed)" is the fallback label whenever `tl`/`sc` is blank in a
  group-by.
- Every % elsewhere in the dashboard (not called out above) rounds to 1
  decimal; the Follow-up funnel's TL-wise cut is the one exception (whole
  percent).
- A group-by sum of an independent boolean condition (e.g. Agreed+Another,
  P1+P2, Others, or the Overview buckets) always ties back exactly to the
  same condition's total over the whole population, because `group_col`
  (`tl`, `sc`, or `cluster`) always partitions the rows it's grouping —
  every lead has exactly one value of each. This is *why* the TL/SC and
  Pan-India city tables can be trusted to reconcile with the summary
  cards above them, and it's the reasoning to reach for whenever adding a
  new drill-down and wanting it to reconcile automatically rather than
  needing a special-case check.

---

## 10. Known issues — found, not yet fixed

These were found during development/review conversations but the person
running this project chose not to fix them yet (either by explicit choice,
or because the question hasn't been asked). Don't silently "fix" any of
these without checking first — some may be intentional trade-offs that
just haven't been explained here yet.

1. **DNP's "Consecutive DNPs" table only has rows for exactly 1, 2, 3.**
   Leads with `tl_is_dnp_tasks == 0` or `>= 4` never appear in that
   specific table (though they DO count in the TL/SC breakdown's "DNP
   leads" and "3+ in a row" columns). Also, despite the name,
   `tl_is_dnp_tasks` is a lifetime count of DNP tasks, not a count of
   literally *consecutive* DNPs — see §7.
2. **`config.toml` has a live Metabase API key in plaintext.** Rotate it
   if this file is ever shared or committed to a public/shared repo;
   prefer the `MB_API_KEY` environment variable for anything beyond local
   development (already supported by `data_pull.load_config`).
3. **The SQL's city allow-list is hardcoded** (`followup_dashboard_query.sql`,
   the big `lower(trim(l.site_address_city)) IN (...)` clause). A new city
   won't appear anywhere in the dashboard until that list is edited in the
   Metabase card itself and the card is re-saved.
4. **`followup_state` (referenced by a `LEFT JOIN` in the SQL) isn't
   defined anywhere in this repo.** It's an external database table (not
   a CTE in the same file) that supplies `last_follow_up_status`,
   `last_follow_up_date`, and `exit_reason` — most of the Follow-up funnel
   and Funnel Quality sections ultimately trace back to it. If numbers
   there ever look wrong and nothing in `metrics.py`/`data_pull.py`
   explains it, the cause is probably upstream in that table or its own
   join logic, not in this codebase.

---

## 11. How this project has been worked on

Notes on working conventions established during development, useful if
you're continuing this project rather than starting fresh:

- **No code change without the person's explicit go-ahead.** Business
  logic here reflects real reporting decisions (thresholds, which pool a
  section draws from, how "Others" is defined) — these are calls only the
  person running the dashboard can make, not something to infer from
  "what looks more correct" in isolation.
- **When an instruction's literal reading would break something (e.g.
  produce an always-empty table, or a mathematically impossible result),
  say so and ask rather than silently picking an interpretation.** This
  came up more than once — e.g. a proposed date-equality filter that
  would have zeroed out the Due Today table entirely, or an ambiguous
  "Meeting done" description that could have meant either a column swap
  or a population filter. Test the literal reading against the real data
  before implementing, and surface the result plainly (numbers, not just
  assertions) so the person can course-correct with actual context in
  front of them.
- **Verify every logic change against the real CSV data before considering
  it done** — not just "does it run," but concrete numbers: does a
  TL/SC column's sum tie back to its summary card, does a `sum-to-total`
  or `independent-condition` claim actually hold across several cities,
  etc. A full headless Streamlit render (`streamlit.testing.v1.AppTest`,
  both Pan-India and at least one City view) catches rendering exceptions
  that pure-function tests miss (a `render_accordion_drill` call
  expecting a column that got renamed, a `column_config` key that no
  longer matches a display label after a label rename, etc.).
- **Keep docstrings honest and current.** This file's `**[FIX]**`
  convention exists in `metrics.py` itself too — when a formula changes,
  the function's docstring is updated in the same edit, explaining what
  it used to do and why that was wrong. Don't leave a stale docstring
  next to corrected code; a stale docstring is worse than no docstring,
  because it actively misleads the next reader (this happened at least
  twice in this project's history — a stale `× 12` divisor, and a stale
  "60 day" Nurture rule left in a comment after the code was already
  fixed).
- **Remove dead code when a change makes it unreachable** (e.g.
  `due_today_categorize` and its supporting `_CATEGORY_COLS` constant were
  deleted once nothing called them any more, rather than left to rot).
- **CRLF line endings** are used consistently across this repo's `.py`
  files (a Windows-developed project) — a Python-based file rewrite tool
  can silently normalise these to LF; check with a quick byte-level
  comparison after any large rewrite and restore CRLF if it drifted.
- **`AskUserQuestion`-style confirmation is worth it before a change with
  real behavioral consequences**, especially when the person's own
  wording is ambiguous between two structurally different
  implementations (see the "Meeting done" example above). A quick,
  concrete test against the real data — "here's what each interpretation
  would actually produce" — makes that confirmation fast and unambiguous
  rather than a vague back-and-forth.
