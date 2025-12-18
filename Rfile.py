
# bot.py  (Merged final with language persistence fixes - bug fixed + Fulani added)
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
import sqlite3
# ====== DATABASE CONNECTION ======
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "main.db")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

# Create movies table if not exists
conn.execute("""
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    price INTEGER,
    file_id TEXT,
    created_at TEXT,
    channel_msg_id INTEGER,
    channel_username TEXT
)
""")
conn.commit()
# Create table (an bada error saboda wannan ya kasance a sama)
conn.execute("CREATE TABLE IF NOT EXISTS buyall_tokens (token TEXT PRIMARY KEY, ids TEXT)")
conn.commit()

# ===============================
# ORDER SYSTEM V2 (SAFE ADDITION)
# ===============================
conn.execute("""
CREATE TABLE IF NOT EXISTS orders_v2 (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    total_amount INTEGER NOT NULL,
    source TEXT NOT NULL,
    payment_ref TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS order_items_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    movie_id INTEGER NOT NULL,
    price INTEGER NOT NULL
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS user_library (
    user_id INTEGER NOT NULL,
    movie_id INTEGER NOT NULL,
    acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, movie_id)
)
""")

conn.commit()



import uuid
import re
import json
import requests
import traceback
import random
import difflib
from datetime import datetime, timedelta
import urllib.parse
admin_states = {}
# --- Admins configuration ---
ADMINS = [6210912739]  # add more admin IDs here

# ========= CONFIG =========
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 6210912739
OTP_ADMIN_ID = 6603268127

CHANNEL = "@yayanebroo"
BOT_USERNAME = "Aslamtv2bot"
# Flutterwave config
FLUTTERWAVE_SECRET = os.getenv("FLUTTERWAVE_SECRET")
FLUTTERWAVE_PUBLIC = os.getenv("FLUTTERWAVE_PUBLIC")

# === PAYMENTS / STORAGE PLACEHOLDERS (fill later) ===
# Set real IDs before deploy:
PAYMENT_NOTIFY_GROUP = -1002372677956  # e.g. -1001234567890 (private group where payment notifications are sent)
STORAGE_CHANNEL = -1003478646839       # e.g. -1009876543210 (private channel where admin movie files are stored)
# Disable admin direct payment notifications (we will send to PAYMENT_NOTIFY_GROUP only)
SEND_ADMIN_PAYMENT_NOTIF = False
# =====================================================

FLW_BASE = "https://api.flutterwave.com/v3"
# Replace redirect URL placeholders in the payment init sections below.

PAYSTACK_SECRET = None   # set real secret to enable Paystack
# Optional: if you want Message-to-Admin to provide a direct t.me link, set ADMIN_USERNAME (without @)
ADMIN_USERNAME = "Nazifiibr"  # e.g. "Aslamtv2" or None
# ============================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")


# ================== RUKUNI C (FINAL) ==================

SEARCH_PAGE_SIZE = 5


# ---------- NORMALIZE ----------
def _norm(t):
    return (t or "").lower().strip()


# ---------- GET ALL MOVIES ----------
def _get_all_movies():
    """
    id | title | price | file_name | created_at
    """
    return conn.execute("""
        SELECT id, title, price, file_name, created_at
        FROM movies
        ORDER BY created_at DESC
    """).fetchall()


# ======================================================
# =============== FILTER / SEARCH CORE =================
# ======================================================

def _unique_add(res, seen, mid, title, price):
    if mid not in seen:
        res.append((mid, title, price))
        seen.add(mid)


# ---------- SEARCH BY NAME ----------
def search_by_name(query):
    q = _norm(query)
    res, seen = [], set()

    for mid, title, price, fname, _ in _get_all_movies():
        if q in _norm(title) or q in _norm(fname):
            _unique_add(res, seen, mid, title, price)

    return res


# ---------- ALGAITA ----------
def get_algaita_movies():
    res, seen = [], set()

    for mid, title, price, fname, _ in _get_all_movies():
        name = _norm(title) + " " + _norm(fname)
        if "algaita" in name:
            _unique_add(res, seen, mid, title, price)

    return res


# ---------- HAUSA SERIES (AR) ----------
def get_hausa_series_movies():
    res, seen = [], set()

    for mid, title, price, fname, _ in _get_all_movies():
        name = _norm(title) + " " + _norm(fname)

        if (
            name.endswith(" ar") or
            name.endswith(" ar.mp4") or
            " ar." in name
        ):
            _unique_add(res, seen, mid, title, price)

    return res


# ---------- OTHERS / PUBLIC (AL) ----------
def get_public_movies():
    res, seen = [], set()

    for mid, title, price, fname, _ in _get_all_movies():
        name = _norm(title) + " " + _norm(fname)

        if (
            name.endswith(" al") or
            name.endswith(" al.mp4") or
            " al." in name
        ):
            _unique_add(res, seen, mid, title, price)

    return res


# ======================================================
# ================== SENDERS ===========================
# ======================================================

def _send_page(uid, movies, page, title, cb_type):
    start = page * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    chunk = movies[start:end]

    if not chunk:
        bot.send_message(uid, "❌ Babu ƙarin sakamako.")
        return

    kb = InlineKeyboardMarkup()

    for mid, name, price in chunk:
        kb.add(
            InlineKeyboardButton(
                f"🎬 {name}",
                callback_data=f"search_pick_{mid}"
            )
        )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton("⬅️ BACK", callback_data=f"C_{cb_type}_{page-1}")
        )
    if end < len(movies):
        nav.append(
            InlineKeyboardButton("MORE ➡️", callback_data=f"C_{cb_type}_{page+1}")
        )

    if nav:
        kb.row(*nav)

    kb.add(InlineKeyboardButton("❌ CANCEL", callback_data="search_cancel"))

    bot.send_message(uid, title, reply_markup=kb)


# ---------- DISPATCH SENDERS (RUKUNI D ke kira) ----------

def send_search_results(uid, page):
    q = admin_states.get(uid, {}).get("query")
    if not q:
        bot.send_message(uid, "❌ An rasa sakamakon nema.")
        return

    movies = search_by_name(q)
    _send_page(uid, movies, page, f"🔍 SAKAMAKON NEMA: {q}", "search")


def send_others_movies(uid, page):
    movies = get_public_movies()
    _send_page(uid, movies, page, "🎞 OTHERS / PUBLIC MOVIES", "others")


def send_hausa_series(uid, page):
    movies = get_hausa_series_movies()
    _send_page(uid, movies, page, "📺 HAUSA SERIES", "hausa")


def send_algaita_movies(uid, page):
    movies = get_algaita_movies()
    _send_page(uid, movies, page, "🎺 ALGAITA MOVIES", "algaita")


# ================== END RUKUNI C ==================

# ====================== RUKUNI D (FINAL) ======================

def safe_delete(chat_id, msg_id):
    try:
        bot.delete_message(chat_id, msg_id)
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("C_"))
def handle_rukuni_d_callbacks(c):
    uid = c.from_user.id
    data = c.data

    try:
        bot.answer_callback_query(c.id)
    except:
        pass

    # goge tsohon message
    safe_delete(c.message.chat.id, c.message.message_id)

    # FORMAT: C_<type>_<page>
    try:
        _, ctype, page = data.split("_", 2)
        page = int(page)
    except:
        return

    if ctype == "search":
        send_search_results(uid, page)

    elif ctype == "others":
        send_others_movies(uid, page)

    elif ctype == "hausa":
        send_hausa_series(uid, page)

    elif ctype == "algaita":
        send_algaita_movies(uid, page)


@bot.callback_query_handler(func=lambda c: c.data == "search_cancel")
def handle_search_cancel(c):
    try:
        bot.answer_callback_query(c.id)
    except:
        pass

    safe_delete(c.message.chat.id, c.message.message_id)

    bot.send_message(
        c.from_user.id,
        "❌ An fasa nema.\n\nKa zabi wani abu daga menu."
    )

# ====================== END RUKUNI D ======================



# --- Added deep-link start handler for viewall/weakupdate (runs before other start handlers) ---
@bot.message_handler(func=lambda m: (m.text or "").strip().split(" ")[0]=="/start" and len((m.text or "").strip().split(" "))>1 and (m.text or "").strip().split(" ")[1] in ("viewall","weakupdate"))
def _start_deeplink_handler(msg):
    """
    Catch /start viewall or /start weakupdate deep-links from channel posts.
    This handler tries to send the weekly list directly and then returns without invoking the normal start flow.
    Placed early to take precedence over other start handlers.
    """
    try:
        send_weekly_list(msg)
    except Exception as e:
        try:
            bot.send_message(msg.chat.id, "An samu matsala wajen nuna weekly list.")
        except:
            pass
    return

# ================== RUKUNI B: SEARCH MOVIE MESSAGE HANDLERS (OFFICIAL) ==================

# ===== SEARCH BY NAME: USER TEXT INPUT =====
@bot.message_handler(
    func=lambda m: admin_states.get(m.from_user.id, {}).get("state") == "search_wait_name"
)
def search_name_text(m):
    uid = m.from_user.id
    text = (m.text or "").strip()

    # kariya
    if not text:
        bot.send_message(uid, "❌ Rubuta sunan fim.")
        return

    # harafi 2 ko 3 kawai
    if len(text) < 2 or len(text) > 3:
        bot.send_message(
            uid,
            "❌ Rubuta *HARAFI 2 KO 3* kawai.\nMisali: *MAS*",
            parse_mode="Markdown"
        )
        return

    # ajiye abin da user ya nema (engine zai karanta daga nan)
    admin_states[uid]["query"] = text.lower()

    # sanar da user
    bot.send_message(
        uid,
        f"🔍 Kana nema: *{text.upper()}*\n⏳ Ina dubawa...",
        parse_mode="Markdown"
    )

    # 👉 KIRA SEARCH ENGINE (RUKUNI C) – PAGE NA FARKO
    send_search_results(uid, 0)


# ===== FALLBACK: IDAN USER YA RUBUTA ABU BA A SEARCH MODE BA =====
@bot.message_handler(
    func=lambda m: m.from_user.id in admin_states
    and admin_states.get(m.from_user.id, {}).get("state") in (
        "search_menu",
        "browse_menu",
        "series_menu",
        "search_trending",
    )
)
def ignore_unexpected_text(m):
    uid = m.from_user.id
    bot.send_message(
        uid,
        "ℹ️ Don Allah ka yi amfani da *buttons* da ke ƙasa.",
        parse_mode="Markdown"
    )

# ================== END RUKUNI B ==================

# --- Added callback handler for in-bot "View All Movies" buttons ---
@bot.callback_query_handler(func=lambda c: c.data in ("view_all_movies","viewall"))
def _callback_view_all(call):
    uid = call.from_user.id
    # Build a small message-like object expected by send_weekly_list
    class _Msg:
        def __init__(self, uid):
            self.chat = type('X', (), {'id': uid})
            self.text = ""
    try:
        send_weekly_list(_Msg(uid))
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, "An samu matsala wajen nuna jerin.")




# small globals
admin_states = {}
last_menu_msg = {}
last_category_msg = {}
films_sessions = {}
last_films_msg = {}



