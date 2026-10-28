#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👑 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL — Final Production Edition
====================================================
Features:
  • Colored ReplyKeyboard Buttons (Primary Blue, Success Green, Danger Red — Telegram Bot API 9.4+ compatible)
  • Real SMM Panel API Integration (SMM_API_URL & SMM_API_KEY from environment variables only):
      - panel_balance(), panel_add_order(), panel_status(), panel_refill()
      - Platform URL validation, Min/Max validation, Confirm/Cancel step, user & panel balance verification
      - Live status check in My Orders with auto-refund on Partial/Cancelled and conditional Refill button
  • 16 Linked Active SMM Services (Instagram, TikTok, Facebook, YouTube, Telegram) — all average time 2-6 hours
  • Referral System: 10% bonus on referred user's COMPLETED order amount only (never on join; shown only in Refer menu)
  • Manual bKash/Nagad/Rocket Personal Send Money Deposit (Paid File bot style, method first) with TrxID duplicate check & 1-Click Admin Approve/Reject
  • 100% Hidden Admin Panel (ONLY visible to ADMIN_ID):
      - Live SMM Panel API Balance & Profit Checking (Panel Cost vs Sell Price)
      - Add / Edit SMM Service (service name, panel_service_id, panel rate, sell price, Min, Max, refill)
      - Admin "All Users" View & 1-Click CSV Export (user ID, username, name, wallet balance, total orders, join date)
      - View Orders (with panel_order_id, Check Panel Status, Refund Order), Manage Balances, Payments & Broadcast
  • Free Google Sheets Live Backup (on every new user, balance change, and order via env vars GOOGLE_SHEET_ID & GOOGLE_SERVICE_ACCOUNT_JSON)
  • Built-in Flask Keep-Alive Server (/ and /ping) + Mini App API (/api/balance, /api/services, /download/<id>) for Render
"""

import base64
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import requests
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

try:
    import gspread
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
except ImportError:
    gspread = None
    ServiceAccountCredentials = None

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account as google_service_account
except ImportError:
    GoogleAuthRequest = None
    google_service_account = None

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
BOT_DISPLAY_NAME = "𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vip_smm_bot.db")
_db_lock = threading.Lock()
_sheets_lock = threading.Lock()
_sheets_warned_unconfigured = False

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


def _fmt_num(val: float) -> str:
    """Formats a rate or price cleanly without losing decimal precision (e.g. 1.13, 2.25, 600)."""
    rounded = round(float(val), 4)
    if rounded == int(rounded):
        return str(int(rounded))
    text = f"{rounded:.4f}".rstrip("0").rstrip(".")
    return text


# ---------------------------------------------------------------------------
# Google Sheets Backup (Free, Environment Variables Only, Non-Blocking)
# ---------------------------------------------------------------------------
def _get_google_sheets_config() -> tuple[str, str]:
    """
    Reads Google Sheet ID and Service Account JSON/path strictly from environment variables.
    Supported env vars:
      - GOOGLE_SHEET_ID (or SHEET_ID)
      - GOOGLE_SERVICE_ACCOUNT_JSON (or GOOGLE_CREDENTIALS_JSON / GOOGLE_APPLICATION_CREDENTIALS)
    """
    sheet_id = (
        os.environ.get("GOOGLE_SHEET_ID", "").strip()
        or os.environ.get("SHEET_ID", "").strip()
    )
    creds_raw = (
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )
    return sheet_id, creds_raw


def _load_service_account_info(creds_raw: str) -> dict | None:
    """Parses service account credentials from JSON string, base64 string, or file path in env var."""
    if not creds_raw:
        return None
    if creds_raw.startswith("{"):
        try:
            return json.loads(creds_raw)
        except Exception:
            return None
    if os.path.isfile(creds_raw):
        try:
            with open(creds_raw, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None
    try:
        decoded = base64.b64decode(creds_raw).decode("utf-8")
        if decoded.strip().startswith("{"):
            return json.loads(decoded)
    except Exception:
        pass
    return None


def _sync_write_or_update_sheet(
    user_id: int,
    username: str,
    balance: float,
    order_details: str,
    row_key: str = "",
) -> None:
    """
    Writes or updates one row in Google Sheet with columns:
    [User ID, Username, Balance, Order Details, Date/Time]
    If Sheets is not configured, logs a warning and returns without error.
    """
    global _sheets_warned_unconfigured
    sheet_id, creds_raw = _get_google_sheets_config()
    if not sheet_id or not creds_raw:
        if not _sheets_warned_unconfigured:
            logger.warning(
                "Google Sheets backup is not configured (GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON missing). Skipping backup."
            )
            _sheets_warned_unconfigured = True
        return

    sa_info = _load_service_account_info(creds_raw)
    if not sa_info:
        logger.warning("Google Sheets credentials could not be parsed. Skipping backup.")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uname_clean = f"@{username.lstrip('@')}" if username else "-"
    bal_str = f"{float(balance):.2f}"
    uid_str = str(user_id)
    details_str = order_details or "Account Active"
    row_values = [uid_str, uname_clean, bal_str, details_str, now_str]
    headers = ["User ID", "Username", "Balance", "Order Details", "Date/Time"]

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    with _sheets_lock:
        # Path 1: gspread if installed
        if gspread is not None and ServiceAccountCredentials is not None:
            try:
                creds = ServiceAccountCredentials.from_service_account_info(
                    sa_info, scopes=scopes
                )
                client = gspread.authorize(creds)
                sh = client.open_by_key(sheet_id)
                ws = sh.sheet1
                all_rows = ws.get_all_values()
                if not all_rows:
                    ws.append_row(headers, value_input_option="USER_ENTERED")
                    all_rows = [headers]

                target_row_idx = None
                for idx, r in enumerate(all_rows[1:], start=2):
                    if not r:
                        continue
                    r_uid = str(r[0]).strip() if len(r) > 0 else ""
                    r_det = str(r[3]).strip() if len(r) > 3 else ""
                    if row_key and row_key in r_det:
                        target_row_idx = idx
                        break
                    if not row_key and r_uid == uid_str and not r_det.startswith("#SMM-"):
                        target_row_idx = idx
                        break

                if target_row_idx is not None:
                    ws.update(
                        f"A{target_row_idx}:E{target_row_idx}",
                        [row_values],
                        value_input_option="USER_ENTERED",
                    )
                else:
                    ws.append_row(row_values, value_input_option="USER_ENTERED")
                return
            except Exception as exc:
                logger.warning("Google Sheets backup error (gspread): %s", exc.__class__.__name__)
                return

        # Path 2: google-auth + requests REST fallback
        if google_service_account is not None and GoogleAuthRequest is not None:
            try:
                creds = google_service_account.Credentials.from_service_account_info(
                    sa_info, scopes=scopes
                )
                creds.refresh(GoogleAuthRequest())
                auth_headers = {
                    "Authorization": f"Bearer {creds.token}",
                    "Content-Type": "application/json",
                }
                base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
                get_resp = requests.get(
                    f"{base_url}/values/A1:E5000", headers=auth_headers, timeout=15
                )
                all_rows = []
                if get_resp.status_code == 200:
                    all_rows = get_resp.json().get("values", [])

                if not all_rows:
                    requests.post(
                        f"{base_url}/values/A1:E1:append?valueInputOption=USER_ENTERED",
                        headers=auth_headers,
                        json={"values": [headers]},
                        timeout=15,
                    )
                    all_rows = [headers]

                target_row_idx = None
                for idx, r in enumerate(all_rows[1:], start=2):
                    if not r:
                        continue
                    r_uid = str(r[0]).strip() if len(r) > 0 else ""
                    r_det = str(r[3]).strip() if len(r) > 3 else ""
                    if row_key and row_key in r_det:
                        target_row_idx = idx
                        break
                    if not row_key and r_uid == uid_str and not r_det.startswith("#SMM-"):
                        target_row_idx = idx
                        break

                if target_row_idx is not None:
                    requests.put(
                        f"{base_url}/values/A{target_row_idx}:E{target_row_idx}?valueInputOption=USER_ENTERED",
                        headers=auth_headers,
                        json={"values": [row_values]},
                        timeout=15,
                    )
                else:
                    requests.post(
                        f"{base_url}/values/A1:E1:append?valueInputOption=USER_ENTERED",
                        headers=auth_headers,
                        json={"values": [row_values]},
                        timeout=15,
                    )
            except Exception as exc:
                logger.warning("Google Sheets REST backup error: %s", exc.__class__.__name__)
        else:
            logger.warning(
                "gspread/google-auth libraries not installed; skipping Google Sheets backup."
            )


def schedule_sheet_backup(
    user_id: int,
    username: str = "",
    balance: float | None = None,
    order_details: str = "",
    row_key: str = "",
) -> None:
    """Schedules a non-blocking Google Sheet write/update in a background daemon thread."""
    try:
        if not username or balance is None:
            with _db_lock, db() as conn:
                u = conn.execute(
                    "SELECT username, first_name, balance FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if u:
                    if not username:
                        username = u["username"] or u["first_name"] or ""
                    if balance is None:
                        balance = float(u["balance"] or 0.0)
                if not order_details:
                    last_o = conn.execute(
                        "SELECT id, service_name, quantity, sell_price, status FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 1",
                        (user_id,),
                    ).fetchone()
                    if last_o:
                        order_details = (
                            f"Last Order #SMM-{last_o['id']}: {last_o['service_name']} "
                            f"(Qty: {last_o['quantity']}, ৳{last_o['sell_price']:.2f}, {last_o['status']})"
                        )
                    else:
                        order_details = "User Wallet / No Orders Yet"
        bal_val = float(balance or 0.0)
        threading.Thread(
            target=_sync_write_or_update_sheet,
            args=(user_id, username, bal_val, order_details, row_key),
            daemon=True,
        ).start()
    except Exception as exc:
        logger.warning("Failed to schedule Google Sheets backup: %s", exc.__class__.__name__)


def schedule_order_sheet_backup(order_id: int) -> None:
    """Reads an order + buyer from SQLite and writes/updates its row in Google Sheets."""
    try:
        with _db_lock, db() as conn:
            o = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not o:
                return
            u = conn.execute(
                "SELECT username, first_name, balance FROM users WHERE user_id = ?",
                (o["buyer_id"],),
            ).fetchone()
        uname = (u["username"] or u["first_name"] or "") if u else o["buyer_name"]
        bal = float(u["balance"] or 0.0) if u else 0.0
        s_price = float(o["sell_price"] or o["price"] or 0.0)
        details = (
            f"#SMM-{o['id']} | Panel #{o['panel_order_id']} | {o['service_name']} | "
            f"Qty: {o['quantity']} | ৳{s_price:.2f} | Status: {o['status']} | Link: {o['target_link']}"
        )
        schedule_sheet_backup(
            user_id=int(o["buyer_id"]),
            username=uname,
            balance=bal,
            order_details=details,
            row_key=f"#SMM-{o['id']}",
        )
    except Exception as exc:
        logger.warning("Failed to schedule order sheet backup: %s", exc.__class__.__name__)


# ---------------------------------------------------------------------------
# 1. SMM PANEL API FUNCTIONS (Environment Variables Only, Never Log Full Key)
# ---------------------------------------------------------------------------
def _get_smm_api_config() -> tuple[str, str]:
    """Reads SMM_API_URL and SMM_API_KEY exclusively from environment variables."""
    api_url = os.environ.get("SMM_API_URL", "").strip()
    api_key = os.environ.get("SMM_API_KEY", "").strip()
    return api_url, api_key


def _masked_key(api_key: str) -> str:
    """Returns only the first 4 characters + '***' for safe logging."""
    if not api_key:
        return "EMPTY"
    return f"{api_key[:4]}***"


def _smm_api_post(payload: dict) -> dict:
    """Sends a form-encoded POST request to SMM_API_URL with a 30-second timeout."""
    api_url, api_key = _get_smm_api_config()
    if not api_url or not api_key:
        return {"ok": False, "error": "Panel API not set - contact admin"}

    form_data = dict(payload)
    form_data["key"] = api_key

    try:
        resp = requests.post(api_url, data=form_data, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning(
            "SMM Panel API timeout (action=%s, key=%s)",
            payload.get("action"),
            _masked_key(api_key),
        )
        return {"ok": False, "error": "Panel API request timed out (30s). Please try again."}
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "SMM Panel API network error (action=%s, key=%s): %s",
            payload.get("action"),
            _masked_key(api_key),
            exc.__class__.__name__,
        )
        return {"ok": False, "error": "Network error connecting to SMM Panel API."}

    try:
        data = resp.json()
    except ValueError:
        logger.warning(
            "SMM Panel API returned non-JSON response (action=%s, key=%s)",
            payload.get("action"),
            _masked_key(api_key),
        )
        return {"ok": False, "error": "Invalid (non-JSON) response from SMM Panel API."}

    if not isinstance(data, dict):
        return {"ok": False, "error": "Unexpected response format from SMM Panel API."}

    if "error" in data and data["error"]:
        return {"ok": False, "error": str(data["error"])}

    return {"ok": True, "data": data}


def panel_balance() -> dict:
    """
    action=balance, key=SMM_API_KEY.
    Returns {"ok": True, "balance": float, "currency": str} or {"ok": False, "error": str}.
    """
    res = _smm_api_post({"action": "balance"})
    if not res.get("ok"):
        return res
    data = res["data"]
    try:
        bal = float(data.get("balance", 0.0))
    except (ValueError, TypeError):
        return {"ok": False, "error": "Invalid balance value from panel."}
    currency = str(data.get("currency", "USD")).strip() or "USD"
    return {"ok": True, "balance": bal, "currency": currency}


def panel_add_order(panel_service_id: int, link: str, quantity: int) -> dict:
    """
    action=add, key, service, link, quantity.
    Returns {"ok": True, "order": int} or {"ok": False, "error": str}.
    """
    res = _smm_api_post(
        {
            "action": "add",
            "service": int(panel_service_id),
            "link": str(link).strip(),
            "quantity": int(quantity),
        }
    )
    if not res.get("ok"):
        return res
    data = res["data"]
    order_id = data.get("order")
    if order_id is None:
        return {"ok": False, "error": str(data.get("error", "Panel did not return an order ID."))}
    try:
        return {"ok": True, "order": int(order_id)}
    except (ValueError, TypeError):
        return {"ok": False, "error": f"Invalid panel order ID: {order_id}"}


def map_panel_status(raw_status: str) -> str:
    """
    Maps panel statuses to bot statuses:
      Pending -> Pending
      In progress / Processing -> Processing
      Completed -> Completed
      Partial -> Partial
      Canceled / Cancelled -> Cancelled
    """
    s = (raw_status or "").strip().lower()
    if s == "pending":
        return "Pending"
    if s in ("in progress", "inprogress", "processing"):
        return "Processing"
    if s == "completed":
        return "Completed"
    if s == "partial":
        return "Partial"
    if s in ("canceled", "cancelled", "refunded"):
        return "Cancelled"
    return raw_status.strip().title() if raw_status else "Pending"


def panel_status(panel_order_id: int) -> dict:
    """
    action=status, key, order.
    Returns status (mapped), raw_status, starts_count, remains, charge, currency.
    """
    res = _smm_api_post({"action": "status", "order": int(panel_order_id)})
    if not res.get("ok"):
        return res
    data = res["data"]
    raw_status = str(data.get("status", "Pending"))
    mapped_status = map_panel_status(raw_status)
    try:
        starts_count = int(float(data.get("start_count") or 0))
    except (ValueError, TypeError):
        starts_count = 0
    try:
        remains = int(float(data.get("remains") or 0))
    except (ValueError, TypeError):
        remains = 0
    try:
        charge = float(data.get("charge") or 0.0)
    except (ValueError, TypeError):
        charge = 0.0
    currency = str(data.get("currency", "USD")).strip() or "USD"
    return {
        "ok": True,
        "status": mapped_status,
        "raw_status": raw_status,
        "starts_count": starts_count,
        "remains": remains,
        "charge": charge,
        "currency": currency,
    }


def panel_refill(panel_order_id: int) -> dict:
    """
    action=refill, key, order.
    Only called for services where refill_enabled == 1.
    """
    res = _smm_api_post({"action": "refill", "order": int(panel_order_id)})
    if not res.get("ok"):
        return res
    data = res["data"]
    if "error" in data and data["error"]:
        return {"ok": False, "error": str(data["error"])}
    refill_id = data.get("refill")
    return {"ok": True, "refill": refill_id if refill_id is not None else "Submitted"}


# ---------------------------------------------------------------------------
# Platform Link Validation Helper (Step 2 of Order Flow)
# ---------------------------------------------------------------------------
def detect_required_platform(category: str, service_name: str) -> tuple[str, tuple[str, ...]] | None:
    """Determines required platform domains from service category and name."""
    combined = f"{category} {service_name}".lower()
    if "telegram" in combined:
        return ("Telegram (t.me)", ("t.me",))
    if "facebook" in combined or "fb " in combined or combined.startswith("fb"):
        return ("Facebook (facebook.com / fb.watch)", ("facebook.com", "fb.com", "fb.watch", "m.facebook.com"))
    if "tiktok" in combined:
        return ("TikTok (tiktok.com)", ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"))
    if "instagram" in combined or "insta" in combined:
        return ("Instagram (instagram.com)", ("instagram.com", "instagr.am"))
    if "youtube" in combined or "yt " in combined:
        return ("YouTube (youtube.com / youtu.be)", ("youtube.com", "youtu.be", "m.youtube.com"))
    return None


def validate_service_link(category: str, service_name: str, link: str) -> tuple[bool, str]:
    """
    Validates that the link starts with http:// or https:// and matches the service platform.
    """
    clean = (link or "").strip()
    if not (clean.startswith("http://") or clean.startswith("https://")):
        return (
            False,
            "⚠️ অবৈধ লিংক! লিংকটি অবশ্যই <code>http://</code> অথবা <code>https://</code> দিয়ে শুরু হতে হবে।",
        )
    try:
        parsed = urllib.parse.urlparse(clean)
        host = (parsed.netloc or "").lower()
    except Exception:
        host = ""
    if not host:
        return (
            False,
            "⚠️ সঠিক ওয়েব লিংক প্রদান করুন (যেমন: <code>https://...</code>)।",
        )

    req = detect_required_platform(category, service_name)
    if req is not None:
        label, domains = req
        if not any(host == d or host.endswith("." + d) for d in domains):
            return (
                False,
                f"⚠️ ভুল প্ল্যাটফর্মের লিংক! এই সার্ভিসটির জন্য অবশ্যই <b>{label}</b> লিংক দিতে হবে।\n"
                f"আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি। সঠিক লিংক পাঠান:",
            )
    return (True, "")


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
BTN_NEW_ORDER = "অর্ডার করুন"
BTN_MINIAPP = "📱 Mini Bot Open"
BTN_SERVICES = "📋 সার্ভিস লিস্ট"
BTN_DEPOSIT = "💰 ডিপোজিট"
BTN_MY_ORDERS = "📦 আমার অর্ডার"
BTN_PROFILE = "👤 প্রোফাইল"
BTN_REFER = "👥 রেফারেল"
BTN_LEADERBOARD = "🏆 লিডারবোর্ড"
BTN_MISSION = "🎯 মিশন ও বোনাস"
BTN_SUPPORT = "🆘 সাপোর্ট"
BTN_ADMIN = "📊 অ্যাডমিন প্যানেল"

BTN_BACK = "🔙 মেইন মেনু"
BTN_CANCEL = "❌ বাতিল করুন"

# Admin Panel Buttons
BTN_A_ADD_SMM = "➕ SMM সার্ভিস অ্যাড"
BTN_A_EDIT_SMM = "✏️ সার্ভিস এডিট / Panel ID"
BTN_A_USERS = "👥 সকল ইউজার (All Users)"
BTN_A_LIST = "📋 সব সার্ভিস দেখুন"
BTN_A_DEL = "🗑️ সার্ভিস ডিলিট"
BTN_A_ORDERS = "📦 পেন্ডিং অর্ডার"
BTN_A_DEPOSITS = "💳 পেন্ডিং ডিপোজিট"
BTN_A_BALANCE = "💰 ব্যালেন্স অ্যাড/কাটুন"
BTN_A_PAYMENTS = "⚙️ পেমেন্ট নাম্বার সেট"
BTN_A_SETTINGS = "🎁 বোনাস ও সাপোর্ট সেট"
BTN_A_BROADCAST = "📢 ব্রডকাস্ট মেসেজ"
BTN_A_STATS = "📈 বট স্ট্যাটাস"
BTN_A_ADD_FILE = "📁 ফাইল প্রোডাক্ট অ্যাড"

# Conversation States stored in context.user_data["state"]
ST_ORDER_LINK = "order_link"
ST_ORDER_QTY = "order_qty"
ST_ORDER_CONFIRM = "order_confirm"
ST_DEP_AMOUNT = "dep_amount"
ST_DEP_TRXID = "dep_trxid"
ST_DEP_METHOD = "dep_method"
ST_DEP_SUBMIT = "dep_submit"
ST_SUPPORT_MSG = "support_msg"

# Admin Add Service States
ST_ADD_SMM_CAT = "add_smm_cat"
ST_ADD_SMM_NAME = "add_smm_name"
ST_ADD_SMM_PID = "add_smm_pid"
ST_ADD_SMM_PRATE = "add_smm_prate"
ST_ADD_SMM_RATE = "add_smm_rate"
ST_ADD_SMM_MIN = "add_smm_min"
ST_ADD_SMM_MAX = "add_smm_max"
ST_ADD_SMM_REFILL = "add_smm_refill"
ST_ADD_SMM_DESC = "add_smm_desc"

# Admin Edit Service States
ST_EDIT_SMM_NAME = "edit_smm_name"
ST_EDIT_SMM_PID = "edit_smm_pid"
ST_EDIT_SMM_PRATE = "edit_smm_prate"
ST_EDIT_SMM_RATE = "edit_smm_rate"
ST_EDIT_SMM_MIN = "edit_smm_min"
ST_EDIT_SMM_MAX = "edit_smm_max"
ST_EDIT_SMM_REFILL = "edit_smm_refill"
ST_EDIT_SMM_ACTIVE = "edit_smm_active"

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
ST_SET_DAILY_BONUS = "set_daily_bonus"
ST_SET_REFER_BONUS = "set_refer_bonus"
ST_SET_FORCE_CHANNEL = "set_force_channel"
ST_BROADCAST_TEXT = "broadcast_text"


# ---------------------------------------------------------------------------
# Database Layer (SQLite with Thread Lock & Safe Schema Migrations)
# ---------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=25.0)
    conn.row_factory = sqlite3.Row
    return conn


# Exact 16 Active Services:
# - All 16 are linked with their real panel_service_id
# - Average time display = 2-6 hours for all 16
# - Sell price (rate_per_1k) is what the customer pays
# - Where Min is not listed, min_qty = 0 (editable in Admin, never invented)
# - Where Max is unlimited, max_qty = -1; where Max is not listed, max_qty = 0 (editable in Admin)
# - Facebook Reaction (Love) is clearly labeled Bot Data
# - Telegram services added in bot__19: Members (10752), Mix Positive Reactions (7856), Post Views (10087) only
# - No YouTube Subscribers, no premium services, no Poll Vote, no Bot Start
ACTIVE_16_SERVICES = [
    # (category, name, service_type, rate_per_1k, min_qty, max_qty, description, file_id,
    #  panel_service_id, refill_enabled, refill_type, avg_time, panel_rate, markup, is_active)
    # --- Instagram (1-4) ---
    (
        "📸 Instagram",
        "Instagram Followers",
        "smm",
        600.0,
        10,
        500000,
        "⏱️ Average time: 2-6 hours | 🔄 LifeTime refill | Drop may happen, refill only if panel provides it.",
        None,
        11432,
        1,
        "LifeTime refill",
        "2-6 hours",
        486.72,
        round(600.0 - 486.72, 4),
        1,
    ),
    (
        "📸 Instagram",
        "Instagram Likes",
        "smm",
        30.0,
        10,
        1000000,
        "⏱️ Average time: 2-6 hours | 🔄 LifeTime refill | Drop may happen, refill only if panel provides it.",
        None,
        10604,
        1,
        "LifeTime refill",
        "2-6 hours",
        16.38,
        round(30.0 - 16.38, 4),
        1,
    ),
    (
        "📸 Instagram",
        "Instagram Views",
        "smm",
        1.13,
        100,
        5000000,
        "⏱️ Average time: 2-6 hours | 🚫 No Refill.",
        None,
        9892,
        0,
        "No Refill",
        "2-6 hours",
        0.13,
        round(1.13 - 0.13, 4),
        1,
    ),
    (
        "📸 Instagram",
        "Instagram Impression",
        "smm",
        15.0,
        10,
        1000000,
        "⏱️ Average time: 2-6 hours | 🚫 No Refill.",
        None,
        9873,
        0,
        "No Refill",
        "2-6 hours",
        8.749,
        round(15.0 - 8.749, 4),
        1,
    ),
    # --- TikTok (5-8) ---
    (
        "🎵 TikTok",
        "TikTok Followers",
        "smm",
        180.0,
        10,
        100000,
        "⏱️ Average time: 2-6 hours | 🚫 No Refill.",
        None,
        11708,
        0,
        "No Refill",
        "2-6 hours",
        169.182,
        round(180.0 - 169.182, 4),
        1,
    ),
    (
        "🎵 TikTok",
        "TikTok Likes",
        "smm",
        40.0,
        100,
        5000000,
        "⏱️ Average time: 2-6 hours | 🔄 REFILL 365D | Drop may happen, refill only if panel provides it.",
        None,
        10670,
        1,
        "REFILL 365D",
        "2-6 hours",
        30.186,
        round(40.0 - 30.186, 4),
        1,
    ),
    (
        "🎵 TikTok",
        "TikTok Views",
        "smm",
        2.25,
        100,
        -1,  # Max unlimited
        "⏱️ Average time: 2-6 hours | 🔄 REFILL 30D | Drop may happen, refill only if panel provides it.",
        None,
        11730,
        1,
        "REFILL 30D",
        "2-6 hours",
        1.248,
        round(2.25 - 1.248, 4),
        1,
    ),
    (
        "🎵 TikTok",
        "TikTok Shares",
        "smm",
        10.0,
        10,
        100000000,
        "⏱️ Average time: 2-6 hours | 🔄 LifeTime refill | Drop may happen, refill only if panel provides it.",
        None,
        10489,
        1,
        "LifeTime refill",
        "2-6 hours",
        8.606,
        round(10.0 - 8.606, 4),
        1,
    ),
    # --- Facebook (9-11) ---
    (
        "📘 Facebook",
        "Facebook Followers",
        "smm",
        30.0,
        100,
        15000,
        "⏱️ Average time: 2-6 hours | 🔄 REFILL 30D | Drop may happen, refill only if panel provides it.",
        None,
        11531,
        1,
        "REFILL 30D",
        "2-6 hours",
        23.439,
        round(30.0 - 23.439, 4),
        1,
    ),
    (
        "📘 Facebook",
        "Facebook Reaction (Love) [Bot Data]",
        "smm",
        50.0,
        10,
        50000,
        "🤖 Bot Data | ⏱️ Average time: 2-6 hours | 🚫 No Refill.",
        None,
        10834,
        0,
        "No Refill",
        "2-6 hours",
        44.681,
        round(50.0 - 44.681, 4),
        1,
    ),
    (
        "📘 Facebook",
        "Facebook Views",
        "smm",
        10.0,
        100,
        1000000000,
        "⏱️ Average time: 2-6 hours | 🔄 REFILL 365D | Drop may happen, refill only if panel provides it.",
        None,
        7213,
        1,
        "REFILL 365D",
        "2-6 hours",
        4.511,
        round(10.0 - 4.511, 4),
        1,
    ),
    # --- YouTube (12-13) ---
    (
        "▶️ YouTube",
        "YouTube Views",
        "smm",
        130.0,
        100,
        10000000,
        "⏱️ Average time: 2-6 hours | 🔄 REFILL 365D | Drop may happen, refill only if panel provides it.",
        None,
        8048,
        1,
        "REFILL 365D",
        "2-6 hours",
        111.358,
        round(130.0 - 111.358, 4),
        1,
    ),
    (
        "▶️ YouTube",
        "YouTube Likes",
        "smm",
        90.0,
        10,
        200000,
        "⏱️ Average time: 2-6 hours | 🔄 LifeTime refill | Drop may happen, refill only if panel provides it.",
        None,
        11838,
        1,
        "LifeTime refill",
        "2-6 hours",
        79.248,
        round(90.0 - 79.248, 4),
        1,
    ),
    # --- Telegram (14-16, added in bot__19) ---
    (
        "✈️ Telegram",
        "Telegram Members",
        "smm",
        100.0,
        10,
        1000000,
        "⏱️ Average time: 2-6 hours | 🔄 LifeTime refill | Drop may happen, refill only if panel provides it. | 🔗 চ্যানেল/গ্রুপ লিংক দিন (যেমন: https://t.me/ChannelName)",
        None,
        10752,
        1,
        "LifeTime refill",
        "2-6 hours",
        75.933,
        round(100.0 - 75.933, 4),
        1,
    ),
    (
        "✈️ Telegram",
        "Telegram Mix Positive Reactions + Free Views",
        "smm",
        20.0,
        10,
        1000000,
        "⏱️ Average time: 2-6 hours | 🚫 No Refill. | 🔗 পোস্ট লিংক দিন (যেমন: https://t.me/ChannelName/123) — চ্যানেল লিংক নয়। + Free Views সহ।",
        None,
        7856,
        0,
        "No Refill",
        "2-6 hours",
        5.59,
        round(20.0 - 5.59, 4),
        1,
    ),
    (
        "✈️ Telegram",
        "Telegram Post Views",
        "smm",
        2.0,
        50,
        2147483647,
        "⏱️ Average time: 2-6 hours | 🚫 No Refill. | 🔗 পোস্ট লিংক দিন (যেমন: https://t.me/ChannelName/123) — চ্যানেল লিংক নয়।",
        None,
        10087,
        0,
        "No Refill",
        "2-6 hours",
        0.273,
        round(2.0 - 0.273, 4),
        1,
    ),
]


def _sync_active_16_services(conn: sqlite3.Connection) -> None:
    """
    Ensures the 16 active services are present and linked, removes any legacy unlinked (panel_service_id = 0),
    YouTube Subscribers, or premium placeholder services, while keeping any custom linked services
    added later by Admin via the Admin Panel. Telegram services are part of the 16 and are NOT deleted.
    """
    conn.execute(
        """
        DELETE FROM services
        WHERE panel_service_id = 0
           OR LOWER(name) LIKE '%subscriber%'
           OR LOWER(name) LIKE '%capcut%'
           OR service_type = 'file'
        """
    )

    for item in ACTIVE_16_SERVICES:
        (
            cat,
            name,
            stype,
            sell_rate,
            mn,
            mx,
            desc,
            fid,
            pid,
            ref_en,
            ref_type,
            avg_t,
            prate,
            mk,
            is_act,
        ) = item
        existing = conn.execute(
            "SELECT id, min_qty, max_qty FROM services WHERE panel_service_id = ?",
            (pid,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO services
                (category, name, service_type, rate_per_1k, min_qty, max_qty, description,
                 file_id, panel_service_id, refill_enabled, refill_type, avg_time,
                 panel_rate, markup, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                item,
            )
        else:
            # Preserve admin-edited Min/Max if Admin already customized them
            new_min = existing["min_qty"] if mn == 0 and int(existing["min_qty"] or 0) > 0 else mn
            new_max = existing["max_qty"] if mx == 0 and int(existing["max_qty"] or 0) > 0 else mx
            conn.execute(
                """
                UPDATE services
                SET category = ?, name = ?, rate_per_1k = ?, min_qty = ?, max_qty = ?,
                    description = ?, refill_enabled = ?, refill_type = ?, avg_time = ?,
                    panel_rate = ?, markup = ?, is_active = 1
                WHERE panel_service_id = ?
                """,
                (
                    cat,
                    name,
                    sell_rate,
                    new_min,
                    new_max,
                    desc,
                    ref_en,
                    ref_type,
                    avg_t,
                    prate,
                    mk,
                    pid,
                ),
            )


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
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                category         TEXT NOT NULL,
                name             TEXT NOT NULL,
                service_type     TEXT NOT NULL DEFAULT 'smm',
                rate_per_1k      REAL NOT NULL DEFAULT 100.0,
                min_qty          INTEGER NOT NULL DEFAULT 0,
                max_qty          INTEGER NOT NULL DEFAULT 0,
                description      TEXT NOT NULL DEFAULT '',
                file_id          TEXT,
                panel_service_id INTEGER NOT NULL DEFAULT 0,
                refill_enabled   INTEGER NOT NULL DEFAULT 0,
                refill_type      TEXT NOT NULL DEFAULT 'No Refill',
                avg_time         TEXT NOT NULL DEFAULT '2-6 hours',
                panel_rate       REAL NOT NULL DEFAULT 0.0,
                markup           REAL NOT NULL DEFAULT 0.0,
                is_active        INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS orders (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id       INTEGER NOT NULL,
                service_name     TEXT NOT NULL,
                buyer_id         INTEGER NOT NULL,
                buyer_name       TEXT NOT NULL,
                target_link      TEXT NOT NULL DEFAULT '',
                quantity         INTEGER NOT NULL DEFAULT 1,
                price            REAL NOT NULL DEFAULT 0.0,
                status           TEXT NOT NULL DEFAULT 'Pending',
                panel_order_id   INTEGER NOT NULL DEFAULT 0,
                panel_service_id INTEGER NOT NULL DEFAULT 0,
                panel_cost       REAL NOT NULL DEFAULT 0.0,
                sell_price       REAL NOT NULL DEFAULT 0.0,
                starts_count     INTEGER NOT NULL DEFAULT 0,
                remains          INTEGER NOT NULL DEFAULT 0,
                refunded         INTEGER NOT NULL DEFAULT 0,
                refer_bonus_paid INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP
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

        # Safe ALTER TABLE migrations for existing databases
        svc_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(services)").fetchall()
        }
        if "panel_service_id" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN panel_service_id INTEGER NOT NULL DEFAULT 0"
            )
        if "refill_enabled" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN refill_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "refill_type" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN refill_type TEXT NOT NULL DEFAULT 'No Refill'"
            )
        if "avg_time" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN avg_time TEXT NOT NULL DEFAULT '2-6 hours'"
            )
        if "panel_rate" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN panel_rate REAL NOT NULL DEFAULT 0.0"
            )
        if "markup" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN markup REAL NOT NULL DEFAULT 0.0"
            )
        if "is_active" not in svc_cols:
            conn.execute(
                "ALTER TABLE services ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )

        ord_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "panel_order_id" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN panel_order_id INTEGER NOT NULL DEFAULT 0"
            )
        if "panel_service_id" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN panel_service_id INTEGER NOT NULL DEFAULT 0"
            )
        if "panel_cost" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN panel_cost REAL NOT NULL DEFAULT 0.0"
            )
        if "sell_price" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN sell_price REAL NOT NULL DEFAULT 0.0"
            )
        if "starts_count" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN starts_count INTEGER NOT NULL DEFAULT 0"
            )
        if "remains" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN remains INTEGER NOT NULL DEFAULT 0"
            )
        if "refunded" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN refunded INTEGER NOT NULL DEFAULT 0"
            )
        if "refer_bonus_paid" not in ord_cols:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN refer_bonus_paid INTEGER NOT NULL DEFAULT 0"
            )

        _sync_active_16_services(conn)
        conn.commit()


# ---------------------------------------------------------------------------
# Pricing, Min/Max & Service Display Helpers
# ---------------------------------------------------------------------------
def get_sell_rate_per_1k(s: sqlite3.Row) -> float:
    """
    Returns the exact sell price per 1000 that the customer pays.
    """
    sell_rate = float(s["rate_per_1k"] or 0.0)
    if sell_rate > 0:
        return round(sell_rate, 4)
    panel_rate = float(s["panel_rate"] or 0.0)
    markup = float(s["markup"] or 0.0)
    return round(panel_rate + markup, 4)


def calc_sell_price(s: sqlite3.Row, quantity: int) -> float:
    """Calculates customer total price = sell price per 1000 * quantity / 1000."""
    rate_1k = get_sell_rate_per_1k(s)
    return round((rate_1k * int(quantity)) / 1000.0, 2)


def calc_panel_cost(s: sqlite3.Row, quantity: int) -> float:
    """Calculates panel cost = panel_rate * quantity / 1000."""
    panel_rate = float(s["panel_rate"] or 0.0)
    if panel_rate <= 0:
        return 0.0
    return round((panel_rate * int(quantity)) / 1000.0, 4)


def get_refill_label(s: sqlite3.Row) -> str:
    """Returns the service's refill label (e.g. LifeTime refill, REFILL 365D, REFILL 30D, No Refill)."""
    try:
        rtype = str(s["refill_type"] or "").strip()
    except Exception:
        rtype = ""
    if rtype:
        return rtype
    return "Refill" if int(s["refill_enabled"] or 0) == 1 else "No Refill"


