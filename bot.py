#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👑 VIP BIG SMM PANEL & DIGITAL STORE BOT — Pro Edition
======================================================
Features:
  • Colored ReplyKeyboard Buttons (Primary Blue, Success Green, Danger Red — Telegram Bot API 9.4+ compatible)
  • Complete SMM Panel Flow: Category -> Service -> Target Link -> Quantity -> Auto Cost Calculation (per 1K) -> Instant Wallet Deduction
  • Pre-loaded VIP SMM Services (Facebook, TikTok, YouTube, Instagram, Telegram) + Instant Digital File Products
  • Multi-Method Wallet Deposit (bKash, Nagad, Rocket, Binance) with TrxID & 1-Click Admin Approve/Reject
  • Live Order Tracking (Pending, Processing, Completed, Cancelled + Auto Wallet Refund)
  • User Profile, VIP Rank System, Refer & Earn, Top Leaderboard, Daily Bonus & Missions, Support Ticket
  • 100% Hidden Admin Panel (ONLY visible to ADMIN_ID):
      - Add SMM Service or Digital File Product
      - Delete Service, View Pending Orders & Pending Deposits
      - Add / Deduct User Balance
      - Set bKash, Nagad, Rocket, Binance, Support Username, Refer Bonus, Daily Bonus
      - Broadcast Message to All Users & Live Bot Statistics
  • Built-in Flask Keep-Alive Server (/ and /ping) + Mini App API (/api/balance, /api/services, /download/<id>) for Render
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from flask import Flask, Response, jsonify, request
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vip_smm_bot.db")
_db_lock = threading.Lock()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _admin_id() -> int:
    raw = str(os.environ.get("ADMIN_ID", "0")).strip().replace("'", "").replace('"', "")
    try:
        return int(raw)
    except ValueError:
        return 0


def is_admin(user_id: int) -> bool:
    aid = _admin_id()
    return aid != 0 and int(user_id) == aid


# ---------------------------------------------------------------------------
# Colored Keyboard Button Helper (Telegram Bot API 9.4+ style support)
# ---------------------------------------------------------------------------
_VALID_STYLES = ("primary", "success", "danger")


def _mkb(text: str, style: str = "primary", web_app: WebAppInfo | None = None) -> KeyboardButton:
    """Creates a colored Reply KeyboardButton compatible with all python-telegram-bot versions."""
    if style not in _VALID_STYLES:
        style = "primary"
    try:
        if web_app is not None:
            return KeyboardButton(text, web_app=web_app, style=style)
        return KeyboardButton(text, style=style)
    except TypeError:
        try:
            if web_app is not None:
                return KeyboardButton(text, web_app=web_app, api_kwargs={"style": style})
            return KeyboardButton(text, api_kwargs={"style": style})
        except Exception:
            if web_app is not None:
                return KeyboardButton(text, web_app=web_app)
            return KeyboardButton(text)


# ---------------------------------------------------------------------------
# Button Labels (User & Admin)
# ---------------------------------------------------------------------------
BTN_NEW_ORDER = "🚀 নতুন অর্ডার (SMM)"
BTN_MINIAPP = "📱 Mini Bot Open"
BTN_SERVICES = "📋 সার্ভিস লিস্ট"
BTN_MY_ORDERS = "📦 আমার অর্ডার"
BTN_DEPOSIT = "💰 ডিপোজিট"
BTN_PROFILE = "👤 প্রোফাইল"
BTN_REFER = "👥 রেফারেল"
BTN_LEADERBOARD = "🏆 লিডারবোর্ড"
BTN_MISSION = "🎯 মিশন ও বোনাস"
BTN_SUPPORT = "📞 সাপোর্ট"
BTN_ADMIN = "📊 অ্যাডমিন প্যানেল"

BTN_BACK = "🔙 মেইন মেনু"
BTN_CANCEL = "❌ বাতিল করুন"

# Admin Panel Buttons
BTN_A_ADD_SMM = "➕ SMM সার্ভিস অ্যাড"
BTN_A_ADD_FILE = "📁 ফাইল প্রোডাক্ট অ্যাড"
BTN_A_LIST = "📋 সব সার্ভিস দেখুন"
BTN_A_DEL = "🗑️ সার্ভিস ডিলিট"
BTN_A_ORDERS = "📦 পেন্ডিং অর্ডার"
BTN_A_DEPOSITS = "💳 পেন্ডিং ডিপোজিট"
BTN_A_BALANCE = "💰 ব্যালেন্স অ্যাড/কাটুন"
BTN_A_PAYMENTS = "⚙️ পেমেন্ট নাম্বার সেট"
BTN_A_SETTINGS = "🎁 বোনাস ও সাপোর্ট সেট"
BTN_A_BROADCAST = "📢 ব্রডকাস্ট মেসেজ"
BTN_A_STATS = "📈 বট স্ট্যাটাস"

# Conversation States stored in context.user_data["state"]
ST_ORDER_LINK = "order_link"
ST_ORDER_QTY = "order_qty"
ST_DEP_AMOUNT = "dep_amount"
ST_DEP_TRXID = "dep_trxid"
ST_SUPPORT_MSG = "support_msg"

# Admin States
ST_ADD_SMM_CAT = "add_smm_cat"
ST_ADD_SMM_NAME = "add_smm_name"
ST_ADD_SMM_RATE = "add_smm_rate"
ST_ADD_SMM_MIN = "add_smm_min"
ST_ADD_SMM_MAX = "add_smm_max"
ST_ADD_SMM_DESC = "add_smm_desc"

ST_ADD_FILE_NAME = "add_file_name"
ST_ADD_FILE_PRICE = "add_file_price"
ST_ADD_FILE_DESC = "add_file_desc"
ST_ADD_FILE_DOC = "add_file_doc"

ST_BAL_UID = "bal_uid"
ST_BAL_AMT = "bal_amt"

ST_SET_BKASH = "set_bkash"
ST_SET_NAGAD = "set_nagad"
ST_SET_ROCKET = "set_rocket"
ST_SET_BINANCE = "set_binance"
ST_SET_SUPPORT = "set_support"
ST_SET_REFER_BONUS = "set_refer_bonus"
ST_SET_DAILY_BONUS = "set_daily_bonus"
ST_BROADCAST_TEXT = "broadcast_text"


# ---------------------------------------------------------------------------
# Database Layer (SQLite with Thread Lock)
# ---------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=25.0)
    conn.row_factory = sqlite3.Row
    return conn


DEFAULT_SMM_SERVICES = [
    # category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id
    (
        "📘 Facebook VIP",
        "FB Page/Profile Followers [Non-Drop VIP]",
        "smm",
        85,
        100,
        100000,
        "⚡ সুপার ফাস্ট ডেলিভারি | ১০০% রিয়েল প্রোফাইল ফলোয়ার | ৩০ দিন গ্যারান্টি।",
        None,
    ),
    (
        "📘 Facebook VIP",
        "FB Post/Photo Likes & Love Reacts [Instant]",
        "smm",
        50,
        100,
        50000,
        "🔥 অটোমেটিক ফাস্ট স্টার্ট | পাবলিক পোস্ট হতে হবে।",
        None,
    ),
    (
        "📘 Facebook VIP",
        "FB Reels & Video Views [Monetizable]",
        "smm",
        35,
        500,
        1000000,
        "🎬 মনিটাইজেশন ফ্রেন্ডলি রিয়েল ভিউ | নন-ড্রপ।",
        None,
    ),
    (
        "🎵 TikTok Viral",
        "TikTok Real Followers [HQ Active]",
        "smm",
        120,
        100,
        100000,
        "🚀 হাই-কোয়ালিটি টিকটক ফলোয়ার | অ্যাকাউন্ট পাবলিক রাখুন।",
        None,
    ),
    (
        "🎵 TikTok Viral",
        "TikTok Video Likes + Hearts [Super Fast]",
        "smm",
        45,
        100,
        200000,
        "❤️ ভিডিও ভাইরাল করার জন্য সেরা লাইক সার্ভিস।",
        None,
    ),
    (
        "🎵 TikTok Viral",
        "TikTok Viral Video Views [Instant Start]",
        "smm",
        12,
        1000,
        5000000,
        "⚡ ১ মিনিটে স্টার্ট | আনলিমিটেড ফাস্ট ভিউ।",
        None,
    ),
    (
        "▶️ YouTube Studio",
        "YouTube Subscribers [Non-Drop Guarantee]",
        "smm",
        420,
        50,
        50000,
        "🏆 চ্যানেল মনিটাইজেশনের জন্য ১০০% সেফ সাবস্ক্রাইবার।",
        None,
    ),
    (
        "▶️ YouTube Studio",
        "YouTube High-Retention Video Views",
        "smm",
        90,
        500,
        500000,
        "📈 হাই রিটেনশন ভিউ + ওয়াচটাইম বুস্ট।",
        None,
    ),
    (
        "📸 Instagram Pro",
        "Instagram Real Followers [Refill Guarantee]",
        "smm",
        110,
        100,
        200000,
        "✨ প্রিমিয়াম অ্যাকাউন্ট ফলোয়ার | ৩৬৫ দিন রিফিল।",
        None,
    ),
    (
        "📸 Instagram Pro",
        "Instagram Post/Reels Likes [Instant]",
        "smm",
        35,
        100,
        100000,
        "⚡ ইনস্ট্যান্ট ডেলিভারি | রিয়েল লুকিং প্রোফাইল।",
        None,
    ),
    (
        "✈️ Telegram VIP",
        "Telegram Channel/Group Members [Non-Drop]",
        "smm",
        130,
        100,
        100000,
        "💎 জিরো ড্রপ গ্যারান্টি মেম্বার | পাবলিক চ্যানেল/গ্রুপ।",
        None,
    ),
    (
        "✈️ Telegram VIP",
        "Telegram Post Views [Last 1 Post]",
        "smm",
        15,
        500,
        1000000,
        "👁️ সুপার ফাস্ট পোস্ট ভিউ।",
        None,
    ),
]


