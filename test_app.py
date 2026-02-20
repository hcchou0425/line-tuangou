"""
LINE 團購接龍機器人 — 單元測試
使用 tmpfile SQLite，mock 掉 LINE API 和 Claude API。
"""

import os
import re
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import app


# ══════════════════════════════════════════
# Fixtures & Helpers
# ══════════════════════════════════════════

@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """每個 test 都用全新的 SQLite 檔案"""
    db_file = str(tmp_path / "test.db")
    app.DB_PATH = db_file
    app.init_db()
    app.claude_client = None  # 預設關閉 AI
    yield
    # cleanup
    try:
        os.unlink(db_file)
    except OSError:
        pass


GID = "test_group"
UID = "user_001"
UNAME = "測試者"

UID2 = "user_002"
UNAME2 = "小明"


def open_buy(group_id=GID, user_id=UID, text=None):
    """快速開團 helper"""
    if text is None:
        text = "#開團\n今日美食\n1) 水餃 50元\n2) 蛋餃 60元\n3) 魚餃 70元"
    return app.cmd_open(group_id, user_id, UNAME, text)


def open_buy_limited(group_id=GID, user_id=UID, limit=5):
    """開一個限量團購"""
    text = f"#開團 限量{limit}份\n限量美食\n1) 水餃 50元\n2) 蛋餃 60元"
    return app.cmd_open(group_id, user_id, UNAME, text)


# ══════════════════════════════════════════
# 1. 開團 (cmd_open)
# ══════════════════════════════════════════

class TestCmdOpen:

    def test_basic_open(self):
        result = open_buy()
        assert "開團成功" in result
        assert "今日美食" in result
        assert "水餃" in result
        assert "蛋餃" in result
        assert "魚餃" in result

    def test_parse_items(self):
        open_buy()
        buys = app.get_active_buys(GID)
        assert len(buys) == 1
        items = app.get_items(buys[0][0])
        assert len(items) == 3
        assert items[0][3] == "水餃 50元"  # name
        assert items[1][3] == "蛋餃 60元"
        assert items[2][3] == "魚餃 70元"

    def test_limited_open(self):
        result = open_buy_limited(limit=5)
        assert "限量" in result
        assert "5" in result
        buys = app.get_active_buys(GID)
        assert buys[0][9] == 5  # max_quantity

    def test_multi_buy_labels(self):
        """同群組開第二團 → 顯示 [團購N] + 目前共有 N 個團購進行中"""
        open_buy()
        result2 = app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元\n2) 排骨飯 90元")
        assert "[團購2]" in result2
        assert "目前共有 2 個團購進行中" in result2

    def test_buy_num_increment(self):
        """buy_num 遞增正確"""
        open_buy()
        open_buy()
        buys = app.get_active_buys(GID)
        nums = sorted([b[8] for b in buys])
        assert nums == [1, 2]

    def test_open_no_items_error(self):
        result = app.cmd_open(GID, UID, UNAME, "#開團\n沒有品項的文字")
        assert "無法解析品項" in result


# ══════════════════════════════════════════
# 2. 下單 (cmd_order)
# ══════════════════════════════════════════

class TestCmdOrder:

    def test_order_with_quantity(self):
        """+1 2 → 品項1訂2份"""
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+1 2")
        assert "水餃" in result
        assert "2" in result

    def test_order_for_someone(self):
        """+1 小明 → 幫小明訂1份"""
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+1 小明")
        assert "小明" in result
        assert "水餃" in result

    def test_order_for_someone_with_qty(self):
        """+1 小明 3 → 幫小明訂3份"""
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+1 小明 3")
        assert "小明" in result
        assert "3" in result

    def test_accumulate(self):
        """重複 +1 → 數量+1"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1")
        result = app.cmd_order(GID, UID, UNAME, "+1")
        assert "共 2 份" in result

    def test_explicit_qty_override(self):
        """+1 5 後 +1 3 → 設為3份"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 5")
        result = app.cmd_order(GID, UID, UNAME, "+1 3")
        assert "→ 3 份" in result

    def test_item_not_exist(self):
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+99")
        assert "沒有品項" in result

    def test_no_buy_silent(self):
        """無團購 → None（靜默）"""
        result = app.cmd_order(GID, UID, UNAME, "+1 2")
        assert result is None

    def test_order_plain_plus_n(self):
        """+1 無數量 → 預設1份"""
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+1")
        assert "水餃" in result
        assert "1" in result


