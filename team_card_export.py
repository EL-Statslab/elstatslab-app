"""
ELSTATSLAB Team Card PNG export (4:5, 12 x 15 in at 150 dpi = 1800 x 2250 px).

New ELSTATSLAB look: warm paper background, deep navy, orange accent, Barlow
Condensed. Percentile colours stay identical to percentile_color() in
team_cards.py, so the PNG always matches the website.

Same public API as before, so team_cards.py does not change:

    from team_card_export import build_team_card_png

    png = build_team_card_png(
        team_name="Besiktas Istanbul",
        rows=[("NET RTG", "0.4", 63), ("OFF RTG", "123.8", 84), ...],
        team_logo_path=logo_path,        # Path or None
        scope_label="Regular Season · Per Game",
        season_label="2026-27",
        games_played=3,
    )

Self contained on purpose (no import from app.py, so no circular import).
Run from the ELSTATSLAB_APP folder so that Logos/ and fonts/ resolve.
"""

import io
from pathlib import Path
from typing import Callable, Optional, Sequence

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from PIL import Image

# =============================================================================
# CONFIG
# =============================================================================
FONTS_DIR = Path("fonts")
LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"

# Palette of the ELSTATSLAB look
PAPER = "#F3EEE4"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
TRACK = "#E9E0CF"
NAVY = "#14213D"
NAVY_SOFT = "#B9C0D0"
ORANGE = "#E4572E"
GREY = "#6B7280"

# Canvas: design units, 1000 wide by 1250 high (4:5, the largest ratio X shows uncropped)
W, H = 1000, 1250
FIG_W_IN, FIG_H_IN, DPI = 12, 15, 150
PT = FIG_W_IN * 72 / W          # points per design unit

# Vertical layout (design units)
HERO_Y, HERO_H = 160, 130
CARDS_Y, CARDS_H = 308, 104
COLS_Y = 440
ROWS_TOP, ROWS_BOTTOM = 462, 1108
ROW_H_MAX = 46

# Metric rows: columns
X_LABEL, X_VALUE = 66, 300
TRACK_X0, TRACK_X1 = 440, 925


def _resolve(p) -> Path:
    """Finds a file relative to the working folder, then relative to this module."""
    p = Path(p)
    if p.exists():
        return p
    alt = Path(__file__).resolve().parent / p
    return alt if alt.exists() else p


def _brand_font(filename: str, fallback_weight: str = "bold") -> font_manager.FontProperties:
    fp = _resolve(FONTS_DIR / filename)
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


BARLOW_BOLD = _brand_font("BarlowCondensed-Bold.ttf", "bold")
BARLOW_SEMIBOLD = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
BARLOW_REGULAR = _brand_font("BarlowCondensed-Regular.ttf", "regular")


# Percentile colours: identical to percentile_color() in team_cards.py.
# Returns (fill, text colour on the fill).
def default_pct_colour(pct: float) -> tuple:
    if pct >= 80:
        return "#1E8449", "#FFFFFF"
    if pct >= 60:
        return "#82C99A", "#153B24"
    if pct >= 40:
        return "#F2C94C", "#5C4300"
    if pct >= 20:
        return "#F2B8B5", "#5A1F1D"
    return "#D9534F", "#FFFFFF"


# =============================================================================
# LOGO HELPERS
# =============================================================================
def _autocrop_rgba(img: np.ndarray, strip_sponsor: bool = True) -> np.ndarray:
    """Trims transparent margins, and drops a sponsor block separated from the
    crest by a real empty band (same rule as _autocrop_logo in app.py)."""
    alpha = img[:, :, 3]
    ys, xs = np.where(alpha > 0.04)
    if len(xs) == 0:
        return img
    cropped = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if not strip_sponsor:
        return cropped

    h = cropped.shape[0]
    row_has = (cropped[:, :, 3] > 0.04).sum(axis=1)
    threshold = cropped.shape[1] * 0.005
    gap_start = None
    for i, s in enumerate(row_has):
        if s < threshold:
            if gap_start is None:
                gap_start = i
        else:
            if gap_start is not None and (i - gap_start) > h * 0.015 and gap_start > h * 0.25:
                cropped = cropped[:gap_start]
                ys2, xs2 = np.where(cropped[:, :, 3] > 0.04)
                if len(xs2):
                    cropped = cropped[ys2.min():ys2.max() + 1, xs2.min():xs2.max() + 1]
                return cropped
            gap_start = None
    return cropped


