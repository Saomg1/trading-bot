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
    BotCommand, CallbackQuery, InlineKeyboardMarkup, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from db import PLANS, TRADE_MIN_USDT, TRADE_MAX_USDT, TRADE_PCT

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "8873605360:AAEAyvAVfjWwnBqafyAhU7Tcewr0W1dUMIs")
ADMIN_ID       = int(os.environ.get("ADMIN_ID",   "7617722286"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME",  "Saomg1")
WALLET_ADDRESS = "TVKM9eHD8iicCKk5tZNa5pajcmdJH9adJd"
BINGX_BASE     = "https://open-api.bingx.com"
TRONSCAN_API   = "https://apilist.tronscanapi.com/api"
USDT_CONTRACT  = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

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

def is_admin(uid: int, uname: str = "") -> bool:
    return uid == ADMIN_ID or (uname and uname.lower() == ADMIN_USERNAME.lower())
# Список модераторов — только рассылка
MODERATOR_IDS = set(
    int(x) for x in os.environ.get("MODERATOR_IDS", "").split(",") if x.strip()
)

def is_moderator(uid: int) -> bool:
    return uid in MODERATOR_IDS

def fmt_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y") if ts else "—"

async def lang(uid: int) -> str:
    return await db.get_language(uid)

DIV = "━━━━━━━━━━━━━━━━━━━━"

def kb_lang() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Русский", callback_data="lang:ru")
    b.button(text="🇺🇸 English", callback_data="lang:en")
    b.adjust(2)
    return b.as_markup()

def kb_menu(l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if l == "ru":
        b.button(text="📊 Сигнал",       callback_data="m:signal")
        b.button(text="💎 Подписка",      callback_data="m:sub")
        b.button(text="🤖 Авто-торговля", callback_data="m:auto")
        b.button(text="👤 Профиль",       callback_data="m:profile")
        b.button(text="ℹ️ Помощь",        callback_data="m:help")
    else:
        b.button(text="📊 Signal",      callback_data="m:signal")
        b.button(text="💎 Subscription",callback_data="m:sub")
        b.button(text="🤖 Auto-Trade",  callback_data="m:auto")
        b.button(text="👤 Profile",     callback_data="m:profile")
        b.button(text="ℹ️ Help",        callback_data="m:help")
    b.adjust(2, 2, 1)
    return b.as_markup()

def kb_plans(l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    mo = "мес" if l == "ru" else "mo"
    b.button(text=f"🥉 Basic · 29 USDT/{mo}",  callback_data="plan:basic")
    b.button(text=f"🥈 Pro · 69 USDT/{mo}",    callback_data="plan:pro")
    b.button(text=f"👑 VIP · 129 USDT/{mo}",   callback_data="plan:vip")
    b.button(text="⬅️ Назад" if l=="ru" else "⬅️ Back", callback_data="m:back")
    b.adjust(1)
    return b.as_markup()

def kb_plan_buy(plan: str, l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Оплатить" if l=="ru" else "💳 Pay Now", callback_data=f"buy:{plan}")
    b.button(text="⬅️ Назад"   if l=="ru" else "⬅️ Back",    callback_data="m:sub")
    b.adjust(1)
    return b.as_markup()

def kb_paid(l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я оплатил — проверить" if l=="ru" else "✅ I paid — verify",
             callback_data="pay:check")
    b.button(text="⬅️ Назад" if l=="ru" else "⬅️ Back", callback_data="m:sub")
    b.adjust(1)
    return b.as_markup()

def kb_pairs(l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT","DOGE-USDT"]:
        b.button(text=p, callback_data=f"sig:{p}")
    b.button(text="✏️ Своя пара" if l=="ru" else "✏️ Custom pair", callback_data="sig:custom")
    b.button(text="⬅️ Назад"     if l=="ru" else "⬅️ Back",        callback_data="m:back")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()

def kb_api(l: str, connected: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if connected:
        b.button(text="🔑 Изменить ключи"  if l=="ru" else "🔑 Update Keys",   callback_data="api:connect")
        b.button(text="❌ Отключить"        if l=="ru" else "❌ Disconnect",    callback_data="api:disc")
        b.button(text="💰 Баланс BingX"    if l=="ru" else "💰 BingX Balance", callback_data="api:balance")
    else:
        b.button(text="🔗 Подключить BingX" if l=="ru" else "🔗 Connect BingX", callback_data="api:connect")
    b.button(text="⬅️ Назад" if l=="ru" else "⬅️ Back", callback_data="m:back")
    b.adjust(1)
    return b.as_markup()

def kb_back(l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад" if l=="ru" else "⬅️ Back", callback_data="m:back")
    return b.as_markup()

def kb_admin() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Отчёт",      callback_data="admin:report")
    b.button(text="👥 Юзеры",      callback_data="admin:users")
    b.button(text="💰 Платежи",    callback_data="admin:payments")
    b.button(text="📡 Сделки",     callback_data="admin:trades")
    b.button(text="🎟 Промокоды",  callback_data="admin:promos")
    b.adjust(2, 2, 1)
    return b.as_markup()

def kb_trade_confirm(trade_id: int, sig: dict, l: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    side_lbl = "LONG 📈" if sig["side"] == "LONG" else "SHORT 📉"
    b.button(
        text=f"✅ Открыть {side_lbl} · {sig.get('confirm_amount',0):.0f} USDT",
        callback_data=f"tr:ok:{trade_id}",
    )
    b.button(
        text="❌ Пропустить" if l=="ru" else "❌ Skip",
        callback_data=f"tr:no:{trade_id}",
    )
    b.adjust(1)
    return b.as_markup()

def T(l: str, key: str, **kw) -> str:
    txt = _TX.get(key, {}).get(l) or _TX.get(key, {}).get("ru") or key
    return txt.format(**kw) if kw else txt

_TX = {
    "welcome": {
        "ru": (
            "👋 Привет, <b>{name}</b>!\n\n"
            f"{DIV}\n"
            "🚀 <b>APEX TRADING BOT</b>\n"
            "ИИ-помощник для торговли на BingX\n"
            f"{DIV}\n\n"
            "📊 <b>Сигналы</b> — EMA · RSI · ATR анализ\n"
            "🤖 <b>Авто-торговля</b> — бот сам открывает сделки\n"
            "💎 <b>Подписки</b> — Basic / Pro / VIP\n\n"
            "🌐 Выберите язык:"
        ),
        "en": (
            "👋 Hello, <b>{name}</b>!\n\n"
            f"{DIV}\n"
            "🚀 <b>APEX TRADING BOT</b>\n"
            "AI-powered BingX trading assistant\n"
            f"{DIV}\n\n"
            "📊 <b>Signals</b> — EMA · RSI · ATR analysis\n"
            "🤖 <b>Auto-Trading</b> — bot opens trades for you\n"
            "💎 <b>Plans</b> — Basic / Pro / VIP\n\n"
            "🌐 Choose language:"
        ),
    },
    "menu": {
        "ru": f"📋 <b>Главное меню</b>\n{DIV}\nВыберите раздел:",
        "en": f"📋 <b>Main Menu</b>\n{DIV}\nChoose a section:",
    },
    "plans": {
        "ru": (
            f"💎 <b>ТАРИФЫ APEX TRADING</b>\n{DIV}\n\n"
            "🥉 <b>Basic · 29 USDT/мес</b>\n"
            "  └ До 5 сигналов/день\n"
            "  └ Вход, TP1, TP2, SL\n\n"
            "🥈 <b>Pro · 69 USDT/мес</b>\n"
            "  └ ∞ Безлимит сигналов\n"
            "  └ 🤖 Авто-ВХОД (мин $5)\n"
            "  └ ⚠️ Выход — только вручную\n\n"
            "👑 <b>VIP · 129 USDT/мес</b>\n"
            "  └ ∞ Безлимит сигналов\n"
            "  └ 🤖 Авто-ВХОД + авто-ВЫХОД (TP/SL)\n"
            "  └ Без лимита сделок (или с вашим)\n"
            "  └ 📊 Ежедневные отчёты\n\n"
            f"{DIV}\n👇 Выберите тариф:"
        ),
        "en": (
            f"💎 <b>APEX TRADING PLANS</b>\n{DIV}\n\n"
            "🥉 <b>Basic · 29 USDT/mo</b>\n"
            "  └ Up to 5 signals/day\n"
            "  └ Entry, TP1, TP2, SL\n\n"
            "🥈 <b>Pro · 69 USDT/mo</b>\n"
            "  └ ∞ Unlimited signals\n"
            "  └ 🤖 Auto-ENTRY (min $5)\n"
            "  └ ⚠️ Manual exit only\n\n"
            "👑 <b>VIP · 129 USDT/mo</b>\n"
            "  └ ∞ Unlimited signals\n"
            "  └ 🤖 Auto-ENTRY + auto-EXIT (TP/SL)\n"
            "  └ Unlimited trades (or your limit)\n"
            "  └ 📊 Daily reports\n\n"
            f"{DIV}\n👇 Choose a plan:"
        ),
    },
    "plan_basic": {
        "ru": (
            f"🥉 <b>Basic · 29 USDT/мес</b>\n{DIV}\n\n"
            "✅ До 5 сигналов в день\n"
            "✅ Зона входа, TP1, TP2, SL\n"
            "✅ EMA + RSI + ATR анализ\n"
            "❌ Авто-торговля\n"
            "❌ Ежедневные отчёты\n\n"
            "💡 <i>Для старта и ручной торговли</i>"
        ),
        "en": (
            f"🥉 <b>Basic · 29 USDT/mo</b>\n{DIV}\n\n"
            "✅ Up to 5 signals/day\n"
            "✅ Entry zone, TP1, TP2, SL\n"
            "✅ EMA + RSI + ATR analysis\n"
            "❌ Auto-trading\n"
            "❌ Daily reports\n\n"
            "💡 <i>Perfect for manual trading</i>"
        ),
    },
    "plan_pro": {
        "ru": (
            f"🥈 <b>Pro · 69 USDT/мес</b>\n{DIV}\n\n"
            "✅ Безлимитные сигналы\n"
            "✅ 🤖 Авто-ВХОД в рынок (Market order)\n"
            "✅ Мин. сумма: <b>$5 USDT</b>\n"
            "⚠️ <b>Закрывать позицию нужно вручную!</b>\n"
            "❌ Авто-выход по TP/SL\n"
            "❌ Ежедневные отчёты\n\n"
            "💡 <i>Бот входит — вы решаете когда выйти</i>"
        ),
        "en": (
            f"🥈 <b>Pro · 69 USDT/mo</b>\n{DIV}\n\n"
            "✅ Unlimited signals\n"
            "✅ 🤖 Auto-ENTRY (Market order)\n"
            "✅ Min amount: <b>$5 USDT</b>\n"
            "⚠️ <b>You must close positions manually!</b>\n"
            "❌ Auto-exit by TP/SL\n"
            "❌ Daily reports\n\n"
            "💡 <i>Bot enters — you decide when to exit</i>"
        ),
    },
    "plan_vip": {
        "ru": (
            f"👑 <b>VIP · 129 USDT/мес</b>\n{DIV}\n\n"
            "✅ Безлимитные сигналы\n"
            "✅ 🤖 Полный автопилот (ВХОД + ВЫХОД)\n"
            "✅ <b>Без лимита сделок</b>\n"
            "✅ /setamount — своя сумма → полный авто\n"
            "✅ Без суммы → подтверждение → авто-выход\n"
            "✅ Ежедневные отчёты\n"
            "✅ VIP-поддержка 24/7\n\n"
            "💡 <i>Максимальная автоматизация</i>"
        ),
        "en": (
            f"👑 <b>VIP · 129 USDT/mo</b>\n{DIV}\n\n"
            "✅ Unlimited signals\n"
            "✅ 🤖 Full autopilot (ENTRY + EXIT)\n"
            "✅ <b>Unlimited trades</b>\n"
            "✅ /setamount — your amount → fully auto\n"
            "✅ No amount → confirm → auto-exit\n"
            "✅ Daily reports\n"
            "✅ VIP support 24/7\n\n"
            "💡 <i>Maximum automation</i>"
        ),
    },
    "payment": {
        "ru": (
            f"💳 <b>Оплата {{plan}} · {{price}} USDT</b>\n{DIV}\n\n"
            "1️⃣ Отправьте ровно <b>{price} USDT</b>\n"
            "   сеть: <b>TRC20 (TRON)</b>\n"
            "   на адрес:\n"
            "   <code>{wallet}</code>\n\n"
            "2️⃣ Нажмите <b>«Я оплатил»</b>\n"
            "3️⃣ Вставьте TX hash из кошелька\n\n"
            f"{DIV}\n"
            "⚠️ <b>Только USDT · TRC20 · TRON!</b>\n"
            "⚠️ Другие токены и сети не принимаем\n"
            "⏱ Активация ~1 минута после подтверждений"
        ),
        "en": (
            f"💳 <b>Payment {{plan}} · {{price}} USDT</b>\n{DIV}\n\n"
            "1️⃣ Send exactly <b>{price} USDT</b>\n"
            "   network: <b>TRC20 (TRON)</b>\n"
            "   to address:\n"
            "   <code>{wallet}</code>\n\n"
            "2️⃣ Press <b>'I paid'</b>\n"
            "3️⃣ Paste TX hash from your wallet\n\n"
            f"{DIV}\n"
            "⚠️ <b>USDT · TRC20 · TRON only!</b>\n"
            "⚠️ Other tokens/networks are rejected\n"
            "⏱ Activation ~1 minute after confirmations"
        ),
    },
    "enter_tx":    {"ru": "🔍 Введите <b>TX hash</b> транзакции:", "en": "🔍 Enter your <b>TX hash</b>:"},
    "verifying":   {"ru": "⏳ Проверяем транзакцию...", "en": "⏳ Verifying transaction..."},
    "pay_ok": {
        "ru": "🎉 <b>Подписка {plan} активирована!</b>\n✅ Получено: <b>{amount} USDT</b>\n📅 Действует до: <b>{exp}</b>",
        "en": "🎉 <b>{plan} activated!</b>\n✅ Received: <b>{amount} USDT</b>\n📅 Valid until: <b>{exp}</b>",
    },
    "pay_fail":    {"ru": "❌ <b>Ошибка верификации:</b>\n{err}", "en": "❌ <b>Verification error:</b>\n{err}"},
    "tx_dup":      {"ru": "⚠️ Этот TX hash уже использован.", "en": "⚠️ TX hash already used."},
    "no_sub":      {
        "ru": f"🔒 <b>Требуется подписка</b>\n{DIV}\nОформите тариф в разделе «💎 Подписка».",
        "en": f"🔒 <b>Subscription required</b>\n{DIV}\nGet a plan in the '💎 Subscription' section.",
    },
    "sig_limit": {
        "ru": (
            f"⚠️ <b>Лимит Basic исчерпан</b>\n{DIV}\n\n"
            "Plan Basic: до 5 сигналов в день.\n"
            "Обновитесь до <b>Pro/VIP</b> для безлимита."
        ),
        "en": (
            f"⚠️ <b>Basic limit reached</b>\n{DIV}\n\n"
            "Basic plan: up to 5 signals/day.\n"
            "Upgrade to <b>Pro/VIP</b> for unlimited."
        ),
    },
    "sig_menu":    {
        "ru": f"📊 <b>Торговые сигналы</b>\n{DIV}\nВыберите пару или введите свою:",
        "en": f"📊 <b>Trading Signals</b>\n{DIV}\nChoose a pair or enter your own:",
    },
    "enter_pair":  {"ru": "✏️ Введите пару (пример: <code>SOL-USDT</code>):", "en": "✏️ Enter pair (e.g. <code>SOL-USDT</code>):"},
    "loading_sig": {"ru": "📡 Анализирую <b>{symbol}</b>...", "en": "📡 Analyzing <b>{symbol}</b>..."},
    "sig_error": {
        "ru": "❌ Не удалось получить данные по <b>{symbol}</b>.\n\nПроверьте название пары (пример: <code>BTC-USDT</code>).",
        "en": "❌ Could not fetch data for <b>{symbol}</b>.\n\nCheck the pair name (e.g. <code>BTC-USDT</code>).",
    },
    "api_intro": {
        "ru": (
            f"🔑 <b>Подключение BingX API</b>\n{DIV}\n\n"
            "<b>📋 Инструкция:</b>\n"
            "① Зайдите на <b>bingx.com</b>\n"
            "② Аккаунт → <b>API Management</b>\n"
            "③ Нажмите <b>Create API Key</b>\n"
            "④ Разрешения:\n"
            "   ✅ <b>Trade</b>  ✅ <b>Read</b>  ❌ <b>Withdraw</b>\n"
            "⑤ Скопируйте API Key и Secret\n\n"
            f"{DIV}\n"
            "🔐 Введите <b>API Key</b>:\n"
            "<i>Сообщение удалится автоматически</i>"
        ),
        "en": (
            f"🔑 <b>Connect BingX API</b>\n{DIV}\n\n"
            "<b>📋 Instructions:</b>\n"
            "① Go to <b>bingx.com</b>\n"
            "② Account → <b>API Management</b>\n"
            "③ Click <b>Create API Key</b>\n"
            "④ Permissions:\n"
            "   ✅ <b>Trade</b>  ✅ <b>Read</b>  ❌ <b>Withdraw</b>\n"
            "⑤ Copy your API Key and Secret\n\n"
            f"{DIV}\n"
            "🔐 Enter your <b>API Key</b>:\n"
            "<i>Message will be deleted automatically</i>"
        ),
    },
    "enter_secret": {"ru": "🔐 Теперь введите <b>API Secret</b>:\n<i>Сообщение удалится автоматически</i>", "en": "🔐 Now enter your <b>API Secret</b>:\n<i>Message will be deleted automatically</i>"},
    "api_ok":       {"ru": "✅ <b>BingX API подключён!</b>\n💰 Баланс: <b>{bal:.2f} USDT</b>", "en": "✅ <b>BingX API connected!</b>\n💰 Balance: <b>{bal:.2f} USDT</b>"},
    "api_err":      {"ru": "❌ <b>Ошибка API:</b>\n<code>{err}</code>\n\n💡 Проверьте ключи и разрешения.", "en": "❌ <b>API error:</b>\n<code>{err}</code>\n\n💡 Check your keys and permissions."},
    "api_disc_ok":  {"ru": "✅ BingX API отключён.", "en": "✅ BingX API disconnected."},
    "api_no_plan":  {
        "ru": f"🔒 <b>Авто-торговля недоступна</b>\n{DIV}\nДоступна только на тарифах <b>Pro</b> и <b>VIP</b>.\n\nОформить: /subscribe",
        "en": f"🔒 <b>Auto-trading unavailable</b>\n{DIV}\nAvailable on <b>Pro</b> and <b>VIP</b> plans only.\n\nGet plan: /subscribe",
    },
    "tr_cancel":    {"ru": "❌ Сделка отменена.", "en": "❌ Trade skipped."},
    "tr_err":       {"ru": "❌ <b>Ошибка сделки:</b>\n<code>{err}</code>", "en": "❌ <b>Trade error:</b>\n<code>{err}</code>"},
    "help": {
        "ru": (
            f"ℹ️ <b>СПРАВКА · APEX TRADING BOT</b>\n{DIV}\n\n"
            "🏠 <b>Основное</b>\n"
            "  /start — главное меню\n"
            "  /profile — мой профиль\n"
            "  /myid — мой Telegram ID\n\n"
            "📊 <b>Сигналы</b>\n"
            "  /signal — выбор пары\n"
            "  /signal BTC-USDT — быстрый сигнал\n\n"
            "💎 <b>Подписка</b>\n"
            "  /subscribe — тарифы и оплата\n"
            "  /promo КОД — активировать промокод\n\n"
            "🤖 <b>Авто-торговля (Pro/VIP)</b>\n"
            "  /api — подключить BingX API\n\n"
            "👑 <b>VIP-команды</b>\n"
            "  /setamount N — сумма авто-сделки в USDT\n"
            "     → При указании суммы: полный автопилот\n"
            "     → Без суммы: бот спрашивает подтверждение\n"
            "     Пример: /setamount 200\n"
            "     /setamount 0 — сбросить (авто 5% баланса)\n\n"
            "  /setlimit N — лимит сделок/день (0 = без лимита)\n"
            f"{DIV}\n"
            f"💬 Поддержка: @Saomg1"
        ),
        "en": (
            f"ℹ️ <b>HELP · APEX TRADING BOT</b>\n{DIV}\n\n"
            "🏠 <b>Main</b>\n"
            "  /start — main menu\n"
            "  /profile — my profile\n"
            "  /myid — my Telegram ID\n\n"
            "📊 <b>Signals</b>\n"
            "  /signal — choose pair\n"
            "  /signal BTC-USDT — quick signal\n\n"
            "💎 <b>Subscription</b>\n"
            "  /subscribe — plans and payment\n"
            "  /promo CODE — activate promo code\n\n"
            "🤖 <b>Auto-Trading (Pro/VIP)</b>\n"
            "  /api — connect BingX API\n\n"
            "👑 <b>VIP Commands</b>\n"
            "  /setamount N — trade size in USDT\n"
            "     → With amount set: full autopilot\n"
            "     → No amount: bot asks confirmation\n"
            "     Example: /setamount 200\n"
            "     /setamount 0 — reset (auto 5% balance)\n\n"
            "  /setlimit N — daily trade limit (0 = unlimited)\n"
            f"{DIV}\n"
            f"💬 Support: @Saomg1"
        ),
    },
    "not_admin": {"ru": "⛔ Нет доступа.", "en": "⛔ Access denied."},
}
async def bx_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list:
    url = f"{BINGX_BASE}/openApi/swap/v2/quote/klines"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
        d = r.json()
    if int(d.get("code", -1)) != 0:
        raise ValueError(d.get("msg", f"BingX error for {symbol}"))
    data = d.get("data", [])
    if not data:
        raise ValueError(f"Нет данных по паре {symbol}. Возможно, пара не торгуется на BingX Futures.")
    return data

async def bx_balance(api_key: str, secret: str) -> float:
    ts  = str(int(time.time() * 1000))
    q   = f"timestamp={ts}"
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BINGX_BASE}/openApi/swap/v2/user/balance",
            params=f"{q}&signature={sig}",
            headers={"X-BX-APIKEY": api_key},
        )
        d = r.json()
    if int(d.get("code", -1)) != 0:
        raise ValueError(d.get("msg", "Balance error"))
    return float(d["data"]["balance"].get("availableMargin", 0))

async def bx_order(api_key: str, secret: str, symbol: str, side: str,
                   amount: float, sl: float, tp: float) -> dict:
    ts = str(int(time.time() * 1000))
    params = {
        "symbol": symbol,
        "side": "BUY" if side == "LONG" else "SELL",
        "positionSide": side,
        "type": "MARKET",
        "quoteOrderQty": str(amount),
        "stopLoss": str(sl),
        "takeProfit": str(tp),
        "timestamp": ts,
    }
    q   = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{BINGX_BASE}/openApi/swap/v2/trade/order",
            params=f"{q}&signature={sig}",
            headers={"X-BX-APIKEY": api_key},
        )
        d = r.json()
    if int(d.get("code", -1)) != 0:
        raise ValueError(d.get("msg", "Order error"))
    return d.get("data", {})

async def bx_order_entry_only(api_key: str, secret: str, symbol: str,
                               side: str, amount: float) -> dict:
    ts = str(int(time.time() * 1000))
    params = {
        "symbol": symbol,
        "side": "BUY" if side == "LONG" else "SELL",
        "positionSide": side,
        "type": "MARKET",
        "quoteOrderQty": str(amount),
        "timestamp": ts,
    }
    q   = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{BINGX_BASE}/openApi/swap/v2/trade/order",
            params=f"{q}&signature={sig}",
            headers={"X-BX-APIKEY": api_key},
        )
        d = r.json()
    if int(d.get("code", -1)) != 0:
        raise ValueError(d.get("msg", "Entry order error"))
    return d.get("data", {})

def _ema(vals, p):
    out, k = [], 2 / (p + 1)
    for i, v in enumerate(vals):
        if i < p - 1:    out.append(None)
        elif i == p - 1: out.append(sum(vals[:p]) / p)
        else:            out.append(v * k + out[-1] * (1 - k))
    return out

def _rsi(vals, p=14):
    if len(vals) < p + 1: return None
    g  = [max(vals[i] - vals[i-1], 0) for i in range(1, len(vals))]
    lo = [max(vals[i-1] - vals[i], 0) for i in range(1, len(vals))]
    ag, al = sum(g[-p:]) / p, sum(lo[-p:]) / p
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

def _atr(klines, p=14):
    trs = [
        max(
            float(k["high"]) - float(k["low"]),
            abs(float(k["high"]) - float(klines[i-1]["close"])),
            abs(float(k["low"])  - float(klines[i-1]["close"])),
        )
        for i, k in enumerate(klines) if i > 0
    ]
    return sum(trs[-p:]) / min(len(trs), p) if trs else 0

def _dec(price):
    if price >= 1000: return 2
    if price >= 10:   return 3
    if price >= 1:    return 4
    return 6

async def generate_signal(symbol: str) -> dict:
    symbol = symbol.upper().replace("/", "-").replace("_", "-")
    if "-" not in symbol: symbol += "-USDT"

    try:
        k15, k1h = await asyncio.gather(
            bx_klines(symbol, "15m", 100),
            bx_klines(symbol, "1h",  60),
        )
    except Exception as e:
        raise ValueError(str(e))

    if len(k15) < 55 or len(k1h) < 25:
        raise ValueError(f"Недостаточно свечей для анализа {symbol}. Попробуйте другую пару.")

    c15 = [float(k["close"]) for k in k15]
    c1h = [float(k["close"]) for k in k1h]
    e20_15, e50_15 = _ema(c15, 20), _ema(c15, 50)
    e20_1h, e50_1h = _ema(c1h, 20), _ema(c1h, 50)
    rsi15 = _rsi(c15)
    atr   = _atr(k15)
    price = float(k15[-1]["close"])
    vols  = [float(k["volume"]) for k in k15]
    avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else sum(vols) / len(vols)
    surge = vols[-1] > avg_vol * 1.5

    score = 0
    rru, ren = [], []

    if e20_15[-1] and e50_15[-1]:
        if e20_15[-1] > e50_15[-1]:
            score += 2; rru.append("EMA20>EMA50 (15м)🟢"); ren.append("EMA20>EMA50 (15m)🟢")
        else:
            score -= 2; rru.append("EMA20<EMA50 (15м)🔴"); ren.append("EMA20<EMA50 (15m)🔴")

    if e20_1h[-1] and e50_1h[-1]:
        if e20_1h[-1] > e50_1h[-1]:
            score += 3; rru.append("Тренд 1ч🟢"); ren.append("1h trend🟢")
        else:
            score -= 3; rru.append("Тренд 1ч🔴"); ren.append("1h trend🔴")

    if rsi15:
        if rsi15 < 35:
            score += 2; rru.append(f"RSI {rsi15:.0f}↓ перепродан"); ren.append(f"RSI {rsi15:.0f}↓ oversold")
        elif rsi15 > 65:
            score -= 2; rru.append(f"RSI {rsi15:.0f}↑ перекуплен"); ren.append(f"RSI {rsi15:.0f}↑ overbought")
        else:
            rru.append(f"RSI {rsi15:.0f} нейтр"); ren.append(f"RSI {rsi15:.0f} neutral")

    if surge:
        score += 1; rru.append("Объём🔥"); ren.append("Volume spike🔥")

    side = "LONG" if score >= 0 else "SHORT"
    dec  = _dec(price)

    if side == "LONG":
        sl  = round(price - atr * 1.5, dec)
        tp1 = round(price + atr * 2.0, dec)
        tp2 = round(price + atr * 3.5, dec)
    else:
        sl  = round(price + atr * 1.5, dec)
        tp1 = round(price - atr * 2.0, dec)
        tp2 = round(price - atr * 3.5, dec)

    conf = min(85, max(30, 50 + abs(score) * 8))
    conf_icon = "🔥🔥🔥" if conf >= 75 else ("🔥🔥" if conf >= 60 else "🔥")

    return {
        "symbol": symbol, "side": side, "price": price,
        "entry_lo": round(price * 0.998, dec),
        "entry_hi": round(price * 1.002, dec),
        "tp1": tp1, "tp2": tp2, "sl": sl,
        "atr": round(atr, dec),
        "rsi": round(rsi15, 1) if rsi15 else None,
        "conf": conf, "conf_icon": conf_icon,
        "reason_ru": " · ".join(rru[:3]),
        "reason_en": " · ".join(ren[:3]),
        "confirm_amount": 0,
    }

def signal_text(l: str, s: dict) -> str:
    side_ru = "🟢 LONG · Покупка" if s["side"]=="LONG" else "🔴 SHORT · Продажа"
    side_en = "🟢 LONG · Buy"     if s["side"]=="LONG" else "🔴 SHORT · Sell"
    rsi     = str(s["rsi"]) if s["rsi"] else "N/A"
    ci      = s.get("conf_icon", "🔥")
    if l == "ru":
        return (
            f"📊 <b>СИГНАЛ · {s['symbol']}</b>\n{DIV}\n"
            f"📍 {side_ru}\n"
            f"💰 Цена: <b>{s['price']} USDT</b>\n"
            f"{DIV}\n"
            f"🎯 Зона входа: {s['entry_lo']} — {s['entry_hi']}\n"
            f"🛑 Stop Loss:     <b>{s['sl']}</b>\n"
            f"✅ Take Profit 1: <b>{s['tp1']}</b>\n"
            f"🏆 Take Profit 2: <b>{s['tp2']}</b>\n"
            f"{DIV}\n"
            f"📈 ATR: {s['atr']}  |  📉 RSI: {rsi}\n"
            f"💡 {s['reason_ru']}\n"
            f"{DIV}\n"
            f"{ci} <b>Сила сигнала: {s['conf']}%</b>\n\n"
            f"<i>⚠️ Не является финансовым советом. Торгуйте осознанно.</i>"
        )
    return (
        f"📊 <b>SIGNAL · {s['symbol']}</b>\n{DIV}\n"
        f"📍 {side_en}\n"
        f"💰 Price: <b>{s['price']} USDT</b>\n"
        f"{DIV}\n"
        f"🎯 Entry zone: {s['entry_lo']} — {s['entry_hi']}\n"
        f"🛑 Stop Loss:     <b>{s['sl']}</b>\n"
        f"✅ Take Profit 1: <b>{s['tp1']}</b>\n"
        f"🏆 Take Profit 2: <b>{s['tp2']}</b>\n"
        f"{DIV}\n"
        f"📈 ATR: {s['atr']}  |  📉 RSI: {rsi}\n"
        f"💡 {s['reason_en']}\n"
        f"{DIV}\n"
        f"{ci} <b>Signal strength: {s['conf']}%</b>\n\n"
        f"<i>⚠️ Not financial advice. Trade responsibly.</i>"
    )

async def verify_tx(tx_hash: str, expected: float) -> dict:
    err_r = lambda m: {"ok": False, "error": m, "amount": 0}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{TRONSCAN_API}/transaction-info", params={"hash": tx_hash.strip()})
            d = r.json()
    except Exception as e:
        return err_r(f"Сетевая ошибка: {e}")
    if not d or d.get("retCode") == 1:
        return err_r("Транзакция не найдена. Проверьте TX hash.")
    if d.get("confirmations", 0) < 10:
        return err_r(f"Мало подтверждений: {d.get('confirmations',0)}/10. Подождите ~1-2 мин.")
    amount = 0.0
    for t in d.get("trc20TransferInfo", []):
        if (
            t.get("contract_address","").upper() == USDT_CONTRACT.upper()
            or t.get("symbol") == "USDT"
        ) and t.get("to_address","").upper() == WALLET_ADDRESS.upper():
            amount = int(t.get("amount_str", t.get("amount","0"))) / 1_000_000
            break
    if amount == 0:
        return err_r("Перевод USDT на наш адрес не найден.\nПроверьте адрес, сеть (TRC20) и сумму.")
    if amount < expected * 0.98:
        return err_r(f"Сумма {amount:.2f} USDT меньше {expected} USDT")
    return {"ok": True, "error": None, "amount": amount}

async def show_menu(message, uid, edit=False):
    l   = await lang(uid)
    txt = T(l, "menu")
    kb  = kb_menu(l)
    if edit:
        try: await message.edit_text(txt, reply_markup=kb)
        except: await message.answer(txt, reply_markup=kb)
    else:
        await message.answer(txt, reply_markup=kb)

# ── HANDLERS ──────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def h_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    await db.upsert_user(uid, message.from_user.username, message.from_user.first_name)
    l = await lang(uid)
    await message.answer(T(l, "welcome", name=message.from_user.first_name or "Trader"), reply_markup=kb_lang())

@router.message(Command("myid"))
async def h_myid(message: Message):
    await message.answer(f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n👤 Username: @{message.from_user.username or '—'}")

@router.callback_query(F.data.startswith("lang:"))
async def h_lang(call: CallbackQuery):
    l = call.data.split(":")[1]
    await db.set_language(call.from_user.id, l)
    await call.message.edit_text("✅ <b>Русский выбран</b>" if l=="ru" else "✅ <b>English selected</b>")
    await asyncio.sleep(0.3)
    await show_menu(call.message, call.from_user.id, edit=True)

@router.message(Command("menu"))
async def h_menu_cmd(message: Message, state: FSMContext):
    await state.clear()
    await show_menu(message, message.from_user.id)

@router.callback_query(F.data == "m:back")
async def h_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_menu(call.message, call.from_user.id, edit=True)

@router.message(Command("profile"))
async def h_profile_cmd(message: Message):
    await _send_profile(message, message.from_user.id)

@router.callback_query(F.data == "m:profile")
async def h_profile_cb(call: CallbackQuery):
    await _send_profile(call.message, call.from_user.id, edit=True)

async def _send_profile(message, uid, edit=False):
    l    = await lang(uid)
    user = await db.get_user(uid)
    plan, exp = await db.get_subscription(uid)
    api_ok = user and user["bingx_connected"]
    name   = (user and user.get("first_name")) or "Trader"
    tl     = await db.get_trade_limit(uid)  if plan == "vip" else None
    ta     = await db.get_trade_amount(uid) if plan in ("vip", "pro") else None
    plan_t = (f"{PLANS[plan]['emoji']} {PLANS[plan]['name']}" if plan else ("❌ Нет подписки" if l=="ru" else "❌ No subscription"))
    api_t  = (("🟢 Подключён" if l=="ru" else "🟢 Connected") if api_ok else ("🔴 Не подключён" if l=="ru" else "🔴 Not connected"))
    tl_line = ""
    if tl is not None:
        tl_s = ("безлимит" if tl==0 else f"{tl}/день") if l=="ru" else ("unlimited" if tl==0 else f"{tl}/day")
        tl_line = f"📡 {'Лимит сделок' if l=='ru' else 'Trade limit'}: <b>{tl_s}</b>\n"
    ta_line = ""
    if ta is not None:
        if plan == "vip":
            ta_s = ("авто 5% → с подтверждением" if ta==0 else f"{ta} USDT → полный авто") if l=="ru" else ("auto 5% → confirm" if ta==0 else f"{ta} USDT → full auto")
        else:
            ta_s = ("авто 5% баланса" if ta==0 else f"{ta} USDT") if l=="ru" else ("auto 5% balance" if ta==0 else f"{ta} USDT")
        ta_line = f"💵 {'Сумма сделки' if l=='ru' else 'Trade amount'}: <b>{ta_s}</b>\n"
    text = (
        f"👤 <b>{'ПРОФИЛЬ' if l=='ru' else 'PROFILE'}</b>\n{DIV}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📛 {'Имя' if l=='ru' else 'Name'}: {name}\n"
        f"💎 {'Тариф' if l=='ru' else 'Plan'}: <b>{plan_t}</b>\n"
        f"📅 {'До' if l=='ru' else 'Until'}: {fmt_date(exp) if plan else '—'}\n"
        f"🤖 BingX API: {api_t}\n"
        f"📊 {'Сигналов сегодня' if l=='ru' else 'Signals today'}: {user['signals_today'] if user else 0}\n"
        f"{tl_line}{ta_line}{DIV}"
    )
    if edit:
        try:    await message.edit_text(text, reply_markup=kb_back(l))
        except: await message.answer(text, reply_markup=kb_back(l))
    else:
        await message.answer(text, reply_markup=kb_back(l))

@router.message(Command("subscribe"))
async def h_sub_cmd(message: Message, state: FSMContext):
    await state.clear()
    l = await lang(message.from_user.id)
    await message.answer(T(l, "plans"), reply_markup=kb_plans(l))

@router.callback_query(F.data == "m:sub")
async def h_sub_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, "plans"), reply_markup=kb_plans(l))

@router.callback_query(F.data.startswith("plan:"))
async def h_plan(call: CallbackQuery):
    plan = call.data.split(":")[1]
    l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, f"plan_{plan}"), reply_markup=kb_plan_buy(plan, l))

@router.callback_query(F.data.startswith("buy:"))
async def h_buy(call: CallbackQuery, state: FSMContext):
    plan  = call.data.split(":")[1]
    l     = await lang(call.from_user.id)
    price = PLANS[plan]["price"]
    await state.update_data(plan=plan, price=price)
    await call.message.edit_text(
        T(l, "payment", plan=PLANS[plan]["name"], price=price, wallet=WALLET_ADDRESS),
        reply_markup=kb_paid(l),
    )

@router.callback_query(F.data == "pay:check")
async def h_pay_check(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("plan"):
        await call.answer("Сначала выберите тариф!", show_alert=True); return
    l = await lang(call.from_user.id)
    await state.set_state(PayFSM.waiting_tx)
    await call.message.edit_text(T(l, "enter_tx"), reply_markup=kb_back(l))

@router.message(PayFSM.waiting_tx)
async def h_tx(message: Message, state: FSMContext):
    uid   = message.from_user.id
    l     = await lang(uid)
    tx    = message.text.strip()
    data  = await state.get_data()
    plan  = data.get("plan", "basic")
    price = data.get("price", PLANS[plan]["price"])
    if await db.get_payment_by_hash(tx):
        await message.answer(T(l, "tx_dup"), reply_markup=kb_back(l))
        await state.clear(); return
    msg = await message.answer(T(l, "verifying"))
    await db.create_payment(uid, plan, price, tx)
    result = await verify_tx(tx, price)
    if result["ok"]:
        await db.activate_subscription(uid, plan, PLANS[plan]["days"])
        await db.update_payment_status(tx, "verified")
        await state.clear()
        exp = int(time.time()) + PLANS[plan]["days"] * 86400
        await msg.edit_text(
            T(l, "pay_ok", plan=PLANS[plan]["name"], amount=result["amount"], exp=fmt_date(exp)),
            reply_markup=kb_back(l),
        )
        user  = await db.get_user(uid)
        uname = f"@{user['username']}" if user and user.get("username") else f"ID:{uid}"
        await bot.send_message(
            ADMIN_ID,
            f"💰 <b>НОВАЯ ОПЛАТА</b>\n{DIV}\n👤 {uname}\n"
            f"💎 Тариф: <b>{plan.upper()}</b>\n"
            f"✅ Сумма: <b>{result['amount']} USDT</b>\n"
            f"🔗 TX: <code>{tx}</code>",
        )
    else:
        await db.update_payment_status(tx, "failed")
        await msg.edit_text(T(l, "pay_fail", err=result["error"]), reply_markup=kb_back(l))
        await state.clear()

@router.message(Command("signal"))
async def h_signal_cmd(message: Message, state: FSMContext):
    await state.clear()
    uid   = message.from_user.id
    l     = await lang(uid)
    parts = message.text.split()
    if len(parts) > 1: await _do_signal(message, uid, l, parts[1], state)
    else:               await message.answer(T(l, "sig_menu"), reply_markup=kb_pairs(l))

@router.callback_query(F.data == "m:signal")
async def h_signal_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, "sig_menu"), reply_markup=kb_pairs(l))

@router.callback_query(F.data == "sig:custom")
async def h_sig_custom(call: CallbackQuery, state: FSMContext):
    l = await lang(call.from_user.id)
    await state.set_state(SignalFSM.waiting_pair)
    await call.message.edit_text(T(l, "enter_pair"), reply_markup=kb_back(l))

@router.message(SignalFSM.waiting_pair)
async def h_sig_pair(message: Message, state: FSMContext):
    await _do_signal(message, message.from_user.id,
                     await lang(message.from_user.id), message.text.strip(), state)

# ✅ ИСПРАВЛЕНО: заменён ~F.data.eq() на F.data != "sig:custom"
@router.callback_query(F.data.startswith("sig:") & (F.data != "sig:custom"))
async def h_sig_select(call: CallbackQuery, state: FSMContext):
    symbol = call.data.split(":", 1)[1]
    uid    = call.from_user.id
    l      = await lang(uid)
    await call.message.edit_text(T(l, "loading_sig", symbol=symbol))
    await _do_signal(call.message, uid, l, symbol, state, edit=True)

async def _do_signal(message, uid, l, symbol, state, edit=False):
    await state.clear()
    plan, _ = await db.get_subscription(uid)
    if not plan:
        txt = T(l, "no_sub")
        if edit: await message.edit_text(txt, reply_markup=kb_back(l))
        else:    await message.answer(txt, reply_markup=kb_back(l))
        return
    if not await db.check_signal_quota(uid, PLANS[plan]["signal_limit"]):
        txt = T(l, "sig_limit")
        if edit: await message.edit_text(txt, reply_markup=kb_back(l))
        else:    await message.answer(txt, reply_markup=kb_back(l))
        return
    load_msg = message if edit else await message.answer(T(l, "loading_sig", symbol=symbol.upper()))
    try:
        sig = await generate_signal(symbol)
        await db.increment_signal_count(uid)
        if PLANS[plan]["auto_trade"]:
            user = await db.get_user(uid)
            if user and user["bingx_connected"]:
                await _maybe_trade(uid, l, sig, plan, load_msg, edit)
                return
        await load_msg.edit_text(signal_text(l, sig), reply_markup=kb_back(l))
    except Exception as e:
        logger.error(f"Signal error [{symbol}]: {e}")
        # ✅ ИСПРАВЛЕНО: показываем реальную причину ошибки пользователю
        err_msg = str(e) if str(e) else f"Пара {symbol.upper()} недоступна на BingX Futures"
        await load_msg.edit_text(
            f"❌ <b>Ошибка получения сигнала</b>\n{DIV}\n"
            f"Пара: <b>{symbol.upper()}</b>\n\n"
            f"💬 {err_msg}\n\n"
            f"<i>Попробуйте другую пару или введите вручную.\nПример: BTC-USDT, ETH-USDT, SOL-USDT</i>",
            reply_markup=kb_pairs(l),
        )
async def _maybe_trade(uid, l, sig, plan, load_msg, edit):
    api_key, secret = await db.get_api_keys(uid)
    if not api_key:
        await load_msg.edit_text(signal_text(l, sig), reply_markup=kb_back(l))
        return
    try:
        balance = await bx_balance(api_key, secret)
    except Exception as e:
        logger.warning(f"Balance error uid={uid}: {e}")
        await load_msg.edit_text(signal_text(l, sig), reply_markup=kb_back(l))
        return
    fixed = await db.get_trade_amount(uid)
    amount = fixed if (fixed and fixed > 0) else round(balance * TRADE_PCT, 2)

    if plan == "pro":
        if amount < TRADE_MIN_USDT:
            note = (f"\n\n⚠️ Баланс слишком мал для авто-сделки (мин {TRADE_MIN_USDT} USDT)"
                    if l=="ru" else f"\n\n⚠️ Balance too low for auto-trade (min {TRADE_MIN_USDT} USDT)")
            await load_msg.edit_text(signal_text(l, sig) + note, reply_markup=kb_back(l))
            return
        amount = round(amount, 2)
        sig["confirm_amount"] = amount
        trade_id = await db.create_trade(
            uid, sig["symbol"], sig["side"],
            sig["price"], sig["sl"], sig["tp1"], sig["tp2"], amount,
        )
        await load_msg.edit_text(
            signal_text(l, sig) + (
                f"\n\n▶️ <b>Авто-вход Pro: {amount} USDT</b>"
                if l=="ru" else f"\n\n▶️ <b>Pro auto-entry: {amount} USDT</b>"
            ),
            reply_markup=kb_back(l),
        )
        await _exec_entry_only(uid, l, trade_id, sig, api_key, secret, amount)
        return

    # VIP
    if amount < TRADE_MIN_USDT:
        note = (f"\n\n⚠️ Баланс слишком мал (мин {TRADE_MIN_USDT} USDT)"
                if l=="ru" else f"\n\n⚠️ Balance too low (min {TRADE_MIN_USDT} USDT)")
        await load_msg.edit_text(signal_text(l, sig) + note, reply_markup=kb_back(l))
        return
    amount = round(amount, 2)
    sig["confirm_amount"] = amount

    if fixed and fixed > 0:
        trade_id = await db.create_trade(
            uid, sig["symbol"], sig["side"],
            sig["price"], sig["sl"], sig["tp1"], sig["tp2"], amount,
        )
        await load_msg.edit_text(
            signal_text(l, sig) + (
                f"\n\n🤖 <b>Автопилот VIP: {amount} USDT</b>"
                if l=="ru" else f"\n\n🤖 <b>VIP Autopilot: {amount} USDT</b>"
            ),
            reply_markup=kb_back(l),
        )
        await _exec_full(uid, l, trade_id, sig, api_key, secret, amount)
    else:
        trade_id = await db.create_trade(
            uid, sig["symbol"], sig["side"],
            sig["price"], sig["sl"], sig["tp1"], sig["tp2"], amount,
        )
        await db.update_trade(trade_id, f"PENDING|{sig['sl']}|{sig['tp1']}", "pending")
        confirm_note = (
            f"\n\n{DIV}\n"
            f"👆 <b>VIP: подтвердите сделку</b>\n"
            f"После подтверждения бот автоматически\n"
            f"установит TP={sig['tp1']} и SL={sig['sl']}\n"
            f"💰 Сумма: <b>{amount} USDT</b> (5% баланса)"
            if l=="ru" else
            f"\n\n{DIV}\n"
            f"👆 <b>VIP: confirm trade</b>\n"
            f"After confirmation bot will automatically\n"
            f"set TP={sig['tp1']} and SL={sig['sl']}\n"
            f"💰 Amount: <b>{amount} USDT</b> (5% balance)"
        )
        await load_msg.edit_text(
            signal_text(l, sig) + confirm_note,
            reply_markup=kb_trade_confirm(trade_id, sig, l),
        )

@router.callback_query(F.data.startswith("tr:ok:"))
async def h_tr_confirm(call: CallbackQuery):
    trade_id = int(call.data.split(":")[2])
    uid = call.from_user.id
    l   = await lang(uid)
    trade = await db.get_trade(trade_id)
    if not trade or trade["status"] != "pending":
        await call.answer("Сделка уже обработана.", show_alert=True); return
    raw = trade.get("bingx_order_id", "")
    try:
        _, sl_str, tp_str = raw.split("|")
        sl = float(sl_str); tp = float(tp_str)
    except Exception:
        sl = trade["sl"]; tp = trade["tp1"]
    amount  = trade["amount_usdt"]
    symbol  = trade["symbol"]
    side    = trade["side"]
    api_key, secret = await db.get_api_keys(uid)
    if not api_key:
        await call.answer("API не подключён!", show_alert=True); return
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("⏳ Открываю сделку..." if l=="ru" else "⏳ Opening trade...", show_alert=False)
    sig_mock = {"symbol": symbol, "side": side, "sl": sl, "tp1": tp, "tp2": tp}
    await _exec_full(uid, l, trade_id, sig_mock, api_key, secret, amount)

@router.callback_query(F.data.startswith("tr:no:"))
async def h_tr_cancel(call: CallbackQuery):
    trade_id = int(call.data.split(":")[2])
    l = await lang(call.from_user.id)
    await db.update_trade(trade_id, "", "cancelled")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(T(l, "tr_cancel"))

async def _exec_full(uid, l, trade_id, sig, api_key, secret, amount):
    try:
        order = await bx_order(api_key, secret, sig["symbol"], sig["side"], amount, sig["sl"], sig["tp1"])
        oid   = order.get("order", {}).get("orderId", "N/A")
        await db.update_trade(trade_id, str(oid), "open")
        await db.increment_trade_count(uid)
        await bot.send_message(
            uid,
            f"✅ <b>СДЕЛКА VIP ОТКРЫТА</b>\n{DIV}\n"
            f"📊 {sig['symbol']} · {sig['side']}\n"
            f"💰 Сумма: <b>{amount} USDT</b>\n"
            f"🛑 SL: {sig['sl']}\n"
            f"✅ TP: {sig['tp1']}\n"
            f"{DIV}\n"
            f"🤖 Бот автоматически закроет позицию\n"
            f"🔑 Order ID: <code>{oid}</code>",
        )
    except Exception as e:
        await db.update_trade(trade_id, "", "error")
        await bot.send_message(uid, T(l, "tr_err", err=str(e)[:150]))

async def _exec_entry_only(uid, l, trade_id, sig, api_key, secret, amount):
    try:
        order = await bx_order_entry_only(api_key, secret, sig["symbol"], sig["side"], amount)
        oid   = order.get("order", {}).get("orderId", "N/A")
        await db.update_trade(trade_id, str(oid), "open")
        await bot.send_message(
            uid,
            f"▶️ <b>ВХОД PRO ОТКРЫТ</b>\n{DIV}\n"
            f"📊 {sig['symbol']} · {sig['side']}\n"
            f"💰 Сумма: <b>{amount} USDT</b>\n"
            f"{DIV}\n"
            f"⚠️ <b>Закройте позицию ВРУЧНУЮ!</b>\n"
            f"🛑 Рек. SL: {sig['sl']}\n"
            f"✅ TP1: {sig['tp1']}  🏆 TP2: {sig['tp2']}\n"
            f"🔑 Order ID: <code>{oid}</code>",
        )
    except Exception as e:
        await db.update_trade(trade_id, "", "error")
        await bot.send_message(uid, T(l, "tr_err", err=str(e)[:150]))

@router.message(Command("api"))
async def h_api_cmd(message: Message):
    uid  = message.from_user.id
    l    = await lang(uid)
    user = await db.get_user(uid)
    plan, _ = await db.get_subscription(uid)
    if not plan or not PLANS.get(plan, {}).get("auto_trade"):
        await message.answer(T(l, "api_no_plan"), reply_markup=kb_back(l)); return
    connected = user and user["bingx_connected"]
    status = ("🟢 Подключён" if l=="ru" else "🟢 Connected") if connected else ("🔴 Не подключён" if l=="ru" else "🔴 Not connected")
    await message.answer(f"🤖 <b>BINGX API</b>\n{DIV}\n{status}", reply_markup=kb_api(l, connected))

@router.callback_query(F.data == "m:auto")
async def h_auto_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid  = call.from_user.id
    l    = await lang(uid)
    plan, _ = await db.get_subscription(uid)
    if not plan or not PLANS.get(plan, {}).get("auto_trade"):
        await call.message.edit_text(T(l, "api_no_plan"), reply_markup=kb_back(l)); return
    user = await db.get_user(uid)
    connected = user and user["bingx_connected"]
    ta = await db.get_trade_amount(uid)
    if plan == "vip":
        mode_ru = f"{'Полный автопилот' if ta else 'Подтверждение + авто-выход'} · {ta or '5% баланса'} USDT"
        mode_en = f"{'Full autopilot' if ta else 'Confirm + auto-exit'} · {ta or '5% balance'} USDT"
    else:
        mode_ru = "Авто-вход · мин $5 · выход вручную"
        mode_en = "Auto-entry · min $5 · manual exit"
    status = ("🟢 Подключён" if l=="ru" else "🟢 Connected") if connected else ("🔴 Не подключён" if l=="ru" else "🔴 Not connected")
    text = (
        f"🤖 <b>АВТО-ТОРГОВЛЯ</b>\n{DIV}\nAPI: {status}\n"
        f"{'Режим' if l=='ru' else 'Mode'}: {mode_ru if l=='ru' else mode_en}\n{DIV}\n"
    ) if connected else f"🤖 <b>АВТО-ТОРГОВЛЯ</b>\n{DIV}\nAPI: {status}\n{DIV}\n"
    await call.message.edit_text(text, reply_markup=kb_api(l, connected))

@router.callback_query(F.data == "api:connect")
async def h_api_connect(call: CallbackQuery, state: FSMContext):
    l = await lang(call.from_user.id)
    await state.set_state(ApiFSM.waiting_key)
    await call.message.edit_text(T(l, "api_intro"), reply_markup=kb_back(l))

@router.message(ApiFSM.waiting_key)
async def h_api_key(message: Message, state: FSMContext):
    await state.update_data(api_key=message.text.strip())
    try: await message.delete()
    except: pass
    l = await lang(message.from_user.id)
    await state.set_state(ApiFSM.waiting_secret)
    await message.answer(T(l, "enter_secret"), reply_markup=kb_back(l))

@router.message(ApiFSM.waiting_secret)
async def h_api_secret(message: Message, state: FSMContext):
    uid    = message.from_user.id
    l      = await lang(uid)
    data   = await state.get_data()
    api_key = data.get("api_key", "")
    secret  = message.text.strip()
    try: await message.delete()
    except: pass
    try:
        bal = await bx_balance(api_key, secret)
        await db.save_api_keys(uid, api_key, secret)
        await state.clear()
        await message.answer(T(l, "api_ok", bal=bal), reply_markup=kb_back(l))
    except Exception as e:
        await state.clear()
        await message.answer(T(l, "api_err", err=str(e)[:120]), reply_markup=kb_back(l))

@router.callback_query(F.data == "api:disc")
async def h_api_disc(call: CallbackQuery):
    l = await lang(call.from_user.id)
    await db.remove_api_keys(call.from_user.id)
    await call.message.edit_text(T(l, "api_disc_ok"), reply_markup=kb_back(l))

@router.callback_query(F.data == "api:balance")
async def h_api_balance(call: CallbackQuery):
    uid = call.from_user.id
    l   = await lang(uid)
    api_key, secret = await db.get_api_keys(uid)
    if not api_key:
        await call.answer("API не подключён" if l=="ru" else "API not connected", show_alert=True); return
    try:
        bal  = await bx_balance(api_key, secret)
        user = await db.get_user(uid)
        await call.message.edit_text(
            f"💰 <b>Баланс BingX: {bal:.2f} USDT</b>",
            reply_markup=kb_api(l, user and user["bingx_connected"]),
        )
    except Exception as e:
        await call.answer(f"Ошибка: {e}", show_alert=True)

@router.message(Command("setamount"))
async def h_setamount(message: Message):
    uid = message.from_user.id
    l   = await lang(uid)
    plan, _ = await db.get_subscription(uid)
    if plan not in ("vip", "pro"):
        await message.answer("🔒 /setamount — только для тарифов <b>Pro</b> и <b>VIP</b>." if l=="ru" else "🔒 /setamount — Pro and VIP only."); return
    parts = message.text.split()
    if len(parts) < 2:
        cur = await db.get_trade_amount(uid)
        if plan == "vip":
            cur_desc = (("авто 5% баланса → с подтверждением" if cur==0 else f"фиксировано {cur} USDT → полный автопилот") if l=="ru" else ("auto 5% balance → with confirmation" if cur==0 else f"fixed {cur} USDT → full autopilot"))
        else:
            cur_desc = (f"авто 5% баланса" if cur==0 else f"{cur} USDT") if l=="ru" else (f"auto 5% balance" if cur==0 else f"{cur} USDT")
        vip_tip = (f"\n\n💡 <b>VIP-режимы:</b>\n• <code>/setamount 0</code> → бот спрашивает подтверждение, потом сам закрывает\n• <code>/setamount 200</code> → полный автопилот" if l=="ru" else f"\n\n💡 <b>VIP modes:</b>\n• <code>/setamount 0</code> → bot asks confirmation, then auto-closes\n• <code>/setamount 200</code> → full autopilot") if plan=="vip" else ""
        await message.answer(
            f"💵 <b>{'Сумма авто-сделки' if l=='ru' else 'Auto-trade amount'}</b>\n{DIV}\n"
            f"{'Текущая' if l=='ru' else 'Current'}: <b>{cur_desc}</b>\n"
            f"{'Мин' if l=='ru' else 'Min'}: <b>{TRADE_MIN_USDT} USDT</b>\n\n"
            f"/setamount 200 — 200 USDT {'за сделку' if l=='ru' else 'per trade'}\n"
            f"/setamount 0 — {'авто (5% баланса)' if l=='ru' else 'auto (5% balance)'}"
            f"{vip_tip}"
        ); return
    try:
        amount = float(parts[1])
        if amount < 0: raise ValueError()
        if 0 < amount < TRADE_MIN_USDT:
            await message.answer(f"❌ Минимум {TRADE_MIN_USDT} USDT (или 0 для авто)"); return
    except:
        await message.answer("❌ Введите число ≥ 0" if l=="ru" else "❌ Enter a number ≥ 0"); return
    await db.set_trade_amount(uid, amount)
    if plan == "vip":
        mode = (("авто 5% → бот будет спрашивать подтверждение" if amount==0 else f"{amount} USDT → ПОЛНЫЙ АВТОПИЛОТ") if l=="ru" else ("auto 5% → bot will ask confirmation" if amount==0 else f"{amount} USDT → FULL AUTOPILOT"))
    else:
        mode = (f"авто 5% баланса" if amount==0 else f"{amount} USDT") if l=="ru" else (f"auto 5% balance" if amount==0 else f"{amount} USDT")
    await message.answer(f"✅ <b>{'Сумма сделки' if l=='ru' else 'Trade amount'}:</b> {mode}")

@router.message(Command("setlimit"))
async def h_setlimit(message: Message):
    uid = message.from_user.id
    l   = await lang(uid)
    plan, _ = await db.get_subscription(uid)
    if plan != "vip":
        await message.answer("🔒 /setlimit — только для тарифа <b>VIP</b>." if l=="ru" else "🔒 /setlimit — VIP only."); return
    parts = message.text.split()
    if len(parts) < 2:
        cur = await db.get_trade_limit(uid)
        cur_t = ("без лимита" if cur==0 else f"{cur}/день") if l=="ru" else ("unlimited" if cur==0 else f"{cur}/day")
        await message.answer(
            f"📡 <b>{'Лимит авто-сделок в день' if l=='ru' else 'Daily auto-trade limit'}</b>\n{DIV}\n"
            f"{'Текущий' if l=='ru' else 'Current'}: <b>{cur_t}</b>\n\n"
            f"/setlimit 5 — {'макс 5 сделок/день' if l=='ru' else 'max 5 trades/day'}\n"
            f"/setlimit 0 — {'без лимита' if l=='ru' else 'unlimited'}"
        ); return
    try:
        limit = int(parts[1])
        if limit < 0: raise ValueError()
    except:
        await message.answer("❌ Введите целое число ≥ 0"); return
    await db.set_trade_limit(uid, limit)
    txt = ("без лимита" if limit==0 else f"{limit}/день") if l=="ru" else ("unlimited" if limit==0 else f"{limit}/day")
    await message.answer(f"✅ <b>Лимит сделок:</b> {txt}")

@router.message(Command("promo"))
async def h_promo(message: Message):
    uid   = message.from_user.id
    l     = await lang(uid)
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(f"🎟 <b>Промокод</b>\n{DIV}\nИспользование: /promo КОД\nПример: /promo APEX2024" if l=="ru" else f"🎟 <b>Promo Code</b>\n{DIV}\nUsage: /promo CODE\nExample: /promo APEX2024"); return
    promo, err = await db.use_promo(parts[1].strip(), uid)
    if err:
        await message.answer(f"❌ {err}"); return
    await db.activate_subscription(uid, promo["plan"], promo["days"])
    pname = PLANS[promo["plan"]]["emoji"] + " " + PLANS[promo["plan"]]["name"]
    await message.answer(
        f"🎉 <b>Промокод активирован!</b>\n{DIV}\n💎 Тариф: <b>{pname}</b>\n📅 Срок: <b>{promo['days']} дней</b>\n\nИспользуйте /profile для просмотра."
        if l=="ru" else
        f"🎉 <b>Promo code activated!</b>\n{DIV}\n💎 Plan: <b>{pname}</b>\n📅 Duration: <b>{promo['days']} days</b>\n\nUse /profile to view."
    )

@router.message(Command("help"))
async def h_help_cmd(message: Message):
    l = await lang(message.from_user.id)
    await message.answer(T(l, "help"), reply_markup=kb_back(l))

@router.callback_query(F.data == "m:help")
async def h_help_cb(call: CallbackQuery):
    l = await lang(call.from_user.id)
    await call.message.edit_text(T(l, "help"), reply_markup=kb_back(l))

@router.message(Command("admin"))
async def h_admin(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔ Нет доступа."); return
    stats = await db.get_stats(); pc = stats["plans"]
    await message.answer(
        f"🛡 <b>ADMIN PANEL</b>\n{DIV}\n👥 Пользователей: <b>{stats['total']}</b>\n✅ Активных: <b>{stats['active']}</b>\n"
        f"🥉 Basic: {pc.get('basic',0)}  🥈 Pro: {pc.get('pro',0)}  👑 VIP: {pc.get('vip',0)}\n💰 Выручка: <b>{stats['revenue']:.2f} USDT</b>\n{DIV}",
        reply_markup=kb_admin(),
    )

@router.callback_query(F.data == "admin:back")
async def h_admin_back(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    stats = await db.get_stats(); pc = stats["plans"]
    await call.message.edit_text(
        f"🛡 <b>ADMIN PANEL</b>\n{DIV}\n👥 {stats['total']} · ✅ {stats['active']}\n🥉 {pc.get('basic',0)} · 🥈 {pc.get('pro',0)} · 👑 {pc.get('vip',0)}\n💰 <b>{stats['revenue']:.2f} USDT</b>\n{DIV}",
        reply_markup=kb_admin(),
    )

@router.callback_query(F.data == "admin:report")
async def h_admin_report(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    stats = await db.get_stats(); adv = await db.get_advanced_stats(); pc = stats["plans"]
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(
        f"📋 <b>ОТЧЁТ</b> · {now}\n{DIV}\n👥 {stats['total']} юзеров · {stats['active']} активных\n"
        f"🥉 {pc.get('basic',0)} · 🥈 {pc.get('pro',0)} · 👑 {pc.get('vip',0)}\n💰 Выручка: <b>{stats['revenue']:.2f} USDT</b>\n"
        f"Платежи: {adv['payments_total']} · ✅{adv['payments_ok']} · ❌{adv['payments_fail']}\n"
        f"Сделки: {adv['trades_total']} · ✅{adv['trades_open']} · ❌{adv['trades_err']}\n"
        f"🤖 API: {adv['api_connected']} · 📊 Сигналов: {adv['signals_today']}",
        reply_markup=b.as_markup(),
    )

@router.callback_query(F.data == "admin:users")
async def h_admin_users(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    users = await db.get_recent_users(10)
    lines = [f"{'@'+u['username'] if u.get('username') else 'ID:'+str(u['telegram_id'])} — {u.get('subscription') or '—'}" for u in users]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"👥 <b>Последние 10 пользователей</b>\n{DIV}\n" + ("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:payments")
async def h_admin_payments(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    payments = await db.get_recent_payments(10)
    lines = [f"{'✅' if p['status']=='verified' else '❌' if p['status']=='failed' else '⏳'} {p['plan'].upper()} {p['amount']} USDT <code>{p['tx_hash'][:10]}…</code>" for p in payments]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"💰 <b>Платежи (последние 10)</b>\n{DIV}\n" + ("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:trades")
async def h_admin_trades(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    trades = await db.get_recent_trades(10)
    lines = [f"{'✅' if t['status']=='open' else '❌' if t['status']=='error' else '⏳'} {t['symbol']} {t['side']} {t['amount_usdt']} USDT" for t in trades]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(f"📡 <b>Сделки (последние 10)</b>\n{DIV}\n" + ("\n".join(lines) or "Нет"), reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:promos")
async def h_admin_promos(call: CallbackQuery):
    if not is_admin(call.from_user.id, call.from_user.username or ""):
        await call.answer("⛔", show_alert=True); return
    promos = await db.list_promos()
    lines = [f"🎟 <code>{p['code']}</code> — {p['plan'].upper()} {p['days']}д [{p['used']}/{p['max_uses']}]" for p in promos]
    b = InlineKeyboardBuilder(); b.button(text="⬅️ Назад", callback_data="admin:back")
    await call.message.edit_text(
        f"🎟 <b>Промокоды</b>\n{DIV}\n" + ("\n".join(lines) or "Промокодов нет") +
        f"\n\n<i>Создать: /addpromo КОД план дней [раз]</i>\n<i>Удалить: /delpromo КОД</i>",
        reply_markup=b.as_markup(),
    )

@router.message(Command("user"))
async def h_admin_user(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 2: await message.answer("Использование: /user @username"); return
    user = await db.get_user_by_username(parts[1])
    if not user: await message.answer("❌ Пользователь не найден"); return
    uid = user["telegram_id"]
    plan, exp = await db.get_subscription(uid)
    api_key, secret = await db.get_api_keys(uid)
    bal_text = "—"
    if api_key:
        try:    bal = await bx_balance(api_key, secret); bal_text = f"{bal:.2f} USDT"
        except: bal_text = "Ошибка API"
    tl = user.get("daily_trade_limit", 0); ta = user.get("trade_amount_usdt", 0)
    await message.answer(
        f"👤 <b>КЛИЕНТ</b>\n{DIV}\n🆔 <code>{uid}</code>\n📛 {user.get('first_name') or '—'}\n"
        f"👤 @{user.get('username') or '—'}\n💎 Тариф: <b>{plan.upper() if plan else '❌ Нет'}</b>\n"
        f"📅 До: {fmt_date(exp) if plan else '—'}\n🤖 API: {'🟢' if api_key else '🔴'}\n"
        f"💰 Баланс: <b>{bal_text}</b>\n📊 Сигналов: {user.get('signals_today',0)}\n"
        f"📡 Лимит: {'∞' if tl==0 else tl}\n💵 Сумма: {'авто' if ta==0 else f'{ta} USDT'}\n{DIV}"
    )

@router.message(Command("stats"))
async def h_stats(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    s = await db.get_stats(); pc = s["plans"]
    await message.answer(f"📊 {s['total']} юзеров · {s['active']} активных\n🥉 {pc.get('basic',0)} · 🥈 {pc.get('pro',0)} · 👑 {pc.get('vip',0)}\n💰 {s['revenue']:.2f} USDT")

@router.message(Command("setvip"))
async def h_setvip(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(f"👑 /setvip @username — VIP 30 дней\n/setvip @username 60 — VIP 60 дней"); return
    uname = parts[1].lstrip("@"); days = int(parts[2]) if len(parts) > 2 else 30
    user  = await db.get_user_by_username(uname)
    if not user: await message.answer(f"❌ @{uname} не найден."); return
    await db.activate_subscription(user["telegram_id"], "vip", days)
    await message.answer(f"✅ <b>VIP выдан</b>\n👤 @{uname} · {days} дней")
    try:
        await bot.send_message(user["telegram_id"],
            f"🎁 <b>Вам выдан VIP на {days} дней!</b>\n{DIV}\n👑 Все функции разблокированы!\nИспользуйте /profile и /setamount")
    except: pass

@router.message(Command("addpromo"))
async def h_addpromo(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 4:
        await message.answer(f"🎟 /addpromo КОД план дней [раз]\nПример: /addpromo VIP7 vip 7 10\nПланы: basic · pro · vip"); return
    code = parts[1].upper(); plan = parts[2].lower(); days = int(parts[3])
    uses = int(parts[4]) if len(parts) > 4 else 1
    if plan not in ("basic","pro","vip"):
        await message.answer("❌ Планы: basic / pro / vip"); return
    await db.create_promo(code, plan, days, uses)
    await message.answer(f"✅ <b>Промокод создан</b>\n🎟 <code>{code}</code> · {plan.upper()} · {days} дней · {uses} исп.")

@router.message(Command("delpromo"))
async def h_delpromo(message: Message):
    if not is_admin(message.from_user.id, message.from_user.username or ""):
        await message.answer("⛔"); return
    parts = message.text.split()
    if len(parts) < 2: await message.answer("🗑 /delpromo КОД"); return
    code = parts[1].upper()
    ok   = await db.delete_promo(code)
    await message.answer(f"✅ <code>{code}</code> удалён." if ok else f"❌ <code>{code}</code> не найден.")

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
    await message.answer(f"✅ @{uname} +{days} дней · {reason}")
    try: await bot.send_message(user["telegram_id"], f"🎁 Компенсация +{days} дней. {reason}")
    except: pass

@router.message(Command("broadcast"))
async def h_broadcast(message: Message):
    uid = message.from_user.id
    # ✅ Доступ: главный админ ИЛИ модератор
    if not is_admin(uid, message.from_user.username or "") and not is_moderator(uid):
        await message.answer("⛔ Нет доступа."); return
        
    text = message.text.split(maxsplit=1)
    if len(text) < 2:
        await message.answer("/broadcast текст — рассылка всем"); return
        
    import aiosqlite as _sq
    async with _sq.connect(db.DATABASE_PATH) as dbc:
        dbc.row_factory = _sq.Row
        async with dbc.execute("SELECT telegram_id FROM users") as cur:
            users = await cur.fetchall()
            
    ok = fail = 0
    for u in users:
        try: 
            await bot.send_message(u["telegram_id"], text[1])
            ok += 1
        except: 
            fail += 1
        await asyncio.sleep(0.05)
    await message.answer(f"📢 <b>Рассылка завершена</b>\n✅ {ok} · ❌ {fail}")
    
async def on_startup():
    await db.init_db()
    await bot.set_my_commands([
        BotCommand(command="start",     description="🏠 Главное меню"),
        BotCommand(command="signal",    description="📊 Получить сигнал"),
        BotCommand(command="subscribe", description="💎 Подписка"),
        BotCommand(command="profile",   description="👤 Мой профиль"),
        BotCommand(command="api",       description="🤖 BingX API (Pro/VIP)"),
        BotCommand(command="promo",     description="🎟 Промокод"),
        BotCommand(command="setamount", description="💵 Сумма авто-сделки (Pro/VIP)"),
        BotCommand(command="setlimit",  description="📡 Лимит сделок в день (VIP)"),
        BotCommand(command="myid",      description="🆔 Мой Telegram ID"),
        BotCommand(command="help",      description="ℹ️ Справка"),
    ])
    asyncio.create_task(daily_reports_task())
    logger.info("✅ Apex Trading Bot запущен!")
    try: await bot.send_message(ADMIN_ID, "🟢 <b>Apex Trading Bot запущен!</b>")
    except: pass

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    print("🚀 Apex Trading Bot запускается...")
    asyncio.run(main())        