"""
ELSTATSLAB EuroLeague Match Center
Streamlit app: matchday picker, head to head stats, logos, live standings,
form sparklines, gradient coloured comparisons, PNG export for X.

Run locally:
    streamlit run app.py
"""

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
# Use the public DB if available (Streamlit Cloud deployment),
# otherwise fall back to the local full archive (developer mode)
_PUBLIC_DB = Path("euroleague_public.db")
_LOCAL_DB = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db")
DB_PATH = _PUBLIC_DB if _PUBLIC_DB.exists() else _LOCAL_DB
LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"
CURRENT_SEASON = 2025
ROLLING_WINDOW = 5

st.set_page_config(
    page_title="ELSTATSLAB Match Center",
    page_icon=str(ELSTATSLAB_LOGO) if ELSTATSLAB_LOGO.exists() else "🏀",
    layout="wide",
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

# Per logo zoom corrections so all logos appear visually balanced in PNG export.
# Key is the team logo file stem (without .png).
ZOOM_CORRECTIONS = {
    "ASM": 1.3, "AXM": 1.5, "CZV": 1.6, "EFS": 0.8,
    "FEN": 1.7, "BAR": 0.8, "PAO": 1.1, "VIR": 0.85,
    "PBB": 0.85, "OLY": 0.9, "HTA": 0.8,
}

# Clean display names per schedule code (sponsors removed for readability)
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
    """Return clean display name for a team code, fallback to title-cased raw."""
    return TEAM_DISPLAY_NAMES.get(code, fallback.title())


def logo_zoom(code: str) -> float:
    """Returns the zoom multiplier for a given schedule code."""
    filename = LOGO_MAP.get(code, "")
    stem = Path(filename).stem
    return ZOOM_CORRECTIONS.get(stem, 1.0)


def logo_path(code: str) -> Path | None:
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    p = LOGOS_DIR / filename
    return p if p.exists() else None


# =============================================================================
# DATA ACCESS
# =============================================================================
@st.cache_resource
def get_conn():
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


@st.cache_data(ttl=600)
def load_seasons() -> list[int]:
    # Public release: current season only
    return [CURRENT_SEASON]


@st.cache_data(ttl=600)
def load_rounds(season: int) -> list[int]:
    q = "SELECT DISTINCT gameday FROM schedule WHERE Season = ? ORDER BY gameday"
    return pd.read_sql(q, get_conn(), params=(season,))["gameday"].tolist()


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
            (COALESCE(ts."2PM", 0) + COALESCE(ts."3PM", 0)) AS fgm
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
        "DREB%":  safe(dreb, dreb + o_oreb),
        "REB%":   safe(oreb + dreb, oreb + dreb + o_oreb + o_dreb),
        "AST%":   safe(ast, fgm),
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
                       before_gameday: int, window: int = ROLLING_WINDOW) -> list[bool]:
    """List of booleans, most recent first: True = win, False = loss."""
    mask = (all_games["team"].str.upper() == team.upper()) & \
           (all_games["gameday"] < before_gameday)
    sub = (all_games[mask]
           .sort_values("gameday", ascending=False)
           .head(window))
    return [bool(row.score > row.opp_score) for row in sub.itertuples()]


def team_single_game_stats(all_games: pd.DataFrame, team: str,
                           gameday: int) -> dict:
    """Stats for one specific match (used in 'This Game' tab for played matches)."""
    mask = (all_games["team"].str.upper() == team.upper()) & \
           (all_games["gameday"] == gameday)
    sub = all_games[mask]
    return aggregate_stats(sub)


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
METRICS = ["ORTG", "DRTG", "NETRTG", "OREB%", "DREB%", "REB%", "AST%"]

# Reference scales (one standard deviation of the metric at team level in EL)
# These normalize the gap to decide colour intensity.
METRIC_SCALE = {
    "ORTG":   8.0,
    "DRTG":   8.0,
    "NETRTG": 10.0,
    "OREB%":  5.0,
    "DREB%":  4.0,
    "REB%":   4.0,
    "AST%":   6.0,
}

# For DRTG lower is better. For everything else higher is better.
LOWER_IS_BETTER = {"DRTG"}