# ══════════════════════════════════════════
# 3. 多品項下單 (cmd_order_multi)
# ══════════════════════════════════════════

class TestCmdOrderMulti:

    def test_multi_items(self):
        """+1 +2 +3 → 三品項各1份"""
        open_buy()
        result = app.cmd_order_multi(GID, UID, UNAME, "+1 +2 +3")
        assert "水餃" in result
        assert "蛋餃" in result
        assert "魚餃" in result

    def test_multi_items_for_someone(self):
        """+1 +2 小明 → 幫小明訂"""
        open_buy()
        result = app.cmd_order_multi(GID, UID, UNAME, "+1 +2 小明")
        assert "小明" in result
        assert "水餃" in result
        assert "蛋餃" in result


# ══════════════════════════════════════════
# 4. 批次下單 (cmd_batch_order)
# ══════════════════════════════════════════

class TestCmdBatchOrder:

    def test_name_times_qty(self):
        """水餃×2"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃×2")
        assert "水餃" in result
        assert "2" in result

    def test_name_plus_qty(self):
        """水餃+1"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃+1")
        assert "水餃" in result

    def test_name_star_qty(self):
        """水餃*2"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃*2")
        assert "水餃" in result
        assert "2" in result

    def test_name_direct_digit(self):
        """水餃2 → 品名直接接數字"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃2")
        assert "水餃" in result

    def test_multi_items_separator(self):
        """水餃×2、蛋餃×3"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃×2、蛋餃×3")
        assert "水餃" in result
        assert "蛋餃" in result

    def test_proxy_pipe(self):
        """小明|水餃×2"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "小明|水餃×2")
        assert "小明" in result
        assert "水餃" in result

    def test_proxy_newline(self):
        """小明\\n水餃+1 → 換行代訂"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "小明\n水餃+1")
        assert "小明" in result
        assert "水餃" in result

    def test_multi_newline_no_proxy(self):
        """水餃+1\\n蛋餃+2 → 換行多品項（非代訂）"""
        open_buy()
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃+1\n蛋餃+2")
        assert "水餃" in result
        assert "蛋餃" in result
        # 應該是自己下單，不是代訂
        assert UNAME in result

    def test_cross_buy_match(self):
        """跨團購匹配：品名在不同團購中"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元\n2) 排骨飯 90元")
        result = app.cmd_batch_order(GID, UID, UNAME, "滷肉飯×1")
        assert "滷肉飯" in result

    def test_no_buy_returns_none(self):
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃×2")
        assert result is None

    def test_batch_no_premature_auto_close(self):
        """限量團購批次下單：自動結團訊息只應出現一次
        例：限量5份，水餃×2、蛋餃×3 → 兩項都成功，自動結團一次
        """
        open_buy_limited(limit=5)
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃×2、蛋餃×3")
        assert "水餃" in result
        assert "蛋餃" in result
        assert result.count("✅") == 2
        assert result.count("自動結團") == 1

    def test_batch_first_item_fills_limit(self):
        """限量3份，水餃×3、蛋餃×2 → 第一項就額滿
        自動結團訊息應只出現一次，不應重複顯示整個列表
        """
        open_buy_limited(limit=3)
        result = app.cmd_batch_order(GID, UID, UNAME, "水餃×3、蛋餃×2")
        assert "水餃" in result
        assert "蛋餃" in result
        assert result.count("✅") == 2
        # 自動結團訊息只出現一次
        assert result.count("自動結團") == 1
        assert result.count("🔒") == 1

    def test_batch_limited_all_items_ordered(self):
        """限量團購批次下單後，DB 中應有所有品項的訂單"""
        open_buy_limited(limit=10)
        app.cmd_batch_order(GID, UID, UNAME, "水餃×3、蛋餃×4")
        conn = sqlite3.connect(app.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT item_num, quantity FROM orders ORDER BY item_num")
        orders = c.fetchall()
        conn.close()
        assert len(orders) == 2
        assert orders[0] == (1, 3)  # 水餃×3
        assert orders[1] == (2, 4)  # 蛋餃×4


# ══════════════════════════════════════════
# 5. 退出 (cmd_cancel_order)
# ══════════════════════════════════════════

class TestCmdCancelOrder:

    def test_cancel_own(self):
        """退出 1 → 取消自己的品項1"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 2")
        result = app.cmd_cancel_order(GID, UID, UNAME, "退出 1")
        assert "已取消" in result
        assert "水餃" in result

    def test_cancel_for_someone(self):
        """退出 1 小明 → 取消小明的品項1"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 小明 2")
        result = app.cmd_cancel_order(GID, UID, UNAME, "退出 1 小明")
        assert "已取消" in result
        assert "小明" in result

    def test_cancel_not_found(self):
        """品項不存在 → 錯誤"""
        open_buy()
        result = app.cmd_cancel_order(GID, UID, UNAME, "退出 99")
        assert "沒有品項" in result

    def test_cancel_no_order(self):
        """有品項但沒下過單"""
        open_buy()
        result = app.cmd_cancel_order(GID, UID, UNAME, "退出 1")
        assert "沒有" in result

    def test_cancel_multi_buy_resolve(self):
        """多團購：自動找到正確的團購"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n4) 滷肉飯 80元\n5) 排骨飯 90元")
        app.cmd_order(GID, UID, UNAME, "+4 2")
        result = app.cmd_cancel_order(GID, UID, UNAME, "退出 4")
        assert "已取消" in result
        assert "滷肉飯" in result


# ══════════════════════════════════════════
# 6. 列表 (cmd_list)
# ══════════════════════════════════════════

class TestCmdList:

    def test_single_buy_list(self):
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 2")
        result = app.cmd_list(GID)
        assert "水餃" in result
        assert UNAME in result
        assert "x2" in result

    def test_list_specified_buy(self):
        """列表 2 → 指定團購"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_list(GID, buy_num=2)
        assert "第二團" in result
        assert "[團購2]" in result

    def test_list_multi_buy(self):
        """多團購：顯示所有，帶 [團購N] 標籤"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_list(GID)
        assert "[團購1]" in result
        assert "[團購2]" in result

    def test_list_no_buy(self):
        result = app.cmd_list(GID)
        assert "沒有進行中的團購" in result

    def test_list_nonexistent_buy_num(self):
        open_buy()
        result = app.cmd_list(GID, buy_num=99)
        assert "沒有團購99" in result


# ══════════════════════════════════════════
# 7. 我的訂單 (cmd_my_orders)
# ══════════════════════════════════════════

class TestCmdMyOrders:

    def test_my_orders_basic(self):
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 2")
        result = app.cmd_my_orders(GID, UID, UNAME)
        assert UNAME in result
        assert "水餃" in result

    def test_my_orders_with_proxy(self):
        """含代訂"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 2")
        app.cmd_order(GID, UID, UNAME, "+2 小明 1")
        result = app.cmd_my_orders(GID, UID, UNAME)
        assert "代訂" in result
        assert "小明" in result

    def test_my_orders_multi_buy(self):
        """跨多團購"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n4) 滷肉飯 80元")
        app.cmd_order(GID, UID, UNAME, "+1 1")
        app.cmd_order(GID, UID, UNAME, "+4 1")
        result = app.cmd_my_orders(GID, UID, UNAME)
        assert "水餃" in result
        assert "滷肉飯" in result

    def test_my_orders_none(self):
        open_buy()
        result = app.cmd_my_orders(GID, UID, UNAME)
        assert "沒有下單" in result

    def test_my_orders_no_buy(self):
        result = app.cmd_my_orders(GID, UID, UNAME)
        assert "沒有進行中的團購" in result


# ══════════════════════════════════════════
# 8. 結團 (cmd_close)
# ══════════════════════════════════════════

class TestCmdClose:

    def test_close_by_creator(self):
        open_buy()
        result = app.cmd_close(GID, UID)
        assert "結團" in result

    def test_close_not_creator(self):
        open_buy()
        result = app.cmd_close(GID, "other_user")
        assert "只有團主" in result

    def test_close_specific_buy(self):
        """結團2 / 結團 2 → 指定團購"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_close(GID, UID, buy_num=2)
        assert "結團" in result
        # 第一團應該還在
        buys = app.get_active_buys(GID)
        assert len(buys) == 1
        assert buys[0][8] == 1  # buy_num == 1

    def test_close_multi_no_specify(self):
        """多團購不指定 → 提示選擇"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_close(GID, UID)
        assert "多個團購" in result
        assert "結團 1" in result

    def test_close_no_buy(self):
        result = app.cmd_close(GID, UID)
        assert "沒有進行中的團購" in result


# ══════════════════════════════════════════
# 9. 取消團購 (cmd_cancel_buy)
# ══════════════════════════════════════════

class TestCmdCancelBuy:

    def test_cancel_by_creator(self):
        open_buy()
        result = app.cmd_cancel_buy(GID, UID)
        assert "已取消" in result
        buys = app.get_active_buys(GID)
        assert len(buys) == 0

    def test_cancel_not_creator(self):
        open_buy()
        result = app.cmd_cancel_buy(GID, "other_user")
        assert "只有團主" in result

    def test_cancel_specific_buy(self):
        """取消團購2 / 取消團購 2 → 指定團購"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_cancel_buy(GID, UID, buy_num=2)
        assert "已取消" in result
        assert "第二團" in result
        buys = app.get_active_buys(GID)
        assert len(buys) == 1

    def test_cancel_multi_no_specify(self):
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        result = app.cmd_cancel_buy(GID, UID)
        assert "多個團購" in result

    def test_cancel_no_buy(self):
        result = app.cmd_cancel_buy(GID, UID)
        assert "沒有進行中的團購" in result


