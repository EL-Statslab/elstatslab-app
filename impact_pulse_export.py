"""
ELSTATSLAB Impact Pulse PNG export (4:5, 12 x 15 in at 150 dpi = 1800 x 2250 px).

New ELSTATSLAB look: warm paper background, deep navy, orange accent, Barlow
Condensed. Home is navy, away is orange (same pairing as Matchup and Game Flow).
Delta cells keep the site green / red logic (IP_LOWER_IS_BETTER, 0.3 threshold).

Drop in replacement for build_impact_pulse_png in app.py, same arguments:

    from impact_pulse_export import build_impact_pulse_png

Self contained on purpose. Run from the ELSTATSLAB_APP folder so that Logos/
and fonts/ resolve.
"""

import io
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

# =============================================================================
# CONFIG
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
EL_GREEN = "#2ea043"   # site data colours
EL_RED = "#da3633"
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




def _title_for(round_label: str) -> str:
    """EuroLeague | Round 4 (the Regular Season prefix is dropped to keep the header light)."""
    r = (round_label or "").strip()
    if r.lower().startswith("regular season"):
        r = r[len("regular season"):].strip()
    return f"EuroLeague | {r}" if r else "EuroLeague"




# =============================================================================
# IMPACT PULSE SPECIFICS (mirror app.py)
# =============================================================================
IP_LOWER_IS_BETTER = {"DRTG", "TOV%"}
IP_EXPORT_METRICS = ["NETRTG", "eFG%", "REB%", "AST%", "TOV%"]
IP_EXPORT_COLS = {
    "NETRTG": ("on_netrtg", "off_netrtg"),
    "eFG%":   ("on_efg",   "off_efg"),
    "REB%":   ("on_reb",   "off_reb"),
    "AST%":   ("on_ast",   "off_ast"),
    "TOV%":   ("on_tov",   "off_tov"),
}
GOOD_FILL = "#D3EBD8"      # site green at 22% on white
BAD_FILL = "#F8D9D8"       # site red at 22% on white
GOOD_TXT = "#1E7A37"
BAD_TXT = "#B02A28"


def _ip_min_poss(r) -> int:
    """Minimum possessions threshold used for this row (12 for rows computed before min_poss existed)."""
    try:
        if "min_poss" in r.index and pd.notna(r["min_poss"]):
            return int(round(r["min_poss"]))
    except Exception:
        pass
    return 12


def _verdict(metric: str, delta: float) -> str:
    """'good', 'bad' or 'flat' (same 0.3 threshold as the site)."""
    if metric in IP_LOWER_IS_BETTER:
        good, bad = delta <= -0.3, delta >= 0.3
    else:
        good, bad = delta >= 0.3, delta <= -0.3
    return "good" if good else ("bad" if bad else "flat")


