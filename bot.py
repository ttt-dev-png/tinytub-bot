import os, json, requests, asyncio, logging
from datetime import datetime, date, time
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===================== CONFIG =====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://yestajnjkjzgxglavhdb.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '')  # Karan's chat ID
BRIEF_HOUR = int(os.environ.get('BRIEF_HOUR', '8'))  # 8am IST

SB_HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=representation'
}

# Allowed Telegram usernames (add more as needed)
ALLOWED_USERS = ['TTTOpsBot', 'karanalim94', 'karan']  # update with real usernames

# ===================== SUPABASE HELPERS =====================
def sb_get(table, params=''):
    r = requests.get(f'{SUPABASE_URL}/rest/v1/{table}?{params}', headers=SB_HEADERS, timeout=10)
    return r.json() if r.status_code == 200 else []

def sb_post(table, data):
    r = requests.post(f'{SUPABASE_URL}/rest/v1/{table}', headers=SB_HEADERS, json=data, timeout=10)
    return r.json() if r.status_code in [200, 201] else None

def sb_patch(table, params, data):
    r = requests.patch(f'{SUPABASE_URL}/rest/v1/{table}?{params}', headers=SB_HEADERS, json=data, timeout=10)
    return r.status_code in [200, 204]

def today():
    return str(date.today())

def is_overdue(task):
    if task.get('status') == 'done':
        return False
    due = task.get('due')
    return due and due < today()

# ===================== BRIEF BUILDER =====================
def build_brief():
    tasks = sb_get('tasks', 'select=*')
    restock = sb_get('restock_requests', 'select=*&status=eq.pending')
    members = sb_get('members', 'select=id,name')
    logs = sb_get('checklist_logs', f'select=*&date=eq.{today()}')
    notices = sb_get('notices', 'select=*&order=created_at.desc&limit=3')

    overdue = [t for t in tasks if is_overdue(t)]
    open_tasks = [t for t in tasks if t.get('status') != 'done']

    # Group overdue by person
    overdue_by_person = {}
    for t in overdue:
        name = t.get('assignee', 'Unknown')
        if name not in overdue_by_person:
            overdue_by_person[name] = []
        overdue_by_person[name].append(t.get('title', '?')[:40])

    msg = f"🌅 *Good morning, Karan!*\n_{datetime.now().strftime('%A, %d %B')}_\n\n"

    # Tasks
    msg += f"📋 *TASKS*\n"
    msg += f"• {len(open_tasks)} open · {len(overdue)} overdue\n"
    if overdue_by_person:
        for person, items in overdue_by_person.items():
            msg += f"  ⚠️ {person} — {len(items)} overdue\n"
            for item in items[:2]:
                msg += f"    · _{item}_\n"
    msg += "\n"

    # Restock
    sos = [r for r in restock if r.get('urgency') == 'sos']
    urgent = [r for r in restock if r.get('urgency') == '1d']
    msg += f"📦 *RESTOCK*\n"
    msg += f"• {len(restock)} pending"
    if sos:
        msg += f" · 🔴 {len(sos)} SOS"
    if urgent:
        msg += f" · 🟠 {len(urgent)} urgent"
    msg += "\n"
    for r in restock[:3]:
        urg_emoji = {'sos':'🔴','1d':'🟠','3d':'🟡','5d':'🟢','20d':'⚪'}.get(r.get('urgency',''),'')
        msg += f"  {urg_emoji} {r.get('item_name','?')} — {r.get('quantity','?')}\n"
    msg += "\n"

    # Checklist
    total_members = len(members)
    completed = len(logs)
    avg_pct = int(sum(l.get('completion_pct', 0) for l in logs) / max(completed, 1))
    msg += f"✅ *CHECKLIST*\n"
    msg += f"• {completed}/{total_members} members logged · avg {avg_pct}%\n\n"

    # Recent notices
    if notices:
        msg += f"📣 *LATEST NOTICE*\n"
        n = notices[0]
        msg += f"• _{n.get('body','')[:80]}_\n  — {n.get('author','')}\n\n"

    msg += f"[Open app →](https://tinytub-ops.vercel.app)"
    return msg

