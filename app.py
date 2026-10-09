"""
ELSTATSLAB EuroLeague Match Center
Streamlit app: matchday picker, head to head stats, logos, live standings,
form sparklines, gradient coloured comparisons, PNG export for X.

Run locally:
    streamlit run app.py
"""

import base64
import io
import ipaddress
import json
import math
import sqlite3
import threading
import uuid
import zlib
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import requests
import streamlit as st
from matplotlib.gridspec import GridSpec
from matplotlib import font_manager
from PIL import Image

from gameflow_chart import render_gameflow_png
import team_cards
import shotmap_ui
import lineup_tab

# =============================================================================
# CONFIG
# =============================================================================
_PUBLIC_DB_LOCAL = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP\euroleague_public.db")
_PUBLIC_DB_CLOUD = Path("euroleague_public.db")
_LOCAL_DB = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db")

if _PUBLIC_DB_LOCAL.exists():
    DB_PATH = _PUBLIC_DB_LOCAL
elif _PUBLIC_DB_CLOUD.exists():
    DB_PATH = _PUBLIC_DB_CLOUD
else:
    DB_PATH = _LOCAL_DB

LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"
CURRENT_SEASON = 2025
ROLLING_WINDOW = 5
# Last 5 gauges (inside the Season table) appear from this regular season round
LAST5_MIN_ROUND = 13
# Gauges only draw a coloured line when the gap reaches this share of the metric scale
LAST5_MIN_GAP = 0.30

# Interrupteurs des Shot Maps : mettre False pour les desactiver sans retirer le code.
SHOTMAPS_IN_MATCH = True    # bloc "Shot Map" sous Impact Pulse dans un match
LINEUPS_IN_MATCH = True     # bloc "Lineups" sous Impact Pulse dans un match
SHOTMAPS_TAB = True         # onglet "Shot Maps"

# Brand typeface for exported PNGs (Barlow Condensed, matches the X banner).
# Drop the .ttf files in a "fonts" folder next to app.py; falls back to a
# plain bold sans-serif automatically if they are not present.
FONTS_DIR = Path("fonts")


def _brand_font(filename: str, fallback_weight: str = "bold") -> font_manager.FontProperties:
    fp = FONTS_DIR / filename
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


BARLOW_BOLD     = _brand_font("BarlowCondensed-Bold.ttf", "bold")
BARLOW_SEMIBOLD = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
BARLOW_REGULAR  = _brand_font("BarlowCondensed-Regular.ttf", "regular")

st.set_page_config(
    page_title="ELSTATSLAB Match Center",
    page_icon=str(ELSTATSLAB_LOGO) if ELSTATSLAB_LOGO.exists() else "🏀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from site_theme import apply_theme, badge_logo_b64, brand_logo_b64  # noqa: E402
apply_theme()

# =============================================================================
# LOGO MAPPING
# =============================================================================
LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "BES": "BJK.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png",
    "MIL": "AXM.png", "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png",
    "PAN": "PAO.png", "PAR": "PAR.png", "PRS": "PBB.png", "RED": "CZV.png",
    "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png", "ZAL": "ZAL.png",
}

ZOOM_CORRECTIONS = {
    "ASM": 1.3, "AXM": 1.5, "CZV": 1.6, "EFS": 0.8,
    "FEN": 1.0, "BAR": 0.8, "PAO": 1.1, "VIR": 0.85,
    "PBB": 0.85, "OLY": 0.9, "HTA": 0.8,
}

TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne",
    "BAR": "FC Barcelona",
    "BAS": "Baskonia Vitoria-Gasteiz",
    "BES": "Beşiktaş Istanbul",
    "DUB": "Dubai Basketball",
    "HTA": "Hapoel Tel Aviv",
    "IST": "Anadolu Efes Istanbul",
    "MAD": "Real Madrid",
    "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan",
    "MUN": "FC Bayern Munich",
    "OLY": "Olympiacos Piraeus",
    "PAM": "Valencia Basket",
    "PAN": "Panathinaikos Athens",
    "PAR": "Partizan Belgrade",
    "PRS": "Paris Basketball",
    "RED": "Crvena Zvezda Belgrade",
    "TEL": "Maccabi Tel Aviv",
    "ULK": "Fenerbahce Istanbul",
    "VIR": "Virtus Bologna",
    "ZAL": "Zalgiris Kaunas",
}


def display_name(code: str, fallback: str) -> str:
    return TEAM_DISPLAY_NAMES.get(code, fallback.title())


def logo_zoom(code: str) -> float:
    filename = LOGO_MAP.get(code, "")
    stem = Path(filename).stem
    return ZOOM_CORRECTIONS.get(stem, 1.0)


def logo_path(code: str) -> Path | None:
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    p = LOGOS_DIR / filename
    return p if p.exists() else None


@st.cache_data(ttl=3600)
def logo_b64(code: str) -> str | None:
    """Cropped, square, white background crest (same footprint for every club)."""
    lp = logo_path(code)
    if not lp:
        return None
    return badge_logo_b64(str(lp))


def _autocrop_logo(img_arr):
    """
    Recadre une image RGBA (format retourné par plt.imread, flottant 0-1) sur
    son contenu réel : coupe la marge transparente, et si un bloc secondaire
    (sponsor, texte) est séparé du dessin principal par un vrai espace vide,
    ne garde que le premier bloc. Même logique que gameflow_chart.py, pour
    que les logos aient un poids visuel cohérent sans réglage par équipe.
    """
    alpha = img_arr[:, :, 3]
    ys, xs = np.where(alpha > 0.04)
    if len(xs) == 0:
        return img_arr
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    cropped = img_arr[y0:y1 + 1, x0:x1 + 1]

    h = cropped.shape[0]
    a2 = cropped[:, :, 3]
    row_has_content = (a2 > 0.04).sum(axis=1)
    threshold = a2.shape[1] * 0.005
    gap_start = None
    for i, s in enumerate(row_has_content):
        if s < threshold:
            if gap_start is None:
                gap_start = i
        else:
            if gap_start is not None:
                gap_h = i - gap_start
                if gap_h > h * 0.015 and gap_start > h * 0.25:
                    cropped = cropped[:gap_start, :, :]
                    a3 = cropped[:, :, 3]
                    ys2, xs2 = np.where(a3 > 0.04)
                    if len(xs2):
                        xx0, xx1, yy0, yy1 = xs2.min(), xs2.max(), ys2.min(), ys2.max()
                        cropped = cropped[yy0:yy1 + 1, xx0:xx1 + 1]
                    return cropped
            gap_start = None
    return cropped


# =============================================================================
# GAMEFLOW
# =============================================================================
@st.cache_data(ttl=3600)
def get_gameflow_png(gamecode: int, season: int, round_label: str = "",
                     aspect: str = "square") -> bytes | None:
    try:
        return render_gameflow_png(gamecode, season, round_label=round_label, aspect=aspect)
    except Exception as e:
        import traceback
        st.error(f"Game Flow error: {e}")
        st.code(traceback.format_exc())
        return None


# =============================================================================
# DATA ACCESS
# =============================================================================
@st.cache_resource
def get_conn():
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


@st.cache_data(ttl=600)
def load_seasons() -> list[int]:
    return [2026, 2025]


@st.cache_data(ttl=600)
def load_rounds(season: int) -> list[int]:
    q = "SELECT DISTINCT gameday FROM schedule WHERE Season = ? ORDER BY gameday"
    return pd.read_sql(q, get_conn(), params=(season,))["gameday"].tolist()


@st.cache_data(ttl=600)
def load_all_schedule(season: int) -> pd.DataFrame:
    q = """
        SELECT gameday, round AS phase
        FROM schedule
        WHERE Season = ?
        ORDER BY gameday
    """
    return pd.read_sql(q, get_conn(), params=(season,))


@st.cache_data(ttl=600)
def load_official_standings() -> pd.DataFrame | None:
    try:
        return pd.read_sql("SELECT * FROM standings_official", get_conn())
    except Exception:
        return None


@st.cache_data(ttl=600)
def load_playoffs_schedule(season: int) -> pd.DataFrame:
    q = """
        SELECT gameday, hometeam, homecode, awayteam, awaycode, played
        FROM schedule
        WHERE Season = ? AND round = 'PO'
        ORDER BY gameday
    """
    return pd.read_sql(q, get_conn(), params=(season,))


def get_series_score(playoffs_schedule: pd.DataFrame,
                     all_games: pd.DataFrame,
                     hcode: str, acode: str,
                     current_gameday: int) -> dict | None:
    if playoffs_schedule.empty:
        return None

    mask = (
        (
            (playoffs_schedule["homecode"].str.upper() == hcode.upper()) &
            (playoffs_schedule["awaycode"].str.upper() == acode.upper())
        ) | (
            (playoffs_schedule["homecode"].str.upper() == acode.upper()) &
            (playoffs_schedule["awaycode"].str.upper() == hcode.upper())
        )
    )
    series_games = playoffs_schedule[mask].sort_values("gameday")

    if series_games.empty:
        return None

    game1 = series_games.iloc[0]
    series_home_code = game1["homecode"].upper()
    series_away_code = game1["awaycode"].upper()

    home_wins = 0
    away_wins = 0
    games_played = 0

    for _, sg in series_games.iterrows():
        if sg["gameday"] > current_gameday:
            break
        if sg["played"] != "true":
            continue
        result = all_games[
            (all_games["gameday"] == int(sg["gameday"])) &
            (all_games["team_code"].str.upper() == sg["homecode"].upper())
        ]
        if result.empty:
            continue
        row = result.iloc[0]
        games_played += 1
        if row["score"] > row["opp_score"]:
            winner_code = sg["homecode"].upper()
        else:
            winner_code = sg["awaycode"].upper()

        if winner_code == series_home_code:
            home_wins += 1
        else:
            away_wins += 1

    return {
        "home_code": series_home_code,
        "away_code": series_away_code,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "games_played": games_played,
    }


def orient_series(raw_series: dict | None, hcode: str) -> dict | None:
    if not raw_series:
        return None
    if raw_series["home_code"] == hcode.upper():
        return raw_series
    return {
        "home_code": hcode.upper(),
        "away_code": raw_series["home_code"],
        "home_wins": raw_series["away_wins"],
        "away_wins": raw_series["home_wins"],
        "games_played": raw_series["games_played"],
    }


@st.cache_data(ttl=600)
def load_matchday(season: int, gameday: int) -> pd.DataFrame:
    q = """
        SELECT gamecode, date, startime, round AS phase,
               hometeam, homecode, awayteam, awaycode, played
        FROM schedule
        WHERE Season = ? AND gameday = ?
        ORDER BY date, startime, gamecode
    """
    return pd.read_sql(q, get_conn(), params=(season, gameday))


@st.cache_data(ttl=600)
def load_team_games(season: int) -> pd.DataFrame:
    q = """
        SELECT
            s.gameday,
            s.date,
            ts.GameCode  AS gamecode_num,
            ts.TeamName  AS team,
            CASE WHEN UPPER(s.hometeam) = UPPER(ts.TeamName)
                 THEN s.homecode ELSE s.awaycode END AS team_code,
            opp.TeamName AS opponent,
            ts.Score     AS score,
            opp.Score    AS opp_score,
            ts.Possessions AS poss,
            ts.Reb_Off   AS oreb,
            ts.Reb_Def   AS dreb,
            opp.Reb_Off  AS opp_oreb,
            opp.Reb_Def  AS opp_dreb,
            ts.Ast       AS ast,
            (COALESCE(ts."2PM", 0) + COALESCE(ts."3PM", 0)) AS fgm,
            ts."2PM"     AS twopm,
            ts."3PM"     AS threepm,
            ts."2PA"     AS twopa,
            ts."3PA"     AS threepa,
            ts.Turnovers AS tov
        FROM team_stats ts
        JOIN team_stats opp
          ON opp.GameCode = ts.GameCode
         AND opp.Season   = ts.Season
         AND opp.TeamName <> ts.TeamName
        JOIN schedule s
          ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = ts.GameCode
         AND s.Season = ts.Season
         AND (UPPER(s.hometeam) = UPPER(ts.TeamName)
           OR UPPER(s.awayteam) = UPPER(ts.TeamName))
        WHERE s.Season = ? AND s.played = 'true'
    """
    df = pd.read_sql(q, get_conn(), params=(season,))
    df["date"] = pd.to_datetime(df["date"], format="%b %d, %Y")
    return df.drop_duplicates(subset=["gamecode_num", "team"])


def aggregate_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    poss    = df["poss"].sum()
    pts     = df["score"].sum()
    pa      = df["opp_score"].sum()
    oreb    = df["oreb"].sum()
    dreb    = df["dreb"].sum()
    o_oreb  = df["opp_oreb"].sum()
    o_dreb  = df["opp_dreb"].sum()
    ast     = df["ast"].sum()
    fgm     = df["fgm"].sum()
    twopm   = df["twopm"].sum()
    threepm = df["threepm"].sum()
    twopa   = df["twopa"].sum()
    threepa = df["threepa"].sum()
    tov     = df["tov"].sum()
    wins    = int((df["score"] > df["opp_score"]).sum())
    games   = len(df)

    def safe(num, den, mult=100.0, nd=1):
        return round(mult * num / den, nd) if den else None

    return {
        "games":   games,
        "wins":    wins,
        "losses":  games - wins,
        "pt_diff": int(pts - pa),
        "ORTG":    safe(pts, poss),
        "DRTG":    safe(pa, poss),
        "NETRTG":  safe(pts - pa, poss),
        "OREB%":   safe(oreb, oreb + o_dreb),
        "REB%":    safe(oreb + dreb, oreb + dreb + o_oreb + o_dreb),
        "AST%":    safe(ast, fgm),
        "eFG%":    safe(twopm + 1.5 * threepm, twopa + threepa),
        "TOV%":    safe(tov, poss),
    }


def team_season_stats(all_games: pd.DataFrame, up_to_gameday: int,
                      official_standings: pd.DataFrame | None = None) -> pd.DataFrame:
    scoped = all_games[all_games["gameday"] <= up_to_gameday]
    rows = []
    for team, g in scoped.groupby("team"):
        stats = aggregate_stats(g)
        stats["team"] = team
        stats["team_code"] = g["team_code"].iloc[0]
        rows.append(stats)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values(["wins", "pt_diff"],
                                        ascending=[False, False])
    df["rank"] = range(1, len(df) + 1)
    df = df.reset_index(drop=True)

    if official_standings is not None and not official_standings.empty:
        off = official_standings.copy()
        off["_code"] = off["team_code"].astype(str).str.strip().str.upper()
        off = off.drop_duplicates("_code").set_index("_code")
        n_teams = len(df)
        matched, unmatched = {}, []
        for i, row in df.iterrows():
            code = str(row["team_code"]).strip().upper()
            if code in off.index:
                matched[i] = int(off.at[code, "rank"])
                if "last_5_form" in off.columns:
                    df.at[i, "last_5_form"] = off.at[code, "last_5_form"]
            else:
                unmatched.append(i)
        # The official table is a snapshot taken when build_public_db.py last ran. If its W/L
        # record differs from the games we have for that team in this scope (stale snapshot,
        # or an older round being viewed), its ranks would not match the displayed record,
        # so they are ignored and the computed ranking is kept.
        stale = []
        if {"wins", "losses"}.issubset(off.columns):
            for i in matched:
                code = str(df.at[i, "team_code"]).strip().upper()
                try:
                    if (int(off.at[code, "wins"]) != int(df.at[i, "wins"])
                            or int(off.at[code, "losses"]) != int(df.at[i, "losses"])):
                        stale.append(code)
                except (TypeError, ValueError):
                    continue
        if stale:
            print("[standings] standings_official looks out of date (W/L differs for: "
                  + ", ".join(stale) + "), using computed ranks", flush=True)
        ranks_ok = (
            matched
            and not stale
            and len(set(matched.values())) == len(matched)
            and all(1 <= r <= n_teams for r in matched.values())
        )
        if ranks_ok:
            # teams missing from standings_official get the leftover ranks (in computed order),
            # so the ranking is always a clean 1..N list and never mixes two sources
            free = [r for r in range(1, n_teams + 1) if r not in set(matched.values())]
            for i, r in matched.items():
                df.at[i, "rank"] = r
            for i, r in zip(unmatched, free):
                df.at[i, "rank"] = r
            if unmatched:
                print("[standings] no standings_official row for: "
                      + ", ".join(str(df.at[i, "team_code"]) for i in unmatched), flush=True)
            df = df.sort_values("rank").reset_index(drop=True)

    return df


