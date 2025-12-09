# =========================================================================
# repo_03_autotrader / main.py - ФИНАЛЬНАЯ ВЕРСИЯ (Торговля + Telegram Парсинг)
# =========================================================================

import os
import sys
import logging
import asyncio
import base64
import json
import re
import websockets
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client 
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from telethon import TelegramClient, events # Добавлено для парсинга

# --- ВАЖНО: Модуль PocketOptionAPI должен быть в той же папке! ---
# Предполагаем, что этот класс находится в pocket_option_api.py
try:
    from pocket_option_api import PocketOptionAPI 
except ImportError:
    print("❌ Ошибка: Не найден файл pocket_option_api.py. Загрузите его!")
    sys.exit(1)

# =========================================================================
# КОНСТАНТЫ И НАСТРОЙКИ
# =========================================================================

load_dotenv()
MOSCOW_TZ = timezone(timedelta(hours=3))
logger = logging.getLogger(__name__)

# --- SUPABASE & CRYPTO НАСТРОЙКИ (Используются переменные из JSON-запроса) ---
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY') 

# --- TELETHON НАСТРОЙКИ (Для парсинга TG) ---
TG_API_ID = os.getenv("TG_API_ID")
TG_API_HASH = os.getenv("TG_API_HASH")
# ID целевого чата для парсинга сигналов (Например, -10012345678)
try:
    TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "0"))
except ValueError:
    TARGET_CHAT_ID = 0
# Название сессии для Telethon (создаст файл .session)
SESSION_NAME = 'autotrader_session'

# --- ПРАВИЛА АВТОТОРГОВЛИ ---
AUTOTRADE_RULES = {
    'MIN_CONFIDENCE': 95.0, # Минимальная уверенность сигнала для торговли
    'TRADE_AMOUNT': 1.0,     
    'TRADE_DURATION': 60,   
}

# --- ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ---
supabase: Optional[Client] = None
active_sessions: Dict[int, PocketOptionAPI] = {} # user_id: PO API Object
telethon_client: Optional[TelegramClient] = None

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)

# =========================================================================
# КРИПТОГРАФИЯ (Fernet)
# =========================================================================

# Salt должна быть одинаковой во всех репозиториях!
CRYPTO_SALT = b'pocket-option-login-encryption' 

def get_encryption_cipher() -> Optional[Fernet]:
    if not ENCRYPTION_KEY: 
        logger.error("❌ ENCRYPTION_KEY не найден.")
        return None
    try:
        key_bytes = ENCRYPTION_KEY.encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=CRYPTO_SALT, 
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(key_bytes))
        return Fernet(key)
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Fernet: {e}")
        return None

def decrypt_data(data: str) -> str:
    """Расшифровывает данные (логин/пароль) из БД."""
    cipher = get_encryption_cipher()
    if not cipher: return ""
    try:
        return cipher.decrypt(data.encode()).decode()
    except Exception as e:
        logger.error(f"❌ Ошибка дешифровки данных: {e}")
        return ""

# =========================================================================
# ФУНКЦИИ БАЗЫ ДАННЫХ (Supabase)
# =========================================================================

def init_supabase() -> bool:
    """Инициализация клиента Supabase."""
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY) 
            logger.info("✅ Supabase клиент успешно инициализирован.")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка подключения Supabase: {e}")
            return False
    else:
        logger.error("❌ Переменные SUPABASE_URL/KEY не найдены.")
        return False

# Синхронные обертки
def _get_active_autotraders_sync() -> List[Dict[str, Any]]:
    if not supabase: return []
    try:
        # Ищем пользователей VIP с включенной автоторговлей и заполненными PO данными
        response = supabase.table('users').select('*').eq('autotrade_enabled', True).neq('pocket_option_email', None).execute()
        return response.data
    except Exception as e:
        logger.error(f"Ошибка получения активных автотрейдеров: {e}")
        return []

# =========================================================================
# ЛОГИКА АВТОТОРГОВЛИ (ЦИКЛ)
# =========================================================================

