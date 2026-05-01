#!/usr/bin/env python3
"""
UFA Weekly Sync
================
Run once a week to find WatchUFA and YouTube highlight links for any
newly played games, and upsert them directly into the game_links table.

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
import re
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, date, timezone
from xml.etree import ElementTree

from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]

YEAR_FILTER        = 2026
SITEMAP_URL        = "https://www.watchufa.tv/sitemap.xml"
UFA_CHANNEL_ID     = "UCzInURHrtSH7208Mf1HVqUA"
SEARCH_DELAY       = 1.0  # seconds between YouTube API calls

# ─── TEAM DATA ────────────────────────────────────────────────────────────────

# Maps watchufa.tv city slugs → database TeamID
CITY_SLUG_TO_TEAM = {
    "atlanta":      "hustle",
    "austin":       "sol",
    "boston":       "glory",
    "carolina":     "flyers",
    "chicago":      "union",
    "colorado":     "apex",
    "dc":           "breeze",
    "detroit":      "mechanix",
    "houston":      "havoc",
    "indianapolis": "alleycats",
    "las-vegas":    "bighorns",
    "los-angeles":  "aviators",
    "madison":      "radicals",
    "minnesota":    "windchill",
    "montreal":     "royal",
    "new-york":     "empire",
    "oakland":      "spiders",
    "oregon":       "steel",
    "philadelphia": "phoenix",
    "pittsburgh":   "thunderbirds",
    "salt-lake":    "shred",
    "san-diego":    "growlers",
    "seattle":      "cascades",
    "toronto":      "rush",
}

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


def load_played_games():
    """Load all games for YEAR_FILTER that have already been played."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "Year":           f"eq.{YEAR_FILTER}",
        "StartTimestamp": f"lt.{now_iso}",
        "select":         "GameID,HomeTeamID,AwayTeamID,StartTimestamp",
        "order":          "StartTimestamp.asc",
    })
    path = f"/rest/v1/games?{params}"
    games = _supabase_request("GET", path)
    print(f"Loaded {len(games)} played games from database.")
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
    """Upsert a single row into game_links."""
    body = {"GameID": game_id}
    if yt_url is not None:
        body["YT_highlights"] = yt_url
    if watchufa_url is not None:
        body["WatchUFA_full"] = watchufa_url

    _supabase_request("POST", "/rest/v1/game_links", body=body, extra_headers={
        "Prefer": "resolution=merge-duplicates",
    })


# ─── WATCHUFA SITEMAP ─────────────────────────────────────────────────────────

_DATE_SUFFIX        = r"(?:-.+)?$"   # tolerates -remaster, timestamps, or nothing
_TEAMS              = r"^(?P<away>[a-z][a-z-]*)-at-(?P<home>[a-z][a-z-]*)"
_GAME_SLUG_RE       = re.compile(_TEAMS + r"-(?P<month>\d{1,2})-(?P<day>\d{1,2})-(?P<year>\d{4})" + _DATE_SUFFIX)
_GAME_SLUG_RE_ALT   = re.compile(_TEAMS + r"-(?P<month>\d{1,2})-\d{1,2}-(?P<day>\d{1,2})-(?P<year>\d{4})" + _DATE_SUFFIX)


