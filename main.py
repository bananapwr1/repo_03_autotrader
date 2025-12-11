#!/usr/bin/env python3
"""
Amvera: Авто-торговля + Telegram парсинг
Главный исполнительный модуль
"""

import os
import sys
import asyncio
import logging
import signal
from typing import Dict, List
from datetime import datetime

from supabase import create_client
from dotenv import load_dotenv

# Наши модули
from telegram_parser import TelegramParser
from websocket_client import MarketDataClient
from config import (
    SUPABASE_URL, SUPABASE_KEY, ENCRYPTION_KEY,
    TG_API_ID, TG_API_HASH, TARGET_CHAT_IDS,
    AUTOTRADE_RULES, EXCHANGE_CONFIG
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('amvera_executor.log')
    ]
)
logger = logging.getLogger(__name__)

class AmveraExecutor:
    """Главный исполнительный модуль на Amvera"""
    
    def __init__(self):
        self.supabase = None
        self.telegram_parser = None
        self.market_client = None
        self.is_running = True
        self.active_tasks = []
        
    async def init(self):
        """Инициализация всех компонентов"""
        logger.info("🚀 Инициализация Amvera Executor...")
        
        # 1. Инициализация Supabase
        try:
            self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("✅ Supabase подключен")
        except Exception as e:
            logger.error(f"❌ Ошибка Supabase: {e}")
            return False
            
        # 2. Инициализация парсера Telegram
        if TG_API_ID and TG_API_HASH:
            try:
                self.telegram_parser = TelegramParser(
                    api_id=TG_API_ID,
                    api_hash=TG_API_HASH,
                    target_chats=TARGET_CHAT_IDS,
                    supabase=self.supabase
                )
                logger.info("✅ Telegram парсер инициализирован")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации парсера: {e}")
                
        # 3. Инициализация WebSocket клиента для бирж
        try:
            self.market_client = MarketDataClient(
                exchanges=EXCHANGE_CONFIG,
                supabase=self.supabase
            )
            logger.info("✅ WebSocket клиент инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка WebSocket: {e}")
            
        return True
        
    async def start_tasks(self):
        """Запуск всех фоновых задач"""
        logger.info("▶️ Запуск фоновых задач...")
        
        # Запускаем парсер (если настроен)
        if self.telegram_parser:
            parser_task = asyncio.create_task(
                self.telegram_parser.start(),
                name="telegram_parser"
            )
            self.active_tasks.append(parser_task)
            logger.info("▶️ Telegram парсер запущен")
            
        # Запускаем WebSocket клиент
        if self.market_client:
            ws_task = asyncio.create_task(
                self.market_client.connect_all(),
                name="websocket_client"
            )
            self.active_tasks.append(ws_task)
            logger.info("▶️ WebSocket клиент запущен")
            
        # Запускаем торговый цикл
        trade_task = asyncio.create_task(
            self.trading_cycle(),
            name="trading_cycle"
        )
        self.active_tasks.append(trade_task)
        logger.info("▶️ Торговый цикл запущен")
        
        # Запускаем проверку команд от админа
        command_task = asyncio.create_task(
            self.command_listener(),
            name="command_listener"
        )
        self.active_tasks.append(command_task)
        logger.info("▶️ Слушатель команд запущен")
        
    async def trading_cycle(self):
        """Основной торговый цикл"""
        logger.info("🔄 Торговый цикл начат")
        
        while self.is_running:
            try:
                # 1. Проверяем новые сигналы от ядра (PythonAnywhere)
                await self.check_core_signals()
                
                # 2. Проверяем парсированные сигналы (если еще парсим)
                await self.check_parsed_signals()
                
                # 3. Мониторим открытые позиции
                await self.monitor_positions()
                
                # 4. Обновляем баланс
                await self.update_balances()
                
                # Пауза между итерациями
                await asyncio.sleep(AUTOTRADE_RULES['CHECK_INTERVAL'])
                
            except Exception as e:
                logger.error(f"❌ Ошибка в торговом цикле: {e}")
                await asyncio.sleep(5)
                
    async def check_core_signals(self):
        """Проверка сигналов от торгового ядра (PythonAnywhere)"""
        try:
            # Ищем непрочитанные сигналы с высокой уверенностью
            response = self.supabase.table("ai_signals") \
                .select("*") \
                .eq("status", "new") \
                .eq("for_autotrade", True) \
                .gte("confidence", AUTOTRADE_RULES['MIN_CONFIDENCE']) \
                .order("created_at", desc=True) \
                .limit(10) \
                .execute()
            
            for signal in response.data:
                logger.info(f"📡 Получен сигнал от ядра: {signal}")
                # TODO: Исполнение сигнала
                # await self.execute_signal(signal)
                
        except Exception as e:
            logger.error(f"❌ Ошибка проверки сигналов ядра: {e}")
            
    async def check_parsed_signals(self):
        """Проверка парсированных сигналов (временная мера)"""
        try:
            response = self.supabase.table("parsed_signals") \
                .select("*") \
                .eq("processed", False) \
                .eq("is_trading_signal", True) \
                .order("saved_at", desc=True) \
                .limit(5) \
                .execute()
            
            for signal in response.data:
                logger.info(f"📨 Парсированный сигнал: {signal['parsed_data']}")
                # TODO: Анализ и исполнение
                
        except Exception as e:
            logger.error(f"❌ Ошибка проверки парсированных сигналов: {e}")
            
    async def command_listener(self):
        """Слушатель команд от админского бота"""
        logger.info("👂 Слушатель команд запущен")
        
        while self.is_running:
            try:
                # Проверяем команды в Supabase
                commands = self.supabase.table("autotrade_commands") \
                    .select("*") \
                    .eq("processed", False) \
                    .execute()
                    
                for cmd in commands.data:
                    await self.process_command(cmd)
                    # Помечаем как обработанную
                    self.supabase.table("autotrade_commands") \
                        .update({"processed": True}) \
                        .eq("id", cmd["id"]) \
                        .execute()
                        
                await asyncio.sleep(10)  # Проверяем каждые 10 секунд
                
            except Exception as e:
                logger.error(f"❌ Ошибка слушателя команд: {e}")
                await asyncio.sleep(30)
                
    async def process_command(self, cmd: Dict):
        """Обработка команды от админа"""
        command_type = cmd.get("command")
        logger.info(f"📩 Получена команда: {command_type}")
        
        if command_type == "start_demo":
            await self.start_demo_trading()
        elif command_type == "stop_trading":
            await self.stop_trading()
        elif command_type == "change_strategy":
            await self.change_strategy(cmd.get("params", {}))
        elif command_type == "parse_history":
            await self.parse_historical(cmd.get("params", {}))
            
    async def start_demo_trading(self):
        """Запуск демо-торговли"""
        logger.info("🟢 Запуск демо-торговли")
        # TODO: Реализация
        
    async def stop_trading(self):
        """Остановка торговли"""
        logger.info("🔴 Остановка торговли")
        # TODO: Реализация
        
    async def change_strategy(self, params: Dict):
        """Изменение стратегии"""
        logger.info(f"⚙️ Изменение стратегии: {params}")
        # TODO: Реализация
        
    async def parse_historical(self, params: Dict):
        """Исторический парсинг"""
        if self.telegram_parser:
            await self.telegram_parser.parse_history(
                hours=params.get("hours", 24)
            )
            
    async def monitor_positions(self):
        """Мониторинг открытых позиций"""
        # TODO: Реализация
        pass
        
    async def update_balances(self):
        """Обновление балансов"""
        # TODO: Реализация
        pass
        
    async def shutdown(self):
        """Корректное завершение работы"""
        logger.info("🛑 Завершение работы Amvera Executor...")
        self.is_running = False
        
        # Ожидаем завершения задач
        for task in self.active_tasks:
            if not task.done():
                task.cancel()
                
        # Закрываем соединения
        if self.telegram_parser:
            await self.telegram_parser.close()
            
        if self.market_client:
            await self.market_client.close()
            
        logger.info("✅ Все компоненты остановлены")
        

async def main():
    """Главная функция"""
    executor = AmveraExecutor()
    
    # Обработка SIGTERM для Docker
    def signal_handler(signum, frame):
        logger.info(f"Получен сигнал {signum}, завершение...")
        asyncio.create_task(executor.shutdown())
        
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Инициализация
    if not await executor.init():
        logger.error("❌ Не удалось инициализировать Amvera Executor")
        return
        
    # Запуск задач
    await executor.start_tasks()
    
    # Бесконечный цикл (до получения сигнала остановки)
    while executor.is_running:
        await asyncio.sleep(1)
        
    logger.info("👋 Amvera Executor завершил работу")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())