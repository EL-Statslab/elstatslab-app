"""
ELSTATSLAB Matchup PNG export (4:5, 12 x 15 in at 150 dpi = 1800 x 2250 px).

New ELSTATSLAB look: warm paper background, deep navy, orange accent, Barlow
Condensed. The comparison cells keep the website gradient (green better, red
worse, intensity from colour_intensity), so the PNG always matches the site.

Drop in replacement for build_preview_png in app.py, same arguments plus the
optional form_gauges flag (Last 5 gauges inside the Season table):

    from preview_export import build_preview_png

Self contained on purpose (no import from app.py, so no circular import).
Run from the ELSTATSLAB_APP folder so that Logos/ and fonts/ resolve.
"""

import io
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from matplotlib.textpath import TextPath
from PIL import Image

# =============================================================================
# CONFIG (mirrors app.py)
# =============================================================================
FONTS_DIR = Path("fonts")
LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"

LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "BES": "BJK.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png",
    "MIL": "AXM.png", "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png",
    "PAN": "PAO.png", "PAR": "PAR.png", "PRS": "PBB.png", "RED": "CZV.png",
    "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png", "ZAL": "ZAL.png",
}

# Same scales as the website (app.py). Keep these two blocks in sync with app.py.
METRICS = ["ORTG", "DRTG", "NETRTG", "OREB%", "REB%", "AST%", "eFG%", "TOV%"]
METRIC_SCALE = {
    "ORTG": 8.0, "DRTG": 8.0, "NETRTG": 10.0,
    "OREB%": 5.0, "REB%": 4.0, "AST%": 6.0,
    "eFG%": 4.0, "TOV%": 2.5,
}
LOWER_IS_BETTER = {"DRTG", "TOV%"}
# Last 5 gauges: the coloured line only shows when the gap reaches this share of the
# metric scale (small gaps stay as a single dot, which keeps the visual calm)
LAST5_MIN_GAP = 0.30

# Website data colours (form squares, series score, comparison cells)
EL_GREEN = "#2ea043"
EL_RED = "#da3633"
_GREEN_RGB = (46, 160, 67)
_RED_RGB = (218, 54, 51)

# ELSTATSLAB look
PAPER = "#F3EEE4"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
NAVY = "#14213D"
ORANGE = "#E4572E"
GREY = "#6B7280"

# Canvas: design units, 1000 wide by 1250 high (4:5, the largest ratio X shows uncropped)
W, H = 1000, 1250
FIG_W_IN, FIG_H_IN, DPI = 12, 15, 150
PT = FIG_W_IN * 72 / W          # points per design unit


def _resolve(p) -> Path:
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


# =============================================================================
# COLOUR (same maths as colour_intensity / mpl_colour in app.py)
# =============================================================================
def colour_intensity(hv, av, metric: str) -> tuple:
    if hv is None or av is None:
        return (0.0, 0.0)
    diff = hv - av
    if metric in LOWER_IS_BETTER:
        diff = -diff
    scale = METRIC_SCALE.get(metric, 5.0)
    norm = max(-1.0, min(1.0, diff / scale))
    return (norm, -norm)


def cell_colour(intensity: float) -> str:
    """Website gradient composited on white, as an opaque hex colour."""
    base = _GREEN_RGB if intensity >= 0 else _RED_RGB
    alpha = min(0.55, abs(intensity) * 0.6)
    rgb = [round(c * alpha + 255 * (1 - alpha)) for c in base]
    return "#%02x%02x%02x" % tuple(rgb)


# =============================================================================
# LOGO HELPERS
# =============================================================================
def _autocrop_rgba(img: np.ndarray, strip_sponsor: bool = True) -> np.ndarray:
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
    img = _read_rgba(path)
    if img[:, :, 3].min() > 0.98:                       # fully opaque file: drop the white
        whiteness = img[:, :, :3].min(axis=2)
        img[:, :, 3] = np.clip((1.0 - whiteness - 0.03) / 0.24, 0.0, 1.0)
    return _autocrop_rgba(img, strip_sponsor=False)


def _load_team_logo_on_white(path: Path) -> np.ndarray:
    img = _autocrop_rgba(_read_rgba(path))
    a = img[:, :, 3:4]
    return img[:, :, :3] * a + (1.0 - a)


def _team_logo_path(code: str) -> Optional[Path]:
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    p = _resolve(LOGOS_DIR / filename)
    return p if p.exists() else None


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


