import aiosqlite
import time
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import os

DATABASE_PATH  = os.environ.get("DATABASE_PATH", "apex_trading.db")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "Yhq8azrWaoaC7tPY1hLuUbwBTFa0U8QCRE8oWwoVmR0=").encode()
fernet         = Fernet(ENCRYPTION_KEY)

PLANS = {
    "basic": {
        "name": "Basic", "emoji": "🥉", "price": 29,  "days": 30,
        "signal_limit": 5,    "auto_trade": False, "entry_only": False, "daily_report": False,
    },
    "pro": {
        "name": "Pro",   "emoji": "🥈", "price": 69,  "days": 30,
        "signal_limit": 9999, "auto_trade": True,  "entry_only": True,  "daily_report": False,
    },
    "vip": {
        "name": "VIP",   "emoji": "👑", "price": 129, "days": 30,
        "signal_limit": 9999, "auto_trade": True,  "entry_only": False, "daily_report": True,
    },
}

TRADE_MIN_USDT = 5
TRADE_MAX_USDT = 500
TRADE_PCT      = 0.05

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                language TEXT DEFAULT 'ru',
                subscription TEXT DEFAULT NULL,
                sub_expires INTEGER DEFAULT 0,
                api_key_enc BLOB DEFAULT NULL,
                api_secret_enc BLOB DEFAULT NULL,
                bingx_connected INTEGER DEFAULT 0,
                signals_today INTEGER DEFAULT 0,
                signals_date TEXT DEFAULT '',
                trades_today INTEGER DEFAULT 0,
                trades_date TEXT DEFAULT '',
                daily_trade_limit INTEGER DEFAULT 0,
                trade_amount_usdt REAL DEFAULT 0,
                balance REAL DEFAULT 0,
                created_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount REAL NOT NULL,
                tx_hash TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at INTEGER DEFAULT 0,
                verified_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL,
                sl REAL, tp1 REAL, tp2 REAL,
                amount_usdt REAL,
                bingx_order_id TEXT DEFAULT NULL,
                status TEXT DEFAULT 'pending',
                created_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                days INTEGER NOT NULL,
                max_uses INTEGER DEFAULT 1,
                used INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS promo_uses (
                code TEXT,
                telegram_id INTEGER,
                PRIMARY KEY (code, telegram_id)
            );
        """)
        migrations = [
            "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'",
            "ALTER TABLE users ADD COLUMN signals_today INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN signals_date TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN trades_today INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN trades_date TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN daily_trade_limit INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN trade_amount_usdt REAL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN bingx_connected INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN api_key_enc BLOB DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN api_secret_enc BLOB DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()

async def get_user(telegram_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_user_by_username(username: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE username=?", (username.lstrip("@"),)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def upsert_user(telegram_id: int, username, first_name):
    now = int(time.time())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO users (telegram_id, username, first_name, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET
               username=excluded.username, first_name=excluded.first_name""",
            (telegram_id, username, first_name, now))
        await db.commit()

async def set_language(telegram_id: int, lang: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET language=? WHERE telegram_id=?", (lang, telegram_id))
        await db.commit()

async def get_language(telegram_id: int) -> str:
    user = await get_user(telegram_id)
    return user["language"] if user and user.get("language") else "ru"

async def activate_subscription(telegram_id: int, plan: str, days: int = 30):
    now = int(time.time())
    user = await get_user(telegram_id)
    base = user["sub_expires"] if user and user["sub_expires"] and user["sub_expires"] > now else now
    new_exp = base + days * 86400
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET subscription=?, sub_expires=? WHERE telegram_id=?",
            (plan, new_exp, telegram_id))
        await db.commit()

async def get_subscription(telegram_id: int):
    user = await get_user(telegram_id)
    if not user: return None, 0
    now = int(time.time())
    if user["sub_expires"] and user["sub_expires"] > now:
        return user["subscription"], user["sub_expires"]
    return None, 0

