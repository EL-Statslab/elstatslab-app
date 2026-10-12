"""
team_rankings.py

Team Rankings tab for ELSTATSLAB.
One table with every team, current regular season only, played games only.
Columns can be sorted by clicking their header.
Two views: Values and Percentiles.

All queries use the same join as the rest of the app (schedule.gamecode, played = 'true'),
so the tab reads euroleague_public.db like everything else on the site.

Usage in app.py:
    import team_rankings
    ...
    with tab_rankings:
        team_rankings.render_team_rankings(
            get_conn(),
            name_fn=lambda c: display_name(c, c),
            logo_loader=..., brand_logo=ELSTATSLAB_LOGO,
        )
"""

import math
import sqlite3

import pandas as pd
import streamlit as st

# The percentile gradient lives in the export module, so the table and the PNG share it
from team_rankings_export import VIEWS, build_team_rankings_png, pct_colors

VIEW_FILES = {name: cfg["file"] for name, cfg in VIEWS.items()}

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# key: (column label, higher is better, coloured)
# Pace describes a style, not a quality, so it is never coloured.
METRICS = {
    "net_rtg": ("NetRtg", True, True),
    "off_rtg": ("OffRtg", True, True),
    "def_rtg": ("DefRtg", False, True),
    "pace": ("Pace", True, False),
    "efg": ("eFG%", True, True),
    "tov": ("TOV%", False, True),
    "oreb": ("OREB%", True, True),
    "ftr": ("FT Rate", True, True),
    "opp_efg": ("Opp eFG%", False, True),
    "opp_tov": ("Opp TOV%", True, True),
    "dreb": ("DREB%", True, True),
    "opp_ftr": ("Opp FT Rate", False, True),
}

