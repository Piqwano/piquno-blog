"""
Piquno Japan Ski Blog — Auto-posting Bot v2
=============================================
Runs daily on Railway. Generates:
  1. A daily roundup covering all Japan ski resorts by region
  2. A standalone feature article (gear, guides, travel tips)

Environment variables:
  ANTHROPIC_API_KEY  — Your Anthropic API key
  NETLIFY_AUTH_TOKEN — Netlify personal access token
  NETLIFY_SITE_ID   — Your Netlify site ID
  UNSPLASH_ACCESS_KEY — Unsplash API access key (free at unsplash.com/developers)
"""

import os
import json
import re
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import zipfile

import feedparser
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NETLIFY_AUTH_TOKEN = os.environ["NETLIFY_AUTH_TOKEN"]
NETLIFY_SITE_ID = os.environ["NETLIFY_SITE_ID"]
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")  # Update when new models release

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
            "Gokase Highland (Miyazaki — southernmost ski area)",
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
    "https://www.snow-forecast.com/feeds/resort_news.xml",
    "https://www.skiasia.com/feed",
    "https://japantoday.com/feed",
    "https://www.powderhounds.com/site/rss.xml",
    "https://snowbrains.com/feed/",
    "https://unofficialnetworks.com/feed/",
    "https://www.tetongravity.com/rss",
    "https://www.japan-guide.com/rss/whatsnew_e.xml",
    "https://matcha-jp.com/en/rss",
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
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]


def fetch_hero_image(query: str) -> dict | None:
    """Fetch a royalty-free image from Unsplash. Returns dict with url, credit, link."""
    if not UNSPLASH_ACCESS_KEY:
        log.info("No Unsplash key, skipping image")
        return None
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query": query,
                "per_page": 5,
                "orientation": "landscape",
                "content_filter": "high",
            },
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            # Fallback to generic Japan skiing query
            r2 = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": "japan skiing snow mountain", "per_page": 5, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=15,
            )
            r2.raise_for_status()
            results = r2.json().get("results", [])
        if not results:
            return None

        # Pick a random-ish result to avoid repeating the same image
        import random
        photo = random.choice(results[:3])

        # Unsplash requires triggering a download endpoint for tracking
        dl_url = photo.get("links", {}).get("download_location", "")
        if dl_url:
            try:
                requests.get(dl_url, headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}, timeout=5)
            except Exception:
                pass

        return {
            "url": photo["urls"]["regular"],  # 1080px wide
            "credit_name": photo["user"]["name"],
            "credit_link": photo["user"]["links"]["html"] + "?utm_source=piquno&utm_medium=referral",
            "unsplash_link": "https://unsplash.com/?utm_source=piquno&utm_medium=referral",
            "alt": photo.get("alt_description", "Japan skiing"),
        }
    except Exception as e:
        log.warning(f"Unsplash fetch failed: {e}")
        return None


def fetch_existing_site():
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get("https://piquno.com/posts/index.json", timeout=10)
        if r.status_code == 200:
            DATA_FILE.write_text(r.text)
            log.info(f"Fetched existing index with {len(r.json())} posts")
            return r.json()
    except Exception as e:
        log.info(f"No existing index: {e}")
    DATA_FILE.write_text("[]")
    return []


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))


