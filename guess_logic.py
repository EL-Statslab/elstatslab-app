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


MIN_POOL_SIZE = 40            # early in the season, relax the games rule until the pool is this big


def eligible_from_summary(items):
    """items: iterable of (name, games, mpg). Returns the sorted eligible names.

    Normal rule: at least MIN_GAMES_FOR_ANSWER games and MIN_MPG_FOR_ANSWER mpg.
    Early in the season very few players reach 5 games, so the games rule is
    lowered step by step (never below 1) until the pool has MIN_POOL_SIZE names.
    The minutes rule always stays, so the mystery player is always a regular.
    """
    items = [(n, g, m) for n, g, m in items if n is not None]
    for min_games in range(MIN_GAMES_FOR_ANSWER, 0, -1):
        pool = sorted(n for n, g, m in items if g >= min_games and m >= MIN_MPG_FOR_ANSWER)
        if len(pool) >= MIN_POOL_SIZE:
            return pool
    return sorted(n for n, g, m in items if g >= 1 and m >= MIN_MPG_FOR_ANSWER)


def eligible_names(rows):
    """rows: iterable of (name, minutes). Returns the sorted names that can be a mystery player."""
    games, total = {}, {}
    for name, minutes in rows:
        m = parse_minutes(minutes)
        if m <= 0 or name is None:
            continue
        games[name] = games.get(name, 0) + 1
        total[name] = total.get(name, 0.0) + m
    return eligible_from_summary((n, games[n], total[n] / games[n]) for n in games)


def pick_name(names, day, exclude=()):
    """Stable daily pick: the name with the lowest hash of date + name.

    Names in `exclude` (already used on other days) are skipped, unless that
    would leave nobody.
    """
    excluded = set(exclude)
    pool = [n for n in names if n not in excluded] or list(names)
    key = lambda n: hashlib.sha256(f"elstatslab-gtp|{day.isoformat()}|{n}".encode()).hexdigest()
    return min(pool, key=key)