def _title_for(round_label: str) -> str:
    """EuroLeague | Round 4 (the Regular Season prefix is dropped to keep the header light)."""
    r = (round_label or "").strip()
    if r.lower().startswith("regular season"):
        r = r[len("regular season"):].strip()
    return f"EuroLeague | {r}" if r else "EuroLeague"


def _same_stats(a: dict, b: dict) -> bool:
    """True when both sides hold the same figures for every metric."""
    for m in METRICS:
        x, y = a.get(m), b.get(m)
        if (x is None) != (y is None):
            return False
        if x is not None and abs(float(x) - float(y)) > 1e-9:
            return False
    return True


def _draw_team(ax, cx, code, name, rank, wl, form, show_record, colour=NAVY):
    box_y = 180
    _rbox(ax, cx - 62, box_y, 124, 124, "white", ec=colour, r=22, lw=3.2, z=2)
    lp = _team_logo_path(code)
    if lp is not None:
        crest = _load_team_logo_on_white(lp)
        _image(ax, crest, *_fit(crest, 92, 92, cx, box_y + 62), z=3)
    size = min(38, 640 / max(len(name), 1))
    _text(ax, cx, 350, name, size, BARLOW_BOLD, colour, ha="center")
    if show_record:
        _text(ax, cx, 386, f"#{rank} · {wl}", 26, BARLOW_REGULAR, GREY, ha="center")
    if form:
        n = len(form)
        sq_w, sq_h, gap = 24, 14, 6
        total = n * sq_w + (n - 1) * gap
        x0 = cx - total / 2
        for i, win in enumerate(reversed(form)):
            ax.add_patch(Rectangle((x0 + i * (sq_w + gap), 414), sq_w, sq_h,
                                   facecolor=EL_GREEN if win else EL_RED, edgecolor="none", zorder=3))


def _draw_table(ax, x0, width, title, h_stats, a_stats, home_label, away_label, y0, row_h):
    """Three columns: home value, metric, away value."""
    cell_w = (width - 10) * 0.34
    mid_w = width - 2 * cell_w - 12
    xh = x0
    xm = x0 + cell_w + 6
    xa = xm + mid_w + 6
    _text(ax, x0 + width / 2, y0 - 62, title, 30, BARLOW_BOLD, NAVY, ha="center")
    size = max(11, min(22, cell_w * 2.2 / max(len(home_label), len(away_label), 1)))
    for cx, lab in ((xh + cell_w / 2, home_label), (xa + cell_w / 2, away_label)):
        _text(ax, cx, y0 - 26, lab, size, BARLOW_SEMIBOLD, GREY, ha="center")
    ax.plot([x0, x0 + width], [y0 - 8, y0 - 8], color=NAVY, lw=1.6, zorder=2)

    for i, m in enumerate(METRICS):
        y = y0 + i * row_h
        hv, av = h_stats.get(m), a_stats.get(m)
        h_int, a_int = colour_intensity(hv, av, m)
        cell_h = row_h - 8
        for val, inten, cx0 in ((hv, h_int, xh), (av, a_int, xa)):
            _rbox(ax, cx0, y, cell_w, cell_h, cell_colour(inten), r=10, z=2)
            txt = f"{val:.1f}" if val is not None else "n/a"
            _text(ax, cx0 + cell_w / 2, y + cell_h / 2 + 1, txt, 36, BARLOW_BOLD, NAVY, ha="center")
        _text(ax, xm + mid_w / 2, y + cell_h / 2 + 1, m, 28, BARLOW_SEMIBOLD, NAVY, ha="center")
        if i < len(METRICS) - 1:
            ax.plot([xm + 14, xm + mid_w - 14], [y + cell_h + 4] * 2, color=RULE, lw=1, zorder=1)


def _text_w(s: str, size: float, fp) -> float:
    """Rendered width of a string in design units (no renderer needed)."""
    try:
        return TextPath((0, 0), s, size=size * PT, prop=fp).get_extents().width / PT
    except Exception:
        return len(s) * size * 0.45


def _draw_form_legend(ax, cx, y, size=24):
    """Legend of the Last 5 gauges: season dot, green end (better), red end (worse)."""
    items = [("dot", NAVY, "Season"), ("end", EL_GREEN, "Last 5 better"), ("end", EL_RED, "Last 5 worse")]
    glyph_w, pad, gap = 30, 10, 44
    widths = [glyph_w + pad + _text_w(t, size, BARLOW_REGULAR) for _, _, t in items]
    x = cx - (sum(widths) + gap * (len(items) - 1)) / 2
    for (kind, col, label), w in zip(items, widths):
        if kind == "dot":
            ax.add_patch(Circle((x + glyph_w - 8, y), 6, facecolor=col, edgecolor="none", zorder=6))
        else:
            ax.plot([x, x + glyph_w - 8], [y, y], color=col, lw=4.2, zorder=5, solid_capstyle="round")
            ax.add_patch(Circle((x + glyph_w - 8, y), 8, facecolor=col, edgecolor="white", linewidth=1.6, zorder=6))
        _text(ax, x + glyph_w + pad, y, label, size, BARLOW_REGULAR, GREY)
        x += w + gap


