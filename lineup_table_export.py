"""
ELSTATSLAB lineup_table_export.py
PNG export (4:5, 1800 x 2250 px) of the table shown in the Lineup tab, made for X.
Beige, navy and orange identity, Barlow Condensed. Layout: a "Together" frame with the
NetRtg of the selected players on court, then one card per unit (like the mobile list).

Needs lineup_export.py in the same folder (fonts, colours and sizes are shared with it).
Logos follow the validated method of team_rankings_export.py: prepared on a copy,
resized once with LANCZOS to their final pixel size, then pasted without resampling
(fig.figimage). Never imshow with extent.

Use inside the app:
    from lineup_table_export import build_lineup_table_png
"""

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

from lineup_export import BG, NAVY, ORANGE, GREY, CARD, RULE, POS, NEG, W, H, _fp, _fit

try:
    from team_rankings_export import brand_logo_image, prepare_team_logo
except Exception:                      # the PNG still works without logos
    brand_logo_image = None
    prepare_team_logo = None

PX_W, PX_H = 1800, 2250
S = PX_W / W                           # pixels per layout unit (1.6667)

MAX_ROWS_HERO = 7                      # with the Together frame
MAX_ROWS_PLAIN = 10                    # without it
ROWS_BOTTOM = 1196                     # rows stop here, the note sits under them
POS_BG = "#DDF0E1"
NEG_BG = "#F8DCDA"
LOW_BAR = "#C8BEA8"


def _txt(ax, x, y, s, px, weight="bold", color=NAVY, ha="left", va="center"):
    return ax.text(x, y, s, fontproperties=_fp(weight, px), color=color,
                   ha=ha, va=va, zorder=10)


