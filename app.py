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

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from matplotlib.gridspec import GridSpec
from PIL import Image

# =============================================================================
# CONFIG
# =============================================================================
# On Streamlit Cloud, euroleague_public.db sits next to app.py (relative path).
# In local developer mode, both DBs live in the parent Euroleague_Stats folder.
_PUBLIC_DB_LOCAL = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague_public.db")
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
    """Return base64 encoded logo for inlining in HTML cards."""
    lp = logo_path(code)
    if not lp:
        return None
    with open(lp, "rb") as f:
        return base64.b64encode(f.read()).decode()


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
    """Load every scheduled game of the season once, for phase detection."""
    q = """
        SELECT gameday, round AS phase
        FROM schedule
        WHERE Season = ?
        ORDER BY gameday
    """
    return pd.read_sql(q, get_conn(), params=(season,))


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
    poss   = df["poss"].sum()
    pts    = df["score"].sum()
    pa     = df["opp_score"].sum()
    oreb   = df["oreb"].sum()
    dreb   = df["dreb"].sum()
    o_oreb = df["opp_oreb"].sum()
    o_dreb = df["opp_dreb"].sum()
    ast    = df["ast"].sum()
    fgm    = df["fgm"].sum()
    twopm  = df["twopm"].sum()
    threepm = df["threepm"].sum()
    twopa  = df["twopa"].sum()
    threepa = df["threepa"].sum()
    tov    = df["tov"].sum()
    wins   = int((df["score"] > df["opp_score"]).sum())
    games  = len(df)

    def safe(num, den, mult=100.0, nd=1):
        return round(mult * num / den, nd) if den else None

    return {
        "games":  games,
        "wins":   wins,
        "losses": games - wins,
        "pt_diff": int(pts - pa),
        "ORTG":   safe(pts, poss),
        "DRTG":   safe(pa, poss),
        "NETRTG": safe(pts - pa, poss),
        "OREB%":  safe(oreb, oreb + o_dreb),
        "REB%":   safe(oreb + dreb, oreb + dreb + o_oreb + o_dreb),
        "AST%":   safe(ast, fgm),
        "eFG%":   safe(twopm + 1.5 * threepm, twopa + threepa),
        "TOV%":   safe(tov, poss),
    }


def team_season_stats(all_games: pd.DataFrame, up_to_gameday: int) -> pd.DataFrame:
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
    return df.reset_index(drop=True)


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
# ROUND LABELS (Play-In / Playoffs Game 1..N / Final Four / Regular Season)
# =============================================================================
PHASE_LABELS = {
    "RS": "Regular Season",
    "PI": "Play-In",
    "PO": "Playoffs",
    "FF": "Final Four",
}


def build_round_labels(schedule_df: pd.DataFrame) -> dict[int, tuple[str, str]]:
    """
    Build a mapping gameday -> (short_label, long_label) for each round,
    where Playoffs rounds are numbered Game 1, Game 2...

    Returns e.g. {
        39: ("Play-In", "Play-In"),
        40: ("Playoffs Game 1", "Playoffs Game 1"),
        41: ("Playoffs Game 2", "Playoffs Game 2"),
        ...
    }
    """
    labels: dict[int, tuple[str, str]] = {}
    po_counter = 0
    ff_counter = 0
    # Take first phase per gameday (all games of a gameday share the phase)
    by_day = schedule_df.drop_duplicates("gameday").sort_values("gameday")
    for _, row in by_day.iterrows():
        gd = int(row["gameday"])
        ph = row["phase"]
        if ph == "PO":
            po_counter += 1
            short = f"Playoffs Game {po_counter}"
            labels[gd] = (short, short)
        elif ph == "PI":
            labels[gd] = ("Play-In", "Play-In")
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
# PREDICTION MODEL
# =============================================================================
WEIGHTS = {"standing": 0.20, "home_away": 0.15, "netrtg": 0.40, "form": 0.25}
HOME_COURT_ADVANTAGE = 0.06