def init_db() -> None:
    with _db_lock, db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT DEFAULT '',
                first_name      TEXT DEFAULT '',
                balance         REAL NOT NULL DEFAULT 0.0,
                total_spent     REAL NOT NULL DEFAULT 0.0,
                total_deposit   REAL NOT NULL DEFAULT 0.0,
                referred_by     INTEGER DEFAULT 0,
                refer_count     INTEGER NOT NULL DEFAULT 0,
                refer_earned    REAL NOT NULL DEFAULT 0.0,
                last_daily_ts   INTEGER NOT NULL DEFAULT 0,
                joined_at       TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS services (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                name            TEXT NOT NULL,
                service_type    TEXT NOT NULL DEFAULT 'smm',
                rate_per_1k     REAL NOT NULL DEFAULT 100.0,
                min_qty         INTEGER NOT NULL DEFAULT 100,
                max_qty         INTEGER NOT NULL DEFAULT 100000,
                description     TEXT NOT NULL DEFAULT '',
                file_id         TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id      INTEGER NOT NULL,
                service_name    TEXT NOT NULL,
                buyer_id        INTEGER NOT NULL,
                buyer_name      TEXT NOT NULL,
                target_link     TEXT NOT NULL DEFAULT '',
                quantity        INTEGER NOT NULL DEFAULT 1,
                price           REAL NOT NULL DEFAULT 0.0,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS balance_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id        INTEGER NOT NULL,
                buyer_name      TEXT NOT NULL,
                method          TEXT NOT NULL,
                amount          REAL NOT NULL,
                trxid           TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key             TEXT PRIMARY KEY,
                value           TEXT NOT NULL
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        if count == 0:
            conn.executemany(
                """
                INSERT INTO services
                (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                DEFAULT_SMM_SERVICES,
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Database Helper Functions
# ---------------------------------------------------------------------------
def get_setting(key: str, default: str = "") -> str:
    with _db_lock, db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _db_lock, db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()


def ensure_user(user_id: int, username: str = "", first_name: str = "") -> sqlite3.Row:
    with _db_lock, db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username or "", first_name or "User"),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        else:
            conn.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (username or row["username"], first_name or row["first_name"], user_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row


def get_balance(user_id: int) -> float:
    with _db_lock, db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return round(float(row["balance"]), 2) if row else 0.0


def add_balance(user_id: int, amount: float, is_deposit: bool = False) -> float:
    ensure_user(user_id)
    with _db_lock, db() as conn:
        if is_deposit and amount > 0:
            conn.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 2), total_deposit = ROUND(total_deposit + ?, 2) WHERE user_id = ?",
                (amount, amount, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 2) WHERE user_id = ?",
                (amount, user_id),
            )
        conn.commit()
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return round(float(row["balance"]), 2) if row else 0.0


def record_spend(user_id: int, amount: float) -> None:
    with _db_lock, db() as conn:
        conn.execute(
            "UPDATE users SET total_spent = ROUND(total_spent + ?, 2) WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


def list_services(category: str | None = None) -> list[sqlite3.Row]:
    with _db_lock, db() as conn:
        if category:
            return conn.execute(
                "SELECT * FROM services WHERE category = ? ORDER BY id ASC", (category,)
            ).fetchall()
        return conn.execute("SELECT * FROM services ORDER BY category ASC, id ASC").fetchall()


def list_categories() -> list[str]:
    with _db_lock, db() as conn:
        rows = conn.execute("SELECT DISTINCT category FROM services ORDER BY id ASC").fetchall()
        return [r["category"] for r in rows]


def get_service(service_id: int) -> sqlite3.Row | None:
    with _db_lock, db() as conn:
        return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def vip_badge(total_spent: float) -> str:
    if total_spent >= 5000:
        return "👑 VVIP Legend"
    if total_spent >= 2000:
        return "💎 Royal Diamond"
    if total_spent >= 500:
        return "🥇 Gold VIP"
    if total_spent >= 100:
        return "🥈 Silver Pro"
    return "🥉 Bronze Member"


def payment_numbers_text() -> str:
    bkash = get_setting("bkash_number")
    nagad = get_setting("nagad_number")
    rocket = get_setting("rocket_number")
    binance = get_setting("binance_id")
    lines = []
    if bkash:
        lines.append(f"🌸 <b>bKash (Personal/Send Money):</b> <code>{bkash}</code>")
    if nagad:
        lines.append(f"🧡 <b>Nagad (Send Money):</b> <code>{nagad}</code>")
    if rocket:
        lines.append(f"💜 <b>Rocket (Send Money):</b> <code>{rocket}</code>")
    if binance:
        lines.append(f"🟡 <b>Binance Pay ID / USDT:</b> <code>{binance}</code>")
    if not lines:
        return "⚠️ পেমেন্ট নাম্বার এখনো সেট করা হয়নি। অ্যাডমিনের সাথে যোগাযোগ করুন।"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Colored Reply Keyboards (User & Admin)
# ---------------------------------------------------------------------------
def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    web_app_url = os.environ.get("WEB_APP_URL", "").strip()
    rows = []
    if web_app_url:
        rows.append(
            [
                _mkb(BTN_NEW_ORDER, "danger"),
                _mkb(BTN_MINIAPP, "primary", web_app=WebAppInfo(url=web_app_url)),
            ]
        )
    else:
        rows.append([_mkb(BTN_NEW_ORDER, "danger")])

    rows.extend(
        [
            [_mkb(BTN_PROFILE, "primary"), _mkb(BTN_DEPOSIT, "success")],
            [_mkb(BTN_SERVICES, "primary"), _mkb(BTN_MY_ORDERS, "danger")],
            [_mkb(BTN_REFER, "success"), _mkb(BTN_LEADERBOARD, "primary")],
            [_mkb(BTN_MISSION, "success"), _mkb(BTN_SUPPORT, "danger")],
        ]
    )
    if is_admin(user_id):
        rows.append([_mkb(BTN_ADMIN, "primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu() -> ReplyKeyboardMarkup:
    rows = [
        [_mkb(BTN_A_ADD_SMM, "success"), _mkb(BTN_A_ADD_FILE, "primary")],
        [_mkb(BTN_A_LIST, "primary"), _mkb(BTN_A_DEL, "danger")],
        [_mkb(BTN_A_ORDERS, "danger"), _mkb(BTN_A_DEPOSITS, "success")],
        [_mkb(BTN_A_BALANCE, "primary"), _mkb(BTN_A_PAYMENTS, "success")],
        [_mkb(BTN_A_SETTINGS, "primary"), _mkb(BTN_A_BROADCAST, "danger")],
        [_mkb(BTN_A_STATS, "success"), _mkb(BTN_BACK, "primary")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[_mkb(BTN_CANCEL, "danger")]], resize_keyboard=True)


# ---------------------------------------------------------------------------
# Main Welcome & Dashboard (/start)
# ---------------------------------------------------------------------------
async def show_main_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    with _db_lock, db() as conn:
        order_cnt = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ?", (user.id,)
        ).fetchone()[0]

    badge = vip_badge(float(u["total_spent"]))
    text = (
        f"👑 <b>স্বাগতম, {user.first_name}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌟 <b>VIP SMM PANEL & DIGITAL AGENCY</b>\n"
        "বাংলাদেশের সেরা ও দ্রুততম অটোমেটিক সোশ্যাল মিডিয়া বুস্ট এবং প্রিমিয়াম সার্ভিস প্যানেলে আপনাকে স্বাগতম!\n\n"
        "📊 <b>আপনার অ্যাকাউন্ট ড্যাশবোর্ড:</b>\n"
        f"├─ 🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        f"├─ 💳 <b>বর্তমান ব্যালেন্স:</b> <b>৳{u['balance']:.2f}</b>\n"
        f"├─ 📦 <b>মোট অর্ডার:</b> {order_cnt} টি\n"
        f"└─ 🏆 <b>ভিআইপি র‍্যাংক:</b> {badge}\n\n"
        "🔥 <b>আমাদের প্রিমিয়াম সার্ভিসসমূহ:</b>\n"
        "├─ 📘 Facebook Followers, Likes, Reels Views\n"
        "├─ 🎵 TikTok Real Followers, Hearts & Viral Views\n"
        "├─ ▶️ YouTube Subscribers & 4000h Watchtime\n"
        "├─ 📸 Instagram & ✈️ Telegram Non-Drop Members\n"
        "└─ 📁 VIP Digital Files & Premium Tools\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>শুরু করতে নিচের রঙিন মেনু বা বাটন সিলেক্ট করুন:</b>"
    )

    web_app_url = os.environ.get("WEB_APP_URL", "").strip()
    inline_rows = [
        [
            InlineKeyboardButton("🚀 দ্রুত অর্ডার করুন", callback_data="open_cats"),
            InlineKeyboardButton("💰 ব্যালেন্স অ্যাড", callback_data="open_deposit"),
        ]
    ]
    if web_app_url:
        inline_rows.insert(
            0,
            [
                InlineKeyboardButton(
                    "🔥 VIP SMM প্যানেল চালু করুন (Mini App)",
                    web_app=WebAppInfo(url=web_app_url),
                )
            ],
        )

    inline_kb = InlineKeyboardMarkup(inline_rows)

    if update.message:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_menu(user.id)
        )
        await update.message.reply_text(
            "⚡ <b>কুইক অ্যাকশন প্যানেল:</b>",
            parse_mode="HTML",
            reply_markup=inline_kb,
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=inline_kb
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    user = update.effective_user
    with _db_lock, db() as conn:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user.id,)
        ).fetchone()

    u = ensure_user(user.id, user.username or "", user.first_name or "User")

    # Handle Referral Deep-Link (/start ref_12345) for brand new users
    if existing is None and context.args:
        arg = str(context.args[0]).strip()
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg.replace("ref_", "", 1))
            except ValueError:
                ref_id = 0
            if ref_id and ref_id != user.id:
                try:
                    bonus = float(get_setting("refer_bonus", "2") or 2)
                except ValueError:
                    bonus = 2.0
                ensure_user(ref_id)
                with _db_lock, db() as conn:
                    conn.execute(
                        "UPDATE users SET referred_by = ? WHERE user_id = ?",
                        (ref_id, user.id),
                    )
                    conn.execute(
                        "UPDATE users SET balance = ROUND(balance + ?, 2), "
                        "refer_count = refer_count + 1, "
                        "refer_earned = ROUND(refer_earned + ?, 2) "
                        "WHERE user_id = ?",
                        (bonus, bonus, ref_id),
                    )
                    conn.commit()
                try:
                    await context.bot.send_message(
                        chat_id=ref_id,
                        text=(
                            "🎉 <b>নতুন রেফারেল বোনাস!</b>\n\n"
                            f"👤 <b>{user.first_name}</b> আপনার লিংক থেকে জয়েন করেছে!\n"
                            f"🎁 আপনার ওয়ালেটে <b>৳{bonus:.2f}</b> যোগ করা হয়েছে।"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

    await show_main_dashboard(update, context)


# ---------------------------------------------------------------------------
# SMM Categories & New Order Flow
# ---------------------------------------------------------------------------
def build_categories_keyboard() -> InlineKeyboardMarkup:
    cats = list_categories()
    rows = []
    for i in range(0, len(cats), 2):
        pair = cats[i : i + 2]
        rows.append(
            [InlineKeyboardButton(c, callback_data=f"cat_{idx + i}") for idx, c in enumerate(pair)]
        )
    return InlineKeyboardMarkup(rows)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cats = list_categories()
    if not cats:
        msg = "⚠️ বর্তমানে কোনো সার্ভিস চালু নেই। অ্যাডমিন সার্ভিস অ্যাড করলে এখানে দেখা যাবে।"
        if update.message:
            await update.message.reply_text(msg, reply_markup=main_menu(update.effective_user.id))
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg)
        return

    text = (
        "🚀 <b>VIP SMM নতুন অর্ডার প্যানেল</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "আপনি কোন ক্যাটাগরির সার্ভিস নিতে চান? নিচের বাটন থেকে সিলেক্ট করুন:"
    )
    kb = build_categories_keyboard()
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def show_all_services_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = list_services()
    if not services:
        await update.message.reply_text(
            "কোনো সার্ভিস নেই।", reply_markup=main_menu(update.effective_user.id)
        )
        return
    lines = ["📋 <b>আমাদের সকল VIP SMM ও ফাইল সার্ভিস লিস্ট:</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    current_cat = None
    for s in services:
        if s["category"] != current_cat:
            current_cat = s["category"]
            lines.append(f"\n💠 <b>{current_cat}</b>")
        if s["service_type"] == "file":
            lines.append(
                f"  ├─ 🆔 <b>#{s['id']}</b> {s['name']} — <b>৳{s['rate_per_1k']:.0f} (Fixed)</b>"
            )
        else:
            lines.append(
                f"  ├─ 🆔 <b>#{s['id']}</b> {s['name']}\n"
                f"  │    💰 রেট: <b>৳{s['rate_per_1k']:.0f} / 1K</b> | Min: {s['min_qty']} | Max: {s['max_qty']}"
            )
    lines.append("\n👇 অর্ডার করতে <b>🚀 নতুন অর্ডার (SMM)</b> বাটনে চাপুন:")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=build_categories_keyboard(),
    )


async def handle_category_And_service_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "open_cats":
        await show_categories(update, context)
        return

    if data == "open_deposit":
        await start_deposit_flow(update, context)
        return

    if data.startswith("cat_"):
        try:
            idx = int(data.replace("cat_", "", 1))
        except ValueError:
            return
        cats = list_categories()
        if idx < 0 or idx >= len(cats):
            return
        cat_name = cats[idx]
        svcs = list_services(cat_name)
        lines = [
            f"💠 <b>ক্যাটাগরি: {cat_name}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "নিচ থেকে আপনার পছন্দের সার্ভিসটি সিলেক্ট করুন:\n"
        ]
        buttons = []
        for s in svcs:
            if s["service_type"] == "file":
                price_tag = f"৳{s['rate_per_1k']:.0f}"
                lines.append(f"🔹 <b>{s['name']}</b>\n   💰 দাম: <b>{price_tag}</b> (Instant File)\n")
            else:
                price_tag = f"৳{s['rate_per_1k']:.0f}/1K"
                lines.append(
                    f"🔹 <b>{s['name']}</b>\n"
                    f"   💰 রেট: <b>{price_tag}</b> | Min: {s['min_qty']} - Max: {s['max_qty']}\n"
                )
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🛒 {s['name']} ({price_tag})", callback_data=f"srv_{s['id']}"
                    )
                ]
            )
        buttons.append([InlineKeyboardButton("🔙 ক্যাটাগরি লিস্টে ফিরুন", callback_data="open_cats")])
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("srv_"):
        try:
            sid = int(data.replace("srv_", "", 1))
        except ValueError:
            return
        s = get_service(sid)
        if not s:
            await query.edit_message_text("⚠️ সার্ভিসটি পাওয়া যায়নি।")
            return

        if s["service_type"] == "file":
            text = (
                f"📁 <b>প্রিমিয়াম ফাইল প্রোডাক্ট</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>নাম:</b> {s['name']}\n"
                f"💰 <b>মূল্য:</b> ৳{s['rate_per_1k']:.2f}\n"
                f"📝 <b>বিবরণ:</b> {s['description']}\n"
                f"💳 <b>আপনার ব্যালেন্স:</b> ৳{get_balance(query.from_user.id):.2f}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "কেনার সাথে সাথে বট অটোমেটিক ফাইল ডেলিভারি করবে!"
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"✅ এখনই কিনুন (৳{s['rate_per_1k']:.0f})",
                            callback_data=f"buyfile_{s['id']}",
                        )
                    ],
                    [InlineKeyboardButton("🔙 পেছনে যান", callback_data="open_cats")],
                ]
            )
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return

        # SMM Service Selected -> Ask for Target Link
        context.user_data.clear()
        context.user_data["state"] = ST_ORDER_LINK
        context.user_data["order_service_id"] = s["id"]
        text = (
            f"🚀 <b>সার্ভিস: {s['name']}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>রেট (প্রতি ১০০০):</b> ৳{s['rate_per_1k']:.2f}\n"
            f"📉 <b>সর্বনিম্ন অর্ডার:</b> {s['min_qty']}\n"
            f"📈 <b>সর্বোচ্চ অর্ডার:</b> {s['max_qty']}\n"
            f"📝 <b>বিবরণ:</b> {s['description']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔗 <b>ধাপ ১/২:</b> আপনার <b>প্রোফাইল / পোস্ট / ভিডিও লিংক (Link)</b> পাঠান:\n"
            "<i>(অবশ্যই অ্যাকাউন্ট বা পোস্ট Public থাকতে হবে)</i>"
        )
        await query.edit_message_text(text, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="🔗 নিচের বক্সে আপনার লিংকটি পেস্ট করে পাঠান (বাতিল করতে ❌ বাতিল করুন চাপুন):",
            reply_markup=cancel_menu(),
        )
        return

    if data.startswith("buyfile_"):
        try:
            sid = int(data.replace("buyfile_", "", 1))
        except ValueError:
            return
        s = get_service(sid)
        if not s:
            await query.edit_message_text("⚠️ প্রোডাক্ট পাওয়া যায়নি।")
            return
        user = query.from_user
        price = round(float(s["rate_per_1k"]), 2)
        bal = get_balance(user.id)
        if bal < price:
            await query.edit_message_text(
                f"⚠️ <b>পর্যাপ্ত ব্যালেন্স নেই!</b>\n\n"
                f"📦 প্রোডাক্ট: {s['name']}\n"
                f"💰 প্রয়োজন: <b>৳{price:.2f}</b>\n"
                f"💳 আপনার ব্যালেন্স: <b>৳{bal:.2f}</b>\n\n"
                "দয়া করে আগে <b>💰 ডিপোজিট</b> বাটনে চাপ দিয়ে ব্যালেন্স অ্যাড করুন।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💰 এখনই ডিপোজিট করুন", callback_data="open_deposit")]]
                ),
            )
            return

        new_bal = add_balance(user.id, -price)
        record_spend(user.id, price)
        status = "completed" if s["file_id"] else "pending"
        with _db_lock, db() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders
                (service_id, service_name, buyer_id, buyer_name, target_link, quantity, price, status)
                VALUES (?, ?, ?, ?, 'Instant File Delivery', 1, ?, ?)
                """,
                (s["id"], s["name"], user.id, user.first_name or "User", price, status),
            )
            order_id = cur.lastrowid
            conn.commit()

        if s["file_id"]:
            try:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=s["file_id"],
                    caption=(
                        f"✅ <b>অর্ডার সফল! (#SMM-{order_id})</b>\n"
                        f"📦 প্রোডাক্ট: {s['name']}\n"
                        f"💰 কর্তন: ৳{price:.2f} | নতুন ব্যালেন্স: ৳{new_bal:.2f}"
                    ),
                    parse_mode="HTML",
                )
                await query.edit_message_text(
                    f"✅ <b>#SMM-{order_id}</b> অটোমেটিক ডেলিভারি সম্পন্ন হয়েছে! নিচে আপনার ফাইলটি ডাউনলোড করুন।",
                    parse_mode="HTML",
                )
                return
            except Exception as exc:
                logger.warning("Instant file send failed: %s", exc)

        await query.edit_message_text(
            f"✅ <b>অর্ডার রিসিভ হয়েছে! (#SMM-{order_id})</b>\n"
            f"📦 প্রোডাক্ট: {s['name']}\n"
            f"💰 দাম: ৳{price:.2f}\n\n"
            "অ্যাডমিন দ্রুত আপনার অর্ডারটি ডেলিভারি করবেন।",
            parse_mode="HTML",
        )
        await notify_admin_new_order(
            context, order_id, s["name"], user, "Instant File Product", 1, price
        )


