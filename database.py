import os
import sqlite3
from datetime import datetime, timedelta
from langchain_community.vectorstores import Chroma
from langchain_ollama import OllamaEmbeddings

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Было:
# embeddings = OllamaEmbeddings(model="nomic-embed-text")
# vector_db = Chroma(persist_directory="./local_assistant_db", embedding_function=embeddings)

# Стало:
# Очищенная и правильная конфигурация для langchain_ollama
embeddings = OllamaEmbeddings(
    model="nomic-embed-text"
)

vector_db = Chroma(
    persist_directory="./local_assistant_db", 
    embedding_function=embeddings
)

DAYS_OF_WEEK = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

def init_alarm_db():
    """Инициализация базы данных SQLite для будильников, профилей и трассировки агентов."""
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    # Таблица пользователей для рассылки
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY
        )
    """)
    # Таблица для хранения личных фактов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER,
            fact_key TEXT,
            fact_value TEXT,
            PRIMARY KEY (user_id, fact_key)
        )
    """)
    # НОВАЯ ТАБЛИЦА: Логи для графического веб-интерфейса трассировки агентов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_name TEXT,
            user_query TEXT,
            intent TEXT,
            agent_chain TEXT,
            execution_time_ms INTEGER,
            raw_payload TEXT
        )
    """)
    conn.commit()
    conn.close()

init_alarm_db()

# НОВАЯ ФУНКЦИЯ ДЛЯ ЗАПИСИ ТРАССИРОВКИ
def add_agent_trace(user_name: str, query: str, intent: str, chain: str, time_ms: int, payload: str):
    """Сохраняет шаг обмена данными между агентами в базу для вывода на дашборд."""
    try:
        conn = sqlite3.connect("users_alarms.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agent_traces (user_name, user_query, intent, agent_chain, execution_time_ms, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_name, query, intent, chain, time_ms, payload))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TRACE ERR] Ошибка записи лога: {e}")

def parse_target_date(text_lower: str) -> tuple:
    import re
    now = datetime.now()
    
    # Регулярное выражение для поиска дат формата ДД.ММ.ГГГГ или ДД.ММ
    date_match = re.search(r'\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b', text_lower)
    
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year = int(date_match.group(3)) if date_match.group(3) else now.year
        try:
            target = datetime(year, month, day)
        except Exception:
            target = now
    elif "послезавтра" in text_lower:
        target = now + timedelta(days=2)
    elif "завтра" in text_lower:
        target = now + timedelta(days=1)
    elif "вчера" in text_lower:
        target = now - timedelta(days=1)
    else:
        target = now
        
    return target.strftime("%d.%m.%Y"), DAYS_OF_WEEK[target.weekday()]

def get_user_alarms():
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def register_user(chat_id: int):
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

# --- НОВЫЕ ФУНКЦИИ ДЛЯ СИСТЕМЫ ПАМЯТИ ФАКТОВ ---

def save_user_fact(user_id: int, key: str, value: str):
    """Сохраняет личный факт о пользователе в базу данных."""
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_profile (user_id, fact_key, fact_value)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, fact_key) DO UPDATE SET fact_value=excluded.fact_value
    """, (user_id, key, value))
    conn.commit()
    conn.close()

def get_user_profile_string(user_id: int) -> str:
    """Собирает все известные факты о пользователе в одну текстовую строку для ИИ."""
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    cursor.execute("SELECT fact_key, fact_value FROM user_profile WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "Дополнительных личных данных (город, предпочтения) пока не сохранено."
        
    facts = [f"- {row[0]}: {row[1]}" for row in rows]
    return "\n".join(facts)
# --- МОДУЛЬ КОНТРОЛЯ ЕЖЕДНЕВНЫХ ПРИВЕТСТВИЙ ---

def init_greeting_db():
    """Создает таблицу для фиксации даты последнего приветствия пользователя."""
    conn = sqlite3.connect("users_alarms.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_greetings (
            user_id INTEGER PRIMARY KEY,
            last_greeting_date TEXT
        )
    """)
    conn.commit()
    conn.close()

# Инициализируем при загрузке модуля
init_greeting_db()

def should_greet_today(user_id: int, today_str: str) -> bool:
    """
    Проверяет, нужно ли сегодня здороваться с пользователем.
    Если это первое сообщение за день — возвращает True и записывает дату.
    Если уже здоровалась — возвращает False.
    """
    try:
        conn = sqlite3.connect("users_alarms.db")
        cursor = conn.cursor()
        
        cursor.execute("SELECT last_greeting_date FROM user_greetings WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row is None:
            # Пользователь пишет впервые вообще
            cursor.execute("INSERT INTO user_greetings (user_id, last_greeting_date) VALUES (?, ?)", (user_id, today_str))
            conn.commit()
            conn.close()
            return True
            
        last_date = row[0]
        if last_date != today_str:
            # Сегодня новый день, нужно поздороваться
            cursor.execute("UPDATE user_greetings SET last_greeting_date = ? WHERE user_id = ?", (today_str, user_id))
            conn.commit()
            conn.close()
            return True
            
        # Уже здоровались сегодня
        conn.close()
        return False
    except Exception as e:
        print(f"[GREETING ERR] Ошибка проверки даты приветствия: {e}")
        return True # В случае сбоя лучше поздороваться, чем упасть
def get_recent_traces(user_id: int, limit: int = 20) -> list:
    """Достает последние успешные диалоги пользователя для анализа агентом-обучения."""
    import json
    try:
        conn = sqlite3.connect("users_alarms.db")
        # Настраиваем чтение строк как словарей
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT user_query, agent_chain, raw_payload 
            FROM agent_traces 
            WHERE user_name = (SELECT last_greeting_date FROM user_greetings WHERE user_id = ?) 
            ORDER BY timestamp DESC LIMIT ?
        """, (user_id, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        # Переводим в обычный список словарей
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"[DB ERR] Не удалось достать логи для обучения: {e}")
        return []