def team_recent_stats(all_games: pd.DataFrame, team: str,
                      before_gameday: int, window: int = ROLLING_WINDOW) -> dict:
    mask = (all_games["team"].str.upper() == team.upper()) & \
           (all_games["gameday"] < before_gameday)
    sub = all_games[mask].sort_values("gameday", ascending=False).head(window)
    return aggregate_stats(sub)


def team_form_sequence(all_games: pd.DataFrame, team: str,
                       window: int = ROLLING_WINDOW) -> list[bool]:
    mask = all_games["team"].str.upper() == team.upper()
    sub = (all_games[mask]
           .sort_values("date", ascending=False)
           .head(window))
    return [bool(row.score > row.opp_score) for row in sub.itertuples()]


def team_single_game_stats(all_games: pd.DataFrame, team: str,
                           gameday: int) -> dict:
    mask = (all_games["team"].str.upper() == team.upper()) & \
           (all_games["gameday"] == gameday)
    sub = all_games[mask]
    return aggregate_stats(sub)


# =============================================================================
# ROUND LABELS
# =============================================================================
PHASE_LABELS = {
    "RS": "Regular Season",
    "PI": "Play-In",
    "PO": "Playoffs",
    "FF": "Final Four",
}


def build_round_labels(schedule_df: pd.DataFrame) -> dict[int, tuple[str, str]]:
    labels: dict[int, tuple[str, str]] = {}
    po_counter = 0
    pi_counter = 0
    ff_counter = 0
    by_day = schedule_df.drop_duplicates("gameday").sort_values("gameday")
    for _, row in by_day.iterrows():
        gd = int(row["gameday"])
        ph = row["phase"]
        if ph == "PO":
            po_counter += 1
            short = f"Playoffs Game {po_counter}"
            labels[gd] = (short, short)
        elif ph == "PI":
            pi_counter += 1
            short = "Play-In" if pi_counter == 1 else f"Play-In Game {pi_counter}"
            labels[gd] = (short, short)
        elif ph == "FF":
            ff_counter += 1
            if ff_counter == 1:
                labels[gd] = ("Semifinals", "Final Four Semifinals")
            else:
                labels[gd] = ("Final", "Final Four Final")
        else:
            labels[gd] = (f"Round {gd}", f"Regular Season Round {gd}")
    return labels


# =============================================================================
# MONTE CARLO WIN PROBABILITY MODEL
# =============================================================================
TEAM_NAME_MAP = {
    "Bitci Baskonia Vitoria-Gasteiz":   "Baskonia Vitoria-Gasteiz",
    "Cazoo Baskonia Vitoria-Gasteiz":   "Baskonia Vitoria-Gasteiz",
    "Kosner Baskonia Vitoria-Gasteiz":  "Baskonia Vitoria-Gasteiz",
    "Crvena Zvezda mts Belgrade":       "Crvena Zvezda Meridianbet Belgrade",
    "AX Armani Exchange Milan":         "EA7 Emporio Armani Milan",
    "Armani Olimpia Milan":             "EA7 Emporio Armani Milan",
    "Virtus Segafredo Bologna":         "Virtus Bologna",
    "Maccabi Rapyd Tel Aviv":           "Maccabi Playtika Tel Aviv",
    "Panathinaikos Athens":             "Panathinaikos AKTOR Athens",
    "Panathinaikos OPAP Athens":        "Panathinaikos AKTOR Athens",
    "Fenerbahce Tarfin Istanbul":       "Fenerbahce Beko Istanbul",
}


def _resolve_team_name_fallback(name: str) -> str | None:
    """Renvoie le nom alternatif connu pour cette équipe (changement de
    sponsor d'une saison à l'autre), ou None si aucun mapping n'existe.
    N'est utilisé qu'en repli, jamais à la place du nom d'origine."""
    if not name:
        return None
    upper = name.strip().upper()
    for k, v in TEAM_NAME_MAP.items():
        if k.upper() == upper and v.upper() != upper:
            return v
    return None

MC_MIN_H2H_GAMES  = 4
MC_MIN_DIST_GAMES = 2
MC_N_SIMULATIONS  = 10_000
MC_HOME_COURT    = 0.06
MC_HOME_COURT_PO = 0.10

WEIGHTS_RS = {
    "current_season": 0.50,
    "h2h":            0.25,
    "home_court":     0.10,
    "style_matchup":  0.15,
}
WEIGHTS_PO_BASE = {
    "current_season": 0.35,
    "h2h":            0.30,
    "home_court":     0.20,
    "style_matchup":  0.25,
}


def _mc_logistic(x: float, scale: float = 2.5) -> float:
    return 1.0 / (1.0 + math.exp(-scale * x))


def _get_dist(conn, team_name: str, season: int,
              round_type: str = "RS", seasons_back: int = 1) -> Optional[dict]:
    rounds = "'RS'" if round_type == "RS" else "'PO', 'FF', 'PI'"
    season_min = season - seasons_back + 1
    q = f"""
        SELECT
            AVG(t.Score)                                                             AS avg_pts,
            SQRT(AVG(t.Score * t.Score) - AVG(t.Score) * AVG(t.Score))              AS std_pts,
            AVG(t.Off_Rtg)                                                           AS avg_ortg,
            AVG(t.Def_Rtg)                                                           AS avg_drtg,
            SQRT(AVG(t.Off_Rtg * t.Off_Rtg) - AVG(t.Off_Rtg) * AVG(t.Off_Rtg))     AS std_ortg,
            SQRT(AVG(t.Def_Rtg * t.Def_Rtg) - AVG(t.Def_Rtg) * AVG(t.Def_Rtg))     AS std_drtg,
            AVG(t.Pace)                                                              AS avg_pace,
            COUNT(*)                                                                 AS games
        FROM team_stats t
        JOIN schedule s
            ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = t.GameCode
            AND s.Season = t.Season
        WHERE t.Season BETWEEN ? AND ?
          AND s.round IN ({rounds})
          AND UPPER(t.TeamName) = UPPER(?)
          AND s.played = 'true'
    """

    def _run(name):
        row = conn.execute(q, (season_min, season, name)).fetchone()
        if row and row[7] and row[7] >= MC_MIN_DIST_GAMES:
            keys = ["avg_pts", "std_pts", "avg_ortg", "avg_drtg",
                    "std_ortg", "std_drtg", "avg_pace", "games"]
            return dict(zip(keys, row))
        return None

    result = _run(team_name)
    if result is None:
        # Repli : nom d'origine introuvable (changement de sponsor d'une
        # saison à l'autre par ex.), on essaie le nom alternatif connu.
        alt_name = _resolve_team_name_fallback(team_name)
        if alt_name:
            result = _run(alt_name)
    return result


def _get_h2h(conn, team_a_code: str, team_b_code: str,
             current_season: int = 2025, seasons_back: int = 4,
             playoff_only: bool = False) -> dict:
    """
    Compare sur le code équipe (stable d'une saison à l'autre), pas sur le
    nom complet : les noms de sponsor changent (ex: Fenerbahce Beko ->
    Fenerbahce Tarfin), le code équipe non.
    """
    season_min = current_season - seasons_back + 1
    round_filter = "AND s.round IN ('PO', 'FF')" if playoff_only else ""
    q = f"""
        WITH matchups AS (
            SELECT
                CASE WHEN s.homecode = ? THEN h.Score ELSE a.Score END AS score_a,
                CASE WHEN s.homecode = ? THEN a.Score ELSE h.Score END AS score_b
            FROM schedule s
            JOIN team_stats h
                ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = h.GameCode
                AND s.Season = h.Season
                AND UPPER(h.TeamName) = UPPER(s.hometeam)
            JOIN team_stats a
                ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = a.GameCode
                AND s.Season = a.Season
                AND UPPER(a.TeamName) = UPPER(s.awayteam)
            WHERE s.played = 'true'
              AND s.Season BETWEEN ? AND ?
              AND (
                    (s.homecode = ? AND s.awaycode = ?)
                 OR (s.homecode = ? AND s.awaycode = ?)
              )
              {round_filter}
        )
        SELECT COUNT(*) AS games,
               SUM(CASE WHEN score_a > score_b THEN 1 ELSE 0 END) AS wins_a,
               ROUND(AVG(score_a - score_b), 2) AS avg_margin
        FROM matchups
    """
    row = conn.execute(q, (
        team_a_code, team_a_code,
        season_min, current_season,
        team_a_code, team_b_code,
        team_b_code, team_a_code
    )).fetchone()
    if row and row[0]:
        return {"games": row[0], "wins_a": row[1], "avg_margin": row[2] or 0.0}
    return {"games": 0, "wins_a": 0, "avg_margin": 0.0}


def _get_win_pct(conn, team_name: str, season: int) -> float:
    q = """
        SELECT
            COUNT(*) AS games,
            SUM(CASE
                WHEN UPPER(s.hometeam) = UPPER(?) AND h.Score > a.Score THEN 1
                WHEN UPPER(s.awayteam) = UPPER(?) AND a.Score > h.Score THEN 1
                ELSE 0
            END) AS wins
        FROM schedule s
        JOIN team_stats h
            ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = h.GameCode
            AND s.Season = h.Season
            AND UPPER(h.TeamName) = UPPER(s.hometeam)
        JOIN team_stats a
            ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = a.GameCode
            AND s.Season = a.Season
            AND UPPER(a.TeamName) = UPPER(s.awayteam)
        WHERE s.Season = ?
          AND s.round = 'RS'
          AND s.played = 'true'
          AND (UPPER(s.hometeam) = UPPER(?) OR UPPER(s.awayteam) = UPPER(?))
    """
    row = conn.execute(q, (team_name, team_name, season, team_name, team_name)).fetchone()
    if row and row[0]:
        return row[1] / row[0]
    return 0.5


def _serie_prob_weight(serie_scores: list) -> tuple:
    if not serie_scores:
        return 0.5, 0.0

    n = len(serie_scores)
    weights = [2 ** i for i in range(n)]
    total_w = sum(weights)
    weighted_margin = sum(s * w for s, w in zip(serie_scores, weights)) / total_w

    prob = _mc_logistic(weighted_margin / 15.0)
    weight_map = {1: 0.20, 2: 0.35, 3: 0.45, 4: 0.50, 5: 0.55}
    return prob, weight_map.get(n, 0.55)


def _get_match_context(conn, gamecode: int, season: int) -> Optional[dict]:
    row = conn.execute("""
        SELECT Season, round, hometeam, awayteam, homecode, awaycode
        FROM schedule
        WHERE Season = ?
          AND CAST(SUBSTR(gamecode, INSTR(gamecode, '_') + 1) AS INTEGER) = ?
    """, (season, gamecode)).fetchone()

    if not row:
        return None

    season, round_, home_team, away_team, home_code, away_code = row

    home_win_pct = _get_win_pct(conn, home_team, season)
    away_win_pct = _get_win_pct(conn, away_team, season)

    serie_scores     = []
    home_series_wins = 0
    away_series_wins = 0

    if round_ in ("PO", "FF", "PI"):
        played = conn.execute("""
            SELECT s.hometeam, s.awayteam, h.Score AS hs, a.Score AS as_
            FROM schedule s
            JOIN team_stats h
                ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = h.GameCode
                AND s.Season = h.Season
                AND UPPER(h.TeamName) = UPPER(s.hometeam)
            JOIN team_stats a
                ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = a.GameCode
                AND s.Season = a.Season
                AND UPPER(a.TeamName) = UPPER(s.awayteam)
            WHERE s.Season = ?
              AND s.round = ?
              AND s.played = 'true'
              AND CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) < ?
              AND (
                    (UPPER(s.hometeam) = UPPER(?) AND UPPER(s.awayteam) = UPPER(?))
                 OR (UPPER(s.hometeam) = UPPER(?) AND UPPER(s.awayteam) = UPPER(?))
              )
            ORDER BY s.game_number ASC
        """, (season, round_, gamecode,
              home_team, away_team,
              away_team, home_team)).fetchall()

        for m in played:
            ht, at, hs, as_ = m
            margin = hs - as_ if ht.upper() == home_team.upper() else as_ - hs
            serie_scores.append(margin)
            if margin > 0:
                home_series_wins += 1
            else:
                away_series_wins += 1

    return {
        "home_team":        home_team,
        "away_team":        away_team,
        "home_code":        home_code,
        "away_code":        away_code,
        "season":           season,
        "round":            round_,
        "is_playoff":       round_ in ("PO", "FF", "PI"),
        "home_win_pct":     home_win_pct,
        "away_win_pct":     away_win_pct,
        "serie_scores":     serie_scores,
        "home_series_wins": home_series_wins,
        "away_series_wins": away_series_wins,
    }


