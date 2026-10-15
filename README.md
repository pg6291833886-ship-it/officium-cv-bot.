# Officium CV Bot

Deployment scaffold for the Officium Telegram CV assistant. This repository must contain `bot.py` before it can run; deployment files alone do not implement the bot.

## What is configured
- Python dependencies for Telegram, OpenAI, Gemini, PDF and DOCX support.
- Docker image and persistent-data paths (`/data/officium_bot.sqlite3`, `/data/generated`).
- Render background worker blueprint (`render.yaml`).
- `.gitignore` rules to keep credentials, local databases and candidate CVs out of Git.

## Deploy
1. Add the complete reviewed `bot.py` source to the repository.
2. Connect this repository to Render using **New → Blueprint** and review the paid worker + persistent disk plan.
3. In Render environment settings, add `TELEGRAM_BOT_TOKEN` and at least one provider key (`OPENAI_API_KEY` or `GEMINI_API_KEY`). Do not commit secrets.
4. Deploy and inspect logs. Test `/start`, upload a test CV, generate PDF/DOCX, and verify data survives a restart.

## Data protection
Never commit `.env`, database files, generated CVs, or candidate uploads. This scaffold stores persistent application data under `/data`; it does not automatically migrate an existing CRM database or any database outside this worker. Back up existing records before migration.

## Status
**Not yet deployable:** `bot.py` has not been committed to this repository. The environment keys, provider calls and full CV workflow also need live tests before production use.
