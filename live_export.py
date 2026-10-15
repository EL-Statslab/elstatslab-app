"""
live_export.py  (ELSTATSLAB)

Builds a clean PNG (1080 x 1350, the 4:5 format of X) of the "View live" panel,
so the live figures can be shared at half time instead of a phone screenshot.

Same content as the panel: score and clock, Ratings, Four Factors (each with the
season average), leading scorer and foul trouble per team.

Design rules (same as the panel): no team logos, English only, no play by play.
Pure Pillow (no matplotlib), so the memory cost is tiny.
Fonts: Barlow Condensed from the "fonts" folder (same as the other exports),
with an automatic fallback to a system font.
"""

import io
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
LOCAL_TZ = ZoneInfo("Europe/Paris")

CREAM = (243, 238, 228)
NAVY = (20, 40, 75)
ORANGE = (232, 85, 42)
RED = (229, 57, 53)
GREEN = (46, 160, 67)
WHITE = (255, 255, 255)
INK = (26, 26, 26)
GREY = (120, 120, 120)
LINE = (232, 226, 214)

FONTS_DIR = Path("fonts")
_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_cache = {}


def _font(kind: str, size: int):
    key = (kind, size)
    if key in _cache:
        return _cache[key]
    names = {
        "bold": "BarlowCondensed-Bold.ttf",
        "semi": "BarlowCondensed-SemiBold.ttf",
        "reg": "BarlowCondensed-Regular.ttf",
    }
    font = None
    for path in [FONTS_DIR / names[kind]] + [Path(p) for p in _FALLBACKS]:
        try:
            if path.exists():
                font = ImageFont.truetype(str(path), size)
                break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _cache[key] = font
    return font


def _fmt(v, suffix=""):
    return "n/a" if v is None else f"{v:.1f}{suffix}"


def _pretty_name(raw: str) -> str:
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


