# Free Multi-Channel Notifications Setup Guide (Discord + Telegram)

**CS2 Trading Sniper — Milestone 5**  
**Document Version:** 2.0
**Status:** Official Setup Guide  

---

## 1. Overview & Zero-Cost Architecture

The CS2 Trading Sniper notification infrastructure is designed specifically to operate **100% free of charge**, requiring:
* **NO** paid SaaS subscriptions (no Twilio, SendGrid, Pushover, or PagerDuty).
* **NO** cloud VPS or dedicated servers (runs entirely on your local machine).
* **NO** paid domains, static IPs, or public hosting.
* **ZERO** credential storage for Steam accounts (strictly read-only public market research).

Supported notification channels (Discord + Telegram ONLY):
1. **Discord Webhooks** (Free, instant rich embeds with market metrics, net proceeds, and warnings).
2. **Telegram Bot API** (Free, instant mobile & desktop push with safely escaped HTML formatting).

---

## 2. Environment Configuration (.env on Windows & Linux)

The application automatically loads local configuration from a `.env` file via `python-dotenv`. Existing system or process environment variables take precedence (`override=False`).

### Creating Your `.env` File

On Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

On Windows Command Prompt (cmd.exe):
```cmd
copy .env.example .env
```

On Linux / macOS:
```bash
cp .env.example .env
```

Edit the `.env` file to add your Discord webhook URL and/or Telegram bot credentials.

---

## 3. Discord Webhook Setup (Recommended — Fastest)

Discord provides free, instantaneous incoming webhooks that render formatted rich embeds on both desktop and mobile.

### Step-by-Step Setup (60 seconds)
1. Open Discord and create a private personal server (or use an existing one).
2. Right-click the text channel where you want alerts (e.g. `#sniper-alerts`) -> **Edit Channel**.
3. Navigate to **Integrations** -> **Webhooks** -> click **New Webhook**.
4. Name the webhook (e.g. `CS2 Sniper`) and optionally upload an avatar.
5. Click **Copy Webhook URL**.
6. Set the environment variable in your `.env` file:
   ```bash
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1234567890/abcdefghijklmnopqrstuvwxyz"
   # Optional: enable/disable channel (defaults to true)
   DISCORD_ENABLED=true
   ```

### Verification
Test the webhook using the CLI:
```bash
python -m sniper.cli test-notification --channel discord
```

---

## 4. Telegram Bot Setup (Recommended — Mobile Push)

Telegram provides a free, highly reliable Bot API (`sendMessage`) with real-time push notifications.

> [!IMPORTANT]
> **User Action Required:** Telegram will reject messages (`400 Bad Request: chat not found` or `403 Forbidden`) until you explicitly initiate contact with your bot. You **MUST** open the bot in Telegram and click **Start** (or send `/start`), or add the bot to your intended alert group, before alerts can be delivered.

### Step-by-Step Setup (2 minutes)
1. In Telegram, search for `@BotFather` (the official verified bot with a blue checkmark).
2. Send `/newbot` to `@BotFather`.
3. Enter a display name (e.g. `My CS2 Sniper`) and a unique username ending in `bot` (e.g. `my_cs2_sniper_bot`).
4. Copy the **HTTP API Token** provided by BotFather (format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678`).
5. **Start the bot:** Click the link to your new bot (e.g. `t.me/my_cs2_sniper_bot`) and click **Start** (or send `/start`).
6. To find your personal Chat ID:
   * Search for `@userinfobot` on Telegram and send `/start`.
   * It will reply with your `Id` (e.g. `987654321`).
7. Set the environment variables in your `.env` file:
   ```bash
   TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678"
   TELEGRAM_CHAT_ID="987654321"
   # Optional: enable/disable channel (defaults to true)
   TELEGRAM_ENABLED=true
   ```

### Verification
Test your Telegram setup using the CLI:
```bash
python -m sniper.cli test-notification --channel telegram
```

---

## 5. Channel Status & Verification Commands

Inspect configured channels and delivery readiness without exposing credentials:

### Check Channel Configuration Status
```bash
python -m sniper.cli channel-status
```
Output displays whether each channel is `Configured & Enabled`, `Disabled`, or `Not configured` without revealing token strings or webhook URLs.

### Send Safe Test Notifications
```bash
# Test all configured channels
python -m sniper.cli test-notification --channel all

# Test specific channel
python -m sniper.cli test-notification --channel discord
python -m sniper.cli test-notification --channel telegram
```

---

## 6. Delivery-Aware Cooldown & Channel Independence

To prevent notification spam while ensuring critical price movements are immediately delivered:

* **Anchored to Successful Deliveries:** Cooldown is tracked per-channel and referenced exclusively against rows in the `notification_deliveries` table where `status = 'delivered'`.
* **Zero Cooldown Resets from Suppressed Events:** Intervening non-actionable observations (such as `DATA INSUFFICIENT`, missing buy depth, or crossed books) or evaluations suppressed by cooldown (`Suppressed (Cooldown)`) **NEVER** update or reset the cooldown reference timestamp or reference bid amount.
* **Meaningful Increase Bypass:** An alert is suppressed only if the elapsed time is within `cooldown_seconds` (default 1800s / 30m) **AND** the new bid is less than the last delivered bid plus `meaningful_bid_increase_pct` (default 5%). A $\ge 5\%$ bid spike immediately bypasses cooldown.
* **Channel Independence & Failure Retry Backoff:**
  - If delivery to Discord succeeds but Telegram fails (e.g. temporary network glitch), Discord enters normal cooldown while Telegram enters a short failure retry backoff (default 60s, max 3 retries).
  - On the next cycle after backoff expires, Telegram is retried without sending a duplicate notification to Discord.
* **Unconfigured Channels:** Disabled or unconfigured channels never enter cooldown or block other channels.

---

## 7. Secret Redaction & Security Assurance

All notification credentials (bot tokens and webhook URLs) are automatically scrubbed by a centralized sanitization engine (`redact_secrets()`):
* Bot tokens (`\d{8,12}:[A-Za-z0-9_-]{30,45}`) and webhook URLs are **NEVER** written to SQLite database logs or `notification_deliveries` audit records.
* Exception messages, HTTP error responses, and console outputs redact credentials before emission.
* Credentials are read strictly from environment variables, never committed to source control or SQLite.
* Notification transports maintain isolated HTTPS clients and never reuse or weaken the Steam Market HTTP client.
* Zero Steam credentials, session cookies, or passwords are used or accepted.
