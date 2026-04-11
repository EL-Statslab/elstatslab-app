# ELSTATSLAB Match Center

A EuroLeague matchday explorer that lets you compare any matchup at a glance.

🌐 **Live app:** [elstatslab.com](https://elstatslab.com)
🐦 **Follow on X:** [@EL_Statslab](https://twitter.com/EL_Statslab)

## What it does

For every game of the current EuroLeague season:

- **Head to head comparison** of both teams across season long advanced metrics (ORTG, DRTG, NETRTG, OREB%, DREB%, REB%, AST%)
- **Recent form** view based on the last 5 games
- **Live standings** with rank, win-loss record and form sparkline
- **Win probability** estimate during the regular season, calibrated on EuroLeague historical data
- **Downloadable PNG previews** for sharing on social media

The model is automatically disabled for play-in, playoffs and Final Four matches, where smaller samples and adjusted rotations make pre-game predictions less reliable.

## Tech stack

- **Streamlit** for the UI
- **SQLite** for the data layer
- **pandas** for stat aggregation
- **matplotlib** for the export PNG generation

## About

ELSTATSLAB is an independent EuroLeague analytics project sharing daily insights on X. Built and maintained by Benoit ([@EL_Statslab](https://twitter.com/EL_Statslab)).

Data sourced from official EuroLeague feeds. All metrics are calculated independently.
