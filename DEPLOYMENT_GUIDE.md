# 🚀 團購接龍 Bot 部署指南 - Render
# Deployment Guide - Using Render Platform

完整的逐步部署教學，從零開始到上線！

---

## 📋 Prerequisites (準備工作)

### ✅ What You Have:
- ✅ 團購接龍 Bot 程式碼 (app.py and all files)
- ✅ LINE Bot created (Channel Access Token & Secret)
- ✅ All packages listed in requirements.txt

### 📦 What You Need:
- [ ] GitHub account (free)
- [ ] Render account (free)
- [ ] 10-15 minutes

---

## 🎯 Step 1: Create GitHub Account & Repository

### 1.1 Sign up for GitHub
1. Go to https://github.com/signup
2. Enter your email, create password, choose username
3. Verify your email
4. ✅ You now have a GitHub account!

### 1.2 Create a New Repository
1. Go to https://github.com/new
2. Fill in:
   - **Repository name**: `line-tuangou`
   - **Description**: "LINE Bot for group buying management"
   - **Visibility**: Choose "Public" or "Private"
   - **❌ DO NOT** check "Add a README file"
   - **❌ DO NOT** check "Add .gitignore"
3. Click **"Create repository"**

---

## 💻 Step 2: Upload Your Code to GitHub

### Option A: Easy Way - Web Upload (Recommended for beginners)

1. On your GitHub repository page, click **"uploading an existing file"** link
2. **Drag and drop** these files:
   ```
   ✅ app.py
   ✅ requirements.txt
   ✅ gunicorn_config.py
   ✅ render.yaml
   ✅ .python-version
   ✅ .gitignore
   ✅ README.md
   ```
3. **⚠️ IMPORTANT: DO NOT upload .env file!**
4. Click **"Commit changes"**

### Option B: Git Command Line

```bash
cd /path/to/line-tuangou

git init
git add .
git commit -m "Initial commit - 團購接龍 Bot"
git remote add origin https://github.com/YOUR_USERNAME/line-tuangou.git
git branch -M main
git push -u origin main
```

---

## ☁️ Step 3: Deploy to Render

### 3.1 Sign up for Render
1. Go to https://render.com/register
2. Click **"Sign up with GitHub"** (easiest option)
3. Authorize Render to access your GitHub
4. ✅ You now have a Render account!

### 3.2 Create New Web Service
1. On Render Dashboard, click **"New +"** → **"Web Service"**
2. Connect your GitHub repository
3. Select your **line-tuangou** repository
4. ⚠️ **IMPORTANT**: Make sure the Environment is set to **Python** (not Go or Docker)

### 3.3 Configure the Service

**Basic Settings:**
- **Name**: `line-tuangou` (or your choice)
- **Region**: Oregon
- **Branch**: `main`

**Build & Deploy:**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn -c gunicorn_config.py --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 --preload app:app`

**Instance Type:**
- Select **"Free"** or **"Starter"**
- ⚠️ Free tier sleeps after 15 min of inactivity

### 3.4 Set Environment Variables ⚠️ CRITICAL!

Click **"Add Environment Variable"** for each:

1. **LINE_CHANNEL_ACCESS_TOKEN**
   - Value: `paste your token from LINE Developers Console`

2. **LINE_CHANNEL_SECRET**
   - Value: `paste your secret from LINE Developers Console`

3. **DB_PATH**
   - Value: `/data/tuangou.db`

### 3.5 Add Persistent Disk

1. Go to **Settings** → **Disks**
2. Click **"Add Disk"**
3. Configure:
   - **Mount Path**: `/data`
   - **Size**: 1 GB
4. ⚠️ Without a disk, data resets on every deploy!

### 3.6 Deploy!
1. Click **"Create Web Service"**
2. ⏳ Wait 2-5 minutes while Render builds and starts your bot
3. Watch the logs - you should see:
   ```
   [startup] 資料庫初始化完成
   ```
4. ✅ When you see "Your service is live 🎉", you're done!

### 3.7 Get Your Webhook URL

At the top of your Render service page, you'll see:
```
https://line-tuangou-xxxx.onrender.com
```

Your webhook URL is:
```
https://line-tuangou-xxxx.onrender.com/webhook
                                      ^^^^^^^^
                                      add /webhook at the end!
```

---

## 🔗 Step 4: Configure LINE Webhook

### 4.1 Go to LINE Developers Console
1. Open https://developers.line.biz/console/
2. Select your provider
3. Select your **團購接龍** channel
4. Go to **"Messaging API"** tab

### 4.2 Set Webhook URL
1. Find **"Webhook settings"** section
2. Click **"Edit"** next to Webhook URL
3. Paste your URL: `https://line-tuangou-xxxx.onrender.com/webhook`
4. Click **"Update"**
5. Click **"Verify"** button
   - Should show ✅ "Success" in green

### 4.3 Enable Webhook
1. Toggle **"Use webhook"** to **ON** (Enabled)

### 4.4 Important Settings:

**✅ Must be ON:**
- **Use webhook**: Enabled
- **Allow bot to join group chats**: Enabled

**❌ Should be OFF:**
- **Auto-reply messages**: Disabled
- **Greeting messages**: Disabled (Bot has its own welcome message)

---

## 🧪 Step 5: Test Your Bot!

### Test 1: Group Chat
1. Create a test group (you + 1 friend)
2. Add Bot to the group
3. ✅ Should receive welcome message
4. Send the following multi-line message:
   ```
   #開團
   測試團購
   1) 蘋果 50元
   2) 橘子 30元
   ```
5. ✅ Should show parsed items
6. Send: `#1`
7. ✅ Should confirm order
8. Send: `列表`
9. ✅ Should show order list
10. Send: `結團`
11. ✅ Should show final list

---

## 🎉 Success! Your Bot is Live!

### 📊 Monitor Your Bot

**View Logs:**
1. Go to Render Dashboard
2. Click your service
3. Click **"Logs"** tab

**Restart Bot (if needed):**
1. Go to your service
2. Click **"Manual Deploy"** → **"Deploy latest commit"**

---

## 🔐 Security Notes

- ✅ Never commit .env to GitHub
- ✅ Environment variables are secret in Render
- ✅ Keep your LINE tokens private
- ✅ .gitignore already excludes .env and .db files

---

## 🎯 Quick Reference Card

```
Webhook URL Format:
https://YOUR-APP-NAME.onrender.com/webhook

Environment Variables Needed:
- LINE_CHANNEL_ACCESS_TOKEN=your_token_here
- LINE_CHANNEL_SECRET=your_secret_here
- DB_PATH=/data/tuangou.db

Bot Commands:
#開團 + 商品列表 - 開團
#N              - 下單品項 N
#N 名字         - 幫人下單
列表            - 查看訂單
我的訂單        - 查看自己訂單
退出 N          - 取消訂單
結團            - 封存訂單
團購說明        - 顯示說明
```

---

**Last Updated:** 2026-02-18
**Platform:** Render
**Status:** Production Ready ✅
