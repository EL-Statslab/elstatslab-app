"""
ELSTATSLAB shot maps.

Module pur (matplotlib + pandas), sans Streamlit : utilise par shotmap_ui.py
dans l'app et par le CLI en bas de fichier pour les exports X.
"""
import sqlite3
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib import font_manager
from matplotlib.patches import Circle, Rectangle, Arc, Polygon
from matplotlib.colors import LinearSegmentedColormap, Normalize, to_rgb
from PIL import Image

# ------------------------------------------------------------------ STYLE
BG = "#ffffff"
COURT_BG = "#f5f0e8"
LINE = "#2c2c2c"
TXT = "#333333"
PE = [pe.withStroke(linewidth=4, foreground="white")]

CORNER_X = 660            # FIBA : 0.90 m de la touche (a verifier sur tes donnees)
ARC_R = 675
Y_JOIN = float(np.sqrt(ARC_R**2 - CORNER_X**2))   # jonction arc / ligne droite
RA_R = 125
RA_LABEL = "wing"         # legende de la Restricted Area : "wing" (aile gauche) ou "below" (sous la ligne de fond)
VIEW_Y_MAX = 900          # terrain coupe a cette hauteur (les stats incluent tous les tirs)
MIN_GAMES_KDE = 10        # la heatmap n'apparait qu'a partir de ce nombre de matchs
MIN_GAMES_LEAGUE = 10     # reference ligue utilisee seulement au dela de ce nombre de matchs
# Signalement des matchs dont les corners semblent absents (voir suspect_games_from_df).
# False : aucun avertissement, "No shots" pour une zone vide, et tous les matchs comptent dans la
# reference ligue (les donnees sont celles du flux officiel). True : comportement de controle.
FLAG_SUSPECT_GAMES = False
MIN_THREES_SUSPECT = 20   # match a 3 points >= ce seuil (les 2 equipes) et 0 corner : donnees suspectes
# Matchs sans corner controles contre le shot chart officiel de l'EuroLeague (saison, game code) :
# les donnees de la base reproduisent la source, donc plus d'avertissement pour eux.
VERIFIED_GAMES = {(2026, 23)}
SHRINK_K = 20             # poids (en tirs) de la moyenne ligue dans la couleur des zones

CMAP_HEAT = LinearSegmentedColormap.from_list("el_heat", [
    "#f5f0e8", "#fee5d9", "#fcae91", "#fb6a4a", "#cb181d", "#67000d"])
# rouge = sous la ligue, vert = au dessus
ZONE_BLEND = 0.68         # intensite des couleurs de zone : 0 = blanc, 1 = couleur pleine
ZONE_RANGE = 7            # ecart (en points de FG%) a partir duquel la couleur est maximale
CMAP_ZONE = LinearSegmentedColormap.from_list("el_zone", [
    tuple(np.array(to_rgb(c)) * ZONE_BLEND + np.array(to_rgb("#ffffff")) * (1 - ZONE_BLEND))
    for c in ("#d9534f", "#f1ede4", "#3fae6a")])

COMPETITIONS = {"EuroLeague": "shot_data", "EuroCup": "ec_shot_data", "Super Cup": "sc_shot_data"}
ZONES = ["Restricted Area", "Paint", "Midrange", "Corner 3", "Above the Break 3"]

TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne", "BAR": "FC Barcelona", "BAS": "Baskonia Vitoria-Gasteiz",
    "BES": "Besiktas Istanbul", "DUB": "Dubai Basketball", "HTA": "Hapoel Tel Aviv",
    "IST": "Anadolu Efes Istanbul", "MAD": "Real Madrid", "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan", "MUN": "FC Bayern Munich", "OLY": "Olympiacos Piraeus",
    "PAM": "Valencia Basket", "PAN": "Panathinaikos Athens", "PAR": "Partizan Belgrade",
    "PRS": "Paris Basketball", "RED": "Crvena Zvezda Belgrade", "TEL": "Maccabi Tel Aviv",
    "ULK": "Fenerbahce Istanbul", "VIR": "Virtus Bologna", "ZAL": "Zalgiris Kaunas",
}

