# LINE 團購接龍助理

在**現有 LINE 群組**中幫忙管理團購下單、統計數量、代訂等事項。

> 不需要新建群組，也不需要其他人加入新群組。
> 只要把 Bot 加入你的群組，直接在原有對話中使用即可。

---

## 如何把 Bot 加入現有群組

1. 開啟你的 LINE 群組
2. 點右上角選單 → **邀請**
3. 搜尋 Bot 的名稱（你在 LINE Developers 設定的名稱）
4. 邀請加入即可

Bot 加入後會發送歡迎訊息，之後等有人輸入指令才會回應。

---

## 使用方式（群組對話中直接輸入）

### 開團（團主）

在群組中貼出以下格式的多行訊息：

```
#開團

農曆過年預購 不是現在出貨喔
1) 水餃（50顆裝）220元／2包420元
(2)砂鍋魚頭火鍋
一包230元
(3)台南師姊三絲捲 , 一組2條150元
```

Bot 會自動解析品項並顯示下單提示。

### 下單指令

| 指令 | 說明 | 範例 |
|------|------|------|
| `#N` | 下單品項 N，1 份 | `#1` |
| `#N 數量` | 下單品項 N，指定數量 | `#1 2` |
| `#N 名字` | 幫人下單 1 份 | `#1 小明` |
| `#N 名字 數量` | 幫人下單指定數量 | `#1 小明 2` |
| `#N #M #K 名字` | 一次下單多品項 | `#1 #3 #5 小明` |
| `退出 N` | 取消品項 N 的訂單 | `退出 1` |
| `退出 N 名字` | 取消指定人的訂單 | `退出 1 小明` |
| `列表` | 查看所有下單狀況 | |
| `我的訂單` | 查看自己的訂單（含代訂）| |
| `團購說明` | 顯示指令說明 | |

> `+N` 和 `N.` 格式也可以使用（如 `+1` 或 `1.`）

### 團主專用

| 指令 | 說明 |
|------|------|
| `結團` | 封存最終訂單，顯示完整列表 |
| `取消團購` | 刪除所有資料 |

---

## 下單規則

- **累加制**：重複下單同品項會累加數量
  - 已有 2 份，再 `#1` → 變 3 份
- **代訂**：`#1 小明` 幫小明下單，記錄代訂者
- **退出**：`退出 N` 移除該品項的全部訂單
- 確認訊息顯示目前總數：`✅ 小明【1】水餃 +1份（共 3 份）`

---

## 對話範例

```
小陳：#開團
     農曆過年預購
     1) 水餃（50顆裝）220元
     2) 砂鍋魚頭火鍋 230元

Bot：🛒 開團成功！農曆過年預購
     ────────────────
     【1】水餃（50顆裝）220元
     【2】砂鍋魚頭火鍋 230元
     ────────────────
     下單方式：#品項編號
     例如：#1 或 #1 2（2份）

小明：#1
Bot：✅ 小明【1】水餃（50顆裝）220元 +1份（共 1 份）

小華：#1 2
Bot：✅ 小華【1】水餃（50顆裝）220元 +2份（共 2 份）

小明：#2 小美
Bot：✅ 小美【2】砂鍋魚頭火鍋 230元 +1份（共 1 份）

小陳：列表
Bot：🛒 農曆過年預購
     ────────────────
     【1】水餃（50顆裝）220元
        👤 小明 x1
        👤 小華 x2
        小計：3 份
     【2】砂鍋魚頭火鍋 230元
        👤 小美 x1
        小計：1 份
     ────────────────
     共 4 份訂單

小陳：結團
Bot：🔒 團購已結團！（顯示完整最終列表）
```

---

## 安裝與部署

### 1. 建立 LINE Bot

1. 前往 [LINE Developers Console](https://developers.line.biz/)
2. 建立 Provider → 建立 **Messaging API Channel**
3. 取得：
   - **Channel Secret**（Basic settings 頁面）
   - **Channel Access Token**（Messaging API → Issue）
4. Messaging API 設定中：
   - 關閉「Auto-reply messages」
   - 關閉「Greeting messages」
   - Use webhooks：**開啟**

### 2. 本地開發

```bash
# 安裝相依套件
pip install -r requirements.txt

# 設定環境變數
export LINE_CHANNEL_ACCESS_TOKEN=your_token
export LINE_CHANNEL_SECRET=your_secret

# 啟動伺服器
python app.py

# 另開終端機，用 ngrok 建立公開 HTTPS URL
ngrok http 5000
```

把 ngrok 產生的 URL（如 `https://xxxx.ngrok.io/webhook`）填入 LINE Developers Console 的 **Webhook URL**。

### 3. 正式部署（Render）

1. 將專案推送到 GitHub
2. 至 [render.com](https://render.com) 建立 **Web Service**，連接 GitHub repo
3. 確認 Environment 為 **Python**
4. 設定：
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`gunicorn -c gunicorn_config.py --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --preload app:app`
5. Environment Variables 填入：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`
   - `DB_PATH` = `/data/tuangou.db`
6. 新增 Persistent Disk（Mount Path: `/data`，Size: 1 GB）
7. 部署完成後，將 Render 提供的網址 + `/webhook` 填入 LINE Developers Console

---

## 注意事項

- **每個群組各自獨立**維護團購，不同群組互不影響
- 同一用戶重複下單會累加數量
- 資料儲存於 SQLite（`tuangou.db`）
- 需要 Persistent Disk 才能在重啟後保留資料
- 使用 `.python-version` 檔案指定 Python 3.11（避免相容性問題）
