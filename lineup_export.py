"""
ELSTATSLAB lineup_export.py
PNG export (4:5, 1800 x 2250 px) for the Lineup tab: best and worst five man
unit of a team by NetRtg. Beige, navy and orange identity, Barlow Condensed.
"""

import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

FONTS_DIR = Path("fonts")
W, H = 1080, 1350

BG = "#F3EEE4"
NAVY = "#14213D"
ORANGE = "#E4572E"
GREY = "#6B7280"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
POS = "#1E7A37"
NEG = "#B02A28"

_FONT_FILES = {
    "bold": "BarlowCondensed-Bold.ttf",
    "semibold": "BarlowCondensed-SemiBold.ttf",
    "regular": "BarlowCondensed-Regular.ttf",
}


def _fp(weight: str, px: float):
    path = FONTS_DIR / _FONT_FILES[weight]
    if path.exists():
        fp = font_manager.FontProperties(fname=str(path))
    else:
        fp = font_manager.FontProperties(weight=weight)
    fp.set_size(px * 0.8)  # 1080 px canvas on a 12 inch wide figure
    return fp


def _t(ax, x, y, s, px, weight="bold", color=NAVY, ha="left", va="center"):
    ax.text(x, y, s, fontproperties=_fp(weight, px), color=color,
            ha=ha, va=va, zorder=10)


def _load_logo(path):
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return None
    a = np.array(im)
    ys, xs = np.where(a[:, :, 3] > 10)
    if len(xs):
        im = im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    return np.asarray(im)


def _draw_logo(ax, arr, cx, cy, box_w, box_h):
    if arr is None:
        return
    h, w = arr.shape[:2]
    s = min(box_w / w, box_h / h)
    dw, dh = w * s, h * s
    ax.imshow(arr, extent=(cx - dw / 2, cx + dw / 2, cy + dh / 2, cy - dh / 2),
              aspect="auto", zorder=5, interpolation="lanczos")


