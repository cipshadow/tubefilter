---
title: "Building a YouTube digest email using RSS feeds + YouTube Data API"
problem_type: knowledge
category: workflow-patterns
tags: [youtube, rss, email, resend, github-actions, cron]
module: tubefilter
component: digest-pipeline
date: 2026-04-05
applies_when: "Building a personal content digest from YouTube channels without scraping or OAuth"
---

# YouTube Digest Email Pipeline

## Context

YouTube's homepage is algorithmically curated for engagement, not for surfacing uploads from channels you follow. Wanted a weekly email digest with only new videos from a curated channel list, no algorithmic noise, no shorts, with video duration shown.

Key constraint: avoid heavy infrastructure (no database, no server, no OAuth). Run as a GitHub Actions cron job.

## Guidance

### Architecture

Single Python script (`tubefilter.py`) with this pipeline:

1. **Channel resolution**: `@handle` -> channel ID via YouTube Data API `channels.list?forHandle=`
2. **Feed fetching**: Public RSS at `youtube.com/feeds/videos.xml?channel_id=`
3. **Shorts detection**: Concurrent HEAD requests to `/shorts/{videoId}`, check if URL stays on `/shorts/` (deterministic) vs redirects to `/watch`
4. **Video enrichment**: YouTube Data API `videos.list?part=contentDetails,statistics,snippet` in batches of 50
5. **Dedup + date filter**: JSON state file tracks sent video IDs + last run timestamp
6. **HTML email**: Inline CSS (email clients strip `<style>` blocks), sent via Resend API
7. **State persistence**: `.state/sent.json` committed back to repo by GitHub Actions

### Channel resolution pitfalls

Scraping YouTube channel pages for channel IDs is unreliable. YouTube pages embed dozens of channel IDs from recommendations, related channels, etc. The EU consent redirect (`consent.youtube.com`) blocks unauthenticated requests from European IPs.

**Use the YouTube Data API instead.** `channels.list?forHandle=` resolves handles correctly every time. One API call per channel, free tier (10K requests/day).

### Shorts detection

Duration-based filtering doesn't work. YouTube Shorts can be up to 3 minutes, and many legitimate short videos are under 60 seconds.

**The deterministic method:** HEAD request to `youtube.com/shorts/{videoId}`. If the final URL contains `/shorts/`, it's a Short. If it redirects to `/watch`, it's a regular video. Use concurrent requests (ThreadPoolExecutor, 10 workers) to keep it fast.

### Email rendering for cross-client compatibility

- All CSS must be inline (no `<style>` block)
- Use `<table>` layout, not flexbox/grid
- Max-width 560px container
- No emoji in body
- `<ul>` with `list-style: none` for clean lists
- Keep personal data (email, API keys) in environment variables, not code

### GitHub Actions as a cron replacement

Local cron on a Mac has issues: machine may be asleep, Stripe Santa may flag outbound requests. GitHub Actions workflow with `schedule` trigger solves both.

State persistence: the workflow commits `.state/sent.json` back to the repo after each run. Needs `permissions: contents: write` in the workflow YAML.

The `[skip ci]` in the commit message prevents infinite loops.

## Why This Matters

- YouTube RSS feeds are free, public, and have been stable for 10+ years
- YouTube Data API free tier (10K requests/day) is more than enough for weekly personal use
- Resend free tier (100 emails/day) handles weekly digests trivially
- GitHub Actions cron runs reliably on any schedule with zero infrastructure
- The whole solution is ~500 lines of Python with 4 dependencies

## When to Apply

- Building any personal content digest (YouTube, RSS, newsletters)
- Need reliable YouTube channel resolution without OAuth
- Need to distinguish Shorts from regular videos programmatically
- Want scheduled email delivery without a server

## Examples

**Channel config (channels.yml):**
```yaml
channels:
  - name: "Kurzgesagt"
    channel: "@kurzgesagt"
  - name: "Coffeezilla"
    channel: "@Coffeezilla"
```

**GitHub Actions schedule (BST-aware):**
```yaml
on:
  schedule:
    - cron: "0 15 * * 5"  # Friday 3pm UTC = 4pm BST
  workflow_dispatch:  # manual trigger
```

**Shorts detection (deterministic):**
```python
def check_short(vid):
    r = requests.head(
        f"https://www.youtube.com/shorts/{vid}",
        allow_redirects=True, timeout=10,
    )
    return "/shorts/" in r.url
```
