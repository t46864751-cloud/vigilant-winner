FROM python:3.11-slim

WORKDIR /app

# Устанавливаем утилиты для namespaces и chroot
RUN apt-get update && apt-get install -y \
    uidmap \
    libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
