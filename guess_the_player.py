"""
ELSTATSLAB | Guess the Player (daily game)

Drop this file next to app.py (ELSTATSLAB_APP) and call render_guess_game()
inside a new tab. It only reads player_stats from euroleague_public.db,
loads no logos and no images, so it is very light on memory.

Rules shown to the user:
  * One mystery EuroLeague player per day, the same for everybody.
  * 8 guesses. Each guess reveals how it compares with the mystery player.
  * Green = match, orange = close, dark blue = no match, arrows show higher or lower.
"""

import hashlib
import html
import os
import sqlite3
from datetime import date, datetime

import pandas as pd
import streamlit as st

from guess_logic import (
    MIN_GAMES_FOR_ANSWER,
    MIN_MPG_FOR_ANSWER,
    parse_minutes as _parse_minutes,
    pick_name as _pick_name,
    eligible_from_summary as _eligible_from_summary,
    today_paris as _today_paris,
)

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

# ----------------------------------------------------------------------------
# Config (edit freely)
# ----------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "euroleague_public.db")
MAX_GUESSES = 10
# MIN_GAMES_FOR_ANSWER and MIN_MPG_FOR_ANSWER now live in guess_logic.py (shared with build_public_db.py)

# Thresholds for the "close" (orange) status
CLOSE_AGE = 2
CLOSE_HEIGHT = 4
CLOSE_MIN = 3.0
CLOSE_PIR = 2.0

# Colors
C_OK = "#2e9e5b"
C_CLOSE = "#e8491c"
C_NO = "#33415c"
C_TEXT = "#ffffff"

# Attribute columns shown in the grid: key, header, kind
ATTRS = [
    ("team", "TEAM", "exact"),
    ("pos", "POS", "exact"),
    ("country", "NAT", "exact"),
    ("age", "AGE", "num"),
    ("height", "HT (cm)", "num"),
    ("mpg", "MIN", "num"),
    ("pir", "PIR", "num"),
]
CLOSE = {"age": CLOSE_AGE, "height": CLOSE_HEIGHT, "mpg": CLOSE_MIN, "pir": CLOSE_PIR}
DECIMALS = {"age": 0, "height": 0, "mpg": 1, "pir": 1}


# ----------------------------------------------------------------------------
# Time helpers
# ----------------------------------------------------------------------------
def _today():
    """Today's date in Paris time, so the puzzle changes at local midnight."""
    return _today_paris()


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _date_label(d):
    """English short date, independent of the server locale: 'Oct 24'."""
    return f"{_MONTHS[d.month - 1]} {d.day}"


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def _parse_height_cm(v):
    try:
        x = float(str(v).replace(",", ".").replace("cm", "").strip())
    except Exception:
        return None
    if x <= 0:
        return None
    if x < 3:      # meters
        x *= 100
    return round(x)


def _age(birth, today):
    try:
        b = pd.to_datetime(birth, errors="coerce")
        if pd.isna(b):
            return None
        a = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        return int(a) if 15 <= a <= 50 else None
    except Exception:
        return None


def _mode(series):
    s = series.dropna()
    s = s[s.astype(str).str.strip() != ""]
    if s.empty:
        return None
    return s.mode().iloc[0]


