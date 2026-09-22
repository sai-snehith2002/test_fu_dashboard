# FollowUp Dashboard — Business Logic Reference

Authoritative spec for `metrics.py` (pure pandas, no Streamlit). This
supersedes any older written spec — several definitions below were
corrected mid-project; deltas from the original spec are marked **[FIX]**.
`app.py` only renders; it has no logic of its own.

## Global filters (apply to the WHOLE dashboard, not just Card 1)

1. `cluster` == selected city (case-insensitive).
2. `sc_channel` == "Field Sale SC" (case-insensitive). **[FIX]** — this is
   applied once, globally, before any section-specific logic. Every
   section (Overview, Due Today, Overdue, Audio Index, Funnel, Funnel
   Quality) only ever sees Field Sale SC rows.

All section pools below start from this already-filtered `city_df`.

---

## 1. Overview (Card 1)

Computed by classifying every row into exactly ONE bucket, by priority
(this priority order is itself the **[FIX]**: the four raw conditions
overlap, so the buckets are resolved first-match-wins to make the
subtraction identity below exact rather than approximate):

1. **Closed on spot** — `is_on_spot == True`
2. **Closed on Follow-Up** — (not bucket 1) AND `is_on_spot == False` AND
   `order_closure_datetime_ist` NOT blank
3. **No audio notes** — (not 1/2) AND `notes_submitted_in_range == 0` AND
   `order_closure_datetime_ist` blank
4. **Eligible for follow-ups** — everything else (not 1/2/3)

- **Meetings done** = count of all rows (post global filters).
- Identity: `Eligible = Meetings done − Closed on Follow-Up − No audio notes − Closed on spot` (exact, by construction).
- **Displays 5 numbers**: Meetings done, Closed on spot, Closed on
  Follow-Up, No audio notes, Eligible for follow-ups. **[FIX]** — earlier
  spec only showed 4 (omitted Closed on Follow-Up as its own tile).
- Every metric except Meetings done shows a small "% of Meetings done"
  caption below it (all 4, including Closed on Follow-Up — **[FIX]**,
  earlier spec only put % under 3 of them).
- City breakdown table (Pan-India view): same 5 counts, one row per city.

---

## 2. Due Today

Base pool = `came_due_pool`: rows where `fu_due_date_today == 1`.
(Note: NOT a union with `fu_completedat_today==1` — a lead with
`fu_completedat_today=1` but `fu_due_date_today=0` is excluded from this
section entirely.)

### Summary boxes — **[FIX, full rewrite]**

Old spec had single completion fractions; current is a **Due / Worked /
Booked** triple in every box, all computed on that box's own subset of
`came_due_pool`:
- **Due** = count in that subset (already `fu_due_date_today==1` for all).
- **Worked** = of Due, `fu_completedat_today == 1`.
- **Booked** = of Worked, `order_closure_datetime_ist` NOT blank.

| Box | Subset | Displayed |
|---|---|---|
| Due Today / Worked / Booked | whole `came_due_pool` | Due/Worked/Booked + Worked÷Due % |
| Agreed to Meet + Another follow-up | `last_follow_up_status` in {agreed_to_meet, another_follow_up_required} | same 3 numbers + % |
| P1+P2 | `priority` in {P1, P2} | same 3 numbers + % |
| Others | = top box's (Due,Worked,Booked) **minus** Agreed+Another's (Due,Worked,Booked), position-by-position | same 3 numbers + % |

Boxes are **independent counts** (a lead can be in both Agreed+Another
and P1+P2) — Others is only ever net of Agreed+Another, never also net
of P1+P2. The % under each box = that box's own Worked ÷ Due.

Also shown: pending count (`Due − Worked` for the whole pool) and the TL
with the highest pending count.

### TL → SC → Lead drill-down

TL/SC view columns: **Came due, Worked, Pending, %** (Worked÷Came due),
plus a 3-way category split — **Agreed to Meet + Another follow-up /
P1+P2 / Others**. Here each lead is bucketed into exactly ONE category by
precedence (Agreed/Another > P1/P2 > Others) — **this is a different
arithmetic convention than the summary boxes above** (which are
independent, overlapping counts). The TL/SC table's "Others" is a true
partition remainder; the summary card's "Others" is a subtraction that
can disagree slightly if a lead is both P1/P2 and Agreed+Another.

