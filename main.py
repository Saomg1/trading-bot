from dotenv import load_dotenv
load_dotenv()

import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import datetime, timezone

import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from db import PLANS

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# ══ СЮДА ВСТАВЬ СВОИ ДАННЫЕ ══════════════════════════════════════════════════
BOT_TOKEN       = "8873605360:AAEAyvAVfjWwnBqafyAhU7Tcewr0W1dUMIs"
ADMIN_ID        = 7617722286        # ← свой ID из @userinfobot
ADMIN_USERNAME  = "Saomg1"   # ← без @
# ═════════════════════════════════════════════════════════════════════════════

WALLET_ADDRESS  = "TVKM9eHD8iicCKk5tZNa5pajcmdJH9adJd"
BINGX_BASE      = "https://open-api.bingx.com"
TRONSCAN_API    = "https://apilist.tronscanapi.com/api"
USDT_CONTRACT   = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

bot    = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp     = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

class PayFSM(StatesGroup):
    waiting_tx = State()

class SignalFSM(StatesGroup):
    waiting_pair = State()

class ApiFSM(StatesGroup):
    waiting_key    = State()
    waiting_secret = State()

def is_admin(uid: int, username: str = "") -> bool:
    return uid == ADMIN_ID or (username and username.lower() == ADMIN_USERNAME.lower())

def kb_lang() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Русский", callback_data="lang:ru")
    b.button(text="🇺🇸 English", callback_data="lang:en")
    b.adjust(2)
    return b.as_markup()