def _monte_carlo_win_prob(conn, ctx: dict) -> dict:
    home_team    = ctx["home_team"]
    away_team    = ctx["away_team"]
    home_code    = ctx["home_code"]
    away_code    = ctx["away_code"]
    season       = ctx["season"]
    round_       = ctx["round"]
    is_playoff   = ctx["is_playoff"]
    home_win_pct = ctx["home_win_pct"]
    away_win_pct = ctx["away_win_pct"]
    serie_scores = ctx["serie_scores"]

    if is_playoff:
        home_dist = _get_dist(conn, home_team, season, "PO", seasons_back=4)
        away_dist = _get_dist(conn, away_team, season, "PO", seasons_back=4)
        if not home_dist:
            home_dist = _get_dist(conn, home_team, season, "RS", seasons_back=1)
        if not away_dist:
            away_dist = _get_dist(conn, away_team, season, "RS", seasons_back=1)
    else:
        home_dist = _get_dist(conn, home_team, season, "RS", seasons_back=1)
        away_dist = _get_dist(conn, away_team, season, "RS", seasons_back=1)
        if not home_dist:
            home_dist = _get_dist(conn, home_team, season - 1, "RS", seasons_back=1)
        if not away_dist:
            away_dist = _get_dist(conn, away_team, season - 1, "RS", seasons_back=1)

    if not home_dist or not away_dist:
        return {"home_prob": 0.5, "away_prob": 0.5,
                "error": "Distributions not found"}

    # Graine deterministe : un meme match donne toujours la meme simulation
    # tant que les donnees ne changent pas (zlib.crc32 est stable d'un
    # lancement de Python a l'autre, contrairement a hash()).
    seed = zlib.crc32(
        f"{season}|{round_}|{home_code}|{away_code}|{len(serie_scores)}".encode()
    )
    rng = np.random.default_rng(seed)
    h_scores = rng.normal(home_dist["avg_pts"], home_dist["std_pts"], MC_N_SIMULATIONS)
    a_scores = rng.normal(away_dist["avg_pts"], away_dist["std_pts"], MC_N_SIMULATIONS)
    mc_prob  = float(np.mean(h_scores > a_scores))
    std_err  = math.sqrt(mc_prob * (1 - mc_prob) / MC_N_SIMULATIONS)
    ci_low   = max(0.02, mc_prob - 1.645 * std_err)
    ci_high  = min(0.98, mc_prob + 1.645 * std_err)

    h2h = _get_h2h(conn, home_code, away_code, season, playoff_only=is_playoff)
    if h2h["games"] < MC_MIN_H2H_GAMES and is_playoff:
        h2h = _get_h2h(conn, home_code, away_code, season, playoff_only=False)

    h2h_prob = 0.5
    if h2h["games"] >= MC_MIN_H2H_GAMES:
        reliability = min(1.0, h2h["games"] / 10.0)
        raw_h2h     = _mc_logistic(h2h["avg_margin"] / 15.0)
        h2h_prob    = 0.5 + reliability * (raw_h2h - 0.5)

    if round_ == "FF":
        home_court_prob = 0.5
    elif round_ in ("PO", "PI"):
        home_court_prob = 0.5 + MC_HOME_COURT_PO
    else:
        home_court_prob = 0.5 + MC_HOME_COURT

    home_net   = home_dist["avg_ortg"] - away_dist["avg_drtg"]
    away_net   = away_dist["avg_ortg"] - home_dist["avg_drtg"]
    style_prob = _mc_logistic((home_net - away_net) / 40.0)

    season_prob         = _mc_logistic(home_win_pct - away_win_pct)
    current_season_prob = 0.6 * mc_prob + 0.4 * season_prob

    if not is_playoff:
        components = {
            "current_season": round(current_season_prob, 3),
            "h2h":            round(h2h_prob, 3),
            "home_court":     round(home_court_prob, 3),
            "style_matchup":  round(style_prob, 3),
        }
        final_prob = sum(WEIGHTS_RS[k] * v for k, v in components.items())
        final_prob = max(0.02, min(0.98, final_prob))
        return {
            "home_prob":       round(final_prob, 3),
            "away_prob":       round(1 - final_prob, 3),
            "mc_raw":          round(mc_prob, 3),
            "confidence_low":  round(ci_low, 3),
            "confidence_high": round(ci_high, 3),
            "h2h_games":       h2h["games"],
            "h2h_margin":      round(h2h["avg_margin"], 2),
            "serie_prob":      None,
            "serie_weight":    None,
            "components":      components,
            "round":           round_,
        }

    serie_prob, serie_weight = _serie_prob_weight(serie_scores)
    remaining = 1.0 - serie_weight
    base_w    = {k: v * remaining for k, v in WEIGHTS_PO_BASE.items()}

    components = {
        "serie_encours":  round(serie_prob, 3),
        "current_season": round(current_season_prob, 3),
        "h2h":            round(h2h_prob, 3),
        "home_court":     round(home_court_prob, 3),
        "style_matchup":  round(style_prob, 3),
    }

    final_prob = (serie_weight * serie_prob +
                  sum(base_w[k] * components[k] for k in WEIGHTS_PO_BASE))
    final_prob = max(0.02, min(0.98, final_prob))

    return {
        "home_prob":       round(final_prob, 3),
        "away_prob":       round(1 - final_prob, 3),
        "mc_raw":          round(mc_prob, 3),
        "confidence_low":  round(ci_low, 3),
        "confidence_high": round(ci_high, 3),
        "h2h_games":       h2h["games"],
        "h2h_margin":      round(h2h["avg_margin"], 2),
        "serie_prob":      round(serie_prob, 3),
        "serie_weight":    round(serie_weight, 3),
        "components":      components,
        "round":           round_,
    }


@st.cache_data(ttl=300)
def _predict_legacy(gamecode: int, season: int) -> dict:
    conn = get_conn()
    ctx = _get_match_context(conn, gamecode, season)
    if not ctx:
        return {"home_prob": 0.5, "away_prob": 0.5,
                "error": f"Gamecode {gamecode} not found for season {season}"}
    return _monte_carlo_win_prob(conn, ctx)


# =============================================================================
# MONTE CARLO WIN PROBABILITY MODEL, VERSION 5 (regular season games)
# Used from round 4 of the 2026 season. Play-In, Playoffs and Final Four games
# still use the previous model (see predict_by_gamecode at the end).
# =============================================================================
V5_N_SIMS = 10_000
V5_N_FULL = 12
V5_MIN_GAMES = 2
V5_MIN_LEAGUE_ROWS = 20
V5_H2H_SEASONS = 4
V5_H2H_MIN_GAMES = 4

# Home win rate in regular season games from 2022 to 2025: 823 wins in 1298 games.
V5_HOME_WIN_RATE = 823 / 1298
V5_HOME_SHIFT = math.log(V5_HOME_WIN_RATE / (1 - V5_HOME_WIN_RATE))

_V5_W = {"season": 0.40, "h2h": 0.25, "style": 0.15}
_V5_TOT = sum(_V5_W.values())
V5_W_SEASON = _V5_W["season"] / _V5_TOT
V5_W_H2H = _V5_W["h2h"] / _V5_TOT
V5_W_STYLE = _V5_W["style"] / _V5_TOT

V5_KEYS = ("avg_pts", "std_pts", "avg_ortg", "avg_drtg", "avg_pace")
_V5_GC = "CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER)"


def _v5_logistic(x, scale=2.5):
    return 1.0 / (1.0 + math.exp(-scale * x))


def _v5_logit(p):
    return math.log(p / (1 - p))


def _v5_sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def _v5_clamp(p):
    return max(0.02, min(0.98, p))


# All queries use the team code, which is stable from one season to the next,
# so sponsor name changes do not break the link with the previous season.
_V5_TEAM_SQL = """
    SELECT AVG(t.Score),
           SQRT(MAX(0.0, AVG(t.Score * t.Score) - AVG(t.Score) * AVG(t.Score))),
           AVG(t.Off_Rtg), AVG(t.Def_Rtg), AVG(t.Pace), COUNT(*)
    FROM team_stats t
    JOIN schedule s
      ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = t.GameCode
     AND s.Season = t.Season
    WHERE t.Season = ? AND s.round = 'RS' AND s.played = 'true'
      AND ((s.homecode = ? AND UPPER(t.TeamName) = UPPER(s.hometeam))
        OR (s.awaycode = ? AND UPPER(t.TeamName) = UPPER(s.awayteam)))
"""

_V5_LEAGUE_SQL = """
    SELECT AVG(t.Score),
           SQRT(MAX(0.0, AVG(t.Score * t.Score) - AVG(t.Score) * AVG(t.Score))),
           AVG(t.Off_Rtg), AVG(t.Def_Rtg), AVG(t.Pace), COUNT(*)
    FROM team_stats t
    JOIN schedule s
      ON CAST(SUBSTR(s.gamecode, INSTR(s.gamecode, '_') + 1) AS INTEGER) = t.GameCode
     AND s.Season = t.Season
    WHERE t.Season = ? AND s.round = 'RS' AND s.played = 'true'
"""

_V5_WIN_SQL = f"""
    SELECT COUNT(*),
           SUM(CASE WHEN (s.homecode = ? AND h.Score > a.Score)
                      OR (s.awaycode = ? AND a.Score > h.Score) THEN 1 ELSE 0 END)
    FROM schedule s
    JOIN team_stats h
      ON h.Season = s.Season AND h.GameCode = {_V5_GC}
     AND UPPER(h.TeamName) = UPPER(s.hometeam)
    JOIN team_stats a
      ON a.Season = s.Season AND a.GameCode = {_V5_GC}
     AND UPPER(a.TeamName) = UPPER(s.awayteam)
    WHERE s.Season = ? AND s.round = 'RS' AND s.played = 'true'
      AND (s.homecode = ? OR s.awaycode = ?)
"""

_V5_H2H_SQL = f"""
    WITH matchups AS (
        SELECT CASE WHEN s.homecode = ? THEN h.Score ELSE a.Score END AS score_a,
               CASE WHEN s.homecode = ? THEN a.Score ELSE h.Score END AS score_b
        FROM schedule s
        JOIN team_stats h
          ON {_V5_GC} = h.GameCode AND s.Season = h.Season
         AND UPPER(h.TeamName) = UPPER(s.hometeam)
        JOIN team_stats a
          ON {_V5_GC} = a.GameCode AND s.Season = a.Season
         AND UPPER(a.TeamName) = UPPER(s.awayteam)
        WHERE s.played = 'true' AND s.Season BETWEEN ? AND ?
          AND ((s.homecode = ? AND s.awaycode = ?)
            OR (s.homecode = ? AND s.awaycode = ?))
    )
    SELECT COUNT(*), ROUND(AVG(score_a - score_b), 2) FROM matchups
"""


def _v5_team_dist(conn, code, season):
    row = conn.execute(_V5_TEAM_SQL, (season, code, code)).fetchone()
    if row and row[5] and row[5] >= V5_MIN_GAMES:
        d = dict(zip(V5_KEYS, row[:5]))
        d["games"] = row[5]
        return d
    return None


def _v5_league_dist(conn, season):
    for yr in (season, season - 1):
        row = conn.execute(_V5_LEAGUE_SQL, (yr,)).fetchone()
        if row and row[5] and row[5] >= V5_MIN_LEAGUE_ROWS:
            return dict(zip(V5_KEYS, row[:5]))
    return None


def _v5_team_profile(conn, code, season, league):
    """Current season blended with the team's previous season.
    Without a previous season, the profile is pulled toward the league average."""
    cur = _v5_team_dist(conn, code, season)
    prev = _v5_team_dist(conn, code, season - 1)
    prior, source = (prev, "previous season") if prev else (league, "league average")
    n = cur["games"] if cur else 0
    w = min(1.0, n / V5_N_FULL)
    prof = {}
    for k in V5_KEYS:
        c = cur[k] if cur else None
        p = prior[k]
        if c is None:
            prof[k] = p
        elif p is None:
            prof[k] = c
        else:
            prof[k] = w * c + (1 - w) * p
    prof["games"] = n
    prof["prior"] = source
    return prof


def _v5_win_rate(conn, code, season):
    row = conn.execute(_V5_WIN_SQL, (code, code, season, code, code)).fetchone()
    if row and row[0]:
        return row[0], (row[1] or 0) / row[0]
    return 0, None


def _v5_blended_win_rate(conn, code, season):
    g, cur = _v5_win_rate(conn, code, season)
    pg, prev = _v5_win_rate(conn, code, season - 1)
    prior = prev if prev is not None else 0.5
    if cur is None:
        return prior
    w = min(1.0, g / V5_N_FULL)
    return w * cur + (1 - w) * prior


def _v5_h2h(conn, code_a, code_b, season):
    season_min = season - V5_H2H_SEASONS + 1
    row = conn.execute(_V5_H2H_SQL, (code_a, code_a, season_min, season,
                                 code_a, code_b, code_b, code_a)).fetchone()
    if row and row[0]:
        return {"games": row[0], "margin": row[1] or 0.0}
    return {"games": 0, "margin": 0.0}


def v5_predict_match(conn, season, hcode, acode):
    league = _v5_league_dist(conn, season)
    if league is None:
        return {"error": "League averages not available"}
    h = _v5_team_profile(conn, hcode, season, league)
    a = _v5_team_profile(conn, acode, season, league)
    for prof in (h, a):
        if any(prof[k] is None for k in V5_KEYS):
            return {"error": "Team profile incomplete"}

    # 1. Opponent adjusted simulation: expected points of each team from its own
    #    attack, the opponent's defense and the pace of the game.
    L = league["avg_ortg"]
    pace = (h["avg_pace"] + a["avg_pace"]) / 2
    mu_h = pace * (h["avg_ortg"] + a["avg_drtg"] - L) / 100
    mu_a = pace * (a["avg_ortg"] + h["avg_drtg"] - L) / 100
    seed = zlib.crc32(f"{season}|RS|{hcode}|{acode}|v5".encode())
    rng = np.random.default_rng(seed)
    hs = rng.normal(mu_h, h["std_pts"], V5_N_SIMS)
    as_ = rng.normal(mu_a, a["std_pts"], V5_N_SIMS)
    mc_raw = float(np.mean(hs > as_))

    # 2. Season signal: 60% simulation, 40% win percentage
    h_wp = _v5_blended_win_rate(conn, hcode, season)
    a_wp = _v5_blended_win_rate(conn, acode, season)
    season_prob = _v5_logistic(h_wp - a_wp)
    current = 0.6 * mc_raw + 0.4 * season_prob

    # 3. Head to head over 4 seasons (neutral under 4 meetings)
    g = _v5_h2h(conn, hcode, acode, season)
    h2h_prob = 0.5
    if g["games"] >= V5_H2H_MIN_GAMES:
        reliability = min(1.0, g["games"] / 10.0)
        raw = _v5_logistic(g["margin"] / 15.0)
        h2h_prob = 0.5 + reliability * (raw - 0.5)

    # 4. Style: net rating gap (ORTG minus DRTG) between the two teams
    h_net = h["avg_ortg"] - h["avg_drtg"]
    a_net = a["avg_ortg"] - a["avg_drtg"]
    style = _v5_logistic((h_net - a_net) / 40.0)

    # 5. Weighted average on a neutral court, then the home court shift
    neutral = _v5_clamp(V5_W_SEASON * current + V5_W_H2H * h2h_prob + V5_W_STYLE * style)
    p_home = _v5_clamp(_v5_sigmoid(_v5_logit(neutral) + V5_HOME_SHIFT))

    return {
        "home_prob": round(p_home, 3),
        "away_prob": round(1 - p_home, 3),
        "mc_raw": round(mc_raw, 3),
        "components": {
            "current_season": round(current, 3),
            "season_prob": round(season_prob, 3),
            "h2h": round(h2h_prob, 3),
            "style_matchup": round(style, 3),
            "neutral_court": round(neutral, 3),
            "home_shift": round(V5_HOME_SHIFT, 3),
            "exp_pts_home": round(mu_h, 1),
            "exp_pts_away": round(mu_a, 1),
            "home_games": h["games"],
            "away_games": a["games"],
            "home_prior": h["prior"],
            "away_prior": a["prior"],
            "h2h_games": g["games"],
        },
    }


# =============================================================================
# DISPATCHER: version 5 for regular season games, previous model otherwise
# =============================================================================
@st.cache_data(ttl=300)
def predict_by_gamecode(gamecode: int, season: int) -> dict:
    conn = get_conn()
    row = conn.execute("""
        SELECT round, homecode, awaycode
        FROM schedule
        WHERE Season = ?
          AND CAST(SUBSTR(gamecode, INSTR(gamecode, '_') + 1) AS INTEGER) = ?
    """, (season, gamecode)).fetchone()
    if row and row[0] == "RS":
        return v5_predict_match(conn, season, row[1], row[2])
    return _predict_legacy(gamecode, season)


