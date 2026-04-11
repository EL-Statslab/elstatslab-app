"""
Build a public version of the EuroLeague DB containing only the current season.

Reads the local euroleague.db (full historical archive), extracts the
schedule and team_stats rows for CURRENT_SEASON, and writes them to
euroleague_public.db. This file is what gets pushed to GitHub for the
public Streamlit deployment.

Run this script every time you update euroleague.db with new round data:
    python build_public_db.py

The output euroleague_public.db will be much smaller (a few MB instead of
~127 MB), well within GitHub's 100 MB file limit.
"""

import sqlite3
import shutil
from pathlib import Path

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
        for table in TABLES_TO_COPY:
            print(f"\n📋 Copying table: {table}")

            # Get the CREATE TABLE statement from the source
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

            # Create the table in destination
            dst.execute(create_sql)

            # Copy rows for CURRENT_SEASON
            # Use a parameterised query so the column name varies per table
            season_col = "Season"  # both tables use this column name
            select_sql = f'SELECT * FROM "{table}" WHERE {season_col} = ?'
            rows = src.execute(select_sql, (CURRENT_SEASON,)).fetchall()
            n = len(rows)

            if n == 0:
                print(f"   ⚠️  No rows for season {CURRENT_SEASON}")
                continue

            # Get column count to build the INSERT statement
            placeholders = ",".join(["?"] * len(rows[0]))
            insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'
            dst.executemany(insert_sql, rows)
            dst.commit()
            print(f"   ✅ Copied {n} rows")

        # Vacuum to reduce file size
        print("\n🧹 Vacuuming database to optimise size...")
        dst.execute("VACUUM")
        dst.commit()

        # Final size
        size_mb = OUTPUT_DB.stat().st_size / (1024 * 1024)
        print(f"\n✨ Done! {OUTPUT_DB} is {size_mb:.2f} MB")
        print(f"   GitHub limit is 100 MB, you're well within bounds.")

    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