def get_avg_time_label(s: sqlite3.Row) -> str:
    """Returns the service's average delivery time display (defaults to 2-6 hours)."""
    try:
        avg_t = str(s["avg_time"] or "").strip()
    except Exception:
        avg_t = ""
    return avg_t or "2-6 hours"


def format_min_max(s: sqlite3.Row, admin_view: bool = False) -> str:
    """
    Formats Min and Max without inventing numbers when Min or Max was not specified.
    """
    mn = int(s["min_qty"] or 0)
    mx = int(s["max_qty"] or 0)
    parts = []
    if mn > 0:
        parts.append(f"Min: {mn}")
    elif admin_view:
        parts.append("Min: Editable in Admin")

    if mx > 0:
        parts.append(f"Max: {mx}")
    elif mx == -1:
        parts.append("Max: Unlimited")
    elif admin_view:
        parts.append("Max: Editable in Admin")

    return " | ".join(parts) if parts else ""


def format_user_service_block(s: sqlite3.Row) -> str:
    """
    Formats a single service block for normal users:
    Shows ONLY: service name, sell price, refill, Min, Max, average time 2-6 hours.
    Never shows panel rate, profit, or panel ID to normal users.
    """
    sell_rate = get_sell_rate_per_1k(s)
    sell_str = _fmt_num(sell_rate)
    refill_txt = get_refill_label(s)
    avg_t = get_avg_time_label(s)
    mn = int(s["min_qty"] or 0)
    mx = int(s["max_qty"] or 0)
    limit_parts = []
    if mn > 0:
        limit_parts.append(f"Min: <b>{mn}</b>")
    if mx > 0:
        limit_parts.append(f"Max: <b>{mx}</b>")
    elif mx == -1:
        limit_parts.append("Max: <b>Unlimited</b>")

    lines = [
        f"{platform_emoji_for(s['category'])} <b>{s['name']}</b>",
        f"💰 <b>মূল্য:</b> {format_price_1k_plain(s['name'], sell_str)}",
        f"🔄 <b>রিফিল:</b> {refill_txt}",
    ]
    if limit_parts:
        lines.append(f"📊 <b>লিমিট:</b> {' | '.join(limit_parts)}")
    lines.append(f"⏱️ <b>Average time:</b> {avg_t}")
    return "\n".join(lines)


def service_price_unit(name: str) -> str:
    """Plain unit word for the user-facing '1000 <Unit> <price> Taka' format."""
    n = (name or "").lower()
    if "reaction" in n:
        return "Reaction"
    if "members" in n or "member" in n:
        return "Members"
    if "followers" in n:
        return "Followers"
    if "likes" in n:
        return "Likes"
    if "views" in n:
        return "Views"
    if "shares" in n:
        return "Shares"
    if "impression" in n:
        return "Impression"
    return "Units"


def format_price_1k_plain(name: str, sell_str: str) -> str:
    """User-facing per-1000 price, e.g. '1000 Likes 30 Taka' (no ৳, no '=')."""
    return f"1000 {service_price_unit(name)} {sell_str} Taka"


def platform_emoji_for(category: str) -> str:
    """Distinct emoji per platform, consistent everywhere (button + heading).
    Categories already store these emojis, so this just falls back safely."""
    c = (category or "").lower()
    if "instagram" in c:
        return "📸"
    if "tiktok" in c:
        return "🎵"
    if "facebook" in c:
        return "📘"
    if "youtube" in c:
        return "▶️"
    if "telegram" in c:
        return "✈️"
    return "🔹"


def build_single_column_service_buttons(
    services: list[sqlite3.Row], include_back_to_cats: bool = False
) -> InlineKeyboardMarkup:
    """
    Builds single-column inline buttons (one service per row) with text:
    "TikTok Followers — 1000 Followers 180 Taka"
    """
    rows = []
    for s in services:
        sell_str = _fmt_num(get_sell_rate_per_1k(s))
        # Button text cannot be bold in Telegram, so emoji + price carry the look.
        btn_text = f"{platform_emoji_for(s['category'])} {s['name']} • {format_price_1k_plain(s['name'], sell_str)}"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"srv_{s['id']}")])
    if include_back_to_cats:
        rows.append(
            [InlineKeyboardButton("🔙 প্ল্যাটফর্ম তালিকায় ফিরুন", callback_data="open_cats")]
        )
    return InlineKeyboardMarkup(rows)


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


def _clean_force_channel(raw: str) -> str:
    """Clean a channel username: strip @, t.me/ links and whitespace. Returns '' if invalid."""
    v = (raw or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
            break
    v = v.strip().lstrip("@").strip().rstrip("/")
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_]{5,32}", v or ""):
        return ""
    return v


def get_force_channels() -> list:
    """Force channels set by admin (list), with env fallback. Never hardcoded."""
    import json as _json
    raw = get_setting("force_channel", "") or get_setting("force_channels", "")
    out = []
    if raw:
        raw = raw.strip()
        if raw.startswith("["):
            try:
                vals = _json.loads(raw)
                if isinstance(vals, list):
                    out = [_clean_force_channel(str(x)) for x in vals]
            except Exception:
                out = []
        else:
            out = [_clean_force_channel(x) for x in raw.split(",")]
    if not out:
        env_raw = os.environ.get("FORCE_CHANNEL", "") or os.environ.get("FORCE_JOIN_CHANNEL", "")
        if env_raw:
            out = [_clean_force_channel(x) for x in env_raw.split(",")]
    return [c for c in out if c]


def set_force_channels(channels: list) -> None:
    import json as _json
    cleaned = []
    for c in channels or []:
        cc = _clean_force_channel(str(c))
        if cc and cc not in cleaned:
            cleaned.append(cc)
    set_setting("force_channels", _json.dumps(cleaned))
    set_setting("force_channel", _json.dumps(cleaned))


def format_force_channels() -> str:
    chs = get_force_channels()
    if not chs:
        return "সেট করা নেই"
    return ", ".join(f"@{c}" for c in chs)