# create tables (idempotent)
conn.execute("""
CREATE TABLE IF NOT EXISTS movies(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 title TEXT,
 price INTEGER,
 file_id TEXT,
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 channel_msg_id INTEGER,
 channel_username TEXT
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS orders(
 id TEXT PRIMARY KEY,
 user_id INTEGER,
 movie_id INTEGER,
 amount INTEGER,
 paid INTEGER DEFAULT 0,
 pay_ref TEXT,
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS order_items(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id TEXT,
 movie_id INTEGER,
 price INTEGER
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS weekly(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 poster_file_id TEXT,
 items TEXT,
 channel_msg_id INTEGER,
 updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS cart(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER,
 movie_id INTEGER,
 added_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
# referrals / credits
conn.execute("""
CREATE TABLE IF NOT EXISTS referrals(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 referrer_id INTEGER,
 referred_id INTEGER,
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 reward_granted INTEGER DEFAULT 0
)
""")
conn.execute("""
CREATE TABLE IF NOT EXISTS referral_credits(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 referrer_id INTEGER,
 amount INTEGER,
 used INTEGER DEFAULT 0,
 granted_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
# user prefs (language)
conn.execute("""
CREATE TABLE IF NOT EXISTS user_prefs(
 user_id INTEGER PRIMARY KEY,
 lang TEXT DEFAULT 'ha'
)
""")
conn.commit()

# ========== HELPERS ==========
def check_join(uid):
    try:
        member = bot.get_chat_member(CHANNEL, uid)
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception:
        return False

# name anonymization
def mask_name(fullname):
    """Mask parts of the name as requested: Muhmad, Khid, Sa*i style."""
    if not fullname:
        return "User"
    s = re.sub(r"\s+", " ", fullname.strip())
    # split on non-alphanumeric to preserve parts
    parts = re.split(r'(\W+)', s)
    out = []
    for p in parts:
        if not p or re.match(r'\W+', p):
            out.append(p)
            continue
        # p is a word
        n = len(p)
        if n <= 2:
            out.append(p[0] + "*"*(n-1))
            continue
        # keep first 2 and last 1, hide middle with **
        if n <= 4:
            keep = p[0] + "*"*(n-2) + p[-1]
            out.append(keep)
        else:
            # first two, two stars, last one
            out.append(p[:2] + "**" + p[-1])
    return "".join(out)

# language helpers (persisted in DB)
def set_user_lang(user_id, lang_code):
    try:
        conn.execute("INSERT OR REPLACE INTO user_prefs(user_id,lang) VALUES(?,?)", (user_id, lang_code))
        conn.commit()
    except Exception as e:
        print("set_user_lang error:", e)

def get_user_lang(user_id):
    try:
        row = conn.execute("SELECT lang FROM user_prefs WHERE user_id=?", (user_id,)).fetchone()
        if row:
            return row[0]
    except Exception as e:
        print("get_user_lang error:", e)
    return "ha"

# translation map for interface (not movie titles). Hausa (ha) = keep original messages in code.
TRANSLATIONS = {
    "en": {
        "welcome_shop": "Welcome to the film store:",
        "ask_name": "Hello! What do you need?:",
        "joined_ok": "✔ Joined the channel!",
        "not_joined": "❌ You have not joined.",
        "invite_text": "Invite friends and earn rewards! Share your link:",
        "no_movies": "No movies to show right now.",
        "cart_empty": "Your cart is empty.",
        "checkout_msg": "Proceed to checkout",
        "choose_language_prompt": "Choose your language:",
        "language_set_success": "Language changed successfully.",
        "change_language_button": "🌐 Change your language",

        # ===== BUTTONS =====
        "btn_choose_films": "Choose films",
        "btn_weekly_films": "This week's films",
        "btn_cart": "🧾 Cart",
        "btn_help": "Help",
        "btn_films": "🎬 Films",
        "btn_my_orders": "📦 My Orders",
        "btn_search_movie": "🔎 Search Movie",
        "btn_invite": "📨 Invite friends",
        "btn_support": "🆘 Support Help",
        "btn_go_home": "⤴️ Go back Home",
        "btn_channel": "📺 Our Channel",
        "btn_add_cart": "➕ Add to Cart",
        "btn_buy_now": "💳 Buy Now"
    },

    "fr": {
        "welcome_shop": "Bienvenue dans la boutique de films:",
        "ask_name": "Bonjour! Que voulez-vous?:",
        "joined_ok": "✔ Vous avez rejoint!",
        "not_joined": "❌ Vous n'avez pas rejoint.",
        "invite_text": "Invitez des amis et gagnez des récompenses!",
        "no_movies": "Aucun film disponible pour l’instant.",
        "cart_empty": "Votre panier est vide.",
        "checkout_msg": "Passer au paiement",
        "choose_language_prompt": "Choisissez votre langue:",
        "language_set_success": "Langue changée avec succès.",
        "change_language_button": "🌐 Changer la langue",

        # BUTTONS
        "btn_choose_films": "Choisir des films",
        "btn_weekly_films": "Films de cette semaine",
        "btn_cart": "🧾 Panier",
        "btn_help": "Aide",
        "btn_films": "🎬 Films",
        "btn_my_orders": "📦 Mes commandes",
        "btn_search_movie": "🔎 Rechercher un film",
        "btn_invite": "📨 Inviter des amis",
        "btn_support": "🆘 Aide",
        "btn_go_home": "⤴️ Retour",
        "btn_channel": "📺 Notre chaîne",
        "btn_add_cart": "➕ Ajouter au panier",
        "btn_buy_now": "💳 Acheter"
    },

    "ig": {
        "welcome_shop": "Nnọọ n’ụlọ ahịa fim:",
        "ask_name": "Ndewo! Gịnị ka ịchọrọ?:",
        "joined_ok": "✔ Ejikọtara gị!",
        "not_joined": "❌ Ị jụbeghị.",
        "invite_text": "Kpọọ enyi ka ha nweta uru!",
        "no_movies": "Enweghị fim ugbu a.",
        "cart_empty": "Ụgbọ gị dị efu.",
        "checkout_msg": "Gaa ịkwụ ụgwọ",
        "choose_language_prompt": "Họrọ asụsụ:",
        "language_set_success": "Asụsụ agbanweela nke ọma.",
        "change_language_button": "🌐 Gbanwee asụsụ",

        # BUTTONS
        "btn_choose_films": "Họrọ fim",
        "btn_weekly_films": "Fim izu a",
        "btn_cart": "🧾 Cart",
        "btn_help": "Nkwado",
        "btn_films": "🎬 Fim",
        "btn_my_orders": "📦 Oru m",
        "btn_search_movie": "🔎 Chọọ fim",
        "btn_invite": "📨 Kpọọ enyi",
        "btn_support": "🆘 Nkwado",
        "btn_go_home": "⤴️ Laghachi",
        "btn_channel": "📺 Channel anyị",
        "btn_add_cart": "➕ Tinye na Cart",
        "btn_buy_now": "💳 Zụta Ugbu a"
    },

    "yo": {
        "welcome_shop": "Kaabo si ile itaja fiimu:",
        "ask_name": "Bawo! Kini o fẹ?:",
        "joined_ok": "✔ Darapọ mọ ikanni!",
        "not_joined": "❌ O kò tíì darapọ.",
        "invite_text": "Pe awọn ọrẹ ki o jèrè ere!",
        "no_movies": "Ko si fiimu lọwọlọwọ.",
        "cart_empty": "Apo rẹ ṣofo.",
        "checkout_msg": "Tẹsiwaju si isanwo",
        "choose_language_prompt": "Yan èdè:",
        "language_set_success": "Èdè ti yipada.",
        "change_language_button": "🌐 Yi èdè pada",

        # BUTTONS
        "btn_choose_films": "Yan fiimu",
        "btn_weekly_films": "Fiimu ọ̀sẹ̀ yìí",
        "btn_cart": "🧾 Cart",
        "btn_help": "Iranwọ",
        "btn_films": "🎬 Fiimu",
        "btn_my_orders": "📦 Awọn aṣẹ mi",
        "btn_search_movie": "🔎 Wa fiimu",
        "btn_invite": "📨 Pe ọ̀rẹ́",
        "btn_support": "🆘 Iranwọ",
        "btn_go_home": "⤴️ Pada",
        "btn_channel": "📺 Ikanni wa",
        "btn_add_cart": "➕ Fi kun Cart",
        "btn_buy_now": "💳 Ra báyìí"
    },

    "ff": {
        "welcome_shop": "A jaɓɓama e suuɗi fim:",
        "ask_name": "Ina! Hol ko yiɗɗa?:",
        "joined_ok": "✔ A seɗɗii e kanal!",
        "not_joined": "❌ A wonaa seɗaako.",
        "invite_text": "Naatu yamiroɓe ngam jeye jukkere!",
        "no_movies": "Fimmuuji alaa oo sahaa.",
        "cart_empty": "Cart maa ko dulli.",
        "checkout_msg": "Yah to nafawngal",
        "choose_language_prompt": "Labo laawol:",
        "language_set_success": "Laawol waylii no haanirta.",
        "change_language_button": "🌐 Waylu laawol",

        # BUTTONS
        "btn_choose_films": "Suɓo fim",
        "btn_weekly_films": "Fimmuuji ndee yontere",
        "btn_cart": "🧾 Cart",
        "btn_help": "Ballal",
        "btn_films": "🎬 Fimmuuji",
        "btn_my_orders": "📦 Noddu maa",
        "btn_search_movie": "🔎 Yiilu fim",
        "btn_invite": "📨 Naatu yamiroɓe",
        "btn_support": "🆘 Ballal",
        "btn_go_home": "⤴️ Rutto galle",
        "btn_channel": "📺 Kanal amen",
        "btn_add_cart": "➕ Ɓeydu Cart",
        "btn_buy_now": "💳 Soodu Jooni"
    }
}

def tr_user(user_id, key, default=None):
    """Translate key for user language, or return default (Hausa original)"""
    lang = get_user_lang(user_id)
    if lang == "ha":
        return default
    return TRANSLATIONS.get(lang, {}).get(key, default)

# referral helpers (same as before)
def add_referral(referrer_id, referred_id):
    try:
        if referrer_id == referred_id:
            return False
        exists = conn.execute("SELECT id FROM referrals WHERE referrer_id=? AND referred_id=?", (referrer_id, referred_id)).fetchone()
        if exists:
            return False
        conn.execute("INSERT INTO referrals(referrer_id,referred_id) VALUES(?,?)", (referrer_id, referred_id))
        conn.commit()
        return True
    except Exception as e:
        print("add_referral error:", e)
        return False

def get_referrer_for(referred_id):
    row = conn.execute("SELECT referrer_id, reward_granted, id FROM referrals WHERE referred_id=? ORDER BY id DESC LIMIT 1", (referred_id,)).fetchone()
    if not row:
        return None
    return {"referrer_id": row[0], "reward_granted": row[1], "referral_row_id": row[2]}

def grant_referral_reward(referral_row_id, referrer_id, amount=200):
    try:
        row = conn.execute("SELECT reward_granted FROM referrals WHERE id=?", (referral_row_id,)).fetchone()
        if not row:
            return False
        if row[0]:
            return False
        conn.execute("INSERT INTO referral_credits(referrer_id,amount,used) VALUES(?,?,0)", (referrer_id, amount))
        conn.execute("UPDATE referrals SET reward_granted=1 WHERE id=?", (referral_row_id,))
        conn.commit()
        try:
            bot.send_message(referrer_id, f"🎉 An ba ka lada N{amount} saboda wani da ka gayyata ya shiga kuma ya yi sayayya 3×. Wannan lada za a iya amfani da shi wajen sayen fim ɗin mu (ba za a iya cire shi ba).")
        except:
            pass
        return True
    except Exception as e:
        print("grant_referral_reward error:", e)
        return False

def get_referrals_by_referrer(referrer_id):
    rows = conn.execute("SELECT referred_id,created_at,reward_granted,id FROM referrals WHERE referrer_id=? ORDER BY id DESC", (referrer_id,)).fetchall()
    return rows

def get_credits_for_user(user_id):
    rows = conn.execute("SELECT id,amount,used,granted_at FROM referral_credits WHERE referrer_id=?", (user_id,)).fetchall()
    total_available = sum(r[1] for r in rows if r[2] == 0)
    return total_available, rows

def check_referral_rewards_for_referred(referred_id):
    try:
        ref = get_referrer_for(referred_id)
        if not ref:
            return False
        referrer_id = ref["referrer_id"]
        reward_granted = ref["reward_granted"]
        referral_row_id = ref["referral_row_id"]
        if reward_granted:
            return False
        rows = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND movie_id!=? AND paid=1", (referred_id, -1)).fetchone()
        count = rows[0] if rows else 0
        if count >= 3 and check_join(referred_id):
            return grant_referral_reward(referral_row_id, referrer_id, amount=200)
        return False
    except Exception as e:
        print("check_referral_rewards_for_referred error:", e)
        return False

def apply_credits_to_amount(user_id, amount):
    try:
        cur = conn.execute("SELECT id,amount FROM referral_credits WHERE referrer_id=? AND used=0 ORDER BY granted_at", (user_id,)).fetchall()
        if not cur:
            return amount, 0, []
        remaining = int(amount)
        applied = 0
        applied_ids = []
        for cid, camount in cur:
            if remaining <= 0:
                break
            try:
                conn.execute("UPDATE referral_credits SET used=1 WHERE id=?", (cid,))
                conn.commit()
                applied += camount
                applied_ids.append(cid)
                remaining -= camount
            except Exception as e:
                print("apply credit update error:", e)
                continue
        if remaining < 0:
            remaining = 0
        return remaining, applied, applied_ids
    except Exception as e:
        print("apply_credits_to_amount error:", e)
        return amount, 0, []

# parse caption
def parse_caption_for_title_price(caption):
    if not caption:
        return None, None
    text = caption.strip()
    if "|" in text:
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) >= 3 and parts[0].lower() == "post":
            title = parts[1]
            price = parts[2]
        elif len(parts) == 2:
            title = parts[0]
            price = parts[1]
        else:
            return None, None
        price_digits = re.findall(r"\d+", price.replace(",", ""))
        if not price_digits:
            return None, None
        return title, int("".join(price_digits))
    m = re.search(r"^(.*?)\s+[₦Nn]?\s*([0-9,]+)\s*$", text)
    if m:
        title = m.group(1).strip()
        price = int(re.sub(r"[^\d]", "", m.group(2)))
        return title, price
    return None, None

# pruning
def prune_old_movies():
    try:
        cutoff = datetime.utcnow() - timedelta(days=21)
        rows = conn.execute("SELECT id, created_at FROM movies ORDER BY created_at ASC").fetchall()
        to_delete = []
        for r in rows:
            if len(to_delete) >= 6:
                break
            cid = r[0]
            created_at = r[1]
            try:
                if not created_at:
                    continue
                dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            except:
                continue
            if dt < cutoff:
                to_delete.append(cid)
        for mid in to_delete:
            try:
                conn.execute("DELETE FROM movies WHERE id=?", (mid,))
            except:
                pass
        if to_delete:
            conn.commit()
            print("Pruned movies:", to_delete)
    except Exception as e:
        print("prune_old_movies error:", e)

prune_old_movies()
# ========== MENUS (FULL TRANSLATION ENABLED) ==========

def footer_kb(user_id=None):
    kb = InlineKeyboardMarkup()

    # Go Home Button (Translated)
    home_label = tr_user(user_id, "btn_go_home", default="⤴️ KOMA FARKO")

    kb.row(
        InlineKeyboardButton(home_label, callback_data="go_home"),
        InlineKeyboardButton(tr_user(user_id, "btn_channel", default="🫂 Our Channel"), url=f"https://t.me/{CHANNEL.lstrip('@')}")
    )

    # Change language button
    change_label = tr_user(user_id, "change_language_button", default="🌐 Change your language")
    kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))

    return kb




def reply_menu(uid=None):    
    kb = InlineKeyboardMarkup()    
    
    films_label   = tr_user(uid, "btn_films", default="🎬 Films")    
       
    invite_label  = tr_user(uid, "btn_invite", default="📨 Invite Friends")    
        
    cart_label    = tr_user(uid, "btn_cart", default="🧾 Cart")    
    support_label = tr_user(uid, "btn_support", default="🆘 Support Help")    
    channel_label = tr_user(uid, "btn_channel", default="📺 Our Channel")    
    home_label    = tr_user(uid, "btn_go_home", default="⤴️ KOMA FARKO")    
    change_label  = tr_user(uid, "change_language_button", default="🌐 Change your language")    
    
    kb.add(    
        InlineKeyboardButton(films_label, callback_data="films"),    
            )
    
    kb.add(InlineKeyboardButton(invite_label, callback_data="invite"))    
    kb.add(InlineKeyboardButton("🛒 MY=ORDERS", callback_data="myorders_new"))    
     
    

    if uid in ADMINS:
        kb.add(InlineKeyboardButton("➕ Add Movie", callback_data="addmovie"))
        kb.add(InlineKeyboardButton("🧹 ERASER", callback_data="eraser_menu"))
        kb.add(InlineKeyboardButton("⬆ Weak update", callback_data="weak_update"))
        kb.add(InlineKeyboardButton("✏️ Edit title", callback_data="edit_title"))

    kb.add(InlineKeyboardButton(cart_label, callback_data="viewcart"))
    kb.add(InlineKeyboardButton(support_label, callback_data="support_help"))

    # Add a full-width Our Channel row (as in original layout screenshot)
    kb.add(InlineKeyboardButton(channel_label, url=f"https://t.me/{CHANNEL.lstrip('@')}"))

    # Then add a row with Home (KOMA FARKO) and Our Channel side-by-side
    kb.row(
        InlineKeyboardButton(home_label, callback_data="go_home"),
        InlineKeyboardButton(channel_label, url=f"https://t.me/{CHANNEL.lstrip('@')}")
    )

    kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))

    return kb



def user_main_menu(uid=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)

    weekly_films = tr_user(uid, "btn_weekly_films", default="Films din wannan satin")
    cart_label   = tr_user(uid, "btn_cart", default="🧾 Cart")
    help_label   = tr_user(uid, "btn_help", default="Taimako")

    kb.add(KeyboardButton(weekly_films))
    kb.add(KeyboardButton(cart_label), KeyboardButton(help_label))

    return kb


#Start
def movie_buttons_inline(mid, user_id=None):
    kb = InlineKeyboardMarkup()

    add_cart = tr_user(user_id, "btn_add_cart", default="➕ Add to Cart")
    buy_now  = tr_user(user_id, "btn_buy_now", default="💳 Buy Now")
    home_btn = tr_user(user_id, "btn_go_home", default="⤴️ KOMA FARKO")
    channel  = tr_user(user_id, "btn_channel", default="🫂 Our Channel")
    change_l = tr_user(user_id, "change_language_button", default="🌐 Change your language")

    kb.add(
        InlineKeyboardButton(add_cart, callback_data=f"addcart:{mid}"),
        InlineKeyboardButton(buy_now, callback_data=f"buy:{mid}")
    )

    # 🛑 Idan user_id == None → channel ne → kada a ƙara sauran buttons
    if user_id is None:
        return kb

    # 🔰 Idan private chat ne → saka sauran buttons
    kb.row(
        InlineKeyboardButton(home_btn, callback_data="go_home"),
        InlineKeyboardButton(channel, url=f"https://t.me/{CHANNEL.lstrip('@')}")
    )

    kb.row(InlineKeyboardButton(change_l, callback_data="change_language"))

    return kb
#END
# ====== User Film Session Helper ======
films_sessions = {}

def ensure_user_session(uid):
    if uid not in films_sessions:
        films_sessions[uid] = {
            "pages": []
        }
    return films_sessions[uid]
def mixed_order_movie_ids():
    try:
        # Karɓar duk movies cikin sabo → tsoho
        rows = conn.execute(
            "SELECT id FROM movies ORDER BY created_at DESC"
        ).fetchall()

        # Idan babu komai
        if not rows:
            return []

        # Jerin ID ɗin fina-finai
        ids = [r[0] for r in rows]

        # Random shuffle – domin a gauraya order
        import random
        random.shuffle(ids)

        return ids

    except Exception as e:
        print("Error in mixed_order_movie_ids:", e)
        return []

def build_pages_from_ids(ids, per_page=10):
    """
    Rarraba IDs zuwa pages (kowane page 10 movies).
    """
    pages = []
    for i in range(0, len(ids), per_page):
        pages.append(ids[i:i + per_page])
    return pages
# ======== Delete user last films message ========
last_films_msg = {}

def delete_user_last_films_message(uid):
    info = last_films_msg.get(uid)
    if not info:
        return
    chat_id, msg_id = info
    try:
        bot.delete_message(chat_id, msg_id)
    except:
        pass
    last_films_msg[uid] = None
# ========== Pagination / Films page ==========

def send_films_page(uid, page_idx):
    sess = films_sessions.get(uid)
    if not sess:
        sess = ensure_user_session(uid)

    pages = sess.get("pages", [])
    if not pages or page_idx < 0 or page_idx >= len(pages):
        bot.send_message(uid, tr_user(uid, "no_movies", default="Babu fina-finai da za'a nuna a yanzu."))
        return

    page = pages[page_idx]

    text_lines = []
    for mid in page:
        row = conn.execute("SELECT title,price FROM movies WHERE id=?", (mid,)).fetchone()
        if row:
            title, price = row
            text_lines.append(f"🎬 <b>{title}</b>\n💵 ₦{price}\n")

    final_text = "\n".join(text_lines) if text_lines else tr_user(uid, "no_movies_page", default="Babu fina-finai a wannan shafi.")

    kb = InlineKeyboardMarkup()

    for mid in page:
        row = conn.execute("SELECT title,price FROM movies WHERE id=?", (mid,)).fetchone()
        if not row:
            continue
        title, _ = row

        add_cart = tr_user(uid, "btn_add_cart", default="➕ Add to Cart")
        buy_now  = tr_user(uid, "btn_buy_now", default="💳 Buy Now")

        kb.add(
            InlineKeyboardButton(f"{add_cart} — {title[:20]}", callback_data=f"addcart:{mid}"),
            InlineKeyboardButton(f"{buy_now} — {title[:12]}", callback_data=f"buy:{mid}")
        )

    back_label = tr_user(uid, "btn_back", default="◀ Back")
    next_label = tr_user(uid, "btn_next", default="Next ▶")
    home_label = tr_user(uid, "btn_go_home", default="⤴️ KOMA FARKO")
    channel    = tr_user(uid, "btn_channel", default="🫂 Our Channel")
    lang_btn   = tr_user(uid, "change_language_button", default="🌐 Change your language")

    kb.row(
        InlineKeyboardButton(back_label, callback_data="films_prev"),
        InlineKeyboardButton(next_label, callback_data="films_next")
    )

    # ====== SEARCH MOVIE (AN KARA SHI KAWAI) ======
    search_label = tr_user(uid, "btn_search_movie", default="🔍 Search Movie")
    kb.row(
        InlineKeyboardButton(search_label, callback_data="search_movie")
    )
    # ============================================

    kb.row(
        InlineKeyboardButton(home_label, callback_data="go_home"),
        InlineKeyboardButton(channel, url=f"https://t.me/{CHANNEL.lstrip('@')}")
    )

    kb.row(InlineKeyboardButton(lang_btn, callback_data="change_language"))

    delete_user_last_films_message(uid)
    sent = bot.send_message(uid, final_text, reply_markup=kb, parse_mode="HTML")

    last_films_msg[uid] = (sent.chat.id, sent.message_id)
    sess["index"] = page_idx
    films_sessions[uid] = sess
