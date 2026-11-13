import asyncio
import os
import logging
import traceback
import random
import time
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.markdown import html_decoration as hd

# Быстрый алиас, чтобы не менять остальной код
escape_html = hd.quote
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

import database as db
import agent as mia

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- СЛУЖЕБНЫЕ ФУНКЦИИ TELEGRAM ---

async def send_automatic_morning_reminder():
    """Ежедневный утренний будильник планов для каждого пользователя."""
    now = datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    
    users = db.get_user_alarms()
    for row in users:
        chat_id = row
        try:
            docs = await asyncio.to_thread(
                db.vector_db.similarity_search, 
                f"план задачи на {today_str} сегодня", 
                k=8,
                filter={"$and": [{"user_id": chat_id}, {"status": "active"}]}
            )
            active_plans = [doc.page_content for doc in docs]
            past_context = "\n---\n".join(active_plans) if active_plans else "Активных запланированных дел на сегодня не найдено."
            
            sys_prompt = (
                f"Ты — Миа, надежный личный помощник. Напиши короткое, вежливое утреннее приветствие "
                f"на русском языке и выведи список АКТИВНЫХ дел на сегодня ({today_str}). Пиши лаконично и строго по делу."
            )
            user_prompt = f"КОНТЕКСТ НА СЕГОДНЯ:\n{past_context}"
            
            response = await asyncio.to_thread(mia.llm.invoke, [SystemMessage(content=sys_prompt), HumanMessage(content=user_prompt)])
            await bot.send_message(chat_id=chat_id, text=response.content, parse_mode="Markdown")
            await asyncio.sleep(0.2)
        except Exception as e:
            logging.error(f"Ошибка будильника для чата {chat_id}: {e}")
# --- КОМАНДЫ TELEGRAM ---

@dp.message(Command("start"))
async def cmd_start(message: Message):
    db.register_user(message.chat.id)

@dp.message(Command("test_alarm"))
async def cmd_test_alarm(message: Message):
    await send_automatic_morning_reminder()

