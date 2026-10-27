"""
ELSTATSLAB | Streamlit app replaced by a single message.

When you are ready: rename this file to app.py in the GitHub repo (the old app.py can be kept
locally as app_old.py). The page shows only the new address, nothing else is loaded.
"""
import streamlit as st

NEW_URL = "https://elstatslab.com/match-center/"

st.set_page_config(page_title="ELSTATSLAB has a new home", page_icon="📊", layout="centered")

st.markdown(
    f"""
<div style="text-align:center;padding:64px 12px 24px 12px;">
  <div style="font-size:38px;font-weight:800;color:#14213D;letter-spacing:.02em;">ELSTATSLAB has a new home</div>
  <div style="font-size:19px;margin-top:14px;opacity:.85;">Match Center, Team Cards, Team Rankings, Lineups, Shot Maps and Guess the Player now live on our own site.</div>
  <div style="margin-top:28px;">
    <a href="{NEW_URL}" target="_blank" rel="noopener" style="display:inline-block;background:#14213D;color:#F3EEE4;padding:14px 30px;border-radius:6px;font-size:20px;font-weight:700;text-decoration:none;">Go to elstatslab.com</a>
  </div>
</div>
""",
    unsafe_allow_html=True,
)