def logistic(x: float, scale: float = 3.0) -> float:
    return 1.0 / (1.0 + math.exp(-scale * x))


def predict_home_win_pct(standings: pd.DataFrame,
                         home: str, away: str,
                         home_recent: dict, away_recent: dict) -> dict:
    try:
        h = standings.loc[standings["team"].str.upper() == home.upper()].iloc[0]
        a = standings.loc[standings["team"].str.upper() == away.upper()].iloc[0]
    except IndexError:
        return {"home_prob": 0.5, "away_prob": 0.5, "components": {}}

    h_wp = h["wins"] / h["games"] if h["games"] else 0.5
    a_wp = a["wins"] / a["games"] if a["games"] else 0.5
    components = {
        "standing":  logistic(h_wp - a_wp),
        "home_away": 0.5 + HOME_COURT_ADVANTAGE,
        "netrtg":    logistic(((h["NETRTG"] or 0) - (a["NETRTG"] or 0)) / 20.0),
        "form":      logistic(
            ((home_recent.get("NETRTG") or 0) -
             (away_recent.get("NETRTG") or 0)) / 20.0),
    }
    home_prob = sum(WEIGHTS[k] * v for k, v in components.items())
    home_prob = max(0.02, min(0.98, home_prob))
    return {"home_prob": home_prob, "away_prob": 1 - home_prob,
            "components": components}


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
        f"font-weight:600;width:30%;border-bottom:2px solid #ddd;white-space:nowrap;"
        f"overflow:hidden;text-overflow:ellipsis;'>{home}</th>"
        "<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        "font-weight:600;width:18%;border-bottom:2px solid #ddd;'>Metric</th>"
        f"<th style='padding:8px 4px;text-align:center;color:#666;font-size:0.8rem;"
        f"font-weight:600;width:30%;border-bottom:2px solid #ddd;white-space:nowrap;"
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
                       form_seq: list[bool]):
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

    if standings_row:
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
                      game_date: str | None):
    """Render a compact visual match card with both logos, names and status."""
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

    html = (
        "<div style='display:flex;align-items:center;justify-content:space-between;"
        "padding:4px 0 12px 0;gap:12px;'>"
        # Home block
        "<div style='flex:1;display:flex;flex-direction:column;align-items:center;"
        "min-width:0;'>"
        f"<div style='height:72px;display:flex;align-items:center;justify-content:center;'>"
        f"{h_logo}</div>"
        f"<div style='font-weight:600;font-size:0.9rem;text-align:center;"
        f"margin-top:6px;color:#1a1a1a;line-height:1.2;min-height:36px;"
        f"display:flex;align-items:center;'>{home_disp}</div>"
        "</div>"
        # Middle block
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "min-width:80px;'>"
        f"{middle}"
        f"<div style='font-size:0.75rem;color:{status_colour};margin-top:6px;"
        f"font-weight:600;text-transform:uppercase;letter-spacing:0.5px;'>"
        f"{status_text}</div>"
        f"{date_html}"
        "</div>"
        # Away block
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
# PNG EXPORT (matplotlib) -- unchanged
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


def build_preview_png(home_code: str, home_name: str, home_rank: int,
                      home_wl: str, home_form: list[bool],
                      away_code: str, away_name: str, away_rank: int,
                      away_wl: str, away_form: list[bool],
                      h_season: dict, a_season: dict,
                      h_right: dict, a_right: dict,
                      home_prob: float, away_prob: float,
                      round_label: str,
                      show_prediction: bool = True,
                      right_label: str = "Last 5") -> bytes:
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

    def draw_team_block(code, name, rank, wl, form, x_center):
        base_w, base_h = 0.18, 0.55
        zoom = logo_zoom(code)
        w = base_w * zoom
        h = base_h * zoom
        w = min(w, 0.28)
        h = min(h, 0.85)
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
        ax_head.text(x_center, 0.20, f"#{rank} · {wl}",
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
        ax_prob.text(0.5, 0.9, "Win probability",
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
    else:
        ax_prob.text(0.5, 0.5, "Predictions disabled for playoffs",
                     ha="center", va="center", fontsize=12,
                     color="#888888", style="italic")

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
# MATCH ANALYSIS RENDERER (extracted so we can call it from the card)
# =============================================================================
def render_match_analysis(g: pd.Series, rnd: int, all_games: pd.DataFrame,
                          phase: str, round_label_long: str,
                          card_index: int):
    home, away = g["hometeam"], g["awayteam"]
    hcode, acode = g["homecode"], g["awaycode"]
    home_disp = display_name(hcode, home)
    away_disp = display_name(acode, away)
    played = g["played"] == "true"
    is_postseason = phase in ("PI", "PO", "FF")

    up_to = int(rnd) if played else int(rnd) - 1
    standings_scope = team_season_stats(all_games, up_to)

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

    hcol, mcol, acol = st.columns([1, 2, 1])
    with hcol:
        render_team_header(hcode, home_disp, h_season, h_form)
    with mcol:
        st.markdown(
            "<div style='text-align:center; padding-top:40px;"
            "font-size:24px; font-weight:bold;'>VS</div>",
            unsafe_allow_html=True,
        )
    with acol:
        render_team_header(acode, away_disp, a_season, a_form)

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        render_comparison_styled("Season", home_disp, away_disp,
                                 h_season, a_season)
    with col2:
        if played:
            h_game = team_single_game_stats(all_games, home, int(rnd))
            a_game = team_single_game_stats(all_games, away, int(rnd))
            render_comparison_styled("This Game",
                                     home_disp, away_disp,
                                     h_game, a_game)
        else:
            render_comparison_styled(f"Last {ROLLING_WINDOW}",
                                     home_disp, away_disp,
                                     h_recent, a_recent)

    pred = predict_home_win_pct(standings_scope, home, away,
                                h_recent, a_recent)
    st.markdown("**Win probability**")
    if is_postseason:
        st.markdown(
            "<div style='text-align:center; padding:14px; "
            "background:#f5f5f5; border-radius:6px; color:#666; "
            "font-style:italic;'>"
            "Predictions disabled for playoffs"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        pcol1, pcol2 = st.columns(2)
        pcol1.metric(f"{home_disp} (Home)", f"{pred['home_prob']*100:.1f}%")
        pcol2.metric(f"{away_disp} (Away)", f"{pred['away_prob']*100:.1f}%")

        with st.popover("How is this calculated?"):
            st.markdown(
                "The win probability blends several signals: each team's "
                "season long efficiency profile, their recent form, the "
                "current standings, and the home court factor. Each element "
                "contributes to a weighted estimate, calibrated from "
                "historical EuroLeague results."
            )

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
                    home_wl=f"{int(h_season['wins'])}W "
                            f"{int(h_season['losses'])}L",
                    home_form=h_form,
                    away_code=acode, away_name=away_disp,
                    away_rank=int(a_season["rank"]),
                    away_wl=f"{int(a_season['wins'])}W "
                            f"{int(a_season['losses'])}L",
                    away_form=a_form,
                    h_season=h_season, a_season=a_season,
                    h_right=h_right_data, a_right=a_right_data,
                    home_prob=pred["home_prob"],
                    away_prob=pred["away_prob"],
                    round_label=round_label_long,
                    show_prediction=not is_postseason,
                    right_label=right_lbl,
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
    # Top branding row
    title_col1, title_col2 = st.columns([1, 8], vertical_alignment="center")
    with title_col1:
        if ELSTATSLAB_LOGO.exists():
            st.image(str(ELSTATSLAB_LOGO), width=110)
    with title_col2:
        st.title("ELSTATSLAB Match Center")
        st.caption("Compare any EuroLeague matchup. Built by @EL_Statslab.")

    # Minimal sidebar (season selector only, kept for forward compatibility)
    with st.sidebar:
        st.header("Filters")
        seasons = load_seasons()
        season = st.selectbox("Season", seasons, index=0)

    schedule_all = load_all_schedule(int(season))
    if schedule_all.empty:
        st.error("No schedule data available.")
        return

    round_labels = build_round_labels(schedule_all)
    all_rounds_sorted = sorted(round_labels.keys())

    # Load full schedule including "played" status to find the next upcoming round
    q_full = """
        SELECT gameday, round AS phase, played
        FROM schedule
        WHERE Season = ?
    """
    full_sched = pd.read_sql(q_full, get_conn(), params=(int(season),))

    # Per-round status: a round is "fully played" if all its games have played='true'
    round_status = (
        full_sched.groupby("gameday")["played"]
        .apply(lambda s: (s == "true").all())
        .to_dict()
    )
    # Next upcoming round = smallest gameday that is not fully played
    upcoming_rounds = [gd for gd in all_rounds_sorted if not round_status.get(gd, True)]
    current_round = upcoming_rounds[0] if upcoming_rounds else all_rounds_sorted[-1]

    # Detect postseason rounds (any round whose phase is PI/PO/FF)
    postseason_rounds = [
        gd for gd in all_rounds_sorted
        if schedule_all[schedule_all["gameday"] == gd]["phase"].iloc[0]
        in ("PI", "PO", "FF")
    ]

    # Build the selector: we want to always include the current round plus
    # surrounding context, so users never lose sight of upcoming games.
    if postseason_rounds and current_round in postseason_rounds:
        # We are in the postseason: show all postseason rounds
        selector_rounds = postseason_rounds
        section_title = "Postseason"
    elif postseason_rounds:
        # Postseason exists in the schedule but we're still in regular season:
        # show last few regular season rounds + the first postseason rounds
        current_idx = all_rounds_sorted.index(current_round)
        # Show 3 rounds before current + current + next 3
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Matchdays"
    else:
        # Pure regular season: show a window around the current round
        current_idx = all_rounds_sorted.index(current_round)
        start = max(0, current_idx - 3)
        end = min(len(all_rounds_sorted), current_idx + 4)
        selector_rounds = all_rounds_sorted[start:end]
        section_title = "Regular Season"

    # Default selection is the current upcoming round (or fallback)
    default_round = current_round if current_round in selector_rounds else selector_rounds[-1]

    # Phase selector as horizontal pills
    st.markdown(f"### {section_title}")

    # Short labels for the radio
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
    )
    rnd = label_to_round[selected_label]

    # Secondary selector: access any regular season round on demand.
    # Only shown when we are in the postseason section, since otherwise
    # regular season rounds are already accessible in the main selector.
    if section_title == "Postseason":
        rs_rounds = [
            gd for gd in all_rounds_sorted
            if schedule_all[schedule_all["gameday"] == gd]["phase"].iloc[0] == "RS"
        ]
        if rs_rounds:
            # Are we currently browsing a regular season round?
            browsing_rs = st.session_state.get("browse_rs_round") is not None

            if browsing_rs:
                # Show a prominent "back" button, then the round selector
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
                    )
                    new_rnd = int(chosen.replace("Round ", ""))
                    if new_rnd != current_rs:
                        st.session_state["browse_rs_round"] = new_rnd
                        st.rerun()
                rnd = st.session_state["browse_rs_round"]
            else:
                # Not browsing yet: show the entry dropdown
                rs_options = ["— Or browse regular season —"] + [
                    f"Round {gd}" for gd in rs_rounds
                ]
                chosen = st.selectbox(
                    "Browse regular season",
                    options=rs_options,
                    index=0,
                    label_visibility="collapsed",
                )
                if chosen != rs_options[0]:
                    st.session_state["browse_rs_round"] = int(
                        chosen.replace("Round ", "")
                    )
                    st.rerun()

    round_label_short, round_label_long = round_labels[rnd]

    # Load matchday
    games = load_matchday(int(season), int(rnd))
    if games.empty:
        st.warning("No games for this round.")
        return

    all_games = load_team_games(int(season))

    # Phase banner
    phase = games["phase"].iloc[0] if "phase" in games.columns else "RS"
    is_postseason = phase in ("PI", "PO", "FF")

    # Section header with game count
    st.markdown(
        f"<div style='margin-top:8px;margin-bottom:16px;'>"
        f"<span style='font-size:1.1rem;font-weight:600;color:#1a1a1a;'>"
        f"{round_label_long}</span>"
        f"<span style='color:#888;margin-left:10px;'>· {len(games)} "
        f"game{'s' if len(games) > 1 else ''}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if is_postseason:
        st.info("📌 Predictions are disabled during the postseason because "
                "the model is calibrated on regular season data.")

    # Render match cards
    for idx, g in games.iterrows():
        home, away = g["hometeam"], g["awayteam"]
        hcode, acode = g["homecode"], g["awaycode"]
        home_disp = display_name(hcode, home)
        away_disp = display_name(acode, away)
        played = g["played"] == "true"

        # Compute status and score
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

        # Card container
        with st.container(border=True):
            render_match_card(
                hcode, acode, home_disp, away_disp,
                score_text, status_text, status_colour, date_str,
            )

            # Toggle button for the analysis
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
                )

    st.divider()

    # Info sections
    with st.expander("📖 How to read the stats"):
        st.markdown("""
    *Section 1* : **Efficiency Ratings**

**ORTG (Offensive Rating)** : Points scored per 100 possessions. Higher is better. A top-tier EuroLeague offense typically runs between 115 and 122.
**DRTG (Defensive Rating)** : Points allowed per 100 possessions. Lower is better. A top-tier EuroLeague defense typically runs between 105 and 112.
**NETRTG (Net Rating)** : The difference between ORTG and DRTG. Higher is better. A NETRTG above +5 usually indicates a playoff-caliber team.


*Section 2* : **Shooting and Ball Control**

**eFG% (Effective Field Goal Percentage)** : Shooting efficiency that accounts for the extra value of three-pointers. Formula: (2PM + 1.5 × 3PM) / FGA. Higher is better. Top EuroLeague teams sit around 55% eFG.
**TOV% (Turnover Percentage)** : Share of possessions ending in a turnover. Lower is better. A disciplined team stays below 13% TOV.
**AST% (Assist Percentage)** : Share of made field goals created from an assist. Higher is better. Teams with strong ball movement exceed 65% AST.


*Section 3* : **Rebounding**

**OREB% (Offensive Rebound Percentage)** : Share of available offensive rebounds grabbed by the team. Higher is better. Elite offensive rebounding teams reach 32% and above.
**REB% (Total Rebound Percentage)** : Share of available total rebounds grabbed by the team. Higher is better. A balanced team sits around 50%.


*Section 4* : **How to read the tables**

The comparison tables show two teams side by side across the same 8 metrics. For each row, both values are color-coded based on which team leads in that area:

**Green** means this team has the advantage. The more intense the green, the bigger the advantage.
**Red** means this team trails. The more intense the red, the bigger the gap.
**White** means the two teams are essentially equal on this metric.

The right-hand table switches automatically: for upcoming games it shows each team's last 5 games average, for played games it shows the actual game stats compared against each team's season average.
    """)

    with st.expander("ℹ️ About ELSTATSLAB Match Center"):
        st.markdown(
            """
            **ELSTATSLAB Match Center** is an independent EuroLeague analytics
            tool that lets you compare any matchup of the season at a glance.

            For each game you can explore:
            - Both teams' season long efficiency profile (ORTG, DRTG, NETRTG, REB%, AST%)
            - Their form over the last 5 games
            - Standings, win-loss record and recent results
            - A win probability estimate during the regular season

            Built and maintained by **[@EL_Statslab](https://twitter.com/EL_Statslab)**,
            an independent EuroLeague analytics project sharing daily insights
            on X. Follow for player and team breakdowns, advanced metrics,
            and matchday previews.

            *Data sourced from official EuroLeague feeds. All numbers are
            calculated independently.*
            """
        )

    st.caption("DataViz by @EL_Statslab")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
