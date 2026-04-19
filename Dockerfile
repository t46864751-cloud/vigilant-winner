FROM docker:24-dind

# Устанавливаем Python
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py .

# Запускаем dockerd и бот
CMD ["sh", "-c", "dockerd --host=unix:///var/run/docker.sock --storage-driver=vfs & sleep 5 && python3 main.py"]
