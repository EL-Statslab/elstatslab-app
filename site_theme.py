"""
ELSTATSLAB site theme: the same paper look as the PNG exports.

Warm paper background, deep navy text, orange accent, Barlow Condensed.
Comparison cells keep the red/green gradient (data colours stay untouched);
orange is only used for brand accents and for the away side of the win
probability bar, exactly like the exports.

Usage in app.py, right after st.set_page_config(...):

    from site_theme import apply_theme
    apply_theme()

Also put .streamlit/config.toml at the root of the repo (base colours).
Logos: badge_logo_b64(path) returns a cropped, square, white background version
of a team logo (sponsor block removed, same size footprint for every club), so all
crests look alike inside the badge. See the logo_b64 replacement in app.py.
"""

import base64
import io
from functools import lru_cache
from pathlib import Path

import numpy as np
import streamlit as st
from PIL import Image

PAPER = "#F3EEE4"
CARD = "#FBF8F1"
RULE = "#E1D8C6"
NAVY = "#14213D"
ORANGE = "#E4572E"
GREY = "#6B7280"

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;500;600;700&display=swap');

:root {{
  --el-paper: {PAPER}; --el-card: {CARD}; --el-rule: {RULE};
  --el-navy: {NAVY}; --el-orange: {ORANGE}; --el-grey: {GREY};
}}

/* ---- page ---- */
.stApp {{ background: var(--el-paper); color: var(--el-navy); }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.2rem; }}
hr {{ border-color: var(--el-rule) !important; }}

/* ---- fonts (icons keep their own font) ---- */
.stApp, .stApp p, .stApp li, .stApp label, .stApp span:not([data-testid="stIconMaterial"]),
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5,
.stApp .stMarkdown div, .stApp td, .stApp th, .stApp button, .stApp input, .stApp textarea,
.stApp [data-baseweb="select"] div, .stApp [data-baseweb="tab"] {{
  font-family: 'Barlow Condensed', sans-serif !important;
}}
.stApp .stMarkdown p, .stApp .stMarkdown li {{ font-size: 1.12rem; }}
.stApp .stMarkdown strong {{ font-weight: 700; font-size: 1.3rem; letter-spacing: .01em; }}
.stApp h1 {{ font-weight: 700; font-size: 2.8rem; letter-spacing: -.01em; color: var(--el-navy); }}
.stApp h2 {{ font-weight: 700; font-size: 2.1rem; color: var(--el-navy); }}
.stApp h3 {{ font-weight: 700; font-size: 1.6rem; color: var(--el-navy); }}

/* ---- tabs ---- */
.stApp button[role="tab"] p {{ font-size: 1.25rem; font-weight: 600; }}
.stApp button[role="tab"][aria-selected="true"] {{ color: var(--el-orange); }}
.stApp [data-baseweb="tab-highlight"] {{ background-color: var(--el-orange) !important; }}
.stApp [data-baseweb="tab-border"] {{ background-color: var(--el-rule) !important; }}

/* ---- inputs and buttons ---- */
.stApp div[data-baseweb="select"] > div,
.stApp div[data-baseweb="input"] > div {{
  background: var(--el-card); border-color: var(--el-rule); border-radius: 10px;
}}
.stApp .stButton > button, .stApp .stDownloadButton > button {{
  background: var(--el-card); color: var(--el-navy);
  border: 1.5px solid var(--el-navy); border-radius: 10px;
  font-weight: 600; font-size: 1.1rem;
}}
.stApp .stButton > button:hover, .stApp .stDownloadButton > button:hover {{
  border-color: var(--el-orange); color: var(--el-orange);
}}
.stApp .stButton > button[kind="primary"] {{
  background: var(--el-orange); border-color: var(--el-orange); color: #fff;
}}

/* ---- comparison tables (render_comparison_styled). Selectors match the browser serialised inline styles. ---- */
.stApp .stMarkdown table {{
  background: #fff !important; border-radius: 14px; overflow: hidden;
  border: 1px solid var(--el-rule);
}}
.stApp .stMarkdown table th[style*="rgb(102, 102, 102)"] {{
  color: var(--el-grey) !important; font-size: 1.05rem !important; font-weight: 600 !important;
  border-bottom: 2px solid var(--el-navy) !important; background: #fff;
}}
.stApp .stMarkdown table td {{ font-size: 1.2rem !important; border-bottom: 1px solid var(--el-rule) !important; }}
.stApp .stMarkdown table td[style*="rgb(26, 26, 26)"] {{ color: var(--el-navy) !important; font-size: 1.35rem !important; }}
.stApp .stMarkdown table td[style*="rgb(245, 245, 245)"] {{
  background: var(--el-card) !important; color: var(--el-navy) !important;
  font-weight: 600; letter-spacing: .02em;
}}
.stApp .stMarkdown table td[style*="rgb(136, 136, 136)"] {{ color: var(--el-grey) !important; }}

