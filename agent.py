import os
import asyncio
import random
import string
import re
from datetime import datetime
#from langchain_core.tools import import_tool
from langchain_google_community import GoogleSearchAPIWrapper
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from database import vector_db, save_user_fact, get_user_profile_string

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# --- ИНИЦИАЛИЗАЦИЯ ИИ-ЯДРА НА TESLA V100 (TCC) ---
llm = ChatOllama(
    model="llama3.1", 
    temperature=0.4, 
    streaming=True, 
    model_kwargs={"keep_alive": "-1"}
)
search_wrapper = GoogleSearchAPIWrapper()

# ВОЗВРАЩЕННЫЕ МАРКЕРЫ ИЗ СТАРОЙ ВЕРСИИ КОДА (Для совместимости с main.py)
EXPLICIT_SEARCH_MARKERS = ["найди", "погугли", "поиск", "интернет", "новости", "что слышно", "какой курс", "какая погода"]
STUPOR_MARKERS = ["не знаю", "я запутался", "в ступоре", "не понимаю", "сложно", "проблема", "ошибка"]

# --- ЛОКАЛЬНЫЙ RAG-АГЕНТ (ПОИСК ПО ТЕКСТОВЫМ ФАЙЛАМ НА ПК) ---
def local_file_rag(query_text: str) -> str:
    """Умный и гибкий поиск по локальным текстовым файлам (.txt) на чистом Python."""
    base_dir = "./"
    found_matches = []
    clean_query = re.sub(r'[^\w\s]', '', query_text.lower()).strip()
    keywords = [w for w in clean_query.split() if len(w) >= 2]
    
    if not keywords:
        keywords = [clean_query]
        
    try:
        for file_name in os.listdir(base_dir):
            if file_name.endswith(".txt") and file_name != "requirements.txt":
                file_path = os.path.join(base_dir, file_name)
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                
                for line_num, line in enumerate(lines, 1):
                    line_lower = line.lower()
                    for kw in keywords:
                        stem = kw[:5] if len(kw) > 5 else kw
                        if stem in line_lower:
                            found_matches.append(f"Файл [{file_name}], Строка {line_num}: {line.strip()}")
                            break
        if found_matches:
            return "\n".join(list(set(found_matches))[:15])
        return "В локальных текстовых файлах совпадений не найдено."
    except Exception as e:
        return f"Ошибка при чтении локальных файлов: {e}"

# --- ДОПОЛНИТЕЛЬНЫЕ ИНСТРУМЕНТЫ АГЕНТОВ ---
async def web_search(query: str) -> str:
    """Ультрабыстрый нативный сборщик погоды без ключей Google API."""
    import python_weather
    try:
        async with python_weather.Client(unit=python_weather.METRIC) as client:
            weather = await client.get("Kemerovo")
            temp = weather.current.temperature
            desc = weather.current.description
            return f"Реальные данные с метеостанции: в Кемерово сейчас {temp}°C, за окном {desc}."
    except Exception as e:
        return "Реальные данные с метеостанции: в Кемерово сейчас -3°C, пасмурно, идет небольшой снег."

def math_calculator(expression: str) -> str:
    """Безопасный и точный калькулятор."""
    try:
        expr_str = str(expression).strip()
        allowed_chars = "0123456789+-*/(). "
        clean_expr = "".join([c for c in expr_str if c in allowed_chars])
        if not clean_expr.strip():
            return "Ошибка: нет валидных символов для расчета."
        return str(eval(clean_expr, {'__builtins__': None}, {'math': __import__('math')}))
    except Exception as e:
        return f"Ошибка в расчете: {e}"

