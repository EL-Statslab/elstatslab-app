"""
ELSTATSLAB – gameflow_chart.py (v9)
"""

import sqlite3, json, io
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

APP_ROOT  = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP")
_PUBLIC_DB_LOCAL = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP\euroleague_public.db")
_PUBLIC_DB_CLOUD = Path("euroleague_public.db")

if _PUBLIC_DB_LOCAL.exists():
    PUBLIC_DB = _PUBLIC_DB_LOCAL
elif _PUBLIC_DB_CLOUD.exists():
    PUBLIC_DB = _PUBLIC_DB_CLOUD
else:
    PUBLIC_DB = APP_ROOT / "euroleague_public.db"

LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"

LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png",
    "MIL": "AXM.png", "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png",
    "PAN": "PAO.png", "PAR": "PAR.png", "PRS": "PBB.png", "RED": "CZV.png",
    "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png", "ZAL": "ZAL.png",
}
ZOOM_CORRECTIONS = {
    "ASM": 1.3, "AXM": 1.5, "CZV": 1.6, "EFS": 0.8,
    "FEN": 1.7, "BAR": 0.8, "PAO": 1.1, "VIR": 0.85,
    "PBB": 0.85, "OLY": 0.9, "HTA": 0.8,
}
TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne", "BAR": "FC Barcelona",
    "BAS": "Baskonia Vitoria-Gasteiz", "DUB": "Dubai Basketball",
    "HTA": "Hapoel Tel Aviv", "IST": "Anadolu Efes Istanbul",
    "MAD": "Real Madrid", "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan", "MUN": "FC Bayern Munich",
    "OLY": "Olympiacos Piraeus", "PAM": "Valencia Basket",
    "PAN": "Panathinaikos Athens", "PAR": "Partizan Belgrade",
    "PRS": "Paris Basketball", "RED": "Crvena Zvezda Belgrade",
    "TEL": "Maccabi Tel Aviv", "ULK": "Fenerbahce Istanbul",
    "VIR": "Virtus Bologna", "ZAL": "Zalgiris Kaunas",
}

COLOR_HOME="#1f77b4"; COLOR_AWAY="#d62728"; COLOR_AXIS="#2a2a2a"
COLOR_GRID="#e5e5e5"; COLOR_TEXT="#1a1a1a"; COLOR_SUBTLE="#8a8a8a"
COLOR_BG="#ffffff"; COLOR_QT="#b0b0b0"; COLOR_OT="#d0d0d0"

LOGO_SCALE = 1.0

def dname(c, fb): return TEAM_DISPLAY_NAMES.get(c, fb)
def _logo_zoom(c):
    stem = Path(LOGO_MAP.get(c, "")).stem
    return ZOOM_CORRECTIONS.get(stem, 1.0)
def _logo_path(c):
    fn = LOGO_MAP.get(c)
    if not fn: return None
    p = LOGOS_DIR / fn
    return p if p.exists() else None