def kb_menu(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if lang == "ru":
        b.button(text="📊 Получить сигнал", callback_data="m:signal")
        b.button(text="💎 Подписка",        callback_data="m:sub")
        b.button(text="🤖 Авто-торговля",   callback_data="m:auto")
        b.button(text="👤 Профиль",         callback_data="m:profile")
        b.button(text="ℹ️ Помощь",          callback_data="m:help")
    else:
        b.button(text="📊 Get Signal",   callback_data="m:signal")
        b.button(text="💎 Subscription", callback_data="m:sub")
        b.button(text="🤖 Auto-Trading", callback_data="m:auto")
        b.button(text="👤 Profile",      callback_data="m:profile")
        b.button(text="ℹ️ Help",         callback_data="m:help")
    b.adjust(2, 2, 1)
    return b.as_markup()

def kb_plans(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    mo = "мес" if lang == "ru" else "mo"
    b.button(text=f"🥉 Basic — 29 USDT/{mo}",        callback_data="plan:basic")
    b.button(text=f"🥈 Pro — 69 USDT/{mo} (вход▶️)", callback_data="plan:pro")
    b.button(text=f"👑 VIP — 129 USDT/{mo}",          callback_data="plan:vip")
    b.button(text="⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data="m:back")
    b.adjust(1)
    return b.as_markup()

def kb_plan_buy(plan: str, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Оплатить" if lang == "ru" else "💳 Pay Now", callback_data=f"buy:{plan}")
    b.button(text="⬅️ Назад"   if lang == "ru" else "⬅️ Back",    callback_data="m:sub")
    b.adjust(1)
    return b.as_markup()

def kb_paid(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я оплатил — проверить" if lang == "ru" else "✅ I paid — verify",
             callback_data="pay:check")
    b.button(text="⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data="m:sub")
    b.adjust(1)
    return b.as_markup()

def kb_pairs(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT","DOGE-USDT"]:
        b.button(text=p, callback_data=f"sig:{p}")
    b.button(text="✏️ Другая пара" if lang == "ru" else "✏️ Other pair", callback_data="sig:custom")
    b.button(text="⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data="m:back")
    b.adjust(2,2,2,1,1)
    return b.as_markup()

def kb_api(lang: str, connected: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if connected:
        b.button(text="🔑 Изменить ключи" if lang=="ru" else "🔑 Update Keys",   callback_data="api:connect")
        b.button(text="❌ Отключить"       if lang=="ru" else "❌ Disconnect",    callback_data="api:disc")
        b.button(text="💰 Баланс BingX"   if lang=="ru" else "💰 BingX Balance", callback_data="api:balance")
    else:
        b.button(text="🔑 Подключить BingX" if lang=="ru" else "🔑 Connect BingX", callback_data="api:connect")
    b.button(text="⬅️ Назад" if lang=="ru" else "⬅️ Back", callback_data="m:back")
    b.adjust(1)
    return b.as_markup()

def kb_back(lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data="m:back")
    return b.as_markup()

def kb_admin() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Полный отчёт", callback_data="admin:report")
    b.button(text="👥 Пользователи", callback_data="admin:users")
    b.button(text="💰 Платежи",      callback_data="admin:payments")
    b.button(text="📡 Сделки",       callback_data="admin:trades")
    b.button(text="🎟 Промокоды",    callback_data="admin:promos")
    b.adjust(1)
    return b.as_markup()

def kb_trade_confirm(trade_id: int, lang: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить" if lang=="ru" else "✅ Confirm", callback_data=f"tr:ok:{trade_id}")
    b.button(text="❌ Отменить"    if lang=="ru" else "❌ Cancel",  callback_data=f"tr:no:{trade_id}")
    b.adjust(1)
    return b.as_markup()

def T(lang: str, key: str, **kw) -> str:
    txt = _TX.get(key, {}).get(lang) or _TX.get(key, {}).get("ru") or key
    return txt.format(**kw) if kw else txt

_TX = {
    "welcome": {
        "ru": "👋 Привет, <b>{name}</b>!\n\n🚀 <b>Apex Trading Bot</b> — ваш помощник для торговли на BingX.\n\n📊 Сигналы · 🤖 Авто-торговля · 💎 Подписки\n\nВыберите язык:",
        "en": "👋 Hello, <b>{name}</b>!\n\n🚀 <b>Apex Trading Bot</b> — your BingX trading assistant.\n\n📊 Signals · 🤖 Auto-Trading · 💎 Subscriptions\n\nChoose language:",
    },
    "menu":     {"ru": "📋 <b>Главное меню</b>\n\nВыберите раздел:", "en": "📋 <b>Main Menu</b>\n\nChoose a section:"},
    "plans": {
        "ru": (
            "💎 <b>Тарифные планы Apex Trading</b>\n\n"
            "🥉 <b>Basic — 29 USDT/мес</b>\n• До 5 сигналов в день\n\n"
            "🥈 <b>Pro — 69 USDT/мес</b>\n• Безлимитные сигналы\n• Авто-ВХОД ▶️ (выход вручную)\n\n"
            "👑 <b>VIP — 129 USDT/мес</b>\n• Авто-ВХОД + авто-ВЫХОД (TP/SL)\n• Свой лимит сделок (/setlimit)\n• Ежедневные отчёты\n\nВыберите тариф:"
        ),
        "en": (
            "💎 <b>Apex Trading Plans</b>\n\n"
            "🥉 <b>Basic — 29 USDT/mo</b>\n• Up to 5 signals/day\n\n"
            "🥈 <b>Pro — 69 USDT/mo</b>\n• Unlimited signals\n• Auto-ENTRY ▶️ (exit manually)\n\n"
            "👑 <b>VIP — 129 USDT/mo</b>\n• Auto-ENTRY + auto-EXIT (TP/SL)\n• Custom trade limit\n• Daily reports\n\nChoose a plan:"
        ),
    },
    "plan_basic": {
        "ru": "🥉 <b>Basic — 29 USDT/мес</b>\n\n✅ До 5 сигналов в день\n✅ Зона входа, TP1, TP2, SL\n❌ Авто-торговля\n❌ Ежедневные отчёты",
        "en": "🥉 <b>Basic — 29 USDT/mo</b>\n\n✅ Up to 5 signals/day\n✅ Entry zone, TP1, TP2, SL\n❌ Auto-trading\n❌ Daily reports",
    },
    "plan_pro": {
        "ru": "🥈 <b>Pro — 69 USDT/мес</b>\n\n✅ Безлимитные сигналы\n✅ Авто-ВХОД ▶️\n⚠️ <b>Выход — только вручную!</b>\n❌ Авто-выход по TP/SL\n❌ Ежедневные отчёты",
        "en": "🥈 <b>Pro — 69 USDT/mo</b>\n\n✅ Unlimited signals\n✅ Auto-ENTRY ▶️\n⚠️ <b>Exit manually only!</b>\n❌ Auto-exit by TP/SL\n❌ Daily reports",
    },
    "plan_vip": {
        "ru": "👑 <b>VIP — 129 USDT/мес</b>\n\n✅ Безлимитные сигналы\n✅ Авто-ВХОД ▶️ + авто-ВЫХОД ⏹ (TP/SL)\n✅ Свой лимит сделок (/setlimit)\n✅ Ежедневные отчёты\n✅ VIP-поддержка 24/7",
        "en": "👑 <b>VIP — 129 USDT/mo</b>\n\n✅ Unlimited signals\n✅ Auto-ENTRY ▶️ + auto-EXIT ⏹ (TP/SL)\n✅ Custom trade limit\n✅ Daily reports\n✅ VIP support 24/7",
    },
    "payment": {
        "ru": "💳 <b>Оплата {plan} — {price} USDT</b>\n\n1️⃣ Переведите <b>{price} USDT (TRC20)</b> на:\n<code>{wallet}</code>\n\n2️⃣ Нажмите «Я оплатил»\n3️⃣ Вставьте TX hash\n\n⚠️ Только USDT TRC20 (TRON)!",
        "en": "💳 <b>Payment {plan} — {price} USDT</b>\n\n1️⃣ Send <b>{price} USDT (TRC20)</b> to:\n<code>{wallet}</code>\n\n2️⃣ Press 'I paid'\n3️⃣ Paste TX hash\n\n⚠️ USDT TRC20 (TRON) only!",
    },
    "enter_tx":    {"ru": "🔍 Введите TX hash транзакции:", "en": "🔍 Enter your TX hash:"},
    "verifying":   {"ru": "⏳ Проверяем...", "en": "⏳ Verifying..."},
    "pay_ok":      {"ru": "🎉 <b>Подписка {plan} активирована!</b>\n✅ {amount} USDT\n📅 До: {exp}", "en": "🎉 <b>{plan} activated!</b>\n✅ {amount} USDT\n📅 Until: {exp}"},
    "pay_fail":    {"ru": "❌ <b>Ошибка:</b> {err}", "en": "❌ <b>Error:</b> {err}"},
    "tx_dup":      {"ru": "⚠️ TX hash уже использован.", "en": "⚠️ TX hash already used."},
    "no_sub":      {"ru": "🔒 Требуется подписка.\n\nОформите в разделе «Подписка».", "en": "🔒 Subscription required."},
    "sig_limit":   {"ru": "⚠️ Лимит 5 сигналов/день (Basic).\n\nОбновитесь до Pro/VIP.", "en": "⚠️ 5 signals/day limit.\n\nUpgrade to Pro/VIP."},
    "sig_menu":    {"ru": "📊 <b>Сигналы</b>\n\nВыберите пару:", "en": "📊 <b>Signals</b>\n\nChoose a pair:"},
    "enter_pair":  {"ru": "✏️ Введите пару (пример: <code>SOL-USDT</code>):", "en": "✏️ Enter pair (e.g. <code>SOL-USDT</code>):"},
    "loading_sig": {"ru": "📡 Анализирую {symbol}...", "en": "📡 Analyzing {symbol}..."},
    "sig_error":   {"ru": "❌ Не удалось получить данные по <b>{symbol}</b>.", "en": "❌ Could not fetch data for <b>{symbol}</b>."},
    "api_intro": {
        "ru": "🔑 <b>Подключение BingX API</b>\n\n1. bingx.com → Аккаунт → API Management\n2. Create API Key\n3. Разрешения: <b>Trade, Read</b> (без Withdraw!)\n\nВведите <b>API Key</b>:",
        "en": "🔑 <b>Connect BingX API</b>\n\n1. bingx.com → Account → API Management\n2. Create API Key\n3. Permissions: <b>Trade, Read</b> (no Withdraw!)\n\nEnter <b>API Key</b>:",
    },
    "enter_secret": {"ru": "Введите <b>API Secret</b>:", "en": "Enter <b>API Secret</b>:"},
    "api_ok":       {"ru": "✅ BingX API подключён!\n💰 Баланс: <b>{bal:.2f} USDT</b>", "en": "✅ BingX API connected!\n💰 Balance: <b>{bal:.2f} USDT</b>"},
    "api_err":      {"ru": "❌ Ошибка API: {err}", "en": "❌ API error: {err}"},
    "api_disc_ok":  {"ru": "✅ API отключён.", "en": "✅ API disconnected."},
    "api_no_plan":  {"ru": "🔒 Авто-торговля только в Pro и VIP.", "en": "🔒 Auto-trading is Pro/VIP only."},
    "tr_cancel":    {"ru": "❌ Сделка отменена.", "en": "❌ Trade cancelled."},
    "tr_err":       {"ru": "❌ Ошибка: {err}", "en": "❌ Error: {err}"},
    "help": {
        "ru": "ℹ️ <b>Помощь</b>\n\n/start — Меню\n/signal — Сигнал\n/subscribe — Подписка\n/profile — Профиль\n/api — BingX API\n/promo КОД — Промокод\n/setlimit N — Лимит сделок (VIP)\n/myid — Мой Telegram ID",
        "en": "ℹ️ <b>Help</b>\n\n/start — Menu\n/signal — Signal\n/subscribe — Subscription\n/profile — Profile\n/api — BingX API\n/promo CODE — Promo code\n/setlimit N — Trade limit (VIP)\n/myid — My Telegram ID",
    },
    "not_admin": {"ru": "⛔ Нет доступа.", "en": "⛔ Access denied."},
    "free_ok":   {"ru": "✅ VIP выдан @{u} на {d} дней.", "en": "✅ VIP granted to @{u} for {d} days."},
    "free_fail": {"ru": "❌ Пользователь не найден.", "en": "❌ User not found."},
}
async def bx_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list:
    url = f"{BINGX_BASE}/openApi/swap/v2/quote/klines"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
        d = r.json()
    if d.get("code") != 0: raise ValueError(d.get("msg", "BingX error"))
    return d.get("data", [])

async def bx_balance(api_key: str, secret: str) -> float:
    ts  = str(int(time.time() * 1000))
    q   = f"timestamp={ts}"
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BINGX_BASE}/openApi/swap/v2/user/balance",
                        params=f"{q}&signature={sig}", headers={"X-BX-APIKEY": api_key})
        d = r.json()
    if d.get("code") != 0: raise ValueError(d.get("msg"))
    return float(d["data"]["balance"].get("availableMargin", 0))

async def bx_order(api_key: str, secret: str, symbol: str, side: str,
                   amount: float, sl: float, tp: float) -> dict:
    ts = str(int(time.time() * 1000))
    params = {"symbol": symbol, "side": "BUY" if side=="LONG" else "SELL",
              "positionSide": side, "type": "MARKET",
              "quoteOrderQty": str(amount), "stopLoss": str(sl),
              "takeProfit": str(tp), "timestamp": ts}
    q   = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BINGX_BASE}/openApi/swap/v2/trade/order",
                         params=f"{q}&signature={sig}", headers={"X-BX-APIKEY": api_key})
        d = r.json()
    if d.get("code") != 0: raise ValueError(d.get("msg"))
    return d.get("data", {})

async def bx_order_entry_only(api_key: str, secret: str, symbol: str,
                               side: str, amount: float) -> dict:
    ts = str(int(time.time() * 1000))
    params = {"symbol": symbol, "side": "BUY" if side=="LONG" else "SELL",
              "positionSide": side, "type": "MARKET",
              "quoteOrderQty": str(amount), "timestamp": ts}
    q   = "&".join(f"{k}={v}" for k,v in sorted(params.items()))
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BINGX_BASE}/openApi/swap/v2/trade/order",
                         params=f"{q}&signature={sig}", headers={"X-BX-APIKEY": api_key})
        d = r.json()
    if d.get("code") != 0: raise ValueError(d.get("msg"))
    return d.get("data", {})

def _ema(vals, p):
    out, k = [], 2/(p+1)
    for i, v in enumerate(vals):
        if i < p-1:    out.append(None)
        elif i == p-1: out.append(sum(vals[:p])/p)
        else:          out.append(v*k + out[-1]*(1-k))
    return out

def _rsi(vals, p=14):
    if len(vals) < p+1: return None
    g = [max(vals[i]-vals[i-1], 0) for i in range(1, len(vals))]
    l = [max(vals[i-1]-vals[i], 0) for i in range(1, len(vals))]
    ag, al = sum(g[-p:])/p, sum(l[-p:])/p
    return 100.0 if al == 0 else 100-100/(1+ag/al)

def _atr(klines, p=14):
    trs = [max(float(k["high"])-float(k["low"]),
               abs(float(k["high"])-float(klines[i-1]["close"])),
               abs(float(k["low"])-float(klines[i-1]["close"])))
           for i, k in enumerate(klines) if i > 0]
    return sum(trs[-p:])/min(len(trs), p) if trs else 0

def _dec(price):
    if price >= 1000: return 2
    if price >= 10:   return 3
    if price >= 1:    return 4
    return 6

async def generate_signal(symbol: str) -> dict:
    symbol = symbol.upper().replace("/", "-").replace("_", "-")
    if "-" not in symbol: symbol += "-USDT"
    k15, k1h = await asyncio.gather(bx_klines(symbol, "15m", 100), bx_klines(symbol, "1h", 60))
    c15 = [float(k["close"]) for k in k15]
    c1h = [float(k["close"]) for k in k1h]
    e20_15, e50_15 = _ema(c15, 20), _ema(c15, 50)
    e20_1h, e50_1h = _ema(c1h, 20), _ema(c1h, 50)
    rsi15  = _rsi(c15)
    atr    = _atr(k15)
    price  = float(k15[-1]["close"])
    vols   = [float(k["volume"]) for k in k15]
    surge  = vols[-1] > sum(vols[-20:])/20*1.5
    score  = 0
    rru, ren = [], []
    if e20_15[-1] and e50_15[-1]:
        if e20_15[-1] > e50_15[-1]: score+=2; rru.append("EMA20>EMA50 15м🟢"); ren.append("EMA20>EMA50 15m🟢")
        else:                        score-=2; rru.append("EMA20<EMA50 15м🔴"); ren.append("EMA20<EMA50 15m🔴")
    if e20_1h[-1] and e50_1h[-1]:
        if e20_1h[-1] > e50_1h[-1]: score+=3; rru.append("1ч тренд🟢"); ren.append("1h trend🟢")
        else:                        score-=3; rru.append("1ч тренд🔴"); ren.append("1h trend🔴")
    if rsi15:
        if rsi15 < 35:   score+=2; rru.append(f"RSI {rsi15:.0f} перепродан"); ren.append(f"RSI {rsi15:.0f} oversold")
        elif rsi15 > 65: score-=2; rru.append(f"RSI {rsi15:.0f} перекуплен"); ren.append(f"RSI {rsi15:.0f} overbought")
        else:            rru.append(f"RSI {rsi15:.0f} нейтр"); ren.append(f"RSI {rsi15:.0f} neutral")
    if surge: rru.append("Объём🔥"); ren.append("Volume🔥")
    side = "LONG" if score >= 0 else "SHORT"
    dec  = _dec(price)
    if side == "LONG":
        sl=round(price-atr*1.5,dec); tp1=round(price+atr*2,dec); tp2=round(price+atr*3.5,dec)
    else:
        sl=round(price+atr*1.5,dec); tp1=round(price-atr*2,dec); tp2=round(price-atr*3.5,dec)
    return {"symbol": symbol, "side": side, "price": price,
            "entry_lo": round(price*0.998,dec), "entry_hi": round(price*1.002,dec),
            "tp1": tp1, "tp2": tp2, "sl": sl, "atr": round(atr,dec),
            "rsi": round(rsi15,1) if rsi15 else None,
            "conf": min(95, max(30, 30+abs(score)*10)),
            "reason_ru": " · ".join(rru[:3]), "reason_en": " · ".join(ren[:3])}

def signal_text(lang: str, s: dict) -> str:
    side_ru = "🟢 LONG (Покупка)" if s["side"]=="LONG" else "🔴 SHORT (Продажа)"
    side_en = "🟢 LONG (Buy)"     if s["side"]=="LONG" else "🔴 SHORT (Sell)"
    rsi = str(s["rsi"]) if s["rsi"] else "N/A"
    if lang == "ru":
        return (f"📊 <b>Сигнал: {s['symbol']}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 {side_ru}\n💰 Цена: {s['price']} USDT\n"
                f"🎯 Вход: {s['entry_lo']} — {s['entry_hi']}\n"
                f"🛑 SL: {s['sl']}\n✅ TP1: {s['tp1']}\n🏆 TP2: {s['tp2']}\n"
                f"📈 ATR: {s['atr']} | RSI: {rsi}\n🔥 Уверенность: {s['conf']}%\n"
                f"━━━━━━━━━━━━━━━━━━━━\n💡 {s['reason_ru']}\n\n⚠️ <i>Не финансовый совет.</i>")
    return (f"📊 <b>Signal: {s['symbol']}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 {side_en}\n💰 Price: {s['price']} USDT\n"
            f"🎯 Entry: {s['entry_lo']} — {s['entry_hi']}\n"
            f"🛑 SL: {s['sl']}\n✅ TP1: {s['tp1']}\n🏆 TP2: {s['tp2']}\n"
            f"📈 ATR: {s['atr']} | RSI: {rsi}\n🔥 Confidence: {s['conf']}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n💡 {s['reason_en']}\n\n⚠️ <i>Not financial advice.</i>")

async def verify_tx(tx_hash: str, expected: float) -> dict:
    err = lambda m: {"ok": False, "error": m, "amount": 0}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{TRONSCAN_API}/transaction-info", params={"hash": tx_hash.strip()})
            d = r.json()
    except Exception as e: return err(f"Сеть: {e}")
    if not d or d.get("retCode") == 1: return err("Транзакция не найдена")
    if d.get("confirmations", 0) < 10: return err(f"Мало подтверждений: {d.get('confirmations',0)}/10")
    amount = 0.0
    for t in d.get("trc20TransferInfo", []):
        if (t.get("contract_address","").upper() == USDT_CONTRACT.upper() or t.get("symbol") == "USDT") \
           and t.get("to_address","").upper() == WALLET_ADDRESS.upper():
            amount = int(t.get("amount_str", t.get("amount","0"))) / 1_000_000; break
    if amount == 0: return err(f"Перевод на {WALLET_ADDRESS[:10]}... не найден")
    if amount < expected*0.98: return err(f"Сумма {amount:.2f} < {expected} USDT")
    return {"ok": True, "error": None, "amount": amount}

def fmt_date(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y") if ts else "—"
async def lang(uid): return await db.get_language(uid)

async def show_menu(message, uid, edit=False):
    l = await lang(uid); txt, kb = T(l, "menu"), kb_menu(l)
    if edit:
        try: await message.edit_text(txt, reply_markup=kb)
        except: await message.answer(txt, reply_markup=kb)
    else: await message.answer(txt, reply_markup=kb)

@router.message(CommandStart())
async def h_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    await db.upsert_user(uid, message.from_user.username, message.from_user.first_name)
    l = await lang(uid)
    await message.answer(T(l, "welcome", name=message.from_user.first_name or "Trader"), reply_markup=kb_lang())

@router.message(Command("myid"))
async def h_myid(message: Message):
    uid = message.from_user.id
    await message.answer(f"🆔 Твой Telegram ID: <code>{uid}</code>\n👤 @{message.from_user.username or '—'}")

@router.callback_query(F.data.startswith("lang:"))
async def h_lang(call: CallbackQuery):
    l = call.data.split(":")[1]
    await db.set_language(call.from_user.id, l)
    await call.message.edit_text("✅ <b>Русский</b>" if l=="ru" else "✅ <b>English</b>")
    await asyncio.sleep(0.3)
    await show_menu(call.message, call.from_user.id, edit=True)

@router.message(Command("menu"))
async def h_menu_cmd(message: Message, state: FSMContext):
    await state.clear(); await show_menu(message, message.from_user.id)

@router.callback_query(F.data == "m:back")
async def h_back(call: CallbackQuery, state: FSMContext):
    await state.clear(); await show_menu(call.message, call.from_user.id, edit=True)

@router.message(Command("profile"))
async def h_profile_cmd(message: Message): await _send_profile(message, message.from_user.id)

@router.callback_query(F.data == "m:profile")
async def h_profile_cb(call: CallbackQuery): await _send_profile(call.message, call.from_user.id, edit=True)

async def _send_profile(message, uid, edit=False):
    l = await lang(uid); user = await db.get_user(uid)
    plan, exp = await db.get_subscription(uid)
    api_ok = user and user["bingx_connected"]
    name = (user and user.get("first_name")) or "Trader"
    tl = await db.get_trade_limit(uid) if plan == "vip" else None
    plan_t = f"{PLANS[plan]['emoji']} {plan.upper()}" if plan else ("❌ Нет подписки" if l=="ru" else "❌ No subscription")
    api_t = ("🟢 Подключён" if l=="ru" else "🟢 Connected") if api_ok else ("🔴 Нет" if l=="ru" else "🔴 Not connected")
    tl_line = ""
    if tl is not None:
        tl_txt = ("безлимит" if tl==0 else str(tl)) if l=="ru" else ("unlimited" if tl==0 else str(tl))
        tl_line = f"📡 {'Лимит сделок/день' if l=='ru' else 'Trade limit/day'}: <b>{tl_txt}</b>\n"
    text = (f"👤 <b>{'Профиль' if l=='ru' else 'Profile'}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: <code>{uid}</code>\n📛 {'Имя' if l=='ru' else 'Name'}: {name}\n"
            f"💎 {'Подписка' if l=='ru' else 'Plan'}: <b>{plan_t}</b>\n"
            f"📅 {'До' if l=='ru' else 'Until'}: {fmt_date(exp) if plan else '—'}\n"
            f"🤖 BingX API: {api_t}\n"
            f"📊 {'Сигналов сегодня' if l=='ru' else 'Signals today'}: {user['signals_today'] if user else 0}\n"
            f"{tl_line}━━━━━━━━━━━━━━━━━━━━")
    kb = kb_back(l)
    if edit:
        try: await message.edit_text(text, reply_markup=kb)
        except: await message.answer(text, reply_markup=kb)
    else: await message.answer(text, reply_markup=kb)

@router.message(Command("subscribe"))
async def h_sub_cmd(message: Message, state: FSMContext):
    await state.clear(); l = await lang(message.from_user.id)
    await message.answer(T(l, "plans"), reply_markup=kb_plans(l))

@router.callback_query(F.data == "m:sub")
async def h_sub_cb(call: CallbackQuery, state: FSMContext):
    await state.clear(); l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, "plans"), reply_markup=kb_plans(l))

@router.callback_query(F.data.startswith("plan:"))
async def h_plan(call: CallbackQuery):
    plan = call.data.split(":")[1]; l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, f"plan_{plan}"), reply_markup=kb_plan_buy(plan, l))

