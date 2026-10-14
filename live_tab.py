"""
live_tab.py  (ELSTATSLAB)

The "View live" panel of the Match Center.

What it shows (and what the EuroLeague site does not):
  score next to each team name, OffRtg / DefRtg / Pace and the Four Factors with the
  season average of each team, the leading scorer of each team, and foul trouble.

Design rules:
  no team logos here, English only, no play by play.
  Ratings and Four Factors stay hidden until the second quarter.
  One request per 20 seconds per match for ALL visitors (shared cache).
  If the feed fails, only this panel says so. The rest of the site is untouched.

The season averages use the same formulas as the Match Center tables
(totals divided by totals, possessions from team_stats).
"""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import live_feed

REFRESH_SECONDS = 20
LIVE_SEASON = 2026          # the live feed (E2026) only exists for this season
LOCAL_TZ = ZoneInfo("Europe/Paris")

NAVY = "#14284B"
ORANGE = "#E8552A"
LIVE_RED = "#E53935"


# ----------------------------------------------------------------------------
# Cached data
# ----------------------------------------------------------------------------

@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def cached_snapshot(gamecode: int) -> dict:
    """Shared by every visitor: one pair of requests per refresh period."""
    return live_feed.get_snapshot(int(gamecode))


@st.cache_data(ttl=600, show_spinner=False)
def cached_game_info(gamecode: int) -> dict:
    """Venue, capacity and referees. Changes rarely, so a long cache."""
    return live_feed.get_game_info(int(gamecode))


