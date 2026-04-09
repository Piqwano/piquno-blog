# Piquno — Japan Ski Blog (Auto-posting)

Fully automated Japan skiing blog. A Railway bot generates 2 original posts daily using Claude, then deploys the static site to Netlify.

## Architecture

```
RSS Feeds → Bot (Railway, daily cron) → Claude API → Static HTML → Netlify Deploy
```

## Setup — Step by Step

### 1. Deploy the site to Netlify

1. Push this repo to GitHub
2. In Netlify, click **Add new site → Import an existing project**
3. Connect your GitHub repo
4. Build settings:
   - **Publish directory:** `site`
   - Leave build command empty (static site)
5. Deploy — your site is now live
6. Go to **Site settings → Domain management** and add `piquno.com`
7. Update your DNS to point to Netlify (they'll give you the records)
8. Note your **Site ID** from Site settings → General → Site ID

### 2. Get your API keys

- **Anthropic API key:** https://console.anthropic.com/settings/keys
- **Netlify personal access token:** https://app.netlify.com/user/applications#personal-access-tokens
- **Unsplash access key:** https://unsplash.com/developers — create a free app, copy the Access Key

Optional (for social auto-posting):
- **Bluesky:** Create an account at bsky.app, then go to Settings → App Passwords → create one
- **Twitter/X:** Create a developer account at developer.x.com, create a project/app, generate OAuth 1.0a keys (API key, API secret, Access token, Access secret)

### 3. Deploy the bot to Railway

1. In Railway, create a new project from the same GitHub repo
2. Set the **root directory** to `/` (it uses the Dockerfile)
3. Add these environment variables:
   - `ANTHROPIC_API_KEY` — your key
   - `NETLIFY_AUTH_TOKEN` — your Netlify token
   - `NETLIFY_SITE_ID` — from step 1
   - `UNSPLASH_ACCESS_KEY` — from step 2
   - `CLAUDE_MODEL` (optional) — defaults to `claude-sonnet-4-6`, update when newer models release
   - `BLUESKY_HANDLE` (optional) — e.g. `piquno.bsky.social`
   - `BLUESKY_APP_PASSWORD` (optional) — app password from Bluesky settings
   - `TWITTER_API_KEY` (optional) — from Twitter/X developer portal
   - `TWITTER_API_SECRET` (optional) — from Twitter/X developer portal
   - `TWITTER_ACCESS_TOKEN` (optional) — from Twitter/X developer portal
   - `TWITTER_ACCESS_SECRET` (optional) — from Twitter/X developer portal
4. Go to **Settings → Cron Schedule** and set: `0 14 * * *`
   (Runs at 2pm UTC daily — 9am US East, 3pm UK, 12am AEST)
5. Deploy

### 4. Custom domain (piquno.com)

Update your domain's DNS:
- If using Netlify DNS: point nameservers to Netlify
- If using external DNS: add a CNAME record pointing to your Netlify subdomain

## How it works

Each day the bot:
1. Fetches RSS feeds from skiing news sites
2. Filters for Japan-related content
3. Generates a daily roundup + feature article via Claude API
4. Fetches hero images from Unsplash for each post
5. Builds static HTML, sitemap.xml, and RSS feed
6. Deploys to Netlify
7. Pings Google and Bing to index new content
8. Shares the feature article to Bluesky and Reddit (if configured)

If no Japan skiing news is found, it writes evergreen content instead (resort guides, gear tips, etc).

## Newsletter

The site includes a Buttondown email signup form. To activate it:
1. Create a free account at https://buttondown.com
2. Set your username to `piquno` (or update the form action URL in index.html and about.html)
3. Subscribers get added automatically when they sign up on the site
4. Send a weekly digest from the Buttondown dashboard, or enable their auto-digest

## Costs

- **Claude API:** ~$0.05-0.10/day (2 Sonnet calls)
- **Railway:** within free tier for a cron job
- **Netlify:** free tier (static hosting)
- **Total:** ~$2-3/month

## Monetisation

Once you have traffic, add:
1. **Affiliate links** — ski gear (evo, Backcountry), accommodation (Booking.com, Agoda), travel insurance
2. **Google AdSense** — add the script tag to the templates
3. **Sponsored content** — once you have authority in the niche

## Customisation

- Edit `RSS_FEEDS` in `bot.py` to add/remove sources
- Edit `POSTS_PER_RUN` to change daily output (more = higher API costs)
- Edit `site/index.html` for homepage design
- Edit `site/post-template.html` for post layout
