"""
ELSTATSLAB – gameflow_chart.py (v9)
"""

import sqlite3, json, io
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import font_manager
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
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"

# Police de marque (Barlow Condensed). Même dossier fonts/ que app.py ;
# retombe automatiquement sur la police par défaut si les fichiers sont absents.
FONTS_DIR = Path("fonts")


def _brand_font(filename: str, fallback_weight: str = "bold") -> font_manager.FontProperties:
    fp = FONTS_DIR / filename
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


BARLOW_BOLD     = _brand_font("BarlowCondensed-Bold.ttf", "bold")
BARLOW_SEMIBOLD = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
BARLOW_REGULAR  = _brand_font("BarlowCondensed-Regular.ttf", "regular")


def _autocrop_logo(img_arr):
    """
    Recadre une image RGBA sur son contenu réel :
    1) coupe la marge transparente autour du dessin
    2) si un bloc secondaire (logo sponsor, texte) est séparé du dessin
       principal par un vrai espace vide, ne garde que le premier bloc
       (le blason/l'écusson), pas le sponsor en dessous.
    Corrige les logos avec beaucoup de marge (ex: CZV) ou un sponsor
    intégré au fichier (ex: FEN) sans réglage manuel par équipe.
    """
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
                # Un vrai espace de séparation : assez haut, et pas collé au
                # tout début (sinon on risque de couper le blason lui même).
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


def _fit_text(ax, x, y, text, fontsize, max_width_in, fontproperties, color,
              ha="center", va="center", min_fontsize=8.5):
    """
    Dessine un texte et, s'il dépasse la largeur disponible (mesurée sur le
    vrai rendu, pas estimée), réduit automatiquement sa taille pour qu'il
    tienne. Remplace les tailles fixes qui cassent selon la longueur réelle
    des noms d'un match à l'autre.
    """
    fig = ax.figure
    t = ax.text(x, y, text, ha=ha, va=va, fontsize=fontsize,
                fontproperties=fontproperties, color=color)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width_in = t.get_window_extent(renderer=renderer).width / fig.dpi
    if width_in > max_width_in and width_in > 0:
        new_fs = max(min_fontsize, fontsize * (max_width_in / width_in))
        t.remove()
        t = ax.text(x, y, text, ha=ha, va=va, fontsize=new_fs,
                    fontproperties=fontproperties, color=color)
    return t

LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "BES": "BJK.png", "DUB": "DUB.png",
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
    """Logo recadré automatiquement sur son contenu réel (voir _autocrop_logo),
    donc plus besoin de facteur de zoom manuel par équipe : chaque logo est
    normalisé à la même taille de base, peu importe la marge du fichier source."""
    base_w, base_h = 0.24, 0.85
    logo_y = 0.60
    lp = _logo_path(code)
    if lp:
        img = plt.imread(str(lp))
        w, h = base_w, base_h
        if img.ndim == 3 and img.shape[2] == 4:
            img = _autocrop_logo(img)
            # Nettoyage alpha : compositer sur fond blanc pour éliminer le bruit
            alpha = img[:, :, 3:4]
            rgb = img[:, :, :3]
            white = np.ones_like(rgb)
            img = rgb * alpha + white * (1 - alpha)  # fond blanc
        la = ax.inset_axes([x_center - w/2, logo_y - h/2 + 0.15, w, h])
        la.imshow(img, interpolation="lanczos")
        la.axis("off")
    ax.text(x_center, 0.14, name, ha="center", va="top",
            fontsize=13, fontproperties=BARLOW_BOLD, color=COLOR_TEXT)