def call_claude(prompt: str, max_tokens: int = 3500) -> str | None:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=90,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        text = re.sub(r"^```json\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        return text
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def fetch_rss_items() -> list[dict]:
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
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

    prompt = f"""You are a blog writer for Piquno, a Japan skiing journal written by an Australian skier.

Today is {today}. Write a DAILY ROUNDUP post covering Japan's ski regions.

Resort database by region:
{resort_db}

Recent skiing news:
{sources_text}

Organise by REGION with <h2> tags. Cover what's newsworthy — you don't need every resort every day. Rotate minor resorts over time.

If off-season (April–November), pivot to: pre-season forecasts, resort upgrades, pass deals, or "what to know for next season".

Respond ONLY with a JSON object (no markdown fences):
{{
  "title": "Japan Snow Report — compelling headline for {today}",
  "tag": "Snow Report",
  "excerpt": "One sentence, max 160 chars",
  "body_html": "600-1000 words. <h2> for regions, <p> for text. Be specific — name resorts, conditions, snow depths. Write like an Aussie mate who checks the cams every morning. Include practical tips."
}}"""

    text = call_claude(prompt, max_tokens=3500)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as e:
        log.error(f"JSON parse error (roundup): {e}")
        return None


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
    "Night skiing guide — best resorts",
    "Family skiing in Japan",
    "Solo skiing in Japan",
    "Spring skiing — Gassan, Tateyama, late-season spots",
    "Transport guide — getting between resorts without a car",
    "Avalanche safety and backcountry awareness",
    "Accommodation breakdown — ryokan vs pension vs hotel vs hostel",
    "Japan ski pass comparison — regional and multi-resort",
    "Best après-ski and nightlife by resort",
    "Ramen, curry, and katsu — the definitive Japan ski food guide",
    "How to combine Tokyo with a ski trip",
    "Snowboard vs ski — which resorts suit which",
    "Japan vs Europe vs North America — why Japan wins on powder",
    "Cat skiing and heli skiing options in Japan",
    "Best onsens near ski resorts",
    "Luggage and ski bag tips for flying to Japan",
    "Japan ski resort trail maps — how to read them",
    "Weather patterns — understanding Japan Sea effect snow",
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

    prompt = f"""You are a blog writer for Piquno, a Japan skiing journal by an Australian skier.

Write a FEATURE ARTICLE — a standalone, evergreen piece someone planning a Japan ski trip would bookmark.

Recent posts (AVOID repeating):
{recent}

Topic ideas (pick one or invent your own):
{topics}

Recent news for hooks:
{sources}

Resort database:
{json.dumps(RESORTS, indent=2, ensure_ascii=False)}

Respond ONLY with a JSON object (no markdown fences):
{{
  "title": "SEO-friendly headline",
  "tag": one of {json.dumps(FEATURE_TAGS)},
  "excerpt": "One sentence, max 160 chars",
  "body_html": "700-1200 words. <h2> subheadings, <p> paragraphs. Be opinionated and specific — name resorts, runs, towns, gear. Write like a knowledgeable mate, not a content mill. Aussie perspective welcome."
}}"""

    text = call_claude(prompt, max_tokens=4000)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as e:
        log.error(f"JSON parse error (feature): {e}")
        return None


# ---------------------------------------------------------------------------
# HTML Builder
# ---------------------------------------------------------------------------

def build_post_html(article: dict, slug: str, date_str: str, image: dict | None = None) -> str:
    template = Path(__file__).parent / "post-template.html"
    if template.exists():
        html = template.read_text()
    else:
        html = """<!DOCTYPE html><html><head><title>{{TITLE}} — Piquno</title>
        <meta name="description" content="{{EXCERPT}}">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Sans+3:wght@300;400;500;600&display=swap" rel="stylesheet">
        </head><body style="font-family:'Source Sans 3',sans-serif;max-width:680px;margin:0 auto;padding:2rem 1.5rem;">
        <a href="/">← Home</a>
        <h1 style="font-family:'DM Serif Display',serif;">{{TITLE}}</h1>
        {{HERO_IMAGE}}
        <div>{{BODY}}</div>
        <footer style="margin-top:3rem;color:#888;font-size:0.8rem;">© 2026 Piquno</footer>
        </body></html>"""

    # Build hero image HTML
    hero_html = ""
    if image:
        hero_html = (
            f'<img class="hero-img" src="{image["url"]}" alt="{image["alt"]}" loading="lazy">'
            f'<p class="hero-credit">Photo by <a href="{image["credit_link"]}">{image["credit_name"]}</a>'
            f' on <a href="{image["unsplash_link"]}">Unsplash</a></p>'
        )

    tag_class = "snow-report" if article["tag"] == "Snow Report" else ("gear" if article["tag"] == "Gear" else "")
    dt = datetime.fromisoformat(date_str)

    for k, v in {
        "{{TITLE}}": article["title"],
        "{{EXCERPT}}": article.get("excerpt", ""),
        "{{SLUG}}": slug,
        "{{DATE_FORMATTED}}": dt.strftime("%-d %b %Y"),
        "{{TAG}}": article["tag"],
        "{{TAG_CLASS}}": tag_class,
        "{{HERO_IMAGE}}": hero_html,
        "{{BODY}}": article["body_html"],
    }.items():
        html = html.replace(k, v)

    return html


# ---------------------------------------------------------------------------
# Sitemap & Google Ping
# ---------------------------------------------------------------------------

def generate_sitemap(posts: list[dict]):
    """Generate sitemap.xml for SEO."""
    urls = [
        '  <url><loc>https://piquno.com/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
        '  <url><loc>https://piquno.com/about</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>',
    ]
    for post in posts[:500]:  # Sitemap limit
        date = post["date"][:10]
        urls.append(
            f'  <url><loc>https://piquno.com/posts/{post["slug"]}.html</loc>'
            f'<lastmod>{date}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>'
        )

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sitemap += '\n'.join(urls)
    sitemap += '\n</urlset>'

    (TEMPLATE_DIR / "sitemap.xml").write_text(sitemap)
    log.info(f"Generated sitemap with {len(urls)} URLs")


def generate_rss_feed(posts: list[dict]):
    """Generate a proper RSS feed with recent posts."""
    items = []
    for post in posts[:20]:
        items.append(f"""  <item>
    <title>{post['title']}</title>
    <link>https://piquno.com/posts/{post['slug']}.html</link>
    <description>{post.get('excerpt', '')}</description>
    <pubDate>{post['date']}</pubDate>
    <guid>https://piquno.com/posts/{post['slug']}.html</guid>
  </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Piquno — Japan Ski Journal</title>
  <link>https://piquno.com</link>
  <description>Snow reports, resort intel, and gear picks for skiing Japan.</description>
  <language>en-au</language>
  <atom:link href="https://piquno.com/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
</channel>
</rss>"""

    (TEMPLATE_DIR / "feed.xml").write_text(feed)
    log.info("Generated RSS feed")


def ping_google():
    """Notify Google that the sitemap has been updated."""
    try:
        r = requests.get(
            "https://www.google.com/ping",
            params={"sitemap": "https://piquno.com/sitemap.xml"},
            timeout=10,
        )
        log.info(f"Pinged Google: {r.status_code}")
    except Exception as e:
        log.warning(f"Google ping failed: {e}")

    # Also ping Bing
    try:
        r = requests.get(
            "https://www.bing.com/ping",
            params={"sitemap": "https://piquno.com/sitemap.xml"},
            timeout=10,
        )
        log.info(f"Pinged Bing: {r.status_code}")
    except Exception as e:
        log.warning(f"Bing ping failed: {e}")


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
    r = requests.post(
        f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys",
        headers={
            "Authorization": f"Bearer {NETLIFY_AUTH_TOKEN}",
            "Content-Type": "application/zip",
        },
        data=buf.read(),
        timeout=120,
    )
    r.raise_for_status()
    log.info(f"Deployed: {r.json().get('ssl_url', 'OK')}")


# ---------------------------------------------------------------------------
# Social Auto-Posting (optional — runs after deploy)
# ---------------------------------------------------------------------------

# Bluesky credentials (free, open API)
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")  # e.g. piquno.bsky.social
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")

# Twitter/X credentials (free tier allows 1,500 tweets/month)
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET", "")


def post_to_bluesky(title: str, excerpt: str, url: str):
    """Post to Bluesky via AT Protocol."""
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        return

    try:
        # Create session
        session = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
            timeout=15,
        )
        session.raise_for_status()
        auth = session.json()

        # Build post text
        text = f"{title}\n\n{excerpt}\n\n{url}"
        if len(text) > 300:
            text = f"{title}\n\n{url}"

        # Create post
        r = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {auth['accessJwt']}"},
            json={
                "repo": auth["did"],
                "collection": "app.bsky.feed.post",
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": text,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "facets": [{
                        "index": {"byteStart": text.encode().index(url.encode()), "byteEnd": text.encode().index(url.encode()) + len(url.encode())},
                        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
                    }],
                },
            },
            timeout=15,
        )
        r.raise_for_status()
        log.info(f"Posted to Bluesky: {title}")
    except Exception as e:
        log.warning(f"Bluesky post failed: {e}")


