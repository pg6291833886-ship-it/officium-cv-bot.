import os, re, io, sqlite3, logging, asyncio
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import AsyncOpenAI
from google import genai
from pypdf import PdfReader
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from xml.sax.saxutils import escape
from aiohttp import web

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("officium")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
# Automatically replace the retired model setting if Render still has the old env value.
if GEMINI_MODEL == "gemini-2.5-flash":
    GEMINI_MODEL = "gemini-3.8-flash"
DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/officium_bot.sqlite3"))
OUT_DIR = Path(os.getenv("OUTPUT_DIR", "/data/generated"))
MAX_UPLOAD = 12 * 1024 * 1024

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS cv_jobs (
 id INTEGER PRIMARY KEY, user_id INTEGER, created_at TEXT, filename TEXT,
 jd TEXT, status TEXT, output_docx TEXT, output_pdf TEXT)""")
# Persist only the active job description so webhook retries/restarts do not lose workflow state.
db.execute("""CREATE TABLE IF NOT EXISTS active_workflows (
 user_id INTEGER PRIMARY KEY, jd TEXT NOT NULL, updated_at TEXT NOT NULL)""")
# De-duplicate webhook deliveries from Telegram.
db.execute("CREATE TABLE IF NOT EXISTS processed_updates (update_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL)")
db.commit()

def save_active_jd(user_id: int, jd: str):
    db.execute(
        "INSERT INTO active_workflows(user_id, jd, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET jd=excluded.jd, updated_at=excluded.updated_at",
        (user_id, jd, datetime.now(timezone.utc).isoformat())
    )
    db.commit()

def get_active_jd(user_id: int):
    row = db.execute("SELECT jd FROM active_workflows WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else None

def clear_active_jd(user_id: int):
    db.execute("DELETE FROM active_workflows WHERE user_id=?", (user_id,))
    db.commit()

def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    if path.suffix.lower() == ".docx":
        d = Document(str(path))
        return "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(
            " | ".join(c.text for c in row.cells) for t in d.tables for row in t.rows)
    raise ValueError("Please upload a PDF or DOCX file.")

async def create_cv(source: str, jd: str) -> str:
    prompt = f"""You are a careful professional CV writer. Tailor the candidate CV to the target job description.
Rules: use ONLY facts supported by the source CV; never invent employers, dates, degrees, skills, certifications, tools, metrics or achievements. DATE HANDLING IS IMPORTANT: do not reject the CV or mark it incomplete just because an employment date lacks a year. Preserve dates exactly as supported by the source. If only months are provided, keep month-only dates (for example, May–September). If only years are provided, keep year-only dates. If both month and year are provided, keep both. Never guess or add a missing year. A partial date is acceptable and must not trigger a validation failure. Do not claim a guaranteed ATS score. Use a clean, ATS-friendly single-column layout. Keep contact details as found in source. Clearly prioritize relevant real experience and keywords from the job description, without keyword stuffing. Return only the finished CV text with clear section headings.

SOURCE CV:
{source[:24000]}

