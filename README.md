# TubeFilter

Weekly YouTube digest email. No algorithm, no ads, just videos from channels you choose.

## Setup

### 1. Install dependencies

```bash
cd ~/Desktop/vibes/tubefilter
pip3 install -r requirements.txt
```

### 2. Get a Resend API key

1. Sign up at [resend.dev](https://resend.dev) (free tier: 100 emails/day)
2. Create an API key
3. Export it:

```bash
export RESEND_API_KEY="re_xxxxxxxxxxxxx"
export TUBEFILTER_RECIPIENT="<your-email>"
```

Or add both to `tubefilter/.env` (gitignored):

```
RESEND_API_KEY=re_xxxxxxxxxxxxx
TUBEFILTER_RECIPIENT=<your-email>
```

### 3. Add your channels

Edit `channels.yml`. Each entry needs a `name` and `channel`:

```yaml
channels:
  - name: "Fireship"
    channel: "@Fireship"

  - name: "Some Channel"
    channel: "UCBcRF18a7Qf58cCRy5xuWwQ"  # or use channel ID directly
```

Supported formats for `channel`:
- `@handle` (e.g., `@Fireship`)
- Channel ID (e.g., `UCBcRF18a7Qf58cCRy5xuWwQ`)
- Full URL (e.g., `https://www.youtube.com/@Fireship`)

### 4. Test manually

```bash
python3 tubefilter.py
```

### 5. Set up weekly cron (Friday 4pm)

```bash
crontab -e
```

Add this line (replace the Python path with your own from `which python3`):

```cron
0 16 * * 5 cd ~/Desktop/vibes/tubefilter && /usr/local/bin/python3 tubefilter.py >> ~/.tubefilter/cron.log 2>&1
```

## How it works

1. Reads your channel list from `channels.yml`
2. Resolves @handles to channel IDs (cached after first resolution)
3. Fetches each channel's public RSS feed
4. Filters out videos already sent in previous digests
5. Renders an HTML email grouped by channel
6. Sends via Resend to your configured email
7. Saves sent video IDs to `~/.tubefilter/sent.json`

If no new videos are found, no email is sent.

## Files

| File | Purpose |
|------|---------|
| `tubefilter.py` | Main script |
| `channels.yml` | Your channel list |
| `requirements.txt` | Python dependencies |
| `~/.tubefilter/sent.json` | Tracks sent videos (auto-created) |
