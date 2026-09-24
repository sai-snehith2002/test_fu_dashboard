"""
metrics.py
==========
Pure data-crunching functions for the FollowUp Streamlit dashboard.

Deliberately has no Streamlit import: every function here takes a
DataFrame and returns a DataFrame/dict, so it can be unit-tested with
plain pandas and reused if the UI layer changes later. app.py is the
only place that touches st.*.
"""
from __future__ import annotations

import pandas as pd

AGREED_OUTCOMES = {"agreed_to_meet", "another_follow_up_required"}
HIGH_PRIORITY = {"P1", "P2"}


# ---------------------------------------------------------------------------
# Top-level filters
# ---------------------------------------------------------------------------

def filter_cluster(df: pd.DataFrame, cluster_name: str) -> pd.DataFrame:
    """Case-insensitive match on the `cluster` column."""
    if "cluster" not in df.columns:
        raise KeyError("Expected a 'cluster' column in the data.")
    mask = df["cluster"].astype(str).str.strip().str.lower() == cluster_name.strip().lower()
    return df[mask].copy()


def filter_sc_channel(df: pd.DataFrame, channel_name: str = "Field Sale SC") -> pd.DataFrame:
    """
    Case-insensitive match on the `sc_channel` column. Applied at the top
    level (alongside the cluster filter) so the whole dashboard -- Card 1,
    Due Today, Overdue, and everything after -- only ever sees Field Sale
    SC records, not just the Card 1 "Meetings done" count.
    """
    if "sc_channel" not in df.columns:
        raise KeyError("Expected an 'sc_channel' column in the data.")
    mask = df["sc_channel"].astype(str).str.strip().str.lower() == channel_name.strip().lower()
    return df[mask].copy()


# ---------------------------------------------------------------------------
# Card 1 — City snapshot
# ---------------------------------------------------------------------------

def _bool_series(df: pd.DataFrame, col: str) -> pd.Series:
    """Nullable-boolean-safe: missing/NA treated as False."""
    s = df.get(col)
    if s is None:
        return pd.Series(False, index=df.index)
    return s.fillna(False).astype(bool)


def _followup_buckets(df: pd.DataFrame) -> dict:
    """
    Classifies every row of `df` into exactly one of four mutually
    exclusive buckets, restricted to the "Meetings done" population
    (sc_channel == "Field Sale SC"). Priority order (first match wins):

      1. Closed on spot          -- is_on_spot == True
      2. Closed on Follow-Up     -- is_on_spot == False AND
                                     order_closure_datetime_ist is NOT blank
      3. No audio notes          -- (of what's left) audio_present is NOT
                                     true (False or missing) AND
                                     order_closure_datetime_ist is blank
      4. Eligible for follow-ups -- everything else in Meetings done

    **[FIX]** -- bucket 3 used to be notes_submitted_in_range == 0, which a
    blank/missing value never satisfies (pandas' `==` with NaN is always
    False). A lead with no meeting_metrics_history row inside the pull's
    [start_date, end_date] -- i.e. no fresh meeting in range at all -- has
    BOTH notes_submitted_in_range and audio_present missing, so the old
    condition silently fell through to bucket 4 (Eligible) instead of
    bucket 3, inflating Eligible (and, downstream, Audio Index's Coverage %
    and Overdue's whole corpus, since both now share this pool) with leads
    that were never actually assessed for audio in this pull. Checking
    audio_present directly (treating missing the same as False, via
    _bool_series) catches those leads correctly.

    This priority ordering exists because the four conditions, read
    independently, are NOT mutually exclusive: a record with
    is_on_spot == True, audio_present not true, and
    order_closure_datetime_ist blank would satisfy both "Closed on spot"
    and "No audio notes" at the same time. Classifying by priority
    instead guarantees the four buckets partition Meetings done exactly,
    so "Eligible for follow-ups == Meetings done - Closed on Follow-Up -
    No audio Notes - Closed on spot" holds as an exact identity rather
    than an approximation that can double-count overlapping rows.

    Returns a dict of boolean masks aligned to df.index: "meetings_done",
    "closed_on_spot", "closed_on_followup", "no_audio_notes", "eligible".
    """
    sc_channel = df.get("sc_channel", pd.Series(dtype=str)).astype(str).str.strip().str.lower()
    meetings_done = sc_channel == "field sale sc"

    on_spot = _bool_series(df, "is_on_spot")
    order_closure_blank = (
        df.get("order_closure_datetime_ist", pd.Series("", index=df.index))
        .astype(str).str.strip() == ""
    )
    audio_present = _bool_series(df, "audio_present")  # missing treated as False, like every other bool column here

    closed_on_spot = meetings_done & on_spot
    closed_on_followup = meetings_done & ~on_spot & ~order_closure_blank
    no_audio_notes = meetings_done & ~on_spot & order_closure_blank & ~audio_present
    eligible = meetings_done & ~closed_on_spot & ~closed_on_followup & ~no_audio_notes

    return {
        "meetings_done": meetings_done,
        "closed_on_spot": closed_on_spot,
        "closed_on_followup": closed_on_followup,
        "no_audio_notes": no_audio_notes,
        "eligible": eligible,
    }


def eligible_for_followups_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    Leads eligible for follow-ups under the corrected definition: within
    Meetings done, not closed on spot, not closed via a later follow-up,
    and not missing audio notes -- see _followup_buckets for the exact
    priority-based classification that keeps this an exact partition of
    Meetings done. Shared by Card 1's count and the Audio Index section,
    which now uses this exact pool as its base population (previously
    Audio Index used its own, separately-defined audio_present/is_on_spot
    pool -- see audio_pool()).
    """
    b = _followup_buckets(df)
    return df[b["eligible"]].copy()


def card1_metrics(df: pd.DataFrame) -> dict:
    """
    City snapshot Card 1, computed on the cluster-filtered DataFrame.
    All five numbers are computed within the "Meetings done" population
    (sc_channel == "Field Sale SC") and partition it exactly -- see
    _followup_buckets for the priority order that makes this an exact
    partition rather than independently-overlapping counts.

    - Meetings done: sc_channel == "Field Sale SC" (case-insensitive)
    - Closed on spot: is_on_spot == True
    - Closed on Follow-Up: is_on_spot == False AND order_closure_datetime_ist is not blank
    - No audio notes: (of the rest) audio_present is NOT true (False or missing) AND order_closure_datetime_ist blank
    - Eligible for follow-ups: Meetings done - Closed on Follow-Up - No audio Notes - Closed on spot
      (i.e. everything left in Meetings done after the three buckets above)
    """
    b = _followup_buckets(df)
    meetings_done = int(b["meetings_done"].sum())
    closed_on_spot = int(b["closed_on_spot"].sum())
    closed_on_followup = int(b["closed_on_followup"].sum())
    no_audio_notes = int(b["no_audio_notes"].sum())
    eligible_for_followups = int(b["eligible"].sum())

    def _pct(n: int) -> float:
        return (n / meetings_done * 100) if meetings_done else 0.0

    return {
        "meetings_done": meetings_done,
        "closed_on_spot": closed_on_spot,
        "closed_on_followup": closed_on_followup,
        "no_audio_notes": no_audio_notes,
        "eligible_for_followups": eligible_for_followups,
        "closed_on_spot_pct": _pct(closed_on_spot),
        "closed_on_followup_pct": _pct(closed_on_followup),
        "no_audio_notes_pct": _pct(no_audio_notes),
        "eligible_for_followups_pct": _pct(eligible_for_followups),
    }


def _known_cities(df: pd.DataFrame) -> list[str]:
    """Distinct, alphabetically-sorted `cluster` values present in df -- same convention as the sidebar's city button list."""
    cluster = df.get("cluster", pd.Series(dtype=str)).astype(str).str.strip()
    return sorted({c for c in cluster if c and c.lower() != "nan"})


def card1_metrics_by_city(df: pd.DataFrame) -> pd.DataFrame:
    """
    Card 1's five counts, broken down one row per city (the `cluster`
    column) -- used for the Pan-India Overview section's City breakdown
    table. Same definitions as card1_metrics, just computed per city.
    """
    rows = []
    cluster = df.get("cluster", pd.Series(dtype=str)).astype(str).str.strip()
    for city in _known_cities(df):
        sub = df[cluster == city]
        m = card1_metrics(sub)
        rows.append({
            "City": city,
            "meetings_done": m["meetings_done"],
            "closed_on_spot": m["closed_on_spot"],
            "closed_on_followup": m["closed_on_followup"],
            "no_audio_notes": m["no_audio_notes"],
            "eligible_for_followups": m["eligible_for_followups"],
        })
    return pd.DataFrame(
        rows, columns=["City", "meetings_done", "closed_on_spot", "closed_on_followup",
                       "no_audio_notes", "eligible_for_followups"]
    )


# ---------------------------------------------------------------------------
# Due Today
# ---------------------------------------------------------------------------

def _is_one(series: pd.Series | None, index) -> pd.Series:
    if series is None:
        return pd.Series(False, index=index)
    return pd.to_numeric(series, errors="coerce").fillna(0) == 1


def due_today_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Records where fu_completedat_today==1 OR fu_due_date_today==1."""
    completed = _is_one(df.get("fu_completedat_today"), df.index)
    came_due = _is_one(df.get("fu_due_date_today"), df.index)
    return df[completed | came_due].copy()


def due_today_corpus_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Due Today section's overall lead corpus: the same "Eligible for
    follow-ups" pool as Overview's Card 1 / Audio Index / Overdue -- see
    eligible_for_followups_pool() / _followup_buckets(). **[FIX, major]**
    -- this used to be the raw cluster-filtered data with no Eligible
    restriction at all (came_due_pool was applied directly to city_df); it
    now shares the exact same eligible-for-follow-ups pool the other three
    sections use, so a TL/SC's roster here matches theirs too.
    """
    return eligible_for_followups_pool(df)


