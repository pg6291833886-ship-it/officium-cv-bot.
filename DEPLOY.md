# Deployment guide

## Current prerequisite
The repository must contain the actual `bot.py` application before deployment. The current commit adds deployment scaffolding only; do not deploy until the source is present.

## Render deployment
1. Sign in to Render and connect the GitHub repository `pg6291833886-ship-it/officium-cv-bot.`.
2. Choose **New → Blueprint**, select the repository, and inspect `render.yaml`.
3. Confirm the worker and persistent disk plan and any charges before creating the service.
4. Add secrets only in Render's environment settings: `TELEGRAM_BOT_TOKEN`, plus `OPENAI_API_KEY` and/or `GEMINI_API_KEY`.
5. Deploy, check startup logs, then test Telegram commands and CV generation with a non-sensitive sample CV.
6. Restart the worker and verify the SQLite database and generated files remain under `/data`.

## Preserve existing data
Do not upload SQLite files or candidate CVs to GitHub. If the prior bot has a database, back it up and plan a controlled migration into `/data/officium_bot.sqlite3`; this scaffold does not migrate data automatically. Keep the old bot stopped before pointing the same Telegram token at a second polling process to avoid Telegram polling conflicts.

## Security
If a Telegram token or provider key was ever shared in a chat or committed to Git, revoke/rotate it. Never put replacements in this repository. Keep `.env` local and untracked.