async def trade_on_signal(user_id: int, po_api: PocketOptionAPI, asset: str, direction: str, confidence: float):
    """Выполняет сделку на основе сигнала."""
    
    if confidence < AUTOTRADE_RULES['MIN_CONFIDENCE']:
        logger.info(f"[{user_id}] 📉 Сигнал {asset} {direction} (Conf: {confidence}) ниже MIN_CONFIDENCE. Пропуск.")
        return
        
    logger.info(f"[{user_id}] 🚀 Выполняется сделка: {asset} {direction} на ${AUTOTRADE_RULES['TRADE_AMOUNT']}")
    
    trade = await po_api.place_trade(
        asset=asset,
        amount=AUTOTRADE_RULES['TRADE_AMOUNT'],
        direction=direction,
        duration=AUTOTRADE_RULES['TRADE_DURATION']
    )
    
    if trade and trade.get("success"):
        logger.info(f"[{user_id}] ✅ Сделка ID {trade['trade_id']} успешно размещена.")
        # Тут можно запустить задачу ожидания результата:
        # await po_api.check_trade_result(trade['trade_id']) 
    else:
        logger.error(f"[{user_id}] ❌ Ошибка размещения сделки: {trade.get('error', 'Unknown Error')}")


async def check_new_signals_and_trade(user_id: int, po_api: PocketOptionAPI):
    """Проверяет Supabase на наличие новых сигналов для торговли."""
    if not supabase: return
    try:
        # Пример: получаем 5 последних необработанных сигналов
        response = supabase.table('signals').select('*').eq('processed_by_autotrader', False).order('created_at', desc=True).limit(5).execute()
        signals = response.data
        
        for signal in signals:
            if not signal.get('asset') or not signal.get('direction'):
                continue
                
            # Проверка, что сигнал не слишком старый (например, < 1 минуты)
            signal_time = datetime.fromisoformat(signal['created_at'].replace('Z', '+00:00'))
            if datetime.now(timezone.utc) - signal_time > timedelta(minutes=1):
                continue
                
            # Тут можно добавить логику проверки, не торговали ли мы уже по этому сигналу
            
            await trade_on_signal(
                user_id=user_id,
                po_api=po_api,
                asset=signal['asset'],
                direction=signal['direction'].lower(),
                confidence=signal.get('confidence', 90.0)
            )
            
            # Отмечаем сигнал как обработанный
            supabase.table('signals').update({'processed_by_autotrader': True}).eq('id', signal['id']).execute()
            
    except Exception as e:
        logger.error(f"[{user_id}] Ошибка проверки сигналов из Supabase: {e}")


async def autotrader_management_loop():
    """Главный цикл, управляющий активными торговыми сессиями."""
    while True:
        try:
            # 1. Получаем список активных пользователей из БД
            active_users = await asyncio.to_thread(_get_active_autotraders_sync)
            current_active_ids = {user['user_id'] for user in active_users}

            # 2. Закрытие неактивных сессий
            sessions_to_remove = list(active_sessions.keys() - current_active_ids)
            for user_id in sessions_to_remove:
                await active_sessions[user_id].close()
                del active_sessions[user_id]
                logger.info(f"🔌 Сессия PO для пользователя {user_id} закрыта.")

            # 3. Инициализация новых сессий и проверка старых
            trade_tasks = []
            for user in active_users:
                user_id = user['user_id']
                
                # Если сессии нет, создаем
                if user_id not in active_sessions:
                    try:
                        decrypted_ssid = decrypt_data(user['pocket_option_password'])
                        
                        po_api = PocketOptionAPI(
                            ssid=decrypted_ssid, 
                            demo=not user.get('is_real_account', False) # Используем is_real_account из БД
                        )
                        if await po_api.connect():
                            active_sessions[user_id] = po_api
                            logger.info(f"✅ Сессия PO для пользователя {user_id} успешно подключена.")
                        else:
                            logger.error(f"❌ Не удалось подключить PO API для {user_id}. Пароль/SSID недействителен.")
                            # Можно отключить autotrade_enabled в БД
                            
                    except Exception as e:
                        logger.error(f"❌ Ошибка инициализации PO для {user_id}: {e}")
                        continue

                # 4. Запускаем проверку сигналов для активной сессии
                if user_id in active_sessions:
                    trade_tasks.append(check_new_signals_and_trade(user_id, active_sessions[user_id]))
            
            # Запускаем все торговые проверки одновременно
            if trade_tasks:
                await asyncio.gather(*trade_tasks)

        except Exception as e:
            logger.critical(f"Критическая ошибка в главном цикле Autotrader: {e}")

        # Цикл проверки: каждые 15 секунд
        await asyncio.sleep(15)