def ensure_user(user_id: int, username: str = "", first_name: str = "") -> sqlite3.Row:
    is_new = False
    with _db_lock, db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            is_new = True
            conn.execute(
                "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (user_id, username or "", first_name or "User"),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        else:
            new_uname = username or row["username"]
            new_fname = first_name or row["first_name"]
            if new_uname != row["username"] or new_fname != row["first_name"]:
                conn.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (new_uname, new_fname, user_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if is_new and row is not None:
        schedule_sheet_backup(
            user_id=user_id,
            username=row["username"] or row["first_name"] or "",
            balance=float(row["balance"] or 0.0),
            order_details="New User Joined",
        )
    return row


def get_balance(user_id: int) -> float:
    with _db_lock, db() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return round(float(row["balance"]), 2) if row else 0.0


def add_balance(
    user_id: int,
    amount: float,
    is_deposit: bool = False,
    reason: str = "Balance Updated",
) -> float:
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
        row = conn.execute(
            "SELECT username, first_name, balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        new_bal = round(float(row["balance"]), 2) if row else 0.0
        uname = (row["username"] or row["first_name"] or "") if row else ""

    sign = "+" if amount >= 0 else ""
    schedule_sheet_backup(
        user_id=user_id,
        username=uname,
        balance=new_bal,
        order_details=f"{reason} ({sign}৳{amount:.2f})",
    )
    return new_bal


def record_spend(user_id: int, amount: float) -> None:
    with _db_lock, db() as conn:
        conn.execute(
            "UPDATE users SET total_spent = ROUND(total_spent + ?, 2) WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()


async def credit_referral_on_completed_order(
    order_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Credits 10% of the referred user's COMPLETED order amount to their referrer.
    Never credits on join, and never credits twice for the same order.
    """
    with _db_lock, db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            return
        if int(order["refer_bonus_paid"] or 0) == 1:
            return
        if str(order["status"] or "").strip().lower() != "completed":
            return

        buyer = conn.execute(
            "SELECT user_id, first_name, referred_by FROM users WHERE user_id = ?",
            (order["buyer_id"],),
        ).fetchone()
        if not buyer:
            return
        ref_id = int(buyer["referred_by"] or 0)
        if ref_id <= 0 or ref_id == int(order["buyer_id"]):
            conn.execute(
                "UPDATE orders SET refer_bonus_paid = 1 WHERE id = ?", (order_id,)
            )
            conn.commit()
            return

        order_amount = float(order["sell_price"] or order["price"] or 0.0)
        bonus = round(order_amount * get_refer_bonus_rate(), 2)
        conn.execute("UPDATE orders SET refer_bonus_paid = 1 WHERE id = ?", (order_id,))
        if bonus > 0:
            conn.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 2), "
                "refer_earned = ROUND(refer_earned + ?, 2) "
                "WHERE user_id = ?",
                (bonus, bonus, ref_id),
            )
        conn.commit()
        ref_row = conn.execute(
            "SELECT username, first_name, balance FROM users WHERE user_id = ?",
            (ref_id,),
        ).fetchone()

    if bonus > 0 and ref_row:
        schedule_sheet_backup(
            user_id=ref_id,
            username=ref_row["username"] or ref_row["first_name"] or "",
            balance=float(ref_row["balance"] or 0.0),
            order_details=f"10% Referral Bonus (+৳{bonus:.2f}) from Completed Order #SMM-{order_id}",
        )
        try:
            await context.bot.send_message(
                chat_id=ref_id,
                text=(
                    "🎁 <b>রেফারেল ১০% অর্ডার কমিশন!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 আপনার রেফার করা ইউজার একটি অর্ডার সম্পন্ন (Completed) করেছে।\n"
                    f"💰 অর্ডার মূল্যের ১০% বোনাস: <b>+৳{bonus:.2f}</b> আপনার ওয়ালেটে যোগ হয়েছে!\n"
                    f"💳 বর্তমান ব্যালেন্স: <b>৳{float(ref_row['balance']):.2f}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass


def list_services(category: str | None = None, include_disabled: bool = False) -> list[sqlite3.Row]:
    with _db_lock, db() as conn:
        if category:
            if include_disabled:
                return conn.execute(
                    "SELECT * FROM services WHERE category = ? ORDER BY id ASC", (category,)
                ).fetchall()
            return conn.execute(
                "SELECT * FROM services WHERE category = ? AND is_active = 1 ORDER BY id ASC",
                (category,),
            ).fetchall()
        if include_disabled:
            return conn.execute("SELECT * FROM services ORDER BY id ASC").fetchall()
        return conn.execute(
            "SELECT * FROM services WHERE is_active = 1 ORDER BY id ASC"
        ).fetchall()


def list_categories(include_disabled: bool = False) -> list[str]:
    with _db_lock, db() as conn:
        if include_disabled:
            rows = conn.execute("SELECT DISTINCT category FROM services ORDER BY id ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT category FROM services WHERE is_active = 1 ORDER BY id ASC"
            ).fetchall()
        return [r["category"] for r in rows]


def get_service(service_id: int) -> sqlite3.Row | None:
    with _db_lock, db() as conn:
        return conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()


def get_user_completed_amount(user_id: int) -> float:
    """Returns total COMPLETED order amount only for a user."""
    with _db_lock, db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN sell_price > 0 THEN sell_price ELSE price END), 0.0)
            FROM orders
            WHERE buyer_id = ? AND LOWER(status) IN ('completed', 'approved')
            """,
            (user_id,),
        ).fetchone()
        return round(float(row[0] or 0.0), 2) if row else 0.0


def get_user_completed_order_count(user_id: int) -> int:
    """Returns total COMPLETED order count only for a user (status completed/approved)."""
    with _db_lock, db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE buyer_id = ? AND LOWER(status) IN ('completed', 'approved')
            """,
            (user_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0


def vip_badge(completed_orders: int) -> str:
    """VIP rank by COMPLETED ORDER COUNT only (never taka amount)."""
    if completed_orders >= 1000:
        return "👑 VVIP Legend"
    if completed_orders >= 500:
        return "💎 Royal Diamond"
    if completed_orders >= 300:
        return "🥇 Gold VIP"
    if completed_orders >= 100:
        return "🥈 Silver Pro"
    return "🥉 Bronze Member"


def vip_discount(completed_orders: int) -> int:
    """
    Returns VIP discount % based on total COMPLETED ORDER COUNT only:
    Silver Pro (>= 100 orders) = 3%
    Gold VIP (>= 300 orders) = 5%
    Royal Diamond (>= 500 orders) = 7%
    VVIP Legend (>= 1000 orders) = 10%
    """
    if completed_orders >= 1000:
        return 10
    if completed_orders >= 500:
        return 7
    if completed_orders >= 300:
        return 5
    if completed_orders >= 100:
        return 3
    return 0


def get_vip_info(user_id: int) -> dict:
    """
    Returns VIP status details based on total COMPLETED ORDER COUNT only:
    current rank, total completed orders, next rank + remaining orders, and current discount %.
    Never uses a taka amount as the rank requirement.
    """
    comp_orders = get_user_completed_order_count(user_id)
    rank = vip_badge(comp_orders)
    disc_pct = vip_discount(comp_orders)
    tiers = [
        (100, "🥈 Silver Pro"),
        (300, "🥇 Gold VIP"),
        (500, "💎 Royal Diamond"),
        (1000, "👑 VVIP Legend"),
    ]
    next_rank = None
    remaining_orders = 0
    for threshold, r_name in tiers:
        if comp_orders < threshold:
            next_rank = r_name
            remaining_orders = int(threshold - comp_orders)
            break
    return {
        "rank": rank,
        "completed_orders": comp_orders,
        "discount_pct": disc_pct,
        "next_rank": next_rank,
        "remaining_orders": remaining_orders,
    }


def format_vip_section(user_id: int) -> str:
    v = get_vip_info(user_id)
    if v["next_rank"]:
        next_line = f"{v['next_rank']} (আরও <b>{v['remaining_orders']}টি অর্ডার</b> প্রয়োজন)"
    else:
        next_line = "👑 সর্বোচ্চ র‍্যাংক অর্জিত (বাকি: ০টি অর্ডার)"
    return (
        "👑 <b>ভিআইপি স্ট্যাটাস (VIP Section):</b>\n"
        f"🏆 <b>Current Rank:</b> {v['rank']}\n"
        f"✅ <b>Total Completed Orders:</b> {v['completed_orders']}টি\n"
        f"🎯 <b>Next Rank:</b> {next_line}\n"
        f"🎁 <b>Current Discount:</b> <b>{v['discount_pct']}%</b>"
    )


def calc_vip_order_price(
    s: sqlite3.Row, quantity: int, user_id: int
) -> tuple[float, int, float]:
    """
    Applies VIP discount (based on total COMPLETED ORDER COUNT only) to sell price at order time.
    Returns (original_price, discount_pct, final_price).
    """
    original_price = calc_sell_price(s, quantity)
    comp_orders = get_user_completed_order_count(user_id)
    disc_pct = vip_discount(comp_orders)
    if disc_pct > 0:
        final_price = round(original_price * (100.0 - disc_pct) / 100.0, 2)
    else:
        final_price = original_price
    return (original_price, disc_pct, final_price)


def deposit_bkash_text() -> str:
    """bKash-only number line for the deposit flow (Paid File bot style)."""
    bkash = get_setting("bkash_number") or os.environ.get("BKASH_NUMBER", "").strip()
    if bkash:
        return f"🌸 <b>bKash Personal (Send Money):</b> <code>{bkash}</code>"
    return "🌸 <b>bKash Personal (Send Money):</b> অ্যাডমিন প্যানেল থেকে নাম্বার সেট করুন।"


# Deposit methods for the USER deposit flow (Binance is admin-only, never here).
DEPOSIT_METHODS = {
    "bkash": ("🌸", "bKash", "bKash Personal (Send Money)", "bkash_number", "BKASH_NUMBER"),
    "nagad": ("🧡", "Nagad", "Nagad Personal (Send Money)", "nagad_number", "NAGAD_NUMBER"),
    "rocket": ("💜", "Rocket", "Rocket Personal (Send Money)", "rocket_number", "ROCKET_NUMBER"),
}


def deposit_method_enabled(key: str) -> bool:
    """Admin toggle: deposit_<key>_enabled, default ON (1) when unset."""
    return get_setting(f"deposit_{key}_enabled", "1") != "0"


def deposit_enabled_methods() -> list:
    return [k for k in DEPOSIT_METHODS if deposit_method_enabled(k)]


def deposit_available_methods() -> list:
    """Paid File available_methods(): enabled AND a number is set (setting or env)."""
    return [k for k in deposit_enabled_methods() if deposit_method_number(k)]


def deposit_method_label(key: str) -> str:
    return DEPOSIT_METHODS.get(key, DEPOSIT_METHODS["bkash"])[2]


def deposit_method_number(key: str) -> str:
    _emoji, _name, _label, setting_key, env_key = DEPOSIT_METHODS.get(key, DEPOSIT_METHODS["bkash"])
    return get_setting(setting_key) or os.environ.get(env_key, "").strip()


def deposit_method_text(key: str) -> str:
    """Only the chosen method's number line (Paid File bot style)."""
    emoji, name, label, _sk, _ek = DEPOSIT_METHODS.get(key, DEPOSIT_METHODS["bkash"])
    num = deposit_method_number(key)
    if num:
        return f"{emoji} <b>{label}:</b> <code>{num}</code>"
    return f"{emoji} <b>{label}:</b> অ্যাডমিন প্যানেল থেকে নাম্বার সেট করুন।"



_METHOD_BN = {
    "bkash": ("🌸", "বিকাশ"),
    "nagad": ("🧡", "নগদ"),
    "rocket": ("💜", "রকেট"),
}


def deposit_method_bn(key: str) -> str:
    """Bangla method name (Paid File style)."""
    return _METHOD_BN.get(key, _METHOD_BN["bkash"])[1]


def deposit_method_instructions(key: str, amt: float) -> str:
    """Paid File style: chosen method number + Send Money (Personal) rules + divider + TrxID prompt."""
    emoji, bn = _METHOD_BN.get(key, _METHOD_BN["bkash"])
    return (
        f"{emoji} <b>{bn} পার্সোনাল ডিপোজিট (Send Money)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>ডিপোজিট পরিমাণ:</b> ৳{amt:.2f}\n"
        f"{deposit_method_text(key)}\n\n"
        "📌 <b>পেমেন্ট করার নিয়ম:</b>\n"
        f"১. উপরের নম্বরে ঠিক ৳{amt:.2f} Send Money করুন।\n"
        "২. সেন্ড মানি সফল হলে প্রাপ্ত মেসেজ থেকে TrxID কপি করুন।\n"
        "৩. নিচে আপনার TrxID টি লিখে পাঠিয়ে দিন।\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ টাকা পাঠানোর পর অ্যাডমিন ম্যানুয়ালি যাচাই করে ব্যালেন্স যোগ করে দেবে!\n\n"
        f"দয়া করে আপনার {bn} এর TrxID টি নিচে লিখে পাঠান:"
    )


def is_trxid_used(trxid: str) -> bool:
    """Duplicate TrxID check (Paid File style): a TrxID counts as used unless its earlier request was rejected."""
    try:
        with _db_lock, db() as conn:
            row = conn.execute(
                "SELECT 1 FROM balance_requests WHERE trxid = ? AND status != 'rejected' LIMIT 1",
                (trxid.strip().upper(),),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def get_refer_bonus_pct() -> float:
    """Refer bonus % from settings, default 10% (unchanged unless admin sets it)."""
    try:
        v = float(get_setting("refer_bonus_pct", "10") or 10)
    except (TypeError, ValueError):
        return 10.0
    if v < 0 or v > 100:
        return 10.0
    return v


def get_refer_bonus_rate() -> float:
    return get_refer_bonus_pct() / 100.0


def payment_numbers_text() -> str:
    bkash = get_setting("bkash_number") or os.environ.get("BKASH_NUMBER", "").strip()
    nagad = get_setting("nagad_number") or os.environ.get("NAGAD_NUMBER", "").strip()
    rocket = get_setting("rocket_number") or os.environ.get("ROCKET_NUMBER", "").strip()
    binance = get_setting("binance_id") or os.environ.get("BINANCE_ID", "").strip()
    lines = []
    if bkash:
        lines.append(f"🌸 <b>bKash Personal (Send Money):</b> <code>{bkash}</code>")
    if nagad:
        lines.append(f"🧡 <b>Nagad Personal (Send Money):</b> <code>{nagad}</code>")
    if rocket:
        lines.append(f"💜 <b>Rocket Personal (Send Money):</b> <code>{rocket}</code>")
    if binance:
        lines.append(f"🟡 <b>Binance Pay ID / USDT:</b> <code>{binance}</code>")
    if not lines:
        return "🌸 <b>bKash Personal (Send Money):</b> অ্যাডমিন প্যানেল থেকে নাম্বার সেট করুন।"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Menu: অর্ডার করুন, ডিপোজিট, আমার অর্ডার, প্রোফাইল, রেফারেল, সাপোর্ট, লিডারবোর্ড (+ অ্যাডমিন প্যানেল for admin only)
# NOTE: No standalone Service List button on the main menu (removed 2026-10-29);
# services are still picked inside the অর্ডার করুন flow (platform > service).
# ---------------------------------------------------------------------------
def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [_mkb(BTN_NEW_ORDER, "primary")],
        [_mkb(BTN_DEPOSIT, "success"), _mkb(BTN_MY_ORDERS, "primary")],
        [_mkb(BTN_PROFILE, "primary"), _mkb(BTN_REFER, "success")],
        [_mkb(BTN_SUPPORT, "primary"), _mkb(BTN_LEADERBOARD, "success")],
    ]
    if is_admin(user_id):
        rows.append([_mkb(BTN_ADMIN, "primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu() -> ReplyKeyboardMarkup:
    rows = [
        [_mkb(BTN_A_ADD_SMM, "success"), _mkb(BTN_A_EDIT_SMM, "primary")],
        [_mkb(BTN_A_USERS, "success"), _mkb(BTN_A_LIST, "primary")],
        [_mkb(BTN_A_DEL, "danger"), _mkb(BTN_A_ORDERS, "danger")],
        [_mkb(BTN_A_DEPOSITS, "success"), _mkb(BTN_A_BALANCE, "primary")],
        [_mkb(BTN_A_PAYMENTS, "success"), _mkb(BTN_A_SETTINGS, "primary")],
        [_mkb(BTN_A_BROADCAST, "danger"), _mkb(BTN_A_STATS, "success")],
        [_mkb(BTN_BACK, "primary")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[_mkb(BTN_CANCEL, "danger")]], resize_keyboard=True)


async def _restore_user_menu(context, user_id: int) -> None:
    """Re-send the reply keyboard after a terminal callback edit.

    A callback edit cannot carry a reply keyboard (Bot API editMessageText
    accepts only an inline keyboard), so the menu is restored with a fresh
    message carrying main_menu -- the same pattern already used for deposit
    cancel/submit. Reply keyboard only.
    """
    await context.bot.send_message(
        chat_id=user_id,
        text="👇 আপনার মেইন মেনু:",
        reply_markup=main_menu(user_id),
    )


async def _restore_admin_menu(context, user_id: int) -> None:
    await context.bot.send_message(
        chat_id=user_id,
        text="👇 অ্যাডমিন মেনু:",
        reply_markup=admin_menu(),
    )


# ---------------------------------------------------------------------------
# Force Channel (Force Join) — multiple channels, admin-set only, never hardcoded
# ---------------------------------------------------------------------------
async def _check_force_memberships(context, user_id: int):
    """Returns (ok, missing, failed). Admins and no-channel setups always pass."""
    if is_admin(user_id):
        return True, [], []
    channels = get_force_channels()
    if not channels:
        return True, [], []
    missing, failed = [], []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=f"@{ch}", user_id=user_id)
            status = str(getattr(member, "status", "") or "")
            if status in ("member", "administrator", "creator", "restricted"):
                continue
            missing.append(ch)
        except Exception:
            failed.append(ch)
    return (not missing and not failed), missing, failed


async def _show_force_join_prompt(update, context, missing, failed, note=""):
    rows = []
    for ch in missing:
        rows.append([InlineKeyboardButton(f"📢 জয়েন করুন: @{ch}", url=f"https://t.me/{ch}")])
    rows.append([InlineKeyboardButton("✅ আমি জয়েন করেছি", callback_data="force_check")])
    kb = InlineKeyboardMarkup(rows)
    lines = ["📢 <b>প্রথমে আমাদের চ্যানেলগুলোতে জয়েন করুন</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
    if note:
        lines.append(note)
    if missing:
        lines.append("👇 নিচের চ্যানেলগুলোতে জয়েন করুন:")
        for ch in missing:
            lines.append(f"• @{ch}")
    if failed:
        lines.append("")
        lines.append("⚠️ <b>চেক করা যাচ্ছে না:</b> নিচের চ্যানেলগুলোর সদস্যতা যাচাই করা যাচ্ছে না। অ্যাডমিনকে বটকে ওই চ্যানেলের অ্যাডমিন করতে বলুন:")
        for ch in failed:
            lines.append(f"• @{ch}")
        lines.append("অ্যাডমিন ঠিক করলে আবার ✅ আমি জয়েন করেছি চাপুন। অ্যাডমিনরা এই চেক ছাড়াই ব্যবহার করতে পারবেন।")
    lines.append("")
    lines.append("জয়েন করে ✅ আমি জয়েন করেছি বাটনে চাপুন।")
    text = "\n".join(lines)
    if update.callback_query:
        try:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
        await context.bot.send_message(chat_id=update.effective_user.id, text=text, parse_mode="HTML", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def ensure_force_join(update, context) -> bool:
    """True if the user may proceed; otherwise shows the join prompt and returns False."""
    ok, missing, failed = await _check_force_memberships(context, update.effective_user.id)
    if ok:
        return True
    await _show_force_join_prompt(update, context, missing, failed)
    return False


async def handle_force_check_cb(update, context) -> None:
    query = update.callback_query
    if query is None:
        return
    ok, missing, failed = await _check_force_memberships(context, query.from_user.id)
    if ok:
        await show_main_dashboard(update, context)
        return
    note = ""
    if missing:
        note = "❌ এখনো সব চ্যানেলে জয়েন হয়নি।"
    await _show_force_join_prompt(update, context, missing, failed, note=note)


# ---------------------------------------------------------------------------
# Main Welcome & Dashboard (/start) — No Quick Action Panel
# ---------------------------------------------------------------------------
async def show_main_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    with _db_lock, db() as conn:
        order_cnt = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ?", (user.id,)
        ).fetchone()[0]

    comp_orders = get_user_completed_order_count(user.id)
    badge = vip_badge(comp_orders)
    text = (
        f"👑 <b>স্বাগতম, {user.first_name}!</b>\n"
        "─────────\n"
        "🌟 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "স্বয়ংক্রিয় সোশ্যাল মিডিয়া বুস্ট প্যানেলে আপনাকে স্বাগতম!\n\n"
        "📊 <b>আপনার অ্যাকাউন্ট ড্যাশবোর্ড:</b>\n"
        f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        f"💳 <b>বর্তমান ব্যালেন্স:</b> <b>৳{u['balance']:.2f}</b>\n"
        f"📦 <b>মোট অর্ডার:</b> {order_cnt} টি\n"
        f"🏆 <b>ভিআইপি র‍্যাংক:</b> {badge}\n\n"
        "🔥 <b>অ্যাক্টিভ প্ল্যাটফর্মসমূহ (⏱️ Average time: 2-6 hours):</b>\n"
        "📸 <b>Instagram</b> • 🎵 <b>TikTok</b> • 📘 <b>Facebook</b> • ▶️ <b>YouTube</b> • ✈️ <b>Telegram</b>\n"
        "─────────\n"
        "👇 শুরু করতে নিচের মেনু থেকে বাটন সিলেক্ট করুন:"
    )

    if update.message:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_menu(user.id)
        )
    elif update.callback_query:
        # Callback edits cannot carry a reply keyboard (Bot API editMessageText
        # accepts only an inline keyboard). Send the dashboard as a fresh
        # message with the reply keyboard instead of editing it away -- this
        # is the dashboard itself, not a second backup message, and it uses
        # the reply keyboard only.
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=text, parse_mode="HTML", reply_markup=main_menu(user.id)
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    user = update.effective_user
    with _db_lock, db() as conn:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user.id,)
        ).fetchone()

    ensure_user(user.id, user.username or "", user.first_name or "User")

    # Handle Referral Deep-Link (/start ref_12345) for brand new users:
    # Links referred_by and increments refer_count ONLY. Never pays bonus on join!
    if existing is None and context.args:
        arg = str(context.args[0]).strip()
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg.replace("ref_", "", 1))
            except ValueError:
                ref_id = 0
            if ref_id and ref_id != user.id:
                ensure_user(ref_id)
                with _db_lock, db() as conn:
                    conn.execute(
                        "UPDATE users SET referred_by = ? WHERE user_id = ?",
                        (ref_id, user.id),
                    )
                    conn.execute(
                        "UPDATE users SET refer_count = refer_count + 1 WHERE user_id = ?",
                        (ref_id,),
                    )
                    conn.commit()

    if not await ensure_force_join(update, context):
        return
    await show_main_dashboard(update, context)


# ---------------------------------------------------------------------------
# SMM Categories & Easy-to-Read Service Listing
# ---------------------------------------------------------------------------
def build_categories_keyboard() -> InlineKeyboardMarkup:
    """Platform-selection grid for step 1/5 of the order flow (DMR-style layout).

    Grid (button labels and callback_data UNCHANGED, only the row layout changes):
        Row 1: Facebook | Telegram
        Row 2: Instagram | TikTok
        Row 3: YouTube (alone)
        Row 4: প্রধান মেনু (full width)
    Callback data stays f"cat_{idx}" where idx is the category's position in
    list_categories(), so the existing cat_ handler routing is untouched.
    A category that is disabled/missing is simply skipped; any unknown
    category is appended as its own row before প্রধান মেনু, so no platform
    can silently disappear from this keyboard.
    """
    cats = list_categories(include_disabled=False)
    by_platform: dict[str, tuple[int, str]] = {}
    unknown: list[tuple[int, str]] = []
    for idx, c in enumerate(cats):
        low = (c or "").lower()
        key = None
        for name in ("facebook", "telegram", "instagram", "tiktok", "youtube"):
            if name in low:
                key = name
                break
        if key is None:
            unknown.append((idx, c))
        elif key not in by_platform:
            by_platform[key] = (idx, c)
    rows: list[list] = []
    for pair in (("facebook", "telegram"), ("instagram", "tiktok"), ("youtube",)):
        row = []
        for name in pair:
            if name in by_platform:
                idx, c = by_platform[name]
                row.append(InlineKeyboardButton(c, callback_data=f"cat_{idx}"))
        if row:
            rows.append(row)
    for idx, c in unknown:
        rows.append([InlineKeyboardButton(c, callback_data=f"cat_{idx}")])
    rows.append([InlineKeyboardButton("প্রধান মেনু", callback_data="open_main_menu")])
    return InlineKeyboardMarkup(rows)


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cats = list_categories(include_disabled=False)
    if not cats:
        msg = "⚠️ বর্তমানে কোনো সার্ভিস চালু নেই। অ্যাডমিন সার্ভিস অ্যাড করলে এখানে দেখা যাবে।"
        if update.message:
            await update.message.reply_text(msg, reply_markup=main_menu(update.effective_user.id))
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg)
            await _restore_user_menu(context, update.effective_user.id)
        return

    text = (
        "🚀 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛒 <b>অর্ডার করুন — ধাপ ১/৫: প্ল্যাটফর্ম বাছুন</b>\n"
        "⏱️ <b>Average time:</b> 2-6 hours\n\n"
        "👇 আপনি কোন প্ল্যাটফর্মের সার্ভিস নিতে চান? নিচ থেকে সিলেক্ট করুন:"
    )
    kb = build_categories_keyboard()
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


async def show_all_services_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    User Service List (📋 সার্ভিস লিস্ট):
    - One service per block, blank line between services.
    - Normal users see ONLY: service name, sell price, refill, Min, Max, average time 2-6 hours.
    - Never shows panel rate, profit, or panel ID.
    - Single-column service buttons: one service per row, text = "TikTok Followers — 1000 Followers 180 Taka".
    - Does NOT repeat the 4 platform buttons under the service list.
    """
    user_id = update.effective_user.id
    services = list_services(include_disabled=False)
    if not services:
        await update.message.reply_text(
            "কোনো সার্ভিস নেই।", reply_markup=main_menu(user_id)
        )
        return

    sections = [
        "📋 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 <b>সার্ভিস লিস্ট</b>\n"
        "💰 দাম প্রতি ১০০০টির জন্য হিসাব করা হয়\n"
        "⏱️ <b>Average time:</b> 2-6 hours"
    ]
    current_cat = None
    cat_blocks = []
    for s in services:
        if s["category"] != current_cat:
            if cat_blocks:
                sections.append("\n\n".join(cat_blocks))
                cat_blocks = []
            current_cat = s["category"]
            sections.append(f"\n<b>{current_cat}</b>\n━━━━━━━━━━━━━━━━━━━━━━")
        cat_blocks.append(format_user_service_block(s))
    if cat_blocks:
        sections.append("\n\n".join(cat_blocks))

    sections.append("━━━━━━━━━━━━━━━━━━━━━━\n👇 অর্ডার করতে নিচের সার্ভিস বাটনে চাপুন:")
    text = "\n".join(sections)
    kb = build_single_column_service_buttons(services, include_back_to_cats=False)
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )


async def admin_show_all_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin Service List (📋 সব সার্ভিস দেখুন in Admin Panel):
    Shows Panel Service ID, Panel Rate, Sell Price, Profit, Refill, Min, Max, Average time.
    """
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    services = list_services(include_disabled=True)
    if not services:
        await update.message.reply_text(
            "কোনো সার্ভিস নেই।", reply_markup=admin_menu()
        )
        return

    blocks = [
        "📋 <b>ADMIN SERVICE LIST — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "⏱️ <b>Average time:</b> 2-6 hours\n"
        "─────────"
    ]
    current_cat = None
    for s in services:
        if s["category"] != current_cat:
            current_cat = s["category"]
            blocks.append(f"\n<b>{current_cat}</b>\n─────────")
        sell_rate = get_sell_rate_per_1k(s)
        sell_str = _fmt_num(sell_rate)
        p_rate = float(s["panel_rate"] or 0.0)
        profit_1k = round(sell_rate - p_rate, 4) if p_rate > 0 else 0.0
        pid = int(s["panel_service_id"] or 0)
        refill_txt = get_refill_label(s)
        avg_t = get_avg_time_label(s)
        mm_txt = format_min_max(s, admin_view=True)
        active_tag = "" if int(s["is_active"] or 0) == 1 else " [DISABLED]"
        blocks.append(
            f"🔹 <b>#{s['id']} {s['name']}</b>{active_tag}\n"
            f"🔗 <b>Panel ID:</b> <code>#{pid}</code>\n"
            f"💵 <b>Panel Rate:</b> {_fmt_num(p_rate)}/1000 | 💰 <b>Sell:</b> ৳{sell_str}/1000 | 📈 <b>Profit:</b> ৳{_fmt_num(profit_1k)}/1000\n"
            f"🔄 <b>Refill:</b> {refill_txt} | ⏱️ <b>Avg:</b> {avg_t}\n"
            f"📊 {mm_txt}"
        )

    await update.message.reply_text(
        "\n\n".join(blocks),
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


# ---------------------------------------------------------------------------
# 3. ORDER FLOW (Steps 1 to 7)
# ---------------------------------------------------------------------------
async def handle_category_And_service_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "open_main_menu":
        # Main Menu from the platform grid: show_main_dashboard's callback
        # branch strips the old message's inline buttons and sends the
        # dashboard as a fresh message carrying the reply keyboard (an
        # edited message cannot carry a reply keyboard).
        await show_main_dashboard(update, context)
        return

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
        cats = list_categories(include_disabled=False)
        if idx < 0 or idx >= len(cats):
            return
        cat_name = cats[idx]
        svcs = list_services(cat_name, include_disabled=False)
        service_blocks = [format_user_service_block(s) for s in svcs]
        text = (
            "🚀 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{cat_name} — ধাপ ২/৫: সার্ভিস বাছুন</b>\n"
            "💰 দাম প্রতি ১০০০টির জন্য হিসাব করা হয়\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            + "\n\n".join(service_blocks)
            + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n👇 অর্ডার করতে নিচের সার্ভিস বাটনে চাপুন:"
        )
        kb = build_single_column_service_buttons(svcs, include_back_to_cats=True)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # Step 1: User selects service -> check panel_service_id is not 0 and Panel API is set
    if data.startswith("srv_"):
        try:
            sid = int(data.replace("srv_", "", 1))
        except ValueError:
            return
        s = get_service(sid)
        if not s or int(s["is_active"] or 0) == 0:
            await query.edit_message_text("⚠️ সার্ভিসটি বর্তমানে চালু নেই।")
            await _restore_user_menu(context, query.from_user.id)
            return

        pid = int(s["panel_service_id"] or 0)
        if pid <= 0:
            await query.edit_message_text(
                "⚠️ এই সার্ভিসটি বর্তমানে অর্ডারের জন্য উপলব্ধ নয়।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 ক্যাটাগরি লিস্টে ফিরুন", callback_data="open_cats")]]
                ),
            )
            return

        # Check SMM_API_URL and SMM_API_KEY
        api_url, api_key = _get_smm_api_config()
        if not api_url or not api_key:
            await query.edit_message_text(
                "⚠️ <b>Panel API not set - contact admin</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 ক্যাটাগরি লিস্টে ফিরুন", callback_data="open_cats")]]
                ),
            )
            return

        sell_rate = get_sell_rate_per_1k(s)
        sell_str = _fmt_num(sell_rate)
        refill_txt = get_refill_label(s)
        avg_t = get_avg_time_label(s)
        mm_txt = format_min_max(s, admin_view=False)
        req_plat = detect_required_platform(s["category"], s["name"])
        plat_hint = f" ({req_plat[0]})" if req_plat else ""
        if "telegram" in (s["category"] or "").lower():
            if "members" in (s["name"] or "").lower():
                tg_hint = "\n🔗 <b>লিংক টাইপ:</b> চ্যানেল/গ্রুপ লিংক (যেমন: https://t.me/ChannelName)"
            else:
                tg_hint = "\n🔗 <b>লিংক টাইপ:</b> পোস্ট লিংক (যেমন: https://t.me/ChannelName/123) — চ্যানেল লিংক নয়"
        else:
            tg_hint = ""
        bot_data_line = (
            "🤖 <b>টাইপ:</b> Bot Data\n"
            if "bot data" in (s["name"] + " " + s["description"]).lower()
            else ""
        )

        context.user_data.clear()
        context.user_data["state"] = ST_ORDER_LINK
        context.user_data["order_service_id"] = s["id"]
        limit_line = f"📊 <b>লিমিট:</b> {mm_txt}\n" if mm_txt else ""
        text = (
            f"{platform_emoji_for(s['category'])} <b>{s['name']}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>মূল্য:</b> <b>{format_price_1k_plain(s['name'], sell_str)}</b>\n"
            f"🔄 <b>রিফিল:</b> {refill_txt}\n"
            f"{limit_line}"
            f"⏱️ <b>Average time:</b> {avg_t}\n"
            f"{bot_data_line}"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>ধাপ ৩/৫: টার্গেট লিংক{plat_hint}</b> পাঠান:{tg_hint}\n"
            "<i>(লিংক অবশ্যই http:// বা https:// দিয়ে শুরু হতে হবে এবং পাবলিক থাকতে হবে)</i>"
        )
        await query.edit_message_text(text, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="🔗 নিচের বক্সে আপনার লিংকটি পেস্ট করে পাঠান (বাতিল করতে ❌ বাতিল করুন চাপুন):",
            reply_markup=cancel_menu(),
        )
        return

    # Step 5, 6, 7: Order Confirm / Cancel Callback
    if data == "order_cancel_cb":
        context.user_data.clear()
        await query.edit_message_text("❌ অর্ডারটি বাতিল করা হয়েছে। আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি।")
        await _restore_user_menu(context, query.from_user.id)
        return

    if data == "order_confirm_cb":
        user = query.from_user
        if context.user_data.get("state") != ST_ORDER_CONFIRM:
            await query.edit_message_text("⚠️ অর্ডার সেশনটির মেয়াদ শেষ হয়েছে। অনুগ্রহ করে নতুন করে অর্ডার করুন।")
            await _restore_user_menu(context, query.from_user.id)
            return

        sid = int(context.user_data.get("order_service_id", 0))
        link = str(context.user_data.get("order_link", "")).strip()
        qty = int(context.user_data.get("order_qty", 0))
        context.user_data.clear()

        s = get_service(sid)
        if not s or int(s["is_active"] or 0) == 0:
            await query.edit_message_text("⚠️ সার্ভিসটি পাওয়া যায়নি। কোনো টাকা কাটা হয়নি।")
            await _restore_user_menu(context, query.from_user.id)
            return

        pid = int(s["panel_service_id"] or 0)
        if pid <= 0:
            await query.edit_message_text("⚠️ সার্ভিসটি প্যানেলের সাথে যুক্ত নয়। কোনো টাকা কাটা হয়নি।")
            await _restore_user_menu(context, query.from_user.id)
            return

        # Check API env vars first
        api_url, api_key = _get_smm_api_config()
        if not api_url or not api_key:
            await query.edit_message_text(
                "⚠️ <b>Panel API not set - contact admin</b>\nআপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি।",
                parse_mode="HTML",
            )
            await _restore_user_menu(context, query.from_user.id)
            return

        original_price, disc_pct, sell_price = calc_vip_order_price(s, qty, user.id)
        panel_cost = calc_panel_cost(s, qty)

        # Step 5a: Check user wallet balance
        user_bal = get_balance(user.id)
        if user_bal < sell_price:
            needed = round(sell_price - user_bal, 2)
            await query.edit_message_text(
                "⚠️ <b>আপনার ওয়ালেটে পর্যাপ্ত ব্যালেন্স নেই!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 সার্ভিস: {s['name']}\n"
                f"🔢 পরিমাণ: {qty}\n"
                f"💰 প্রয়োজন: <b>৳{sell_price:.2f}</b>\n"
                f"💳 আপনার ব্যালেন্স: <b>৳{user_bal:.2f}</b>\n"
                f"❗ আরও প্রয়োজন: <b>৳{needed:.2f}</b>\n\n"
                "আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি। দয়া করে আগে ডিপোজিট করুন।",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💰 এখনই ডিপোজিট করুন", callback_data="open_deposit")]]
                ),
            )
            return

        # Step 5b: Check panel_balance() is enough for panel_cost
        pbal_res = panel_balance()
        if not pbal_res.get("ok"):
            await query.edit_message_text(
                f"⚠️ <b>প্যানেল ব্যালেন্স যাচাই করা যায়নি:</b> {pbal_res.get('error')}\n"
                "আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি।",
                parse_mode="HTML",
            )
            await _restore_user_menu(context, query.from_user.id)
            return

        if float(pbal_res.get("balance", 0.0)) < panel_cost or (
            panel_cost == 0.0 and float(pbal_res.get("balance", 0.0)) <= 0.0
        ):
            await query.edit_message_text(
                "⚠️ <b>সার্ভার প্যানেলে পর্যাপ্ত ব্যালেন্স নেই। অ্যাডমিনের সাথে যোগাযোগ করুন।</b>\n"
                "আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি।",
                parse_mode="HTML",
            )
            await _restore_user_menu(context, query.from_user.id)
            return

        # Step 6: Call panel_add_order()
        await query.edit_message_text("⏳ প্যানেলে অর্ডার পাঠানো হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
        add_res = panel_add_order(pid, link, qty)
        if not add_res.get("ok"):
            err_msg = add_res.get("error", "Unknown panel error")
            await query.edit_message_text(
                f"❌ <b>প্যানেল অর্ডার গ্রহণ করেনি:</b>\n<code>{err_msg}</code>\n\n"
                "💳 আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি।",
                parse_mode="HTML",
            )
            await _restore_user_menu(context, query.from_user.id)
            return

        panel_oid = int(add_res["order"])

        # Step 7: Only after panel success -> deduct wallet, save order, notify admin, backup to Google Sheets
        new_bal = add_balance(
            user.id, -sell_price, reason=f"Order Placed ({s['name']} x{qty})"
        )
        record_spend(user.id, sell_price)
        with _db_lock, db() as conn:
            cur = conn.execute(
                """
                INSERT INTO orders
                (service_id, service_name, buyer_id, buyer_name, target_link, quantity,
                 price, status, panel_order_id, panel_service_id, panel_cost, sell_price,
                 starts_count, remains, refunded, refer_bonus_paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, 0, ?, 0, 0)
                """,
                (
                    s["id"],
                    s["name"],
                    user.id,
                    user.first_name or "User",
                    link,
                    qty,
                    sell_price,
                    panel_oid,
                    pid,
                    panel_cost,
                    sell_price,
                    qty,
                ),
            )
            order_id = cur.lastrowid
            conn.commit()

        schedule_order_sheet_backup(order_id)

        await query.edit_message_text(
            "🎉 <b>আপনার অর্ডার সফলভাবে সাবমিট হয়েছে!</b>\n"
            "─────────\n"
            f"🧾 <b>Order ID:</b> <code>#SMM-{order_id}</code>\n"
            f"📦 <b>সার্ভিস:</b> {s['name']}\n"
            f"🔗 <b>লিংক:</b> <code>{link}</code>\n"
                        f"🔢 <b>পরিমাণ:</b> {qty}\n"
            f"💵 <b>Original price:</b> ৳{original_price:.2f} | 🎁 <b>Discount:</b> {disc_pct}%\n"
            f"💰 <b>Final price (মোট চার্জ):</b> ৳{sell_price:.2f}\n"
            f"💳 <b>নতুন ব্যালেন্স:</b> ৳{new_bal:.2f}\n"
            f"⏱️ <b>Average time:</b> {get_avg_time_label(s)}\n"
            "⏳ <b>স্ট্যাটাস:</b> Pending\n"
            "─────────\n"
            "📦 <b>আমার অর্ডার</b> মেনু থেকে যেকোনো সময় লাইভ স্ট্যাটাস চেক করতে পারবেন।",
            parse_mode="HTML",
        )
        await notify_admin_new_order(
            context,
            order_id,
            s["name"],
            user,
            link,
            qty,
            sell_price,
            panel_order_id=panel_oid,
            panel_service_id=pid,
            panel_cost=panel_cost,
        )
        await _restore_user_menu(context, query.from_user.id)
        return


async def notify_admin_new_order(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    service_name: str,
    user,
    link: str,
    qty: int,
    price: float,
    panel_order_id: int = 0,
    panel_service_id: int = 0,
    panel_cost: float = 0.0,
) -> None:
    aid = _admin_id()
    if not aid:
        return
    profit = round(price - panel_cost, 2) if panel_cost > 0 else price
    kb_rows = []
    if panel_order_id > 0:
        kb_rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Check Panel Status", callback_data=f"adm_chk_{order_id}"
                ),
                InlineKeyboardButton(
                    "💸 Refund Order", callback_data=f"adm_ref_{order_id}"
                ),
            ]
        )
    kb_rows.extend(
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
                f"🧾 <b>Bot Order ID:</b> <code>#SMM-{order_id}</code>\n"
                f"🌐 <b>Panel Order ID:</b> <code>#{panel_order_id}</code> (Service #{panel_service_id})\n"
                f"📦 <b>সার্ভিস:</b> {service_name}\n"
                f"🔗 <b>লিংক:</b> <code>{link}</code>\n"
                f"🔢 <b>পরিমাণ (Quantity):</b> {qty}\n"
                f"💰 <b>Sell Price:</b> ৳{price:.2f} | <b>Panel Cost:</b> {panel_cost:.4f} | <b>Profit:</b> ৳{profit:.2f}\n"
                f"👤 <b>ক্রেতা:</b> {user.first_name} (ID: <code>{user.id}</code>)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "👇 অর্ডারের স্ট্যাটাস চেক বা আপডেট করতে নিচের বাটনে চাপুন:"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
    except Exception as exc:
        logger.warning("Failed to notify admin of order: %s", exc)


# ---------------------------------------------------------------------------
# Step 8 & Step 9: Check Order Status & Refill (User & Admin Sync Helper)
# ---------------------------------------------------------------------------
async def sync_order_with_panel(
    order_id: int, context: ContextTypes.DEFAULT_TYPE
) -> tuple[bool, str, sqlite3.Row | None]:
    """
    Calls panel_status(panel_order_id), updates the order row in SQLite,
    credits 10% referral bonus on Completed, and performs automatic refunds on Partial or Cancelled.
    """
    with _db_lock, db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        return (False, "⚠️ অর্ডারটি পাওয়া যায়নি।", None)

    panel_oid = int(order["panel_order_id"] or 0)
    if panel_oid <= 0:
        return (False, "ℹ️ এই অর্ডারটির কোনো Panel Order ID নেই।", order)

    st_res = panel_status(panel_oid)
    if not st_res.get("ok"):
        return (
            False,
            f"⚠️ প্যানেল স্ট্যাটাস চেক ব্যর্থ হয়েছে: {st_res.get('error')}",
            order,
        )

    mapped_st = st_res["status"]  # Pending | Processing | Completed | Partial | Cancelled
    starts_cnt = int(st_res.get("starts_count", 0))
    remains = int(st_res.get("remains", 0))
    prev_st = str(order["status"] or "Pending")
    already_refunded = int(order["refunded"] or 0) == 1
    sell_price = float(order["sell_price"] or order["price"] or 0.0)
    qty = max(1, int(order["quantity"] or 1))
    buyer_id = int(order["buyer_id"])

    refund_msg = ""
    if mapped_st == "Completed" and prev_st.lower() != "completed":
        with _db_lock, db() as conn:
            conn.execute(
                "UPDATE orders SET status = 'Completed', starts_count = ?, remains = 0 WHERE id = ?",
                (starts_cnt, order_id),
            )
            conn.commit()
        await credit_referral_on_completed_order(order_id, context)
        schedule_order_sheet_backup(order_id)
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"🎉 <b>আপনার অর্ডার সম্পন্ন হয়েছে! (#SMM-{order_id})</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 <b>Panel Order ID:</b> <code>#{panel_oid}</code>\n"
                    f"📦 <b>সার্ভিস:</b> {order['service_name']}\n"
                    f"🔢 <b>পরিমাণ:</b> {qty} | ▶️ Start Count: {starts_cnt}\n"
                    "✨ <b>স্ট্যাটাস:</b> ✅ Completed"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif mapped_st == "Partial" and not already_refunded:
        rem_clamped = max(0, min(qty, remains))
        refund_amt = round(sell_price * (rem_clamped / float(qty)), 2)
        new_bal = get_balance(buyer_id)
        with _db_lock, db() as conn:
            conn.execute(
                "UPDATE orders SET status = 'Partial', starts_count = ?, remains = ?, refunded = 1 WHERE id = ?",
                (starts_cnt, rem_clamped, order_id),
            )
            conn.commit()
        if refund_amt > 0:
            new_bal = add_balance(
                buyer_id,
                refund_amt,
                reason=f"Partial Order Refund #SMM-{order_id}",
            )
        schedule_order_sheet_backup(order_id)
        refund_msg = (
            f"\n⚠️ <b>Partial Order!</b> বাকি ছিল (Remains): <b>{rem_clamped}</b>\n"
            f"💰 আনডেলিভারড অংশের <b>৳{refund_amt:.2f}</b> ওয়ালেটে রিফান্ড করা হয়েছে (ব্যালেন্স: ৳{new_bal:.2f})।"
        )
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"⚠️ <b>অর্ডার আংশিক সম্পন্ন ও রিফান্ড (#SMM-{order_id})</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 <b>Panel Order ID:</b> <code>#{panel_oid}</code>\n"
                    f"📦 <b>সার্ভিস:</b> {order['service_name']}\n"
                    f"🔢 <b>অর্ডার পরিমাণ:</b> {qty} | 📉 <b>বাকি (Remains):</b> {rem_clamped}\n"
                    f"💰 <b>রিফান্ড:</b> +৳{refund_amt:.2f} ওয়ালেটে ফেরত দেওয়া হয়েছে।\n"
                    f"💳 <b>বর্তমান ব্যালেন্স:</b> ৳{new_bal:.2f}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif mapped_st == "Cancelled" and not already_refunded:
        with _db_lock, db() as conn:
            conn.execute(
                "UPDATE orders SET status = 'Cancelled', starts_count = ?, remains = ?, refunded = 1 WHERE id = ?",
                (starts_cnt, remains, order_id),
            )
            conn.commit()
        new_bal = add_balance(
            buyer_id,
            sell_price,
            reason=f"Cancelled Order Refund #SMM-{order_id}",
        )
        schedule_order_sheet_backup(order_id)
        refund_msg = (
            f"\n❌ <b>Cancelled by Panel!</b> সম্পূর্ণ <b>৳{sell_price:.2f}</b> ওয়ালেটে রিফান্ড করা হয়েছে (ব্যালেন্স: ৳{new_bal:.2f})।"
        )
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"❌ <b>অর্ডার বাতিল ও সম্পূর্ণ রিফান্ড (#SMM-{order_id})</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 <b>Panel Order ID:</b> <code>#{panel_oid}</code>\n"
                    f"📦 <b>সার্ভিস:</b> {order['service_name']}\n"
                    f"💰 <b>রিফান্ড:</b> +৳{sell_price:.2f} আপনার ওয়ালেটে ফেরত দেওয়া হয়েছে।\n"
                    f"💳 <b>বর্তমান ব্যালেন্স:</b> ৳{new_bal:.2f}"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        with _db_lock, db() as conn:
            conn.execute(
                "UPDATE orders SET status = ?, starts_count = ?, remains = ? WHERE id = ?",
                (mapped_st, starts_cnt, remains, order_id),
            )
            conn.commit()
        schedule_order_sheet_backup(order_id)

    with _db_lock, db() as conn:
        updated_order = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()

    summary = (
        f"🧾 <b>Order #SMM-{order_id}</b> (Panel <code>#{panel_oid}</code>)\n"
        f"📦 <b>সার্ভিস:</b> {order['service_name']}\n"
        f"📊 <b>বর্তমান স্ট্যাটাস:</b> <b>{mapped_st}</b>\n"
        f"▶️ <b>Start Count:</b> {starts_cnt} | 📉 <b>Remains:</b> {remains}"
        f"{refund_msg}"
    )
    return (True, summary, updated_order)


def build_user_order_keyboard(order: sqlite3.Row, service: sqlite3.Row | None) -> InlineKeyboardMarkup | None:
    """
    Builds inline buttons for an order in My Orders:
      - Step 8: "Check Order Status" button
      - Step 9: "Refill" button ONLY if refill_enabled = 1 and status is Completed.
        Never show a Refill button for No Refill services.
    """
    oid = order["id"]
    panel_oid = int(order["panel_order_id"] or 0)
    if panel_oid <= 0:
        return None

    buttons = [
        [
            InlineKeyboardButton(
                "🔄 Check Order Status", callback_data=f"usr_chk_{oid}"
            )
        ]
    ]
    refill_en = int(service["refill_enabled"] or 0) if service else 0
    st_norm = str(order["status"] or "").strip().lower()
    if refill_en == 1 and st_norm == "completed":
        r_label = get_refill_label(service) if service else "Refill"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"♻️ Report Drop & Refill ({r_label})",
                    callback_data=f"usr_drop_{oid}",
                )
            ]
        )
    return InlineKeyboardMarkup(buttons)