def _place_logo(fig, im, cx, cy, box_w, box_h):
    """Resize once to the final pixel size, then paste without any resampling."""
    if im is None:
        return
    r = min(box_w / im.width, box_h / im.height)
    tw = max(1, round(im.width * r * S))
    th = max(1, round(im.height * r * S))
    small = im.convert("RGBA").resize((tw, th), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    xo = round(cx * S - tw / 2)
    yo = round(PX_H - (cy * S + th / 2))
    fig.figimage(arr, xo=xo, yo=yo, zorder=20, origin="upper")


def _open_brand(path):
    if not path or brand_logo_image is None:
        return None
    try:
        return brand_logo_image(path)
    except Exception:
        return None


def _open_team(path):
    if not path or prepare_team_logo is None:
        return None
    try:
        return prepare_team_logo(Image.open(path))
    except Exception:
        return None


def _names_line(fig, ax, x, y, names, sel, px, max_w):
    """Surnames separated by commas, the selected players in orange."""
    text = ", ".join(names)
    px = _fit(text, max_w, px, 18)
    rend = fig.canvas.get_renderer()
    inv = ax.transData.inverted()

    def width(t):
        bb = t.get_window_extent(rend)
        return abs(inv.transform((bb.x1, bb.y1))[0] - inv.transform((bb.x0, bb.y0))[0])

    cx = x
    for i, n in enumerate(names):
        picked = bool(sel[i]) if i < len(sel) else False
        t = _txt(ax, cx, y, n, px, "bold" if picked else "semibold",
                 ORANGE if picked else NAVY)
        cx += width(t)
        if i < len(names) - 1:
            t2 = _txt(ax, cx, y, ", ", px, "semibold", NAVY)
            cx += width(t2)


def _hero(ax, recap):
    y0, h = 384, 228
    card = FancyBboxPatch((64, y0), W - 128, h, boxstyle="round,pad=0,rounding_size=26",
                          fc=CARD, ec=RULE, lw=2, zorder=2)
    ax.add_patch(card)
    bar = Rectangle((64, y0), W - 128, 12, fc=ORANGE, ec="none", zorder=3)
    ax.add_patch(bar)
    bar.set_clip_path(card)
    _txt(ax, 96, y0 + 46, recap.get("label", "TOGETHER, NET RATING"), 26, "bold", GREY)
    net = float(recap["net"])
    _txt(ax, 96, y0 + 152, f"{net:+.1f}", 92, "bold", POS if net >= 0 else NEG, va="baseline")
    stats = (("MIN", f"{float(recap['minutes']):.1f}"),
             ("ORTG", f"{float(recap['ortg']):.1f}"),
             ("DRTG", f"{float(recap['drtg']):.1f}"))
    for i, (k, v) in enumerate(stats):
        x = 420 + i * 205
        _txt(ax, x, y0 + 100, k, 24, "bold", GREY)
        _txt(ax, x, y0 + 150, v, 48, "bold", NAVY)
    _txt(ax, 420, y0 + 198, recap.get("sub", ""), 24, "semibold", GREY)
    return y0 + h


def _rows(fig, ax, rows, top, show_gp):
    n = len(rows)
    if n == 0:
        return
    rh = min(150.0, (ROWS_BOTTOM - top) / n)
    ch = rh - 14                                    # card height
    big = rh >= 100
    names_px = max(21.0, min(30.0, 0.17 * rh + 3))
    stat_px = max(16.0, names_px - 6)
    net_px = max(30.0, min(44.0, 0.28 * rh))
    margin = 20 if big else 10
    bx = W - 64 - 170
    for i, r in enumerate(rows):
        y = top + i * rh
        cy = y + ch / 2
        low = bool(r.get("low"))
        net = float(r["net"])
        good = net >= 0
        ax.add_patch(FancyBboxPatch((64, y), W - 128, ch, boxstyle="round,pad=0,rounding_size=20",
                                    fc=CARD, ec=RULE, lw=2, zorder=2))
        ax.add_patch(Rectangle((64, y + 10), 8, ch - 20, fc=LOW_BAR if low else NAVY,
                               ec="none", zorder=3))
        off = 0.14 * rh
        _names_line(fig, ax, 94, cy - off, r["players"], r.get("sel", []), names_px,
                    bx - 94 - 14)
        parts = [f"{float(r['minutes']):.1f} MIN", f"ORTG {float(r['ortg']):.1f}",
                 f"DRTG {float(r['drtg']):.1f}"]
        if show_gp:
            parts.append(f"GP {int(r['gp'])}")
        if low:
            parts.append("LOW SAMPLE")
        stat = "  |  ".join(parts)
        _txt(ax, 94, cy + off, stat, _fit(stat, bx - 94 - 14, stat_px, 14), "semibold", GREY)
        ax.add_patch(FancyBboxPatch((bx + 20, y + margin), 130, ch - 2 * margin,
                                    boxstyle="round,pad=0,rounding_size=16",
                                    fc=POS_BG if good else NEG_BG, ec="none", zorder=3))
        if big:
            _txt(ax, bx + 85, cy - 4, f"{net:+.1f}", net_px, "bold", POS if good else NEG, ha="center")
            _txt(ax, bx + 85, cy + 30, "NET", 20, "bold", GREY, ha="center")
        else:
            _txt(ax, bx + 85, cy, f"{net:+.1f}", net_px, "bold", POS if good else NEG, ha="center")


def build_lineup_table_png(p: dict) -> bytes:
    """
    p keys:
      label, title, team_name, scope, note, brand_logo, team_logo,
      recap (dict with net, ortg, drtg, minutes, label, sub; or None),
      rows (list of dicts: players, sel, net, ortg, drtg, minutes, gp, low),
      total_units (int), show_gp (bool), foot (str)
    Rows are shown in the order given.
    """
    fig = plt.figure(figsize=(12, 15), dpi=150, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    # Title bar, same structure as the other Lineup exports
    _txt(ax, 206, 86, p.get("label", "LINEUPS"), 30, "bold", ORANGE)
    title = p.get("title", "Lineups")
    _txt(ax, 206, 150, title, _fit(title, W - 64 - 206, 72, 36), "bold", NAVY)
    _txt(ax, W - 64, 82, "EUROLEAGUE", 30, "bold", GREY, ha="right")

    # Team header
    ax.add_patch(FancyBboxPatch((64, 236), 120, 120, boxstyle="round,pad=0,rounding_size=26",
                                fc="#FFFFFF", ec=NAVY, lw=4, zorder=3))
    team = p.get("team_name", "")
    _txt(ax, 206, 268, team, _fit(team, W - 64 - 206, 60, 30), "bold", NAVY)
    scope = p.get("scope", "")
    _txt(ax, 206, 314, scope, _fit(scope, W - 64 - 206, 28, 18), "semibold", GREY)
    note = p.get("note", "")
    _txt(ax, 206, 346, note, _fit(note, W - 64 - 206, 26, 16), "regular", GREY)

    rows = list(p.get("rows", []))
    recap = p.get("recap")
    if recap:
        top = _hero(ax, recap) + 22
        cap = MAX_ROWS_HERO
    else:
        top = 384
        cap = MAX_ROWS_PLAIN
    rows = rows[:cap]
    _rows(fig, ax, rows, top, bool(p.get("show_gp")))

    foot = p.get("foot", "")
    if foot:
        _txt(ax, 64, 1220, foot, _fit(foot, W - 128, 20, 14), "regular", GREY)

    # Footer
    ax.plot([64, W - 64], [1240, 1240], color=NAVY, lw=3, zorder=3)
    _txt(ax, 64, 1276, "DataViz By EL_STATSLAB", 36, "bold", NAVY)
    _txt(ax, 64, 1312, "Insights, Trends, Metrics, Dataviz", 24, "semibold", ORANGE)
    _txt(ax, W - 64, 1276, "X @EL_Statslab", 36, "bold", NAVY, ha="right")
    _txt(ax, W - 64, 1312, "elstatslab.com", 24, "regular", GREY, ha="right")

    # Logos last, pasted once at their final size
    _place_logo(fig, _open_brand(p.get("brand_logo")), 124, 116, 120, 120)
    _place_logo(fig, _open_team(p.get("team_logo")), 124, 296, 84, 84)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def max_rows(has_recap: bool) -> int:
    """How many units fit on the image, used by the tab to pick the rows."""
    return MAX_ROWS_HERO if has_recap else MAX_ROWS_PLAIN
