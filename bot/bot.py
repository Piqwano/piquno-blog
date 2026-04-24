"""
Piquno Japan Ski Blog - Auto-posting Bot v2
=============================================
Runs daily on Railway. Generates:
  1. A daily roundup covering all Japan ski resorts by region
  2. A standalone feature article (gear, guides, travel tips)

Then deploys to Netlify and shares to Bluesky + X/Twitter.

Environment variables (required):
  ANTHROPIC_API_KEY    - Your Anthropic API key
  NETLIFY_AUTH_TOKEN   - Netlify personal access token
  NETLIFY_SITE_ID      - Your Netlify site ID
  UNSPLASH_ACCESS_KEY  - Unsplash API access key (free at unsplash.com/developers)

Environment variables (optional):
  CLAUDE_MODEL         - Defaults to claude-sonnet-4-6
  SITE_URL             - Defaults to https://piquno.com (useful for staging)
  BLUESKY_HANDLE       - e.g. piqunoski.bsky.social
  BLUESKY_APP_PASSWORD - App password (NOT your login password)
  TWITTER_API_KEY      - OAuth 1.0a consumer key
  TWITTER_API_SECRET
  TWITTER_ACCESS_TOKEN
  TWITTER_ACCESS_SECRET
"""

import os
import json
import re
import time
import html
import random
import secrets
import hashlib
import hmac
import base64
import shutil
import logging
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import zipfile

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NETLIFY_AUTH_TOKEN = os.environ["NETLIFY_AUTH_TOKEN"]
NETLIFY_SITE_ID = os.environ["NETLIFY_SITE_ID"]
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")  # Update when new models release

SITE_URL = os.environ.get("SITE_URL", "https://piquno.com").rstrip("/")
SITE_NAME = "Piquno"
SITE_LANG = "en-AU"
SITE_TAGLINE = "Japan Ski Journal"

# Shared HTTP session with sensible retries for transient 5xx / 429.
# Used for GET requests everywhere, plus the POSTs that are idempotent for our
# purposes (Claude completions are safe to re-call, Netlify deploys are idempotent
# at the resource level, Unsplash search returns the same result set).
_retry = Retry(
    total=4,
    connect=3,
    read=3,
    backoff_factor=1.5,  # 0, 1.5, 3, 6s between tries
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "HEAD", "POST"]),
    raise_on_status=False,
)
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=10, pool_maxsize=10)
http = requests.Session()
http.mount("https://", _adapter)
http.mount("http://", _adapter)
http.headers.update({"User-Agent": "PiqunoBot/2.0 (+https://piquno.com)"})

# Separate session for NON-IDEMPOTENT writes (Bluesky createRecord, Twitter POST /tweets).
# Retrying a transient 5xx here could publish the same post twice, so we only retry
# idempotent methods and let the caller decide how to handle write failures.
_strict_retry = Retry(
    total=2,
    connect=2,
    read=2,
    backoff_factor=1.0,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "HEAD"]),  # POSTs are NOT retried
    raise_on_status=False,
)
_strict_adapter = HTTPAdapter(max_retries=_strict_retry, pool_connections=5, pool_maxsize=5)
http_strict = requests.Session()
http_strict.mount("https://", _strict_adapter)
http_strict.mount("http://", _strict_adapter)
http_strict.headers.update({"User-Agent": "PiqunoBot/2.0 (+https://piquno.com)"})

# ---------------------------------------------------------------------------
# Complete Japan Ski Resort Database
# ---------------------------------------------------------------------------

RESORTS = {
    "Hokkaido": {
        "major": [
            "Niseko United (Grand Hirafu, Hanazono, Niseko Village, Annupuri)",
            "Rusutsu",
            "Kiroro",
            "Furano",
            "Tomamu (Hoshino Resorts)",
            "Sahoro",
            "Kamui Ski Links",
            "Teine (Highland & Olympia)",
            "Asahidake",
            "Kurodake",
        ],
        "minor": [
            "Moiwa (Niseko)",
            "Kokusai (Sapporo)",
            "Bankei (Sapporo)",
            "Fu's Snow Area (Sapporo)",
            "Nayoro Piyashiri",
            "Asarigawa Onsen",
            "Nakayama Toge",
            "Pippu",
            "Santa Present Park (Abashiri)",
            "Kami-Sunagawa",
            "Mount Racey",
            "Iwanai Resort",
            "Nakafurano",
            "Yoichi",
            "Shimamaki Catski",
        ],
    },
    "Tohoku": {
        "major": [
            "Appi Kogen",
            "Zao Onsen (Yamagata)",
            "Zao Sumikawa (Miyagi)",
            "Tazawako",
            "Hakkoda",
            "Aomori Spring (Ajigasawa)",
            "Ani",
        ],
        "minor": [
            "Gassan",
            "Tengendai Kogen",
            "Okutadami Maruyama",
            "Geto Kogen",
            "Iwate Kogen Snow Park",
            "Yakeishi Dake",
            "Bandai (Inawashiro / Alts Bandai / Nekoma)",
            "Yonezawa",
            "Shinjo Kanmuriyama",
            "Chokai Kogen Yashima",
            "Miyagi Izumigatake",
            "Moriyoshi",
        ],
    },
    "Nagano": {
        "major": [
            "Hakuba (Happo-One, Goryu, Hakuba 47, Cortina, Tsugaike, Iwatake, Kashimayari, Norikura)",
            "Shiga Kogen (21 linked resorts)",
            "Nozawa Onsen",
            "Madarao Kogen",
            "Tangram Ski Circus",
        ],
        "minor": [
            "Togakushi",
            "Iizuna Kogen",
            "Ryuoo",
            "X-JAM Takai",
            "Yamaboku Wild Snow Park",
            "Ontake 2240",
            "Kiso Fukushima",
            "Tateshina",
            "Kurumayama Kogen",
            "Karuizawa Prince Hotel",
            "Sugadaira Kogen",
            "Minenohara",
            "Asahi Prime",
        ],
    },
    "Niigata": {
        "major": [
            "Myoko Kogen (Akakura Onsen, Akakura Kanko, Suginohara, Ikenotaira, Seki Onsen)",
            "Lotte Arai Resort",
            "Naeba (Prince Hotels)",
            "Kagura / Mitsumata / Tashiro",
            "Gala Yuzawa",
            "Yuzawa Kogen",
            "Ishiuchi Maruyama",
            "Muikamachi Hakkaisan",
            "Joetsu Kokusai",
            "Charmant Hiuchi",
            "Maiko Snow Resort",
        ],
        "minor": [
            "NASPA Ski Garden",
            "Cupol",
            "Matsunoyama Onsen",
            "Norn Minakami (Gunma border)",
            "Echigo-Yuzawa station resorts",
            "Tsuchitaru",
            "Kandatsu Kogen",
        ],
    },
    "Central Honshu (Gifu / Toyama / Ishikawa / Fukui)": {
        "major": [
            "Takasu Snow Park (Gifu)",
            "Dynaland (Gifu)",
            "Washigatake (Gifu)",
            "Ski Jam Katsuyama (Fukui)",
        ],
        "minor": [
            "Hirugano Kogen",
            "Meiho",
            "Winghills Shirotori",
            "Whitepia Takasu",
            "IOX-Arosa (Toyama)",
            "Taira (Toyama)",
            "Hakusan Seymour (Ishikawa)",
            "Ichirino Onsen (Ishikawa)",
            "Tateyama (spring only)",
        ],
    },
    "Kanto (Tochigi / Gunma)": {
        "major": [
            "Hunter Mountain Shiobara (Tochigi)",
            "Kawaba (Gunma)",
            "Tambara (Gunma)",
            "Marunuma Kogen (Gunma)",
            "Oze-Iwakura (Gunma)",
            "Kusatsu Onsen (Gunma)",
            "Palcall Tsumagoi (Gunma)",
        ],
        "minor": [
            "Norn Minakami (Gunma)",
            "Hodaigi (Gunma)",
            "Mount Jeans Nasu (Tochigi)",
            "Edelweiss (Tochigi)",
            "Fujimi Panorama (Nagano border)",
        ],
    },
    "Western Honshu & Shikoku": {
        "major": [
            "Daisen (Tottori)",
            "Hakodateyama (Shiga)",
            "Biwako Valley (Shiga)",
        ],
        "minor": [
            "Hyonosen (Hyogo)",
            "Hachi Kita Kogen (Hyogo)",
            "Osorakan (Hiroshima)",
            "Geihoku Kokusai (Hiroshima)",
            "Mizuho Highland (Shimane)",
            "Gokase Highland (Miyazaki - southernmost ski area)",
            "Sol-Fa Oda (Shimane)",
        ],
    },
}

