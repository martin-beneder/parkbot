FROM python:3.11-slim

RUN apt-get update -o Acquire::ForceIPv4=true \
    && apt-get install -y --no-install-recommends \
        android-tools-adb \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY scripts/ /app/scripts/
COPY frontend/ /frontend/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
