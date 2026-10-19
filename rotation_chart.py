"""
ELSTATSLAB rotation_chart.py (v1)

Rotation chart of one match: one row per player, one bar per stint on court, bar colour
and number = team plus/minus while the player was on court. Format 4:5
(12 x 15 in at 150 dpi = 1800 x 2250 px), same look as the Game Flow.

This module only draws. It reads two tables of euroleague_public.db:
    rotation_games  (season, gamecode, home_code, away_code, home_score, away_score, total_sec)
    rotation_stints (season, gamecode, team_code, player_id, player_name,
                     start_sec, end_sec, pts_for, pts_against)
Both are built locally by build_rotations.py and copied by build_public_db.py.

Used by lineup_tab.py (Lineup tab, Match scope) and by rotation_export.py (local PNG).
Place it in ELSTATSLAB_APP, next to gameflow_chart.py. Logos and fonts are read the same
way as in gameflow_chart.py.
"""

import io
import sqlite3
from pathlib import Path

import numpy as np
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

from gameflow_chart import LOGO_MAP, TEAM_DISPLAY_NAMES

# ELSTATSLAB look (same values as gameflow_chart.py)
PAPER = "#F3EEE4"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
NAVY = "#14213D"
ORANGE = "#E4572E"
GREY = "#6B7280"
COLOR_HOME = NAVY
COLOR_AWAY = ORANGE
BAR_EDGE = "#B5A98F"

# Plus/minus gradient (red to green, neutral centre clearly visible on the card)
PM_BAD = (214, 96, 77)
PM_MID = (228, 222, 208)
PM_GOOD = (92, 176, 120)
PM_CAP = 10

W, H = 1000, 1250
FIG_W_IN, FIG_H_IN, DPI = 12, 15, 150
PT = FIG_W_IN * 72 / W                 # points per design unit
PXU = FIG_W_IN * DPI / W               # pixels per design unit (1.8)

FONTS_DIR = Path("fonts")
LOGOS_DIR = Path("Logos")


def _resolve(p) -> Path:
    p = Path(p)
    if p.exists():
        return p
    alt = Path(__file__).resolve().parent / p
    return alt if alt.exists() else p


def _brand_font(filename, fallback_weight="bold"):
    fp = _resolve(FONTS_DIR / filename)
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


def period_len(p):
    return 600 if p <= 4 else 300


def pretty_name(raw):
    """Capital after a hyphen and after Mc (Miller-mcintyre -> Miller-McIntyre). Same rule as lineup_tab.py."""
    out = []
    for tok in str(raw or "").split(" "):
        parts = []
        for p in tok.split("-"):
            if len(p) > 1 and p[0].isalpha():
                p = p[0].upper() + p[1:]
            if p.startswith("Mc") and len(p) > 2:
                p = "Mc" + p[2].upper() + p[3:]
            parts.append(p)
        out.append("-".join(parts))
    return " ".join(out)


# =============================================================================
# DATA
# =============================================================================
def rotation_available(conn, season, gamecode) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM rotation_games WHERE season=? AND gamecode=? LIMIT 1",
                           (int(season), int(gamecode))).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def load_rotation_data(conn, season, gamecode, round_label=""):
    """Reads the two tables and returns what render_rotation_png needs, or None."""
    try:
        g = conn.execute("""SELECT home_code, away_code, home_score, away_score, total_sec
                            FROM rotation_games WHERE season=? AND gamecode=?""",
                         (int(season), int(gamecode))).fetchone()
        rows = conn.execute("""SELECT team_code, player_id, player_name, start_sec, end_sec,
                                      pts_for, pts_against
                               FROM rotation_stints WHERE season=? AND gamecode=?
                               ORDER BY team_code, player_id, start_sec""",
                            (int(season), int(gamecode))).fetchall()
    except sqlite3.Error:
        return None
    if not g or not rows:
        return None
    home, away, hs, as_, total = g
    teams = []
    for side, code, score in (("home", home, hs), ("away", away, as_)):
        players = {}
        for tc, pid, nm, st_, en, pf, pa in rows:
            if tc != code:
                continue
            p = players.setdefault(pid, {"id": pid, "name": pretty_name(nm), "sec": 0.0, "bars": []})
            p["bars"].append((float(st_), float(en), float(pf), float(pa)))
            p["sec"] += float(en) - float(st_)
        plist = sorted(players.values(), key=lambda p: (-p["sec"], p["name"]))
        teams.append({"side": side, "code": code, "name": TEAM_DISPLAY_NAMES.get(code, code),
                      "score": score, "players": plist})
    return {"gamecode": int(gamecode), "season": int(season), "total_sec": float(total),
            "round_label": round_label or "", "teams": teams}