Lead-level columns: **Lead ID, Category, Meeting done, Priority, Audio,
Missing, FUs done, Last FU, Due, State**.
- Category = the lead's own raw `last_follow_up_status` if it's
  agreed_to_meet/another_follow_up_required; else "P1+P2" if priority is
  P1/P2; else "Others".
- Meeting done = `last_follow_up_date`. Priority = `priority`. Audio =
  `audio_duration_sec`. Missing = `dispositions_missing`. FUs done =
  `total_followup_done`. Last FU = `latest_status`. Due = today's date
  (literal string). State = constant `"Due Today"`.
- **Sort order [FIX]**: agreed_to_meet, then another_follow_up_required,
  then P1+P2, then everything else (Others). (Earlier spec had
  another_follow_up_required before agreed_to_meet, and no explicit
  P1+P2 slot.)

---

## 3. Overdue

Base pool = rows where `funnel_bucket == 'overdue'`.
Denominator pool (for the headline %) = rows where `funnel_bucket` is
**not** in {booked, terminal, lost, later, customer_unreachable} — i.e.
overdue + task_gap + today + tomorrow + this_week.

- **Overdue %** = overdue count ÷ denominator count.
- Also shown: TL with the most overdue leads (plain count).
- **Ageing split** (over the overdue pool, by `overdue_days`): Under 3
  days (`<3`), 3-5 days (`3 ≤ x ≤ 5`), Over 5 days (`>5`).
- **Agreed to Meet + Another follow-up / P1+P2 / Others**: same
  independent-count convention as Due Today's summary boxes (Others =
  total overdue − P1+P2 − Agreed+Another; NOT mutually exclusive).

### TL → SC → Lead drill-down

TL/SC columns: **Overdue, Overdue %** (this group's own overdue ÷ this
group's own denominator — same ratio, just scoped), the **Agreed+Another
/ P1+P2 / Others** partition (precedence-bucketed, same caveat as Due
Today's TL/SC table), and 3 more columns under an "Ageing" header:
**Under 3 days, 3-5 days, Over 5 days**.

Lead-level columns: **Lead ID, Category, Meeting done, Priority, Audio,
Missing, FUs done, Last FU, Due, Ageing**. Same Category logic as Due
Today. Due here = the lead's own `followup_due_date` (NOT today's date —
different from Due Today's Lead table). Ageing = `overdue_days` rendered
as text, e.g. "8 days".
- **Sort order [FIX]**: same as Due Today — agreed_to_meet, then
  another_follow_up_required, then P1+P2, then Others.

---

## 4. Audio Index

Base pool = **`eligible_for_followups_pool`** — i.e. the SAME "Eligible
for follow-ups" bucket as Overview's 4th bucket (Meetings done minus
Closed-on-spot, Closed-on-Follow-Up, No-audio-notes). **[FIX, major]** —
this used to be its own separately-defined pool
(`audio_present==True AND is_on_spot==False`); it now shares Overview's
corrected pool so the two sections never disagree.

- **Coverage %** = count(eligible pool) ÷ count(`is_on_spot == False`) × 100.
  (Denominator unchanged from the original spec; only the numerator's
  pool definition changed.)
- **Completeness %** = `(pool_count × 11 − Σ dispositions_missing over pool) / (pool_count × 11) × 100`.
  **[FIX]** — divisor is **11**, not 12: `Remarks` is deliberately
  excluded from the 12 disposition questions (it's free-text, not a
  structured disposition), so only 11 columns count toward missing/total.
- **Audio Index** = `(Coverage % × Completeness %) / 1000`, clipped to
  [0, 10], rounded to 1 decimal. Shown as "`X.X / 10`".
- "From Meeting's Today" toggle: when ON, restricts everything in this
  section to `fu_completedat_today == 1` first, then the eligible pool is
  recomputed on that narrowed set. OFF = no restriction.
- Pan-India city breakdown table: same 3 numbers per city.

### Split (TL → SC → Lead)

TL/SC columns: **TL/SC name, Index, Leads, % leads with a disposition
missing, Dispositions missing (average per lead)**.
- **Index** = same formula as the headline Audio Index, computed for
  that TL/SC's own slice: needs BOTH the wider "Meeting Done" population
  (for the Coverage % denominator, `is_on_spot==False`, not restricted to
  the eligible pool) AND the eligible-pool slice (for the numerator and
  Completeness %).
- **Leads** = count of eligible-pool rows under that TL/SC.
- **% leads with a disposition missing** = share of those leads with
  `dispositions_missing > 0`.
- **Dispositions missing (average per lead)** = mean `dispositions_missing`
  over that TL/SC's leads.

Lead-level columns: **Lead ID, Category, Meeting done, Priority, Audio
Duration (in sec), Missing, FUs done, Last FU, Due**. Same field mapping
as Due Today's lead table (Category/Priority/Audio/Missing/FUs
done/Last FU), Due = `followup_due_date`.
- **Sort order [FIX]**: now the same as Due Today/Overdue — agreed_to_meet,
  then another_follow_up_required, then P1+P2, then Others (previously
  used a different, narrower order: another_follow_up_required then
  agreed_to_meet only).