def came_due_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    Records where fu_due_date_today==1 -- the base for every Due Today
    card. `df` should already be due_today_corpus_pool(city_df) (or a
    subset of it) wherever the corpus restriction matters -- see
    due_today_corpus_pool's docstring.
    """
    came_due = _is_one(df.get("fu_due_date_today"), df.index)
    return df[came_due].copy()


def category_display(row) -> str:
    """
    Per-lead Category value for the lead-level drill-down tables: a
    mutually-exclusive bucket by precedence -- Agreed/Another > P1/P2 >
    Others -- so every lead in a table gets exactly one Category to sort
    and read by. The Agreed/Another branch shows the lead's own raw status
    value (e.g. "agreed_to_meet" or "another_follow_up_required") rather
    than a generic label; P1+P2 and Others are plain labels.

    This precedence bucketing is deliberately NOT used for the TL/SC
    breakdown tables' own Agreed+Another/P1+P2/Others columns -- those use
    independent counts instead, matching the summary cards (see Due
    Today's breakdown_by / Overdue's overdue_breakdown_by).
    """
    status_raw = str(row.get("last_follow_up_status") or "").strip()
    if status_raw.lower() in AGREED_OUTCOMES:
        return status_raw
    priority = str(row.get("priority") or "").strip().upper()
    if priority in HIGH_PRIORITY:
        return "P1+P2"
    return "Others"


# Lead-level Category sort order: another_follow_up_required first, then
# agreed_to_meet, then everything else (P1+P2, Others, ...) in its existing
# relative order (stable sort) -- replaces the old Priority-based sort for
# the Audio Index lead-level drill-down table.
_CATEGORY_SORT_ORDER = ["another_follow_up_required", "agreed_to_meet"]

# Due Today / Overdue lead-level Category sort order: agreed_to_meet first,
# then another_follow_up_required, then P1+P2, then everything else
# (Others, ...) in its existing relative order (stable sort).
_DUE_OVERDUE_CATEGORY_SORT_ORDER = ["agreed_to_meet", "another_follow_up_required", "p1+p2"]


def _category_sort_frame(df: pd.DataFrame, category_col: str = "Category", order: list[str] | None = None) -> pd.DataFrame:
    """
    Sorts a lead-level table by Category according to `order` (lower-cased
    category values, highest priority first); any Category value not in
    `order` sorts last, keeping its existing relative order (stable sort).
    Defaults to _CATEGORY_SORT_ORDER when `order` isn't given.
    """
    sort_order = order if order is not None else _CATEGORY_SORT_ORDER
    raw = df[category_col].astype(str).str.strip().str.lower()
    rank_map = {name: i for i, name in enumerate(sort_order)}
    rank = raw.map(rank_map).fillna(len(sort_order))
    out = df.assign(_cat_rank=rank)
    out = out.sort_values(by="_cat_rank", kind="stable")
    return out.drop(columns=["_cat_rank"]).reset_index(drop=True)


def due_today_summary(df: pd.DataFrame) -> dict:
    """
    df must already be cluster-filtered. Computes the Due Today section's
    headline numbers, all within the "came due today" population (see
    came_due_pool -- fu_due_date_today == 1) -- itself narrowed to the
    Eligible-for-follow-ups corpus first (see due_today_corpus_pool).
    **[FIX]** -- this used to filter fu_due_date_today directly on the
    whole cluster-filtered data with no Eligible restriction.

    Each of the 4 summary boxes now shows a 3-part Due / Worked / Booked
    reading, all computed on the SAME population that box covers (the
    whole came-due-today pool for the top box, that outcome's leads for
    Agreed+Another, that priority's leads for P1+P2):
      - Due:    fu_due_date_today == 1 (already true for every row here)
      - Worked: fu_completedat_today == 1
      - Booked: order_closure_datetime_ist is not blank, out of that SAME
        box's Due population -- independent of Worked, not nested inside
        it. **[FIX]** -- Booked used to be "of Worked, order_closure_datetime_ist
        not blank" (i.e. AND'd with the Worked condition); a lead whose
        order got closed without today's specific follow-up task being
        marked complete (fu_completedat_today != 1) was invisible to
        Booked even though the order was, in fact, booked. Booked is now
        its own independent condition over Due, same relationship
        Agreed+Another/P1+P2/Others already have to each other.

    Others is a genuine independent population, not a residual: every lead
    in the came-due-today pool whose last_follow_up_status is checked first
    (not agreed_to_meet/another_follow_up_required) and, of what's left,
    whose priority is also not P1/P2 -- equivalently, ~Agreed+Another AND
    ~P1+P2. Its own Due/Worked/Booked are that population's own counts,
    the same way Agreed+Another's and P1+P2's Worked/Booked are computed
    (their own mask AND completed/booked). **[FIX]** -- this used to be
    total_came_due minus Agreed+Another minus P1+P2, which double-subtracted
    any lead that was BOTH (undercounting Others for that overlap). Because
    all three boxes are independent, Agreed+Another + P1+P2 + Others can
    add up to MORE than total_came_due whenever a lead is in both
    Agreed+Another and P1+P2 -- that's expected, not a bug.
    """
    cd = came_due_pool(due_today_corpus_pool(df))
    total_came_due = len(cd)

    completed_mask = _is_one(cd.get("fu_completedat_today"), cd.index)
    completed_count = int(completed_mask.sum())
    pending_count = total_came_due - completed_count
    pct_completed = (completed_count / total_came_due * 100) if total_came_due else 0.0

    pending_rows = cd[~completed_mask].copy()
    pending_rows["tl"] = pending_rows.get("tl", pd.Series(dtype=str)).fillna("(unattributed)")
    pending_by_tl = pending_rows.groupby("tl").size().sort_values(ascending=False)
    top_pending_tl = pending_by_tl.index[0] if len(pending_by_tl) else None
    top_pending_count = int(pending_by_tl.iloc[0]) if len(pending_by_tl) else 0

    order_closure_filled = (
        cd.get("order_closure_datetime_ist", pd.Series("", index=cd.index)).astype(str).str.strip() != ""
    )
    booked_count = int(order_closure_filled.sum())

    status = cd.get("last_follow_up_status", pd.Series("", index=cd.index)).astype(str).str.strip().str.lower()
    status_mask = status.isin(AGREED_OUTCOMES)
    agreed_another = int(status_mask.sum())

    priority = cd.get("priority", pd.Series("", index=cd.index)).astype(str).str.strip().str.upper()
    priority_mask = priority.isin(HIGH_PRIORITY)
    p1p2 = int(priority_mask.sum())

    # Per-outcome/per-priority "completed today" and "booked" counts,
    # against that same group's own "due today" count -- an independent
    # count per group (not a mutually-exclusive partition), same
    # convention as before. Booked is independent of Worked here too (see
    # the docstring's [FIX] note) -- not AND'd with completed_mask.
    agreed_another_completed = int((status_mask & completed_mask).sum())
    agreed_another_booked = int((status_mask & order_closure_filled).sum())
    p1p2_completed = int((priority_mask & completed_mask).sum())
    p1p2_booked = int((priority_mask & order_closure_filled).sum())

    others_mask = ~status_mask & ~priority_mask
    others_due = int(others_mask.sum())
    others_completed = int((others_mask & completed_mask).sum())
    others_booked = int((others_mask & order_closure_filled).sum())

    agreed_another_pct = (agreed_another_completed / agreed_another * 100) if agreed_another else 0.0
    p1p2_pct = (p1p2_completed / p1p2 * 100) if p1p2 else 0.0
    others_pct = (others_completed / others_due * 100) if others_due else 0.0

    return {
        "total_came_due": total_came_due,
        "completed_count": completed_count,
        "pending_count": pending_count,
        "pct_completed": pct_completed,
        "top_pending_tl": top_pending_tl,
        "top_pending_count": top_pending_count,
        "booked_count": booked_count,
        "agreed_another": agreed_another,
        "agreed_another_completed": agreed_another_completed,
        "agreed_another_booked": agreed_another_booked,
        "agreed_another_pct": agreed_another_pct,
        "p1p2": p1p2,
        "p1p2_completed": p1p2_completed,
        "p1p2_booked": p1p2_booked,
        "p1p2_pct": p1p2_pct,
        "others": others_due,
        "others_completed": others_completed,
        "others_booked": others_booked,
        "others_pct": others_pct,
    }


# ---------------------------------------------------------------------------
# Drill-down: TL -> SC -> Lead
# ---------------------------------------------------------------------------


def breakdown_by(eligible_pool: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    One row per TL (or per SC, depending on group_col): came due, worked,
    pending, pct, plus Agreed+Another / P1+P2 / Others -- using the SAME
    independent-count convention as due_today_summary's cards, not the
    mutually-exclusive due_today_categorize() bucketing the lead-level
    tables use.

    `eligible_pool` is the Due Today section's overall corpus (see
    due_today_corpus_pool) -- NOT pre-filtered to fu_due_date_today==1.
    Every TL/SC present anywhere in this corpus gets a row, even one with
    zero due-today leads (all its numeric columns are then 0). **[FIX]**
    -- this used to take the already came_due-filtered pool directly, so a
    TL/SC with no lead due today was silently absent from the table
    entirely rather than showing a 0 row.

    For a group's rows (computed only from that group's came_due subset --
    see came_due_pool):
      - Agreed + Another / P1+P2 / Others: all three are independent
        per-lead conditions (Others = ~Agreed+Another AND ~P1+P2, checked
        against status first, then priority for what's left -- same as
        due_today_summary's cards), each summed via groupby. Because all
        three are independent, a row's Agreed+Another + P1+P2 + Others can
        add up to more than its own came_due whenever a lead is in both
        Agreed+Another and P1+P2 -- that's expected, not a bug.
    Each column's sum across every TL, or every SC, still ties exactly to
    due_today_summary's own agreed_another / p1p2 / others -- a single SC's
    numbers need not resemble its own TL's row (a TL's total is itself a
    sum over several SCs), but the SUM across a whole level always matches
    the card, because group_col always partitions `came_due` exactly
    (every row has exactly one tl and one sc), so a group-by sum of the
    same independent condition can never double-count or drop a row -- the
    0-count rows this adds don't change that identity, they just add zeros.
    """
    elig = eligible_pool.copy()
    elig[group_col] = elig.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    roster = pd.DataFrame({group_col: sorted(elig[group_col].unique())})

    cd = came_due_pool(eligible_pool).copy()
    cd[group_col] = cd.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    cd["_completed"] = _is_one(cd.get("fu_completedat_today"), cd.index)
    status = cd.get("last_follow_up_status", pd.Series("", index=cd.index)).astype(str).str.strip().str.lower()
    cd["_agreed_another"] = status.isin(AGREED_OUTCOMES)
    priority = cd.get("priority", pd.Series("", index=cd.index)).astype(str).str.strip().str.upper()
    cd["_p1p2"] = priority.isin(HIGH_PRIORITY)
    cd["_others"] = ~cd["_agreed_another"] & ~cd["_p1p2"]

    grp = cd.groupby(group_col)
    counts = grp.agg(came_due=("_completed", "size"), worked=("_completed", "sum")).reset_index()
    agreed_counts = grp["_agreed_another"].sum().astype(int).rename("Agreed to Meet + Another follow-up")
    p1p2_counts = grp["_p1p2"].sum().astype(int).rename("P1+P2")
    others_counts = grp["_others"].sum().astype(int).rename("Others")
    counts = (counts.merge(agreed_counts, on=group_col, how="left")
                     .merge(p1p2_counts, on=group_col, how="left")
                     .merge(others_counts, on=group_col, how="left"))

    out = roster.merge(counts, on=group_col, how="left").fillna(0)
    for c in ("came_due", "worked", "Agreed to Meet + Another follow-up", "P1+P2", "Others"):
        out[c] = out[c].astype(int)
    out["pending"] = out["came_due"] - out["worked"]
    out["pct"] = out.apply(
        lambda r: round(r["worked"] / r["came_due"] * 100, 1) if r["came_due"] else 0.0, axis=1,
    )
    out = out[[group_col, "came_due", "worked", "pending", "pct",
               "Agreed to Meet + Another follow-up", "P1+P2", "Others"]]

    out = out.sort_values("pending", ascending=False).reset_index(drop=True)
    return out


def tl_breakdown(eligible_pool: pd.DataFrame) -> pd.DataFrame:
    return breakdown_by(eligible_pool, "tl")


def sc_breakdown(eligible_pool: pd.DataFrame, tl_name: str) -> pd.DataFrame:
    subset = eligible_pool[eligible_pool.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    return breakdown_by(subset, "sc")


def lead_table(came_due: pd.DataFrame, tl_name: str | None = None,
               sc_name: str | None = None) -> pd.DataFrame:
    """
    Lead-level table for the Due Today drill-down, columns:
    Lead ID, Category (the lead's own raw value -- see category_display),
    Meeting done (first_meeting_done_date), Priority, Audio Duration
    (in sec) (audio_duration_sec), Missing (dispositions_missing), FUs done
    (total_followup_done), Last FU (last_follow_up_date), Due
    (followup_due_date), State (constant "Due Today"). No Outcome column.
    Sorted by Category: agreed_to_meet first, then
    another_follow_up_required, then P1+P2, then everything else (Others).

    Due isn't always today even though every row here is "due today" by
    fu_due_date_today -- that flag (see data_pull.apply_end_date_flags) now
    also checks followup_due_date == as_of directly, so the two agree far
    more often than before, but not always: a lead can still reach
    came_due_pool via its FU1..FU6 due dates matching as_of while its
    current open task (followup_due_date) has since moved to a different
    date (e.g. the matching follow-up was rescheduled after the fact).
    This column shows followup_due_date as-is, so that narrower remaining
    gap can still show up here.
    """
    subset = came_due.copy()
    if tl_name:
        subset = subset[subset.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    if sc_name:
        subset = subset[subset.get("sc", pd.Series(dtype=str)).fillna("(unattributed)") == sc_name]

    subset = subset.copy()
    subset["_category"] = subset.apply(category_display, axis=1)

    out = pd.DataFrame({
        "Lead ID": subset.get("lead_id"),
        "Category": subset["_category"],
        "Meeting done": subset.get("first_meeting_done_date"),
        "Priority": subset.get("priority"),
        "Audio Duration (in sec)": subset.get("audio_duration_sec"),
        "Missing": subset.get("dispositions_missing"),
        "FUs done": subset.get("total_followup_done"),
        "Last FU": subset.get("last_follow_up_date"),
        "Due": subset.get("followup_due_date"),
        "State": "Due Today",
    }).reset_index(drop=True)
    return _category_sort_frame(out, "Category", order=_DUE_OVERDUE_CATEGORY_SORT_ORDER)


# ---------------------------------------------------------------------------
# Overdue section
# ---------------------------------------------------------------------------

_AGEING_COLS = ["Under 3 days", "3-5 days", "Over 5 days"]


def _funnel_bucket_series(df: pd.DataFrame) -> pd.Series:
    return df.get("funnel_bucket", pd.Series("", index=df.index)).astype(str).str.strip().str.lower()


def overdue_corpus_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Overdue section's base population, and the Overdue %'s denominator:
    the same "Eligible for follow-ups" pool as Overview's Card 1 / Audio
    Index -- see eligible_for_followups_pool() / _followup_buckets().
    **[FIX, major]** -- this used to be its own, separately-defined
    population (every funnel_bucket except
    booked/terminal/lost/later/customer_unreachable); it now shares the
    exact same eligible-for-follow-ups pool that Card 1 and Audio Index
    use, so the three sections never disagree about which leads are in
    scope. Overdue % is now directly "of the Eligible for follow-ups
    leads, what share are overdue" rather than a separately tuned ratio.
    """
    return eligible_for_followups_pool(df)


def overdue_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    Records within the Overdue corpus (see overdue_corpus_pool) whose
    funnel_bucket == 'overdue' -- the base for the ageing split, the
    Agreed+Another/P1+P2/Others cards, and the lead-level drill-down.
    """
    corpus = overdue_corpus_pool(df)
    fb = _funnel_bucket_series(corpus)
    return corpus[fb == "overdue"].copy()


def _ageing_bucket_from_days(days) -> str:
    if pd.isna(days):
        return "Unknown"
    if days < 3:
        return "Under 3 days"
    if days <= 5:
        return "3-5 days"
    return "Over 5 days"


def overdue_summary(df: pd.DataFrame, as_of: str) -> dict:
    """
    df must already be cluster-filtered. as_of is the dashboard's single
    "today" (the data's end date -- see data_pull.apply_end_date_flags),
    same value passed to every other date-comparison function. Computes
    the Overdue section's headline numbers, all within/against the
    Eligible-for-follow-ups corpus (see overdue_corpus_pool):
      - top_overdue_tl / top_overdue_count: the TL with the most overdue
        leads (a plain count, not net of anything).
      - under_3_days / three_to_five_days / over_5_days: the ageing split
        of overdue_days within the overdue pool.

    **[FIX, major]** -- the headline card used to be a single "Overdue %"
    (overdue count / corpus count). It's now a **Due / Worked / Booked**
    triple, mirroring due_today_summary's cards exactly, all computed on
    the SAME population that box covers (the whole overdue pool for the
    top box, that outcome's leads for Agreed+Another, that priority's
    leads for P1+P2):
      - Due ("Overdue (as on today)"): funnel_bucket == 'overdue' within
        the corpus (already true for every row here) -- this is the same
        count the old total_overdue/"Total overdue leads" metric showed;
        that separate metric is now redundant with this box's first
        number and has been dropped.
      - Worked ("Worked today"): of Due, last_follow_up_date == as_of.
        **[FIX, twice]** -- this used to be fu_completedat_today == 1
        (Due Today's own definition of "worked"), but that flag and
        funnel_bucket == 'overdue' never co-occur in the data (see
        logics.md §5) -- Worked today always read 0 as a result. It was
        then briefly "last_follow_up_date is not blank" (any date at
        all), which over-counted: a lead last followed up weeks ago still
        has a non-blank last_follow_up_date, so that reading isn't "worked
        TODAY" at all. Checking the date actually equals as_of is what
        "worked today" means.
      - Booked ("Booked today"): order_closure_datetime_ist is not blank,
        out of Due -- independent of Worked, not nested inside it, same
        [FIX] as due_today_summary's Booked (see its docstring). This is
        NOT "of Worked, booked" -- it's "of the whole Overdue-as-on-today
        pool (or that category's subset), booked", regardless of whether
        that same lead also counts as Worked.
    pct_overdue (overdue / corpus, the OLD headline %) is still returned,
    unused by the card now but still what the TL/SC table's own "Overdue %"
    column ratio is based on (see overdue_breakdown_by) -- not removed.

    agreed_another / p1p2 / others are now also Due/Worked/Booked triples,
    same independent-condition convention as before (Others =
    ~Agreed+Another AND ~P1+P2, checked against status first, then
    priority for what's left) plus their own Worked/Booked counts, exactly
    like due_today_summary's per-category boxes (Booked independent of
    Worked there too). Because all three are independent, Agreed+Another +
    P1+P2 + Others (on any of the three Due/Worked/Booked numbers) can add
    up to more than the top box's own total whenever a lead is in both
    Agreed+Another and P1+P2 -- expected, not a bug (see
    due_today_summary's docstring for the full reasoning).
    """
    corpus = overdue_corpus_pool(df)
    fb = _funnel_bucket_series(corpus)
    od = corpus[fb == "overdue"].copy()

    total_denominator = len(corpus)
    total_overdue = len(od)
    pct_overdue = (total_overdue / total_denominator * 100) if total_denominator else 0.0

    od_tl = od.copy()
    od_tl["tl"] = od_tl.get("tl", pd.Series(dtype=str)).fillna("(unattributed)")
    by_tl = od_tl.groupby("tl").size().sort_values(ascending=False)
    top_overdue_tl = by_tl.index[0] if len(by_tl) else None
    top_overdue_count = int(by_tl.iloc[0]) if len(by_tl) else 0

    overdue_days_num = pd.to_numeric(od.get("overdue_days"), errors="coerce")
    under_3 = int((overdue_days_num < 3).sum())
    three_to_five = int(((overdue_days_num >= 3) & (overdue_days_num <= 5)).sum())
    over_5 = int((overdue_days_num > 5).sum())

    under_3_pct = (under_3 / total_overdue * 100) if total_overdue else 0.0
    three_to_five_pct = (three_to_five / total_overdue * 100) if total_overdue else 0.0
    over_5_pct = (over_5 / total_overdue * 100) if total_overdue else 0.0

    completed_mask = (
        od.get("last_follow_up_date", pd.Series("", index=od.index)).astype(str).str.strip() == str(as_of).strip()
    )
    completed_count = int(completed_mask.sum())
    order_closure_filled = (
        od.get("order_closure_datetime_ist", pd.Series("", index=od.index)).astype(str).str.strip() != ""
    )
    booked_count = int(order_closure_filled.sum())

    status = od.get("last_follow_up_status", pd.Series("", index=od.index)).astype(str).str.strip().str.lower()
    status_mask = status.isin(AGREED_OUTCOMES)
    agreed_another = int(status_mask.sum())

    priority = od.get("priority", pd.Series("", index=od.index)).astype(str).str.strip().str.upper()
    priority_mask = priority.isin(HIGH_PRIORITY)
    p1p2 = int(priority_mask.sum())

    others_mask = ~status_mask & ~priority_mask
    others = int(others_mask.sum())

    agreed_another_completed = int((status_mask & completed_mask).sum())
    agreed_another_booked = int((status_mask & order_closure_filled).sum())
    p1p2_completed = int((priority_mask & completed_mask).sum())
    p1p2_booked = int((priority_mask & order_closure_filled).sum())
    others_completed = int((others_mask & completed_mask).sum())
    others_booked = int((others_mask & order_closure_filled).sum())

    agreed_another_pct = (agreed_another_completed / agreed_another * 100) if agreed_another else 0.0
    p1p2_pct = (p1p2_completed / p1p2 * 100) if p1p2 else 0.0
    others_pct = (others_completed / others * 100) if others else 0.0

    return {
        "total_overdue": total_overdue,
        "total_denominator": total_denominator,
        "pct_overdue": pct_overdue,
        "top_overdue_tl": top_overdue_tl,
        "top_overdue_count": top_overdue_count,
        "completed_count": completed_count,
        "booked_count": booked_count,
        "under_3_days": under_3,
        "three_to_five_days": three_to_five,
        "over_5_days": over_5,
        "under_3_days_pct": under_3_pct,
        "three_to_five_days_pct": three_to_five_pct,
        "over_5_days_pct": over_5_pct,
        "agreed_another": agreed_another,
        "agreed_another_completed": agreed_another_completed,
        "agreed_another_booked": agreed_another_booked,
        "agreed_another_pct": agreed_another_pct,
        "p1p2": p1p2,
        "p1p2_completed": p1p2_completed,
        "p1p2_booked": p1p2_booked,
        "p1p2_pct": p1p2_pct,
        "others": others,
        "others_completed": others_completed,
        "others_booked": others_booked,
        "others_pct": others_pct,
    }


def overdue_breakdown_by(cluster_df: pd.DataFrame, group_col: str,
                          filter_col: str | None = None, filter_value: str | None = None) -> pd.DataFrame:
    """
    One row per TL (or per SC, when drilling into a TL): Overdue count,
    Overdue % (that group's overdue count over that group's own corpus
    count -- see overdue_corpus_pool -- same ratio as the section card),
    the Agreed+Another / P1+P2 / Others split, and the Under 3 / 3-5 /
    Over 5 days ageing split.

    Every TL (and, within a TL, every SC) present anywhere in the Eligible
    corpus gets a row -- including one with zero overdue leads, shown as a
    row of zeros rather than being absent. **[FIX]** -- this used to seed
    rows only from the overdue-only subset, so a TL/SC with no overdue
    lead never appeared in this table at all, even though they might have
    plenty of other Eligible-for-follow-ups leads just not overdue (same
    bug, and same fix, as Due Today's breakdown_by).

    Agreed+Another / P1+P2 / Others are all independent per-lead
    conditions, same convention as overdue_summary's cards: a lead in both
    Agreed+Another and P1+P2 is counted in both columns, and Others =
    ~Agreed+Another AND ~P1+P2 (checked against status first, then
    priority for what's left), counted directly rather than as a residual.
    A row's Agreed+Another + P1+P2 + Others can add up to more than its
    own Overdue count whenever a lead is in both Agreed+Another and
    P1+P2 -- that's expected, not a bug (see overdue_summary's docstring).
    Each column's sum across every TL (or every SC) still ties back to the
    summary card's own total for that column -- see breakdown_by's
    docstring for the underlying reason (grouping always partitions the
    corpus exactly, so a group-by sum of the same independent condition
    can never double-count or drop a row -- the zero-count rows this adds
    don't change that identity, they just add zeros).

    `cluster_df` must be the *full* cluster-filtered data (not just the
    overdue subset) so each group's own corpus count can be computed -- the
    overdue subset alone isn't enough to know how many of that TL's/SC's
    leads were eligible to be overdue in the first place.
    """
    base = cluster_df
    if filter_col and filter_value is not None:
        base = base[base.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]

    corpus = overdue_corpus_pool(base).copy()
    corpus[group_col] = corpus.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    roster = pd.DataFrame({group_col: sorted(corpus[group_col].unique())})
    corpus_counts = corpus.groupby(group_col).size().rename("_corpus").reset_index()

    fb = _funnel_bucket_series(corpus)
    od = corpus[fb == "overdue"].copy()
    status = od.get("last_follow_up_status", pd.Series("", index=od.index)).astype(str).str.strip().str.lower()
    od["_agreed_another"] = status.isin(AGREED_OUTCOMES)
    priority = od.get("priority", pd.Series("", index=od.index)).astype(str).str.strip().str.upper()
    od["_p1p2"] = priority.isin(HIGH_PRIORITY)
    od["_others"] = ~od["_agreed_another"] & ~od["_p1p2"]
    od["_ageing"] = pd.to_numeric(od.get("overdue_days"), errors="coerce").apply(_ageing_bucket_from_days)
    od["_one"] = 1

    overdue_counts = od.groupby(group_col).size().rename("overdue").reset_index()
    agreed_counts = od.groupby(group_col)["_agreed_another"].sum().rename("Agreed to Meet + Another follow-up")
    p1p2_counts = od.groupby(group_col)["_p1p2"].sum().rename("P1+P2")
    others_counts = od.groupby(group_col)["_others"].sum().rename("Others")
    age_pivot = od.pivot_table(
        index=group_col, columns="_ageing", values="_one", aggfunc="size", fill_value=0,
    ).reindex(columns=_AGEING_COLS, fill_value=0).reset_index()

    out = (roster.merge(corpus_counts, on=group_col, how="left")
                 .merge(overdue_counts, on=group_col, how="left")
                 .merge(agreed_counts, on=group_col, how="left")
                 .merge(p1p2_counts, on=group_col, how="left")
                 .merge(others_counts, on=group_col, how="left")
                 .merge(age_pivot, on=group_col, how="left")
                 .fillna(0))

    for c in ["_corpus", "overdue", "Agreed to Meet + Another follow-up", "P1+P2", "Others"] + _AGEING_COLS:
        out[c] = out[c].astype(int)

    out["overdue_pct"] = out.apply(
        lambda r: round(r["overdue"] / r["_corpus"] * 100, 1) if r["_corpus"] else 0.0, axis=1
    )
    out = out.drop(columns=["_corpus"])
    out = out[[group_col, "overdue", "overdue_pct", "Agreed to Meet + Another follow-up", "P1+P2", "Others"]
              + _AGEING_COLS]

    out = out.sort_values("overdue", ascending=False).reset_index(drop=True)
    return out


def tl_overdue_breakdown(cluster_df: pd.DataFrame) -> pd.DataFrame:
    return overdue_breakdown_by(cluster_df, "tl")


def sc_overdue_breakdown(cluster_df: pd.DataFrame, tl_name: str) -> pd.DataFrame:
    return overdue_breakdown_by(cluster_df, "sc", filter_col="tl", filter_value=tl_name)


def overdue_lead_table(overdue_pool_df: pd.DataFrame, tl_name: str | None = None,
                        sc_name: str | None = None) -> pd.DataFrame:
    """
    Lead-level table for the Overdue drill-down. Same Category/Priority/
    Audio Duration (in sec)/Missing/FUs-done/Meeting-done/Last-FU/Due
    definitions as lead_table() (no Outcome column, sorted by Category: agreed_to_meet
    first, then another_follow_up_required, then P1+P2, then everything
    else), but:
      - Category shows the lead's own raw value -- see category_display.
      - Due: the lead's own followup_due_date (not today's date -- unlike
        Due Today, where every row is due today by definition, an overdue
        lead's followup_due_date is in the past).
      - Ageing: the lead's overdue_days number rendered as text, e.g. "8 days"
        (this column is named "State" in lead_table()'s Due Today output,
        but "Ageing" here per spec).
    **[FIX]** -- Meeting done and Last FU used to be last_follow_up_date and
    latest_status respectively (latest_status is blank for ~all leads
    except those with more than 6 follow-up tasks). This table was missed
    when lead_table() got the same fix, leaving the two sections
    inconsistent; now aligned.
    """
    subset = overdue_pool_df.copy()
    if tl_name:
        subset = subset[subset.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    if sc_name:
        subset = subset[subset.get("sc", pd.Series(dtype=str)).fillna("(unattributed)") == sc_name]

    subset = subset.copy()
    subset["_category"] = subset.apply(category_display, axis=1)

    overdue_days_num = pd.to_numeric(subset.get("overdue_days"), errors="coerce")
    ageing = overdue_days_num.apply(lambda d: f"{int(d)} days" if pd.notna(d) else "")

    out = pd.DataFrame({
        "Lead ID": subset.get("lead_id"),
        "Category": subset["_category"],
        "Meeting done": subset.get("first_meeting_done_date"),
        "Priority": subset.get("priority"),
        "Audio Duration (in sec)": subset.get("audio_duration_sec"),
        "Missing": subset.get("dispositions_missing"),
        "FUs done": subset.get("total_followup_done"),
        "Last FU": subset.get("last_follow_up_date"),
        "Due": subset.get("followup_due_date"),
        "Ageing": ageing,
    }).reset_index(drop=True)
    return _category_sort_frame(out, "Category", order=_DUE_OVERDUE_CATEGORY_SORT_ORDER)


# ---------------------------------------------------------------------------
# Audio Index
# ---------------------------------------------------------------------------

# Total disposition questions tracked per lead -- matches the length of
# DISPOSITION_COLUMNS below and is the divisor in the Completeness %
# formula. 'Remarks' is deliberately excluded (see DISPOSITION_COLUMNS),
# so this is 11, not the original 12.
TOTAL_DISPOSITIONS = 11


def audio_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Audio Index sub-section's base population (Coverage %/
    Completeness %/Audio Index, Split, Disposition Breakdown, and the
    TL -> SC -> Lead drill-down): the corrected "Eligible for
    follow-ups" pool -- see eligible_for_followups_pool() /
    _followup_buckets() -- i.e. Meetings done, minus Closed on spot,
    Closed on Follow-Up and No audio notes.

    This used to be its own, separately-defined
    audio_present == True & is_on_spot == False pool; per the latest
    requirement, Audio Index now shares the exact same "eligible for
    follow-ups" pool that Card 1's own count uses, so the two sections
    never disagree about which leads are "eligible."
    """
    return eligible_for_followups_pool(df)


def audio_index_summary(df: pd.DataFrame) -> dict:
    """
    df must already be cluster + sc_channel filtered (the "Meeting Done"
    population).

    - Coverage %: count(the eligible-for-follow-ups pool -- see
      audio_pool()), over count(Meetings done AND
      order_closure_datetime_ist is blank) -- irrespective of Closed on
      spot / Closed on Follow-Up bucket membership; a lead's own
      order_closure_datetime_ist value is checked directly. **[FIX]** --
      the denominator used to be count(is_on_spot == False), which still
      included Closed on Follow-Up leads (they aren't on-spot, but they
      do have order_closure_datetime_ist filled in) and so overcounted the
      denominator. Every Closed-on-spot lead has
      order_closure_datetime_ist filled in too (confirmed against the
      data: 0 exceptions), so this new denominator drops both closed
      buckets cleanly and is algebraically identical to "No audio notes"
      count + "Eligible for follow-ups" count.
    - Completeness %: **[FIX]** -- now computed over that SAME wider
      denominator population (No audio notes + Eligible), not just the
      Eligible pool -- (denom_count * 11 - sum(dispositions_missing over
      that wider population)) / (denom_count * 11) * 100. A "No audio
      notes" lead's dispositions_missing defaults to 11 (fully missing) in
      the source SQL, so folding those leads in pulls Completeness % down
      substantially versus the old Eligible-only calculation -- this is
      intentional, not a regression.
    - Audio Index: (Coverage % * Completeness %) / 1000, clipped to
      [0, 10] and rounded to 1 decimal -- same formula as the TL/SC-wise
      Index column in audio_split_breakdown().
    """
    order_closure_blank = (
        df.get("order_closure_datetime_ist", pd.Series("", index=df.index)).astype(str).str.strip() == ""
    )
    completeness_pool = df[order_closure_blank]
    total_denominator = len(completeness_pool)

    pool = audio_pool(df)
    pool_count = len(pool)
    coverage_pct = (pool_count / total_denominator * 100) if total_denominator else 0.0

    missing_sum = pd.to_numeric(completeness_pool.get("dispositions_missing"), errors="coerce").fillna(0).sum()
    total_slots = total_denominator * TOTAL_DISPOSITIONS
    completeness_pct = ((total_slots - missing_sum) / total_slots * 100) if total_slots else 0.0

    audio_index = round(min(max(coverage_pct * completeness_pct / 1000, 0.0), 10.0), 1)

    return {
        "total_denominator": total_denominator,
        "coverage_count": pool_count,
        "coverage_pct": coverage_pct,
        "completeness_pct": completeness_pct,
        "audio_index": audio_index,
    }


def audio_index_summary_by_city(df: pd.DataFrame) -> pd.DataFrame:
    """
    Audio Index / Coverage % / Completeness %, broken down one row per
    city (the `cluster` column) -- used for the Pan-India Audio Index
    section's City breakdown table. Same definitions as
    audio_index_summary, just computed per city. Audio Index is the 2nd
    column (right after City) since it's this table's headline number.
    """
    rows = []
    cluster = df.get("cluster", pd.Series(dtype=str)).astype(str).str.strip()
    for city in _known_cities(df):
        sub = df[cluster == city]
        a = audio_index_summary(sub)
        rows.append({
            "City": city,
            "audio_index": a["audio_index"],
            "coverage_pct": a["coverage_pct"],
            "completeness_pct": a["completeness_pct"],
        })
    return pd.DataFrame(rows, columns=["City", "audio_index", "coverage_pct", "completeness_pct"])


def apply_meetings_today_filter(df: pd.DataFrame, enabled: bool, as_of: str) -> pd.DataFrame:
    """
    The Audio Index sub-section's 'From Meeting's Today' toggle: when on,
    keeps only leads whose first_meeting_done_date == as_of (the
    dashboard's "today" -- the data's end date, never the server clock; see
    data_pull.apply_end_date_flags). **[FIX]** -- this used to check
    fu_completedat_today == 1 (a follow-up-task flag, unrelated to when the
    meeting itself happened), then briefly meeting_done_date (the most
    recent meeting activity, which can differ from the lead's first
    meeting); it now checks first_meeting_done_date specifically.
    """
    if not enabled:
        return df
    mask = (
        df.get("first_meeting_done_date", pd.Series("", index=df.index)).astype(str).str.strip()
        == str(as_of).strip()
    )
    return df[mask].copy()


def audio_breakdown_by(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """
    One row per TL (or per SC): lead count, % of those leads with
    dispositions_missing > 0, and the average dispositions_missing per lead.
    `df` here is the sub-section's actual lead pool (audio_pool, optionally
    toggle-filtered) -- see audio_split_breakdown for the Index column,
    which needs a wider population than this.
    """
    d = df.copy()
    d[group_col] = d.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    missing = pd.to_numeric(d.get("dispositions_missing"), errors="coerce").fillna(0)
    d["_missing"] = missing
    d["_has_missing"] = missing > 0

    out = d.groupby(group_col).agg(
        leads=("_missing", "size"),
        pct_with_missing=("_has_missing", "mean"),
        avg_missing=("_missing", "mean"),
    ).reset_index()
    out["pct_with_missing"] = (out["pct_with_missing"] * 100).round(1)
    out["avg_missing"] = out["avg_missing"].round(1)
    return out


def audio_split_breakdown(meeting_done_pool: pd.DataFrame, audio_base: pd.DataFrame, group_col: str,
                           filter_col: str | None = None, filter_value: str | None = None) -> pd.DataFrame:
    """
    One row per TL (or per SC, when drilling into a TL): Index, Leads, %
    leads with a disposition missing, and average dispositions missing per
    lead -- sorted by % leads with a disposition missing (descending).

    - meeting_done_pool: the full Meeting-Done population (city_df, already
      filtered by the 'From Meeting's Today' toggle if it's on). Needed
      because the Coverage %/Completeness % denominator (Meetings done AND
      order_closure_datetime_ist blank -- see audio_index_summary) isn't
      restricted to audio_present == True, so it can't be derived from
      audio_base alone. **[FIX]** -- denominator used to be is_on_spot ==
      False, which still included Closed on Follow-Up leads; see
      audio_index_summary's docstring for why order_closure_datetime_ist
      blank is the correct, narrower check (and is algebraically identical
      to "No audio notes" + "Eligible for follow-ups" per group).
    - audio_base: meeting_done_pool narrowed to the corrected
      eligible-for-follow-ups pool (audio_pool(meeting_done_pool) --
      see eligible_for_followups_pool) -- the sub-section's actual
      leads, used for Leads / % missing / avg missing (audio_breakdown_by)
      and, via its own count, the Coverage % numerator for the Index.
      **[FIX]** -- Completeness % (and so the Index) is no longer computed
      from audio_base's own dispositions_missing; it now uses the same
      wider "order_closure_datetime_ist blank" population as the
      denominator (see audio_index_summary's docstring for why).
    """
    md, ab = meeting_done_pool, audio_base
    if filter_col and filter_value is not None:
        md = md[md.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]
        ab = ab[ab.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]

    order_closure_blank = (
        md.get("order_closure_datetime_ist", pd.Series("", index=md.index)).astype(str).str.strip() == ""
    )
    denom = md[order_closure_blank].copy()
    denom[group_col] = denom.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    denom_counts = denom.groupby(group_col).size().rename("_denom")
    denom["_missing"] = pd.to_numeric(denom.get("dispositions_missing"), errors="coerce").fillna(0)
    denom_missing_sum = denom.groupby(group_col)["_missing"].sum().rename("_denom_missing_sum")

    ab = ab.copy()
    ab[group_col] = ab.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    pool_counts = ab.groupby(group_col).size().rename("_pool")

    idx_df = pd.concat([denom_counts, denom_missing_sum, pool_counts], axis=1).fillna(0).reset_index()
    idx_df["coverage_pct"] = idx_df.apply(
        lambda r: (r["_pool"] / r["_denom"] * 100) if r["_denom"] else 0.0, axis=1,
    )
    idx_df["completeness_pct"] = idx_df.apply(
        lambda r: ((r["_denom"] * TOTAL_DISPOSITIONS - r["_denom_missing_sum"]) / (r["_denom"] * TOTAL_DISPOSITIONS) * 100)
        if r["_denom"] else 0.0,
        axis=1,
    )
    idx_df["index"] = (idx_df["coverage_pct"] * idx_df["completeness_pct"] / 1000).clip(lower=0, upper=10).round(1)
    idx_df = idx_df[[group_col, "index"]]

    miss_df = audio_breakdown_by(ab, group_col)  # group_col, leads, pct_with_missing, avg_missing

    out = miss_df.merge(idx_df, on=group_col, how="left")
    out["index"] = out["index"].fillna(0.0)
    out = out[[group_col, "index", "leads", "pct_with_missing", "avg_missing"]]
    out = out.sort_values("pct_with_missing", ascending=False).reset_index(drop=True)
    return out


def tl_audio_breakdown(meeting_done_pool: pd.DataFrame, audio_base: pd.DataFrame) -> pd.DataFrame:
    return audio_split_breakdown(meeting_done_pool, audio_base, "tl")


def sc_audio_breakdown(meeting_done_pool: pd.DataFrame, audio_base: pd.DataFrame, tl_name: str) -> pd.DataFrame:
    return audio_split_breakdown(meeting_done_pool, audio_base, "sc", filter_col="tl", filter_value=tl_name)


def audio_lead_table(df: pd.DataFrame, tl_name: str | None = None, sc_name: str | None = None) -> pd.DataFrame:
    """
    Lead-level table for the Audio Index Split drill-down: Lead ID, Category,
    Meeting done, Priority, Audio Duration (in sec), Missing, FUs done,
    Last FU, Due -- same definitions as lead_table()/overdue_lead_table()
    (Category shows the lead's own raw value, Meeting done is
    first_meeting_done_date, Last FU is last_follow_up_date, Due is the
    lead's followup_due_date). No State/Ageing/Outcome column, per spec.
    Sorted by Category with the same priority as Due Today/Overdue:
    agreed_to_meet first, then another_follow_up_required, then P1+P2,
    then everything else (Others).

    **[FIX]** -- Meeting done and Last FU used to be last_follow_up_date and
    latest_status respectively (latest_status is blank for ~all leads
    except those with more than 6 follow-up tasks). This table was missed
    when lead_table() got the same fix, leaving this section inconsistent
    with Due Today; now aligned.
    """
    subset = df.copy()
    if tl_name:
        subset = subset[subset.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    if sc_name:
        subset = subset[subset.get("sc", pd.Series(dtype=str)).fillna("(unattributed)") == sc_name]

    subset = subset.copy()
    subset["_category"] = subset.apply(category_display, axis=1)

    out = pd.DataFrame({
        "Lead ID": subset.get("lead_id"),
        "Category": subset["_category"],
        "Meeting done": subset.get("first_meeting_done_date"),
        "Priority": subset.get("priority"),
        "Audio Duration (in sec)": subset.get("audio_duration_sec"),
        "Missing": subset.get("dispositions_missing"),
        "FUs done": subset.get("total_followup_done"),
        "Last FU": subset.get("last_follow_up_date"),
        "Due": subset.get("followup_due_date"),
    }).reset_index(drop=True)
    return _category_sort_frame(out, "Category", order=_DUE_OVERDUE_CATEGORY_SORT_ORDER)


# The 11 disposition questions tracked per lead, mapped to their slugified
# CSV column names (data_pull.py's slugify_column() applied to the SQL
# query's own column aliases -- see followup_dashboard_query.sql).
#
# 'Remarks' is deliberately NOT in this list (and not consumed anywhere in
# the dashboard) -- it's a free-text field, not a structured disposition,
# so it's dropped from both the CSV fetch and the Completeness %/missing
# calculations. If it still appears as a raw column in the CSV, it's simply
# ignored, same as any other extraneous column.
DISPOSITION_COLUMNS = {
    "Quoted Price": "quoted_price",
    "Offering": "offering",
    "Buffer / Margin Left": "buffer_margin_left",
    "SC's Reading": "scs_reading",
    "Site Visit Done?": "site_visit_done",
    "Bill Value": "bill_value",
    "Competitor Quote": "competitor_quote",
    "Next Follow up (Voice Note)": "next_follow_up_voice_note",
    "Timeline to go Solar": "timeline_to_go_solar",
    "Decision Maker": "decision_maker",
    "Main Objection": "main_objection",
}


def disposition_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per disposition question: how many leads in `df` are blank for
    that disposition (Leads), what share of df that is (Share), and of
    those blank leads, how many have audio_duration_sec < 45 seconds
    (Of those, <45s).
    """
    total = len(df)
    audio_dur = pd.to_numeric(df.get("audio_duration_sec"), errors="coerce")

    rows = []
    for label, col in DISPOSITION_COLUMNS.items():
        series = df.get(col)
        if series is None:
            is_blank = pd.Series(True, index=df.index)
        else:
            is_blank = series.isna() | (series.astype(str).str.strip() == "")
        leads = int(is_blank.sum())
        share = (leads / total * 100) if total else 0.0
        under_45 = int((is_blank & (audio_dur < 45)).sum())
        rows.append({
            "Disposition": label,
            "Leads": leads,
            "Share": round(share, 1),
            "Of those, <45s": under_45,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Follow-up funnel
# ---------------------------------------------------------------------------

# Sentinel raw_status used for the special Booked box, which has its own
# lead-filtering criteria (see booked_box_pool) rather than being one of the
# dynamically-discovered last_follow_up_status values.
BOOKED_SENTINEL = "__booked__"

# Outcomes whose display label isn't a plain title-cased version of the raw
# value -- everything else falls back to funnel_box_label()'s generic rule.
FUNNEL_RELABELS = {
    "another_follow_up_required": "Another Follow up needed",
    "dnp": "DNP",
}


def funnel_box_label(raw_status: str) -> str:
    raw = str(raw_status or "").strip()
    key = raw.lower()
    if key in FUNNEL_RELABELS:
        return FUNNEL_RELABELS[key]
    return raw.replace("_", " ").title() if raw else "(blank)"


def funnel_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    Follow-up funnel base pool (applies to every box except Booked) --
    shared by the Funnel Quality section too (see funnel_quality_pool):
    the same "Eligible for follow-ups" pool as Overview's Card 1 / Audio
    Index / Overdue / Due Today -- see eligible_for_followups_pool().
    **[FIX, major]** -- this used to be its own, separately-defined pool
    (audio_present == True AND is_on_spot == False AND
    order_closure_datetime_ist blank); it now shares the exact same
    eligible-for-follow-ups pool every other section uses, so none of them
    disagree about which leads are in scope.
    """
    return eligible_for_followups_pool(df)


def booked_box_pool(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Booked box's own lead-filtering criteria (the one exception to
    funnel_pool): order_closure_datetime_ist is NOT blank AND
    total_followup_done > 0.
    """
    order_closure_filled = (
        df.get("order_closure_datetime_ist", pd.Series("", index=df.index)).astype(str).str.strip() != ""
    )
    fus_done = pd.to_numeric(df.get("total_followup_done"), errors="coerce").fillna(0)
    return df[order_closure_filled & (fus_done > 0)].copy()


def funnel_box_counts(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per distinct last_follow_up_status value present in the funnel
    pool, plus the special Booked box, sorted by lead count descending.
    Columns: raw_status, label, leads.

    A raw status of 'booked' (if it ever appears inside funnel_pool -- in
    practice it shouldn't, since a booked lead would normally have
    order_closure_datetime_ist filled in) is folded into the dedicated
    Booked box rather than creating a second, differently-defined box with
    the same label.
    """
    pool = funnel_pool(df)
    status = pool.get("last_follow_up_status", pd.Series("", index=pool.index)).astype(str).str.strip()
    status_lower = status.str.lower()
    status = status[(status != "") & (status_lower != "booked")]

    counts = status.value_counts()
    rows = [{"raw_status": s, "label": funnel_box_label(s), "leads": int(c)} for s, c in counts.items()]
    rows.append({"raw_status": BOOKED_SENTINEL, "label": "Booked", "leads": len(booked_box_pool(df))})

    out = pd.DataFrame(rows)
    return out.sort_values("leads", ascending=False).reset_index(drop=True)


def funnel_box_pool(df: pd.DataFrame, raw_status: str) -> pd.DataFrame:
    """The specific pool of leads behind one box (by its raw_status, or BOOKED_SENTINEL)."""
    if raw_status == BOOKED_SENTINEL:
        return booked_box_pool(df)
    pool = funnel_pool(df)
    status = pool.get("last_follow_up_status", pd.Series("", index=pool.index)).astype(str).str.strip().str.lower()
    return pool[status == str(raw_status).strip().lower()].copy()


def _days_from_today(as_of_str: str, date_series: pd.Series) -> pd.Series:
    """date_series - as_of, in days. Positive = future, negative = past. NaN if either side is unparseable."""
    as_of_ts = pd.to_datetime(as_of_str, errors="coerce")
    parsed = pd.to_datetime(date_series, errors="coerce")
    if pd.isna(as_of_ts):
        return pd.Series(float("nan"), index=date_series.index)
    return (parsed - as_of_ts).dt.days.astype(float)


# ---- Another Follow up needed (last_follow_up_status == 'another_follow_up_required') ----

_WHEN_ORDER = ["Overdue", "Due Today", "1-3 days", "4-7 days"]


def _when_bucket(days_ahead) -> str:
    """
    Overdue (days_ahead < 0), Due Today (== 0), 1-3 days, or 4-7 days.
    The last bucket is open-ended (4 or more days out) so every lead with a
    parseable next_follow_up_date lands in exactly one of the 4 rows.
    """
    if pd.isna(days_ahead):
        return "Unknown"
    if days_ahead < 0:
        return "Overdue"
    if days_ahead == 0:
        return "Due Today"
    if days_ahead <= 3:
        return "1-3 days"
    return "4-7 days"


def another_fu_when_table(pool: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """When / Leads / Share, for the Another Follow up needed box's 1st cut."""
    total = len(pool)
    days_ahead = _days_from_today(as_of, pool.get("next_follow_up_date", pd.Series("", index=pool.index)))
    buckets = days_ahead.apply(_when_bucket)
    counts = buckets.value_counts()

    rows = []
    for w in _WHEN_ORDER:
        leads = int(counts.get(w, 0))
        share = (leads / total * 100) if total else 0.0
        rows.append({"When": w, "Leads": leads, "Share": round(share, 1)})
    return pd.DataFrame(rows)


def another_fu_breakdown_by(pool: pd.DataFrame, as_of: str, group_col: str,
                             filter_col: str | None = None, filter_value: str | None = None) -> pd.DataFrame:
    """TL (or SC) view: Leads, Overdue, Due today, Upcoming (1-3 + 4-7 days), Avg days out."""
    base = pool
    if filter_col and filter_value is not None:
        base = base[base.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]

    d = base.copy()
    d[group_col] = d.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    d["_days_ahead"] = _days_from_today(as_of, d.get("next_follow_up_date", pd.Series("", index=d.index)))
    d["_bucket"] = d["_days_ahead"].apply(_when_bucket)

    out = d.groupby(group_col).size().rename("leads").reset_index()
    for bucket_name, col_name, buckets in [
        ("overdue", "overdue", ["Overdue"]),
        ("due_today", "due_today", ["Due Today"]),
        ("upcoming", "upcoming", ["1-3 days", "4-7 days"]),
    ]:
        counts = d[d["_bucket"].isin(buckets)].groupby(group_col).size().rename(col_name)
        out = out.merge(counts, on=group_col, how="left")
        out[col_name] = out[col_name].fillna(0).astype(int)

    avg_days_out = d.groupby(group_col)["_days_ahead"].mean().rename("avg_days_out")
    out = out.merge(avg_days_out, on=group_col, how="left")
    out["avg_days_out"] = out["avg_days_out"].round(1)

    out = out.sort_values("leads", ascending=False).reset_index(drop=True)
    return out


def another_fu_tl_breakdown(pool: pd.DataFrame, as_of: str) -> pd.DataFrame:
    return another_fu_breakdown_by(pool, as_of, "tl")


def another_fu_sc_breakdown(pool: pd.DataFrame, as_of: str, tl_name: str) -> pd.DataFrame:
    return another_fu_breakdown_by(pool, as_of, "sc", filter_col="tl", filter_value=tl_name)


def another_fu_lead_table(pool: pd.DataFrame, as_of: str, tl_name: str | None = None,
                           sc_name: str | None = None) -> pd.DataFrame:
    """
    Lead ID, City, Priority, Scheduled (next_follow_up_date), Days from
    Today (signed day count relative to as_of), Last FU (last_follow_up_date).
    Sorted most-overdue-first (no priority sort here -- unlike the Due
    Today/Overdue tables, this box has no Category column to key off of).

    **[FIX]** -- City used to read a "city" column, which doesn't exist in
    the data (always blank). "City" means the `cluster` column everywhere
    else in this dashboard (the sidebar's own city filter is
    filter_cluster() against `cluster`), so this now matches that.
    """
    subset = pool.copy()
    if tl_name:
        subset = subset[subset.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    if sc_name:
        subset = subset[subset.get("sc", pd.Series(dtype=str)).fillna("(unattributed)") == sc_name]

    days_ahead = _days_from_today(as_of, subset.get("next_follow_up_date", pd.Series("", index=subset.index)))
    days_label = days_ahead.apply(lambda d: f"{int(d):+d} days" if pd.notna(d) else "")

    out = pd.DataFrame({
        "Lead ID": subset.get("lead_id"),
        "City": subset.get("cluster"),
        "Priority": subset.get("priority"),
        "Scheduled": subset.get("next_follow_up_date"),
        "Days from Today": days_label,
        "Last FU": subset.get("last_follow_up_date"),
        "_sort": days_ahead.to_numpy(),
    }).reset_index(drop=True)
    out = out.sort_values("_sort", na_position="last", kind="stable").drop(columns="_sort").reset_index(drop=True)
    return out


# ---- DNP (last_follow_up_status == 'dnp') ----

_DNP_ROWS = [1, 2, 3]


def dnp_consecutive_table(pool: pd.DataFrame) -> pd.DataFrame:
    """Consecutive DNPs (1/2/3) / Leads / Share, for the DNP box's 1st cut."""
    total = len(pool)
    tl_dnp = pd.to_numeric(pool.get("tl_is_dnp_tasks"), errors="coerce")
    rows = []
    for n in _DNP_ROWS:
        leads = int((tl_dnp == n).sum())
        share = (leads / total * 100) if total else 0.0
        rows.append({"Consecutive DNPs": n, "Leads": leads, "Share": round(share, 1)})
    return pd.DataFrame(rows)


def dnp_breakdown_by(pool: pd.DataFrame, group_col: str,
                      filter_col: str | None = None, filter_value: str | None = None) -> pd.DataFrame:
    """TL (or SC) view: DNP leads, 3+ in a row (tl_is_dnp_tasks > 3), Avg DNPs."""
    base = pool
    if filter_col and filter_value is not None:
        base = base[base.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]

    d = base.copy()
    d[group_col] = d.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    d["_dnp"] = pd.to_numeric(d.get("tl_is_dnp_tasks"), errors="coerce").fillna(0)

    out = d.groupby(group_col).agg(
        dnp_leads=("_dnp", "size"),
        three_plus=("_dnp", lambda s: int((s > 3).sum())),
        avg_dnps=("_dnp", "mean"),
    ).reset_index()
    out["avg_dnps"] = out["avg_dnps"].round(1)
    out = out.sort_values("dnp_leads", ascending=False).reset_index(drop=True)
    return out


def dnp_tl_breakdown(pool: pd.DataFrame) -> pd.DataFrame:
    return dnp_breakdown_by(pool, "tl")


def dnp_sc_breakdown(pool: pd.DataFrame, tl_name: str) -> pd.DataFrame:
    return dnp_breakdown_by(pool, "sc", filter_col="tl", filter_value=tl_name)


def dnp_lead_table(pool: pd.DataFrame, tl_name: str | None = None, sc_name: str | None = None) -> pd.DataFrame:
    """
    Lead ID, City, Priority, Consecutive DNPs (tl_is_dnp_tasks), Last FU,
    Due (next_follow_up_date).

    **[FIX]** -- City used to read a "city" column, which doesn't exist in
    the data (always blank); now uses `cluster`, same as
    another_fu_lead_table and the sidebar's own city filter.
    """
    subset = pool.copy()
    if tl_name:
        subset = subset[subset.get("tl", pd.Series(dtype=str)).fillna("(unattributed)") == tl_name]
    if sc_name:
        subset = subset[subset.get("sc", pd.Series(dtype=str)).fillna("(unattributed)") == sc_name]

    out = pd.DataFrame({
        "Lead ID": subset.get("lead_id"),
        "City": subset.get("cluster"),
        "Priority": subset.get("priority"),
        "Consecutive DNPs": pd.to_numeric(subset.get("tl_is_dnp_tasks"), errors="coerce"),
        "Last FU": subset.get("last_follow_up_date"),
        "Due": subset.get("next_follow_up_date"),
    }).reset_index(drop=True)
    return out.sort_values("Consecutive DNPs", ascending=False, na_position="last").reset_index(drop=True)


# ---- Agreed to Meet (last_follow_up_status == 'agreed_to_meet') ----

def agreed_to_meet_summary(pool: pd.DataFrame, as_of: str) -> dict:
    """Already slipped / Due today / Still ahead, by next_follow_up_date vs as_of. No drill-down per spec."""
    days_ahead = _days_from_today(as_of, pool.get("next_follow_up_date", pd.Series("", index=pool.index)))
    return {
        "already_slipped": int((days_ahead < 0).sum()),
        "due_today": int((days_ahead == 0).sum()),
        "still_ahead": int((days_ahead > 0).sum()),
        "total": len(pool),
    }


# ---- Generic template: No action possible / Will go later / Lost to Competitor / Booked / anything else ----

def funnel_generic_breakdown(pool: pd.DataFrame, as_of: str, group_col: str = "tl") -> pd.DataFrame:
    """
    <group_col>, Leads, Share, Avg FUs (mean total_followup_done), Avg days
    to last FU (mean as_of - last_follow_up_date), P1+P2 (count) -- the
    shared single-cut template for every box other than Another Follow up
    needed, DNP, and Agreed to Meet. group_col defaults to "tl"; pass
    "cluster" for the Pan-India City-level view of the same boxes.
    """
    total = len(pool)
    d = pool.copy()
    d[group_col] = d.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
    d["_fus"] = pd.to_numeric(d.get("total_followup_done"), errors="coerce").fillna(0)
    d["_days_since_last_fu"] = -_days_from_today(as_of, d.get("last_follow_up_date", pd.Series("", index=d.index)))
    priority = d.get("priority", pd.Series("", index=d.index)).astype(str).str.strip().str.upper()
    d["_is_p1p2"] = priority.isin(HIGH_PRIORITY)

    out = d.groupby(group_col).agg(
        leads=("_fus", "size"),
        avg_fus=("_fus", "mean"),
        avg_days_to_last_fu=("_days_since_last_fu", "mean"),
        p1p2=("_is_p1p2", "sum"),
    ).reset_index()

    out["share"] = (out["leads"] / total * 100).round(1) if total else 0.0
    out["avg_fus"] = out["avg_fus"].round(1)
    out["avg_days_to_last_fu"] = out["avg_days_to_last_fu"].round(1)
    out["p1p2"] = out["p1p2"].astype(int)

    out = out[[group_col, "leads", "share", "avg_fus", "avg_days_to_last_fu", "p1p2"]]
    out = out.sort_values("leads", ascending=False).reset_index(drop=True)
    return out


# ---- TL wise cut: the seven fixed boxes, grouped into Still in play / Closed out ----

FUNNEL_STILL_IN_PLAY_STATUSES = ["another_follow_up_required", "dnp", "will_go_later", "agreed_to_meet"]
FUNNEL_CLOSED_OUT_STATUSES = ["no_action_possible", "lost_to_competitor", BOOKED_SENTINEL]


def funnel_group_wise_table(df: pd.DataFrame, group_col: str,
                             filter_col: str | None = None, filter_value: str | None = None) -> pd.DataFrame:
    """
    The Follow-up funnel's 'TL wise' cut, generalised to group by TL or by
    SC (for the TL -> SC drill-down): one row per group, against the seven
    fixed boxes from the reference layout -- Still in play (Another follow
    up required / DNP / Will go later / Agreed to meet) and Closed out (No
    action possible / Lost to competitor / Booked). Any other
    last_follow_up_status value that might exist in the data (i.e. outside
    these seven, unlike the fully-dynamic 'Overall' cut) isn't part of
    either group and won't appear in this cut.

    Pass filter_col="tl", filter_value=<tl name> to scope this to one TL's
    SCs (the drill-down); omit both for the top-level TL view.

    Returns one row per group with: <group_col>, followed_up_once,
    still_in_play_total (+ _pct), another_fu (+ _pct), dnp (+ _pct),
    will_go_later (+ _pct), agreed_to_meet (+ _pct), closed_out_total
    (+ _pct), no_action_possible (+ _pct), lost_to_competitor (+ _pct),
    booked (+ _pct).

    Each _pct is that box's leads over the group's grand total across all
    seven boxes, rounded to the nearest whole percent (matching the
    reference layout, not the 1-decimal convention used elsewhere).
    followed_up_once is total_followup_done > 0 leads, over the union of
    the group's leads across all seven boxes.
    """
    all_statuses = FUNNEL_STILL_IN_PLAY_STATUSES + FUNNEL_CLOSED_OUT_STATUSES
    box_pools = {status: funnel_box_pool(df, status) for status in all_statuses}
    if filter_col and filter_value is not None:
        box_pools = {
            status: pool[pool.get(filter_col, pd.Series(dtype=str)).fillna("(unattributed)") == filter_value]
            for status, pool in box_pools.items()
        }

    groups: set[str] = set()
    for pool in box_pools.values():
        groups.update(pool.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)").unique())

    rows = []
    for group_name in sorted(groups):
        counts = {}
        followed_up = 0
        for status in all_statuses:
            pool = box_pools[status]
            group_series = pool.get(group_col, pd.Series(dtype=str)).fillna("(unattributed)")
            subset = pool[group_series == group_name]
            counts[status] = len(subset)
            fus = pd.to_numeric(subset.get("total_followup_done"), errors="coerce").fillna(0)
            followed_up += int((fus > 0).sum())

        still_total = sum(counts[s] for s in FUNNEL_STILL_IN_PLAY_STATUSES)
        closed_total = sum(counts[s] for s in FUNNEL_CLOSED_OUT_STATUSES)
        grand_total = still_total + closed_total

        def pct(n: int) -> int:
            return round(n / grand_total * 100) if grand_total else 0

        rows.append({
            group_col: group_name,
            "followed_up_once": followed_up,
            "still_in_play_total": still_total, "still_in_play_total_pct": pct(still_total),
            "another_fu": counts["another_follow_up_required"],
            "another_fu_pct": pct(counts["another_follow_up_required"]),
            "dnp": counts["dnp"], "dnp_pct": pct(counts["dnp"]),
            "will_go_later": counts["will_go_later"], "will_go_later_pct": pct(counts["will_go_later"]),
            "agreed_to_meet": counts["agreed_to_meet"], "agreed_to_meet_pct": pct(counts["agreed_to_meet"]),
            "closed_out_total": closed_total, "closed_out_total_pct": pct(closed_total),
            "no_action_possible": counts["no_action_possible"],
            "no_action_possible_pct": pct(counts["no_action_possible"]),
            "lost_to_competitor": counts["lost_to_competitor"],
            "lost_to_competitor_pct": pct(counts["lost_to_competitor"]),
            "booked": counts[BOOKED_SENTINEL], "booked_pct": pct(counts[BOOKED_SENTINEL]),
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("followed_up_once", ascending=False).reset_index(drop=True)
    return out


def funnel_tl_wise_table(df: pd.DataFrame) -> pd.DataFrame:
    return funnel_group_wise_table(df, "tl")


def funnel_sc_wise_table(df: pd.DataFrame, tl_name: str) -> pd.DataFrame:
    return funnel_group_wise_table(df, "sc", filter_col="tl", filter_value=tl_name)


# ---------------------------------------------------------------------------
# Funnel quality
# ---------------------------------------------------------------------------
#
# Same base pool as the Follow-up funnel section (funnel_pool), but with no
# separate Booked carve-out -- this section has no Booked box.
#
# Three categories, each a boolean condition over that base pool:
#   dnp              -- last_follow_up_status == 'dnp'
#   dnp_lost_nurture -- dnp OR lost_to_competitor OR "Nurture"
#   beyond_5_days    -- agreed_to_meet/another_follow_up_required leads
#                       whose (next_follow_up_date - last_follow_up_date)
#                       is more than 5 days
#
# "Nurture" = a will_go_later lead (any next_follow_up_date, including
# blank -- no day-count condition), OR a dropped_plan lead, OR a
# not_qualified_not_serviceable lead. **[FIX]** -- will_go_later used to
# only count as Nurture past 60 days out from last_follow_up_date; now
# every will_go_later lead counts, unconditionally, same as the other two
# statuses. **[FIX]** -- the third status was misspelled
# "not_qualified_not_servicable" (missing the middle "e"), which never
# matched the real last_follow_up_status value
# ("not_qualified_not_serviceable") -- those leads were silently never
# counted as Nurture. Corrected to match the actual data value.

FUNNEL_QUALITY_THRESHOLDS = {
    "dnp": 25.0,
    "dnp_lost_nurture": 45.0,
    "beyond_5_days": 50.0,
}

_NURTURE_STATUSES = ["will_go_later", "dropped_plan", "not_qualified_not_serviceable"]


def funnel_quality_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Funnel quality's base pool -- identical filter to funnel_pool, just named for this section."""
    return funnel_pool(df)


def _fq_status(pool: pd.DataFrame) -> pd.Series:
    return pool.get("last_follow_up_status", pd.Series("", index=pool.index)).astype(str).str.strip().str.lower()


def _days_next_minus_last(pool: pd.DataFrame) -> pd.Series:
    """(next_follow_up_date - last_follow_up_date), in days. NaN if either side is unparseable."""
    next_dt = pd.to_datetime(pool.get("next_follow_up_date", pd.Series("", index=pool.index)), errors="coerce")
    last_dt = pd.to_datetime(pool.get("last_follow_up_date", pd.Series("", index=pool.index)), errors="coerce")
    return (next_dt - last_dt).dt.days.astype(float)


def _fq_is_dnp(pool: pd.DataFrame) -> pd.Series:
    return _fq_status(pool) == "dnp"


def _fq_is_nurture(pool: pd.DataFrame) -> pd.Series:
    """
    A lead is Nurture if its last_follow_up_status is will_go_later,
    dropped_plan, or not_qualified_not_serviceable -- unconditionally, with
    no day-count on next_follow_up_date (see the module comment above).
    """
    return _fq_status(pool).isin(_NURTURE_STATUSES)


def _fq_is_dnp_lost_nurture(pool: pd.DataFrame) -> pd.Series:
    status = _fq_status(pool)
    return status.isin(["dnp", "lost_to_competitor"]) | _fq_is_nurture(pool)


def _fq_beyond5_eligible(pool: pd.DataFrame) -> pd.Series:
    """The 'follow-ups carrying a next date' denominator for the 3rd category."""
    return _fq_status(pool).isin(["agreed_to_meet", "another_follow_up_required"])


def _fq_is_beyond5(pool: pd.DataFrame) -> pd.Series:
    diff = _days_next_minus_last(pool)
    return _fq_beyond5_eligible(pool) & (diff > 5)


def _fq_with_outcome(pool: pd.DataFrame) -> pd.Series:
    """'With an outcome': last_follow_up_status is not blank."""
    return _fq_status(pool) != ""


# (key, label, mask_fn, basis_label, denominator_fn) -- the denominator for
# category 1/2 is "With an outcome" (non-blank last_follow_up_status)
# within the pool; category 3's is narrower still (only the leads that
# carry an eligible status at all), matching the reference layout's own
# "Basis" wording. **[FIX]** -- category 1/2's denominator used to be the
# WHOLE pool (len(pool)), including blank-status leads, even though the
# "of leads with an outcome" label text already implied only non-blank
# ones -- the label and the number now actually agree.
FUNNEL_QUALITY_CATEGORIES = [
    ("dnp", "Did not pick up", _fq_is_dnp,
     "of leads with an outcome", lambda pool: int(_fq_with_outcome(pool).sum())),
    ("dnp_lost_nurture", "Did not pick up + Lost + Nurture", _fq_is_dnp_lost_nurture,
     "of leads with an outcome", lambda pool: int(_fq_with_outcome(pool).sum())),
    ("beyond_5_days", "Another follow-up dated beyond 5 days", _fq_is_beyond5,
     "of follow-ups carrying a next date", lambda pool: int(_fq_beyond5_eligible(pool).sum())),
]


def funnel_quality_overall(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per category: key, measure, now_pct, threshold_pct,
    basis_label, basis_count, state ('Breaching' when now_pct is strictly
    greater than threshold_pct, i.e. the reading has gone past its cap;
    'Within' otherwise, including when it sits exactly on the threshold).
    """
    pool = funnel_quality_pool(df)
    rows = []
    for key, label, mask_fn, basis_label, denom_fn in FUNNEL_QUALITY_CATEGORIES:
        matched = int(mask_fn(pool).sum())
        denom = denom_fn(pool)
        now_pct = round(matched / denom * 100, 1) if denom else 0.0
        threshold_pct = FUNNEL_QUALITY_THRESHOLDS[key]
        rows.append({
            "key": key,
            "measure": label,
            "now_pct": now_pct,
            "threshold_pct": threshold_pct,
            "basis_label": basis_label,
            "basis_count": denom,
            "state": "Breaching" if now_pct > threshold_pct else "Within",
        })
    return pd.DataFrame(rows)


def funnel_quality_sc_breakdown(df: pd.DataFrame, category_key: str) -> pd.DataFrame:
    """
    SC / leads / matched_leads for one category's expander: leads is the
    SC's total funnel-quality-pool lead count, matched_leads is that SC's
    count of leads meeting the category's own condition.
    """
    pool = funnel_quality_pool(df)
    mask_fn = next(mfn for k, _, mfn, _, _ in FUNNEL_QUALITY_CATEGORIES if k == category_key)

    d = pool.copy()
    d["sc"] = d.get("sc", pd.Series(dtype=str)).fillna("(unattributed)")
    d["_match"] = mask_fn(pool)

    out = d.groupby("sc").agg(leads=("_match", "size"), matched_leads=("_match", "sum")).reset_index()
    out["matched_leads"] = out["matched_leads"].astype(int)
    out = out.sort_values("leads", ascending=False).reset_index(drop=True)
    return out


def funnel_quality_tl_wise(df: pd.DataFrame) -> pd.DataFrame:
    """
    TL wise cut (no SC drill-down): one row per TL with with_outcome (a
    raw count of leads whose last_follow_up_status isn't blank) plus each
    category's percentage for that TL (None when its denominator is 0,
    which the display layer renders as a dash, same as a literal 0%).

    dnp_pct / dnp_lost_nurture_pct are now out of that same with_outcome
    count, not the TL's whole pool -- **[FIX]**, matching the Overall
    cut's own Basis (see FUNNEL_QUALITY_CATEGORIES) so the two cuts use
    the same denominator for the same category.
    """
    pool = funnel_quality_pool(df)
    tl_series = pool.get("tl", pd.Series(dtype=str)).fillna("(unattributed)")

    rows = []
    for tl_name in sorted(tl_series.unique()):
        sub = pool[tl_series == tl_name]
        with_outcome = int(_fq_with_outcome(sub).sum())

        def pct(n: int, denom: int):
            return round(n / denom * 100, 1) if denom else None

        eligible_total = int(_fq_beyond5_eligible(sub).sum())
        rows.append({
            "tl": tl_name,
            "with_outcome": with_outcome,
            "dnp_pct": pct(int(_fq_is_dnp(sub).sum()), with_outcome),
            "dnp_lost_nurture_pct": pct(int(_fq_is_dnp_lost_nurture(sub).sum()), with_outcome),
            "beyond_5_days_pct": pct(int(_fq_is_beyond5(sub).sum()), eligible_total),
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("with_outcome", ascending=False).reset_index(drop=True)
    return out