def render_gameflow_png(gamecode, season, round_label="", output_path=None, aspect="square"):
    """
    Génère le PNG du gameflow.
    aspect: "square" (12×12 pour site) ou "16:9" (12×6.75 pour export X)
    round_label: ex. "Regular Season Round 3", affiché dans le bandeau de titre
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
        gs = fig.add_gridspec(6, 1,
                              height_ratios=[0.55, 0.85, 2.9, 0.85, 1.0, 0.3],
                              hspace=0.25,
                              left=0.08, right=0.95, top=0.95, bottom=0.03)
        title_fs = 15
        score_fs = 24; name_fs = 11; run_fs = 11.5; best5_title_fs = 13
        best5_team_fs = 12; best5_stat_fs = 10.5; best5_players_fs = 11.5
        footer_fs = 9
    else:
        fig = plt.figure(figsize=(12, 12), dpi=120, facecolor=COLOR_BG)
        gs = fig.add_gridspec(6, 1,
                              height_ratios=[0.6, 1.3, 3.3, 1.0, 1.2, 0.6],
                              hspace=0.30,
                              left=0.08, right=0.95, top=0.95, bottom=0.03)
        title_fs = 17
        score_fs = 30; name_fs = 13; run_fs = 13.5; best5_title_fs = 15
        best5_team_fs = 13.5; best5_stat_fs = 12; best5_players_fs = 13.5
        footer_fs = 11

    # ─── Bandeau de titre (logo ELSTATSLAB + round + logo EuroLeague) ──────
    ax_title = fig.add_subplot(gs[0])
    ax_title.axis("off"); ax_title.set_xlim(0, 1); ax_title.set_ylim(0, 1)

    if ELSTATSLAB_LOGO.exists():
        brand_ax = ax_title.inset_axes([-0.075, -0.3, 0.16, 1.6])
        brand_ax.imshow(plt.imread(str(ELSTATSLAB_LOGO)), interpolation="lanczos")
        brand_ax.axis("off")
    if EUROLEAGUE_LOGO.exists():
        el_ax = ax_title.inset_axes([0.84, -0.1, 0.16, 1.2])
        el_ax.imshow(plt.imread(str(EUROLEAGUE_LOGO)), interpolation="lanczos")
        el_ax.axis("off")

    title_text = f"Game Flow  |  EuroLeague {round_label}".strip()
    ax_title.text(0.50, 0.5, title_text, ha="center", va="center",
                  fontsize=title_fs, fontproperties=BARLOW_BOLD, color=COLOR_TEXT)

    # ─── Header (logos équipes + score) ─────────────────────────────────────
    ax_h = fig.add_subplot(gs[1])
    ax_h.axis("off"); ax_h.set_xlim(0, 1); ax_h.set_ylim(0, 1)

    _draw_team(ax_h, hc, hn, 0.17)
    ax_h.text(0.5, 0.55, f"{fh}  –  {fa}", ha="center", va="center",
              fontsize=score_fs, fontweight="bold", color=COLOR_TEXT)
    _draw_team(ax_h, ac, an, 0.83)

    # ─── Chart ─────────────────────────────────────────────────────────────
    ax = fig.add_subplot(gs[2])
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
    ax_r = fig.add_subplot(gs[3])
    ax_r.set_facecolor(COLOR_BG); ax_r.set_xlim(0,10); ax_r.set_ylim(0,10); ax_r.axis("off")
    ax_r.text(5, 9.5, "BIGGEST RUNS", ha="center", va="center",
              fontsize=best5_title_fs, fontproperties=BARLOW_BOLD, color=COLOR_TEXT)
    if runs:
        sr = sorted(runs, key=lambda r: -r["pts"])[:3]
        n_runs = len(sr)
        row_h_run = 2.4
        y0 = 5.1 + (n_runs - 1) * row_h_run / 2
        for i, r in enumerate(sr):
            y = y0 - i * row_h_run
            code = hc if r["team"] == "home" else ac
            c = COLOR_HOME if r["team"] == "home" else COLOR_AWAY
            ld = r.get("leader", ""); lp_ = r.get("leader_pts")
            detail = f"led by {ld}" + (f", {lp_} pts" if lp_ is not None else "")

            chip = plt.matplotlib.patches.FancyBboxPatch(
                (0.4, y - row_h_run * 0.34), 9.2, row_h_run * 0.66,
                boxstyle="round,pad=0.02", facecolor="#f5f6f7", edgecolor="none",
            )
            ax_r.add_patch(chip)
            accent = plt.matplotlib.patches.FancyBboxPatch(
                (0.4, y - row_h_run * 0.32), 0.10, row_h_run * 0.62,
                boxstyle="round,pad=0.005", facecolor=c, edgecolor="none",
            )
            ax_r.add_patch(accent)

            ax_r.text(1.1, y, f"{code} +{r['pts']}", ha="left", va="center",
                      fontsize=run_fs + 2.5, fontproperties=BARLOW_BOLD, color=c)
            _fit_text(ax_r, 9.1, y, detail, fontsize=run_fs + 1.5, max_width_in=6.0,
                      fontproperties=BARLOW_SEMIBOLD, color="#1a1a1a", ha="right", va="center")
        ax_r.text(5, 0.3, "A run is a streak of points scored without the opponent scoring.",
                  ha="center", va="center", fontsize=8, fontproperties=BARLOW_REGULAR,
                  color=COLOR_SUBTLE, style="italic")
    else:
        ax_r.text(5, 5, "No runs ≥ 9 pts detected", ha="center", va="center",
                  fontsize=10, fontproperties=BARLOW_REGULAR, color=COLOR_SUBTLE, style="italic")

    # ─── Best 5 ────────────────────────────────────────────────────────────
    ax_b = fig.add_subplot(gs[4])
    ax_b.set_facecolor(COLOR_BG); ax_b.set_xlim(0,10); ax_b.set_ylim(0,10); ax_b.axis("off")
    ax_b.text(5, 9.5, "BEST 5 BY NETRTG", ha="center", va="center",
              fontsize=best5_title_fs, fontproperties=BARLOW_BOLD, color=COLOR_TEXT)
    for lu, x0, tc in [
        (next((l for l in lineups if l["team_code"]==hc),None), 0.3, COLOR_HOME),
        (next((l for l in lineups if l["team_code"]==ac),None), 5.2, COLOR_AWAY),
    ]:
        if not lu: continue
        xp = x0 + 2.25
        card = plt.matplotlib.patches.FancyBboxPatch(
            (x0, 0.6), 4.5, 7.6,
            boxstyle="round,pad=0.03", facecolor="#f5f6f7", edgecolor="none",
        )
        ax_b.add_patch(card)
        top_accent = plt.matplotlib.patches.FancyBboxPatch(
            (x0, 7.75), 4.5, 0.16,
            boxstyle="round,pad=0.005", facecolor=tc, edgecolor="none",
        )
        ax_b.add_patch(top_accent)

        tl = dname(lu["team_code"], lu["team"])
        ax_b.text(xp, 6.9, tl, ha="center", va="center",
                  fontsize=best5_team_fs, fontproperties=BARLOW_BOLD, color=tc)
        ax_b.text(xp, 5.5, f"{lu['pts_for']}-{lu['pts_against']}   ·   NetRtg {lu['net_rtg']:+.1f}   ·   {lu['min']}",
                  ha="center", va="center", fontsize=best5_stat_fs, fontproperties=BARLOW_SEMIBOLD, color=COLOR_TEXT)

        # 5 joueurs sur une seule ligne : taille mesurée sur le vrai rendu,
        # réduite automatiquement si la liste est trop longue pour la carte
        # (plutôt qu'une taille fixe qui déborde selon les noms du match).
        _fit_text(ax_b, xp, 2.7, "  ·  ".join(lu["players"]),
                  fontsize=best5_players_fs + 1, max_width_in=4.1,
                  fontproperties=BARLOW_SEMIBOLD, color="#1a1a1a", ha="center", va="center")

# ─── Footer ────────────────────────────────────────────────────────────
    ax_f = fig.add_subplot(gs[5])
    ax_f.axis("off"); ax_f.set_xlim(0,1); ax_f.set_ylim(0,1)
    ax_f.text(0.47, 0.64, "DataViz By EL_STATSLAB", ha="right", va="center",
              fontsize=footer_fs + 4, fontproperties=BARLOW_BOLD, color="#1a1a1a")
    ax_f.text(0.5, 0.64, "·", ha="center", va="center",
              fontsize=footer_fs + 4, color="#bbbbbb")
    ax_f.text(0.53, 0.64, "Insights, Trends, Metrics, Dataviz", ha="left", va="center",
              fontsize=footer_fs - 0.5, fontproperties=BARLOW_SEMIBOLD, color="#e8491c")
    ax_f.text(0.5, 0.24, "𝕏 @EL_Statslab   ·   elstatslab.com",
              ha="center", va="center", fontsize=footer_fs - 0.5,
              fontproperties=BARLOW_REGULAR, color="#888888")

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
    p.add_argument("--round-label", type=str, default="")
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--aspect", type=str, default="square", choices=["square", "16:9"])
    a = p.parse_args()
    render_gameflow_png(a.gamecode, a.season, round_label=a.round_label,
                        output_path=a.output or f"gameflow_E{a.season}_{a.gamecode}.png",
                        aspect=a.aspect)