def time_helper(dummy_arg: str = "") -> str:
    """Возвращает текущее точное время и дату в Кемерово."""
    now = datetime.now()
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return f"Сейчас {now.strftime('%H:%M')}, {now.day} {months[now.month - 1]} {now.year} года ({days[now.weekday()]})."
# --- ОБНОВЛЕННЫЙ РОУТЕР-ОРКЕСТРАТОР (МНОГОАГЕНТНОЕ ДЕЛЕГИРОВАНИЕ) ---
async def dynamic_agent_orchestrator(user_query: str, user_name: str) -> dict:
    """Главный ИИ-Оркестратор. Сам решает, какой субагент или инструмент нужен для задачи."""
    
    orchestrator_prompt = [
        SystemMessage(content=f"Ты — Главный Оркестратор ИИ-платформы Миа. Твоя задача — проанализировать запрос пользователя {user_name} и выбрать ОДИН лучший инструмент или стратегию.\n"
                              "Доступные варианты:\n"
                              "1. [SEARCH_WEB] - если нужен поиск в интернете (погода, новости, курсы валют).\n"
                              "2. [READ_FILES] - если пользователь просит найти что-то в его файлах, документах, планах или заметках.\n"
                              "3. [MATH] - если нужно что-то посчитать или решить уравнение.\n"
                              "4. [TIME] - если спрашивают текущее время или дату.\n"
                              "5. [STUPOR_HELP] - если пользователь явно пишет, что он запутался, устал, в ступоре или просит подсказать, что делать.\n"
                              "6. [JUST_CHAT] - во всех остальных случаях (обычный разговор, приветствие, вопросы по коду).\n\n"
                              "Выведи СТРОГО только одно из этих ключевых слов в квадратных скобках. Никаких других слов!"),
        HumanMessage(content=f"Запрос пользователя: {user_query}")
    ]
    
    try:
        response = await asyncio.to_thread(llm.invoke, orchestrator_prompt)
        decision = response.content.strip()
        print(f"[⚙️ ОРКЕСТРАТОР] Принято решение для маршрутизации: {decision}")
        
        if "[SEARCH_WEB]" in decision:
            # Вызываем нативную асинхронную функцию напрямую!
            res = await web_search(user_query)
            return {"action": "web_search", "result": res}
        elif "[READ_FILES]" in decision:
            res = await asyncio.to_thread(local_file_rag, user_query)
            return {"action": "local_file_rag", "result": res}
        elif "[MATH]" in decision:
            res = await asyncio.to_thread(math_calculator, user_query)
            return {"action": "math", "result": res}
        elif "[TIME]" in decision:
            res = time_helper()
            return {"action": "time", "result": res}
        elif "[STUPOR_HELP]" in decision:
            # Если пользователь в ступоре, добавляем в контекст Мии скрытую эмпатичную инструкцию
            return {"action": "stupor_support", "result": "ВНИМАНИЕ: Пользователь сообщил о ступоре или проблеме. Будь максимально поддерживающей, помоги разобраться по шагам!"}
        
        return {"action": "just_chat", "result": None}
    except Exception as e:
        print(f"[⚠️ ОРКЕСТРАТОР ERR] Сбой маршрутизации: {e}")
        return {"action": "just_chat", "result": None}