@router.callback_query(F.data.startswith("buy:"))
async def h_buy(call: CallbackQuery, state: FSMContext):
    plan = call.data.split(":")[1]; l = await lang(call.from_user.id); price = PLANS[plan]["price"]
    await state.update_data(plan=plan, price=price)
    await call.message.edit_text(T(l, "payment", plan=PLANS[plan]["name"], price=price, wallet=WALLET_ADDRESS), reply_markup=kb_paid(l))

@router.callback_query(F.data == "pay:check")
async def h_pay_check(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("plan"): await call.answer("Сначала выберите тариф!", show_alert=True); return
    l = await lang(call.from_user.id); await state.set_state(PayFSM.waiting_tx)
    await call.message.edit_text(T(l, "enter_tx"), reply_markup=kb_back(l))

@router.message(PayFSM.waiting_tx)
async def h_tx(message: Message, state: FSMContext):
    uid = message.from_user.id; l = await lang(uid); tx = message.text.strip()
    data = await state.get_data(); plan = data.get("plan", "basic"); price = data.get("price", PLANS[plan]["price"])
    if await db.get_payment_by_hash(tx):
        await message.answer(T(l, "tx_dup"), reply_markup=kb_back(l)); await state.clear(); return
    msg = await message.answer(T(l, "verifying"))
    await db.create_payment(uid, plan, price, tx)
    result = await verify_tx(tx, price)
    if result["ok"]:
        await db.activate_subscription(uid, plan, PLANS[plan]["days"])
        await db.update_payment_status(tx, "verified"); await state.clear()
        exp = int(time.time()) + PLANS[plan]["days"]*86400
        await msg.edit_text(T(l, "pay_ok", plan=PLANS[plan]["name"], amount=result["amount"], exp=fmt_date(exp)), reply_markup=kb_back(l))
        user = await db.get_user(uid); uname = f"@{user['username']}" if user and user.get("username") else f"ID:{uid}"
        await bot.send_message(ADMIN_ID, f"🆕 <b>Новая подписка!</b>\n👤 {uname}\n💎 {plan.upper()}\n💰 {result['amount']} USDT\n🔗 <code>{tx}</code>")
    else:
        await db.update_payment_status(tx, "failed")
        await msg.edit_text(T(l, "pay_fail", err=result["error"]), reply_markup=kb_back(l)); await state.clear()

@router.message(Command("signal"))
async def h_signal_cmd(message: Message, state: FSMContext):
    await state.clear(); uid = message.from_user.id; l = await lang(uid); parts = message.text.split()
    if len(parts) > 1: await _do_signal(message, uid, l, parts[1], state)
    else: await message.answer(T(l, "sig_menu"), reply_markup=kb_pairs(l))

@router.callback_query(F.data == "m:signal")
async def h_signal_cb(call: CallbackQuery, state: FSMContext):
    await state.clear(); l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, "sig_menu"), reply_markup=kb_pairs(l))

