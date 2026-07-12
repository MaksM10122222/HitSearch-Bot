"""
HITSearchBot - ПОЛНАЯ ВЕРСИЯ (DEEPSCAN + CERERA)
"""

import asyncio
import json
import re
import os
import sqlite3
import logging
import asyncpg
from datetime import datetime
from io import BytesIO
from typing import Dict, Optional, List
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# ============= ОТКЛЮЧЕНИЕ ПРОКСИ =============
for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 
            'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy',
            'SOCKS_PROXY', 'socks_proxy', 'SOCKS5_PROXY', 'socks5_proxy']:
    os.environ.pop(var, None)
os.environ['NO_PROXY'] = '*'

# ============= НАСТРОЙКИ =============
TELEGRAM_BOT_TOKEN = "8907678614:AAGPMEE42azP4FjRkUmDKI8rPp_a4Ua6Gfg"
ADMIN_USER_ID = 8688258357

# API КЛЮЧИ
DEEPSCAN_TOKEN = "deepscan_8688258357:SOmmx1p2"
VK_ACCESS_TOKEN = "vk1.a.U3pTiiT7sF-WgWulS7kTo_Tkez3TMZtgLeB-pK96-bOSiq7zrGjCRim8T5LARyZP-Ju7oIgZCIKxKyXis_oR8ty09faVwTEjGsFKzOIYgWVvymXu9JpiqwbhflLzGp7sh9tp2IbPsuoP8Gv-VF90gbSdQ0aIhAXdJdbvPimDJto96QfkIDosurBU3NqdT4CQh9SJV_xEokTfh0RucK4J-A"
CERERA_TOKEN = "ca_HAw90ycruzfgq3QRFvtcVLvwDkRzN49y7nRxwMQSrWM"  # ВСТАВЬ СВОЙ ТОКЕН: ca_...

# РЕФЕРАЛЬНАЯ СИСТЕМА
REFERRAL_BONUS = 1
INITIAL_SEARCHES = 2

# БАЗА ДАННЫХ
DATABASE_URL = os.environ.get("DATABASE_URL")

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0, proxy=None)


# ============= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =============
def escape_md(text: str) -> str:
    if not text:
        return ""
    for ch in r'_*[]()~`>#+-=|{}.!':
        text = text.replace(ch, '\\' + ch)
    return text


