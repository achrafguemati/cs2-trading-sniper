# Free Multi-Channel Notifications Setup Guide

**CS2 Trading Sniper — Milestone 5**  
**Document Version:** 1.0  
**Status:** Official Setup Guide  

---

## 1. Overview & Zero-Cost Architecture

The CS2 Trading Sniper notification infrastructure is designed specifically to operate **100% free of charge**, requiring:
* **NO** paid SaaS subscriptions (no Twilio, SendGrid, Pushover, or PagerDuty).
* **NO** cloud VPS or dedicated servers (runs on your local machine).
* **NO** paid domains or public IP leases.
* **ZERO** credential storage for Steam accounts (strictly read-only public market research).

Supported notification channels:
1. **Discord Webhooks** (Free, instant rich embeds).
2. **Telegram Bot API** (Free, instant mobile & desktop push with HTML formatting).
3. **Personal Gmail / Standard SMTP Email** (Free, dual plain-text & HTML emails).
4. **Meta Facebook Messenger** (*Platform policy analysis: UNAVAILABLE for unattended alerts*).

---

## 2. Discord Webhook Setup (Recommended — Fastest)

Discord provides free, instantaneous incoming webhooks that render formatted rich embeds on both desktop and mobile.

### Step-by-Step Setup (60 seconds)
1. Open Discord and create a private personal server (or use an existing one).
2. Right-click the text channel where you want alerts (e.g. `#sniper-alerts`) -> **Edit Channel**.
3. Navigate to **Integrations** -> **Webhooks** -> click **New Webhook**.
4. Name the bot (e.g. `CS2 Sniper`) and optionally upload an avatar.
5. Click **Copy Webhook URL**.
6. Set the environment variable in your `.env` file:
   ```bash
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1234567890/abcdefghijklmnopqrstuvwxyz"
   ```

### Verification
Test the webhook using the CLI:
```bash
python -m sniper.cli test-notifications --channel discord
```

---

## 3. Telegram Bot Setup (Recommended — Mobile Push)

Telegram provides a free, highly reliable Bot API with real-time push notifications.

### Step-by-Step Setup (2 minutes)
1. In Telegram, search for `@BotFather` (the official verified bot with a blue checkmark).
2. Send `/newbot` to `@BotFather`.
3. Enter a display name (e.g. `My CS2 Sniper`) and a unique username ending in `bot` (e.g. `my_cs2_sniper_bot`).
4. Copy the **HTTP API Token** provided by BotFather (format: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678`).
5. Open a chat with your newly created bot and click **Start** (or send `/start`).
6. To find your personal Chat ID:
   * Search for `@userinfobot` on Telegram and send `/start`.
   * It will reply with your `Id` (e.g. `987654321`).
7. Set the environment variables in your `.env` file:
   ```bash
   TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678"
   TELEGRAM_CHAT_ID="987654321"
   ```

### Verification
Test your Telegram setup using the CLI:
```bash
python -m sniper.cli test-notifications --channel telegram
```

---

## 4. Free Personal Email Setup (Gmail SMTP)

You can send free email notifications directly from your personal Gmail account using Google's free SMTP relay with an **App Password**.

### Step-by-Step Setup with Gmail (2 minutes)
1. Ensure your Google account has **2-Step Verification** enabled (required by Google for SMTP relay).
2. Go to your [Google Account Security settings](https://myaccount.google.com/security).
3. Under *How you sign in to Google*, click on **2-Step Verification**.
4. Scroll to the bottom and select **App passwords** (or search "App passwords" in the top search bar).
5. Enter an app name (e.g. `CS2 Sniper`) and click **Create**.
6. Google will generate a 16-character password (e.g. `abcd efgh ijkl mnop`).
7. Set the environment variables in your `.env` file:
   ```bash
   SMTP_HOST="smtp.gmail.com"
   SMTP_PORT=587
   SMTP_USERNAME="your_gmail_username@gmail.com"
   SMTP_PASSWORD="abcdefghijklmnop"
   SMTP_FROM_EMAIL="your_gmail_username@gmail.com"
   SMTP_TO_EMAIL="your_destination_email@gmail.com"
   SMTP_USE_TLS=true
   SMTP_USE_SSL=false
   ```

### Other Free Email Providers
If you use Outlook, Yahoo, or a custom domain, standard SMTP settings apply:
* **Outlook/Hotmail:** `smtp-mail.outlook.com`, port `587`, STARTTLS enabled.
* **Yahoo:** `smtp.mail.yahoo.com`, port `465`, SSL enabled.

### Verification
Test your email setup using the CLI:
```bash
python -m sniper.cli test-notifications --channel email
```

---

## 5. Facebook Messenger Platform Policy Analysis

The system marks Facebook Messenger as:
```
UNAVAILABLE — requires official Meta messaging setup and an active permitted messaging window
```

### Detailed Technical and Policy Explanation

Users frequently ask if free alerts can be delivered to their personal Facebook Messenger account. An exhaustive evaluation of the official **Meta Messenger Platform API** and **Platform Terms** demonstrates why unattended local trading alerts cannot be supported:

1. **The 24-Hour Standard Messaging Window:**
   * Under Meta Platform Policy, a Facebook Page may only send messages to a user within **24 hours** of that user's most recent interaction with the Page.
   * Outbound, unsolicited notifications sent after 24 hours of inactivity are automatically blocked by the Graph API (`{"error": {"code": 10, "message": "(#10) Message failed to send because more than 24 hours have passed..."}}`).

2. **Message Tag Restrictions:**
   * Outside the 24-hour window, Meta permits messages only if tagged with one of four approved `MessageTag` categories:
     - `CONFIRMED_EVENT_UPDATE`
     - `POST_PURCHASE_UPDATE`
     - `ACCOUNT_UPDATE`
     - `HUMAN_AGENT`
   * Steam market opportunity detection, price alerts, and inventory notifications **do not qualify** under any of these categories. Using a tag for financial opportunity notifications violates Meta Platform Policy, resulting in immediate application and Page suspension.

3. **Public Webhook & SSL Requirements:**
   * Receiving user opt-ins requires a verified Meta App, App Review approval, a Facebook Page, and a public HTTPS webhook endpoint with SSL certificates and SHA256 signature verification (`hub.challenge`).
   * This directly conflicts with our zero-cost, local-first mandate (no VPS, no paid domains, no reverse proxy tunnels).

4. **Deprecation of One-Time Notifications (OTN):**
   * Meta deprecated free recurring notification tokens for unattended bots in personal accounts.

**Recommendation:** Use **Telegram** or **Discord** for instant, zero-cost, reliable alerts.

---

## 6. Secret Redaction & Security Assurance

All notification credentials (bot tokens, webhook URLs, and SMTP passwords) are automatically scrubbed by a centralized sanitization engine (`redact_secrets()`).
* Tokens and passwords are **NEVER** written to SQLite database logs or `notification_deliveries` records.
* Exception messages and console outputs redact secrets before emission.
* Zero Steam credentials or session cookies are used or accepted.