TARGET JOB DESCRIPTION:
{jd[:14000]}
"""
    provider_errors = []
    # Try OpenAI first when configured, but do not let a broken/quota-limited key block Gemini fallback.
    if OPENAI_KEY:
        try:
            client = AsyncOpenAI(api_key=OPENAI_KEY)
            response = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role":"system","content":"You are a meticulous CV editor. Accuracy over embellishment."},
                          {"role":"user","content":prompt}],
                temperature=0.2)
            text_result = (response.choices[0].message.content or "").strip()
            if text_result:
                return text_result
            provider_errors.append("OpenAI returned an empty response.")
        except Exception as exc:
            log.exception("OpenAI CV generation failed; trying Gemini if configured")
            provider_errors.append("OpenAI: " + str(exc)[:220])
    if GEMINI_KEY:
        client = genai.Client(api_key=GEMINI_KEY)
        # Stable model IDs documented by Google; try lower-cost model first after the primary.
        models_to_try = list(dict.fromkeys([GEMINI_MODEL, "gemini-3.5-flash-lite", "gemini-3.6-flash"]))
        last_error = None
        for model_name in models_to_try:
            for attempt in range(3):
                try:
                    result = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model_name,
                        contents=prompt
                    )
                    text_result = (getattr(result, "text", "") or "").strip()
                    if text_result:
                        return text_result
                    raise RuntimeError("Gemini returned an empty response.")
                except Exception as exc:
                    last_error = exc
                    error_text = str(exc).lower()
                    is_temporary = any(term in error_text for term in ("503", "unavailable", "429", "resource_exhausted", "high demand", "temporarily", "quota", "rate limit"))
                    if not is_temporary:
                        break
                    if attempt < 2:
                        await asyncio.sleep(2 ** (attempt + 1))
        provider_errors.append("Gemini: " + str(last_error)[:300])
    if provider_errors:
        raise RuntimeError("CV generation failed for the configured AI provider(s). " + " | ".join(provider_errors))
    raise RuntimeError("No AI provider is configured. Add a valid GEMINI_API_KEY or OPENAI_API_KEY in hosting environment settings.")

def write_docx(text: str, path: Path):
    d = Document()
    d.core_properties.title = "ATS-Friendly CV"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            title = line.lstrip("# ").strip()
            d.add_heading(title, level=1 if line.startswith("# ") else 2)
        elif line.endswith(":") and len(line) < 70:
            d.add_heading(line[:-1], level=2)
        elif line.startswith(("- ", "• ", "* ")):
            d.add_paragraph(line[2:].strip(), style="List Bullet")
        else:
            d.add_paragraph(line)
    d.save(path)

def write_pdf(text: str, path: Path):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CVTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=16, leading=20, spaceAfter=10))
    styles.add(ParagraphStyle(name="CVSection", parent=styles["Heading2"], fontSize=10.5, leading=13, textColor=colors.HexColor("#203864"), spaceBefore=8, spaceAfter=3))
    styles.add(ParagraphStyle(name="CVBody", parent=styles["BodyText"], fontSize=8.8, leading=11, spaceAfter=3))
    story = []
    first = True
    for line in text.splitlines():
        s = line.strip()
        if not s:
            story.append(Spacer(1, 3)); continue
        clean = escape(s.lstrip("# ").strip()).replace("&amp;", "&amp;")
        if first:
            story.append(Paragraph(clean, styles["CVTitle"])); first = False
        elif s.startswith("#") or (s.endswith(":") and len(s) < 70):
            story.append(Paragraph(clean.rstrip(":"), styles["CVSection"]))
        elif s.startswith(("- ", "• ", "* ")):
            story.append(Paragraph("&#8226; " + escape(s[2:].strip()), styles["CVBody"]))
        else:
            story.append(Paragraph(clean, styles["CVBody"]))
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=42, leftMargin=42, topMargin=38, bottomMargin=38).build(story)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.effective_user:
        clear_active_jd(update.effective_user.id)
    await update.message.reply_text(
        "Welcome to Officium CV Bot.\n\n1) Send the full job description as a text message.\n"
        "2) Then upload your current CV as PDF or DOCX.\n3) I will create a tailored CV in PDF and DOCX.\n\n"
        "I will not intentionally add experience or qualifications that are not in your source CV. Use /cancel to reset.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.effective_user:
        clear_active_jd(update.effective_user.id)
    await update.message.reply_text("Current CV workflow cleared. Send /start to begin again.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").strip()
    if not msg:
        return
    if len(msg) < 100:
        await update.message.reply_text("Please send the complete job description (ideally 100+ characters), or use /start to begin.")
        return
    jd = msg[:14000]
    context.user_data["jd"] = jd
    save_active_jd(update.effective_user.id, jd)
    await update.message.reply_text("Job description saved. Now upload the candidate CV as a PDF or DOCX file.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    jd = context.user_data.get("jd") or get_active_jd(update.effective_user.id)
    if not jd:
        await msg.reply_text("First send the target job description as a text message, then upload the CV.")
        return
    doc = msg.document
    if not doc or not (doc.file_name or "").lower().endswith((".pdf", ".docx")):
        await msg.reply_text("Please upload a PDF or DOCX CV.")
        return
    if doc.file_size and doc.file_size > MAX_UPLOAD:
        await msg.reply_text("The file is too large. Please upload a CV smaller than 12 MB.")
        return
    await msg.reply_text("CV received. Extracting content and preparing your tailored CV. This may take a minute.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", doc.file_name or "candidate.docx")[:100]
    source_path = OUT_DIR / f"{msg.from_user.id}_{stamp}_{safe_name}"
    stage = "connecting to Telegram to download the uploaded CV"
    try:
        # Transient outbound network failures can happen on free hosting. Retry the Telegram
        # file download before failing the user's CV workflow.
        tg_file = None
        for attempt in range(3):
            try:
                tg_file = await context.bot.get_file(doc.file_id)
                await tg_file.download_to_drive(custom_path=str(source_path))
                break
            except Exception as network_exc:
                error_name = type(network_exc).__name__.lower()
                error_text = str(network_exc).lower()
                is_connection_error = any(term in error_name + " " + error_text for term in (
                    "connecterror", "timeoutexception", "networkerror", "temporarily unavailable"
                ))
                if not is_connection_error or attempt == 2:
                    raise
                log.warning("Telegram CV download connection failed (attempt %s/3); retrying", attempt + 1)
                await asyncio.sleep(2 ** (attempt + 1))

        stage = "extracting text from the uploaded CV"
        source_text = extract_text(source_path)
        if len(source_text.strip()) < 80:
            raise ValueError("I could not extract enough text. If this is a scanned PDF, please upload a text-based PDF or DOCX.")
        stage = "generating the tailored CV with the configured AI provider"
        cv_text = await create_cv(source_text, jd)
        if len(cv_text) < 100:
            raise RuntimeError("The AI provider returned an empty or incomplete CV. Please retry.")
        stage = "creating the PDF and DOCX files"
        base = OUT_DIR / f"{msg.from_user.id}_{stamp}_tailored_cv"
        docx_path, pdf_path = Path(str(base)+".docx"), Path(str(base)+".pdf")
        write_docx(cv_text, docx_path)
        write_pdf(cv_text, pdf_path)
        stage = "sending the generated CV files back to Telegram"
        # Privacy-first mode: do not store CV/job-description history in SQLite.
        with open(docx_path, "rb") as f:
            await msg.reply_document(document=f, filename="Officium_Tailored_CV.docx", caption="Editable tailored CV (DOCX). Please review every detail before using.")
        with open(pdf_path, "rb") as f:
            await msg.reply_document(document=f, filename="Officium_Tailored_CV.pdf", caption="PDF version of your tailored CV.")
        await msg.reply_text("CV generation completed. Review dates, contact details, and every claim before submitting. No application has been submitted.")
        context.user_data.clear()
        clear_active_jd(update.effective_user.id)
    except Exception as e:
        log.exception("CV workflow failed")
        error_name = type(e).__name__
        error_detail = str(e).strip() or "(the network library returned no additional details)"
        log.error("CV workflow failed during stage '%s' (%s): %s", stage, error_name, error_detail)
        await msg.reply_text(
            f"I couldn't complete this CV while {stage}. "
            f"Error: {error_name}: {error_detail[:500]}\n"
            "Your job description is saved. You can upload the CV again to retry."
        )
    finally:
        # Remove uploaded and generated files after delivery/processing to prevent storage growth.
        for cleanup_path in (source_path, locals().get("docx_path"), locals().get("pdf_path")):
            try:
                if cleanup_path:
                    Path(cleanup_path).unlink(missing_ok=True)
            except Exception:
                log.warning("Could not remove temporary file: %s", cleanup_path)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Commands: /start begins a CV workflow; /cancel clears the current workflow; /help shows this message. Send a job description first, then upload a PDF/DOCX CV.")

def build_application():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app

async def run_webhook():
    external_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url:
        raise SystemExit("RENDER_EXTERNAL_URL is missing for webhook mode.")
    app = build_application()
    await app.initialize()
    await app.start()

    # Process Telegram updates sequentially. If CV and JD updates are handled concurrently,
    # the CV handler can run first and incorrectly claim that no job description was saved.
    update_queue = asyncio.Queue()

    async def update_worker():
        while True:
            update = await update_queue.get()
            try:
                await app.process_update(update)
            except Exception:
                log.exception("Queued Telegram update processing failed")
            finally:
                update_queue.task_done()

    worker_task = asyncio.create_task(update_worker())

    async def telegram_webhook(request):
        if request.method != "POST":
            return web.Response(status=405)
        try:
            payload = await request.json()
            update_id = payload.get("update_id")
            # Acknowledge Telegram promptly; long CV generation must not make Telegram retry the same webhook update.
            if update_id is not None:
                seen = db.execute("SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)).fetchone()
                if seen:
                    return web.Response(text="ok")
                db.execute("INSERT INTO processed_updates(update_id, processed_at) VALUES (?, ?)",
                           (update_id, datetime.now(timezone.utc).isoformat()))
                db.commit()
            update = Update.de_json(payload, app.bot)
            await update_queue.put(update)
            return web.Response(text="ok")
        except Exception:
            log.exception("Failed to process Telegram webhook update")
            return web.Response(status=400, text="invalid update")

    server = web.Application(client_max_size=16 * 1024 * 1024)
    server.router.add_get("/", lambda request: web.Response(text="Officium CV Bot is running"))
    server.router.add_post("/telegram", telegram_webhook)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
    await site.start()
    await app.bot.set_webhook(f"{external_url}/telegram", max_connections=1)
    log.info("Officium CV Bot webhook server is running with sequential update processing")
    try:
        await asyncio.Event().wait()
    finally:
        await app.bot.delete_webhook()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        await app.stop()
        await app.shutdown()

def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Configure it in hosting environment settings; never commit it to GitHub.")
    if not OPENAI_KEY and not GEMINI_KEY:
        log.warning("No AI provider key configured. Bot can start, but CV generation will fail until a provider key is added.")
    if os.getenv("RENDER_EXTERNAL_URL"):
        asyncio.run(run_webhook())
    else:
        app = build_application()
        log.info("Starting Officium CV Bot polling process")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
