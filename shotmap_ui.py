"""
Interface Streamlit des shot maps ELSTATSLAB.

Deux points d'entree, appeles depuis app.py :
  render_match_shotmaps  : sous Impact Pulse, dans la vue match
  render_shot_maps_tab   : onglet "Shot Maps" (choix competition, saison, equipe)

Dans la vue match, les cartes ne sont calculees qu'au clic sur "Show shot maps" et s'affichent
d'abord en miniature ; la taille normale (1800 x 1800, telechargeable) n'est produite qu'au clic
sur "View full size", pour une seule equipe a la fois. Cela evite de surcharger l'app a chaque
ouverture d'un match.
"""
import gc
import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import shootmap as sm



def _mem_mb():
    """(memoire du process, memoire utilisee du conteneur, limite du conteneur) en Mo, ou None."""
    rss = used = limit = None
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) / 1024
                    break
    except Exception:
        pass
    for cur, lim in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                     ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
                      "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            with open(cur) as f:
                used = int(f.read().strip()) / 1048576
            with open(lim) as f:
                raw = f.read().strip()
            limit = None if raw == "max" else int(raw) / 1048576
            if limit is not None and limit > 1_000_000:      # "pas de limite" en cgroup v1
                limit = None
            break
        except Exception:
            continue
    return rss, used, limit


def _log_mem(tag: str):
    """Ecrit la memoire dans les journaux de l'app (Manage app), pour reperer un depassement."""
    rss, used, limit = _mem_mb()
    fmt = lambda v: "n/a" if v is None else f"{v:.0f}"
    print(f"[shotmap] {tag} | process {fmt(rss)} MB | container {fmt(used)} / {fmt(limit)} MB",
          flush=True)


THUMB_DPI = 60         # miniature dans la vue match (720 px)
TAB_DPI = 75           # apercu dans l'onglet Shot Maps (900 px)
FULL_DPI = 150         # taille normale et image telechargeable (1800 px)


# ------------------------------------------------------------------ DATA (cache)
@st.cache_data(ttl=600, show_spinner=False)
def _has_table(_conn, table: str) -> bool:
    row = _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


@st.cache_data(ttl=600, show_spinner=False, max_entries=64)
def _shots(_conn, table: str, season: int, team=None, game_code=None) -> pd.DataFrame:
    return sm.load_shots(_conn, table, season, team, game_code)


@st.cache_data(ttl=600, show_spinner=False)
def _teams(_conn, table: str, season: int) -> list:
    q = f"SELECT DISTINCT CODETEAM FROM {table} WHERE Season = ? ORDER BY CODETEAM"
    return [r[0] for r in _conn.execute(q, (season,)).fetchall() if r[0]]


@st.cache_data(ttl=600, show_spinner=False, max_entries=4)
def _league_context(_conn, table: str, season: int):
    """(reference ligue, matchs suspects) : une seule lecture de la saison pour les deux."""
    _log_mem(f"avant lecture saison {season}")
    out = sm.league_context(_conn, table, season)
    gc.collect()
    _log_mem(f"apres lecture saison {season}")
    return out