# ========== START ==========
@bot.message_handler(commands=["start"])
def start(message):
    uid = message.from_user.id
    fname = message.from_user.first_name or ""
    uname = f"@{message.from_user.username}" if message.from_user.username else "Babu username"
    text = (message.text or "").strip()
    param = None
    if text.startswith("/start "):
        param = text.split(" ",1)[1].strip()
    elif text.startswith("/start"):
        parts = text.split(" ",1)
        if len(parts) > 1:
            param = parts[1].strip()
    if param and param.startswith("ref"):
        try:
            ref_id = int(param[3:])
            try:
                add_referral(ref_id, uid)
                try:
                    bot.send_message(ref_id, f"Someone used your invite link! ID: <code>{uid}</code>", parse_mode="HTML")
                except:
                    pass
            except:
                pass
        except:
            pass
    # notify admin
    try:
        bot.send_message(
            ADMIN_ID,
            f"🟢 SABON VISITOR!\n\n"
            f"👤 Sunan: <b>{fname}</b>\n"
            f"🔗 Username: {uname}\n"
            f"🆔 ID: <code>{uid}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        print("Failed to notify admin about visitor:", e)
    if not check_join(uid):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
        kb.add(InlineKeyboardButton("I've Joined✅", callback_data="checkjoin"))
        bot.send_message(uid, "⚠️ Don cigaba, sai ka shiga channel ɗin mu.", reply_markup=kb)
        return
    # send menus
    bot.send_message(uid, "Abokin kasuwanci barka da zuwa shagon fina finai:", reply_markup=user_main_menu(uid))
    bot.send_message(uid, "Sannu da zuwa!\n Me kake bukata?:", reply_markup=reply_menu(uid))

# ========== get group id & misc handlers ==========
@bot.message_handler(commands=["getgroupid"])
def getgroupid(message):
    chat = message.chat
    if chat.type in ("group", "supergroup", "channel"):
        bot.reply_to(message, f"Chat title: {chat.title}\nChat id: <code>{chat.id}</code>", parse_mode="HTML")
    else:
        bot.reply_to(message,
                     "Don samun group id: ƙara bot ɗin zuwa group ɗin, sannan a rubita /getgroupid a cikin group. Ko kuma ka forward wani message daga group zuwa nan (DM) kuma zan nuna original chat id idan forwarded.")

# ========== HANDLE REPLY KEYBOARD (Zabi films REMOVED) ==========
@bot.message_handler(func=lambda msg: isinstance(getattr(msg, "text", None), str) and msg.text in ["Films din wannan satin", "Taimako", "🧾 Cart"])
def user_buttons(message):
    txt = message.text
    uid = message.from_user.id

    # ======= FILMS DIN WANNAN SATIN =======
    if txt == "Films din wannan satin":
        weekly = conn.execute("SELECT poster_file_id,items,channel_msg_id FROM weekly ORDER BY id DESC LIMIT 1").fetchone()
        if weekly:
            poster = weekly[0]
            items_json = weekly[1] or "[]"
            try:
                items = json.loads(items_json)
            except:
                items = []
            caption = "Wadannan sune fina-finai na wannan satin.\nWanne kake bukata?"

            kb = InlineKeyboardMarkup()
            for it in items:
                title = it.get("title", "Film")
                url = it.get("url", "#")
                kb.add(InlineKeyboardButton(f"🎬 {title}", url=url))

            try:
                if poster:
                    bot.send_photo(message.chat.id, poster, caption=caption, reply_markup=kb)
                else:
                    bot.send_message(message.chat.id, caption, reply_markup=kb)
            except Exception as e:
                print("Failed to send weekly poster:", e)
                bot.send_message(message.chat.id, caption, reply_markup=kb)
            return

        bot.send_message(message.chat.id, "📅 Ga finafinan wannan satin babu su tukuna.")
        return
# ---------- END myorders_new ----------
# ======= TAIMAKO =======                
    if txt == "Taimako":                
        kb = InlineKeyboardMarkup()                

        # ALWAYS open admin DM directly – no callback, no message sending
        if ADMIN_USERNAME:                
            kb.add(InlineKeyboardButton("Contact Admin", url=f"https://t.me/{ADMIN_USERNAME}"))                
        else:                
            kb.add(InlineKeyboardButton("🆘 Support Help", url="https://t.me/{}".format(ADMIN_USERNAME)))                

        bot.send_message(                
            message.chat.id,                
            "Idan kana bukatar taimako, Yi magana da admin.",                
            reply_markup=kb                
        )                
        return            

    # ======= CART =======            
    if txt == "🧾 Cart":            
        show_cart(message.chat.id, message.from_user.id)            
        return

# ================== FINAL ISOLATED ERASER SYSTEM ==================

import os, json, random, time, re
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

ERASER_BACKUP_FOLDER = "eraser_backups"
ERASER_PASSWORD_DEFAULT = "E66337"
ERASER_OTP_TTL = 120
ERASER_MAX_RESEND = 3
ERASER_RESEND_COOLDOWN = 30
ERASER_BACKUP_TTL_DAYS = 30

os.makedirs(ERASER_BACKUP_FOLDER, exist_ok=True)

# ================= DATABASE =================
try:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS eraser_settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS eraser_backups(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
except:
    pass

# ================= HELPERS =================
def eraser_reset_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔑 Reset Password", callback_data="eraser_forgot"))
    kb.add(InlineKeyboardButton("✖ Cancel", callback_data="eraser_cancel"))
    return kb

# ================= PASSWORD =================
def _eraser_get_password():
    r = conn.execute(
        "SELECT value FROM eraser_settings WHERE key='eraser_password'"
    ).fetchone()
    if r and r[0]:
        return r[0]

    conn.execute(
        "INSERT OR REPLACE INTO eraser_settings VALUES(?,?)",
        ("eraser_password", ERASER_PASSWORD_DEFAULT)
    )
    conn.commit()
    return ERASER_PASSWORD_DEFAULT


def _eraser_set_password(p):
    conn.execute(
        "INSERT OR REPLACE INTO eraser_settings VALUES(?,?)",
        ("eraser_password", p)
    )
    conn.commit()


def _eraser_password_valid(p):
    return bool(re.fullmatch(r"\d{5}[A-Z]", p))

# ================= OTP =================
_eraser_otp = {}
_eraser_meta = {}

def _eraser_gen_otp():
    return str(random.randint(100000, 999999))


def _eraser_send_otp(uid, resend=False):
    now = time.time()
    meta = _eraser_meta.get(uid, {})

    if resend:
        if meta.get("resends", 0) >= ERASER_MAX_RESEND:
            return False, "OTP resend limit reached."
        if now - meta.get("last", 0) < ERASER_RESEND_COOLDOWN:
            return False, "Wait before resending OTP."

    otp = _eraser_gen_otp()
    _eraser_otp[uid] = {"otp": otp, "expires": now + ERASER_OTP_TTL}
    _eraser_meta[uid] = {"resends": meta.get("resends", 0), "last": now}

    bot.send_message(OTP_ADMIN_ID, f"🔐 ERASER OTP for admin {uid}: {otp}")
    return True, None


def _eraser_otp_expired(uid):
    return uid not in _eraser_otp or time.time() > _eraser_otp[uid]["expires"]

# ================= BACKUP =================
def _eraser_create_backup():
    now = datetime.utcnow()
    ts = now.strftime("%Y%m%d%H%M%S")
    fname = f"eraser_backup_{ts}.json"
    path = os.path.join(ERASER_BACKUP_FOLDER, fname)

    cur = conn.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]

    data = {}
    for t in tables:
        if t in ("sqlite_sequence", "eraser_settings", "eraser_backups"):
            continue
        rows = cur.execute(f"SELECT * FROM {t}").fetchall()
        cols = [d[0] for d in cur.description] if rows else []
        data[t] = [dict(zip(cols, r)) for r in rows]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    conn.execute(
        "INSERT INTO eraser_backups(filename,created_at) VALUES(?,?)",
        (fname, now.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    return path

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("eraser_"))
def eraser_cb(c):
    uid = c.from_user.id
    data = c.data
    bot.answer_callback_query(c.id)

    if uid != ADMIN_ID:
        return

    if data == "eraser_menu":
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✔ Yes – Erase", callback_data="eraser_yes"))
        kb.add(InlineKeyboardButton("📦 Backup", callback_data="eraser_backup"))
        kb.add(InlineKeyboardButton("♻ Restore", callback_data="eraser_restore"))
        kb.add(InlineKeyboardButton("🔑 Forgot Password", callback_data="eraser_forgot"))
        kb.add(InlineKeyboardButton("✖ Cancel", callback_data="eraser_cancel"))
        bot.send_message(uid, "🧹 ERASER SYSTEM", reply_markup=kb)

    elif data == "eraser_cancel":
        admin_states.pop(uid, None)
        bot.send_message(uid, "Cancelled.", reply_markup=reply_menu(uid))

    elif data == "eraser_backup":
        admin_states[uid] = {"state": "eraser_backup_pass"}
        bot.send_message(uid, "Enter ERASER password:")

    elif data == "eraser_yes":
        admin_states[uid] = {"state": "eraser_erase_pass"}
        bot.send_message(uid, "Enter ERASER password:")

    elif data == "eraser_restore":
        admin_states[uid] = {"state": "eraser_restore_pass"}
        bot.send_message(uid, "Enter ERASER password:")

    elif data == "eraser_forgot":
        _eraser_send_otp(uid)
        admin_states[uid] = {"state": "eraser_wait_otp"}
        bot.send_message(uid, "OTP sent. Enter OTP:")

# ================= TEXT =================
@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID
    and admin_states.get(m.from_user.id, {}).get("state", "").startswith("eraser_")
)
def eraser_text(m):
    uid = m.from_user.id
    text = m.text.strip()
    st = admin_states[uid]["state"]

    # ---- BACKUP PASS ----
    if st == "eraser_backup_pass":
        if text != _eraser_get_password():
            bot.send_message(uid, "❌ Wrong password.", reply_markup=eraser_reset_kb())
            return
        path = _eraser_create_backup()
        admin_states.pop(uid)
        bot.send_message(uid, f"✔ Backup created:\n{path}")

    # ---- ERASE PASS ----
    elif st == "eraser_erase_pass":
        if text != _eraser_get_password():
            bot.send_message(uid, "❌ Wrong password.", reply_markup=eraser_reset_kb())
            return
        _eraser_create_backup()
        cur = conn.cursor()
        for (t,) in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if t not in ("sqlite_sequence", "eraser_settings", "eraser_backups"):
                cur.execute(f"DELETE FROM {t}")
        conn.commit()
        admin_states.pop(uid)
        bot.send_message(uid, "🧹 ERASE COMPLETE.")

    # ---- RESTORE PASS ----
    elif st == "eraser_restore_pass":
        if text != _eraser_get_password():
            bot.send_message(uid, "❌ Wrong password.", reply_markup=eraser_reset_kb())
            return

        rows = conn.execute(
            "SELECT id, filename, created_at FROM eraser_backups ORDER BY id DESC"
        ).fetchall()

        if not rows:
            bot.send_message(uid, "❌ No backups available.")
            admin_states.pop(uid)
            return

        kb = InlineKeyboardMarkup()
        for i, f, c in rows[:5]:
            kb.add(InlineKeyboardButton(c, callback_data=f"eraser_restore_{i}"))

        admin_states[uid] = {"state": "eraser_restore_select"}
        bot.send_message(uid, "Select backup:", reply_markup=kb)

    # ---- OTP ----
    elif st == "eraser_wait_otp":
        if _eraser_otp_expired(uid):
            bot.send_message(uid, "OTP expired.")
            return
        if text != _eraser_otp[uid]["otp"]:
            bot.send_message(uid, "❌ OTP ba daidai ba. Tambayi admin mai karɓa.")
            return
        admin_states[uid] = {"state": "eraser_new_pass"}
        bot.send_message(uid, "Enter new password:")

    elif st == "eraser_new_pass":
        if not _eraser_password_valid(text):
            bot.send_message(uid, "Invalid format. Example: 66788K")
            return
        admin_states[uid] = {"state": "eraser_confirm_pass", "tmp": text}
        bot.send_message(uid, "Confirm password:")

    elif st == "eraser_confirm_pass":
        if text != admin_states[uid]["tmp"]:
            bot.send_message(uid, "Passwords do not match.")
            return
        _eraser_set_password(text)
        admin_states.pop(uid)
        bot.send_message(uid, "✅ Password changed successfully.")

# ================= END ERASER SYSTEM =================
                
            
# ========== admin_inputs for weak_update and edit title ==========            
@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.from_user.id in admin_states)            
def admin_inputs(message):            
    state_entry = admin_states.get(message.from_user.id)            
    if not state_entry:            
        return            

    state_entry = admin_states.get(message.from_user.id)            
    if not state_entry:            
        return            

    state = state_entry.get("state")            

    # === NEW: Add Movie admin flow (store file to STORAGE_CHANNEL) ===            
    if state == "add_movie_wait_file":            
        try:            
            file_id = None            
            file_name = None
            if hasattr(message, 'content_type'):            
                if message.content_type == 'photo':            
                    file_id = message.photo[-1].file_id            
                elif message.content_type == 'video':            
                    file_id = message.video.file_id            
                elif message.content_type == 'document':            
                    file_id = message.document.file_id            
                    file_name = message.document.file_name

            if not file_id:            
                bot.reply_to(message, "Ba a gane file ba. Tura fim (photo/video/document).")            
            else:            
                storage_file_id = file_id            
                storage_msg_id = None            

                try:            
                    if STORAGE_CHANNEL:            
                        if message.content_type == 'photo':            
                            sent = bot.send_photo(STORAGE_CHANNEL, file_id)            
                            storage_file_id = sent.photo[-1].file_id if getattr(sent, 'photo', None) else storage_file_id            
                            storage_msg_id = getattr(sent, 'message_id', None)            

                        elif message.content_type == 'video':            
                            sent = bot.send_video(STORAGE_CHANNEL, file_id)            
                            storage_file_id = getattr(sent, 'video', None) and sent.video.file_id or storage_file_id            
                            storage_msg_id = getattr(sent, 'message_id', None)            

                        else:            
                            sent = bot.send_document(STORAGE_CHANNEL, file_id)            
                            storage_file_id = getattr(sent, 'document', None) and sent.document.file_id or storage_file_id            
                            storage_msg_id = getattr(sent, 'message_id', None)            

                except Exception as e:            
                    print("Failed to send to STORAGE_CHANNEL:", e)            

                try:            
                    cur = conn.execute(
    "INSERT INTO movies(title,price,file_id,file_name,created_at,channel_msg_id,channel_username) "
    "VALUES(?,?,?,?,?,?,?)",
    (
        None,
        0,
        storage_file_id,
        file_name,
        datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        storage_msg_id,
        None
    )
)            
                    conn.commit()            
                    movie_id = cur.lastrowid            

                except Exception as e:            
                    print("DB insert (add movie storage) error:", e)            
                    bot.reply_to(message, "An samu matsala wajen adana fim. Duba log.")            
                    admin_states.pop(ADMIN_ID, None)            
                    return            

                bot.send_message(
                    ADMIN_ID,
                    "Da kyau — na adana fim ɗin a storage. Bani poster/sunan fim ɗin na aika sashen tallah (misali: Garaje - 200).",
                    reply_markup=footer_kb(ADMIN_ID)
                )            

                admin_states[ADMIN_ID] = {
                    "state": "add_movie_wait_poster",
                    "movie_id": movie_id,
                    "file_id": storage_file_id,
                    "storage_msg_id": storage_msg_id
                }            

        except Exception as e:            
            print("add_movie_wait_file error:", e)
            bot.reply_to(message, "An samu kuskure yayin adana fim.")
        return

    if state == "add_movie_wait_poster":
        try:
            st = state_entry
            movie_id = st.get('movie_id')
            file_id_saved = st.get('file_id')
            if not movie_id:
                bot.reply_to(message, "Bai dace ba — babu fim ɗin da aka fara. Fara daga Add Movie.")
                admin_states.pop(ADMIN_ID, None)
                return
            caption_text = (message.caption or message.text or "").strip()
            title, price = parse_caption_for_title_price(caption_text)
            poster_file_id = None
            if hasattr(message, 'content_type'):
                if message.content_type == 'photo':
                    poster_file_id = message.photo[-1].file_id
                elif message.content_type == 'video':
                    poster_file_id = message.video.file_id
                elif message.content_type == 'document':
                    poster_file_id = message.document.file_id
            if not title or not price:
                bot.reply_to(message, "Format bai dace ba. Aika poster (photo/video/document) tare da caption: Title - 200")
                return
            try:
                conn.execute("UPDATE movies SET title=?, price=? WHERE id=?", (title, int(price), movie_id))
                conn.commit()
            except Exception as e:
                print("Failed updating movie title/price:", e)
            try:
                post_caption = f"🎬 <b>{title}</b>\n💵 ₦{price}\nTap buttons to buy or add to cart."
                markup = movie_buttons_inline(movie_id, user_id=None)
                sent_msg = None
                if poster_file_id:
                    if message.content_type == 'photo':
                        sent_msg = bot.send_photo(CHANNEL, poster_file_id, caption=post_caption, parse_mode='HTML', reply_markup=markup)
                    elif message.content_type == 'video':
                        sent_msg = bot.send_video(CHANNEL, poster_file_id, caption=post_caption, parse_mode='HTML', reply_markup=markup)
                    else:
                        sent_msg = bot.send_document(CHANNEL, poster_file_id, caption=post_caption, parse_mode='HTML', reply_markup=markup)
                else:
                    sent_msg = bot.send_message(CHANNEL, post_caption, parse_mode='HTML', reply_markup=markup)
                channel_msg_id = sent_msg.message_id if sent_msg else None
                channel_username = CHANNEL.lstrip('@')
                try:
                    conn.execute("UPDATE movies SET channel_msg_id=?, channel_username=? WHERE id=?", (channel_msg_id, channel_username, movie_id))
                    conn.commit()
                except Exception as e:
                    print("Failed storing channel msg id after posting poster:", e)
            except Exception as e:
                print("Failed to post poster to CHANNEL:", e)
                bot.reply_to(message, "An adana fim amma an kasa turawa tallah. Duba logs.")
                admin_states.pop(ADMIN_ID, None)
                return
            bot.send_message(ADMIN_ID, f"An adana fim (ID: {movie_id}) kuma an tura poster a tallah.")
            admin_states.pop(ADMIN_ID, None)
        except Exception as e:
            print("add_movie_wait_poster error:", e)
            bot.reply_to(message, "An samu kuskure wajen aiwatar da poster/process.")
            admin_states.pop(ADMIN_ID, None)
        return
    
    # edit title flows (kept same logic)
    if state == "edit_title_wait_for_query":
        q = (message.text or "").strip()
        if not q:
            bot.reply_to(message, "Ba ka turo sunan ko ID ba. Rubuta sunan fim ko ID domin in bincika.")
            return
        movie = None
        try:
            mid = int(q)
            movie = conn.execute("SELECT id,title,channel_msg_id,channel_username FROM movies WHERE id=?", (mid,)).fetchone()
        except:
            rows = conn.execute("SELECT id,title,channel_msg_id,channel_username FROM movies").fetchall()
            exact = [r for r in rows if r[1] and r[1].strip().lower() == q.lower()]
            if exact:
                movie = exact[0]
            else:
                contains = [r for r in rows if r[1] and q.lower() in r[1].strip().lower()]
                if len(contains) == 0:
                    movie = None
                elif len(contains) == 1:
                    movie = contains[0]
                else:
                    text = "An samu fina-finai masu kama. Aiko ID ɗin fim ɗin daga cikin waɗannan:\n"
                    for r in contains:
                        text += f"• {r[1]} — ID: {r[0]}\n"
                    bot.reply_to(message, text)
                    admin_states[ADMIN_ID] = {"state": "edit_title_wait_for_id", "inst_msgs": state_entry.get("inst_msgs", [])}
                    return
        if not movie:
            bot.reply_to(message, "Ban samu wannan fim din a jerin ba. Sake gwadawa ko aiko ID ɗin.")
            admin_states[ADMIN_ID] = {"state": "edit_title_wait_for_query", "inst_msgs": state_entry.get("inst_msgs", [])}
            return
        mid = movie[0]
        current_title = movie[1]
        sent = bot.reply_to(message, f"Na samu fim ɗin: <b>{current_title}</b> (ID: {mid}).\nAiko sabon title da kake so a maye gurbin wannan.", parse_mode="HTML")
        admin_states[ADMIN_ID] = {"state": "edit_title_wait_new", "movie_id": mid, "inst_msgs": state_entry.get("inst_msgs", []) + [sent.message_id]}
        return

    if state == "edit_title_wait_for_id":
        q = (message.text or "").strip()
        if not q:
            bot.reply_to(message, "Aiko ID na fim ko sunan fim.")
            return
        try:
            mid = int(q)
        except:
            bot.reply_to(message, "Ba valid ID ba. Aiko lambar ID na fim daga jerin da na nuna.")
            return
        row = conn.execute("SELECT id,title,channel_msg_id,channel_username FROM movies WHERE id=?", (mid,)).fetchone()
        if not row:
            bot.reply_to(message, "Ban samu fim da wannan ID ba. Duba ID ɗin ka kuma aiko.")
            return
        current_title = row[1]
        sent = bot.reply_to(message, f"Na samu fim ɗin: <b>{current_title}</b> (ID: {mid}).\nAiko sabon title da kake so a maye gurbin wannan.", parse_mode="HTML")
        admin_states[ADMIN_ID] = {"state": "edit_title_wait_new", "movie_id": mid, "inst_msgs": state_entry.get("inst_msgs", []) + [sent.message_id]}
        return

    if state == "edit_title_wait_new":
        new_title = (message.text or "").strip()
        if not new_title:
            bot.reply_to(message, "Ba ka turo sabon suna ba. Rubuta sabon title yanzu.")
            return
        mid = state_entry.get("movie_id")
        if not mid:
            bot.reply_to(message, "Bai dace ba — babu fim ɗin da aka zaɓa. Fara sabo daga Edit title.")
            admin_states.pop(ADMIN_ID, None)
            return
        bot.reply_to(message, f"Naga me ka rubuta:\n<b>{new_title}</b>\nIna sabunta sunan a database...", parse_mode="HTML")
        try:
            conn.execute("UPDATE movies SET title=? WHERE id=?", (new_title, mid))
            conn.commit()
            row = conn.execute("SELECT channel_msg_id,channel_username,price,file_id FROM movies WHERE id=?", (mid,)).fetchone()
            if row:
                channel_msg_id, channel_username, price, file_id = row[0], row[1], row[2], row[3]
                try:
                    if channel_username and channel_msg_id:
                        new_caption = f"🎬 <b>{new_title}</b>\n"
                        if price:
                            new_caption += f"💵 ₦{price}\n"
                        else:
                            new_caption += "\n"
                        new_caption += "Tap buttons to buy or add to cart."
                        bot.edit_message_caption(chat_id=f"@{channel_username}" if not str(channel_username).startswith("@") else channel_username,
                                                 message_id=int(channel_msg_id),
                                                 caption=new_caption,
                                                 parse_mode="HTML",
                                                 reply_markup=movie_buttons_inline(mid, user_id=None))
                except Exception as e:
                    print("Failed to edit channel message caption for movie:", mid, e)
            sent = bot.send_message(ADMIN_ID, f"Anyi nasara 🥰\nNa sabunta sunan fim (ID: {mid}) zuwa:\n<b>{new_title}</b>", parse_mode="HTML")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🗑️ Delete conversation messages", callback_data=f"edit_delete:{mid}"))
            del_msg = bot.send_message(ADMIN_ID, "Idan kana son share hirar da muka yi (sakonan da bot ya aiko), danna Delete:", reply_markup=kb)
            insts = state_entry.get("inst_msgs", [])
            insts.append(sent.message_id)
            insts.append(del_msg.message_id)
            admin_states[ADMIN_ID] = {"state": "edit_title_done", "movie_id": mid, "inst_msgs": insts}
        except Exception as e:
            print("Error updating movie title:", e)
            bot.reply_to(message, "An samu matsala yayin sabunta title. Duba log.")
            admin_states.pop(ADMIN_ID, None)
        return

    return

