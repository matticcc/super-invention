# 🎰 Crazy Time Telegram Bot — Render Free Hosting

## How it works on Render free plan

Render's free Web Services spin down after **15 minutes of no HTTP traffic**.  
The bot solves this by running two things in the same process:
- A **background thread** that polls the Crazy Time API every 30 s and posts to Telegram
- A **tiny HTTP server** on the port Render assigns, returning `200 OK` to any GET request

You then use a free external ping service (UptimeRobot) to hit that URL every 14 minutes — keeping the service alive 24/7.

---

## Step-by-step deploy

### 1. Push to GitHub

Create a **new GitHub repo** and push these 3 files:
```
bot.py
requirements.txt
render.yaml
```

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USER/crazytime-bot.git
git push -u origin main
```

### 2. Create a Render Web Service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Render auto-detects `render.yaml` — just click **Apply**
4. Or fill manually:
   - **Environment:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `python bot.py`
   - **Plan:** Free

### 3. Set environment variables

In Render dashboard → your service → **Environment**:

| Key | Value |
|-----|-------|
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` (from @BotFather) |
| `TELEGRAM_CHANNEL_ID` | `@yourchannel` or `-100xxxxxxxxxx` |

Click **Save Changes** → the service will redeploy automatically.

### 4. Keep it alive with UptimeRobot (free)

Render free services sleep after 15 min of no traffic. Fix it in 2 minutes:

1. Go to [uptimerobot.com](https://uptimerobot.com) → create a free account
2. **New Monitor → HTTP(s)**
3. **URL:** your Render service URL (e.g. `https://crazytime-bot.onrender.com`)
4. **Interval:** every **14 minutes**
5. Save — that's it. UptimeRobot pings your bot, Render never sleeps.

---

## Verify it's running

- **Render logs:** Dashboard → your service → **Logs** tab  
  You should see lines like:
  ```
  2026-06-19 21:00:00 [INFO] 🎰 Polling loop started | channel=@mychan | interval=30s
  2026-06-19 21:00:00 [INFO] 🌐 Health server listening on port 10000
  2026-06-19 21:00:30 [INFO] 🆕 New round: 6a35acbb...
  2026-06-19 21:00:30 [INFO] ✅ Message posted.
  ```

- **UptimeRobot:** shows green uptime once pings start succeeding

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Service crashes on start | Check Render logs — usually missing env vars |
| `Telegram error 400` | Double-check `TELEGRAM_CHANNEL_ID` format; bot must be admin of the channel |
| `Telegram error 401` | `TELEGRAM_BOT_TOKEN` is wrong or revoked |
| No messages posting | Check logs for fetch errors; the casinoscores API may be rate-limiting |
| Service sleeps anyway | Make sure UptimeRobot monitor is active and interval is ≤14 min |
