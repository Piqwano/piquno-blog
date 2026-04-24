# Piquno — Japan Ski Blog (Auto-posting)

Fully automated Japan skiing blog. A Railway bot generates 2 original posts daily using Claude, then deploys the static site to Netlify and shares to Bluesky + X/Twitter.

## Architecture

```
RSS Feeds → Bot (Railway, daily cron) → Claude API → Static HTML →
    Netlify zip deploy → Bluesky + X share
```

Key design choices:

- **Zip deploy via Netlify API** (`/sites/<id>/deploys`), *not* a GitHub-triggered build. This means Netlify honors `site/_headers` and `site/_redirects` inside the publish directory. The repo-root `netlify.toml` is kept as a fallback for anyone re-enabling GitHub builds later; the two files mirror each other.
- **Server-rendered homepage and tag pages.** The bot rewrites `index.html` post-list at deploy time and generates static `/tags/<slug>/index.html` pages, so crawlers, social previews, and no-JS clients see real content.
- **Full OG + Twitter + JSON-LD meta** on every post, injected by the bot into `post-template.html`.
- **State persistence via the live site itself**: on each run the bot re-fetches `/posts/index.json` and existing post HTML from `piquno.com`, then uploads the merged set. No external DB needed.

## Directory layout

```
.
├── bot/
│   ├── bot.py              # Main bot — 1.5k lines, single file on purpose
│   └── requirements.txt
├── site/
│   ├── index.html          # Homepage (bot rewrites post-list block at deploy)
│   ├── about.html
│   ├── post-template.html  # Template with {{TITLE}}, {{BODY}}, {{SOCIAL_META}}, etc.
│   ├── 404.html
│   ├── styles.css          # Shared stylesheet
│   ├── favicon.svg
│   ├── site.webmanifest
│   ├── _headers            # Netlify security + cache headers
│   ├── _redirects          # Netlify redirect rules
│   ├── feed.xml            # Bot-generated
│   └── posts/
│       └── index.json      # Bot-generated list of all posts
├── Dockerfile
├── netlify.toml            # Mirror of _headers/_redirects for GitHub builds
└── README.md
```

## Setup — Step by Step

### 1. Deploy the site to Netlify

1. Push this repo to GitHub.
2. In Netlify, **Add new site → Import an existing project** → connect the GitHub repo.
3. Build settings:
   * **Publish directory:** `site`
   * **Build command:** leave empty (static site; the bot does the work externally).
4. Deploy. The site will be live but empty.
5. **Site settings → Domain management** → add `piquno.com`, follow DNS instructions.
6. Note your **Site ID** (Site settings → General → Site ID).

### 2. Get your API keys

* **Anthropic API key:** <https://console.anthropic.com/settings/keys>
* **Netlify personal access token:** <https://app.netlify.com/user/applications#personal-access-tokens>
* **Pexels API key:** <https://www.pexels.com/api/> — sign up, request an API key (free, no credit card).

Optional (for social auto-posting):

* **Bluesky:** create an account, then Settings → App Passwords → create one.
* **Twitter/X:** developer account at developer.x.com → create a project & app → generate OAuth 1.0a keys (API key, API secret, Access token, Access secret).
  * Note: X free-tier is **500 writes/month** as of 2024. Two posts a day ≈ 60/month, comfortably inside the limit.

### 3. Generate favicon/OG assets

The SVG favicon works in all modern browsers. You still want the PNGs and a default OG image for iOS and social previews:

```bash
# Requires ImageMagick and rsvg-convert (or use inkscape / any raster tool)
cd site/
rsvg-convert -w 512 favicon.svg -o icon-512.png
rsvg-convert -w 192 favicon.svg -o icon-192.png
rsvg-convert -w 180 favicon.svg -o apple-touch-icon.png
# Classic favicon.ico (multi-size)
convert -background none favicon.svg -define icon:auto-resize=16,32,48 favicon.ico

# Default OG image (1200×630). Start from any landscape photo you have rights to,
# or generate a simple branded fallback:
convert -size 1200x630 gradient:'#1a1d23-#2e6fb5' \
  -font 'DejaVu-Serif' -fill '#f7f8fa' -gravity center \
  -pointsize 88 -annotate +0-40 'Piquno' \
  -pointsize 34 -annotate +0+60 'Japan Ski Journal' \
  og-default.jpg
```