async def check_signal_quota(telegram_id: int, daily_limit: int) -> bool:
    if daily_limit >= 9999: return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user = await get_user(telegram_id)
    if not user: return False
    if user.get("signals_date") != today: return True
    return user.get("signals_today", 0) < daily_limit

async def increment_signal_count(telegram_id: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user = await get_user(telegram_id)
    if not user: return
    count = 1 if user.get("signals_date") != today else user.get("signals_today", 0) + 1
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET signals_today=?, signals_date=? WHERE telegram_id=?",
            (count, today, telegram_id))
        await db.commit()

async def check_trade_quota(telegram_id: int) -> bool:
    user = await get_user(telegram_id)
    if not user: return False
    limit = user.get("daily_trade_limit", 0)
    if limit == 0: return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if user.get("trades_date") != today: return True
    return user.get("trades_today", 0) < limit

async def increment_trade_count(telegram_id: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user = await get_user(telegram_id)
    if not user: return
    count = 1 if user.get("trades_date") != today else user.get("trades_today", 0) + 1
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET trades_today=?, trades_date=? WHERE telegram_id=?",
            (count, today, telegram_id))
        await db.commit()

async def set_trade_limit(telegram_id: int, limit: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET daily_trade_limit=? WHERE telegram_id=?", (limit, telegram_id))
        await db.commit()

async def get_trade_limit(telegram_id: int) -> int:
    user = await get_user(telegram_id)
    return user.get("daily_trade_limit", 0) if user else 0

async def set_trade_amount(telegram_id: int, amount: float):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET trade_amount_usdt=? WHERE telegram_id=?", (amount, telegram_id))
        await db.commit()

async def get_trade_amount(telegram_id: int) -> float:
    user = await get_user(telegram_id)
    return user.get("trade_amount_usdt", 0) if user else 0

async def save_api_keys(telegram_id: int, api_key: str, secret: str):
    enc_key = fernet.encrypt(api_key.encode())
    enc_sec = fernet.encrypt(secret.encode())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET api_key_enc=?, api_secret_enc=?, bingx_connected=1 WHERE telegram_id=?",
            (enc_key, enc_sec, telegram_id))
        await db.commit()

async def get_api_keys(telegram_id: int):
    user = await get_user(telegram_id)
    if not user or not user.get("api_key_enc"): return None, None
    try:
        k = user["api_key_enc"]
        s = user["api_secret_enc"]
        if isinstance(k, str): k = k.encode()
        if isinstance(s, str): s = s.encode()
        return fernet.decrypt(k).decode(), fernet.decrypt(s).decode()
    except Exception:
        return None, None

async def remove_api_keys(telegram_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE users SET api_key_enc=NULL, api_secret_enc=NULL, bingx_connected=0 WHERE telegram_id=?",
            (telegram_id,))
        await db.commit()

async def create_payment(telegram_id: int, plan: str, amount: float, tx_hash: str):
    now = int(time.time())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO payments (telegram_id, plan, amount, tx_hash, status, created_at) VALUES (?,?,?,?,?,?)",
            (telegram_id, plan, amount, tx_hash, "pending", now))
        await db.commit()