# Police des exports ELSTATSLAB (Barlow Condensed), dossier "fonts" a cote du module.
FONTS_DIR = Path(__file__).resolve().parent / "fonts"


def _brand_font(filename, fallback_weight="bold"):
    fp = FONTS_DIR / filename
    if fp.exists():
        return font_manager.FontProperties(fname=str(fp))
    return font_manager.FontProperties(weight=fallback_weight)


F_BOLD = _brand_font("BarlowCondensed-Bold.ttf", "bold")
F_SEMI = _brand_font("BarlowCondensed-SemiBold.ttf", "semibold")
F_REG = _brand_font("BarlowCondensed-Regular.ttf", "regular")


# ------------------------------------------------------------------ DATA
def classify_zone(x, y, is3):
    if is3:
        return "Corner 3" if y < Y_JOIN else "Above the Break 3"
    if np.hypot(x, y) < RA_R:
        return "Restricted Area"
    if abs(x) <= 245 and y <= 423:
        return "Paint"
    return "Midrange"


def load_shots(source, table, season, team=None, game_code=None):
    """Tirs hors lancers francs.

    source : chemin de la base (str ou Path) ou connexion sqlite3 deja ouverte.
    team=None : toute la competition (sert de reference ligue).
    """
    if table not in COMPETITIONS.values():
        raise ValueError(f"Table inconnue : {table}")
    query = f"""
        SELECT GameCode, CODETEAM AS team, COORD_X AS x, COORD_Y AS y, ACTION_ID AS action
        FROM {table}
        WHERE Season = ? AND ACTION_ID NOT IN ('FTM', 'FTA')
    """
    params = [season]
    if team is not None:
        query += " AND CODETEAM = ?"
        params.append(team)
    if game_code is not None:
        query += " AND GameCode = ?"
        params.append(game_code)

    own = isinstance(source, (str, Path))
    conn = sqlite3.connect(str(source)) if own else source
    try:
        df = pd.read_sql_query(query, conn, params=params)
    finally:
        if own:
            conn.close()
    return prepare_shots(df)


def prepare_shots(df):
    """Prepare les tirs. Tous les tirs de champ valides sont conserves, meme ceux hors du terrain
    dessine (lancers de l'autre moitie de terrain, tirs derriere le panneau) : ils comptent dans les
    totaux et dans les zones. Seuls les tirs sans position (x ou y vide) restent hors des zones et
    du dessin, mais comptent dans les totaux."""
    df = df.dropna(subset=["action"]).copy()
    df = df[df["action"].astype(str).str.match(r"^[23]FG[MA]$")].copy()
    df["made"] = df["action"].str.endswith("M")
    df["is3"] = df["action"].str.startswith("3")
    df["located"] = df["x"].notna() & df["y"].notna()
    df["zone"] = [classify_zone(x, y, i) if ok else None
                  for x, y, i, ok in zip(df["x"], df["y"], df["is3"], df["located"])]
    # dans la fenetre de la heatmap (les lancers de loin fausseraient la densite)
    df["in_view"] = df["located"] & df["x"].between(-750, 750) & df["y"].between(-157, 1300)
    return df.reset_index(drop=True)


def zone_stats(df):
    out = pd.DataFrame(index=ZONES, data={"fgm": 0, "fga": 0})
    if len(df):
        g = df.groupby("zone")["made"].agg(["sum", "count"])
        out.loc[g.index, "fgm"] = g["sum"].astype(int)
        out.loc[g.index, "fga"] = g["count"].astype(int)
    out["pct"] = np.where(out["fga"] > 0, out["fgm"] / out["fga"].replace(0, np.nan) * 100, np.nan)
    return out


