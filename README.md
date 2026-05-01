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

## Usage

Run once a week, ideally on Monday or Tuesday after the weekend's games have been played. WatchUFA typically uploads streams within a day or two of each game, and the UFA YouTube channel posts highlights around the same time.

```bash
python ufa_weekly_sync.py
```

- **WatchUFA links** — re-checked and updated every run. Prefers `week-N` collection URLs over team collection URLs, and remaster versions over originals.
- **YouTube links** — only searched for games that don't already have one (HIGH confidence only). YouTube API has a daily quota of ~100 searches, so re-run the next day if quota is hit.

## game_links table

| Column | Description |
|--------|-------------|
| `GameID` | Matches the `games` table |
| `YT_highlights` | YouTube highlight video (HIGH confidence only) |
| `YT_full` | Full game YouTube VOD (not yet populated) |
| `WatchUFA_full` | WatchUFA stream link |
