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
Pure CSS: no app logic changes, remove the two lines above to go back.
"""

import streamlit as st

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

/* ---- team header: crest on a white badge ---- */
.stApp .stMarkdown img[style*="object-fit: contain"] {{
  background: #fff; padding: 10px; border-radius: 22px; border: 2px solid var(--el-rule);
  box-sizing: content-box;
}}
.stApp .stMarkdown div[style*="height: 140px"] {{ height: auto !important; min-height: 160px; padding: 14px 0 4px; }}
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
