"""
ELSTATSLAB EuroLeague Match Center
Streamlit app: matchday picker, head to head stats, logos, live standings,
form sparklines, gradient coloured comparisons, PNG export for X.

Run locally:
    streamlit run app.py
"""

import base64
import io
import math
import sqlite3
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from matplotlib.gridspec import GridSpec
from PIL import Image

from gameflow_chart import render_gameflow_png

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

st.set_page_config(
    page_title="ELSTATSLAB Match Center",
    page_icon=str(ELSTATSLAB_LOGO) if ELSTATSLAB_LOGO.exists() else "🏀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =============================================================================
# LOGO MAPPING
# =============================================================================
LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png",
    "MIL": "AXM.png", "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png",
    "PAN": "PAO.png", "PAR": "PAR.png", "PRS": "PBB.png", "RED": "CZV.png",
    "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png", "ZAL": "ZAL.png",
}

ZOOM_CORRECTIONS = {
    "ASM": 1.3, "AXM": 1.5, "CZV": 1.6, "EFS": 0.8,
    "FEN": 1.7, "BAR": 0.8, "PAO": 1.1, "VIR": 0.85,
    "PBB": 0.85, "OLY": 0.9, "HTA": 0.8,
}

TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne",
    "BAR": "FC Barcelona",
    "BAS": "Baskonia Vitoria-Gasteiz",
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
    lp = logo_path(code)
    if not lp:
        return None
    with open(lp, "rb") as f:
        return base64.b64encode(f.read()).decode()


# =============================================================================
# GAMEFLOW
# =============================================================================
@st.cache_data(ttl=3600)
def get_gameflow_png(gamecode: int, season: int, aspect: str = "square") -> bytes | None:
    try:
        return render_gameflow_png(gamecode, season, aspect=aspect)
    except Exception:
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
    return [CURRENT_SEASON]


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
        for i, row in df.iterrows():
            match = official_standings[
                official_standings["team_code"].str.upper() == row["team_code"].upper()
            ]
            if not match.empty:
                df.at[i, "rank"] = int(match.iloc[0]["rank"])
                if "last_5_form" in match.columns:
                    df.at[i, "last_5_form"] = match.iloc[0]["last_5_form"]
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
    "Virtus Segafredo Bologna":         "Virtus Bologna",
    "Maccabi Rapyd Tel Aviv":           "Maccabi Playtika Tel Aviv",
    "Panathinaikos Athens":             "Panathinaikos AKTOR Athens",
    "Panathinaikos OPAP Athens":        "Panathinaikos AKTOR Athens",
}

MC_MIN_H2H_GAMES  = 4
MC_MIN_DIST_GAMES = 2
MC_N_SIMULATIONS  = 10_000
MC_HOME_COURT     = 0.06

