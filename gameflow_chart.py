"""
ELSTATSLAB Game Flow PNG export (4:5, 12 x 15 in at 150 dpi = 1800 x 2250 px).

New ELSTATSLAB look: warm paper background, deep navy, orange accent, Barlow
Condensed. Home is navy, away is orange (same pairing as the Matchup win
probability bar), so the two teams read the same way on every visual.

Drop in replacement for gameflow_chart.py, same public function:

    from gameflow_chart import render_gameflow_png
    png = render_gameflow_png(gamecode, season, round_label="Regular Season Round 3")

The aspect argument is kept for compatibility. Both values now return the 4:5
export, the format X shows uncropped.

Self contained on purpose. Run from the ELSTATSLAB_APP folder so that Logos/
and fonts/ resolve.
"""

import io
import json
import sqlite3
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

# =============================================================================
# CONFIG
# =============================================================================
APP_ROOT = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP")
_PUBLIC_DB_LOCAL = APP_ROOT / "euroleague_public.db"
_PUBLIC_DB_CLOUD = Path("euroleague_public.db")

if _PUBLIC_DB_LOCAL.exists():
    PUBLIC_DB = _PUBLIC_DB_LOCAL
elif _PUBLIC_DB_CLOUD.exists():
    PUBLIC_DB = _PUBLIC_DB_CLOUD
else:
    PUBLIC_DB = APP_ROOT / "euroleague_public.db"

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
TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne", "BAR": "FC Barcelona",
    "BAS": "Baskonia Vitoria-Gasteiz", "BES": "Beşiktaş Istanbul", "DUB": "Dubai Basketball",
    "HTA": "Hapoel Tel Aviv", "IST": "Anadolu Efes Istanbul",
    "MAD": "Real Madrid", "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan", "MUN": "FC Bayern Munich",
    "OLY": "Olympiacos Piraeus", "PAM": "Valencia Basket",
    "PAN": "Panathinaikos Athens", "PAR": "Partizan Belgrade",
    "PRS": "Paris Basketball", "RED": "Crvena Zvezda Belgrade",
    "TEL": "Maccabi Tel Aviv", "ULK": "Fenerbahce Istanbul",
    "VIR": "Virtus Bologna", "ZAL": "Zalgiris Kaunas",
}

# ELSTATSLAB look
PAPER = "#F3EEE4"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
NAVY = "#14213D"
ORANGE = "#E4572E"
GREY = "#6B7280"
COLOR_HOME = NAVY
COLOR_AWAY = ORANGE

# Canvas: design units, 1000 wide by 1250 high (4:5)
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


def dname(code, fallback):
    return TEAM_DISPLAY_NAMES.get(code, fallback)


