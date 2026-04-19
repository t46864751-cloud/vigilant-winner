# Используем легкий образ Python 3.11
FROM python:3.11-slim

# Устанавливаем системные библиотеки для отрисовки графиков (matplotlib) и картинок (PIL/Pillow)
# ИСПРАВЛЕНО: libgl1-mesa-glx заменено на libgl1 для совместимости с Debian Trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY main.py .

# Запускаем бота
CMD ["python", "main.py"]