### Disposition Breakdown

One row per disposition question — **[FIX]**: only **11** questions,
`Remarks` is excluded entirely (not fetched, not scored):
Quoted Price, Offering, Buffer/Margin Left, SC's Reading, Site Visit
Done?, Bill Value, Competitor Quote, Next Follow up (Voice Note),
Timeline to go Solar, Decision Maker, Main Objection.

Columns: **Leads** (count of eligible-pool rows blank for that
disposition), **Share** (÷ total eligible pool, %), **Of those, <45s**
(of the blank ones, how many have `audio_duration_sec < 45`).

---

## 5. Follow-up funnel

Base pool (`funnel_pool`, applies to every box except Booked):
`audio_present == True` AND `is_on_spot == False` AND
`order_closure_datetime_ist` blank.

**Booked box exception**: its own pool is `order_closure_datetime_ist`
NOT blank AND `total_followup_done > 0` — ignores the base pool filter
entirely.

Boxes = one per distinct `last_follow_up_status` value present in the
base pool, plus the fixed Booked box. Display label: `another_follow_up_required` → "Another Follow up needed"; `dnp` → "DNP"; anything
else → title-cased raw value.

### Another Follow up needed (`another_follow_up_required`)

**When / Leads / Share** cut, by `next_follow_up_date − as_of`:
Overdue (`<0`), Due Today (`==0`), 1-3 days (`1–3`), 4-7 days
(**open-ended, `>3` days out** — **[FIX/clarify]**: not capped at 7, it's
"4 or more" so every parseable lead lands somewhere).

TL/SC columns: **Leads, Overdue, Due today, Upcoming** (1-3 + 4-7
combined), **Avg days out**.

Lead columns: **Lead ID, City, Priority, Scheduled** (`next_follow_up_date`), **Days from Today** (signed, e.g. "+3 days"), **Last FU**
(`last_follow_up_date`). Sorted most-overdue-first.

### DNP (`dnp`)

**Consecutive DNPs / Leads / Share**: rows 1/2/3, counted by
`tl_is_dnp_tasks == n`.

TL/SC columns: **DNP leads** (count), **3+ in a row**
(`tl_is_dnp_tasks > 3`), **Avg DNPs** (mean `tl_is_dnp_tasks`).

Lead columns: **Lead ID, City, Priority, Consecutive DNPs**
(`tl_is_dnp_tasks`), **Last FU** (`last_follow_up_date`), **Due**
(`next_follow_up_date`). Sorted by Consecutive DNPs descending.

### No action possible / Will go later / Lost to Competitor / Booked

Shared generic template — **TL, Leads, Share, Avg FUs** (mean
`total_followup_done`), **Avg days to last FU** (mean of
`as_of − last_follow_up_date`), **P1+P2** (count, priority P1 or P2).
(Pan-India view groups by `cluster` instead of `tl`.)

### Agreed to Meet (`agreed_to_meet`)