def suspect_games_from_df(lg):
    """Codes des matchs (les deux equipes ensemble) avec assez de tirs a 3 points mais aucun corner.

    Sur de vraies donnees, environ un tir a 3 points sur huit part du corner : un match sans aucun
    corner sur 20 tirs a 3 points ou plus a presque certainement des coordonnees incompletes.
    """
    if lg.empty:
        return []
    three = lg[lg["is3"]]
    g = three.groupby("GameCode").agg(threes=("zone", "size"),
                                      corners=("zone", lambda z: int((z == "Corner 3").sum())))
    return sorted(int(c) for c in g[(g["threes"] >= MIN_THREES_SUSPECT) & (g["corners"] == 0)].index)


def competition_suspect_games(source, table, season):
    """Matchs suspects a signaler (hors VERIFIED_GAMES)."""
    if not FLAG_SUSPECT_GAMES:
        return []
    return [g for g in suspect_games_from_df(load_shots(source, table, season))
            if (season, g) not in VERIFIED_GAMES]


def suspect_note(games):
    if not FLAG_SUSPECT_GAMES or not games:
        return None
    word = "game" if len(games) == 1 else "games"
    ids = ", ".join(str(g) for g in games)
    return (f"Shot locations look incomplete for {word} {ids} (no corner three recorded). "
            "Zone figures may be inaccurate.")


def league_context(source, table, season):
    """(reference ligue, matchs suspects a signaler) avec une seule lecture de la base."""
    lg = load_shots(source, table, season)
    if lg.empty:
        return None, []
    raw = suspect_games_from_df(lg) if FLAG_SUSPECT_GAMES else []
    kept = lg[~lg["GameCode"].isin(raw)]
    ref = zone_stats(kept) if (not kept.empty and kept["GameCode"].nunique() >= MIN_GAMES_LEAGUE) else None
    shown = [g for g in raw if (season, g) not in VERIFIED_GAMES]
    return ref, shown


def league_reference(source, table, season):
    """FG% par zone sur toute la competition, ou None si moins de MIN_GAMES_LEAGUE matchs.

    Les matchs suspects (voir suspect_games_from_df) sont exclus de la reference."""
    lg = load_shots(source, table, season)
    if lg.empty:
        return None
    if FLAG_SUSPECT_GAMES:
        lg = lg[~lg["GameCode"].isin(suspect_games_from_df(lg))]
    if lg.empty or lg["GameCode"].nunique() < MIN_GAMES_LEAGUE:
        return None
    return zone_stats(lg)


def summary_stats(df):
    two, three = df[~df["is3"]], df[df["is3"]]
    m2, m3 = int(two["made"].sum()), int(three["made"].sum())
    fga = len(df)
    return {"fga": fga, "2pm": m2, "2pa": len(two), "3pm": m3, "3pa": len(three),
            "efg": ((m2 + 1.5 * m3) / fga * 100) if fga else 0.0}


def games_label(n):
    return f"{n} game" if n == 1 else f"{n} games"


PHASE_LABELS = {"PI": "Play-In", "PO": "Playoffs", "FF": "Final Four"}


def match_context(source, season, game_code, team):
    """(libelle de journee, adversaire) d'un match depuis la table schedule, ou None."""
    own = isinstance(source, (str, Path))
    conn = sqlite3.connect(str(source)) if own else source
    try:
        row = conn.execute(
            """SELECT gameday, round, homecode, awaycode, hometeam, awayteam FROM schedule
               WHERE Season = ?
                 AND CAST(SUBSTR(gamecode, INSTR(gamecode, '_') + 1) AS INTEGER) = ?""",
            (season, game_code)).fetchone()
    except Exception:
        return None
    finally:
        if own:
            conn.close()
    if not row:
        return None
    gameday, rnd, hcode, acode, hname, aname = row
    if str(team).upper() == str(hcode).upper():
        opp_code, opp_name = acode, aname
    else:
        opp_code, opp_name = hcode, hname
    label = f"Round {gameday}" if rnd == "RS" else PHASE_LABELS.get(rnd, str(rnd))
    return label, TEAM_DISPLAY_NAMES.get(opp_code, str(opp_name).title())


