"""
ELSTATSLAB Team Card PNG export (square, 12 x 12 in at 150 dpi = 1800 px).

Same brand layout as build_preview_png / build_impact_pulse_png in app.py:
title bar (ELSTATSLAB logo + EuroLeague logo), white background, Barlow
Condensed, standard footer.

This module is self contained on purpose: team_cards.py can import it
without importing app.py (which would create a circular import).

Run from the ELSTATSLAB_APP folder so that Logos/ and fonts/ resolve.

Usage from team_cards.py:

    from team_card_export import build_team_card_png

    png = build_team_card_png(
        team_name="Panathinaikos Athens",
        rows=[("NET RTG", "27.6", 100), ("OFF RTG", "126.8", 89), ...],
        team_logo_path=logo_path,        # Path or None
        # colour_fn is optional: the default matches percentile_color()
        scope_label="Regular Season · Per Game",
        season_label="2026-27",
        games_played=2,
    )
"""

import io
from pathlib import Path
from typing import Callable, Optional, Sequence

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Ellipse

# =============================================================================
# CONFIG (mirrors app.py)
# =============================================================================
FONTS_DIR = Path("fonts")
LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"

BG_WHITE = "#ffffff"
BRAND_ORANGE = "#e8491c"

# Title bar logo boxes [x, y, width, height] in title-bar axes fractions.
# Increase width/height to make a logo bigger. The logo is fitted inside its
# box without distortion, anchored to the left (brand) or right (EuroLeague).
BRAND_LOGO_BOX = [0.0, 0.0, 0.14, 1.0]
EL_LOGO_BOX    = [0.78, 0.12, 0.22, 0.76]

# Team header block (team logo + team name), in header axes fractions.
# TEAM_BLOCK_X: left edge of the team logo. 0.0 lines it up with the ELSTATSLAB logo
#   and the row bands (0.02 would line it up with the metric labels instead).
# TEAM_LOGO_W / TEAM_LOGO_H: max size of the team logo (width fraction of the
#   card, height fraction of the header). The logo keeps its own proportions.
# The team name starts right after the logo, whatever its shape.
TEAM_BLOCK_X  = 0.0
TEAM_LOGO_W   = 0.09
TEAM_LOGO_H   = 0.70
TEAM_LOGO_GAP = 0.018
TEAM_LOGO_CY  = 0.45   # vertical centre of the logo, in header fractions


def _brand_font(filename: str, fallback_weight: str = "bold") -> font_manager.FontProperties:
    fp = FONTS_DIR / filename
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


BARLOW_BOLD     = _brand_font("BarlowCondensed-Bold.ttf", "bold")
BARLOW_SEMIBOLD = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
BARLOW_REGULAR  = _brand_font("BarlowCondensed-Regular.ttf", "regular")

# Percentile colours: identical to percentile_color() in team_cards.py, so the
# PNG always matches the website. Returns (badge/bar fill, badge text colour).
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
# LOGO HELPERS (same logic as _autocrop_logo in app.py)
# =============================================================================
def _autocrop_logo(img_arr):
    alpha = img_arr[:, :, 3]
    ys, xs = np.where(alpha > 0.04)
    if len(xs) == 0:
        return img_arr
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    cropped = img_arr[y0:y1 + 1, x0:x1 + 1]

    h = cropped.shape[0]
    a2 = cropped[:, :, 3]
    row_has_content = (a2 > 0.04).sum(axis=1)
    threshold = a2.shape[1] * 0.005
    gap_start = None
    for i, s in enumerate(row_has_content):
        if s < threshold:
            if gap_start is None:
                gap_start = i
        else:
            if gap_start is not None:
                gap_h = i - gap_start
                if gap_h > h * 0.015 and gap_start > h * 0.25:
                    cropped = cropped[:gap_start, :, :]
                    a3 = cropped[:, :, 3]
                    ys2, xs2 = np.where(a3 > 0.04)
                    if len(xs2):
                        xx0, xx1, yy0, yy1 = xs2.min(), xs2.max(), ys2.min(), ys2.max()
                        cropped = cropped[yy0:yy1 + 1, xx0:xx1 + 1]
                    return cropped
            gap_start = None
    return cropped


def _load_brand_logo(path: Path):
    """Loads a title bar logo, trims its empty margins (transparent or near
    white) so it really fills its box, and flattens it on white."""
    img = plt.imread(str(path))
    if img.dtype == np.uint8:
        img = img.astype("float32") / 255.0
    has_alpha = img.ndim == 3 and img.shape[2] == 4
    if has_alpha:
        mask = img[:, :, 3] > 0.04
    elif img.ndim == 3:
        mask = (img[:, :, :3] < 0.96).any(axis=2)
    else:
        mask = img < 0.96
    ys, xs = np.where(mask)
    if len(xs):
        img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if has_alpha:
        alpha = img[:, :, 3:4]
        img = img[:, :, :3] * alpha + np.ones_like(img[:, :, :3]) * (1 - alpha)
    return img


