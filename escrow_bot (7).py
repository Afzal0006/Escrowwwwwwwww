import os
import re
import json
from datetime import datetime, timezone
import telebot
from telebot.types import Message

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_IDS_ENV = os.getenv("OWNER_IDS", "")
LOG_CHANNEL = os.getenv("LOG_CHANNEL")
DATA_FILE = "data.json"
FEE_PCT = 3.0

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN not set. Add it to environment/secrets.")

try:
    OWNERS = [int(x.strip()) for x in OWNER_IDS_ENV.split(",") if x.strip()]
except:
    OWNERS = []

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ---------- Persistence ----------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"trades": {}, "admins": [], "next_id": 1}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()
trades = data.get("trades", {})
admins = set(data.get("admins", []))
next_id = int(data.get("next_id", 1))

def persist():
    data["trades"] = trades
    data["admins"] = list(admins)
    data["next_id"] = next_id
    save_data(data)

# ---------- Utilities ----------
def gen_trade_id():
    global next_id
    tid = next_id
    next_id += 1
    persist()
    return tid

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')

def parse_deal_form(text):
    buyer = re.search(r"BUYER\s*[:\-]\s*(?P<b>@[A-Za-z0-9_]+|\w+)", text, re.IGNORECASE)
    seller = re.search(r"SELLER\s*[:\-]\s*(?P<s>@[A-Za-z0-9_]+|\w+)", text, re.IGNORECASE)
    amount = re.search(r"DEAL\s*AMOUNT\s*[:\-]\s*(?P<a>[\d\.,]+)", text, re.IGNORECASE)
    info = re.search(r"DEAL\s*INFO\s*[:\-]\s*(?P<i>.+)", text, re.IGNORECASE)
    ttd = re.search(r"TIME\s*TO\s*DEAL\s*[:\-]\s*(?P<t>.+)", text, re.IGNORECASE)

    res = {}
    res['buyer'] = buyer.group('b').strip() if buyer else None
    res['seller'] = seller.group('s').strip() if seller else None
    if amount:
        a = amount.group('a').replace(',', '').strip()
        try:
            res['amount'] = float(a)
        except:
            res['amount'] = None
    else:
        res['amount'] = None
    res['info'] = info.group('i').strip() if info else ""
    res['time_to_deal'] = ttd.group('t').strip() if ttd else ""
    return res

def is_owner(user_id):
    return user_id in OWNERS

def is_admin(user_id):
    return user_id in admins or is_owner(user_id)

def safe_dm(user, text):
    try:
        if user and user.startswith("@"):  # only username supported
            bot.send_message(user, text)
    except Exception as e:
        print(f"DM failed for {user}: {e}")

# ---------- Commands ----------
@bot.message_handler(commands=['start'])
def cmd_start(m: Message):
    user_id = m.from_user.id
    if not is_admin(user_id):
        return  # hide for non-admins

    txt = (
        "👋 Welcome to Escrow Bot (Admin Panel)!\n\n"
        "Available Commands:\n"
        "• /form → Get deal form template\n"
        "• /add or /add+fee → Register payment\n"
        "• /done or /done+fee → Mark deal completed\n"
        "• /refund or /refund+fee → Refund a deal\n"
        "• /mystats → See your deals\n"
        "• /stats → Group stats\n"
        "• /gstats → Global stats per admin\n\n"
        "Owner only:\n"
        "• /addadmin <user_id>\n"
        "• /removeadmin <user_id>\n"
    )
    bot.reply_to(m, txt)

@bot.message_handler(commands=['form'])
def cmd_form(m: Message):
    template = (
        "BUYER : \n"
        "SELLER : \n"
        "DEAL AMOUNT : \n"
        "DEAL INFO : pvt\n"
        "TIME TO COMPLETE DEAL : "
    )
    bot.reply_to(m, template)

@bot.message_handler(commands=['addadmin'])
def cmd_addadmin(m: Message):
    user_id = m.from_user.id
    if not is_owner(user_id):
        bot.reply_to(m, "⛔ Only Owners can add admins.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Usage: /addadmin <user_id>")
        return
    try:
        uid = int(args[1])
    except:
        bot.reply_to(m, "Invalid user id.")
        return
    admins.add(uid)
    persist()
    bot.reply_to(m, f"✅ Added admin: {uid}")