def post_to_twitter(title: str, excerpt: str, url: str):
    """Post to Twitter/X using OAuth 1.0a."""
    if not TWITTER_API_KEY or not TWITTER_ACCESS_TOKEN:
        return

    try:
        import hashlib
        import hmac
        import urllib.parse

        # OAuth 1.0a signature
        tweet_text = f"{title}\n\n{url}"
        if len(tweet_text) > 280:
            # Truncate title to fit
            max_title = 280 - len(url) - 3  # 3 for newlines
            tweet_text = f"{title[:max_title]}…\n\n{url}"

        oauth_nonce = hashlib.md5(str(time.time()).encode()).hexdigest()
        oauth_timestamp = str(int(time.time()))

        params = {
            "oauth_consumer_key": TWITTER_API_KEY,
            "oauth_nonce": oauth_nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": oauth_timestamp,
            "oauth_token": TWITTER_ACCESS_TOKEN,
            "oauth_version": "1.0",
        }

        # Build signature base string
        base_url = "https://api.twitter.com/2/tweets"
        param_string = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(params.items()))
        base_string = f"POST&{urllib.parse.quote(base_url, safe='')}&{urllib.parse.quote(param_string, safe='')}"
        signing_key = f"{urllib.parse.quote(TWITTER_API_SECRET, safe='')}&{urllib.parse.quote(TWITTER_ACCESS_SECRET, safe='')}"

        import base64
        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()

        params["oauth_signature"] = signature

        auth_header = "OAuth " + ", ".join(
            f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
            for k, v in sorted(params.items())
        )

        r = requests.post(
            base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
            },
            json={"text": tweet_text},
            timeout=15,
        )
        r.raise_for_status()
        log.info(f"Posted to Twitter: {title}")
    except Exception as e:
        log.warning(f"Twitter post failed: {e}")


