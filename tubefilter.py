#!/usr/bin/env python3
"""TubeFilter — Personal YouTube weekly digest via email."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

import feedparser
import requests
import resend
import yaml

# --- Config ---

# Load .env file if present (for cron)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

CHANNELS_FILE = Path(__file__).parent / "channels.yml"
# Use .state/ in repo dir (for GitHub Actions persistence), fallback to ~/.tubefilter/
_REPO_STATE = Path(__file__).parent / ".state" / "sent.json"
_HOME_STATE = Path.home() / ".tubefilter" / "sent.json"
STATE_FILE = _REPO_STATE if os.environ.get("GITHUB_ACTIONS") else _HOME_STATE
RECIPIENT = os.environ.get("TUBEFILTER_RECIPIENT", "")


# --- Channel Resolution ---

def resolve_channel_id(channel_str: str) -> str | None:
    """Resolve a channel string (ID, @handle, URL) to a YouTube channel ID."""
    channel_str = channel_str.strip()

    # Already a channel ID (starts with UC, 24 chars)
    if re.match(r"^UC[\w-]{22}$", channel_str):
        return channel_str

    # Extract @handle from URL or bare handle
    handle = None
    url_match = re.match(r"https?://(?:www\.)?youtube\.com/(@[\w.-]+)", channel_str)
    if url_match:
        handle = url_match.group(1)
    elif channel_str.startswith("@"):
        handle = channel_str

    # Also handle /channel/UCXXX URLs
    channel_url_match = re.match(
        r"https?://(?:www\.)?youtube\.com/channel/(UC[\w-]{22})", channel_str
    )
    if channel_url_match:
        return channel_url_match.group(1)

    # Resolve handle by fetching the YouTube page
    if handle:
        return _resolve_handle(handle)

    # Try as a custom URL: /c/name or /user/name
    custom_match = re.match(
        r"https?://(?:www\.)?youtube\.com/(?:c|user)/([\w.-]+)", channel_str
    )
    if custom_match:
        return _resolve_handle(f"@{custom_match.group(1)}")

    print(f"  [warn] Cannot parse channel: {channel_str}", file=sys.stderr)
    return None


def _resolve_handle(handle: str) -> str | None:
    """Fetch a YouTube handle page and extract the channel ID."""
    url = f"https://www.youtube.com/{handle}"
    # Cookies to bypass EU consent redirect
    cookies = {
        "SOCS": "CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjUwNDAxLjA1X3AxGgJlbiACGgYIgJCYuwY",
        "CONSENT": "YES+cb.20210328-17-p0.en+FX+999",
    }
    try:
        resp = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
            cookies=cookies,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [warn] Failed to fetch {url}: {e}", file=sys.stderr)
        return None

    # Look for channel ID in meta tags or page data
    # Pattern 1: <meta property="og:url" content="https://www.youtube.com/channel/UCXXX">
    match = re.search(r'"channelId"\s*:\s*"(UC[\w-]{22})"', resp.text)
    if match:
        return match.group(1)

    match = re.search(
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
        resp.text,
    )
    if match:
        return match.group(1)

    match = re.search(r'"externalId"\s*:\s*"(UC[\w-]{22})"', resp.text)
    if match:
        return match.group(1)

    print(f"  [warn] Could not find channel ID for {handle}", file=sys.stderr)
    return None


# --- Feed Fetching ---

def _check_shorts_batch(video_ids: list[str]) -> set[str]:
    """Check which video IDs are Shorts via YouTube Data API. Returns set of Short IDs."""
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("  [warn] YOUTUBE_API_KEY not set, skipping shorts filter", file=sys.stderr)
        return set()

    shorts = set()
    # API supports up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "contentDetails",
                    "id": ",".join(batch),
                    "key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                # Parse ISO 8601 duration (PT#M#S). Shorts are <= 60s.
                duration = item.get("contentDetails", {}).get("duration", "")
                if _is_short_duration(duration):
                    shorts.add(item["id"])
        except requests.RequestException as e:
            print(f"  [warn] YouTube API error: {e}", file=sys.stderr)

    return shorts


def _is_short_duration(duration: str) -> bool:
    """Check if an ISO 8601 duration is <= 60 seconds (i.e., a Short)."""
    # Format: PT1H2M3S, PT5M, PT30S, etc.
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return False
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total <= 60


def fetch_feed(channel_id: str, exclude_shorts: bool = True) -> list[dict]:
    """Fetch the RSS feed for a channel and return a list of video dicts."""
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)

    videos = []
    for entry in feed.entries:
        video_id = entry.get("yt_videoid", "")
        if not video_id:
            # Try to extract from link
            link = entry.get("link", "")
            vid_match = re.search(r"v=([\w-]+)", link)
            video_id = vid_match.group(1) if vid_match else ""

        # Get thumbnail from media namespace
        thumbnail = ""
        media_group = entry.get("media_group", [])
        if media_group:
            for item in media_group:
                if hasattr(item, "get") and item.get("url"):
                    thumbnail = item["url"]
                    break

        # Fallback: standard YouTube thumbnail URL
        if not thumbnail and video_id:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"

        published = entry.get("published", "")
        try:
            pub_date = datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ).strftime("%b %d, %Y")
        except (ValueError, AttributeError):
            pub_date = published[:10] if published else "Unknown"

        videos.append(
            {
                "id": video_id,
                "title": entry.get("title", "Untitled"),
                "url": entry.get("link", f"https://www.youtube.com/watch?v={video_id}"),
                "thumbnail": thumbnail,
                "published": pub_date,
                "published_raw": published,
            }
        )

    if exclude_shorts and videos:
        all_ids = [v["id"] for v in videos if v["id"]]
        short_ids = _check_shorts_batch(all_ids)
        if short_ids:
            videos = [v for v in videos if v["id"] not in short_ids]

    return videos


# --- State Management ---

def load_state() -> dict:
    """Load the state file (sent video IDs and last run timestamp)."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"sent_ids": [], "last_run": None}