def colour_intensity(hv, av, metric: str) -> tuple[float, float]:
    """
    Returns (home_intensity, away_intensity) in [-1, 1].
    Positive = this team is better (gets green), negative = worse (gets red).
    """
    if hv is None or av is None:
        return (0.0, 0.0)
    diff = hv - av
    if metric in LOWER_IS_BETTER:
        diff = -diff
    scale = METRIC_SCALE.get(metric, 5.0)
    norm = max(-1.0, min(1.0, diff / scale))
    return (norm, -norm)


def gradient_colour(intensity: float) -> str:
    """
    Map intensity in [-1, 1] to an rgba colour string for CSS.
    Positive => green, negative => red, zero => transparent.
    """
    if intensity >= 0:
        # Green
        alpha = min(0.55, intensity * 0.6)
        return f"background-color: rgba(46, 160, 67, {alpha:.3f});"
    else:
        # Red
        alpha = min(0.55, -intensity * 0.6)
        return f"background-color: rgba(218, 54, 51, {alpha:.3f});"


def render_comparison_styled(label: str, home: str, away: str,
                              h_stats: dict, a_stats: dict):
    """
    Render a stat comparison table as pure HTML so it:
    - Stays fixed (no draggable columns like st.dataframe)
    - Adapts naturally to mobile via CSS
    - Keeps the gradient colour coding
    """
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