No drill-down. 3 numbers by `next_follow_up_date` vs `as_of`: **Already
slipped** (`<0`), **Due today** (`==0`), **Still ahead** (`>0`).

### TL-wise cut (fixed 7 boxes, no dynamic discovery)

Groups: **Still in play** = another_follow_up_required + dnp +
will_go_later + agreed_to_meet. **Closed out** = no_action_possible +
lost_to_competitor + booked. Any OTHER status value in the data is
excluded from this cut entirely (unlike the Overall cut, which is fully
dynamic).

Per TL/SC row: `followed_up_once` (count with `total_followup_done > 0`,
over the union of that group's leads across all 7 boxes), each box's
count + its % of the 7-box grand total (**rounded to whole percent**, not
1 decimal — an intentional exception to the rest of the dashboard).

---

## 6. Funnel Quality

Base pool = same as Follow-up funnel's base pool (no Booked carve-out —
this section has no Booked box).

Three categories, each with its own threshold and denominator:

| Key | Condition | Threshold | Denominator |
|---|---|---|---|
| Did not pick up (dnp) | `last_follow_up_status == 'dnp'` | 25% | whole base pool |
| Did not pick up + Lost + Nurture | dnp OR lost_to_competitor OR **Nurture** | 45% | whole base pool |
| Another follow-up dated beyond 5 days | status in {agreed_to_meet, another_follow_up_required} AND `(next_follow_up_date − last_follow_up_date) > 5` | 50% | only leads with status in {agreed_to_meet, another_follow_up_required} |

**Nurture** = `last_follow_up_status` in {will_go_later, dropped_plan,
not_qualified_not_serviceable} -- unconditional, no day-count on
next_follow_up_date. **[FIX]** -- will_go_later used to only count past 60
days out from last_follow_up_date; every will_go_later lead counts now,
same as the other two statuses. **[FIX]** -- not_qualified_not_serviceable
was previously misspelled "servicable" in the code (missing the "e"),
which never matched the real data value, so those leads were silently
never counted as Nurture.

Per-category row: Measure, Now % (matched ÷ denominator), Threshold %
(hardcoded above), Basis (denominator's label), State = **"Breaching"**
if Now % is strictly greater than Threshold, else **"Within"** (exactly
on threshold counts as Within).

Per-category SC expander: **SC, Leads** (total pool count for that SC),
**[category's matched-count column]**, plus **DNP%** — **[addition, DNP
category only]**: `matched ÷ leads × 100`, 2 decimals with `%`. This 4th
column only appears on the "Did not pick up" category's table, not the
other two.

TL-wise cut (no SC drill): **TL, With an outcome** (count of non-blank
`last_follow_up_status`), then each category's % — DNP and DNP+Lost+Nurture
are % of that TL's total pool; Beyond-5-days is % of that TL's
agreed_to_meet/another_follow_up_required leads only. A 0-denominator
category renders as a dash, not 0%.

---

## Shared conventions

- `AGREED_OUTCOMES = {agreed_to_meet, another_follow_up_required}`;
  `HIGH_PRIORITY = {P1, P2}` (priority compared case-insensitively,
  uppercased).
- "(unattributed)" is the fallback label whenever `tl`/`sc` is blank in a
  group-by.
- Every % elsewhere in the dashboard (not called out above) rounds to 1
  decimal; the Follow-up funnel's TL-wise cut is the one exception
  (whole percent).

---

## 7. Data source & dates

- **One "today" = the data's end date**, i.e. the CSV's `snapshot_date`. Every
  section that says "today" (Due Today, Overdue's `funnel_bucket`, the
  funnel's "Days from Today", the lead tables' Due column) uses it. The server
  clock is never used.
- **`fu_due_date_today` / `fu_completedat_today` are recomputed in Python**
  (`data_pull.apply_end_date_flags`), replacing the SQL's `now()`-based
  values: 1 if ANY of FU1..FU6's due date / completed date equals the end
  date. Same rule as the SQL, including its FU1..FU6 limit (a 7th+ follow-up
  is not looked at).
- **Loading:** the dashboard reads `followup_dashboard.csv` straight off disk
  on load (no live fetch, no background pull). Refreshing the data means
  re-running `data_pull.py` to overwrite that CSV.