import os
import re
import json
from datetime import datetime, timezone
import telebot
from telebot.types import Message

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_IDS_ENV = os.getenv("OWNER_IDS", "")
LOG_CHANNEL_ENV = os.getenv("LOG_CHANNEL", "")
DATA_FILE = "data.json"
FEE_PCT = 3.0  # percentage fee when using +fee variants

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN not set. Add it to environment/secrets.")

# parse owners
try:
    OWNERS = [int(x.strip()) for x in OWNER_IDS_ENV.split(",") if x.strip()]
except Exception:
    OWNERS = []

# parse log channel (allow numeric or @username)
LOG_CHANNEL = None
if LOG_CHANNEL_ENV:
    try:
        LOG_CHANNEL = int(LOG_CHANNEL_ENV)
    except Exception:
        LOG_CHANNEL = LOG_CHANNEL_ENV  # keep as string (e.g. @channelusername)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ---------- Persistence ----------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"trades": {}, "admins": [], "next_id": 1}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            # corrupted file -> reset
            return {"trades": {}, "admins": [], "next_id": 1}

def save_data(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)

data = load_data()
trades = data.get("trades", {})
admins = set(data.get("admins", []))
try:
    next_id = int(data.get("next_id", 1))
except Exception:
    next_id = 1

def persist():
    global data
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
    """
    Extracts a deal form with fields:
    BUYER: @username or text
    SELLER: @username or text
    DEAL AMOUNT: numeric (comma allowed)
    DEAL INFO: optional rest of line
    TIME TO DEAL: optional
    """
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
        except Exception:
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

# ---------- Commands ----------
@bot.message_handler(commands=['start', 'help'])
def cmd_start(m: Message):
    txt = (
        "👋 Welcome to Escrow Bot!\n\n"
        "• Reply to a deal form and Admin uses /add or /add+fee to register payment.\n"
        "• Admin later uses /done, /done+fee, /refund, /refund+fee.\n"
        "• /mystats → See all your deals (as buyer or seller).\n"
        "• /stats → Group stats.\n"
        "• /gstats → Global stats per admin.\n"
        "Owner commands: /addadmin <user_id>, /removeadmin <user_id>\n"
    )
    bot.reply_to(m, txt)

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
    except Exception:
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
    except Exception:
        bot.reply_to(m, "Invalid user id.")
        return
    if uid in admins:
        admins.remove(uid)
        persist()
        bot.reply_to(m, f"❌ Removed admin: {uid}")
    else:
        bot.reply_to(m, "⚠️ This user is not an admin.")

# ---- Single handler for /add, /add+fee, and redirect for refund+fee ----
@bot.message_handler(commands=['add', 'add+fee', 'refund+fee'])
def cmd_add(m: Message):
    user_id = m.from_user.id
    if not is_admin(user_id):
        bot.reply_to(m, "⛔ Only Admins can add or refund deals.")
        return

    # If command is refund+fee → redirect to refund logic
    if m.text.strip().lower() == '/refund+fee':
        return cmd_refund(m)

    if not m.reply_to_message or not m.reply_to_message.text:
        bot.reply_to(m, "⚠️ Please reply to the deal form message with /add or /add+fee")
        return

    form = parse_deal_form(m.reply_to_message.text)
    if not form['buyer'] or not form['seller'] or form['amount'] is None:
        bot.reply_to(m, "❌ Could not extract Buyer/Seller/Amount from the form. Make sure it's formatted like:\n\nBUYER: @user\nSELLER: @user\nDEAL AMOUNT: 100\nDEAL INFO: ...")
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
        "origin_message_id": m.reply_to_message.message_id,
        "info": form.get("info", ""),
        "time_to_deal": form.get("time_to_deal", "")
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

    if LOG_CHANNEL:
        try:
            bot.send_message(LOG_CHANNEL, f"📜 Payment Received (Log)\n{('-'*24)}\n{ text }")
        except Exception:
            # don't fail bot if logging fails
            pass

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
    if use_fee and float(trade.get('fee', 0)) == 0:
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
    if LOG_CHANNEL:
        try:
            bot.send_message(LOG_CHANNEL, f"📜 Deal Completed (Log)\n{('-'*24)}\n{ out }")
        except Exception:
            pass

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
    if use_fee and float(trade.get('fee', 0)) == 0:
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
    if LOG_CHANNEL:
        try:
            bot.send_message(LOG_CHANNEL, f"📜 Refund Completed (Log)\n{('-'*24)}\n{ out }")
        except Exception:
            pass

@bot.message_handler(commands=['stats'])
def cmd_stats(m: Message):
    chat_id = m.chat.id
    total = completed = refunded = 0
    volume = 0.0
    for t in trades.values():
        if t.get('chat_id') == chat_id:
            total += 1
            volume += float(t.get('amount', 0) or 0)
            if t.get('status') == 'completed':
                completed += 1
            if t.get('status') == 'refunded':
                refunded += 1
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
        adm = str(t.get('admin', 'unknown'))
        if adm not in agg:
            agg[adm] = {"hold": 0.0, "completed": 0.0, "refunded": 0.0, "count": 0}
        agg[adm]["count"] += 1
        if t.get('status') == 'open':
            agg[adm]["hold"] += float(t.get('amount', 0) or 0)
        if t.get('status') == 'completed':
            agg[adm]["completed"] += float(t.get('amount', 0) or 0)
        if t.get('status') == 'refunded':
            agg[adm]["refunded"] += float(t.get('amount', 0) or 0)
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
    user = m.from_user
    uname = "@" + user.username if user.username else None
    uid = user.id
    matches = []
    for t in trades.values():
        b = (t.get('buyer') or "").lower()
        s = (t.get('seller') or "").lower()
        if uname and uname.lower() in b or uname and uname.lower() in s:
            matches.append(t); continue
        if str(uid) in b or str(uid) in s:
            matches.append(t); continue
    if not matches:
        bot.reply_to(m, "ℹ️ Koi deals nahi mile aapke liye.")
        return
    parts = [f"📋 {len(matches)} deals found for {user.first_name}:"]
    for t in sorted(matches, key=lambda x: x.get('created_at', '')):
        parts.append(
            f"\n🆔 #{t['id']}\nBuyer: {t['buyer']}\nSeller: {t['seller']}\nAmount: {t['amount']}\nStatus: {t['status']}\nCreated: {t['created_at']}\nUpdated: {t['updated_at']}"
        )
    bot.reply_to(m, "\n".join(parts))

@bot.message_handler(func=lambda m: True, content_types=['text'])
def fallback(m: Message):
    # unknown command or plain text fallback
    if m.text and m.text.startswith('/'):
        bot.reply_to(m, "Unknown command. Use /start to see available commands.")
    else:
        # for normal chat messages we don't respond (prevent spam)
        return

# ---------- Start bot ----------
if __name__ == "__main__":
    print("Bot started. Press Ctrl-C to stop.")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        print("Stopping (keyboard).")
    except Exception as e:
        print("Polling stopped with exception:", e)