Commit those files alongside the SVG.

### 4. Deploy the bot to Railway

1. Railway → new project → deploy from same GitHub repo.
2. Root directory: `/` (uses the Dockerfile).
3. Add these environment variables:

   | Variable | Required | Notes |
   |----------|----------|-------|
   | `ANTHROPIC_API_KEY` | yes | |
   | `NETLIFY_AUTH_TOKEN` | yes | |
   | `NETLIFY_SITE_ID` | yes | From step 1. |
   | `PEXELS_API_KEY` | yes | Hero images. |
   | `CLAUDE_MODEL` | no | Defaults to `claude-sonnet-4-6`. |
   | `SITE_URL` | no | Defaults to `https://piquno.com`. Useful for staging. |
   | `BLUESKY_HANDLE` | no | e.g. `piqunoski.bsky.social`. |
   | `BLUESKY_APP_PASSWORD` | no | App password, not login password. |
   | `TWITTER_API_KEY` | no | OAuth 1.0a consumer key. |
   | `TWITTER_API_SECRET` | no | |
   | `TWITTER_ACCESS_TOKEN` | no | User access token. |
   | `TWITTER_ACCESS_SECRET` | no | |

4. **Settings → Cron Schedule:** `0 14 * * *` (14:00 UTC daily ≈ 11pm AEST).
5. Deploy.

### 5. Newsletter (optional)

The homepage and about page include a Buttondown signup form. Create a free account at <https://buttondown.com>, set your username to `piquno`, or update the `action="…/embed-subscribe/…"` URL in both HTML files to your username.

## How it works

Each day the bot:

1. Fetches `/posts/index.json` + every existing post HTML from the live site (so the next deploy includes all old content).
2. Pulls RSS from a set of skiing news sites (see `RSS_FEEDS` in `bot.py`).
3. Filters for Japan-relevant items by keyword.
4. Calls Claude twice — once for a daily roundup, once for a feature article.
5. Fetches a relevant Pexels image for each.
6. Renders post HTML with proper OG/Twitter/JSON-LD meta.
7. Regenerates sitemap.xml, feed.xml, robots.txt.
8. Rewrites `index.html` post-list with server-rendered cards.
9. Generates `/tags/<slug>/index.html` for every content tag and featured resort.
10. Zips everything and deploys to Netlify.
11. Shares the new posts to Bluesky (with link preview embed + hashtag facets) and X/Twitter.

If no Japan skiing news is found, it still writes evergreen content (resort guides, gear tips, etc.).

## Costs

* **Claude API:** ~$0.05–0.10/day (2 Sonnet calls).
* **Railway:** within free tier for a cron job.
* **Netlify:** free tier (static hosting).
* **Total:** ~$2–3/month.

## Customisation

* `RSS_FEEDS` in `bot.py` — add/remove sources.
* `RESORTS` dict in `bot.py` — Japan ski resort database used in prompts.
* `FEATURED_RESORTS` in `bot.py` — resorts that get dedicated `/tags/<slug>/` pages and homepage links.
* `site/styles.css` — shared styling for every page.
* `site/post-template.html` — post layout. Placeholders: `{{TITLE}}`, `{{TITLE_TEXT}}`, `{{EXCERPT}}`, `{{SLUG}}`, `{{DATE_FORMATTED}}`, `{{DATE_ISO}}`, `{{TAG}}`, `{{TAG_SLUG}}`, `{{TAG_CLASS}}`, `{{HERO_IMAGE}}`, `{{BODY}}`, `{{READ_NEXT}}`, `{{SOCIAL_META}}`, `{{JSON_LD}}`, `{{LANG}}`.

## Monetisation

Piquno currently ships no monetisation — no ads, no affiliate links, no tracking. If you want to add revenue later, good options are:

1. **Sponsored content** — once you have authority in the niche, resorts and gear brands will pay for featured placements.
2. **Newsletter sponsorship** — if you build a meaningful subscriber base via Buttondown, newsletter ads pay well per subscriber.
3. **Display ads** (e.g. Google AdSense) — fastest to set up, usually the worst reader experience. Inject a script tag in `site/post-template.html` if you want to try it.