@bot.message_handler(commands=['removeadmin'])
def cmd_removeadmin(m: Message):
    user_id = m.from_user.id
    if not is_owner(user_id):
        bot.reply_to(m, "⛔ Only Owners can remove admins.")
        return
    args = m.text.split()
    if len(args) < 2:
        bot.reply_to(m, "Usage: /removeadmin <user_id>")
        return
    try:
        uid = int(args[1])
    except:
        bot.reply_to(m, "Invalid user id.")
        return
    if uid in admins:
        admins.remove(uid)
        persist()
        bot.reply_to(m, f"❌ Removed admin: {uid}")
    else:
        bot.reply_to(m, "⚠️ This user is not an admin.")

@bot.message_handler(commands=['add', 'add+fee', 'refund+fee'])
def cmd_add(m: Message):
    user_id = m.from_user.id
    if not is_admin(user_id):
        bot.reply_to(m, "⛔ Only Admins can add or refund deals.")
        return

    if m.text.strip().lower() == '/refund+fee':
        return cmd_refund(m)

    if not m.reply_to_message or not m.reply_to_message.text:
        bot.reply_to(m, "⚠️ Please reply to the deal form message with /add or /add+fee")
        return

    form = parse_deal_form(m.reply_to_message.text)
    if not form['buyer'] or not form['seller'] or form['amount'] is None:
        bot.reply_to(m, "❌ Could not extract Buyer/Seller/Amount from the form. Make sure it's formatted.")
        return

    use_fee = m.text.strip().lower().endswith('+fee')
    fee_amt = round(form['amount'] * (FEE_PCT/100.0), 2) if use_fee else 0.0
    total = round(form['amount'] + fee_amt, 2)

    tid = gen_trade_id()
    trade = {
        "id": tid,
        "buyer": form['buyer'],
        "seller": form['seller'],
        "amount": form['amount'],
        "fee": fee_amt,
        "total": total,
        "status": "open",
        "admin": user_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "chat_id": m.chat.id,
        "origin_message_id": m.reply_to_message.message_id
    }
    trades[str(tid)] = trade
    persist()

    text = (
        f"✅ PAYMENT RECEIVED\n"
        f"────────────────\n"
        f"👤 Buyer  : {trade['buyer']}\n"
        f"👤 Seller : {trade['seller']}\n"
        f"💸 Received : {trade['amount']}\n"
        f"🆔 Trade ID : #{trade['id']}\n"
        f"💰 Fee     : {trade['fee']}\n"
        f"🧾 TOTAL   : {trade['total']}\n"
        f"CONTINUE DEAL ❤️\n"
        f"────────────────"
    )
    bot.send_message(m.chat.id, text)

    # DM buyer & seller
    dm_text = (
        f"📜 New Escrow Deal Created!\n"
        f"Buyer  : {trade['buyer']}\n"
        f"Seller : {trade['seller']}\n"
        f"Amount : {trade['amount']}\n"
        f"Trade ID : #{trade['id']}\n"
        f"Status : OPEN"
    )
    safe_dm(trade['buyer'], dm_text)
    safe_dm(trade['seller'], dm_text)

@bot.message_handler(commands=['done', 'done+fee'])
def cmd_done(m: Message):
    user_id = m.from_user.id
    if not is_admin(user_id):
        bot.reply_to(m, "⛔ Only Admins can complete deals.")
        return
    if not m.reply_to_message:
        bot.reply_to(m, "⚠️ Reply to the PAYMENT RECEIVED message (the bot's message with the Trade ID).")
        return

    text = m.reply_to_message.text or ""
    match = re.search(r"#(\d+)", text)
    if not match:
        bot.reply_to(m, "❌ Could not find Trade ID (#number) in the replied message.")
        return
    tid_str = match.group(1)
    if tid_str not in trades:
        bot.reply_to(m, "❌ Trade not found.")
        return

    trade = trades[tid_str]
    use_fee = m.text.strip().lower().endswith('+fee')
    if use_fee and trade.get('fee', 0) == 0:
        trade['fee'] = round(trade['amount'] * (FEE_PCT/100.0), 2)
        trade['total'] = round(trade['amount'] + trade['fee'], 2)
    trade['status'] = 'completed'
    trade['updated_at'] = now_iso()
    trade['completed_by'] = user_id
    persist()

    out = (
        f"✅ DEAL COMPLETED\n"
        f"────────────────\n"
        f"👤 Buyer  : {trade['buyer']}\n"
        f"👤 Seller : {trade['seller']}\n"
        f"💰 Amount : {trade['amount']}\n"
        f"🆔 Trade ID : #{trade['id']}\n"
        f"💰 Fee     : {trade['fee']}\n"
        f"🧾 TOTAL   : {trade['total']}\n"
        f"────────────────\n"
        f"🛡️ Escrowed by @{m.from_user.username or m.from_user.id}"
    )
    bot.send_message(m.chat.id, out)

    dm_text = (
        f"✅ Deal Completed!\n"
        f"Trade #{trade['id']}\n"
        f"Buyer : {trade['buyer']}\n"
        f"Seller: {trade['seller']}\n"
        f"Amount: {trade['amount']}"
    )
    safe_dm(trade['buyer'], dm_text)
    safe_dm(trade['seller'], dm_text)

