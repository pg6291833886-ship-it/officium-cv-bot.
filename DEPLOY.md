# Deployment guide

## Deploy on Render
1. Sign in to Render and connect the GitHub repository `pg6291833886-ship-it/officium-cv-bot.`.
2. Choose New → Blueprint, select the repository, and review `render.yaml`.
3. Review the worker and persistent disk plan and confirm any charges before creating the service.
4. Add secrets only in Render environment settings: `TELEGRAM_BOT_TOKEN` and at least one of `OPENAI_API_KEY` or `GEMINI_API_KEY`.
5. Deploy and inspect startup logs. Test with a non-sensitive sample CV: send `/start`, paste the target job description, upload a PDF/DOCX, and confirm both generated files arrive.
6. Restart the worker and verify database and generated files remain under `/data`.

## Preserve existing data
This repository uses `/data/officium_bot.sqlite3` and `/data/generated` for persistent data. It does not automatically migrate an older bot database or connect to an existing CRM database. Back up and inspect any existing database before migration; do not upload SQLite files or candidate CVs to GitHub.

Stop any other running copy of the same Telegram bot before starting this polling process with the same token, to avoid Telegram polling conflicts.

## Security
If a Telegram token or provider key was ever shared in a chat or committed to Git, revoke/rotate it. Never put replacement secrets in this repository. Keep `.env` local and untracked. Test with a non-sensitive sample CV before processing real candidate data.

## Current limitations
This repository version creates a tailored CV in PDF and DOCX. It does not yet provide batch processing, a separate AI quality-audit report, website application submission, or CRM integration. Live Telegram and provider calls have not yet been verified, so treat deployment as a test until end-to-end checks pass.
