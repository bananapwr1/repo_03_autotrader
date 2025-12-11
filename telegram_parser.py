#!/usr/bin/env python3
"""
Telegram парсер для Amvera
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telethon import TelegramClient, events

logger = logging.getLogger(__name__)

class TelegramParser:
    """Парсер Telegram чатов"""
    
    def __init__(self, api_id: int, api_hash: str, target_chats: List, supabase):
        self.api_id = api_id
        self.api_hash = api_hash
        self.target_chats = target_chats
        self.supabase = supabase
        self.client = None
        self.processed_messages = set()
        
        # Паттерны для парсинга
        self.patterns = {
            'symbol': r'([A-Z]{3,6}/[A-Z]{3,6}|[A-Z]{3,10}(?:USDT|BTC|ETH))',
            'direction': r'(купить|покупаем|бай|buy|long|лонг|продать|продаем|селл|sell|short|шорт)',
            'entry': r'(вход|entry)[:\s]*([0-9.]+)',
            'tp': r'(тп|tp|target)[:\s]*([0-9.]+)',
            'sl': r'(сл|sl|stop)[:\s]*([0-9.]+)',
            'pre_signal': r'(готовность|через|сигнал через)\s*(\d+)\s*(мин|минут|min)'
        }
        
    async def start(self):
        """Запуск парсера"""
        try:
            self.client = TelegramClient(
                'amvera_session',
                self.api_id,
                self.api_hash
            )
            
            await self.client.start()
            logger.info("✅ Telegram парсер запущен")
            
            # Настройка обработчиков для каждого чата
            for chat_id in self.target_chats:
                @self.client.on(events.NewMessage(chats=chat_id))
                async def handler(event):
                    await self.process_message(event)
                    
            # Запускаем бесконечный цикл
            await self.client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска парсера: {e}")
            
    async def process_message(self, event):
        """Обработка сообщения"""
        try:
            message_id = f"{event.chat_id}_{event.message.id}"
            
            if message_id in self.processed_messages:
                return
                
            self.processed_messages.add(message_id)
            
            text = event.message.text or ""
            if not text:
                return
                
            # Парсинг сигнала
            signal_data = self.parse_signal(text)
            
            if signal_data:
                # Сохраняем в Supabase
                await self.save_to_supabase(signal_data, event)
                logger.info(f"📨 Сохранен сигнал: {signal_data.get('symbol', 'N/A')}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}")
            
    def parse_signal(self, text: str) -> Optional[Dict]:
        """Парсинг текста на сигнал"""
        text_lower = text.lower()
        
        # Поиск символа
        symbol_match = re.search(self.patterns['symbol'], text, re.IGNORECASE)
        if not symbol_match:
            return None
            
        symbol = symbol_match.group(1).upper()
        
        # Поиск направления
        direction = None
        buy_words = ['купить', 'покупаем', 'бай', 'buy', 'long', 'лонг']
        sell_words = ['продать', 'продаем', 'селл', 'sell', 'short', 'шорт']
        
        for word in buy_words:
            if word in text_lower:
                direction = 'buy'
                break
                
        if not direction:
            for word in sell_words:
                if word in text_lower:
                    direction = 'sell'
                    break
                    
        if not direction:
            return None
            
        # Извлечение цен
        entry_price = self.extract_price(text, 'entry')
        tp_price = self.extract_price(text, 'tp')
        sl_price = self.extract_price(text, 'sl')
        
        # Проверка на пре-сигнал
        pre_signal = re.search(self.patterns['pre_signal'], text_lower)
        
        return {
            'symbol': symbol,
            'direction': direction,
            'entry_price': entry_price,
            'tp_price': tp_price,
            'sl_price': sl_price,
            'is_pre_signal': bool(pre_signal),
            'raw_text': text,
            'parsed_at': datetime.utcnow().isoformat()
        }
        
    def extract_price(self, text: str, price_type: str) -> Optional[float]:
        """Извлечение цены"""
        pattern = self.patterns.get(price_type)
        if not pattern:
            return None
            
        match = re.search(pattern, text.lower())
        if match and len(match.groups()) >= 2:
            try:
                return float(match.group(2))
            except ValueError:
                return None
        return None
        
    async def save_to_supabase(self, signal_data: Dict, event):
        """Сохранение сигнала в Supabase"""
        try:
            data = {
                'chat_id': event.chat_id,
                'message_id': event.message.id,
                'date': event.message.date.isoformat(),
                'parsed_data': signal_data,
                'is_trading_signal': signal_data.get('symbol') and signal_data.get('direction'),
                'is_pre_signal': signal_data.get('is_pre_signal', False),
                'processed': False,
                'saved_at': datetime.utcnow().isoformat()
            }
            
            self.supabase.table("parsed_signals").insert(data).execute()
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения в Supabase: {e}")
            
    async def parse_history(self, hours: int = 24):
        """Исторический парсинг"""
        logger.info(f"🕐 Начинаю исторический парсинг за {hours} часов")
        
        for chat_id in self.target_chats:
            try:
                entity = await self.client.get_entity(chat_id)
                from_date = datetime.utcnow() - timedelta(hours=hours)
                
                messages = await self.client.get_messages(
                    entity,
                    limit=1000,
                    offset_date=from_date
                )
                
                for message in messages:
                    await self.process_message(
                        type('Event', (), {
                            'chat_id': chat_id,
                            'message': message,
                            'text': message.text
                        })()
                    )
                    
            except Exception as e:
                logger.error(f"❌ Ошибка исторического парсинга чата {chat_id}: {e}")
                
    async def close(self):
        """Закрытие соединения"""
        if self.client:
            await self.client.disconnect()
            logger.info("✅ Telegram парсер остановлен")