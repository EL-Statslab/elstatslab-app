"""
live_feed.py  (ELSTATSLAB)

Data layer of the Live view. It reads the EuroLeague live feed, and turns it into
one small dictionary ("snapshot") that the app can display.

Rules of this module:
  no Streamlit, no database access, no file writing, no git.
  Only two light requests per refresh (Header and Boxscore).

Quick test from a terminal, during a live game:
    python live_feed.py 46
Offline test from saved samples:
    python live_feed.py --files legacy_Header_46_poll3.json legacy_Boxscore_46_poll3.json
"""

import json
import sys
import time

import requests

BASE_URL = "https://live.euroleague.net/api"
SEASON_CODE = "E2026"
TIMEOUT = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
}

# Ratings and Four Factors are too noisy before the second quarter
RATINGS_FROM_QUARTER = 2
FOUL_TROUBLE_AT = 3


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clock_to_seconds(text):
    """'07:00' -> 420, '035:00' -> 2100, None or 'DNP' -> 0."""
    if not text or not isinstance(text, str) or ":" not in text:
        return 0
    try:
        minutes, seconds = text.strip().split(":")
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return 0


def _pct(numerator, denominator):
    return round(100.0 * numerator / denominator, 1) if denominator else None


def _elapsed_minutes(quarter, remaining_seconds):
    """Elapsed game time in minutes, from the quarter and the time left in it."""
    if quarter <= 0:
        return 0.0
    if quarter <= 4:
        before = (quarter - 1) * 600
        length = 600
    else:
        before = 2400 + (quarter - 5) * 300
        length = 300
    played_in_quarter = max(0, length - remaining_seconds)
    return (before + played_in_quarter) / 60.0


# ----------------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------------

def _get(endpoint, gamecode, season=SEASON_CODE):
    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        params={"gamecode": gamecode, "seasoncode": season},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    if not response.content:
        return None  # empty body: the game has not started
    return response.json()


def fetch_raw(gamecode, season=SEASON_CODE):
    """Return (header, boxscore). Either can be None before the game starts."""
    header = _get("Header", gamecode, season)
    boxscore = _get("Boxscore", gamecode, season)
    return header, boxscore


# ----------------------------------------------------------------------------
# Building the snapshot
# ----------------------------------------------------------------------------

def _team_totals(entry):
    totals = entry.get("totr") or {}
    fga2 = _num(totals.get("FieldGoalsAttempted2"))
    fga3 = _num(totals.get("FieldGoalsAttempted3"))
    fgm2 = _num(totals.get("FieldGoalsMade2"))
    fgm3 = _num(totals.get("FieldGoalsMade3"))
    return {
        "pts": _num(totals.get("Points")),
        "fgm": fgm2 + fgm3,
        "fga": fga2 + fga3,
        "fgm3": fgm3,
        "ftm": _num(totals.get("FreeThrowsMade")),
        "fta": _num(totals.get("FreeThrowsAttempted")),
        "orb": _num(totals.get("OffensiveRebounds")),
        "drb": _num(totals.get("DefensiveRebounds")),
        "to": _num(totals.get("Turnovers")),
        "fouls": _num(totals.get("FoulsCommited")),
    }


def _players(entry):
    rows = []
    for p in entry.get("PlayersStats") or []:
        seconds = _clock_to_seconds(p.get("Minutes"))
        if seconds == 0 and p.get("Minutes") in (None, "DNP", ""):
            continue  # did not play
        rows.append({
            "name": (p.get("Player") or "").strip(),
            "points": _int(p.get("Points")),
            "fouls": _int(p.get("FoulsCommited")),
            "seconds": seconds,
            "on_court": bool(p.get("IsPlaying")),
        })
    return rows


def _top_scorer(players):
    if not players:
        return None
    best = max(players, key=lambda p: (p["points"], p["seconds"]))
    if best["points"] <= 0:
        return None
    return {"name": best["name"], "points": best["points"]}


def _foul_trouble(players):
    flagged = [p for p in players if p["fouls"] >= FOUL_TROUBLE_AT]
    flagged.sort(key=lambda p: (-p["fouls"], p["name"]))
    return [{"name": p["name"], "fouls": p["fouls"]} for p in flagged]