# ========== CANCEL ==========
@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if message.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) and admin_states[ADMIN_ID].get("state") in ("weak_update", "update_week"):
        inst = admin_states[ADMIN_ID]
        inst_msg_id = inst.get("inst_msg_id")
        if inst_msg_id:
            try:
                bot.delete_message(chat_id=ADMIN_ID, message_id=inst_msg_id)
            except Exception as e:
                print("Failed to delete instruction message on cancel:", e)
        admin_states.pop(ADMIN_ID, None)
        bot.reply_to(message, "An soke Update/Weak update kuma an goge sakon instruction.")
        return
    if message.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID):
        admin_states.pop(ADMIN_ID, None)
        bot.reply_to(message, "An soke aikin admin na yanzu.")
        return

# ========== /getid ==========
@bot.message_handler(commands=["getid"])
def getid_command(message):
    text = message.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Amfani: /getid Sunan fim\nMisali: /getid Wutar jeji")
        return
    query = parts[1].strip().lower()
    rows = conn.execute("SELECT id,title FROM movies").fetchall()
    exact = [(r[0], r[1]) for r in rows if r[1] and r[1].strip().lower() == query]
    if exact:
        mid, title = exact[0]
        bot.reply_to(message, f"Kamar yadda ka bukata ga ID ɗin fim ɗin <b>{title}</b>: <code>{mid}</code>", parse_mode="HTML")
        return
    contains = [(r[0], r[1]) for r in rows if r[1] and query in r[1].strip().lower()]
    if not contains:
        bot.reply_to(message,
                     "Ba daidai bane umarninka. Idan kana so na turo maka ID na wani fim, rubuta haka:\n"
                     "/getid Sunan fim")
        return
    if len(contains) == 1:
        mid, title = contains[0]
        bot.reply_to(message, f"Kamar yadda ka bukata ga ID ɗin fim ɗin <b>{title}</b>: <code>{mid}</code>", parse_mode="HTML")
    else:
        text_out = "An samu fina-finai masu kama:\n"
        for mid, title in contains:
            text_out += f"• {title} — ID: {mid}\n"
        bot.reply_to(message, text_out)

# ========== detect forwarded channel post ==========
@bot.message_handler(func=lambda m: getattr(m, "forward_from_chat", None) is not None or getattr(m, "forward_from_message_id", None) is not None)
def handle_forwarded_post(m):
    fc = getattr(m, "forward_from_chat", None)
    fid = getattr(m, "forward_from_message_id", None)
    if not fc and not fid:
        return
    try:
        chat_info = ""
        if fc:
            if getattr(fc, "username", None):
                chat_info = f"@{fc.username}"
            else:
                chat_info = f"chat_id:{fc.id}"
        else:
            chat_info = "Unknown channel"
        if fid:
            bot.reply_to(m, f"Original channel: {chat_info}\nOriginal message id: {fid}")
        else:
            bot.reply_to(m, f"Original channel: {chat_info}\nMessage id not found.")
    except Exception as e:
        print("forward handler error:", e)

# ========== show_cart ==========
# Note: get_cart/add_to_cart/remove_from_cart/clear_cart implementations provided later if missing
def show_cart(chat_id, user_id):
    rows = get_cart(user_id)
    if not rows:
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("⤴️ KOMA FARKO", callback_data="go_home"),
               InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
        # add change language button
        change_label = tr_user(user_id, "change_language_button", default="🌐 Change your language")
        kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
        s = tr_user(user_id, "cart_empty", default="🧾 Cart ɗinka babu komai.")
        bot.send_message(chat_id, s, reply_markup=kb)
        return
    text_lines = ["🧾 Kayayyakin da ka zaba:"]
    kb = InlineKeyboardMarkup()
    for movie_id, title, price, file_id in rows:
        text_lines.append(f"• {title} — ₦{price}")
        kb.add(InlineKeyboardButton(f"❌ Remove: {title[:18]}", callback_data=f"removecart:{movie_id}"))
    total = sum(r[2] for r in rows)
    text_lines.append(f"\nJimillar: ₦{total}")
    total_available, credit_rows = get_credits_for_user(user_id)
    credit_info = ""
    if total_available > 0:
        credit_info = f"\n\nNote: Available referral credit: N{total_available}. It will be automatically applied at checkout."
    kb.add(InlineKeyboardButton("🧹 Clear Cart", callback_data="clearcart"),
           InlineKeyboardButton("💳 Checkout", callback_data="checkout"))
    kb.row(InlineKeyboardButton("KOMA ⤴️ASALIN FARKO", callback_data="go_home"),
           InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
    # change language
    change_label = tr_user(user_id, "change_language_button", default="🌐 Change your language")
    kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
    bot.send_message(chat_id, "\n".join(text_lines) + credit_info, reply_markup=kb)


# ====================== WEAK UPDATE (BULK WEEKLY) ======================
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import re
from datetime import datetime
import json

weak_update_temp = {}

def parse_title_price_block(text_block):
    out = []
    for line in (text_block or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?P<title>.+?)\s*[–\-:]\s*(?P<price>\d+)", line)
        if m:
            out.append({
                "title": m.group("title").strip(),
                "price": int(m.group("price"))
            })
    return out

def find_best_match(title, candidates):
    t = (title or "").lower()
    for i, c in enumerate(candidates):
        fn = (c.get("file_name") or "").lower()
        toks = re.split(r"[ \-_.]+", fn)
        if t in toks:
            return i
    for i, c in enumerate(candidates):
        fn = (c.get("file_name") or "").lower()
        if fn.startswith(t):
            return i
    for i, c in enumerate(candidates):
        fn = (c.get("file_name") or "").lower()
        if t in fn:
            return i
    return None

# ---------- Start weak update ----------
@bot.callback_query_handler(func=lambda c: c.data == "weak_update")
def start_weak_update(call):
    uid = call.from_user.id
    weak_update_temp[uid] = {
        "stage": "collect_files",
        "movies": [],
        "poster": None,
        "caption": None
    }
    bot.answer_callback_query(call.id)
    bot.send_message(uid,
        "Turomin fina-finai na wannan makon, boss 🌚\n"
        "Tura videos/documents ɗinka yanzu. Idan ka gama, danna YES."
    )

# ---------- collect files ----------
@bot.message_handler(
    func=lambda m: m.from_user.id in weak_update_temp and weak_update_temp[m.from_user.id]["stage"] == "collect_files",
    content_types=['video','document','animation','audio','photo']
)
def collect_files(msg):
    uid = msg.from_user.id
    temp = weak_update_temp[uid]

    orig_chat = msg.chat.id
    orig_msg_id = msg.message_id

    if msg.content_type == "video":
        file_name = getattr(msg.video, "file_name", None) or f"video_{orig_msg_id}"
    elif msg.content_type == "document":
        file_name = getattr(msg.document, "file_name", None) or f"doc_{orig_msg_id}"
    elif msg.content_type == "audio":
        file_name = getattr(msg.audio, "file_name", None) or f"audio_{orig_msg_id}"
    elif msg.content_type == "animation":
        file_name = getattr(msg.animation, "file_name", None) or f"anim_{orig_msg_id}"
    elif msg.content_type == "photo":
        file_name = f"photo_{orig_msg_id}"
    else:
        file_name = f"file_{orig_msg_id}"

    temp["movies"].append({
    "orig_chat_id": orig_chat,
    "msg_id": orig_msg_id,
    "file_name": file_name,
    "title": None,
    "price": None
})

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("YES, Na gama", callback_data="weak_files_done"))
    kb.add(InlineKeyboardButton("NO, Zan ci gaba", callback_data="weak_more_files"))

    bot.send_message(uid, f"An karɓi: {file_name}\nKa gama?", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "weak_more_files")
def weak_more_files(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.from_user.id, "Ci gaba da turo fina-finai...")

@bot.callback_query_handler(func=lambda c: c.data == "weak_files_done")
def weak_files_done(call):
    uid = call.from_user.id
    weak_update_temp[uid]["stage"] = "poster"
    bot.answer_callback_query(call.id)
    bot.send_message(uid,
        "Na karɓi duk fina-finai. Yanzu turo POSTER (hoton).\n"
        "Bayan poster, turo rubutun sunaye + farashi."
    )

# ---------- collect poster ----------
@bot.message_handler(
    func=lambda m: m.from_user.id in weak_update_temp and weak_update_temp[m.from_user.id]["stage"] == "poster",
    content_types=['photo']
)
def collect_poster(msg):
    uid = msg.from_user.id
    temp = weak_update_temp[uid]
    temp["poster"] = msg.photo[-1].file_id

    caption = msg.caption or ""
    if caption.strip():
        temp["caption"] = caption.strip()
        process_weak_finalize(uid)
    else:
        temp["stage"] = "caption"
        bot.send_message(uid,
            "Poster an karɓa. Yanzu turo rubutun sunaye + farashi (misali: Gagarumi - 200)."
        )

# ---------- collect caption text ----------
@bot.message_handler(
    func=lambda m: m.from_user.id in weak_update_temp and weak_update_temp[m.from_user.id]["stage"] == "caption",
    content_types=['text']
)
def collect_caption_text(msg):
    uid = msg.from_user.id
    temp = weak_update_temp[uid]
    temp["caption"] = msg.text.strip()
    process_weak_finalize(uid)