async def handle_user_order_actions_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user = query.from_user

    if data.startswith("usr_chk_"):
        try:
            oid = int(data.replace("usr_chk_", "", 1))
        except ValueError:
            return
        with _db_lock, db() as conn:
            order = conn.execute(
                "SELECT * FROM orders WHERE id = ? AND buyer_id = ?", (oid, user.id)
            ).fetchone()
        if not order:
            await query.edit_message_text("⚠️ অর্ডারটি পাওয়া যায়নি।")
            await _restore_user_menu(context, query.from_user.id)
            return

        ok, summary, updated = await sync_order_with_panel(oid, context)
        svc = get_service(updated["service_id"]) if updated else None
        kb = build_user_order_keyboard(updated, svc) if updated else None
        await query.edit_message_text(summary, parse_mode="HTML", reply_markup=kb)
        if kb is None:
            await _restore_user_menu(context, query.from_user.id)
        return

    if data.startswith("usr_drop_"):
        try:
            oid = int(data.replace("usr_drop_", "", 1))
        except ValueError:
            return
        with _db_lock, db() as conn:
            order = conn.execute(
                "SELECT * FROM orders WHERE id = ? AND buyer_id = ?", (oid, user.id)
            ).fetchone()
        if not order:
            await query.edit_message_text("⚠️ অর্ডারটি পাওয়া যায়নি।")
            await _restore_user_menu(context, query.from_user.id)
            return
        svc = get_service(order["service_id"])
        if not svc or int(svc["refill_enabled"] or 0) != 1:
            await query.edit_message_text("⚠️ এই সার্ভিসটিতে রিফিল সুবিধা নেই (No Refill)।")
            await _restore_user_menu(context, query.from_user.id)
            return
        if str(order["status"] or "").strip().lower() != "completed":
            await query.edit_message_text("⚠️ শুধুমাত্র Completed অর্ডারে ড্রপ হলে রিফিল করা যাবে।")
            await _restore_user_menu(context, query.from_user.id)
            return

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "♻️ Confirm Refill Now", callback_data=f"usr_refill_{oid}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back to Order", callback_data=f"usr_chk_{oid}"
                    )
                ],
            ]
        )
        await query.edit_message_text(
            f"♻️ <b>Refill Request (#SMM-{oid})</b>\n"
            f"🌐 Panel Order ID: <code>#{order['panel_order_id']}</code>\n"
            f"📦 সার্ভিস: {order['service_name']} ({get_refill_label(svc)})\n\n"
            "আপনার অর্ডারে কি ড্রপ হয়েছে? নিচের বাটনে চাপ দিলে প্যানেলে অটোমেটিক Refill রিকোয়েস্ট পাঠানো হবে:",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data.startswith("usr_refill_"):
        try:
            oid = int(data.replace("usr_refill_", "", 1))
        except ValueError:
            return
        with _db_lock, db() as conn:
            order = conn.execute(
                "SELECT * FROM orders WHERE id = ? AND buyer_id = ?", (oid, user.id)
            ).fetchone()
        if not order:
            await query.edit_message_text("⚠️ অর্ডারটি পাওয়া যায়নি।")
            await _restore_user_menu(context, query.from_user.id)
            return
        svc = get_service(order["service_id"])
        if not svc or int(svc["refill_enabled"] or 0) != 1:
            await query.edit_message_text("⚠️ এই সার্ভিসটিতে রিফিল সুবিধা নেই (No Refill)।")
            await _restore_user_menu(context, query.from_user.id)
            return
        if str(order["status"] or "").strip().lower() != "completed":
            await query.edit_message_text("⚠️ শুধুমাত্র Completed অর্ডারে রিফিল করা যাবে।")
            await _restore_user_menu(context, query.from_user.id)
            return

        ref_res = panel_refill(int(order["panel_order_id"]))
        if ref_res.get("ok"):
            await query.edit_message_text(
                f"✅ <b>Refill রিকোয়েস্ট সফল! (#SMM-{oid})</b>\n"
                f"🌐 Panel Order ID: <code>#{order['panel_order_id']}</code>\n"
                f"🆔 Refill ID: <code>{ref_res.get('refill')}</code>",
                parse_mode="HTML",
            )
            await _restore_user_menu(context, query.from_user.id)
        else:
            await query.edit_message_text(
                f"❌ <b>প্যানেল রিফিল গ্রহণ করেনি:</b>\n<code>{ref_res.get('error')}</code>",
                parse_mode="HTML",
            )
        await _restore_user_menu(context, query.from_user.id)
        return


# ---------------------------------------------------------------------------
# 4. DEPOSIT FLOW — Clean 5 Steps:
#    1) Method -> 2) Amount (limit 10-50000) -> 3) Number + Send Money instructions -> 4) TrxID (duplicate check) -> 5) Submit for Admin Approval -> 6) Pending Confirmation
# ---------------------------------------------------------------------------
async def start_deposit_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Paid File style, Step 1 = method selection (only enabled methods; deposit closed if none)."""
    context.user_data.clear()
    context.user_data["state"] = ST_DEP_METHOD
    context.user_data["dep_method_key"] = ""
    context.user_data["dep_method"] = ""
    enabled = deposit_available_methods()
    if not enabled:
        closed_text = "⚠️ বর্তমানে ডিপোজিট বন্ধ আছে। সাহায্যের জন্য Support-এ যোগাযোগ করুন।"
        if update.message:
            await update.message.reply_text(closed_text, reply_markup=main_menu(update.effective_user.id))
        elif update.callback_query:
            await update.callback_query.answer()
            await context.bot.send_message(
                chat_id=update.callback_query.from_user.id,
                text=closed_text,
                reply_markup=main_menu(update.callback_query.from_user.id),
            )
        context.user_data.clear()
        return
    method_lines = []
    for _k in enabled:
        _emoji, _bn = _METHOD_BN.get(_k, _METHOD_BN["bkash"])
        method_lines.append(f"{_emoji} {_bn} (পার্সোনাল Send Money)")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{DEPOSIT_METHODS[k][0]} {DEPOSIT_METHODS[k][1]}", callback_data=f"dep_m_{k}")]
            for k in enabled
        ]
        + [[InlineKeyboardButton("❌ বাতিল করুন", callback_data="dep_cancel_cb")]]
    )
    text = (
        "💰 <b>ডিপোজিট — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💳 <b>ধাপ ১: ডিপোজিট মেথড বাছুন</b>\n"
        "💰 <b>ডিপোজিট লিমিট: মিনিমাম ৳10 - ম্যাক্সিমাম ৳50000</b>\n"
        "আমাদের পার্সোনাল নাম্বারে Send Money করুন। অ্যাডমিন ম্যানুয়ালি যাচাই করে ব্যালেন্স যোগ করে দেবে:\n\n"
        + "\n".join(method_lines)
        + "\n━━━━━━━━━━━━━━━━━━━━━━\n👇 পছন্দের মাধ্যম বাছুন:"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)




async def _finalize_deposit_submission(
    user,
    method: str,
    amt: float,
    trxid: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int, str]:
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

    confirm_text = (
        "✅ <b>ডিপোজিট রিকোয়েস্ট সফলভাবে সাবমিট হয়েছে!</b>\n"
        "─────────\n"
        "🌟 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        f"🧾 <b>Request ID:</b> <code>#DEP-{req_id}</code>\n"
        f"🌸 <b>মেথড:</b> {method}\n"
        f"💰 <b>পরিমাণ:</b> <b>৳{amt:.2f}</b>\n"
        f"🔢 <b>TrxID:</b> <code>{trxid}</code>\n"
        "⏳ <b>স্ট্যাটাস:</b> Pending - admin approval er por balance jog hobe\n"
        "আপনার রিকোয়েস্টটি ইতিমধ্যে অ্যাডমিনের কাছে চলে গেছে, তাই সাপোর্টে আলাদা মেসেজ করার প্রয়োজন নেই। ধৈর্য ধরে অপেক্ষা করুন!\n"
        "─────────\n"
        "অ্যাডমিন আপনার পেমেন্ট যাচাই করে অ্যাপ্রুভ করার সাথে সাথে ওয়ালেটে ব্যালেন্স যোগ হয়ে যাবে।"
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
                    "💳 <b>নতুন ডিপোজিট রিকোয়েস্ট — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
                    "─────────\n"
                    f"🧾 <b>Request ID:</b> <code>#DEP-{req_id}</code>\n"
                    f"🌸 <b>মেথড:</b> {method}\n"
                    f"💰 <b>পরিমাণ:</b> <b>৳{amt:.2f}</b>\n"
                    f"🔢 <b>TrxID:</b> <code>{trxid}</code>\n"
                    f"👤 <b>ইউজার:</b> {user.first_name} (<code>{user.id}</code>)"
                ),
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass
    return req_id, confirm_text


async def handle_deposit_method_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user = query.from_user

    if data == "dep_cancel_cb":
        context.user_data.clear()
        await query.edit_message_text("❌ ডিপোজিট রিকোয়েস্ট বাতিল করা হয়েছে।")
        await context.bot.send_message(
            chat_id=user.id,
            text="মেইন মেনু:",
            reply_markup=main_menu(user.id),
        )
        return

    if data == "dep_submit_cb":
        if context.user_data.get("state") != ST_DEP_SUBMIT:
            await query.edit_message_text("⚠️ ডিপোজিট সেশনটির মেয়াদ শেষ হয়েছে। অনুগ্রহ করে আবার 💰 ডিপোজিট বাটনে চাপুন।")
            await _restore_user_menu(context, query.from_user.id)
            return
        method = str(context.user_data.get("dep_method", "bKash Personal (Send Money)"))
        amt = float(context.user_data.get("dep_amount", 0.0))
        trxid = str(context.user_data.get("dep_trxid", "")).strip()
        context.user_data.clear()
        if amt <= 0 or not trxid:
            await query.edit_message_text("⚠️ ডিপোজিট তথ্য অসম্পূর্ণ। আবার চেষ্টা করুন।")
            await _restore_user_menu(context, query.from_user.id)
            return
        if is_trxid_used(trxid):
            await query.edit_message_text(
                "⚠️ এই TrxID আগেই ব্যবহার হয়েছে। অনুগ্রহ করে সঠিক/নতুন TrxID দিন। আবার 💰 ডিপোজিট বাটনে চাপুন।"
            )
            await context.bot.send_message(
                chat_id=user.id,
                text="👇 আপনার মেইন মেনু:",
                reply_markup=main_menu(user.id),
            )
            return
        _, confirm_text = await _finalize_deposit_submission(
            user, method, amt, trxid, context
        )
        await query.edit_message_text(confirm_text, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=user.id,
            text="👇 আপনার মেইন মেনু:",
            reply_markup=main_menu(user.id),
        )
        return

    # Deposit method selection (Paid File style): method first, then amount.
    if data.startswith("dep_m_"):
        method_key = data[len("dep_m_"):]
        if method_key not in DEPOSIT_METHODS or not deposit_method_enabled(method_key):
            await query.edit_message_text("❌ এই মেথডটি বর্তমানে বন্ধ আছে। অন্য মেথড বাছুন।")
            await _restore_user_menu(context, query.from_user.id)
            return
        if not deposit_method_number(method_key):
            _kb2 = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(f"{DEPOSIT_METHODS[k][0]} {DEPOSIT_METHODS[k][1]}", callback_data=f"dep_m_{k}")]
                    for k in deposit_available_methods()
                ]
                + [[InlineKeyboardButton("❌ বাতিল করুন", callback_data="dep_cancel_cb")]]
            )
            await query.edit_message_text(
                "❌ এই মাধ্যমটি বর্তমানে চালু নেই। অন্য একটি বেছে নিন।",
                reply_markup=_kb2,
            )
            return
        method = deposit_method_label(method_key)
        context.user_data["dep_method_key"] = method_key
        context.user_data["dep_method"] = method
        context.user_data["state"] = ST_DEP_AMOUNT
        await query.edit_message_text(
            "💰 <b>ডিপোজিট — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 <b>ধাপ ১: {method} বাছা হয়েছে</b>\n"
            "💵 <b>ধাপ ২: টাকার পরিমাণ (Amount)</b>\n"
            "💰 <b>ডিপোজিট লিমিট: মিনিমাম ৳10 - ম্যাক্সিমাম ৳50000</b>\n"
            "আপনি কত টাকা ডিপোজিট করতে চান? নিচে সংখ্যায় লিখুন (যেমন: <code>100</code>):",
            parse_mode="HTML",
        )
        await context.bot.send_message(
            chat_id=user.id,
            text="💵 টাকার পরিমাণ লিখুন (বাতিল করতে ❌ বাতিল করুন চাপুন):",
            reply_markup=cancel_menu(),
        )
        return

    await query.edit_message_text("⚠️ ডিপোজিট সেশনটির মেয়াদ শেষ হয়েছে। অনুগ্রহ করে আবার 💰 ডিপোজিট বাটনে চাপুন.")
    await _restore_user_menu(context, query.from_user.id)


# ---------------------------------------------------------------------------
# Admin Order, Deposit & CSV Export Callbacks
# ---------------------------------------------------------------------------
def _build_admin_order_kb(order_id: int, panel_order_id: int) -> InlineKeyboardMarkup:
    rows = []
    if panel_order_id > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Check Panel Status", callback_data=f"adm_chk_{order_id}"
                ),
                InlineKeyboardButton(
                    "💸 Refund Order", callback_data=f"adm_ref_{order_id}"
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "💸 Refund Order", callback_data=f"adm_ref_{order_id}"
                )
            ]
        )
    rows.extend(
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
    return InlineKeyboardMarkup(rows)


async def handle_admin_decisions_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ শুধুমাত্র অ্যাডমিন এই অ্যাকশন নিতে পারবে।")
        await _restore_user_menu(context, query.from_user.id)
        return

    data = query.data or ""

    # Admin: Export All Users CSV
    if data == "adm_export_users":
        await send_all_users_csv(query.from_user.id, context)
        return

    # Admin: Check Panel Status button
    if data.startswith("adm_chk_"):
        try:
            oid = int(data.replace("adm_chk_", "", 1))
        except ValueError:
            return
        ok, summary, updated = await sync_order_with_panel(oid, context)
        if updated:
            p_cost = float(updated["panel_cost"] or 0.0)
            s_price = float(updated["sell_price"] or updated["price"] or 0.0)
            profit = round(s_price - p_cost, 2) if p_cost > 0 else s_price
            summary += (
                f"\n💰 <b>Sell:</b> ৳{s_price:.2f} | <b>Panel Cost:</b> {p_cost:.4f} | <b>Profit:</b> ৳{profit:.2f}"
            )
            kb = _build_admin_order_kb(oid, int(updated["panel_order_id"] or 0))
            await query.edit_message_text(summary, parse_mode="HTML", reply_markup=kb)
        else:
            await query.edit_message_text(summary, parse_mode="HTML")
            await _restore_admin_menu(context, query.from_user.id)
        return

    # Admin: Refund Order button (refunds sell price to user wallet and marks order Cancelled)
    if data.startswith("adm_ref_"):
        try:
            oid = int(data.replace("adm_ref_", "", 1))
        except ValueError:
            return
        with _db_lock, db() as conn:
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
            if not order:
                await query.edit_message_text("⚠️ অর্ডার পাওয়া যায়নি।")
                await _restore_admin_menu(context, query.from_user.id)
                return
            if int(order["refunded"] or 0) == 1 or str(order["status"]).lower() == "cancelled":
                await query.edit_message_text(
                    f"ℹ️ Order #SMM-{oid} ইতিমধ্যে Cancelled / Refunded করা হয়েছে।"
                )
                await _restore_admin_menu(context, query.from_user.id)
                return
            conn.execute(
                "UPDATE orders SET status = 'Cancelled', refunded = 1 WHERE id = ?",
                (oid,),
            )
            conn.commit()

        refund = float(order["sell_price"] or order["price"] or 0.0)
        new_bal = add_balance(
            order["buyer_id"], refund, reason=f"Admin Refund #SMM-{oid}"
        )
        schedule_order_sheet_backup(oid)
        await query.edit_message_text(
            f"💸 <b>Order #SMM-{oid} (Panel #{order['panel_order_id']}) Cancelled & Refunded!</b>\n"
            f"💰 <b>৳{refund:.2f}</b> ক্রেতার ওয়ালেটে ফেরত দেওয়া হয়েছে (নতুন ব্যালেন্স: ৳{new_bal:.2f})।",
            parse_mode="HTML",
        )
        await _restore_admin_menu(context, query.from_user.id)
        try:
            await context.bot.send_message(
                chat_id=order["buyer_id"],
                text=(
                    f"❌ <b>অর্ডার বাতিল ও রিফান্ড (#SMM-{oid})</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌐 <b>Panel Order ID:</b> <code>#{order['panel_order_id']}</code>\n"
                    f"📦 <b>সার্ভিস:</b> {order['service_name']}\n"
                    f"💰 <b>রিফান্ড:</b> <b>+৳{refund:.2f}</b> আপনার ওয়ালেটে ফেরত দেওয়া হয়েছে।\n"
                    f"💳 <b>বর্তমান ব্যালেন্স:</b> <b>৳{new_bal:.2f}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

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
                await _restore_admin_menu(context, query.from_user.id)
                return
            if row["status"] != "pending":
                await query.edit_message_text(f"ℹ️ এই রিকোয়েস্ট ইতিমধ্যে {row['status']}।")
                await _restore_admin_menu(context, query.from_user.id)
                return
            new_st = "approved" if is_app else "rejected"
            conn.execute(
                "UPDATE balance_requests SET status = ? WHERE id = ?", (new_st, req_id)
            )
            conn.commit()

        if is_app:
            new_bal = add_balance(
                row["buyer_id"],
                float(row["amount"]),
                is_deposit=True,
                reason=f"Deposit Approved #{req_id} ({row['method']} - {row['trxid']})",
            )
            await query.edit_message_text(
                f"✅ <b>Deposit #{req_id} Approved!</b>\n"
                f"👤 User ID: <code>{row['buyer_id']}</code>\n"
                f"💰 যোগ হয়েছে: ৳{row['amount']:.2f} | নতুন ব্যালেন্স: ৳{new_bal:.2f}",
                parse_mode="HTML",
            )
            await _restore_admin_menu(context, query.from_user.id)
            try:
                await context.bot.send_message(
                    chat_id=row["buyer_id"],
                    text=(
                        "🎉 <b>ডিপোজিট অ্যাপ্রুভ হয়েছে!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💳 মেথড: {row['method']}\n"
                        f"💰 যোগ হয়েছে: <b>৳{row['amount']:.2f}</b>\n"
                        f"🏦 বর্তমান ব্যালেন্স: <b>৳{new_bal:.2f}</b>\n\n"
                        "এখন আপনি <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>-এ যেকোনো সার্ভিস অর্ডার করতে পারবেন!"
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
            await _restore_admin_menu(context, query.from_user.id)
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
                await _restore_admin_menu(context, query.from_user.id)
                return
            if str(order["status"]).lower() in ("completed", "cancelled"):
                await query.edit_message_text(
                    f"ℹ️ Order #SMM-{oid} ইতিমধ্যে {order['status']} করা হয়েছে।"
                )
                await _restore_admin_menu(context, query.from_user.id)
                return

            if action == "proc":
                conn.execute("UPDATE orders SET status = 'Processing' WHERE id = ?", (oid,))
                conn.commit()
                schedule_order_sheet_backup(oid)
                kb = _build_admin_order_kb(oid, int(order["panel_order_id"] or 0))
                await query.edit_message_text(
                    f"⚙️ <b>Order #SMM-{oid} (Panel #{order['panel_order_id']}) এখন Processing-এ আছে।</b>\n"
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
                            f"🌐 Panel Order ID: <code>#{order['panel_order_id']}</code>\n"
                            f"📦 সার্ভিস: {order['service_name']}\n"
                            "🚀 আপনার অর্ডারটির কাজ চলছে (<b>Processing</b>)!"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return

            if action == "comp":
                conn.execute("UPDATE orders SET status = 'Completed' WHERE id = ?", (oid,))
                conn.commit()

        if action == "comp":
            await credit_referral_on_completed_order(oid, context)
            schedule_order_sheet_backup(oid)
            await query.edit_message_text(
                f"✅ <b>Order #SMM-{oid} (Panel #{order['panel_order_id']}) সফলভাবে Completed করা হয়েছে!</b>",
                parse_mode="HTML",
            )
            await _restore_admin_menu(context, query.from_user.id)
            try:
                await context.bot.send_message(
                    chat_id=order["buyer_id"],
                    text=(
                        f"🎉 <b>অর্ডার সম্পন্ন হয়েছে! (#SMM-{oid})</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 Panel Order ID: <code>#{order['panel_order_id']}</code>\n"
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
            with _db_lock, db() as conn:
                conn.execute(
                    "UPDATE orders SET status = 'Cancelled', refunded = 1 WHERE id = ?",
                    (oid,),
                )
                conn.commit()
            refund = float(order["sell_price"] or order["price"] or 0.0)
            new_bal = add_balance(
                order["buyer_id"], refund, reason=f"Order Cancelled Refund #SMM-{oid}"
            )
            schedule_order_sheet_backup(oid)
            await query.edit_message_text(
                f"❌ <b>Order #SMM-{oid} (Panel #{order['panel_order_id']}) বাতিল করা হয়েছে এবং ৳{refund:.2f} ক্রেতার ওয়ালেটে রিফান্ড দেওয়া হয়েছে।</b>",
                parse_mode="HTML",
            )
            await _restore_admin_menu(context, query.from_user.id)
            try:
                await context.bot.send_message(
                    chat_id=order["buyer_id"],
                    text=(
                        f"❌ <b>অর্ডার বাতিল ও রিফান্ড (#SMM-{oid})</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌐 Panel Order ID: <code>#{order['panel_order_id']}</code>\n"
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
    await _restore_admin_menu(context, query.from_user.id)


async def handle_edit_service_cb(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    try:
        sid = int((query.data or "").replace("edit_", "", 1))
    except ValueError:
        return
    s = get_service(sid)
    if not s:
        await query.edit_message_text("⚠️ সার্ভিসটি পাওয়া যায়নি।")
        await _restore_admin_menu(context, query.from_user.id)
        return

    context.user_data.clear()
    context.user_data["edit_sid"] = sid
    context.user_data["state"] = ST_EDIT_SMM_NAME
    sell_rate = get_sell_rate_per_1k(s)
    await query.edit_message_text(
        f"✏️ <b>সার্ভিস এডিট: #{s['id']} {s['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 বর্তমান নাম: <code>{s['name']}</code>\n"
        f"🌐 বর্তমান Panel Service ID: <code>{s['panel_service_id']}</code>\n"
        f"💵 বর্তমান Panel Rate/1000: <code>{_fmt_num(s['panel_rate'])}</code>\n"
        f"💰 বর্তমান Sell Price/1000: <code>৳{_fmt_num(sell_rate)}</code>\n"
        f"📉 Min: <code>{s['min_qty']}</code> | 📈 Max: <code>{s['max_qty']}</code>\n"
        f"🔄 Refill: <code>{get_refill_label(s)}</code> | Active: <code>{s['is_active']}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 <b>ধাপ ১/৮:</b> সার্ভিসের নতুন নাম লিখুন (আগের নামই রাখতে <code>.</code> লিখুন):",
        parse_mode="HTML",
    )
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="📝 সার্ভিসের নাম লিখুন অথবা আগের নাম রাখতে . (ডট) পাঠান (বাতিল করতে ❌ বাতিল করুন চাপুন):",
        reply_markup=cancel_menu(),
    )


# ---------------------------------------------------------------------------
# User Menu Views (Orders, Profile, Refer, Leaderboard, Missions, Support)
# ---------------------------------------------------------------------------
async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 10",
            (uid,),
        ).fetchall()
    if not rows:
        await update.message.reply_text(
            "📦 <b>আপনার এখনো কোনো অর্ডার নেই।</b>\n\n"
            "অর্ডার করতে <b>অর্ডার করুন</b> বাটনে ক্লিক করুন!",
            parse_mode="HTML",
            reply_markup=main_menu(uid),
        )
        return

    status_map = {
        "pending": "⏳ Pending",
        "processing": "⚙️ Processing",
        "completed": "✅ Completed",
        "approved": "✅ Completed",
        "partial": "⚠️ Partial (Auto-Refunded Remains)",
        "cancelled": "❌ Cancelled (Refunded)",
        "rejected": "❌ Rejected",
    }
    await update.message.reply_text(
        "📦 <b>আপনার সর্বশেষ অর্ডারসমূহ (লাইভ স্ট্যাটাস ও রিফিল প্যানেল):</b>",
        parse_mode="HTML",
        reply_markup=main_menu(uid),
    )
    for r in rows:
        st_norm = str(r["status"] or "Pending").lower()
        st_badge = status_map.get(st_norm, r["status"])
        panel_oid = int(r["panel_order_id"] or 0)
        svc = get_service(r["service_id"])
        refill_label = f"🔄 {get_refill_label(svc)}" if svc else "🚫 No Refill"
        p_line = f"🌐 <b>Panel Order ID:</b> <code>#{panel_oid}</code> | {refill_label}\n" if panel_oid > 0 else ""
        rem_line = (
            f"▶️ Start: {r['starts_count']} | 📉 Remains: {r['remains']}\n"
            if panel_oid > 0
            else ""
        )
        text = (
            f"🧾 <b>#SMM-{r['id']}</b> — {st_badge}\n"
            f"{p_line}"
            f"📦 <b>সার্ভিস:</b> {r['service_name']}\n"
            f"🔢 <b>পরিমাণ:</b> {r['quantity']} | 💰 <b>চার্জ:</b> ৳{r['price']:.2f}\n"
            f"{rem_line}"
            f"🔗 <code>{r['target_link']}</code>"
        )
        kb = build_user_order_keyboard(r, svc)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    with _db_lock, db() as conn:
        total_orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ?", (user.id,)
        ).fetchone()[0]
        comp_orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ? AND LOWER(status) IN ('completed','approved')",
            (user.id,),
        ).fetchone()[0]

    vip_sec = format_vip_section(user.id)
    text = (
        "👤 <b>প্রোফাইল — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
        "─────────\n"
        f"📛 <b>নাম:</b> {user.first_name}\n"
        f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        "─────────\n"
        f"{vip_sec}\n"
        "─────────\n"
        "💳 <b>ওয়ালেট সামারি:</b>\n"
        f"💰 <b>বর্তমান ব্যালেন্স:</b> <b>৳{u['balance']:.2f}</b>\n"
        f"🏦 <b>মোট ডিপোজিট:</b> ৳{u['total_deposit']:.2f}\n"
        f"🛒 <b>মোট খরচ:</b> ৳{u['total_spent']:.2f}\n"
        "─────────\n"
        "📊 <b>অর্ডার সামারি:</b>\n"
        f"📦 <b>মোট অর্ডার:</b> {total_orders} টি | ✅ <b>সম্পন্ন:</b> {comp_orders} টি"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_menu(user.id)
    )


async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Short and clear referral menu (ONLY place where referral rule is shown):
    Refer bonus = 10% of the referred user's COMPLETED order amount only, never on join.
    """
    user = update.effective_user
    u = ensure_user(user.id, user.username or "", user.first_name or "User")
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
    text = (
        "👥 <b>রেফার করুন, আয় করুন!</b>\n"
        "─────────\n"
        "🎁 <b>নিয়ম:</b> আপনার রেফার করা ইউজারের COMPLETED অর্ডারের 10% আপনার ওয়ালেটে যোগ হবে।\n\n"
        "🔗 <b>আপনার রেফারেল লিংক:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "─────────\n"
        f"👥 <b>মোট রেফার:</b> {u['refer_count']} জন | 💰 <b>মোট আয়:</b> ৳{u['refer_earned']:.2f}"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_menu(user.id)
    )


async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top 10 by COMPLETED ORDER COUNT. Never shows balance, user ID, or phone."""
    uid = update.effective_user.id
    with _db_lock, db() as conn:
        rows = conn.execute(
            """
            SELECT u.username, u.first_name,
                   COUNT(o.id) AS comp_orders,
                   COALESCE(SUM(CASE WHEN o.sell_price > 0 THEN o.sell_price ELSE o.price END), 0.0) AS comp_amount
            FROM users u
            LEFT JOIN orders o ON o.buyer_id = u.user_id AND LOWER(o.status) IN ('completed', 'approved')
            GROUP BY u.user_id
            HAVING COUNT(o.id) > 0
            ORDER BY comp_orders DESC, comp_amount DESC
            LIMIT 10
            """
        ).fetchall()
        all_rows = conn.execute(
            """
            SELECT u.user_id,
                   COUNT(o.id) AS comp_orders,
                   COALESCE(SUM(CASE WHEN o.sell_price > 0 THEN o.sell_price ELSE o.price END), 0.0) AS comp_amount
            FROM users u
            LEFT JOIN orders o ON o.buyer_id = u.user_id AND LOWER(o.status) IN ('completed', 'approved')
            GROUP BY u.user_id
            HAVING COUNT(o.id) > 0
            ORDER BY comp_orders DESC, comp_amount DESC
            """
        ).fetchall()
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [
        "🏆 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL — লিডারবোর্ড</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <b>টপ ১০ (Completed অর্ডার সংখ্যা অনুযায়ী):</b>"
    ]
    if not rows:
        lines.append("\n😔 এখনো কেউ Completed অর্ডার করেনি।")
    for idx, r in enumerate(rows):
        name = f"@{r['username']}" if r["username"] else (r["first_name"] or "User")
        lines.append(
            f"{medals[idx]} <b>{name}</b> — ✅ {int(r['comp_orders'])}টি Completed অর্ডার | 💰 ৳{float(r['comp_amount']):.2f}"
        )
    my_rank = None
    my_orders = 0
    my_amount = 0.0
    for pos, r in enumerate(all_rows, start=1):
        if int(r["user_id"]) == int(uid):
            my_rank = pos
            my_orders = int(r["comp_orders"])
            my_amount = float(r["comp_amount"])
            break
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    if my_rank is not None:
        lines.append(
            f"👤 <b>আপনার র‍্যাংক:</b> #{my_rank} | ✅ {my_orders}টি Completed অর্ডার | 💰 ৳{my_amount:.2f}"
        )
    else:
        lines.append("👤 <b>আপনার র‍্যাংক:</b> এখনো র‍্যাংকিংয়ে নেই (Completed অর্ডার ০টি)")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=main_menu(uid),
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

    vip_sec = format_vip_section(user.id)
    text = (
        "🎯 <b>ডেইলি বোনাস ও ভিআইপি সেকশন</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{vip_sec}\n\n"
        "🏆 <b>ভিআইপি র‍্যাংক ও ডিসকাউন্ট চার্ট (Completed Order সংখ্যা অনুযায়ী):</b>\n"
        "├─ 🥈 <b>Silver Pro:</b> ≥ 100টি Completed অর্ডার (3% ডিসকাউন্ট)\n"
        "├─ 🥇 <b>Gold VIP:</b> ≥ 300টি Completed অর্ডার (5% ডিসকাউন্ট)\n"
        "├─ 💎 <b>Royal Diamond:</b> ≥ 500টি Completed অর্ডার (7% ডিসকাউন্ট)\n"
        "└─ 👑 <b>VVIP Legend:</b> ≥ 1000টি Completed অর্ডার (10% ডিসকাউন্ট)\n\n"
        f"🎁 <b>ডেইলি লগইন বোনাস:</b> ৳{daily_amt:.2f} টাকা (প্রতি ২৪ ঘণ্টায় ১ বার)\n"
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
        await _restore_user_menu(context, query.from_user.id)
        return

    with _db_lock, db() as conn:
        conn.execute(
            "UPDATE users SET balance = ROUND(balance + ?, 2), last_daily_ts = ? WHERE user_id = ?",
            (daily_amt, now_ts, user.id),
        )
        conn.commit()
    new_bal = get_balance(user.id)
    schedule_sheet_backup(
        user_id=user.id,
        username=user.username or user.first_name or "",
        balance=new_bal,
        order_details=f"Daily Bonus Claimed (+৳{daily_amt:.2f})",
    )
    await query.edit_message_text(
        f"🎉 <b>অভিনন্দন! আপনি ৳{daily_amt:.2f} ডেইলি বোনাস পেয়েছেন!</b>\n"
        f"💳 আপনার বর্তমান ব্যালেন্স: <b>৳{new_bal:.2f}</b>",
        parse_mode="HTML",
    )
    await _restore_user_menu(context, query.from_user.id)


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sup = get_setting("support_username")
    context.user_data.clear()
    context.user_data["state"] = ST_SUPPORT_MSG
    sup_line = f"📞 সরাসরি অ্যাডমিন ইনবক্স: @{sup}\n\n" if sup else "🛟 সাপোর্টের জন্য অ্যাডমিনের সাথে যোগাযোগ করুন।\n\n"
    text = (
        "🛟 <b>২৪/৭ ভিআইপি কাস্টমাইজড সাপোর্ট সেন্টার</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sup_line}"
        "💬 আপনার যেকোনো সমস্যা, অর্ডার আইডি বা প্রশ্ন নিচের মেসেজ বক্সে লিখে পাঠান। আমাদের অ্যাডমিন টিম সরাসরি আপনার মেসেজ দেখতে পাবে:"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=cancel_menu())


# ---------------------------------------------------------------------------
# 5. Secret Admin Panel Views & Flows (including "All Users" Sheet & CSV Export)
# ---------------------------------------------------------------------------
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    context.user_data.clear()
    with _db_lock, db() as conn:
        u_cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        p_ord = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE LOWER(status) IN ('pending','processing')"
        ).fetchone()[0]
        p_dep = conn.execute(
            "SELECT COUNT(*) FROM balance_requests WHERE status = 'pending'"
        ).fetchone()[0]

    pbal_res = panel_balance()
    if pbal_res.get("ok"):
        pbal_line = f"🌐 <b>SMM Panel Balance:</b> <b>{pbal_res['balance']} {pbal_res['currency']}</b>"
    else:
        pbal_line = f"🌐 <b>SMM Panel Balance:</b> ⚠️ <code>{pbal_res.get('error')}</code>"

    sheet_id, creds_raw = _get_google_sheets_config()
    gs_status = "✅ Active" if (sheet_id and creds_raw) else "⚠️ Not Configured (Skipped)"

    text = (
        "📊 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL — ADMIN CONTROL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{pbal_line}\n"
        f"📑 <b>Google Sheets Backup:</b> {gs_status}\n"
        f"👥 <b>মোট ইউজার:</b> {u_cnt} জন\n"
        f"📦 <b>পেন্ডিং/প্রসেসিং অর্ডার:</b> {p_ord} টি\n"
        f"💳 <b>পেন্ডিং ডিপোজিট:</b> {p_dep} টি\n\n"
        "🏦 <b>বর্তমান পেমেন্ট নাম্বারসমূহ:</b>\n"
        f"{payment_numbers_text()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 কন্ট্রোল করতে নিচের বাটন বেছে নিন:"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_menu())


def _fetch_all_users_with_orders() -> list[sqlite3.Row]:
    """Fetches all users with user ID, username, name, wallet balance, total orders, and join date."""
    with _db_lock, db() as conn:
        return conn.execute(
            """
            SELECT u.user_id,
                   u.username,
                   u.first_name,
                   u.balance,
                   u.joined_at,
                   COUNT(o.id) AS total_orders
            FROM users u
            LEFT JOIN orders o ON o.buyer_id = u.user_id
            GROUP BY u.user_id
            ORDER BY u.joined_at DESC, u.user_id ASC
            """
        ).fetchall()


async def send_all_users_csv(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exports all users to a CSV file and sends it directly to the Admin in Telegram."""
    rows = _fetch_all_users_with_orders()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["User ID", "Username", "Name", "Wallet Balance", "Total Orders", "Join Date"]
    )
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "-"
        writer.writerow(
            [
                r["user_id"],
                uname,
                r["first_name"] or "User",
                f"{float(r['balance'] or 0.0):.2f}",
                int(r["total_orders"] or 0),
                r["joined_at"] or "-",
            ]
        )
    csv_bytes = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    csv_bytes.name = f"all_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await context.bot.send_document(
        chat_id=chat_id,
        document=csv_bytes,
        caption=f"📥 <b>All Users Sheet Export ({len(rows)} Users)</b>\nColumns: User ID, Username, Name, Wallet Balance, Total Orders, Join Date",
        parse_mode="HTML",
    )