WEIGHTS_RS = {
    "current_season": 0.50,
    "h2h":            0.25,
    "home_court":     0.10,
    "style_matchup":  0.15,
}
WEIGHTS_PO_BASE = {
    "current_season": 0.35,
    "h2h":            0.30,
    "home_court":     0.10,
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
    row = conn.execute(q, (season_min, season, team_name)).fetchone()
    if row and row[7] and row[7] >= MC_MIN_DIST_GAMES:
        keys = ["avg_pts", "std_pts", "avg_ortg", "avg_drtg",
                "std_ortg", "std_drtg", "avg_pace", "games"]
        return dict(zip(keys, row))
    return None


def _get_h2h(conn, team_a: str, team_b: str,
             current_season: int = 2025, seasons_back: int = 4,
             playoff_only: bool = False) -> dict:
    season_min = current_season - seasons_back + 1
    round_filter = "AND s.round IN ('PO', 'FF')" if playoff_only else ""
    q = f"""
        WITH matchups AS (
            SELECT
                CASE WHEN UPPER(s.hometeam) = UPPER(?) THEN h.Score ELSE a.Score END AS score_a,
                CASE WHEN UPPER(s.hometeam) = UPPER(?) THEN a.Score ELSE h.Score END AS score_b
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
                    (UPPER(s.hometeam) = UPPER(?) AND UPPER(s.awayteam) = UPPER(?))
                 OR (UPPER(s.hometeam) = UPPER(?) AND UPPER(s.awayteam) = UPPER(?))
              )
              {round_filter}
        )
        SELECT COUNT(*) AS games,
               SUM(CASE WHEN score_a > score_b THEN 1 ELSE 0 END) AS wins_a,
               ROUND(AVG(score_a - score_b), 2) AS avg_margin
        FROM matchups
    """
    row = conn.execute(q, (
        team_a, team_a,
        season_min, current_season,
        team_a, team_b,
        team_b, team_a
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


def _serie_prob_weight(serie_scores: list) -> tuple[float, float]:
    """Probabilite et poids bases sur les matchs deja joues dans la serie."""
    if not serie_scores:
        return 0.5, 0.0
    n = len(serie_scores)
    avg = sum(serie_scores) / n
    prob = _mc_logistic(avg / 15.0)
    weight_map = {1: 0.20, 2: 0.35, 3: 0.45, 4: 0.50, 5: 0.55}
    return prob, weight_map.get(n, 0.55)


def _get_match_context(conn, gamecode: int) -> Optional[dict]:
    """
    Recupere automatiquement depuis la DB tout le contexte
    necessaire pour calculer la win probability d'un match.
    Detecte le round et adapte le modele en consequence.
    """
    row = conn.execute("""
        SELECT Season, round, hometeam, awayteam, homecode, awaycode
        FROM schedule
        WHERE CAST(SUBSTR(gamecode, INSTR(gamecode, '_') + 1) AS INTEGER) = ?
    """, (gamecode,)).fetchone()

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
    """
    Calcule la win probability pour le prochain match.
    S'adapte automatiquement selon le round detecte :
      RS  : 4 composantes, home court actif
      PI  : playoffs sans serie precedente, home court actif
      PO  : playoffs avec dynamique de serie, home court actif
      FF  : terrain neutre, home court desactive, pas de serie
    """
    home_team    = ctx["home_team"]
    away_team    = ctx["away_team"]
    season       = ctx["season"]
    round_       = ctx["round"]
    is_playoff   = ctx["is_playoff"]
    home_win_pct = ctx["home_win_pct"]
    away_win_pct = ctx["away_win_pct"]
    serie_scores = ctx["serie_scores"]

    # --- Distributions de performance ---
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
                "error": "Distributions introuvables"}

    # --- Monte Carlo ---
    rng = np.random.default_rng()
    h_scores = rng.normal(home_dist["avg_pts"], home_dist["std_pts"], MC_N_SIMULATIONS)
    a_scores = rng.normal(away_dist["avg_pts"], away_dist["std_pts"], MC_N_SIMULATIONS)
    mc_prob  = float(np.mean(h_scores > a_scores))
    std_err  = math.sqrt(mc_prob * (1 - mc_prob) / MC_N_SIMULATIONS)
    ci_low   = max(0.02, mc_prob - 1.645 * std_err)
    ci_high  = min(0.98, mc_prob + 1.645 * std_err)

    # --- H2H ---
    h2h = _get_h2h(conn, home_team, away_team, season, playoff_only=is_playoff)
    if h2h["games"] < MC_MIN_H2H_GAMES and is_playoff:
        h2h = _get_h2h(conn, home_team, away_team, season, playoff_only=False)

    h2h_prob = 0.5
    if h2h["games"] >= MC_MIN_H2H_GAMES:
        reliability = min(1.0, h2h["games"] / 10.0)
        raw_h2h     = _mc_logistic(h2h["avg_margin"] / 15.0)
        h2h_prob    = 0.5 + reliability * (raw_h2h - 0.5)

    # --- Home court : desactive en Final Four (terrain neutre) ---
    home_court_prob = 0.5 if round_ == "FF" else (0.5 + MC_HOME_COURT)

    # --- Style matchup ---
    home_net   = home_dist["avg_ortg"] - away_dist["avg_drtg"]
    away_net   = away_dist["avg_ortg"] - home_dist["avg_drtg"]
    style_prob = _mc_logistic((home_net - away_net) / 40.0)

    # --- Saison en cours ---
    season_prob         = _mc_logistic(home_win_pct - away_win_pct)
    current_season_prob = 0.6 * mc_prob + 0.4 * season_prob

    # --- Regular Season : 4 composantes fixes ---
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

    # --- Playoffs / Play-In / Final Four : dynamique de serie ---
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
def predict_by_gamecode(gamecode: int) -> dict:
    """Point d'entree principal : gamecode → prediction complete."""
    conn = get_conn()
    ctx = _get_match_context(conn, gamecode)
    if not ctx:
        return {"home_prob": 0.5, "away_prob": 0.5,
                "error": f"Gamecode {gamecode} introuvable"}
    return _monte_carlo_win_prob(conn, ctx)


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

    # Contextual label
    if round_ == "FF":
        section_label = "Match edge (Final Four · neutral court)"
    elif round_ == "PO":
        section_label = "Match edge (Playoffs)"
    elif round_ == "PI":
        section_label = "Win probability (Play-In)"
    else:
        section_label = "Win probability"

    st.markdown(f"**{section_label}**")

    # Probability bar
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

    # Confidence interval and H2H context
    ci_low    = pred.get("confidence_low", hp)
    ci_high   = pred.get("confidence_high", hp)
    h2h_games = pred.get("h2h_games", 0)
    h2h_margin = pred.get("h2h_margin", 0.0)

    if h2h_games > 0:
        margin_leader = home_disp if h2h_margin >= 0 else away_disp
        margin_abs = abs(h2h_margin)
        h2h_text = (
            f" · {h2h_games} head-to-head games over the last 4 seasons, "
            f"avg margin {margin_abs:.1f} pts in favor of {margin_leader}"
        )
    else:
        h2h_text = " · No head-to-head history available"

    st.caption(f"Based on {MC_N_SIMULATIONS:,} simulations")

    # Explanatory popover — generic, no weights revealed
    with st.popover("How is this calculated?"):
        if round_ == "RS":
            st.markdown(
                "The win probability combines multiple signals: each team's current season "
                "efficiency profile, their head-to-head history over the last 4 seasons, "
                "home court advantage, and offensive/defensive style matchup. "
                "Monte Carlo simulation runs thousands of iterations to model the full range "
                "of possible outcomes based on each team's scoring distribution."
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
    labels = METRICS
    n = len(labels)
    angles = [i * 2 * 3.14159 / n for i in range(n)]
    angles += angles[:1]

    h_vals = [_normalize_radar(h_stats.get(m), m) for m in labels]
    a_vals = [_normalize_radar(a_stats.get(m), m) for m in labels]
    h_vals += h_vals[:1]
    a_vals += a_vals[:1]

    fig, ax = plt.subplots(figsize=(5, 5), dpi=120,
                           subplot_kw=dict(polar=True),
                           facecolor=BG_WHITE)
    ax.set_facecolor(BG_WHITE)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, size=9, color="#444")
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels([])
    ax.set_ylim(0, 1)
    ax.grid(color="#e0e0e0", linewidth=0.8)
    ax.spines["polar"].set_color("#cccccc")
    ax.plot(angles, h_vals, color=EL_GREEN, linewidth=2.0, linestyle="-")
    ax.fill(angles, h_vals, color=EL_GREEN, alpha=0.15)
    ax.plot(angles, a_vals, color=EL_RED, linewidth=2.0, linestyle="-")
    ax.fill(angles, a_vals, color=EL_RED, alpha=0.15)
    ax.set_title(title, size=11, fontweight="bold", pad=18, color="#1a1a1a")
    fig.text(0.25, 0.02, f"● {home_name}", ha="center", va="bottom",
             color=EL_GREEN, fontsize=9, fontweight="bold")
    fig.text(0.75, 0.02, f"● {away_name}", ha="center", va="bottom",
             color=EL_RED, fontsize=9, fontweight="bold")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_WHITE, dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_preview_png(home_code: str, home_name: str, home_rank: int,
                      home_wl: str, home_form: list[bool],
                      away_code: str, away_name: str, away_rank: int,
                      away_wl: str, away_form: list[bool],
                      h_season: dict, a_season: dict,
                      h_right: dict, a_right: dict,
                      home_prob: float, away_prob: float,
                      round_label: str,
                      show_prediction: bool = True,
                      right_label: str = "Last 5",
                      round_: str = "RS",
                      series_score: dict | None = None) -> bytes:
    fig = plt.figure(figsize=(12, 12), dpi=120, facecolor=BG_WHITE)
    gs = GridSpec(
        nrows=5, ncols=2,
        height_ratios=[0.6, 2.2, 3.8, 1.2, 0.4],
        hspace=0.35, wspace=0.15,
        left=0.05, right=0.95, top=0.95, bottom=0.03,
    )

    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.set_xlim(0, 1)
    ax_title.set_ylim(0, 1)
    if ELSTATSLAB_LOGO.exists():
        brand_ax = ax_title.inset_axes([0.0, -0.4, 0.16, 1.8])
        brand_ax.imshow(plt.imread(str(ELSTATSLAB_LOGO)), interpolation="lanczos")
        brand_ax.axis("off")
    if EUROLEAGUE_LOGO.exists():
        el_ax = ax_title.inset_axes([0.74, -0.15, 0.34, 1.3])
        el_ax.imshow(plt.imread(str(EUROLEAGUE_LOGO)), interpolation="lanczos")
        el_ax.axis("off")
    ax_title.text(0.5, 0.5, f"EuroLeague {round_label}",
                  ha="center", va="center", fontsize=22, fontweight="bold")

    ax_head = fig.add_subplot(gs[1, :])
    ax_head.axis("off")
    ax_head.set_xlim(0, 1)
    ax_head.set_ylim(0, 1)

    is_postseason_png = round_ in ("PO", "FF", "PI")

    def draw_team_block(code, name, rank, wl, form, x_center):
        base_w, base_h = 0.18, 0.55
        zoom = logo_zoom(code)
        w = min(base_w * zoom, 0.28)
        h = min(base_h * zoom, 0.85)
        logo_y = 0.55
        lp = logo_path(code)
        if lp:
            logo_ax = ax_head.inset_axes(
                [x_center - w / 2, logo_y - h / 2 + 0.15, w, h]
            )
            logo_ax.imshow(plt.imread(str(lp)))
            logo_ax.axis("off")
        ax_head.text(x_center, 0.32, name, ha="center", va="top",
                     fontsize=14, fontweight="bold")
        # En postseason : pas de rank, afficher le bilan RS
        if not is_postseason_png:
            ax_head.text(x_center, 0.20, f"#{rank} · {wl}",
                         ha="center", va="top", fontsize=11, color="#555555")
        else:
            ax_head.text(x_center, 0.20, wl,
                         ha="center", va="top", fontsize=11, color="#555555")
        if form:
            n = len(form)
            sq = 0.022
            gap = 0.006
            total = n * sq + (n - 1) * gap
            start = x_center - total / 2
            sparkline_y = 0.05
            for i, win in enumerate(reversed(form)):
                colour = EL_GREEN if win else EL_RED
                rect = plt.Rectangle(
                    (start + i * (sq + gap), sparkline_y),
                    sq, sq * 2.2,
                    transform=ax_head.transAxes,
                    facecolor=colour, edgecolor="none",
                )
                ax_head.add_patch(rect)

    draw_team_block(home_code, home_name, home_rank, home_wl, home_form, 0.17)
    draw_team_block(away_code, away_name, away_rank, away_wl, away_form, 0.83)

    # Centre : VS ou score de série si playoffs
    if is_postseason_png and series_score and series_score.get("games_played", 0) > 0:
        hw = series_score["home_wins"]
        aw = series_score["away_wins"]
        hw_col = EL_GREEN if hw > aw else (EL_RED if hw < aw else "#1a1a1a")
        aw_col = EL_GREEN if aw > hw else (EL_RED if aw < hw else "#1a1a1a")
        ax_head.text(0.5, 0.65, "Series", ha="center", va="center",
                     fontsize=12, color="#555555")
        ax_head.text(0.44, 0.48, str(hw), ha="center", va="center",
                     fontsize=36, fontweight="bold", color=hw_col)
        ax_head.text(0.5, 0.48, "-", ha="center", va="center",
                     fontsize=28, color="#aaaaaa")
        ax_head.text(0.56, 0.48, str(aw), ha="center", va="center",
                     fontsize=36, fontweight="bold", color=aw_col)
    else:
        ax_head.text(0.5, 0.55, "VS", ha="center", va="center",
                     fontsize=34, fontweight="bold")

    def draw_table(ax, title, h_stats, a_stats, home_name, away_name):
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.97, title, ha="center", va="top",
                fontsize=14, fontweight="bold")
        col_x = {"home": 0.22, "metric": 0.5, "away": 0.78}
        header_y = 0.88
        ax.text(col_x["home"],   header_y, home_name,
                ha="center", va="center", fontsize=10,
                color="#444444", fontweight="bold")
        ax.text(col_x["metric"], header_y, "Metric",
                ha="center", va="center", fontsize=10, color="#444444")
        ax.text(col_x["away"],   header_y, away_name,
                ha="center", va="center", fontsize=10,
                color="#444444", fontweight="bold")
        row_h = 0.105
        top_y = 0.78
        for i, m in enumerate(METRICS):
            y = top_y - i * row_h
            hv = h_stats.get(m)
            av = a_stats.get(m)
            h_int, a_int = colour_intensity(hv, av, m)
            cell_w = 0.22
            cell_h = row_h * 0.85
            for side, intensity, cx in [
                ("home", h_int, col_x["home"]),
                ("away", a_int, col_x["away"]),
            ]:
                rect = plt.Rectangle(
                    (cx - cell_w / 2, y - cell_h / 2),
                    cell_w, cell_h,
                    facecolor=mpl_colour(intensity),
                    edgecolor="#e0e0e0",
                    linewidth=0.5,
                )
                ax.add_patch(rect)
            rect_m = plt.Rectangle(
                (col_x["metric"] - 0.1, y - cell_h / 2),
                0.2, cell_h,
                facecolor="#f5f5f5",
                edgecolor="#e0e0e0",
                linewidth=0.5,
            )
            ax.add_patch(rect_m)
            hv_s = f"{hv:.1f}" if hv is not None else "-"
            av_s = f"{av:.1f}" if av is not None else "-"
            ax.text(col_x["home"], y, hv_s, ha="center", va="center",
                    fontsize=12, fontweight="bold")
            ax.text(col_x["metric"], y, m, ha="center", va="center",
                    fontsize=11)
            ax.text(col_x["away"], y, av_s, ha="center", va="center",
                    fontsize=12, fontweight="bold")

    ax_season = fig.add_subplot(gs[2, 0])
    draw_table(ax_season, "Season", h_season, a_season, home_name, away_name)
    ax_right = fig.add_subplot(gs[2, 1])
    draw_table(ax_right, right_label, h_right, a_right, home_name, away_name)

    ax_prob = fig.add_subplot(gs[3, :])
    ax_prob.axis("off")
    ax_prob.set_xlim(0, 1)
    ax_prob.set_ylim(0, 1)

    if show_prediction:
        # Label adapte au round
        prob_label = "Win probability" if round_ == "RS" else "Match edge"
        if round_ == "FF":
            prob_label += " (neutral court)"
        ax_prob.text(0.5, 0.9, prob_label,
                     ha="center", va="center", fontsize=13, fontweight="bold")
        bar_y = 0.35
        bar_h = 0.3
        ax_prob.add_patch(plt.Rectangle(
            (0.1, bar_y), 0.8 * home_prob, bar_h,
            facecolor=EL_GREEN, edgecolor="none"))
        ax_prob.add_patch(plt.Rectangle(
            (0.1 + 0.8 * home_prob, bar_y), 0.8 * away_prob, bar_h,
            facecolor=EL_RED, edgecolor="none"))
        ax_prob.text(0.1, bar_y - 0.12, f"{home_name}  {home_prob*100:.1f}%",
                     ha="left", va="top", fontsize=11, fontweight="bold")
        ax_prob.text(0.9, bar_y - 0.12, f"{away_prob*100:.1f}%  {away_name}",
                     ha="right", va="top", fontsize=11, fontweight="bold")

    ax_foot = fig.add_subplot(gs[4, :])
    ax_foot.axis("off")
    ax_foot.set_xlim(0, 1)
    ax_foot.set_ylim(0, 1)
    ax_foot.text(0.5, 0.5, "DataViz by  𝕏 @EL_Statslab",
                 ha="center", va="center", fontsize=11,
                 color="#888888", style="italic")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_WHITE, dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


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
        series = get_series_score(playoffs_schedule, all_games, hcode, acode, int(rnd))

    hcol, mcol, acol = st.columns([1, 2, 1])
    with hcol:
        render_team_header(hcode, home_disp, h_season, h_form,
                           is_postseason=is_postseason)
    with mcol:
        vs_extra = ""
        if series and series["games_played"] > 0:
            hw, aw = series["home_wins"], series["away_wins"]
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

    # Game Flow
    if played:
        raw_gc = g["gamecode"]
        if isinstance(raw_gc, str) and "_" in raw_gc:
            gc_num = int(raw_gc.split("_")[1])
        else:
            gc_num = int(raw_gc)

        gf_png = get_gameflow_png(gc_num, rnd_season, aspect="square")
        if gf_png is not None:
            st.divider()
            with st.expander("📊 Game Flow — Runs & Best 5 by NetRtg"):
                st.image(gf_png, use_container_width=True)

    # -------------------------------------------------------------------------
    # WIN PROBABILITY — Monte Carlo automatique
    # -------------------------------------------------------------------------
    st.divider()

    raw_gc = g["gamecode"]
    if isinstance(raw_gc, str) and "_" in raw_gc:
        gc_num = int(raw_gc.split("_")[1])
    else:
        gc_num = int(raw_gc)

    pred = predict_by_gamecode(gc_num)

    if "error" in pred:
        st.caption(f"Win probability unavailable: {pred['error']}")
    else:
        render_win_probability(pred, home_disp, away_disp, phase)

    st.divider()

    # PNG Export
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
                    show_prediction=True,
                    right_label=right_lbl,
                    round_=phase,
                    series_score=series,
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
def main():
    title_col1, title_col2 = st.columns([1, 8], vertical_alignment="center")
    with title_col1:
        if ELSTATSLAB_LOGO.exists():
            st.image(str(ELSTATSLAB_LOGO), width=110)
    with title_col2:
        st.title("ELSTATSLAB Match Center")
        st.caption("Compare any EuroLeague matchup. Built by @EL_Statslab.")

    with st.sidebar:
        st.header("Filters")
        seasons = load_seasons()
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
    upcoming_rounds = [gd for gd in all_rounds_sorted if not round_status.get(gd, True)]
    current_round = upcoming_rounds[0] if upcoming_rounds else all_rounds_sorted[-1]

    postseason_rounds = [
        gd for gd in all_rounds_sorted
        if schedule_all[schedule_all["gameday"] == gd]["phase"].iloc[0]
        in ("PI", "PO", "FF")
    ]

    if postseason_rounds and current_round in postseason_rounds:
        selector_rounds = postseason_rounds
        section_title = "Postseason"
    elif postseason_rounds:
        current_idx = all_rounds_sorted.index(current_round)
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Matchdays"
    else:
        current_idx = all_rounds_sorted.index(current_round)
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Regular Season"

    default_round = current_round if current_round in selector_rounds else selector_rounds[-1]

    st.markdown(f"### {section_title}")

    short_labels = [round_labels[gd][0] for gd in selector_rounds]
    label_to_round = dict(zip(short_labels, selector_rounds))
    try:
        default_index = selector_rounds.index(default_round)
    except ValueError:
        default_index = len(selector_rounds) - 1

    selected_label = st.radio(
        "Select a round",
        options=short_labels,
        index=default_index,
        horizontal=True,
        label_visibility="collapsed",
        key="main_round_radio",
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

        is_playoffs_phase = phase == "PO"
        series = None
        if is_playoffs_phase and not playoffs_schedule.empty:
            series = get_series_score(playoffs_schedule, all_games,
                                      hcode, acode, int(rnd))

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

    with st.expander("📖 How to read the stats"):
        st.markdown("""
*Section 1* : **Efficiency Ratings**

**ORTG (Offensive Rating)** : Points scored per 100 possessions. Higher is better.
**DRTG (Defensive Rating)** : Points allowed per 100 possessions. Lower is better.
**NETRTG (Net Rating)** : The difference between ORTG and DRTG. Higher is better.

*Section 2* : **Shooting and Ball Control**

**eFG% (Effective Field Goal Percentage)** : Shooting efficiency accounting for three-pointers.
**TOV% (Turnover Percentage)** : Share of possessions ending in a turnover. Lower is better.
**AST% (Assist Percentage)** : Share of made field goals assisted. Higher is better.

*Section 3* : **Rebounding**

**OREB% (Offensive Rebound Percentage)** : Share of available offensive rebounds grabbed.
**REB% (Total Rebound Percentage)** : Share of available total rebounds grabbed.

*Section 4* : **Win Probability**

The model uses Monte Carlo simulation (10,000 iterations) combining four signals:
current season performance, head-to-head history (last 4 seasons), home court advantage,
and offensive/defensive style matchup. During playoffs, the model also integrates
the current series context — the weight of games already played in the series increases
with each game (G1=20%, G2=35%, G3=45%). For the Final Four, home court advantage
is removed as games are played on neutral court.
    """)

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


if __name__ == "__main__":
    main()