# =============================================================================
# DATA
# =============================================================================
def load_gameflow(gc, s):
    conn = sqlite3.connect(str(PUBLIC_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gameflow_data WHERE season=? AND gamecode=?", (s, gc))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["diff_series"] = json.loads(d["diff_series"])
    d["runs"] = json.loads(d["runs"])
    d["lineups"] = json.loads(d["lineups"])
    d["periods_series"] = json.loads(d["periods_series"]) if d.get("periods_series") else None
    return d


def _qt_bounds(ps):
    if not ps:
        return []
    b = []
    prev = ps[0]
    for i, p in enumerate(ps):
        if p != prev:
            b.append((i, p))
            prev = p
    return b


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


def _fit_text(ax, x, y, s, size, fp, color, max_w, ha="left", min_size=12, **kw):
    """Draws text, then shrinks it (measured on the real render) to fit max_w design units."""
    t = _text(ax, x, y, s, size, fp, color, ha=ha, **kw)
    fig = ax.figure
    fig.canvas.draw()
    w_units = t.get_window_extent(fig.canvas.get_renderer()).width / (FIG_W_IN * DPI) * W
    if w_units > max_w and w_units > 0:
        t.remove()
        t = _text(ax, x, y, s, max(min_size, size * max_w / w_units), fp, color, ha=ha, **kw)
    return t


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


def _draw_team(ax, cx, code, name, colour=NAVY):
    box_y = 178
    _rbox(ax, cx - 58, box_y, 116, 116, "white", ec=colour, r=22, lw=3.2, z=2)
    lp = _team_logo_path(code)
    if lp is not None:
        crest = _load_team_logo_on_white(lp)
        _image(ax, crest, *_fit(crest, 86, 86, cx, box_y + 58), z=3)
    _fit_text(ax, cx, 324, name, min(30, 700 / max(len(name), 1)), BARLOW_BOLD, colour,
              max_w=330, ha="center")


def _title_for(round_label: str) -> str:
    """EuroLeague | Round 4 (the Regular Season prefix is dropped to keep the header light)."""
    r = (round_label or "").strip()
    if r.lower().startswith("regular season"):
        r = r[len("regular season"):].strip()
    return f"EuroLeague | {r}" if r else "EuroLeague"


def _nice_step(span: float) -> int:
    for step in (2, 5, 10, 20, 25, 50):
        if span / step <= 7:
            return step
    return 50


# =============================================================================
# MAIN
# =============================================================================
def render_gameflow_png(gamecode, season, round_label="", output_path=None, aspect="square"):
    """
    Builds the Game Flow PNG. round_label e.g. "Regular Season Round 3".
    aspect is kept for compatibility (both values return the 4:5 export).
    """
    data = load_gameflow(gamecode, season)
    if not data:
        raise ValueError(f"No gameflow for GC {gamecode} season {season}")
    png = _render(data, round_label)
    if output_path:
        Path(output_path).write_bytes(png)
        print(f"PNG written: {output_path}")
    return png


def _render(data: dict, round_label: str = "") -> bytes:
    ds = data["diff_series"]
    ps = data["periods_series"]
    runs = data["runs"]
    lineups = data["lineups"]
    hc, ac = data["home_code"], data["away_code"]
    hn = dname(hc, data["home_team"])
    an = dname(ac, data["away_team"])
    fh, fa = data["final_home"], data["final_away"]

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
    _text(ax, 170, 52, "GAME FLOW", 22, BARLOW_BOLD, ORANGE)
    title = _title_for(round_label)
    _text(ax, 170, 106, title, min(54, 1700 / max(len(title), 1)), BARLOW_BOLD, NAVY)
    _text(ax, W - 50, 52, "EUROLEAGUE", 20, BARLOW_BOLD, GREY, ha="right")

    # ── Teams and final score ───────────────────────────────────────────
    _draw_team(ax, 165, hc, hn, COLOR_HOME)
    _draw_team(ax, 835, ac, an, COLOR_AWAY)
    _text(ax, 478, 244, f"{fh}", 96, BARLOW_BOLD, COLOR_HOME, ha="right")
    _text(ax, 500, 240, "-", 70, BARLOW_REGULAR, GREY, ha="center")
    _text(ax, 522, 244, f"{fa}", 96, BARLOW_BOLD, COLOR_AWAY, ha="left")

    # ── Chart card ──────────────────────────────────────────────────────
    CARD_Y0, CARD_Y1 = 356, 748
    PX0, PX1 = 118, 925
    PY0, PY1 = 408, 704
    _rbox(ax, 50, CARD_Y0, 900, CARD_Y1 - CARD_Y0, CARD, ec=RULE, r=18, z=1)

    n = len(ds)
    ymax = max(max(ds), 0) + 5
    ymin = min(min(ds), 0) - 5

    def X(i):
        return PX0 + (PX1 - PX0) * (i / max(n - 1, 1))

    def Y(v):
        return PY0 + (PY1 - PY0) * (ymax - v) / (ymax - ymin)

    step = _nice_step(ymax - ymin)
    t = int(np.ceil(ymin / step)) * step
    while t <= ymax:
        if t != 0:
            ax.plot([PX0, PX1], [Y(t), Y(t)], color=RULE, lw=0.9, zorder=2)
        _text(ax, PX0 - 12, Y(t), f"{abs(t)}", 18, BARLOW_SEMIBOLD, GREY, ha="right")
        t += step

    for r in runs:
        si, ei = r["start_idx"], r["end_idx"]
        c = COLOR_HOME if r["team"] == "home" else COLOR_AWAY
        ax.add_patch(Rectangle((X(si), PY0), X(ei) - X(si), PY1 - PY0,
                               facecolor=c, edgecolor="none", alpha=0.13, zorder=2))
        _text(ax, (X(si) + X(ei)) / 2, PY0 + 14, f"+{r['pts']}", 20, BARLOW_BOLD, c, ha="center")

    xs = np.array([X(i) for i in range(n)])
    ys = np.array([Y(v) for v in ds])
    y0 = Y(0)
    ax.fill_between(xs, ys, y0, where=ys <= y0, color=COLOR_HOME, alpha=0.22,
                    interpolate=True, linewidth=0, zorder=3)
    ax.fill_between(xs, ys, y0, where=ys >= y0, color=COLOR_AWAY, alpha=0.28,
                    interpolate=True, linewidth=0, zorder=3)
    ax.plot([PX0, PX1], [y0, y0], color=GREY, lw=1.1, zorder=4)
    ax.plot(xs, ys, color=NAVY, lw=2.6, zorder=5, solid_joinstyle="round")

    for idx_b, period in _qt_bounds(ps):
        if period <= 4:
            ax.plot([X(idx_b)] * 2, [PY0, PY1], color=RULE, lw=1.4, zorder=2)
            _text(ax, X(idx_b) + 8, PY1 - 14, f"Q{period}", 18, BARLOW_BOLD, GREY, ha="left")
        else:
            ax.plot([X(idx_b)] * 2, [PY0, PY1], color=GREY, lw=1.0, ls=(0, (4, 4)), zorder=2)
            _text(ax, X(idx_b) + 8, PY1 - 14, f"OT{period - 4}", 18, BARLOW_BOLD, GREY, ha="left")
    if ps:
        _text(ax, PX0 + 8, PY1 - 14, "Q1", 18, BARLOW_BOLD, GREY, ha="left")

    ax.add_patch(Rectangle((70, CARD_Y0 + 20), 8, 24, facecolor=COLOR_HOME, edgecolor="none", zorder=5))
    _fit_text(ax, 90, CARD_Y0 + 33, hn, 22, BARLOW_SEMIBOLD, NAVY, max_w=380)
    ax.add_patch(Rectangle((70, CARD_Y1 - 44), 8, 24, facecolor=COLOR_AWAY, edgecolor="none", zorder=5))
    _fit_text(ax, 90, CARD_Y1 - 31, an, 22, BARLOW_SEMIBOLD, NAVY, max_w=380)
    _text(ax, 930, CARD_Y1 - 31, "Point differential", 20, BARLOW_REGULAR, GREY, ha="right")

    # ── Biggest runs ────────────────────────────────────────────────────
    _text(ax, 500, 772, "BIGGEST RUNS", 28, BARLOW_BOLD, NAVY, ha="center")
    if runs:
        sr = sorted(runs, key=lambda r: -r["pts"])[:3]
        for i, r in enumerate(sr):
            cy = 818 + i * 46
            code = hc if r["team"] == "home" else ac
            c = COLOR_HOME if r["team"] == "home" else COLOR_AWAY
            ld = r.get("leader", "")
            lp_ = r.get("leader_pts")
            detail = f"led by {ld}" + (f", {lp_} pts" if lp_ is not None else "")
            _rbox(ax, 50, cy - 19, 900, 38, CARD, ec=RULE, r=10, lw=1.0, z=2)
            ax.add_patch(Rectangle((50, cy - 19), 8, 38, facecolor=c, edgecolor="none", zorder=3))
            _text(ax, 80, cy, f"{code} +{r['pts']}", 28, BARLOW_BOLD, c)
            _fit_text(ax, 925, cy, detail, 24, BARLOW_SEMIBOLD, NAVY, max_w=620, ha="right")
        _text(ax, 500, 950, "A run is a streak of points scored without the opponent scoring.",
              17, BARLOW_REGULAR, GREY, ha="center")
    else:
        _text(ax, 500, 830, "No runs of 9 points or more detected", 22, BARLOW_REGULAR, GREY, ha="center")

    # ── Best 5 by NetRtg ────────────────────────────────────────────────
    _text(ax, 500, 982, "BEST 5 BY NETRTG", 28, BARLOW_BOLD, NAVY, ha="center")
    for lu, x0, tc in [
        (next((l for l in lineups if l["team_code"] == hc), None), 50, COLOR_HOME),
        (next((l for l in lineups if l["team_code"] == ac), None), 520, COLOR_AWAY),
    ]:
        if not lu:
            continue
        cw, cy0, ch = 430, 1014, 148
        _rbox(ax, x0, cy0, cw, ch, CARD, ec=RULE, r=16, lw=1.0, z=2)
        ax.add_patch(Rectangle((x0 + 16, cy0), cw - 32, 6, facecolor=tc, edgecolor="none", zorder=3))
        cx = x0 + cw / 2
        _fit_text(ax, cx, cy0 + 36, dname(lu["team_code"], lu["team"]), 28, BARLOW_BOLD, tc,
                  max_w=cw - 40, ha="center")
        _text(ax, cx, cy0 + 68,
              f"{lu['pts_for']}-{lu['pts_against']}   ·   NetRtg {lu['net_rtg']:+.1f}   ·   {lu['min']}",
              21, BARLOW_SEMIBOLD, NAVY, ha="center")
        pl = list(lu["players"])
        l1, l2 = "  ·  ".join(pl[:3]), "  ·  ".join(pl[3:])
        _fit_text(ax, cx, cy0 + 102, l1, 22, BARLOW_REGULAR, NAVY, max_w=cw - 30, ha="center")
        if l2:
            _fit_text(ax, cx, cy0 + 130, l2, 22, BARLOW_REGULAR, NAVY, max_w=cw - 30, ha="center")

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


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--gamecode", type=int, required=True)
    p.add_argument("--season", type=int, default=2025)
    p.add_argument("--round-label", type=str, default="")
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--aspect", type=str, default="square", choices=["square", "16:9"])
    a = p.parse_args()
    render_gameflow_png(a.gamecode, a.season, round_label=a.round_label,
                        output_path=a.output or f"gameflow_E{a.season}_{a.gamecode}.png",
                        aspect=a.aspect)
