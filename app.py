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
from anthropic import Anthropic

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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
claude_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# ── 品項解析正規表示式
ITEM_NUM_RE = re.compile(r'^\s*[（(]?(\d+)[）)\.\、\)]\s*(.*)')

HELP_TEXT = """📖 團購指令說明
━━━━━━━━━━━━━━
【團主開團】
#開團 + 商品列表（多行貼文）

【下單方式】
#N 數量　　　　　下單品項N指定數量
#N+數量　　　　　同上（如 #1+2）
#N 名字　　　　　幫人下單1份
#N 名字 數量　　 幫人下單指定數量
#N #M 名字　　　 一次下單多品項
品名×數量、...　　用品名批次下單
名字|品名×數量　　幫人批次下單
　（例：#1 2份、水餃×2、小明|水餃×2）

【其他指令】
退出 N　　　　　 取消品項N的訂單
退出 N 名字　　　取消指定人的訂單
列表　　　　　　　查看所有下單狀況
我的訂單　　　　　查看自己的訂單
統計　　　　　　　AI 智能訂單統計
團購說明　　　　　顯示本說明

【AI 智能理解】
直接說想買什麼，AI 會幫你下單
　（例：「我要水餃兩包」「幫小明訂一份魚頭」）

━━━━━━━━━━━━━━
【團主專用】
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


def extract_price(price_info):
    """從品項文字中提取單價（取第一個 N元 的 N，供 AI 統計用）"""
    if not price_info:
        return None
    m = re.search(r'(\d+)\s*元', price_info)
    return int(m.group(1)) if m else None


def extract_price_tiers(price_info):
    """從品項文字中提取所有價格階梯 [(quantity, price), ...]
    例如 '220元／2包420元' → [(1, 220), (2, 420)]
    例如 '一包 200 元 2包 300 元' → [(1, 200), (2, 300)]
    """
    if not price_info:
        return []
    tiers = []
    tier_prices = set()

    # 先掃描整段文字，找出所有 "N包M元" 階梯價（N >= 2）
    for m in re.finditer(r'(\d+)\s*[包份組盒袋]\s*(\d+)\s*元', price_info):
        qty = int(m.group(1))
        price = int(m.group(2))
        if qty >= 2:
            tiers.append((qty, price))
            tier_prices.add(price)

    # 再找所有 "M元" 作為單價候選（排除已被階梯價使用的金額）
    for m in re.finditer(r'(\d+)\s*元', price_info):
        price = int(m.group(1))
        if price not in tier_prices:
            if not any(t[0] == 1 for t in tiers):
                tiers.append((1, price))
            break  # 取第一個作為單價

    return sorted(tiers, key=lambda t: t[0])


def calculate_amount(price_info, quantity):
    """根據價格階梯計算最佳金額
    例如 '220元／2包420元', qty=2 → 420（不是 440）
    """
    tiers = extract_price_tiers(price_info)
    if not tiers:
        return None

    # 貪心法：優先使用大包裝
    tiers_desc = sorted(tiers, key=lambda t: t[0], reverse=True)
    remaining = quantity
    total = 0
    for tier_qty, tier_price in tiers_desc:
        if remaining >= tier_qty:
            count = remaining // tier_qty
            total += count * tier_price
            remaining -= count * tier_qty
    if remaining > 0:
        # 用單價計算剩餘
        unit_tier = next((t for t in tiers if t[0] == 1), None)
        if unit_tier:
            total += remaining * unit_tier[1]
        else:
            # 無單價，用最小階梯的平均價
            smallest = tiers[0]
            total += int(remaining * smallest[1] / smallest[0])
    return total


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

    # 跳過第一行的「#開團」或「開團」字樣
    start = 0
    if lines and re.match(r'^\s*#?開團\s*$', lines[0]):
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

    # 移除開頭的「#開團」或「開團」
    post_text = re.sub(r'^\s*#?開團\s*\n?', '', text, count=1).strip()
    full_text = text  # 保留原始完整貼文

    title, items_list = parse_group_buy(text)

    if not items_list:
        return "⚠️ 無法解析品項，請確認格式：\n#開團\n標題\n1) 品名 價格\n2) 品名 價格"

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
    lines.append("下單方式：#品項編號")
    lines.append("例如：#1 或 #1 2（2份）")

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
    explicit_qty = False  # 是否明確指定數量
    registered_by = None

    if rest:
        # 嘗試判斷：純數字 或 數字+單位(份/個/包/組/盒/袋/條) → 數量
        qty_m = re.match(r'^(\d+)\s*[份個包組盒袋條]?$', rest)
        if qty_m:
            quantity = int(qty_m.group(1))
            explicit_qty = True
        else:
            # 名字 [數量]
            parts = rest.rsplit(None, 1)
            if len(parts) == 2:
                qty_m2 = re.match(r'^(\d+)\s*[份個包組盒袋條]?$', parts[1])
                if qty_m2:
                    order_name = parts[0]
                    quantity = int(qty_m2.group(1))
                    explicit_qty = True
                    registered_by = user_name
                else:
                    order_name = rest
                    registered_by = user_name
            else:
                order_name = rest
                registered_by = user_name

    if quantity < 1:
        return "⚠️ 數量必須大於 0"

    # 查詢是否已有同品項同名的訂單
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, quantity FROM orders WHERE group_buy_id=? AND item_num=? AND user_name=?",
        (buy_id, item_num, order_name),
    )
    existing = c.fetchone()

    if existing:
        if explicit_qty:
            # 明確指定數量 → 設定為該數量
            c.execute("UPDATE orders SET quantity=? WHERE id=?", (quantity, existing[0]))
            total = quantity
        else:
            # 未指定數量（#N）→ 累加 1
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

    if explicit_qty and existing:
        return f"✅ {order_name}【{item_num}】{item_name} → {total} 份"
    else:
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


def cmd_batch_order(group_id, user_id, user_name, text):
    """批次下單：Name|item×qty、item×qty 或 item×qty、item×qty"""
    active = get_active_buy(group_id)
    if not active:
        return None

    buy_id = active[0]
    items = get_items(buy_id)
    if not items:
        return None

    # 判斷是否有代訂人（以 | 分隔）
    if '|' in text:
        parts = text.split('|', 1)
        order_name = parts[0].strip()
        items_text = parts[1].strip()
        registered_by = user_name
    else:
        order_name = user_name or "（未知）"
        items_text = text.strip()
        registered_by = None

    # 解析每個品項：以 、 或 , 分隔
    item_entries = re.split(r'[、,]\s*', items_text)

    results = []
    for entry in item_entries:
        entry = entry.strip()
        if not entry:
            continue

        # 解析 item_name×qty 或 item_name*qty
        m = re.match(r'^(.+?)\s*[×xX*]\s*(\d+)\s*[份個包組盒袋條]?\s*$', entry)
        if not m:
            # 品名直接接數字：麻油猴頭菇2
            m = re.match(r'^(.*[\u4e00-\u9fff\u3400-\u4dbf])\s*(\d+)\s*[份個包組盒袋條]?\s*$', entry)
        if m:
            search_name = m.group(1).strip()
            qty = int(m.group(2))
        else:
            # 沒有數量標記 → 預設 1 份
            search_name = entry.strip()
            qty = 1

        if qty < 1:
            continue

        # 在品項中找匹配（子字串比對）
        matched_item = None
        for item in items:
            item_name = item[3]  # name field
            price_info = item[4] or ""
            if search_name in item_name or search_name in price_info:
                matched_item = item
                break

        if not matched_item:
            results.append(f"⚠️ 找不到品項「{search_name}」")
            continue

        item_num = matched_item[2]

        # 透過 cmd_order 下單
        if registered_by:
            order_text = f"+{item_num} {order_name} {qty}"
        else:
            order_text = f"+{item_num} {qty}"

        order_result = cmd_order(group_id, user_id, user_name, order_text)
        if order_result:
            results.append(order_result)

    return '\n'.join(results) if results else None


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
    total_amount = 0
    has_price = False

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
            item_amount = 0
            for o in item_orders:
                name = o[4] or "（未知）"
                qty = o[5]
                subtotal += qty
                # 階梯價按每個人的數量計算
                person_amount = calculate_amount(price_info, qty)
                if person_amount:
                    lines.append(f"   👤 {name} x{qty}　💰{person_amount}元")
                    item_amount += person_amount
                else:
                    lines.append(f"   👤 {name} x{qty}")
            total_orders += subtotal
            item_amount_str = ""
            if item_amount:
                total_amount += item_amount
                has_price = True
                item_amount_str = f"　💰{item_amount}元"
            lines.append(f"   小計：{subtotal} 份{item_amount_str}")
        else:
            lines.append("   （尚無人下單）")

        lines.append("")  # 空行分隔

    lines.append("────────────────")
    summary = f"共 {total_orders} 份訂單"
    if has_price:
        summary += f"　💰總金額：{total_amount} 元"
    lines.append(summary)

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

    # AI 結單報告（在 status 更新前呼叫，因為更新後 get_active_buy 就找不到了）
    ai_report = ""
    try:
        ai_summary = cmd_ai_summary(group_id)
        if ai_summary and not ai_summary.startswith("⚠️"):
            ai_report = f"\n\n{ai_summary}"
    except Exception as e:
        logger.error(f"[close] AI 報告生成失敗: {e}")

    # 更新狀態
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE group_buys SET status='closed' WHERE id=?", (buy_id,))
    conn.commit()
    conn.close()

    return f"🔒 團購已結團！\n\n{final_list}{ai_report}"


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
# AI 功能（Claude API）
# ══════════════════════════════════════════

def call_claude(prompt_text):
    """呼叫 Claude API 進行分析"""
    if not claude_client:
        return None
    try:
        message = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system="你是團購統計助理，負責彙整訂單資料。回覆必須簡潔清楚，適合在 LINE 群組中顯示。使用繁體中文。不要使用 markdown 格式（不要用 ** 或 # 等符號）。用 emoji 和分隔線讓報告容易閱讀。",
            messages=[
                {"role": "user", "content": prompt_text}
            ]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"[claude] API 呼叫失敗: {e}")
        return None


def is_possibly_order_related(text, items):
    """檢查訊息是否可能跟團購下單有關"""
    # 包含品項名稱中的關鍵字
    for item in items:
        item_name = item[3]  # name field
        if any(keyword in text for keyword in item_name.split() if len(keyword) >= 2):
            return True
    # 包含下單相關的詞彙
    order_keywords = ['要', '買', '訂', '加', '來', '份', '個', '包', '組', '盒',
                      '幫我', '我也', '一樣', '跟', '同上', '加一', '再來', '還要',
                      '取消', '不要', '退', '改', '換']
    return any(kw in text for kw in order_keywords)


def build_nlu_prompt(title, items, orders, user_name, user_text):
    """組合 NLU prompt"""
    # 品項清單
    items_text = ""
    for item in items:
        item_num = item[2]
        name = item[3]
        price_info = item[4] or name
        items_text += f"  {item_num}. {name} ({price_info})\n"

    # 用戶現有訂單
    user_orders_text = "無"
    user_order_list = [o for o in orders if o[4] == user_name]
    if user_order_list:
        user_orders_text = ", ".join(
            f"品項{o[2]} x{o[5]}" for o in user_order_list
        )

    prompt = f"""你是團購接龍助理的語意分析模組。

