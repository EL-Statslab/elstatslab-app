"""
team_rankings_export.py

PNG export of the Team Rankings table, made for X.
Style: beige background, dark blue, orange accent, Barlow Condensed.
Cell colours follow the percentile among all teams, with the same five colour steps as the Team Cards.

Use inside the app:
    from team_rankings_export import build_team_rankings_png

Use from the command line. By default it reads euroleague_public.db next to this file,
the same database as the site, so the image matches what visitors see:
    python team_rankings_export.py
    python team_rankings_export.py --db "C:\\path\\euroleague.db" --out ranking.png
"""

import argparse
import io
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Brand settings
# ---------------------------------------------------------------------------

BEIGE = (243, 236, 223)
BEIGE_STRIPE = (236, 228, 213)
NAVY = (27, 42, 65)
NAVY_SOFT = (96, 108, 128)
ORANGE = (232, 73, 28)

PUBLIC_DB = Path(__file__).resolve().parent / "euroleague_public.db"
LOCAL_DB = r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db"

W = 1080
MARGIN = 48
ROW_H = 44
LOGO_BOX_W = 40
LOGO_BOX_H = 36

# key, header label, coloured
COLUMNS = [
    ("net_rtg", "NetRtg", True),
    ("off_rtg", "OffRtg", True),
    ("def_rtg", "DefRtg", True),
    ("pace", "Pace", False),
]

FOUR_FACTORS_COLUMNS = [
    ("net_rtg", "NetRtg", True),
    ("efg", "eFG%", True),
    ("tov", "TOV%", True),
    ("oreb", "OREB%", True),
    ("ftr", "FT Rate", True),
    ("opp_efg", "Opp eFG%", True),
    ("opp_tov", "Opp TOV%", True),
    ("dreb", "DREB%", True),
    ("opp_ftr", "Opp FT Rate", True),
]

# Two images for X. "ratings" is the compact one, "four_factors" shows the rest of the table.
VIEWS = {
    "ratings": {
        "width": 1080,
        "label": "TEAM RANKINGS",
        "subtitle": "{season} regular season, ratings per 100 possessions, ranked by NetRtg",
        "columns": COLUMNS,
        "groups": [],
        "show_gp": True,
        "team_w": 384,
        "head_font": 28,
        "legend": "Colour shows the percentile among all teams, green is better. Pace is not coloured.",
        "file": "team_rankings",
    },
    "four_factors": {
        "width": 1440,
        "label": "FOUR FACTORS",
        "subtitle": "{season} regular season, ranked by NetRtg",
        # (title, first column index, last column index) among the metric columns
        "columns": FOUR_FACTORS_COLUMNS,
        "groups": [("OFFENSE", 1, 4), ("DEFENSE", 5, 8)],
        "show_gp": False,
        "team_w": 340,
        "head_font": 24,
        "legend": ("Colour shows the percentile among all teams, green is better. "
                   "For TOV%, Opp eFG% and Opp FT Rate a lower value is better."),
        "file": "team_rankings_four_factors",
    },
}


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def pct_colors(p):
    """
    Background and text colours for a percentile from 0 (worst) to 100 (best).
    Same five steps as percentile_color in team_cards.py, keep both in sync.
    """
    if p >= 80:
        bg, fg = "#1E8449", "#FFFFFF"
    elif p >= 60:
        bg, fg = "#82C99A", "#153B24"
    elif p >= 40:
        bg, fg = "#F2C94C", "#5C4300"
    elif p >= 20:
        bg, fg = "#F2B8B5", "#5A1F1D"
    else:
        bg, fg = "#D9534F", "#FFFFFF"
    return bg, fg


def pct_rgb(p):
    """Background colour as an RGB tuple."""
    return _hex_to_rgb(pct_colors(float(p))[0])


# ---------------------------------------------------------------------------
# Fonts and assets
# ---------------------------------------------------------------------------

_FONT_FILES = {
    "regular": "BarlowCondensed-Regular.ttf",
    "semibold": "BarlowCondensed-SemiBold.ttf",
    "bold": "BarlowCondensed-Bold.ttf",
}