def available_modes(df):
    """Modes proposes dans l'app : 'heat' seulement a partir de MIN_GAMES_KDE matchs."""
    n = df["GameCode"].nunique() if len(df) else 0
    return ["zones", "heat"] if n >= MIN_GAMES_KDE else ["zones"]


# ------------------------------------------------------------------ COURT
def draw_court(ax, color=LINE, lw=3, ymin=-165):
    ax.set_xlim(-765, 765)
    ax.set_ylim(ymin, VIEW_Y_MAX)
    ax.set_aspect("equal")
    ax.add_patch(Rectangle((-750, -157), 1500, VIEW_Y_MAX + 157, linewidth=0,
                           facecolor=COURT_BG, zorder=0))
    Z = 5
    for xs, ys in [([-750, 750], [-157, -157]), ([-750, -750], [-157, VIEW_Y_MAX]),
                   ([750, 750], [-157, VIEW_Y_MAX])]:
        ax.plot(xs, ys, color=color, lw=lw, zorder=Z)
    ax.plot([-90, 90], [-37, -37], color=color, lw=lw * 1.4, zorder=Z)
    ax.add_patch(Circle((0, 0), 22.75, linewidth=lw, color=color, fill=False, zorder=Z))
    ax.add_patch(Rectangle((-245, -157), 490, 580, linewidth=lw, edgecolor=color,
                           facecolor="none", zorder=Z))
    ax.add_patch(Arc((0, 423), 360, 360, theta1=0, theta2=180, linewidth=lw, color=color, zorder=Z))
    ax.add_patch(Arc((0, 423), 360, 360, theta1=180, theta2=360, linewidth=lw,
                     color=color, linestyle="--", zorder=Z))
    ax.add_patch(Arc((0, 0), RA_R * 2, RA_R * 2, theta1=0, theta2=180, linewidth=lw,
                     color=color, zorder=Z))
    theta = np.degrees(np.arctan2(Y_JOIN, CORNER_X))
    ax.add_patch(Arc((0, 0), ARC_R * 2, ARC_R * 2, theta1=theta, theta2=180 - theta,
                     linewidth=lw, color=color, zorder=Z))
    ax.plot([-CORNER_X, -CORNER_X], [-157, Y_JOIN], color=color, lw=lw, zorder=Z)
    ax.plot([CORNER_X, CORNER_X], [-157, Y_JOIN], color=color, lw=lw, zorder=Z)
    ax.axis("off")


# ------------------------------------------------------------------ LAYERS
def _heat_layer(ax, df):
    from scipy.stats import gaussian_kde   # import local : scipy n'est requis que pour la heatmap
    v = df[df["in_view"]]
    kde = gaussian_kde(np.vstack([v["x"], v["y"]]), bw_method=0.22)
    X, Y = np.meshgrid(np.linspace(-750, 750, 400), np.linspace(-100, VIEW_Y_MAX - 10, 400))
    Zd = kde(np.vstack([X.ravel(), Y.ravel()])).reshape(X.shape)
    Zs = np.sqrt(Zd)   # racine : evite que le cercle sous le panier ecrase le reste
    ax.contourf(X, Y, Zs, levels=np.linspace(Zs.max() * 0.12, Zs.max(), 24),
                cmap=CMAP_HEAT, alpha=0.9, zorder=2)