async def notify_admin_new_order(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    service_name: str,
    user,
    link: str,
    qty: int,
    price: float,
) -> None:
    aid = _admin_id()
    if not aid:
        return
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚙️ Processing", callback_data=f"ord_proc_{order_id}"),
                InlineKeyboardButton("✅ Completed", callback_data=f"ord_comp_{order_id}"),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel & Refund", callback_data=f"ord_canc_{order_id}"
                )
            ],
        ]
    )
    try:
        await context.bot.send_message(
            chat_id=aid,
            text=(
                "🔔 <b>নতুন SMM অর্ডার এসেছে!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🧾 <b>Order ID:</b> <code>#SMM-{order_id}</code>\n"
                f"📦 <b>সার্ভিস:</b> {service_name}\n"
                f"🔗 <b>লিংক:</b> <code>{link}</code>\n"
                f"🔢 <b>পরিমাণ (Quantity):</b> {qty}\n"
                f"💰 <b>পেমেন্ট:</b> ৳{price:.2f} (Paid from Wallet)\n"
                f"👤 <b>ক্রেতা:</b> {user.first_name} (ID: <code>{user.id}</code>)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "👇 অর্ডারের স্ট্যাটাস আপডেট করতে নিচের বাটনে চাপুন:"
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        logger.warning("Failed to notify admin of order: %s", exc)


# ---------------------------------------------------------------------------
# Deposit / Add Balance Flow
# ---------------------------------------------------------------------------
async def start_deposit_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "💰 <b>ওয়ালেট ডিপোজিট (Add Balance)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "নিচের যেকোনো নাম্বারে <b>Send Money</b> করুন:\n\n"
        f"{payment_numbers_text()}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 আপনি কোন মেথডে টাকা পাঠিয়েছেন সিলেক্ট করুন:"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌸 bKash", callback_data="dep_m_bKash"),
                InlineKeyboardButton("🧡 Nagad", callback_data="dep_m_Nagad"),
            ],
            [
                InlineKeyboardButton("💜 Rocket", callback_data="dep_m_Rocket"),
                InlineKeyboardButton("🟡 Binance", callback_data="dep_m_Binance"),
            ],
        ]
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def handle_deposit_method_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    method = (query.data or "").replace("dep_m_", "", 1)
    context.user_data.clear()
    context.user_data["state"] = ST_DEP_AMOUNT
    context.user_data["dep_method"] = method
    await query.edit_message_text(
        f"✅ <b>মেথড সিলেক্টেড: {method}</b>\n\n"
        f"{payment_numbers_text()}\n\n"
        "💵 <b>ধাপ ১/২:</b> আপনি কত টাকা পাঠিয়েছেন? (শুধু সংখ্যা লিখুন, যেমন: <code>100</code>):",
        parse_mode="HTML",
    )
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="💵 টাকার পরিমাণ লিখুন (বাতিল করতে ❌ বাতিল করুন চাপুন):",
        reply_markup=cancel_menu(),
    )