# Same join and filters as the other queries of the app
_GAMES_FROM = """
    FROM team_stats t
    JOIN team_stats o
      ON o.Season = t.Season AND o.GameCode = t.GameCode AND o.TeamName <> t.TeamName
    JOIN schedule s
      ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = t.GameCode
     AND s.Season = t.Season
     AND (UPPER(s.hometeam) = UPPER(t.TeamName) OR UPPER(s.awayteam) = UPPER(t.TeamName))
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _connect(conn_or_path):
    if isinstance(conn_or_path, (str, bytes)) or hasattr(conn_or_path, "__fspath__"):
        return sqlite3.connect(conn_or_path), True
    return conn_or_path, False


def _season_label(season):
    season = int(season)
    return f"{season}-{str(season + 1)[-2:]}"


def _current_season(conn):
    row = pd.read_sql_query(
        "SELECT MAX(t.Season) AS season" + _GAMES_FROM
        + " WHERE s.round = 'RS' AND s.played = 'true'",
        conn,
    )
    value = row["season"].iloc[0]
    return None if pd.isna(value) else int(value)


def _last_round(conn, season):
    """Latest round with regular season games played, for the PNG title."""
    try:
        row = pd.read_sql_query(
            "SELECT MAX(CAST(s.gameday AS INTEGER)) AS rnd" + _GAMES_FROM
            + " WHERE t.Season = ? AND s.round = 'RS' AND s.played = 'true'",
            conn,
            params=(season,),
        )
        value = row["rnd"].iloc[0]
        return None if pd.isna(value) else int(value)
    except Exception:
        return None


def percentile_scale(values, higher_is_better):
    """
    Same method as the Team Cards (PERCENT_RANK): 0 for the worst team, 100 for the
    best, equal steps in between (rank divided by number of teams minus one).
    """
    goodness = values if higher_is_better else -values
    n = goodness.notna().sum()
    if n < 2:
        return pd.Series([50.0] * len(values), index=values.index)
    ranks = goodness.rank(method="min") - 1
    return (ranks / (n - 1) * 100.0 + 0.5).apply(math.floor)


def compute_team_table(conn, season):
    """
    Returns (values, percentiles, games_played), indexed by team code.
    Ratings and pace are averages over the regular season games already played, the other
    ratios are totals over the season.
    """
    games = pd.read_sql_query(
        """
        SELECT
            CASE WHEN UPPER(s.hometeam) = UPPER(t.TeamName) THEN s.homecode
                 ELSE s.awaycode END AS code,
            t.GameCode AS gc,
            t.Off_Rtg  AS off_rtg,
            t.Def_Rtg  AS def_rtg,
            t.Pace     AS pace,
            t.OREB_Pct AS oreb, t.DREB_Pct AS dreb,
            t."2PM" AS p2m, t."2PA" AS p2a, t."3PM" AS p3m, t."3PA" AS p3a,
            t.FTA AS fta, t.Turnovers AS tov,
            o."2PM" AS o2m, o."2PA" AS o2a, o."3PM" AS o3m, o."3PA" AS o3a,
            o.FTA AS ofta, o.Turnovers AS otov
        """
        + _GAMES_FROM
        + " WHERE t.Season = ? AND s.round = 'RS' AND s.played = 'true'",
        conn,
        params=(season,),
    )
    if games.empty:
        return None, None, None

    games = games.drop_duplicates(subset=["gc", "code"])

    for c in ["off_rtg", "def_rtg", "pace", "oreb", "dreb"]:
        games[c] = pd.to_numeric(games[c], errors="coerce")
    # OREB_Pct and DREB_Pct are stored as 32.3 style, but accept 0.323 too
    for c in ["oreb", "dreb"]:
        if games[c].dropna().abs().max() <= 1.0:
            games[c] = games[c] * 100.0

    games["net_rtg"] = games["off_rtg"] - games["def_rtg"]
    games["fga"] = games["p2a"] + games["p3a"]
    games["fgm"] = games["p2m"] + games["p3m"]
    games["ofga"] = games["o2a"] + games["o3a"]
    games["ofgm"] = games["o2m"] + games["o3m"]

    grouped = games.groupby("code")
    gp = grouped["gc"].nunique()
    avg = grouped[["net_rtg", "off_rtg", "def_rtg", "pace", "oreb", "dreb"]].mean()
    tot = grouped[["fga", "fgm", "p3m", "fta", "tov", "ofga", "ofgm", "o3m", "ofta", "otov"]].sum()

    # Ratios are pooled over the season (sum over sum), like TOV% on the Team Cards
    values = avg.copy()
    values["efg"] = (tot["fgm"] + 0.5 * tot["p3m"]) / tot["fga"] * 100.0
    values["tov"] = tot["tov"] / (tot["fga"] + 0.44 * tot["fta"] + tot["tov"]) * 100.0
    values["ftr"] = tot["fta"] / tot["fga"] * 100.0
    values["opp_efg"] = (tot["ofgm"] + 0.5 * tot["o3m"]) / tot["ofga"] * 100.0
    values["opp_tov"] = tot["otov"] / (tot["ofga"] + 0.44 * tot["ofta"] + tot["otov"]) * 100.0
    values["opp_ftr"] = tot["ofta"] / tot["ofga"] * 100.0
    values = values[list(METRICS.keys())]

    pct = pd.DataFrame(index=values.index)
    for key, (_, higher_better, _) in METRICS.items():
        pct[key] = percentile_scale(values[key], higher_better)

    return values, pct, gp


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _pct_color(p):
    if pd.isna(p):
        return ""
    bg, fg = pct_colors(float(p))
    return f"background-color: {bg}; color: {fg};"


def _build_view(values, pct, gp, mode, name_fn=None):
    labels = {k: v[0] for k, v in METRICS.items()}
    shown = pct.copy() if mode == "Percentiles" else values.copy()
    shown.insert(0, "GP", gp)

    # Default order is the best NetRtg first, whichever view is shown
    order = values["net_rtg"].sort_values(ascending=False).index
    shown = shown.reindex(order)
    pct_aligned = pct.reindex(order)

    shown = shown.rename(columns=labels)
    if name_fn is not None:
        shown.index = [name_fn(c) for c in shown.index]
    shown.index.name = "Team"

    css = pd.DataFrame("", index=shown.index, columns=shown.columns)
    for key, (label, _, is_colored) in METRICS.items():
        if is_colored:
            css[label] = pct_aligned[key].map(_pct_color).values

    fmt = {"GP": "{:.0f}"}
    for key, (label, _, _) in METRICS.items():
        fmt[label] = "{:.0f}" if mode == "Percentiles" else "{:.1f}"

    styler = shown.style.apply(lambda _: css, axis=None).format(fmt)
    return styler, len(shown)


def render_team_rankings(conn_or_path, name_fn=None, logo_loader=None, brand_logo=None):
    """
    name_fn: team code in, display name out.
    logo_loader: team code in, PIL image out (optional, team logos in the PNG).
    brand_logo: optional path of the ELSTATSLAB logo, shown in the PNG header.
    """
    conn, opened_here = _connect(conn_or_path)
    try:
        season = _current_season(conn)
        if season is None:
            st.info("No regular season games available yet.")
            return

        values, pct, gp = compute_team_table(conn, season)
        if values is None:
            st.info("No regular season games available yet.")
            return
        last_round = _last_round(conn, season)
    finally:
        if opened_here:
            conn.close()

    st.subheader("Team Rankings")
    st.caption(
        f"EuroLeague {_season_label(season)} regular season, games played so far. "
        "Click any column header to sort."
    )

    mode = st.radio(
        "View",
        ["Values", "Percentiles"],
        horizontal=True,
        label_visibility="collapsed",
        key="team_rankings_view",
    )

    styler, n_rows = _build_view(values, pct, gp, mode, name_fn)
    st.dataframe(styler, use_container_width=True, height=35 * (n_rows + 1) + 3)

    round_part = last_round if last_round else "x"
    for view, button_label, key in (
        ("ratings", "Download PNG", "team_rankings_png"),
        ("four_factors", "Download PNG (Four Factors)", "team_rankings_png_ff"),
    ):
        try:
            png = build_team_rankings_png(
                values, pct, gp, _season_label(season),
                f"Round {last_round}" if last_round else None,
                names=name_fn,
                brand_logo=brand_logo,
                logo_loader=logo_loader,
                view=view,
            )
            st.download_button(
                button_label,
                data=png,
                file_name=f"{VIEW_FILES[view]}_round_{round_part}.png",
                mime="image/png",
                key=key,
            )
        except Exception:
            pass

    st.caption(
        "Colours follow the percentile among all teams, green is better and red is worse. "
        "For DefRtg, TOV%, Opp eFG% and Opp FT Rate a lower value is better. "
        "Pace is not coloured because it describes a style. "
        "Percentiles are calculated the same way as on the Team Cards."
    )
