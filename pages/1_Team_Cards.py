"""
ELSTATSLAB — Team Cards
========================
Page multipage Streamlit (détectée automatiquement dans pages/).
Grille de logos cliquable des 20 équipes EuroLeague, carte de stats
per-game + percentiles, filtres Round / journée / glissant N matchs.

Dépend de shared.py (config équipes, logos, connexion DB) présent à la
racine du projet, à côté de app.py.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from shared import (
    CURRENT_SEASON,
    ELSTATSLAB_LOGO,
    LOGO_MAP,
    LOGOS_DIR,
    ROUND_LABELS,
    TEAM_DISPLAY_NAMES,
    ZOOM_CORRECTIONS,
    logo_b64,
    logo_zoom,
    read_sql,
)

st.set_page_config(
    page_title="ELSTATSLAB — Team Cards",
    page_icon=str(ELSTATSLAB_LOGO) if ELSTATSLAB_LOGO.exists() else "🏀",
    layout="centered",
    initial_sidebar_state="expanded",
)

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
# LOGOS (HTML base64 — réutilise logo_b64 mis en cache par shared.py)
# ============================================================
def render_logo(code: str, box_size: int, opacity: float = 1.0) -> None:
    b64 = logo_b64(code)
    if b64:
        zoom = logo_zoom(code)
        max_width = int(box_size * zoom)
        grayscale = "grayscale(70%)" if opacity < 1.0 else "none"
        st.markdown(
            f"""
            <div style="height:{box_size}px;display:flex;align-items:center;
                        justify-content:center;overflow:hidden;opacity:{opacity};">
                <img src="data:image/png;base64,{b64}"
                     style="max-height:{box_size}px;max-width:{max_width}px;
                            width:auto;height:auto;object-fit:contain;
                            filter:{grayscale};" />
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""<div style="height:{box_size}px;display:flex;align-items:center;
                justify-content:center;font-size:28px;opacity:{opacity};">🏀</div>""",
            unsafe_allow_html=True,
        )


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


def render_stat_row(label: str, value: float, pct: float, value_fmt: str) -> None:
    bg, text_color = percentile_color(pct)
    formatted_value = value_fmt.format(value) if value is not None else "—"
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;padding:7px 0;
                    border-bottom:1px solid #EDEDED;">
            <div style="width:120px;font-size:12px;font-weight:700;
                        color:#8A8A85;letter-spacing:0.5px;">{label}</div>
            <div style="width:75px;font-size:17px;font-weight:800;
                        color:#1A1A1A;">{formatted_value}</div>
            <div style="width:52px;">
                <span title="Ranked better than {int(pct)}% of the other teams in scope"
                      style="background:{bg};color:{text_color};
                            border-radius:10px;padding:2px 9px;
                            font-size:11px;font-weight:800;cursor:help;">{int(pct)}</span>
            </div>
            <div style="flex:1;background:#EEEEEE;border-radius:6px;
                        height:10px;margin-left:6px;overflow:hidden;">
                <div style="width:{pct}%;background:{bg};height:10px;
                            border-radius:6px;"></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# FILTRES (barre horizontale sous le titre)
# ============================================================
def render_filters() -> tuple[str, str | None, int | None, int | None]:
    available_rounds = get_available_rounds(CURRENT_SEASON)

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
        gamedays = get_available_gamedays(CURRENT_SEASON)
        if not gamedays:
            st.warning("No matchday data found.")
            return "round", "RS", None, None
        with filter_cols[2]:
            gameday = st.selectbox(
                "Matchday", options=gamedays, index=len(gamedays) - 1,
                key="tc_gameday_select",
            )
        return "day", None, gameday, None

    max_gp = get_max_games_played(CURRENT_SEASON)
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
# PAGE
# ============================================================
def main() -> None:
    if "tc_selected_team_code" not in st.session_state:
        st.session_state.tc_selected_team_code = None

    st.markdown(
        """
        <style>
        [data-testid="column"] button p {
            font-size: 12px !important;
            white-space: normal !important;
            line-height: 1.25 !important;
            text-align: center !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    title_col1, title_col2 = st.columns([1, 8], vertical_alignment="center")
    with title_col1:
        if ELSTATSLAB_LOGO.exists():
            st.image(str(ELSTATSLAB_LOGO), width=110)
    with title_col2:
        st.title("ELSTATSLAB Team Cards")
        st.caption("Every EuroLeague team, benchmarked against the rest of the league.")

    filter_type, round_code, gameday, n_games = render_filters()

    df = load_team_percentiles(
        CURRENT_SEASON, filter_type,
        round_code=round_code, gameday=gameday, n_games=n_games,
    )

    if df.empty:
        st.error(
            "No data for this filter. This round may not have started yet, "
            "or no matchday/game count matches your selection."
        )
        return

    qualified_codes = set(df["TeamCode"].tolist())

    selected_code = st.session_state.tc_selected_team_code
    if selected_code:
        if st.button("← Back to all teams", key="tc_back_btn"):
            st.session_state.tc_selected_team_code = None
            st.rerun()

        if selected_code not in qualified_codes:
            st.warning(
                f"{TEAM_DISPLAY_NAMES.get(selected_code, selected_code)} did not "
                f"qualify for this round/filter."
            )
            return

        row = df[df["TeamCode"] == selected_code].iloc[0]
        disp_name = TEAM_DISPLAY_NAMES.get(selected_code, row["TeamName"])

        header_col1, header_col2 = st.columns([1, 4])
        with header_col1:
            render_logo(selected_code, box_size=90)
        with header_col2:
            st.markdown(f"#### {disp_name}")
            st.caption(_filter_caption(filter_type, round_code, gameday, n_games, int(row["GP"])))
            st.caption(
                "The colored badge is a rank out of 100 vs the other teams in scope, not a "
                "percentage of the stat. Example: a badge of 74 on OREB% means this team "
                "rebounds better than about 74% of the other teams (100 = best, 0 = worst)."
            )

        for value_key, label, pct_key, fmt in STAT_ROWS:
            render_stat_row(label, row[value_key], row[pct_key], fmt)

        return

    codes = list(LOGO_MAP.keys())
    n_cols = 4
    box_size = 90
    for row_start in range(0, len(codes), n_cols):
        cols = st.columns(n_cols)
        for col, code in zip(cols, codes[row_start:row_start + n_cols]):
            with col:
                is_qualified = code in qualified_codes
                render_logo(code, box_size, opacity=1.0 if is_qualified else 0.35)
                team_name = TEAM_DISPLAY_NAMES.get(code, code)
                if not is_qualified:
                    st.button(
                        team_name, key=f"tc_btn_{code}", use_container_width=True,
                        disabled=True,
                    )
                elif st.button(team_name, key=f"tc_btn_{code}", use_container_width=True):
                    st.session_state.tc_selected_team_code = code
                    st.rerun()


if __name__ == "__main__":
    main()