目前團購「{title}」的品項列表：
{items_text}
用戶「{user_name}」目前已下單：{user_orders_text}

用戶發了這則訊息：「{user_text}」

請判斷用戶的意圖，回覆嚴格的 JSON 格式（不要加其他文字）：

情況1 - 明確要下單：
{{"action": "order", "item_num": 品項編號, "quantity": 數量, "for_name": "下單人名字或null"}}

情況2 - 明確要取消：
{{"action": "cancel", "item_num": 品項編號, "for_name": "取消人名字或null"}}

情況3 - 明確要修改數量：
{{"action": "update", "item_num": 品項編號, "quantity": 新數量, "for_name": "修改人名字或null"}}

情況4 - 意圖跟團購有關但不明確，需要釐清：
{{"action": "clarify", "message": "你的釐清問題（用繁體中文，簡短友善）"}}

情況5 - 跟團購無關的閒聊：
{{"action": "ignore"}}

注意：
- for_name 預設為 null（代表用戶自己），只有明確幫別人訂才填名字
- quantity 預設為 1
- 如果用戶說「我也要」「跟上面一樣」但無法判斷是哪個品項，用 clarify
- 如果用戶說了品項名稱但品項列表中有多個類似的，用 clarify 列出選項
- 釐清問題要簡短，列出可能的選項讓用戶選擇
- 只回覆 JSON，不要加任何其他文字"""

    return prompt


def cmd_nlu_order(group_id, user_id, user_name, text):
    """用 Claude 理解自然語言下單意圖"""
    if not claude_client:
        return None

    active = get_active_buy(group_id)
    if not active:
        return None

    buy_id = active[0]
    title = active[2]
    items = get_items(buy_id)
    orders = get_orders(buy_id)

    # 預先過濾
    if not is_possibly_order_related(text, items):
        return None

    # 呼叫 Claude
    prompt = build_nlu_prompt(title, items, orders, user_name, text)
    try:
        message = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system="你是團購語意分析模組。只回覆 JSON，不要加其他文字。",
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = message.content[0].text.strip()

        # 解析 JSON（處理可能的 markdown code block）
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(result_text)

    except Exception as e:
        logger.error(f"[nlu] Claude 呼叫或解析失敗: {e}")
        return None  # 失敗就靜默，不影響正常使用

    action = result.get("action")

    if action == "ignore":
        return None

    elif action == "clarify":
        return f"🤔 {result.get('message', '請問你想訂什麼呢？')}"

    elif action == "order":
        item_num = result.get("item_num")
        quantity = result.get("quantity", 1)
        for_name = result.get("for_name")

        # 驗證品項存在
        item_name = get_item_name(buy_id, item_num)
        if not item_name:
            return f"🤔 找不到品項【{item_num}】，請確認編號。\n輸入「列表」查看所有品項。"

        # 組合標準下單指令，複用現有 cmd_order
        if for_name:
            order_text = f"+{item_num} {for_name} {quantity}"
        else:
            order_text = f"+{item_num} {quantity}"

        order_result = cmd_order(group_id, user_id, user_name, order_text)
        return f"🤖 AI 理解：{order_result}"

    elif action == "cancel":
        item_num = result.get("item_num")
        for_name = result.get("for_name")

        if for_name:
            cancel_text = f"退出 {item_num} {for_name}"
        else:
            cancel_text = f"退出 {item_num}"

        cancel_result = cmd_cancel_order(group_id, user_id, user_name, cancel_text)
        return f"🤖 AI 理解：{cancel_result}"

    elif action == "update":
        item_num = result.get("item_num")
        quantity = result.get("quantity", 1)
        for_name = result.get("for_name")

        item_name = get_item_name(buy_id, item_num)
        if not item_name:
            return f"🤔 找不到品項【{item_num}】，請確認編號。"

        if for_name:
            order_text = f"+{item_num} {for_name} {quantity}"
        else:
            order_text = f"+{item_num} {quantity}"

        order_result = cmd_order(group_id, user_id, user_name, order_text)
        return f"🤖 AI 理解（修改數量）：{order_result}"

    return None


def cmd_ai_summary(group_id):
    """AI 智能訂單統計"""
    if not claude_client:
        return "⚠️ AI 功能未啟用（ANTHROPIC_API_KEY 未設定）"

    active = get_active_buy(group_id)
    if not active:
        return "目前沒有進行中的團購。"

    buy_id = active[0]
    title = active[2]
    items = get_items(buy_id)
    orders = get_orders(buy_id)

    if not orders:
        return f"📋 {title}\n目前還沒有人下單。"

    # 組合訂單資料
    items_text = ""
    for item in items:
        price = extract_price(item[4])
        price_str = f" - 單價 {price} 元" if price else ""
        items_text += f"  {item[2]}. {item[3]}{price_str}\n"

    orders_text = ""
    for o in orders:
        item_name = get_item_name(buy_id, o[2]) or f"品項{o[2]}"
        orders_text += f"  - {o[4]}: {item_name}(品項{o[2]}) x{o[5]}\n"

    prompt = f"""以下是團購「{title}」的訂單資料，請做統計分析：