@router.callback_query(F.data == "sig:custom")
async def h_sig_custom(call: CallbackQuery, state: FSMContext):
    l = await lang(call.from_user.id); await state.set_state(SignalFSM.waiting_pair)
    await call.message.edit_text(T(l, "enter_pair"), reply_markup=kb_back(l))

@router.message(SignalFSM.waiting_pair)
async def h_sig_pair(message: Message, state: FSMContext):
    await _do_signal(message, message.from_user.id, await lang(message.from_user.id), message.text.strip(), state)

@router.callback_query(F.data.startswith("sig:") & ~F.data.eq("sig:custom"))
async def h_sig_select(call: CallbackQuery, state: FSMContext):
    symbol = call.data.split(":", 1)[1]; uid = call.from_user.id; l = await lang(uid)
    await call.message.edit_text(T(l, "loading_sig", symbol=symbol))
    await _do_signal(call.message, uid, l, symbol, state, edit=True)

async def _do_signal(message, uid, l, symbol, state, edit=False):
    await state.clear()
    plan, _ = await db.get_subscription(uid)
    if not plan:
        if edit: await message.edit_text(T(l, "no_sub"), reply_markup=kb_back(l))
        else:    await message.answer(T(l, "no_sub"), reply_markup=kb_back(l))
        return
    if not await db.check_signal_quota(uid, PLANS[plan]["signal_limit"]):
        if edit: await message.edit_text(T(l, "sig_limit"), reply_markup=kb_back(l))
        else:    await message.answer(T(l, "sig_limit"), reply_markup=kb_back(l))
        return
    load_msg = message if edit else await message.answer(T(l, "loading_sig", symbol=symbol.upper()))
    try:
        sig = await generate_signal(symbol)
        await db.increment_signal_count(uid)
        await load_msg.edit_text(signal_text(l, sig), reply_markup=kb_back(l))
        if PLANS[plan]["auto_trade"]:
            user = await db.get_user(uid)
            if user and user["bingx_connected"]: await _maybe_trade(uid, l, sig, plan)
    except Exception as e:
        logger.error(f"Signal {symbol}: {e}")
        await load_msg.edit_text(T(l, "sig_error", symbol=symbol.upper()), reply_markup=kb_back(l))