def render_team_header(code: str, display_name: str,
                       standings_row: dict | None,
                       form_seq: list[bool]):
    """Render a team header block with strict vertical alignment.

    Strategy: build a single HTML block where:
    - Logo lives in a fixed height container, centered vertically and horizontally
    - The logo image gets max-height/max-width so any aspect ratio fits
    - Name has fixed min-height (covers 1 or 2 line names)
    - Standings and sparkline have fixed heights
    Result: every block has the same total height regardless of logo shape.
    """
    import base64

    lp = logo_path(code)
    logo_html = ""
    if lp:
        # Embed the image as base64 so we can fully control sizing via CSS
        with open(lp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        zoom = logo_zoom(code)
        # Base max dimensions inside the 130px container
        max_h = int(110 * zoom)
        max_w = int(130 * zoom)
        max_h = max(70, min(max_h, 130))
        max_w = max(80, min(max_w, 160))
        logo_html = (
            f"<img src='data:image/png;base64,{b64}' "
            f"style='max-height:{max_h}px; max-width:{max_w}px; "
            f"object-fit:contain;'/>"
        )

    # Standings text
    if standings_row:
        rk = standings_row.get("rank", "?")
        w = int(standings_row.get("wins", 0))
        l = int(standings_row.get("losses", 0))
        standings_text = f"{w}W {l}L"
    else:
        standings_text = ""

    # Sparkline squares
    squares = ""
    if form_seq:
        for win in reversed(form_seq):
            colour = "#2ea043" if win else "#da3633"
            squares += (
                f"<span style='display:inline-block;width:14px;height:14px;"
                f"background:{colour};margin-right:3px;border-radius:2px;'></span>"
            )

    # Single HTML block with strict layout (no indentation to avoid markdown code block)
    html = (
        "<div style='display:flex;flex-direction:column;align-items:center;"
        "font-family:sans-serif;'>"
        "<div style='height:140px;display:flex;align-items:center;"
        f"justify-content:center;width:100%;'>{logo_html}</div>"
        "<div style='font-weight:bold;font-size:1rem;text-align:center;"
        "min-height:48px;line-height:1.2;margin-top:8px;display:flex;"
        f"align-items:center;justify-content:center;'>{display_name}</div>"
        "<div style='color:#888;font-size:0.85rem;height:22px;"
        f"text-align:center;margin-top:4px;'>{standings_text}</div>"
        "<div style='height:20px;text-align:center;margin-top:4px;'>"
        f"{squares}</div>"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# PNG EXPORT (matplotlib)
# =============================================================================
EL_GREEN = "#2ea043"
EL_RED   = "#da3633"
BG_WHITE = "#ffffff"


def mpl_colour(intensity: float) -> tuple[float, float, float, float]:
    """Same gradient logic but returns an rgba tuple for matplotlib."""
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
    """
    Build a square 1200x1200 PNG with logos, standings, form, both stat tables
    (Season + Last 5) with gradient colouring, and win probabilities.
    Returns the PNG bytes.
    """
    fig = plt.figure(figsize=(12, 12), dpi=120, facecolor=BG_WHITE)
    gs = GridSpec(
        nrows=5, ncols=2,
        height_ratios=[0.6, 2.2, 3.8, 1.2, 0.4],
        hspace=0.35, wspace=0.15,
        left=0.05, right=0.95, top=0.95, bottom=0.03,
    )

    # --- Title bar with branding (ELSTATSLAB left, EuroLeague right) ---
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.set_xlim(0, 1)
    ax_title.set_ylim(0, 1)
    # ELSTATSLAB logo top left (square aspect, can use a tall box)
    if ELSTATSLAB_LOGO.exists():
        brand_ax = ax_title.inset_axes([0.0, -0.4, 0.16, 1.8])
        brand_ax.imshow(plt.imread(str(ELSTATSLAB_LOGO)), interpolation="lanczos")
        brand_ax.axis("off")
    # EuroLeague logo top right: horizontal logo, wide short box
    if EUROLEAGUE_LOGO.exists():
        el_ax = ax_title.inset_axes([0.74, -0.15, 0.34, 1.3])
        el_ax.imshow(plt.imread(str(EUROLEAGUE_LOGO)), interpolation="lanczos")
        el_ax.axis("off")
    ax_title.text(0.5, 0.5, f"EuroLeague {round_label}",
                  ha="center", va="center", fontsize=22, fontweight="bold")

    # --- Team headers row ---
    ax_head = fig.add_subplot(gs[1, :])
    ax_head.axis("off")
    ax_head.set_xlim(0, 1)
    ax_head.set_ylim(0, 1)

    def draw_team_block(code: str, name: str, rank: int, wl: str,
                        form: list[bool], x_center: float):
        """Draws logo + name + standings + sparkline stacked at x_center."""
        # Logo: base box [width=0.18, height=0.55] then apply zoom
        base_w, base_h = 0.18, 0.55
        zoom = logo_zoom(code)
        w = base_w * zoom
        h = base_h * zoom
        # Cap so very large logos do not overflow
        w = min(w, 0.28)
        h = min(h, 0.85)
        logo_y = 0.55  # bottom of logo box
        lp = logo_path(code)
        if lp:
            logo_ax = ax_head.inset_axes(
                [x_center - w / 2, logo_y - h / 2 + 0.15, w, h]
            )
            logo_ax.imshow(plt.imread(str(lp)))
            logo_ax.axis("off")

        # Team name
        ax_head.text(x_center, 0.32, name, ha="center", va="top",
                     fontsize=14, fontweight="bold")
        # Rank + W-L
        ax_head.text(x_center, 0.20, f"{wl}",
                     ha="center", va="top", fontsize=11, color="#555555")
        # Sparkline below standings, centered on x_center
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

    # Big VS centered
    ax_head.text(0.5, 0.55, "VS", ha="center", va="center",
                 fontsize=34, fontweight="bold")

    # --- Stats tables (Season left, Last 5 right) ---
    def draw_table(ax, title: str, h_stats: dict, a_stats: dict,
                   home_name: str, away_name: str):
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

            # Cell backgrounds
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

            # Metric label background (neutral)
            rect_m = plt.Rectangle(
                (col_x["metric"] - 0.1, y - cell_h / 2),
                0.2, cell_h,
                facecolor="#f5f5f5",
                edgecolor="#e0e0e0",
                linewidth=0.5,
            )
            ax.add_patch(rect_m)

            # Values
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

    # --- Win probability bar ---
    ax_prob = fig.add_subplot(gs[3, :])
    ax_prob.axis("off")
    ax_prob.set_xlim(0, 1)
    ax_prob.set_ylim(0, 1)

    if show_prediction:
        ax_prob.text(0.5, 0.9, "Win probability",
                     ha="center", va="center", fontsize=13, fontweight="bold")
        bar_y = 0.35
        bar_h = 0.3
        # Home (green) portion
        ax_prob.add_patch(plt.Rectangle(
            (0.1, bar_y), 0.8 * home_prob, bar_h,
            facecolor=EL_GREEN, edgecolor="none"))
        # Away (red) portion
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

    # --- Footer ---
    ax_foot = fig.add_subplot(gs[4, :])
    ax_foot.axis("off")
    ax_foot.set_xlim(0, 1)
    ax_foot.set_ylim(0, 1)
    # X logo as text (the brand uses a stylized 𝕏)
    ax_foot.text(0.5, 0.5, "DataViz by  𝕏 @EL_Statslab",
                 ha="center", va="center", fontsize=11,
                 color="#888888", style="italic")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_WHITE, dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# =============================================================================
# APP
# =============================================================================
def main():
    # Title row with logo
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
        season = st.selectbox("Season", seasons, index=0)
        rounds = load_rounds(int(season))
        if not rounds:
            st.error("No rounds found for this season.")
            st.stop()
        rnd = st.selectbox("Round (gameday)", rounds, index=len(rounds) - 1)

    games = load_matchday(int(season), int(rnd))
    if games.empty:
        st.warning("No games for this round.")
        return

    all_games = load_team_games(int(season))

    st.subheader(f"Round {rnd} → {len(games)} games")

    # Phase detection: PO=playoffs, PI=play-in, FF=final four, otherwise RS
    phase = games["phase"].iloc[0] if "phase" in games.columns else "RS"
    PHASE_LABELS = {
        "RS": "Regular Season",
        "PI": "Play-In",
        "PO": "Playoffs",
        "FF": "Final Four",
    }
    is_postseason = phase in ("PI", "PO", "FF")
    if is_postseason:
        st.info(f"📌 {PHASE_LABELS.get(phase, phase)} — predictions are "
                "disabled because the model is calibrated on regular "
                "season data.")

    for idx, g in games.iterrows():
        home, away = g["hometeam"], g["awayteam"]
        hcode, acode = g["homecode"], g["awaycode"]
        home_disp = display_name(hcode, home)
        away_disp = display_name(acode, away)

        label = f"{home_disp}   vs   {away_disp}"
        played = g["played"] == "true"
        if played:
            match_row = all_games[
                (all_games["gameday"] == int(rnd)) &
                (all_games["team"].str.upper() == home.upper())
            ]
            if not match_row.empty:
                sh = int(match_row.iloc[0]["score"])
                sa = int(match_row.iloc[0]["opp_score"])
                label += f"   ({sh} {sa})"
        else:
            label += "   (upcoming)"

        # Only the last match where the user generated a PNG stays open
        match_id = f"{rnd}_{hcode}_{acode}"
        is_active = st.session_state.get("active_match") == match_id

        with st.expander(label, expanded=is_active):
            up_to = int(rnd) if played else int(rnd) - 1
            standings_scope = team_season_stats(all_games, up_to)

            if standings_scope.empty:
                st.info("No season data available yet.")
                continue

            try:
                h_row = standings_scope.loc[
                    standings_scope["team"].str.upper() == home.upper()
                ].iloc[0]
                a_row = standings_scope.loc[
                    standings_scope["team"].str.upper() == away.upper()
                ].iloc[0]
            except IndexError:
                st.warning("One of the teams has no prior games in this season scope.")
                continue

            h_season = h_row.to_dict()
            a_season = a_row.to_dict()
            h_recent = team_recent_stats(all_games, home, int(rnd))
            a_recent = team_recent_stats(all_games, away, int(rnd))
            h_form = team_form_sequence(all_games, home, int(rnd))
            a_form = team_form_sequence(all_games, away, int(rnd))

            # Headers
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
                pcol1.metric(f"{home_disp} (Home)",
                             f"{pred['home_prob']*100:.1f}%")
                pcol2.metric(f"{away_disp} (Away)",
                             f"{pred['away_prob']*100:.1f}%")

                with st.popover("How is this calculated?"):
                    st.markdown(
                        "The win probability blends several signals: each "
                        "team's season long efficiency profile, their recent "
                        "form, the current standings, and the home court "
                        "factor. Each element contributes to a weighted "
                        "estimate, calibrated from historical EuroLeague "
                        "results."
                    )

            st.divider()

            # --- PNG export: lazy generation, no page rerun ---
            png_key = f"png_{idx}_{rnd}_{hcode}_{acode}"

            if png_key not in st.session_state:
                if st.button("📥 Generate downloadable image",
                             key=f"btn_{idx}"):
                    st.session_state["active_match"] = match_id
                    with st.spinner("Generating image..."):
                        if is_postseason:
                            rl = f"{PHASE_LABELS.get(phase, phase)} Round {rnd}"
                        else:
                            rl = f"Round {rnd}"
                        # Decide what goes in the right table
                        if played:
                            h_right_data = team_single_game_stats(
                                all_games, home, int(rnd))
                            a_right_data = team_single_game_stats(
                                all_games, away, int(rnd))
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
                            round_label=rl,
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
                    key=f"dl_{idx}",
                )

    st.divider()

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