def _zone_colors(stats, ref_stats):
    """Couleur opaque par zone. Avec ref_stats : ecart au FG% de la ligue dans la meme zone."""
    if ref_stats is not None and SHRINK_K >= 0:
        norm = Normalize(vmin=-ZONE_RANGE, vmax=ZONE_RANGE)
        shrunk = (stats["fgm"] + SHRINK_K * ref_stats["pct"] / 100) / (stats["fga"] + SHRINK_K) * 100
        val = (shrunk - ref_stats["pct"]).where(stats["fga"] > 0)
    else:
        norm = Normalize(vmin=25, vmax=65)
        val = stats["pct"]
    cols = {}
    for z in ZONES:
        v = val[z]
        cols[z] = (0.90, 0.90, 0.90) if np.isnan(v) else tuple(CMAP_ZONE(norm(v))[:3])
    return cols


def _zone_layer(ax, stats, ref_stats):
    col = _zone_colors(stats, ref_stats)
    kw = dict(linewidth=0, alpha=1.0)
    ax.add_patch(Rectangle((-750, -157), 1500, VIEW_Y_MAX + 157,
                           facecolor=col["Above the Break 3"], zorder=1, **kw))
    for x0 in (-750, CORNER_X):
        ax.add_patch(Rectangle((x0, -157), 750 - CORNER_X, 157 + Y_JOIN,
                               facecolor=col["Corner 3"], zorder=1.1, **kw))
    a0 = np.arctan2(Y_JOIN, CORNER_X)
    th = np.linspace(a0, np.pi - a0, 120)
    arc = np.column_stack([ARC_R * np.cos(th), ARC_R * np.sin(th)])
    poly = np.vstack([[CORNER_X, -157], [CORNER_X, Y_JOIN], arc, [-CORNER_X, Y_JOIN], [-CORNER_X, -157]])
    ax.add_patch(Polygon(poly, closed=True, facecolor=col["Midrange"], zorder=1.2, **kw))
    ax.add_patch(Rectangle((-245, -157), 490, 580, facecolor=col["Paint"], zorder=1.3, **kw))
    ax.add_patch(Circle((0, 0), RA_R, facecolor=col["Restricted Area"], zorder=1.4, **kw))


def _layout():
    """Mise en page selon RA_LABEL : (ymin, rect de l'axe, y de la barre de legende, labels, fleches).

    labels : centre du bloc de texte (unites du terrain), legende, taille relative.
    fleches : (depart), (pointe dans la zone), pour les zones trop petites pour porter le texte.
    """
    labels = {
        "Paint": (0, 335, "PAINT", 1.0),
        "Midrange": (-470, 300, "MIDRANGE", 1.0),
        "Corner 3": (445, 40, "CORNER 3", 1.0),
        "Above the Break 3": (0, 790, "ABOVE THE BREAK 3", 1.0),
    }
    arrows = {"Corner 3": ((560, 40), (700, 40))}
    if RA_LABEL == "below":      # sous la ligne de fond, la ou il n'y a jamais de tirs
        labels["Restricted Area"] = (0, -252, "RESTRICTED AREA", 0.85)
        arrows["Restricted Area"] = ((0, -178), (0, -112))
        return -335, [0.05, 0.118, 0.90, 0.667], 0.088, labels, arrows
    # "wing" : aile gauche, symetrique du Corner 3
    labels["Restricted Area"] = (-445, 40, "RESTRICTED AREA", 1.0)
    arrows["Restricted Area"] = ((-340, 40), (-128, 40))
    return -165, [0.05, 0.145, 0.90, 0.63], 0.100, labels, arrows