def _card(ax, x, y, w, h, kicker, accent, row):
    card = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=26",
                          fc=CARD, ec=RULE, lw=2, zorder=2)
    ax.add_patch(card)
    bar = Rectangle((x, y), w, 14, fc=accent, ec="none", zorder=3)
    ax.add_patch(bar)
    bar.set_clip_path(card)

    px = x + 28
    _t(ax, px, y + 62, kicker, 30, "bold", GREY)
    if row is None:
        _t(ax, x + w / 2, y + h / 2, "No second lineup meets the minimum",
           30, "semibold", GREY, ha="center")
        return

    net = row["net"]
    _t(ax, px, y + 190, f"{net:+.1f}", 136, "bold", POS if net >= 0 else NEG, va="baseline")
    _t(ax, px, y + 232, "NET RATING", 26, "bold", GREY)

    top = y + 270
    names = row["players"][:5]
    n = max(len(names), 1)
    rh = 310.0 / n                      # the list always fills the same block
    size = 42 if n >= 5 else (48 if n == 3 else 56)
    for i, name in enumerate(names):
        _t(ax, px, top + rh * (i + 0.5), name, size, "semibold", NAVY)
        ax.plot([px, x + w - 28], [top + rh * (i + 1)] * 2, color=RULE, lw=1.2, zorder=3)

    ly = y + 596
    ax.plot([px, x + w - 28], [ly, ly], color=NAVY, lw=2.4, zorder=3)
    tiles = [("ORTG", f"{row['ortg']:.1f}"), ("DRTG", f"{row['drtg']:.1f}"),
             ("MINUTES", f"{row['minutes']:.1f}"), ("GAMES", str(row["gp"]))]
    tw = (w - 56 - 12) / 2
    for i, (k, v) in enumerate(tiles):
        tx = px + (i % 2) * (tw + 12)
        ty = y + 614 + (i // 2) * 92
        ax.add_patch(FancyBboxPatch((tx, ty), tw, 80,
                                    boxstyle="round,pad=0,rounding_size=14",
                                    fc="#FFFFFF", ec="#EFE8D8", lw=1.5, zorder=3))
        _t(ax, tx + 16, ty + 22, k, 20, "bold", GREY)
        _t(ax, tx + 16, ty + 56, v, 42, "bold", NAVY)


def build_lineup_png(p: dict) -> bytes:
    fig = plt.figure(figsize=(12, 15), dpi=150, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    # Title bar
    _draw_logo(ax, _load_logo(p.get("brand_logo")), 124, 116, 120, 120)
    _t(ax, 206, 86, p.get("heading", "BEST AND WORST 5"), 30, "bold", ORANGE)
    _t(ax, 206, 150, "EuroLeague | Lineups", 76, "bold", NAVY)
    _t(ax, W - 64, 82, "EUROLEAGUE", 30, "bold", GREY, ha="right")

    # Team header
    ax.add_patch(FancyBboxPatch((64, 236), 120, 120,
                                boxstyle="round,pad=0,rounding_size=26",
                                fc="#FFFFFF", ec=NAVY, lw=4, zorder=3))
    _draw_logo(ax, _load_logo(p.get("team_logo")), 124, 296, 84, 84)
    _t(ax, 206, 268, p["team_name"], 64, "bold", NAVY)
    _t(ax, 206, 314, p["scope"], 28, "semibold", GREY)
    _t(ax, 206, 346, p.get("note", ""), 26, "regular", GREY)

    # Cards
    _card(ax, 64, 396, 464, 810, p.get("kick_best", "BEST 5"), NAVY, p["best"])
    _card(ax, 552, 396, 464, 810, p.get("kick_worst", "WORST 5"), ORANGE, p.get("worst"))

    # Footer
    ax.plot([64, W - 64], [1240, 1240], color=NAVY, lw=3, zorder=3)
    _t(ax, 64, 1276, "DataViz By EL_STATSLAB", 36, "bold", NAVY)
    _t(ax, 64, 1312, "Insights, Trends, Metrics, Dataviz", 24, "semibold", ORANGE)
    _t(ax, W - 64, 1276, "X @EL_Statslab", 36, "bold", NAVY, ha="right")
    _t(ax, W - 64, 1312, "elstatslab.com", 24, "regular", GREY, ha="right")

    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Match view: both teams side by side (4:5)
# ---------------------------------------------------------------------------
def _fit(text: str, max_px: float, base: float, min_px: float = 20.0) -> float:
    """Font size (px) so that a Barlow Condensed text fits in max_px."""
    est = 0.44 * max(len(text), 1)
    return max(min(base, max_px / est), min_px)


def _match_side(ax, x, accent, team: dict, label: str):
    w = 456
    _draw_badge = FancyBboxPatch((x, 200), 100, 100, boxstyle="round,pad=0,rounding_size=22",
                                 fc="#FFFFFF", ec=accent, lw=4, zorder=3)
    ax.add_patch(_draw_badge)
    _draw_logo(ax, _load_logo(team.get("logo")), x + 50, 250, 70, 70)
    name = team["name"]
    _t(ax, x + 120, 236, name, _fit(name, w - 130, 52, 20), "bold", accent)
    _t(ax, x + 120, 280, label, 26, "semibold", GREY)

    top, bot = 322, 1206
    card = FancyBboxPatch((x, top), w, bot - top, boxstyle="round,pad=0,rounding_size=26",
                          fc=CARD, ec=RULE, lw=2, zorder=2)
    ax.add_patch(card)
    bar = Rectangle((x, top), w, 14, fc=accent, ec="none", zorder=3)
    ax.add_patch(bar)
    bar.set_clip_path(card)
    px, rx = x + 26, x + w - 26
    y = top + 14

    # (kicker, row, section height, name size, net size, row height, listed)
    specs = [("BEST 5", team.get("best5"), 298, 34, 78, 36, True),
             ("WORST 5", team.get("worst5"), 288, 30, 64, 32, True),
             ("BEST TRIO", team.get("trio"), 140, 34, 56, 0, False),
             ("BEST DUO", team.get("duo"), 140, 34, 56, 0, False)]
    for i, (kicker, row, h, nsize, netsize, rows_h, listed) in enumerate(specs):
        if i:
            ax.plot([px, rx], [y, y], color=NAVY, lw=2.4, zorder=3)
        _t(ax, px, y + 34, kicker, 26, "bold", GREY)
        if row is None:
            _t(ax, px, y + h / 2 + 10, "Not enough minutes together", 28, "semibold", GREY)
            y += h
            continue
        net = row["net"]
        _t(ax, rx, y + 40, f"{net:+.1f}", netsize, "bold", POS if net >= 0 else NEG, ha="right")
        yy = y + 70
        names = row["players"][:5]
        if listed:
            for n in names:
                _t(ax, px, yy + rows_h / 2, n, nsize, "semibold", NAVY)
                ax.plot([px, rx], [yy + rows_h] * 2, color=RULE, lw=1.2, zorder=3)
                yy += rows_h
        else:
            txt = ", ".join(names)
            _t(ax, px, yy + 14, txt, _fit(txt, rx - px, nsize, 22), "semibold", NAVY)
            yy += 20
        stat = f"{row['minutes']:.1f} MIN  |  ORTG {row['ortg']:.1f}  |  DRTG {row['drtg']:.1f}"
        _t(ax, px, yy + 26, stat, 21, "semibold", GREY)
        y += h


def build_match_lineups_png(p: dict) -> bytes:
    """
    p: round_label, brand_logo, home / away dicts with name, logo, best5,
    worst5, trio, duo (each a dict with players, net, ortg, drtg, minutes, or None).
    """
    fig = plt.figure(figsize=(12, 15), dpi=150, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    _draw_logo(ax, _load_logo(p.get("brand_logo")), 124, 116, 120, 120)
    _t(ax, 206, 86, "MATCH LINEUPS BY NETRTG", 30, "bold", ORANGE)
    _t(ax, 206, 150, f"EuroLeague | {p.get('round_label', '')}", 76, "bold", NAVY)
    _t(ax, W - 64, 82, "EUROLEAGUE", 30, "bold", GREY, ha="right")
    _t(ax, W - 64, 114, "Single match sample", 24, "regular", GREY, ha="right")

    _match_side(ax, 64, NAVY, p["home"], "Home")
    _match_side(ax, 552, ORANGE, p["away"], "Away")

    ax.plot([64, W - 64], [1240, 1240], color=NAVY, lw=3, zorder=3)
    _t(ax, 64, 1276, "DataViz By EL_STATSLAB", 36, "bold", NAVY)
    _t(ax, 64, 1312, "Insights, Trends, Metrics, Dataviz", 24, "semibold", ORANGE)
    _t(ax, W - 64, 1276, "X @EL_Statslab", 36, "bold", NAVY, ha="right")
    _t(ax, W - 64, 1312, "elstatslab.com", 24, "regular", GREY, ha="right")

    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
