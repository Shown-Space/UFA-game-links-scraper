# UFA YouTube Scraper

Finds YouTube highlight links and WatchUFA stream links for UFA games and writes them to the `game_links` table in Supabase.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file:
```
YOUTUBE_API_KEY=...
SUPABASE_URL=...
SUPABASE_KEY=...
```

## Scripts

### `ufa_weekly_sync.py` — run this weekly
Finds missing WatchUFA and YouTube links for any newly played games and upserts them directly into the database. Only adds YouTube links with HIGH confidence.

```bash
python ufa_weekly_sync.py
```

### `ufa_youtube_linker.py` — one-time / manual use
Searches YouTube for highlight videos across all games in a season. Outputs a spreadsheet and SQL file for review before importing.

### `ufa_watchufa_linker.py` — one-time / manual use
Parses the WatchUFA sitemap to find stream URLs for all games in a season. Outputs a spreadsheet and SQL file for review.

### `generate_game_links_sql.py` — one-time / manual use
Merges both spreadsheets into a single SQL file to create and populate the `game_links` table.

## game_links table

| Column | Description |
|--------|-------------|
| `GameID` | Matches the `games` table |
| `YT_highlights` | YouTube highlight video (HIGH confidence only) |
| `YT_full` | Full game YouTube VOD (not yet populated) |
| `WatchUFA_full` | WatchUFA stream link |
