# 🔧 團購接龍 Bot 問題排除指南
# Troubleshooting Guide

---

## ❌ Problem: Build Failed on Render
## 問題：Render 建置失敗

### Cause 1: Wrong Environment
Render 偵測為 Go 而非 Python。

**Solution:**
- 刪除 Render service，重新建立
- 建立時確認 Environment 為 **Python**
- 可用 `.python-version` 檔案指定版本（如 `3.11`）

### Cause 2: Python Version Too New
Python 3.14 缺少部分套件的預建 wheel。

**Solution:**
- 在專案根目錄建立 `.python-version` 檔案
- 內容：`3.11`（不需指定 patch 版本）
- Render 會自動使用最新的 3.11.x

### Cause 3: Missing Build Command
Build Command 欄位為空。

**Solution:**
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn -c gunicorn_config.py --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --preload app:app`

---

## ❌ Problem: "unable to open database file"
## 問題：無法開啟資料庫檔案

### Cause: No Persistent Disk
Render 沒有掛載 Persistent Disk，`/data` 目錄不存在或無法寫入。

**Solution (正式環境):**
1. Render Dashboard → Service → Settings → Disks
2. Add Disk: Mount Path = `/data`, Size = 1 GB

**Solution (測試用):**
- 程式會自動 fallback 到當前目錄的 `tuangou.db`
- 注意：每次重新部署資料會清空

---

## ❌ Problem: Bot Doesn't Respond
## 問題：機器人沒有回應

### Check 1: Webhook URL
1. LINE Developers Console → Messaging API
2. 確認 Webhook URL 格式正確：`https://your-app.onrender.com/webhook`
3. 點「Verify」確認連線

### Check 2: Environment Variables
1. Render Dashboard → Environment
2. 確認 `LINE_CHANNEL_ACCESS_TOKEN` 和 `LINE_CHANNEL_SECRET` 已設定
3. 確認值沒有多餘的空格

### Check 3: Render Logs
1. Render Dashboard → Logs
2. 檢查是否有錯誤訊息
3. 常見錯誤：
   - `Invalid signature` → LINE_CHANNEL_SECRET 不正確
   - `Unauthorized` → LINE_CHANNEL_ACCESS_TOKEN 不正確

### Check 4: LINE Console Settings
- **Use webhooks**: 必須開啟
- **Allow bot to join group chats**: 必須開啟
- **Auto-reply messages**: 建議關閉

---

## ❌ Problem: Bot Leaves Group Immediately
## 問題：機器人加入群組後立即退出

### Solution:
1. 確認程式碼有 `JoinEvent` handler（已內建 ✅）
2. LINE Console → **Allow bot to join group chats**: Enabled
3. 確認 Webhook 有回應（Render logs 應該看到 incoming requests）

---

## ❌ Problem: "開團" Doesn't Work
## 問題：開團指令沒有反應

### Check:
1. 必須是**多行訊息**（開團 + 換行 + 品項）
2. 品項必須有編號格式：`1)`, `(1)`, `1.`, `1、` 等
3. 正確格式：
   ```
   #開團
   標題
   1) 品名 價格
   2) 品名 價格
   ```
4. 如果已有進行中的團購，需先「結團」或「取消團購」

---

## ❌ Problem: Orders Not Showing in "我的訂單"
## 問題：我的訂單看不到自己的下單

### Cause:
早期版本用 `user_name` 查詢，已修正為用 `user_id` 查詢。

**Solution:**
- 確認使用最新版本的 app.py
- `我的訂單` 會顯示自己的訂單和代訂的訂單

---

## ❌ Problem: Free Tier Sleeping
## 問題：免費方案休眠

### Explanation:
Render 免費方案在 15 分鐘無活動後會休眠，第一條訊息可能需要 30-60 秒才會回應。

### Solutions:
- 這是正常現象，等待即可
- 升級到 Render Starter 方案避免休眠
- 使用外部 uptime monitor 定期 ping

---

## 🧪 Testing Checklist

在將 Bot 加入正式群組前：

- [ ] 程式碼已推送到 GitHub
- [ ] Render service 建立為 **Python** 環境
- [ ] Build 成功，service 正常運行
- [ ] Environment variables 已設定
- [ ] Persistent Disk 已掛載（正式環境）
- [ ] Webhook URL 已設定並驗證通過
- [ ] LINE Console 的 webhook 和群組設定已開啟
- [ ] 在測試群組中測試過 開團 → 下單 → 列表 → 結團

---

## 📝 Common Error Messages

| 錯誤訊息 | 原因 | 解決方法 |
|----------|------|---------|
| `unable to open database file` | 無 Persistent Disk | 加掛 Disk 或讓程式 fallback |
| `Invalid signature` | Channel Secret 錯誤 | 檢查環境變數 |
| `Unauthorized` | Access Token 錯誤 | 重新產生 Token |
| `gunicorn: command not found` | 環境設為 Go | 重新建立 service 選 Python |
| `No module named gunicorn` | Build Command 未設定 | 設定 `pip install -r requirements.txt` |

---

**Last Updated:** 2026-02-18
**Status:** Production Ready ✅