async def _maybe_trade(uid, l, sig, plan):
    api_key, secret = await db.get_api_keys(uid)
    if not api_key: return
    try: balance = await bx_balance(api_key, secret)
    except: return
    amount = round(balance*0.05, 2)
    if amount < 5: return
    if plan == "vip" and not await db.check_trade_quota(uid):
        limit = await db.get_trade_limit(uid)
        await bot.send_message(uid, f"⚠️ Дневной лимит сделок: <b>{limit}</b>\nИзменить: /setlimit"); return
    trade_id = await db.create_trade(uid, sig["symbol"], sig["side"], sig["price"], sig["sl"], sig["tp1"], sig["tp2"], amount)
    if plan == "pro": await _exec_entry_only(uid, l, trade_id, sig, api_key, secret, amount)
    else:             await _exec_full(uid, l, trade_id, sig, api_key, secret, amount)

async def _exec_full(uid, l, trade_id, sig, api_key, secret, amount):
    try:
        order = await bx_order(api_key, secret, sig["symbol"], sig["side"], amount, sig["sl"], sig["tp1"])
        oid = order.get("order", {}).get("orderId", "N/A")
        await db.update_trade(trade_id, str(oid), "open"); await db.increment_trade_count(uid)
        await bot.send_message(uid,
            f"✅ <b>Сделка VIP открыта</b>\n{sig['symbol']} {sig['side']} | {amount} USDT\n"
            f"🛑 SL: {sig['sl']}  ✅ TP: {sig['tp1']}\n🔑 <code>{oid}</code>")
    except Exception as e:
        await db.update_trade(trade_id, "", "error")
        await bot.send_message(uid, T(l, "tr_err", err=str(e)[:100]))

