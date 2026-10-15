# Officium CV Bot

Telegram bot that accepts a job description and a candidate CV (PDF/DOCX), drafts a tailored CV using an AI provider, and returns PDF and editable DOCX files. It uses the source CV as the factual source of truth; candidates must review all output before use.

## Current feature scope
- Telegram polling bot with /start, /cancel and /help.
- Accepts text job descriptions and PDF/DOCX CV uploads.
- Uses OpenAI when OPENAI_API_KEY is configured; otherwise uses Gemini when GEMINI_API_KEY is configured.
- Generates a PDF and editable DOCX CV.
- Saves a minimal job record in SQLite and stores temporary/generated files under OUTPUT_DIR.
- This initial repository version does not include batch CV processing, a separate AI audit/report, application submission, CRM integration, or a guaranteed ATS score.

## Deploy on Render
1. Connect this GitHub repository to Render and select New → Blueprint.
2. Review the render.yaml worker and persistent disk plan; confirm any charges before creating it.
3. Set TELEGRAM_BOT_TOKEN and at least one provider key (OPENAI_API_KEY or GEMINI_API_KEY) in Render's environment settings. Never commit secrets.
4. Deploy and inspect logs.
5. Test with a non-sensitive sample CV: send /start, paste the job description, upload a PDF/DOCX, and confirm both output files arrive.
6. Restart the worker and verify that database records and generated files persist under /data.

## Run locally
Python 3.11+ is recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set credentials in .env locally; never commit this file.
python bot.py
```

## Data and privacy
- .gitignore excludes .env, local database files, generated CVs, and Python cache files.
- The Render disk is configured for /data/officium_bot.sqlite3 and /data/generated.
- This setup does not migrate an existing database or connect to an existing Officium CRM. Back up any existing records before migrating.
- Never upload candidate CVs, generated documents, databases, or credentials to GitHub.
- If any bot token or provider key was exposed in a chat or committed to Git, revoke/rotate it before deployment.
- Live Telegram and AI-provider behavior has not yet been tested from this repository; do not consider it production-ready until end-to-end tests pass.

## Files
- bot.py — Telegram application
- requirements.txt — Python dependencies
- Dockerfile, start.sh — container startup
- render.yaml — Render background worker + persistent disk blueprint
- .env.example — variable names only, no secrets
- DEPLOY.md — deployment and data-protection notes
