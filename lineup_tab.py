"""
ELSTATSLAB Lineup tab (v1, five man units).

Reads lineup_stints from euroleague_public.db (built by build_lineups.py,
copied by build_public_db.py). Shows the best and worst five man unit of a
team by NetRtg, a sortable table of every qualifying unit, and a PNG export.

Needs lineup_export.py in the same folder.
"""

import json
import math
from pathlib import Path

import pandas as pd
import streamlit as st
from matplotlib.colors import LinearSegmentedColormap

from lineup_export import build_lineup_png

# Thresholds, adjust here
MIN_MINUTES_DEFAULT = 5.0   # season and rounds scopes (slider default)
MIN_MINUTES_MATCH = 3.0     # single match, same as the Game Flow Best 5
MIN_POSS_EACH = 10.0        # minimum possessions on each side (season and rounds)

NAVY = "#14213D"
ORANGE = "#E4572E"
POS_TXT = "#1E7A37"
NEG_TXT = "#B02A28"

_CMAP = LinearSegmentedColormap.from_list("lu_net", ["#F2B8B5", "#FBF8F1", "#B7E1BF"])

_CSS = """
<style>
.lu-card{background:#FBF8F1;border:1px solid #E1D8C6;border-radius:20px;padding:18px 24px 20px 24px;}
.lu-k{font-size:1rem;font-weight:700;letter-spacing:.16em;color:#6B7280;}
.lu-net{font-size:4.4rem;font-weight:700;line-height:1;margin-top:2px;}
.lu-nk{font-size:.95rem;font-weight:600;letter-spacing:.12em;color:#6B7280;margin-bottom:10px;}
.lu-p{display:flex;align-items:center;gap:10px;font-size:1.35rem;font-weight:600;color:#14213D;padding:6px 0;border-bottom:1px solid #E1D8C6;}
.lu-dot{width:10px;height:10px;border-radius:50%;display:inline-block;}
.lu-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:12px;padding-top:10px;border-top:2px solid #14213D;}
.lu-sk{font-size:.8rem;font-weight:700;letter-spacing:.12em;color:#6B7280;}
.lu-sv{font-size:1.4rem;font-weight:700;color:#14213D;}
</style>
"""


def _season_label(s) -> str:
    s = int(s)
    return f"{s}-{str(s + 1)[-2:]}"


def _pretty_name(raw: str) -> str:
    """Capital after a hyphen and after Mc (Miller-mcintyre -> Miller-McIntyre)."""
    out = []
    for tok in raw.split(" "):
        parts = []
        for p in tok.split("-"):
            if len(p) > 1 and p[0].isalpha():
                p = p[0].upper() + p[1:]
            if p.startswith("Mc") and len(p) > 2:
                p = "Mc" + p[2].upper() + p[3:]
            parts.append(p)
        out.append("-".join(parts))
    return " ".join(out)


@st.cache_data(ttl=600, show_spinner=False)
def _load_available(_conn) -> pd.DataFrame:
    return pd.read_sql("SELECT DISTINCT season, team_code FROM lineup_stints", _conn)


@st.cache_data(ttl=600, show_spinner=False)
def _load_team_lineups(_conn, season: int, team: str) -> pd.DataFrame:
    q = """
        SELECT gamecode, gameday, round, team_code, player_ids, player_names,
               sec, pts_for, pts_against, poss_off, poss_def
        FROM lineup_stints
        WHERE season = ? AND team_code = ?
    """
    return pd.read_sql(q, _conn, params=(int(season), str(team)))


@st.cache_data(ttl=600, show_spinner=False)
def _load_schedule(_conn, season: int) -> pd.DataFrame:
    q = "SELECT game_number, gameday, homecode, awaycode FROM schedule WHERE Season = ?"
    return pd.read_sql(q, _conn, params=(int(season),))