async def _exec_entry_only(uid, l, trade_id, sig, api_key, secret, amount):
    try:
        order = await bx_order_entry_only(api_key, secret, sig["symbol"], sig["side"], amount)
        oid = order.get("order", {}).get("orderId", "N/A")
        await db.update_trade(trade_id, str(oid), "open")
        await bot.send_message(uid,
            f"▶️ <b>Вход Pro</b>\n{sig['symbol']} {sig['side']} | {amount} USDT\n"
            f"━━━━━━━━━━━━━━━━━━━━\n⚠️ <b>Закройте ВРУЧНУЮ!</b>\n"
            f"🛑 SL: {sig['sl']}\n✅ TP1: {sig['tp1']}  🏆 TP2: {sig['tp2']}\n🔑 <code>{oid}</code>")
    except Exception as e:
        await db.update_trade(trade_id, "", "error")
        await bot.send_message(uid, T(l, "tr_err", err=str(e)[:100]))

@router.callback_query(F.data.startswith("tr:no:"))
async def h_tr_cancel(call: CallbackQuery):
    trade_id = int(call.data.split(":")[2]); l = await lang(call.from_user.id)
    await db.update_trade(trade_id, "", "cancelled"); await call.message.edit_text(T(l, "tr_cancel"))
@router.message(Command("api"))
async def h_api_cmd(message: Message):
    uid = message.from_user.id; l = await lang(uid); user = await db.get_user(uid)
    plan, _ = await db.get_subscription(uid)
    if not plan or not PLANS.get(plan, {}).get("auto_trade"):
        await message.answer(T(l, "api_no_plan"), reply_markup=kb_back(l)); return
    connected = user and user["bingx_connected"]
    txt = ("🤖 <b>BingX API</b>\n\n🟢 Подключён" if connected and l=="ru"
           else "🤖 <b>BingX API</b>\n\n🟢 Connected" if connected
           else "🤖 <b>BingX API</b>\n\n🔴 Не подключён" if l=="ru"
           else "🤖 <b>BingX API</b>\n\n🔴 Not connected")
    await message.answer(txt, reply_markup=kb_api(l, connected))

@router.callback_query(F.data == "m:auto")
async def h_auto_cb(call: CallbackQuery, state: FSMContext):
    await state.clear(); uid = call.from_user.id; l = await lang(uid)
    plan, _ = await db.get_subscription(uid)
    if not plan or not PLANS.get(plan, {}).get("auto_trade"):
        await call.message.edit_text(T(l, "api_no_plan"), reply_markup=kb_back(l)); return
    user = await db.get_user(uid); connected = user and user["bingx_connected"]
    txt = ("🤖 <b>BingX API</b>\n\n🟢 Подключён" if connected and l=="ru"
           else "🤖 <b>BingX API</b>\n\n🟢 Connected" if connected
           else "🤖 <b>BingX API</b>\n\n🔴 Не подключён" if l=="ru"
           else "🤖 <b>BingX API</b>\n\n🔴 Not connected")
    await call.message.edit_text(txt, reply_markup=kb_api(l, connected))

@router.callback_query(F.data == "api:connect")
async def h_api_connect(call: CallbackQuery, state: FSMContext):
    l = await lang(call.from_user.id); await state.set_state(ApiFSM.waiting_key)
    await call.message.edit_text(T(l, "api_intro"), reply_markup=kb_back(l))

@router.message(ApiFSM.waiting_key)
async def h_api_key(message: Message, state: FSMContext):
    await state.update_data(api_key=message.text.strip()); await message.delete()
    l = await lang(message.from_user.id); await state.set_state(ApiFSM.waiting_secret)
    await message.answer(T(l, "enter_secret"), reply_markup=kb_back(l))

@router.message(ApiFSM.waiting_secret)
async def h_api_secret(message: Message, state: FSMContext):
    uid = message.from_user.id; l = await lang(uid)
    data = await state.get_data(); api_key = data.get("api_key", ""); secret = message.text.strip()
    await message.delete()
    try:
        bal = await bx_balance(api_key, secret)
        await db.save_api_keys(uid, api_key, secret); await state.clear()
        await message.answer(T(l, "api_ok", bal=bal), reply_markup=kb_back(l))
    except Exception as e:
        await state.clear(); await message.answer(T(l, "api_err", err=str(e)[:100]), reply_markup=kb_back(l))

@router.callback_query(F.data == "api:disc")
async def h_api_disc(call: CallbackQuery):
    l = await lang(call.from_user.id); await db.remove_api_keys(call.from_user.id)
    await call.message.edit_text(T(l, "api_disc_ok"), reply_markup=kb_back(l))

@router.callback_query(F.data == "api:balance")
async def h_api_balance(call: CallbackQuery):
    uid = call.from_user.id; l = await lang(uid)
    api_key, secret = await db.get_api_keys(uid)
    if not api_key: await call.answer("API not connected", show_alert=True); return
    try:
        bal = await bx_balance(api_key, secret); user = await db.get_user(uid)
        await call.message.edit_text(f"💰 <b>Баланс BingX: {bal:.2f} USDT</b>",
                                     reply_markup=kb_api(l, user and user["bingx_connected"]))
    except Exception as e: await call.answer(f"Error: {e}", show_alert=True)