def build_snapshot(header, boxscore, gamecode=None):
    """Turn the raw Header and Boxscore into the dictionary the app displays."""
    if not header or not boxscore:
        return {"status": "pre", "game_code": gamecode, "updated": time.time()}

    stats = boxscore.get("Stats") or []
    if len(stats) < 2:
        return {"status": "pre", "game_code": gamecode, "updated": time.time()}

    live = bool(header.get("Live")) or bool(boxscore.get("Live"))
    quarter = _int(header.get("Quarter"))
    remaining = _clock_to_seconds(header.get("RemainingPartialTime"))
    elapsed = _elapsed_minutes(quarter, remaining)

    a_tot, b_tot = _team_totals(stats[0]), _team_totals(stats[1])
    a_players, b_players = _players(stats[0]), _players(stats[1])

    # The Header score is fresher than its quarter by quarter fields
    score_a = _int(header.get("ScoreA"), int(a_tot["pts"]))
    score_b = _int(header.get("ScoreB"), int(b_tot["pts"]))

    if not live and (score_a + score_b) == 0:
        status = "pre"
    elif live:
        status = "live"
    else:
        status = "final"

    # Possessions: standard estimate per team, then the average of both teams
    def poss(t):
        return t["fga"] + 0.44 * t["fta"] + t["to"] - t["orb"]

    game_poss = (poss(a_tot) + poss(b_tot)) / 2.0

    def metrics(own, opp):
        return {
            "off_rtg": round(100.0 * own["pts"] / game_poss, 1) if game_poss else None,
            "def_rtg": round(100.0 * opp["pts"] / game_poss, 1) if game_poss else None,
            "pace": round(game_poss / elapsed * 40.0, 1) if elapsed > 0 and game_poss else None,
            "efg": _pct(own["fgm"] + 0.5 * own["fgm3"], own["fga"]),
            "tov": _pct(own["to"], game_poss),  # same as the app: turnovers per game possession
            "oreb": _pct(own["orb"], own["orb"] + opp["drb"]),
            "ft_rate": _pct(own["fta"], own["fga"]),
        }

    def team(side, code_key, name_key, tv_key, score, totals, opp_totals, players, fouls_key):
        return {
            "side": side,
            "code": (header.get(code_key) or "").strip(),
            "tv_code": (header.get(tv_key) or "").strip(),
            "name": (header.get(name_key) or "").strip(),
            "score": score,
            "fouls": _int(header.get(fouls_key), int(totals["fouls"])),
            "metrics": metrics(totals, opp_totals),
            "top_scorer": _top_scorer(players),
            "foul_trouble": _foul_trouble(players),
        }

    snapshot = {
        "status": status,
        "game_code": gamecode,
        "quarter": quarter,
        "remaining": (header.get("RemainingPartialTime") or "").strip(),
        "elapsed_min": round(elapsed, 1),
        "ratings_ready": quarter >= RATINGS_FROM_QUARTER or status == "final",
        "possessions": round(game_poss, 1),
        "updated": time.time(),
        "teams": [
            team("home", "CodeTeamA", "TeamA", "TVCodeA", score_a, a_tot, b_tot, a_players, "FoultsA"),
            team("away", "CodeTeamB", "TeamB", "TVCodeB", score_b, b_tot, a_tot, b_players, "FoultsB"),
        ],
    }
    return snapshot


def get_snapshot(gamecode, season=SEASON_CODE):
    """Fetch and build in one call. Never raises: errors come back as status 'error'."""
    try:
        header, boxscore = fetch_raw(gamecode, season)
        return build_snapshot(header, boxscore, gamecode)
    except Exception as exc:  # network, timeout, bad JSON
        return {"status": "error", "game_code": gamecode, "error": str(exc), "updated": time.time()}


# ----------------------------------------------------------------------------
# Command line test
# ----------------------------------------------------------------------------

def _print_snapshot(s):
    print(json.dumps(s, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--files" and len(args) == 3:
        with open(args[1], encoding="utf-8") as f:
            h = json.load(f)
        with open(args[2], encoding="utf-8") as f:
            b = json.load(f)
        _print_snapshot(build_snapshot(h, b))
    elif args:
        _print_snapshot(get_snapshot(int(args[0])))
    else:
        print(__doc__)