# =============================================================================
# DRAWING
# =============================================================================
def _blend(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def pm_color(v):
    t = max(-1.0, min(1.0, v / PM_CAP))
    c = _blend(PM_MID, PM_GOOD, t) if t >= 0 else _blend(PM_MID, PM_BAD, -t)
    return tuple(x / 255 for x in c)


def _prepared_logo(path):
    """Working copy of 400 px, cropped to the visible content."""
    im = Image.open(path).convert("RGBA")
    im.thumbnail((400, 400), Image.LANCZOS)
    a = np.asarray(im)[:, :, 3]
    ys, xs = np.where(a > 10)
    if len(xs):
        im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    return im


def _brand_logo(path):
    """ELSTATSLAB logo: white background turned into transparency, ink recovered."""
    im = Image.open(path).convert("RGBA")
    im.thumbnail((400, 400), Image.LANCZOS)
    arr = np.asarray(im).astype(float)
    if arr[:, :, 3].min() < 250:                       # already has transparency
        out = im
    else:
        rgb = arr[:, :, :3]
        sat = rgb.max(axis=2) - rgb.min(axis=2)
        black = np.array([10, 10, 10], dtype=float)
        orange = np.array([232, 73, 28], dtype=float)
        grey = sat < 30
        alpha_grey = np.clip((255 - rgb.mean(axis=2)) / (255 - black[0]), 0, 1)
        alpha_col = np.clip((255 - rgb[:, :, 2]) / (255 - orange[2]), 0, 1)
        alpha = np.where(grey, alpha_grey, alpha_col)
        alpha = np.where(alpha < 0.03, 0, alpha)
        col = np.where(grey[..., None], black, orange)
        res = np.zeros(arr.shape, dtype=np.uint8)
        res[:, :, :3] = col.astype(np.uint8)
        res[:, :, 3] = (alpha * 255).astype(np.uint8)
        out = Image.fromarray(res, "RGBA")
    a = np.asarray(out)[:, :, 3]
    ys, xs = np.where(a > 10)
    if len(xs):
        out = out.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    return out


def _paste(fig, im, cx, cy, box_w, box_h):
    """Reduces the logo once (LANCZOS) to its final size in pixels and pastes it, no resampling."""
    bw, bh = box_w * PXU, box_h * PXU
    s = min(bw / im.width, bh / im.height)
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    small = im.resize((nw, nh), Image.LANCZOS)
    xo = int(round(cx * PXU - nw / 2))
    yo = int(round(FIG_H_IN * DPI - (cy * PXU + nh / 2)))
    fig.figimage(np.asarray(small), xo=xo, yo=yo, origin="upper", zorder=20)


def _title_for(round_label):
    """EuroLeague | Round 4 (a Regular Season prefix is dropped, like on the Game Flow)."""
    r = (round_label or "").strip()
    if r.lower().startswith("regular season"):
        r = r[len("regular season"):].strip()
    return f"EuroLeague | {r}" if r else "EuroLeague"


def render_rotation_png(data, output_path=None) -> bytes:
    BOLD = _brand_font("BarlowCondensed-Bold.ttf", "bold")
    SEMI = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
    REG = _brand_font("BarlowCondensed-Regular.ttf", "regular")

    total = max(float(data["total_sec"]), 1.0)
    fig = Figure(figsize=(FIG_W_IN, FIG_H_IN), dpi=DPI, facecolor=PAPER)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")

    def text(x, y, s, size, fp, color, ha="left", z=6):
        f = fp.copy()
        f.set_size(size * PT)
        return ax.text(x, y, s, fontproperties=f, color=color, ha=ha, va="center_baseline", zorder=z)

    def fit_text(x, y, s, size, fp, color, max_w, ha="left", min_size=10):
        t = text(x, y, s, size, fp, color, ha=ha)
        renderer = fig.canvas.get_renderer()
        w_units = t.get_window_extent(renderer).width / (FIG_W_IN * DPI) * W
        if w_units > max_w and w_units > 0:
            t.remove()
            t = text(x, y, s, max(min_size, size * max_w / w_units), fp, color, ha=ha)
        return t

    def rbox(x, y, w, h, fc, ec="none", r=14, lw=1.2, z=1):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                    facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))

    # Title bar
    brand = _resolve(LOGOS_DIR / "logo.png")
    if brand.exists():
        _paste(fig, _brand_logo(brand), 98, 84, 96, 96)
    text(170, 52, "ROTATIONS", 22, BOLD, ORANGE)
    title = _title_for(data.get("round_label"))
    text(170, 106, title, min(54, 1700 / max(len(title), 1)), BOLD, NAVY)
    text(W - 50, 52, "EUROLEAGUE", 20, BOLD, GREY, ha="right")
    fit_text(50, 160, "Each bar is one stint on court. Colour and number: team plus/minus while the player was on court.",
             19, REG, GREY, max_w=900)

    # Layout
    teams = data["teams"]
    n_total = sum(len(t["players"]) for t in teams)
    top0, bottom = 190, 1150
    block_fixed = 60 + 8 + 30 + 12
    rh = min(36.0, (bottom - top0 - len(teams) * block_fixed) / max(n_total, 1))
    PX0, PX1 = 190, 930

    def X(sec):
        return PX0 + (PX1 - PX0) * sec / total

    y = top0
    for team in teams:
        colour = COLOR_HOME if team["side"] == "home" else COLOR_AWAY
        players = team["players"]
        n = len(players)

        # Header: logo, name, final score
        lp = LOGO_MAP.get(team["code"])
        lpath = _resolve(LOGOS_DIR / lp) if lp else None
        if lpath is not None and lpath.exists():
            _paste(fig, _prepared_logo(lpath), 78, y + 28, 50, 50)
        fit_text(122, y + 28, team["name"], 30, BOLD, colour, max_w=620)
        if team.get("score") is not None:
            text(950, y + 28, str(int(team["score"])), 44, BOLD, colour, ha="right")

        card_y = y + 60
        card_h = 8 + n * rh + 30
        rbox(50, card_y, 900, card_h, CARD, ec=RULE, r=16, lw=1.0, z=1)
        ax.add_patch(Rectangle((66, card_y), 868, 5, facecolor=colour, edgecolor="none", zorder=3))
        row0 = card_y + 8

        # Period lines and labels
        p = 1
        start = 0
        while start < total:
            length = period_len(p)
            end = min(start + length, total)
            if start > 0:
                if p <= 4 and p != 3:
                    ax.plot([X(start)] * 2, [row0, row0 + n * rh], color=RULE, lw=1.4, zorder=2)
                else:
                    ax.plot([X(start)] * 2, [row0, row0 + n * rh], color=GREY, lw=1.0,
                            ls=(0, (4, 4)), zorder=2)
            label = f"Q{p}" if p <= 4 else f"OT{p - 4}"
            text((X(start) + X(end)) / 2, row0 + n * rh + 16, label, 17, BOLD, GREY, ha="center")
            start = end
            p += 1

        # Rows
        for i, pl in enumerate(players):
            ry = row0 + i * rh
            ax.plot([66, 934], [ry + rh, ry + rh], color=RULE, lw=0.6, zorder=2)
            fit_text(PX0 - 12, ry + rh / 2, pl["name"], min(21, rh * 0.62), SEMI, NAVY,
                     max_w=PX0 - 12 - 62, ha="right")
            for st_, en, pf, pa in pl["bars"]:
                v = int(round(pf - pa))
                bw = max(X(en) - X(st_), 2.0)
                ax.add_patch(Rectangle((X(st_), ry + rh * 0.13), bw, rh * 0.74,
                                       facecolor=pm_color(v), edgecolor=BAR_EDGE, linewidth=0.9, zorder=4))
                if bw >= 34:
                    text(X(st_) + bw / 2, ry + rh / 2, f"{v:+d}" if v else "0",
                         min(18, rh * 0.52), BOLD, NAVY, ha="center", z=5)
        y = card_y + card_h + 12

    # Footer
    ax.plot([50, 950], [1172, 1172], color=NAVY, lw=1.6, zorder=2)
    text(50, 1198, "DataViz By EL_STATSLAB", 25, BOLD, NAVY)
    text(50, 1224, "Insights, Trends, Metrics, Dataviz", 19, SEMI, ORANGE)
    text(950, 1198, "X @EL_Statslab", 25, BOLD, NAVY, ha="right")
    text(950, 1224, "elstatslab.com", 19, REG, GREY, ha="right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=PAPER, dpi=DPI)
    png = buf.getvalue()
    if output_path:
        Path(output_path).write_bytes(png)
        print(f"PNG written: {output_path}")
    return png