@st.cache_data(ttl=600, show_spinner=False, max_entries=24)
def _shotmap_png(_conn, table: str, season: int, team: str, game_code, mode: str,
                 team_name: str, subtitle: str, logo_path: str, team_logo_path: str, note: str = "",
                 dpi: int = FULL_DPI):
    df = _shots(_conn, table, season, team, game_code)
    if df.empty:
        return None
    _log_mem(f"avant rendu {team} match={game_code} dpi={dpi}")
    fig = sm.render_shootmap(
        df, team, subtitle, mode=mode, ref_stats=_league_context(_conn, table, season)[0],
        logo_path=logo_path or None, team_logo_path=team_logo_path or None,
        team_name=team_name, note=note or None,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=sm.BG)
    plt.close(fig)
    gc.collect()
    _log_mem(f"apres rendu {team} match={game_code} dpi={dpi}")
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

        show_key = f"sm_show_{card_index}_{game_code}"
        if not st.session_state.get(show_key):
            st.caption("Where each team shot in this game, coloured against the league average.")
            if st.button("Show shot maps", key=f"sm_show_btn_{card_index}_{game_code}"):
                st.session_state[show_key] = True
                st.rerun()
            return

        suspects = _league_context(conn, table, int(season))[1]
        note = sm.suspect_note([int(game_code)]) if int(game_code) in suspects else None
        if note:
            st.warning(note)
        st.caption(
            "Zone colour compares each team's FG% in that zone with the league average. "
            "Green cross: made shot. Red circle: missed shot."
        )

        teams = [(home_code, home_disp, away_disp), (away_code, away_disp, home_disp)]
        args_by_code = {}
        for code, disp, opp in teams:
            tl = team_logo_fn(code) if team_logo_fn else None
            args_by_code[code] = (conn, table, int(season), code, int(game_code), "zones", disp,
                                  f"EuroLeague {round_label} | vs {opp}",
                                  _p(elstatslab_logo), _p(tl), note or "")

        full_key = f"sm_full_{card_index}_{game_code}"     # equipe affichee en taille normale
        cols = st.columns(2)
        for col, (code, disp, _opp) in zip(cols, teams):
            with col:
                thumb = _shotmap_png(*args_by_code[code], dpi=THUMB_DPI)
                if thumb is None:
                    st.info(f"No shot data for {disp}.")
                    continue
                st.image(thumb, use_container_width=True)
                if st.session_state.get(full_key) == code:
                    st.caption("Shown below in full size.")
                elif st.button("🔍 View full size", key=f"sm_big_{card_index}_{game_code}_{code}"):
                    st.session_state[full_key] = code
                    st.rerun()

        chosen = st.session_state.get(full_key)
        if chosen in args_by_code:
            full = _shotmap_png(*args_by_code[chosen], dpi=FULL_DPI)
            if full is not None:
                st.image(full, use_container_width=True)
                d1, d2 = st.columns(2)
                with d1:
                    st.download_button(
                        "📥 Download", data=full,
                        file_name=f"ShotMap_{chosen}_G{game_code}.png", mime="image/png",
                        key=f"sm_dl_{card_index}_{game_code}_{chosen}",
                    )
                with d2:
                    if st.button("Close full size", key=f"sm_close_{card_index}_{game_code}"):
                        st.session_state.pop(full_key, None)
                        st.rerun()


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

    bad = sorted(set(int(g) for g in df["GameCode"]) & set(_league_context(conn, table, int(season))[1]))
    note = sm.suspect_note(bad)
    if note:
        st.warning(note)

    tl = team_logo_fn(team) if team_logo_fn else None
    args = (conn, table, int(season), team, None, pick, name_fn(team),
            f"{compet} {season} | {sm.games_label(n_games)}", _p(elstatslab_logo), _p(tl), note or "")
    sel = (compet, int(season), team, pick)               # selection courante
    is_full = st.session_state.get("sm_tab_full") == sel

    png = _shotmap_png(*args, dpi=FULL_DPI if is_full else TAB_DPI)
    if png is None:
        st.info("No shots found for this selection.")
        return
    _, mid, _ = st.columns([1, 4, 1])
    with mid:
        st.image(png, use_container_width=True)
        if is_full:
            b1, b2 = st.columns(2)
            with b1:
                st.download_button(
                    "📥 Download image", data=png,
                    file_name=f"ShotMap_{team}_{compet.replace(' ', '')}{season}_{pick}.png",
                    mime="image/png", key="sm_tab_dl",
                )
            with b2:
                if st.button("Back to preview", key="sm_tab_small"):
                    st.session_state.pop("sm_tab_full", None)
                    st.rerun()
        elif st.button("🔍 View full size", key="sm_tab_big"):
            st.session_state["sm_tab_full"] = sel
            st.rerun()