# Flatten resort names for keyword matching
ALL_RESORT_NAMES = []
for region, tiers in RESORTS.items():
    for tier_list in tiers.values():
        for name in tier_list:
            ALL_RESORT_NAMES.append(name)

JAPAN_KEYWORDS = list(set([
    "japan", "japow", "hokkaido", "honshu", "nagano", "niigata", "tohoku",
    "japanese ski", "japan snow", "japan powder", "japan resort",
] + [
    name.split("(")[0].strip().split("/")[0].strip().lower()
    for name in ALL_RESORT_NAMES
    if len(name.split("(")[0].strip()) > 3
]))

RSS_FEEDS = [
    # Active feeds verified April 2026. If any start returning 4xx/5xx repeatedly,
    # check the publisher's site for a new URL or drop them.
    "https://snowbrains.com/feed/",
    "https://unofficialnetworks.com/feed/",
    "https://www.tetongravity.com/feed/",       # direct (skips an old 2-hop redirect)
    "https://snowjapan.com/feed",
    "https://www.japantimes.co.jp/feed/",       # broad Japan news; bot filters for ski content
]

FEATURE_TAGS = ["Resort Guide", "Gear", "Travel", "Culture", "Planning"]

TEMPLATE_DIR = Path("/tmp/site")
POSTS_DIR = TEMPLATE_DIR / "posts"
DATA_FILE = POSTS_DIR / "index.json"
SEEN_FILE = Path("/tmp/seen_urls.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("piquno-bot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slug_from_title(title: str) -> str:
    """Turn a title into a URL slug, truncating on word boundary to ~75 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(slug) <= 75:
        return slug
    # Truncate at last hyphen before the 75-char cutoff so we don't cut mid-word
    cut = slug.rfind("-", 0, 75)
    if cut <= 0:  # pathological case (single long token)
        cut = 75
    return slug[:cut].rstrip("-")


def fetch_hero_image(query: str) -> dict | None:
    """Fetch a royalty-free hero image from Pexels (much better variety than Unsplash)."""
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        log.info("No Pexels API key found, skipping hero image")
        return None

    try:
        r = http.get(
            "https://api.pexels.com/v1/search",
            params={
                "query": query,
                "per_page": 8,
                "orientation": "landscape",
            },
            headers={"Authorization": api_key},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("photos", [])

        if not results:
            r2 = http.get(
                "https://api.pexels.com/v1/search",
                params={"query": "japan powder skiing mountain", "per_page": 8, "orientation": "landscape"},
                headers={"Authorization": api_key},
                timeout=15,
            )
            r2.raise_for_status()
            results = r2.json().get("photos", [])

        if not results:
            log.warning("No images found even with fallback query")
            return None

        import random
        photo = random.choice(results)

        src = photo["src"]
        return {
            "url": src["large2x"],
            "srcset": f"{src['large']} 1024w, {src['large2x']} 1280w, {src['original']} 1920w",
            "alt": photo.get("alt", "Japan skiing"),
            "credit": f"Photo by {photo['photographer']} on Pexels",
            "credit_url": photo["photographer_url"],
        }

    except Exception as e:
        log.warning(f"Pexels image fetch failed: {e}")
        return None


def fetch_existing_site():
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = http.get(f"{SITE_URL}/posts/index.json", timeout=10)
        if r.status_code == 200:
            posts = r.json()
            DATA_FILE.write_text(r.text)
            log.info(f"Fetched existing index with {len(posts)} posts")

            # Download each existing post HTML so they're included in the next deploy.
            # On fetch failure we still keep the post in the index; the previous deploy
            # will still serve the old HTML, so no data loss, and we retry next run.
            for post in posts:
                slug = post["slug"]
                post_file = POSTS_DIR / f"{slug}.html"
                if post_file.exists():
                    continue
                try:
                    pr = http.get(f"{SITE_URL}/posts/{slug}.html", timeout=10)
                    if pr.status_code == 200:
                        post_file.write_text(pr.text)
                        log.info(f"Downloaded existing post: {slug}")
                    else:
                        log.warning(f"Could not download post {slug}: {pr.status_code}")
                except Exception as e:
                    log.warning(f"Failed to download post {slug}: {e}")

            return posts
    except Exception as e:
        log.info(f"No existing index: {e}")
    DATA_FILE.write_text("[]")
    return []


# Cap on the seen-URL set. RSS items older than ~500 entries (3-6 months of
# daily runs) are unlikely to resurface, so we trim to avoid unbounded growth.
SEEN_URLS_MAX = 500


def load_seen() -> set:
    """Load the set of RSS URLs we've already processed. Tolerates corruption."""
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"seen_urls.json unreadable ({e}); starting fresh")
        return set()
    if not isinstance(data, list):
        log.warning(f"seen_urls.json has unexpected type {type(data).__name__}; starting fresh")
        return set()
    return {x for x in data if isinstance(x, str)}


def save_seen(seen: set):
    """Persist the seen set, trimmed to SEEN_URLS_MAX most-recent entries."""
    # Python sets don't preserve order. We can't do true LRU without tracking
    # insertion time, but since we only ever add to the set (never re-reference
    # old entries), slicing an arbitrary subset is good enough — any item we
    # re-encounter from RSS will be re-added anyway.
    trimmed = list(seen)[-SEEN_URLS_MAX:] if len(seen) > SEEN_URLS_MAX else list(seen)
    try:
        SEEN_FILE.write_text(json.dumps(trimmed))
    except OSError as e:
        log.warning(f"Could not persist seen_urls.json: {e}")


