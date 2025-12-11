#!/usr/bin/env python3
"""
Конфигурация для Amvera модуля
"""

import os
from typing import List, Dict

# ============== SUPABASE ==============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# ============== TELEGRAM ==============
TG_API_ID = int(os.getenv("TG_API_ID", 0))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TARGET_CHAT_IDS = [
    int(chat_id.strip()) for chat_id in 
    os.getenv("TARGET_CHAT_IDS", "").split(",") 
    if chat_id.strip()
]

# ============== АВТО-ТОРГОВЛЯ ==============
AUTOTRADE_RULES = {
    'MIN_CONFIDENCE': 70.0,  # Минимальная уверенность
    'CHECK_INTERVAL': 5,     # Интервал проверки (сек)
    'DEMO_MODE': True,       # Демо-режим
    'MAX_RISK_PER_TRADE': 0.02,  # 2% риска на сделку
    'DAILY_LOSS_LIMIT': 0.05,    # 5% дневной лимит
}

# ============== БИРЖИ ==============
EXCHANGE_CONFIG = [
    {
        'name': 'binance',
        'ws_url': 'wss://stream.binance.com:9443/ws',
        'symbols': ['btcusdt', 'ethusdt', 'bnbusdt'],
        'subscribe_msg': {
            'method': 'SUBSCRIBE',
            'id': 1
        }
    },
    {
        'name': 'bybit',
        'ws_url': 'wss://stream.bybit.com/v5/public/spot',
        'symbols': ['BTCUSDT', 'ETHUSDT'],
        'subscribe_msg': {
            'op': 'subscribe',
            'args': []
        }
    }
]