@bot.message_handler(commands=['refund', 'refund+fee'])
def cmd_refund(m: Message):
    user_id = m.from_user.id
    if not is_admin(user_id):
        bot.reply_to(m, "⛔ Only Admins can refund deals.")
        return
    if not m.reply_to_message:
        bot.reply_to(m, "⚠️ Reply to the PAYMENT/TRADE message with /refund")
        return
    text = m.reply_to_message.text or ""
    match = re.search(r"#(\d+)", text)
    if not match:
        bot.reply_to(m, "❌ Could not find Trade ID (#number) in the replied message.")
        return
    tid_str = match.group(1)
    if tid_str not in trades:
        bot.reply_to(m, "❌ Trade not found.")
        return

    trade = trades[tid_str]
    use_fee = m.text.strip().lower().endswith('+fee')
    if use_fee and trade.get('fee', 0) == 0:
        trade['fee'] = round(trade['amount'] * (FEE_PCT/100.0), 2)
        trade['total'] = round(trade['amount'] + trade['fee'], 2)

    trade['status'] = 'refunded'
    trade['updated_at'] = now_iso()
    trade['refunded_by'] = user_id
    persist()

    out = (
        f"❌ REFUND COMPLETED\n"
        f"────────────────\n"
        f"👤 Buyer  : {trade['buyer']}\n"
        f"👤 Seller : {trade['seller']}\n"
        f"💰 Refund : {trade['amount']}\n"
        f"🆔 Trade ID : #{trade['id']}\n"
        f"💰 Fee     : {trade['fee']}\n"
        f"────────────────\n"
        f"🛡️ Escrowed by @{m.from_user.username or m.from_user.id}"
    )
    bot.send_message(m.chat.id, out)

    dm_text = (
        f"❌ Deal Refunded!\n"
        f"Trade #{trade['id']}\n"
        f"Buyer : {trade['buyer']}\n"
        f"Seller: {trade['seller']}\n"
        f"Amount: {trade['amount']}"
    )
    safe_dm(trade['buyer'], dm_text)
    safe_dm(trade['seller'], dm_text)

@bot.message_handler(commands=['stats'])
def cmd_stats(m: Message):
    chat_id = m.chat.id
    total = completed = refunded = 0
    volume = 0.0
    for t in trades.values():
        if t.get('chat_id') == chat_id:
            total += 1
            volume += float(t.get('amount',0) or 0)
            if t.get('status') == 'completed': completed += 1
            if t.get('status') == 'refunded': refunded += 1
    txt = (
        f"📊 Group Stats\n"
        f"Total Trades: {total}\n"
        f"Completed: {completed}\n"
        f"Refunded: {refunded}\n"
        f"Total Volume: {volume}"
    )
    bot.reply_to(m, txt)

@bot.message_handler(commands=['gstats'])
def cmd_gstats(m: Message):
    agg = {}
    for t in trades.values():
        adm = str(t.get('admin','unknown'))
        if adm not in agg:
            agg[adm] = {"hold":0.0, "completed":0.0, "refunded":0.0, "count":0}
        agg[adm]["count"] += 1
        if t.get('status') == 'open':
            agg[adm]["hold"] += float(t.get('amount',0) or 0)
        if t.get('status') == 'completed':
            agg[adm]["completed"] += float(t.get('amount',0) or 0)
        if t.get('status') == 'refunded':
            agg[adm]["refunded"] += float(t.get('amount',0) or 0)
    lines = ["🌐 Global Stats (All time)"]
    for adm, v in agg.items():
        lines.append(f"\nEscrowed by : {adm}")
        lines.append(f"Hold        : {v['hold']}")
        lines.append(f"Completed   : {v['completed']}")
        lines.append(f"Refunded    : {v['refunded']}")
        lines.append(f"Total Trades: {v['count']}")
    bot.reply_to(m, "\n".join(lines))

@bot.message_handler(commands=['mystats'])
def cmd_mystats(m: Message):