def _panel(ax, y0, code, disp, r, colour):
    """One team panel (900 x 462 design units) starting at y0."""
    x0, w, h = 50, 900, 462
    _rbox(ax, x0, y0, w, h, CARD, ec=RULE, r=18, lw=1.2, z=1)
    _rbox(ax, x0, y0, w, 124, colour, r=18, z=2)

    # crest badge
    _rbox(ax, x0 + 22, y0 + 16, 92, 92, "white", r=18, z=3)
    lp = _team_logo_path(code)
    if lp is not None:
        crest = _load_team_logo_on_white(lp)
        _image(ax, crest, *_fit(crest, 68, 68, x0 + 68, y0 + 62), z=4)

    _text(ax, x0 + 136, y0 + 30, disp.upper(), 21, BARLOW_SEMIBOLD, "#FFFFFF", alpha=0.82)
    _fit_text(ax, x0 + 136, y0 + 70, str(r["player_name"]).upper(), 46, BARLOW_BOLD, "#FFFFFF", max_w=500)
    _text(ax, x0 + 136, y0 + 104, "DIFFERENCE MAKER", 18, BARLOW_SEMIBOLD, "#FFFFFF", alpha=0.82)

    score = float(r["impact_score"])
    score_str = f"{score:+.2f}" if score >= 0 else f"{score:.2f}"
    _rbox(ax, x0 + w - 220, y0 + 20, 196, 84, "white", r=16, z=3)
    _text(ax, x0 + w - 122, y0 + 42, "IMPACT SCORE", 16, BARLOW_SEMIBOLD, GREY, ha="center")
    _text(ax, x0 + w - 122, y0 + 78, score_str, 40, BARLOW_BOLD,
          EL_GREEN if score >= 0 else EL_RED, ha="center")

    # table
    cols = {"metric": x0 + 70, "on": x0 + 420, "off": x0 + 585, "delta": x0 + 770}
    hy = y0 + 156
    _text(ax, cols["metric"], hy, "Metric", 20, BARLOW_SEMIBOLD, GREY)
    _text(ax, cols["on"], hy, "ON", 20, BARLOW_SEMIBOLD, GREY, ha="center")
    _text(ax, cols["off"], hy, "OFF", 20, BARLOW_SEMIBOLD, GREY, ha="center")
    _text(ax, cols["delta"], hy, "DIFF", 20, BARLOW_SEMIBOLD, GREY, ha="center")
    ax.plot([x0 + 24, x0 + w - 24], [y0 + 172, y0 + 172], color=NAVY, lw=1.6, zorder=3)

    row_h = 46
    for i, m in enumerate(IP_EXPORT_METRICS):
        yc = y0 + 198 + i * row_h
        on_col, off_col = IP_EXPORT_COLS[m]
        on_v, off_v = float(r[on_col]), float(r[off_col])
        delta = on_v - off_v
        v = _verdict(m, delta)

        accent = EL_GREEN if v == "good" else (EL_RED if v == "bad" else RULE)
        ax.add_patch(Rectangle((x0 + 24, yc - 17), 6, 34, facecolor=accent, edgecolor="none", zorder=3))
        if i < len(IP_EXPORT_METRICS) - 1:
            ax.plot([x0 + 44, x0 + w - 24], [yc + row_h / 2, yc + row_h / 2], color=RULE, lw=0.9, zorder=2)

        _text(ax, cols["metric"], yc, m, 25, BARLOW_SEMIBOLD, NAVY)
        _text(ax, cols["on"], yc, f"{on_v:.1f}", 32, BARLOW_BOLD, NAVY, ha="center")
        _text(ax, cols["off"], yc, f"{off_v:.1f}", 28, BARLOW_REGULAR, GREY, ha="center")

        fill = GOOD_FILL if v == "good" else (BAD_FILL if v == "bad" else None)
        if fill:
            _rbox(ax, cols["delta"] - 62, yc - 18, 124, 36, fill, r=9, z=3)
        tcol = GOOD_TXT if v == "good" else (BAD_TXT if v == "bad" else GREY)
        _text(ax, cols["delta"], yc, f"{delta:+.1f}", 30, BARLOW_BOLD, tcol, ha="center")

    # possessions
    on_poss = int(round(r["on_poss"])) if "on_poss" in r.index and pd.notna(r["on_poss"]) else None
    if on_poss is not None:
        total = None
        if "team_poss" in r.index and pd.notna(r["team_poss"]):
            total = int(round(r["team_poss"]))
        txt = (f"Poss played: {on_poss} / {total} team possessions" if total else f"Poss played: {on_poss}")
        _text(ax, x0 + w / 2, y0 + h - 40, txt, 21, BARLOW_SEMIBOLD, NAVY, ha="center")
    _text(ax, x0 + w / 2, y0 + h - 16, f"Min. threshold: {_ip_min_poss(r)} poss per game",
          17, BARLOW_REGULAR, GREY, ha="center")


def build_impact_pulse_png(ip_df: pd.DataFrame,
                           home_code: str, away_code: str,
                           home_disp: str, away_disp: str,
                           round_label: str) -> bytes:
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
    _text(ax, 170, 52, "IMPACT PULSE", 22, BARLOW_BOLD, ORANGE)
    title = _title_for(round_label)
    _text(ax, 170, 106, title, min(54, 1700 / max(len(title), 1)), BARLOW_BOLD, NAVY)
    _text(ax, W - 50, 52, "EUROLEAGUE", 20, BARLOW_BOLD, GREY, ha="right")
    _text(ax, 170, 152, "Who moved the needle? Impact Score is a proprietary On/Off composite metric.",
          20, BARLOW_REGULAR, GREY)

    # ── Team panels ─────────────────────────────────────────────────────
    for (code, disp, colour), y0 in zip(
        [(home_code, home_disp, NAVY), (away_code, away_disp, ORANGE)], (180, 658)
    ):
        row = ip_df[ip_df["team_code"].str.upper() == code.upper()]
        if row.empty:
            _rbox(ax, 50, y0, 900, 462, CARD, ec=RULE, r=18)
            _text(ax, 500, y0 + 231, f"No data for {disp}", 30, BARLOW_SEMIBOLD, GREY, ha="center")
            continue
        _panel(ax, y0, code, disp, row.iloc[0], colour)

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