def _read_rgba(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGBA")
    im.thumbnail((900, 900), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def _load_brand_logo(path: Path) -> np.ndarray:
    """ELSTATSLAB / EuroLeague logo with a real alpha channel, so it sits on the
    paper background. A logo saved on opaque white gets its white removed."""
    img = _read_rgba(path)
    if img[:, :, 3].min() > 0.98:                       # fully opaque file
        whiteness = img[:, :, :3].min(axis=2)
        img[:, :, 3] = np.clip((1.0 - whiteness - 0.03) / 0.24, 0.0, 1.0)
    return _autocrop_rgba(img, strip_sponsor=False)


def _load_team_logo_on_white(path: Path) -> np.ndarray:
    """Team crest flattened on white (it is shown on a white badge)."""
    img = _autocrop_rgba(_read_rgba(path))
    a = img[:, :, 3:4]
    return img[:, :, :3] * a + (1.0 - a)


def _pick_extremes(rows: Sequence[tuple], k: int, neutral: Sequence[str]):
    """Ordered indexes of up to k strongest rows (percentile above 50) and up to
    k weakest rows (percentile below 50). Rows in `neutral` (PACE) are skipped.
    Ties are broken by row order, which follows the site's order."""
    if k <= 0:
        return [], []
    valid = [i for i, r in enumerate(rows) if r[2] is not None and r[0] not in neutral]
    strong = [i for i in sorted(valid, key=lambda i: (-rows[i][2], i)) if rows[i][2] > 50][:k]
    weak = [i for i in sorted(valid, key=lambda i: (rows[i][2], i)) if rows[i][2] < 50][:k]
    return strong, weak


# =============================================================================
# DRAWING HELPERS
# =============================================================================
def _text(ax, x, y, s, size, fp, color, ha="left", va="center_baseline", z=6, **kw):
    f = fp.copy()
    f.set_size(size * PT)
    return ax.text(x, y, s, fontproperties=f, color=color, ha=ha, va=va, zorder=z, **kw)


def _rbox(ax, x, y, w, h, fc, ec="none", r=14, lw=1.2, z=1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))


def _image(ax, img, x0, y0, x1, y1, z=4):
    ax.imshow(img, extent=[x0, x1, y1, y0], zorder=z, interpolation="lanczos")
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("auto")


def _fit(img: np.ndarray, box_w: float, box_h: float, cx: float, cy: float):
    h, w = img.shape[:2]
    s = min(box_w / w, box_h / h)
    dw, dh = w * s, h * s
    return cx - dw / 2, cy - dh / 2, cx + dw / 2, cy + dh / 2


def _dot(ax, x, y, r, pct, colour_fn, size):
    fill, txt = colour_fn(pct)
    ax.add_patch(Circle((x, y), r, facecolor=fill, edgecolor="none", zorder=5))
    _text(ax, x, y, f"{pct:.0f}", size, BARLOW_BOLD, txt, ha="center", z=6)


# =============================================================================
# MAIN EXPORT
# =============================================================================
def build_team_card_png(
    team_name: str,
    rows: Sequence[tuple],
    team_logo_path: Optional[Path] = None,
    scope_label: str = "Regular Season · Per Game",
    season_label: str = "",
    games_played: Optional[int] = None,
    colour_fn: Optional[Callable[[float], tuple]] = None,
    n_highlight: int = 3,
    neutral_labels: Sequence[str] = ("PACE",),
    euroleague_logo: bool = False,
) -> bytes:
    """
    rows: sequence of (label, value_text, percentile) in display order.
          value_text is already formatted by the caller (e.g. "126.8", "61.2%").
          percentile is 0 to 100, or None if unavailable.
    n_highlight: number of strongest and weakest metrics shown in the top cards
          and marked on the left edge of their rows. 0 disables.
    neutral_labels: rows never highlighted (style stats, not quality).
    euroleague_logo: True draws Logos/EL.png top right instead of the word EUROLEAGUE.
    Returns PNG bytes.
    """
    colour_fn = colour_fn or default_pct_colour
    n = len(rows)

    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), dpi=DPI, facecolor=PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    # ── Title bar ───────────────────────────────────────────────────────
    brand = _resolve(ELSTATSLAB_LOGO)
    if brand.exists():
        _image(ax, _load_brand_logo(brand), *_fit(_load_brand_logo(brand), 96, 96, 98, 84), z=6)
    _text(ax, 170, 52, "TEAM CARD", 22, BARLOW_BOLD, ORANGE)
    title = f"EuroLeague {season_label}".strip()
    _text(ax, 170, 106, title, 54, BARLOW_BOLD, NAVY)
    el = _resolve(EUROLEAGUE_LOGO)
    if euroleague_logo and el.exists():
        el_img = _load_brand_logo(el)
        _image(ax, el_img, *_fit(el_img, 200, 60, W - 150, 70), z=6)
    else:
        _text(ax, W - 50, 52, "EUROLEAGUE", 20, BARLOW_BOLD, GREY, ha="right")

    # ── Team hero ───────────────────────────────────────────────────────
    _rbox(ax, 50, HERO_Y, 900, HERO_H, NAVY, r=18)
    name_x = 80
    if team_logo_path is not None and _resolve(team_logo_path).exists():
        _rbox(ax, 76, HERO_Y + 17, 96, 96, "white", r=16, z=2)
        crest = _load_team_logo_on_white(_resolve(team_logo_path))
        _image(ax, crest, *_fit(crest, 74, 74, 124, HERO_Y + 65), z=3)
        name_x = 200
    _text(ax, name_x, HERO_Y + 46, team_name, 62, BARLOW_BOLD, PAPER)
    _text(ax, name_x, HERO_Y + 105, scope_label, 24, BARLOW_REGULAR, NAVY_SOFT)
    if games_played is not None:
        gp = f"{games_played} GAME{'S' if games_played != 1 else ''} PLAYED"
        pill_w = 40 + len(gp) * 11.5
        _rbox(ax, 922 - pill_w, HERO_Y + 45, pill_w, 40, ORANGE, r=20, z=2)
        _text(ax, 922 - pill_w / 2, HERO_Y + 65, gp, 21, BARLOW_BOLD, "white", ha="center", z=3)

    # ── Strongest and weakest ───────────────────────────────────────────
    strong, weak = _pick_extremes(rows, n_highlight, neutral_labels)
    if n_highlight > 0:
        for k, (title_txt, idxs, tone) in enumerate((
                ("STRONGEST", strong, colour_fn(100)[0]),
                ("WEAKEST", weak, colour_fn(0)[0]))):
            x = 50 + k * 460
            _rbox(ax, x, CARDS_Y, 440, CARDS_H, CARD, ec=RULE, r=16)
            ax.add_patch(Rectangle((x + 20, CARDS_Y + 14), 6, 18, facecolor=tone, zorder=3))
            _text(ax, x + 34, CARDS_Y + 24, title_txt, 18, BARLOW_BOLD, NAVY)
            if not idxs:
                _text(ax, x + 22, CARDS_Y + 72, "None past the league median", 20, BARLOW_REGULAR, GREY)
            slot = 396 / max(n_highlight, 1)
            for j, i in enumerate(idxs):
                label, value_text, _pct = rows[i]
                cx = x + 22 + j * slot
                _text(ax, cx, CARDS_Y + 56, label, 18, BARLOW_SEMIBOLD, GREY)
                _text(ax, cx, CARDS_Y + 84, value_text, 34, BARLOW_BOLD, NAVY)

    # ── Metric rows ─────────────────────────────────────────────────────
    def X(p):
        return TRACK_X0 + (TRACK_X1 - TRACK_X0) * p / 100.0

    _text(ax, X_LABEL - 6, COLS_Y, "METRIC", 16, BARLOW_SEMIBOLD, GREY)
    _text(ax, X_VALUE, COLS_Y, "VALUE", 16, BARLOW_SEMIBOLD, GREY)
    _text(ax, X(0), COLS_Y, "WORST", 16, BARLOW_SEMIBOLD, GREY)
    _text(ax, X(50), COLS_Y, "LEAGUE MEDIAN", 16, BARLOW_BOLD, NAVY, ha="center")
    _text(ax, X(100), COLS_Y, "BEST", 16, BARLOW_SEMIBOLD, GREY, ha="right")
    ax.plot([50, 950], [COLS_Y + 16] * 2, color=NAVY, lw=1.6, zorder=2)

    row_h = min(ROW_H_MAX, (ROWS_BOTTOM - ROWS_TOP) / max(n, 1))
    ax.plot([X(50)] * 2, [ROWS_TOP - 2, ROWS_TOP + row_h * n - 4], color="#CFC5B0", lw=1.4,
            ls=(0, (3, 3)), zorder=1)
    strong_set, weak_set = set(strong), set(weak)
    for i, (label, value_text, pct) in enumerate(rows):
        top = ROWS_TOP + i * row_h
        y = top + row_h / 2 - 3
        if i % 2 == 0:
            _rbox(ax, 50, top - 2, 900, row_h - 2, CARD, r=8, z=0)
        if i in strong_set or i in weak_set:
            tone = colour_fn(100)[0] if i in strong_set else colour_fn(0)[0]
            ax.add_patch(Rectangle((50, top - 1), 6, row_h - 4, facecolor=tone, zorder=2))
        _text(ax, X_LABEL, y, label, 25, BARLOW_SEMIBOLD, NAVY)
        _text(ax, X_VALUE, y, value_text, 35, BARLOW_BOLD, NAVY)
        ax.plot([X(0), X(100)], [y, y], color=TRACK, lw=3.2, solid_capstyle="round", zorder=2)
        if pct is None:
            _text(ax, X(50), y, "n/a", 22, BARLOW_SEMIBOLD, "#999999", ha="center")
            continue
        pct = max(0.0, min(100.0, float(pct)))
        fill, _ = colour_fn(pct)
        ax.plot([X(0), X(pct)], [y, y], color=fill, lw=3.6, solid_capstyle="round", zorder=3)
        _dot(ax, X(pct), y, 15, pct, colour_fn, 22)

    # ── Legend ──────────────────────────────────────────────────────────
    labels = [r[0] for r in rows]
    legend = "Dot = percentile rank vs the other teams (100 best, 0 worst)."
    if "PACE" in labels:
        legend += " PACE: high means fast."
    _text(ax, 50, 1128, legend, 19, BARLOW_REGULAR, GREY)
    neutral_txt = " and ".join(neutral_labels)
    if n_highlight > 0:
        extra = "Strongest and weakest metrics are marked on the left edge of their rows"
        extra += f" ({neutral_txt} excluded)." if neutral_txt else "."
        _text(ax, 50, 1148, extra, 19, BARLOW_REGULAR, GREY)

    # ── Footer ──────────────────────────────────────────────────────────
    ax.plot([50, 950], [1172, 1172], color=NAVY, lw=1.6, zorder=2)
    _text(ax, 50, 1198, "DataViz By EL_STATSLAB", 25, BARLOW_BOLD, NAVY)
    _text(ax, 50, 1224, "Insights, Trends, Metrics, Dataviz", 19, BARLOW_SEMIBOLD, ORANGE)
    _text(ax, 950, 1198, "X @EL_Statslab", 25, BARLOW_BOLD, NAVY, ha="right")
    _text(ax, 950, 1224, "elstatslab.com", 19, BARLOW_REGULAR, GREY, ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=PAPER, dpi=DPI)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