# =============================================================================
# VISUAL HELPERS
# =============================================================================
METRICS = ["ORTG", "DRTG", "NETRTG", "OREB%", "REB%", "AST%", "eFG%", "TOV%"]

METRIC_SCALE = {
    "ORTG":   8.0, "DRTG":   8.0, "NETRTG": 10.0,
    "OREB%":  5.0, "REB%":   4.0, "AST%":   6.0,
    "eFG%":   4.0, "TOV%":   2.5,
}
LOWER_IS_BETTER = {"DRTG", "TOV%"}


def colour_intensity(hv, av, metric: str) -> tuple[float, float]:
    if hv is None or av is None:
        return (0.0, 0.0)
    diff = hv - av
    if metric in LOWER_IS_BETTER:
        diff = -diff
    scale = METRIC_SCALE.get(metric, 5.0)
    norm = max(-1.0, min(1.0, diff / scale))
    return (norm, -norm)


def render_comparison_styled(label: str, home: str, away: str,
                              h_stats: dict, a_stats: dict):
    st.markdown(f"**{label}**")
    if not h_stats or not a_stats:
        st.info("Not enough games yet for this scope.")
        return

    def bg(intensity):
        if intensity >= 0:
            alpha = min(0.55, intensity * 0.6)
            return f"rgba(46, 160, 67, {alpha:.3f})"
        alpha = min(0.55, -intensity * 0.6)
        return f"rgba(218, 54, 51, {alpha:.3f})"

    rows_html = ""
    for m in METRICS:
        hv = h_stats.get(m)
        av = a_stats.get(m)
        h_int, a_int = colour_intensity(hv, av, m)
        hv_s = f"{hv:.1f}" if hv is not None else "-"
        av_s = f"{av:.1f}" if av is not None else "-"
        diff = f"{hv - av:+.1f}" if (hv is not None and av is not None) else ""
        rows_html += (
            f"<tr>"
            f"<td style='background:{bg(h_int)};padding:8px 6px;text-align:center;"
            f"font-weight:bold;border-bottom:1px solid #eee;color:#1a1a1a;'>{hv_s}</td>"
            f"<td style='background:#f5f5f5;padding:8px 6px;text-align:center;"
            f"color:#555;border-bottom:1px solid #eee;'>{m}</td>"
            f"<td style='background:{bg(a_int)};padding:8px 6px;text-align:center;"
            f"font-weight:bold;border-bottom:1px solid #eee;color:#1a1a1a;'>{av_s}</td>"
            f"<td style='padding:8px 6px;text-align:center;color:#888;"
            f"font-size:0.85rem;border-bottom:1px solid #eee;'>{diff}</td>"
            f"</tr>"
        )

    table_html = (
        "<div style='width:100%;overflow-x:auto;'>"
        "<table style='width:100%;border-collapse:collapse;table-layout:fixed;"
        "font-family:sans-serif;font-size:0.9rem;'>"
        "<thead><tr>"
        f"<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        f"font-weight:600;width:27%;border-bottom:2px solid #ddd;white-space:nowrap;"
        f"overflow:hidden;text-overflow:ellipsis;'>{home}</th>"
        "<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        "font-weight:600;width:22%;border-bottom:2px solid #ddd;'>Metric</th>"
        f"<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        f"font-weight:600;width:27%;border-bottom:2px solid #ddd;white-space:nowrap;"
        f"overflow:hidden;text-overflow:ellipsis;'>{away}</th>"
        "<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        "font-weight:600;width:22%;border-bottom:2px solid #ddd;'>Δ</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def render_comparison_form(home: str, away: str,
                           h_stats: dict, a_stats: dict,
                           h_recent: dict, a_recent: dict):
    """Season table where each cell carries a small gauge: dot = season value,
    line end = Last 5 value (green better, red worse). The Last 5 number sits
    next to the gauge, never on top of the line."""
    if not h_stats or not a_stats:
        st.info("Not enough games yet for this scope.")
        return

    def bg(intensity):
        if intensity >= 0:
            return f"rgba(46, 160, 67, {min(0.55, intensity * 0.6):.3f})"
        return f"rgba(218, 54, 51, {min(0.55, -intensity * 0.6):.3f})"

    def gauge(metric, season_v, l5_v):
        if season_v is None or l5_v is None:
            return ""
        d = float(l5_v) - float(season_v)
        better = (d < 0) if metric in LOWER_IS_BETTER else (d > 0)
        col = "#2ea043" if better else "#da3633"
        norm = max(-1.0, min(1.0, d / METRIC_SCALE.get(metric, 5.0)))
        end = 50 + norm * 42
        lo, hi = min(50, end), max(50, end)
        parts = ("<div style='position:relative;flex:1;height:16px;'>"
                 "<div style='position:absolute;left:8%;right:8%;top:7px;height:2px;"
                 "background:rgba(20,33,61,0.18);border-radius:2px;'></div>")
        if abs(d) >= LAST5_MIN_GAP * METRIC_SCALE.get(metric, 5.0):
            parts += (f"<div style='position:absolute;left:{lo:.1f}%;width:{hi - lo:.1f}%;"
                      f"top:6px;height:4px;background:{col};border-radius:2px;'></div>"
                      f"<div style='position:absolute;left:calc({end:.1f}% - 6px);top:2px;"
                      f"width:12px;height:12px;border-radius:50%;background:{col};"
                      f"border:2px solid #F3EEE4;box-sizing:border-box;'></div>")
        parts += ("<div style='position:absolute;left:calc(50% - 4px);top:4px;width:8px;"
                  "height:8px;border-radius:50%;background:#14213D;'></div></div>")
        return (f"<div style='display:flex;align-items:center;gap:8px;margin-top:2px;'>"
                f"{parts}<div style='width:46px;text-align:right;font-size:0.8rem;"
                f"color:#6B7280;font-weight:600;'>{float(l5_v):.1f}</div></div>")

    def legend_item(kind, col, text):
        if kind == "dot":
            glyph = (f"<span style='display:inline-block;width:11px;height:11px;border-radius:50%;"
                     f"background:{col};margin-right:8px;vertical-align:middle;'></span>")
        else:
            glyph = (f"<span style='display:inline-block;width:20px;height:4px;background:{col};"
                     f"border-radius:2px;margin-right:2px;vertical-align:middle;'></span>"
                     f"<span style='display:inline-block;width:13px;height:13px;border-radius:50%;"
                     f"background:{col};margin-right:8px;vertical-align:middle;'></span>")
        return f"<span style='margin:0 14px;white-space:nowrap;'>{glyph}{text}</span>"

    legend = ("<div style='text-align:center;font-size:1.1rem;color:#6B7280;margin:2px 0 12px 0;'>"
              + legend_item("dot", "#14213D", "Season")
              + legend_item("end", "#2ea043", "Last 5 better")
              + legend_item("end", "#da3633", "Last 5 worse")
              + "</div>")

    rows_html = ""
    for m in METRICS:
        hv, av = h_stats.get(m), a_stats.get(m)
        h_int, a_int = colour_intensity(hv, av, m)
        hv_s = f"{hv:.1f}" if hv is not None else "-"
        av_s = f"{av:.1f}" if av is not None else "-"
        cell = ("padding:8px 12px 7px 12px;border-bottom:1px solid #F3EEE4;"
                "color:#14213D;")
        rows_html += (
            "<tr>"
            f"<td style='background:{bg(h_int)};{cell}'>"
            f"<div style='text-align:center;font-weight:700;font-size:1.2rem;line-height:1.2;'>{hv_s}</div>"
            f"{gauge(m, hv, (h_recent or {}).get(m))}</td>"
            f"<td style='background:#FBF8F1;padding:8px 6px;text-align:center;color:#14213D;"
            f"font-weight:600;border-bottom:1px solid #E1D8C6;'>{m}</td>"
            f"<td style='background:{bg(a_int)};{cell}'>"
            f"<div style='text-align:center;font-weight:700;font-size:1.2rem;line-height:1.2;'>{av_s}</div>"
            f"{gauge(m, av, (a_recent or {}).get(m))}</td>"
            "</tr>"
        )

    th = ("padding:8px 4px;text-align:center;color:#6B7280;font-size:0.85rem;font-weight:600;"
          "border-bottom:2px solid #14213D;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;")
    table_html = (
        "<div style='width:100%;max-width:820px;margin:0 auto;overflow-x:auto;'>"
        "<div style='text-align:center;font-weight:700;font-size:1.15rem;color:#14213D;"
        "margin-bottom:4px;'>Season</div>"
        f"{legend}"
        "<table style='width:100%;border-collapse:collapse;table-layout:fixed;font-size:0.9rem;'>"
        "<thead><tr>"
        f"<th style='{th}width:37%;'>{home}</th>"
        f"<th style='{th}width:26%;'></th>"
        f"<th style='{th}width:37%;'>{away}</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def render_team_header(code: str, disp_name: str,
                       standings_row: dict | None,
                       form_seq: list[bool],
                       is_postseason: bool = False):
    lp = logo_path(code)
    logo_html = ""
    if lp:
        b64 = logo_b64(code)
        zoom = logo_zoom(code)
        max_h = int(110 * zoom)
        max_w = int(130 * zoom)
        max_h = max(70, min(max_h, 130))
        max_w = max(80, min(max_w, 160))
        logo_html = (
            f"<img src='data:image/png;base64,{b64}' "
            f"style='max-height:{max_h}px; max-width:{max_w}px; "
            f"object-fit:contain;'/>"
        )

    if standings_row and not is_postseason:
        rk = standings_row.get("rank", "?")
        w = int(standings_row.get("wins", 0))
        l = int(standings_row.get("losses", 0))
        standings_text = f"#{rk} · {w}W {l}L"
    else:
        standings_text = ""

    squares = ""
    if form_seq:
        for win in reversed(form_seq):
            colour = "#2ea043" if win else "#da3633"
            squares += (
                f"<span style='display:inline-block;width:14px;height:14px;"
                f"background:{colour};margin-right:3px;border-radius:2px;'></span>"
            )

    html = (
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "font-family:sans-serif;'>"
        "<div style='height:140px;display:flex;align-items:center;"
        f"justify-content:center;width:100%;'>{logo_html}</div>"
        "<div style='font-weight:bold;font-size:1rem;text-align:center;"
        "min-height:48px;line-height:1.2;margin-top:8px;display:flex;"
        f"align-items:center;justify-content:center;'>{disp_name}</div>"
        "<div style='color:#888;font-size:0.85rem;height:22px;"
        f"text-align:center;margin-top:4px;'>{standings_text}</div>"
        "<div style='height:20px;text-align:center;margin-top:4px;'>"
        f"{squares}</div>"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def render_match_card(hcode: str, acode: str,
                      home_disp: str, away_disp: str,
                      score_text: str | None,
                      status_text: str,
                      status_colour: str,
                      game_date: str | None,
                      series_score: dict | None = None):
    h_b64 = logo_b64(hcode)
    a_b64 = logo_b64(acode)
    h_zoom = logo_zoom(hcode)
    a_zoom = logo_zoom(acode)

    h_logo = (
        f"<img src='data:image/png;base64,{h_b64}' "
        f"style='max-height:{int(60 * h_zoom)}px;max-width:{int(70 * h_zoom)}px;"
        f"object-fit:contain;'/>"
        if h_b64 else ""
    )
    a_logo = (
        f"<img src='data:image/png;base64,{a_b64}' "
        f"style='max-height:{int(60 * a_zoom)}px;max-width:{int(70 * a_zoom)}px;"
        f"object-fit:contain;'/>"
        if a_b64 else ""
    )

    middle = (
        f"<div style='font-size:1.5rem;font-weight:bold;color:#1a1a1a;"
        f"letter-spacing:1px;'>{score_text}</div>"
        if score_text
        else "<div style='font-size:1.2rem;font-weight:bold;color:#666;'>VS</div>"
    )

    date_html = (
        f"<div style='font-size:0.75rem;color:#999;margin-top:2px;'>{game_date}</div>"
        if game_date else ""
    )

    series_html = ""
    if series_score and series_score["games_played"] > 0:
        hw = series_score["home_wins"]
        aw = series_score["away_wins"]
        if hw > aw:
            hw_col, aw_col = "#2ea043", "#1a1a1a"
        elif aw > hw:
            hw_col, aw_col = "#1a1a1a", "#2ea043"
        else:
            hw_col, aw_col = "#1a1a1a", "#1a1a1a"
        series_html = (
            f"<div style='margin-top:6px;font-size:0.8rem;color:#555;"
            f"font-weight:500;letter-spacing:0.3px;'>Series</div>"
            f"<div style='font-size:1.1rem;font-weight:bold;letter-spacing:2px;'>"
            f"<span style='color:{hw_col};'>{hw}</span>"
            f"<span style='color:#aaa;margin:0 4px;'>-</span>"
            f"<span style='color:{aw_col};'>{aw}</span>"
            f"</div>"
        )

    html = (
        "<div style='display:flex;align-items:center;justify-content:space-between;"
        "padding:4px 0 12px 0;gap:12px;'>"
        "<div style='flex:1;display:flex;flex-direction:column;align-items:center;"
        "min-width:0;'>"
        f"<div style='height:72px;display:flex;align-items:center;justify-content:center;'>"
        f"{h_logo}</div>"
        f"<div style='font-weight:600;font-size:0.9rem;text-align:center;"
        f"margin-top:6px;color:#1a1a1a;line-height:1.2;min-height:36px;"
        f"display:flex;align-items:center;'>{home_disp}</div>"
        "</div>"
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "min-width:80px;'>"
        f"{middle}"
        f"<div style='font-size:0.75rem;color:{status_colour};margin-top:6px;"
        f"font-weight:600;text-transform:uppercase;letter-spacing:0.5px;'>"
        f"{status_text}</div>"
        f"{date_html}"
        f"{series_html}"
        "</div>"
        "<div style='flex:1;display:flex;flex-direction:column;align-items:center;"
        "min-width:0;'>"
        f"<div style='height:72px;display:flex;align-items:center;justify-content:center;'>"
        f"{a_logo}</div>"
        f"<div style='font-weight:600;font-size:0.9rem;text-align:center;"
        f"margin-top:6px;color:#1a1a1a;line-height:1.2;min-height:36px;"
        f"display:flex;align-items:center;'>{away_disp}</div>"
        "</div>"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# WIN PROBABILITY UI
# =============================================================================
def render_win_probability(pred: dict, home_disp: str, away_disp: str,
                           round_: str):
    hp = pred["home_prob"]
    ap = pred["away_prob"]

    if round_ == "FF":
        section_label = "Match edge (Final Four · neutral court)"
    elif round_ == "PO":
        section_label = "Match edge (Playoffs)"
    elif round_ == "PI":
        section_label = "Win probability (Play-In)"
    else:
        section_label = "Win probability"

    st.markdown(f"**{section_label}**")

    bar_html = (
        "<div style='margin:12px 0 6px 0;'>"
        "<div style='display:flex;height:28px;border-radius:4px;overflow:hidden;'>"
        f"<div style='flex:{hp:.3f};background:#2ea043;'></div>"
        f"<div style='flex:{ap:.3f};background:#da3633;'></div>"
        "</div>"
        "<div style='display:flex;justify-content:space-between;"
        "margin-top:6px;font-size:0.9rem;font-weight:bold;'>"
        f"<span style='color:#2ea043;'>{home_disp}  {hp*100:.1f}%</span>"
        f"<span style='color:#da3633;'>{ap*100:.1f}%  {away_disp}</span>"
        "</div>"
        "</div>"
    )
    st.markdown(bar_html, unsafe_allow_html=True)

    st.caption(f"Based on {MC_N_SIMULATIONS:,} simulations")

    with st.popover("How is this calculated?"):
        if round_ == "RS":
            st.markdown(
                "The win probability combines three signals: each team's season performance, "
                "their head to head history over the last 4 seasons, and their style matchup. "
                "The season signal comes from a Monte Carlo simulation of 10,000 games based on "
                "each team's attack, the opponent's defense and the pace of the game. "
                "The home court edge observed in past games is applied last."
            )
        elif round_ == "FF":
            st.markdown(
                "The match edge combines each team's current season efficiency, "
                "their head-to-head history over the last 4 seasons, "
                "and offensive/defensive style matchup. "
                "Home court advantage is removed as the Final Four is played on neutral ground. "
                "Monte Carlo simulation models thousands of possible outcomes "
                "based on each team's historical scoring distribution in high-stakes games."
            )
        else:
            st.markdown(
                "The match edge integrates the current series context alongside "
                "each team's season efficiency, head-to-head history over the last 4 seasons, "
                "home court advantage, and style matchup. "
                "As the series progresses, the influence of games already played increases, "
                "reflecting that recent playoff performance is the strongest predictor "
                "of the next game outcome. "
                "Monte Carlo simulation models thousands of possible game outcomes "
                "based on each team's playoff scoring distribution."
            )


# =============================================================================
# PNG EXPORT (matplotlib)
# =============================================================================
EL_GREEN = "#2ea043"
EL_RED   = "#da3633"
BG_WHITE = "#ffffff"


def mpl_colour(intensity: float) -> tuple[float, float, float, float]:
    if intensity >= 0:
        alpha = min(0.55, intensity * 0.6)
        return (46/255, 160/255, 67/255, alpha)
    alpha = min(0.55, -intensity * 0.6)
    return (218/255, 54/255, 51/255, alpha)


RADAR_RANGES = {
    "ORTG":   (95,  130),
    "DRTG":   (95,  130),
    "NETRTG": (-20, 20),
    "OREB%":  (20,  45),
    "REB%":   (42,  58),
    "AST%":   (45,  80),
    "eFG%":   (44,  62),
    "TOV%":   (8,   20),
}
RADAR_LOWER_IS_BETTER = {"DRTG", "TOV%"}


def _normalize_radar(value, metric):
    if value is None:
        return 0.5
    lo, hi = RADAR_RANGES[metric]
    norm = (value - lo) / (hi - lo)
    norm = max(0.0, min(1.0, norm))
    if metric in RADAR_LOWER_IS_BETTER:
        norm = 1.0 - norm
    return norm


def build_radar_png(home_name: str, away_name: str,
                    h_stats: dict, a_stats: dict,
                    title: str) -> bytes:
    """Radar in the ELSTATSLAB paper style: home navy, away orange, real values under each axis.
    Shape size is a fixed scale per metric (DRTG and TOV% flipped), so bigger is always better."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import FancyBboxPatch

    NAVY_, ORANGE_, GREY_, RULE_, CARD_ = "#14213D", "#E4572E", "#6B7280", "#E1D8C6", "#FBF8F1"
    labels = METRICS
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ang_c = np.concatenate([angles, angles[:1]])

    def _vals(stats):
        v = [_normalize_radar(stats.get(m), m) for m in labels]
        return np.array(v + v[:1])

    h_vals, a_vals = _vals(h_stats), _vals(a_stats)

    fig = Figure(figsize=(6.0, 6.6), dpi=150)
    FigureCanvasAgg(fig)
    fig.patch.set_alpha(0.0)
    card = FancyBboxPatch((0.01, 0.01), 0.98, 0.98, boxstyle="round,pad=0,rounding_size=0.035",
                          transform=fig.transFigure, facecolor=CARD_, edgecolor=RULE_,
                          linewidth=1.6, zorder=0)
    fig.add_artist(card)

    # header: title + team chips
    fig.text(0.5, 0.945, title.upper(), ha="center", va="center", fontproperties=BARLOW_BOLD,
             fontsize=19, color=NAVY_)
    from matplotlib.lines import Line2D
    for x0, nm, col in ((0.27, home_name, NAVY_), (0.73, away_name, ORANGE_)):
        fig.add_artist(Line2D([x0 - 0.17], [0.895], marker="o", markersize=10, color=col,
                              transform=fig.transFigure, linestyle="none"))
        fig.text(x0 - 0.145, 0.895, nm, ha="left", va="center", fontproperties=BARLOW_SEMIBOLD,
                 fontsize=14, color=col)

    ax = fig.add_axes([0.20, 0.215, 0.60, 0.60 * 6.0 / 6.6], polar=True)
    ax.set_facecolor("none")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1)
    ax.set_xticks(angles)
    ax.set_xticklabels([])
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels([])
    ax.grid(color=RULE_, linewidth=1.0)
    ax.spines["polar"].set_color(RULE_)
    ax.spines["polar"].set_linewidth(1.6)
    # median ring (50 percent of the scale) dashed
    ring = np.linspace(0, 2 * np.pi, 200)
    ax.plot(ring, [0.5] * len(ring), color="#CFC5B0", linewidth=1.2, linestyle=(0, (3, 3)), zorder=1)

    for vals, col in ((a_vals, ORANGE_), (h_vals, NAVY_)):
        ax.fill(ang_c, vals, color=col, alpha=0.16, zorder=2)
        ax.plot(ang_c, vals, color=col, linewidth=3.0, solid_joinstyle="round", zorder=3)
        ax.scatter(ang_c[:-1], vals[:-1], s=46, color=col, edgecolor=CARD_, linewidth=1.4, zorder=4)

    # axis labels with the real values under each name
    def _fmt(v):
        return "n/a" if v is None else f"{v:.1f}"

    for ang, m in zip(angles, labels):
        x, y = np.sin(ang), np.cos(ang)
        ha = "center" if abs(x) < 0.2 else ("left" if x > 0 else "right")
        r = 1.17
        ax.text(ang, r, m, ha=ha, va="center", fontproperties=BARLOW_BOLD, fontsize=15,
                color=NAVY_, transform=ax.transData)
        # value line: placed slightly further out on the same ray
        for dy, st_, col in ((-17, h_stats, NAVY_), (-31, a_stats, ORANGE_)):
            ax.annotate(_fmt(st_.get(m)), xy=(ang, 1.17), xycoords="data", xytext=(0, dy),
                        textcoords="offset points", annotation_clip=False, ha=ha, va="center",
                        fontproperties=BARLOW_SEMIBOLD, fontsize=12.5, color=col)

    fig.text(0.5, 0.04, "Shape size uses a fixed scale per metric. DRTG and TOV% are flipped, so bigger is better.",
             ha="center", va="center", fontproperties=BARLOW_REGULAR, fontsize=10.5, color=GREY_)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, transparent=True)
    return buf.getvalue()


# Matchup PNG export lives in preview_export.py (ELSTATSLAB paper style)
from preview_export import build_preview_png  # noqa: E402



# =============================================================================
# IMPACT PULSE
# =============================================================================

IP_METRICS_DISPLAY = ["ORTG", "DRTG", "NETRTG", "OREB%", "REB%", "AST%", "eFG%", "TOV%"]
IP_LOWER_IS_BETTER = {"DRTG", "TOV%"}

IP_METRIC_COLS = {
    "ORTG":   ("on_ortg",  "off_ortg"),
    "DRTG":   ("on_drtg",  "off_drtg"),
    "NETRTG": ("on_netrtg","off_netrtg"),
    "OREB%":  ("on_oreb",  "off_oreb"),
    "REB%":   ("on_reb",   "off_reb"),
    "AST%":   ("on_ast",   "off_ast"),
    "eFG%":   ("on_efg",   "off_efg"),
    "TOV%":   ("on_tov",   "off_tov"),
}


@st.cache_data(ttl=600)
def load_impact_pulse(gamecode: int, season: int) -> pd.DataFrame:
    try:
        q = """
            SELECT * FROM impact_pulse
            WHERE season = ? AND gamecode = ?
        """
        return pd.read_sql(q, get_conn(), params=(season, gamecode))
    except Exception:
        return pd.DataFrame()


def _ip_min_poss(r) -> int:
    """Seuil minimum de possessions utilise pour cette ligne (12 par defaut
    pour les lignes calculees avant l'ajout de la colonne min_poss)."""
    try:
        if "min_poss" in r.index and pd.notna(r["min_poss"]):
            return int(round(r["min_poss"]))
    except Exception:
        pass
    return 12


def _ip_delta_bg(metric: str, delta: float) -> str:
    """Couleur de fond pour une cellule delta On/Off."""
    if metric in IP_LOWER_IS_BETTER:
        good = delta <= -0.3
        bad  = delta >= 0.3
    else:
        good = delta >= 0.3
        bad  = delta <= -0.3

    if good:
        return "rgba(46,160,67,0.22)"
    if bad:
        return "rgba(218,54,51,0.22)"
    return "#f5f5f5"


_IP_CSS = """<style>
.ip-card{background:#fff;border:1px solid #E1D8C6;border-top:5px solid var(--ip-side,#14213D);
 border-radius:16px;padding:18px 22px 16px;margin:4px 0 12px;}
.ip-kick{font-size:.95rem;letter-spacing:.14em;text-transform:uppercase;color:#6B7280;font-weight:600;}
.ip-name{font-size:2.1rem;font-weight:700;color:#14213D;line-height:1.1;margin-top:2px;}
.ip-pills{margin-top:10px;display:flex;flex-wrap:wrap;gap:8px;}
.ip-pill{font-size:.85rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
 padding:3px 12px;border-radius:999px;border:1px solid;}
.ip-pill.dm{color:#E4572E;border-color:#E4572E;background:rgba(228,87,46,.10);}
.ip-pill.pos{color:#1E7A37;border-color:#2ea043;background:rgba(46,160,67,.12);}
.ip-pill.neg{color:#B02A28;border-color:#da3633;background:rgba(218,54,51,.10);}
.ip-sub{margin-top:10px;font-size:1rem;color:#6B7280;}
.ip-tbl{background:#fff;border:1px solid #E1D8C6;border-radius:16px;overflow:hidden;margin-bottom:12px;}
.ip-row{display:grid;grid-template-columns:1.25fr 1fr 1fr 1fr;align-items:stretch;
 border-top:1px solid #EFE8D8;font-variant-numeric:tabular-nums;}
.ip-row.h{background:#FBF8F1;border-top:none;border-bottom:2px solid #14213D;}
.ip-row>div{padding:9px 14px;font-size:1.05rem;display:flex;align-items:center;justify-content:center;}
.ip-row>div:first-child{justify-content:flex-start;font-weight:700;color:#14213D;letter-spacing:.03em;}
.ip-row.h>div{font-size:.85rem;letter-spacing:.12em;color:#6B7280;font-weight:700;padding:8px 14px;}
.ip-on{font-weight:700;color:#14213D;}
.ip-on.good{background:rgba(46,160,67,.18);}
.ip-on.bad{background:rgba(218,54,51,.16);}
.ip-off{color:#6B7280;}
.ip-d{font-weight:700;color:#6B7280;}
.ip-d.good{color:#1E7A37;}
.ip-d.bad{color:#B02A28;}
.ip-rk{background:#fff;border:1px solid #E1D8C6;border-radius:12px;overflow-x:auto;}
.ip-rk .ip-rrow{display:grid;grid-template-columns:34px 1.6fr .8fr 1fr 1fr .9fr .9fr;min-width:560px;
 border-top:1px solid #EFE8D8;font-variant-numeric:tabular-nums;}
.ip-rk .ip-rrow>div{padding:7px 10px;font-size:.98rem;text-align:right;color:#14213D;}
.ip-rk .ip-rrow>div:nth-child(2){text-align:left;font-weight:600;}
.ip-rk .ip-rrow>div:first-child{text-align:center;color:#6B7280;}
.ip-rk .ip-rrow.h{background:#FBF8F1;border-top:none;border-bottom:2px solid #14213D;}
.ip-rk .ip-rrow.h>div{white-space:nowrap;font-size:.8rem;letter-spacing:.1em;color:#6B7280;font-weight:700;}
.ip-rk .ip-rrow.top{background:rgba(228,87,46,.07);}
.ip-rk .ip-rrow.top>div:nth-child(2){color:#E4572E;}
.ip-rk .ip-rrow>div.good{color:#1E7A37;font-weight:700;}
.ip-rk .ip-rrow>div.bad{color:#B02A28;font-weight:700;}
</style>"""


def _ip_state(metric: str, delta: float) -> str:
    """good / bad / flat for an On/Off delta (threshold 0.3, lower is better for DRTG and TOV%)."""
    if metric in IP_LOWER_IS_BETTER:
        delta = -delta
    if delta >= 0.3:
        return "good"
    if delta <= -0.3:
        return "bad"
    return ""


def _ip_card_html(disp: str, r, side_color: str) -> str:
    import html as _h
    score = float(r["impact_score"])
    cls = "pos" if score >= 0 else "neg"
    return (
        _IP_CSS
        + f"<div class='ip-card' style='--ip-side:{side_color};'>"
        f"<div class='ip-kick'>{_h.escape(str(disp))}</div>"
        f"<div class='ip-name'>{_h.escape(str(r['player_name']))}</div>"
        f"<div class='ip-pills'><span class='ip-pill dm'>Difference Maker</span>"
        f"<span class='ip-pill {cls}'>Impact Score {score:+.2f}</span></div>"
        f"<div class='ip-sub'>{int(round(r['on_poss']))} poss ON · min. threshold {_ip_min_poss(r)} poss</div>"
        f"</div>"
    )


def _ip_table_html(r) -> str:
    rows = ""
    for m in IP_METRICS_DISPLAY:
        on_col, off_col = IP_METRIC_COLS[m]
        on_val, off_val = r[on_col], r[off_col]
        delta = on_val - off_val
        st_ = _ip_state(m, delta)
        rows += (
            f"<div class='ip-row'><div>{m}</div>"
            f"<div class='ip-on {st_}'>{on_val:.1f}</div>"
            f"<div class='ip-off'>{off_val:.1f}</div>"
            f"<div class='ip-d {st_}'>{delta:+.1f}</div></div>"
        )
    return (
        "<div class='ip-tbl'>"
        "<div class='ip-row h'><div>METRIC</div><div>ON</div><div>OFF</div><div>DIFF</div></div>"
        f"{rows}</div>"
    )


def _ip_ranking_html(ranking: list) -> str:
    import html as _h
    out = ("<div class='ip-rk'><div class='ip-rrow h'><div>#</div><div>PLAYER</div><div>SCORE</div>"
           "<div>NET ON</div><div>NET OFF</div><div>DIFF</div><div>POSS</div></div>")
    for i, p in enumerate(ranking, 1):
        on_, off_ = p.get("on_netrtg", 0), p.get("off_netrtg", 0)
        d = on_ - off_
        sc = p["score"]
        sc_cls = "good" if sc > 0 else ("bad" if sc < 0 else "")
        d_cls = "good" if d >= 0.3 else ("bad" if d <= -0.3 else "")
        out += (
            f"<div class='ip-rrow{' top' if i == 1 else ''}'><div>{i}</div>"
            f"<div>{_h.escape(str(p['name']))}</div>"
            f"<div class='{sc_cls}'>{sc:+.2f}</div><div>{on_:.1f}</div><div>{off_:.1f}</div>"
            f"<div class='{d_cls}'>{d:+.1f}</div><div>{p.get('on_poss', 0):.0f}</div></div>"
        )
    return out + "</div>"



def render_impact_pulse_section(gamecode: int, season: int,
                                home_code: str, away_code: str,
                                home_disp: str, away_disp: str,
                                round_label: str,
                                card_index: int):
    """Affiche la section Impact Pulse dans un st.expander."""

    ip_df = load_impact_pulse(gamecode, season)

    with st.expander("⚡ Impact Pulse — Who moved the needle?"):
        if ip_df.empty:
            st.info("No Impact Pulse data available for this game.")
            return

        st.caption(
            "Impact Score — proprietary On/Off composite metric. "
            "Identifies the player who moved the needle most for his team within this game, "
            "across key efficiency metrics."
        )

        col_h, col_a = st.columns(2)

        for col, code, disp in [
            (col_h, home_code, home_disp),
            (col_a, away_code, away_disp),
        ]:
            row = ip_df[ip_df["team_code"].str.upper() == code.upper()]
            if row.empty:
                with col:
                    st.info(f"No data for {disp}")
                continue

            r = row.iloc[0]
            score = r["impact_score"]
            score_str = f"{score:+.2f}"
            score_color = "#2ea043" if score >= 0 else "#da3633"

            side_color = "#14213D" if col is col_h else "#E4572E"
            with col:
                st.markdown(_ip_card_html(disp, r, side_color), unsafe_allow_html=True)
                st.markdown(_ip_table_html(r), unsafe_allow_html=True)

                # Full ranking dans un expander imbriqué
                if r.get("full_ranking"):
                    try:
                        ranking = json.loads(r["full_ranking"])
                        with st.expander(f"Full ranking — {code}"):
                            st.markdown(_ip_ranking_html(ranking), unsafe_allow_html=True)
                    except Exception:
                        pass

        # ── Export PNG ────────────────────────────────────────────────────
        st.divider()
        png_key = f"ip_png_{card_index}_{gamecode}"
        if png_key not in st.session_state:
            if st.button(
                "📥 Generate Impact Pulse image",
                key=f"ip_btn_{card_index}_{gamecode}",
            ):
                with st.spinner("Generating Impact Pulse image..."):
                    st.session_state[png_key] = build_impact_pulse_png(
                        ip_df, home_code, away_code,
                        home_disp, away_disp, round_label,
                    )
                st.rerun()

        if png_key in st.session_state:
            st.download_button(
                label="📥 Download Impact Pulse image",
                data=st.session_state[png_key],
                file_name=f"ImpactPulse_{home_code}_vs_{away_code}.png",
                mime="image/png",
                key=f"ip_dl_{card_index}_{gamecode}",
            )


from impact_pulse_export import build_impact_pulse_png




# =============================================================================
# MATCH ANALYSIS RENDERER
# =============================================================================
def render_match_analysis(g: pd.Series, rnd: int, all_games: pd.DataFrame,
                          phase: str, round_label_long: str,
                          card_index: int,
                          official_standings: pd.DataFrame | None = None,
                          playoffs_schedule: pd.DataFrame | None = None,
                          rnd_season: int = 2025):
    home, away = g["hometeam"], g["awayteam"]
    hcode, acode = g["homecode"], g["awaycode"]
    home_disp = display_name(hcode, home)
    away_disp = display_name(acode, away)
    played = g["played"] == "true"
    is_postseason = phase in ("PI", "PO", "FF")
    is_playoffs = phase == "PO"

    up_to = int(rnd) if played else int(rnd) - 1
    standings_scope = team_season_stats(all_games, up_to, official_standings)

    if standings_scope.empty:
        st.info("No season data available yet.")
        return

    try:
        h_row = standings_scope.loc[
            standings_scope["team"].str.upper() == home.upper()
        ].iloc[0]
        a_row = standings_scope.loc[
            standings_scope["team"].str.upper() == away.upper()
        ].iloc[0]
    except IndexError:
        st.warning("One of the teams has no prior games in this season scope.")
        return

    h_season = h_row.to_dict()
    a_season = a_row.to_dict()
    h_recent = team_recent_stats(all_games, home, int(rnd))
    a_recent = team_recent_stats(all_games, away, int(rnd))
    h_form = team_form_sequence(all_games, home)
    a_form = team_form_sequence(all_games, away)

    series = None
    if is_playoffs and playoffs_schedule is not None:
        raw = get_series_score(playoffs_schedule, all_games, hcode, acode, int(rnd))
        series = orient_series(raw, hcode)

    hcol, mcol, acol = st.columns([1, 2, 1])
    with hcol:
        render_team_header(hcode, home_disp, h_season, h_form,
                           is_postseason=is_postseason)
    with mcol:
        vs_extra = ""
        if series and series["games_played"] > 0:
            hw = series["home_wins"]
            aw = series["away_wins"]
            hw_col = "#2ea043" if hw > aw else ("#da3633" if hw < aw else "#1a1a1a")
            aw_col = "#2ea043" if aw > hw else ("#da3633" if aw < hw else "#1a1a1a")
            vs_extra = (
                f"<div style='margin-top:8px;font-size:0.85rem;color:#555;"
                f"font-weight:500;'>Series</div>"
                f"<div style='font-size:1.3rem;font-weight:bold;letter-spacing:3px;'>"
                f"<span style='color:{hw_col};'>{hw}</span>"
                f"<span style='color:#aaa;margin:0 6px;'>-</span>"
                f"<span style='color:{aw_col};'>{aw}</span>"
                f"</div>"
            )
        st.markdown(
            "<div style='text-align:center; padding-top:40px;"
            f"font-size:24px; font-weight:bold;'>VS</div>"
            f"<div style='text-align:center;'>{vs_extra}</div>",
            unsafe_allow_html=True,
        )
    with acol:
        render_team_header(acode, away_disp, a_season, a_form,
                           is_postseason=is_postseason)

    st.divider()

    toggle_key = f"radar_{card_index}_{rnd}_{hcode}_{acode}"
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = False

    tcol1, tcol2, tcol3 = st.columns([2, 1, 2])
    with tcol2:
        use_radar = st.toggle("🕸️ Radar", key=toggle_key,
                              value=st.session_state[toggle_key])
    with tcol3:
        share_url = f"{SHARE_BASE_URL}/?" + urlencode(
            {"s": int(rnd_season), "r": int(rnd), "m": f"{hcode}_{acode}"}
        )
        with st.popover("🔗 Share"):
            st.caption("Copy this link to share this match:")
            st.code(share_url, language=None)

    use_form = (not played and phase == "RS" and int(rnd) >= LAST5_MIN_ROUND
                and bool(h_recent) and bool(a_recent))

    if use_form and not use_radar:
        render_comparison_form(home_disp, away_disp, h_season, a_season,
                               h_recent, a_recent)
    else:
        col1, col2 = st.columns(2)
        if use_radar:
            if played:
                h_game = team_single_game_stats(all_games, home, int(rnd))
                a_game = team_single_game_stats(all_games, away, int(rnd))
                right_label_str = "This Game"
                right_h, right_a = h_game, a_game
            else:
                right_label_str = f"Last {ROLLING_WINDOW}"
                right_h, right_a = h_recent, a_recent
            with col1:
                radar_png = build_radar_png(home_disp, away_disp, h_season, a_season, "Season")
                st.image(radar_png, use_container_width=True)
            with col2:
                radar_png2 = build_radar_png(home_disp, away_disp, right_h, right_a,
                                             right_label_str)
                st.image(radar_png2, use_container_width=True)
        else:
            with col1:
                render_comparison_styled("Season", home_disp, away_disp,
                                         h_season, a_season)
            with col2:
                if played:
                    h_game = team_single_game_stats(all_games, home, int(rnd))
                    a_game = team_single_game_stats(all_games, away, int(rnd))
                    render_comparison_styled("This Game", home_disp, away_disp,
                                             h_game, a_game)
                else:
                    render_comparison_styled(f"Last {ROLLING_WINDOW}",
                                             home_disp, away_disp,
                                             h_recent, a_recent)

    # ── Gameflow ──────────────────────────────────────────────────────────
    if played:
        raw_gc = g["gamecode"]
        if isinstance(raw_gc, str) and "_" in raw_gc:
            gc_num = int(raw_gc.split("_")[1])
        else:
            gc_num = int(raw_gc)

        gf_png = get_gameflow_png(gc_num, rnd_season, round_label=round_label_long, aspect="square")
        if gf_png is not None:
            st.divider()
            with st.expander("📊 Game Flow — Runs & Best 5 by NetRtg"):
                st.image(gf_png, use_container_width=True)

        # ── Impact Pulse ──────────────────────────────────────────────────
        st.divider()
        render_impact_pulse_section(
            gamecode=gc_num,
            season=rnd_season,
            home_code=hcode,
            away_code=acode,
            home_disp=home_disp,
            away_disp=away_disp,
            round_label=round_label_long,
            card_index=card_index,
        )

        # ── Lineups ───────────────────────────────────────────────────────
        if LINEUPS_IN_MATCH:
            lineup_tab.render_match_lineups(
                conn=get_conn(),
                season=rnd_season,
                game_code=gc_num,
                home_code=hcode,
                away_code=acode,
                home_disp=home_disp,
                away_disp=away_disp,
                round_label=round_label_long,
                card_index=card_index,
                elstatslab_logo=ELSTATSLAB_LOGO,
                team_logo_fn=logo_path,
            )

        # ── Shot Map ──────────────────────────────────────────────────────
        if SHOTMAPS_IN_MATCH:
            shotmap_ui.render_match_shotmaps(
                conn=get_conn(),
                season=rnd_season,
                game_code=gc_num,
                home_code=hcode,
                away_code=acode,
                home_disp=home_disp,
                away_disp=away_disp,
                round_label=round_label_long,
                card_index=card_index,
                elstatslab_logo=ELSTATSLAB_LOGO,
                team_logo_fn=logo_path,
            )

    st.divider()

    raw_gc = g["gamecode"]
    if isinstance(raw_gc, str) and "_" in raw_gc:
        gc_num = int(raw_gc.split("_")[1])
    else:
        gc_num = int(raw_gc)

    pred = predict_by_gamecode(gc_num, rnd_season)

    if not played:
        if "error" in pred:
            st.caption(f"Win probability unavailable: {pred['error']}")
        else:
            render_win_probability(pred, home_disp, away_disp, phase)

        st.divider()

    png_key = f"png_{card_index}_{rnd}_{hcode}_{acode}"
    if png_key not in st.session_state:
        if st.button("📥 Generate downloadable image",
                     key=f"btn_{card_index}_{rnd}_{hcode}_{acode}"):
            with st.spinner("Generating image..."):
                if played:
                    h_right_data = team_single_game_stats(all_games, home, int(rnd))
                    a_right_data = team_single_game_stats(all_games, away, int(rnd))
                    right_lbl = "This Game"
                else:
                    h_right_data = h_recent
                    a_right_data = a_recent
                    right_lbl = f"Last {ROLLING_WINDOW}"

                st.session_state[png_key] = build_preview_png(
                    home_code=hcode, home_name=home_disp,
                    home_rank=int(h_season["rank"]),
                    home_wl=f"{int(h_season['wins'])}W {int(h_season['losses'])}L",
                    home_form=h_form,
                    away_code=acode, away_name=away_disp,
                    away_rank=int(a_season["rank"]),
                    away_wl=f"{int(a_season['wins'])}W {int(a_season['losses'])}L",
                    away_form=a_form,
                    h_season=h_season, a_season=a_season,
                    h_right=h_right_data, a_right=a_right_data,
                    home_prob=pred.get("home_prob", 0.5),
                    away_prob=pred.get("away_prob", 0.5),
                    round_label=round_label_long,
                    show_prediction=not played,
                    right_label=right_lbl,
                    round_=phase,
                    series_score=series,
                    form_gauges=use_form,
                )
            st.rerun()

    if png_key in st.session_state:
        st.download_button(
            label="📥 Download",
            data=st.session_state[png_key],
            file_name=f"R{rnd}_{hcode}_vs_{acode}.png",
            mime="image/png",
            key=f"dl_{card_index}_{rnd}_{hcode}_{acode}",
        )


# =============================================================================
# APP
# =============================================================================
# =============================================================================
# SHARE LINKS
# =============================================================================
SHARE_BASE_URL = "https://elstatslab.streamlit.app"


def _read_deeplink_once() -> dict:
    """Reads s (season), r (round) and m (HOME_AWAY codes) from the URL, once
    per session. The parameters are then cleared from the address bar so the
    link copied from the browser never goes stale after the visitor browses."""
    if "_dl" in st.session_state:
        return st.session_state["_dl"]
    dl: dict = {}
    try:
        qp = st.query_params
        s, r, m = qp.get("s"), qp.get("r"), qp.get("m")
        if s and str(s).isdigit():
            dl["season"] = int(s)
        if r and str(r).isdigit():
            dl["round"] = int(r)
        if m and "_" in str(m) and str(m).replace("_", "").isalnum() and len(str(m)) <= 12:
            dl["match"] = str(m).upper()
        if ("round" in dl or "match" in dl) and "season" not in dl:
            dl["season"] = load_seasons()[0]
        if dl:
            st.query_params.clear()
    except Exception:
        dl = {}
    st.session_state["_dl"] = dl
    return dl


def render_match_center():
    dl = _read_deeplink_once()
    with st.sidebar:
        st.header("Filters")
        seasons = load_seasons()
        if dl.get("season") in seasons and not st.session_state.get("_dl_season_set"):
            st.session_state["season_select"] = dl["season"]
            st.session_state["_dl_season_set"] = True
        season = st.selectbox("Season", seasons, index=0, key="season_select")

    schedule_all = load_all_schedule(int(season))
    if schedule_all.empty:
        st.error("No schedule data available.")
        return

    round_labels = build_round_labels(schedule_all)
    all_rounds_sorted = sorted(round_labels.keys())

    q_full = """
        SELECT gameday, round AS phase, played
        FROM schedule
        WHERE Season = ?
    """
    full_sched = pd.read_sql(q_full, get_conn(), params=(int(season),))

    round_status = (
        full_sched.groupby("gameday")["played"]
        .apply(lambda s: (s == "true").all())
        .to_dict()
    )
    round_played_frac = (
        full_sched.groupby("gameday")["played"]
        .apply(lambda s: (s == "true").mean())
        .to_dict()
    )
    upcoming_rounds = [gd for gd in all_rounds_sorted if not round_status.get(gd, True)]
    current_round = upcoming_rounds[0] if upcoming_rounds else all_rounds_sorted[-1]

    # Share link: a visitor arriving with a round in the URL is anchored on it.
    dl_active = (
        dl.get("season") == int(season)
        and dl.get("round") in all_rounds_sorted
    )
    anchor_round = dl["round"] if dl_active else current_round

    # ✅ journée entièrement jouée ; 🟡 en cours (certains matchs joués,
    # d'autres pas, cas d'une journée à cheval sur deux jours) ; ⏳ uniquement
    # sur la journée courante quand elle n'a pas encore démarré du tout.
    def _round_badge(gd: int) -> str:
        frac = round_played_frac.get(gd, 0.0)
        if frac >= 1.0:
            return "✅"
        if frac > 0.0:
            return "🟡"
        if gd == current_round:
            return "⏳"
        return ""

    postseason_rounds = [
        gd for gd in all_rounds_sorted
        if schedule_all[schedule_all["gameday"] == gd]["phase"].iloc[0]
        in ("PI", "PO", "FF")
    ]

    if postseason_rounds and current_round in postseason_rounds:
        selector_rounds = postseason_rounds
        section_title = "Postseason"
    elif postseason_rounds:
        current_idx = all_rounds_sorted.index(anchor_round)
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Matchdays"
    else:
        current_idx = all_rounds_sorted.index(anchor_round)
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Regular Season"

    default_round = anchor_round if anchor_round in selector_rounds else (
        current_round if current_round in selector_rounds else selector_rounds[-1]
    )

    # Share link to a regular season round while the postseason is displayed:
    # open the "browse regular season" view on that round.
    if (dl_active and section_title == "Postseason"
            and dl["round"] not in selector_rounds
            and not st.session_state.get("_dl_rs_set")):
        st.session_state["browse_rs_round"] = dl["round"]
        st.session_state["_dl_rs_set"] = True

    st.markdown(f"### {section_title}")

    short_labels = []
    for gd in selector_rounds:
        base_label = round_labels[gd][0]
        badge = _round_badge(gd)
        short_labels.append(f"{base_label} {badge}" if badge else base_label)
    label_to_round = dict(zip(short_labels, selector_rounds))
    try:
        default_index = selector_rounds.index(default_round)
    except ValueError:
        default_index = len(selector_rounds) - 1

    if (dl_active and dl["round"] in selector_rounds
            and not st.session_state.get("_dl_round_set")):
        st.session_state["main_round_radio"] = short_labels[
            selector_rounds.index(dl["round"])
        ]
        st.session_state["_dl_round_set"] = True

    radio_kwargs = {}
    if "main_round_radio" not in st.session_state:
        radio_kwargs["index"] = default_index

    selected_label = st.radio(
        "Select a round",
        options=short_labels,
        horizontal=True,
        label_visibility="collapsed",
        key="main_round_radio",
        **radio_kwargs,
    )
    rnd = label_to_round[selected_label]

    if section_title == "Postseason":
        rs_rounds = [
            gd for gd in all_rounds_sorted
            if schedule_all[schedule_all["gameday"] == gd]["phase"].iloc[0] == "RS"
        ]
        if rs_rounds:
            browsing_rs = st.session_state.get("browse_rs_round") is not None

            if browsing_rs:
                back_col, select_col = st.columns([1, 3])
                with back_col:
                    if st.button("← Back to Postseason",
                                 use_container_width=True,
                                 type="primary"):
                        st.session_state["browse_rs_round"] = None
                        st.rerun()
                with select_col:
                    rs_options = [f"Round {gd}" for gd in rs_rounds]
                    current_rs = st.session_state["browse_rs_round"]
                    current_label = f"Round {current_rs}"
                    try:
                        idx = rs_options.index(current_label)
                    except ValueError:
                        idx = 0
                    chosen = st.selectbox(
                        "Regular season round",
                        options=rs_options,
                        index=idx,
                        label_visibility="collapsed",
                        key="rs_round_active",
                    )
                    new_rnd = int(chosen.replace("Round ", ""))
                    if new_rnd != current_rs:
                        st.session_state["browse_rs_round"] = new_rnd
                        st.rerun()
                rnd = st.session_state["browse_rs_round"]
            else:
                rs_options = ["— Or browse regular season —"] + [
                    f"Round {gd}" for gd in rs_rounds
                ]
                chosen = st.selectbox(
                    "Browse regular season",
                    options=rs_options,
                    index=0,
                    label_visibility="collapsed",
                    key="rs_round_entry",
                )
                if chosen != rs_options[0]:
                    st.session_state["browse_rs_round"] = int(
                        chosen.replace("Round ", "")
                    )
                    st.rerun()

    round_label_short, round_label_long = round_labels[rnd]

    games = load_matchday(int(season), int(rnd))
    if games.empty:
        st.warning("No games for this round.")
        return

    # Share link to a specific match: show it first and open its analysis once.
    dl_match = dl.get("match") if (dl_active and dl.get("round") == int(rnd)) else None
    if dl_match:
        game_keys = (games["homecode"].astype(str).str.upper() + "_"
                     + games["awaycode"].astype(str).str.upper())
        linked = games[game_keys == dl_match]
        if not linked.empty:
            games = pd.concat([linked, games.drop(linked.index)])
            if not st.session_state.get("_dl_match_opened"):
                lr = linked.iloc[0]
                st.session_state[f"open_{int(rnd)}_{lr['homecode']}_{lr['awaycode']}"] = True
                st.session_state["_dl_match_opened"] = True

    all_games = load_team_games(int(season))
    official_standings = load_official_standings()
    playoffs_schedule = load_playoffs_schedule(int(season))

    phase = games["phase"].iloc[0] if "phase" in games.columns else "RS"
    is_postseason = phase in ("PI", "PO", "FF")

    st.markdown(
        f"<div style='margin-top:8px;margin-bottom:16px;'>"
        f"<span style='font-size:1.1rem;font-weight:600;color:#1a1a1a;'>"
        f"{round_label_long}</span>"
        f"<span style='color:#888;margin-left:10px;'>· {len(games)} "
        f"game{'s' if len(games) > 1 else ''}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    for idx, g in games.iterrows():
        home, away = g["hometeam"], g["awayteam"]
        hcode, acode = g["homecode"], g["awaycode"]
        home_disp = display_name(hcode, home)
        away_disp = display_name(acode, away)
        played = g["played"] == "true"

        score_text = None
        if played:
            match_row = all_games[
                (all_games["gameday"] == int(rnd)) &
                (all_games["team"].str.upper() == home.upper())
            ]
            if not match_row.empty:
                sh = int(match_row.iloc[0]["score"])
                sa = int(match_row.iloc[0]["opp_score"])
                score_text = f"{sh} — {sa}"
            status_text = "Final"
            status_colour = "#2ea043"
        else:
            status_text = "Upcoming"
            status_colour = "#1e88e5"

        game_date = g.get("date")
        if pd.notna(game_date):
            try:
                dt = pd.to_datetime(game_date, format="%b %d, %Y")
                date_str = dt.strftime("%a %b %d")
                if g.get("startime"):
                    date_str += f" · {g['startime']}"
            except Exception:
                date_str = str(game_date)
        else:
            date_str = None

        series = None
        if phase == "PO" and not playoffs_schedule.empty:
            raw = get_series_score(playoffs_schedule, all_games, hcode, acode, int(rnd))
            series = orient_series(raw, hcode)

        with st.container(border=True):
            render_match_card(
                hcode, acode, home_disp, away_disp,
                score_text, status_text, status_colour, date_str,
                series_score=series,
            )

            toggle_key = f"open_{rnd}_{hcode}_{acode}"
            if toggle_key not in st.session_state:
                st.session_state[toggle_key] = False

            is_open = st.session_state[toggle_key]
            btn_label = "Hide analysis ▲" if is_open else "View analysis ▼"

            if st.button(btn_label, key=f"toggle_{idx}_{rnd}_{hcode}_{acode}",
                         use_container_width=True):
                st.session_state[toggle_key] = not is_open
                st.rerun()

            if st.session_state[toggle_key]:
                st.divider()
                render_match_analysis(
                    g, int(rnd), all_games, phase, round_label_long,
                    card_index=idx,
                    official_standings=official_standings,
                    playoffs_schedule=playoffs_schedule,
                    rnd_season=int(season),
                )

    st.divider()

    with st.expander("ℹ️ About ELSTATSLAB Match Center"):
        st.markdown(
            """
            **ELSTATSLAB Match Center** is an independent EuroLeague analytics
            tool that lets you compare any matchup of the season at a glance.

            Built and maintained by **[@EL_Statslab](https://twitter.com/EL_Statslab)**.

            *Data sourced from official EuroLeague feeds. All numbers are
            calculated independently.*
            """
        )

    st.caption("DataViz by @EL_Statslab")


# =============================================================================
# METHODOLOGY TAB
# =============================================================================
def _methodology_match_center():
    st.markdown("## Reading the Match Center")

    st.markdown("### The match header")
    st.markdown(
        """
Each team shows its rank, its record and its last five results. The rank comes from
the official standings. In the last five results, a green square is a win, a red square
is a loss, and the most recent game is on the right. In the postseason, the record is
replaced by the series score.
"""
    )

    st.markdown("### The comparison tables")
    st.markdown(
        """
Two tables sit side by side.

**Season** covers every game the two teams have played this season up to that round.

The table on the right depends on the game. Before tip off it shows **Last 5**: the five
most recent games each team played before that round, which reflects current form.
Once the game has been played it shows **This Game**: the numbers of that game only.

Every figure is computed on totals, not by averaging game by game. For example, a team's
ORTG is its total points divided by its total possessions, so a game with more possessions
weighs more than a short one.

**Colors.** In each row, green marks the better of the two values and red the weaker one.
The larger the gap between the teams, the stronger the color.

**Δ** is the value of the left team minus the value of the right team. It is not flipped
for metrics where lower is better. For DRTG and TOV%, a negative Δ therefore means the
left team is doing better.

**Radar.** The toggle above the tables shows the same comparison as a shape. Each metric
is placed on a fixed scale, and DRTG and TOV% are flipped, so a bigger shape is always
a better profile.
"""
    )

    st.markdown("### The metrics")
    st.markdown(
        """
| Metric | How it is calculated | How to read it |
|---|---|---|
| **ORTG** (Offensive Rating) | Points scored per 100 possessions | Higher is better. Using 100 possessions removes the effect of pace, so slow and fast teams compare fairly. |
| **DRTG** (Defensive Rating) | Points allowed per 100 possessions | Lower is better. |
| **NETRTG** (Net Rating) | Points scored minus points allowed, per 100 possessions (ORTG minus DRTG) | Higher is better. A positive value means the team outscores opponents over the same number of possessions. |
| **OREB%** (Offensive Rebound %) | Team offensive rebounds divided by the offensive rebounds it could have grabbed (its own offensive rebounds plus the opponent's defensive rebounds) | Higher is better. |
| **REB%** (Total Rebound %) | Team total rebounds divided by all rebounds available in the game | Higher is better. Above 50% means the team wins the rebounding battle. |
| **AST%** (Assist %) | Team assists divided by team made field goals | Higher means more of the scoring comes from passing. It describes a style more than a level of quality. |
| **eFG%** (Effective Field Goal %) | (Two pointers made + 1.5 × three pointers made) divided by field goal attempts | Higher is better. It gives a fairer view than raw FG% because a three is worth more than a two. |
| **TOV%** (Turnover %) | Turnovers divided by possessions | Lower is better. |
"""
    )

    st.markdown("### Win probability and Match edge")
    st.markdown(
        """
The bar under the tables appears for games that have not been played yet. It is called
**Win probability** in the regular season and the Play-In, and **Match edge** in the
playoffs and the Final Four.

#### Regular season

**Step 1: team profiles.** For each team, the model builds a profile made of its offensive
rating, its defensive rating, its pace and how much its scores vary from one game to the next.
The profile mixes this season's games with the previous season's. The weight of this season grows
with every game played and reaches 100% after 12 games. A team with no previous season in the
EuroLeague is pulled toward the league average instead, so that two or three games cannot
dominate the estimate.

**Step 2: the simulation.** The model estimates how many points each team is expected to score
against this specific opponent, using its own attack, the opponent's defense and the pace of the
game. It then plays the game 10,000 times, drawing a random score for each team around those
expectations. This is a Monte Carlo simulation: repeat a random experiment many times and count
how often each outcome happens. The share of simulated games won by a team is its simulation result.

**Step 3: three signals on a neutral court.** The simulation result is not used alone. The
probability on a neutral court combines three signals (weights rounded):

1. **Season performance** (50%). 60% of this signal is the simulation result and 40% is built from
   each team's win percentage, mixed with the previous season in the same way as the profiles.
2. **Head to head history** (31%). The results of the last four seasons between the two teams. The
   average margin is turned into a probability, and the more meetings there are, the more this
   signal counts. With fewer than four meetings, it stays neutral.
3. **Style matchup** (19%). The gap in net rating (offensive rating minus defensive rating) between
   the two teams.

**Step 4: home court.** Home court advantage is applied last. It moves the probability toward the
home team by the edge observed in past games: home teams won 823 of the 1,298 regular season games
played from 2022 to 2025 (63.4%). Two teams of equal strength therefore get about 63% for the home team.

The final probability is always kept between 2% and 98%.

#### Play-In, Playoffs and Final Four

These games use the earlier version of the model. The profile of each team comes from postseason
games of the last four seasons, and falls back to the regular season when a team has too few. The
model plays the game 10,000 times, drawing a random score for each team from its own profile each
time. The result is combined with four signals: current season performance, head to head history
over the last four seasons, home court advantage (a fixed bonus, larger than in the regular season
and removed at the Final Four because games are played on a neutral court) and style matchup.

In a playoff series, the results of the games already played are added as a fifth signal, and games
played later count more than earlier ones. The weight of this signal grows with each game: 20% after
one game, 35% after two, 45% after three, 50% after four and 55% after five or more. The other
signals share the remaining weight.

**How to read it.** A 54% probability means that, according to the model, the team wins about 54
games out of 100 in this situation. It is an estimate of likelihood, not a prediction of the result.
The model works from team level results only: it does not know about injuries, lineups or rest days.
"""
    )

def _methodology_game_flow():
    st.markdown("## Reading Game Flow")
    st.markdown(
        """
Game Flow appears in the Match Center once a game has been played. It has three parts: a
chart of the score margin, the biggest scoring runs, and the best five man lineup of each
team.

### The colors

Game Flow always uses two colors. **Blue is the home team and red is the away team**, in
the chart, in the runs and in the Best 5 cards. They are not the clubs' own colors.

### The chart

The line shows the score margin, calculated from the play by play as home team points minus
away team points. A new point is recorded every time the margin changes.

The zero line is a tie. Above it, shaded in blue, the home team leads. Below it, shaded in
red, the away team leads. The further the line is from zero, the bigger the lead. The
vertical axis, labeled Point differential, shows the size of the margin without a sign, so
use the zero line and the colors to know who is ahead. The team codes on the left of the
chart (blue at the top for the home team, red at the bottom for the away team) remind you
which side is which.

The horizontal axis is not a clock. It follows the sequence of scores, one step for each
change in the margin. A quarter can therefore look longer or shorter than another depending
on how many times the margin changed during it. Vertical lines mark the start of each
quarter (Q2, Q3, Q4), and dashed lines mark the overtime periods (OT1, OT2 and so on).

### Scoring runs

A run is a stretch where one team scores and the opponent does not score at all. Only runs
of at least 9 unanswered points are marked. When several runs of the same team overlap,
only the largest one is kept.

Every run is highlighted on the chart with a band in the color of the team that made it,
labeled with its size in points. Below the chart, the **Biggest Runs** panel lists the three
largest ones, each with the team code, the size of the run and its **top contributor**: the
player who scored the most points for that team during the run. A three pointer counts for
3, a free throw for 1 and any other basket for 2. If two players are tied, both are listed.

**How to read it.** Runs point to the moments where the momentum swung, but a game can also
be decided by many small stretches rather than one big run.

### Best 5 by NetRtg

For each team, the site reconstructs which five players were on the court at every moment
of the game: the starters are identified at tip off, then the lineup is updated with each
substitution. The game is then split into stretches where the same five players stayed on
the court, and each five is evaluated over all of its stretches combined.

For each five, the site measures:

- the **time** the five spent on the court together
- the **points scored and allowed** by the team while they were together
- the **possessions** of each side during that time, estimated from the play by play as
  field goal attempts + 0.44 × free throw attempts + turnovers − offensive rebounds

From these come the ratings:

| Metric | How it is calculated |
|---|---|
| **ORTG** | Points scored per 100 of the team's possessions |
| **DRTG** | Points allowed per 100 of the opponent's possessions |
| **NetRtg** | ORTG minus DRTG |

The **Best 5** is the five with the highest NetRtg among those that played enough time
together. This minimum keeps a lineup that shared the court for only a few possessions from
topping the list. **From the 2026-27 season, a five needs at least 3 minutes together to be
eligible. In 2025-26 the minimum was 2 minutes.** If no lineup reaches the minimum in a game,
the best available lineup is shown instead, so always check the time displayed. If the
lineups of a team cannot be reconstructed reliably from the data, no Best 5 is shown for
that team.

Each team gets one card, with the home team on the left and the away team on the right. It
shows the points scored and allowed by the five, its NetRtg and its time on the court
together, above the names of the five players.

**How to read it.** Higher is better, and a positive NetRtg means the five outscored the
opponent over the same number of possessions. Lineups often share the court for only a few
minutes, so their NetRtg can look very large. Always read it together with the time on
court and the points scored and allowed.

### Why the Best 5 can show a negative NetRtg

"Best" means the best of that team's lineups in this game, not necessarily a good one. If a
team was outscored whenever its eligible fives were on the court, which happens most often
to the losing team in a one sided game, even the top five can end with a negative NetRtg.
It then simply means the least bad lineup of the night.

A second reason is that possessions are estimated separately for each side. A five that was
only narrowly ahead on points can therefore show a slightly negative NetRtg. For the same
reason, this NetRtg can differ slightly from the NETRTG in the Match Center comparison
tables.
"""
    )


def _methodology_team_cards():
    st.markdown("## Reading Team Cards")
    st.markdown(
        """
Team Cards show a team's profile as **percentiles**. A percentile tells you where the team
ranks compared with the other EuroLeague teams on a given stat. A 90th percentile means the
team is ahead of about 90% of teams on that stat, and around the 50th percentile means
league average territory. It is not a percentage of anything: a 63rd percentile does not
mean 63%.

Percentiles are oriented so that **a higher percentile is always better**, including for
DEF RTG and TOV%, where a lower raw value is better. The one exception is **PACE**: it
describes a playing style, not a level of quality, so a high percentile means a fast team
and a low percentile means a slow one.

Team Cards use the metrics defined in the Match Center section above (NET RTG, OFF RTG,
DEF RTG, AST%, TOV%, OREB%) plus the following:

| Metric | How it is calculated | How to read it |
|---|---|---|
| **PACE** | Number of possessions per game | Describes tempo. It says nothing about quality. |
| **DREB%** (Defensive Rebound %) | Share of available defensive rebounds the team grabbed | Higher is better. |
| **3PM** | Three pointers made per game | Volume of made threes. |
| **3P%** | Three point shooting percentage | Higher is better. |
| **TS%** (True Shooting %) | Points scored relative to shooting attempts, counting two pointers, threes and free throws | Higher is better. It is the most complete single shooting efficiency number. |
| **PAINT PTS** | Points scored in the paint per game | Shows how much of the offense comes from inside. |
| **FAST BRK** | Fast break points per game | Shows how much the team scores in transition. |
| **2ND CHANCE** | Points scored after an offensive rebound, per game | Shows how well the team turns offensive rebounds into points. |
| **PTS OFF TO** | Points scored off opponent turnovers, per game | Shows how well the team punishes mistakes. |
"""
    )


def _methodology_impact_pulse():
    st.markdown("## Impact Pulse")
    st.markdown(
        """
Impact Pulse is ELSTATSLAB's own proprietary metric. It is the only individual metric on
the site, and it appears in the Match Center once a game has been played. It answers one
question: **how did the team perform with this player on the court compared with without
him, in this single game?**

### How it is calculated

1. **Split the game into two states for each player.** Possessions with the player on the
   court, and possessions with him on the bench.
2. **Measure the team in each state** on six metrics: NETRTG, eFG%, REB%, AST%, OREB% and
   TOV%. These are the team metrics defined in the Match Center section, with the possession
   count described in the note below.
3. **Take the On/Off delta** for each metric: the team's value with the player on the court
   minus its value with him off. The delta is oriented so that a positive number always
   means a positive effect. For TOV%, fewer turnovers with the player on the court counts
   as positive.
4. **Scale each delta.** Each delta is divided by a fixed reference size for its metric, so
   that a swing in one metric can be added to a swing in another on a common footing.
5. **Combine the six scaled deltas** into a single composite score using fixed weights.
   NETRTG carries the most weight, followed by eFG%, then REB%, AST% and TOV%, and OREB%
   carries the least.
6. **Apply a minimum threshold.** From the 2026 season, a player needs at least 20
   possessions on the court to receive a score. Games from the 2025 season use a minimum
   of 12.

### A note on possessions

From the 2026 season, Impact Pulse counts possessions with the same formula as the Match
Center: field goal attempts plus 0.44 times free throw attempts plus turnovers minus offensive
rebounds. Games from the 2025 season were calculated earlier with a simpler count, field goal
attempts plus turnovers, so 2025 figures can differ slightly from the Match Center tables.
Within any game, ON and OFF are always measured with the same count, so the comparison stays
fair.

### What you see on the site

Each team shows the player with the highest Impact Pulse score in that game, flagged as the
**Difference Maker**, with his score next to the label.

Under his name, the On/Off table compares the team's numbers with him **ON** the court and
**OFF** it, and **Δ** is ON minus OFF. Green means the difference clearly helps the team,
red means it clearly hurts, and grey means the difference is small. For DRTG and TOV%,
where lower is better, the colors are flipped accordingly. The table shows eight metrics
for context, while the score itself is built from the six listed above.

The **Full ranking** lists every player who reached the minimum threshold, ranked by score.
On the downloadable image, the line under the table shows the possessions the player was
on the court out of the possessions his team played in the game.

### How to read it

**Zero means no difference** between the team's numbers with the player on the court and
with him off it. A positive score means the team did better with the player on the court,
and a negative score means the opposite. The scaling is fixed, so scores can be compared
from one game to another, but games differ in length and pace, so compare with care.

Keep in mind what an On/Off metric cannot do. It measures what happened while a player was
on the court, not what he caused alone: teammates and opponents on the floor at the same
time matter, and a single game is a small sample. Read it as a signal of impact, not as a
full judgment of a player's performance.
"""
    )


def _methodology_shot_maps():
    st.markdown("## Reading Shot Maps")
    st.markdown(
        """
Shot Maps show where a team shoots and how well it converts. Free throws are not included.
Every field goal attempt is drawn on the court: a green cross is a made shot and a red circle
is a missed one. They appear in the Match Center once a game has been played, under Impact
Pulse, and in the Shot Maps tab for a team over a competition and a season.

### The five zones

Each shot belongs to exactly one zone:

| Zone | What it covers |
|---|---|
| **Restricted Area** | Within 1.25 m of the basket |
| **Paint** | The painted area, outside the restricted area |
| **Midrange** | Every other two point shot |
| **Corner 3** | Three pointers taken from the straight section of the line, about 3 m from the baseline |
| **Above the Break 3** | Every other three pointer |

Under each zone the map shows its FG% and the made shots out of attempts. A zone without any
shot reads No shots. The map is cut about 9 m from the basket to stay readable, but every
shot is counted in the numbers.

### The colors

A zone is colored by comparing the team's FG% in that zone with the league average in the
same zone, competition and season. Green is above the league average and red is below. The
strongest colors correspond to a gap of about 7 points or more. Zones with few shots are
pulled toward the league average, with a weight equivalent to 20 shots, so that a handful of
attempts does not produce an extreme color. The numbers written on the map are always the
raw ones. Until about ten games have been played in the competition, there is no reliable
league average, and the color simply follows the FG%.

### Heatmap

In the Shot Maps tab, a heatmap of shot density is available once a team has at least ten
games in the selection. With fewer games, the density mostly reflects chance, so only the zone
view is offered. In a single game view, only the zone view is shown.
"""
    )


def render_methodology():
    st.caption("How every number on ELSTATSLAB is calculated, and how to read it.")

    _methodology_match_center()
    st.divider()
    _methodology_game_flow()
    st.divider()
    _methodology_team_cards()
    st.divider()
    _methodology_impact_pulse()
    st.divider()
    _methodology_shot_maps()


# =============================================================================
# VISITOR TRACKING (GoatCounter, sent from the server)
# DO NOT REMOVE: this block and the two calls at the top of main() feed the
# visitor dashboard at elstatslab.goatcounter.com.
# =============================================================================
# One hit per browser session, sent from Python so no script has to run in the
# visitor's browser. The token is read from the Streamlit Secrets:
#     [goatcounter]
#     code = "elstatslab"
#     token = "..."
# If the secrets are missing or GoatCounter does not answer, the app simply
# carries on without tracking.
#   ?me=1       the visit is not counted (use it in your own bookmark)
#   ?gcdebug=1  shows a diagnostic panel at the top of the page
SEND_VISITOR_IP = True   # only used when a public IP is available
_BOT_MARKERS = ("headlesschrome", "playwright", "bot", "crawler", "spider",
                "python-requests", "curl", "wget")


def _goatcounter_post(code: str, token: str, hit: dict) -> str:
    result = ""
    for attempt in (1, 2):
        try:
            r = requests.post(
                f"https://{code}.goatcounter.com/api/v0/count",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={"hits": [hit]},
                timeout=4,
            )
            result = f"HTTP {r.status_code} {r.text[:300]} (attempt {attempt})"
            if r.status_code < 500:
                return result
        except Exception as e:
            result = f"Error: {type(e).__name__}: {e} (attempt {attempt})"
    return result


def track_visit_once() -> None:
    if st.session_state.get("_gc_done"):
        return
    st.session_state["_gc_done"] = True

    debug = bool(st.query_params.get("gcdebug"))
    st.session_state["_gc_debug"] = debug
    info: dict = {"step": "start"}
    st.session_state["_gc_info"] = info

    # Owner visit: an address ending with ?me=1 is not counted. Streamlit Cloud
    # does not pass cookies to the app, so it has to be in the address.
    if st.query_params.get("me") == "1":
        info["step"] = "stopped: owner visit (?me=1)"
        try:
            st.toast("This visit is not counted.")
        except Exception:
            pass
        return

    try:
        cfg = st.secrets["goatcounter"]
        code, token = cfg["code"], cfg["token"]
        info["secrets"] = f"found (code={code}, token length={len(str(token))})"
    except Exception as e:
        info["secrets"] = f"NOT FOUND ({type(e).__name__})"
        info["step"] = "stopped: no secrets"
        return

    ua, ip = "", ""
    try:
        headers = st.context.headers
        ua = headers.get("User-Agent", "") or ""
        if SEND_VISITOR_IP:
            ip = (headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
    except Exception as e:
        info["headers"] = f"error: {type(e).__name__}: {e}"
    info["user_agent"] = ua or "(empty)"
    info["ip_seen"] = ip or "(none)"

    # Streamlit Cloud only exposes an internal address (192.168.x.x). Sending it
    # would make GoatCounter treat visitors with the same browser as one person,
    # so private addresses are dropped and a random session id is used instead.
    try:
        if ip and not ipaddress.ip_address(ip).is_global:
            info["ip_note"] = "private address ignored, random session id used"
            ip = ""
    except ValueError:
        ip = ""

    # Skip the keep-awake workflow and other automated visitors.
    if any(marker in ua.lower() for marker in _BOT_MARKERS):
        info["step"] = "stopped: user agent looks like a bot"
        return

    hit = {"path": "/", "title": "ELSTATSLAB Match Center"}
    if ua and ip:
        hit["user_agent"] = ua
        hit["ip"] = ip
    else:
        hit["session"] = uuid.uuid4().hex
        if ua:
            hit["user_agent"] = ua

    if debug:
        info["goatcounter_answer"] = _goatcounter_post(code, token, hit)
        info["step"] = "sent (synchronous, debug mode)"
    else:
        info["step"] = "sent (background)"
        threading.Thread(target=_goatcounter_post, args=(code, token, hit),
                         daemon=True).start()


def show_tracking_debug() -> None:
    if st.session_state.get("_gc_debug"):
        with st.expander("GoatCounter diagnostic", expanded=True):
            st.json(st.session_state.get("_gc_info", {}))


def main():
    track_visit_once()
    show_tracking_debug()
    st.markdown(
        """
        <style>
        button[data-baseweb="tab"] p {
            font-size: 18px !important;
            font-weight: 600 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    title_col1, title_col2 = st.columns([1, 8], vertical_alignment="center")
    with title_col1:
        if ELSTATSLAB_LOGO.exists():
            st.markdown(
                f"<img src='data:image/png;base64,{brand_logo_b64(str(ELSTATSLAB_LOGO))}' "
                f"style='width:110px;height:auto;display:block;background:none;border:0;padding:0;'/>",
                unsafe_allow_html=True,
            )
    with title_col2:
        st.title("ELSTATSLAB")
        st.caption("Independent EuroLeague analytics. Built by @EL_Statslab.")

    tab_match, tab_cards, tab_shots, tab_lineup, tab_method = st.tabs(
        ["📊 Match Center", "🛡️ Team Cards", "🎯 Shot Maps", "🧩 Lineup", "📖 Methodology"]
    )

    with tab_match:
        st.caption("Compare any EuroLeague matchup.")
        render_match_center()

    with tab_cards:
        team_cards.render()

    with tab_shots:
        if SHOTMAPS_TAB:
            shotmap_ui.render_shot_maps_tab(
                conn=get_conn(),
                seasons=load_seasons(),
                name_fn=lambda c: display_name(c, c),
                elstatslab_logo=ELSTATSLAB_LOGO,
                team_logo_fn=logo_path,
            )
        else:
            st.info("Shot Maps are temporarily unavailable.")

    with tab_lineup:
        lineup_tab.render_lineup_tab(
            conn=get_conn(),
            seasons=load_seasons(),
            name_fn=lambda c: display_name(c, c),
            elstatslab_logo=ELSTATSLAB_LOGO,
            team_logo_fn=logo_path,
        )

    with tab_method:
        render_methodology()


if __name__ == "__main__":
    main()