def _load_team_logo_on_white(path: Path):
    img = plt.imread(str(path))
    if img.ndim == 3 and img.shape[2] == 4:
        img = _autocrop_logo(img)
        alpha = img[:, :, 3:4]
        rgb = img[:, :, :3]
        img = rgb * alpha + np.ones_like(rgb) * (1 - alpha)
    return img


def _pick_extremes(rows: Sequence[tuple], k: int, neutral: Sequence[str]):
    """Indexes of up to k strongest rows (percentile above 50) and up to k
    weakest rows (percentile below 50). Rows listed in `neutral` are skipped:
    PACE and AST% describe a style, not a level of quality.
    Percentiles only take about 20 distinct values, so ties between stats are
    common (several rows at 95 or 100). Ties are broken by row order on the
    card, which follows the site's order (NET RTG first)."""
    if k <= 0:
        return set(), set()
    valid = [i for i, r in enumerate(rows)
             if r[2] is not None and r[0] not in neutral]
    by_high = sorted(valid, key=lambda i: (-rows[i][2], i))
    strong = [i for i in by_high if rows[i][2] > 50][:k]
    by_low = sorted(valid, key=lambda i: (rows[i][2], i))
    weak = [i for i in by_low if rows[i][2] < 50][:k]
    return set(strong), set(weak)


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
    neutral_labels: Sequence[str] = ("PACE", "AST%"),
) -> bytes:
    """
    rows: sequence of (label, value_text, percentile) in display order.
          value_text is already formatted by the caller (e.g. "126.8", "61.2%").
          percentile is 0 to 100, or None if unavailable.
    n_highlight: max number of strongest (green band, percentile above 50) and
          weakest (red band, percentile below 50) rows to shade. 0 disables.
    neutral_labels: rows never shaded (style stats, not quality).
    Returns PNG bytes.
    """
    colour_fn = colour_fn or default_pct_colour
    n = len(rows)

    fig = plt.figure(figsize=(12, 12), dpi=150, facecolor=BG_WHITE)
    gs = GridSpec(
        nrows=5, ncols=1,
        height_ratios=[1.0, 1.25, 7.1, 0.35, 0.55],
        hspace=0.10,
        left=0.04, right=0.96, top=0.96, bottom=0.03,
    )

    # ── Title bar ───────────────────────────────────────────────────────
    ax_title = fig.add_subplot(gs[0, 0])
    ax_title.axis("off")
    ax_title.set_xlim(0, 1)
    ax_title.set_ylim(0, 1)
    if ELSTATSLAB_LOGO.exists():
        brand_ax = ax_title.inset_axes(BRAND_LOGO_BOX)
        brand_ax.imshow(_load_brand_logo(ELSTATSLAB_LOGO), interpolation="lanczos")
        brand_ax.set_anchor("W")
        brand_ax.axis("off")
    if EUROLEAGUE_LOGO.exists():
        el_ax = ax_title.inset_axes(EL_LOGO_BOX)
        el_ax.imshow(_load_brand_logo(EUROLEAGUE_LOGO), interpolation="lanczos")
        el_ax.set_anchor("E")
        el_ax.axis("off")
    title = "Team Card  |  EuroLeague"
    if season_label:
        title += f" {season_label}"
    ax_title.text(0.50, 0.50, title, ha="center", va="center", fontsize=30,
                  fontproperties=BARLOW_BOLD, color="#1a1a1a")

    # ── Team header ─────────────────────────────────────────────────────
    ax_head = fig.add_subplot(gs[1, 0])
    ax_head.axis("off")
    ax_head.set_xlim(0, 1)
    ax_head.set_ylim(0, 1)

    name_x = TEAM_BLOCK_X

    if team_logo_path is not None and Path(team_logo_path).exists():
        logo_img = _load_team_logo_on_white(Path(team_logo_path))
        img_h, img_w = logo_img.shape[:2]
        aspect = img_w / img_h

        pos = ax_head.get_position()
        ax_w_in, ax_h_in = pos.width * 12, pos.height * 12
        max_h_in = TEAM_LOGO_H * ax_h_in
        max_w_in = TEAM_LOGO_W * ax_w_in
        draw_h_in = min(max_h_in, max_w_in / aspect)
        draw_w_in = draw_h_in * aspect
        w_frac, h_frac = draw_w_in / ax_w_in, draw_h_in / ax_h_in

        logo_ax = ax_head.inset_axes(
            [TEAM_BLOCK_X, TEAM_LOGO_CY - h_frac / 2, w_frac, h_frac])
        logo_ax.imshow(logo_img, interpolation="lanczos")
        logo_ax.axis("off")
        name_x = TEAM_BLOCK_X + w_frac + TEAM_LOGO_GAP

    ax_head.text(name_x, 0.60, team_name, ha="left", va="center",
                 fontsize=42, fontproperties=BARLOW_BOLD, color="#1a1a1a")
    ax_head.text(name_x, 0.20, scope_label, ha="left", va="center", fontsize=18,
                 fontproperties=BARLOW_REGULAR, color="#777777")

    if games_played is not None:
        gp_txt = f"{games_played} GAME{'S' if games_played != 1 else ''} PLAYED"
        ax_head.text(0.985, 0.24, gp_txt, ha="right", va="center", fontsize=18,
                     fontproperties=BARLOW_BOLD, color=BRAND_ORANGE,
                     bbox=dict(boxstyle="round,pad=0.35,rounding_size=0.6",
                               facecolor="#fdeee8", edgecolor=BRAND_ORANGE,
                               linewidth=1.0))

    # ── Metric rows ─────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[2, 0])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    pos = ax.get_position()
    ax_w_in = pos.width * 12
    ax_h_in = pos.height * 12
    badge_w = 0.60 / ax_w_in      # 0.60 in wide
    badge_h = 0.40 / ax_h_in      # 0.40 in tall

    x_label, x_value, x_badge = 0.02, 0.215, 0.395
    bar_x0, bar_x1 = 0.445, 0.965
    row_h = 1.0 / max(n, 1)
    strong_idx, weak_idx = _pick_extremes(rows, n_highlight, neutral_labels)

    for i, (label, value_text, pct) in enumerate(rows):
        y = 1.0 - (i + 0.5) * row_h

        if i in strong_idx or i in weak_idx:
            band_col, accent_col = (("#E8F4EC", "#1E8449") if i in strong_idx
                                    else ("#FBE9E8", "#D9534F"))
            ax.add_patch(plt.Rectangle((0.0, y - row_h * 0.47), 0.995, row_h * 0.94,
                                       facecolor=band_col, edgecolor="none", zorder=0))
            ax.add_patch(plt.Rectangle((0.0, y - row_h * 0.47), 0.006, row_h * 0.94,
                                       facecolor=accent_col, edgecolor="none", zorder=0.2))

        if i > 0:
            ax.plot([0.02, 0.985], [1.0 - i * row_h] * 2,
                    color="#eeeeee", linewidth=0.8, zorder=0.5)

        ax.text(x_label, y, label, ha="left", va="center_baseline", fontsize=18,
                fontproperties=BARLOW_SEMIBOLD, color="#555555")
        ax.text(x_value, y, value_text, ha="left", va="center_baseline", fontsize=27,
                fontproperties=BARLOW_BOLD, color="#1a1a1a")

        # Track
        ax.plot([bar_x0, bar_x1], [y, y], color="#ececec", linewidth=15,
                solid_capstyle="round", zorder=1)

        if pct is None:
            ax.text(x_badge, y, "n/a", ha="center", va="center_baseline", fontsize=15,
                    fontproperties=BARLOW_SEMIBOLD, color="#999999")
            continue

        col, col_text = colour_fn(pct)
        fill_x1 = bar_x0 + (bar_x1 - bar_x0) * max(0.0, min(100.0, pct)) / 100.0
        ax.plot([bar_x0, fill_x1], [y, y], color=col, linewidth=15,
                solid_capstyle="round", zorder=2)

        ax.add_patch(Ellipse((x_badge, y), badge_w, badge_h,
                             facecolor=col, edgecolor="none", zorder=3))
        ax.text(x_badge, y, f"{pct:.0f}", ha="center", va="center_baseline",
                fontsize=18, fontproperties=BARLOW_BOLD,
                color=col_text, zorder=4)

    # ── Legend ──────────────────────────────────────────────────────────
    ax_leg = fig.add_subplot(gs[3, 0])
    ax_leg.axis("off")
    ax_leg.set_xlim(0, 1)
    ax_leg.set_ylim(0, 1)
    ax_leg.text(0.5, 0.5,
                "Badge = percentile rank vs the other teams (100 best, 0 worst). "
                "PACE: high means fast. Shaded rows: strongest and weakest (PACE and AST% excluded).",
                ha="center", va="center", fontsize=14,
                fontproperties=BARLOW_REGULAR, color="#777777")

    # ── Footer (standard branding) ──────────────────────────────────────
    ax_foot = fig.add_subplot(gs[4, 0])
    ax_foot.axis("off")
    ax_foot.set_xlim(0, 1)
    ax_foot.set_ylim(0, 1)
    ax_foot.text(0.47, 0.64, "DataViz By EL_STATSLAB", ha="right", va="center",
                 fontsize=20, fontproperties=BARLOW_BOLD, color="#1a1a1a")
    ax_foot.text(0.5, 0.64, "|", ha="center", va="center",
                 fontsize=20, fontproperties=BARLOW_REGULAR, color="#bbbbbb")
    ax_foot.text(0.53, 0.64, "Insights, Trends, Metrics, Dataviz", ha="left", va="center",
                 fontsize=14, fontproperties=BARLOW_SEMIBOLD, color=BRAND_ORANGE)
    ax_foot.text(0.5, 0.20, "X @EL_Statslab   |   elstatslab.com",
                 ha="center", va="center", fontsize=14,
                 fontproperties=BARLOW_REGULAR, color="#888888")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG_WHITE, dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
