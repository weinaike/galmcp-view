FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir -r requirements.txt

COPY . .

# Create data directories for galfit results and analysis images
RUN mkdir -p /data/galfit_example /data/analysis_images

ENV GALFIT_BASE_PATH=/data/galfit_example

EXPOSE 35091

CMD ["python", "app.py"]
