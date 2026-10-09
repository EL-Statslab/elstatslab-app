"""
ELSTATSLAB Lineup tab (v2: five man units, trios, duos, duo matrix).

Reads lineup_stints from euroleague_public.db (built by build_lineups.py,
copied by build_public_db.py). Shows the best and worst unit of a team by
NetRtg, a sortable table of every qualifying unit, a duo matrix, and a PNG
export. Trios and duos are built from the five man units: every unit on the
floor contains 10 duos and 10 trios, so their minutes and points add up.

Needs lineup_export.py in the same folder.
"""

import json
import math
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from matplotlib.colors import LinearSegmentedColormap

from lineup_export import build_lineup_png, build_match_lineups_png

# Thresholds, adjust here
MIN_MINUTES_DEFAULT = 5.0   # season and rounds scopes (slider default, five man units)
MIN_MINUTES_MATCH = 3.0     # single match, same as the Game Flow Best 5
MIN_POSS_EACH = 10.0        # minimum possessions on each side (season and rounds)
MIN_POSS_MATCH = 6.0        # single match, same value as MIN_LINEUP_POSS in Gameflow_generator.py
# Matches played before the time and possession fixes keep their published numbers in the
# Match Center section (old seconds in sec_v1, no possession minimum). The Lineup tab
# always uses the corrected seconds. {season: last gamecode kept as published}
LEGACY_UP_TO = {2026: 37}
# Trios and duos play much longer together: default minutes = minutes per game
# of the scope times the number of games, never below the base above.
MIN_PER_GAME = {5: 0.0, 3: 6.0, 2: 10.0}
LOW_SAMPLE = " (LOW SAMPLE)"   # added to BEST 5 / WORST 5 when no five clears the possession minimum
SINGLE_KICKER = "ONLY QUALIFYING 5"   # shown instead of BEST 5 when one unit only clears the minimum
MATRIX_PLAYERS = 10         # players shown in the duo matrix (most minutes)

NAVY = "#14213D"
ORANGE = "#E4572E"
POS_TXT = "#1E7A37"
NEG_TXT = "#B02A28"

_CMAP = LinearSegmentedColormap.from_list("lu_net", ["#F2B8B5", "#FBF8F1", "#B7E1BF"])