# ══════════════════════════════════════════
# 10. 自動結團 (check_auto_close)
# ══════════════════════════════════════════

class TestAutoClose:

    def test_auto_close_on_limit(self):
        """限量5份 → 下滿5份 → 自動結團"""
        open_buy_limited(limit=5)
        buys = app.get_active_buys(GID)
        buy_id = buys[0][0]

        app.cmd_order(GID, UID, UNAME, "+1 3")
        app.cmd_order(GID, UID, UNAME, "+2 2")

        # 確認自動結團
        buys = app.get_active_buys(GID)
        assert len(buys) == 0  # 已結團

    def test_no_auto_close_without_limit(self):
        """無限量 → 不自動結團"""
        open_buy()
        buys = app.get_active_buys(GID)
        buy_id = buys[0][0]

        app.cmd_order(GID, UID, UNAME, "+1 100")
        result = app.check_auto_close(buy_id, GID)
        assert result is None

        buys = app.get_active_buys(GID)
        assert len(buys) == 1

    def test_auto_close_progress_message(self):
        """未達限量時顯示進度"""
        open_buy_limited(limit=5)
        buys = app.get_active_buys(GID)
        buy_id = buys[0][0]

        app.cmd_order(GID, UID, UNAME, "+1 2")
        result = app.check_auto_close(buy_id, GID)
        assert "剩餘" in result