def _match_labels(conn, season, games, name_fn) -> dict:
    labels = {}
    try:
        sched = _load_schedule(conn, season).drop_duplicates("game_number").set_index("game_number")
    except Exception:
        sched = None
    for gc, _gd in games:
        txt = f"Game {int(gc)}"
        if sched is not None and gc in sched.index:
            r = sched.loc[gc]
            txt = (f"Round {int(r['gameday'])}: "
                   f"{name_fn(r['homecode'])} vs {name_fn(r['awaycode'])}")
        labels[int(gc)] = txt
    return labels


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    g = (df.groupby(["player_ids", "player_names"], as_index=False)
           .agg(gp=("gamecode", "nunique"), sec=("sec", "sum"),
                pf=("pts_for", "sum"), pa=("pts_against", "sum"),
                poff=("poss_off", "sum"), pdef=("poss_def", "sum")))
    g["minutes"] = g["sec"] / 60.0
    g = g[(g["poff"] > 0) & (g["pdef"] > 0)].copy()
    g["ortg"] = 100.0 * g["pf"] / g["poff"]
    g["drtg"] = 100.0 * g["pa"] / g["pdef"]
    g["netrtg"] = g["ortg"] - g["drtg"]
    return g.reset_index(drop=True)


def _row(r) -> dict:
    return {
        "players": [_pretty_name(p) for p in str(r["player_names"]).split("|")],
        "net": round(float(r["netrtg"]), 1),
        "ortg": round(float(r["ortg"]), 1),
        "drtg": round(float(r["drtg"]), 1),
        "minutes": round(float(r["minutes"]), 1),
        "gp": int(r["gp"]),
    }


def _card_html(kicker: str, accent: str, r: dict) -> str:
    col = POS_TXT if r["net"] >= 0 else NEG_TXT
    players = "".join(
        f"<div class='lu-p'><span class='lu-dot' style='background:{accent}'></span>{p}</div>"
        for p in r["players"]
    )
    stats = "".join(
        f"<div><div class='lu-sk'>{k}</div><div class='lu-sv'>{v}</div></div>"
        for k, v in (("ORTG", f"{r['ortg']:.1f}"), ("DRTG", f"{r['drtg']:.1f}"),
                     ("MIN", f"{r['minutes']:.1f}"), ("GP", f"{r['gp']}"))
    )
    return (f"<div class='lu-card' style='border-top:6px solid {accent}'>"
            f"<div class='lu-k'>{kicker}</div>"
            f"<div class='lu-net' style='color:{col}'>{r['net']:+.1f}</div>"
            f"<div class='lu-nk'>NET RATING</div>"
            f"{players}<div class='lu-stats'>{stats}</div></div>")


def _show_df(obj, n_rows: int):
    height = int(min(38 * (n_rows + 1) + 3, 640))
    cfg = {"Lineup": st.column_config.TextColumn("Lineup", width="large")}
    try:
        st.dataframe(obj, hide_index=True, width="stretch", height=height, column_config=cfg)
    except Exception:
        st.dataframe(obj, hide_index=True, use_container_width=True, height=height, column_config=cfg)


@st.cache_data(show_spinner=False)
def _cached_png(payload_json: str) -> bytes:
    return build_lineup_png(json.loads(payload_json))