# ============= БАЗА ДАННЫХ =============
async def get_db():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_db()
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            searches INT DEFAULT 0,
            registered_at TIMESTAMP,
            phone_searches INT DEFAULT 0,
            email_searches INT DEFAULT 0,
            ip_searches INT DEFAULT 0,
            vk_searches INT DEFAULT 0,
            tg_searches INT DEFAULT 0,
            fio_searches INT DEFAULT 0,
            inn_searches INT DEFAULT 0,
            snils_searches INT DEFAULT 0,
            passport_searches INT DEFAULT 0
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            user_id BIGINT PRIMARY KEY,
            referrer_id BIGINT,
            referral_link TEXT,
            referrals_count INT DEFAULT 0,
            registered_at TIMESTAMP
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS hidden_items (
            id SERIAL PRIMARY KEY,
            search_type TEXT NOT NULL,
            value TEXT NOT NULL,
            added_by BIGINT,
            added_at TIMESTAMP,
            UNIQUE(search_type, value)
        )
    ''')
    await conn.close()


# ============= РЕФЕРАЛЬНЫЕ ФУНКЦИИ =============
def generate_referral_link(user_id: int) -> str:
    return f"https://t.me/HITSEARCHROBOT?start=ref_{user_id}"

async def is_user_registered(user_id: int) -> bool:
    conn = await get_db()
    result = await conn.fetchval('SELECT user_id FROM users WHERE user_id = $1', user_id)
    await conn.close()
    return result is not None

async def register_user(user_id: int) -> bool:
    if await is_user_registered(user_id):
        return False
    conn = await get_db()
    await conn.execute('INSERT INTO users (user_id, searches, registered_at) VALUES ($1, $2, $3)',
                       user_id, INITIAL_SEARCHES, datetime.now().isoformat())
    await conn.close()
    return True

async def get_referrals_count(user_id: int) -> int:
    conn = await get_db()
    result = await conn.fetchval('SELECT referrals_count FROM referrals WHERE user_id = $1', user_id)
    await conn.close()
    return result if result else 0

async def add_referral(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False
    if await is_user_registered(user_id):
        return False
    conn = await get_db()
    exists = await conn.fetchval('SELECT user_id FROM referrals WHERE user_id = $1', user_id)
    if exists:
        await conn.close()
        return False
    await conn.execute('INSERT INTO referrals (user_id, referrer_id, referral_link, registered_at) VALUES ($1, $2, $3, $4)',
                       user_id, referrer_id, "", datetime.now().isoformat())
    await conn.execute('UPDATE referrals SET referrals_count = referrals_count + 1 WHERE user_id = $1', referrer_id)
    await conn.close()
    await add_searches_to_user(user_id, REFERRAL_BONUS)
    return True

async def get_referral_link(user_id: int) -> str:
    conn = await get_db()
    link = await conn.fetchval('SELECT referral_link FROM referrals WHERE user_id = $1', user_id)
    await conn.close()
    if link:
        return link
    new_link = generate_referral_link(user_id)
    conn = await get_db()
    await conn.execute('INSERT INTO referrals (user_id, referral_link) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET referral_link = $2',
                       user_id, new_link)
    await conn.close()
    return new_link


# ============= ФУНКЦИИ ЗАПРОСОВ =============
async def get_searches(user_id: int) -> int:
    conn = await get_db()
    result = await conn.fetchval('SELECT searches FROM users WHERE user_id = $1', user_id)
    await conn.close()
    return result if result else 0

async def add_searches_to_user(user_id: int, amount: int):
    conn = await get_db()
    if await is_user_registered(user_id):
        await conn.execute('UPDATE users SET searches = searches + $1 WHERE user_id = $2', amount, user_id)
    else:
        await conn.execute('INSERT INTO users (user_id, searches, registered_at) VALUES ($1, $2, $3)',
                          user_id, amount, datetime.now().isoformat())
    await conn.close()

async def decrement_search(user_id: int) -> bool:
    if user_id == ADMIN_USER_ID:
        return True
    conn = await get_db()
    searches = await conn.fetchval('SELECT searches FROM users WHERE user_id = $1', user_id)
    if not searches or searches < 1:
        await conn.close()
        return False
    await conn.execute('UPDATE users SET searches = searches - 1 WHERE user_id = $1', user_id)
    await conn.close()
    return True

async def can_search(user_id: int) -> bool:
    if user_id == ADMIN_USER_ID:
        return True
    return await get_searches(user_id) > 0

async def get_user_stats(user_id: int) -> Dict:
    conn = await get_db()
    row = await conn.fetchrow('SELECT phone_searches, email_searches, ip_searches, vk_searches, tg_searches, fio_searches, inn_searches, snils_searches, passport_searches, searches FROM users WHERE user_id = $1', user_id)
    await conn.close()
    if row:
        return {
            "phone": row[0] or 0, "email": row[1] or 0, "ip": row[2] or 0,
            "vk": row[3] or 0, "tg": row[4] or 0, "fio": row[5] or 0,
            "inn": row[6] or 0, "snils": row[7] or 0, "passport": row[8] or 0,
            "total": row[9] or 0
        }
    return {"phone": 0, "email": 0, "ip": 0, "vk": 0, "tg": 0, "fio": 0, "inn": 0, "snils": 0, "passport": 0, "total": 0}


# ============= СКРЫТИЕ =============
async def add_shadow_username(username: str, admin_id: int, platform: str) -> bool:
    normalized = username.lstrip('@').lower()
    conn = await get_db()
    try:
        await conn.execute('INSERT INTO hidden_items (search_type, value, added_by, added_at) VALUES ($1, $2, $3, $4)',
                          f"{platform}_username", normalized, admin_id, datetime.now().isoformat())
        await conn.close()
        return True
    except:
        await conn.close()
        return False

async def is_username_shadowed(username: str, platform: str) -> bool:
    normalized = username.lstrip('@').lower()
    return await is_hidden(f"{platform}_username", normalized)

async def add_hidden_item(search_type: str, value: str, admin_id: int) -> bool:
    conn = await get_db()
    try:
        await conn.execute('INSERT INTO hidden_items (search_type, value, added_by, added_at) VALUES ($1, $2, $3, $4)',
                          search_type.lower(), value.lower().strip(), admin_id, datetime.now().isoformat())
        await conn.close()
        return True
    except:
        await conn.close()
        return False

async def remove_hidden_item(search_type: str, value: str) -> bool:
    conn = await get_db()
    result = await conn.execute('DELETE FROM hidden_items WHERE search_type = $1 AND value = $2',
                               search_type.lower(), value.lower().strip())
    await conn.close()
    return result != "DELETE 0"

async def is_hidden(search_type: str, value: str) -> bool:
    conn = await get_db()
    result = await conn.fetchval('SELECT 1 FROM hidden_items WHERE search_type = $1 AND value = $2',
                                search_type.lower(), value.lower().strip())
    await conn.close()
    return result is not None

async def get_all_hidden_items() -> List[Dict]:
    conn = await get_db()
    rows = await conn.fetch('SELECT id, search_type, value, added_by, added_at FROM hidden_items ORDER BY search_type, value')
    await conn.close()
    return [{"id": r[0], "type": r[1], "value": r[2], "added_by": r[3], "added_at": r[4]} for r in rows]


# ============= API ЗАПРОСЫ =============
async def deepscan_search(search_value: str) -> dict:
    url = "https://deepscan.cc/api/v1/search"
    payload = {"token": DEEPSCAN_TOKEN, "search": search_value}
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                return {"ok": False, "error": f"HTTP {resp.status}"}

async def cerera_search(search_type: str, query: str) -> dict:
    if not CERERA_TOKEN:
        return {"status": "error", "error": "Cerera token not configured"}
    url = "https://cerera.cc/api"
    payload = {"token": CERERA_TOKEN, "type": search_type, "q": query}
    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                return {"status": "error", "error": f"HTTP {resp.status}"}

def parse_deepscan_response(data: dict) -> dict:
    if not data.get("ok"):
        return {"error": data.get("error", "Ошибка API")}
    result = {"found": True}
    fast = data.get("fast-result", {})
    if fast:
        if fast.get("fullname"):
            result["full_names"] = [n for n in fast.get("fullname", []) if isinstance(n, str) and len(n) > 2 and n not in ["2"]]
        if fast.get("birthday"):
            result["birth_dates"] = [b for b in fast.get("birthday", []) if isinstance(b, str) and len(b) >= 8]
        if fast.get("email"):
            result["emails"] = [e for e in fast.get("email", []) if isinstance(e, str) and "@" in e]
        if fast.get("ip"):
            result["ips"] = [i for i in fast.get("ip", []) if isinstance(i, str) and "." in i]
        if fast.get("region"):
            result["region"] = fast.get("region", [])[0] if fast.get("region") else None
    full = data.get("full-result", [])
    if full:
        snils_list, inn_list, passport_list = [], [], []
        all_names = result.get("full_names", [])
        all_birthdates = result.get("birth_dates", [])
        all_emails = result.get("emails", [])
        for item in full:
            if item.get("fio") and item["fio"] not in all_names:
                all_names.append(item["fio"])
            if item.get("full_name") and item["full_name"] not in all_names:
                all_names.append(item["full_name"])
            if item.get("snils") and item["snils"] not in snils_list:
                snils_list.append(item["snils"])
            if item.get("СНИЛС") and item["СНИЛС"] not in snils_list:
                snils_list.append(item["СНИЛС"])
            if item.get("inn") and item["inn"] not in inn_list:
                inn_list.append(item["inn"])
            if item.get("passport") and item["passport"] not in passport_list:
                passport_list.append(item["passport"])
            if item.get("email") and item["email"] not in all_emails:
                all_emails.append(item["email"])
        result["full_names"] = all_names
        result["birth_dates"] = all_birthdates
        result["emails"] = all_emails
        result["snils"] = snils_list
        result["inn"] = inn_list
        result["passports"] = passport_list
    additional = data.get("additional-result", {})
    if additional:
        phone_info = additional.get("phone_info", {})
        if phone_info.get("operator"):
            result["operator"] = phone_info.get("operator")
        if phone_info.get("region"):
            result["region"] = result.get("region") or phone_info.get("region")
        modules = additional.get("modules", {})
        vk = modules.get("vk", {})
        if vk and vk.get("id"):
            result["vk_profile"] = {"id": vk.get("id"), "name": f"{vk.get('first_name', '')} {vk.get('last_name', '')}".strip(), "url": vk.get("profile"), "photo": vk.get("photo"), "birthday": vk.get("birthday")}
        yoomoney = modules.get("yoomoney", {})
        if yoomoney:
            result["yoomoney"] = yoomoney
    result["possible_names"] = data.get("possible-names", [])
    result["registers"] = data.get("registers", [])
    result["links"] = data.get("links", [])
    return result

def parse_cerera_response(data: dict) -> dict:
    if data.get("status") != "success":
        return {"error": data.get("error", "Ошибка Cerera")}
    result = {"found": True}
    cerera_data = data.get("data", {})
    if cerera_data.get("fios"):
        result["full_names"] = [f["value"] for f in cerera_data["fios"] if f.get("value")]
    if cerera_data.get("phones"):
        result["phones"] = [p["value"] for p in cerera_data["phones"] if p.get("value")]
        if cerera_data["phones"] and cerera_data["phones"][0].get("operator"):
            result["operator"] = cerera_data["phones"][0]["operator"]
    if cerera_data.get("emails"):
        result["emails"] = [e["value"] for e in cerera_data["emails"] if e.get("value")]
    if cerera_data.get("addresses"):
        result["addresses"] = [a["value"] for a in cerera_data["addresses"] if a.get("value")]
    if cerera_data.get("passports"):
        result["passports"] = [p["value"] for p in cerera_data["passports"] if p.get("value")]
    if cerera_data.get("inn"):
        result["inn"] = [i["value"] for i in cerera_data["inn"] if i.get("value")]
    if cerera_data.get("snils"):
        result["snils"] = [s["value"] for s in cerera_data["snils"] if s.get("value")]
    if cerera_data.get("telegram"):
        result["telegram"] = cerera_data["telegram"]
    if cerera_data.get("vk"):
        result["vk"] = cerera_data["vk"]
    return result


# ============= VK API =============
async def vk_api_lookup(username: str) -> Dict:
    if await is_username_shadowed(username, "vk"):
        return {"error": "shadowed"}
    clean_username = username.strip().lstrip('@')
    url = "https://api.vk.com/method/users.get"
    params = {"user_ids": clean_username, "access_token": VK_ACCESS_TOKEN, "v": "5.131", "fields": "photo_max,sex,bdate,city,country,status,last_seen,online,followers_count,counters,domain"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                if "response" in data and data["response"]:
                    user = data["response"][0]
                    return {"found": True, "id": user.get("id"), "first_name": user.get("first_name"), "last_name": user.get("last_name"), "screen_name": user.get("screen_name") or user.get("domain"), "photo": user.get("photo_max"), "sex": "Женский" if user.get("sex") == 1 else "Мужской" if user.get("sex") == 2 else "Не указан", "bdate": user.get("bdate"), "city": user.get("city", {}).get("title") if user.get("city") else None, "country": user.get("country", {}).get("title") if user.get("country") else None, "status": user.get("status"), "online": "Да" if user.get("online") else "Нет", "last_seen": user.get("last_seen", {}).get("time") if user.get("last_seen") else None, "followers": user.get("followers_count", 0), "profile_url": f"https://vk.com/{user.get('screen_name') or user.get('domain') or user.get('id')}"}
                else:
                    return {"found": False, "error": data.get("error", {}).get("error_msg", "Не найдено")}
            else:
                return {"found": False, "error": f"HTTP {resp.status}"}


async def telegram_lookup(username: str) -> Dict:
    if await is_username_shadowed(username, "tg"):
        return {"error": "shadowed"}
    return {"username": username, "found": False, "url": f"https://t.me/{username.lstrip('@')}"}


# ============= ОСНОВНЫЕ ФУНКЦИИ ПОИСКА =============
async def phone_lookup(phone: str) -> Dict:
    clean = re.sub(r'[^\d]', '', phone)
    if await is_hidden("phone", clean):
        return {"error": "hidden"}
    if clean.startswith('8'):
        clean = '7' + clean[1:]
    if len(clean) == 10:
        clean = '7' + clean
    ds_response = await deepscan_search(clean)
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("phone", clean)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"number": phone, "deepscan": ds_data, "cerera": cr_data}

async def email_lookup(email: str) -> Dict:
    if await is_hidden("email", email):
        return {"error": "hidden"}
    ds_response = await deepscan_search(email)
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("email", email)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"email": email, "deepscan": ds_data, "cerera": cr_data}

async def fio_lookup(full_name: str) -> Dict:
    normalized = full_name.lower().strip()
    if await is_hidden("fio", normalized):
        return {"error": "hidden"}
    ds_response = await deepscan_search(full_name.strip())
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("fio", full_name.strip())
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"full_name": full_name, "deepscan": ds_data, "cerera": cr_data}

async def inn_lookup(inn: str) -> Dict:
    clean = re.sub(r'[^\d]', '', inn)
    if await is_hidden("inn", clean):
        return {"error": "hidden"}
    ds_response = await deepscan_search(f"inn:{clean}")
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("inn", clean)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"inn": inn, "deepscan": ds_data, "cerera": cr_data}

async def snils_lookup(snils: str) -> Dict:
    clean = re.sub(r'[^\d]', '', snils)
    if await is_hidden("snils", clean):
        return {"error": "hidden"}
    ds_response = await deepscan_search(f"snils:{clean}")
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("snils", clean)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"snils": snils, "deepscan": ds_data, "cerera": cr_data}

async def passport_lookup(passport: str) -> Dict:
    clean = re.sub(r'[^\d]', '', passport)
    if await is_hidden("passport", clean):
        return {"error": "hidden"}
    ds_response = await deepscan_search(f"passport:{clean}")
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("passport", clean)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"passport": passport, "deepscan": ds_data, "cerera": cr_data}

async def ip_lookup(ip: str) -> Dict:
    if await is_hidden("ip", ip):
        return {"error": "hidden"}
    ds_response = await deepscan_search(ip)
    ds_data = parse_deepscan_response(ds_response) if ds_response.get("ok") else {"found": False}
    cr_response = await cerera_search("ip", ip)
    cr_data = parse_cerera_response(cr_response) if cr_response.get("status") == "success" else {"found": False}
    return {"ip": ip, "deepscan": ds_data, "cerera": cr_data}

async def username_lookup(username: str, platform: str) -> Dict:
    normalized = username.lower().strip()
    if await is_hidden(f"{platform}_username", normalized):
        return {"error": "hidden"}
    if platform == "vk":
        return await vk_api_lookup(username)
    else:
        return await telegram_lookup(username)


# ============= ФОРМИРОВАНИЕ ОТЧЕТА =============
def format_list(items, prefix=""):
    if not items:
        return ""
    return "\n".join([f"{prefix} • {item}" for item in items if item])

def build_report_from_sources(data: Dict, search_type: str, query: str) -> str:
    if data.get("error") == "hidden":
        return "❌ *Информация по данному запросу не найдена*"
    
    lines = []
    lines.append("=" * 60)
    lines.append(f"📋 ОТЧЕТ ПО ЗАПРОСУ: {query}")
    lines.append(f"📂 Тип: {search_type}")
    lines.append(f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    
    if search_type == "phone":
        lines.append("📞 ИНФОРМАЦИЯ О НОМЕРЕ")
        lines.append("-" * 40)
        lines.append(f"Номер: {data.get('number', 'Не указан')}")
        lines.append("")
    
    ds = data.get("deepscan", {})
    if ds and ds.get("found") and not ds.get("error"):
        lines.append("🔍 ИСТОЧНИК #1")
        lines.append("-" * 40)
        if ds.get("full_names"):
            lines.append("👤 ФИО:")
            for name in ds["full_names"]:
                lines.append(f"   • {name}")
        if ds.get("birth_dates"):
            lines.append("🎂 ДАТЫ РОЖДЕНИЯ:")
            for bd in ds["birth_dates"]:
                lines.append(f"   • {bd}")
        if ds.get("emails"):
            lines.append("📧 EMAIL:")
            for email in ds["emails"]:
                lines.append(f"   • {email}")
        if ds.get("snils"):
            lines.append("📄 СНИЛС:")
            for snils in ds["snils"]:
                lines.append(f"   • {snils}")
        if ds.get("inn"):
            lines.append("🆔 ИНН:")
            for inn in ds["inn"]:
                lines.append(f"   • {inn}")
        if ds.get("passports"):
            lines.append("🪪 ПАСПОРТА:")
            for passport in ds["passports"]:
                lines.append(f"   • {passport}")
        if ds.get("ips"):
            lines.append("🌐 IP АДРЕСА:")
            for ip in ds["ips"]:
                lines.append(f"   • {ip}")
        if ds.get("address"):
            lines.append(f"📍 АДРЕС: {ds['address']}")
        if ds.get("operator"):
            lines.append(f"📡 ОПЕРАТОР: {ds['operator']}")
        if ds.get("region"):
            lines.append(f"🗺️ РЕГИОН: {ds['region']}")
        if ds.get("vk_profile"):
            lines.append("🎯 VK ПРОФИЛЬ:")
            vk = ds["vk_profile"]
            if vk.get("name"):
                lines.append(f"   • Имя: {vk['name']}")
            if vk.get("id"):
                lines.append(f"   • ID: {vk['id']}")
            if vk.get("url"):
                lines.append(f"   • Ссылка: {vk['url']}")
        if ds.get("links"):
            lines.append("🔗 ССЫЛКИ:")
            for link in ds["links"]:
                lines.append(f"   • {link}")
        lines.append("")
    
    cr = data.get("cerera", {})
    if cr and cr.get("found") and not cr.get("error"):
        lines.append("🔍 ИСТОЧНИК #2")
        lines.append("-" * 40)
        if cr.get("full_names"):
            lines.append("👤 ФИО:")
            for name in cr["full_names"]:
                lines.append(f"   • {name}")
        if cr.get("emails"):
            lines.append("📧 EMAIL:")
            for email in cr["emails"]:
                lines.append(f"   • {email}")
        if cr.get("phones"):
            lines.append("📞 ТЕЛЕФОНЫ:")
            for phone in cr["phones"]:
                lines.append(f"   • {phone}")
        if cr.get("addresses"):
            lines.append("📍 АДРЕСА:")
            for addr in cr["addresses"]:
                lines.append(f"   • {addr}")
        if cr.get("passports"):
            lines.append("🪪 ПАСПОРТА:")
            for passport in cr["passports"]:
                lines.append(f"   • {passport}")
        if cr.get("inn"):
            lines.append("🆔 ИНН:")
            for inn in cr["inn"]:
                lines.append(f"   • {inn}")
        if cr.get("snils"):
            lines.append("📄 СНИЛС:")
            for snils in cr["snils"]:
                lines.append(f"   • {snils}")
        if cr.get("telegram"):
            lines.append(f"✈️ TELEGRAM: {cr['telegram']}")
        if cr.get("vk"):
            lines.append(f"🎯 VK: {cr['vk']}")
        if cr.get("operator"):
            lines.append(f"📡 ОПЕРАТОР: {cr['operator']}")
        lines.append("")
    
    if not ds.get("found") and not cr.get("found"):
        lines.append("❌ ИНФОРМАЦИЯ НЕ НАЙДЕНА")
        lines.append("")
    
    lines.append("=" * 60)
    lines.append("HITSearch Bot")
    return "\n".join(lines)


# ============= ОБРАБОТЧИКИ TELEGRAM =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_user_registered(user_id):
        await register_user(user_id)
    if context.args and context.args[0].startswith('ref_'):
        try:
            referrer_id = int(context.args[0].replace('ref_', ''))
            if await add_referral(user_id, referrer_id):
                await update.message.reply_text(
                    f"🎉 *Вы пришли по реферальной ссылке!*\n\n"
                    f"Вам начислен `{REFERRAL_BONUS}` дополнительный запрос!\n"
                    f"Приятного использования! 🚀",
                    parse_mode="Markdown"
                )
        except:
            pass
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск", callback_data="search_menu")],
        [InlineKeyboardButton("❓ Как пользоваться?", callback_data="howto")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton("📞 Поддержка", callback_data="support")],
    ]
    if user_id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    await update.message.reply_text(
        "🔍 *HITSearch*\n\n"
        "Мощный бот для поиска информации\n"
        "📁 *Отчеты приходят в TXT файле*\n"
        "📡 *Источники: DeepScan + Cerera*\n"
        f"📊 *Доступно запросов:* {await get_searches(user_id)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск", callback_data="search_menu")],
        [InlineKeyboardButton("❓ Как пользоваться?", callback_data="howto")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton("📞 Поддержка", callback_data="support")],
    ]
    if user_id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    await update.callback_query.edit_message_text(
        f"🔍 *HITSearch*\n\n"
        f"📊 *Доступно запросов:* {await get_searches(user_id)}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def search_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📞 Номер телефона", callback_data="type_phone")],
        [InlineKeyboardButton("📧 Email", callback_data="type_email")],
        [InlineKeyboardButton("👥 ФИО", callback_data="type_fio")],
        [InlineKeyboardButton("🆔 ИНН / СНИЛС / Паспорт", callback_data="type_doc")],
        [InlineKeyboardButton("🌐 IP-адрес", callback_data="type_ip")],
        [InlineKeyboardButton("👤 Username", callback_data="type_username")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ]
    await update.callback_query.edit_message_text("🔍 *Выберите тип поиска:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def search_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    search_type = query.data.replace("type_", "")
    context.user_data['search_type'] = search_type
    prompts = {
        "phone": "📞 Отправьте номер телефона:\n• `79001234567`",
        "email": "📧 Отправьте email:\n• `user@example.com`",
        "fio": "👥 Отправьте ФИО:\n• `Иванов Иван Иванович`",
        "doc": "🆔 Отправьте:\n• ИНН: `123456789012`\n• СНИЛС: `12345678901`\n• Паспорт: `1234567890`",
        "ip": "🌐 Отправьте IP-адрес:\n• `8.8.8.8`",
        "username": "👤 Отправьте username:\n• `@username`"
    }
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="search_menu")]]
    await query.edit_message_text(
        f"{prompts.get(search_type, 'Отправьте данные')}\n\nℹ️ *Результат придет в TXT файле*\n\n⚠️ *За этот поиск спишется 1 запрос*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def howto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("📚 *Как пользоваться*\n\n"
            "1. Нажмите «Поиск»\n"
            "2. Выберите тип данных\n"
            "3. Отправьте данные\n\n"
            "📋 *Примеры:*\n"
            "• 📞 `79001234567`\n"
            "• 📧 `user@example.com`\n"
            "• 👥 `Иванов Иван Иванович`\n"
            "• 🆔 ИНН: `123456789012`\n"
            "• 🆔 СНИЛС: `12345678901`\n"
            "• 🆔 Паспорт: `1234567890`\n\n"
            "📊 *При регистрации выдаётся 2 запроса*\n"
            "➕ *За реферала +1 запрос*\n\n"
            "📡 *Источники: DeepScan + Cerera*\n"
            "📁 *Отчет приходит в TXT файле*")
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="main_menu")]]
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    stats = await get_user_stats(user_id)
    total = await get_searches(user_id)
    referrals = await get_referrals_count(user_id)
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="profile_stats")],
        [InlineKeyboardButton("📋 Рефералы", callback_data="profile_referrals")],
        [InlineKeyboardButton("🔗 Реферальная ссылка", callback_data="profile_referral_link")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ]
    text = (f"👤 *Мой профиль*\n\n"
            f"• ID: `{user.id}`\n"
            f"• Имя: {user.first_name or '-'}\n\n"
            f"📊 *Доступно запросов:* `{total}`\n"
            f"👥 *Рефералов:* `{referrals}`")
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def profile_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = await get_user_stats(user_id)
    text = (f"📊 *Моя статистика*\n\n"
            f"📞 Телефоны: {stats['phone']}\n"
            f"📧 Email: {stats['email']}\n"
            f"👥 ФИО: {stats['fio']}\n"
            f"🎯 VK/Telegram: {stats['vk'] + stats['tg']}\n"
            f"🆔 Документы: {stats['inn'] + stats['snils'] + stats['passport']}\n"
            f"📈 Всего запросов: {stats['total']}")
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="profile")]]))

async def profile_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrals = await get_referrals_count(user_id)
    await update.callback_query.edit_message_text(
        f"👥 *Мои рефералы*\n\n"
        f"Всего рефералов: `{referrals}`\n\n"
        f"💡 За каждого реферала вы получаете `{REFERRAL_BONUS}` запрос",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="profile")]])
    )

async def profile_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    link = await get_referral_link(user_id)
    await update.callback_query.edit_message_text(
        f"🔗 *Ваша реферальная ссылка*\n\n"
        f"`{link}`\n\n"
        f"📋 Отправьте эту ссылку друзьям!\n"
        f"За каждого пришедшего вы получите `{REFERRAL_BONUS}` запрос",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="profile")]])
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("📞 *Поддержка HITSearch*\n\n"
            "Если у вас возникли вопросы или проблемы:\n\n"
            "👨‍💻 Разработчик: @zghit\n"
            "🔄 Переходник: @vxhit\n\n"
            "📚 *Частые вопросы:*\n"
            "• Как пригласить друзей? → Меню «Мой профиль» → Рефералы\n\n"
            "✉️ Для быстрой связи пишите @zghit")
    keyboard = [
        [InlineKeyboardButton("👨‍💻 Связаться с разработчиком", url="https://t.me/zghit")],
        [InlineKeyboardButton("🔄 Переходник", url="https://t.me/vxhit")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ]
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ============= АДМИН ПАНЕЛЬ =============
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    keyboard = [
        [InlineKeyboardButton("🌑 Управление скрытием", callback_data="admin_hide_menu")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("◀️ Назад", callback_data="main_menu")],
    ]
    await update.callback_query.edit_message_text("👑 *Админ панель*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_hide_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    keyboard = [
        [InlineKeyboardButton("📞 Скрыть номер телефона", callback_data="admin_hide_phone")],
        [InlineKeyboardButton("📧 Скрыть Email", callback_data="admin_hide_email")],
        [InlineKeyboardButton("👥 Скрыть ФИО", callback_data="admin_hide_fio")],
        [InlineKeyboardButton("🆔 Скрыть ИНН", callback_data="admin_hide_inn")],
        [InlineKeyboardButton("📄 Скрыть СНИЛС", callback_data="admin_hide_snils")],
        [InlineKeyboardButton("🪪 Скрыть Паспорт", callback_data="admin_hide_passport")],
        [InlineKeyboardButton("🌐 Скрыть IP", callback_data="admin_hide_ip")],
        [InlineKeyboardButton("🎯 Скрыть VK Username", callback_data="admin_hide_vk")],
        [InlineKeyboardButton("✈️ Скрыть Telegram Username", callback_data="admin_hide_tg")],
        [InlineKeyboardButton("📋 Список скрытых", callback_data="admin_hide_list")],
        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="admin_hide_clear")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
    ]
    await update.callback_query.edit_message_text(
        "🌑 *Управление скрытием*\n\n"
        "Выберите тип данных для скрытия.\n"
        "После выбора введите значение для скрытия.\n\n"
        "Примеры:\n"
        "• Телефон: `79001234567`\n"
        "• ФИО: `Иванов Иван`\n"
        "• Email: `user@mail.ru`\n"
        "• IP: `8.8.8.8`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_hide_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    query = update.callback_query
    search_type = query.data.replace("admin_hide_", "")
    context.user_data['hide_type'] = search_type
    prompts = {
        "phone": "📞 Введите номер телефона для скрытия:\nНапример: `79001234567`",
        "email": "📧 Введите Email для скрытия:\nНапример: `user@example.com`",
        "fio": "👥 Введите ФИО для скрытия:\nНапример: `Иванов Иван Иванович`",
        "inn": "🆔 Введите ИНН для скрытия:\nНапример: `123456789012`",
        "snils": "📄 Введите СНИЛС для скрытия:\nНапример: `12345678901`",
        "passport": "🪪 Введите паспорт для скрытия:\nНапример: `1234567890`",
        "ip": "🌐 Введите IP-адрес для скрытия:\nНапример: `8.8.8.8`",
        "vk": "🎯 Введите VK username для скрытия:\nНапример: `durov` (без @)",
        "tg": "✈️ Введите Telegram username для скрытия:\nНапример: `durov` (без @)"
    }
    await query.edit_message_text(
        f"{prompts.get(search_type, 'Введите значение для скрытия')}\n\n"
        f"✏️ Отправьте значение в следующем сообщении.\n"
        f"Для отмены нажмите «Назад».",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_hide_menu")]])
    )

async def admin_hide_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Нет прав.")
        return
    hide_type = context.user_data.get('hide_type')
    if not hide_type:
        await update.message.reply_text("❌ Сначала выберите тип в меню.")
        return
    value = update.message.text.strip()
    if not value:
        await update.message.reply_text("❌ Введите значение.")
        return
    normalized_value = value
    if hide_type == "phone":
        normalized_value = re.sub(r'[^\d]', '', value)
    elif hide_type in ["vk", "tg"]:
        normalized_value = value.lower().lstrip('@')
    elif hide_type in ["inn", "snils", "passport"]:
        normalized_value = re.sub(r'[^\d]', '', value)
    if await add_hidden_item(hide_type, normalized_value, update.effective_user.id):
        await update.message.reply_text(
            f"✅ *Скрыто успешно!*\n\n"
            f"Тип: `{hide_type}`\n"
            f"Значение: `{value}`\n\n"
            f"Теперь при поиске этого значения будет выдаваться:\n"
            f"«Информация по данному запросу не найдена»",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"❌ *Не удалось скрыть.*\n\n"
            f"Возможно, это значение уже скрыто.",
            parse_mode="Markdown"
        )
    context.user_data['hide_type'] = None

async def admin_hide_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    items = await get_all_hidden_items()
    if not items:
        await update.callback_query.edit_message_text("📋 *Список скрытых*\n\nНет скрытых записей.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_hide_menu")]]))
        return
    text = "📋 *Список скрытых записей*\n\n"
    for item in items:
        text += f"• `{item['type']}` → `{item['value']}`\n"
    text += "\nДля удаления используйте команду:\n`/unhide тип значение`\n\n"
    text += "Пример: `/unhide phone 79001234567`"
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_hide_menu")]]))

async def admin_hide_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    conn = await get_db()
    await conn.execute('DELETE FROM hidden_items')
    await conn.close()
    await update.callback_query.edit_message_text("🗑️ *Все скрытые записи удалены!*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_hide_menu")]]))

async def admin_hide_unhide_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Нет прав.")
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "📋 `/unhide тип значение`\n\n"
            "Примеры:\n"
            "`/unhide phone 79001234567`\n"
            "`/unhide fio Иванов Иван`\n"
            "`/unhide email user@mail.ru`\n\n"
            "Доступные типы: phone, email, fio, inn, snils, passport, ip, vk, tg",
            parse_mode="Markdown"
        )
        return
    hide_type = context.args[0].lower()
    value = ' '.join(context.args[1:])
    if hide_type not in ["phone", "email", "fio", "inn", "snils", "passport", "ip", "vk", "tg"]:
        await update.message.reply_text("❌ Неверный тип. Доступные: phone, email, fio, inn, snils, passport, ip, vk, tg")
        return
    if hide_type == "phone":
        value = re.sub(r'[^\d]', '', value)
    elif hide_type in ["vk", "tg"]:
        value = value.lower().lstrip('@')
    elif hide_type in ["inn", "snils", "passport"]:
        value = re.sub(r'[^\d]', '', value)
    if await remove_hidden_item(hide_type, value):
        await update.message.reply_text(f"✅ *Скрытие удалено!*\n\nТип: `{hide_type}`\nЗначение: `{value}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Запись не найдена.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        return
    conn = await get_db()
    users_count = await conn.fetchval('SELECT COUNT(*) FROM users') or 0
    total_searches = await conn.fetchval('SELECT SUM(searches) FROM users') or 0
    total_referrals = await conn.fetchval('SELECT SUM(referrals_count) FROM referrals') or 0
    hidden_count = await conn.fetchval('SELECT COUNT(*) FROM hidden_items') or 0
    await conn.close()
    await update.callback_query.edit_message_text(
        f"📊 *Статистика*\n\n"
        f"👥 Пользователей: `{users_count}`\n"
        f"🔍 Всего запросов: `{total_searches}`\n"
        f"👥 Всего рефералов: `{total_referrals}`\n"
        f"🌑 Скрытых записей: `{hidden_count}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]])
    )


# ============= ОСТАЛЬНЫЕ КОМАНДЫ =============
async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = await get_user_stats(user_id)
    total = await get_searches(user_id)
    await update.message.reply_text(
        f"📊 *Статистика*\n\n"
        f"🔍 Доступно запросов: `{total}`\n"
        f"📞 Телефоны: {stats['phone']}\n"
        f"📧 Email: {stats['email']}\n"
        f"👥 ФИО: {stats['fio']}\n"
        f"🆔 Документы: {stats['inn'] + stats['snils'] + stats['passport']}\n"
        f"🎯 VK/Telegram: {stats['vk'] + stats['tg']}",
        parse_mode="Markdown"
    )


# ============= ОСНОВНОЙ ОБРАБОТЧИК =============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get('hide_type'):
        await admin_hide_input(update, context)
        return
    if not await can_search(user_id):
        await update.message.reply_text(
            f"❌ *Недостаточно запросов!*\n\n"
            f"💡 При регистрации выдаётся 2 запроса.\n"
            f"➕ За каждого реферала +1 запрос.",
            parse_mode="Markdown"
        )
        return
    query = update.message.text.strip()
    if not query:
        return
    search_type = context.user_data.get('search_type')
    
    if search_type == "doc":
        context.user_data['search_type'] = None
        if re.match(r'^\d{10}$|^\d{12}$', query):
            result = await inn_lookup(query)
            report = build_report_from_sources(result, "inn", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif re.match(r'^\d{11}$', query):
            result = await snils_lookup(query)
            report = build_report_from_sources(result, "snils", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif re.match(r'^\d{10}$|^\d{11}$', query):
            result = await passport_lookup(query)
            report = build_report_from_sources(result, "passport", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        else:
            await update.message.reply_text("❌ Не удалось определить тип.\n\nИНН: 10-12 цифр\nСНИЛС: 11 цифр\nПаспорт: 10-11 цифр", parse_mode="Markdown")
            return
        await update.message.delete()
        txt_file = BytesIO(report.encode('utf-8'))
        txt_file.seek(0)
        await update.message.reply_document(document=txt_file, filename=f"report_{search_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        await update.message.reply_text("✅ *Готово!*", parse_mode="Markdown")
        return
    
    if search_type == "username" or (query.startswith('@') and not search_type):
        if search_type == "username":
            context.user_data['search_type'] = None
        username = query.lstrip('@')
        if re.match(r'^[a-zA-Z0-9_-]+$', username):
            keyboard = [
                [InlineKeyboardButton("🎯 VK", callback_data=f"username_vk_{username}")],
                [InlineKeyboardButton("✈️ Telegram", callback_data=f"username_tg_{username}")],
                [InlineKeyboardButton("◀️ Назад", callback_data="search_menu")],
            ]
            await update.message.reply_text(f"👤 Выберите платформу: @{username}", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Неверный формат username.")
        return
    
    if not search_type:
        if re.match(r'^\d{10}$|^\d{12}$', query):
            search_type = "inn"
        elif re.match(r'^\d{11}$', query):
            search_type = "snils"
        elif re.match(r'^\d{10}$|^\d{11}$', query):
            search_type = "passport"
        elif re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', query):
            search_type = "email"
        elif re.search(r'\d', query) and len(re.sub(r'[^\d]', '', query)) >= 10:
            search_type = "phone"
        elif re.match(r'^[А-ЯЁ][а-яё]+\s[А-ЯЁ][а-яё]+(?:\s[А-ЯЁ][а-яё]+)?$', query):
            search_type = "fio"
        elif query.startswith('@') and re.match(r'^@[a-zA-Z0-9_\-]+$', query):
            search_type = "username"
        else:
            search_type = "unknown"
    else:
        context.user_data['search_type'] = None
    
    status_msg = await update.message.reply_text(f"🔍 Поиск...", parse_mode="Markdown")
    try:
        if search_type == "phone":
            result = await phone_lookup(query)
            report = build_report_from_sources(result, "phone", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "email":
            result = await email_lookup(query)
            report = build_report_from_sources(result, "email", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "fio":
            result = await fio_lookup(query)
            report = build_report_from_sources(result, "fio", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "inn":
            result = await inn_lookup(query)
            report = build_report_from_sources(result, "inn", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "snils":
            result = await snils_lookup(query)
            report = build_report_from_sources(result, "snils", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "passport":
            result = await passport_lookup(query)
            report = build_report_from_sources(result, "passport", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        elif search_type == "ip":
            result = await ip_lookup(query)
            report = build_report_from_sources(result, "ip", query)
            if result.get("deepscan", {}).get("found") or result.get("cerera", {}).get("found"):
                await decrement_search(user_id)
        else:
            await status_msg.edit_text("❓ Не удалось определить тип.\n\nВыберите тип в меню.")
            return
        await update.message.delete()
        await status_msg.delete()
        txt_file = BytesIO(report.encode('utf-8'))
        txt_file.seek(0)
        await update.message.reply_document(document=txt_file, filename=f"report_{search_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        await update.message.reply_text("✅ *Готово!*", parse_mode="Markdown")
        remaining = await get_searches(user_id)
        if remaining < 3:
            await update.message.reply_text(
                f"⚠️ *Осталось запросов:* {remaining}\n"
                f"➕ Пригласите друга по реферальной ссылке и получите +1 запрос!",
                parse_mode="Markdown"
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")


async def username_platform_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split('_')
    platform = parts[1]
    username = '_'.join(parts[2:])
    user_id = update.effective_user.id
    if not await can_search(user_id):
        await query.edit_message_text(
            f"❌ *Недостаточно запросов!*\n\n"
            f"💡 При регистрации выдаётся 2 запроса.\n"
            f"➕ За каждого реферала +1 запрос.",
            parse_mode="Markdown"
        )
        return
    await query.edit_message_text("🔍 Поиск...")
    try:
        if platform == "vk":
            result = await username_lookup(username, "vk")
            if result.get("error") == "hidden":
                report = "❌ *Информация по данному запросу не найдена*"
            else:
                await decrement_search(user_id)
                lines = []
                lines.append("=" * 60)
                lines.append(f"ОТЧЕТ ПО VK: @{username}")
                lines.append(f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
                lines.append("=" * 60)
                lines.append("")
                if result.get("found"):
                    lines.append("🎯 VK ПРОФИЛЬ:")
                    lines.append("-" * 40)
                    lines.append(f"ID: {result.get('id')}")
                    lines.append(f"Имя: {result.get('first_name')} {result.get('last_name')}")
                    lines.append(f"Ссылка: {result.get('profile_url')}")
                    if result.get('screen_name'):
                        lines.append(f"Screen name: {result.get('screen_name')}")
                    if result.get('bdate'):
                        lines.append(f"Дата рождения: {result.get('bdate')}")
                    if result.get('sex') != "Не указан":
                        lines.append(f"Пол: {result.get('sex')}")
                    if result.get('city'):
                        lines.append(f"Город: {result.get('city')}")
                    if result.get('country'):
                        lines.append(f"Страна: {result.get('country')}")
                    if result.get('status'):
                        lines.append(f"Статус: {result.get('status')}")
                    lines.append(f"Online: {result.get('online')}")
                    if result.get('followers'):
                        lines.append(f"Подписчиков: {result.get('followers')}")
                else:
                    lines.append("❌ VK ПРОФИЛЬ НЕ НАЙДЕН")
                lines.append("")
                lines.append("=" * 60)
                lines.append("HITSearch Bot")
                report = "\n".join(lines)
        else:
            result = await username_lookup(username, "tg")
            if result.get("error") == "hidden":
                report = "❌ *Информация по данному запросу не найдена*"
            else:
                await decrement_search(user_id)
                lines = []
                lines.append("=" * 60)
                lines.append(f"ОТЧЕТ ПО TELEGRAM: @{username}")
                lines.append(f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
                lines.append("=" * 60)
                lines.append("")
                lines.append(f"✈️ Telegram: @{username}")
                lines.append(f"Ссылка: https://t.me/{username}")
                lines.append("")
                lines.append("=" * 60)
                lines.append("HITSearch Bot")
                report = "\n".join(lines)
        txt_file = BytesIO(report.encode('utf-8'))
        txt_file.seek(0)
        await query.message.reply_document(document=txt_file, filename=f"report_{platform}_{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        await query.message.reply_text("✅ *Готово!*", parse_mode="Markdown")
        await query.delete_message()
        remaining = await get_searches(user_id)
        if remaining < 3:
            await query.message.reply_text(
                f"⚠️ *Осталось запросов:* {remaining}\n"
                f"➕ Пригласите друга по реферальной ссылке и получите +1 запрос!",
                parse_mode="Markdown"
            )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")


# ============= ЗАПУСК =============
async def main():
    print("=" * 60)
    print("🔍 HITSearch Bot - DEEPSCAN + CERERA")
    print("=" * 60)
    print(f"При регистрации: {INITIAL_SEARCHES} запроса")
    print(f"За реферала: +{REFERRAL_BONUS} запрос")
    print(f"База данных: {'✅' if DATABASE_URL else '❌ не настроена'}")
    print(f"Cerera: {'✅' if CERERA_TOKEN else '❌ не настроен'}")
    print("=" * 60)
    
    # Инициализация БД
    if DATABASE_URL:
        try:
            await init_db()
            print("✅ База данных инициализирована")
        except Exception as e:
            print(f"❌ Ошибка БД: {e}")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mystats", mystats_command))
    app.add_handler(CommandHandler("unhide", admin_hide_unhide_command))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(search_menu, pattern="^search_menu$"))
    app.add_handler(CallbackQueryHandler(search_type_handler, pattern="^type_"))
    app.add_handler(CallbackQueryHandler(howto_handler, pattern="^howto$"))
    app.add_handler(CallbackQueryHandler(profile_handler, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(profile_stats, pattern="^profile_stats$"))
    app.add_handler(CallbackQueryHandler(profile_referrals, pattern="^profile_referrals$"))
    app.add_handler(CallbackQueryHandler(profile_referral_link, pattern="^profile_referral_link$"))
    app.add_handler(CallbackQueryHandler(support, pattern="^support$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_hide_menu, pattern="^admin_hide_menu$"))
    app.add_handler(CallbackQueryHandler(admin_hide_prompt, pattern="^admin_hide_"))
    app.add_handler(CallbackQueryHandler(admin_hide_list, pattern="^admin_hide_list$"))
    app.add_handler(CallbackQueryHandler(admin_hide_clear, pattern="^admin_hide_clear$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(username_platform_callback, pattern="^username_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())