UNITS = {"5 man": 5, "Trio": 3, "Duo": 2}
LABELS = {
    5: {"best": "BEST 5", "worst": "WORST 5", "plural": "five man units", "title": "5"},
    3: {"best": "BEST TRIO", "worst": "WORST TRIO", "plural": "trios", "title": "TRIO"},
    2: {"best": "BEST DUO", "worst": "WORST DUO", "plural": "duos", "title": "DUO"},
}

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
.lu-li{display:flex;align-items:center;gap:12px;background:#FBF8F1;border:1px solid #E1D8C6;border-radius:14px;padding:10px 12px;margin-bottom:8px;}
.lu-rk{font-size:.9rem;font-weight:700;color:#6B7280;min-width:1.6rem;text-align:center;}
.lu-bd{flex:1;min-width:0;}
.lu-pl{font-size:1rem;font-weight:700;color:#14213D;line-height:1.25;overflow-wrap:anywhere;}
.lu-ms{font-size:.8rem;font-weight:600;color:#6B7280;margin-top:3px;letter-spacing:.02em;}
.lu-nb{min-width:4.2rem;text-align:center;border-radius:10px;padding:6px 4px;}
.lu-nv{font-size:1.45rem;font-weight:700;line-height:1;}
.lu-nl{font-size:.65rem;font-weight:700;letter-spacing:.1em;color:#6B7280;margin-top:2px;}
/* Desktop shows the sortable table, phones show the compact list */
@media (min-width: 641px){ .st-key-lu_list{display:none !important;} }
@media (max-width: 640px){
  .st-key-lu_tbl{display:none !important;}
  .lu-card{padding:14px 16px 16px 16px;margin-bottom:10px;}
  .lu-net{font-size:3.4rem;}
  .lu-p{font-size:1.1rem;}
  .lu-sv{font-size:1.15rem;}
}
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


def _short_name(raw: str) -> str:
    pretty = _pretty_name(raw)
    parts = pretty.split(" ", 1)
    return parts[1] if len(parts) == 2 else pretty


@st.cache_data(ttl=600, show_spinner=False)
def _load_available(_conn) -> pd.DataFrame:
    return pd.read_sql("SELECT DISTINCT season, team_code FROM lineup_stints", _conn)


@st.cache_data(ttl=600, show_spinner=False)
def _load_team_lineups(_conn, season: int, team: str) -> pd.DataFrame:
    q = """
        SELECT gamecode, gameday, round, team_code, player_ids, player_names,
               sec, pts_for, pts_against, poss_off, poss_def, sec_v1
        FROM lineup_stints
        WHERE season = ? AND team_code = ?
    """
    try:
        df = pd.read_sql(q, _conn, params=(int(season), str(team)))
    except Exception:       # table built before the sec_v1 column existed
        df = pd.read_sql(q.replace(", sec_v1", ""), _conn, params=(int(season), str(team)))
        df["sec_v1"] = df["sec"]
    df["sec_v1"] = df["sec_v1"].fillna(df["sec"])
    return df


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


def _finish(g: pd.DataFrame) -> pd.DataFrame:
    """Adds minutes, ORTG, DRTG and NetRtg to an aggregated table."""
    if g.empty:
        return g
    g = g.copy()
    g["minutes"] = g["sec"] / 60.0
    g = g[(g["poff"] > 0) & (g["pdef"] > 0)].copy()
    g["ortg"] = 100.0 * g["pf"] / g["poff"]
    g["drtg"] = 100.0 * g["pa"] / g["pdef"]
    g["netrtg"] = g["ortg"] - g["drtg"]
    return g.reset_index(drop=True)


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Five man units, cumulated over the games of the selection."""
    if df.empty:
        return df
    g = (df.groupby(["player_ids", "player_names"], as_index=False)
           .agg(gp=("gamecode", "nunique"), sec=("sec", "sum"),
                pf=("pts_for", "sum"), pa=("pts_against", "sum"),
                poff=("poss_off", "sum"), pdef=("poss_def", "sum")))
    return _finish(g)


def _combo_table(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """
    Groups of k players (1, 2 or 3), from the five man units. Each unit on the
    floor contributes its seconds, points and possessions to every group of k
    players it contains. Player ids are sorted so a group has a single key.
    """
    acc = {}
    for r in df.itertuples(index=False):
        ids = str(r.player_ids).split("|")
        names = str(r.player_names).split("|")
        if len(ids) != 5 or len(names) != 5:
            continue
        pairs = sorted(zip(ids, names))
        for combo in combinations(pairs, k):
            key = tuple(c[0] for c in combo)
            a = acc.get(key)
            if a is None:
                a = acc[key] = {"names": [c[1] for c in combo], "games": set(),
                                "sec": 0.0, "pf": 0.0, "pa": 0.0, "poff": 0.0, "pdef": 0.0}
            a["games"].add(r.gamecode)
            a["sec"] += float(r.sec)
            a["pf"] += float(r.pts_for)
            a["pa"] += float(r.pts_against)
            a["poff"] += float(r.poss_off)
            a["pdef"] += float(r.poss_def)
    rows = [{"player_ids": "|".join(key), "player_names": "|".join(a["names"]),
             "gp": len(a["games"]), "sec": a["sec"], "pf": a["pf"], "pa": a["pa"],
             "poff": a["poff"], "pdef": a["pdef"]} for key, a in acc.items()]
    return pd.DataFrame(rows, columns=["player_ids", "player_names", "gp", "sec",
                                       "pf", "pa", "poff", "pdef"])


def _row(r, short: bool = False) -> dict:
    conv = _short_name if short else _pretty_name
    return {
        "players": [conv(p) for p in str(r["player_names"]).split("|")],
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


def _list_html(fs: pd.DataFrame) -> str:
    """Compact list for phones: surnames on top, small stats line, NetRtg badge."""
    out = []
    for i, r in enumerate(fs.itertuples(index=False), start=1):
        names = ", ".join(_short_name(p) for p in str(r.player_names).split("|"))
        net = float(r.netrtg)
        col = POS_TXT if net >= 0 else NEG_TXT
        bg = "#DDF0E1" if net >= 0 else "#F8DCDA"
        out.append(
            "<div class='lu-li'>"
            f"<div class='lu-rk'>{i}</div>"
            f"<div class='lu-bd'><div class='lu-pl'>{names}</div>"
            f"<div class='lu-ms'>{r.minutes:.1f} MIN &middot; ORTG {r.ortg:.1f} &middot; "
            f"DRTG {r.drtg:.1f} &middot; {int(r.gp)} GP</div></div>"
            f"<div class='lu-nb' style='background:{bg}'>"
            f"<div class='lu-nv' style='color:{col}'>{net:+.1f}</div>"
            "<div class='lu-nl'>NET</div></div>"
            "</div>"
        )
    return "".join(out)


def _show_df(obj, n_rows: int, hide_index: bool = True, cfg=None):
    height = int(min(38 * (n_rows + 1) + 3, 640))
    kwargs = {"hide_index": hide_index, "height": height}
    if cfg:
        kwargs["column_config"] = cfg
    try:
        st.dataframe(obj, width="stretch", **kwargs)
    except Exception:
        st.dataframe(obj, use_container_width=True, **kwargs)


@st.cache_data(show_spinner=False)
def _cached_png(payload_json: str) -> bytes:
    return build_lineup_png(json.loads(payload_json))


def _render_matrix(df: pd.DataFrame, f: pd.DataFrame):
    """Duo matrix: NetRtg of each pair among the players with the most minutes."""
    singles = _combo_table(df, 1)
    if singles.empty:
        return
    top = singles.sort_values("sec", ascending=False).head(MATRIX_PLAYERS)
    ids = top["player_ids"].tolist()
    labels = [_short_name(n) for n in top["player_names"]]
    if len(set(labels)) < len(labels):
        labels = [_pretty_name(n) for n in top["player_names"]]

    net = {r.player_ids: float(r.netrtg) for r in f.itertuples(index=False)}
    data = []
    for a in ids:
        data.append([np.nan if a == b else net.get("|".join(sorted((a, b))), np.nan)
                     for b in ids])
    mat = pd.DataFrame(data, index=labels, columns=labels)
    if int(mat.notna().sum().sum()) == 0:
        st.caption("No pair among these players meets the minimum yet.")
        return
    m = max(float(np.nanmax(np.abs(mat.values))), 1.0)
    sty = (mat.style.format("{:+.1f}", na_rep="")
           .background_gradient(cmap=_CMAP, vmin=-m, vmax=m, axis=None))
    _show_df(sty, len(mat), hide_index=False)
    st.caption(
        f"The {len(ids)} players with the most minutes. A cell appears when the pair meets "
        "the minimum shown above. NetRtg of the two players on the floor together."
    )


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

    c1, c2, c3, c4 = st.columns([1, 2, 2, 2])
    season = c1.selectbox("Season", season_opts, format_func=_season_label, key="lu_season")
    team_codes = sorted(avail[avail["season"] == season]["team_code"].unique(),
                        key=lambda c: name_fn(c))
    team = c2.selectbox("Team", team_codes, format_func=name_fn, key="lu_team")
    scope = c3.radio("Scope", ["Season", "Rounds", "Match"], horizontal=True, key="lu_scope")
    unit_name = c4.radio("Unit", list(UNITS.keys()), horizontal=True, key="lu_unit")
    k = UNITS[unit_name]
    lab = LABELS[k]

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

    n_games = int(df["gamecode"].nunique())
    st.caption(f"{name_fn(team)}: {n_games} game{'s' if n_games != 1 else ''} in this selection. "
               "GP in the table is the number of games the unit played together.")
    agg = _aggregate(df) if k == 5 else _finish(_combo_table(df, k))
    if agg.empty:
        st.info("No lineup with enough data for this selection.")
        return

    base_min = MIN_MINUTES_MATCH if scope == "Match" else MIN_MINUTES_DEFAULT
    default_min = max(base_min, MIN_PER_GAME[k] * n_games)
    max_min = float(max(10.0, math.ceil(float(agg["minutes"].max()))))
    min_minutes = st.slider("Minimum minutes together", 1.0, max_min,
                            float(min(default_min, max_min)), 0.5,
                            key=f"lu_min_{scope}_{k}_{n_games}")
    min_poss = MIN_POSS_MATCH if scope == "Match" else MIN_POSS_EACH

    f = agg[(agg["minutes"] >= min_minutes) & (agg["poff"] >= min_poss) & (agg["pdef"] >= min_poss)]
    f = f.sort_values("netrtg", ascending=False).reset_index(drop=True)

    if f.empty:
        st.info(f"No {lab['plural'][:-1] if lab['plural'].endswith('s') else lab['plural']} "
                "meets the minimum yet. Lower the minimum minutes, or come back as the season goes.")
        return

    best = _row(f.iloc[0])
    worst = _row(f.iloc[-1]) if len(f) > 1 else None

    col_b, col_w = st.columns(2)
    col_b.markdown(_card_html(lab["best"], NAVY, best), unsafe_allow_html=True)
    if worst is not None:
        col_w.markdown(_card_html(lab["worst"], ORANGE, worst), unsafe_allow_html=True)
    else:
        col_w.info("Only one unit meets the minimum, so there is no separate worst one.")

    # PNG export
    try:
        logo = team_logo_fn(team)
        brand = Path(elstatslab_logo) if elstatslab_logo else None
        payload = {
            "team_name": name_fn(team),
            "scope": scope_label,
            "heading": f"BEST AND WORST {lab['title']}",
            "kick_best": lab["best"],
            "kick_worst": lab["worst"],
            "note": f"By NetRtg, min {min_minutes:g} min together",
            "best": best,
            "worst": worst,
            "team_logo": str(logo) if logo else "",
            "brand_logo": str(brand) if brand and brand.exists() else "",
        }
        png = _cached_png(json.dumps(payload, sort_keys=True))
        st.download_button("Download PNG", data=png,
                           file_name=f"lineup_{unit_name.replace(' ', '')}_{team}_{season}.png",
                           mime="image/png", key="lu_dl")
    except Exception as e:
        st.error(f"Export error: {e}")

    # Sortable table
    st.markdown(f"#### All {lab['plural']}")
    tbl = pd.DataFrame({
        "Players": f["player_names"].apply(
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
    try:
        players_cfg = st.column_config.TextColumn("Players", width="large", pinned=True)
    except TypeError:          # older Streamlit without "pinned"
        players_cfg = st.column_config.TextColumn("Players", width="large")
    cfg = {
        "Players": players_cfg,
        "GP": st.column_config.NumberColumn("GP", width="small"),
        "MIN": st.column_config.NumberColumn("MIN", width="small"),
        "OFF POSS": st.column_config.NumberColumn("OFF POSS", width="small"),
        "DEF POSS": st.column_config.NumberColumn("DEF POSS", width="small"),
        "ORTG": st.column_config.NumberColumn("ORTG", width="small"),
        "DRTG": st.column_config.NumberColumn("DRTG", width="small"),
        "NETRTG": st.column_config.NumberColumn("NETRTG", width="small"),
    }
    try:
        tbl_box = st.container(key="lu_tbl")
        list_box = st.container(key="lu_list")
        keyed = True
    except TypeError:          # older Streamlit: no keyed containers, table only
        tbl_box, list_box, keyed = st.container(), None, False

    with tbl_box:
        _show_df(sty, len(tbl), cfg=cfg)

    if keyed:
        with list_box:
            sort_opts = {"NETRTG": ("netrtg", False), "MIN": ("minutes", False),
                         "ORTG": ("ortg", False), "DRTG": ("drtg", True),
                         "GP": ("gp", False)}
            s1, s2 = st.columns([3, 2])
            sort_key = s1.selectbox("Sort by", list(sort_opts.keys()), key="lu_sort_key")
            order = s2.radio("Order", ["Best first", "Worst first"], horizontal=True,
                             key="lu_sort_dir")
            col_name, low_is_best = sort_opts[sort_key]
            asc = low_is_best if order == "Best first" else (not low_is_best)
            fs = f.sort_values(col_name, ascending=asc).reset_index(drop=True)
            shown = fs.head(30)
            st.markdown(_list_html(shown), unsafe_allow_html=True)
            if len(fs) > len(shown):
                st.caption(f"Showing {len(shown)} of {len(fs)}. Use the table on a larger screen to see all.")
    st.caption(
        "Click a column header to sort. NetRtg is ORTG minus DRTG, per 100 possessions. "
        f"Shown: at least {min_minutes:g} minutes together"
        + f" and {min_poss:g} possessions on each side"
        + ". Small samples swing a lot, read them with care."
    )

    # Duo matrix
    if k == 2:
        st.markdown("#### Duo matrix")
        _render_matrix(df, f)


# ---------------------------------------------------------------------------
# Match Center section: both teams of one played match
# ---------------------------------------------------------------------------
def _match_units(df: pd.DataFrame, short: bool, legacy: bool = False) -> dict:
    """
    Best and worst 5, best trio and best duo of one team in one match.
    legacy=True reproduces what was published before the fixes: old seconds
    (sec_v1) and no possession minimum.
    """
    out = {"best5": None, "worst5": None, "trio": None, "duo": None}
    if df.empty:
        return out
    min_poss = 0.0 if legacy else MIN_POSS_MATCH
    if legacy:
        df = df.assign(sec=df["sec_v1"])
    for key, k in (("5", 5), ("trio", 3), ("duo", 2)):
        agg = _aggregate(df) if k == 5 else _finish(_combo_table(df, k))
        if agg.empty:
            continue
        need = max(MIN_MINUTES_MATCH, MIN_PER_GAME[k])
        f = (agg[(agg["minutes"] >= need) & (agg["poff"] >= min_poss)
                 & (agg["pdef"] >= min_poss)]
             .sort_values(["netrtg", "minutes"], ascending=[False, False])
             .reset_index(drop=True))
        relaxed = False
        if f.empty and k == 5 and min_poss > 0:
            # no five clears the possession minimum: keep the minutes rule only, flagged LOW SAMPLE
            f = (agg[agg["minutes"] >= need]
                 .sort_values(["netrtg", "minutes"], ascending=[False, False])
                 .reset_index(drop=True))
            relaxed = True
        if f.empty:
            continue
        if k == 5:
            out["best5"] = _row(f.iloc[0], short)
            if len(f) > 1:
                out["worst5"] = _row(f.iloc[-1], short)
            else:
                # a single unit cleared the minimum: it is neither the best nor the worst
                out["best5"]["single"] = True
            if relaxed:
                for _r in (out["best5"], out["worst5"]):
                    if _r:
                        _r["relaxed"] = True
        else:
            out[key] = _row(f.iloc[0], short)
    return out


def _kicker5(base: str, row: dict) -> str:
    if row.get("single"):
        return SINGLE_KICKER
    return base + (LOW_SAMPLE if row.get("relaxed") else "")


def _mini_html(kicker: str, r: dict, accent: str) -> str:
    col = POS_TXT if r["net"] >= 0 else NEG_TXT
    names = ", ".join(r["players"])
    return (f"<div class='lu-li' style='border-left:6px solid {accent}'>"
            f"<div class='lu-bd'><div class='lu-ms' style='margin:0 0 3px 0'>{kicker}</div>"
            f"<div class='lu-pl'>{names}</div>"
            f"<div class='lu-ms'>{r['minutes']:.1f} MIN &middot; ORTG {r['ortg']:.1f} &middot; "
            f"DRTG {r['drtg']:.1f}</div></div>"
            f"<div class='lu-nb' style='background:{'#DDF0E1' if r['net'] >= 0 else '#F8DCDA'}'>"
            f"<div class='lu-nv' style='color:{col}'>{r['net']:+.1f}</div>"
            "<div class='lu-nl'>NET</div></div></div>")


def render_match_lineups(conn, season, game_code, home_code, away_code,
                         home_disp, away_disp, round_label, card_index,
                         elstatslab_logo, team_logo_fn, logo_zoom_fn=None):
    """Expander for the Match Center: best and worst 5, trio and duo of both teams."""
    with st.expander("\U0001F9E9 Lineups \u2014 Best and worst units by NetRtg"):
        st.markdown(_CSS, unsafe_allow_html=True)
        try:
            data = {}
            for code in (home_code, away_code):
                full = _load_team_lineups(conn, int(season), str(code))
                data[code] = full[full["gamecode"] == int(game_code)]
        except Exception:
            st.info("Lineup data is not available yet.")
            return
        if all(d.empty for d in data.values()):
            st.info("No lineup data for this game yet.")
            return

        legacy = int(game_code) <= LEGACY_UP_TO.get(int(season), 0)
        poss_txt = ("" if legacy else
                    f", and at least {MIN_POSS_MATCH:g} possessions on each side")
        st.caption("Units by NetRtg (ORTG minus DRTG per 100 possessions). Minimum "
                   f"{MIN_MINUTES_MATCH:g} min together for a five man unit, "
                   f"{MIN_PER_GAME[3]:g} for a trio and {MIN_PER_GAME[2]:g} for a duo"
                   f"{poss_txt}. If no five clears the possession minimum, fives with {MIN_MINUTES_MATCH:g} minutes "
                   "or more are shown and marked LOW SAMPLE. A single match is a small sample, read it with care.")

        cols = st.columns(2)
        for col, code, accent in ((cols[0], home_code, NAVY), (cols[1], away_code, ORANGE)):
            units = _match_units(data[code], short=False, legacy=legacy)
            with col:
                st.markdown(f"**{home_disp if code == home_code else away_disp}**")
                if units["best5"]:
                    _k5 = _kicker5("BEST 5", units["best5"])
                    st.markdown(_card_html(_k5, accent, units["best5"]), unsafe_allow_html=True)
                else:
                    st.caption(f"No five man unit played {MIN_MINUTES_MATCH:g} minutes together.")
                if units["worst5"]:
                    st.markdown(_card_html(_kicker5("WORST 5", units["worst5"]), accent, units["worst5"]),
                                unsafe_allow_html=True)
                if units["trio"]:
                    st.markdown(_mini_html("BEST TRIO", units["trio"], accent), unsafe_allow_html=True)
                if units["duo"]:
                    st.markdown(_mini_html("BEST DUO", units["duo"], accent), unsafe_allow_html=True)
                if not any(units.values()):
                    st.info("Not enough minutes together.")

        # PNG, generated on demand like the Impact Pulse image
        png_key = f"lu_png_{card_index}_{game_code}"
        if png_key not in st.session_state:
            if st.button("\U0001F4E5 Generate Lineups image", key=f"lu_btn_{card_index}_{game_code}"):
                with st.spinner("Generating Lineups image..."):
                    try:
                        brand = Path(elstatslab_logo) if elstatslab_logo else None
                        sides = {}
                        for key, code, disp in (("home", home_code, home_disp),
                                                ("away", away_code, away_disp)):
                            logo = team_logo_fn(code)
                            zoom = float(logo_zoom_fn(code)) if logo_zoom_fn else 1.0
                            sides[key] = {"name": disp, "logo": str(logo) if logo else "",
                                          "zoom": zoom,
                                          **_match_units(data[code], short=False, legacy=legacy)}
                        payload = {"round_label": round_label,
                                   "brand_logo": str(brand) if brand and brand.exists() else "",
                                   **sides}
                        st.session_state[png_key] = build_match_lineups_png(payload)
                    except Exception as e:
                        st.error(f"Export error: {e}")
                        return
                st.rerun()
        if png_key in st.session_state:
            st.download_button("\U0001F4E5 Download Lineups image",
                               data=st.session_state[png_key],
                               file_name=f"Lineups_{home_code}_vs_{away_code}.png",
                               mime="image/png", key=f"lu_dl_{card_index}_{game_code}")