def _zone_text(ax, zs, k, labels, arrows, empty_text="No shots"):
    for z, (x, y, cap, f) in labels.items():
        row = zs.loc[z]
        empty = int(row["fga"]) == 0
        if cap:
            ax.text(x, y + 66 * f, cap, ha="center", va="center", fontsize=15 * k * f,
                    fontproperties=F_SEMI, color="#555555", zorder=20, path_effects=PE)
        if empty:   # zone sans aucun tir : texte clair plutot que "n/a" et "0/0"
            ax.text(x, y - 4 * f, empty_text, ha="center", va="center", fontsize=20 * k * f,
                    fontproperties=F_BOLD, color="#777777", zorder=20, path_effects=PE)
        else:
            ax.text(x, y, f"{row['pct']:.1f}%", ha="center", va="center", fontsize=32 * k * f,
                    fontproperties=F_BOLD, color="#111111", zorder=20, path_effects=PE)
            ax.text(x, y - 56 * f, f"{int(row['fgm'])}/{int(row['fga'])}", ha="center", va="center",
                    fontsize=18 * k * f, fontproperties=F_SEMI, color="#444444", zorder=20,
                    path_effects=PE)
        if z in arrows:
            (sx, sy), (tx, ty) = arrows[z]
            ax.annotate("", xy=(tx, ty), xytext=(sx, sy),
                        arrowprops=dict(arrowstyle="-|>", color="#555555", lw=2.2 * k), zorder=20)


def _points_layer(ax, df, alpha, k):
    """Reussi : croix verte. Rate : rond rouge."""
    df = df[df["located"]]
    made, missed = df[df["made"]], df[~df["made"]]
    if len(missed):
        ax.scatter(missed["x"], missed["y"], c="#9b2d22", s=40 * k, alpha=alpha, zorder=10,
                   marker="o", edgecolor="white", linewidths=1.0, label="Missed")
    if len(made):
        ax.scatter(made["x"], made["y"], c="#0d5a2f", s=46 * k, alpha=min(1.0, alpha + 0.1),
                   zorder=11, marker="x", linewidths=2.2 * k, label="Made",
                   path_effects=[pe.withStroke(linewidth=4.2 * k, foreground="white")])


def _scale_bar(fig, k, mode, ref_stats, y0=0.100):
    cax = fig.add_axes([0.34, y0, 0.32, 0.013])
    grad = np.linspace(0, 1, 256).reshape(1, -1)
    cax.imshow(grad, aspect="auto", cmap=CMAP_ZONE if mode == "zones" else CMAP_HEAT)
    cax.axis("off")
    if mode == "zones":
        lo, hi = ("Below league average", "Above league average") if ref_stats is not None \
            else ("Low FG%", "High FG%")
    else:
        lo, hi = "Fewer shots", "More shots"
    fig.text(0.33, y0 + 0.0065, lo, ha="right", va="center", fontsize=16 * k,
             fontproperties=F_SEMI, color=TXT)
    fig.text(0.67, y0 + 0.0065, hi, ha="left", va="center", fontsize=16 * k,
             fontproperties=F_SEMI, color=TXT)


LOGO_MAX_PX = 400      # un logo est affiche a ~220 px de large : inutile de garder plus
LOGO_MAX_PIXELS = 25_000_000   # au dela (5000 x 5000), le logo est ignore plutot que de risquer l'app


@lru_cache(maxsize=32)
def _load_logo(path):
    """Logo compose sur fond blanc (RGB uint8), reduit et recadre, ou None.

    Meme principe que les exports de app.py : on aplatit la transparence avant de redimensionner,
    sinon les pixels semi transparents (RGB noir dessous) forment un halo sombre. Le logo est
    reduit des la lecture (LOGO_MAX_PX) et mis en cache : un PNG haute resolution ne coute plus
    des centaines de Mo de memoire a chaque rendu.
    """
    try:
        with Image.open(path) as probe:          # lit seulement l'en-tete : pas de decodage
            w, h = probe.size
        if w * h > LOGO_MAX_PIXELS:
            print(f"[shotmap] logo ignore, trop grand ({w} x {h}) : {path}", flush=True)
            return None
        im = Image.open(path).convert("RGBA")
        im.thumbnail((LOGO_MAX_PX, LOGO_MAX_PX), Image.LANCZOS)
        img = np.asarray(im, dtype=np.float32) / 255.0
    except Exception:
        return None
    alpha = img[:, :, 3]
    alpha = np.where(alpha < 0.12, 0.0, alpha)          # supprime le liseré quasi invisible
    ys, xs = np.where(alpha > 0)
    if len(xs):
        sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
        img, alpha = img[sl], alpha[sl]
    a = alpha[:, :, None]
    rgb = img[:, :, :3] * a + (1.0 - a)
    return (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)