# ===================== COMMAND PARSER =====================
def parse_command(text):
    t = text.lower().strip()

    # Summary / brief
    if any(w in t for w in ['summary', 'brief', 'update', "what's happening", 'status', 'overview']):
        return 'brief'

    # Overdue
    if 'overdue' in t or 'behind' in t or 'late' in t:
        return 'overdue'

    # Add task
    if t.startswith('add task') or t.startswith('task:') or t.startswith('create task'):
        return 'add_task'

    # Add restock
    if 'restock' in t or 'order' in t and 'ingredient' in t:
        return 'add_restock'

    # Post notice
    if t.startswith('notice:') or t.startswith('post notice') or t.startswith('announce'):
        return 'add_notice'

    # Checklist
    if 'checklist' in t or 'daily' in t and 'check' in t:
        return 'checklist'

    # Help
    if 'help' in t or t == '/help':
        return 'help'

    return 'unknown'

async def handle_brief(update, context):
    await update.message.reply_text("Pulling live data... ⏳")
    try:
        msg = build_brief()
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Error pulling data: {str(e)}")

async def handle_overdue(update, context):
    tasks = sb_get('tasks', 'select=*')
    overdue = [t for t in tasks if is_overdue(t)]
    if not overdue:
        await update.message.reply_text("✅ No overdue tasks right now!")
        return
    msg = f"⚠️ *{len(overdue)} overdue tasks:*\n\n"
    for t in overdue:
        msg += f"• *{t.get('assignee','?')}* — {t.get('title','?')[:50]}\n  Due: {t.get('due','?')}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_add_task(update, context, text):
    # Parse: "Add task: [assignee] to [title] by [date]"
    # Simple extraction
    import re
    msg = text.lower()
    # Remove trigger words
    for w in ['add task:', 'add task', 'task:', 'create task:']:
        msg = msg.replace(w, '').strip()

    # Try to find assignee from member names
    members = sb_get('members', 'select=id,name')
    assignee = 'Karan'
    title = msg

    for m in members:
        name = m['name'].lower()
        if name in msg:
            assignee = m['name']
            title = msg.replace(name, '').replace(' to ', '').replace('  ', ' ').strip()
            break

    # Try to find date
    due = None
    date_patterns = [
        (r'by (\w+ \d+)', None),
        (r'by (monday|tuesday|wednesday|thursday|friday|saturday|sunday)', None),
        (r'by (tomorrow)', None),
    ]
    for pat, fmt in date_patterns:
        match = re.search(pat, title, re.IGNORECASE)
        if match:
            date_str = match.group(1).lower()
            title = title[:match.start()].strip()
            if date_str == 'tomorrow':
                from datetime import timedelta
                due = str(date.today() + timedelta(days=1))
            break

    # Capitalise title
    title = title.capitalize()

    task = {
        'title': title,
        'assignee': assignee,
        'assigned_by': 'Karan',
        'due': due,
        'status': 'new',
        'tag': 'operations',
        'comments': []
    }

    result = sb_post('tasks', task)
    if result:
        reply = f"✅ Task created!\n*{title}*\nAssigned to: {assignee}"
        if due:
            reply += f"\nDue: {due}"
    else:
        reply = "❌ Failed to create task. Try again."
    await update.message.reply_text(reply, parse_mode='Markdown')