# ---------------------------------------------------------------------------
# Admin Order & Deposit Decision Callbacks
# ---------------------------------------------------------------------------
async def handle_admin_decisions_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ শুধুমাত্র অ্যাডমিন এই অ্যাকশন নিতে পারবে।")
        return

    data = query.data or ""

    # Deposit Approve / Reject
    if data.startswith("approve_bal_") or data.startswith("reject_bal_"):
        is_app = data.startswith("approve_bal_")
        try:
            req_id = int(data.rsplit("_", 1)[1])
        except ValueError:
            return
        with _db_lock, db() as conn:
            row = conn.execute(
                "SELECT * FROM balance_requests WHERE id = ?", (req_id,)
            ).fetchone()
            if not row:
                await query.edit_message_text("⚠️ ডিপোজিট রিকোয়েস্ট পাওয়া যায়নি।")
                return
            if row["status"] != "pending":
                await query.edit_message_text(f"ℹ️ এই রিকোয়েস্ট ইতিমধ্যে {row['status']}।")
                return
            new_st = "approved" if is_app else "rejected"
            conn.execute(
                "UPDATE balance_requests SET status = ? WHERE id = ?", (new_st, req_id)
            )
            conn.commit()

        if is_app:
            new_bal = add_balance(row["buyer_id"], float(row["amount"]), is_deposit=True)
            await query.edit_message_text(
                f"✅ <b>Deposit #{req_id} Approved!</b>\n"
                f"👤 User ID: <code>{row['buyer_id']}</code>\n"
                f"💰 যোগ হয়েছে: ৳{row['amount']:.2f} | নতুন ব্যালেন্স: ৳{new_bal:.2f}",
                parse_mode="HTML",
            )
            try:
                await context.bot.send_message(
                    chat_id=row["buyer_id"],
                    text=(
                        "🎉 <b>ডিপোজিট অ্যাপ্রুভ হয়েছে!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💳 মেথড: {row['method']}\n"
                        f"💰 যোগ হয়েছে: <b>৳{row['amount']:.2f}</b>\n"
                        f"🏦 বর্তমান ব্যালেন্স: <b>৳{new_bal:.2f}</b>\n\n"
                        "এখন আপনি যেকোনো VIP SMM সার্ভিস অর্ডার করতে পারবেন!"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                f"❌ <b>Deposit #{req_id} Rejected!</b> (User: {row['buyer_id']})",
                parse_mode="HTML",
            )
            try:
                await context.bot.send_message(
                    chat_id=row["buyer_id"],
                    text=(
                        f"❌ আপনার <b>৳{row['amount']:.2f} ({row['method']} - {row['trxid']})</b> "
                        "ডিপোজিট রিকোয়েস্টটি বাতিল হয়েছে। সঠিক তথ্য দিয়ে আবার চেষ্টা করুন বা সাপোর্টে যোগাযোগ করুন।"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    # Order Processing / Completed / Cancel & Refund
    if data.startswith(("ord_proc_", "ord_comp_", "ord_canc_", "approve_", "reject_")):
        if data.startswith("approve_"):
            action = "comp"
            oid = int(data.split("_", 1)[1])
        elif data.startswith("reject_"):
            action = "canc"
            oid = int(data.split("_", 1)[1])
        else:
            parts = data.split("_")
            action = parts[1]  # proc, comp, canc
            oid = int(parts[2])

        with _db_lock, db() as conn:
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
            if not order:
                await query.edit_message_text("⚠️ অর্ডার পাওয়া যায়নি।")
                return
            if order["status"] in ("completed", "cancelled"):
                await query.edit_message_text(
                    f"ℹ️ Order #SMM-{oid} ইতিমধ্যে {order['status']} করা হয়েছে।"
                )
                return

            if action == "proc":
                conn.execute("UPDATE orders SET status = 'processing' WHERE id = ?", (oid,))
                conn.commit()
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Completed", callback_data=f"ord_comp_{oid}"
                            ),
                            InlineKeyboardButton(
                                "❌ Cancel & Refund", callback_data=f"ord_canc_{oid}"
                            ),
                        ]
                    ]
                )
                await query.edit_message_text(
                    f"⚙️ <b>Order #SMM-{oid} এখন Processing-এ আছে।</b>\n"
                    f"📦 {order['service_name']}\n"
                    f"🔗 <code>{order['target_link']}</code>\n"
                    f"🔢 Qty: {order['quantity']} | 💰 ৳{order['price']:.2f}",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                try:
                    await context.bot.send_message(
                        chat_id=order["buyer_id"],
                        text=(
                            f"⚙️ <b>অর্ডার আপডেট (#SMM-{oid})</b>\n"
                            f"📦 সার্ভিস: {order['service_name']}\n"
                            "🚀 আপনার অর্ডারটির কাজ শুরু হয়েছে (<b>Processing</b>)!"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return

            if action == "comp":
                conn.execute("UPDATE orders SET status = 'completed' WHERE id = ?", (oid,))
                conn.commit()
                svc = get_service(order["service_id"])
                if svc and svc["file_id"]:
                    try:
                        await context.bot.send_document(
                            chat_id=order["buyer_id"],
                            document=svc["file_id"],
                            caption=f"✅ Order #SMM-{oid} Completed!\n📦 {order['service_name']}",
                        )
                    except Exception:
                        pass
                await query.edit_message_text(
                    f"✅ <b>Order #SMM-{oid} সফলভাবে Completed করা হয়েছে!</b>",
                    parse_mode="HTML",
                )
                try:
                    await context.bot.send_message(
                        chat_id=order["buyer_id"],
                        text=(
                            f"🎉 <b>অর্ডার সম্পন্ন হয়েছে! (#SMM-{oid})</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📦 সার্ভিস: {order['service_name']}\n"
                            f"🔢 পরিমাণ: {order['quantity']}\n"
                            "✨ স্ট্যাটাস: <b>✅ Completed</b>\n\n"
                            "আমাদের সাথে থাকার জন্য ধন্যবাদ!"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return

            if action == "canc":
                conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (oid,))
                conn.commit()
                refund = float(order["price"])
                new_bal = add_balance(order["buyer_id"], refund)
                await query.edit_message_text(
                    f"❌ <b>Order #SMM-{oid} বাতিল করা হয়েছে এবং ৳{refund:.2f} ক্রেতার ওয়ালেটে রিফান্ড দেওয়া হয়েছে।</b>",
                    parse_mode="HTML",
                )
                try:
                    await context.bot.send_message(
                        chat_id=order["buyer_id"],
                        text=(
                            f"❌ <b>অর্ডার বাতিল ও রিফান্ড (#SMM-{oid})</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📦 সার্ভিস: {order['service_name']}\n"
                            f"💰 রিফান্ড: <b>+৳{refund:.2f}</b> আপনার ওয়ালেটে ফেরত দেওয়া হয়েছে।\n"
                            f"💳 বর্তমান ব্যালেন্স: <b>৳{new_bal:.2f}</b>"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return


async def handle_delete_service_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    try:
        sid = int((query.data or "").replace("del_", "", 1))
    except ValueError:
        return
    with _db_lock, db() as conn:
        conn.execute("DELETE FROM services WHERE id = ?", (sid,))
        conn.commit()
    await query.edit_message_text(f"🗑️ সার্ভিস #{sid} সফলভাবে ডিলিট করা হয়েছে।")


# ---------------------------------------------------------------------------
# User Menu Views (Orders, Profile, Refer, Leaderboard, Missions, Support)
# ---------------------------------------------------------------------------
async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 15",
            (uid,),
        ).fetchall()
    if not rows:
        await update.message.reply_text(
            "📦 <b>আপনার এখনো কোনো অর্ডার নেই।</b>\n\n"
            "অর্ডার করতে <b>🚀 নতুন অর্ডার (SMM)</b> বাটনে ক্লিক করুন!",
            parse_mode="HTML",
            reply_markup=main_menu(uid),
        )
        return

    status_map = {
        "pending": "⏳ Pending",
        "processing": "⚙️ Processing",
        "completed": "✅ Completed",
        "approved": "✅ Completed",
        "cancelled": "❌ Cancelled (Refunded)",
        "rejected": "❌ Rejected",
    }
    lines = ["📦 <b>আপনার সর্বশেষ ১৫টি অর্ডারের স্ট্যাটাস:</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        st_badge = status_map.get(r["status"], r["status"])
        lines.append(
            f"🧾 <b>#SMM-{r['id']}</b> — {st_badge}\n"
            f"├─ 📦 {r['service_name']}\n"
            f"├─ 🔢 Qty: {r['quantity']} | 💰 ৳{r['price']:.2f}\n"
            f"└─ 🔗 <code>{r['target_link']}</code>\n"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=main_menu(uid)
    )


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    with _db_lock, db() as conn:
        total_orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ?", (user.id,)
        ).fetchone()[0]
        comp_orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND status IN ('completed','approved')",
            (user.id,),
        ).fetchone()[0]

    badge = vip_badge(float(u["total_spent"]))
    text = (
        "👤 <b>আপনার ভিআইপি প্রোফাইল ও ওয়ালেট</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📛 <b>নাম:</b> {user.first_name}\n"
        f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        f"🏆 <b>ভিআইপি র‍্যাংক:</b> {badge}\n\n"
        "💳 <b>ওয়ালেট সামারি:</b>\n"
        f"├─ 💰 <b>বর্তমান ব্যালেন্স:</b> <b>৳{u['balance']:.2f}</b>\n"
        f"├─ 🏦 <b>মোট ডিপোজিট:</b> ৳{u['total_deposit']:.2f}\n"
        f"└─ 🛒 <b>মোট খরচ:</b> ৳{u['total_spent']:.2f}\n\n"
        "📊 <b>অ্যাক্টিভিটি সামারি:</b>\n"
        f"├─ 📦 <b>মোট অর্ডার:</b> {total_orders} টি\n"
        f"├─ ✅ <b>সম্পন্ন অর্ডার:</b> {comp_orders} টি\n"
        f"├─ 👥 <b>মোট রেফারেল:</b> {u['refer_count']} জন\n"
        f"└─ 🎁 <b>রেফারেল আয়:</b> ৳{u['refer_earned']:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_menu(user.id)
    )


async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
    bonus = get_setting("refer_bonus", "2")
    text = (
        "👥 <b>রেফার করুন এবং আনলিমিটেড আয় করুন!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 প্রতি সফল রেফারে পাবেন: <b>৳{bonus} টাকা বোনাস!</b>\n\n"
        "🔗 <b>আপনার পার্সোনাল রেফারেল লিংক:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "📊 <b>আপনার রেফারেল স্ট্যাটাস:</b>\n"
        f"├─ 👥 <b>মোট রেফার:</b> {u['refer_count']} জন\n"
        f"└─ 💰 <b>মোট বোনাস আয়:</b> ৳{u['refer_earned']:.2f} টাকা\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 আপনার বন্ধুদের লিংকটি শেয়ার করুন। তারা লিংকে ক্লিক করে Start করলেই আপনার ব্যালেন্সে অটোমেটিক টাকা যোগ হয়ে যাবে!"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_menu(user.id)
    )


async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db_lock, db() as conn:
        top_buyers = conn.execute(
            "SELECT first_name, user_id, total_spent FROM users ORDER BY total_spent DESC, balance DESC LIMIT 5"
        ).fetchall()
        top_refs = conn.execute(
            "SELECT first_name, user_id, refer_count FROM users ORDER BY refer_count DESC LIMIT 5"
        ).fetchall()

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [
        "🏆 <b>VIP লিডারবোর্ড (Top 5 Champions)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>টপ ভিআইপি ক্রেতা (Top Buyers):</b>"
    ]
    for idx, r in enumerate(top_buyers):
        name = r["first_name"] or f"User {str(r['user_id'])[:4]}**"
        lines.append(f"{medals[idx]} <b>{name}</b> — ৳{r['total_spent']:.0f}")

    lines.append("\n👥 <b>টপ রেফারার (Top Referrers):</b>")
    for idx, r in enumerate(top_refs):
        name = r["first_name"] or f"User {str(r['user_id'])[:4]}**"
        lines.append(f"{medals[idx]} <b>{name}</b> — {r['refer_count']} রেফার")

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 বেশি অর্ডার ও রেফার করে আপনিও চলে আসুন ভিআইপি লিডারবোর্ডের শীর্ষে!"
    )
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=main_menu(update.effective_user.id),
    )


async def show_missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    try:
        daily_amt = float(get_setting("daily_bonus", "1") or 1)
    except ValueError:
        daily_amt = 1.0

    now_ts = int(time.time())
    last_ts = int(u["last_daily_ts"] or 0)
    can_claim = daily_amt > 0 and (now_ts - last_ts >= 86400)

    text = (
        "🎯 <b>ডেইলি বোনাস ও ভিআইপি মিশন</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 <b>ডেইলি লগইন বোনাস:</b> ৳{daily_amt:.2f} টাকা (প্রতি ২৪ ঘণ্টায় ১ বার)\n\n"
        "🏆 <b>ভিআইপি র‍্যাংক মিশনসমূহ:</b>\n"
        "├─ 🥈 <b>Silver Pro:</b> মোট ৳১০০ অর্ডার সম্পন্ন করুন\n"
        "├─ 🥇 <b>Gold VIP:</b> মোট ৳৫০০ অর্ডার সম্পন্ন করুন\n"
        "├─ 💎 <b>Royal Diamond:</b> মোট ৳২০০০ অর্ডার সম্পন্ন করুন\n"
        "└─ 👑 <b>VVIP Legend:</b> মোট ৳৫০০০ অর্ডার সম্পন্ন করুন\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = None
    if can_claim:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"🎁 আজকের বোনাস নিন (+৳{daily_amt:.2f})", callback_data="claim_daily")]]
        )
    else:
        rem_hours = max(1, int((86400 - (now_ts - last_ts)) / 3600))
        text += f"\n⏳ আপনি ইতিমধ্যে আজকের বোনাস নিয়েছেন। ({rem_hours} ঘণ্টা পর আবার নিতে পারবেন)"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb or main_menu(user.id))