def _place_logo(fig, path, rect):
    if not path or not Path(path).exists():
        return
    img = _load_logo(path)
    if img is None:
        return
    lax = fig.add_axes(rect)
    lax.imshow(img, interpolation="lanczos")
    lax.axis("off")


# ------------------------------------------------------------------ RENDER
def render_shootmap(df, team, subtitle, mode="zones", ref_stats=None, show_points=True,
                    logo_path=None, team_logo_path=None, team_name=None,
                    figsize=(12, 12), watermark=None, note=None):
    """Retourne une Figure carree. 'heat' bascule en 'zones' sous MIN_GAMES_KDE matchs."""
    k = figsize[0] / 12.0
    n_games = df["GameCode"].nunique() if len(df) else 0
    if mode == "heat" and (n_games < MIN_GAMES_KDE or (len(df) and int(df["in_view"].sum()) <= 5)
                           or len(df) <= 5):
        mode = "zones"

    fig = plt.figure(figsize=figsize)
    fig.patch.set_facecolor(BG)
    ymin, rect, sb_y, labels, arrows = _layout()
    ax = fig.add_axes(rect)
    ax.set_facecolor(BG)
    draw_court(ax, ymin=ymin)
    zs = zone_stats(df)

    if mode == "heat":
        _heat_layer(ax, df)
    else:
        _zone_layer(ax, zs, ref_stats)
        _zone_text(ax, zs, k, labels, arrows,
                   empty_text="Not recorded" if note else "No shots")
    if show_points and len(df):
        _points_layer(ax, df, alpha=0.75 if mode == "zones" else 0.8, k=k)
        h, l = ax.get_legend_handles_labels()
        if "Made" in l and "Missed" in l:
            order = [l.index("Made"), l.index("Missed")]
            lp = F_SEMI.copy()
            lp.set_size(22 * k)
            fig.legend([h[i] for i in order], [l[i] for i in order], loc="center",
                       bbox_to_anchor=(0.5, 0.803), ncol=2, frameon=False, prop=lp,
                       labelcolor=TXT, handletextpad=0.4, columnspacing=2.5, markerscale=1.4)

    s = summary_stats(df)
    pct2 = (s["2pm"] / s["2pa"] * 100) if s["2pa"] else 0
    pct3 = (s["3pm"] / s["3pa"] * 100) if s["3pa"] else 0
    name = team_name or TEAM_DISPLAY_NAMES.get(team, team)

    fig.text(0.5, 0.940, name, ha="center", va="center", fontsize=38 * k,
             fontproperties=F_BOLD, color=TXT)
    fig.text(0.5, 0.893, subtitle, ha="center", va="center", fontsize=21 * k,
             fontproperties=F_REG, color="#666666")
    fig.text(0.5, 0.848,
             f"{s['fga']} FGA   |   2P {pct2:.1f}% ({s['2pm']}/{s['2pa']})   |   "
             f"3P {pct3:.1f}% ({s['3pm']}/{s['3pa']})   |   eFG% {s['efg']:.1f}%",
             ha="center", va="center", fontsize=23 * k, fontproperties=F_BOLD, color=TXT)

    _scale_bar(fig, k, mode, ref_stats, sb_y)
    _place_logo(fig, logo_path, [0.035, 0.895, 0.12, 0.085])
    _place_logo(fig, team_logo_path, [0.845, 0.895, 0.12, 0.085])

    n_unloc = int((~df["located"]).sum()) if len(df) else 0
    if n_unloc and not note:
        word = "shot has" if n_unloc == 1 else "shots have"
        fig.text(0.5, 0.080, f"{n_unloc} {word} no recorded location: counted in the totals, not in the zones.",
                 ha="center", va="center", fontsize=14 * k, fontproperties=F_SEMI, color="#777777")
    if note:
        fig.text(0.5, 0.080, note, ha="center", va="center", fontsize=14 * k,
                 fontproperties=F_SEMI, color="#b03a2e")

    # footer identique aux autres exports ELSTATSLAB
    fig.text(0.47, 0.052, "DataViz By EL_STATSLAB", ha="right", va="center",
             fontsize=20 * k, fontproperties=F_BOLD, color="#1a1a1a")
    fig.text(0.5, 0.052, "|", ha="center", va="center", fontsize=20 * k,
             fontproperties=F_REG, color="#bbbbbb")
    fig.text(0.53, 0.052, "Insights, Trends, Metrics, Dataviz", ha="left", va="center",
             fontsize=14 * k, fontproperties=F_SEMI, color="#e8491c")
    fig.text(0.5, 0.022, "X @EL_Statslab   |   elstatslab.com", ha="center", va="center",
             fontsize=14 * k, fontproperties=F_REG, color="#888888")

    if watermark:
        fig.text(0.5, 0.50, watermark, ha="center", va="center", fontsize=60 * k, color="#000",
                 alpha=0.10, rotation=25, fontweight="bold", zorder=100)
    return fig