/* ---- crests: one fixed badge size everywhere (logos come from badge_logo_b64) ---- */
.stApp .stMarkdown img[style*="object-fit: contain"] {{
  max-width: none !important; max-height: none !important;
  object-fit: contain; box-sizing: border-box;
  background: #fff; border-radius: 20px; border: 2px solid var(--el-rule); padding: 8px;
}}
.stApp .stMarkdown div[style*="height: 140px"] img {{ width: 128px; height: 128px; }}
.stApp .stMarkdown div[style*="height: 72px"] img {{ width: 80px; height: 80px; border-radius: 16px; padding: 6px; }}

/* ---- team header ---- */
.stApp .stMarkdown div[style*="height: 140px"] {{ height: auto !important; min-height: 140px; padding: 14px 0 4px; }}
.stApp .stMarkdown div[style*="height: 72px"] {{ height: 88px !important; }}
.stApp .stMarkdown div[style*="height: 20px"][style*="text-align: center"] {{ height: auto !important; margin-top: 10px !important; }}
.stApp .stMarkdown div[style*="font-weight: bold"][style*="font-size: 1rem"] {{
  font-size: 1.55rem !important; font-weight: 700 !important; color: var(--el-navy); min-height: 40px !important;
}}
.stApp .stMarkdown div[style*="rgb(136, 136, 136)"][style*="font-size: 0.85rem"] {{
  font-size: 1.15rem !important; color: var(--el-grey) !important; height: auto !important;
}}

/* ---- win probability bar: navy (home) vs orange (away), as in the export ---- */
.stApp .stMarkdown div[style*="flex: "][style*="background: rgb(46, 160, 67)"] {{ background: var(--el-navy) !important; }}
.stApp .stMarkdown div[style*="flex: "][style*="background: rgb(218, 54, 51)"] {{ background: var(--el-orange) !important; }}
.stApp .stMarkdown div[style*="justify-content: space-between"] > span[style*="color: rgb(46, 160, 67)"] {{
  color: var(--el-navy) !important; font-size: 1.35rem; font-weight: 700;
}}
.stApp .stMarkdown div[style*="justify-content: space-between"] > span[style*="color: rgb(218, 54, 51)"] {{
  color: var(--el-orange) !important; font-size: 1.35rem; font-weight: 700;
}}
</style>
"""


def apply_theme() -> None:
    """Injects the ELSTATSLAB paper theme. Call once, right after set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


# =============================================================================
# LOGOS
# =============================================================================
def _autocrop(img: np.ndarray) -> np.ndarray:
    """Trims transparent margins and drops a sponsor block separated from the crest
    by a real empty band (same rule as _autocrop_logo in app.py)."""
    alpha = img[:, :, 3]
    ys, xs = np.where(alpha > 0.04)
    if len(xs) == 0:
        return img
    cropped = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h = cropped.shape[0]
    row_has = (cropped[:, :, 3] > 0.04).sum(axis=1)
    threshold = cropped.shape[1] * 0.005
    gap_start = None
    for i, v in enumerate(row_has):
        if v < threshold:
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


@lru_cache(maxsize=64)
def badge_logo_b64(path: str) -> str:
    """Square 240 px PNG, crest cropped and centred on white, base64 encoded.
    Every club gets the same footprint, so logos look consistent on the site."""
    im = Image.open(Path(path)).convert("RGBA")
    im.thumbnail((900, 900), Image.LANCZOS)
    arr = _autocrop(np.asarray(im, dtype=np.float32) / 255.0)
    a = arr[:, :, 3:4]
    rgb = arr[:, :, :3] * a + (1.0 - a)
    crest = Image.fromarray((rgb * 255).astype("uint8"), "RGB")
    box = 240
    inner = int(box * 0.86)
    crest.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGB", (box, box), "white")
    canvas.paste(crest, ((box - crest.width) // 2, (box - crest.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
