"""
LINE 團購接龍機器人
團主貼出商品清單，成員用 +編號 下單，支援累加、代訂、退出。
"""

import os
import re
import json
import sqlite3
import logging
import threading
from datetime import datetime

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, JoinEvent
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TZ_TAIPEI = pytz.timezone("Asia/Taipei")

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
DB_PATH = os.environ.get("DB_PATH", "/data/tuangou.db")

# 立即檢查 DB 路徑是否可寫，不可用就 fallback 到當前目錄
try:
    _db_dir = os.path.dirname(DB_PATH)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)
    # 嘗試實際開啟 DB 測試寫入
    _test_conn = sqlite3.connect(DB_PATH)
    _test_conn.execute("CREATE TABLE IF NOT EXISTS _ping (id INTEGER)")
    _test_conn.close()
    logger.info(f"[startup] 資料庫路徑可用: {DB_PATH}")
except Exception:
    DB_PATH = "tuangou.db"
    logger.warning(f"[startup] 原路徑不可寫，改用當前目錄: {DB_PATH}")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ── 品項解析正規表示式
ITEM_NUM_RE = re.compile(r'^\s*[（(]?(\d+)[）)\.\、\)]\s*(.*)')

HELP_TEXT = """📖 團購指令說明
━━━━━━━━━━━━━━
【所有人可用】
指令　　　　　　　說明
──────────────
+N　　　　　　　 下單品項N（1份）
+N 數量　　　　　下單品項N指定數量
+N 名字　　　　　幫人下單1份
+N 名字 數量　　 幫人下單指定數量
+N +M +K 名字　 一次下單多品項
退出 N　　　　　 取消品項N的訂單
退出 N 名字　　　取消指定人的訂單
列表　　　　　　　查看所有下單狀況
我的訂單　　　　　查看自己的訂單
團購說明　　　　　顯示本說明

━━━━━━━━━━━━━━
【團主專用】
指令　　　　　　　說明
──────────────
結團　　　　　　　封存最終訂單
取消團購　　　　　刪除所有資料"""


# ══════════════════════════════════════════
# 資料庫
# ══════════════════════════════════════════

def init_db():
    global DB_PATH
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"[startup] 建立資料庫目錄: {db_dir}")
        except OSError as e:
            logger.warning(f"[startup] 無法建立 {db_dir}: {e}，改用當前目錄")
            DB_PATH = "tuangou.db"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS group_buys (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id      TEXT    NOT NULL,
            title         TEXT    NOT NULL,
            description   TEXT,
            creator_id    TEXT    NOT NULL,
            creator_name  TEXT,
            status        TEXT    DEFAULT 'open',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_buy_id  INTEGER NOT NULL,
            item_num      INTEGER NOT NULL,
            name          TEXT    NOT NULL,
            price_info    TEXT,
            FOREIGN KEY (group_buy_id) REFERENCES group_buys (id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            group_buy_id  INTEGER NOT NULL,
            item_num      INTEGER NOT NULL,
            user_id       TEXT    NOT NULL,
            user_name     TEXT,
            quantity      INTEGER DEFAULT 1,
            registered_by TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_buy_id) REFERENCES group_buys (id)
        )
    """)

    conn.commit()
    conn.close()


# ══════════════════════════════════════════
# 資料庫輔助函式
# ══════════════════════════════════════════

def get_active_buy(group_id):
    """取得群組中目前進行中的團購"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        'SELECT * FROM group_buys WHERE group_id=? AND status="open" ORDER BY id DESC LIMIT 1',
        (group_id,),
    )
    row = c.fetchone()
    conn.close()
    # cols: id, group_id, title, description, creator_id, creator_name, status, created_at
    return row


def get_items(group_buy_id):
    """取得團購的所有品項"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM items WHERE group_buy_id=? ORDER BY item_num", (group_buy_id,))
    rows = c.fetchall()
    conn.close()
    # cols: id, group_buy_id, item_num, name, price_info
    return rows


def get_orders(group_buy_id):
    """取得團購的所有訂單"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE group_buy_id=? ORDER BY item_num, id", (group_buy_id,))
    rows = c.fetchall()
    conn.close()
    # cols: id, group_buy_id, item_num, user_id, user_name, quantity, registered_by, created_at
    return rows