def save_state(state: dict):
    """Save the state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Email Rendering ---

def render_email(channels_with_videos: list[dict]) -> str:
    """Render the HTML digest email."""
    total_videos = sum(len(ch["videos"]) for ch in channels_with_videos)
    total_channels = len(channels_with_videos)
    today = datetime.now().strftime("%B %d, %Y")

    video_sections = ""
    for ch in channels_with_videos:
        video_cards = ""
        for v in ch["videos"]:
            video_cards += f"""
            <tr>
              <td style="padding: 12px 0; border-bottom: 1px solid #f0f0f0;">
                <a href="{escape(v['url'], quote=True)}" style="text-decoration: none; color: inherit;">
                  <table cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                      <td width="168" style="vertical-align: top; padding-right: 16px;">
                        <img src="{escape(v['thumbnail'], quote=True)}" width="168" height="94"
                             alt="" style="display: block; border-radius: 6px; object-fit: cover;" />
                      </td>
                      <td style="vertical-align: top;">
                        <p style="margin: 0 0 4px 0; font-size: 15px; font-weight: 600; color: #1a1a1a; line-height: 1.3;">
                          {escape(v['title'])}
                        </p>
                        <p style="margin: 0; font-size: 13px; color: #666;">
                          {escape(v['published'])}
                        </p>
                      </td>
                    </tr>
                  </table>
                </a>
              </td>
            </tr>"""

        video_sections += f"""
        <tr>
          <td style="padding: 24px 0 8px 0;">
            <h2 style="margin: 0; font-size: 16px; font-weight: 700; color: #333; text-transform: uppercase; letter-spacing: 0.5px;">
              {escape(ch['name'])}
            </h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; color: #999;">
              {len(ch['videos'])} new video{'s' if len(ch['videos']) != 1 else ''}
            </p>
          </td>
        </tr>
        <tr>
          <td>
            <table cellpadding="0" cellspacing="0" border="0" width="100%">
              {video_cards}
            </table>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 0; background-color: #fafafa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <table cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>
      <td align="center" style="padding: 32px 16px;">
        <table cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width: 560px;">
          <!-- Header -->
          <tr>
            <td style="padding-bottom: 24px; border-bottom: 2px solid #e0e0e0;">
              <h1 style="margin: 0; font-size: 22px; font-weight: 800; color: #111; letter-spacing: -0.5px;">
                TubeFilter
              </h1>
              <p style="margin: 4px 0 0 0; font-size: 13px; color: #888;">
                Weekly digest &middot; {today}
              </p>
            </td>
          </tr>

          <!-- Channel sections -->
          {video_sections}

          <!-- Footer -->
          <tr>
            <td style="padding: 32px 0 0 0; border-top: 1px solid #e0e0e0;">
              <p style="margin: 0; font-size: 12px; color: #aaa; text-align: center;">
                {total_videos} video{'s' if total_videos != 1 else ''} from {total_channels} channel{'s' if total_channels != 1 else ''}
                &middot; Sent by TubeFilter
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# --- Email Sending ---