async def get_payment_by_hash(tx_hash: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments WHERE tx_hash=?", (tx_hash,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def update_payment_status(tx_hash: str, status: str):
    now = int(time.time())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE payments SET status=?, verified_at=? WHERE tx_hash=?", (status, now, tx_hash))
        await db.commit()

async def create_trade(telegram_id: int, symbol: str, side: str,
                       entry: float, sl: float, tp1: float, tp2: float, amount: float) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            """INSERT INTO trades (telegram_id, symbol, side, entry_price, sl, tp1, tp2,
               amount_usdt, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (telegram_id, symbol, side, entry, sl, tp1, tp2, amount, "pending", now))
        await db.commit()
        return cur.lastrowid

async def get_trade(trade_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trades WHERE id=?", (trade_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def update_trade(trade_id: int, order_id: str, status: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE trades SET bingx_order_id=?, status=? WHERE id=?", (order_id, status, trade_id))
        await db.commit()

async def create_promo(code: str, plan: str, days: int, max_uses: int = 1):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO promos (code, plan, days, max_uses, used, created_at) VALUES (?,?,?,?,0,?)",
            (code.upper(), plan, days, max_uses, int(time.time())))
        await db.commit()

async def delete_promo(code: str) -> bool:
    # ✅ ИСПРАВЛЕНО: удаляем и из promos и из promo_uses
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM promos WHERE code=?", (code.upper(),))
        await db.execute("DELETE FROM promo_uses WHERE code=?", (code.upper(),))
        await db.commit()
        return cur.rowcount > 0

async def use_promo(code: str, telegram_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Проверяем существование промокода
        async with db.execute("SELECT * FROM promos WHERE code=?", (code.upper(),)) as cur:
            promo = await cur.fetchone()
        if not promo:
            return None, "Промокод не найден"
        if promo["used"] >= promo["max_uses"]:
            return None, "Промокод уже исчерпан"
        # Проверяем, не использовал ли уже этот юзер
        async with db.execute(
            "SELECT 1 FROM promo_uses WHERE code=? AND telegram_id=?",
            (code.upper(), telegram_id)) as cur:
            if await cur.fetchone():
                return None, "Вы уже использовали этот промокод"
        # Фиксируем использование
        await db.execute("INSERT INTO promo_uses VALUES (?,?)", (code.upper(), telegram_id))
        await db.execute("UPDATE promos SET used=used+1 WHERE code=?", (code.upper(),))
        await db.commit()
        return dict(promo), None

async def list_promos():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promos ORDER BY created_at DESC") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_stats() -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = int(time.time())
        async with db.execute("SELECT COUNT(*) as c FROM users") as cur:
            total = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM users WHERE sub_expires>?", (now,)) as cur:
            active = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT subscription, COUNT(*) as c FROM users WHERE sub_expires>? GROUP BY subscription", (now,)) as cur:
            plans = {r["subscription"]: r["c"] for r in await cur.fetchall() if r["subscription"]}
        async with db.execute("SELECT SUM(amount) as s FROM payments WHERE status='verified'") as cur:
            rev = (await cur.fetchone())["s"] or 0
    return {"total": total, "active": active, "plans": plans, "revenue": rev}

async def get_advanced_stats() -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with db.execute("SELECT COUNT(*) as c FROM payments") as cur:
            pt = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM payments WHERE status='verified'") as cur:
            po = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM payments WHERE status='failed'") as cur:
            pf = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM trades") as cur:
            tt = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM trades WHERE status='open'") as cur:
            to_ = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM trades WHERE status='error'") as cur:
            te = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM users WHERE bingx_connected=1") as cur:
            api = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT SUM(signals_today) as s FROM users WHERE signals_date=?", (today,)) as cur:
            sig = (await cur.fetchone())["s"] or 0
    return {"payments_total": pt, "payments_ok": po, "payments_fail": pf,
            "trades_total": tt, "trades_open": to_, "trades_err": te,
            "api_connected": api, "signals_today": sig}

async def get_recent_users(limit: int = 10) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_recent_payments(limit: int = 10) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_recent_trades(limit: int = 10) -> list:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def get_vip_with_api() -> list:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = int(time.time())
        async with db.execute(
            "SELECT * FROM users WHERE subscription='vip' AND sub_expires>? AND bingx_connected=1",
            (now,)) as cur:
            return [dict(r) for r in await cur.fetchall()]
async def add_recovery_alert(user_id: int, symbol: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO recovery_alerts (user_id, symbol) VALUES (?, ?)", (user_id, symbol.upper()))
        await db.commit()

async def get_users_for_recovery(symbol: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT user_id FROM recovery_alerts WHERE symbol=?", (symbol.upper(),)) as cursor:
            rows = await cursor.fetchall()
        await db.execute("DELETE FROM recovery_alerts WHERE symbol=?", (symbol.upper(),))
        await db.commit()
        return [r[0] for r in rows]