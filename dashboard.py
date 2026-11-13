import streamlit as st
import sqlite3
import pandas as pd
import json
import time

st.set_page_config(page_title="Миа — Панель Трассировки Агентов", layout="wide", page_icon="⚙️")

st.title("⚙️ Миа: Панель мониторинга и трассировки суперагентов")
st.subheader("Визуализация обмена данными и цепочек рассуждений в реальном времени")

# Функция чтения логов
def load_traces():
    try:
        conn = sqlite3.connect("users_alarms.db")
        df = pd.read_sql_query("SELECT * FROM agent_traces ORDER BY id DESC LIMIT 50", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

# Автообновление страницы каждые 2 секунды
if "load_count" not in st.session_state:
    st.session_state.load_count = 0
time.sleep(2)
st.session_state.load_count += 1

df = load_traces()

if df.empty:
    st.info("⏳ Ожидание первого обмена данными между агентами... Отправьте запрос Мие в Telegram!")
else:
    # Берем самый последний лог для отрисовки живого графа
    latest = df.iloc[0]
    
    # Блок 1: Метрики последнего шага
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Последний пользователь", latest['user_name'])
    with col2:
        st.metric("Определенный Интент", latest['intent'].upper())
    with col3:
        st.metric("Скорость обработки", f"{latest['execution_time_ms']} мс")
    with col4:
        st.metric("Всего логов в базе", len(df))

    st.markdown("---")
    st.subheader("🗺 Живой граф обмена данными (Текущая цепочка)")
    
    # Визуализация графа цепочки в виде красивых HTML блоков со стрелочками
    chain_steps = latest['agent_chain'].split(" ➔ ")
    
    html_blocks = []
    for step in chain_steps:
        # Подсвечиваем разные типы агентов разными цветами
        color = "#2e7d32" if "Успех" in step or "Ollama" in step else "#1565c0"
        if "Диспетчер" in step: color = "#e65100"
        if "RAG" in step: color = "#6a1b9a"
        
        html_blocks.append(
            f"<div style='display:inline-block; background-color:{color}; color:white; "
            f"padding:12px 20px; border-radius:8px; margin:5px; font-weight:bold; "
            f"box-shadow: 2px 2px 5px rgba(0,0,0,0.2); font-family:sans-serif;'>{step}</div>"
        )
    
    st.markdown(" ➔ ".join(html_blocks), unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Таблица со всей историей логов
    st.subheader("📜 Терминал истории вызовов подсистем")
    st.dataframe(
        df[['timestamp', 'user_name', 'user_query', 'intent', 'agent_chain', 'execution_time_ms']], 
        use_container_width=True
    )
    
    # Просмотр сырых JSON пакетов
    st.subheader("📦 Сырые данные обмена пакетами (JSON Payload)")
    with st.expander("Посмотреть структуру последнего JSON пакета"):
        try:
            st.json(json.loads(latest['raw_payload']))
        except Exception:
            st.text(latest['raw_payload'])
            
    # Кнопка принудительного обновления
    if st.button("🔄 Обновить дашборд"):
        st.rerun()