# ══════════════════════════════════════════
# 11. 多團購解析 (resolve_buy_for_item)
# ══════════════════════════════════════════

class TestResolveBuyForItem:

    def test_unique_match(self):
        """唯一匹配 → 回傳 buy"""
        open_buy()
        buy, err = app.resolve_buy_for_item(GID, 1)
        assert buy is not None
        assert err is None

    def test_ambiguous_match(self):
        """多團購都有品項1 → 歧義錯誤"""
        open_buy()
        app.cmd_open(GID, UID, UNAME, "#開團\n第二團\n1) 滷肉飯 80元")
        buy, err = app.resolve_buy_for_item(GID, 1)
        assert buy is None
        assert "多個團購" in err

    def test_no_match(self):
        """無匹配 → 錯誤"""
        open_buy()
        buy, err = app.resolve_buy_for_item(GID, 99)
        assert buy is None
        assert "沒有品項" in err

    def test_no_buy(self):
        """無團購 → (None, None)"""
        buy, err = app.resolve_buy_for_item(GID, 1)
        assert buy is None
        assert err is None


# ══════════════════════════════════════════
# 12. handle_message 路由
# ══════════════════════════════════════════

class TestHandleMessageRouting:
    """測試 handle_message 的指令路由邏輯。
    Mock 掉 LINE API 的 reply_message 和 get_group_member_profile。
    """

    def _make_event(self, text):
        """建立 mock MessageEvent"""
        event = MagicMock()
        event.message.text = text
        event.source.type = "group"
        event.source.group_id = GID
        event.source.user_id = UID
        event.reply_token = "test_token"
        return event

    def _handle(self, text):
        """呼叫 handle_message 並回傳 reply 的文字"""
        event = self._make_event(text)
        with patch.object(app.line_bot_api, 'reply_message') as mock_reply, \
             patch.object(app.line_bot_api, 'get_group_member_profile') as mock_profile:
            mock_profile.return_value = MagicMock(display_name=UNAME)
            app.handle_message(event)
            if mock_reply.called:
                args = mock_reply.call_args
                return args[0][1].text if args[0] else args[1].get('messages', [MagicMock(text="")])[0].text
        return None

    def test_open_buy_routing(self):
        result = self._handle("#開團\n今日美食\n1) 水餃 50元\n2) 蛋餃 60元")
        assert result is not None
        assert "開團成功" in result

    def test_order_routing(self):
        open_buy()
        result = self._handle("#1 2")
        assert result is not None
        assert "水餃" in result

    def test_hash_plus_format(self):
        """#1+2 → cmd_order（品項1，2份）"""
        open_buy()
        result = self._handle("#1+2")
        assert result is not None
        assert "水餃" in result
        assert "2" in result

    def test_cancel_buy_no_space(self):
        """取消團購2（無空格）→ cmd_cancel_buy"""
        open_buy()
        open_buy()
        result = self._handle("取消團購2")
        assert result is not None
        # 應該觸發 cmd_cancel_buy，因為是別人不是團主可能會報錯
        # 但至少說明路由正確
        assert "取消" in result or "團主" in result or "已取消" in result

    def test_close_no_space(self):
        """結團2（無空格）→ cmd_close"""
        open_buy()
        open_buy()
        result = self._handle("結團2")
        assert result is not None

    def test_list_no_space(self):
        """列表2（無空格）→ cmd_list"""
        open_buy()
        open_buy()
        result = self._handle("列表2")
        assert result is not None

    def test_list_routing(self):
        open_buy()
        result = self._handle("列表")
        assert result is not None
        assert "水餃" in result

    def test_my_orders_routing(self):
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 1")
        result = self._handle("我的訂單")
        assert result is not None
        assert "水餃" in result

    def test_cancel_order_routing(self):
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 1")
        result = self._handle("退出 1")
        assert result is not None
        assert "已取消" in result

    def test_close_routing(self):
        open_buy()
        result = self._handle("結團")
        assert result is not None
        assert "結團" in result

    def test_cancel_buy_routing(self):
        open_buy()
        result = self._handle("取消團購")
        assert result is not None

    def test_help_routing(self):
        result = self._handle("團購說明")
        assert result is not None
        assert "指令說明" in result

    def test_batch_order_routing(self):
        """品名批次下單路由正確"""
        open_buy()
        result = self._handle("水餃×2")
        assert result is not None
        assert "水餃" in result

    def test_multi_order_routing(self):
        """多品項下單路由"""
        open_buy()
        result = self._handle("#1 #2 #3")
        assert result is not None

    def test_plus_format_order(self):
        """+1 2 → 下單"""
        open_buy()
        result = self._handle("+1 2")
        assert result is not None
        assert "水餃" in result

    def test_dot_format_order(self):
        """1. 2 → 數字點格式下單"""
        open_buy()
        result = self._handle("1. 2")
        assert result is not None
        assert "水餃" in result

    def test_solo_hash_prompt(self):
        """單獨 #1 → 提示輸入數量"""
        open_buy()
        result = self._handle("#1")
        assert result is not None
        assert "請輸入數量" in result or "水餃" in result


