"""
Build a public version of the EuroLeague DB containing only the current season.

Reads the local euroleague.db (full historical archive), extracts the
schedule and team_stats rows for CURRENT_SEASON, and writes them to
euroleague_public.db. This file is what gets pushed to GitHub for the
public Streamlit deployment.

Also fetches official standings from the EuroLeague API and stores them
in a standings_official table (rank, home/away record, last5Form, etc.)

Run this script every time you update euroleague.db with new round data:
    python build_public_db.py

The output euroleague_public.db will be much smaller (a few MB instead of
~127 MB), well within GitHub's 100 MB file limit.
"""

import sqlite3
from pathlib import Path

import pandas as pd
from euroleague_api.standings import Standings

# =============================================================================
# CONFIG
# =============================================================================
SOURCE_DB = Path(r"C:\Users\benoi\OneDrive\Bureau\Euroleague_Stats\euroleague.db")
OUTPUT_DB = Path("euroleague_public.db")
CURRENT_SEASON = 2025

# Tables to copy, restricted to the current season
TABLES_TO_COPY = ["schedule", "team_stats"]


def main():
    if not SOURCE_DB.exists():
        print(f"❌ Source DB not found: {SOURCE_DB}")
        return

    # Remove old public DB if exists
    if OUTPUT_DB.exists():
        OUTPUT_DB.unlink()
        print(f"🗑️  Removed old {OUTPUT_DB}")

    # Open source in read only
    src_uri = f"file:{SOURCE_DB}?mode=ro"
    src = sqlite3.connect(src_uri, uri=True)
    dst = sqlite3.connect(OUTPUT_DB)

    try:
        # =====================================================================
        # STEP 1 : Copy schedule and team_stats (current season only)
        # =====================================================================
        for table in TABLES_TO_COPY:
            print(f"\n📋 Copying table: {table}")

            cur = src.cursor()
            cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            row = cur.fetchone()
            if not row:
                print(f"   ⚠️  Table {table} not found in source, skipping")
                continue
            create_sql = row[0]

            dst.execute(create_sql)

            season_col = "Season"
            select_sql = f'SELECT * FROM "{table}" WHERE {season_col} = ?'
            rows = src.execute(select_sql, (CURRENT_SEASON,)).fetchall()
            n = len(rows)

            if n == 0:
                print(f"   ⚠️  No rows for season {CURRENT_SEASON}")
                continue

            placeholders = ",".join(["?"] * len(rows[0]))
            insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'
            dst.executemany(insert_sql, rows)
            dst.commit()
            print(f"   ✅ Copied {n} rows")

        # =====================================================================
        # STEP 2 : Fetch official standings from EuroLeague API
        # =====================================================================
        print("\n📡 Fetching official standings from EuroLeague API...")

        # Find the last fully played round
        last_round = pd.read_sql(
            """
            SELECT MAX(gameday) as r
            FROM schedule
            WHERE Season = ? AND played = 'true'
            """,
            dst,
            params=(CURRENT_SEASON,),
        ).iloc[0]["r"]

        if last_round is None:
            print("   ⚠️  No played rounds found, skipping standings fetch")
        else:
            print(f"   Last played round: {int(last_round)}")
            s = Standings()
            df = s.get_standings(season=CURRENT_SEASON, round_number=int(last_round))

            # Keep only the useful columns
            cols_to_keep = [
                "position", "club.code", "club.name", "club.editorialName",
                "gamesPlayed", "gamesWon", "gamesLost", "winPercentage",
                "pointsDifference", "pointsFor", "pointsAgainst",
                "homeRecord", "awayRecord", "lastTenRecord", "last5Form",
                "qualified",
            ]
            # Only keep columns that actually exist in the response
            cols_to_keep = [c for c in cols_to_keep if c in df.columns]
            df = df[cols_to_keep].copy()

            # Rename to clean snake_case
            rename_map = {
                "position":          "rank",
                "club.code":         "team_code",
                "club.name":         "team_name",
                "club.editorialName":"team_short",
                "gamesPlayed":       "games",
                "gamesWon":          "wins",
                "gamesLost":         "losses",
                "winPercentage":     "win_pct",
                "pointsDifference":  "pt_diff",
                "pointsFor":         "pf",
                "pointsAgainst":     "pa",
                "homeRecord":        "home_record",
                "awayRecord":        "away_record",
                "lastTenRecord":     "last_10",
                "last5Form":         "last_5_form",
                "qualified":         "qualified",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

            # SQLite cannot store Python lists: serialize any list/dict columns to JSON strings
            import json
            for col in df.columns:
                if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                    df[col] = df[col].apply(
                        lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x
                    )

            df.to_sql("standings_official", dst, if_exists="replace", index=False)
            dst.commit()
            print(f"   ✅ Stored {len(df)} teams in standings_official")

        # =====================================================================
        # STEP 3 : Vacuum
        # =====================================================================
        print("\n🧹 Vacuuming database to optimise size...")
        dst.execute("VACUUM")
        dst.commit()

        size_mb = OUTPUT_DB.stat().st_size / (1024 * 1024)
        print(f"\n✨ Done! {OUTPUT_DB} is {size_mb:.2f} MB")
        print(f"   GitHub limit is 100 MB, you're well within bounds.")

    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