# ---------- FINALIZE ----------
def process_weak_finalize(uid):
    temp = weak_update_temp.get(uid)
    if not temp:
        return

    caption_text = temp.get("caption") or ""
    parsed = parse_title_price_block(caption_text)

    bot.send_message(uid, "Ana tura fina-finai zuwa STORAGE...")

    stored_files = []
    for mv in temp.get("movies", []):
        try:
            sent = bot.copy_message(STORAGE_CHANNEL, mv["orig_chat_id"], mv["msg_id"])
            fid = None
            try:
                if getattr(sent, "document", None):
                    fid = sent.document.file_id
                elif getattr(sent, "video", None):
                    fid = sent.video.file_id
                elif getattr(sent, "audio", None):
                    fid = sent.audio.file_id
                elif getattr(sent, "animation", None):
                    fid = sent.animation.file_id
                elif getattr(sent, "photo", None):
                    fid = sent.photo[-1].file_id
                else:
                    fid = sent.message_id
            except:
                fid = sent.message_id
            stored_files.append({
    "file_id": fid,
    "file_name": mv["file_name"],
    "orig_index": len(stored_files)
})
        except Exception as e:
            print("weak_update copy error:", e)
            continue

    # insert into DB
    cur = conn.cursor()
    items_for_weekly = []

    for item in parsed:
        idx = find_best_match(item["title"], stored_files)
        file_id = stored_files[idx]["file_id"] if idx is not None else None

        try:
            cur.execute(
    "INSERT INTO movies(title, price, file_id, file_name, created_at, channel_msg_id, channel_username) VALUES(?,?,?,?,?,?,?)",
    (
        item["title"],
        item["price"],
        file_id,
        stored_files[idx]["file_name"] if idx is not None else None,
        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        None,
        CHANNEL.lstrip("@")
    )
)
            conn.commit()
            movie_id = cur.lastrowid

            items_for_weekly.append({
                "id": movie_id,
                "title": item["title"],
                "price": item["price"],
                "file_id": file_id
            })

        except Exception as e:
            print("weak_update DB error:", e)
            continue

    # ----- CHANNEL BUTTON (RESTORED) -----
    channel_kb = InlineKeyboardMarkup()
    channel_kb.add(
        InlineKeyboardButton(
            "📽 VIEW ALL MOVIES",
            url=f"https://t.me/{BOT_USERNAME}?start=viewall"
        )
    )

    # ----- SEND POSTER TO CHANNEL -----
    try:
        sent_post = bot.send_photo(
            CHANNEL,
            temp.get("poster"),
            caption=caption_text,
            reply_markup=channel_kb
        )
        new_post_id = getattr(sent_post, "message_id", None)
    except Exception as e:
        print("weak_update send poster error:", e)
        new_post_id = None

    # save weekly
    try:
        items_json = json.dumps(items_for_weekly)
        cur.execute(
            "INSERT INTO weekly(poster_file_id, items, channel_msg_id) VALUES (?,?,?)",
            (temp.get("poster"), items_json, new_post_id)
        )
        conn.commit()
    except Exception as e:
        print("Failed saving weekly entry:", e)

    bot.send_message(uid, "An gama Weak Update! 🎉")
    weak_update_temp.pop(uid, None)

# ---------- Show weekly films in DM ----------
def send_weekly_list(msg):
    cur = conn.cursor()
    row = cur.execute(
        "SELECT items, channel_msg_id FROM weekly ORDER BY rowid DESC LIMIT 1"
    ).fetchone()

    if not row:
        return bot.send_message(msg.chat.id, "Ba a samu jerin fina finan wannan makon ba tukuna.")

    try:
        items = json.loads(row[0] or "[]")
    except:
        items = []

    if not items:
        return bot.send_message(msg.chat.id, "Ba a samu jerin fina finan wannan makon ba tukuna.")

    # HEADER with date
    today = datetime.now().strftime("%d/%m/%Y")
    text = f"📅 Weak Update ({today})\n\n"

    kb = InlineKeyboardMarkup()
    all_ids = []

    for m in items:
        title = m.get("title")
        price = m.get("price")
        mid = m.get("id")

        # SUNAN FILM
        text += f"{title} – ₦{price}\n"

        # BUTTON DIN FILM A KARKASHIN SA
        kb.row(
            InlineKeyboardButton("➕ Add Cart", callback_data=f"addcart:{mid}"),
            InlineKeyboardButton("💳 Buy Now", callback_data=f"buy:{mid}")
        )

        # LITTLE SPACE
        text += "\n"

        all_ids.append(str(mid))

    # BUY ALL
    if all_ids:
        kb.add(
            InlineKeyboardButton("🎁 BUY ALL", callback_data="buyall:" + ",".join(all_ids))
        )

    bot.send_message(msg.chat.id, text, reply_markup=kb, parse_mode="HTML")


# ---------- weekly button ----------
@bot.callback_query_handler(func=lambda c: c.data == "weekly_films")
def send_weekly_films(call):
    return send_weekly_list(call.message)


# ---------- START handler ----------
@bot.message_handler(commands=['start'])
def start_handler(msg):
    args = msg.text.split()
    if len(args) > 1 and args[1] == "weakupdate":
        return send_weekly_list(msg)
    if len(args) > 1 and args[1] == "viewall":
        return send_weekly_list(msg)
    bot.send_message(msg.chat.id, "Welcome!")

# ===== END WEAK UPDATE =====
        # INVITE
# ---------- My Orders (UNPAID with per-item REMOVE) ----------
@bot.callback_query_handler(func=lambda c: c.data == "myorders_new")
def myorders_new(c):
    uid = c.from_user.id

    rows = conn.execute("""
        SELECT o.id, o.movie_id, o.amount
        FROM orders o
        WHERE o.user_id=? AND o.paid=0
        ORDER BY o.rowid DESC
    """, (uid,)).fetchall()

    if not rows:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⤴️ Back", callback_data="go_home"))
        bot.send_message(uid, "Babu **unpaid order**.", reply_markup=kb)
        return

    text = f"🧾 <b>Your unpaid orders ({len(rows)})</b>\n\n"
    kb = InlineKeyboardMarkup()
    total = 0

    for oid, mid, amount in rows:
        mv = conn.execute(
            "SELECT title FROM movies WHERE id=?",
            (mid,)
        ).fetchone()

        title = mv[0] if mv else "Unknown"
        short = title[:12] + "…" if len(title) > 12 else title

        text += f"• {short} - ₦{int(amount)}\n"
        total += int(amount)

        # ---- PER ITEM REMOVE BUTTON (WITH MOVIE NAME) ----
        kb.row(
            InlineKeyboardButton(
                f"❌ {short} Cire ❌",
                callback_data=f"remove_unpaid:{oid}"
            )
        )

    text += f"\n<b>Total: ₦{total}</b>"

    # ---- GLOBAL ACTIONS (UNCHANGED) ----
    kb.row(
        InlineKeyboardButton("💳 Pay all", callback_data="pay_all_now"),
        InlineKeyboardButton("📦 Paid orders", callback_data="paid_orders")
    )
    kb.row(
        InlineKeyboardButton("🗑 Delete unpaid", callback_data="delete_unpaid"),
        InlineKeyboardButton("⤴️ Back", callback_data="go_home")
    )

    bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

# ---------- PAY ALL UNPAID ----------
@bot.callback_query_handler(func=lambda c: c.data == "pay_all_now")
def pay_all_now(c):
    uid = c.from_user.id

    items = conn.execute("""
        SELECT movie_id, price FROM (

            -- OLD SINGLE ORDERS
            SELECT
                o.movie_id AS movie_id,
                COALESCE(m.price,0) AS price
            FROM orders o
            LEFT JOIN movies m ON o.movie_id = m.id
            WHERE o.user_id = ? AND o.paid = 0 AND o.movie_id != -1

            UNION ALL

            -- GROUP / V2 ORDERS
            SELECT
                oi.movie_id AS movie_id,
                COALESCE(oi.price,0) AS price
            FROM order_items oi
            JOIN orders o2 ON oi.order_id = o2.id
            WHERE o2.user_id = ? AND o2.paid = 0
        )
        GROUP BY movie_id
    """, (uid, uid)).fetchall()

    if not items:
        try:
            bot.answer_callback_query(c.id, text="You have no unpaid movies.")
        except:
            pass
        bot.send_message(uid, "You have no unpaid movies.")
        return

    total = 0
    unique = {}
    for mid, p in items:
        try:
            price_int = int(p or 0)
        except:
            price_int = 0
        total += price_int
        unique[mid] = price_int

    new_order_id = str(uuid.uuid4())

    conn.execute(
        "INSERT INTO orders (id, user_id, movie_id, amount, paid) VALUES (?, ?, ?, ?, 0)",
        (new_order_id, uid, -1, total)
    )

    for mid, p in unique.items():
        conn.execute(
            "INSERT INTO order_items (order_id, movie_id, price) VALUES (?, ?, ?)",
            (new_order_id, mid, p)
        )

    conn.commit()

    try:
        pay_url = create_flutterwave_payment(uid, new_order_id, total, "")
    except:
        pay_url = None

    kb = InlineKeyboardMarkup()
    if pay_url:
        kb.add(InlineKeyboardButton("💳 PAY NOW", url=pay_url))
    else:
        kb.add(InlineKeyboardButton("💳 PAY NOW", callback_data=f"confirm:{new_order_id}"))

    kb.row(
        InlineKeyboardButton("⤴️ Koma Baya", callback_data="go_home"),
        InlineKeyboardButton("🫂 Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}")
    )

    try:
        bot.answer_callback_query(c.id)
    except:
        pass

    bot.send_message(
        uid,
        f"🔗 Click below to pay ₦{total} for all your movies:",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("remove_unpaid:"))
def remove_unpaid_one(c):
    uid = c.from_user.id
    oid = c.data.split(":")[1]

    conn.execute(
        "DELETE FROM orders WHERE id=? AND user_id=? AND paid=0",
        (oid, uid)
    )
    conn.commit()

    bot.answer_callback_query(c.id, "An cire order ɗin.")
    return myorders_new(c)
@bot.callback_query_handler(func=lambda c: c.data == "delete_unpaid")
def delete_all_unpaid(c):
    uid = c.from_user.id

    conn.execute(
        "DELETE FROM orders WHERE user_id=? AND paid=0",
        (uid,)
    )
    conn.commit()

    bot.answer_callback_query(c.id, "An goge duk unpaid orders.")
    bot.send_message(uid, "🗑 Duk unpaid orders an goge su.")
@bot.callback_query_handler(func=lambda c: c.data == "paid_orders")
def paid_orders(c):
    uid = c.from_user.id

    rows = conn.execute("""
        SELECT DISTINCT movie_id
        FROM orders
        WHERE user_id=? AND paid=1
    """, (uid,)).fetchall()

    if not rows:
        bot.send_message(uid, "Babu fim da ka saya tukuna.")
        return

    kb = InlineKeyboardMarkup()
    text = "📦 <b>Paid Movies</b>\n\n"

    for (mid,) in rows:
        mv = conn.execute(
            "SELECT title FROM movies WHERE id=?",
            (mid,)
        ).fetchone()
        title = mv[0] if mv else f"Movie {mid}"

        kb.add(
            InlineKeyboardButton(
                f"🎬 {title}",
                callback_data=f"assist_get:{mid}"
            )
        )

    bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

# ================== RUKUNI A (FINAL) ==================

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