@router.message(Command("setlimit"))
async def h_setlimit(message: Message):
    uid = message.from_user.id; l = await lang(uid); plan, _ = await db.get_subscription(uid)
    if plan != "vip":
        await message.answer("🔒 /setlimit только для VIP." if l=="ru" else "🔒 /setlimit is VIP only."); return
    parts = message.text.split()
    if len(parts) < 2:
        cur = await db.get_trade_limit(uid)
        cur_t = ("безлимит" if cur==0 else str(cur)) if l=="ru" else ("unlimited" if cur==0 else str(cur))
        await message.answer(f"📊 Лимит сделок/день: <b>{cur_t}</b>\n\n/setlimit 5\n/setlimit 0 — безлимит"); return
    try:
        limit = int(parts[1])
        if limit < 0: raise ValueError()
    except: await message.answer("❌ Введите число ≥ 0"); return
    await db.set_trade_limit(uid, limit)
    txt = ("безлимит" if limit==0 else f"{limit}/день") if l=="ru" else ("unlimited" if limit==0 else f"{limit}/day")
    await message.answer(f"✅ Лимит: <b>{txt}</b>")

@router.message(Command("promo"))
async def h_promo(message: Message):
    uid = message.from_user.id; l = await lang(uid); parts = message.text.split()
    if len(parts) < 2:
        await message.answer("🎟 Использование: /promo КОД" if l=="ru" else "🎟 Usage: /promo CODE"); return
    promo, err = await db.use_promo(parts[1].strip(), uid)
    if err: await message.answer(f"❌ {err}"); return
    await db.activate_subscription(uid, promo["plan"], promo["days"])
    await message.answer(f"🎉 <b>Промокод активирован!</b>\n💎 {promo['plan'].upper()}\n📅 {promo['days']} дней")

@router.message(Command("help"))
async def h_help_cmd(message: Message):
    l = await lang(message.from_user.id); await message.answer(T(l, "help"), reply_markup=kb_back(l))

@router.callback_query(F.data == "m:help")
async def h_help_cb(call: CallbackQuery):
    l = await lang(call.from_user.id); await call.message.edit_text(T(l, "help"), reply_markup=kb_back(l))