def call_claude(prompt: str, system: str = "", max_tokens: int = 3500) -> str | None:
    """Call Claude's messages API with system prompt and temperature."""
    try:
        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "temperature": 0.75,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system

        r = http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        body = r.json()
        for block in body.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                text = block["text"].strip()
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```\s*$", "", text)
                return text.strip()
        log.error(f"Claude response had no text block: {body}")
        return None
    except requests.exceptions.HTTPError as e:
        body = getattr(e.response, "text", "")[:500]
        log.error(f"Claude API HTTP {e.response.status_code}: {body}")
        return None
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None


def call_claude_json(prompt: str, max_tokens: int = 3500, required_keys: tuple = ()) -> dict | None:
    """Call Claude and parse the response as JSON. Returns None on failure."""
    text = call_claude(prompt, max_tokens)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to salvage a JSON object embedded in surrounding prose
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                log.error(f"JSON parse error: {e}. Preview: {text[:200]!r}")
                return None
        else:
            log.error(f"JSON parse error: {e}. Preview: {text[:200]!r}")
            return None
    missing = [k for k in required_keys if k not in data]
    if missing:
        log.error(f"Claude JSON missing required keys: {missing}")
        return None
    return data


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def fetch_rss_items() -> list[dict]:
    """Fetch RSS feeds in a bounded way (feedparser.parse has no built-in timeout)."""
    items = []
    for url in RSS_FEEDS:
        try:
            # Fetch body via requests (with timeout) then hand bytes to feedparser
            resp = http.get(url, timeout=10, headers={"Accept": "application/rss+xml,application/xml,*/*"})
            if resp.status_code != 200:
                log.warning(f"RSS fetch {url} returned {resp.status_code}")
                continue
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")
                text = f"{title} {summary}".lower()
                if any(kw in text for kw in JAPAN_KEYWORDS):
                    items.append({
                        "title": title,
                        "summary": summary[:600],
                        "link": link,
                        "source": feed.feed.get("title", url),
                    })
        except Exception as e:
            log.warning(f"Failed to fetch {url}: {e}")
    log.info(f"Found {len(items)} Japan-related RSS items")
    return items


# ---------------------------------------------------------------------------
# Post 1: Daily Roundup
# ---------------------------------------------------------------------------

def generate_daily_roundup(rss_items: list[dict]) -> dict | None:
    today = datetime.now(timezone.utc).strftime("%A %-d %B %Y")
    resort_db = json.dumps(RESORTS, indent=2, ensure_ascii=False)
    sources_text = "\n\n".join(
        f"Source: {it['source']}\nHeadline: {it['title']}\nSummary: {it['summary']}"
        for it in rss_items[:10]
    )

        system = """You are Piquno, a Japan skiing journal written by an Australian skier living in Melbourne who is completely obsessed with Japow but keeps it brutally honest.

Voice rules:
- Write like a mate who’s been there — warm, practical, slightly sarcastic when needed, zero corporate fluff.
- Use light Aussie slang naturally ("ripper", "bloody", "mate", "she’ll be right").
- Every post must feel like it was written by a real person who actually skis Japan, not a generic AI.
- Short paragraphs. Lots of white space. Never wall-of-text.
- Always give the reader a clear takeaway or decision.

Content rules:
- Lead with the answer or strongest hook in the first 2–3 sentences.
- Use descriptive H2 headings (never generic “Overview”).
- Include at least one comparison, list, or table where it makes sense.
- Be specific — name resorts, snow depths, runs, towns.
- Avoid AI-tell phrases like "delve into", "vibrant tapestry", "testament to", "landscape of", "unlock", "embark on", or "nuanced"."""

    prompt = f"""Today is {today}. Write a DAILY ROUNDUP post covering Japan's ski regions.

Resort database by region:
{resort_db}

Recent skiing news:
{sources_text}

Organise by REGION with <h2> tags. Cover what's newsworthy. Rotate minor resorts over time.

If off-season (April–November), pivot to: pre-season forecasts, resort upgrades, pass deals, or "what to know for next season".

WRITING STYLE:
- Write like an Aussie mate giving a mate a rundown. Natural, direct, occasionally opinionated.
- Short, varied sentence lengths. Mix long and short. Sometimes just a few words.
- Use contractions freely (don't, can't, it's, there's, you'll).
- Be specific. Name resorts, snow depths, runs, towns. No filler adjectives.
- Include your honest take: “I’d rather be at X than Y right now because…”

Respond ONLY with a JSON object (no markdown fences):
{{
  "title": "Japan Snow Report - compelling headline for {today}",
  "tag": "Snow Report",
  "excerpt": "One sentence, max 160 chars",
  "body_html": "700-1000 words. <h2> for regions, <p> for text. Be specific - name resorts, conditions, snow depths. Write like an Aussie mate who checks the cams every morning. Include practical tips."
}}"""

    return call_claude_json(prompt, system=system, max_tokens=3500, required_keys=("title", "tag", "body_html"))



# ---------------------------------------------------------------------------
# Post 2: Feature Article
# ---------------------------------------------------------------------------

FEATURE_TOPICS = [
    "Deep-dive guide to a specific resort or region",
    "Ski gear for Japan conditions (powder skis, layering, goggles)",
    "Japan ski trip planning (flights from Australia, JR pass, budget)",
    "Cultural guide (onsen etiquette, ski food, rest day activities)",
    "Comparison of two resorts for different skier types",
    "Hidden gem / underrated resort profile",
    "Best runs and secret stashes at a popular resort",
    "Japan ski season calendar and booking timing",
    "Backcountry and sidecountry guide for a specific area",
    "Skiing Japan on a budget",
    "Night skiing guide - best resorts",
    "Family skiing in Japan",
    "Solo skiing in Japan",
    "Spring skiing - Gassan, Tateyama, late-season spots",
    "Transport guide - getting between resorts without a car",
    "Avalanche safety and backcountry awareness",
    "Accommodation breakdown - ryokan vs pension vs hotel vs hostel",
    "Japan ski pass comparison - regional and multi-resort",
    "Best après-ski and nightlife by resort",
    "Ramen, curry, and katsu - the definitive Japan ski food guide",
    "How to combine Tokyo with a ski trip",
    "Snowboard vs ski - which resorts suit which",
    "Japan vs Europe vs North America - why Japan wins on powder",
    "Cat skiing and heli skiing options in Japan",
    "Best onsens near ski resorts",
    "Luggage and ski bag tips for flying to Japan",
    "Japan ski resort trail maps - how to read them",
    "Weather patterns - understanding Japan Sea effect snow",
    "Ski rental vs bringing your own gear to Japan",
    "The ultimate Hokkaido road trip itinerary",
]


def generate_feature_article(rss_items: list[dict], existing_titles: list[str]) -> dict | None:
    recent = "\n".join(f"- {t}" for t in existing_titles[:30])
    topics = "\n".join(f"- {t}" for t in FEATURE_TOPICS)
    sources = "\n\n".join(
        f"Headline: {it['title']}\nSummary: {it['summary']}"
        for it in rss_items[:6]
    )

        system = """You are Piquno, a Japan skiing journal written by an Australian skier living in Melbourne who is completely obsessed with Japow but keeps it brutally honest.

Voice rules:
- Write like a mate who’s been there — warm, practical, slightly sarcastic when needed, zero corporate fluff.
- Use light Aussie slang naturally ("ripper", "bloody", "mate", "she’ll be right").
- Every post must feel like it was written by a real person who actually skis Japan.
- Short paragraphs. Lots of white space.
- Always give the reader a clear takeaway.

Content rules:
- Lead with the answer or strongest hook in the first 2–3 sentences.
- Use descriptive H2 headings.
- Include at least one list, comparison or table.
- Add a short “My take as an Aussie who skis Japan every year” section near the end.
- Finish with 4–5 FAQ questions + answers.
- Be specific — name real resorts, runs, towns, gear brands.
- Avoid AI-tell phrases."""

    prompt = f"""Write a FEATURE ARTICLE - a standalone, evergreen piece someone planning a Japan ski trip would bookmark.

Recent posts (AVOID repeating these topics):
{recent}

Topic ideas (pick one or invent your own):
{topics}

Recent news for hooks:
{sources}

Resort database:
{json.dumps(RESORTS, indent=2, ensure_ascii=False)}

WRITING STYLE:
- Write like an Aussie mate explaining something to another Aussie mate. Opinionated, specific, direct.
- Mix short and long sentences. Occasional one-word sentence for emphasis.
- Include a hot take or two. Opinions make articles memorable.
- Name real resorts, runs, towns, gear brands.

Respond ONLY with a JSON object (no markdown fences):
{{
  "title": "SEO-friendly headline",
  "tag": one of {json.dumps(FEATURE_TAGS)},
  "excerpt": "One sentence, max 160 chars",
  "body_html": "900-1300 words. <h2> subheadings, <p> paragraphs. Be opinionated and specific. Aussie perspective welcome. End with 4-5 FAQ questions + answers."
}}"""

    return call_claude_json(prompt, system=system, max_tokens=4000, required_keys=("title", "tag", "body_html"))


# ---------------------------------------------------------------------------
# HTML Builder
# ---------------------------------------------------------------------------

def tag_to_slug(tag: str) -> str:
    """Map a tag label to its URL slug (e.g. 'Snow Report' -> 'snow-report')."""
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


def _escape_attr(s: str) -> str:
    """HTML-escape a value destined for an attribute. Uses html.escape with quote=True."""
    return html.escape(s or "", quote=True)


def build_post_html(article: dict, slug: str, date_str: str,
                    image: dict | None = None,
                    related: list[dict] | None = None) -> str:
    template_path = Path(__file__).parent / "post-template.html"
    if not template_path.exists():
        raise FileNotFoundError(
            f"post-template.html not found at {template_path}. "
            "The Dockerfile must copy it next to bot.py."
        )
    tpl = template_path.read_text()

    # Build hero image HTML (with responsive srcset + fetchpriority for LCP)
    hero_html = ""
    og_image = ""
    if image:
        alt = _escape_attr(image.get("alt") or article.get("title", ""))
        credit_link = _escape_attr(image["credit_link"])
        unsplash_link = _escape_attr(image["unsplash_link"])
        src = _escape_attr(image["url"])
        srcset = _escape_attr(image.get("srcset", ""))
        sizes = _escape_attr(image.get("sizes", ""))
        srcset_attr = f' srcset="{srcset}" sizes="{sizes}"' if srcset else ""
        hero_html = (
            f'<img class="hero-img" src="{src}"{srcset_attr} '
            f'alt="{alt}" width="1200" height="675" '
            f'fetchpriority="high" decoding="async">'
            f'<p class="hero-credit">Photo by '
            f'<a href="{credit_link}" rel="nofollow noopener">{html.escape(image["credit_name"])}</a> '
            f'on <a href="{unsplash_link}" rel="nofollow noopener">Unsplash</a></p>'
        )
        og_image = image["url"]

    # Post body is the Claude-generated HTML as-is.
    body_html = article["body_html"]

    tag = article["tag"]
    tag_class = tag_to_slug(tag)  # e.g. "snow-report", "gear", "planning"
    dt = datetime.fromisoformat(date_str)

    title = article["title"]
    excerpt = article.get("excerpt", "")
    url = f"{SITE_URL}/posts/{slug}.html"

    # --- "Read next" block: up to 3 related posts, same-tag prioritized ---
    read_next_html = ""
    if related:
        # Exclude self, put same-tag matches first, then fill with other recents.
        others = [p for p in related if p.get("slug") != slug]
        same_tag = [p for p in others if p.get("tag") == tag]
        diff_tag = [p for p in others if p.get("tag") != tag]
        picks = (same_tag + diff_tag)[:3]
        if picks:
            items = []
            for p in picks:
                p_title = html.escape(p.get("title", ""))
                p_excerpt = html.escape(p.get("excerpt", ""))
                p_slug = _escape_attr(p.get("slug", ""))
                p_tag = html.escape(p.get("tag", ""))
                p_tag_class = tag_to_slug(p.get("tag", ""))
                items.append(
                    f'<a class="post-card" href="/posts/{p_slug}.html">'
                    f'<div class="post-meta">'
                    f'<span class="post-tag {p_tag_class}">{p_tag}</span>'
                    f'</div>'
                    f'<h3 class="post-title">{p_title}</h3>'
                    f'<p class="post-excerpt">{p_excerpt}</p>'
                    f'</a>'
                )
            read_next_html = (
                '<section class="read-next" aria-labelledby="read-next-heading">'
                '<h2 id="read-next-heading">Read next</h2>'
                '<div class="post-list">' + "".join(items) + '</div>'
                '</section>'
            )

    # JSON-LD BlogPosting schema
    jsonld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": excerpt,
        "datePublished": dt.isoformat(),
        "dateModified": dt.isoformat(),
        "author": {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL},
        "publisher": {
            "@type": "Organization",
            "name": SITE_NAME,
            "url": SITE_URL,
            "logo": {"@type": "ImageObject", "url": f"{SITE_URL}/icon-512.png"},
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "articleSection": tag,
        "inLanguage": SITE_LANG,
    }
    if og_image:
        jsonld["image"] = og_image
    # Embedded JSON-LD must not allow the string "</script>" to appear in any value,
    # or the HTML parser will end the enclosing <script> tag early and interpret
    # whatever follows as markup. Escape angle brackets (and & for good measure)
    # using Unicode escapes that are valid JSON and invisible to parsers.
    jsonld_json = (json.dumps(jsonld, ensure_ascii=False)
                   .replace("<", "\\u003c")
                   .replace(">", "\\u003e")
                   .replace("&", "\\u0026"))
    jsonld_html = f'<script type="application/ld+json">{jsonld_json}</script>'

    # Social meta block (OG + Twitter Card)
    social_meta_lines = [
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:locale" content="{SITE_LANG.replace("-", "_")}">',
        f'<meta property="article:published_time" content="{dt.isoformat()}">',
        f'<meta property="article:modified_time" content="{dt.isoformat()}">',
        f'<meta property="article:section" content="{_escape_attr(tag)}">',
        f'<meta property="article:author" content="{SITE_NAME}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:site" content="@piqunoski">',
        '<meta name="twitter:creator" content="@piqunoski">',
        f'<meta name="twitter:title" content="{_escape_attr(title)} — {SITE_NAME}">',
        f'<meta name="twitter:description" content="{_escape_attr(excerpt)}">',
    ]
    if og_image:
        social_meta_lines.append(f'<meta property="og:image" content="{_escape_attr(og_image)}">')
        social_meta_lines.append(f'<meta property="og:image:alt" content="{_escape_attr(image.get("alt", ""))}">')
        social_meta_lines.append('<meta property="og:image:width" content="1200">')
        social_meta_lines.append('<meta property="og:image:height" content="675">')
        social_meta_lines.append(f'<meta name="twitter:image" content="{_escape_attr(og_image)}">')
    social_meta = "\n    ".join(social_meta_lines)

    # Substitution map
    subs = {
        "{{TITLE}}": _escape_attr(title),
        "{{TITLE_TEXT}}": html.escape(title),  # text context (inside <h1>)
        "{{EXCERPT}}": _escape_attr(excerpt),
        "{{SLUG}}": _escape_attr(slug),
        "{{DATE_FORMATTED}}": dt.strftime("%-d %b %Y"),
        "{{DATE_ISO}}": dt.isoformat(),
        "{{TAG}}": html.escape(tag),
        "{{TAG_SLUG}}": tag_to_slug(tag),
        "{{TAG_CLASS}}": tag_class,
        "{{HERO_IMAGE}}": hero_html,
        "{{BODY}}": body_html,
        "{{READ_NEXT}}": read_next_html,
        "{{SOCIAL_META}}": social_meta,
        "{{JSON_LD}}": jsonld_html,
        "{{LANG}}": SITE_LANG,
    }
    html_out = tpl
    for k, v in subs.items():
        html_out = html_out.replace(k, v)
    return html_out


# ---------------------------------------------------------------------------
# Sitemap & Google Ping
# ---------------------------------------------------------------------------

def generate_sitemap(posts: list[dict]):
    """Generate sitemap.xml for SEO."""
    urls = [
        f'  <url><loc>{SITE_URL}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'  <url><loc>{SITE_URL}/about</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>',
    ]
    # Tag landing pages
    tags_present = sorted({p.get("tag", "") for p in posts if p.get("tag")})
    for t in tags_present:
        urls.append(
            f'  <url><loc>{SITE_URL}/tags/{tag_to_slug(t)}/</loc>'
            f'<changefreq>weekly</changefreq><priority>0.5</priority></url>'
        )
    for post in posts[:500]:  # Sitemap spec: 50k URL limit; we cap well below
        date = post["date"][:10]
        urls.append(
            f'  <url><loc>{SITE_URL}/posts/{post["slug"]}.html</loc>'
            f'<lastmod>{date}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>'
        )

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += '\n'.join(urls)
    sitemap += '\n</urlset>\n'

    (TEMPLATE_DIR / "sitemap.xml").write_text(sitemap)
    log.info(f"Generated sitemap with {len(urls)} URLs")


def generate_robots_txt():
    """Write a robots.txt pointing at the sitemap."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /posts/index.json\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    (TEMPLATE_DIR / "robots.txt").write_text(content)
    log.info("Generated robots.txt")


def _xml_escape(s: str) -> str:
    """Escape text for inclusion in XML element content."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_rss_feed(posts: list[dict]):
    """Generate a proper RSS feed with recent posts."""
    items = []
    for post in posts[:20]:
        items.append(f"""  <item>
    <title>{_xml_escape(post['title'])}</title>
    <link>{SITE_URL}/posts/{post['slug']}.html</link>
    <description>{_xml_escape(post.get('excerpt', ''))}</description>
    <pubDate>{post['date']}</pubDate>
    <guid>{SITE_URL}/posts/{post['slug']}.html</guid>
  </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{SITE_NAME} - {SITE_TAGLINE}</title>
  <link>{SITE_URL}</link>
  <description>Snow reports, resort intel, and gear picks for skiing Japan.</description>
  <language>{SITE_LANG.lower()}</language>
  <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
</channel>
</rss>
"""

    (TEMPLATE_DIR / "feed.xml").write_text(feed)
    log.info("Generated RSS feed")


# Note: Google and Bing sitemap-ping endpoints were deprecated in 2023.
# Google now relies on sitemap discovery via robots.txt + Search Console.
# (The old ping_google() / Bing ping functions have been removed.)


# ---------------------------------------------------------------------------
# Netlify Deploy
# ---------------------------------------------------------------------------

def deploy_to_netlify():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in TEMPLATE_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, str(path.relative_to(TEMPLATE_DIR)))
    buf.seek(0)
    log.info(f"Deploying ({buf.getbuffer().nbytes} bytes)...")
    r = http.post(
        f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys",
        headers={
            "Authorization": f"Bearer {NETLIFY_AUTH_TOKEN}",
            "Content-Type": "application/zip",
        },
        data=buf.read(),
        timeout=180,
    )
    r.raise_for_status()
    log.info(f"Deployed: {r.json().get('ssl_url', 'OK')}")


# ---------------------------------------------------------------------------
# Static HTML rendering (homepage + tag pages)
# ---------------------------------------------------------------------------

def _render_post_cards_html(posts: list[dict], limit: int = 20) -> str:
    """Build the inner HTML for <div class="post-list">."""
    lines = []
    for p in posts[:limit]:
        dt_str = p.get("date", "")
        try:
            dt = datetime.fromisoformat(dt_str)
            date_fmt = dt.strftime("%-d %b %Y")
        except Exception:
            date_fmt = dt_str[:10]
        tag = p.get("tag", "")
        tag_class = tag_to_slug(tag)
        title = html.escape(p.get("title", ""))
        excerpt = html.escape(p.get("excerpt", ""))
        slug = _escape_attr(p.get("slug", ""))
        lines.append(
            f'        <a class="post-card" href="/posts/{slug}.html">\n'
            f'          <div class="post-meta">\n'
            f'            <span class="post-date">{date_fmt}</span>\n'
            f'            <span class="post-tag {tag_class}">{html.escape(tag)}</span>\n'
            f'          </div>\n'
            f'          <h2 class="post-title">{title}</h2>\n'
            f'          <p class="post-excerpt">{excerpt}</p>\n'
            f'        </a>'
        )
    return "\n".join(lines)


# Matches the actual <div class="post-list" id="post-list"> block AND the
# empty-state block that follows. The regex:
#   - Requires a newline+whitespace before the opening <div, so it doesn't
#     match the same string when it appears inside an HTML comment above.
#   - Uses two non-greedy .*? segments, one for the post-list inner content
#     (stops at its </div>) and one for the empty-state's mountain div.
#   - Then requires the empty-state's closing </div> after the <p>…</p>.
_POST_LIST_RE = re.compile(
    r'\n\s+<div class="post-list" id="post-list">.*?</div>\s*'
    r'<div class="empty-state" id="empty-state"[^>]*>'
    r'\s*<div class="mountain">[^<]*</div>'
    r'\s*<p>[^<]*</p>'
    r'\s*</div>',
    re.DOTALL,
)


def render_homepage(posts: list[dict]):
    """Replace the JS-only post list in index.html with server-rendered cards."""
    index_path = TEMPLATE_DIR / "index.html"
    if not index_path.exists():
        log.warning("index.html not in template dir, skipping homepage render")
        return
    src = index_path.read_text()

    if posts:
        cards = _render_post_cards_html(posts, limit=20)
        replacement = (
            f'\n        <div class="post-list" id="post-list">\n{cards}\n        </div>\n\n'
            f'        <div class="empty-state" id="empty-state" style="display:none">\n'
            f'            <div class="mountain">⛰️</div>\n'
            f'            <p>First posts coming soon. The bot is warming up.</p>\n'
            f'        </div>'
        )
    else:
        # No posts yet: leave the empty state visible
        replacement = (
            f'\n        <div class="post-list" id="post-list"></div>\n\n'
            f'        <div class="empty-state" id="empty-state">\n'
            f'            <div class="mountain">⛰️</div>\n'
            f'            <p>First posts coming soon. The bot is warming up.</p>\n'
            f'        </div>'
        )

    new_src, n = _POST_LIST_RE.subn(replacement, src, count=1)
    if n == 0:
        log.warning("Could not find post-list block in index.html; leaving unchanged")
        return
    index_path.write_text(new_src)
    log.info(f"Rendered {min(len(posts), 20)} post cards into homepage")


# Core taxonomy: main content tags that get directory-style pages
MAIN_TAGS = {
    "Snow Report": "snow-report",
    "Resort Guide": "resort-guide",
    "Gear": "gear",
    "Travel": "travel",
    "Culture": "culture",
    "Planning": "planning",
}

# Resort pages featured on homepage — these match against post content, not the tag field
FEATURED_RESORTS = [
    ("Niseko", "niseko", ["niseko"]),
    ("Hakuba", "hakuba", ["hakuba"]),
    ("Myoko Kogen", "myoko", ["myoko", "akakura"]),
    ("Nozawa Onsen", "nozawa", ["nozawa"]),
    ("Furano", "furano", ["furano"]),
    ("Rusutsu", "rusutsu", ["rusutsu"]),
    ("Shiga Kogen", "shiga-kogen", ["shiga kogen", "shiga-kogen"]),
    ("Appi Kogen", "appi", ["appi"]),
]


def _tag_page_template(title: str, desc: str, posts: list[dict], canonical: str) -> str:
    """Build a full standalone tag-page HTML document (server-rendered)."""
    cards = _render_post_cards_html(posts, limit=50) if posts else ""
    empty_html = ("" if posts else
                  '<div class="empty-state"><div class="mountain">⛰️</div>'
                  '<p>No posts in this category yet. Check back soon.</p></div>')
    return f"""<!DOCTYPE html>
<html lang="{SITE_LANG}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)} — {SITE_NAME}</title>
    <meta name="description" content="{_escape_attr(desc)}">
    <link rel="canonical" href="{canonical}">
    <meta property="og:title" content="{_escape_attr(title)} — {SITE_NAME}">
    <meta property="og:description" content="{_escape_attr(desc)}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{canonical}">
    <meta property="og:site_name" content="{SITE_NAME}">
    <meta name="twitter:card" content="summary">
    <meta name="twitter:site" content="@piqunoski">
    <link rel="icon" href="/favicon.svg" type="image/svg+xml">
    <link rel="alternate icon" href="/favicon.ico">
    <link rel="alternate" type="application/rss+xml" title="{SITE_NAME} RSS" href="/feed.xml">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Sans+3:wght@300;400;500;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/styles.css">
</head>
<body>
    <a href="#main" class="skip-link">Skip to content</a>
    <nav aria-label="Primary">
        <ul>
            <li><a href="/">Latest</a></li>
            <li><a href="/tags/snow-report/">Snow Reports</a></li>
            <li><a href="/tags/resort-guide/">Resorts</a></li>
            <li><a href="/tags/gear/">Gear</a></li>
            <li><a href="/tags/travel/">Travel</a></li>
            <li><a href="/tags/planning/">Planning</a></li>
            <li><a href="/about">About</a></li>
        </ul>
    </nav>
    <main id="main" class="tag-page">
        <h1 class="page-title">{html.escape(title)}</h1>
        <p class="page-desc">{html.escape(desc)}</p>
        <div class="post-list">
{cards}
        </div>
        {empty_html}
    </main>
    <footer>
        <p>&copy; 2026 {SITE_NAME}. Built with snow and caffeine in Melbourne.<br>
        <a href="/">Home</a> · <a href="/feed.xml">RSS</a></p>
    </footer>
</body>
</html>
"""


def generate_tag_pages(posts: list[dict]):
    """Generate a static /tags/<slug>/index.html for every tag and featured resort."""
    tags_dir = TEMPLATE_DIR / "tags"
    tags_dir.mkdir(parents=True, exist_ok=True)

    # Remove old duplicate tags.html if present in the publish dir
    stale = TEMPLATE_DIR / "tags.html"
    if stale.exists():
        stale.unlink()

    # Main content tags
    tag_descriptions = {
        "Snow Report": "Daily conditions across every Japan ski region.",
        "Resort Guide": "Deep dives into Japan's best ski resorts.",
        "Gear": "Ski gear reviews and recommendations for Japan conditions.",
        "Travel": "Trip planning, flights, transport, and budgeting for Japan ski trips.",
        "Culture": "Onsen, food, and the cultural side of skiing Japan.",
        "Planning": "Everything you need to plan your Japan ski trip.",
    }
    for tag_label, slug in MAIN_TAGS.items():
        matching = [p for p in posts if p.get("tag") == tag_label]
        desc = tag_descriptions.get(tag_label, f"{tag_label} posts on {SITE_NAME}.")
        canonical = f"{SITE_URL}/tags/{slug}/"
        page_html = _tag_page_template(tag_label, desc, matching, canonical)
        dest = tags_dir / slug
        dest.mkdir(exist_ok=True)
        (dest / "index.html").write_text(page_html)

    # Featured resort pages — match by presence of resort name in title/body/tag
    for label, slug, keywords in FEATURED_RESORTS:
        matching = []
        for p in posts:
            hay = (p.get("title", "") + " " + p.get("excerpt", "")).lower()
            if any(kw in hay for kw in keywords):
                matching.append(p)
        desc = f"Posts about {label}, one of Japan's standout ski destinations."
        canonical = f"{SITE_URL}/tags/{slug}/"
        page_html = _tag_page_template(label, desc, matching, canonical)
        dest = tags_dir / slug
        dest.mkdir(exist_ok=True)
        (dest / "index.html").write_text(page_html)

    log.info(f"Generated {len(MAIN_TAGS) + len(FEATURED_RESORTS)} tag pages")


# ---------------------------------------------------------------------------
# Social Auto-Posting (optional - runs after deploy)
# ---------------------------------------------------------------------------

# Bluesky credentials (free, open API)
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")  # e.g. piquno.bsky.social
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")

# Twitter/X credentials. Free tier is 500 writes/month as of mid-2024.
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET", "")


def get_random_hashtags(tag: str = "", count: int = 3) -> str:
    """Return a small, topical set of hashtags for a post.

    Strategy: always include the two niche core tags (#SkiJapan, #Japow) — these
    are small enough communities that we reliably surface in their feeds. Then
    add one topic-specific tag. We deliberately avoid generic high-volume tags
    like #Skiing, #MountainLife, #FreshTracks — they're drowned in unrelated
    content and reading as "bot spam" to Bluesky's algorithmic suppression.
    """
    core = ["#SkiJapan", "#Japow"]
    topic_tags = {
        "Snow Report":  ["#JapanSnow", "#PowderDay"],
        "Resort Guide": ["#Niseko", "#Hakuba", "#Nozawa", "#Myoko", "#Furano"],
        "Gear":         ["#SkiGear", "#PowderSkis"],
        "Travel":       ["#JapanTravel", "#SkiTrip"],
        "Culture":      ["#JapanTravel", "#Onsen"],
        "Planning":     ["#JapanTravel", "#SkiTrip"],
    }
    picked = list(core)
    extras = topic_tags.get(tag, ["#JapanTravel"])
    picked.append(random.choice(extras))
    # Trim / shuffle to requested count
    random.shuffle(picked)
    return " ".join(picked[:max(1, count)])


def _build_bsky_facets(text: str, url: str) -> list[dict]:
    """Build Bluesky richtext facets for URLs and hashtags found in `text`.

    All indices are UTF-8 BYTE offsets, per AT Protocol spec.
    """
    facets = []
    text_bytes = text.encode("utf-8")

    # Link facet for the post's canonical URL (use first occurrence)
    url_bytes = url.encode("utf-8")
    start = text_bytes.find(url_bytes)
    if start >= 0:
        facets.append({
            "index": {"byteStart": start, "byteEnd": start + len(url_bytes)},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })

    # Hashtag facets. Match #word (ASCII + unicode letters), skip facet if it overlaps URL.
    url_range = range(start, start + len(url_bytes)) if start >= 0 else range(0, 0)
    # Walk the UTF-8 bytes, locating #tags. We regex on str then map back to byte offsets.
    for m in re.finditer(r"(?<![\w/])#([A-Za-z][A-Za-z0-9_]{0,63})", text):
        tag_char_start = m.start()
        tag_char_end = m.end()
        # Convert char offsets to byte offsets
        byte_start = len(text[:tag_char_start].encode("utf-8"))
        byte_end = len(text[:tag_char_end].encode("utf-8"))
        # Skip if this range overlaps the URL facet (e.g., fragment)
        if any(b in url_range for b in (byte_start, byte_end - 1)):
            continue
        facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}],
        })

    return facets


def _upload_bsky_blob(session_auth: dict, image_url: str) -> dict | None:
    """Download an image and upload it as a Bluesky blob. Returns blob ref or None."""
    try:
        img_resp = http.get(image_url, timeout=15)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
        content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if len(img_bytes) > 900_000:
            # Bluesky blob limit ~1MB; request a smaller size from Unsplash if so
            # Try a smaller variant if this is an Unsplash URL
            sep = "&" if "?" in image_url else "?"
            smaller = re.sub(r"([?&])w=\d+", r"\1w=800", image_url)
            if smaller == image_url:
                smaller = f"{image_url}{sep}w=800"
            img_resp = http.get(smaller, timeout=15)
            img_resp.raise_for_status()
            img_bytes = img_resp.content
            content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if len(img_bytes) > 950_000:
            log.warning("Image too large for Bluesky blob; skipping thumbnail")
            return None
        r = http.post(
            "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {session_auth['accessJwt']}",
                "Content-Type": content_type,
            },
            data=img_bytes,
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("blob")
    except Exception as e:
        log.warning(f"Bluesky blob upload failed: {e}")
        return None


def post_to_bluesky(title: str, excerpt: str, url: str, tag: str = "", image_url: str = ""):
    """Post to Bluesky via AT Protocol with proper hashtag facets and link preview embed."""
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        return

    try:
        session = http.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
            timeout=15,
        )
        session.raise_for_status()
        auth = session.json()

        # --- Pre-fetch the thumbnail blob so we know if we'll have an embed card ---
        # If we have a real embed card with an image, Bluesky renders the URL as a
        # clickable preview tile, so we can drop the URL from the visible text and
        # free up character budget.
        thumb_blob = None
        if image_url:
            thumb_blob = _upload_bsky_blob(auth, image_url)

        embed = {
            "$type": "app.bsky.embed.external",
            "external": {
                "uri": url,
                "title": title,
                "description": excerpt or f"Read on {SITE_NAME}",
            },
        }
        if thumb_blob:
            embed["external"]["thumb"] = thumb_blob

        # --- Compose visible post text ---
        # Strategy: title + (excerpt if room) + hashtags.
        # URL is omitted when we have a rich embed (the card shows it). We keep the
        # URL only if the embed lacks a thumbnail, as a fallback for clients that
        # don't render embeds well.
        hashtags = get_random_hashtags(tag=tag, count=3)
        show_url_inline = not thumb_blob

        def _compose(title_text: str, include_excerpt: bool, tags: str, include_url: bool) -> str:
            parts = [title_text]
            if include_excerpt and excerpt:
                parts.append(excerpt)
            if include_url:
                parts.append(url)
            if tags:
                parts.append(tags)
            return "\n\n".join(parts)

        text = _compose(title, True, hashtags, show_url_inline)
        if len(text) > 300:
            text = _compose(title, False, hashtags, show_url_inline)
        if len(text) > 300:
            text = _compose(title, False, get_random_hashtags(tag=tag, count=2), show_url_inline)
        if len(text) > 300:
            text = _compose(title, False, "", show_url_inline)
        if len(text) > 300:
            # Title itself is still too long — truncate it so the whole payload
            # fits within the 300-char grapheme budget (approximated via len()).
            # Reserve budget for: "\n\n" + URL (if shown) + the ellipsis char.
            reserve = (2 + len(url)) if show_url_inline else 0
            max_title_len = max(10, 300 - reserve - 1)  # -1 for the ellipsis
            safe_title = title[:max_title_len].rstrip() + "…"
            text = _compose(safe_title, False, "", show_url_inline)
            # Final safety: if even that overshoots (shouldn't be possible), clamp hard.
            if len(text) > 300:
                text = text[:299] + "…"

        facets = _build_bsky_facets(text, url)

        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "langs": [SITE_LANG.lower()],
            "facets": facets,
            "embed": embed,
        }

        r = http_strict.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {auth['accessJwt']}"},
            json={
                "repo": auth["did"],
                "collection": "app.bsky.feed.post",
                "record": record,
            },
            timeout=20,
        )
        r.raise_for_status()
        log.info(f"Posted to Bluesky: {title}")
    except Exception as e:
        log.warning(f"Bluesky post failed: {e}")


def post_to_twitter(title: str, excerpt: str, url: str, tag: str = ""):
    """Post to Twitter/X using OAuth 1.0a user context.

    The v2 /tweets endpoint supports OAuth 1.0a User Context even though JSON bodies
    aren't signed (bodies aren't part of the signature base string per RFC 5849).
    """
    if not TWITTER_API_KEY or not TWITTER_ACCESS_TOKEN:
        return

    try:
        # X free-tier is 500 writes/month. Keep it tight: title, URL, 2-3 hashtags.
        hashtags = get_random_hashtags(tag=tag, count=3)
        tweet_text = f"{title}\n\n{url}\n\n{hashtags}"
        if len(tweet_text) > 280:
            tweet_text = f"{title}\n\n{url}\n\n" + get_random_hashtags(tag=tag, count=2)
        if len(tweet_text) > 280:
            tweet_text = f"{title}\n\n{url}"
        if len(tweet_text) > 280:
            # Reserve: "\n\n" (2) + URL + ellipsis (1)
            max_title_len = max(10, 280 - len(url) - 2 - 1)
            tweet_text = title[:max_title_len].rstrip() + "…\n\n" + url

        oauth_nonce = secrets.token_hex(16)
        oauth_timestamp = str(int(time.time()))

        params = {
            "oauth_consumer_key": TWITTER_API_KEY,
            "oauth_nonce": oauth_nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": oauth_timestamp,
            "oauth_token": TWITTER_ACCESS_TOKEN,
            "oauth_version": "1.0",
        }

        base_url = "https://api.twitter.com/2/tweets"
        param_string = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(params.items())
        )
        base_string = (
            f"POST&{urllib.parse.quote(base_url, safe='')}"
            f"&{urllib.parse.quote(param_string, safe='')}"
        )
        signing_key = (
            f"{urllib.parse.quote(TWITTER_API_SECRET, safe='')}"
            f"&{urllib.parse.quote(TWITTER_ACCESS_SECRET, safe='')}"
        )

        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()

        params["oauth_signature"] = signature
        auth_header = "OAuth " + ", ".join(
            f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
            for k, v in sorted(params.items())
        )

        r = http_strict.post(
            base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
            },
            json={"text": tweet_text},
            timeout=20,
        )
        if r.status_code >= 400:
            log.warning(f"Twitter post failed: HTTP {r.status_code} body={r.text[:300]}")
            return
        log.info(f"Posted to Twitter: {title}")
    except Exception as e:
        log.warning(f"Twitter post failed: {e}")


def share_to_socials(new_posts: list[dict]):
    """Share only the freshly created posts to social platforms.

    `new_posts` should be the list of dicts actually created this run (not a slice of
    existing_posts, which could re-share yesterday's content on partial failures).
    """
    for i, p in enumerate(new_posts):
        url = f"{SITE_URL}/posts/{p['slug']}.html"
        tag = p.get("tag", "")
        image_url = p.get("image_url", "")
        post_to_bluesky(p["title"], p.get("excerpt", ""), url, tag, image_url=image_url)
        # Add a realistic delay + jitter between platforms and between posts
        time.sleep(random.randint(8, 22))
        post_to_twitter(p["title"], p.get("excerpt", ""), url, tag)
        if i < len(new_posts) - 1:
            time.sleep(random.randint(60, 180))  # 1–3 min between posts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== Piquno Bot v2 starting ===")

    existing_posts = fetch_existing_site()
    existing_slugs = {p["slug"] for p in existing_posts}
    existing_titles = [p["title"] for p in existing_posts]
    seen = load_seen()

    # Copy static files (index.html, about.html, post-template.html, _headers, _redirects, etc.)
    static_src = Path(__file__).parent / "site"
    if static_src.exists():
        for f in static_src.rglob("*"):
            if f.is_file():
                dest = TEMPLATE_DIR / f.relative_to(static_src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    POSTS_DIR.mkdir(parents=True, exist_ok=True)

    # Don't ship the post template as public content
    leaked_tpl = TEMPLATE_DIR / "post-template.html"
    if leaked_tpl.exists():
        leaked_tpl.unlink()

    # Fetch RSS
    rss_items = fetch_rss_items()
    new_items = [it for it in rss_items if it["link"] not in seen]
    if not new_items:
        new_items = rss_items[:5] if rss_items else []

    new_posts: list[dict] = []  # Track only what we create this run

    # --- Daily roundup ---
    roundup = generate_daily_roundup(new_items)
    if roundup:
        slug = slug_from_title(roundup["title"])
        if slug in existing_slugs:
            slug = f"{slug}-{int(time.time()) % 10000}"
        now = datetime.now(timezone.utc)
        image = fetch_hero_image("japan skiing snow powder mountain")
        (POSTS_DIR / f"{slug}.html").write_text(
            build_post_html(roundup, slug, now.isoformat(), image,
                            related=existing_posts)
        )
        post_entry = {
            "title": roundup["title"], "slug": slug, "date": now.isoformat(),
            "tag": roundup["tag"], "excerpt": roundup.get("excerpt", ""),
            "image_url": image["url"] if image else "",
        }
        existing_posts.insert(0, post_entry)
        existing_slugs.add(slug)
        existing_titles.insert(0, roundup["title"])
        new_posts.append(post_entry)
        log.info(f"Roundup: {slug}")

    # --- Feature article ---
    feature = generate_feature_article(new_items, existing_titles)
    if feature:
        slug = slug_from_title(feature["title"])
        if slug in existing_slugs:
            slug = f"{slug}-{int(time.time()) % 10000}"
        now = datetime.now(timezone.utc)
        # Build an image search query from the article tag + first 3 title words.
        # (Previously f-stringed a list, producing "japan gear skiing ['Best','Powder','Skis']".)
        first_words = " ".join(feature["title"].split()[:3])
        image_query = f"japan {feature['tag'].lower()} skiing {first_words}"
        image = fetch_hero_image(image_query)
        (POSTS_DIR / f"{slug}.html").write_text(
            build_post_html(feature, slug, now.isoformat(), image,
                            related=existing_posts)
        )
        post_entry = {
            "title": feature["title"], "slug": slug, "date": now.isoformat(),
            "tag": feature["tag"], "excerpt": feature.get("excerpt", ""),
            "image_url": image["url"] if image else "",
        }
        existing_posts.insert(0, post_entry)
        existing_slugs.add(slug)
        new_posts.append(post_entry)
        log.info(f"Feature: {slug}")

    # Save state (only mark RSS items seen if we actually used them)
    if new_posts:
        for it in new_items:
            seen.add(it["link"])
        save_seen(seen)

    # Always refresh index.json so the JS fallback on the homepage stays accurate
    DATA_FILE.write_text(json.dumps(existing_posts, indent=2))

    if new_posts:
        generate_sitemap(existing_posts)
        generate_rss_feed(existing_posts)
        generate_robots_txt()
        render_homepage(existing_posts)
        generate_tag_pages(existing_posts)
        deploy_to_netlify()
        # Wait a beat for CDN to propagate before Bluesky tries to scrape card metadata
        time.sleep(15)
        share_to_socials(new_posts)
        log.info(f"Done - {len(new_posts)} posts published")
    else:
        log.info("No posts generated")


if __name__ == "__main__":
    main()
