FROM python:3.13-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kavita_anilist_sync kavita_anilist_sync
USER 1000:1000
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8091
CMD ["python", "-m", "kavita_anilist_sync"]
