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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DB_PATH = Path(os.getenv("DATABASE_PATH", "/data/officium_bot.sqlite3"))
OUT_DIR = Path(os.getenv("OUTPUT_DIR", "/data/generated"))
MAX_UPLOAD = 12 * 1024 * 1024

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS cv_jobs (
 id INTEGER PRIMARY KEY, user_id INTEGER, created_at TEXT, filename TEXT,
 jd TEXT, status TEXT, output_docx TEXT, output_pdf TEXT)""")
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
Rules: use ONLY facts supported by the source CV; never invent employers, dates, degrees, skills, certifications, tools, metrics or achievements. Do not claim a guaranteed ATS score. Use a clean, ATS-friendly single-column layout. Keep contact details as found in source. Clearly prioritize relevant real experience and keywords from the job description, without keyword stuffing. Return only the finished CV text with clear section headings.

SOURCE CV:
{source[:24000]}

TARGET JOB DESCRIPTION:
{jd[:14000]}
"""
    if OPENAI_KEY:
        client = AsyncOpenAI(api_key=OPENAI_KEY)
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role":"system","content":"You are a meticulous CV editor. Accuracy over embellishment."},
                      {"role":"user","content":prompt}],
            temperature=0.2)
        return (response.choices[0].message.content or "").strip()
    if GEMINI_KEY:
        client = genai.Client(api_key=GEMINI_KEY)
        result = await asyncio.to_thread(client.models.generate_content, model=GEMINI_MODEL, contents=prompt)
        return (getattr(result, "text", "") or "").strip()
    raise RuntimeError("No AI provider is configured. Add OPENAI_API_KEY or GEMINI_API_KEY in hosting environment settings.")

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
    await update.message.reply_text(
        "Welcome to Officium CV Bot.\n\n1) Send the full job description as a text message.\n"
        "2) Then upload your current CV as PDF or DOCX.\n3) I will create a tailored CV in PDF and DOCX.\n\n"
        "I will not intentionally add experience or qualifications that are not in your source CV. Use /cancel to reset.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Current CV workflow cleared. Send /start to begin again.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (update.message.text or "").strip()
    if not msg:
        return
    if len(msg) < 100:
        await update.message.reply_text("Please send the complete job description (ideally 100+ characters), or use /start to begin.")
        return
    context.user_data["jd"] = msg[:14000]
    await update.message.reply_text("Job description saved. Now upload the candidate CV as a PDF or DOCX file.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    jd = context.user_data.get("jd")
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
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(custom_path=str(source_path))
        source_text = extract_text(source_path)
        if len(source_text.strip()) < 80:
            raise ValueError("I could not extract enough text. If this is a scanned PDF, please upload a text-based PDF or DOCX.")
        cv_text = await create_cv(source_text, jd)
        if len(cv_text) < 100:
            raise RuntimeError("The AI provider returned an empty or incomplete CV. Please retry.")
        base = OUT_DIR / f"{msg.from_user.id}_{stamp}_tailored_cv"
        docx_path, pdf_path = Path(str(base)+".docx"), Path(str(base)+".pdf")
        write_docx(cv_text, docx_path)
        write_pdf(cv_text, pdf_path)
        # Privacy-first mode: do not store CV/job-description history in SQLite.
        with open(docx_path, "rb") as f:
            await msg.reply_document(document=f, filename="Officium_Tailored_CV.docx", caption="Editable tailored CV (DOCX). Please review every detail before using.")
        with open(pdf_path, "rb") as f:
            await msg.reply_document(document=f, filename="Officium_Tailored_CV.pdf", caption="PDF version of your tailored CV.")
        await msg.reply_text("CV generation completed. Review dates, contact details, and every claim before submitting. No application has been submitted.")
        context.user_data.clear()
    except Exception as e:
        log.exception("CV workflow failed")
        await msg.reply_text("I couldn't complete this CV. " + str(e)[:700] + "\nPlease check the file and hosting provider logs, then try again.")
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

    async def telegram_webhook(request):
        if request.method != "POST":
            return web.Response(status=405)
        try:
            payload = await request.json()
            update = Update.de_json(payload, app.bot)
            await app.process_update(update)
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
    await app.bot.set_webhook(f"{external_url}/telegram")
    log.info("Officium CV Bot webhook server is running")
    try:
        await asyncio.Event().wait()
    finally:
        await app.bot.delete_webhook()
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
