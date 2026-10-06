"""
ELSTATSLAB — Team Cards (module)
==================================
Module importé par app.py sous l'onglet "Team Cards" (st.tabs).
Grille de logos cliquable (style papier, aligne sur l'export) des 20 équipes EuroLeague, carte de stats
per-game + percentiles, filtres Round / journée / glissant N matchs.

Dépend de shared.py (config équipes, logos, connexion DB) présent à la
racine du projet, à côté de app.py.

N'appelle PAS st.set_page_config (fait une seule fois dans app.py).
Exposé via render(), appelé depuis app.py à l'intérieur d'un onglet.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from shared import (
    AVAILABLE_SEASONS,
    CURRENT_SEASON,
    ELSTATSLAB_LOGO,
    LOGO_MAP,
    LOGOS_DIR,
    ROUND_LABELS,
    TEAM_DISPLAY_NAMES,
    ZOOM_CORRECTIONS,
    get_season_team_codes,
    logo_b64,
    logo_zoom,
    read_sql,
)
from site_theme import badge_logo_b64, brand_logo_b64
from team_card_export import build_team_card_png

STAT_ROWS = [
    ("net_rtg", "NET RTG", "pct_net_rtg", "{:.1f}"),
    ("off_rtg", "OFF RTG", "pct_off_rtg", "{:.1f}"),
    ("def_rtg", "DEF RTG", "pct_def_rtg", "{:.1f}"),
    ("pace", "PACE", "pct_pace", "{:.1f}"),
    ("ast_pct", "AST%", "pct_ast_pct", "{:.1f}%"),
    ("tov_pct", "TOV%", "pct_tov_pct", "{:.1f}%"),
    ("oreb_pct", "OREB%", "pct_oreb_pct", "{:.1f}%"),
    ("dreb_pct", "DREB%", "pct_dreb_pct", "{:.1f}%"),
    ("threepm", "3PM", "pct_threepm", "{:.1f}"),
    ("three_pct", "3P%", "pct_three_pct", "{:.1f}%"),
    ("ts_pct", "TS%", "pct_ts_pct", "{:.1f}%"),
    ("paint_pts", "PAINT PTS", "pct_paint_pts", "{:.1f}"),
    ("fastbreak_pts", "FAST BRK", "pct_fastbreak_pts", "{:.1f}"),
    ("second_chance_pts", "2ND CHANCE", "pct_second_chance_pts", "{:.1f}"),
    ("pts_off_to", "PTS OFF TO", "pct_pts_off_to", "{:.1f}"),
]
_INVERTED_STATS = {"def_rtg", "tov_pct"}



# ============================================================
# QUERY BUILDER
# ============================================================
def _build_scope_sql(filter_type: str) -> str:
    if filter_type == "round":
        return """
        scope_pairs AS (
            SELECT tb.TeamName, tb.GameCode
            FROM team_base_all tb
            JOIN schedule_full s
                ON s.Season = tb.Season AND s.game_number = tb.GameCode
            WHERE s.round = ?
        )
        """
    if filter_type == "day":
        return """
        scope_pairs AS (
            SELECT tb.TeamName, tb.GameCode
            FROM team_base_all tb
            JOIN schedule_full s
                ON s.Season = tb.Season AND s.game_number = tb.GameCode
            WHERE s.round = 'RS' AND s.gameday = ?
        )
        """
    if filter_type == "rolling":
        return """
        scope_pairs AS (
            SELECT TeamName, GameCode FROM (
                SELECT tb.TeamName, tb.GameCode,
                       ROW_NUMBER() OVER (
                           PARTITION BY tb.TeamName
                           ORDER BY s.date DESC, s.game_number DESC
                       ) AS rn
                FROM team_base_all tb
                JOIN schedule_full s
                    ON s.Season = tb.Season AND s.game_number = tb.GameCode
                WHERE s.round = 'RS'
            )
            WHERE rn <= ?
        )
        """
    raise ValueError(f"Unknown filter_type: {filter_type}")


def _build_full_query(filter_type: str) -> str:
    scope_cte = _build_scope_sql(filter_type)
    pct_order = {
        stat: f'tp.{stat}{" DESC" if stat in _INVERTED_STATS else ""}'
        for stat, _, _, _ in STAT_ROWS
    }
    select_lines = []
    for stat, _, pct_col, _ in STAT_ROWS:
        select_lines.append(
            f"    tp.{stat}, ROUND(PERCENT_RANK() OVER (ORDER BY {pct_order[stat]}) * 100) AS {pct_col}"
        )
    select_block = ",\n".join(select_lines)

    return f"""
    WITH schedule_full AS (
        SELECT Season, game_number, round, gameday, date,
               hometeam, homecode, awayteam, awaycode
        FROM schedule
        WHERE Season = ?
    ),
    team_codes AS (
        SELECT DISTINCT homecode AS code, hometeam AS name FROM schedule_full
        UNION
        SELECT DISTINCT awaycode AS code, awayteam AS name FROM schedule_full
    ),
    team_base_all AS (
        SELECT ts.* FROM team_stats ts WHERE ts.Season = ?
    ),
    shot_agg AS (
        SELECT
            sd.Season, sd.GameCode, sd.CODETEAM,
            SUM(CASE WHEN sd.COORD_X <> -1 AND ABS(sd.COORD_X) <= 245
                      AND sd.COORD_Y BETWEEN -20 AND 580 THEN sd.POINTS ELSE 0 END) AS paint_pts,
            SUM(CASE WHEN sd.FASTBREAK = 1 THEN sd.POINTS ELSE 0 END) AS fastbreak_pts,
            SUM(CASE WHEN sd.SECOND_CHANCE = 1 THEN sd.POINTS ELSE 0 END) AS second_chance_pts,
            SUM(CASE WHEN sd.POINTS_OFF_TURNOVER = 1 THEN sd.POINTS ELSE 0 END) AS pts_off_to
        FROM shot_data sd
        WHERE sd.Season = ?
        GROUP BY sd.Season, sd.GameCode, sd.CODETEAM
    ),
    shot_named AS (
        SELECT
            sa.Season, sa.GameCode,
            CASE WHEN sa.CODETEAM = s.homecode THEN s.hometeam ELSE s.awayteam END AS TeamName,
            sa.paint_pts, sa.fastbreak_pts, sa.second_chance_pts, sa.pts_off_to
        FROM shot_agg sa
        JOIN schedule_full s ON s.Season = sa.Season AND s.game_number = sa.GameCode
    ),
    {scope_cte},
    team_full AS (
        SELECT tb.*, sn.paint_pts, sn.fastbreak_pts, sn.second_chance_pts, sn.pts_off_to
        FROM team_base_all tb
        JOIN scope_pairs sp ON sp.TeamName = tb.TeamName AND sp.GameCode = tb.GameCode
        LEFT JOIN shot_named sn
            ON sn.Season = tb.Season AND sn.GameCode = tb.GameCode
            AND UPPER(sn.TeamName) = UPPER(tb.TeamName)
    ),
    team_pg AS (
        SELECT
            TeamName,
            COUNT(*) AS GP,
            AVG(Off_Rtg) AS off_rtg,
            AVG(Def_Rtg) AS def_rtg,
            AVG(Off_Rtg - Def_Rtg) AS net_rtg,
            AVG(Pace) AS pace,
            SUM(Ast) * 1.0 / NULLIF(SUM("2PM" + "3PM"), 0) * 100 AS ast_pct,
            SUM(Turnovers) * 1.0
                / NULLIF(SUM("2PA" + "3PA" + 0.44 * "FTA" + Turnovers), 0) * 100 AS tov_pct,
            AVG("3PM") AS threepm,
            SUM("3PM") * 1.0 / NULLIF(SUM("3PA"), 0) * 100 AS three_pct,
            AVG(TS_Pct) AS ts_pct,
            AVG(OREB_Pct) AS oreb_pct,
            AVG(DREB_Pct) AS dreb_pct,
            AVG(paint_pts) AS paint_pts,
            AVG(fastbreak_pts) AS fastbreak_pts,
            AVG(second_chance_pts) AS second_chance_pts,
            AVG(pts_off_to) AS pts_off_to
        FROM team_full
        GROUP BY TeamName
    )
    SELECT
        tc.code AS TeamCode,
        tp.TeamName, tp.GP,
{select_block}
    FROM team_pg tp
    JOIN team_codes tc ON UPPER(tc.name) = UPPER(tp.TeamName)
    ORDER BY tp.TeamName;
    """


@st.cache_data(ttl=600)
def load_team_percentiles(
    season: int,
    filter_type: str,
    round_code: str | None = None,
    gameday: int | None = None,
    n_games: int | None = None,
) -> pd.DataFrame:
    query = _build_full_query(filter_type)

    if filter_type == "round":
        scope_param = (round_code,)
    elif filter_type == "day":
        scope_param = (gameday,)
    elif filter_type == "rolling":
        scope_param = (n_games,)
    else:
        raise ValueError(f"Unknown filter_type: {filter_type}")

    params = (season, season, season) + scope_param
    return read_sql(query, params)


@st.cache_data(ttl=600)
def get_available_rounds(season: int) -> list[str]:
    rounds = read_sql(
        "SELECT DISTINCT round FROM schedule WHERE Season = ? ORDER BY round",
        (season,),
    )["round"].tolist()
    if "RS" in rounds:
        rounds.remove("RS")
        rounds = ["RS"] + rounds
    return rounds


@st.cache_data(ttl=600)
def get_available_gamedays(season: int) -> list[int]:
    days = read_sql(
        "SELECT DISTINCT gameday FROM schedule "
        "WHERE Season = ? AND round = 'RS' ORDER BY gameday",
        (season,),
    )["gameday"].tolist()
    return [int(d) for d in days]


@st.cache_data(ttl=600)
def get_max_games_played(season: int) -> int:
    result = read_sql(
        """
        SELECT MAX(cnt) AS max_gp FROM (
            SELECT ts.TeamName, COUNT(*) AS cnt
            FROM team_stats ts
            JOIN schedule s ON s.Season = ts.Season AND s.game_number = ts.GameCode
            WHERE ts.Season = ? AND s.round = 'RS'
            GROUP BY ts.TeamName
        )
        """,
        (season,),
    )
    max_gp = result["max_gp"].iloc[0]
    return int(max_gp) if max_gp else 38


# ============================================================
# COULEURS
# ============================================================
def percentile_color(pct: float) -> tuple[str, str]:
    if pct >= 80:
        return "#1E8449", "#FFFFFF"
    elif pct >= 60:
        return "#82C99A", "#153B24"
    elif pct >= 40:
        return "#F2C94C", "#5C4300"
    elif pct >= 20:
        return "#F2B8B5", "#5A1F1D"
    else:
        return "#D9534F", "#FFFFFF"


# ============================================================
# FILTRES (barre horizontale sous le titre)
# ============================================================
def render_filters(season: int) -> tuple[str, str | None, int | None, int | None]:
    available_rounds = get_available_rounds(season)

    filter_cols = st.columns([2, 2, 2])
    with filter_cols[0]:
        round_code = st.selectbox(
            "Round",
            options=available_rounds,
            format_func=lambda r: ROUND_LABELS.get(r, r),
            key="tc_round_select",
        )

    if round_code != "RS":
        return "round", round_code, None, None

    with filter_cols[1]:
        rs_mode = st.selectbox(
            "Regular Season view",
            options=["Full season", "Single matchday", "Last N games"],
            key="tc_rs_mode",
        )

    if rs_mode == "Full season":
        return "round", "RS", None, None

    if rs_mode == "Single matchday":
        gamedays = get_available_gamedays(season)
        if not gamedays:
            st.warning("No matchday data found.")
            return "round", "RS", None, None
        with filter_cols[2]:
            gameday = st.selectbox(
                "Matchday", options=gamedays, index=len(gamedays) - 1,
                key="tc_gameday_select",
            )
        return "day", None, gameday, None

    max_gp = get_max_games_played(season)
    with filter_cols[2]:
        n_games = st.number_input(
            "Number of games", min_value=1, max_value=max_gp, value=min(5, max_gp),
            key="tc_n_games_input",
        )
    return "rolling", None, None, int(n_games)


def _filter_caption(filter_type: str, round_code: str | None, gameday: int | None,
                     n_games: int | None, gp: int) -> str:
    if filter_type == "round":
        label = ROUND_LABELS.get(round_code, round_code)
        return f"{label} · {gp} GP · Per Game"
    if filter_type == "day":
        return f"Regular Season · Matchday {gameday} · 1 Game"
    if filter_type == "rolling":
        return f"Regular Season · Last {n_games} Games"
    return f"{gp} GP · Per Game"


# ============================================================
# EXPORT PNG (visuel de marque pour X)
# ============================================================
def _png_scope_label(filter_type: str, round_code: str | None,
                     gameday: int | None, n_games: int | None) -> str:
    if filter_type == "round":
        return f"{ROUND_LABELS.get(round_code, round_code)} · Per Game"
    if filter_type == "day":
        return f"Regular Season · Round {gameday} · 1 Game"
    return f"Regular Season · Last {n_games} Games · Per Game"


def _season_label(season: int) -> str:
    # Season=YYYY en base correspond à la saison YYYY-(YY+1)
    return f"{season}-{str(season + 1)[-2:]}"


def _team_logo_path(code: str) -> Path | None:
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    p = Path(LOGOS_DIR) / filename
    return p if p.exists() else None


def build_team_card_export(row: pd.Series, code: str, disp_name: str, season: int,
                           filter_type: str, round_code: str | None,
                           gameday: int | None, n_games: int | None) -> bytes:
    rows = []
    for value_key, label, pct_key, fmt in STAT_ROWS:
        value = row[value_key]
        pct = row[pct_key]
        value_text = fmt.format(value) if pd.notna(value) else "n/a"
        rows.append((label, value_text, float(pct) if pd.notna(pct) else None))

    return build_team_card_png(
        team_name=disp_name,
        rows=rows,
        team_logo_path=_team_logo_path(code),
        scope_label=_png_scope_label(filter_type, round_code, gameday, n_games),
        season_label=_season_label(season),
        games_played=int(row["GP"]) if filter_type == "round" else None,
        colour_fn=percentile_color,
    )



# ============================================================
# LOOK ON SITE (same paper identity as the PNG export)
# ============================================================
NAVY, ORANGE, GREY = "#14213D", "#E4572E", "#6B7280"

_TC_CSS = """<style>
.tc-banner{background:#14213D;border-radius:22px;padding:22px 28px;display:flex;align-items:center;
 gap:24px;margin:6px 0 16px;}