def get_item_name(group_buy_id, item_num):
    """取得指定品項的名稱"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT name FROM items WHERE group_buy_id=? AND item_num=?",
        (group_buy_id, item_num),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


# ══════════════════════════════════════════
# 通用輔助函式
# ══════════════════════════════════════════

def get_user_name(event, group_id, user_id):
    try:
        if event.source.type == "group":
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception:
        return None


def source_id(event):
    src = event.source
    if src.type == "group":
        return src.group_id
    if src.type == "room":
        return src.room_id
    return src.user_id


def normalize(text):
    """全形英數符號 → 半形（處理中文輸入法輸入的 ＋、１２３ 等）"""
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif ch == '\u3000':
            result.append(' ')
        else:
            result.append(ch)
    return ''.join(result)


# ══════════════════════════════════════════
# 品項解析
# ══════════════════════════════════════════

def parse_group_buy(text):
    """
    解析開團貼文，回傳 (title, items_list)
    items_list = [(item_num, name, price_info), ...]
    """
    lines = text.split('\n')

    # 跳過第一行的「開團」字樣
    start = 0
    if lines and re.match(r'^\s*開團\s*$', lines[0]):
        start = 1

    # 找出所有品項的起始行
    item_starts = []  # [(line_index, item_num, first_line_text)]
    for i in range(start, len(lines)):
        m = ITEM_NUM_RE.match(lines[i])
        if m:
            item_starts.append((i, int(m.group(1)), m.group(2).strip()))

    if not item_starts:
        return None, []

    # 品項編號之前的非空行 = 標題
    title_lines = []
    for i in range(start, item_starts[0][0]):
        line = lines[i].strip()
        if line:
            title_lines.append(line)
    title = ' '.join(title_lines) if title_lines else "團購"

    # 解析每個品項（包含到下一個品項之前的所有行）
    items_list = []
    for idx, (line_i, item_num, first_text) in enumerate(item_starts):
        # 確定此品項的結束行
        if idx + 1 < len(item_starts):
            end_i = item_starts[idx + 1][0]
        else:
            end_i = len(lines)

        # 收集該品項的所有行
        item_lines = []
        # 第一行：品項編號後的文字
        if first_text:
            item_lines.append(first_text)
        # 後續行
        for j in range(line_i + 1, end_i):
            line = lines[j].strip()
            if line:
                item_lines.append(line)

        name = item_lines[0] if item_lines else f"品項{item_num}"
        price_info = '\n'.join(item_lines) if item_lines else name

        items_list.append((item_num, name, price_info))

    return title, items_list


# ══════════════════════════════════════════
# 指令函式
# ══════════════════════════════════════════

def cmd_open(group_id, user_id, user_name, text):
    """開團：解析貼文建立團購"""
    # 檢查是否已有進行中的團購
    active = get_active_buy(group_id)
    if active:
        return f"⚠️ 目前已有進行中的團購：{active[2]}\n請先「結團」或「取消團購」再開新團。"

    # 移除開頭的「開團」
    post_text = re.sub(r'^\s*開團\s*\n?', '', text, count=1).strip()
    full_text = text  # 保留原始完整貼文

    title, items_list = parse_group_buy(text)

    if not items_list:
        return "⚠️ 無法解析品項，請確認格式：\n開團\n標題\n1) 品名 價格\n2) 品名 價格"

    # 寫入資料庫
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO group_buys (group_id, title, description, creator_id, creator_name) VALUES (?, ?, ?, ?, ?)",
        (group_id, title, full_text, user_id, user_name),
    )
    buy_id = c.lastrowid

    for item_num, name, price_info in items_list:
        c.execute(
            "INSERT INTO items (group_buy_id, item_num, name, price_info) VALUES (?, ?, ?, ?)",
            (buy_id, item_num, name, price_info),
        )

    conn.commit()
    conn.close()

    # 組合回覆
    lines = [f"🛒 開團成功！{title}", "────────────────"]
    for item_num, name, price_info in items_list:
        # 顯示完整 price_info（多行品項資訊）
        info_lines = price_info.split('\n')
        lines.append(f"【{item_num}】{info_lines[0]}")
        for extra in info_lines[1:]:
            lines.append(f"　　{extra}")
    lines.append("────────────────")
    lines.append("下單方式：+品項編號")
    lines.append("例如：+1 或 +1 2（2份）")

    return '\n'.join(lines)


def cmd_order(group_id, user_id, user_name, text):
    """下單：+N / +N 數量 / +N 名字 / +N 名字 數量"""
    active = get_active_buy(group_id)
    if not active:
        return None  # 沒有進行中的團購，靜默

    buy_id = active[0]

    # 解析指令
    m = re.match(r'\+(\d+)(?:\s+(.*))?$', text)
    if not m:
        return None
    item_num = int(m.group(1))
    rest = m.group(2).strip() if m.group(2) else ""

    # 確認品項存在
    item_name = get_item_name(buy_id, item_num)
    if not item_name:
        return f"⚠️ 沒有品項【{item_num}】，請確認編號。"

    # 解析 rest：數量 / 名字 / 名字 數量
    order_name = user_name or "（未知）"
    quantity = 1
    registered_by = None

    if rest:
        # 嘗試判斷：純數字 → 數量
        if re.match(r'^\d+$', rest):
            quantity = int(rest)
        else:
            # 名字 [數量]
            parts = rest.rsplit(None, 1)
            if len(parts) == 2 and re.match(r'^\d+$', parts[1]):
                order_name = parts[0]
                quantity = int(parts[1])
                registered_by = user_name
            else:
                order_name = rest
                registered_by = user_name

    if quantity < 1:
        return "⚠️ 數量必須大於 0"

    # 累加制：查詢是否已有同品項同名的訂單
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, quantity FROM orders WHERE group_buy_id=? AND item_num=? AND user_name=?",
        (buy_id, item_num, order_name),
    )
    existing = c.fetchone()

    if existing:
        new_qty = existing[1] + quantity
        c.execute("UPDATE orders SET quantity=? WHERE id=?", (new_qty, existing[0]))
        total = new_qty
    else:
        c.execute(
            "INSERT INTO orders (group_buy_id, item_num, user_id, user_name, quantity, registered_by) VALUES (?, ?, ?, ?, ?, ?)",
            (buy_id, item_num, user_id, order_name, quantity, registered_by),
        )
        total = quantity

    conn.commit()
    conn.close()

    return f"✅ {order_name}【{item_num}】{item_name} +{quantity}份（共 {total} 份）"


def cmd_order_multi(group_id, user_id, user_name, text):
    """多品項下單：+1 +3 +5 名字"""
    active = get_active_buy(group_id)
    if not active:
        return None

    buy_id = active[0]

    # 提取所有 +N
    item_nums = [int(x) for x in re.findall(r'\+(\d+)', text)]

    # 提取名字（去除所有 +N 後的剩餘文字）
    rest = re.sub(r'\+\d+', '', text).strip()
    order_name = rest if rest else (user_name or "（未知）")
    registered_by = user_name if rest else None

    results = []
    for item_num in item_nums:
        item_name = get_item_name(buy_id, item_num)
        if not item_name:
            results.append(f"⚠️ 沒有品項【{item_num}】")
            continue

        # 累加制
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT id, quantity FROM orders WHERE group_buy_id=? AND item_num=? AND user_name=?",
            (buy_id, item_num, order_name),
        )
        existing = c.fetchone()

        if existing:
            new_qty = existing[1] + 1
            c.execute("UPDATE orders SET quantity=? WHERE id=?", (new_qty, existing[0]))
            total = new_qty
        else:
            c.execute(
                "INSERT INTO orders (group_buy_id, item_num, user_id, user_name, quantity, registered_by) VALUES (?, ?, ?, ?, ?, ?)",
                (buy_id, item_num, user_id, order_name, 1, registered_by),
            )
            total = 1

        conn.commit()
        conn.close()
        results.append(f"✅ {order_name}【{item_num}】{item_name}（共 {total} 份）")

    return '\n'.join(results)


def cmd_cancel_order(group_id, user_id, user_name, text):
    """退出：退出 N / 退出 N 名字"""
    active = get_active_buy(group_id)
    if not active:
        return None

    buy_id = active[0]

    m = re.match(r'退出\s+(\d+)(?:\s+(\S+))?', text)
    if not m:
        return None
    item_num = int(m.group(1))
    target_name = m.group(2)

    # 確認品項存在
    item_name = get_item_name(buy_id, item_num)
    if not item_name:
        return f"⚠️ 沒有品項【{item_num}】"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if target_name:
        # 退出指定人的訂單
        c.execute(
            "SELECT id FROM orders WHERE group_buy_id=? AND item_num=? AND user_name=?",
            (buy_id, item_num, target_name),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return f"⚠️ 找不到 {target_name} 在【{item_num}】{item_name} 的訂單"
        c.execute("DELETE FROM orders WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        return f"❌ 已取消 {target_name}【{item_num}】{item_name} 的訂單"
    else:
        # 退出自己的訂單（用 user_name 比對）
        cancel_name = user_name or "（未知）"
        c.execute(
            "SELECT id FROM orders WHERE group_buy_id=? AND item_num=? AND user_name=?",
            (buy_id, item_num, cancel_name),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return f"⚠️ 你沒有在【{item_num}】{item_name} 下單"
        c.execute("DELETE FROM orders WHERE id=?", (row[0],))
        conn.commit()
        conn.close()
        return f"❌ 已取消【{item_num}】{item_name} 的訂單"


def cmd_list(group_id):
    """列表：查看所有下單狀況"""
    active = get_active_buy(group_id)
    if not active:
        return "目前沒有進行中的團購。"

    buy_id = active[0]
    title = active[2]
    items = get_items(buy_id)
    orders = get_orders(buy_id)

    # 按品項分組訂單
    orders_by_item = {}
    for o in orders:
        # o: id, group_buy_id, item_num, user_id, user_name, quantity, registered_by, created_at
        orders_by_item.setdefault(o[2], []).append(o)

    lines = [f"🛒 {title}", "────────────────"]
    total_orders = 0

    for item in items:
        # item: id, group_buy_id, item_num, name, price_info
        item_num = item[2]
        price_info = item[4] or item[3]

        # 顯示品項（含完整價格資訊）
        info_lines = price_info.split('\n')
        lines.append(f"【{item_num}】{info_lines[0]}")
        for extra in info_lines[1:]:
            lines.append(f"　　{extra}")

        item_orders = orders_by_item.get(item_num, [])
        if item_orders:
            subtotal = 0
            for o in item_orders:
                name = o[4] or "（未知）"
                qty = o[5]
                subtotal += qty
                lines.append(f"   👤 {name} x{qty}")
            total_orders += subtotal
            lines.append(f"   小計：{subtotal} 份")
        else:
            lines.append("   （尚無人下單）")

        lines.append("")  # 空行分隔

    lines.append("────────────────")
    lines.append(f"共 {total_orders} 份訂單")

    return '\n'.join(lines)


def cmd_my_orders(group_id, user_id, user_name):
    """我的訂單：查看自己的下單（含代訂）"""
    active = get_active_buy(group_id)
    if not active:
        return "目前沒有進行中的團購。"

    buy_id = active[0]
    title = active[2]
    my_name = user_name or "（未知）"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 自己的訂單（user_id 比對，排除代訂）
    c.execute(
        "SELECT item_num, user_name, quantity FROM orders WHERE group_buy_id=? AND user_id=? AND registered_by IS NULL ORDER BY item_num",
        (buy_id, user_id),
    )
    own_orders = c.fetchall()

    # 幫別人代訂的（registered_by 不為空，且 user_id 是自己）
    c.execute(
        "SELECT item_num, user_name, quantity FROM orders WHERE group_buy_id=? AND user_id=? AND registered_by IS NOT NULL ORDER BY item_num",
        (buy_id, user_id),
    )
    proxy_orders = c.fetchall()

    conn.close()

    if not own_orders and not proxy_orders:
        return f"📋 {title}\n你目前沒有下單。"

    lines = [f"📋 {title}", f"👤 {my_name} 的訂單", "────────────────"]

    for item_num, name, qty in own_orders:
        item_name = get_item_name(buy_id, item_num) or f"品項{item_num}"
        lines.append(f"【{item_num}】{item_name} x{qty}")

    if proxy_orders:
        lines.append("")
        lines.append("📦 代訂：")
        for item_num, name, qty in proxy_orders:
            item_name = get_item_name(buy_id, item_num) or f"品項{item_num}"
            lines.append(f"【{item_num}】{item_name} x{qty}（{name}）")

    lines.append("────────────────")
    total = len(own_orders) + len(proxy_orders)
    lines.append(f"共 {total} 項")

    return '\n'.join(lines)


def cmd_close(group_id, user_id):
    """結團：封存訂單（僅團主可用）"""
    active = get_active_buy(group_id)
    if not active:
        return "目前沒有進行中的團購。"

    buy_id = active[0]
    title = active[2]
    creator_id = active[4]

    if user_id != creator_id:
        return "⚠️ 只有團主可以結團。"

    # 先產生最終列表
    final_list = cmd_list(group_id)

    # 更新狀態
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE group_buys SET status='closed' WHERE id=?", (buy_id,))
    conn.commit()
    conn.close()

    return f"🔒 團購已結團！\n\n{final_list}"


def cmd_cancel_buy(group_id, user_id):
    """取消團購：刪除所有資料（僅團主可用）"""
    active = get_active_buy(group_id)
    if not active:
        return "目前沒有進行中的團購。"

    buy_id = active[0]
    title = active[2]
    creator_id = active[4]

    if user_id != creator_id:
        return "⚠️ 只有團主可以取消團購。"

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE group_buy_id=?", (buy_id,))
    c.execute("DELETE FROM items WHERE group_buy_id=?", (buy_id,))
    c.execute("DELETE FROM group_buys WHERE id=?", (buy_id,))
    conn.commit()
    conn.close()

    return f"🗑️ 團購「{title}」已取消，所有資料已刪除。"


# ══════════════════════════════════════════
# Flask 路由 & LINE Webhook
# ══════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    return str({
        "status": "ok",
        "token_set": bool(LINE_CHANNEL_ACCESS_TOKEN),
        "secret_set": bool(LINE_CHANNEL_SECRET),
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        events = json.loads(body).get("events", [])
        for ev in events:
            logger.info(f"[webhook] type={ev.get('type')} source={ev.get('source', {}).get('type')}")
    except Exception:
        logger.info(f"[webhook] raw: {body[:200]}")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("[webhook] Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"[webhook] 處理失敗: {e}")
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = normalize(event.message.text.strip())
    gid = source_id(event)
    uid = event.source.user_id

    logger.info(f"[msg] text={repr(text[:60])}")

    def lazy_name():
        return get_user_name(event, gid, uid)

    reply = None

    # ── 開團（多行文字且含品項編號）
    if re.match(r'^\s*開團', text) and '\n' in text:
        reply = cmd_open(gid, uid, lazy_name(), text)

    # ── 多品項下單（+1 +3 +5 名字）
    elif len(re.findall(r'\+\d+', text)) > 1:
        reply = cmd_order_multi(gid, uid, lazy_name(), text)

    # ── 單品項下單（+N / +N 數量 / +N 名字 / +N 名字 數量）
    elif re.match(r'\+\d+(\s|$)', text):
        reply = cmd_order(gid, uid, lazy_name(), text)

    # ── 退出
    elif re.match(r'退出\s+\d+', text):
        reply = cmd_cancel_order(gid, uid, lazy_name(), text)

    # ── 列表
    elif text in ("列表", "/列表", "查看", "清單"):
        reply = cmd_list(gid)

    # ── 我的訂單
    elif text in ("我的訂單", "我的單"):
        reply = cmd_my_orders(gid, uid, lazy_name())

    # ── 結團（團主專用）
    elif text in ("結團",):
        reply = cmd_close(gid, uid)

    # ── 取消團購（團主專用）
    elif text in ("取消團購",):
        reply = cmd_cancel_buy(gid, uid)

    # ── 團購說明（所有人可用）
    elif text in ("團購說明", "操作說明", "說明"):
        reply = HELP_TEXT

    logger.info(f"[msg] reply={'（無）' if reply is None else repr(reply[:40])}")

    if reply:
        if len(reply) > 5000:
            reply = reply[:4950] + "\n\n⋯（訊息過長已截斷，請輸入「列表」查看完整內容）"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            logger.error(f"[reply] 失敗: {e}")


@handler.add(JoinEvent)
def handle_join(event):
    msg = (
        "👋 大家好！我是團購接龍助理\n\n"
        "🛒 團主貼出商品清單即可開團\n"
        "📝 格式：開團 + 商品列表\n\n"
        "下單方式：+品項編號\n"
        "例如：+1 或 +1 2（2份）"
    )
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
    except Exception as e:
        logger.error(f"[Join] 失敗: {e}")


# ══════════════════════════════════════════
# 啟動初始化（模組層級）
# ══════════════════════════════════════════

def _startup():
    """模組載入時：在背景執行緒初始化 DB（避免阻塞 port 綁定）"""

    def _delayed_init():
        import time
        time.sleep(3)
        try:
            init_db()
            logger.info("[startup] 資料庫初始化完成")
        except Exception as e:
            logger.error(f"[startup] 資料庫初始化失敗: {e}")

    t = threading.Thread(target=_delayed_init, daemon=True)
    t.start()
    logger.info("[startup] 背景初始化執行緒已啟動")


_startup()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