# =========================================================================
# ЛОГИКА TELEGRAM ПАРСИНГА
# =========================================================================

async def tg_parser_loop():
    """Основной цикл для парсинга Telegram-чатов."""
    if not TG_API_ID or not TG_API_HASH or not TARGET_CHAT_ID:
        logger.error("❌ TG_API_ID/HASH/TARGET_CHAT_ID не заданы. Парсинг отключен.")
        return
        
    global telethon_client
    try:
        # Инициализация Telethon клиента
        telethon_client = TelegramClient(SESSION_NAME, TG_API_ID, TG_API_HASH)
        await telethon_client.start()
        logger.info("✅ Telethon клиент запущен и подключен.")

    except Exception as e:
        logger.error(f"❌ Ошибка запуска Telethon: {e}. Возможно, неверный ID/HASH или требуется авторизация (сессия).")
        return

    # Регистрируем хэндлер сообщений
    @telethon_client.on(events.NewMessage(chats=TARGET_CHAT_ID))
    async def handle_signal_message(event):
        """Обрабатывает новое сообщение из целевого чата, парсит и сохраняет сигнал."""
        message_text = event.message.message
        
        # 1. Парсинг: Пример простой регулярки для поиска актива и направления
        # Пример: 'Сигнал: EURUSD | CALL (98% Confidence)'
        match = re.search(r'([A-Z]+[A-Z]{3})\s*[|\-]\s*(CALL|PUT)\s*\((\d+)\%', message_text, re.IGNORECASE)
        
        if match:
            asset = match.group(1).upper()
            direction = match.group(2).upper()
            confidence = float(match.group(3))
            
            logger.info(f"📢 Найден внешний сигнал: {asset} {direction} (Conf: {confidence})")
            
            # 2. Запись сигнала в Supabase (чтобы Repo 3 мог его обработать в цикле autotrader_management_loop)
            # Запись происходит в таблицу 'signals' (которая используется в autotrader_management_loop)
            if supabase:
                signal_data = {
                    'asset': asset,
                    'direction': direction,
                    'confidence': confidence,
                    'source': 'telegram_parser',
                    'processed_by_autotrader': False
                }
                supabase.table('signals').insert(signal_data).execute()
                logger.info("✅ Внешний сигнал сохранен в Supabase.")
            
        else:
            logger.debug(f"Сообщение из чата не содержит сигнала: {message_text[:50]}...")


    # Блокируем, чтобы клиент оставался запущенным
    await telethon_client.run_until_disconnected()

# =========================================================================
# ГЛАВНЫЙ ЗАПУСК
# =========================================================================

async def main_async():
    # Инициализация Supabase
    if not init_supabase():
        logger.error("КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Supabase.")
        return

    # Запуск параллельных циклов
    await asyncio.gather(
        autotrader_management_loop(),  # Основной цикл торговли (чтение сигналов из БД)
        tg_parser_loop()               # Цикл парсинга Telegram (запись сигналов в БД)
    )

def main() -> None:
    # Проверка, что все ключевые переменные заданы
    if not ENCRYPTION_KEY or not TG_API_ID or not TG_API_HASH:
        logger.error("КРИТИЧЕСКАЯ ОШИБКА: Проверьте переменные ENCRYPTION_KEY, TG_API_ID, TG_API_HASH.")
        sys.exit(1)
        
    logger.info("🚀 AutoTrader Service (Repo 3) запущен и готов к работе!")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Сервис остановлен вручную.")

if __name__ == '__main__':
    main()
