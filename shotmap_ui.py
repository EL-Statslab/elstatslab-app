"""
Interface Streamlit des shot maps ELSTATSLAB.

Deux points d'entree, appeles depuis app.py :
  render_match_shotmaps  : sous Impact Pulse, dans la vue match
  render_shot_maps_tab   : onglet "Shot Maps" (choix competition, saison, equipe)

Dans la vue match, les cartes ne sont calculees qu'au clic sur "Show shot maps" et s'affichent
d'abord en miniature ; la taille normale (1800 x 2250, telechargeable) n'est produite qu'au clic
sur "View full size", pour une seule equipe a la fois. Cela evite de surcharger l'app a chaque
ouverture d'un match.
"""
import faulthandler
import gc
import io
import time

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import shootmap as sm

try:   # en cas de plantage dur (segfault), la pile Python est ecrite dans les journaux
    faulthandler.enable()
except Exception:
    pass



SEP_PX = 6             # trait de separation entre les deux cartes dans l'image cote a cote


def _side_by_side(png_left: bytes, png_right: bytes) -> bytes:
    """Colle deux images PNG cote a cote sur le fond papier ELSTATSLAB (par defaut 3600 x 2250 px)."""
    from PIL import Image, ImageDraw

    with Image.open(io.BytesIO(png_left)) as a, Image.open(io.BytesIO(png_right)) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        canvas = Image.new("RGB", (a.width + SEP_PX + b.width, max(a.height, b.height)), (243, 238, 228))
        canvas.paste(a, (0, 0))
        canvas.paste(b, (a.width + SEP_PX, 0))
        x = a.width + SEP_PX // 2
        ImageDraw.Draw(canvas).line([(x, 60), (x, canvas.height - 60)], fill=(225, 216, 198), width=SEP_PX)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


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


# Test de diagnostic : mettre False pour afficher les cartes sans le logo de l'equipe.
USE_TEAM_LOGOS = True

# Vue match : True = les miniatures s'affichent des l'ouverture du match ; False = il faut d'abord
# cliquer sur "Show shot maps" (utile si l'app devait un jour manquer de memoire).
SHOW_ON_OPEN = True

THUMB_DPI = 60         # miniature dans la vue match (720 x 900 px)
TAB_DPI = 75           # apercu dans l'onglet Shot Maps (900 x 1125 px)
FULL_DPI = 150         # taille normale et image telechargeable (1800 x 2250 px)


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
                 dpi: int = FULL_DPI, show_points: bool = True, overlay: str = "none"):
    df = _shots(_conn, table, season, team, game_code)
    if df.empty:
        return None
    _log_mem(f"avant rendu {team} match={game_code} dpi={dpi}")
    t0 = time.time()
    fig = sm.render_shootmap(
        df, team, subtitle, mode=mode, ref_stats=_league_context(_conn, table, season)[0],
        logo_path=logo_path or None,
        team_logo_path=(team_logo_path or None) if USE_TEAM_LOGOS else None,
        team_name=team_name, note=note or None, show_points=show_points,
        overlay=overlay,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=sm.BG)
    plt.close(fig)
    gc.collect()
    _log_mem(f"apres rendu {team} match={game_code} dpi={dpi} en {time.time() - t0:.1f} s")
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
        if not SHOW_ON_OPEN and not st.session_state.get(show_key):
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

        # Image des deux cartes cote a cote, en pleine qualite (pratique pour X).
        # Generee au clic dans l'execution normale de l'app (comme les autres exports), puis proposee
        # au telechargement : la generation "differee" de Streamlit a donne une image mal composee.
        if all(_shotmap_png(*args_by_code[c], dpi=THUMB_DPI) is not None for c, _, _ in teams):
            both_key = f"sm_both_{card_index}_{game_code}"
            both_name = f"ShotMaps_{home_code}_vs_{away_code}_G{game_code}.png"
            if both_key not in st.session_state:
                if st.button("📥 Generate both side by side (full quality)", key=f"{both_key}_gen"):
                    with st.spinner("Generating image..."):
                        left = _shotmap_png(*args_by_code[home_code], dpi=FULL_DPI)
                        right = _shotmap_png(*args_by_code[away_code], dpi=FULL_DPI)
                        st.session_state[both_key] = _side_by_side(left, right)
                    st.rerun()
            else:
                st.download_button("📥 Download both side by side", data=st.session_state[both_key],
                                   file_name=both_name, mime="image/png", key=f"{both_key}_dl")

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
    """Onglet : choisir saison et equipe (EuroLeague uniquement, sans les points tir par tir)."""
    st.caption("Where does a team shoot, and how well? Pick a season and a team.")

    compet = "EuroLeague"      # EuroCup et Super Cup : exports pour X uniquement, jamais sur le site
    table = sm.COMPETITIONS[compet]
    c1, c2 = st.columns(2)
    season = c1.selectbox("Season", seasons, key="sm_season")

    if not _has_table(conn, table):
        st.info(f"No {compet} shot data available yet.")
        return
    teams = _teams(conn, table, int(season))
    if not teams:
        st.info(f"No {compet} shots for season {season}.")
        return
    team = c2.selectbox("Team", teams, format_func=name_fn, key="sm_team")

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

    png = _shotmap_png(*args, dpi=FULL_DPI if is_full else TAB_DPI, show_points=False, overlay="hex")
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