def _fit(draw, text, kind, size, max_width, min_size=22):
    """Largest font (down to min_size) in which the text fits max_width."""
    while size > min_size:
        f = _font(kind, size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return _font(kind, min_size)


def _text(draw, xy, text, font, fill, anchor="ls"):
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _section_title(draw, x, y, text):
    _text(draw, (x, y), text.upper(), _font("bold", 30), ORANGE, "ls")


def _compare_block(draw, y, title, rows, snap, ref_h, ref_a):
    """One white table card with big values. Returns the bottom y of the card."""
    h, a = snap["teams"]
    _section_title(draw, 52, y + 24, title)
    top = y + 40
    row_h = 92
    bottom = top + row_h * len(rows)
    draw.rounded_rectangle((40, top, W - 40, bottom), radius=24, fill=WHITE)
    vf, sf = _font("bold", 54), _font("reg", 23)
    for i, (label, key, suffix) in enumerate(rows):
        ry = top + i * row_h
        if i > 0:
            draw.line((64, ry, W - 64, ry), fill=LINE, width=2)
        hv, av = h["metrics"].get(key), a["metrics"].get(key)
        hs = ref_h.get(key) if ref_h else None
        as_ = ref_a.get(key) if ref_a else None
        _text(draw, (68, ry + 56), _fmt(hv, suffix), vf, NAVY, "ls")
        _text(draw, (68, ry + 84), f"Season {_fmt(hs, suffix)}", sf, GREY, "ls")
        _text(draw, (W - 68, ry + 56), _fmt(av, suffix), vf, NAVY, "rs")
        _text(draw, (W - 68, ry + 84), f"Season {_fmt(as_, suffix)}", sf, GREY, "rs")
        _text(draw, (W // 2, ry + 50), label, _font("semi", 32), (70, 80, 100), "mm")
    return bottom


def _scorer(draw, x0, x1, align, team, top):
    """Leading scorer of one team: name and points on one line."""
    t = team.get("top_scorer")
    _text(draw, (x0 if align == "left" else x1, top), "TOP SCORER", _font("semi", 20), ORANGE,
          "ls" if align == "left" else "rs")
    if not t:
        _text(draw, (x0 if align == "left" else x1, top + 40), "No points yet", _font("reg", 28), GREY,
              "ls" if align == "left" else "rs")
        return
    pretty = _pretty_name(t["name"])
    pts = f"{t['points']} pts"
    pf = _font("bold", 34)
    pw = draw.textlength(pts, font=pf)
    nf = _fit(draw, pretty, "bold", 34, (x1 - x0) - pw - 16, 20)
    if align == "left":
        _text(draw, (x0, top + 40), pretty, nf, INK, "ls")
        _text(draw, (x0 + draw.textlength(pretty, font=nf) + 16, top + 40), pts, pf, ORANGE, "ls")
    else:
        _text(draw, (x1, top + 40), pts, pf, ORANGE, "rs")
        _text(draw, (x1 - pw - 16, top + 40), pretty, nf, INK, "rs")


def build_live_png(snap: dict, home_disp: str, away_disp: str,
                   ref_h: dict | None, ref_a: dict | None,
                   hcode: str, acode: str) -> bytes:
    """snap: a live snapshot (status live or final). Returns PNG bytes."""
    teams = snap["teams"]
    if teams[0]["code"] == acode and teams[1]["code"] == hcode:
        snap = dict(snap)
        snap["teams"] = [teams[1], teams[0]]
        teams = snap["teams"]
    h, a = teams

    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)

    # Header
    _text(d, (48, 78), "ELSTATSLAB", _font("bold", 52), NAVY, "ls")
    _text(d, (W - 48, 78), "LIVE VIEW", _font("bold", 40), ORANGE, "rs")

    # Scoreboard
    d.rounded_rectangle((40, 112, W - 40, 362), radius=28, fill=NAVY)
    live = snap.get("status") == "live"
    badge, bcol = ("LIVE", RED) if live else ("FINAL", GREEN)
    clock = _clock_label(snap) if live else ""
    bfont, cfont = _font("bold", 30), _font("bold", 34)
    badge_w = d.textlength(badge, font=bfont) + 36
    clock_w = d.textlength(clock, font=cfont) + 22 if clock else 0
    total = badge_w + clock_w
    bx = W / 2 - total / 2
    d.rounded_rectangle((bx, 130, bx + badge_w, 176), radius=8, fill=bcol)
    _text(d, (bx + badge_w / 2, 153), badge, bfont, WHITE, "mm")
    if clock:
        _text(d, (bx + badge_w + 22, 153), clock, cfont, WHITE, "lm")

    half = W / 2 - 110
    _text(d, (80, 214), home_disp, _fit(d, home_disp, "semi", 40, half, 24), WHITE, "ls")
    _text(d, (W - 80, 214), away_disp, _fit(d, away_disp, "semi", 40, half, 24), WHITE, "rs")
    _text(d, (80, 338), str(h["score"]), _font("bold", 130), WHITE, "ls")
    _text(d, (W - 80, 338), str(a["score"]), _font("bold", 130), WHITE, "rs")
    _text(d, (W / 2, 302), ":", _font("bold", 80), (140, 155, 185), "mm")

    # Ratings and Four Factors
    if snap.get("ratings_ready"):
        y = _compare_block(d, 384, "Ratings tonight",
                           [("OFF RTG", "off_rtg", ""), ("DEF RTG", "def_rtg", ""), ("PACE", "pace", "")],
                           snap, ref_h, ref_a)
        y = _compare_block(d, y + 12, "Four Factors",
                           [("eFG%", "efg", "%"), ("TOV%", "tov", "%"),
                            ("OREB%", "oreb", "%"), ("FT RATE", "ft_rate", "%")],
                           snap, ref_h, ref_a)
        card_top = y + 22
    else:
        _section_title(d, 52, 408, "Ratings and Four Factors")
        d.rounded_rectangle((40, 424, W - 40, 1000), radius=24, fill=WHITE)
        _text(d, (W / 2, 690), "Unlocked in Q2", _font("bold", 60), NAVY, "mm")
        _text(d, (W / 2, 760), "Too few possessions before then to be reliable.",
              _font("reg", 30), GREY, "mm")
        card_top = 1022

    # Leading scorers
    d.rounded_rectangle((40, card_top, W - 40, card_top + 112), radius=24, fill=WHITE)
    _scorer(d, 68, W // 2 - 24, "left", h, card_top + 38)
    _scorer(d, W // 2 + 24, W - 68, "right", a, card_top + 38)

    # Footer
    stamp = datetime.fromtimestamp(snap.get("updated", 0), LOCAL_TZ).strftime("%H:%M")
    _text(d, (56, 1326), f"Live figures, captured at {stamp} Paris time.", _font("reg", 22), GREY, "ls")
    _text(d, (W - 56, 1326), "elstatslab.com", _font("bold", 26), ORANGE, "rs")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
