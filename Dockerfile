# Используем легкий образ Python 3.11
FROM python:3.11-slim

# ОБЯЗАТЕЛЬНЫЙ ШАГ ДЛЯ КАРТИНОК:
# Устанавливаем системные библиотеки (C-зависимости), нужные для Pillow и Matplotlib.
# Без них при сохранении картинок бот будет выдавать ошибки типа "_imaging не найден".
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию /app (совпадает с путем из твоих логов)
WORKDIR /app

# Копируем файл с зависимостями и устанавливаем их
# Флаг --no-cache-dir экономит место на диске
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код твоего бота в контейнер
COPY main.py .

# Запускаем бота при старте контейнера
CMD ["python", "main.py"]