# fichiers de logos par code equipe (identique a LOGO_MAP de app.py)
TEAM_LOGO_FILES = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "BES": "BJK.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png", "MIL": "AXM.png",
    "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png", "PAN": "PAO.png", "PAR": "PAR.png",
    "PRS": "PBB.png", "RED": "CZV.png", "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png",
    "ZAL": "ZAL.png",
}


# ------------------------------------------------------------------ CLI (export PNG pour X)
if __name__ == "__main__":
    DB_PATH = r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db"
    LOGOS_DIR = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP\Logos")
    LOGO_PATH = LOGOS_DIR / "logo.png"
    OUT_DIR = r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats"

    TEAM = "MAD"
    SEASON = 2026
    COMPET = "Super Cup"      # "EuroLeague", "EuroCup" ou "Super Cup"
    MODE = "zones"            # "zones" ou "heat" (heat seulement a partir de 10 matchs)
    GAME_CODE = None          # un code de match pour un seul match

    table = COMPETITIONS[COMPET]
    data = load_shots(DB_PATH, table, SEASON, TEAM, GAME_CODE)
    if data.empty:
        raise SystemExit(f"Aucun tir pour {TEAM} dans {table} (saison {SEASON}).")
    ref = league_reference(DB_PATH, table, SEASON)
    bad = sorted(set(int(g) for g in data["GameCode"]) & set(competition_suspect_games(DB_PATH, table, SEASON)))
    note = suspect_note(bad)
    if note:
        print("ATTENTION : " + note + " Ne pas publier avant verification.")
    n = data["GameCode"].nunique()
    subtitle = f"{COMPET} {SEASON} | {games_label(n)}"
    if GAME_CODE is not None:
        ctx = match_context(DB_PATH, SEASON, GAME_CODE, TEAM)
        if ctx:
            subtitle = f"{COMPET} {ctx[0]} | vs {ctx[1]}"
    fig = render_shootmap(data, TEAM, subtitle, mode=MODE,
                          ref_stats=ref, logo_path=LOGO_PATH,
                          team_logo_path=LOGOS_DIR / TEAM_LOGO_FILES.get(TEAM, ""), note=note)
    out = rf"{OUT_DIR}\shootmap_{TEAM}_{COMPET.replace(' ', '')}{SEASON}_{MODE}.png"
    fig.savefig(out, dpi=150, facecolor=BG)
    plt.close(fig)
    print(f"OK {out}")
