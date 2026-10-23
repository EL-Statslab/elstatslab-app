"""
ELSTATSLAB | Guess the Player, shared pure logic (no Streamlit, no pandas).

Used by:
  * build_public_db.py : freezes the mystery player of each day in the
    guess_answers table, so a later import can never change it.
  * guess_the_player.py : reads the frozen answer, and falls back to the
    same rule if a day has no frozen answer yet.

Both files must sit in the same folder (ELSTATSLAB_APP).
"""

import hashlib
from datetime import date, datetime

MIN_GAMES_FOR_ANSWER = 5      # mystery player must have played at least this many games
MIN_MPG_FOR_ANSWER = 15.0     # and average at least this many minutes


def today_paris():
    """Today's date in Paris time (the puzzle changes at local midnight)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return datetime.now().date()


def parse_minutes(v):
    """Handles 'MM:SS' strings and plain numbers. Returns minutes as float."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return 0.0 if v != v else float(v)   # v != v is True for NaN
    s = str(v).strip()
    if not s or s.upper() in ("DNP", "NAN", "NONE"):
        return 0.0
    if ":" in s:
        try:
            parts = s.split(":")
            return int(parts[0]) + int(parts[1]) / 60.0
        except Exception:
            return 0.0
    try:
        return float(s.replace(",", "."))
    except Exception:
        return 0.0


def eligible_names(rows):
    """rows: iterable of (name, minutes). Returns the sorted names that can be a mystery player."""
    games, total = {}, {}
    for name, minutes in rows:
        m = parse_minutes(minutes)
        if m <= 0 or name is None:
            continue
        games[name] = games.get(name, 0) + 1
        total[name] = total.get(name, 0.0) + m
    return sorted(
        n for n in games
        if games[n] >= MIN_GAMES_FOR_ANSWER and total[n] / games[n] >= MIN_MPG_FOR_ANSWER
    )


def pick_name(names, day):
    """Stable daily pick: the name with the lowest hash of date + name."""
    key = lambda n: hashlib.sha256(f"elstatslab-gtp|{day.isoformat()}|{n}".encode()).hexdigest()
    return min(names, key=key)