@st.cache_data(ttl=600, show_spinner=False)
def season_reference(_conn, season: int) -> dict:
    """Season averages per team code, same formulas as the Match Center."""
    q = """
        SELECT
            ts.GameCode AS gamecode_num,
            CASE WHEN UPPER(s.hometeam) = UPPER(ts.TeamName)
                 THEN s.homecode ELSE s.awaycode END AS code,
            ts.Score        AS pts,
            opp.Score       AS opp_pts,
            ts.Possessions  AS poss,
            ts.Turnovers    AS tov,
            ts.Reb_Off      AS oreb,
            opp.Reb_Def     AS opp_dreb,
            ts."2PM" AS m2, ts."3PM" AS m3, ts."2PA" AS a2, ts."3PA" AS a3,
            ts.FTA AS fta
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
    try:
        df = pd.read_sql(q, _conn, params=(int(season),))
    except Exception:
        return {}
    if df.empty:
        return {}
    df = df.drop_duplicates(subset=["gamecode_num", "code"])

    out = {}
    for code, d in df.groupby("code"):
        poss = d["poss"].sum()
        fga = (d["a2"] + d["a3"]).sum()

        def pct(num, den):
            return round(100.0 * num / den, 1) if den else None

        out[code] = {
            "games": int(len(d)),
            "off_rtg": pct(d["pts"].sum(), poss),
            "def_rtg": pct(d["opp_pts"].sum(), poss),
            "pace": round(float(d["poss"].mean()), 1),
            "efg": pct((d["m2"] + 1.5 * d["m3"]).sum(), fga),
            "tov": pct(d["tov"].sum(), poss),
            "oreb": pct(d["oreb"].sum(), d["oreb"].sum() + d["opp_dreb"].sum()),
            "ft_rate": pct(d["fta"].sum(), fga),
        }
    return out


# ----------------------------------------------------------------------------
# Small formatting helpers
# ----------------------------------------------------------------------------

def _fmt(v, suffix=""):
    return "n/a" if v is None else f"{v:.1f}{suffix}"


def _pretty_name(raw: str) -> str:
    """'SHIELDS, SHAVON' -> 'Shavon Shields'."""
    raw = (raw or "").strip()
    if "," in raw:
        last, first = [x.strip() for x in raw.split(",", 1)]
        raw = f"{first} {last}"
    return raw.title()


def _clock_label(snap: dict) -> str:
    q = snap.get("quarter") or 0
    if q <= 0:
        return ""
    qname = f"Q{q}" if q <= 4 else f"OT{q - 4}"
    if snap.get("between_periods"):
        return f"End of {qname}"
    if snap.get("clock_known") is False:
        return qname
    return f"{qname} {snap.get('remaining', '')}".strip()


def _note(text: str) -> None:
    st.markdown(
        f"<div style='border:1px dashed rgba(128,128,128,0.5);border-radius:10px;"
        f"padding:14px 16px;text-align:center;font-size:0.9rem;opacity:0.85;'>"
        f"{text}</div>",
        unsafe_allow_html=True,
    )


def _section_title(text: str) -> None:
    st.markdown(
        f"<div style='font-weight:700;font-size:0.8rem;letter-spacing:1.2px;"
        f"text-transform:uppercase;margin:18px 0 8px 0;color:{ORANGE};'>{text}</div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# Blocks
# ----------------------------------------------------------------------------

def _scoreboard(snap, home_disp, away_disp):
    h, a = snap["teams"]
    status = snap["status"]
    if status == "live":
        badge = (f"<span style='background:{LIVE_RED};color:#fff;font-weight:700;"
                 f"font-size:0.7rem;letter-spacing:1px;padding:2px 8px;border-radius:4px;'>"
                 f"LIVE</span>")
        clock = _clock_label(snap)
    else:
        badge = ("<span style='background:#2ea043;color:#fff;font-weight:700;"
                 "font-size:0.7rem;letter-spacing:1px;padding:2px 8px;border-radius:4px;'>"
                 "FINAL</span>")
        clock = ""

    def side(name, score, align):
        return (
            f"<div style='flex:1;min-width:0;text-align:{align};'>"
            f"<div style='font-size:0.85rem;font-weight:600;opacity:0.9;line-height:1.2;'>"
            f"{escape(name)}</div>"
            f"<div style='font-size:2.6rem;font-weight:800;line-height:1.05;'>{score}</div>"
            f"</div>"
        )

    st.markdown(
        f"<div style='background:{NAVY};color:#fff;border-radius:12px;padding:16px 18px;'>"
        f"<div style='text-align:center;margin-bottom:8px;'>{badge}"
        f"<span style='margin-left:10px;font-size:0.9rem;font-weight:600;'>{escape(clock)}</span>"
        f"<span style='margin-left:10px;border:1px solid rgba(255,255,255,0.6);font-size:0.65rem;"
        f"font-weight:700;letter-spacing:1px;padding:1px 6px;border-radius:4px;'>BETA</span></div>"
        f"<div style='display:flex;align-items:center;gap:14px;'>"
        f"{side(home_disp, h['score'], 'left')}"
        f"<div style='font-size:1.2rem;opacity:0.6;'>:</div>"
        f"{side(away_disp, a['score'], 'right')}"
        f"</div></div>",
        unsafe_allow_html=True,
    )


def _compare_rows(title, rows, snap, ref_h, ref_a):
    """rows: list of (label, metric key, suffix)."""
    h, a = snap["teams"]
    _section_title(title)
    body = ""
    for label, key, suffix in rows:
        hv, av = h["metrics"].get(key), a["metrics"].get(key)
        hs = ref_h.get(key) if ref_h else None
        as_ = ref_a.get(key) if ref_a else None
        body += (
            "<tr>"
            f"<td style='text-align:left;padding:8px 4px;width:30%;'>"
            f"<div style='font-size:1.25rem;font-weight:700;'>{_fmt(hv, suffix)}</div>"
            f"<div style='font-size:0.72rem;opacity:0.65;'>Season {_fmt(hs, suffix)}</div></td>"
            f"<td style='text-align:center;font-weight:600;font-size:0.85rem;"
            f"letter-spacing:0.5px;opacity:0.8;'>{label}</td>"
            f"<td style='text-align:right;padding:8px 4px;width:30%;'>"
            f"<div style='font-size:1.25rem;font-weight:700;'>{_fmt(av, suffix)}</div>"
            f"<div style='font-size:0.72rem;opacity:0.65;'>Season {_fmt(as_, suffix)}</div></td>"
            "</tr>"
        )
    st.markdown(
        "<table style='width:100%;border-collapse:collapse;'>" + body + "</table>",
        unsafe_allow_html=True,
    )


def _two_columns(title, left_html, right_html, home_disp, away_disp):
    _section_title(title)
    st.markdown(
        "<div style='display:flex;gap:16px;'>"
        f"<div style='flex:1;min-width:0;'>"
        f"<div style='font-size:0.75rem;opacity:0.65;margin-bottom:2px;'>{escape(home_disp)}</div>"
        f"{left_html}</div>"
        f"<div style='flex:1;min-width:0;text-align:right;'>"
        f"<div style='font-size:0.75rem;opacity:0.65;margin-bottom:2px;'>{escape(away_disp)}</div>"
        f"{right_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _scorer_html(team):
    t = team.get("top_scorer")
    if not t:
        return "<div style='opacity:0.6;'>No points yet</div>"
    return (f"<div style='font-weight:700;'>{escape(_pretty_name(t['name']))}</div>"
            f"<div style='font-size:1.3rem;font-weight:800;color:{ORANGE};'>{t['points']} pts</div>")


def _fouls_html(team):
    rows = team.get("foul_trouble") or []
    if not rows:
        return "<div style='opacity:0.6;'>No player with 3 or more fouls</div>"
    return "".join(
        f"<div><span style='font-weight:600;'>{escape(_pretty_name(r['name']))}</span> "
        f"<span style='font-weight:800;color:{LIVE_RED};'>{r['fouls']} fouls</span></div>"
        for r in rows
    )


# ----------------------------------------------------------------------------
# Panel
# ----------------------------------------------------------------------------

def _pregame_info(info: dict) -> None:
    """Venue, capacity and referees, only what the official sheet really contains."""
    if not info:
        return
    rows = ""
    if info.get("venue"):
        rows += (f"<div><span style='opacity:0.65;'>Venue</span> "
                 f"<span style='font-weight:700;'>{escape(info['venue'])}</span></div>")
    if info.get("capacity"):
        rows += (f"<div><span style='opacity:0.65;'>Capacity</span> "
                 f"<span style='font-weight:700;'>{int(info['capacity']):,}</span></div>")
    refs = info.get("referees") or []
    if refs:
        names = "".join(
            f"<div style='font-weight:700;'>{escape(r['name'])}"
            f"<span style='font-weight:400;opacity:0.65;'>"
            f"{' (' + escape(r['country']) + ')' if r.get('country') else ''}</span></div>"
            for r in refs
        )
        rows += f"<div style='margin-top:6px;'><span style='opacity:0.65;'>Referees</span>{names}</div>"
    else:
        rows += "<div style='margin-top:6px;opacity:0.65;'>Referees not announced yet</div>"
    _section_title("Game information")
    st.markdown(f"<div style='font-size:0.95rem;line-height:1.5;'>{rows}</div>",
                unsafe_allow_html=True)


def _panel(conn, gamecode, hcode, acode, home_disp, away_disp, start_label):
    snap = cached_snapshot(int(gamecode))
    status = snap.get("status")

    if status == "error":
        _note("The live feed is temporarily unavailable. It will retry automatically.")
        return

    if status == "pre":
        when = f" Tip off: {escape(start_label)}." if start_label else ""
        _note(f"The game has not started yet.{when} This view fills in as soon as the game begins.")
        _pregame_info(cached_game_info(int(gamecode)))
        return

    # Make sure the home side of the feed is the home side of the schedule
    teams = snap["teams"]
    if teams[0]["code"] == acode and teams[1]["code"] == hcode:
        snap = dict(snap)
        snap["teams"] = [teams[1], teams[0]]

    _scoreboard(snap, home_disp, away_disp)

    ref = season_reference(conn, LIVE_SEASON)
    ref_h, ref_a = ref.get(hcode, {}), ref.get(acode, {})

    if snap.get("ratings_ready"):
        _compare_rows("Ratings tonight",
                      [("OFF RTG", "off_rtg", ""), ("DEF RTG", "def_rtg", ""), ("PACE", "pace", "")],
                      snap, ref_h, ref_a)
        _compare_rows("Four Factors",
                      [("eFG%", "efg", "%"), ("TOV%", "tov", "%"),
                       ("OREB%", "oreb", "%"), ("FT RATE", "ft_rate", "%")],
                      snap, ref_h, ref_a)
    else:
        _section_title("Ratings and Four Factors")
        _note("<b>Unlocked in Q2.</b><br>Too few possessions before then to be reliable.")

    h, a = snap["teams"]
    _two_columns("Leading scorers", _scorer_html(h), _scorer_html(a), home_disp, away_disp)
    _two_columns("Foul trouble", _fouls_html(h), _fouls_html(a), home_disp, away_disp)

    stamp = datetime.fromtimestamp(snap.get("updated", 0), LOCAL_TZ).strftime("%H:%M:%S")
    if status == "final":
        foot = "Final score from the live feed. Full analysis is added after the official update."
    else:
        foot = f"Refreshes every {REFRESH_SECONDS} seconds. Last update {stamp}."
    st.markdown(
        f"<div style='font-size:0.72rem;opacity:0.6;margin-top:14px;'>{foot} "
        f"Live figures are computed from the official live feed and can differ slightly from "
        f"the final stats. Season values are ELSTATSLAB averages over games played.</div>",
        unsafe_allow_html=True,
    )


# The panel refreshes on its own, and only the panel (not the whole page)
if hasattr(st, "fragment"):
    _panel_live = st.fragment(run_every=REFRESH_SECONDS)(_panel)
else:  # very old Streamlit: static panel, refreshed when the visitor interacts
    _panel_live = _panel


def render_live_panel(conn, gamecode, hcode, acode, home_disp, away_disp, start_label=""):
    """Entry point called by the Match Center. Never raises."""
    try:
        _panel_live(conn, int(gamecode), hcode, acode, home_disp, away_disp, start_label)
    except Exception:
        _note("The live view is temporarily unavailable.")