.tc-crest{width:104px;height:104px;border-radius:20px;background:#fff;flex:0 0 auto;display:block;}
.tc-bn{flex:1;min-width:0;}
.tc-bn .n{font-size:2.6rem;font-weight:700;color:#F3EEE4;line-height:1.05;}
.tc-bn .s{font-size:1.2rem;color:#B9BFCC;margin-top:6px;}
.tc-gp{background:#E4572E;color:#fff;font-weight:700;letter-spacing:.06em;font-size:1rem;
 padding:10px 22px;border-radius:999px;white-space:nowrap;}
.tc-hl{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px;}
.tc-hlc{background:#FBF8F1;border:2px solid #E1D8C6;border-radius:18px;padding:14px 18px 16px;}
.tc-hlc .t{font-weight:700;color:#14213D;letter-spacing:.06em;font-size:1rem;display:flex;align-items:center;gap:8px;}
.tc-hlc .t i{display:inline-block;width:7px;height:18px;border-radius:2px;}
.tc-hlc .it{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px;}
.tc-hlc .l{font-size:.9rem;color:#6B7280;font-weight:600;letter-spacing:.04em;}
.tc-hlc .v{display:flex;align-items:center;justify-content:flex-start;gap:10px;margin-top:2px;
 font-size:1.55rem;font-weight:700;color:#14213D;}
.tc-pill{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;
 border-radius:999px;font-size:.85rem;font-weight:800;padding:0 4px;}
.tc-hdr,.tc-row{display:grid;grid-template-columns:170px 120px 1fr;align-items:center;}
.tc-hdr{border-bottom:2px solid #14213D;padding:6px 14px;font-size:.85rem;font-weight:700;
 letter-spacing:.1em;color:#6B7280;}
.tc-hdr .sc{display:flex;justify-content:space-between;padding:0 16px;}
.tc-hdr .sc b{color:#14213D;}
.tc-row{padding:0 14px;min-height:54px;border-left:6px solid transparent;border-radius:10px;
 font-variant-numeric:tabular-nums;}
.tc-row.alt{background:#FBF8F1;}
.tc-row.st{border-left-color:#1E8449;}
.tc-row.wk{border-left-color:#D9534F;}
.tc-row .lb{font-size:1.15rem;font-weight:600;color:#14213D;letter-spacing:.02em;}
.tc-row .vl{font-size:1.6rem;font-weight:700;color:#14213D;}
.tc-tr{position:relative;height:54px;}
.tc-tr::before{content:"";position:absolute;left:50%;top:0;bottom:0;border-left:2px dashed #CFC5B0;}
.tc-in{position:absolute;left:16px;right:16px;top:0;bottom:0;}
.tc-bar{position:absolute;left:0;right:0;top:50%;height:5px;margin-top:-2.5px;background:#E8DFCD;border-radius:3px;}
.tc-fill{position:absolute;left:0;top:50%;height:6px;margin-top:-3px;border-radius:3px;}
.tc-dot{position:absolute;top:50%;width:32px;height:32px;margin:-16px 0 0 -16px;border-radius:50%;
 display:flex;align-items:center;justify-content:center;font-size:.95rem;font-weight:800;}
.tc-note{color:#6B7280;font-size:.95rem;margin:10px 4px 4px;line-height:1.5;}
.tc-team{background:#fff;border:2px solid #E1D8C6;border-radius:20px;height:128px;display:flex;
 align-items:center;justify-content:center;margin-bottom:6px;}
.tc-team img{width:104px;height:104px;border-radius:14px;display:block;}
.tc-team.off{opacity:.35;filter:grayscale(70%);}
@media (max-width:760px){
 .tc-hdr,.tc-row{grid-template-columns:96px 74px 1fr;}
 .tc-row .lb{font-size:.95rem;} .tc-row .vl{font-size:1.2rem;}
 .tc-banner{flex-wrap:wrap;padding:16px;} .tc-bn .n{font-size:1.8rem;}
 .tc-crest{width:76px;height:76px;} .tc-hl{grid-template-columns:1fr;}
}
</style>"""


def _logo_path(code: str):
    p = _team_logo_path(code)
    return str(p) if p else None


def _crest_img(code: str, cls: str) -> str:
    p = _logo_path(code)
    if not p:
        return f"<div class='{cls}' style='display:flex;align-items:center;justify-content:center;font-size:2rem;'>🏀</div>"
    return f"<img class='{cls}' src='data:image/png;base64,{badge_logo_b64(p)}' alt='{code}'/>"


def _pill(pct) -> str:
    bg, fg = percentile_color(pct)
    return f"<span class='tc-pill' style='background:{bg};color:{fg};'>{int(round(pct))}</span>"


def _pick_extremes(rows, k=3, neutral=("PACE",)):
    """Same rule as the PNG export: strongest above 50, weakest below 50, PACE skipped."""
    valid = [i for i, r in enumerate(rows) if r[2] is not None and r[0] not in neutral]
    strong = [i for i in sorted(valid, key=lambda i: (-rows[i][2], i)) if rows[i][2] > 50][:k]
    weak = [i for i in sorted(valid, key=lambda i: (rows[i][2], i)) if rows[i][2] < 50][:k]
    return strong, weak


def _card_rows(row: pd.Series):
    rows = []
    for value_key, label, pct_key, fmt in STAT_ROWS:
        v, p = row[value_key], row[pct_key]
        rows.append((label, fmt.format(v) if pd.notna(v) else "n/a",
                     float(p) if pd.notna(p) else None))
    return rows


def _highlight_html(title: str, color: str, idxs: list, rows: list) -> str:
    items = ""
    for i in idxs:
        label, vtxt, pct = rows[i]
        items += f"<div><div class='l'>{label}</div><div class='v'><span>{vtxt}</span>{_pill(pct)}</div></div>"
    if not items:
        items = "<div class='l'>None</div>"
    return f"<div class='tc-hlc'><div class='t'><i style='background:{color}'></i>{title}</div><div class='it'>{items}</div></div>"


def _team_card_html(disp_name: str, code: str, subtitle: str, gp_text: str | None, rows: list) -> str:
    strong, weak = _pick_extremes(rows)
    pill = f"<div class='tc-gp'>{gp_text}</div>" if gp_text else ""
    html = (
        f"<div class='tc-banner'>{_crest_img(code, 'tc-crest')}"
        f"<div class='tc-bn'><div class='n'>{disp_name}</div><div class='s'>{subtitle}</div></div>{pill}</div>"
        "<div class='tc-hl'>"
        + _highlight_html("STRONGEST", percentile_color(100)[0], strong, rows)
        + _highlight_html("WEAKEST", percentile_color(0)[0], weak, rows)
        + "</div>"
        "<div class='tc-hdr'><div>METRIC</div><div>VALUE</div>"
        "<div class='sc'><span>WORST</span><b>LEAGUE MEDIAN</b><span>BEST</span></div></div>"
    )
    sset, wset = set(strong), set(weak)
    for i, (label, vtxt, pct) in enumerate(rows):
        cls = "tc-row" + (" alt" if i % 2 == 0 else "") + (" st" if i in sset else "") + (" wk" if i in wset else "")
        if pct is None:
            track = "<div class='tc-tr'><div class='tc-in'><div class='tc-bar'></div></div></div>"
        else:
            p = max(0.0, min(100.0, pct))
            bg, fg = percentile_color(p)
            track = (
                "<div class='tc-tr'><div class='tc-in'><div class='tc-bar'></div>"
                f"<div class='tc-fill' style='width:{p}%;background:{bg};'></div>"
                f"<div class='tc-dot' style='left:{p}%;background:{bg};color:{fg};'>{int(round(p))}</div>"
                "</div></div>"
            )
        html += f"<div class='{cls}'><div class='lb'>{label}</div><div class='vl'>{vtxt}</div>{track}</div>"
    html += (
        "<div class='tc-note'>Dot = rank out of 100 vs the other teams in scope (100 best, 0 worst). "
        "PACE: high means fast.<br>Strongest and weakest metrics are marked on the left edge of their rows "
        "(PACE excluded).</div>"
    )
    return html


# ============================================================
# RENDER, called from app.py inside a tab
# ============================================================
def render() -> None:
    if "tc_selected_team_code" not in st.session_state:
        st.session_state.tc_selected_team_code = None

    st.markdown(_TC_CSS, unsafe_allow_html=True)
    st.markdown(
        """<style>[data-testid="column"] button p{font-size:14px !important;white-space:normal !important;
        line-height:1.25 !important;text-align:center !important;}</style>""",
        unsafe_allow_html=True,
    )

    st.caption("Every EuroLeague team, benchmarked against the rest of the league.")

    season_col, _ = st.columns([1, 5])
    with season_col:
        season = st.selectbox("Season", options=AVAILABLE_SEASONS, index=0, key="tc_season_select")

    filter_type, round_code, gameday, n_games = render_filters(season)

    df = load_team_percentiles(season, filter_type, round_code=round_code,
                               gameday=gameday, n_games=n_games)
    if df.empty:
        st.error("No data for this filter. This round may not have started yet, "
                 "or no matchday/game count matches your selection.")
        return

    qualified_codes = set(df["TeamCode"].tolist())
    selected_code = st.session_state.tc_selected_team_code

    if selected_code:
        if st.button("← Back to all teams", key="tc_back_btn"):
            st.session_state.tc_selected_team_code = None
            st.rerun()

        if selected_code not in qualified_codes:
            st.warning(f"{TEAM_DISPLAY_NAMES.get(selected_code, selected_code)} did not "
                       f"qualify for this round/filter.")
            return

        row = df[df["TeamCode"] == selected_code].iloc[0]
        disp_name = TEAM_DISPLAY_NAMES.get(selected_code, row["TeamName"])
        gp = int(row["GP"])
        subtitle = _png_scope_label(filter_type, round_code, gameday, n_games).replace(" · ", " · ")
        gp_text = f"{gp} GAME{'S' if gp != 1 else ''} PLAYED" if filter_type == "round" else None

        st.markdown(_team_card_html(disp_name, selected_code, subtitle, gp_text, _card_rows(row)),
                    unsafe_allow_html=True)

        # Export PNG
        st.divider()
        scope_key = f"{selected_code}_{season}_{filter_type}_{round_code}_{gameday}_{n_games}"
        png_key = f"tc_png_{scope_key}"
        if png_key not in st.session_state:
            if st.button("📥 Generate Team Card image", key=f"tc_png_btn_{scope_key}"):
                with st.spinner("Generating Team Card image..."):
                    st.session_state[png_key] = build_team_card_export(
                        row, selected_code, disp_name, season,
                        filter_type, round_code, gameday, n_games,
                    )
                st.rerun()
        if png_key in st.session_state:
            st.download_button(
                label="📥 Download Team Card image",
                data=st.session_state[png_key],
                file_name=f"TeamCard_{selected_code}.png",
                mime="image/png",
                key=f"tc_png_dl_{scope_key}",
            )
        return

    codes = get_season_team_codes(season)
    n_cols = 4
    for row_start in range(0, len(codes), n_cols):
        cols = st.columns(n_cols)
        for col, code in zip(cols, codes[row_start:row_start + n_cols]):
            with col:
                ok = code in qualified_codes
                st.markdown(f"<div class='tc-team{'' if ok else ' off'}'>{_crest_img(code, '')}</div>",
                            unsafe_allow_html=True)
                team_name = TEAM_DISPLAY_NAMES.get(code, code)
                if not ok:
                    st.button(team_name, key=f"tc_btn_{code}", use_container_width=True, disabled=True)
                elif st.button(team_name, key=f"tc_btn_{code}", use_container_width=True):
                    st.session_state.tc_selected_team_code = code
                    st.rerun()