@router.message(Command("admin"))
async def h_admin(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔ Нет доступа"); return
    stats = await db.get_stats(); pc = stats["plans"]
    await message.answer(
        f"🛡 <b>Admin Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: <b>{stats['total']}</b>\n✅ Активных: <b>{stats['active']}</b>\n"
        f"🥉{pc.get('basic',0)} 🥈{pc.get('pro',0)} 👑{pc.get('vip',0)}\n"
        f"💰 Выручка: <b>{stats['revenue']:.2f} USDT</b>\n━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb_admin())

@router.callback_query(F.data == "admin:report")
async def h_admin_report(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    stats = await db.get_stats(); adv = await db.get_advanced_stats(); pc = stats["plans"]
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(
        f"📋 <b>Отчёт</b> | {now}\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 {stats['total']} юзеров | {stats['active']} активных\n"
        f"🥉{pc.get('basic',0)} 🥈{pc.get('pro',0)} 👑{pc.get('vip',0)}\n"
        f"💰 Выручка: <b>{stats['revenue']:.2f} USDT</b>\n"
        f"Платежей: {adv['payments_total']} ✅{adv['payments_ok']} ❌{adv['payments_fail']}\n"
        f"Сделок: {adv['trades_total']} ✅{adv['trades_open']} ❌{adv['trades_err']}\n"
        f"🤖 API: {adv['api_connected']} | 📊 Сигналов: {adv['signals_today']}",
        reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:users")
async def h_admin_users(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    users = await db.get_recent_users(10)
    lines = [f"• {'@'+u['username'] if u.get('username') else 'ID:'+str(u['telegram_id'])} — {u.get('subscription') or '—'}" for u in users]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"👥 <b>Последние 10</b>\n━━━━━━━━━━━━━━━━━━━━\n"+("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:payments")
async def h_admin_payments(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    payments = await db.get_recent_payments(10)
    lines = [f"{'✅' if p['status']=='verified' else '❌' if p['status']=='failed' else '⏳'} {p['plan'].upper()} {p['amount']} USDT <code>{p['tx_hash'][:10]}…</code>" for p in payments]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"💰 <b>Платежи</b>\n━━━━━━━━━━━━━━━━━━━━\n"+("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:trades")
async def h_admin_trades(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    trades = await db.get_recent_trades(10)
    lines = [f"{'✅' if t['status']=='open' else '❌' if t['status']=='error' else '⏳'} {t['symbol']} {t['side']} {t['amount_usdt']} USDT" for t in trades]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"📡 <b>Сделки</b>\n━━━━━━━━━━━━━━━━━━━━\n"+("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:promos")
async def h_admin_promos(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    promos = await db.list_promos()
    lines = [f"🎟 <code>{p['code']}</code> — {p['plan'].upper()} {p['days']}д [{p['used']}/{p['max_uses']}]" for p in promos]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(
        f"🎟 <b>Промокоды</b>\n━━━━━━━━━━━━━━━━━━━━\n"+("\n".join(lines) or "Нет")+
        "\n\n<i>Создать: /addpromo КОД план дней</i>", reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:back")
async def h_admin_back(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    stats = await db.get_stats(); pc = stats["plans"]
    await call.message.edit_text(
        f"🛡 <b>Admin Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 {stats['total']} | ✅ {stats['active']}\n"
        f"🥉{pc.get('basic',0)} 🥈{pc.get('pro',0)} 👑{pc.get('vip',0)}\n"
        f"💰 <b>{stats['revenue']:.2f} USDT</b>\n━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb_admin())

@router.message(Command("user"))
async def h_admin_user(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔ Нет доступа"); return
    parts = message.text.split()
    if len(parts) < 2: await message.answer("Использование: /user @username"); return
    user = await db.get_user_by_username(parts[1].lstrip("@"))
    if not user: await message.answer("❌ Пользователь не найден"); return
    uid = user["telegram_id"]; plan, exp = await db.get_subscription(uid)
    api_key, secret = await db.get_api_keys(uid)
    bal_text = "—"
    if api_key:
        try: bal = await bx_balance(api_key, secret); bal_text = f"{bal:.2f} USDT"
        except: bal_text = "Ошибка API"
    tl = user.get("daily_trade_limit", 0)
    await message.answer(
        f"👤 <b>Клиент</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{uid}</code>\n📛 {user.get('first_name') or '—'}\n"
        f"👤 @{user.get('username') or '—'}\n"
        f"💎 <b>{plan.upper() if plan else '❌ Нет'}</b>\n"
        f"📅 До: {fmt_date(exp) if plan else '—'}\n"
        f"🤖 API: {'🟢' if api_key else '🔴'}\n"
        f"💰 Баланс: <b>{bal_text}</b>\n"
        f"📊 Сигналов сегодня: {user.get('signals_today', 0)}\n"
        f"📡 Лимит сделок: {'безлимит' if tl==0 else tl}\n"
        f"━━━━━━━━━━━━━━━━━━━━")

@router.message(Command("stats"))
async def h_stats(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    stats = await db.get_stats(); pc = stats["plans"]
    await message.answer(f"📊 Всего: {stats['total']} | Актив: {stats['active']}\n"
                         f"🥉{pc.get('basic',0)} 🥈{pc.get('pro',0)} 👑{pc.get('vip',0)}\n"
                         f"💰 {stats['revenue']:.2f} USDT")

@router.message(Command("freeaccess"))
async def h_free(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 2: await message.answer("/freeaccess @username [дней]"); return
    uname = parts[1].lstrip("@"); days = int(parts[2]) if len(parts) > 2 else 30
    user = await db.get_user_by_username(uname)
    if not user: await message.answer("❌ Не найден"); return
    await db.activate_subscription(user["telegram_id"], "vip", days)
    await message.answer(f"✅ VIP @{uname} на {days} дней")
    await bot.send_message(user["telegram_id"], f"🎁 Вам выдан бесплатный VIP на {days} дней!")

@router.message(Command("addpromo"))
async def h_addpromo(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 4: await message.answer("Использование: /addpromo КОД план дней [раз]\nПример: /addpromo SALE50 pro 30 1\nПланы: basic / pro / vip"); return
    code=parts[1].upper(); plan=parts[2].lower(); days=int(parts[3]); uses=int(parts[4]) if len(parts)>4 else 1
    if plan not in ("basic","pro","vip"): await message.answer("❌ Планы: basic / pro / vip"); return
    await db.create_promo(code, plan, days, uses)
    await message.answer(f"✅ Промокод создан!\n🎟 <code>{code}</code>\n💎 {plan.upper()} на {days} дней\n👥 Использований: {uses}")

@router.message(Command("compensate"))
async def h_comp(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3: await message.answer("/compensate @user дней [причина]"); return
    uname=parts[1].lstrip("@"); days=int(parts[2]); reason=parts[3] if len(parts)>3 else "Компенсация"
    user = await db.get_user_by_username(uname)
    if not user: await message.answer("❌ Не найден"); return
    await db.activate_subscription(user["telegram_id"], user.get("subscription") or "vip", days)
    await message.answer(f"✅ @{uname} +{days} дней. {reason}")
    await bot.send_message(user["telegram_id"], f"🎁 Компенсация +{days} дней. Причина: {reason}")

@router.message(Command("broadcast"))
async def h_broadcast(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    text = message.text.split(maxsplit=1)
    if len(text) < 2: await message.answer("/broadcast текст"); return
    import aiosqlite as _sq
    async with _sq.connect(db.DATABASE_PATH) as dbc:
        dbc.row_factory = _sq.Row
        async with dbc.execute("SELECT telegram_id FROM users") as cur:
            users = await cur.fetchall()
    ok = fail = 0
    for u in users:
        try: await bot.send_message(u["telegram_id"], text[1]); ok+=1
        except: fail+=1
        await asyncio.sleep(0.05)
    await message.answer(f"📢 ✅{ok} ❌{fail}")

async def daily_reports_task():
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == 8 and now.minute == 0:
            for u in await db.get_vip_with_api():
                try:
                    api_key, secret = await db.get_api_keys(u["telegram_id"])
                    if not api_key: continue
                    bal = await bx_balance(api_key, secret); l2 = u.get("language","ru")
                    await bot.send_message(u["telegram_id"],
                        f"📊 {'Ежедневный отчёт VIP' if l2=='ru' else 'VIP Daily Report'}\n"
                        f"💰 {'Баланс' if l2=='ru' else 'Balance'}: <b>{bal:.2f} USDT</b>\n"
                        f"📅 {now.strftime('%d.%m.%Y')}")
                except Exception as e: logger.error(f"Report {u['telegram_id']}: {e}")
            await asyncio.sleep(60)
        else: await asyncio.sleep(30)

async def on_startup():
    await db.init_db()
    await bot.set_my_commands([
        BotCommand(command="start",     description="🏠 Главное меню"),
        BotCommand(command="signal",    description="📊 Получить сигнал"),
        BotCommand(command="subscribe", description="💎 Подписка"),
        BotCommand(command="profile",   description="👤 Профиль"),
        BotCommand(command="api",       description="🤖 BingX API"),
        BotCommand(command="promo",     description="🎟 Промокод"),
        BotCommand(command="setlimit",  description="📡 Лимит сделок (VIP)"),
        BotCommand(command="myid",      description="🆔 Мой Telegram ID"),
        BotCommand(command="help",      description="ℹ️ Помощь"),
    ])
    asyncio.create_task(daily_reports_task())
    logger.info("✅ Apex Trading Bot запущен!")
    try: await bot.send_message(ADMIN_ID, "🟢 <b>Apex Trading Bot запущен!</b>")
    except: pass

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    # 1. Обработка нажатия кнопки "Промокоды" (покажет список или меню)
@router.callback_query(lambda c: c.data == "admin:promos")
async def admin_promos_menu(callback: CallbackQuery):
    promos = await db.list_promos() # Функция list_promos у тебя есть в db.py
    if not promos:
        await callback.message.answer("Список промокодов пуст.")
        return
    
    text = "🎟 **Активные промокоды:**\n\n"
    for p in promos:
        text += f"Код: `{p['code']}` | План: {p['plan']} | Использовано: {p['used']}/{p['max_uses']}\n"
    await callback.message.answer(text, parse_mode="HTML")

# 2. Обработка ввода промокода пользователем (через текст)
# Допустим, пользователь пишет "ПРОМО: КОД"
@router.message(lambda message: message.text.lower().startswith("промо:"))
async def activate_promo_command(message: Message):
    code = message.text.split(":")[1].strip()
    promo, error = await db.use_promo(code, message.from_user.id)
    
    if error:
        await message.answer(f"❌ Ошибка: {error}")
    else:
        # Активируем подписку (функция activate_subscription у тебя есть в db.py)
        await db.activate_subscription(message.from_user.id, promo['plan'], promo['days'])
        await message.answer(f"✅ Успешно! Подписка {promo['plan']} на {promo['days']} дней активирована.")
        # Эту часть вставь в конец файла main.py

@router.message(Command("set_vip"))
async def make_me_vip(message: Message):
    # Добавим вывод в консоль Railway
    print(f"DEBUG: Попытка админ-команды от ID: {message.from_user.id}, Тип ID: {type(message.from_user.id)}")
    
    # Сравним жестко
    if int(message.from_user.id) != 7617722286:
        await message.answer(f"❌ Нет прав. Ваш ID: {message.from_user.id}")
        return 
    
    await db.activate_subscription(message.from_user.id, "vip", 365)
    await message.answer("✅ Статус VIP активирован на 365 дней!")

if __name__ == "__main__":
    print("🚀 Apex Trading Terminal запущен!")
    asyncio.run(main())