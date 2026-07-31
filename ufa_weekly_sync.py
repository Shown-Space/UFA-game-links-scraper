#!/usr/bin/env python3
"""
UFA Weekly Sync
================
Run once a week to find WatchUFA and YouTube highlight links for any
newly played games, and upsert them directly into the game_links table.

WatchUFA links come from the official UFA API (streamingURL per game),
keyed by the same gameID we store. YouTube highlight links are found by
searching the UFA YouTube channel (no API exists for these).

SETUP:
  pip install google-api-python-client python-dotenv

.env file needs:
  YOUTUBE_API_KEY=...
  SUPABASE_URL=...
  SUPABASE_KEY=...

USAGE:
  python ufa_weekly_sync.py
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]

YEAR_FILTER    = 2026
UFA_API_BASE   = "https://www.backend.ufastats.com/api/v1"
UFA_CHANNEL_ID = "UCzInURHrtSH7208Mf1HVqUA"
SEARCH_DELAY   = 1.0  # seconds between YouTube API calls

# ─── TEAM DATA ────────────────────────────────────────────────────────────────

# Maps database TeamID → names used in YouTube search + title matching
TEAM_NAMES = {
    "hustle":       ["Atlanta Hustle", "Hustle"],
    "sol":          ["Austin Sol", "Sol"],
    "glory":        ["Boston Glory", "Glory"],
    "flyers":       ["Carolina Flyers", "Flyers"],
    "union":        ["Chicago Union", "Union"],
    "apex":         ["Colorado Apex", "Apex", "Colorado Summit", "Summit"],
    "breeze":       ["DC Breeze", "Breeze"],
    "mechanix":     ["Detroit Mechanix", "Mechanix"],
    "havoc":        ["Houston Havoc", "Havoc"],
    "alleycats":    ["Indianapolis AlleyCats", "AlleyCats", "Indy AlleyCats"],
    "bighorns":     ["Vegas Bighorns", "Bighorns", "Las Vegas Bighorns"],
    "aviators":     ["Los Angeles Aviators", "Aviators"],
    "radicals":     ["Madison Radicals", "Radicals"],
    "windchill":    ["Minnesota Wind Chill", "Wind Chill", "Windchill"],
    "royal":        ["Montreal Royal", "Royal"],
    "empire":       ["New York Empire", "Empire"],
    "spiders":      ["Oakland Spiders", "Spiders"],
    "steel":        ["Oregon Steel", "Steel"],
    "phoenix":      ["Philadelphia Phoenix", "Phoenix"],
    "thunderbirds": ["Pittsburgh Thunderbirds", "Thunderbirds"],
    "shred":        ["Salt Lake Shred", "Shred"],
    "growlers":     ["San Diego Growlers", "Growlers"],
    "cascades":     ["Seattle Cascades", "Cascades"],
    "rush":         ["Toronto Rush", "Rush"],
}

# ─── SUPABASE HELPERS ─────────────────────────────────────────────────────────

def _supabase_request(method, path, body=None, extra_headers=None):
    url = SUPABASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as r:
        body = r.read()
        return json.loads(body) if body else {}


def load_games():
      """Load all games for YEAR_FILTER (played AND upcoming)."""
      params = urllib.parse.urlencode({
          "Year":   f"eq.{YEAR_FILTER}",
          "select": "GameID,HomeTeamID,AwayTeamID,StartTimestamp",
          "order":  "StartTimestamp.asc",
      })
      path = f"/rest/v1/games?{params}"
      # games lives in the `prod` schema (same as the website). Without this
      # header PostgREST reads the stale default/public.games and misses the
      # most recent games, so their WatchUFA links never get written.
      games = _supabase_request("GET", path, extra_headers={"Accept-Profile": "prod"})
      print(f"Loaded {len(games)} games from database (played + upcoming).")
      return games


def load_existing_links():
    """Load current game_links table so we know what's already filled."""
    path = (
        f"/rest/v1/game_links"
        f"?select=GameID,YT_highlights,WatchUFA_full"
        f"&GameID=like.{YEAR_FILTER}*"
    )
    try:
        rows = _supabase_request("GET", path)
        links = {r["GameID"]: r for r in rows}
        print(f"Found {len(links)} existing game_links rows.")
        return links
    except Exception:
        print("game_links table not found or empty — will insert fresh.")
        return {}


def upsert_game_link(game_id, yt_url=None, watchufa_url=None):
    """Upsert a single row into game_links (partial — only sets provided columns)."""
    body = {"GameID": game_id}
    if yt_url is not None:
        body["YT_highlights"] = yt_url
    if watchufa_url is not None:
        body["WatchUFA_full"] = watchufa_url

    _supabase_request("POST", "/rest/v1/game_links", body=body, extra_headers={
        "Prefer": "resolution=merge-duplicates",
    })


# ─── UFA API (WatchUFA streaming links) ───────────────────────────────────────