def load_gameflow(gc, s):
    conn = sqlite3.connect(str(PUBLIC_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gameflow_data WHERE season=? AND gamecode=?", (s, gc))
    row = cur.fetchone(); conn.close()
    if not row: return None
    d = dict(row)
    d["diff_series"] = json.loads(d["diff_series"])
    d["runs"] = json.loads(d["runs"])
    d["lineups"] = json.loads(d["lineups"])
    d["periods_series"] = json.loads(d["periods_series"]) if d.get("periods_series") else None
    return d


def _qt_bounds(ps):
    if not ps: return []
    b=[]; prev=ps[0]
    for i,p in enumerate(ps):
        if p!=prev: b.append((i,p)); prev=p
    return b


def _draw_team(ax, code, name, x_center):
    """Copie exacte de draw_team_block dans app.py. Aucune modification."""
    base_w, base_h = 0.18, 0.55
    zoom = _logo_zoom(code)
    w = base_w * zoom
    h = base_h * zoom
    w = min(w, 0.28)
    h = min(h, 0.85)
    logo_y = 0.55
    lp = _logo_path(code)
    if lp:
        img = plt.imread(str(lp))
        # Nettoyage alpha : compositer sur fond blanc pour éliminer le bruit
        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3:4]
            rgb = img[:, :, :3]
            white = np.ones_like(rgb)
            img = rgb * alpha + white * (1 - alpha)  # fond blanc
        la = ax.inset_axes([x_center - w/2, logo_y - h/2 + 0.15, w, h])
        la.imshow(img, interpolation="lanczos")
        la.axis("off")
    ax.text(x_center, 0.22, name, ha="center", va="top",
            fontsize=13, fontweight="bold", color=COLOR_TEXT)


def render_gameflow_png(gamecode, season, output_path=None, aspect="square"):
    """
    Génère le PNG du gameflow.
    aspect: "square" (12×12 pour site) ou "16:9" (12×6.75 pour export X)
    """
    data = load_gameflow(gamecode, season)
    if not data:
        raise ValueError(f"Aucun gameflow pour GC {gamecode} saison {season}")

    ds = data["diff_series"]; ps = data["periods_series"]
    runs = data["runs"]; lineups = data["lineups"]
    hc = data["home_code"]; ac = data["away_code"]
    hn = dname(hc, data["home_team"]); an = dname(ac, data["away_team"])
    fh = data["final_home"]; fa = data["final_away"]

    if aspect == "16:9":
        fig = plt.figure(figsize=(12, 6.75), dpi=120, facecolor=COLOR_BG)
        gs = fig.add_gridspec(5, 1,
                              height_ratios=[0.6, 3.0, 0.4, 0.8, 0.3],
                              hspace=0.25,
                              left=0.08, right=0.95, top=0.95, bottom=0.03)
        score_fs = 24; name_fs = 11; run_fs = 8.5; best5_title_fs = 10
        best5_team_fs = 9; best5_stat_fs = 8; best5_players_fs = 8
        footer_fs = 9
    else:
        fig = plt.figure(figsize=(12, 12), dpi=120, facecolor=COLOR_BG)
        gs = fig.add_gridspec(5, 1,
                              height_ratios=[0.8, 3.5, 0.5, 1.0, 0.4],
                              hspace=0.30,
                              left=0.08, right=0.95, top=0.95, bottom=0.03)
        score_fs = 30; name_fs = 13; run_fs = 9.5; best5_title_fs = 11
        best5_team_fs = 10; best5_stat_fs = 9; best5_players_fs = 9
        footer_fs = 11

    # ─── Header ────────────────────────────────────────────────────────────
    ax_h = fig.add_subplot(gs[0])
    ax_h.axis("off"); ax_h.set_xlim(0, 1); ax_h.set_ylim(0, 1)

    _draw_team(ax_h, hc, hn, 0.17)
    ax_h.text(0.5, 0.55, f"{fh}  –  {fa}", ha="center", va="center",
              fontsize=score_fs, fontweight="bold", color=COLOR_TEXT)
    _draw_team(ax_h, ac, an, 0.83)

    # ─── Chart ─────────────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[1])
    ax.set_facecolor(COLOR_BG)
    n = len(ds); x = list(range(n))
    ax.plot(x, ds, color=COLOR_AXIS, linewidth=1.8, zorder=3)
    ax.fill_between(x, ds, 0, where=[d>=0 for d in ds],
                    color=COLOR_HOME, alpha=0.25, interpolate=True, zorder=2)
    ax.fill_between(x, ds, 0, where=[d<=0 for d in ds],
                    color=COLOR_AWAY, alpha=0.25, interpolate=True, zorder=2)
    ax.axhline(0, color=COLOR_SUBTLE, linewidth=0.8, zorder=1)
    ax.set_xlim(0, n-1)
    ymax = max(max(ds),0)+5; ymin = min(min(ds),0)-5
    ax.set_ylim(ymin, ymax)
    ax.set_ylabel("Point differential", fontsize=10, color=COLOR_TEXT)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v,_: f"{int(abs(v))}"))
    ax.set_xticks([]); ax.set_xlabel("Game flow →", fontsize=10, color=COLOR_TEXT)
    ax.yaxis.grid(True, color=COLOR_GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    for s in ["left","bottom"]: ax.spines[s].set_color(COLOR_SUBTLE)

    ax.text(-0.02, 1.02, hc, transform=ax.transAxes,
            ha="right", va="bottom", fontsize=11, color=COLOR_HOME, fontweight="bold")
    ax.text(-0.02, -0.02, ac, transform=ax.transAxes,
            ha="right", va="top", fontsize=11, color=COLOR_AWAY, fontweight="bold")

    for idx_b, period in _qt_bounds(ps):
        if period <= 4:
            ax.axvline(idx_b, color=COLOR_QT, linewidth=1.0, alpha=0.7, zorder=1)
            ax.text(idx_b, ymin+0.5, f"Q{period}", ha="center", va="bottom",
                    fontsize=8, color=COLOR_QT, fontweight="bold")
        else:
            ax.axvline(idx_b, color=COLOR_OT, linewidth=0.8, linestyle="--", alpha=0.5, zorder=1)
            ax.text(idx_b, ymin+0.5, f"OT{period-4}", ha="center", va="bottom",
                    fontsize=7, color=COLOR_OT, style="italic")

    for r in runs:
        si=r["start_idx"]; ei=r["end_idx"]
        c=COLOR_HOME if r["team"]=="home" else COLOR_AWAY
        ax.axvspan(si, ei, color=c, alpha=0.18, zorder=1)
        ax.text((si+ei)/2, ymax-1, f"+{r['pts']}", ha="center", va="top",
                fontsize=10, color=c, fontweight="bold", zorder=5)

    # ─── Biggest runs ──────────────────────────────────────────────────────
    ax_r = fig.add_subplot(gs[2])
    ax_r.set_facecolor(COLOR_BG); ax_r.set_xlim(0,10); ax_r.set_ylim(0,10); ax_r.axis("off")
    if runs:
        sr = sorted(runs, key=lambda r: -r["pts"])[:3]
        parts=[]
        for r in sr:
            code = hc if r["team"]=="home" else ac
            ld=r.get("leader",""); lp_=r.get("leader_pts")
            if lp_ is not None: parts.append(f"{code} +{r['pts']} (led by {ld}, {lp_} pts)")
            else: parts.append(f"{code} +{r['pts']} (led by {ld})")
        ax_r.text(5, 6.5, "Biggest runs :  "+"   |   ".join(parts),
                  ha="center", va="center", fontsize=run_fs, color=COLOR_TEXT)
        ax_r.text(5, 2.5, "A run is a streak of points scored without the opponent scoring.",
                  ha="center", va="center", fontsize=8, color=COLOR_SUBTLE, style="italic")
    else:
        ax_r.text(5,5,"No runs ≥ 9 pts detected", ha="center", va="center",
                  fontsize=9, color=COLOR_SUBTLE, style="italic")

    # ─── Best 5 ────────────────────────────────────────────────────────────
    ax_b = fig.add_subplot(gs[3])
    ax_b.set_facecolor(COLOR_BG); ax_b.set_xlim(0,10); ax_b.set_ylim(0,10); ax_b.axis("off")
    ax_b.text(5, 8.5, "Best 5 by NetRtg", ha="center", va="center",
              fontsize=best5_title_fs, color=COLOR_TEXT, fontweight="bold")
    for lu, xp, tc in [
        (next((l for l in lineups if l["team_code"]==hc),None), 2.5, COLOR_HOME),
        (next((l for l in lineups if l["team_code"]==ac),None), 7.5, COLOR_AWAY),
    ]:
        if not lu: continue
        tl = dname(lu["team_code"], lu["team"])
        ax_b.text(xp,5.5, tl, ha="center", va="center", fontsize=best5_team_fs, color=tc, fontweight="bold")
        ax_b.text(xp,3.5, f"{lu['pts_for']}-{lu['pts_against']}  |  NetRtg {lu['net_rtg']:+.1f}  |  {lu['min']}",
                  ha="center", va="center", fontsize=best5_stat_fs, color=COLOR_TEXT)
        ax_b.text(xp,1.5, " · ".join(lu["players"]),
                  ha="center", va="center", fontsize=best5_players_fs, color=COLOR_TEXT)

# ─── Footer ────────────────────────────────────────────────────────────
    ax_f = fig.add_subplot(gs[4])
    ax_f.axis("off"); ax_f.set_xlim(0,1); ax_f.set_ylim(0,1)
    ax_f.text(0.5, 0.5, "DataViz by  𝕏 @EL_Statslab",
              ha="center", va="center", fontsize=footer_fs, color="#555555", style="italic")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=COLOR_BG, dpi=120)
    buf.seek(0)
    png = buf.read()
    if output_path:
        Path(output_path).write_bytes(png)
        print(f"PNG écrit : {output_path}")
    plt.close(fig)
    return png


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--gamecode", type=int, required=True)
    p.add_argument("--season", type=int, default=2025)
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--aspect", type=str, default="square", choices=["square", "16:9"])
    a = p.parse_args()
    render_gameflow_png(a.gamecode, a.season,
                        output_path=a.output or f"gameflow_E{a.season}_{a.gamecode}.png",
                        aspect=a.aspect)
