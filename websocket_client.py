#!/usr/bin/env python3
"""
WebSocket клиент для бирж
"""

import asyncio
import json
import logging
from typing import Dict, List
import websockets

logger = logging.getLogger(__name__)

class MarketDataClient:
    """Клиент для получения рыночных данных"""
    
    def __init__(self, exchanges: List[Dict], supabase):
        self.exchanges = exchanges
        self.supabase = supabase
        self.connections = {}
        self.subscriptions = {}
        
    async def connect_all(self):
        """Подключение ко всем биржам"""
        tasks = []
        for exchange in self.exchanges:
            tasks.append(self.connect_exchange(exchange))
            
        await asyncio.gather(*tasks)
        
    async def connect_exchange(self, exchange: Dict):
        """Подключение к конкретной бирже"""
        name = exchange['name']
        url = exchange['ws_url']
        symbols = exchange['symbols']
        
        try:
            logger.info(f"🔌 Подключаюсь к {name} WebSocket...")
            websocket = await websockets.connect(url)
            self.connections[name] = websocket
            
            # Подписка на символы
            await self.subscribe(websocket, symbols, exchange['subscribe_msg'])
            
            # Запуск слушателя
            asyncio.create_task(self.listen(websocket, name))
            
            logger.info(f"✅ Подключен к {name}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к {name}: {e}")
            
    async def subscribe(self, websocket, symbols: List[str], template: Dict):
        """Подписка на символы"""
        for symbol in symbols:
            msg = template.copy()
            msg['params'] = [f"{symbol}@ticker"]  # Пример для Binance
            
            await websocket.send(json.dumps(msg))
            logger.debug(f"📡 Подписан на {symbol}")
            
    async def listen(self, websocket, exchange_name: str):
        """Прослушивание сообщений от WebSocket"""
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.process_message(data, exchange_name)
                
        except websockets.ConnectionClosed:
            logger.warning(f"🔌 Соединение с {exchange_name} закрыто")
        except Exception as e:
            logger.error(f"❌ Ошибка в слушателе {exchange_name}: {e}")
            
    async def process_message(self, data: Dict, exchange: str):
        """Обработка сообщения от WebSocket"""
        try:
            # Сохраняем рыночные данные в Supabase
            market_data = {
                'exchange': exchange,
                'data': data,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Сохраняем для быстрого доступа
            self.supabase.table("market_data_cache").insert(market_data).execute()
            
            # Логируем каждые 100 сообщений
            if random.random() < 0.01:  # 1%
                logger.debug(f"📈 {exchange}: {data.get('s', 'N/A')} - {data.get('c', 'N/A')}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки WebSocket сообщения: {e}")
            
    async def close(self):
        """Закрытие всех соединений"""
        for name, ws in self.connections.items():
            try:
                await ws.close()
                logger.info(f"🔌 Соединение с {name} закрыто")
            except:
                pass