async def handle_claim_daily_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    try:
        daily_amt = float(get_setting("daily_bonus", "1") or 1)
    except ValueError:
        daily_amt = 1.0

    now_ts = int(time.time())
    if daily_amt <= 0 or (now_ts - int(u["last_daily_ts"] or 0) < 86400):
        await query.edit_message_text("⏳ আপনি ইতিমধ্যে আজকের ডেইলি বোনাস ক্লেইম করেছেন!")
        return

    with _db_lock, db() as conn:
        conn.execute(
            "UPDATE users SET balance = ROUND(balance + ?, 2), last_daily_ts = ? WHERE user_id = ?",
            (daily_amt, now_ts, user.id),
        )
        conn.commit()
    new_bal = get_balance(user.id)
    await query.edit_message_text(
        f"🎉 <b>অভিনন্দন! আপনি ৳{daily_amt:.2f} ডেইলি বোনাস পেয়েছেন!</b>\n"
        f"💳 আপনার বর্তমান ব্যালেন্স: <b>৳{new_bal:.2f}</b>",
        parse_mode="HTML",
    )


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sup = get_setting("support_username")
    context.user_data.clear()
    context.user_data["state"] = ST_SUPPORT_MSG
    sup_line = f"📞 সরাসরি অ্যাডমিন ইনবক্স: @{sup}\n\n" if sup else ""
    text = (
        "🛟 <b>২৪/৭ ভিআইপি কাস্টমাইজড সাপোর্ট সেন্টার</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sup_line}"
        "💬 আপনার যেকোনো সমস্যা, অর্ডার আইডি বা প্রশ্ন নিচের মেসেজ বক্সে লিখে পাঠান। আমাদের অ্যাডমিন টিম সরাসরি আপনার মেসেজ দেখতে পাবে:"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=cancel_menu())


# ---------------------------------------------------------------------------
# Secret Admin Panel Views & Flows
# ---------------------------------------------------------------------------
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    with _db_lock, db() as conn:
        u_cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        p_ord = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')"
        ).fetchone()[0]
        p_dep = conn.execute(
            "SELECT COUNT(*) FROM balance_requests WHERE status = 'pending'"
        ).fetchone()[0]

    text = (
        "📊 <b>VIP SMM ADMIN CONTROL PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>মোট ইউজার:</b> {u_cnt} জন\n"
        f"📦 <b>পেন্ডিং/প্রসেসিং অর্ডার:</b> {p_ord} টি\n"
        f"💳 <b>পেন্ডিং ডিপোজিট:</b> {p_dep} টি\n\n"
        "🏦 <b>বর্তমান পেমেন্ট নাম্বারসমূহ:</b>\n"
        f"{payment_numbers_text()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 কন্ট্রোল করতে নিচের বাটন বেছে নিন:"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_menu())


async def admin_show_pending_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending','processing') ORDER BY id ASC LIMIT 15"
        ).fetchall()
    if not rows:
        await update.message.reply_text(
            "✅ কোনো পেন্ডিং বা প্রসেসিং অর্ডার নেই!", reply_markup=admin_menu()
        )
        return
    for r in rows:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("⚙️ Processing", callback_data=f"ord_proc_{r['id']}"),
                    InlineKeyboardButton("✅ Completed", callback_data=f"ord_comp_{r['id']}"),
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel & Refund", callback_data=f"ord_canc_{r['id']}"
                    )
                ],
            ]
        )
        await update.message.reply_text(
            f"🧾 <b>Order #SMM-{r['id']}</b> [{r['status'].upper()}]\n"
            f"📦 সার্ভিস: {r['service_name']}\n"
            f"🔗 লিংক: <code>{r['target_link']}</code>\n"
            f"🔢 পরিমাণ: {r['quantity']} | 💰 দাম: ৳{r['price']:.2f}\n"
            f"👤 ক্রেতা: {r['buyer_name']} (<code>{r['buyer_id']}</code>)",
            parse_mode="HTML",
            reply_markup=kb,
        )


async def admin_show_pending_deposits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT * FROM balance_requests WHERE status = 'pending' ORDER BY id ASC LIMIT 15"
        ).fetchall()
    if not rows:
        await update.message.reply_text(
            "✅ কোনো পেন্ডিং ডিপোজিট রিকোয়েস্ট নেই!", reply_markup=admin_menu()
        )
        return
    for r in rows:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Approve Balance", callback_data=f"approve_bal_{r['id']}"
                    ),
                    InlineKeyboardButton(
                        "❌ Reject", callback_data=f"reject_bal_{r['id']}"
                    ),
                ]
            ]
        )
        await update.message.reply_text(
            f"💳 <b>Deposit Request #{r['id']}</b>\n"
            f"🏦 মেথড: <b>{r['method']}</b>\n"
            f"💰 পরিমাণ: <b>৳{r['amount']:.2f}</b>\n"
            f"🔢 TrxID: <code>{r['trxid']}</code>\n"
            f"👤 ইউজার: {r['buyer_name']} (<code>{r['buyer_id']}</code>)",
            parse_mode="HTML",
            reply_markup=kb,
        )


