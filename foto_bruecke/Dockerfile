ARG BUILD_FROM
FROM ${BUILD_FROM}

# exiftool: Metadaten lesen/schreiben, ffmpeg: Video-Vorschaubilder
RUN apk add --no-cache exiftool ffmpeg tzdata

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && (pip install --no-cache-dir "pillow-heif>=0.16" \
        || echo "pillow-heif nicht verfügbar - HEIC-Vorschau über eingebettetes Vorschaubild")

COPY app /app
WORKDIR /app

CMD [ "python3", "-u", "/app/server.py" ]