def send_email(html: str, video_count: int):
    """Send the digest email via Resend."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[error] RESEND_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not RECIPIENT:
        print("[error] TUBEFILTER_RECIPIENT not set", file=sys.stderr)
        sys.exit(1)

    resend.api_key = api_key
    today = datetime.now().strftime("%b %d")

    resend.Emails.send(
        {
            "from": "TubeFilter <onboarding@resend.dev>",
            "to": [RECIPIENT],
            "subject": f"TubeFilter: {video_count} new video{'s' if video_count != 1 else ''} — {today}",
            "html": html,
        }
    )


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="TubeFilter — YouTube weekly digest")
    parser.add_argument("--days", type=int, default=0,
                        help="Only include videos published in the last N days")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render email but don't send or update state")
    args = parser.parse_args()

    # Load channels
    with open(CHANNELS_FILE) as f:
        config = yaml.safe_load(f)

    channels = config.get("channels", [])
    if not channels:
        print("[info] No channels configured.", file=sys.stderr)
        return

    # Load state
    state = load_state()
    sent_ids = set(state.get("sent_ids", []))

    # Fetch feeds and collect new videos
    channels_with_videos = []
    all_new_ids = []

    for ch in channels:
        name = ch.get("name", "Unknown")
        channel_str = ch.get("channel", "")

        print(f"  Checking {name}...")
        channel_id = resolve_channel_id(channel_str)
        if not channel_id:
            print(f"  [skip] Could not resolve channel ID for {name}", file=sys.stderr)
            continue

        videos = fetch_feed(channel_id)
        new_videos = [v for v in videos if v["id"] and v["id"] not in sent_ids]

        # Filter by publish date if --days is set
        if args.days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
            filtered = []
            for v in new_videos:
                try:
                    pub = datetime.fromisoformat(v["published_raw"].replace("Z", "+00:00"))
                    if pub >= cutoff:
                        filtered.append(v)
                except (ValueError, AttributeError):
                    filtered.append(v)  # include if we can't parse the date
            new_videos = filtered

        # Deduplicate within this batch
        seen = set()
        deduped = []
        for v in new_videos:
            if v["id"] not in seen:
                seen.add(v["id"])
                deduped.append(v)
        new_videos = deduped

        if new_videos:
            channels_with_videos.append({"name": name, "videos": new_videos})
            all_new_ids.extend(v["id"] for v in new_videos)

    # Skip if nothing new (R11)
    if not channels_with_videos:
        print("[info] No new videos. Skipping email.")
        return

    total = sum(len(ch["videos"]) for ch in channels_with_videos)
    print(f"  Found {total} new videos across {len(channels_with_videos)} channels.")

    # Render and send
    html = render_email(channels_with_videos)

    if args.dry_run:
        # Write to file for preview
        out = Path(__file__).parent / "preview.html"
        out.write_text(html)
        print(f"  Dry run: preview written to {out}")
        return

    send_email(html, total)
    print(f"  Digest sent to {RECIPIENT}!")

    # Update state
    state["sent_ids"] = list(sent_ids | set(all_new_ids))
    save_state(state)


if __name__ == "__main__":
    main()