# ══════════════════════════════════════════
# 13. Helper 函式
# ══════════════════════════════════════════

class TestHelpers:

    def test_normalize_fullwidth(self):
        """全形→半形"""
        assert app.normalize("＋１２３") == "+123"
        assert app.normalize("＃１") == "#1"
        assert app.normalize("ＡＢＣ") == "ABC"

    def test_normalize_fullwidth_space(self):
        assert app.normalize("　") == " "

    def test_normalize_mixed(self):
        assert app.normalize("＋1 ２份") == "+1 2份"

    def test_parse_group_buy_basic(self):
        title, items = app.parse_group_buy("#開團\n今日美食\n1) 水餃 50元\n2) 蛋餃 60元")
        assert title == "今日美食"
        assert len(items) == 2
        assert items[0][0] == 1  # item_num
        assert "水餃" in items[0][1]

    def test_parse_group_buy_no_title(self):
        title, items = app.parse_group_buy("#開團\n1) 水餃 50元\n2) 蛋餃 60元")
        assert title == "團購"  # 預設標題
        assert len(items) == 2

    def test_parse_group_buy_no_items(self):
        title, items = app.parse_group_buy("#開團\n什麼都沒有")
        assert title is None
        assert items == []

    def test_extract_price(self):
        assert app.extract_price("水餃 50元") == 50
        assert app.extract_price("免費") is None
        assert app.extract_price(None) is None

    def test_extract_price_tiers(self):
        tiers = app.extract_price_tiers("220元／2包420元")
        assert len(tiers) == 2
        assert (1, 220) in tiers
        assert (2, 420) in tiers

    def test_calculate_amount_single(self):
        assert app.calculate_amount("50元", 3) == 150

    def test_calculate_amount_tiered(self):
        """220元／2包420元, qty=2 → 420（不是 440）"""
        assert app.calculate_amount("220元／2包420元", 2) == 420

    def test_calculate_amount_no_price(self):
        assert app.calculate_amount("無價格", 5) is None

    def test_get_active_buy_single(self):
        """get_active_buy 向下相容行為"""
        open_buy()
        buy = app.get_active_buy(GID)
        assert buy is not None
        assert buy[2] == "今日美食"

    def test_get_active_buy_multi_returns_none(self):
        """多個 buy + buy_num=None → None"""
        open_buy()
        open_buy()
        buy = app.get_active_buy(GID)
        assert buy is None

    def test_get_active_buy_specific(self):
        """指定 buy_num → 回傳該筆"""
        open_buy()
        open_buy()
        buy = app.get_active_buy(GID, buy_num=1)
        assert buy is not None
        assert buy[8] == 1

    def test_get_active_buy_no_buy(self):
        buy = app.get_active_buy(GID)
        assert buy is None