async def admin_show_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin 'All Users' sheet view: lists ALL users in one place with
    user ID, username, name, wallet balance, total orders, join date, plus 1-click CSV export.
    """
    rows = _fetch_all_users_with_orders()
    if not rows:
        await update.message.reply_text(
            "👥 এখনো কোনো ইউজার রেজিস্টার করেনি।", reply_markup=admin_menu()
        )
        return

    header = (
        f"👥 <b>ALL USERS SHEET (মোট ইউজার: {len(rows)} জন)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>User ID | Username | Name | Balance | Orders | Join Date</i>\n\n"
    )
    export_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📥 Export All Users (CSV Sheet)",
                    callback_data="adm_export_users",
                )
            ]
        ]
    )

    chunks = []
    current = header
    for idx, r in enumerate(rows, start=1):
        uname = f"@{r['username']}" if r["username"] else "—"
        name = (r["first_name"] or "User").replace("<", "").replace(">", "")
        bal = float(r["balance"] or 0.0)
        tot_ord = int(r["total_orders"] or 0)
        jdate = str(r["joined_at"] or "—")[:16]
        line = (
            f"{idx}. <code>{r['user_id']}</code> | {uname} | <b>{name}</b>\n"
            f"   💳 <b>৳{bal:.2f}</b> | 📦 Orders: <b>{tot_ord}</b> | 📅 {jdate}\n"
        )
        if len(current) + len(line) > 3800:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    for i, chunk_text in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await update.message.reply_text(
            chunk_text,
            parse_mode="HTML",
            reply_markup=export_kb if is_last else None,
        )


async def admin_show_pending_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db_lock, db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE LOWER(status) IN ('pending','processing') ORDER BY id ASC LIMIT 15"
        ).fetchall()
    if not rows:
        await update.message.reply_text(
            "✅ কোনো পেন্ডিং বা প্রসেসিং অর্ডার নেই!", reply_markup=admin_menu()
        )
        return
    for r in rows:
        panel_oid = int(r["panel_order_id"] or 0)
        panel_sid = int(r["panel_service_id"] or 0)
        p_cost = float(r["panel_cost"] or 0.0)
        s_price = float(r["sell_price"] or r["price"] or 0.0)
        profit = round(s_price - p_cost, 2) if p_cost > 0 else s_price
        kb = _build_admin_order_kb(r["id"], panel_oid)
        await update.message.reply_text(
            f"🧾 <b>Order #SMM-{r['id']}</b> [{str(r['status']).upper()}]\n"
            f"🌐 <b>Panel Order ID:</b> <code>#{panel_oid}</code> (Panel Service #{panel_sid})\n"
            f"📦 <b>সার্ভিস:</b> {r['service_name']}\n"
            f"🔗 <b>লিংক:</b> <code>{r['target_link']}</code>\n"
            f"🔢 <b>পরিমাণ:</b> {r['quantity']}\n"
            f"💰 <b>Sell Price:</b> ৳{s_price:.2f} | <b>Panel Cost:</b> {p_cost:.4f} | <b>Profit:</b> ৳{profit:.2f}\n"
            f"👤 <b>ক্রেতা:</b> {r['buyer_name']} (<code>{r['buyer_id']}</code>)",
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
            "SELECT COUNT(*) FROM orders WHERE LOWER(status) IN ('completed','approved')"
        ).fetchone()[0]
        s_cnt = conn.execute("SELECT COUNT(*) FROM services WHERE is_active = 1").fetchone()[0]
        tot_pcost = conn.execute(
            "SELECT COALESCE(SUM(panel_cost),0) FROM orders WHERE LOWER(status) NOT IN ('cancelled','rejected')"
        ).fetchone()[0]
        tot_sprice = conn.execute(
            "SELECT COALESCE(SUM(sell_price),0) FROM orders WHERE LOWER(status) NOT IN ('cancelled','rejected')"
        ).fetchone()[0]

    pbal_res = panel_balance()
    if pbal_res.get("ok"):
        pbal_line = f"{pbal_res['balance']} {pbal_res['currency']}"
    else:
        pbal_line = f"⚠️ {pbal_res.get('error')}"

    est_profit = round(float(tot_sprice) - float(tot_pcost), 2)
    text = (
        "📈 <b>বট লাইভ অ্যানালিটিক্স ও রিপোর্ট</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>SMM Panel Balance:</b> <b>{pbal_line}</b>\n"
        f"👥 <b>মোট রেজিস্টার্ড ইউজার:</b> {u_cnt} জন\n"
        f"📋 <b>মোট অ্যাক্টিভ সার্ভিস:</b> {s_cnt} টি\n"
        f"📦 <b>মোট অর্ডার:</b> {o_tot} টি (সম্পন্ন: {o_comp} টি)\n\n"
        "💰 <b>ফাইন্যান্সিয়াল ও প্রফিট রিপোর্ট:</b>\n"
        f"├─ 🏦 <b>মোট ডিপোজিট কালেকশন:</b> ৳{tot_dep:.2f}\n"
        f"├─ 🛒 <b>মোট সার্ভিস সেল:</b> ৳{tot_spn:.2f}\n"
        f"├─ 🌐 <b>মোট প্যানেল খরচ (Panel Cost):</b> {tot_pcost:.2f}\n"
        f"├─ 💹 <b>আনুমানিক প্রফিট:</b> ৳{est_profit:.2f}\n"
        f"└─ 💳 <b>ইউজার ওয়ালেটে জমা আছে:</b> ৳{tot_bal:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_menu())


def _parse_refill_input(raw_text: str) -> tuple[int, str]:
    """
    Parses Admin refill input:
      - '0' or 'no' or 'no refill' -> (0, 'No Refill')
      - '1' or 'yes' -> (1, 'Refill')
      - Custom refill string like 'LifeTime refill', 'REFILL 365D', 'REFILL 30D' -> (1, raw_text)
    """
    val = (raw_text or "").strip()
    low = val.lower()
    if low in ("0", "no", "no refill", "none", "false"):
        return (0, "No Refill")
    if low in ("1", "yes", "true", "refill"):
        return (1, "Refill")
    return (1, val)


def _parse_max_input(raw_text: str) -> int:
    """
    Parses Admin Max input:
      - 'unlimited' or '-1' -> -1 (Unlimited)
      - '0' -> 0 (Not set / Editable in Admin)
      - positive integer -> int
    """
    val = (raw_text or "").strip().lower()
    if val in ("unlimited", "unl", "inf", "-1"):
        return -1
    n = int(val)
    if n < 0:
        raise ValueError
    return n


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
        markup = (
            admin_menu()
            if is_admin(user.id)
            and state.startswith(("add_", "edit_", "bal_", "set_", "broadcast"))
            else main_menu(user.id)
        )
        await update.message.reply_text("❌ বাতিল করা হয়েছে।", reply_markup=markup)
        return True

    # Deposit method selection (Paid File style, Step 1): user must tap a method button, not type.
    if state == ST_DEP_METHOD:
        _enabled = deposit_available_methods()
        if not _enabled:
            await update.message.reply_text(
                "⚠️ বর্তমানে ডিপোজিট বন্ধ আছে। সাহায্যের জন্য Support-এ যোগাযোগ করুন।",
                reply_markup=main_menu(user.id),
            )
            return True
        _kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"{DEPOSIT_METHODS[k][0]} {DEPOSIT_METHODS[k][1]}", callback_data=f"dep_m_{k}")]
                for k in _enabled
            ]
            + [[InlineKeyboardButton("❌ বাতিল করুন", callback_data="dep_cancel_cb")]]
        )
        await update.message.reply_text(
            "💰 <b>ডিপোজিট — 𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💳 <b>ধাপ ১: ডিপোজিট মেথড বাছুন</b>\n"
            "💰 <b>ডিপোজিট লিমিট: মিনিমাম ৳10 - ম্যাক্সিমাম ৳50000</b>\n"
            "নিচের বাটন থেকে একটি মেথড বাছুন (bKash / Nagad / Rocket):",
            parse_mode="HTML",
            reply_markup=_kb,
        )
        return True

    # Step 2: User sends link -> validate link (http/https + platform domain check)
    if state == ST_ORDER_LINK:
        sid = context.user_data.get("order_service_id")
        s = get_service(sid) if sid else None
        if not s or int(s["panel_service_id"] or 0) <= 0:
            context.user_data.clear()
            await update.message.reply_text(
                "⚠️ সার্ভিসটি বর্তমানে অর্ডারের জন্য উপলব্ধ নয়।",
                reply_markup=main_menu(user.id),
            )
            return True

        # Telegram: accept a bare t.me/... link by normalizing it to https://t.me/...
        # so the panel always receives a full URL. Other platforms are unchanged.
        if "telegram" in (s["category"] or "").lower() and text.strip().startswith("t.me/"):
            text = "https://" + text.strip()
        valid, err_msg = validate_service_link(s["category"], s["name"], text)
        if not valid:
            await update.message.reply_text(
                err_msg,
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True

        sell_rate = get_sell_rate_per_1k(s)
        mm_txt = format_min_max(s, admin_view=False)
        context.user_data["order_link"] = text
        context.user_data["state"] = ST_ORDER_QTY
        await update.message.reply_text(
            f"✅ লিংক যাচাই সম্পন্ন: <code>{text}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{platform_emoji_for(s['category'])} <b>{s['name']}</b>\n"
            f"🔢 <b>ধাপ ৪/৫: পরিমাণ (Quantity)</b> কত নিতে চান?\n"
            f"📊 লিমিট: <b>{mm_txt}</b>\n"
            f"💰 মূল্য: <b>{format_price_1k_plain(s['name'], _fmt_num(sell_rate))}</b>\n"
            f"⏱️ Average time: <b>{get_avg_time_label(s)}</b>\n\n"
            "শুধু সংখ্যায় লিখুন (যেমন: <code>1000</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    # Step 3 & Step 4: User sends quantity -> validate Min/Max -> show price & Confirm/Cancel
    if state == ST_ORDER_QTY:
        sid = context.user_data.get("order_service_id")
        s = get_service(sid) if sid else None
        if not s or int(s["panel_service_id"] or 0) <= 0:
            context.user_data.clear()
            await update.message.reply_text(
                "⚠️ সার্ভিস পাওয়া যায়নি। কোনো টাকা কাটা হয়নি।",
                reply_markup=main_menu(user.id),
            )
            return True

        try:
            qty = int(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ শুধুমাত্র পূর্ণসংখ্যায় পরিমাণ লিখুন (যেমন: 1000)। কোনো টাকা কাটা হয়নি:",
                reply_markup=cancel_menu(),
            )
            return True

        min_q = int(s["min_qty"] or 0)
        max_q = int(s["max_qty"] or 0)
        if min_q > 0 and qty < min_q:
            await update.message.reply_text(
                f"⚠️ সর্বনিম্ন অর্ডার পরিমাণ (Min): <b>{min_q}</b>!\n"
                "আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি। সঠিক সংখ্যা লিখুন:",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True
        if max_q > 0 and qty > max_q:
            await update.message.reply_text(
                f"⚠️ সর্বোচ্চ অর্ডার পরিমাণ (Max): <b>{max_q}</b>!\n"
                "আপনার ওয়ালেট থেকে কোনো টাকা কাটা হয়নি। সঠিক সংখ্যা লিখুন:",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True

        sell_rate = get_sell_rate_per_1k(s)
        original_price, disc_pct, final_price = calc_vip_order_price(s, qty, user.id)
        link = context.user_data.get("order_link", "")
        context.user_data["order_qty"] = qty
        context.user_data["state"] = ST_ORDER_CONFIRM

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"✅ Confirm Order (৳{final_price:.2f})",
                        callback_data="order_confirm_cb",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel", callback_data="order_cancel_cb"
                    )
                ],
            ]
        )
        await update.message.reply_text(
            "🧾 <b>𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🧾 <b>ধাপ ৫/৫: অর্ডার কনফার্ম করুন</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{platform_emoji_for(s['category'])} <b>সার্ভিস:</b> {s['name']}\n"
            f"🔗 <b>লিংক:</b> <code>{link}</code>\n"
            f"🔢 <b>পরিমাণ (Quantity):</b> {qty}\n"
            f"🔄 <b>রিফিল:</b> {get_refill_label(s)}\n"
            f"⏱️ <b>Average time:</b> {get_avg_time_label(s)}\n"
            "💰 <b>মূল্য হিসাব:</b>\n"
            f"💰 <b>মূল্য:</b> {format_price_1k_plain(s['name'], _fmt_num(sell_rate))}\n"
            f"💵 <b>Original price:</b> ৳{original_price:.2f}\n"
            f"🎁 <b>VIP Discount:</b> {disc_pct}%\n"
            f"💳 <b>Final price:</b> <b>৳{final_price:.2f}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "👇 অর্ডারটি কনফার্ম করতে নিচের বাটনে চাপুন:",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return True

    # Deposit Step 2 (Paid File style): amount -> show chosen method number + Send Money instructions -> ask TrxID.
    if state == ST_DEP_AMOUNT:
        try:
            amt = float(text)
            if amt < 10 or amt > 50000:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ ভুল অ্যামাউন্ট! ডিপোজিট লিমিট ৳10 - ৳50000 এর মধ্যে হতে হবে। আবার লিখুন।",
                reply_markup=cancel_menu(),
            )
            return True
        _mkey = str(context.user_data.get("dep_method_key") or "")
        if _mkey not in DEPOSIT_METHODS:
            context.user_data["state"] = ST_DEP_METHOD
            _enabled2 = deposit_available_methods()
            _kb2 = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(f"{DEPOSIT_METHODS[k][0]} {DEPOSIT_METHODS[k][1]}", callback_data=f"dep_m_{k}")]
                    for k in _enabled2
                ]
                + [[InlineKeyboardButton("❌ বাতিল করুন", callback_data="dep_cancel_cb")]]
            )
            await update.message.reply_text(
                "💳 <b>ধাপ ১: ডিপোজিট মেথড বাছুন</b>\n"
                "নিচের বাটন থেকে একটি মেথড বাছুন (bKash / Nagad / Rocket):",
                parse_mode="HTML",
                reply_markup=_kb2,
            )
            return True
        if not deposit_method_number(_mkey):
            await update.message.reply_text(
                "❌ এই মাধ্যমটি বর্তমানে চালু নেই। অন্য একটি বেছে নিন।",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["dep_amount"] = round(amt, 2)
        context.user_data["state"] = ST_DEP_TRXID
        await update.message.reply_text(
            deposit_method_instructions(_mkey, amt),
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    # Deposit Step 3 (TrxID) -> Step 4 (Submit for Admin Approval)
    if state == ST_DEP_TRXID:
        method_key = str(context.user_data.get("dep_method_key") or "")
        if method_key not in DEPOSIT_METHODS:
            _m = str(context.user_data.get("dep_method") or "").lower()
            if "nagad" in _m:
                method_key = "nagad"
            elif "rocket" in _m:
                method_key = "rocket"
            else:
                method_key = "bkash"
        method = str(context.user_data.get("dep_method") or deposit_method_label(method_key))
        amt = float(context.user_data.get("dep_amount", 0.0))
        trxid = text.strip().upper()
        if method_key == "bkash":
            if len(trxid) != 10 or not trxid.isalnum():
                await update.message.reply_text(
                    "❌ সঠিক ১০ সংখ্যার/অক্ষরের TrxID লিখুন। পেমেন্ট মেসেজ থেকে TrxID কপি করে আবার পাঠান:",
                    reply_markup=cancel_menu(),
                )
                return True
        else:
            if not trxid:
                await update.message.reply_text(
                    "❌ আপনার TrxID টি পাঠান। পেমেন্ট মেসেজ থেকে TrxID কপি করে আবার পাঠান:",
                    reply_markup=cancel_menu(),
                )
                return True
        if is_trxid_used(trxid):
            await update.message.reply_text(
                "⚠️ এই TrxID আগেই ব্যবহার হয়েছে। অনুগ্রহ করে সঠিক/নতুন TrxID দিন, অথবা ❌ বাতিল করুন চাপুন।",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["dep_trxid"] = trxid
        context.user_data["state"] = ST_DEP_SUBMIT
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ ডিপোজিট সাবমিট করুন (Submit for Approval)",
                        callback_data="dep_submit_cb",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ বাতিল করুন", callback_data="dep_cancel_cb"
                    )
                ],
            ]
        )
        await update.message.reply_text(
            "📤 <b>ধাপ ৪: ডিপোজিট সাবমিট কনফার্মেশন</b>\n"
            "─────────\n"
            f"🌸 <b>মেথড:</b> {method}\n"
            f"💰 <b>পরিমাণ:</b> <b>৳{amt:.2f}</b>\n"
            f"🔢 <b>TrxID:</b> <code>{trxid}</code>\n"
            "─────────\n"
            "👇 অ্যাডমিন অ্যাপ্রুভালের জন্য জমা দিতে নিচের বাটনে চাপুন:",
            parse_mode="HTML",
            reply_markup=kb,
        )
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

    # Admin Add SMM Service (service name, panel_service_id, panel rate, sell price, Min, Max, refill)
    if state == ST_ADD_SMM_CAT:
        context.user_data["smm_cat"] = text
        context.user_data["state"] = ST_ADD_SMM_NAME
        await update.message.reply_text(
            "📝 <b>সার্ভিসের নাম (Service Name)</b> লিখুন (যেমন: <code>Instagram Followers</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_NAME:
        context.user_data["smm_name"] = text
        context.user_data["state"] = ST_ADD_SMM_PID
        await update.message.reply_text(
            "🌐 প্যানেলের <b>panel_service_id</b> লিখুন (যেমন: <code>11432</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_PID:
        try:
            pid = int(text)
            if pid <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "সঠিক পূর্ণসংখ্যা panel_service_id লিখুন (যেমন: 11432):",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["smm_pid"] = pid
        context.user_data["state"] = ST_ADD_SMM_PRATE
        await update.message.reply_text(
            "💵 প্যানেলে প্রতি ১০০০ এর রেট (<b>Panel Rate per 1000</b>) কত? (যেমন: <code>486.72</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_PRATE:
        try:
            prate = float(text)
            if prate < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "সঠিক সংখ্যা লিখুন (যেমন: 486.72):", reply_markup=cancel_menu()
            )
            return True
        context.user_data["smm_prate"] = prate
        context.user_data["state"] = ST_ADD_SMM_RATE
        await update.message.reply_text(
            "💰 কাস্টমারের জন্য প্রতি ১০০০ এর বিক্রয় মূল্য (<b>Sell Price per 1000</b>) কত টাকা? (যেমন: <code>600</code>):",
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
            await update.message.reply_text("সঠিক সংখ্যা লিখুন (যেমন: 600):", reply_markup=cancel_menu())
            return True
        context.user_data["smm_rate"] = rate
        context.user_data["state"] = ST_ADD_SMM_MIN
        await update.message.reply_text(
            "📉 সর্বনিম্ন অর্ডার সংখ্যা (<b>Min Quantity</b>) কত? (যেমন: <code>100</code>, অথবা নির্ধারণ না করতে <code>0</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_MIN:
        try:
            mn = int(text)
            if mn < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("সঠিক পূর্ণসংখ্যা লিখুন (যেমন: 100 বা 0):", reply_markup=cancel_menu())
            return True
        context.user_data["smm_min"] = mn
        context.user_data["state"] = ST_ADD_SMM_MAX
        await update.message.reply_text(
            "📈 সর্বোচ্চ অর্ডার সংখ্যা (<b>Max Quantity</b>) কত? (যেমন: <code>500000</code>, আনলিমিটেড হলে <code>unlimited</code>, বা <code>0</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_MAX:
        try:
            mx = _parse_max_input(text)
        except ValueError:
            await update.message.reply_text(
                "সঠিক সংখ্যা অথবা unlimited লিখুন (যেমন: 500000 বা unlimited):",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["smm_max"] = mx
        context.user_data["state"] = ST_ADD_SMM_REFILL
        await update.message.reply_text(
            "🔄 এই সার্ভিসের <b>Refill</b> তথ্য লিখুন (যেমন: <code>LifeTime refill</code>, <code>REFILL 365D</code>, <code>REFILL 30D</code>, অথবা না থাকলে <code>No Refill</code> / <code>0</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_REFILL:
        ref_en, ref_label = _parse_refill_input(text)
        context.user_data["smm_refill"] = ref_en
        context.user_data["smm_refill_type"] = ref_label
        context.user_data["state"] = ST_ADD_SMM_DESC
        await update.message.reply_text(
            "📝 সার্ভিসের সংক্ষিপ্ত বিবরণ লিখুন (অথবা ডিফল্ট বিবরণ রাখতে <code>.</code> লিখুন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_SMM_DESC:
        cat = context.user_data.get("smm_cat", "📸 Instagram")
        name = context.user_data.get("smm_name", "SMM Service")
        pid = int(context.user_data.get("smm_pid", 0))
        prate = float(context.user_data.get("smm_prate", 0.0))
        rate = float(context.user_data.get("smm_rate", 100.0))
        mn = int(context.user_data.get("smm_min", 0))
        mx = int(context.user_data.get("smm_max", 0))
        refill_en = int(context.user_data.get("smm_refill", 0))
        refill_type = str(context.user_data.get("smm_refill_type", "No Refill"))
        mk = round(rate - prate, 4) if prate > 0 else 0.0
        desc = (
            f"⏱️ Average time: 2-6 hours | 🔄 {refill_type}"
            if text.strip() == "."
            else text.strip()
        )
        context.user_data.clear()
        with _db_lock, db() as conn:
            conn.execute(
                """
                INSERT INTO services
                (category, name, service_type, rate_per_1k, min_qty, max_qty, description,
                 file_id, panel_service_id, refill_enabled, refill_type, avg_time,
                 panel_rate, markup, is_active)
                VALUES (?, ?, 'smm', ?, ?, ?, ?, NULL, ?, ?, ?, '2-6 hours', ?, ?, 1)
                """,
                (cat, name, rate, mn, mx, desc, pid, refill_en, refill_type, prate, mk),
            )
            conn.commit()
        await update.message.reply_text(
            f"✅ <b>নতুন SMM সার্ভিস অ্যাড হয়েছে!</b>\n"
            f"💠 ক্যাটাগরি: {cat}\n"
            f"📦 নাম: {name}\n"
            f"🌐 Panel Service ID: <code>#{pid}</code> | 🔄 Refill: <code>{refill_type}</code>\n"
            f"💵 Panel Rate: {_fmt_num(prate)}/1000 | 💰 Sell Price: ৳{_fmt_num(rate)}/1000\n"
            f"📉 Min: {mn if mn > 0 else 'Editable'} | 📈 Max: {'Unlimited' if mx == -1 else (mx if mx > 0 else 'Editable')}",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return True

    # Admin Edit SMM Service States
    if state == ST_EDIT_SMM_NAME:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        context.user_data["edit_name"] = (
            s["name"] if (text == "." and s) else text
        )
        context.user_data["state"] = ST_EDIT_SMM_PID
        await update.message.reply_text(
            f"🌐 <b>ধাপ ২/৮:</b> প্যানেলের <b>panel_service_id</b> লিখুন (বর্তমান: <code>{s['panel_service_id'] if s else 0}</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_PID:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            pid = int(s["panel_service_id"])
        else:
            try:
                pid = int(text)
                if pid <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("সঠিক পূর্ণসংখ্যা panel_service_id লিখুন:", reply_markup=cancel_menu())
                return True
        context.user_data["edit_pid"] = pid
        context.user_data["state"] = ST_EDIT_SMM_PRATE
        await update.message.reply_text(
            f"💵 <b>ধাপ ৩/৮:</b> প্যানেলের রেট (<b>Panel Rate per 1000</b>) লিখুন (বর্তমান: <code>{_fmt_num(s['panel_rate']) if s else 0}</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_PRATE:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            prate = float(s["panel_rate"])
        else:
            try:
                prate = float(text)
                if prate < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("সঠিক সংখ্যা লিখুন:", reply_markup=cancel_menu())
                return True
        context.user_data["edit_prate"] = prate
        context.user_data["state"] = ST_EDIT_SMM_RATE
        await update.message.reply_text(
            f"💰 <b>ধাপ ৪/৮:</b> কাস্টমারের বিক্রয় মূল্য (<b>Sell Price per 1000</b>) লিখুন (বর্তমান: <code>{_fmt_num(get_sell_rate_per_1k(s)) if s else 100}</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_RATE:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            rate = get_sell_rate_per_1k(s)
        else:
            try:
                rate = float(text)
                if rate <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("সঠিক সংখ্যা লিখুন:", reply_markup=cancel_menu())
                return True
        context.user_data["edit_rate"] = rate
        context.user_data["state"] = ST_EDIT_SMM_MIN
        await update.message.reply_text(
            f"📉 <b>ধাপ ৫/৮:</b> সর্বনিম্ন অর্ডার (<b>Min Quantity</b>) লিখুন (বর্তমান: <code>{s['min_qty'] if s else 0}</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_MIN:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            mn = int(s["min_qty"])
        else:
            try:
                mn = int(text)
                if mn < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("সঠিক পূর্ণসংখ্যা লিখুন (যেমন: 100 বা 0):", reply_markup=cancel_menu())
                return True
        context.user_data["edit_min"] = mn
        context.user_data["state"] = ST_EDIT_SMM_MAX
        await update.message.reply_text(
            f"📈 <b>ধাপ ৬/৮:</b> সর্বোচ্চ অর্ডার (<b>Max Quantity</b>) লিখুন (বর্তমান: <code>{'unlimited' if (s and int(s['max_qty']) == -1) else (s['max_qty'] if s else 0)}</code>, আনলিমিটেড হলে <code>unlimited</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_MAX:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            mx = int(s["max_qty"])
        else:
            try:
                mx = _parse_max_input(text)
            except ValueError:
                await update.message.reply_text("সঠিক সংখ্যা অথবা unlimited লিখুন:", reply_markup=cancel_menu())
                return True
        context.user_data["edit_max"] = mx
        context.user_data["state"] = ST_EDIT_SMM_REFILL
        await update.message.reply_text(
            f"🔄 <b>ধাপ ৭/৮:</b> এই সার্ভিসের <b>Refill</b> তথ্য লিখুন (বর্তমান: <code>{get_refill_label(s) if s else 'No Refill'}</code>, অপরিবর্তিত রাখতে <code>.</code> দিন):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_REFILL:
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        if text == "." and s:
            ref_en = int(s["refill_enabled"] or 0)
            ref_label = get_refill_label(s)
        else:
            ref_en, ref_label = _parse_refill_input(text)
        context.user_data["edit_refill"] = ref_en
        context.user_data["edit_refill_type"] = ref_label
        context.user_data["state"] = ST_EDIT_SMM_ACTIVE
        await update.message.reply_text(
            "✅ <b>ধাপ ৮/৮:</b> সার্ভিসটি কি চালু (Active) রাখবেন? (চালু রাখতে <code>1</code>, বন্ধ রাখতে <code>0</code>):",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_EDIT_SMM_ACTIVE:
        if text not in ("0", "1"):
            await update.message.reply_text("শুধুমাত্র 1 অথবা 0 লিখুন:", reply_markup=cancel_menu())
            return True
        sid = int(context.user_data.get("edit_sid", 0))
        s = get_service(sid)
        name = str(context.user_data.get("edit_name") or (s["name"] if s else "SMM Service"))
        pid = int(context.user_data.get("edit_pid", 0))
        prate = float(context.user_data.get("edit_prate", 0.0))
        rate = float(context.user_data.get("edit_rate", 100.0))
        mn = int(context.user_data.get("edit_min", 0))
        mx = int(context.user_data.get("edit_max", 0))
        refill_en = int(context.user_data.get("edit_refill", 0))
        refill_type = str(context.user_data.get("edit_refill_type", "No Refill"))
        is_act = int(text)
        mk = round(rate - prate, 4) if prate > 0 else 0.0
        context.user_data.clear()
        with _db_lock, db() as conn:
            conn.execute(
                """
                UPDATE services
                SET name = ?, panel_service_id = ?, panel_rate = ?, markup = ?, rate_per_1k = ?,
                    min_qty = ?, max_qty = ?, refill_enabled = ?, refill_type = ?, is_active = ?
                WHERE id = ?
                """,
                (name, pid, prate, mk, rate, mn, mx, refill_en, refill_type, is_act, sid),
            )
            conn.commit()
        await update.message.reply_text(
            f"✅ <b>সার্ভিস #{sid} ({name}) সফলভাবে আপডেট হয়েছে!</b>\n"
            f"🌐 Panel Service ID: <code>#{pid}</code>\n"
            f"💵 Panel Rate: {_fmt_num(prate)}/1000 | 💰 Sell Price: ৳{_fmt_num(rate)}/1000\n"
            f"📉 Min: {mn if mn > 0 else 'Editable'} | 📈 Max: {'Unlimited' if mx == -1 else (mx if mx > 0 else 'Editable')}\n"
            f"🔄 Refill: {refill_type} | Active: {is_act}",
            parse_mode="HTML",
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
        new_b = add_balance(
            target_uid, delta, reason="Admin Manual Balance Adjustment"
        )
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

    # Admin Force Channel States: add one by one, remove one, list, clear all.
    if state == ST_SET_FORCE_CHANNEL:
        raw = text.strip()
        low = raw.lower()
        if low in ("clear", "off", "none", "বন্ধ"):
            set_force_channels([])
            context.user_data.clear()
            await update.message.reply_text(
                "✅ <b>Force Channels সব মুছে দেওয়া হয়েছে।</b> এখন কোনো Force Join চেক হবে না।",
                parse_mode="HTML",
                reply_markup=admin_menu(),
            )
            return True
        if low == "list":
            await update.message.reply_text(
                f"📋 <b>বর্তমান Force Channels:</b> {format_force_channels()}\n\n"
                "আরেকটি যোগ করতে চ্যানেল ইউজারনেম লিখুন, মুছতে <code>remove ইউজারনেম</code> লিখুন, সব মুছতে <code>clear</code> লিখুন।",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True
        if low.startswith("remove "):
            target = _clean_force_channel(raw[7:])
            if not target:
                await update.message.reply_text(
                    "❌ সঠিক চ্যানেল ইউজারনেম লিখুন (যেমন: <code>remove ইউজারনেম</code>):",
                    parse_mode="HTML",
                    reply_markup=cancel_menu(),
                )
                return True
            chs = get_force_channels()
            if target in chs:
                chs.remove(target)
                set_force_channels(chs)
                await update.message.reply_text(
                    f"✅ <b>@{target}</b> Force Channel থেকে মুছে দেওয়া হয়েছে।\n📋 বর্তমান: {format_force_channels()}",
                    parse_mode="HTML",
                    reply_markup=cancel_menu(),
                )
            else:
                await update.message.reply_text(
                    f"⚠️ <b>@{target}</b> Force Channels-এ নেই।\n📋 বর্তমান: {format_force_channels()}",
                    parse_mode="HTML",
                    reply_markup=cancel_menu(),
                )
            return True
        cleaned = _clean_force_channel(raw)
        if not cleaned:
            await update.message.reply_text(
                "❌ সঠিক চ্যানেল ইউজারনেম লিখুন (@ ছাড়া, শুধু অক্ষর/সংখ্যা/_):",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True
        chs = get_force_channels()
        if cleaned in chs:
            await update.message.reply_text(
                f"⚠️ <b>@{cleaned}</b> ইতিমধ্যেই Force Channels-এ আছে।\n📋 বর্তমান: {format_force_channels()}",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return True
        chs.append(cleaned)
        set_force_channels(chs)
        await update.message.reply_text(
            f"✅ <b>@{cleaned}</b> Force Channel-এ যোগ করা হয়েছে।\n📋 বর্তমান: {format_force_channels()}\n\n"
            "আরেকটি যোগ করতে আরেকটি ইউজারনেম লিখুন, অথবা 🔙 মেইন মেনু চাপুন।",
            parse_mode="HTML",
            reply_markup=cancel_menu(),
        )
        return True

    # Admin Settings States
    if state in (
        ST_SET_BKASH,
        ST_SET_NAGAD,
        ST_SET_ROCKET,
        ST_SET_BINANCE,
        ST_SET_SUPPORT,
        ST_SET_DAILY_BONUS,
        ST_SET_REFER_BONUS,
    ):
        key_map = {
            ST_SET_BKASH: ("bkash_number", "bKash Number"),
            ST_SET_NAGAD: ("nagad_number", "Nagad Number"),
            ST_SET_ROCKET: ("rocket_number", "Rocket Number"),
            ST_SET_BINANCE: ("binance_id", "Binance Pay ID"),
            ST_SET_SUPPORT: ("support_username", "Support Username"),
            ST_SET_DAILY_BONUS: ("daily_bonus", "Daily Bonus (৳)"),
            ST_SET_REFER_BONUS: ("refer_bonus_pct", "Refer Bonus (%)"),
        }
        k, label = key_map[state]
        if state == ST_SET_REFER_BONUS:
            try:
                _rv = float(text.strip().replace("%", ""))
            except ValueError:
                await update.message.reply_text(
                    "❌ সঠিক সংখ্যা লিখুন (যেমন: 10):",
                    reply_markup=cancel_menu(),
                )
                return True
            if _rv < 0 or _rv > 100:
                await update.message.reply_text(
                    "❌ Refer Bonus 0% থেকে 100% এর মধ্যে হতে হবে:",
                    reply_markup=cancel_menu(),
                )
                return True
            text = str(_rv)
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
    return


async def handle_admin_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    data = query.data or ""
    # Toggle deposit method ON/OFF (Paid File bot style admin option).
    if data.startswith("adm_toggle_"):
        _tk = data[len("adm_toggle_"):]
        if _tk in DEPOSIT_METHODS:
            _cur = deposit_method_enabled(_tk)
            set_setting(f"deposit_{_tk}_enabled", "0" if _cur else "1")
            _nm = DEPOSIT_METHODS[_tk][1]
            await query.edit_message_text(
                f"✅ {_nm} ডিপোজিট এখন {'OFF' if _cur else 'ON'} করা হয়েছে।\n\n{payment_numbers_text()}",
                parse_mode="HTML",
            )
        return
    if data == "adm_force_list":
        await query.edit_message_text(
            f"📋 <b>বর্তমান Force Channels:</b> {format_force_channels()}\n\n"
            "যোগ/মুছতে বোনাস ও সাপোর্ট সেটিংস থেকে 📢 Force Channel Set চাপুন।",
            parse_mode="HTML",
        )
        return
    if data == "adm_force_clear":
        set_force_channels([])
        await query.edit_message_text(
            "✅ <b>Force Channels সব মুছে দেওয়া হয়েছে।</b> এখন কোনো Force Join চেক হবে না।",
            parse_mode="HTML",
        )
        return
    prompts = {
        "adm_set_force": (
            ST_SET_FORCE_CHANNEL,
            "📢 <b>Force Channel সেট করুন</b>\n\n"
            f"📋 বর্তমান: {format_force_channels()}\n\n"
            "একটি করে যোগ করতে চ্যানেল ইউজারনেম লিখে পাঠান (@ ছাড়া)।\n"
            "মুছতে: <code>remove ইউজারনেম</code> লিখুন।\n"
            "সব মুছতে: <code>clear</code> লিখুন।\n"
            "তালিকা দেখতে: <code>list</code> লিখুন।",
        ),
        "adm_set_bkash": (ST_SET_BKASH, "🌸 নতুন <b>bKash Number</b> লিখে পাঠান:"),
        "adm_set_nagad": (ST_SET_NAGAD, "🧡 নতুন <b>Nagad Number</b> লিখে পাঠান:"),
        "adm_set_rocket": (ST_SET_ROCKET, "💜 নতুন <b>Rocket Number</b> লিখে পাঠান:"),
        "adm_set_binance": (ST_SET_BINANCE, "🟡 নতুন <b>Binance Pay ID / USDT Address</b> লিখে পাঠান:"),
        "adm_set_support": (ST_SET_SUPPORT, "🛟 <b>Support Username</b> লিখুন (@ ছাড়া):"),
        "adm_set_daily": (ST_SET_DAILY_BONUS, "🎯 ডেইলি বোনাস কত টাকা দিবেন? (সংখ্যা লিখুন, যেমন: 1):"),
        "adm_set_refer": (ST_SET_REFER_BONUS, "🎁 নতুন <b>Refer Bonus %</b> লিখুন (যেমন: 10):"),
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

    # 1. Mini App Admin Add Service
    if action == "add_product" and is_admin(user.id):
        pname = str(data.get("name", "")).strip()
        pdesc = str(data.get("desc", "")).strip() or "⏱️ Average time: 2-6 hours"
        try:
            pprice = float(data.get("price", 100))
        except (ValueError, TypeError):
            pprice = 100.0
        try:
            pid = int(data.get("panel_service_id", 0) or 0)
        except (ValueError, TypeError):
            pid = 0
        if pname and pprice > 0 and pid > 0:
            with _db_lock, db() as conn:
                conn.execute(
                    """
                    INSERT INTO services
                    (category, name, service_type, rate_per_1k, min_qty, max_qty, description,
                     file_id, panel_service_id, refill_enabled, refill_type, avg_time,
                     panel_rate, markup, is_active)
                    VALUES ('📸 Instagram', ?, 'smm', ?, 0, 0, ?, NULL, ?, 0, 'No Refill', '2-6 hours', 0.0, 0.0, 1)
                    """,
                    (pname, pprice, pdesc, pid),
                )
                conn.commit()
            await update.message.reply_text(
                f"✅ Mini App থেকে সার্ভিস অ্যাড হয়েছে: {pname} — ৳{_fmt_num(pprice)}/1000 (Panel ID #{pid})",
                reply_markup=main_menu(user.id),
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
            f"🏦 মেথড: {method} | 💰 ৳{amount:.2f} | TrxID: {trxid}",
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

    # 3. Mini App Order (Enforces Panel API & panel_service_id checks)
    sid = int(data.get("service_id", 0) or 0)
    s = get_service(sid) if sid else None
    name = str(data.get("product") or data.get("product_name") or (s["name"] if s else "")).strip()
    link = str(data.get("link") or data.get("target_link") or "").strip()
    try:
        qty = int(data.get("quantity", 1) or 1)
    except (ValueError, TypeError):
        qty = 1

    if not s or int(s["panel_service_id"] or 0) <= 0:
        await update.message.reply_text(
            "⚠️ এই সার্ভিসটি বর্তমানে অর্ডারের জন্য উপলব্ধ নয়।",
            reply_markup=main_menu(user.id),
        )
        return

    api_url, api_key = _get_smm_api_config()
    if not api_url or not api_key:
        await update.message.reply_text("⚠️ Panel API not set - contact admin", reply_markup=main_menu(user.id))
        return

    valid_link, link_err = validate_service_link(s["category"], s["name"], link)
    if not valid_link:
        await update.message.reply_text(link_err, parse_mode="HTML", reply_markup=main_menu(user.id))
        return

    min_q = int(s["min_qty"] or 0)
    max_q = int(s["max_qty"] or 0)
    if qty <= 0 or (min_q > 0 and qty < min_q) or (max_q > 0 and qty > max_q):
        await update.message.reply_text(
            f"⚠️ পরিমাণ সঠিক নয় ({format_min_max(s)})!",
            reply_markup=main_menu(user.id),
        )
        return

    _, _, sell_price = calc_vip_order_price(s, qty, user.id)
    panel_cost = calc_panel_cost(s, qty)
    bal = get_balance(user.id)
    if bal < sell_price:
        await update.message.reply_text(
            f"⚠️ ব্যালেন্স কম! প্রয়োজন ৳{sell_price:.2f}, আপনার আছে ৳{bal:.2f}। আগে ডিপোজিট করুন।",
            reply_markup=main_menu(user.id),
        )
        return

    pbal_res = panel_balance()
    if not pbal_res.get("ok") or float(pbal_res.get("balance", 0.0)) < panel_cost:
        await update.message.reply_text(
            "⚠️ প্যানেলে পর্যাপ্ত ব্যালেন্স নেই বা প্যানেল সংযোগ ব্যর্থ হয়েছে। কোনো টাকা কাটা হয়নি।",
            reply_markup=main_menu(user.id),
        )
        return

    pid = int(s["panel_service_id"])
    add_res = panel_add_order(pid, link, qty)
    if not add_res.get("ok"):
        await update.message.reply_text(
            f"❌ প্যানেল অর্ডার গ্রহণ করেনি: {add_res.get('error')}\nকোনো টাকা কাটা হয়নি।",
            reply_markup=main_menu(user.id),
        )
        return

    panel_oid = int(add_res["order"])
    add_balance(
        user.id, -sell_price, reason=f"Mini App Order ({name} x{qty})"
    )
    record_spend(user.id, sell_price)
    with _db_lock, db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders
            (service_id, service_name, buyer_id, buyer_name, target_link, quantity,
             price, status, panel_order_id, panel_service_id, panel_cost, sell_price,
             starts_count, remains, refunded, refer_bonus_paid)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, ?, 0, ?, 0, 0)
            """,
            (
                s["id"],
                name,
                user.id,
                user.first_name or "User",
                link,
                qty,
                sell_price,
                panel_oid,
                pid,
                panel_cost,
                sell_price,
                qty,
            ),
        )
        oid = cur.lastrowid
        conn.commit()

    schedule_order_sheet_backup(oid)

    await update.message.reply_text(
        f"✅ Mini App অর্ডার সফল (#SMM-{oid} | Panel #{panel_oid})!\n📦 {name}\n💰 ৳{sell_price:.2f}",
        reply_markup=main_menu(user.id),
    )
    await notify_admin_new_order(
        context,
        oid,
        name,
        user,
        link,
        qty,
        sell_price,
        panel_order_id=panel_oid,
        panel_service_id=pid,
        panel_cost=panel_cost,
    )