# ===== ENTRY POINT =====
@bot.callback_query_handler(func=lambda c: c.data == "search_movie")
def search_movie_entry(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔎 NEMA DA SUNA", callback_data="search_by_name"))
    kb.add(InlineKeyboardButton("🎺 ALGAITA", callback_data="C_algaita_0"))
    kb.add(InlineKeyboardButton("📺 HAUSA SERIES", callback_data="C_hausa_0"))
    kb.add(InlineKeyboardButton("🎞 OTHERS", callback_data="C_others_0"))
    kb.add(InlineKeyboardButton("❌ CANCEL", callback_data="search_cancel"))

    bot.send_message(
        uid,
        "🔍 *SASHEN NEMAN FIM*\nZaɓi yadda kake so:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


# ===== SEARCH BY NAME =====
@bot.callback_query_handler(func=lambda c: c.data == "search_by_name")
def cb_search_by_name(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    admin_states[uid] = {"state": "search_wait_name"}

    bot.send_message(
        uid,
        "✍️ RUBUTA *HARAFI 2 KO 3* NA SUNAN FIM\nMisali: *MAS*",
        parse_mode="Markdown"
    )


# ===== CANCEL =====
@bot.callback_query_handler(func=lambda c: c.data == "search_cancel")
def cb_search_cancel(c):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    admin_states.pop(uid, None)
    bot.send_message(uid, "❌ An rufe sashen nema.", reply_markup=reply_menu(uid))


# ================== END RUKUNI A ==================



@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    uid = c.from_user.id
    data = c.data or ""
    
    
    # Map new erase_all_data callback to existing erase_data handler (compat shim)
    if data == "erase_all_data":
        data = "erase_data"


    # NEW WEAK UPDATE SYSTEM
    if data == "weak_update":
        start_weak_update(msg=c.message)
        return
    # checkjoin: after user clicks I've Joined, prompt language selection
    if data == "checkjoin":
        try:
            if check_join(uid):
                bot.answer_callback_query(callback_query_id=c.id, text=tr_user(uid, "joined_ok", default="✔ An shiga channel!"))
                # prompt language selection now
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("English", callback_data="setlang_en"),
                       InlineKeyboardButton("Françe", callback_data="setlang_fr"))
                kb.add(InlineKeyboardButton("Hausa", callback_data="setlang_ha"),
                       InlineKeyboardButton("Igbo", callback_data="setlang_ig"))
                kb.add(InlineKeyboardButton("Yaruba", callback_data="setlang_yo"),
                       InlineKeyboardButton("Fulani/Fulfulde", callback_data="setlang_ff"))
                bot.send_message(uid, tr_user(uid, "choose_language_prompt", default="Choose language / Zaɓi harshe:"), reply_markup=kb)
            else:
                bot.answer_callback_query(callback_query_id=c.id, text=tr_user(uid, "not_joined", default="❌ Baka shiga ba."))
        except Exception as e:
            print("checkjoin callback error:", e)
        return

    # show change language menu (global button)
    if data == "change_language":
        try:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("English", callback_data="setlang_en"),
                   InlineKeyboardButton("Françe", callback_data="setlang_fr"))
            kb.add(InlineKeyboardButton("Hausa", callback_data="setlang_ha"),
                   InlineKeyboardButton("Igbo", callback_data="setlang_ig"))
            kb.add(InlineKeyboardButton("Yaruba", callback_data="setlang_yo"),
                   InlineKeyboardButton("Fulani/Fulfulde", callback_data="setlang_ff"))
            bot.answer_callback_query(callback_query_id=c.id)
            bot.send_message(uid, tr_user(uid, "choose_language_prompt", default="Choose language / Zaɓi harshe:"), reply_markup=kb)
        except Exception as e:
            print("change_language callback error:", e)
        return

    # set language callbacks
    if data.startswith("setlang_"):
        lang = data.split("_",1)[1]
        set_user_lang(uid, lang)
        # If Hausa selected, keep original Hausa text
        if lang == "ha":
            bot.answer_callback_query(callback_query_id=c.id, text="An saita Hausa. (Ba a canza rubutu Hausa ba.)")
            bot.send_message(uid, "Abokin kasuwanci barka da zuwa shagon fina finai:", reply_markup=user_main_menu(uid))
            bot.send_message(uid, "Sannu da zuwa!\n Me kake bukata?:", reply_markup=reply_menu(uid))
            return
        # for other languages, use translations where available
        welcome = tr_user(uid, "welcome_shop", default="Abokin kasuwanci barka da zuwa shagon fina finai:")
        ask = tr_user(uid, "ask_name", default="Sannu da zuwa!\n Me kake bukata?:")
        bot.answer_callback_query(callback_query_id=c.id, text=tr_user(uid, "language_set_success", default="Language set."))
        bot.send_message(uid, welcome, reply_markup=user_main_menu(uid))
        bot.send_message(uid, ask, reply_markup=reply_menu(uid))
        return

    # go home
    if data == "go_home":
        try:
            bot.answer_callback_query(callback_query_id=c.id)
            bot.send_message(uid, "Sannu! Ga zabuka:", reply_markup=reply_menu(uid))
        except:
            pass
        return

    if data == "invite":
        try:
            bot_info = bot.get_me()
            bot_username = bot_info.username if bot_info and getattr(bot_info, "username", None) else None
        except:
            bot_username = None
        if bot_username:
            ref_link = f"https://t.me/{bot_username}?start=ref{uid}"
            share_url = "https://t.me/share/url?"+urllib.parse.urlencode({
                "url": ref_link,
                "text": f"Gayyato ni zuwa wannan bot: {ref_link}\nJoin channel: https://t.me/{CHANNEL.lstrip('@')}\nKa samu lada idan wanda ka gayyata yayi join sannan ya siya fim 3×."
            })
        else:
            ref_link = f"/start ref{uid}"
            share_url = f"https://t.me/{CHANNEL.lstrip('@')}"
        text = (
            "Gayyato abokanka👨‍👨‍👦‍👦 suyi join domin samun GARABASA!🎁\n\n"
            "Ka tura musu wannan link ɗin.\n\n"
            "Idan wanda ka gayyata ya shiga channel ɗinmu kuma ya sayi fim uku, za'a baka N200🎊🎉\n"
            "10 friends N2000😲🥳🤑\n(yi amfani Kyautar wajen sayen fim).\n\n"
            "Danne alamar COPY karka daga zaka samu damar kofe link din ka, ko!\n"
            "ka taba 📤SHARE kai tsaye"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔗 Copy / Open Link", url=ref_link))
        kb.add(InlineKeyboardButton("📤 Share", url=share_url))
        kb.row(InlineKeyboardButton("👥 My referrals", callback_data="my_referrals"),
               InlineKeyboardButton("💰 My credits", callback_data="my_credits"))
        kb.row(InlineKeyboardButton(" ⤴️ KOMA FARKO", callback_data="go_home"),
               InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
        change_label = tr_user(uid, "change_language_button", default="🌐 Change your language")
        kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
        bot.answer_callback_query(callback_query_id=c.id)
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "my_referrals":
        rows = get_referrals_by_referrer(uid)
        if not rows:
            bot.answer_callback_query(callback_query_id=c.id, text="Babu wanda ka gayyata tukuna.")
            bot.send_message(uid, "Babu wanda ka gayyata tukuna.", reply_markup=reply_menu(uid))
            return
        text = "Mutanen da ka gayyata:\n\n"
        for referred_id, created_at, reward_granted, rowid in rows:
            name = None
            try:
                chat = bot.get_chat(referred_id)
                fname = getattr(chat, "first_name", "") or ""
                uname = getattr(chat, "username", None)
                if uname:
                    name = "@" + uname
                elif fname:
                    name = fname
            except:
                s = str(referred_id)
                name = s[:3] + "*"*(len(s)-6) + s[-3:] if len(s) > 6 else "User"+s[-4:]
            status = "+reward success" if reward_granted else "pending👀"
            text += f"• {name} — {status}\n"
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton(" ⤴️ KOMA FARKO", callback_data="go_home"),
               InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
        change_label = tr_user(uid, "change_language_button", default="🌐 Change your language")
        kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
        bot.answer_callback_query(callback_query_id=c.id)
        bot.send_message(uid, text, reply_markup=kb)
        return

    if data == "my_credits":
        total, rows = get_credits_for_user(uid)
        text = f"Total available credit: N{total}\n\n"
        for cid, amount, used, granted_at in rows:
            text += f"• ID:{cid} — N{amount} — {'USED' if used else 'AVAILABLE'} — {granted_at}\n"
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton(" ⤴️ KOMA FARKO", callback_data="go_home"),
               InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
        change_label = tr_user(uid, "change_language_button", default="🌐 Change your language")
        kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
        bot.answer_callback_query(callback_query_id=c.id)
        bot.send_message(uid, text, reply_markup=kb)
        return

   
     # CATEGORY
    if data in ("cat_india","cat_arewa","cat_american","cat_china"):
        prev = last_category_msg.get(uid)
        if prev:
            try:
                bot.delete_message(chat_id=prev[0], message_id=prev[1])
            except Exception as e:
                print("delete previous category msg error:", e)
        if data == "cat_india":
            sent = bot.send_message(uid, category_description_text("FILMS DIN INDIA"), reply_markup=footer_kb(uid))
            bot.answer_callback_query(callback_query_id=c.id)
            last_category_msg[uid] = (sent.chat.id, sent.message_id)
            return
        if data == "cat_arewa":
            sent = bot.send_message(uid, category_description_text("AREWA24 SERIES"), reply_markup=footer_kb(uid))
            bot.answer_callback_query(callback_query_id=c.id)
            last_category_msg[uid] = (sent.chat.id, sent.message_id)
            return
        if data == "cat_american":
            sent = bot.send_message(uid, category_description_text("AMERICAN FILM"), reply_markup=footer_kb(uid))
            bot.answer_callback_query(callback_query_id=c.id)
            last_category_msg[uid] = (sent.chat.id, sent.message_id)
            return
        if data == "cat_china":
            sent = bot.send_message(uid, category_description_text("CHAINA MASU ZAFI🔥"), reply_markup=footer_kb(uid))
            bot.answer_callback_query(callback_query_id=c.id)
            last_category_msg[uid] = (sent.chat.id, sent.message_id)
            return

    # ==== SHOW FILMS ====
    if data == "films":
        ids = mixed_order_movie_ids()
        if not ids:
            bot.answer_callback_query(
                callback_query_id=c.id,
                text=tr_user(uid, "no_movies", default="Babu fina-finai tukuna.")
            )
            return

        pages = build_pages_from_ids(ids, per_page=5)
        films_sessions[uid] = {
            "pages": pages,
            "index": 0
        }

        delete_user_last_films_message(uid)
        send_films_page(uid, 0)

        bot.answer_callback_query(callback_query_id=c.id)
        return

    # ==== FILMS PAGINATION ====
    if data == "films_next" or data == "films_prev":
        sess = films_sessions.get(uid)
        if not sess:
            bot.answer_callback_query(
                callback_query_id=c.id,
                text="Babu session na fina-finai."
            )
            return

        idx = sess.get("index", 0)
        pages = sess.get("pages", [])

        if data == "films_next":
            new_idx = idx + 1
            if new_idx >= len(pages):
                bot.answer_callback_query(
                    callback_query_id=c.id,
                    text="Babu karin shafuka."
                )
                return
        else:
            new_idx = idx - 1
            if new_idx < 0:
                bot.answer_callback_query(
                    callback_query_id=c.id,
                    text="Bakada baya."
                )
                return

        sess["index"] = new_idx
        films_sessions[uid] = sess

        delete_user_last_films_message(uid)
        send_films_page(uid, new_idx)

        bot.answer_callback_query(callback_query_id=c.id)
        return
# BUY single movie
# BUY single movie
    if data.startswith("buy:"):
        try:
            mid = int(data.split(":",1)[1])
        except:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid movie id.")
            return

        movie = conn.execute("SELECT title,price FROM movies WHERE id=?", (mid,)).fetchone()
        if not movie:
            bot.answer_callback_query(callback_query_id=c.id, text="Movie not found.")
            return

        title, price = movie
        remaining_price, applied_sum, applied_ids = apply_credits_to_amount(uid, int(price))
        order_id = str(uuid.uuid4())

        conn.execute("INSERT INTO orders(id,user_id,movie_id,amount,paid) VALUES(?,?,?,?,0)",
                     (order_id, uid, mid, remaining_price))
        conn.commit()

        # Create a compact DM-style confirmation message (no long admin ping)
        dm_text = f"🧾 Order Created Successfully\n\n🎬 {title}\n💰 Price: ₦{remaining_price}"
        # markup with Confirm / Cancel buttons
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("✅ Confirm payment", callback_data=f"confirm:{order_id}"))
        kb.add(InlineKeyboardButton("❌ Cancel order", callback_data=f"cancel:{order_id}"))

        # If user clicked inside a channel/group, move them to DM quickly
        try:
            chat_type = c.message.chat.type
        except Exception:
            chat_type = "private"

        if chat_type != "private":
            try:
                bot.answer_callback_query(callback_query_id=c.id, text="Moving to DM...")
            except:
                pass
            try:
                bot.send_message(uid, "🎉🎁 Order Created Successfully.\n\nKammala biyanka abokin kasuwanci, aturama fim cikin 2sec..:", reply_markup=kb)
            except Exception as e:
                print("Failed to send DM buy prompt:", e)
                try:
                    bot.answer_callback_query(callback_query_id=c.id, text="Could not open DM. Make sure you started the bot.")
                except:
                    pass
            return

        # If already in DM: send the compact order message and the buttons
        try:
            bot.send_message(uid, dm_text, reply_markup=kb)
        except Exception as e:
            print("Failed to send DM order:", e)
            try:
                bot.answer_callback_query(callback_query_id=c.id, text="Failed to open order in DM.")
            except:
                pass

        # minimal admin notification optional (disabled per request)
        # try:
        #     bot.send_message(ADMIN_ID, f"New order (no spam): {order_id} by {uid} for {title} — ₦{remaining_price}")
        # except:
        #     pass

        # done for now
        bot.answer_callback_query(callback_query_id=c.id, text="Order created. Check your DM.")
        return

    #END buy handler
    


    # ADD TO CART
    if data.startswith("addcart:"):
        try:
            mid = int(data.split(":",1)[1])
        except:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid movie id.")
            return
        ok = add_to_cart(uid, mid)
        if ok:
            bot.answer_callback_query(callback_query_id=c.id, text="An saka a cart. Duba Cart daga menu (🧾 Cart) ko danna Cart a menu.")
            try:
                bot.send_message(ADMIN_I, f"User {uid} added movie id {mid} to cart.")
            except:
                pass
        else:
            bot.answer_callback_query(callback_query_id=c.id, text="An riga an saka wannan fim a cart dinka.")
        return

    # REMOVECART
    if data.startswith("removecart:"):
        try:
            mid = int(data.split(":",1)[1])
        except:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid movie id.")
            return
        removed = remove_from_cart(uid, mid)
        if removed:
            bot.answer_callback_query(callback_query_id=c.id, text="An cire daga cart.")
            show_cart(uid, uid)
        else:
            bot.answer_callback_query(callback_query_id=c.id, text="Ba a sami abun ba a cart.")
        return

    # CLEAR CART
    if data == "clearcart":
        ok = clear_cart(uid)
        if ok:
            bot.answer_callback_query(callback_query_id=c.id, text="An goge cart ɗinka.")
            kb = InlineKeyboardMarkup()
            kb.row(InlineKeyboardButton(" ⤴️ KOMA FARKO", callback_data="go_home"),
                   InlineKeyboardButton("🫂Our Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
            # change language
            change_label = tr_user(uid, "change_language_button", default="🌐 Change your language")
            kb.row(InlineKeyboardButton(change_label, callback_data="change_language"))
            bot.send_message(uid, "Cart ɗinka yanzu babu komai.", reply_markup=kb)
        else:
            bot.answer_callback_query(callback_query_id=c.id, text="Failed to clear cart.")
        return

    # VIEW CART
    if data == "viewcart":
        show_cart(uid, uid)
        bot.answer_callback_query(callback_query_id=c.id)
        return

    # CHECKOUT
    if data == "checkout":
        rows = get_cart(uid)
        if not rows:
            bot.answer_callback_query(callback_query_id=c.id, text="Cart ɗinka babu komai.")
            return
        items = []
        total_before = 0
        for movie_id, title, price, file_id in rows:
            items.append({"movie_id": movie_id, "price": int(price)})
            total_before += int(price)
        remaining_price, applied_sum, applied_ids = apply_credits_to_amount(uid, int(total_before))
        order_id, total = create_group_order(uid, items)
        if not order_id:
            bot.answer_callback_query(callback_query_id=c.id, text="Failed to create order.")
            return
        clear_cart(uid)
        try:
            bot.send_message(ADMIN_I, f"Sabon Order Group: {order_id} by {uid} — Jimillar: ₦{remaining_price} (Unpaid). Applied credits: N{applied_sum}")
        except:
            pass
        if applied_sum > 0:
            bot.send_message(uid, f"An yi amfani da credit ɗinka N{applied_sum} a wannan sayayya. Jimillar da za a biya: ₦{remaining_price}.")
        if remaining_price <= 0:
            try:
                conn.execute("UPDATE orders SET paid=1 WHERE id=?", (order_id,))
                conn.commit()
            except:
                pass
            bot.send_message(uid, f"An rufe biyan oda {order_id} ta amfani da credits ɗinka. Za a tuntube ka don bayani.")
            try:
                bot.send_message(ADMIN_ID, f"Oda {order_id} ta riga ta kasance 'Paid' (used credits). User: {uid}. Jimillar: ₦{total_before}")
            except:
                pass
            try:
                check_referral_rewards_for_referred(uid)
            except:
                pass
            # send buy others + feedback
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🛍️ Buy others", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
            kb.add(InlineKeyboardButton("🆘 Support Help", callback_data="support_help"))
            bot.send_message(uid, "Alhamdulillah🥰 An rufe biyan. Idan kana son sayen wani fim, danna Buy others.", reply_markup=kb)
            send_feedback_prompt(uid, order_id)
            bot.answer_callback_query(callback_query_id=c.id, text="Order created and paid with credits.")
            return
        if FLUTTERWAVE_SECRET and FLUTTERWAVE_SECRET != "SAKA_FLW_SECRET":
            try:
                payload = {
                    "email": f"user{uid}@example.com",
                    "amount": int(remaining_price) * 100,
                    "reference": order_id,
                    "metadata": {"order_id": order_id, "user_id": str(uid)}
                }
                headers = {"Authorization": f"Bearer {FLUTTERWAVE_SECRET}", "Content-Type": "application/json"}
                resp = requests.post("https://api.flutterwave.com/v3/payments", json=payload, headers=headers, timeout=15)
                data = resp.json()
                if data.get("status"):
                    # try multiple possible link locations depending on Flutterwave API shape
                    link = None
                    try:
                        if isinstance(data.get("data"), dict):
                            link = data["data"].get("authorization_url") or data["data"].get("link") or data["data"].get("checkout_url")
                    except Exception:
                        link = None
                    if not link:
                        link = data.get("data") if isinstance(data.get("data"), str) else None
                    # store reference if available
                    try:
                        ref = None
                        if isinstance(data.get("data"), dict):
                            ref = data["data"].get("reference") or data["data"].get("tx_ref")
                        if ref:
                            conn.execute("UPDATE orders SET pay_ref=? WHERE id=?", (ref, order_id))
                            conn.commit()
                    except Exception:
                        pass
                    if link:
                        bot.send_message(uid, f"Don biyan dukkan abubuwan da ka zaba: {link}\nJimillar: ₦{remaining_price}")
                    else:
                        bot.send_message(uid, "An kirkiri odarka amma ba a samu link na biyan kudi daga Flutterwave ba. Tuntubi support.")
                else:
                    bot.send_message(uid, "Kuskure wajen kirkiro payment. Sai ka sake gwadawa.")
                bot.answer_callback_query(callback_query_id=c.id, text="An shirya oda. Ka biya ta link.")
            except Exception as e:
                print("paystack init error:", e)
                bot.answer_callback_query(callback_query_id=c.id, text="Error creating payment. Contact admin.")
        else:
            bot.send_message(uid, f"Flutterwave ba a saita ba (Paystack removed). An ƙirƙiri oda (ID: {order_id}) Jimillar: ₦{remaining_price}. Admin zai tuntuɓe ka.")
            bot.answer_callback_query(callback_query_id=c.id, text="Payment not configured. Order created locally.")
        return

    # ADD MOVIE (admin) -- NEW flow: store file in STORAGE_CHANNEL
    if data == "addmovie":
        if uid != ADMIN_ID:
            bot.answer_callback_query(callback_query_id=c.id, text="Only admin.")
            return
        # set admin state to expect the movie file
        admin_states[ADMIN_ID] = {"state": "add_movie_wait_file"}
        bot.send_message(uid, "Bani film ɗin da kake son siyarwa my Boss😁❤️", reply_markup=footer_kb(uid))
        bot.answer_callback_query(callback_query_id=c.id)
        return

    # EDIT TITLE (admin)
    if data == "edit_title":
        if uid != ADMIN_ID:
            bot.answer_callback_query(callback_query_id=c.id, text="Only admin.")
            return
        sent = bot.send_message(uid, "Sunan wanne film kakeso ka gyara? Aiko sunan ko ID na fim.")
        admin_states[ADMIN_ID] = {"state": "edit_title_wait_for_query", "inst_msgs": [sent.message_id]}
        bot.answer_callback_query(callback_query_id=c.id, text="Rubita sunan fim ko ID yanzu.")
        return

    # edit_delete flow (admin)
    if data.startswith("edit_delete:"):
        if uid != ADMIN_ID:
            bot.answer_callback_query(callback_query_id=c.id, text="Only admin.")
            return
        try:
            mid = int(data.split(":")[1])
        except:
            mid = None
        state_entry = admin_states.get(ADMIN_ID, {})
        insts = state_entry.get("inst_msgs", [])
        deleted = 0
        for msg_id in insts:
            try:
                bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
                deleted += 1
            except Exception as e:
                print("Failed to delete inst msg:", e)
        admin_states.pop(ADMIN_ID, None)
        try:
            bot.answer_callback_query(callback_query_id=c.id, text=f"An share {deleted} saƙon da bot ya aiko.")
        except:
            pass
        return

    # WEEKLY BUY
    if data.startswith("weekly_buy:"):
        try:
            idx = int(data.split(":",1)[1])
        except:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid selection.")
            return
        weekly_row = conn.execute("SELECT poster_file_id,items,channel_msg_id FROM weekly ORDER BY id DESC LIMIT 1").fetchone()
        if not weekly_row:
            bot.answer_callback_query(callback_query_id=c.id, text="Ba a samu listing na wannan satin ba.")
            return
        try:
            items = json.loads(weekly_row[1] or "[]")
        except:
            items = []
        if idx < 0 or idx >= len(items):
            bot.answer_callback_query(callback_query_id=c.id, text="Selection out of range.")
            return
        item = items[idx]
        title = item.get("title", "Film")
        price = int(item.get("price", 0))
        remaining_price, applied_sum, applied_ids = apply_credits_to_amount(uid, price)
        order_id = create_single_order_for_weekly(uid, title, remaining_price)
        if not order_id:
            bot.answer_callback_query(callback_query_id=c.id, text="Failed to create order. Contact admin.")
            return
        try:
            bot.send_message(ADMIN_ID, f"Sabon oda (weekly): User {uid} ya kirkiro oda {order_id} domin fim: {title} — ₦{remaining_price} (Unpaid). Applied credits: N{applied_sum}")
        except:
            pass
        if applied_sum > 0:
            bot.send_message(uid, f"An yi amfani da credit ɗinka N{applied_sum} a wannan sayayya. Za ka biya: ₦{remaining_price}.")
        if remaining_price <= 0:
            try:
                conn.execute("UPDATE orders SET paid=1 WHERE id=?", (order_id,))
                conn.commit()
            except:
                pass
            bot.send_message(uid, f"An rufe biyan oda {order_id} ta amfani da credits ɗinka. Za a tuntube ka don bayani.")
            try:
                check_referral_rewards_for_referred(uid)
            except:
                pass
            # send buy others and feedback
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🛍️ Buy others", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
            kb.add(InlineKeyboardButton("🆘 Support Help", callback_data="support_help"))
            bot.send_message(uid, "Alhamdulillah🥰 An rufe biyan. Idan kana son sayen wani fim, danna Buy others.", reply_markup=kb)
            send_feedback_prompt(uid, order_id)
            bot.answer_callback_query(callback_query_id=c.id, text="Order created and paid with credits.")
            return

    # Support Help -> Open admin DM directly (NO messages to admin, NO notifications)
    if data == "support_help":
        try:
            bot.answer_callback_query(callback_query_id=c.id)
        except:
            pass

        if ADMIN_USERNAME:
            # Open admin DM directly
            bot.send_message(uid, f"👉 Tuntuɓi admin kai tsaye: https://t.me/{ADMIN_USERNAME}")
        else:
            bot.send_message(uid, "Admin username bai sa ba. Tuntubi support.")
        return

 
    # fallback
    try:
        bot.answer_callback_query(callback_query_id=c.id)
    except:
        pass

# ========== message handlers (search, feedback input, admin messages) ==========





# ========== feedback prompt + callback handler for feedback ==========
def send_feedback_prompt(user_id, order_id):
    kb = InlineKeyboardMarkup()
    # emojis as buttons
    kb.add(
        InlineKeyboardButton("😁 Very satisfied", callback_data=f"feedback:very:{order_id}"),
        InlineKeyboardButton("🙂 Satisfied", callback_data=f"feedback:good:{order_id}")
    )
    kb.add(
        InlineKeyboardButton("😕 Not sure", callback_data=f"feedback:neutral:{order_id}"),
        InlineKeyboardButton("😓 Didn't like", callback_data=f"feedback:bad:{order_id}")
    )
    kb.add(InlineKeyboardButton("😠 Angry", callback_data=f"feedback:angry:{order_id}"))
    bot.send_message(user_id, "Ina fatan kana Jin dadin siyayya da wannan bot😁\nDan allah ka zabi me kakeji Karka wuce baka zaba ba", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("feedback:"))
def handle_feedback(c):
    data = c.data
    uid = c.from_user.id
    parts = data.split(":")
    if len(parts) < 3:
        bot.answer_callback_query(callback_query_id=c.id)
        return
    mood = parts[1]
    order_id = parts[2]
    try:
        chat = bot.get_chat(uid)
        fname = getattr(chat, "first_name", "") or ""
        masked = mask_name(fname)
    except:
        masked = "User"
    mood_map = {
        "very": "Gaskiya muna jin dadin siyya da wannan tsarin. Allah Kara daukaka🥰",
        "good": "Muna jin dadin siyayya",
        "neutral": "Ban iya amfani dashi sosai ba😓🥺",
        "bad": "Gaskiya wannan tsarin bot din bai min ba",
        "angry": "😠Banason wannan tsarin naku"
    }
    admin_text = f"📣 Feedback from {masked} — Order {order_id if order_id else 'N/A'}:\n\n{mood_map.get(mood, mood)}"
    try:
        bot.send_message(ADMIN_ID, admin_text)
    except:
        pass
    bot.answer_callback_query(callback_query_id=c.id, text="Mun karbi ra'ayinka. Na gode.")
    try:
        bot.send_message(uid, "Na gode da ra'ayinka. Za mu duba kuma za mu tuntube ka idan ya cancanta.")
    except:
        pass

# ========== /verify ==========
@bot.message_handler(commands=["verify"])
def verify_payment_cmd(message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        bot.reply_to(message, "Amfani: /verify <order_id>  (misali: /verify a1b2c3-... )")
        return
    order_id = parts[1].strip()
    row = conn.execute("SELECT id,user_id,amount,paid,pay_ref FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        bot.reply_to(message, "Ba a samu order ɗin ba a database.")
        return
    order_id_db, user_id, amount, paid, pay_ref = row
    if paid:
        bot.reply_to(message, f"Oda {order_id} ta riga ta kasance 'Paid'.")
        return
    if not FLUTTERWAVE_SECRET or FLUTTERWAVE_SECRET == "SAKA_FLW_SECRET":
        bot.reply_to(message, "Flutterwave ba a saita ba (Paystack removed). Ba zan iya tantance biyan ba.")
        return
    reference = pay_ref if pay_ref else order_id
    try:
        headers = {"Authorization": f"Bearer {FLUTTERWAVE_SECRET}"}
        resp = requests.get(f"https://api.flutterwave.com/v3/transactions/{reference}", headers=headers, timeout=15)
        data = resp.json()
        if data.get("status") and data.get("data") and data["data"].get("status") in ("success","successful"):
            conn.execute("UPDATE orders SET paid=1 WHERE id=?", (order_id,))
            conn.commit()
            bot.reply_to(message, f"An tabbatar da biyan oda {order_id}. An sabunta matsayin zuwa 'Paid'.")
            try:
                bot.send_message(ADMIN_ID, f"Oda {order_id} ta biya sosai. User: {user_id}. Jimillar: ₦{amount}")
            except:
                pass
            try:
                bot.send_message(user_id, f"Na tabbatar: Oda {order_id} an biya. Za a turo maka fim ɗin / info a cikin DM.")
            except:
                pass
            try:
                check_referral_rewards_for_referred(user_id)
            except Exception as e:
                print("referral check after verify error:", e)
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🛍️ Buy others", url=f"https://t.me/{CHANNEL.lstrip('@')}"))
            kb.add(InlineKeyboardButton("🆘 Support Help", callback_data="support_help"))
            try:
                bot.send_message(user_id, "Na gode! Idan kana son ƙarin saya, danna Buy others.", reply_markup=kb)
                send_feedback_prompt(user_id, order_id)
            except:
                pass
        else:
            bot.reply_to(message, "Biya bai tabbatar ba tukuna (Paystack returned not success).")
    except Exception as e:
        print("verify paystack error:", e)
        bot.reply_to(message, "Error yayin ƙoƙarin tantance biyan. Gwada daga baya ko tuntubi admin.")

# ========== /myorders command ==========
@bot.message_handler(commands=["myorders"])
def myorders(message):
    uid = message.from_user.id
    rows = conn.execute("SELECT id,movie_id,amount,paid FROM orders WHERE user_id=?", (uid,)).fetchall()
    if not rows:
        bot.reply_to(message, "Babu odarka tukuna.", reply_markup=reply_menu(uid))
        return
    txt = "Your orders:\n"
    for oid, mid, amount, paid in rows:
        if mid == -1:
            items = conn.execute("SELECT movie_id,price FROM order_items WHERE order_id=?", (oid,)).fetchall()
            txt += f"• Order Group {oid} — ₦{amount} — {'Paid' if paid else 'Unpaid'}\n"
            for m_id, price in items:
                title = conn.execute("SELECT title FROM movies WHERE id=?", (m_id,)).fetchone()
                title_text = title[0] if title else "Unknown"
                txt += f"   - {title_text} — ₦{price}\n"
        else:
            title = conn.execute("SELECT title FROM movies WHERE id=?", (mid,)).fetchone()
            title_text = title[0] if title else "Unknown"
            txt += f"• {title_text} — ₦{amount} — {'Paid' if paid else 'Unpaid'}\n"
    bot.send_message(uid, txt, reply_markup=reply_menu(uid))

# ========== ADMIN FILE UPLOAD (regular movie upload) ==========
@bot.message_handler(content_types=["photo", "video", "document"])
def file_upload(message):
    if message.from_user.id in ADMINS and admin_states.get(message.from_user.id):
        try:
            admin_inputs(message)
        except Exception as e:
            print("admin_inputs error while in file_upload:", e)
        return
    chat_username = getattr(message.chat, "username", None)
    if chat_username and ("@" + chat_username).lower() == CHANNEL.lower():
        caption = message.caption or (getattr(message, "caption_html", None) or "") or (getattr(message, "caption_markdown", None) or "")
        if not caption:
            caption = getattr(message, "text", "") or ""
        title, price = parse_caption_for_title_price(caption)
        if not title:
            title = (message.caption or getattr(message, "caption_html", "") or getattr(message, "caption_markdown", "")).strip() or f"Film {uuid.uuid4().hex[:6]}"
            price = 0
        file_id = None
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        elif message.content_type == "video":
            file_id = message.video.file_id
        elif message.content_type == "document":
            file_id = message.document.file_id
        try:
            exists = conn.execute("SELECT id FROM movies WHERE title=? COLLATE NOCASE", (title,)).fetchone()
            if not exists:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                channel_msg_id = getattr(message, "message_id", None)
                conn.execute("INSERT INTO movies(title,price,file_id,created_at,channel_msg_id,channel_username) VALUES(?,?,?,?,?,?)",
                             (title, price or 0, file_id, now, channel_msg_id, chat_username))
                conn.commit()
                print("Auto-saved channel post to movies:", title)
                prune_old_movies()
        except Exception as e:
            print("error saving channel post to db:", e)
        return
    # Non-admin uploads are ignored here. Admin flows are handled via admin_inputs when admin_states active.
    if message.from_user.id != ADMIN_ID:
        return
    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
    elif message.content_type == "video":
        file_id = message.video.file_id
    else:
        file_id = message.document.file_id
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "INSERT INTO movies(title,price,file_id,created_at) VALUES(?,?,?,?)",
            (title, price, file_id, now)
        )
        conn.commit()
        movie_id = cur.lastrowid
    except Exception as e:
        print("db insert movie error:", e)
        bot.reply_to(message, "Failed to save movie to database.")
        return
    post_caption = f"🎬 <b>{title}</b>\n💵 ₦{price}\nTap buttons to buy or add to cart."
    markup = movie_buttons_inline(movie_id, user_id=None)
    try:
        sent_msg = None
        if message.content_type == "photo":
            sent_msg = bot.send_photo(CHANNEL, file_id, caption=post_caption, parse_mode="HTML", reply_markup=markup)
        elif message.content_type == "video":
            sent_msg = bot.send_video(CHANNEL, file_id, caption=post_caption, parse_mode="HTML", reply_markup=markup)
        else:
            sent_msg = bot.send_document(CHANNEL, file_id, caption=post_caption, parse_mode="HTML", reply_markup=markup)
        bot.reply_to(message, f"Posted to {CHANNEL} with buttons. Movie id: {movie_id}")
        try:
            channel_msg_id = sent_msg.message_id if sent_msg else None
            channel_username = CHANNEL.lstrip("@")
            conn.execute("UPDATE movies SET channel_msg_id=?, channel_username=? WHERE id=?", (channel_msg_id, channel_username, movie_id))
            conn.commit()
        except Exception as e:
            print("failed to store channel msg id:", e)
        prune_old_movies()
    except Exception as e:
        print("post to channel error:", e)
        bot.reply_to(message, f"Saved locally (ID: {movie_id}) but failed to post to channel. Error: {e}")

# ========== MISSING HELPERS (basic implementations if not present) ==========
# These are minimal implementations so the bot runs. If you already have your own versions, replace them.

def get_cart(user_id):
    rows = conn.execute("SELECT c.movie_id, m.title, m.price, m.file_id FROM cart c JOIN movies m ON c.movie_id=m.id WHERE c.user_id=?", (user_id,)).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]

def add_to_cart(user_id, movie_id):
    exists = conn.execute("SELECT id FROM cart WHERE user_id=? AND movie_id=?", (user_id, movie_id)).fetchone()
    if exists:
        return False
    conn.execute("INSERT INTO cart(user_id,movie_id) VALUES(?,?)", (user_id, movie_id))
    conn.commit()
    return True

def remove_from_cart(user_id, movie_id):
    cur = conn.execute("DELETE FROM cart WHERE user_id=? AND movie_id=?", (user_id, movie_id))
    conn.commit()
    return cur.rowcount > 0

def clear_cart(user_id):
    cur = conn.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
    conn.commit()
    return True

def create_group_order(user_id, items):
    # items: list of {"movie_id":id,"price":price}
    order_id = str(uuid.uuid4())
    total = sum(i["price"] for i in items)
    try:
        conn.execute("INSERT INTO orders(id,user_id,movie_id,amount,paid) VALUES(?,?,?,?,0)", (order_id, user_id, -1, total))
        for it in items:
            conn.execute("INSERT INTO order_items(order_id,movie_id,price) VALUES(?,?,?)", (order_id, it["movie_id"], it["price"]))
        conn.commit()
        return order_id, total
    except Exception as e:
        print("create_group_order error:", e)
        return None, 0

def create_single_order_for_weekly(user_id, title, amount):
    order_id = str(uuid.uuid4())
    try:
        conn.execute("INSERT INTO orders(id,user_id,movie_id,amount,paid) VALUES(?,?,?,?,0)", (order_id, user_id, -1, amount))
        conn.commit()
        return order_id
    except Exception as e:
        print("create_single_order_for_weekly error:", e)
        return None

def save_weekly(poster_file_id, items_for_db, channel_msg_id=None):
    try:
        items_json = json.dumps(items_for_db)
        conn.execute("INSERT INTO weekly(poster_file_id,items,channel_msg_id) VALUES(?,?,?)", (poster_file_id, items_json, channel_msg_id))
        conn.commit()
    except Exception as e:
        print("save_weekly error:", e)

# ======= ADDED BY ASSISTANT: PAYMENT + CART HELPERS (non-destructive append) =======
# These functions are added to extend existing bot without removing or modifying original handlers.
# They use existing conn, bot, and database tables where possible.
import os
import threading
from flask import Flask, request

# Use existing conn and bot from original file. If not present, these helpers will try to use them safely.
try:
    conn  # noqa
except NameError:
    conn = None

try:
    bot  # noqa
except NameError:
    bot = None

_assist_flw_secret = os.environ.get("FLW_SECRET", "")
_assist_flw_webhook_secret = os.environ.get("FLW_WEBHOOK_SECRET", "")
_assist_currency = "NGN"

# create additional tables if missing (cart_items, deliveries, order_items may already exist)
def assist_create_tables():
    if conn is None:
        return
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS cart_items_assist(
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     user_id INTEGER,
     movie_id INTEGER,
     added_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS deliveries_assist(
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     user_id INTEGER,
     movie_id INTEGER,
     delivered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
     order_id TEXT
    );
    """)
    conn.commit()

# safe add to cart (non-conflicting table)
def assist_add_to_cart(user_id, movie_id):
    if conn is None:
        return False
    cur = conn.cursor()
    r = cur.execute("SELECT id FROM cart_items_assist WHERE user_id=? AND movie_id=?", (user_id, movie_id)).fetchone()
    if r:
        return False
    cur.execute("INSERT INTO cart_items_assist(user_id,movie_id) VALUES(?,?)", (user_id, movie_id))
    conn.commit()
    return True

def assist_get_cart(user_id):
    if conn is None:
        return []
    cur = conn.cursor()
    rows = cur.execute("SELECT ci.movie_id, m.title, m.price, m.file_id FROM cart_items_assist ci JOIN movies m ON ci.movie_id=m.id WHERE ci.user_id=?", (user_id,)).fetchall()
    return rows

def assist_clear_cart(user_id):
    if conn is None:
        return True
    cur = conn.cursor()
    cur.execute("DELETE FROM cart_items_assist WHERE user_id=?", (user_id,))
    conn.commit()
    return True

def assist_record_delivery(user_id, movie_id, order_id=None):
    if conn is None:
        return
    cur = conn.cursor()
    cur.execute("INSERT INTO deliveries_assist(user_id,movie_id,order_id) VALUES(?,?,?)", (user_id, movie_id, order_id))
    conn.commit()

def assist_delivery_exists(user_id, movie_id):
    if conn is None:
        return False
    cur = conn.cursor()
    r = cur.execute("SELECT id FROM deliveries_assist WHERE user_id=? AND movie_id=?", (user_id, movie_id)).fetchone()
    return bool(r)

# Minimal Flask app for webhook if original doesn't run one. We attach to same process only if Flask present.
_assist_flask_app = None
try:
    from flask import Flask
    _assist_flask_app = Flask(name)
except Exception:
    _assist_flask_app = None

if _assist_flask_app:
    @_assist_flask_app.route("/assist_flw_webhook", methods=["POST"])
    def _assist_flw_webhook():
        try:
            payload = request.get_json(force=True)
        except Exception:
            return "invalid", 400
        # verify header if secret set
        if _assist_flw_webhook_secret:
            header = request.headers.get("verif-hash")
            if header != _assist_flw_webhook_secret:
                return "unauthorized", 403
        data = payload.get("data") or payload
        status = data.get("status")
        tx_ref = data.get("tx_ref") or data.get("reference")
        meta = data.get("meta") or {}
        order_id = meta.get("order_id") or tx_ref
        user_id = int(meta.get("user_id")) if meta.get("user_id") else None
        amount = data.get("amount") or data.get("charged_amount") or data.get("settled_amount")
        currency = data.get("currency") or _assist_currency
        if status == "successful" and currency == _assist_currency:
            # deliver items for this order_id using order_items table if exists, else send single movie matching orders table
            try:
                cur = conn.cursor()
                items = cur.execute("SELECT movie_id FROM order_items WHERE order_id=?", (order_id,)).fetchall()
                if not items:
                    # fallback: try orders table movie_id  
                    row = cur.execute("SELECT movie_id FROM orders WHERE id=?", (order_id,)).fetchone()  
                    if row and row[0] and int(row[0])!=-1:  
                        items = [(row[0],)]  
                  
                
               

        # fallback: try orders table movie_id  
                    row = cur.execute("SELECT movie_id FROM orders WHERE id=?", (order_id,)).fetchone()  
                    if row and row[0] and int(row[0])!=-1:  
                        items = [(row[0],)]  
                # send dm with buttons  
                if items and user_id and bot:  
                    try:  
                        txt = f"🎉 Payment Successful!\n\nTransaction: {tx_ref}\nAmount: ₦{int(amount)}\n\nClick to download your movies."  
                        kb = []  
                        # build simple inline keyboard using bot types if available  
                        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton  
                        kb = InlineKeyboardMarkup()  
                        for it in items:  
                            mid = int(it[0])  
                            mv = cur.execute("SELECT title FROM movies WHERE id=?", (mid,)).fetchone()  
                            title = mv[0] if mv else f"Movie {mid}"  
                            kb.add(InlineKeyboardButton(f"Get - {title}", callback_data=f"assist_get:{mid}"))  
                        bot.send_message(user_id, txt, reply_markup=kb)  
                    except Exception as e:  
                        print("assist send dm error:", e)  
                # mark order paid  
                try:  
                    cur.execute("UPDATE orders SET paid=1, pay_ref=? WHERE id=?", (tx_ref, order_id))  
                    conn.commit()  
                except:  
                    pass  
            except Exception as e:  
                print("assist webhook processing error:", e)  
        return "ok", 200

# register the assist callback handler if bot exists
try:
    bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("assist_get:"))(assist_callback_handler)
except Exception:
    pass

# create tables on import
assist_create_tables()

@bot.callback_query_handler(func=lambda call: call.data == "confirm_payment_menu")
def confirm_payment_menu(call):
    user_id = call.from_user.id
    data = get_pending_order(user_id)
    if not data:
        bot.answer_callback_query(call.id, "No pending payment.")
        return
    order_id, amount, payment_link = data
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🔁 Continue Payment", url=payment_link))
    keyboard.add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_pending_payment"))
    keyboard.add(types.InlineKeyboardButton("🔍 I Have Paid", callback_data="verify_payment_now"))
    bot.send_message(user_id, f"Pending payment for order {order_id}.", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_pending_payment")
def cancel_pending_payment(call):
    clear_pending_order(call.from_user.id)
    bot.edit_message_text("❌ Payment canceled.", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "verify_payment_now")
def verify_payment_now(call):
    user_id = call.from_user.id

    data = get_pending_order(user_id)
    if not data:
        bot.answer_callback_query(call.id, "No pending payment.")
        return

    order_id, amount, payment_link = data

    import requests
    from datetime import datetime

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    # STEP 1: search transaction using tx_ref
    search_url = f"https://api.flutterwave.com/v3/transactions?tx_ref={order_id}"

    try:
        search_result = requests.get(
            search_url,
            headers=headers,
            timeout=30
        ).json()
    except Exception:
        bot.send_message(user_id, "❌ Network error. Try again.")
        return

    try:
        transaction_id = search_result["data"][0]["id"]
    except Exception:
        bot.send_message(
            user_id,
            "❌ Could not find payment. Make sure you paid correctly."
        )
        return

    # STEP 2: verify payment
    verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"

    try:
        verify_result = requests.get(
            verify_url,
            headers=headers,
            timeout=30
        ).json()
    except Exception:
        bot.send_message(user_id, "❌ Payment verification failed. Try again.")
        return

    # STEP 3: read verification result safely
    try:
        status = verify_result["data"]["status"]
        paid_amount = float(verify_result["data"]["amount"])
    except Exception:
        bot.send_message(user_id, "❌ Invalid verification response.")
        return

    # STEP 4: confirm payment
    if status == "successful" and float(amount) <= paid_amount:
        clear_pending_order(user_id)

        bot.send_message(
            user_id,
            "✅ Payment confirmed!\nYour order is being processed."
        )

        bot.send_message(
            PAYMENT_NOTIFY_GROUP,
            f"""
📢 NEW PAYMENT RECEIVED!

👤 User: {call.from_user.first_name or ""} {call.from_user.last_name or ""}
🆔 User ID: {user_id}
🧾 Order ID: {order_id}
💳 Amount: ₦{amount}
⏰ Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
        )
    else:
        bot.send_message(
            user_id,
            "❌ Payment not found or incomplete. If you already paid, wait a bit and try again."
        )


# =========================
# SERVER / LOCAL MODE HANDLER
# =========================
import os

def is_server():
    # Railway sets certain environment vars
    return bool(os.environ.get("RAILWAY_STATIC_URL")) or bool(os.environ.get("PORT"))

def run_bot():
    try:
        bot.remove_webhook()
    except Exception:
        pass

    if is_server():
        print("SERVER MODE ENABLED → Using Webhook")
        WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://placeholder-url.com/webhook")
        bot.set_webhook(url=WEBHOOK_URL)
    else:
        print("LOCAL/PHONE MODE → Polling")
        bot.infinity_polling(skip_pending=True)
        kb.add(InlineKeyboardButton("🗑 ERASE DATA", callback_data="erase_data"))

run_bot()



# ================= ADDED VIEWALL + BUYALL HANDLERS =================

def send_weekly_list(msg):
    uid = msg.chat.id
    cur = conn.cursor()

    row = cur.execute("SELECT msg_id FROM weekly_pinned ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        bot.send_message(uid, "Ga fina-finan wannan satin babu su tukuna.")
        return

    movies = cur.execute(
        "SELECT id,title,price,file_id,created_at FROM movies ORDER BY id DESC LIMIT 50"
    ).fetchall()

    if not movies:
        bot.send_message(uid, "Ga fina-finan wannan satin babu su tukuna.")
        return

    date_str = movies[0][4].split(" ")[0] if movies[0][4] else "Unknown"
    text = f"🎬 *Weak update ({date_str})*\n\n"
    kb = InlineKeyboardMarkup()
    buy_all_ids = []

    for m in movies:
        mid, title, price, file_id, dt = m
        buy_all_ids.append(mid)
        text += f"{title} - {price}\n"
        kb.row(
            InlineKeyboardButton("Add cart", callback_data=f"addcart:{mid}"),
            InlineKeyboardButton("Buy now", callback_data=f"buy:{mid}")
        )

    all_ids_str = ",".join(str(i) for i in buy_all_ids)
    # use token if callback_data would be long
    try:
        token_map
    except NameError:
        token_map = {}
    if len(all_ids_str) > 48 or "," in all_ids_str and len(all_ids_str) > 30:
        import uuid as _uuid
        tkn = _uuid.uuid4().hex[:12]
        conn.execute("INSERT OR REPLACE INTO buyall_tokens(token,ids) VALUES(?,?)",(tkn,all_ids_str))
        kb.row(InlineKeyboardButton("🎁 BUY ALL", callback_data=f"buyall:{tkn}"))
    else:
        kb.row(InlineKeyboardButton("🎁 BUY ALL", callback_data=f"buyall:{all_ids_str}"))
    bot.send_message(uid, text, reply_markup=kb, parse_mode="Markdown")



# ===================== BUY ALL HANDLER (for callback_data like "buyall:1,2,3") =====================
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("buyall:"))
def buy_all_handler(call):
    uid = call.from_user.id
    try:
        ids_str = call.data.split("buyall:", 1)[1]
        ids_list = [int(x) for x in ids_str.split(",") if x.strip().isdigit()]
    except Exception:
        try:
            bot.answer_callback_query(call.id, "Invalid BUY ALL request.")
        except:
            pass
        return

    if not ids_list:
        try:
            bot.answer_callback_query(call.id, "No movies selected.")
        except:
            pass
        return

    items = []
    total = 0

    for mid in ids_list:
        try:
            row = conn.execute("SELECT id, title, price FROM movies WHERE id=?", (mid,)).fetchone()
        except Exception as e:
            print("buy_all_handler select movie error:", e)
            row = None
        if row:
            _id, title, price = row
            try:
                price = int(price) if price is not None else 0
            except:
                price = 0
            items.append({"movie_id": _id, "title": title, "price": price})
            total += price

    if not items:
        try:
            bot.answer_callback_query(call.id, "Movies not found.")
        except:
            pass
        return

    # Apply 10% discount if 10 or more movies
    original_total = total
    if len(items) >= 10:
        discount = int(original_total * 0.10)
        final_total = original_total - discount
    else:
        discount = 0
        final_total = original_total

    # Create group order
    try:
        order_id, created_amount = create_group_order(uid, [{"movie_id": i["movie_id"], "price": i["price"]} for i in items])
    except Exception as e:
        print("buy_all_handler create_group_order error:", e)
        order_id = None
        created_amount = final_total

    if not order_id:
        try:
            bot.answer_callback_query(call.id, "Failed to create order. Try again later.")
        except:
            pass
        return

    # Build order summary text
    lines = []
    for it in items:
        lines.append(f"🎬 {it['title']} — ₦{it['price']}")
    summary = "\n".join(lines)

    text_msg = (
        f"🧾 <b>Order Created Successfully (BUY ALL)</b>\n\n"
        f"{summary}\n\n"
        f"<b>Original Total:</b> ₦{original_total}\n"
        f"<b>Discount:</b> ₦{discount}\n"
        f"<b>Final Total:</b> ₦{final_total}"
    )

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Confirm payment", callback_data=f"confirmall:{order_id}"),
        InlineKeyboardButton("❌ Cancel order", callback_data=f"cancel:{order_id}")
    )

    try:
        bot.send_message(uid, text_msg, parse_mode="HTML", reply_markup=kb)
        try:
            bot.answer_callback_query(call.id)
        except:
            pass
    except Exception as e:
        print("buy_all_handler send DM error:", e)
        try:
            bot.answer_callback_query(call.id, "Failed to open DM. Start the bot and try again.")
        except:
            pass

# -------------------- BUY ALL (weekly) handler --------------------
@bot.callback_query_handler(func=lambda c: c.data and c.data == "buyall_week")
def buy_all_week_handler(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id if call.message else uid
    # fetch latest weekly items (assumes weekly.items is comma-separated ids)
    try:
        row = conn.execute("SELECT items FROM weekly ORDER BY id DESC LIMIT 1").fetchone()
    except Exception as e:
        print("buy_all_week_handler DB error:", e)
        try:
            bot.answer_callback_query(call.id, "Internal error reading weekly list.")
        except:
            pass
        return

    if not row or not row[0]:
        try:
            bot.answer_callback_query(call.id, "No weekly movies available.")
        except:
            pass
        return

    items_raw = row[0]
    # items_raw may be stored as CSV like "12,13,14" or JSON; try CSV first
    ids_list = []
    try:
        ids_list = [int(x.strip()) for x in str(items_raw).split(",") if x.strip().isdigit()]
    except Exception:
        ids_list = []

    if not ids_list:
        try:
            bot.answer_callback_query(call.id, "No valid movies found in weekly list.")
        except:
            pass
        return

    # fetch movie details
    items = []
    total = 0
    for mid in ids_list:
        try:
            mv = conn.execute("SELECT id, title, price FROM movies WHERE id=?", (mid,)).fetchone()
        except Exception as e:
            print("buy_all_week_handler select movie error:", e)
            mv = None
        if mv:
            _mid, title, price = mv
            try:
                price = int(price) if price is not None else 0
            except:
                price = 0
            items.append({"movie_id": _mid, "title": title, "price": price})
            total += price

    if not items:
        try:
            bot.answer_callback_query(call.id, "Movies not available.")
        except:
            pass
        return

    # Apply 10% discount if 10 or more movies
    original_total = total
    if len(items) >= 10:
        discount = int(original_total * 0.10)
        final_total = original_total - discount
    else:
        discount = 0
        final_total = original_total

    # create group order (uses existing helper create_group_order)
    try:
        order_id, created_amount = create_group_order(uid, [{"movie_id": i["movie_id"], "price": i["price"]} for i in items])
    except Exception as e:
        print("buy_all_week_handler create_group_order error:", e)
        order_id = None
        created_amount = final_total

    if not order_id:
        try:
            bot.answer_callback_query(call.id, "Failed to create order. Try again later.")
        except:
            pass
        return

    # Build order summary text
    lines = []
    for it in items:
        lines.append(f"🎬 {it['title']} — ₦{it['price']}")
    summary = "\n".join(lines)

    text_msg = (
        f"🧾 <b>Order Created Successfully (BUY ALL)</b>\n\n"
        f"{summary}\n\n"
        f"<b>Original Total:</b> ₦{original_total}\n"
        f"<b>Discount:</b> ₦{discount}\n"
        f"<b>Final Total:</b> ₦{final_total}"
    )

    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("✅ Confirm payment", callback_data=f"confirmall:{order_id}"),
        InlineKeyboardButton("❌ Cancel order", callback_data=f"cancel:{order_id}")
    )

    # prefer DM
    try:
        bot.send_message(uid, text_msg, parse_mode="HTML", reply_markup=kb)
        try:
            bot.answer_callback_query(call.id)
        except:
            pass
    except Exception as e:
        print("buy_all_week_handler send DM error:", e)
        try:
            bot.answer_callback_query(call.id, "Failed to open DM. Start the bot and try again.")
        except:
            pass

# -------------------- end buy all weekly handler --------------------

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("confirm:"))
def handle_confirm_payment(c):
    try:
        uid = c.from_user.id
        parts = c.data.split(":", 1)
        order_id = parts[1]
    except Exception as e:
        try:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid confirm data.")
        except:
            pass
        return
    # fetch order amount
    try:
        row = conn.execute("SELECT amount FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            try:
                bot.answer_callback_query(callback_query_id=c.id, text="Order not found.")
            except:
                pass
            return
        amount = row[0]
    except Exception as e:
        print("DB error on confirm:", e)
        try:
            bot.answer_callback_query(callback_query_id=c.id, text="Internal error.")
        except:
            pass
        return

    # create flutterwave link
    if not (globals().get("FLUTTERWAVE_SECRET")):
        try:
            bot.answer_callback_query(callback_query_id=c.id, text="Payment gateway not configured.")
        except:
            pass
        try:
            bot.send_message(uid, "Payment is not configured. Contact support.")
        except:
            pass
        return

    try:
        bot.answer_callback_query(callback_query_id=c.id, text="Creating payment link...")
    except:
        pass

    paylink = create_flutterwave_payment(uid, order_id, amount, "")
    if not paylink:
        try:
            bot.send_message(uid, "Failed to create payment link. Please try again later or contact support.")
        except:
            pass
        return

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Open payment page", url=paylink))
    kb.add(InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancel:{order_id}"))

    try:
        bot.send_message(uid, "🔗 Click below to complete your payment:", reply_markup=kb)
    except Exception as e:
        print("Failed to send payment link DM:", e)
        try:
            bot.send_message(uid, f"Payment Link: {paylink}")
        except:
            pass

# Cancel order handler (also works from confirm stage)
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("cancel:"))
def handle_cancel_order_cb(c):
    try:
        uid = c.from_user.id
        oid = c.data.split(":",1)[1]
    except:
        try:
            bot.answer_callback_query(callback_query_id=c.id, text="Invalid cancel data.")
        except:
            pass
        return
    try:
        conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
        conn.execute("DELETE FROM orders WHERE id=?", (oid,))
        conn.commit()
    except Exception as e:
        print("cancel order error:", e)
        try:
            bot.answer_callback_query(callback_query_id=c.id, text="Failed to cancel order.")
        except:
            pass
        try:
            bot.send_message(uid, "Failed to cancel order. Contact support.")
        except:
            pass
        return

    try:
        bot.answer_callback_query(callback_query_id=c.id, text="Order cancelled")
    except:
        pass
    try:
        bot.send_message(uid, "Order cancelled successfully ❌")
    except:
        pass


# /start paid_{order_id} handler - user returns from Flutterwave redirect
@bot.message_handler(func=lambda m: m.text and m.text.startswith("/start paid_"))
def paid_start_handler(m):
    try:
        parts = m.text.split("paid_")
        order_id = parts[1]
    except:
        bot.send_message(m.chat.id, "Invalid payment callback.")
        return

    # verify payment via Flutterwave verify payments endpoint
    try:
        # we will attempt to verify by tx_ref equal to order_id
        url = f"https://api.flutterwave.com/v3/transactions/verify_by_reference?tx_ref={order_id}"
        headers = {"Authorization": f"Bearer {FLUTTERWAVE_SECRET}"}
        r = requests.get(url, headers=headers, timeout=15)
        js = r.json()
    except Exception as e:
        print("Flutterwave verify error:", e)
        bot.send_message(m.chat.id, "❌ Failed to verify payment. Try again or contact support.")
        return
        # status check may vary; attempt to inspect data
status_ok = False
if js.get("status") == "success":
    data = js.get("data")
    if isinstance(data, dict):
        if data.get("status") in ("successful", "success") or data.get("processor_response") in ("Approved", "Successful"):
            status_ok = True

if status_ok:
    # mark order paid  
    try:  
        conn.execute("UPDATE orders SET paid=1 WHERE id=?", (order_id,))  
        conn.commit()  
    except:  
        pass  

    bot.send_message(
        m.chat.id,
        "🎉 Payment verified. Your order is now marked paid. You will receive your movie shortly."
    )  

    # Trigger post-payment behavior if any (send movie, notify admin etc.)
    try:
        if PAYMENT_NOTIFY_GROUP:
            bot.send_message(
                PAYMENT_NOTIFY_GROUP,
                f"""
📢 NEW PAYMENT RECEIVED!

👤 User: {m.from_user.first_name or ""} {m.from_user.last_name or ""}
🆔 User ID: {m.from_user.id}
🧾 Order ID: {order_id}
💳 Amount: ₦{amount}
⏰ Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
            )
    except:
        pass

else:
    bot.send_message(
        m.chat.id,
        "Payment not verified yet. If you already paid, wait a few seconds and press the payment link again."
    )
   # ================= RENDER WEB SERVICE FIX =================
from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Bot is running on Render"

def run_bot():
    bot.infinity_polling(
        skip_pending=True,
        timeout=20,
        long_polling_timeout=20
    )

# Start bot in background thread (Render-safe)
t = threading.Thread(target=run_bot, daemon=True)
t.start()