# Используем легковесный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Устанавливаем системные зависимости для сборки пакетов баз данных
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем и устанавливаем Python-библиотеки
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt python-docx openpyxl streamlit

# ВАЖНО: копируем все три наших новых файла в контейнер
COPY database.py agent.py main.py ./

# Создаем папку под локальную векторную базу
RUN mkdir -p /app/local_assistant_db

# Теперь запускаем main.py вместо bot.py
CMD ["python", "main.py"]