def _find_db():
    """euroleague_public.db next to this file (site), else euroleague.db one folder up (local tests)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [p for p in (DB_PATH, os.path.join(os.path.dirname(here), "euroleague.db")) if os.path.exists(p)]
    for p in candidates:
        try:
            con = sqlite3.connect(p)
            try:
                _find_source_table(con, p)
                return p
            finally:
                con.close()
        except Exception:
            continue
    return candidates[0] if candidates else DB_PATH


def _find_source_table(con, label=""):
    """Find the per match player table: needs Nom, Season and Minutes. Prefers player_stats."""
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    found = {}
    for t in tables:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
        if {"Nom", "Season", "Minutes"} <= set(cols) and ("PIR" in cols or "Score" in cols):
            found[t] = cols
    if not found:
        listing = ", ".join(tables) if tables else "no tables"
        raise RuntimeError(f"No per match player table found in {label}. Tables available: {listing}")
    for pref in ("guess_player_stats", "player_stats", "player_stats_advanced"):
        if pref in found:
            return pref, found[pref]
    t = sorted(found)[0]
    return t, found[t]


def _display_name(nom):
    """'SURNAME, Firstname' becomes 'Firstname SURNAME' (official casing kept)."""
    s = str(nom).strip()
    if "," in s:
        last, first = s.split(",", 1)
        return f"{first.strip()} {last.strip()}".strip()
    return s


@st.cache_data(ttl=3600, show_spinner=False)
def _load_players(today_iso):
    """One row per player for the latest season in the public database."""
    today = date.fromisoformat(today_iso)
    db_path = _find_db()
    con = sqlite3.connect(db_path)
    try:
        table, cols = _find_source_table(con)
        pir_col = "PIR" if "PIR" in cols else "Score"
        season = con.execute(f'SELECT MAX(Season) FROM "{table}"').fetchone()[0]
        wanted = ["Nom", "Team", "Position", "Country", "Height", "BirthDate", "Minutes", pir_col]
        sel = [c for c in wanted if c in cols]
        df = pd.read_sql_query(
            f'SELECT {", ".join(chr(34) + c + chr(34) for c in sel)} FROM "{table}" WHERE Season = ?',
            con, params=(season,)
        )
        if pir_col != "PIR":
            df = df.rename(columns={pir_col: "PIR"})
        mapping = {}
        try:
            m = pd.read_sql_query("SELECT raw_name, canonical_name FROM team_mapping", con)
            mapping = dict(zip(m["raw_name"], m["canonical_name"]))
        except Exception:
            pass
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame(), season

    df["min_f"] = df["Minutes"].apply(_parse_minutes) if "Minutes" in df else 0.0
    df["PIR"] = pd.to_numeric(df["PIR"], errors="coerce") if "PIR" in df else 0.0
    df = df[df["min_f"] > 0]

    rows = []
    for nom, g in df.groupby("Nom"):
        team = _mode(g["Team"]) if "Team" in g else None
        rows.append({
            "name": nom,
            "label": _display_name(nom),
            "team": mapping.get(team, team),
            "pos": _mode(g["Position"]) if "Position" in g else None,
            "country": _mode(g["Country"]) if "Country" in g else None,
            "height": _parse_height_cm(_mode(g["Height"])) if "Height" in g else None,
            "age": _age(_mode(g["BirthDate"]), today) if "BirthDate" in g else None,
            "games": int(len(g)),
            "mpg": round(float(g["min_f"].mean()), 1),
            "pir": round(float(g["PIR"].mean()), 1) if g["PIR"].notna().any() else None,
        })
    out = pd.DataFrame(rows).sort_values("label").reset_index(drop=True)
    return out, season


def _season_label(season):
    try:
        s = int(season)
        return f"{s}-{str(s + 1)[-2:]}"
    except Exception:
        return str(season)


def _pick_answer(players, puzzle_date):
    """Fallback pick (same rule as guess_logic.pick_name) when a day has no frozen answer."""
    names = _eligible_from_summary(zip(players["name"], players["games"], players["mpg"]))
    if not names:
        names = players["name"].tolist()
    best = _pick_name(names, puzzle_date)
    return players[players["name"] == best].iloc[0]


@st.cache_data(ttl=3600, show_spinner=False)
def _load_frozen(day_iso):
    """The answer frozen for this day by build_public_db.py (table guess_answers), or None."""
    try:
        con = sqlite3.connect(_find_db())
        try:
            row = con.execute("SELECT name FROM guess_answers WHERE day = ?", (day_iso,)).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Comparison
# ----------------------------------------------------------------------------
def _compare(guess, answer):
    """Returns {attr: (status, arrow)} with status in ok, close, no, na."""
    res = {}
    for key, _, kind in ATTRS:
        g, a = guess.get(key), answer.get(key)
        if g is None or a is None or (isinstance(g, float) and pd.isna(g)) or (isinstance(a, float) and pd.isna(a)):
            res[key] = ("na", "")
            continue
        if kind == "exact":
            res[key] = ("ok" if str(g) == str(a) else "no", "")
        else:
            d = round(float(a) - float(g), 1)
            if abs(d) < 1e-9:
                res[key] = ("ok", "")
            else:
                arrow = "▲" if d > 0 else "▼"   # arrow points to where the answer is
                res[key] = ("close" if abs(d) <= CLOSE[key] else "no", arrow)
    return res


def _fmt(key, v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    if key in DECIMALS:
        return f"{float(v):.{DECIMALS[key]}f}"
    if key == "country" and str(v) == "United States of America":
        return "USA"
    return str(v)


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
_CSS = f"""
<style>
.gtp-wrap {{ width: 100%; }}
.gtp-grid {{ display: grid; grid-template-columns: minmax(130px, 1.6fr) minmax(92px, 1.4fr) repeat({len(ATTRS) - 1}, minmax(62px, 1fr));
            gap: 4px; margin-bottom: 4px; }}
