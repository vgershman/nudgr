# nudgr

A voice-first Telegram reminder bot that nudges until done.

Send a voice/text/video message describing what you want to be reminded about and when ("call mom in 15 minutes", "remind me to take meds at 9pm", "напомни через час позвонить маме"). The bot:

1. Transcribes audio via Whisper (if needed).
2. Parses intent + time via Claude Haiku.
3. Confirms back ("✓ Got it. I'll remind you in 15 minutes: 📌 call mom").
4. Fires the reminder at the requested time.
5. **Re-pings on an escalation schedule** (5 → 10 → 20 → 60 minutes) until you tap **Done** or **Snooze**.

## Quick start

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID

docker compose up -d postgres
docker compose run --rm app alembic upgrade head
docker compose run --rm app nudgr doctor
docker compose up -d bot
docker compose logs -f bot
```

In Telegram, open your bot and send `/start`, then any of:

- `remind me to call mom in 15 minutes`
- `take meds in 1h`
- voice message: "напомни через 30 минут позвонить врачу"
- `/list` — show pending reminders

## Project layout

- `nudgr/config.py` — env-driven settings (pydantic).
- `nudgr/db/` — SQLAlchemy models (User, Reminder).
- `nudgr/llm/` — Anthropic + OpenAI router.
- `nudgr/transcribe.py` — Whisper transcription wrapper.
- `nudgr/parser.py` — text → structured reminder intent (Haiku).
- `nudgr/scheduler.py` — async loop that fires reminders + escalates.
- `nudgr/bot.py` — aiogram polling bot, message handlers, inline keyboards.
- `alembic/` — DB migrations.