async def admin_show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db_lock, db() as conn:
        u_cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        tot_bal = conn.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0]
        tot_dep = conn.execute("SELECT COALESCE(SUM(total_deposit),0) FROM users").fetchone()[0]
        tot_spn = conn.execute("SELECT COALESCE(SUM(total_spent),0) FROM users").fetchone()[0]
        o_tot = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        o_comp = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('completed','approved')"
        ).fetchone()[0]
        s_cnt = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]

    text = (
        "📈 <b>বট লাইভ অ্যানালিটিক্স ও রিপোর্ট</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>মোট রেজিস্টার্ড ইউজার:</b> {u_cnt} জন\n"
        f"📋 <b>মোট অ্যাক্টিভ সার্ভিস:</b> {s_cnt} টি\n"
        f"📦 <b>মোট অর্ডার:</b> {o_tot} টি (সম্পন্ন: {o_comp} টি)\n\n"
        "💰 <b>ফাইন্যান্সিয়াল রিপোর্ট:</b>\n"
        f"├─ 🏦 <b>মোট ডিপোজিট কালেকশন:</b> ৳{tot_dep:.2f}\n"
        f"├─ 🛒 <b>মোট সার্ভিস সেল:</b> ৳{tot_spn:.2f}\n"
        f"└─ 💳 <b>ইউজার ওয়ালেটে জমা আছে:</b> ৳{tot_bal:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_menu())


# ---------------------------------------------------------------------------
# Stateful Input Router (User & Admin Multi-Step Flows)
# ---------------------------------------------------------------------------
async def handle_stateful_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("state")
    if not state:
        return False

    text = (update.message.text or "").strip()
    user = update.effective_user

    if text in (BTN_CANCEL, BTN_BACK):
        context.user_data.clear()
        markup = admin_menu() if is_admin(user.id) and state.startswith(("add_", "bal_", "set_", "broadcast")) else main_menu(user.id)
        await update.message.reply_text("❌ বাতিল করা হয়েছে।", reply_markup=markup)
        return True

    # 1. User SMM Order Link -> Ask Quantity
    if state == ST_ORDER_LINK:
        sid = context.user_data.get("order_service_id")
        s = get_service(sid) if sid else None
        if not s:
            context.user_data.clear()
            await update.message.reply_text("⚠️ সার্ভিস পাওয়া যায়নি।", reply_markup=main_menu(user.id))
            return True
        context.user_data["order_link"] = text
        context.user_data["state"] = ST_ORDER_QTY
        await update.message.reply_text(
            f"✅ লিংক সেভ হয়েছে: <code>{text}</code>\n\n"
            f"🔢 <b>ধাপ ২/২:</b> আপনি কত <b>পরিমাণ (Quantity)</b> নিতে চান?\n"
            f"📉 সর্বনিম্ন: <b>{s['min_qty']}</b> | 📈 সর্বোচ্চ: <b>{s['max_qty']}</b>\n"
            f"💰 রেট: <b>৳{s['rate_per_1k']:.2f} / ১০০০</b>\n\n"
            "শুধু সংখ্যায় লিখুন (যেমন: <code>1000</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    # 2. User SMM Order Quantity -> Calculate Price, Deduct Balance & Place Order
    if state == ST_ORDER_QTY:
        sid = context.user_data.get("order_service_id")
        s = get_service(sid) if sid else None
        if not s:
            context.user_data.clear()
            return True
        try:
            qty = int(text)
        except ValueError:
            await update.message.reply_text(
                "⚠️ শুধুমাত্র সংখ্যায় পরিমাণ লিখুন (যেমন: 1000):",
                reply_markup=cancel_menu(),
            )
            return True

        if qty < int(s["min_qty"]) or qty > int(s["max_qty"]):
            await update.message.reply_text(
                f"⚠️ পরিমাণ অবশ্যই <b>{s['min_qty']}</b> থেকে <b>{s['max_qty']}</b> এর মধ্যে হতে হবে!",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True

        cost = max(1.0, round((qty / 1000.0) * float(s["rate_per_1k"]), 2))
        bal = get_balance(user.id)
        link = context.user_data.get("order_link", "")
        context.user_data.clear()

        if bal < cost:
            needed = round(cost - bal, 2)
            await update.message.reply_text(
                "⚠️ <b>আপনার ওয়ালেটে পর্যাপ্ত ব্যালেন্স নেই!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 সার্ভিস: {s['name']}\n"
                f"🔢 পরিমাণ: {qty}\n"
                f"💰 মোট খরচ: <b>৳{cost:.2f}</b>\n"
                f"💳 আপনার ব্যালেন্স: <b>৳{bal:.2f}</b>\n"
                f"❗ আরও প্রয়োজন: <b>৳{needed:.2f}</b>\n\n"
                "দয়া করে <b>💰 ডিপোজিট</b> বাটনে চাপ দিয়ে ব্যালেন্স অ্যাড করুন।",
                parse_mode="HTML",
                reply_markup=main_menu(user.id),
            )
            return True

        new_bal = add_balance(user.id, -cost)
        record_spend(user.id, cost)
        with _db_lock, db() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders
                (service_id, service_name, buyer_id, buyer_name, target_link, quantity, price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (s["id"], s["name"], user.id, user.first_name or "User", link, qty, cost),
            )
            order_id = cur.lastrowid
            conn.commit()

        await update.message.reply_text(
            "🎉 <b>আপনার অর্ডার সফলভাবে কনফার্ম হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧾 <b>Order ID:</b> <code>#SMM-{order_id}</code>\n"
            f"📦 <b>সার্ভিস:</b> {s['name']}\n"
            f"🔗 <b>লিংক:</b> <code>{link}</code>\n"
            f"🔢 <b>পরিমাণ:</b> {qty}\n"
            f"💰 <b>মোট চার্জ:</b> ৳{cost:.2f}\n"
            f"💳 <b>নতুন ব্যালেন্স:</b> ৳{new_bal:.2f}\n"
            "⏳ <b>স্ট্যাটাস:</b> Pending (খুব দ্রুত কাজ শুরু হবে)\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
            reply_markup=main_menu(user.id),
        )
        await notify_admin_new_order(context, order_id, s["name"], user, link, qty, cost)
        return True

    # 3. Deposit Amount -> Ask TrxID
    if state == ST_DEP_AMOUNT:
        try:
            amt = float(text)
            if amt < 10:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ সর্বনিম্ন ১০ টাকা ডিপোজিট করা যাবে। শুধু সংখ্যা লিখুন (যেমন: 100):",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["dep_amount"] = round(amt, 2)
        context.user_data["state"] = ST_DEP_TRXID
        await update.message.reply_text(
            f"💰 পরিমাণ: <b>৳{amt:.2f}</b>\n\n"
            "🔢 <b>ধাপ ২/২:</b> এবার পেমেন্টের <b>Transaction ID (TrxID)</b> অথবা সেন্ডার নাম্বারের শেষ ৪ ডিজিট লিখে পাঠান:",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    # 4. Deposit TrxID -> Save & Notify Admin
    if state == ST_DEP_TRXID:
        method = context.user_data.get("dep_method", "bKash")
        amt = float(context.user_data.get("dep_amount", 0))
        trxid = text
        context.user_data.clear()
        with _db_lock, db() as conn:
            cur = conn.execute(
                """
                INSERT INTO balance_requests (buyer_id, buyer_name, method, amount, trxid, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (user.id, user.first_name or "User", method, amt, trxid),
            )
            req_id = cur.lastrowid
            conn.commit()

        await update.message.reply_text(
            "✅ <b>আপনার ডিপোজিট রিকোয়েস্ট জমা হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧾 <b>Request ID:</b> <code>#DEP-{req_id}</code>\n"
            f"🏦 <b>মেথড:</b> {method}\n"
            f"💰 <b>পরিমাণ:</b> ৳{amt:.2f}\n"
            f"🔢 <b>TrxID:</b> <code>{trxid}</code>\n"
            "⏳ <b>স্ট্যাটাস:</b> Pending Verification\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "অ্যাডমিন যাচাই করার সাথে সাথে আপনার ওয়ালেটে অটোমেটিক ব্যালেন্স যোগ হয়ে যাবে!",
            parse_mode="HTML",
            reply_markup=main_menu(user.id),
        )
        aid = _admin_id()
        if aid:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve Balance", callback_data=f"approve_bal_{req_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject", callback_data=f"reject_bal_{req_id}"
                        ),
                    ]
                ]
            )
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        "💳 <b>নতুন ডিপোজিট রিকোয়েস্ট এসেছে!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🧾 <b>Request ID:</b> #DEP-{req_id}\n"
                        f"🏦 <b>মেথড:</b> {method}\n"
                        f"💰 <b>পরিমাণ:</b> <b>৳{amt:.2f}</b>\n"
                        f"🔢 <b>TrxID:</b> <code>{trxid}</code>\n"
                        f"👤 <b>ইউজার:</b> {user.first_name} (<code>{user.id}</code>)"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                pass
        return True

    # 5. User Support Ticket -> Forward to Admin
    if state == ST_SUPPORT_MSG:
        context.user_data.clear()
        aid = _admin_id()
        if aid:
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        "🛟 <b>নতুন সাপোর্ট মেসেজ!</b>\n"
                        f"👤 প্রেরক: {user.first_name} (@{user.username or 'none'})\n"
                        f"🆔 User ID: <code>{user.id}</code>\n\n"
                        f"💬 <b>মেসেজ:</b>\n{text}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ আপনার মেসেজটি অ্যাডমিন সাপোর্ট টিমের কাছে পাঠানো হয়েছে। খুব শীঘ্রই আপনাকে রিপ্লাই দেওয়া হবে।",
            reply_markup=main_menu(user.id),
        )
        return True

    # ---------------- ADMIN FLOWS ----------------
    if not is_admin(user.id):
        context.user_data.clear()
        return False

    if state == ST_ADD_SMM_CAT:
        context.user_data["smm_cat"] = text
        context.user_data["state"] = ST_ADD_SMM_NAME
        await update.message.reply_text(
            "📝 সার্ভিসের নাম লিখুন (যেমন: <code>TikTok Real Followers [VIP]</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_NAME:
        context.user_data["smm_name"] = text
        context.user_data["state"] = ST_ADD_SMM_RATE
        await update.message.reply_text(
            "💰 প্রতি ১০০০ (1K) এর রেট কত টাকা? (শুধু সংখ্যা, যেমন: <code>90</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_RATE:
        try:
            rate = float(text)
            if rate <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("সঠিক সংখ্যা লিখুন (যেমন: 90):", reply_markup=cancel_menu())
            return True
        context.user_data["smm_rate"] = rate
        context.user_data["state"] = ST_ADD_SMM_MIN
        await update.message.reply_text(
            "📉 সর্বনিম্ন অর্ডার সংখ্যা (Min Quantity) কত? (যেমন: <code>100</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_MIN:
        try:
            mn = int(text)
            if mn <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("সঠিক সংখ্যা লিখুন (যেমন: 100):", reply_markup=cancel_menu())
            return True
        context.user_data["smm_min"] = mn
        context.user_data["state"] = ST_ADD_SMM_MAX
        await update.message.reply_text(
            "📈 সর্বোচ্চ অর্ডার সংখ্যা (Max Quantity) কত? (যেমন: <code>100000</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_MAX:
        try:
            mx = int(text)
            if mx <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("সঠিক সংখ্যা লিখুন (যেমন: 100000):", reply_markup=cancel_menu())
            return True
        context.user_data["smm_max"] = mx
        context.user_data["state"] = ST_ADD_SMM_DESC
        await update.message.reply_text(
            "📝 সার্ভিসের সংক্ষিপ্ত বিবরণ লিখুন (গ্যারান্টি/স্পিড ইত্যাদি):",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_DESC:
        cat = context.user_data.get("smm_cat", "🌟 VIP SMM")
        name = context.user_data.get("smm_name", "SMM Service")
        rate = float(context.user_data.get("smm_rate", 100))
        mn = int(context.user_data.get("smm_min", 100))
        mx = int(context.user_data.get("smm_max", 100000))
        context.user_data.clear()
        with _db_lock, db() as conn:
            conn.execute(
                """
                INSERT INTO services
                (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id)
                VALUES (?, ?, 'smm', ?, ?, ?, ?, NULL)
                """,
                (cat, name, rate, mn, mx, text),
            )
            conn.commit()
        await update.message.reply_text(
            f"✅ <b>নতুন SMM সার্ভিস অ্যাড হয়েছে!</b>\n"
            f"💠 ক্যাটাগরি: {cat}\n"
            f"📦 নাম: {name}\n"
            f"💰 রেট: ৳{rate:.2f}/1K ({mn} - {mx})",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return True

    # Add File Product Flow
    if state == ST_ADD_FILE_NAME:
        context.user_data["f_name"] = text
        context.user_data["state"] = ST_ADD_FILE_PRICE
        await update.message.reply_text(
            "💰 ফাইল প্রোডাক্টের ফিক্সড দাম কত টাকা? (যেমন: <code>150</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_FILE_PRICE:
        try:
            pr = float(text)
            if pr <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("সঠিক দাম লিখুন (যেমন: 150):", reply_markup=cancel_menu())
            return True
        context.user_data["f_price"] = pr
        context.user_data["state"] = ST_ADD_FILE_DESC
        await update.message.reply_text("📝 প্রোডাক্টের বিবরণ লিখুন:", reply_markup=cancel_menu())
        return True

    if state == ST_ADD_FILE_DESC:
        context.user_data["f_desc"] = text
        context.user_data["state"] = ST_ADD_FILE_DOC
        await update.message.reply_text(
            "📎 এবার ফাইলটি (Document হিসেবে) পাঠান, অথবা ফাইল ছাড়া সেভ করতে <code>skip</code> লিখুন:",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_FILE_DOC and text.lower() == "skip":
        name = context.user_data.get("f_name", "Premium File")
        pr = float(context.user_data.get("f_price", 100))
        desc = context.user_data.get("f_desc", "")
        context.user_data.clear()
        with _db_lock, db() as conn:
            conn.execute(
                """
                INSERT INTO services
                (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id)
                VALUES ('📁 VIP Digital Files', ?, 'file', ?, 1, 1, ?, NULL)
                """,
                (name, pr, desc),
            )
            conn.commit()
        await update.message.reply_text(
            f"✅ প্রোডাক্ট অ্যাড হয়েছে (ফাইল ছাড়া): {name} — ৳{pr:.0f}",
            reply_markup=admin_menu(),
        )
        return True

    # Admin Balance Add / Deduct
    if state == ST_BAL_UID:
        try:
            target_uid = int(text)
        except ValueError:
            await update.message.reply_text("সঠিক User ID লিখুন (শুধু সংখ্যা):", reply_markup=cancel_menu())
            return True
        context.user_data["bal_uid"] = target_uid
        context.user_data["state"] = ST_BAL_AMT
        await update.message.reply_text(
            f"🆔 User ID: <code>{target_uid}</code> (বর্তমান ব্যালেন্স: ৳{get_balance(target_uid):.2f})\n\n"
            "💰 কত টাকা যোগ বা কাটতে চান? (যোগ করতে <code>100</code>, কাটতে <code>-50</code> লিখুন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_BAL_AMT:
        try:
            delta = float(text)
        except ValueError:
            await update.message.reply_text("সঠিক সংখ্যা লিখুন (যেমন: 100 বা -50):", reply_markup=cancel_menu())
            return True
        target_uid = int(context.user_data.get("bal_uid", 0))
        context.user_data.clear()
        new_b = add_balance(target_uid, delta)
        await update.message.reply_text(
            f"✅ User <code>{target_uid}</code> এর ব্যালেন্স আপডেট হয়েছে!\n"
            f"💳 বর্তমান ব্যালেন্স: <b>৳{new_b:.2f}</b>",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        try:
            action_word = "যোগ করা হয়েছে" if delta >= 0 else "কেটে নেওয়া হয়েছে"
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    f"🏦 <b>অ্যাডমিন ওয়ালেট আপডেট:</b>\n"
                    f"💰 <b>৳{abs(delta):.2f}</b> আপনার ব্যালেন্সে {action_word}।\n"
                    f"💳 বর্তমান ব্যালেন্স: <b>৳{new_b:.2f}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return True

    # Admin Settings States
    if state in (
        ST_SET_BKASH,
        ST_SET_NAGAD,
        ST_SET_ROCKET,
        ST_SET_BINANCE,
        ST_SET_SUPPORT,
        ST_SET_REFER_BONUS,
        ST_SET_DAILY_BONUS,
    ):
        key_map = {
            ST_SET_BKASH: ("bkash_number", "bKash Number"),
            ST_SET_NAGAD: ("nagad_number", "Nagad Number"),
            ST_SET_ROCKET: ("rocket_number", "Rocket Number"),
            ST_SET_BINANCE: ("binance_id", "Binance Pay ID"),
            ST_SET_SUPPORT: ("support_username", "Support Username"),
            ST_SET_REFER_BONUS: ("refer_bonus", "Refer Bonus (৳)"),
            ST_SET_DAILY_BONUS: ("daily_bonus", "Daily Bonus (৳)"),
        }
        k, label = key_map[state]
        val = text.lstrip("@").strip() if state == ST_SET_SUPPORT else text
        set_setting(k, val)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ <b>{label}</b> সফলভাবে সেট করা হয়েছে: <code>{val}</code>",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return True

    # Admin Broadcast to All Users
    if state == ST_BROADCAST_TEXT:
        context.user_data.clear()
        with _db_lock, db() as conn:
            users = conn.execute("SELECT user_id FROM users").fetchall()
        sent = 0
        for r in users:
            try:
                await context.bot.send_message(
                    chat_id=r["user_id"],
                    text=f"📢 <b>অ্যাডমিন নোটিশ:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{text}",
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(
            f"✅ ব্রডকাস্ট মেসেজ সফলভাবে <b>{sent}</b> জন ইউজারের কাছে পাঠানো হয়েছে!",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return True

    return False


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id) or context.user_data.get("state") != ST_ADD_FILE_DOC:
        return
    if not update.message.document:
        return
    fid = update.message.document.file_id
    name = context.user_data.get("f_name", "Premium File")
    pr = float(context.user_data.get("f_price", 100))
    desc = context.user_data.get("f_desc", "")
    context.user_data.clear()
    with _db_lock, db() as conn:
        conn.execute(
            """
            INSERT INTO services
            (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id)
            VALUES ('📁 VIP Digital Files', ?, 'file', ?, 1, 1, ?, ?)
            """,
            (name, pr, desc, fid),
        )
        conn.commit()
    await update.message.reply_text(
        f"✅ <b>ফাইল প্রোডাক্ট অ্যাড হয়েছে (অটো-ডেলিভারি সহ):</b>\n📦 {name} — ৳{pr:.0f}",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


async def handle_admin_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    data = query.data or ""
    prompts = {
        "adm_set_bkash": (ST_SET_BKASH, "🌸 নতুন <b>bKash Number</b> লিখে পাঠান:"),
        "adm_set_nagad": (ST_SET_NAGAD, "🧡 নতুন <b>Nagad Number</b> লিখে পাঠান:"),
        "adm_set_rocket": (ST_SET_ROCKET, "💜 নতুন <b>Rocket Number</b> লিখে পাঠান:"),
        "adm_set_binance": (ST_SET_BINANCE, "🟡 নতুন <b>Binance Pay ID / USDT Address</b> লিখে পাঠান:"),
        "adm_set_support": (ST_SET_SUPPORT, "🛟 <b>Support Username</b> লিখুন (@ ছাড়া):"),
        "adm_set_refer": (ST_SET_REFER_BONUS, "🎁 প্রতি রেফারে কত টাকা বোনাস দিবেন? (সংখ্যা লিখুন, যেমন: 2):"),
        "adm_set_daily": (ST_SET_DAILY_BONUS, "🎯 ডেইলি বোনাস কত টাকা দিবেন? (সংখ্যা লিখুন, যেমন: 1):"),
    }
    if data in prompts:
        st, msg = prompts[data]
        context.user_data.clear()
        context.user_data["state"] = st
        await query.edit_message_text(msg, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="নিচে লিখে পাঠান (বাতিল করতে ❌ বাতিল করুন চাপুন):",
            reply_markup=cancel_menu(),
        )


# ---------------------------------------------------------------------------
# Mini App WebAppData Handler (Full Compatibility with VIP Mini App)
# ---------------------------------------------------------------------------
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.web_app_data is None:
        return
    try:
        data = json.loads(update.message.web_app_data.data)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return

    user = update.effective_user
    ensure_user(user.id, user.username or "", user.first_name or "User")

    action = str(data.get("type") or data.get("action") or "").strip()

    # 1. Mini App Admin Add Product/Service
    if action == "add_product" and is_admin(user.id):
        pname = str(data.get("name", "")).strip()
        pdesc = str(data.get("desc", "")).strip()
        try:
            pprice = float(data.get("price", 100))
        except (ValueError, TypeError):
            pprice = 100.0
        if pname and pprice > 0:
            with _db_lock, db() as conn:
                conn.execute(
                    """
                    INSERT INTO services
                    (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id)
                    VALUES ('🌟 Mini App VIP', ?, 'smm', ?, 100, 100000, ?, NULL)
                    """,
                    (pname, pprice, pdesc),
                )
                conn.commit()
            await update.message.reply_text(
                f"✅ Mini App থেকে সার্ভিস অ্যাড হয়েছে: {pname} — ৳{pprice:.0f}"
            )
        return

    # 2. Mini App Add Balance Request
    if action == "add_money_request":
        try:
            amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            amount = 0.0
        method = str(data.get("method", "bKash")).strip()
        trxid = str(data.get("trxid", "")).strip()
        if amount <= 0 or not trxid:
            return
        with _db_lock, db() as conn:
            cur = conn.execute(
                """
                INSERT INTO balance_requests (buyer_id, buyer_name, method, amount, trxid, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (user.id, user.first_name or "User", method, amount, trxid),
            )
            req_id = cur.lastrowid
            conn.commit()
        await update.message.reply_text(
            f"✅ Mini App ডিপোজিট রিকোয়েস্ট (#DEP-{req_id}) জমা হয়েছে!\n"
            f"🏦 মেথড: {method} | 💰 ৳{amount:.2f} | TrxID: {trxid}"
        )
        aid = _admin_id()
        if aid:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve Balance", callback_data=f"approve_bal_{req_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject", callback_data=f"reject_bal_{req_id}"
                        ),
                    ]
                ]
            )
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        f"💳 <b>Mini App Deposit Request (#DEP-{req_id})</b>\n"
                        f"🏦 মেথড: {method} | 💰 ৳{amount:.2f}\n"
                        f"🔢 TrxID: <code>{trxid}</code>\n"
                        f"👤 ইউজার: {user.first_name} (<code>{user.id}</code>)"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                pass
        return

    # 3. Mini App Order
    name = str(data.get("product") or data.get("product_name") or "").strip()
    link = str(data.get("link") or data.get("target_link") or "Mini App Order").strip()
    try:
        qty = int(data.get("quantity", 1) or 1)
    except (ValueError, TypeError):
        qty = 1
    try:
        price = float(data.get("price", 0) or 0)
    except (ValueError, TypeError):
        price = 0.0

    if not name:
        return

    bal = get_balance(user.id)
    if bal < price:
        await update.message.reply_text(
            f"⚠️ ব্যালেন্স কম! প্রয়োজন ৳{price:.2f}, আপনার আছে ৳{bal:.2f}। আগে ডিপোজিট করুন।"
        )
        return

    add_balance(user.id, -price)
    record_spend(user.id, price)
    with _db_lock, db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
            (service_id, service_name, buyer_id, buyer_name, target_link, quantity, price, status)
            VALUES (0, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (name, user.id, user.first_name or "User", link, qty, price),
        )
        oid = cur.lastrowid
        conn.commit()

    await update.message.reply_text(
        f"✅ Mini App অর্ডার সফল (#SMM-{oid})!\n📦 {name}\n💰 ৳{price:.2f}"
    )
    await notify_admin_new_order(context, oid, name, user, link, qty, price)


# ---------------------------------------------------------------------------
# Master Text Message Dispatcher
# ---------------------------------------------------------------------------
ALL_MENU_BUTTONS = {
    BTN_NEW_ORDER,
    BTN_MINIAPP,
    BTN_SERVICES,
    BTN_MY_ORDERS,
    BTN_DEPOSIT,
    BTN_PROFILE,
    BTN_REFER,
    BTN_LEADERBOARD,
    BTN_MISSION,
    BTN_SUPPORT,
    BTN_ADMIN,
    BTN_BACK,
    BTN_A_ADD_SMM,
    BTN_A_ADD_FILE,
    BTN_A_LIST,
    BTN_A_DEL,
    BTN_A_ORDERS,
    BTN_A_DEPOSITS,
    BTN_A_BALANCE,
    BTN_A_PAYMENTS,
    BTN_A_SETTINGS,
    BTN_A_BROADCAST,
    BTN_A_STATS,
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return
    text = update.message.text.strip()
    user = update.effective_user
    ensure_user(user.id, user.username or "", user.first_name or "User")

    # Auto-cancel active state if the user taps any main/admin menu button
    if text in ALL_MENU_BUTTONS:
        context.user_data.clear()
    elif context.user_data.get("state"):
        handled = await handle_stateful_input(update, context)
        if handled:
            return

    if text == BTN_BACK:
        await show_main_dashboard(update, context)
        return

    if text == BTN_NEW_ORDER:
        await show_categories(update, context)
        return

    if text == BTN_SERVICES:
        await show_all_services_list(update, context)
        return

    if text == BTN_MY_ORDERS:
        await show_my_orders(update, context)
        return

    if text == BTN_DEPOSIT:
        await start_deposit_flow(update, context)
        return

    if text == BTN_PROFILE:
        await show_profile(update, context)
        return

    if text == BTN_REFER:
        await show_referral(update, context)
        return

    if text == BTN_LEADERBOARD:
        await show_leaderboard(update, context)
        return

    if text == BTN_MISSION:
        await show_missions(update, context)
        return

    if text == BTN_SUPPORT:
        await show_support(update, context)
        return

    if text in (BTN_MINIAPP, "📱 Mini App Kholo", "Mini Bot Open"):
        web_app_url = os.environ.get("WEB_APP_URL", "").strip()
        if web_app_url:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🚀 Open VIP Mini App", web_app=WebAppInfo(url=web_app_url)
                        )
                    ]
                ]
            )
            await update.message.reply_text("📱 নিচের বাটনে চাপ দিয়ে Mini App খুলুন:", reply_markup=kb)
        else:
            await show_categories(update, context)
        return

    # ---------------- ADMIN MENU ACTIONS ----------------
    if text == BTN_ADMIN:
        if not is_admin(user.id):
            await update.message.reply_text("⛔ শুধুমাত্র অ্যাডমিন প্রবেশ করতে পারবে।")
            return
        await show_admin_panel(update, context)
        return

    if is_admin(user.id):
        if text == BTN_A_ADD_SMM:
            context.user_data["state"] = ST_ADD_SMM_CAT
            await update.message.reply_text(
                "➕ <b>নতুন SMM সার্ভিস অ্যাড:</b>\n\n"
                "💠 প্রথমে ক্যাটাগরির নাম লিখুন (যেমন: <code>📘 Facebook VIP</code> বা <code>🎵 TikTok Viral</code>):",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return

        if text == BTN_A_ADD_FILE:
            context.user_data["state"] = ST_ADD_FILE_NAME
            await update.message.reply_text(
                "📁 <b>নতুন ফাইল প্রোডাক্ট অ্যাড:</b>\n\n"
                "📦 প্রোডাক্টের নাম লিখুন:",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return

        if text == BTN_A_LIST:
            await show_all_services_list(update, context)
            return

        if text == BTN_A_DEL:
            svcs = list_services()
            if not svcs:
                await update.message.reply_text("কোনো সার্ভিস নেই।", reply_markup=admin_menu())
                return
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"🗑️ #{s['id']} {s['name']} (৳{s['rate_per_1k']:.0f})",
                            callback_data=f"del_{s['id']}",
                        )
                    ]
                    for s in svcs
                ]
            )
            await update.message.reply_text(
                "🗑️ যে সার্ভিসটি ডিলিট করতে চান সেটিতে চাপুন:", reply_markup=kb
            )
            return

        if text == BTN_A_ORDERS:
            await admin_show_pending_orders(update, context)
            return

        if text == BTN_A_DEPOSITS:
            await admin_show_pending_deposits(update, context)
            return

        if text == BTN_A_BALANCE:
            context.user_data["state"] = ST_BAL_UID
            await update.message.reply_text(
                "🆔 যে ইউজারের ব্যালেন্স যোগ বা কাটতে চান তার <b>User ID</b> লিখুন:",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return

        if text == BTN_A_PAYMENTS:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🌸 bKash সেট", callback_data="adm_set_bkash"),
                        InlineKeyboardButton("🧡 Nagad সেট", callback_data="adm_set_nagad"),
                    ],
                    [
                        InlineKeyboardButton("💜 Rocket সেট", callback_data="adm_set_rocket"),
                        InlineKeyboardButton("🟡 Binance সেট", callback_data="adm_set_binance"),
                    ],
                ]
            )
            await update.message.reply_text(
                f"⚙️ <b>পেমেন্ট নাম্বার সেটিংস:</b>\n\n{payment_numbers_text()}\n\n👇 কোনটি পরিবর্তন করবেন?",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        if text == BTN_A_SETTINGS:
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🛟 Support Username সেট", callback_data="adm_set_support"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎁 Refer Bonus সেট", callback_data="adm_set_refer"
                        ),
                        InlineKeyboardButton(
                            "🎯 Daily Bonus সেট", callback_data="adm_set_daily"
                        ),
                    ],
                ]
            )
            await update.message.reply_text(
                "🎁 <b>বোনাস ও সাপোর্ট সেটিংস:</b>\n"
                f"├─ Support: @{get_setting('support_username', 'Not Set')}\n"
                f"├─ Refer Bonus: ৳{get_setting('refer_bonus', '2')}\n"
                f"└─ Daily Bonus: ৳{get_setting('daily_bonus', '1')}",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        if text == BTN_A_BROADCAST:
            context.user_data["state"] = ST_BROADCAST_TEXT
            await update.message.reply_text(
                "📢 <b>ব্রডকাস্ট মেসেজ:</b>\nসকল ইউজারের কাছে যে নোটিশ বা অফার পাঠাতে চান তা লিখে পাঠান:",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return

        if text == BTN_A_STATS:
            await admin_show_stats(update, context)
            return

    await show_main_dashboard(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning("Bot update error: %s", context.error)


# ---------------------------------------------------------------------------
# Flask Keep-Alive & Mini App API Server (Render Ready)
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/")
def home():
    return "VIP SMM Bot is Running 24/7 OK", 200


@app.route("/ping")
def ping():
    return "OK", 200


@app.route("/api/services")
def api_services():
    svcs = list_services()
    return jsonify(
        {
            "services": [
                {
                    "id": s["id"],
                    "category": s["category"],
                    "name": s["name"],
                    "type": s["service_type"],
                    "rate_per_1k": s["rate_per_1k"],
                    "min_qty": s["min_qty"],
                    "max_qty": s["max_qty"],
                    "description": s["description"],
                }
                for s in svcs
            ]
        }
    )


@app.route("/api/balance")
def api_balance():
    try:
        uid = int(request.args.get("user_id", "0"))
    except ValueError:
        uid = 0
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT id, service_id, service_name, status, price FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 20",
            (uid,),
        ).fetchall()
    return jsonify(
        {
            "balance": get_balance(uid),
            "orders": [
                {
                    "id": r["id"],
                    "product_id": r["service_id"],
                    "service_name": r["service_name"],
                    "status": "approved" if r["status"] == "completed" else r["status"],
                    "price": r["price"],
                }
                for r in rows
            ],
        }
    )


@app.route("/download/<int:product_id>")
def download_product(product_id: int):
    try:
        uid = int(request.args.get("user_id", "0"))
    except ValueError:
        uid = 0
    s = get_service(product_id)
    if not s or not s["file_id"]:
        return "File not found", 404
    with _db_lock, db() as conn:
        ok = conn.execute(
            "SELECT 1 FROM orders WHERE service_id = ? AND buyer_id = ? AND status IN ('completed','approved') LIMIT 1",
            (product_id, uid),
        ).fetchone()
    if not ok:
        return "Order not approved", 403
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return "Token missing", 500
    try:
        file_id = s["file_id"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(file_id)}"
        ) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        path = info["result"]["file_path"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{path}"
        ) as resp:
            data = resp.read()
        name = (s["name"] or "vip_file").replace("/", "_")
        return Response(
            data,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={name}"},
        )
    except Exception as exc:
        logger.warning("Download failed: %s", exc)
        return "Download failed", 500


def run_flask() -> None:
    try:
        port = int(os.environ.get("PORT", "8080") or 8080)
    except ValueError:
        port = 8080
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN environment variable is not set.")
        return
    if _admin_id() == 0:
        print("WARNING: ADMIN_ID is not set. Admin Panel will remain hidden.")

    init_db()
    threading.Thread(target=run_flask, daemon=True, name="flask-keepalive").start()

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("admin", show_admin_panel))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_category_And_service_cb,
            pattern="^(open_cats|open_deposit|cat_[0-9]+|srv_[0-9]+|buyfile_[0-9]+)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_deposit_method_cb, pattern="^dep_m_")
    )
    application.add_handler(
        CallbackQueryHandler(handle_claim_daily_cb, pattern="^claim_daily$")
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_admin_decisions_cb,
            pattern="^(approve_bal_|reject_bal_|ord_proc_|ord_comp_|ord_canc_|approve_|reject_)",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_delete_service_cb, pattern="^del_[0-9]+$")
    )
    application.add_handler(
        CallbackQueryHandler(handle_admin_settings_cb, pattern="^adm_set_")
    )

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_error_handler(error_handler)

    logger.info("VIP BIG SMM Panel Bot is running...")
    application.run_polling(close_loop=False, drop_pending_updates=True)


if __name__ == "__main__":
    main()