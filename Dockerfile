FROM python:3.12-slim

WORKDIR /app

COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/bot.py .
COPY site/ ./site/
COPY site/post-template.html ./post-template.html

CMD ["python", "bot.py"]