async def handle_add_restock(update, context, text):
    # Parse: "Restock: [item] [qty] [urgency]"
    msg = text.lower()
    for w in ['restock:', 'restock', 'order:']:
        msg = msg.replace(w, '').strip()

    urgency = 'pending'
    if 'sos' in msg or 'immediate' in msg:
        urgency = 'sos'
        msg = msg.replace('sos','').replace('immediate','').strip()
    elif 'urgent' in msg or '1 day' in msg or '1d' in msg:
        urgency = '1d'
        msg = msg.replace('urgent','').strip()
    elif '3 day' in msg or '3d' in msg:
        urgency = '3d'
    elif '5 day' in msg or '5d' in msg:
        urgency = '5d'

    parts = msg.split()
    item = ' '.join(parts[:2]).capitalize() if parts else 'Unknown'
    qty = ' '.join(parts[2:4]) if len(parts) > 2 else 'To confirm'

    restock = {
        'item_name': item,
        'quantity': qty,
        'urgency': urgency,
        'status': 'pending',
        'submitted_by': 'Karan (via Telegram)',
        'notes': None,
        'vendor_id': None
    }
    result = sb_post('restock_requests', restock)
    urg_label = {'sos':'🔴 SOS','1d':'🟠 1-2 days','3d':'🟡 3-5 days','5d':'🟢 5-10 days'}.get(urgency,'⚪')
    if result:
        await update.message.reply_text(f"✅ Restock request added!\n*{item}* — {qty}\nUrgency: {urg_label}", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Failed to add restock. Try again.")

async def handle_add_notice(update, context, text):
    msg = text
    for w in ['Notice:', 'notice:', 'Post notice:', 'post notice:', 'Announce:', 'announce:']:
        msg = msg.replace(w, '').strip()

    notice = {
        'author': 'Karan',
        'body': msg,
        'tag': 'general',
        'tagged': []
    }
    result = sb_post('notices', notice)
    if result:
        await update.message.reply_text(f"✅ Notice posted!\n_{msg}_", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Failed to post notice.")

async def handle_checklist(update, context):
    logs = sb_get('checklist_logs', f'select=*&date=eq.{today()}')
    members = sb_get('members', 'select=id,name')
    member_map = {m['id']: m['name'] for m in members}
    logged_ids = {l['member_id'] for l in logs}
    msg = f"✅ *Checklist — {today()}*\n\n"
    for m in members:
        log = next((l for l in logs if l['member_id'] == m['id']), None)
        if log:
            pct = log.get('completion_pct', 0)
            bar = '🟢' if pct >= 80 else '🟡' if pct >= 50 else '🔴'
            msg += f"{bar} {m['name']} — {pct}%\n"
        else:
            msg += f"⚪ {m['name']} — not started\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_help(update, context):
    msg = """🤖 *Tiny Tub Ops Bot*

*Commands:*
• `summary` — morning brief
• `overdue` — see late tasks
• `checklist` — today's completion
• `add task: [person] to [task] by [date]`
• `restock: [item] [qty] [urgency]`
• `notice: [message]`

*Examples:*
_"Add task: Chaitali to check freezer by Friday"_
_"Restock: dark chocolate 10kg urgent"_
_"Notice: Kitchen closed Sunday"_
_"What's overdue?"_
"""
    await update.message.reply_text(msg, parse_mode='Markdown')

# ===================== MAIN HANDLERS =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Save chat ID on first message
    chat_id = update.effective_chat.id
    logger.info(f"Chat ID: {chat_id}")
    await update.message.reply_text(
        f"👋 Welcome to Tiny Tub Ops!\n\nYour chat ID is: `{chat_id}`\n\nType `help` to see what I can do.",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ''
    chat_id = update.effective_chat.id
    logger.info(f"Message from {chat_id}: {text[:50]}")

    cmd = parse_command(text)

    if cmd == 'brief':
        await handle_brief(update, context)
    elif cmd == 'overdue':
        await handle_overdue(update, context)
    elif cmd == 'add_task':
        await handle_add_task(update, context, text)
    elif cmd == 'add_restock':
        await handle_add_restock(update, context, text)
    elif cmd == 'add_notice':
        await handle_add_notice(update, context, text)
    elif cmd == 'checklist':
        await handle_checklist(update, context)
    elif cmd == 'help':
        await handle_help(update, context)
    else:
        await update.message.reply_text(
            "I didn't understand that. Type `help` to see what I can do.",
            parse_mode='Markdown'
        )

# ===================== SCHEDULER =====================
async def send_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        logger.warning("No ADMIN_CHAT_ID set")
        return
    try:
        msg = build_brief()
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode='Markdown')
        logger.info("Morning brief sent!")
    except Exception as e:
        logger.error(f"Failed to send brief: {e}")

# ===================== ENTRY POINT =====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', handle_help))
    app.add_handler(CommandHandler('summary', handle_brief))
    app.add_handler(CommandHandler('overdue', handle_overdue))
    app.add_handler(CommandHandler('checklist', handle_checklist))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Morning brief — runs at 8am IST (2:30am UTC)
    job_queue = app.job_queue
    job_queue.run_daily(
        send_morning_brief,
        time=time(hour=2, minute=30),  # 8am IST = 2:30am UTC
        name='morning_brief'
    )

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