def _draw_table_form(ax, x0, width, title, h_stats, a_stats, h_recent, a_recent,
                     home_label, away_label, y0, row_h):
    """Season table where every cell carries a small gauge: dot = season value,
    line end = Last 5 value, green when the Last 5 is better, red when worse."""
    cell_w = (width - 10) * 0.34
    mid_w = width - 2 * cell_w - 12
    xh = x0
    xm = x0 + cell_w + 6
    xa = xm + mid_w + 6
    _text(ax, x0 + width / 2, y0 - 90, title, 30, BARLOW_BOLD, NAVY, ha="center")
    _draw_form_legend(ax, x0 + width / 2, y0 - 56, size=24)
    size = max(11, min(22, cell_w * 2.2 / max(len(home_label), len(away_label), 1)))
    for cx, lab in ((xh + cell_w / 2, home_label), (xa + cell_w / 2, away_label)):
        _text(ax, cx, y0 - 22, lab, size, BARLOW_SEMIBOLD, GREY, ha="center")
    ax.plot([x0, x0 + width], [y0 - 8, y0 - 8], color=NAVY, lw=1.6, zorder=2)

    for i, m in enumerate(METRICS):
        y = y0 + i * row_h
        hv, av = h_stats.get(m), a_stats.get(m)
        h_int, a_int = colour_intensity(hv, av, m)
        cell_h = row_h - 8
        for val, inten, cx0, recent in ((hv, h_int, xh, h_recent), (av, a_int, xa, a_recent)):
            _rbox(ax, cx0, y, cell_w, cell_h, cell_colour(inten), r=10, z=2)
            txt = f"{val:.1f}" if val is not None else "n/a"
            _text(ax, cx0 + cell_w / 2, y + cell_h / 2 - 9, txt, 30, BARLOW_BOLD, NAVY, ha="center")
            l5 = (recent or {}).get(m)
            if val is None or l5 is None:
                continue
            gx0, gx1 = cx0 + cell_w * 0.15, cx0 + cell_w * 0.85
            gy = y + cell_h - 8
            mid, half = (gx0 + gx1) / 2, (gx1 - gx0) / 2
            ax.plot([gx0, gx1], [gy, gy], color=NAVY, alpha=0.22, lw=2.6, zorder=3, solid_capstyle="round")
            d = float(l5) - float(val)
            if abs(d) >= LAST5_MIN_GAP * METRIC_SCALE.get(m, 5.0):
                good = (d < 0) if m in LOWER_IS_BETTER else (d > 0)
                col = EL_GREEN if good else EL_RED
                off = max(-1.0, min(1.0, d / METRIC_SCALE.get(m, 5.0))) * half
                # white halo under the coloured line so it stays readable on any cell colour
                ax.plot([mid, mid + off], [gy, gy], color="white", lw=6.6, zorder=4, solid_capstyle="round")
                ax.plot([mid, mid + off], [gy, gy], color=col, lw=4.2, zorder=4.5, solid_capstyle="round")
                ax.add_patch(Circle((mid + off, gy), 6.8, facecolor=col, edgecolor="white", linewidth=2, zorder=6))
            ax.add_patch(Circle((mid, gy), 4.6, facecolor=NAVY, edgecolor="white", linewidth=1.4, zorder=7))
        _text(ax, xm + mid_w / 2, y + cell_h / 2 + 1, m, 28, BARLOW_SEMIBOLD, NAVY, ha="center")
        if i < len(METRICS) - 1:
            ax.plot([xm + 14, xm + mid_w - 14], [y + cell_h + 4] * 2, color=RULE, lw=1, zorder=1)


