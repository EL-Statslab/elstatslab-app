"""
Interface Streamlit des shot maps ELSTATSLAB.

Deux points d'entree, appeles depuis app.py :
  render_match_shotmaps  : sous Impact Pulse, dans la vue match
  render_shot_maps_tab   : onglet "Shot Maps" (choix competition, saison, equipe)

Les images sont rendues en PNG 1800 x 1800 et mises en cache, comme Game Flow.
"""
import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import shootmap as sm


# ------------------------------------------------------------------ DATA (cache)
@st.cache_data(ttl=600, show_spinner=False)
def _has_table(_conn, table: str) -> bool:
    row = _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


@st.cache_data(ttl=600, show_spinner=False)
def _shots(_conn, table: str, season: int, team=None, game_code=None) -> pd.DataFrame:
    return sm.load_shots(_conn, table, season, team, game_code)


@st.cache_data(ttl=600, show_spinner=False)
def _teams(_conn, table: str, season: int) -> list:
    q = f"SELECT DISTINCT CODETEAM FROM {table} WHERE Season = ? ORDER BY CODETEAM"
    return [r[0] for r in _conn.execute(q, (season,)).fetchall() if r[0]]


@st.cache_data(ttl=600, show_spinner=False)
def _suspect_games(_conn, table: str, season: int) -> list:
    return sm.competition_suspect_games(_conn, table, season)


@st.cache_data(ttl=600, show_spinner=False)
def _league_ref(_conn, table: str, season: int):
    return sm.league_reference(_conn, table, season)


@st.cache_data(ttl=600, show_spinner=False)
def _shotmap_png(_conn, table: str, season: int, team: str, game_code, mode: str,
                 team_name: str, subtitle: str, logo_path: str, team_logo_path: str, note: str = ""):
    df = _shots(_conn, table, season, team, game_code)
    if df.empty:
        return None
    fig = sm.render_shootmap(
        df, team, subtitle, mode=mode, ref_stats=_league_ref(_conn, table, season),
        logo_path=logo_path or None, team_logo_path=team_logo_path or None,
        team_name=team_name, note=note or None,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=sm.BG)
    plt.close(fig)
    return buf.getvalue()


def _p(path) -> str:
    return str(path) if path else ""


# ------------------------------------------------------------------ VUE MATCH
def render_match_shotmaps(conn, season: int, game_code: int,
                          home_code: str, away_code: str,
                          home_disp: str, away_disp: str,
                          round_label: str, card_index: int,
                          elstatslab_logo=None, team_logo_fn=None):
    """Expander sous Impact Pulse : une shot map par equipe pour ce match (zones)."""
    table = sm.COMPETITIONS["EuroLeague"]
    with st.expander("🎯 Shot Map — Where did they score?"):
        if not _has_table(conn, table):
            st.info("Shot data is not available yet.")
            return
        note = sm.suspect_note([int(game_code)]) if int(game_code) in _suspect_games(
            conn, table, int(season)) else None
        if note:
            st.warning(note)
        st.caption(
            "Zone colour compares each team's FG% in that zone with the league average. "
            "Green cross: made shot. Red circle: missed shot."
        )
        cols = st.columns(2)
        for col, code, disp, opp in [
            (cols[0], home_code, home_disp, away_disp),
            (cols[1], away_code, away_disp, home_disp),
        ]:
            with col:
                tl = team_logo_fn(code) if team_logo_fn else None
                png = _shotmap_png(
                    conn, table, int(season), code, int(game_code), "zones", disp,
                    f"EuroLeague {round_label} | vs {opp}", _p(elstatslab_logo), _p(tl), note or "",
                )
                if png is None:
                    st.info(f"No shot data for {disp}.")
                    continue
                st.image(png, use_container_width=True)
                st.download_button(
                    "📥 Download", data=png,
                    file_name=f"ShotMap_{code}_G{game_code}.png", mime="image/png",
                    key=f"sm_dl_{card_index}_{game_code}_{code}",
                )


# ------------------------------------------------------------------ ONGLET SHOT MAPS
def render_shot_maps_tab(conn, seasons: list, name_fn,
                         elstatslab_logo=None, team_logo_fn=None):
    """Onglet : choisir competition, saison et equipe, comme dans Team Cards."""
    st.caption("Where does a team shoot, and how well? Pick a competition and a team.")

    c1, c2, c3 = st.columns(3)
    compet = c1.selectbox("Competition", list(sm.COMPETITIONS), key="sm_compet")
    season = c2.selectbox("Season", seasons, key="sm_season")
    table = sm.COMPETITIONS[compet]

    if not _has_table(conn, table):
        st.info(f"No {compet} shot data available yet.")
        return
    teams = _teams(conn, table, int(season))
    if not teams:
        st.info(f"No {compet} shots for season {season}.")
        return
    team = c3.selectbox("Team", teams, format_func=name_fn, key="sm_team")

    df = _shots(conn, table, int(season), team)
    if df.empty:
        st.info("No shots found for this selection.")
        return

    n_games = df["GameCode"].nunique()
    modes = sm.available_modes(df)
    if len(modes) > 1:
        labels = {"zones": "Zones", "heat": "Heatmap"}
        pick = st.radio("View", modes, horizontal=True, format_func=labels.get, key="sm_mode")
    else:
        pick = "zones"
        st.caption(
            f"Heatmap view unlocks after {sm.MIN_GAMES_KDE} games "
            f"({n_games} played so far in this selection)."
        )

    bad = sorted(set(int(g) for g in df["GameCode"]) & set(_suspect_games(conn, table, int(season))))
    note = sm.suspect_note(bad)
    if note:
        st.warning(note)

    tl = team_logo_fn(team) if team_logo_fn else None
    png = _shotmap_png(
        conn, table, int(season), team, None, pick, name_fn(team),
        f"{compet} {season} | {sm.games_label(n_games)}", _p(elstatslab_logo), _p(tl), note or "",
    )
    if png is None:
        st.info("No shots found for this selection.")
        return
    _, mid, _ = st.columns([1, 4, 1])
    with mid:
        st.image(png, use_container_width=True)
        st.download_button(
            "📥 Download image", data=png,
            file_name=f"ShotMap_{team}_{compet.replace(' ', '')}{season}_{pick}.png",
            mime="image/png", key="sm_tab_dl",
        )