def _font(weight, size, fonts_dir=None):
    folder = Path(fonts_dir) if fonts_dir else Path(__file__).resolve().parent / "fonts"
    path = folder / _FONT_FILES[weight]
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        try:
            return ImageFont.load_default(size)
        except Exception:
            return ImageFont.load_default()


def _symbol_font(size):
    """Font able to draw the X logo glyph. Returns None if nothing suitable is found."""
    for name in ["seguisym.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Arial Unicode.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return None


def _fit_font(draw, text, weight, size, max_width, fonts_dir):
    """Reduce the size until the text fits in max_width."""
    while size > 14:
        f = _font(weight, size, fonts_dir)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return _font(weight, size, fonts_dir)


def _paste_logo(img, path_or_image, box_h, x, y, max_w=None):
    """Paste a logo with its height set to box_h at (x, y). Returns the width used."""
    try:
        logo = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
        logo = logo.convert("RGBA")
    except Exception:
        return 0
    ratio = box_h / logo.height
    new_w = max(1, round(logo.width * ratio))
    if max_w and new_w > max_w:
        ratio = max_w / logo.width
        new_w = max_w
        box_h = max(1, round(logo.height * ratio))
    logo = logo.resize((new_w, box_h), Image.LANCZOS)
    img.paste(logo, (x, y), logo)
    return new_w


def brand_logo_image(path_or_image):
    """
    ELSTATSLAB logo with a real alpha channel, cropped to its content.
    A logo saved on a white or opaque background gets its white turned into transparency
    (ink colour recovered, no halo), same method as brand_logo_b64 in site_theme.py.
    """
    import numpy as np

    im = path_or_image if isinstance(path_or_image, Image.Image) else Image.open(path_or_image)
    im = im.convert("RGBA")
    im.thumbnail((900, 900), Image.LANCZOS)
    arr = np.asarray(im, dtype=np.float32) / 255.0
    rgb, alpha = arr[:, :, :3], arr[:, :, 3]
    flat = rgb * alpha[:, :, None] + (1.0 - alpha[:, :, None])
    dist = 1.0 - flat.min(axis=2)
    a = np.clip((dist - 0.03) / 0.24, 0.0, 1.0)
    a_safe = np.maximum(a, 1e-3)[:, :, None]
    ink = np.clip((flat - (1.0 - a_safe)) / a_safe, 0.0, 1.0)
    out = np.dstack([ink, a])
    ys, xs = np.where(a > 0.04)
    if len(xs):
        out = out[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return Image.fromarray((out * 255).astype("uint8"), "RGBA")


def prepare_team_logo(img):
    """
    Crop a team logo to its real content, and drop a secondary block (sponsor, text)
    separated from the main drawing by an empty band. Same logic as _autocrop_logo in app.py,
    so every logo has a comparable weight and can be centred in the same box.
    """
    import numpy as np

    img = img.convert("RGBA")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    alpha = arr[:, :, 3]
    ys, xs = np.where(alpha > 0.04)
    if len(xs) == 0:
        return img
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    cropped = arr[y0:y1 + 1, x0:x1 + 1]

    h = cropped.shape[0]
    a2 = cropped[:, :, 3]
    row_has_content = (a2 > 0.04).sum(axis=1)
    threshold = a2.shape[1] * 0.005
    gap_start = None
    for i, count in enumerate(row_has_content):
        if count < threshold:
            if gap_start is None:
                gap_start = i
        else:
            if gap_start is not None:
                gap_h = i - gap_start
                if gap_h > h * 0.015 and gap_start > h * 0.25:
                    cropped = cropped[:gap_start]
                    a3 = cropped[:, :, 3]
                    ys2, xs2 = np.where(a3 > 0.04)
                    if len(xs2):
                        cropped = cropped[ys2.min():ys2.max() + 1, xs2.min():xs2.max() + 1]
                    break
            gap_start = None
    return Image.fromarray((np.clip(cropped, 0, 1) * 255).astype("uint8"), "RGBA")


def _paste_contained(img, logo, cx, cy, box_w, box_h):
    """Paste a logo scaled to fit inside a box, centred on (cx, cy)."""
    try:
        logo = logo.convert("RGBA")
    except Exception:
        return False
    ratio = min(box_w / logo.width, box_h / logo.height)
    new_w = max(1, round(logo.width * ratio))
    new_h = max(1, round(logo.height * ratio))
    logo = logo.resize((new_w, new_h), Image.LANCZOS)
    img.paste(logo, (round(cx - new_w / 2), round(cy - new_h / 2)), logo)
    return True


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

def build_team_rankings_png(
    values,
    pct,
    gp,
    season_label,
    round_label=None,
    names=None,
    brand_logo=None,
    logo_loader=None,
    fonts_dir=None,
    view="ratings",
):
    """
    values, pct, gp come from team_rankings.compute_team_table.
    names is optional: a function that turns a team code into its display name.
    logo_loader is optional: a function that takes a team code and returns a PIL image or None.
    brand_logo: path or image of the ELSTATSLAB logo, its white background is made transparent.
    view: "ratings" (NetRtg, OffRtg, DefRtg, Pace) or "four_factors" (the Four Factors table).
    Returns the PNG as bytes.
    """
    cfg = VIEWS[view]
    W = cfg["width"]
    columns = cfg["columns"]
    groups = cfg["groups"]

    order = list(values["net_rtg"].sort_values(ascending=False).index)
    n = len(order)

    header_h = 196
    group_h = 40 if groups else 0
    col_head_h = 54
    legend_h = 50
    footer_h = 112
    H = header_h + group_h + col_head_h + n * ROW_H + legend_h + footer_h

    img = Image.new("RGB", (W, H), BEIGE)
    d = ImageDraw.Draw(img)

    f = lambda weight, size: _font(weight, size, fonts_dir)

    # Header, same structure as the other exports: transparent ELSTATSLAB logo on the left,
    # visual name in orange, title in dark blue, grey subtitle, EUROLEAGUE in grey on the right.
    logo_h = 104
    logo_w = 0
    if brand_logo is not None:
        try:
            logo = brand_logo_image(brand_logo)
            ratio = logo_h / logo.height
            logo_w = max(1, round(logo.width * ratio))
            if logo_w > 200:
                logo_w = 200
                logo_h_used = max(1, round(logo.height * 200 / logo.width))
            else:
                logo_h_used = logo_h
            logo = logo.resize((logo_w, logo_h_used), Image.LANCZOS)
            img.paste(logo, (MARGIN, 30 + (logo_h - logo_h_used) // 2), logo)
        except Exception:
            logo_w = 0
    text_x = MARGIN + (logo_w + 30 if logo_w else 0)

    d.text((text_x, 66), cfg["label"], font=f("bold", 34), fill=ORANGE, anchor="ls")
    title = f"EuroLeague | {round_label}" if round_label else "EuroLeague"
    d.text((text_x, 128), title, font=f("bold", 68), fill=NAVY, anchor="ls")
    d.text(
        (text_x, 162),
        cfg["subtitle"].format(season=season_label),
        font=f("regular", 24),
        fill=NAVY_SOFT,
        anchor="ls",
    )
    d.text((W - MARGIN, 54), "EUROLEAGUE", font=f("semibold", 34), fill=NAVY_SOFT, anchor="rs")

    # Column geometry
    x0, x1 = MARGIN, W - MARGIN
    rank_w, team_w = 52, cfg["team_w"]
    gp_w = 64 if cfg["show_gp"] else 0
    metric_w = (x1 - x0 - rank_w - team_w - gp_w) / len(columns)
    team_x = x0 + rank_w
    gp_cx = team_x + team_w + gp_w / 2
    metric_cx = [team_x + team_w + gp_w + metric_w * (i + 0.5) for i in range(len(columns))]

    # Group titles above the columns (OFFENSE / DEFENSE)
    for title_, first, last in groups:
        gx0 = metric_cx[first] - metric_w / 2 + 6
        gx1 = metric_cx[last] + metric_w / 2 - 6
        gy = header_h + group_h - 12
        d.text(((gx0 + gx1) / 2, gy - 6), title_, font=f("bold", 26), fill=ORANGE, anchor="ms")
        d.rectangle([gx0, gy, gx1, gy + 2], fill=ORANGE)

    # Table header
    y_head = header_h + group_h + col_head_h / 2 + 4
    hf = f("bold", cfg["head_font"])
    d.text((x0 + 6, y_head), "#", font=hf, fill=NAVY_SOFT, anchor="lm")
    d.text((team_x, y_head), "TEAM", font=hf, fill=NAVY, anchor="lm")
    if cfg["show_gp"]:
        d.text((gp_cx, y_head), "GP", font=hf, fill=NAVY, anchor="mm")
    for cx, (_, label, _) in zip(metric_cx, columns):
        d.text((cx, y_head), label.upper(), font=hf, fill=NAVY, anchor="mm")
    line_y = header_h + group_h + col_head_h
    d.rectangle([x0, line_y - 3, x1, line_y], fill=ORANGE)

    # Rows
    y = line_y + 2
    for i, team in enumerate(order):
        if i % 2 == 0:
            d.rectangle([x0, y, x1, y + ROW_H - 1], fill=BEIGE_STRIPE)
        cy = y + ROW_H / 2

        d.text((x0 + 6, cy), str(i + 1), font=f("semibold", 28), fill=NAVY_SOFT, anchor="lm")

        # Every logo sits centred in the same box, so the logos line up in one column
        # and every team name starts at the same x.
        tx = team_x
        if logo_loader is not None:
            tx = team_x + LOGO_BOX_W + 14
            try:
                logo = logo_loader(team)
            except Exception:
                logo = None
            if logo is not None:
                _paste_contained(img, logo, team_x + LOGO_BOX_W / 2, cy, LOGO_BOX_W, LOGO_BOX_H)
        label = names(team) if names else str(team)
        name_font = _fit_font(d, label, "bold", 31, team_w - (tx - team_x) - 8, fonts_dir)
        d.text((tx, cy), label, font=name_font, fill=NAVY, anchor="lm")

        if cfg["show_gp"]:
            d.text((gp_cx, cy), str(int(gp[team])), font=f("semibold", 28), fill=NAVY_SOFT, anchor="mm")

        for cx, (key, _, coloured) in zip(metric_cx, columns):
            val = values.loc[team, key]
            text_rgb = NAVY
            if coloured:
                bg_hex, fg_hex = pct_colors(float(pct.loc[team, key]))
                text_rgb = _hex_to_rgb(fg_hex)
                d.rounded_rectangle(
                    [cx - metric_w / 2 + 6, y + 4, cx + metric_w / 2 - 6, y + ROW_H - 5],
                    radius=8,
                    fill=_hex_to_rgb(bg_hex),
                )
            d.text((cx, cy), f"{val:.1f}", font=f("bold", 31), fill=text_rgb, anchor="mm")
        y += ROW_H

    # Legend
    ly = y + 26
    d.text((MARGIN, ly), cfg["legend"], font=f("regular", 23), fill=NAVY_SOFT, anchor="lm")

    # Footer
    fy = H - footer_h + 30
    d.line([x0, fy - 14, x1, fy - 14], fill=NAVY_SOFT, width=1)
    d.text(
        (W / 2, fy + 8),
        "DataViz By EL_STATSLAB \u00b7 Insights, Trends, Metrics, Dataviz",
        font=f("semibold", 28),
        fill=ORANGE,
        anchor="mm",
    )

    # Handle line, with the X glyph drawn from a font that has it
    handle = " @EL_Statslab \u00b7 elstatslab.com"
    hfont = f("regular", 26)
    sym = _symbol_font(24)
    glyph = "\U0001D54F" if sym is not None else "X"
    gfont = sym if sym is not None else f("bold", 26)
    gw = d.textlength(glyph, font=gfont)
    tw = d.textlength(handle, font=hfont)
    start = W / 2 - (gw + tw) / 2
    d.text((start, fy + 52), glyph, font=gfont, fill=NAVY, anchor="lm")
    d.text((start + gw, fy + 52), handle, font=hfont, fill=NAVY, anchor="lm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

# Keep in sync with LOGO_MAP and TEAM_DISPLAY_NAMES of app.py
LOGO_MAP = {
    "ASV": "ASV.png", "BAR": "BAR.png", "BAS": "BKN.png", "BES": "BJK.png", "DUB": "DUB.png",
    "HTA": "HTA.png", "IST": "EFS.png", "MAD": "RMD.png", "MCO": "ASM.png",
    "MIL": "AXM.png", "MUN": "BAY.png", "OLY": "OLY.png", "PAM": "VAL.png",
    "PAN": "PAO.png", "PAR": "PAR.png", "PRS": "PBB.png", "RED": "CZV.png",
    "TEL": "MTA.png", "ULK": "FEN.png", "VIR": "VIR.png", "ZAL": "ZAL.png",
}

TEAM_DISPLAY_NAMES = {
    "ASV": "LDLC ASVEL Villeurbanne", "BAR": "FC Barcelona", "BAS": "Baskonia Vitoria-Gasteiz",
    "BES": "Be\u015fikta\u015f Istanbul", "DUB": "Dubai Basketball", "HTA": "Hapoel Tel Aviv",
    "IST": "Anadolu Efes Istanbul", "MAD": "Real Madrid", "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan", "MUN": "FC Bayern Munich", "OLY": "Olympiacos Piraeus",
    "PAM": "Valencia Basket", "PAN": "Panathinaikos Athens", "PAR": "Partizan Belgrade",
    "PRS": "Paris Basketball", "RED": "Crvena Zvezda Belgrade", "TEL": "Maccabi Tel Aviv",
    "ULK": "Fenerbahce Istanbul", "VIR": "Virtus Bologna", "ZAL": "Zalgiris Kaunas",
}


def team_logo_image(code, logos_dir):
    """Team logo cropped to its visible content, or None."""
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    path = Path(logos_dir) / filename
    if not path.exists():
        return None
    try:
        return prepare_team_logo(Image.open(path))
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Team Rankings PNG for X")
    parser.add_argument("--db", default=None, help="database path, default is euroleague_public.db")
    parser.add_argument("--view", choices=["both", "ratings", "four_factors"], default="both",
                        help="which image to make, default both")
    parser.add_argument("--out", default=None, help="output PNG path (only with a single view)")
    args = parser.parse_args()

    import team_rankings as tr

    here = Path(__file__).resolve().parent
    logos = here / "Logos"
    brand = logos / "logo.png"
    print("ELSTATSLAB logo:", brand if brand.exists() else "not found")

    db_path = args.db or (str(PUBLIC_DB) if PUBLIC_DB.exists() else LOCAL_DB)
    print("Database:", db_path)
    conn = sqlite3.connect(db_path)
    try:
        season = tr._current_season(conn)
        if season is None:
            print("No regular season games found.")
            return
        values, pct, gp = tr.compute_team_table(conn, season)
        rnd = tr._last_round(conn, season)
    finally:
        conn.close()

    label = f"Round {rnd}" if rnd else None
    views = ["ratings", "four_factors"] if args.view == "both" else [args.view]
    if args.out and len(views) > 1:
        print("--out needs a single --view, using the default file names instead.")
        args.out = None

    for view in views:
        png = build_team_rankings_png(
            values, pct, gp, tr._season_label(season), label,
            names=lambda c: TEAM_DISPLAY_NAMES.get(c, c),
            brand_logo=brand if brand.exists() else None,
            logo_loader=lambda c: team_logo_image(c, logos),
            fonts_dir=here / "fonts",
            view=view,
        )
        out = args.out or f"{VIEWS[view]['file']}_round_{rnd if rnd else 'x'}.png"
        Path(out).write_bytes(png)
        print("Saved", out)


if __name__ == "__main__":
    main()