# ---------------------------------------------------------------------------
# Master Text Message Dispatcher
# ---------------------------------------------------------------------------
ALL_MENU_BUTTONS = {
    BTN_NEW_ORDER,
    "🚀 নতুন অর্ডার (SMM)",
    "নতুন অর্ডার",
    BTN_MINIAPP,
    BTN_SERVICES,
    "সার্ভিস লিস্ট",
    BTN_MY_ORDERS,
    "আমার অর্ডার",
    BTN_DEPOSIT,
    "ডিপোজিট",
    BTN_PROFILE,
    "প্রোফাইল",
    BTN_REFER,
    "রেফারেল",
    BTN_LEADERBOARD,
    BTN_MISSION,
    BTN_SUPPORT,
    BTN_ADMIN,
    BTN_BACK,
    BTN_A_ADD_SMM,
    BTN_A_EDIT_SMM,
    BTN_A_USERS,
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

    if text in (BTN_NEW_ORDER, "🚀 নতুন অর্ডার (SMM)", "নতুন অর্ডার"):
        if not await ensure_force_join(update, context):
            return
        await show_categories(update, context)
        return

    if text in (BTN_SERVICES, "সার্ভিস লিস্ট"):
        # Standalone Service List button removed from main menu (2026-10-29).
        # Old text kept harmless: it now opens the অর্ডার করুন flow, where
        # services are picked (platform > service). show_all_services_list
        # is retained but no longer reachable from the main menu.
        if not await ensure_force_join(update, context):
            return
        await show_categories(update, context)
        return

    if text in (BTN_MY_ORDERS, "আমার অর্ডার"):
        if not await ensure_force_join(update, context):
            return
        await show_my_orders(update, context)
        return

    if text in (BTN_DEPOSIT, "ডিপোজিট"):
        if not await ensure_force_join(update, context):
            return
        await start_deposit_flow(update, context)
        return

    if text in (BTN_PROFILE, "প্রোফাইল"):
        if not await ensure_force_join(update, context):
            return
        await show_profile(update, context)
        return

    if text in (BTN_REFER, "রেফারেল"):
        if not await ensure_force_join(update, context):
            return
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
            await update.message.reply_text("⛔ শুধুমাত্র অ্যাডমিন প্রবেশ করতে পারবে।", reply_markup=main_menu(user.id))
            return
        await show_admin_panel(update, context)
        return

    if is_admin(user.id):
        if text == BTN_A_ADD_SMM:
            context.user_data["state"] = ST_ADD_SMM_CAT
            await update.message.reply_text(
                "➕ <b>নতুন SMM সার্ভিস অ্যাড:</b>\n\n"
                "💠 প্রথমে ক্যাটাগরির নাম লিখুন (যেমন: <code>📸 Instagram</code>, <code>🎵 TikTok</code>, <code>📘 Facebook</code>, <code>▶️ YouTube</code>):",
                parse_mode="HTML",
                reply_markup=cancel_menu(),
            )
            return

        if text == BTN_A_EDIT_SMM:
            svcs = list_services(include_disabled=True)
            if not svcs:
                await update.message.reply_text("কোনো সার্ভিস নেই।", reply_markup=admin_menu())
                return
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"✏️ #{s['id']} {s['name']} (PID:{s['panel_service_id']} | ৳{_fmt_num(get_sell_rate_per_1k(s))})",
                            callback_data=f"edit_{s['id']}",
                        )
                    ]
                    for s in svcs
                ]
            )
            await update.message.reply_text(
                "✏️ <b>যে সার্ভিসটির Name / Panel ID / Rate / Min / Max / Refill এডিট করতে চান সেটিতে চাপুন:</b>",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        if text == BTN_A_USERS:
            await admin_show_all_users(update, context)
            return

        if text == BTN_A_LIST:
            await admin_show_all_services(update, context)
            return

        if text == BTN_A_DEL:
            svcs = list_services(include_disabled=True)
            if not svcs:
                await update.message.reply_text("কোনো সার্ভিস নেই।", reply_markup=admin_menu())
                return
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"🗑️ #{s['id']} {s['name']} (৳{_fmt_num(get_sell_rate_per_1k(s))})",
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
                    [
                        InlineKeyboardButton(
                            f"🔄 Toggle bKash: {'ON' if deposit_method_enabled('bkash') else 'OFF'}",
                            callback_data="adm_toggle_bkash",
                        ),
                        InlineKeyboardButton(
                            f"🔄 Toggle Nagad: {'ON' if deposit_method_enabled('nagad') else 'OFF'}",
                            callback_data="adm_toggle_nagad",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            f"🔄 Toggle Rocket: {'ON' if deposit_method_enabled('rocket') else 'OFF'}",
                            callback_data="adm_toggle_rocket",
                        ),
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
                            "🎯 Daily Bonus সেট", callback_data="adm_set_daily"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎁 Refer Bonus সেট", callback_data="adm_set_refer"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📢 Force Channel Set", callback_data="adm_set_force"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "📋 Force Channels List", callback_data="adm_force_list"
                        ),
                        InlineKeyboardButton(
                            "🗑️ Clear All Force Channels", callback_data="adm_force_clear"
                        ),
                    ],
                ]
            )
            await update.message.reply_text(
                "🎁 <b>বোনাস ও সাপোর্ট সেটিংস:</b>\n"
                f"├─ Support: @{get_setting('support_username', 'Not Set')}\n"
                f"├─ Daily Bonus: ৳{get_setting('daily_bonus', '1')}\n"
                f"├─ Refer Bonus: {get_refer_bonus_pct():g}%\n"
                f"└─ Force Channels: {format_force_channels()}",
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
    return "𝗧𝗿𝘂𝘀𝘁𝗩𝗮𝘂𝗹𝘁 SMM PANEL Bot is Running 24/7 OK", 200


@app.route("/ping")
def ping():
    return "OK", 200


@app.route("/api/services")
def api_services():
    svcs = list_services(include_disabled=False)
    return jsonify(
        {
            "services": [
                {
                    "id": s["id"],
                    "category": s["category"],
                    "name": s["name"],
                    "type": s["service_type"],
                    "rate_per_1k": get_sell_rate_per_1k(s),
                    "min_qty": s["min_qty"],
                    "max_qty": s["max_qty"],
                    "description": s["description"],
                    "panel_service_id": s["panel_service_id"],
                    "refill_enabled": s["refill_enabled"],
                    "refill_type": get_refill_label(s),
                    "avg_time": get_avg_time_label(s),
                    "linked": int(s["panel_service_id"] or 0) > 0,
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
            "SELECT id, service_id, service_name, status, price, panel_order_id FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 20",
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
                    "status": "approved" if str(r["status"]).lower() == "completed" else str(r["status"]).lower(),
                    "price": r["price"],
                    "panel_order_id": r["panel_order_id"],
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
            "SELECT 1 FROM orders WHERE service_id = ? AND buyer_id = ? AND LOWER(status) IN ('completed','approved') LIMIT 1",
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
            pattern="^(open_main_menu|open_cats|open_deposit|cat_[0-9]+|srv_[0-9]+|order_confirm_cb|order_cancel_cb)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_user_order_actions_cb,
            pattern="^(usr_chk_[0-9]+|usr_drop_[0-9]+|usr_refill_[0-9]+)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_deposit_method_cb, pattern="^(dep_m_|dep_submit_cb$|dep_cancel_cb$)")
    )
    application.add_handler(
        CallbackQueryHandler(handle_claim_daily_cb, pattern="^claim_daily$")
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_admin_decisions_cb,
            pattern="^(approve_bal_|reject_bal_|ord_proc_|ord_comp_|ord_canc_|approve_|reject_|adm_chk_|adm_ref_|adm_export_users$)",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_delete_service_cb, pattern="^del_[0-9]+$")
    )
    application.add_handler(
        CallbackQueryHandler(handle_edit_service_cb, pattern="^edit_[0-9]+$")
    )
    application.add_handler(CallbackQueryHandler(handle_force_check_cb, pattern="^force_check$"))
    application.add_handler(
        CallbackQueryHandler(handle_admin_settings_cb, pattern="^adm_(set|toggle|force)_")
    )

    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_error_handler(error_handler)

    logger.info("TrustVault SMM PANEL Bot is running...")
    application.run_polling(close_loop=False, drop_pending_updates=True)


if __name__ == "__main__":
    main()