def fetch_streaming_urls(year):
    """Fetch {gameID: streamingURL} for every game in `year` from the UFA API.

    Replaces the old watchufa.tv sitemap scraping — the API keys streamingURL
    by the exact gameID we already store, so no slug/date matching is needed.
    """
    params = urllib.parse.urlencode({"date": str(year)})
    req = urllib.request.Request(
        f"{UFA_API_BASE}/games?{params}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())

    lookup = {
        g["gameID"]: g["streamingURL"]
        for g in payload.get("data", [])
        if g.get("streamingURL")
    }
    print(f"Fetched {len(lookup)} streaming URLs from UFA API.")
    return lookup


# ─── YOUTUBE ──────────────────────────────────────────────────────────────────

def _format_date(ts):
    return ts.strftime("%B %d, %Y").replace(" 0", " ")

def _all_names(team_id):
    return TEAM_NAMES.get(team_id, [team_id])

def _primary_name(team_id):
    return _all_names(team_id)[0]

def _build_query(game):
    ts   = datetime.fromisoformat(game["StartTimestamp"])
    away = _primary_name(game["AwayTeamID"])
    home = _primary_name(game["HomeTeamID"])
    return f"{away} at {home} highlights {_format_date(ts)} UFA"

def _date_variants(ts):
    """Lowercased date strings a title might use, over a ±1 day window so
    late-night / timezone-shifted uploads still match."""
    out = set()
    for delta in (-1, 0, 1):
        d = ts + timedelta(days=delta)
        out.add(d.strftime("%B %d, %Y"))                     # July 05, 2026
        out.add(d.strftime("%B %d, %Y").replace(" 0", " "))  # July 5, 2026
        out.add(d.strftime("%b %d, %Y"))                     # Jul 05, 2026
        out.add(d.strftime("%b %d, %Y").replace(" 0", " "))  # Jul 5, 2026
        out.add(f"{d.month}/{d.day}/{d.year}")               # 7/5/2026
        out.add(f"{d.month}/{d.day}/{str(d.year)[2:]}")      # 7/5/26
    return {s.lower() for s in out}


def _score(title, game):
    t  = title.lower()
    ts = datetime.fromisoformat(game["StartTimestamp"])

    home_match = any(n.lower() in t for n in _all_names(game["HomeTeamID"]))
    away_match = any(n.lower() in t for n in _all_names(game["AwayTeamID"]))
    date_match = any(d in t for d in _date_variants(ts))
    is_highlights = any(w in t for w in [
        "highlight", "full game", "full match",
        "playoffs", "championship", "semifinal", "division",
    ])

    # An exact matchup + date on the official channel is definitive, even when the
    # title has no "highlights" keyword — playoff VODs are titled e.g.
    # "LIVE Playoffs | DC Breeze vs New York Empire | July 25, 2026".
    if home_match and away_match and date_match: return "HIGH"
    if not is_highlights:              return "LOW"
    if home_match and away_match:      return "MEDIUM"
    return "LOW"


def find_youtube_url(youtube, game):
    """Returns a YouTube URL only if a HIGH confidence match is found."""
    from googleapiclient.errors import HttpError
    query = _build_query(game)
    try:
        response = youtube.search().list(
            q=query,
            part="snippet",
            type="video",
            maxResults=5,
            order="relevance",
            channelId=UFA_CHANNEL_ID,
        ).execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise  # quota exceeded — let caller handle
        return None, query

    rank   = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    scored = sorted(
        [(_score(item["snippet"]["title"], game), item["id"]["videoId"])
         for item in response.get("items", [])],
        key=lambda x: rank.get(x[0], 99),
    )

    if scored and scored[0][0] == "HIGH":
        video_id = scored[0][1]
        return f"https://www.youtube.com/watch?v={video_id}", query

    return None, query


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    now       = datetime.now(timezone.utc)
    games     = load_games()
    existing  = load_existing_links()
    streaming = fetch_streaming_urls(YEAR_FILTER)

    all_game_ids = {g["GameID"]: g for g in games}

    print(f"\n{len(all_game_ids)} games will be checked for WatchUFA links (played + upcoming).\n")

    yt_found       = 0
    watchufa_found = 0
    quota_hit      = False

    for i, game_id in enumerate(sorted(all_game_ids), 1):
        game         = all_game_ids[game_id]
        existing_row = existing.get(game_id, {})

        # A game is "played" if its start time is in the past.
        ts = datetime.fromisoformat(game["StartTimestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        is_played = ts < now

        # WatchUFA: any game the API has a URL for (incl. upcoming previews).
        # YouTube:  only played games still missing a highlight link.
        want_youtube = is_played and not existing_row.get("YT_highlights") and not quota_hit

        print(f"[{i}/{len(all_game_ids)}] {game_id}{'' if is_played else '  (upcoming)'}")

        watchufa_url = streaming.get(game_id)
        yt_url       = None

        if watchufa_url:
            print(f"  WatchUFA: {watchufa_url}")
            watchufa_found += 1
        else:
            print(f"  WatchUFA: not found in UFA API")

        if want_youtube:
            try:
                yt_url, query = find_youtube_url(youtube, game)
                if yt_url:
                    print(f"  YouTube:  {yt_url}")
                    yt_found += 1
                else:
                    print(f"  YouTube:  no HIGH confidence match")
                time.sleep(SEARCH_DELAY)
            except HttpError as e:
                if e.resp.status == 403:
                    print("  YouTube:  quota exceeded — skipping remaining YouTube searches.")
                    quota_hit = True
                else:
                    raise

        if watchufa_url or yt_url:
            upsert_game_link(game_id, yt_url=yt_url, watchufa_url=watchufa_url)

    print(f"\nDone!")
    print(f"  WatchUFA links added: {watchufa_found}")
    print(f"  YouTube links added:  {yt_found}")
    if quota_hit:
        print("  YouTube quota was hit — re-run tomorrow to catch remaining games.")


if __name__ == "__main__":
    main()