# ══════════════════════════════════════════
# 14. Edge cases
# ══════════════════════════════════════════

class TestEdgeCases:

    def test_fullwidth_order(self):
        """全形 ＋１ ２ → 正常下單"""
        open_buy()
        # handle_message 會先 normalize
        event = MagicMock()
        event.message.text = "＋１　２"
        event.source.type = "group"
        event.source.group_id = GID
        event.source.user_id = UID
        event.reply_token = "test_token"
        with patch.object(app.line_bot_api, 'reply_message') as mock_reply, \
             patch.object(app.line_bot_api, 'get_group_member_profile') as mock_profile:
            mock_profile.return_value = MagicMock(display_name=UNAME)
            app.handle_message(event)
            if mock_reply.called:
                reply_text = mock_reply.call_args[0][1].text
                assert "水餃" in reply_text

    def test_quantity_with_unit(self):
        """數量帶單位：+1 2份"""
        open_buy()
        result = app.cmd_order(GID, UID, UNAME, "+1 2份")
        assert "2" in result

    def test_open_different_groups(self):
        """不同群組開團互不影響"""
        open_buy(group_id="group_a")
        open_buy(group_id="group_b")
        buys_a = app.get_active_buys("group_a")
        buys_b = app.get_active_buys("group_b")
        assert len(buys_a) == 1
        assert len(buys_b) == 1

    def test_close_then_list(self):
        """結團後列表不顯示"""
        open_buy()
        app.cmd_close(GID, UID)
        result = app.cmd_list(GID)
        assert "沒有進行中的團購" in result

    def test_cancel_then_list(self):
        """取消團購後資料清除"""
        open_buy()
        app.cmd_order(GID, UID, UNAME, "+1 2")
        app.cmd_cancel_buy(GID, UID)
        result = app.cmd_list(GID)
        assert "沒有進行中的團購" in result
        # 確認 orders 和 items 也被刪除
        conn = sqlite3.connect(app.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM orders")
        assert c.fetchone()[0] == 0
        c.execute("SELECT COUNT(*) FROM items")
        assert c.fetchone()[0] == 0
        conn.close()