def fetch_sitemap():
    print(f"Downloading sitemap from {SITEMAP_URL} ...")
    req = urllib.request.Request(SITEMAP_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _watchufa_url_priority(url):
    is_week     = bool(re.search(r"/week-\d+/", url))
    is_remaster = "remaster" in url
    if is_week and is_remaster: return 0
    if is_week:                 return 1
    if is_remaster:             return 2
    return 3


def parse_sitemap(xml_bytes):
    """Returns {(away_id, home_id, game_date): url} for all YEAR_FILTER games."""
    root = ElementTree.fromstring(xml_bytes)
    seen_urls = set()
    lookup          = {}
    lookup_priority = {}

    for loc_el in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        url = loc_el.text.strip()
        if url in seen_urls:
            continue
        seen_urls.add(url)

        m = re.search(r"/videos/(.+)$", url)
        if not m:
            continue
        slug = m.group(1)

        match = _GAME_SLUG_RE.match(slug) or _GAME_SLUG_RE_ALT.match(slug)
        if not match:
            continue
        if int(match.group("year")) != YEAR_FILTER:
            continue

        away_id = CITY_SLUG_TO_TEAM.get(match.group("away"))
        home_id = CITY_SLUG_TO_TEAM.get(match.group("home"))
        if not away_id or not home_id:
            continue

        game_date = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        key      = (away_id, home_id, game_date)
        priority = _watchufa_url_priority(url)
        if priority < lookup_priority.get(key, 99):
            lookup[key]          = url
            lookup_priority[key] = priority

    print(f"Parsed {len(lookup)} unique {YEAR_FILTER} games from sitemap.")
    return lookup


def find_watchufa_url(game, sitemap_lookup):
    ts        = datetime.fromisoformat(game["StartTimestamp"])
    utc_date  = ts.date()
    prev_date = utc_date - timedelta(days=1)
    away, home = game["AwayTeamID"], game["HomeTeamID"]
    return (
        sitemap_lookup.get((away, home, utc_date)) or
        sitemap_lookup.get((away, home, prev_date))
    )


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

def _score(title, game):
    t  = title.lower()
    ts = datetime.fromisoformat(game["StartTimestamp"])

    home_match = any(n.lower() in t for n in _all_names(game["HomeTeamID"]))
    away_match = any(n.lower() in t for n in _all_names(game["AwayTeamID"]))
    date_match = any(d.lower() in t for d in [
        _format_date(ts), ts.strftime("%B %d, %Y"),
        ts.strftime("%b %d, %Y").replace(" 0", " "),
        _format_date(ts).upper(),
    ])
    is_highlights = any(w in t for w in [
        "highlight", "full game", "full match",
        "playoffs", "championship", "semifinal", "division",
    ])

    if not is_highlights:              return "LOW"
    if home_match and away_match and date_match: return "HIGH"
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

    games         = load_played_games()
    existing      = load_existing_links()
    sitemap_xml   = fetch_sitemap()
    sitemap       = parse_sitemap(sitemap_xml)

    needs_youtube  = [g for g in games if not existing.get(g["GameID"], {}).get("YT_highlights")]

    print(f"\n{len(games)} games will be checked for WatchUFA links (always re-checked).")
    print(f"{len(needs_youtube)} games need YouTube link.\n")

    # Merge both needs into one pass per game
    all_game_ids = {g["GameID"]: g for g in games}
    to_process   = {g["GameID"] for g in games} | {g["GameID"] for g in needs_youtube}

    yt_found       = 0
    watchufa_found = 0
    quota_hit      = False

    for i, game_id in enumerate(sorted(to_process), 1):
        game         = all_game_ids[game_id]
        existing_row = existing.get(game_id, {})

        want_watchufa = True
        want_youtube  = not existing_row.get("YT_highlights") and not quota_hit

        print(f"[{i}/{len(to_process)}] {game_id}")

        watchufa_url = None
        yt_url       = None

        if want_watchufa:
            watchufa_url = find_watchufa_url(game, sitemap)
            if watchufa_url:
                print(f"  WatchUFA: {watchufa_url}")
                watchufa_found += 1
            else:
                print(f"  WatchUFA: not found in sitemap")

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

        # Only upsert if we found something new
        if watchufa_url or yt_url:
            upsert_game_link(game_id, yt_url=yt_url, watchufa_url=watchufa_url)

    print(f"\nDone!")
    print(f"  WatchUFA links added: {watchufa_found}")
    print(f"  YouTube links added:  {yt_found}")
    if quota_hit:
        print("  YouTube quota was hit — re-run tomorrow to catch remaining games.")


if __name__ == "__main__":
    main()
