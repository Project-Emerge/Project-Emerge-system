# OTA update server for the dropbot firmware.
FROM python:3.14-slim

WORKDIR /app

COPY ota-server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ota-server/app.py ./app.py

ENV PORT=80
ENV DATA_DIR=/data

EXPOSE 80
VOLUME ["/data"]

CMD ["python", "app.py"]
