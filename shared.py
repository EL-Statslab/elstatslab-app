"""
ELSTATSLAB — Shared config & helpers
=====================================
Constantes et fonctions communes entre app.py (Match Center) et les pages
du dossier pages/ (ex: Team Cards). Centralisé ici pour éviter que
LOGO_MAP / ZOOM_CORRECTIONS / TEAM_DISPLAY_NAMES ne dérivent entre
plusieurs copies au fil du temps.

Ce module ne doit contenir AUCUN appel Streamlit de rendu (st.title,
st.markdown, etc.) au niveau module, seulement des constantes et des
fonctions cache-friendly, pour rester importable en toute sécurité
depuis n'importe quelle page.
"""

import base64
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

# ============================================================
# DB PATH — même logique de fallback que app.py
# ============================================================
_PUBLIC_DB_LOCAL = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\ELSTATSLAB_APP\euroleague_public.db")
_PUBLIC_DB_CLOUD = Path("euroleague_public.db")
_LOCAL_DB = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db")

if _PUBLIC_DB_LOCAL.exists():
    DB_PATH = _PUBLIC_DB_LOCAL
elif _PUBLIC_DB_CLOUD.exists():
    DB_PATH = _PUBLIC_DB_CLOUD
else:
    DB_PATH = _LOCAL_DB

LOGOS_DIR = Path("Logos")
ELSTATSLAB_LOGO = LOGOS_DIR / "logo.png"
EUROLEAGUE_LOGO = LOGOS_DIR / "EL.png"
CURRENT_SEASON = 2025

# ============================================================
# LOGO MAPPING
# ============================================================
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
ROUND_LABELS = {
    "RS": "Regular Season", "PI": "Play-In", "PO": "Playoffs",
    "FF": "Final Four", "TS": "Super Cup",
}


def display_name(code: str, fallback: str) -> str:
    return TEAM_DISPLAY_NAMES.get(code, fallback.title())


def logo_zoom(code: str) -> float:
    filename = LOGO_MAP.get(code, "")
    stem = Path(filename).stem
    return ZOOM_CORRECTIONS.get(stem, 1.0)


def logo_path(code: str) -> Path | None:
    filename = LOGO_MAP.get(code)
    if not filename:
        return None
    p = LOGOS_DIR / filename
    return p if p.exists() else None


@st.cache_data(ttl=3600)
def logo_b64(code: str) -> str | None:
    lp = logo_path(code)
    if not lp:
        return None
    with open(lp, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ============================================================
# DB CONNECTION — connexion read-only mise en cache, réutilisée
# par toutes les pages (évite d'ouvrir/fermer une connexion par
# requête et respecte le mode lecture seule utilisé par app.py)
# ============================================================
@st.cache_resource
def get_conn():
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def read_sql(query: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql(query, get_conn(), params=params)