.gtp-h {{ font-size: 0.8rem; letter-spacing: 0.06em; opacity: 0.7; text-align: center; padding-bottom: 2px; }}
.gtp-name {{ font-weight: 700; display: flex; align-items: center; padding: 0 4px; line-height: 1.1; }}
.gtp-c {{ color: {C_TEXT}; text-align: center; border-radius: 6px; padding: 8px 2px; font-weight: 600;
          font-size: 0.95rem; line-height: 1.15; display: flex; align-items: center; justify-content: center;
          flex-direction: column; min-width: 0; overflow-wrap: anywhere; }}
.gtp-l {{ display: none; }}
.gtp-ok {{ background: {C_OK}; }}
.gtp-close {{ background: {C_CLOSE}; }}
.gtp-no {{ background: {C_NO}; }}
.gtp-na {{ background: #8a8f98; }}

/* Phones: one card per guess, name on top, values wrapped on two rows */
@media (max-width: 700px) {{
  .gtp-head {{ display: none; }}
  .gtp-grid.gtp-row {{ grid-template-columns: repeat(4, 1fr); gap: 4px; margin: 0 0 14px 0;
                      padding-top: 10px; border-top: 1px solid rgba(128,128,128,0.35); }}
  .gtp-name {{ grid-column: 1 / -1; font-size: 1.05rem; padding: 0 2px 2px 2px; }}
  .gtp-c.gtp-team {{ grid-column: span 2; }}
  .gtp-c {{ padding: 6px 2px; font-size: 0.9rem; min-height: 48px; }}
  .gtp-l {{ display: block; font-size: 0.62rem; letter-spacing: 0.06em; opacity: 0.8; font-weight: 500; margin-bottom: 2px; }}
}}
</style>
"""


def _grid_html(guesses, answer):
    head = '<div class="gtp-grid gtp-head"><div class="gtp-h"></div>' + "".join(
        f'<div class="gtp-h">{html.escape(h)}</div>' for _, h, _ in ATTRS
    ) + "</div>"
    rows = []
    for g in reversed(guesses):
        cmp_ = _compare(g, answer)
        cells = [f'<div class="gtp-name">{html.escape(str(g["label"]))}</div>']
        for key, header, _ in ATTRS:
            status, arrow = cmp_[key]
            txt = html.escape(_fmt(key, g.get(key)))
            if arrow:
                txt += f" {arrow}"
            cells.append(
                f'<div class="gtp-c gtp-{status} gtp-{key}">'
                f'<span class="gtp-l">{html.escape(header)}</span>{txt}</div>'
            )
        rows.append('<div class="gtp-grid gtp-row">' + "".join(cells) + "</div>")
    return _CSS + '<div class="gtp-wrap">' + head + "".join(rows) + "</div>"


def _share_text(day, guesses, answer, won):
    score = f"{len(guesses)}/{MAX_GUESSES}" if won else f"X/{MAX_GUESSES}"
    sym = {"ok": "🟩", "close": "🟧", "no": "🟦", "na": "⬜"}
    lines = [f"ELSTATSLAB Guess the Player {_date_label(day)} {score}", ""]
    for g in guesses:
        c = _compare(g, answer)
        lines.append("".join(sym[c[k][0]] for k, _, _ in ATTRS))
    lines += ["", "Play it on ELSTATSLAB, by @EL_Statslab #EuroLeague"]
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def render_guess_game():
    today = _today()
    number = today.isoformat()   # used only to key the session state

    st.subheader(f"Guess the Player: {_date_label(today)}")
    try:
        players, season = _load_players(today.isoformat())
    except Exception as e:
        st.error(f"Could not load player data. {e}")
        return
    if players is None or players.empty:
        st.info("The game opens as soon as the first games of the season are in the database.")
        return

    answer = None
    frozen = _load_frozen(today.isoformat())
    if frozen:
        match = players[players["name"] == frozen]
        if not match.empty:
            answer = match.iloc[0]
    if answer is None:
        answer = _pick_answer(players, today)

    with st.expander("How to play", expanded=False):
        st.markdown(
            f"""
A mystery EuroLeague player is picked every day, the same one for everybody.
You have **{MAX_GUESSES} guesses**. After each guess you see how that player compares with the mystery one.

* 🟩 **Green**: same value
* 🟧 **Orange**: close (age within {CLOSE_AGE} years, height within {CLOSE_HEIGHT} cm, minutes within {CLOSE_MIN:g}, PIR within {CLOSE_PIR:g})
* 🟦 **Dark blue**: not a match
* ▲ the mystery value is higher, ▼ it is lower

NAT is the nationality listed on the official EuroLeague player profile, which can differ from the national team a player represents.

MIN and PIR are per game averages for the {_season_label(season)} season. A new player every day at midnight, Paris time.
"""
        )

    # Session state, one entry per puzzle
    skey = f"gtp_guesses_{number}"
    if skey not in st.session_state:
        st.session_state[skey] = []
    # Refresh the stored guesses with the latest stats (the database may have been updated mid session)
    latest = {r["name"]: r for r in players.to_dict("records")}
    st.session_state[skey] = [latest.get(g["name"], g) for g in st.session_state[skey]]
    guesses = st.session_state[skey]

    won = any(g["name"] == answer["name"] for g in guesses)
    lost = (not won) and len(guesses) >= MAX_GUESSES
    over = won or lost

    st.caption(f"Season averages {_season_label(season)}. Guesses used: {len(guesses)}/{MAX_GUESSES}")

    if not over:
        used = {g["name"] for g in guesses}
        options = [r for r in players.to_dict("records") if r["name"] not in used]
        teams = sorted({str(r["team"]) for r in options if r["team"]})

        c0, c1, c2 = st.columns([2, 3, 1])
        team_choice = c0.selectbox(
            "Team",
            teams,
            index=None,
            placeholder="Team (optional)",
            key=f"gtp_team_{number}_{len(guesses)}",
            label_visibility="collapsed",
        )
        pool = [r for r in options if team_choice is None or str(r["team"]) == team_choice]

        by_label = {}
        for r in pool:
            lab = r["label"]
            if lab in by_label:  # same display name twice, keep both
                lab = f'{lab} ({r["team"]})'
            by_label[lab] = r
        labels = list(by_label.keys())

        choice = c1.selectbox(
            "Your guess",
            labels,
            index=None,
            placeholder="Pick a player" if team_choice else "Type a player name",
            key=f"gtp_sel_{number}_{len(guesses)}_{team_choice}",
            label_visibility="collapsed",
        )
        if c2.button("Guess", use_container_width=True, disabled=choice is None, key=f"gtp_btn_{number}_{len(guesses)}"):
            guesses.append(by_label[choice])
            st.rerun()

    if guesses:
        st.markdown(_grid_html(guesses, answer), unsafe_allow_html=True)

    if over:
        if won:
            st.success(f"Found it in {len(guesses)}/{MAX_GUESSES}. The mystery player was {answer['label']}.")
        else:
            st.error(f"Out of guesses. The mystery player was {answer['label']} ({answer['team']}).")
        st.markdown("**Share your result on X**")
        st.code(_share_text(today, guesses, answer, won), language=None)
        st.caption("Next player at midnight, Paris time.")