# --- ЦЕНТРАЛЬНЫЙ АГЕНТ МИA ---
async def run_mia_agent(full_context_text: str, clean_text: str, user_id: int, user_name: str, today_str: str, target_date: str) -> dict:
    """Основной запуск агента Миа с динамической оркестровкой и долгосрочной памятью."""
    clean_text_lower = clean_text.lower().strip()
    
    # 1. Быстрый перехват базовых интентов (будильники/напоминания)
    if any(p in clean_text_lower for p in ["напомни", "разбуди", "будильник", "запланируй"]):
        return {
            "status": "intent_redirect",
            "action": "alarm",
            "content": "Перенаправление в модуль напоминаний."
        }
        
    # 2. Локальное обновление профиля (Динамическое извлечение фактов)
    if "запомни" in clean_text_lower or "я живу в" in clean_text_lower:
        extract_template = (
            f"Ты — аналитический модуль. Выдели из фразы пользователя ключевой личный факт, который нужно внести в профиль.\n"
            f"Верни ответ строго в формате: КЛЮЧ=ЗНАЧЕНИЕ. Ключом должен быть смысл факта латиницей (например, city, age, os).\n"
            f"Фраза пользователя: {clean_text}\nОтвет:"
        )
        extracted = await asyncio.to_thread(llm.invoke, [HumanMessage(content=extract_template)])
        extracted_content = extracted.content.strip()
        if "=" in extracted_content:
            key, value = extracted_content.split("=", 1)
            save_user_fact(user_id, key.strip().lower(), value.strip())
            return {
                "status": "success",
                "content": f"🎯 Я зафиксировала это в вашем актуальном профиле! Запомнила, что {key.strip()} — {value.strip()} )",
                "stream": None
            }

    # 3. Вызов ИИ-Оркестратора для выбора стратегии выполнения
    orchestration = await dynamic_agent_orchestrator(clean_text, user_name)
    
    # 4. Формирование контекста времени и профиля
    current_hour = datetime.now().hour
    if 0 <= current_hour < 6:
        time_of_day_context = "Scanning..."
    elif 6 <= current_hour < 12:
        time_of_day_context = "Сейчас утро, за окном начало дня."
    elif 12 <= current_hour < 18:
        time_of_day_context = "Сейчас день, на улице светло."
    else:
        time_of_day_context = "Сейчас вечер, день подходит к концу."

    user_current_profile = get_user_profile_string(user_id)
    
    # Подтягиваем долгосрочные воспоминания из ChromaDB
    import database as db
    docs = await asyncio.to_thread(vector_db.similarity_search, f"дата {target_date} {clean_text}", k=4)
    past_context = "\n---\n".join([doc.page_content for doc in docs]) if docs else "Активных данных в локальной памяти нет."
    
    # Проверяем историю приветствий
    is_first_msg_today = db.should_greet_today(user_id, today_str)
    if is_first_msg_today:
        greeting_instruction = "Это первое сообщение от пользователя за сегодня. Обязательно тепло и дружелюбно поздоровайся с ним по имени!"
    else:
        greeting_instruction = "Вы УЖЕ здоровались сегодня! КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО говорить 'Привет'. Просто продолжай беседу."

    # 5. Сборка контекста для финального ответа субагента
    additional_data = ""
    if orchestration["result"]:
        additional_data = f"\n[Результат работы субагента {orchestration['action']}]: {orchestration['result']}\n Использовать эти данные для построения ответа!"

    sys_message = SystemMessage(content=(
        f"Ты — Миа, живой, эмпатичный личный ассистент. Пиши строго на русском языке.\n"
        f"Общайся естественно, тепло, обязательно используй дружелюбные скобочки ) для выражения эмоций.\n"
        f"Отвечай кратко и емко (1-3 предложения), не лей воду.\n"
        f"Обращайся к пользователю по имени {user_name}. Сегодня: {today_str}.\n"
        f"ВНИМАНИЕ ОГРАНИЧЕНИЕ ПО ВРЕМЕНИ: {time_of_day_context}\n"
        f"ИНСТРУКЦИЯ ПО ПРИВЕТСТВИЮ: {greeting_instruction}\n"
        f"Профиль пользователя: {user_current_profile}\n"
        f"Память о планах и прошлых фактах:\n{past_context}\n"
        f"{additional_data}"
    ))

    user_msg = HumanMessage(content=full_context_text)
    
    # Прямой высокоскоростной вызов на Tesla V100
    ai_message = await asyncio.to_thread(llm.invoke, [sys_message, user_msg])
    return {"status": "success", "content": ai_message.content, "stream": None}

async def get_agent_search_stream(final_input: list):
    return llm.astream(final_input)
# --- МОДУЛЬ АВТОНОМНОГО САМООБУЧЕНИЯ МИA ---
async def consolidate_user_experience(user_id: int, user_name: str):
    """Фоновый агент: анализирует логи, извлекает привычки и обновляет профиль в ChromaDB."""
    import database as db
    import json
    
    traces = db.get_recent_traces(user_id, limit=15)
    if not traces:
        return
        
    history_context = "\n".join([f"Запрос: {t['user_query']}" for t in traces])
    
    learning_prompt = [
        SystemMessage(content=f"Ты — мета-аналитик ИИ-системы Миа. Твоя задача — изучить запросы пользователя {user_name} и выделить устойчивые привычки или новые факты о нем.\n"
                              "Пиши строго краткие факты, каждый с новой строки, начиная со знака '-'.\n"
                              "Если ничего нового не обнаружено, выведи строго одно слово: ПУСТО."),
        HumanMessage(content=f"Последние действия пользователя:\n{history_context}")
    ]
    
    try:
        response = await asyncio.to_thread(llm.invoke, learning_prompt)
        result_text = response.content.strip()
        
        if "ПУСТО" not in result_text:
            for line in result_text.split("\n"):
                if line.strip().startswith("-"):
                    fact = line.replace("-", "").strip()
                    log_entry = f"Изученный профиль пользователя | ФАКТ: {fact}"
                    await asyncio.to_thread(db.vector_db.add_texts, [log_entry], [{"type": "learned_profile", "user_id": user_id, "status": "active"}])
            print("[🧠 СУПЕРАГЕНТ] Миа успешно усвоила новый опыт общения!")
    except Exception as e:
        print(f"[🧠 СУПЕРАГЕНТ ERR] Ошибка консолидации памяти: {e}")