【品項列表】
{items_text}
【訂單明細】
{orders_text}
請產出以下報告：
1. 📊 品項統計：每個品項的總訂購數量和金額小計
2. 👥 人員統計：每個人買了哪些品項、各多少份、應付總金額
3. 💰 總計：總訂購份數和總金額

格式要求：簡潔清楚，適合 LINE 群組顯示，用 emoji 和分隔線排版。"""

    result = call_claude(prompt)
    if result:
        return f"🤖 AI 統計分析\n━━━━━━━━━━━━━━\n{result}"
    else:
        # fallback：回傳現有的列表功能
        return cmd_list(group_id)


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
    if re.match(r'^\s*#?開團', text) and '\n' in text:
        reply = cmd_open(gid, uid, lazy_name(), text)

    # ── #N+M 格式（品項N，數量M，如 #1+2 = 品項1訂2份）
    elif re.match(r'^[+#]\d+\+\d+\s*[份個包組盒袋條]?\s*$', text):
        m = re.match(r'^[+#](\d+)\+(\d+)', text)
        reply = cmd_order(gid, uid, lazy_name(), f"+{m.group(1)} {m.group(2)}")

    # ── 多品項下單（#1 #3 #5 名字，需有空格分隔）
    elif len(re.findall(r'(?:^|\s)[+#]\d+', text)) > 1:
        # 統一 # 為 + 格式
        reply = cmd_order_multi(gid, uid, lazy_name(), text.replace('#', '+'))

    # ── 單品項下單（#N 數量 / #N 名字 等，#N 後面必須有內容）
    elif re.match(r'^[+#]\d+\s+\S', text):
        reply = cmd_order(gid, uid, lazy_name(), text.replace('#', '+', 1))

    # ── 單獨 #N（無數量無名字）→ 不動作，提示補充數量
    elif re.match(r'^[+#]\d+\s*$', text):
        active = get_active_buy(gid)
        if active:
            m = re.match(r'^[+#](\d+)', text)
            item_num = int(m.group(1))
            item_name = get_item_name(active[0], item_num)
            if item_name:
                reply = f"📝【{item_num}】{item_name}\n請輸入數量，例如：#{item_num} 1份"

    # ── 數字點格式下單（1. 2 / 1. 小明，1. 後面必須有內容）
    elif re.match(r'^\d+[\.．]\s+\S', text):
        m_dot = re.match(r'^(\d+)[\.．]\s*(.*)', text)
        rest = m_dot.group(2).strip() if m_dot.group(2) else ""
        reply = cmd_order(gid, uid, lazy_name(), f"+{m_dot.group(1)} {rest}".strip())

    # ── 單獨 N.（無內容）→ 不動作，提示補充數量
    elif re.match(r'^\d+[\.．]\s*$', text):
        active = get_active_buy(gid)
        if active:
            m = re.match(r'^(\d+)', text)
            item_num = int(m.group(1))
            item_name = get_item_name(active[0], item_num)
            if item_name:
                reply = f"📝【{item_num}】{item_name}\n請輸入數量，例如：#{item_num} 1份"

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

    # ── AI 統計
    elif text in ("統計", "AI統計", "智能統計"):
        reply = cmd_ai_summary(gid)

    # ── 團購說明（所有人可用）
    elif text in ("團購說明", "操作說明", "說明"):
        reply = HELP_TEXT

    # ── 批次下單（品名×數量、品名數量 或 Name|品名數量）
    elif re.search(r'[\u4e00-\u9fff\u3400-\u4dbf）\)]\s*[×xX*]\s*\d', text) or \
         (('|' in text or '、' in text) and re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]\d', text)) or \
         re.match(r'^[\u4e00-\u9fff\u3400-\u4dbf]{2,}\s*\d+\s*[份個包組盒袋條]?\s*$', text):
        reply = cmd_batch_order(gid, uid, lazy_name(), text)

    # ── AI 自然語言理解（放在所有指令判斷的最後）
    if reply is None and len(text) >= 2 and len(text) <= 200:
        if not re.match(r'^[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\s]+$', text):
            nlu_reply = cmd_nlu_order(gid, uid, lazy_name(), text)
            if nlu_reply:
                reply = nlu_reply

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
        "📝 格式：#開團 + 商品列表\n\n"
        "下單方式：#品項編號\n"
        "例如：#1 或 #1 2（2份）"
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