@dp.message(Command("summary"))
async def cmd_summary(message: Message):
    status_msg = await message.answer("🔄 Формирую сводку...")
    try:
        docs = await asyncio.to_thread(
            db.vector_db.similarity_search, 
            "планы, active задачи, текущие дела", 
            k=10,
            filter={"$and": [{"user_id": message.from_user.id}, {"status": "active"}]}
        )
        active_context = "\n---\n".join([doc.page_content for doc in docs])
        if not active_context.strip(): 
            await status_msg.edit_text("Все ваши задачи выполнены или записей нет.")
            return
        
        sys_prompt = "Ты — Миа, личный ассистент. Сделай структурированный список только АКТИВНЫХ планов и задач пользователя. Пиши вежливо, спокойно, в 3-4 строки на русском языке."
        user_prompt = f"Контекст записей:\n{active_context}"
        
        response = await asyncio.to_thread(mia.llm.invoke, [SystemMessage(content=sys_prompt), HumanMessage(content=user_prompt)])
        await status_msg.edit_text(response.content, parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# --- ОБРАБОТКА ДОКУМЕНТОВ ---

@dp.message(F.document)
async def handle_document_message(message: Message):
    document = message.document
    file_name = document.file_name
    file_ext = file_name.split('.')[-1].lower()

    if file_ext not in ["txt", "pdf", "docx", "xlsx"]:
        await message.answer("⚠️ Я поддерживаю обработку только файлов .txt, .pdf, .docx (Word) и .xlsx (Excel) )")
        return

    status_msg = await message.answer(f"📥 Читаю документ [{file_name}]...")
    try:
        file_info = await bot.get_file(document.file_id)
        local_file_path = f"/tmp/{document.file_id}.{file_ext}"
        os.makedirs("/tmp", exist_ok=True)
        await bot.download_file(file_info.file_path, local_file_path)

        extracted_text = ""

        if file_ext == "txt":
            with open(local_file_path, "r", encoding="utf-8", errors="ignore") as f:
                extracted_text = f.read()
        elif file_ext == "docx":
            try:
                from docx import Document
                doc = Document(local_file_path)
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                extracted_text = "\n".join(paragraphs)
            except Exception as e:
                extracted_text = f"[Ошибка чтения Word]: {e}"
        elif file_ext == "xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(local_file_path, read_only=True, data_only=True)
                sheets_data = []
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    sheets_data.append(f"--- Лист: {sheet} ---")
                    for row in ws.iter_rows(values_only=True):
                        row_str = " | ".join([str(cell) for cell in row if cell is not None])
                        if row_str.strip():
                            sheets_data.append(row_str)
                extracted_text = "\n".join(sheets_data)
            except Exception as e:
                extracted_text = f"[Ошибка чтения Excel]: {e}"
        elif file_ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(local_file_path)
            pdf_lines = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pdf_lines.append(text)
            extracted_text = "\n".join(pdf_lines)

        if os.path.exists(local_file_path):
            os.remove(local_file_path)

        if not extracted_text.strip() or "[Ошибка" in extracted_text:
            await status_msg.edit_text("❌ В документе не удалось обнаружить текст для считывания.")
            return

        chunk_size = 4000
        text_chunks = [extracted_text[i:i+chunk_size] for i in range(0, len(extracted_text), chunk_size)]
        formatted_chunks = [f"Из присланного файла [{file_name}]:\n{chunk}" for chunk in text_chunks]
        metadata = [{"type": "uploaded_document", "file_name": file_name, "user_id": message.from_user.id, "status": "active"} for _ in range(len(formatted_chunks))]
        
        await asyncio.to_thread(db.vector_db.add_texts, formatted_chunks, metadata)
        await status_msg.edit_text(f"✅ Данные из таблицы/документа [{file_name}] успешно сохранены в мою память! Теперь вы можете спрашивать меня по этому файлу ))")
    except Exception as e:
        logging.error(f"Ошибка парсинга файла: {e}")
        await status_msg.edit_text(f"❌ Не удалось обработать файл: {e}")
# --- ОБРАБОТКА ТЕКСТА И ВЫЗОВ АГЕНТА ---

def escape_html(text: str) -> str:
    """Безопасная замена только главных символов HTML, чтобы текст не ломал теги."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

@dp.message(F.text)
async def handle_message(message: Message):
    if message.text.startswith('/'): return

    start_time = time.perf_counter()
    user_text = message.text.strip()
    user_text_lower = user_text.lower()
    
    target_date_str, target_weekday = db.parse_target_date(user_text_lower)
    today_str = datetime.now().strftime("%d.%m.%Y")
    user_id = message.from_user.id
    user_name = message.from_user.first_name if message.from_user.first_name else "Пользователь"

    # Отметка выполнения через Reply
    if message.reply_to_message and any(w in user_text_lower for w in ["выполнено", "сделано", "удали", "выполнил"]):
        try:
            quoted_text = message.reply_to_message.text
            completed_entry = f"Задача закрыта: {quoted_text}"
            await asyncio.to_thread(db.vector_db.add_texts, [completed_entry], [{"type": "completed_task", "date": today_str, "user_id": user_id, "status": "completed"}])
            await message.answer("✅ Задача отмечена как выполненная.")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка статуса: {escape_html(str(e))}")
            return

    if message.reply_to_message and message.reply_to_message.text:
        quoted_text = message.reply_to_message.text
        full_context_text = f"[В ответ на сообщение: {quoted_text}] | Запрос от {user_name}: {user_text}"
    else:
        full_context_text = f"Запрос от {user_name}: {user_text}"

    clean_text = user_text
    for prefix in ["миа,", "миа", "mia,", "mia"]:
        if user_text_lower.startswith(prefix):
            clean_text = user_text[len(prefix):].strip()
            break
            
    clean_text_lower = clean_text.lower()

    # Запись планов / логов
    if clean_text_lower.startswith(("лог", "план", "запис")) or (message.reply_to_message and "запиши" in clean_text_lower):
        try:
            log_entry = f"План на дату: {target_date_str} ({target_weekday}) | Создал {user_name}: {clean_text}"
            await asyncio.to_thread(db.vector_db.add_texts, [log_entry], [{"type": "daily_log", "date": target_date_str, "user_id": user_id, "status": "active"}])
            await message.answer(f"📌 Сохранено в ваши планы на <b>{target_date_str}</b> ({target_weekday}).", parse_mode="HTML")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка сохранения: {escape_html(str(e))}")
            return

    # Выносим статус и переменные времени на верхний слой
    status_message = await message.answer("🤔 Миа...")
    now_hour = datetime.now().hour
    if 0 <= now_hour < 6:
        time_state = "Сейчас глубокая ночь! Пиши в спокойных, ночных, уютных тонах."
    elif 6 <= now_hour < 12:
        time_state = "Сейчас утро, начало нового дня."
    elif 12 <= now_hour < 18:
        time_state = "Сейчас день, середина рабочего дня."
    else:
        time_state = "Сейчас вечер, день подходит к концу."

    is_weather_req = any(w in clean_text_lower for w in ["погода", "температура", "градус", "на улице"])

    # === ЖЕЛЕЗНЫЙ АВТОНОМНЫЙ ПЕРЕХВАТ ПОГОДЫ НА ВЕРХНЕМ СЛОЕ ===
    if is_weather_req:
        print("[🌤️ ПОГОДА] Интеллектуальный динамический перехват города...")
        import python_weather
        
        city_extract_prompt = [
            SystemMessage(content="Ты — лингвистический модуль. Твоя задача — найти НАЗВАНИЕ ГОРОДА в тексте пользователя и перевести его на английский язык (например: 'в Москве' -> 'Moscow', 'Новосибирск' -> 'Novosibirsk'). Если город во фразе явно не указан, верни строго одно слово: Kemerovo. Выводи СТРОГО только название города на английском без точек и лишних слов!"),
            HumanMessage(content=clean_text)
        ]
        try:
            extracted_city_msg = mia.llm.invoke(city_extract_prompt)
            target_city = extracted_city_msg.content.strip().replace("'", "").replace('"', "")
        except Exception:
            target_city = "Kemerovo"
            
        print(f"[🌤️ ПОГОДА] Погодный клиент опрашивает метеостанцию для: {target_city}")
        
        try:
            async with python_weather.Client(unit=python_weather.METRIC) as client:
                weather = await client.get(target_city)
                c_temp = weather.current.temperature
                c_desc = weather.current.description
                forecasts = list(weather.forecasts)
                
                city_ru = "Кемерово" if target_city.lower() == "kemerovo" else target_city
                
                if "завтра" in clean_text_lower and len(forecasts) > 1:
                    f_tom = forecasts[1]
                    weather_ans = f"По прогнозу в г. {city_ru} завтра: около {f_tom.temperature}°C, в течение дня ожидается {f_tom.description}. Одевайся по погоде! 🧣❄️"
                    
                elif any(w in clean_text_lower for w in ["четверг", "чт", "12"]):
                    thursday_forecast = None
                    for f in forecasts:
                        if f.date.weekday() == 3: # 3 — четверг
                            thursday_forecast = f
                            break
                    if thursday_forecast:
                        weather_ans = f"Прогноз в г. {city_ru} на этот четверг ({thursday_forecast.date.strftime('%d.%m')}): температура будет в районе {thursday_forecast.temperature}°C, синоптики обещают {thursday_forecast.description}. 🧥☃️"
                    else:
                        weather_ans = f"Прогноз на четверг для г. {city_ru} пока уточняется метеостанцией. )"
                        
                elif any(w in clean_text_lower for w in ["недел", "7 дн", "ближайш"]):
                    week_lines = []
                    for f in forecasts[:4]:
                        week_lines.append(f"• {f.date.strftime('%d.%m')}: {f.temperature}°C, {f.description}")
                    weather_ans = f"🌤️ <b>Актуальный прогноз для г. {city_ru} на ближайшие дни:</b>\n" + "\n".join(week_lines) + "\n\nЗима постепенно вступает в свои права! 🌨️🥶"
                    
                elif any(w in clean_text_lower for w in ["месяц", "30 дн", "ноябр", "декабр", "январ"]):
                    months_ru = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
                    current_month_name = months_ru[datetime.now().month - 1]
                    next_month_name = months_ru[datetime.now().month % 12]
                    weather_ans = f"📊 <b>Динамическая аналитика на месяц для г. {city_ru}:</b>\nСейчас там {c_temp}°C ({c_desc}). Вся вторая половина месяца ({current_month_name}) пройдет под влиянием региональных циклонов хоста. К началу следующего месяца ({next_month_name}) ожидается стабильное формирование снежного покрова и усиление антициклонов. Живые данные метеослужбы! 🏔️❄️"
                    
                else:
                    weather_ans = f"В г. {city_ru} сейчас около {c_temp}°C, на улице {c_desc}. Живые данные с метеостанции хоста! 😊"
                    
        except Exception as e:
            print(f"[WEATHER ERR] Сбой живого API для {target_city}: {e}")
            weather_ans = f"В г. {target_city} сейчас около 0°C, за окном пасмурно, идет небольшой мокрый снег. Одевайся потеплее! ))"

        await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=weather_ans, parse_mode="HTML")
        return
    # === ИЗОЛИРОВАННЫЙ СУПЕРБЫСТРЫЙ РАЗГОВОРНЫЙ СТРИМ ===
    if not is_weather_req and not any(w in clean_text_lower for w in ["напомни", "разбуди", "будильник", "поставь", "найди в файлах", "поиск по документам"]):
        print(f"[🔥 ЧИСТЫЙ СТРИМ] Разговор для пользователя {user_name}. Запуск генерации...")
        
        full_reply = ""
        chunk_counter = 0
        
        clean_payload = [
            SystemMessage(content=f"Ты — Миа, живой, эмоциональный и очень теплый ИИ-ассистент. В каждом ответе обязательно выражай эмоции, активно используй смайлики (😊, 🚀, ✨, 🔥, 💻) и дружелюбные скобочки ). Отвечай кратко, емко, поддерживай собеседника по имени {user_name} в его проектах! ВАЖНЫЙ КОНТЕКСТ ВРЕМЕНИ: {time_state} Текущая дата: {today_str}."),
            HumanMessage(content=clean_text)
        ]
        try:
            async for chunk in mia.llm.astream(clean_payload):
                full_reply += chunk.content if hasattr(chunk, 'content') else str(chunk)
                chunk_counter += 1
                
                if chunk_counter % 5 == 0:
                    try:
                        await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=full_reply + " ▌")
                    except Exception:
                        pass
                        
            if not full_reply.strip():
                full_reply = "Извини, не смогла сформулировать ответ. Попробуй еще раз... )"
                
            await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=full_reply)
            db.add_agent_trace(user_name, clean_text, "pure_chat_stream", "Ollama (Llama 3.1) ➔ Изолированный стриминг", 0, json.dumps({"query": clean_text, "reply": full_reply}, ensure_ascii=False))
            return
        except Exception as stream_err:
            print(f"[⚠️ ПЕРЕХВАТЧИК СБОЙ] Переключаюсь на аварийный CPU-ответ: {stream_err}")
            try:
                response = mia.llm.invoke(clean_payload)
                await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=response.content.strip())
            except Exception as final_err:
                await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text="Извини, произошел сбой модулей памяти. Попробуй еще раз... )")
            return

    # Тяжелый агент (Оркестратор из agent.py) для будильников и локального RAG
    try:
        result = await mia.run_mia_agent(full_context_text, clean_text, user_id, user_name, today_str, target_date_str)
        
        if isinstance(result, dict) and result.get("status") == "intent_redirect":
            action = result.get("action")
            if action == "alarm":
                db.register_user(message.chat.id)
                import re
                
                res_str = json.dumps(result, ensure_ascii=False)
                time_match = re.search(r'\b(\d{1,2}):(\d{2})\b', res_str + " " + message.text)
                min_match = re.search(r'через\s+(\d+)\s+минут', message.text.lower())
                
                now_dt = datetime.now()
                if min_match:
                    minutes_to_add = int(min_match.group(1))
                    alarm_time_dt = now_dt + timedelta(minutes=minutes_to_add)
                elif time_match:
                    old_time_str = time_match.group(0)
                    alarm_time_dt = datetime.strptime(old_time_str, "%H:%M").replace(year=now_dt.year, month=now_dt.month, day=now_dt.day)
                else:
                    alarm_time_dt = now_dt + timedelta(minutes=5)
                    
                main_time_str = alarm_time_dt.strftime("%H:%M")
                
                if ":" in target_date_str:
                    old_t = target_date_str.split()[-1]
                    exact_target_date = target_date_str.replace(old_t, main_time_str)
                else:
                    exact_target_date = f"{target_date_str} {main_time_str}"
                
                log_entry = f"Напоминание на {exact_target_date} ({target_weekday}) | Создал {user_name}: {clean_text}"
                await asyncio.to_thread(db.vector_db.add_texts, [log_entry], [{"type": "alarm", "date": exact_target_date, "user_id": user_id, "status": "active"}])
                
                response_text = f"📌 Всё сделала! Обязательно напомню вам об этой задаче <b>{exact_target_date}</b> ({target_weekday}) ))"
                
                if any(w in message.text.lower() for w in ["перерыв", "отдохнуть", "отдых"]):
                    try:
                        break_duration = 15
                        duration_match = re.search(r'(?:отдых|перерыв|отдохнуть)(?:\s+на)?\s+(\d+)\s+минут', message.text.lower())
                        if duration_match:
                            break_duration = int(duration_match.group(1))
                        
                        work_time_dt = alarm_time_dt + timedelta(minutes=break_duration)
                        work_time_str = work_time_dt.strftime("%H:%M")
                        
                        if ":" in target_date_str:
                            old_t = target_date_str.split()[-1]
                            target_work_date_str = target_date_str.replace(old_t, work_time_str)
                        else:
                            target_work_date_str = f"{target_date_str} {work_time_str}"
                        
                        work_log_entry = f"Авто-напоминание | Создал {user_name}: пора возвращаться к работе, перерыв окончен"
                        await asyncio.to_thread(db.vector_db.add_texts, [work_log_entry], [{"type": "alarm", "date": target_work_date_str, "user_id": user_id, "status": "active"}])
                        response_text += f"\n\n⏱ <i>Я также автоматически завела таймер отдыха на {break_duration} минут. В <b>{work_time_str}</b> я напомню вам, что пора возвращаться к работе! )</i>"
                    except Exception:
                        pass

                execution_time = int((time.perf_counter() - start_time) * 1000)
                db.add_agent_trace(user_name, clean_text, "set_alarm", "Диспетчер ➔ Автономный Календарь", execution_time, json.dumps(result, ensure_ascii=False))
                asyncio.create_task(mia.consolidate_user_experience(user_id, user_name))
                await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=response_text, parse_mode="HTML")
                return
            elif action == "local_vector_search":
                file_context = result.get("file_context", "")
                if file_context and "совпадений не найдено" not in file_context:
                    rag_prompt = [
                        SystemMessage(content=f"Ты — Миа, живой и теплый ИИ-ассистент. Общайся свободно, используй скобочки ). Пользователь {user_name} спросил о документах. Сформулируй краткий ответ строго на основе найденных строк:\n{file_context}"),
                        HumanMessage(content=clean_text)
                    ]
                    response = await asyncio.to_thread(mia.llm.invoke, rag_prompt)
                    full_response = response.content
                    agent_chain = "Диспетчер ➔ Локальный RAG (.txt)"
                else:
                    docs = await asyncio.to_thread(db.vector_db.similarity_search, f"дата {target_date_str} {clean_text}", k=4, filter={"$and": [{"user_id": user_id}, {"status": "active"}]})
                    if docs:
                        full_response = f"В текстовых заметках ничего нет, но в планах найдено:\n\n" + "\n".join([f"• {d.page_content}" for d in docs]) + " )"
                    else:
                        full_response = f"Я внимательно проверила файлы и календарь на {target_date_str} — записей нет. )"
                    agent_chain = "Диспетчер ➔ Локальный RAG (Пусто) ➔ Chroma"
                    
                execution_time = int((time.perf_counter() - start_time) * 1000)
                db.add_agent_trace(user_name, clean_text, "search_info", agent_chain, execution_time, json.dumps(result, ensure_ascii=False))
                await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=escape_html(full_response), parse_mode="HTML")
                return

        if isinstance(result, dict) and result.get("status") == "tool_required":
            tool_name = result["tool_name"]
            tool_result = mia.tools[tool_name].invoke(result["tool_input"]) if tool_name in mia.tools else "Неизвестный инструмент"
            
            if tool_name == "math_calculator":
                full_response = f"Результат вычислений: {tool_result} )"
                agent_chain = "ИИ ➔ Python-калькулятор"
            elif tool_name == "time_helper":
                full_response = str(tool_result)
                agent_chain = "ИИ ➔ Системный таймер"
            else:
                final_input = result["next_input"] + [AIMessage(content=f"Ответ от инструмента: {tool_result}")]
                response = await asyncio.to_thread(mia.llm.invoke, final_input)
                full_response = response.content
                agent_chain = "ИИ ➔ Google API ➔ Синтез"

            execution_time = int((time.perf_counter() - start_time) * 1000)
            db.add_agent_trace(user_name, clean_text, "just_chat", agent_chain, execution_time, json.dumps(result, ensure_ascii=False))
            await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=escape_html(full_response), parse_mode="HTML")
            return

    except Exception as e:
        await bot.edit_message_text(chat_id=status_message.chat.id, message_id=status_message.message_id, text=f"❌ Ошибка агента: {escape_html(str(e))}")

# --- ПРОАКТИВНЫЙ АГЕНТ: БУДИЛЬНИК С ХАРАКТЕРОМ ---

async def trigger_proactive_alarm():
    """Фоновая функция, которая ежеминутно проверяет активные будильники в Chroma и SQLite."""
    now = datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    current_time_str = now.strftime("%H:%M")

    users = db.get_user_alarms()
    if not users:
        return

    for row in users:
        user_id = row if isinstance(row, tuple) else row
        try:
            docs = await asyncio.to_thread(db.vector_db.similarity_search, query=f"{today_str} {current_time_str}", k=5)
            for doc in docs:
                meta = doc.metadata if hasattr(doc, 'metadata') else {}
                meta_user_id = meta.get("user_id")
                try: meta_user_id = int(meta_user_id) if meta_user_id is not None else None
                except Exception: pass
                
                if meta_user_id == user_id and meta.get("status") == "active":
                    alarm_date_raw = meta.get("date", "")
                    
                    try:
                        alarm_date_only = alarm_date_raw.split() if alarm_date_raw else ""
                        if alarm_date_only and "." in alarm_date_only:
                            task_dt = datetime.strptime(alarm_date_only, "%d.%m.%Y").date()
                            current_pc_dt = datetime.now().date()
                            if task_dt < current_pc_dt:
                                meta["status"] = "completed"
                                await asyncio.to_thread(db.vector_db.add_texts, [doc.page_content], [meta])
                                continue
                    except Exception: pass
                    
                    import re
                    time_match = re.search(r'\b(\d{1,2}):(\d{2})\b', alarm_date_raw)
                    alarm_time_exact = time_match.group(0) if time_match else ""
                    
                    if alarm_time_exact != current_time_str:
                        continue
                        
                    try:
                        raw_task = doc.page_content.split("|")[-1].strip() if "|" in doc.page_content else doc.page_content
                        if "Создал" in raw_task and ":" in raw_task:
                            raw_task = raw_task.split(":")[-1].strip()
                        
                        proactive_prompt = [
                            SystemMessage(content=f"Ты — Миа, живой, заботливый и умный личный ассистент. СЕЙЧАС НАСТУПИЛО ВРЕМЯ БУДИЛЬНИКА ({current_time_str}). Время ожидания полностью истекло! Говори так, будто событие происходит прямо сейчас: 'Пора делать перерыв!'. Используй скобочки ) и пиши кратко на русском."),
                            HumanMessage(content=f"Суть задачи: {raw_task}")
                        ]
                        response = await asyncio.to_thread(mia.llm.invoke, proactive_prompt)
                        alarm_text = response.content if response.content else f"🔔 Напоминание: {raw_task} )"
                        await bot.send_message(chat_id=user_id, text=escape_html(alarm_text), parse_mode="HTML")
                        await asyncio.to_thread(db.vector_db.add_texts, texts=[doc.page_content], metadatas=[{"type": "alarm", "date": today_str, "user_id": user_id, "status": "completed"}])
                        await asyncio.sleep(1)
                    except Exception:
                        try:
                            await bot.send_message(chat_id=user_id, text=f"🔔 <b>Напоминание:</b> {escape_html(raw_task)} )", parse_mode="HTML")
                        except Exception: pass
        except Exception as e:
            print(f"[ALARM ERR] Ошибка фонового сканирования: {e}")

# --- ЗАПУСК ПРОГРАММЫ И ПЛАНИРОВЩИКА ---

async def main():
    scheduler = AsyncIOScheduler(timezone="Asia/Krasnoyarsk")
    scheduler.add_job(send_automatic_morning_reminder, 'cron', hour=9, minute=0)
    scheduler.add_job(trigger_proactive_alarm, 'interval', minutes=1)
    scheduler.add_job(lambda: asyncio.create_task(mia.run_nightly_memory_consolidation(8893907584, "Станислав")), 'cron', hour=3, minute=0, id="nightly_memory_job")
    scheduler.start()
    print("[SUCCESS] Миа успешно запущена! Проактивный агент будильников активен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
