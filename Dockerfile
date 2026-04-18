FROM python:3.12-slim

# Non-root user so the container doesn't run as root
RUN groupadd --system --gid 1000 bot && \
    useradd  --system --uid 1000 --gid bot --create-home bot

WORKDIR /app

# Install deps first for Docker layer cache
COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY bot/bot.py .
COPY site/ ./site/
# bot.py reads post-template.html from its own directory (Path(__file__).parent).
# We intentionally keep a single copy of it next to bot.py, in addition to the
# one inside site/. The bot removes the site/ copy from the deploy output so
# the template never ships to production.
COPY site/post-template.html ./post-template.html

RUN chown -R bot:bot /app
USER bot

# Force unbuffered stdout/stderr so Railway shows logs live instead of on exit
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
