# UFA YouTube Scraper

Finds YouTube highlight links and WatchUFA stream links for UFA games and writes them to the `game_links` table in Supabase.

## Automated setup (GitHub Actions)

The script runs automatically every Saturday, Sunday, and Monday at 12pm ET via GitHub Actions — no manual runs needed.

**One-time setup:**

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions** and add three repository secrets:
   - `YOUTUBE_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`

That's it. The workflow will run on schedule and can also be triggered manually from the **Actions** tab.

## Local setup

```bash
pip install -r requirements.txt
```

Create a `.env` file:
```
YOUTUBE_API_KEY=...
SUPABASE_URL=...
SUPABASE_KEY=...
```

Then run manually:
```bash
python ufa_weekly_sync.py
```

## How it works

- **WatchUFA links** — re-checked and updated on every run. Prefers `week-N` collection URLs over team collection URLs, and remaster versions over originals.
- **YouTube links** — only searched for games that don't already have one (HIGH confidence only). YouTube API has a daily quota of ~100 searches; re-run the next day if quota is hit.

## game_links table

| Column | Description |
|--------|-------------|
| `GameID` | Matches the `games` table |
| `YT_highlights` | YouTube highlight video (HIGH confidence only) |
| `YT_full` | Full game YouTube VOD (not yet populated) |
| `WatchUFA_full` | WatchUFA stream link |