def render_lineup_tab(conn, seasons, name_fn, elstatslab_logo, team_logo_fn):
    st.markdown(_CSS, unsafe_allow_html=True)

    try:
        avail = _load_available(conn)
    except Exception:
        st.info("Lineup data is not available yet.")
        return
    if avail.empty:
        st.info("Lineup data is not available yet.")
        return

    present = set(int(s) for s in avail["season"].unique())
    season_opts = [int(s) for s in seasons if int(s) in present] or sorted(present, reverse=True)

    c1, c2, c3 = st.columns([1, 2, 2])
    season = c1.selectbox("Season", season_opts, format_func=_season_label, key="lu_season")
    team_codes = sorted(avail[avail["season"] == season]["team_code"].unique(),
                        key=lambda c: name_fn(c))
    team = c2.selectbox("Team", team_codes, format_func=name_fn, key="lu_team")
    scope = c3.radio("Scope", ["Season", "Rounds", "Match"], horizontal=True, key="lu_scope")

    df = _load_team_lineups(conn, int(season), str(team))
    if df.empty:
        st.info("No lineup data for this team yet.")
        return

    season_txt = f"Season {_season_label(season)}"
    scope_label = season_txt

    if scope == "Rounds":
        gamedays = sorted(int(g) for g in df["gameday"].dropna().unique())
        if len(gamedays) >= 2:
            lo, hi = st.select_slider("Rounds", options=gamedays,
                                      value=(gamedays[0], gamedays[-1]), key="lu_rounds")
            df = df[df["gameday"].between(lo, hi)]
            scope_label = f"{season_txt} | " + (f"Round {lo}" if lo == hi else f"Rounds {lo} to {hi}")
        elif len(gamedays) == 1:
            scope_label = f"{season_txt} | Round {gamedays[0]}"
    elif scope == "Match":
        games = (df[["gamecode", "gameday"]].drop_duplicates()
                 .sort_values("gameday", na_position="last").values.tolist())
        labels = _match_labels(conn, season, games, name_fn)
        if not labels:
            st.info("No match available.")
            return
        gc = st.selectbox("Match", list(labels.keys()), format_func=labels.get, key="lu_match")
        df = df[df["gamecode"] == gc]
        scope_label = f"{season_txt} | {labels[gc]}"

    agg = _aggregate(df)
    if agg.empty:
        st.info("No lineup with enough data for this selection.")
        return

    default_min = MIN_MINUTES_MATCH if scope == "Match" else MIN_MINUTES_DEFAULT
    max_min = float(max(10.0, math.ceil(float(agg["minutes"].max()))))
    min_minutes = st.slider("Minimum minutes together", 1.0, max_min,
                            float(min(default_min, max_min)), 0.5, key=f"lu_min_{scope}")
    min_poss = 0.0 if scope == "Match" else MIN_POSS_EACH

    f = agg[(agg["minutes"] >= min_minutes) & (agg["poff"] >= min_poss) & (agg["pdef"] >= min_poss)]
    f = f.sort_values("netrtg", ascending=False).reset_index(drop=True)

    if f.empty:
        st.info("No five man unit meets the minimum yet. Lower the minimum minutes, "
                "or come back as the season goes.")
        return

    best = _row(f.iloc[0])
    worst = _row(f.iloc[-1]) if len(f) > 1 else None

    col_b, col_w = st.columns(2)
    col_b.markdown(_card_html("BEST 5", NAVY, best), unsafe_allow_html=True)
    if worst is not None:
        col_w.markdown(_card_html("WORST 5", ORANGE, worst), unsafe_allow_html=True)
    else:
        col_w.info("Only one lineup meets the minimum, so there is no separate worst 5.")

    # PNG export
    try:
        logo = team_logo_fn(team)
        brand = Path(elstatslab_logo) if elstatslab_logo else None
        payload = {
            "team_name": name_fn(team),
            "scope": scope_label,
            "note": f"Best and worst 5 by NetRtg, min {min_minutes:g} min together",
            "best": best,
            "worst": worst,
            "team_logo": str(logo) if logo else "",
            "brand_logo": str(brand) if brand and brand.exists() else "",
        }
        png = _cached_png(json.dumps(payload, sort_keys=True))
        st.download_button("Download PNG", data=png,
                           file_name=f"lineup_{team}_{season}.png", mime="image/png",
                           key="lu_dl")
    except Exception as e:
        st.error(f"Export error: {e}")

    # Sortable table
    st.markdown("#### All five man units")
    tbl = pd.DataFrame({
        "Lineup": f["player_names"].apply(
            lambda s: ", ".join(_pretty_name(p) for p in str(s).split("|"))),
        "GP": f["gp"].astype(int),
        "MIN": f["minutes"].round(1),
        "OFF POSS": f["poff"].round(1),
        "DEF POSS": f["pdef"].round(1),
        "ORTG": f["ortg"].round(1),
        "DRTG": f["drtg"].round(1),
        "NETRTG": f["netrtg"].round(1),
    })
    m = max(float(tbl["NETRTG"].abs().max()), 1.0)
    sty = (tbl.style
           .format({"MIN": "{:.1f}", "OFF POSS": "{:.1f}", "DEF POSS": "{:.1f}",
                    "ORTG": "{:.1f}", "DRTG": "{:.1f}", "NETRTG": "{:+.1f}"})
           .background_gradient(subset=["NETRTG"], cmap=_CMAP, vmin=-m, vmax=m))
    _show_df(sty, len(tbl))
    st.caption(
        "Click a column header to sort. NetRtg is ORTG minus DRTG, per 100 possessions. "
        f"Shown: at least {min_minutes:g} minutes together"
        + ("" if scope == "Match" else f" and {min_poss:g} possessions on each side")
        + ". Small samples swing a lot, read them with care."
    )