# =============================================================================
# MAIN EXPORT
# =============================================================================
def build_preview_png(home_code: str, home_name: str, home_rank: int,
                      home_wl: str, home_form: list,
                      away_code: str, away_name: str, away_rank: int,
                      away_wl: str, away_form: list,
                      h_season: dict, a_season: dict,
                      h_right: dict, a_right: dict,
                      home_prob: float, away_prob: float,
                      round_label: str,
                      show_prediction: bool = True,
                      right_label: str = "Last 5",
                      round_: str = "RS",
                      series_score: dict = None,
                      form_gauges: bool = False) -> bytes:
    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), dpi=DPI, facecolor=PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    # ── Title bar ───────────────────────────────────────────────────────
    brand = _resolve(ELSTATSLAB_LOGO)
    if brand.exists():
        logo = _load_brand_logo(brand)
        _image(ax, logo, *_fit(logo, 96, 96, 98, 84), z=6)
    _text(ax, 170, 52, "MATCHUP", 22, BARLOW_BOLD, ORANGE)
    title = _title_for(round_label)
    _text(ax, 170, 106, title, min(54, 1700 / max(len(title), 1)), BARLOW_BOLD, NAVY)
    _text(ax, W - 50, 52, "EUROLEAGUE", 20, BARLOW_BOLD, GREY, ha="right")

    # ── Teams ───────────────────────────────────────────────────────────
    is_post = round_ in ("PO", "FF", "PI")
    lx, rx = 205, 795
    _draw_team(ax, lx, home_code, home_name, home_rank, home_wl, home_form, not is_post, NAVY)
    _draw_team(ax, rx, away_code, away_name, away_rank, away_wl, away_form, not is_post, ORANGE)

    if is_post and series_score and series_score.get("games_played", 0) > 0:
        if series_score.get("home_code", "").upper() == home_code.upper():
            hw, aw = series_score["home_wins"], series_score["away_wins"]
        else:
            hw, aw = series_score["away_wins"], series_score["home_wins"]
        hcol = EL_GREEN if hw > aw else (EL_RED if hw < aw else NAVY)
        acol = EL_GREEN if aw > hw else (EL_RED if aw < hw else NAVY)
        _text(ax, 500, 218, "SERIES", 22, BARLOW_SEMIBOLD, GREY, ha="center")
        _text(ax, 430, 284, str(hw), 84, BARLOW_BOLD, hcol, ha="center")
        _text(ax, 500, 284, "-", 60, BARLOW_REGULAR, "#B5AB98", ha="center")
        _text(ax, 570, 284, str(aw), 84, BARLOW_BOLD, acol, ha="center")
    else:
        _text(ax, 500, 254, "VS", 84, BARLOW_BOLD, NAVY, ha="center")

    # ── Comparison tables ───────────────────────────────────────────────
    use_gauges = bool(form_gauges) and bool(h_right) and bool(a_right)
    row_h = 56 if show_prediction else 72
    y0 = 536
    if use_gauges:
        row_h, y0 = 52, 560
        _draw_table_form(ax, 120, 760, "Season", h_season, a_season, h_right, a_right,
                         home_name, away_name, y0, row_h)
    else:
        single = _same_stats(h_season, h_right) and _same_stats(a_season, a_right)
        if single:
            _draw_table(ax, 120, 760, "Season", h_season, a_season, home_name, away_name, y0, row_h)
        else:
            _draw_table(ax, 50, 430, "Season", h_season, a_season, home_name, away_name, y0, row_h)
            _draw_table(ax, 520, 430, right_label, h_right, a_right, home_name, away_name, y0, row_h)

    # ── Win probability ─────────────────────────────────────────────────
    if show_prediction:
        label = "Win probability" if round_ == "RS" else "Match edge"
        if round_ == "FF":
            label += " (neutral court)"
        py = y0 + 8 * row_h + 36
        _text(ax, 500, py, label, 32, BARLOW_BOLD, NAVY, ha="center")
        bx, bw, by, bh = 80, 840, py + 28, 30
        split = bw * float(home_prob)
        ax.add_patch(Rectangle((bx, by), split, bh, facecolor=NAVY, zorder=2))
        ax.add_patch(Rectangle((bx + split, by), bw - split, bh, facecolor=ORANGE, zorder=2))
        ax.plot([bx + split] * 2, [by - 5, by + bh + 5], color=PAPER, lw=3, zorder=3)
        _text(ax, bx, by + 62, f"{home_prob * 100:.1f}%", 52, BARLOW_BOLD, NAVY)
        _text(ax, bx, by + 104, home_name, 24, BARLOW_REGULAR, GREY)
        _text(ax, bx + bw, by + 62, f"{away_prob * 100:.1f}%", 52, BARLOW_BOLD, ORANGE, ha="right")
        _text(ax, bx + bw, by + 104, away_name, 24, BARLOW_REGULAR, GREY, ha="right")

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