def share_to_socials(posts_data: list[dict]):
    """Share the latest feature article (not daily roundup) to social platforms."""
    # Find the most recent feature article (non-Snow Report)
    feature = None
    for p in posts_data:
        if p["tag"] != "Snow Report":
            feature = p
            break

    if not feature:
        return

    url = f"https://piquno.com/posts/{feature['slug']}.html"
    post_to_bluesky(feature["title"], feature.get("excerpt", ""), url)
    post_to_twitter(feature["title"], feature.get("excerpt", ""), url)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== Piquno Bot v2 starting ===")

    existing_posts = fetch_existing_site()
    existing_slugs = {p["slug"] for p in existing_posts}
    existing_titles = [p["title"] for p in existing_posts]
    seen = load_seen()

    # Copy static files
    static_src = Path(__file__).parent / "site"
    if static_src.exists():
        import shutil
        for f in static_src.rglob("*"):
            if f.is_file():
                dest = TEMPLATE_DIR / f.relative_to(static_src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    POSTS_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch RSS
    rss_items = fetch_rss_items()
    new_items = [it for it in rss_items if it["link"] not in seen]
    if not new_items:
        new_items = rss_items[:5] if rss_items else []

    posts_created = 0

    # --- Daily roundup ---
    roundup = generate_daily_roundup(new_items)
    if roundup:
        slug = slug_from_title(roundup["title"])
        if slug in existing_slugs:
            slug = f"{slug}-{int(time.time()) % 10000}"
        now = datetime.now(timezone.utc)
        image = fetch_hero_image("japan skiing snow powder mountain")
        (POSTS_DIR / f"{slug}.html").write_text(build_post_html(roundup, slug, now.isoformat(), image))
        existing_posts.insert(0, {
            "title": roundup["title"], "slug": slug, "date": now.isoformat(),
            "tag": roundup["tag"], "excerpt": roundup.get("excerpt", ""),
        })
        existing_slugs.add(slug)
        existing_titles.insert(0, roundup["title"])
        posts_created += 1
        log.info(f"Roundup: {slug}")

    # --- Feature article ---
    feature = generate_feature_article(new_items, existing_titles)
    if feature:
        slug = slug_from_title(feature["title"])
        if slug in existing_slugs:
            slug = f"{slug}-{int(time.time()) % 10000}"
        now = datetime.now(timezone.utc)
        # Build a search query from the article title for a relevant image
        image_query = f"japan {feature['tag'].lower()} skiing {feature['title'].split()[0:3]}"
        image = fetch_hero_image(image_query)
        (POSTS_DIR / f"{slug}.html").write_text(build_post_html(feature, slug, now.isoformat(), image))
        existing_posts.insert(0, {
            "title": feature["title"], "slug": slug, "date": now.isoformat(),
            "tag": feature["tag"], "excerpt": feature.get("excerpt", ""),
        })
        posts_created += 1
        log.info(f"Feature: {slug}")

    # Save state
    for it in new_items:
        seen.add(it["link"])
    save_seen(seen)
    DATA_FILE.write_text(json.dumps(existing_posts, indent=2))

    if posts_created > 0:
        generate_sitemap(existing_posts)
        generate_rss_feed(existing_posts)
        deploy_to_netlify()
        ping_google()
        share_to_socials(existing_posts)
        log.info(f"Done — {posts_created} posts published")
    else:
        log.info("No posts generated")


if __name__ == "__main__":
    main()
