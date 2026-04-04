FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for galfit results
RUN mkdir -p /data/galfit_example

ENV GALFIT_BASE_PATH=/data/galfit_example

EXPOSE 35091

CMD ["python", "app.py"]