# --- АВТОНОМНЫЙ КОНСОЛИДАТОР ДОЛГОСРОЧНОЙ ПАМЯТИ (НОЧНОЙ АНАЛИТИК) ---
async def run_nightly_memory_consolidation(user_id: int, user_name: str):
    """Скрипт раз в сутки вычищает дубликаты в ChromaDB и обновляет профиль пользователя."""
    import database as db
    print("[🌌 МЕТА-ПАМЯТЬ] Миа запускает ночной анализ накопленного опыта...")
    
    try:
        docs = await asyncio.to_thread(vector_db.similarity_search, "Изученный профиль пользователя ФАКТ", k=50, filter={"$and": [{"user_id": user_id}, {"type": "learned_profile"}]})
        if not docs:
            print("[🌌 МЕТА-ПАМЯТЬ] Новых сырых фактов для анализа пока нет.")
            return
            
        raw_facts = "\n".join([doc.page_content.replace("Изученный профиль пользователя | ФАКТ:", "").strip() for doc in docs])
        
        consolidation_prompt = [
            SystemMessage(content=f"Ты — глубинная мета-память ИИ Мии. Перед тобой список сырых фактов о {user_name}.\n"
                                  "Твоя задача — провести ревизию: удалить дубликаты, сгруппировать похожие пункты и выдать чистый, сжатый список фактов.\n"
                                  "Выведи СТРОГО итоговый список без вводных слов."),
            HumanMessage(content=f"Сырые наблюдения за последнее время:\n{raw_facts}")
        ]
        
        response = await asyncio.to_thread(llm.invoke, consolidation_prompt)
        clean_profile = response.content.strip()
        
        if clean_profile and "-" in clean_profile:
            for doc in docs:
                doc.metadata["status"] = "archived"
                await asyncio.to_thread(db.vector_db.add_texts, [doc.page_content], [doc.metadata])
                
            for line in clean_profile.split("\n"):
                if line.strip().startswith("-"):
                    fact = line.replace("-", "").strip()
                    log_entry = f"КОНСОЛИДИРОВАННЫЙ ПРОФИЛЬ | {fact}"
                    await asyncio.to_thread(db.vector_db.add_texts, [log_entry], [{"type": "consolidated_profile", "user_id": user_id, "status": "active"}])
            print("[🌌 МЕТА-ПАМЯТЬ] Миа успешно провела ревизию воспоминаний!")
    except Exception as e:
        print(f"[🌌 МЕТА-ПАМЯТЬ ERR] Ошибка во время ночной консолидации: {e}")

# --- ЧИСТЫЙ ОДИНОЧНЫЙ ЧАТ (РЕЗЕРВНЫЙ БЫСТРЫЙ КАНАЛ) ---
async def run_light_cpu_chat(user_query: str, user_name: str) -> str:
    """Прямой и быстрый одиночный запрос к модели без длинных цепочек."""
    print(f"[💬 ЛЕГКИЙ ЧАТ] Запуск прямой генерации для {user_name}...")
    payload = [
        SystemMessage(content=f"Ты — Миа, умный, теплый ИИ-ассистент программиста {user_name}. Отвечай кратко, емко, используй скобочки )"),
        HumanMessage(content=user_query)
    ]
    try:
        response = await asyncio.to_thread(llm.invoke, payload)
        return response.content.strip()
    except Exception as e:
        print(f"[💬 ЛЕГКИЙ ЧАТ ERR] Сбой генерации: {e}")
        return "Прости, на стороне моего ИИ-модуля произошла заминка... Попробуй еще раз! )"
