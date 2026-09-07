import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
from telegram import Update, InputFile, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("gst-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "gst-insights-api.p.rapidapi.com").strip()

GST_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{13}$")
MAX_CONCURRENT = 10  # Pro plan: stay within 10 requests/sec.


def menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 GSTIN Search", "🏢 Name Search"],
            ["📂 Bulk GSTIN Search", "📂 Bulk Name Search"],
        ],
        resize_keyboard=True,
    )


def clean_name(s: str) -> str:
    # The API path is shown without spaces in the user's cURL example.
    return re.sub(r"\s+", "", s.strip())


def valid_gstin(s: str) -> bool:
    return bool(GST_RE.fullmatch(s.strip().upper()))


def address_text(record: dict) -> str:
    p = record.get("principalAddress") or {}
    a = p.get("address") or {}
    parts = []

    building = a.get("buildingNumber", "")
    building_name = a.get("buildingName", "")
    floor = a.get("floorNumber", "")
    street = a.get("street", "")
    location = a.get("location", "")
    locality = a.get("locality", "")
    district = a.get("district", "")
    state = a.get("stateCode", "")
    pin = a.get("pincode", "")

    # Keep useful fields, avoiding NULL/empty values.
    for x in [building, building_name, floor, street, locality, location, district]:
        if x and str(x).strip() and str(x).strip().upper() != "NULL":
            parts.append(str(x).strip())

    # State + PIN on the last line, matching the requested style.
    last = " - ".join([x for x in [state, pin] if x and str(x).strip()])
    if last:
        parts.append(last)

    return ", ".join(parts) if parts else "Not Available"


def nature_text(record: dict) -> str:
    value = record.get("natureOfBusinessActivity") or []
    if isinstance(value, list):
        return ", ".join(str(x) for x in value if x)
    return str(value)


def format_record(record: dict) -> str:
    gst = record.get("gstNumber", "Not Available")
    legal = record.get("legalName", "Not Available")
    trade = record.get("tradeName", "Not Available")
    status = str(record.get("status", "Not Available"))
    status_display = "✅ Active" if status.lower() == "active" else f"❌ {status}"
    registration = record.get("registrationDate", "Not Available")
    constitution = record.get("constitutionOfBusiness", "Not Available")
    nature = nature_text(record)

    return (
        "🔎 GST DETAILS\n\n"
        f"GST: {gst}\n\n"
        f"Legal Name:\n{legal}\n\n"
        f"Trade Name:\n{trade}\n\n"
        f"Status:\n{status_display}\n\n"
        f"Principal Address:\n{address_text(record)}\n\n"
        f"Registration Date:\n{registration}\n\n"
        f"Constitution:\n{constitution}\n\n"
        f"Nature:\n{nature}"
    )


def extract_records(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


async def api_get(path: str):
    url = f"https://{RAPIDAPI_HOST}/{path.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": RAPIDAPI_HOST,
        "x-rapidapi-key": RAPIDAPI_KEY,
    }
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.get(url, headers=headers)
        try:
            payload = r.json()
        except Exception:
            payload = {"message": r.text}
        return r.status_code, payload


async def gst_lookup(gst: str):
    return await api_get(f"getGSTDetailsUsingGST/{gst}")


async def name_lookup(name: str):
    return await api_get(f"getGSTDetailsUsingCompanyName/{clean_name(name)}")


def unique_gst(records):
    seen = set()
    out = []
    for r in records:
        gst = str(r.get("gstNumber", "")).upper().strip()
        if gst and gst not in seen:
            seen.add(gst)
            out.append(r)
    return out


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 GST SEARCH BOT\n\nChoose an option:",
        reply_markup=menu(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=menu())


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🔍 GSTIN Search":
        context.user_data["mode"] = "single_gst"
        await update.message.reply_text("GSTIN bhejo:")
        return

    if text == "🏢 Name Search":
        context.user_data["mode"] = "single_name"
        await update.message.reply_text("Company / Legal / Trade name bhejo:")
        return

    if text == "📂 Bulk GSTIN Search":
        context.user_data["mode"] = "bulk_gst"
        await update.message.reply_text("📂 Ab .txt file upload karo. Har line me ek GSTIN.")
        return

    if text == "📂 Bulk Name Search":
        context.user_data["mode"] = "bulk_name"
        await update.message.reply_text("📂 Ab .txt file upload karo. Har line me ek company name.")
        return

    mode = context.user_data.get("mode")

    if mode == "single_gst":
        gst = text.upper().replace(" ", "")
        if not valid_gstin(gst):
            await update.message.reply_text("❌ GSTIN format invalid hai.")
            return
        await do_single_gst(update, gst)
        return

    if mode == "single_name":
        if not text:
            await update.message.reply_text("❌ Company name empty nahi ho sakta.")
            return
        await do_single_name(update, text)
        return

    await update.message.reply_text(
        "Pehle menu se option choose karo.",
        reply_markup=menu(),
    )


async def do_single_gst(update, gst):
    msg = await update.message.reply_text("🔎 Searching...")
    try:
        status, payload = await gst_lookup(gst)
        records = unique_gst(extract_records(payload))
        if status == 200 and records:
            # Normally one record for a GSTIN.
            await msg.edit_text(format_record(records[0]))
        elif status in (401, 403):
            await msg.edit_text("❌ RapidAPI authorization error. API key/subscription check karo.")
        elif status == 429:
            await msg.edit_text("⏳ API rate limit hit. Thodi der baad try karo.")
        else:
            await msg.edit_text("❌ GST DETAILS NOT FOUND")
    except Exception:
        log.exception("single GST error")
        await msg.edit_text("❌ API error. Railway logs check karo.")


async def do_single_name(update, name):
    msg = await update.message.reply_text("🏢 Searching company name...")
    try:
        status, payload = await name_lookup(name)
        records = unique_gst(extract_records(payload))
        if status != 200 or not records:
            if status in (401, 403):
                await msg.edit_text("❌ RapidAPI authorization error. API key/subscription check karo.")
            elif status == 429:
                await msg.edit_text("⏳ API rate limit hit. Thodi der baad try karo.")
            else:
                await msg.edit_text("❌ No GST records found.")
            return

        # Telegram has a message-size limit, so send records in separate messages.
        await msg.delete()
        for record in records:
            await update.message.reply_text(format_record(record))
            await asyncio.sleep(0.05)
    except Exception:
        log.exception("single name error")
        await msg.edit_text("❌ API error. Railway logs check karo.")


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    if mode not in ("bulk_gst", "bulk_name"):
        await update.message.reply_text("Pehle Bulk option select karo.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("❌ Sirf .txt file upload karo.")
        return

    tg_file = await doc.get_file()
    work = Path("/tmp") / f"{update.effective_user.id}_{doc.file_unique_id}.txt"
    await tg_file.download_to_drive(work)

    try:
        lines = work.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        await update.message.reply_text("❌ TXT read nahi ho paayi.")
        return

    if mode == "bulk_gst":
        await process_bulk_gst(update, lines)
    else:
        await process_bulk_names(update, lines)


async def process_bulk_gst(update, lines):
    raw = [x.strip().upper().replace(" ", "") for x in lines if x.strip()]
    unique = list(dict.fromkeys(raw))
    duplicates = len(raw) - len(unique)

    valid = [x for x in unique if valid_gstin(x)]
    invalid = [x for x in unique if not valid_gstin(x)]

    status_msg = await update.message.reply_text(
        f"📂 File received\n\n"
        f"Total lines: {len(raw)}\n"
        f"Unique GSTIN: {len(unique)}\n"
        f"Duplicates removed: {duplicates}\n"
        f"Valid GSTIN: {len(valid)}\n"
        f"Invalid GSTIN: {len(invalid)}\n\n"
        f"⏳ Processing..."
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results, failed = [], []

    async def one(gst):
        async with semaphore:
            try:
                code, payload = await gst_lookup(gst)
                recs = unique_gst(extract_records(payload))
                if code == 200 and recs:
                    return gst, recs[0]
                return gst, None
            except Exception:
                return gst, None

    tasks = [asyncio.create_task(one(g)) for g in valid]
    done = 0
    for task in asyncio.as_completed(tasks):
        gst, record = await task
        done += 1
        if record:
            results.append(record)
        else:
            failed.append(gst)
        if done % 50 == 0 or done == len(tasks):
            try:
                await status_msg.edit_text(
                    f"⏳ Processing GSTIN...\n\n"
                    f"Progress: {done}/{len(tasks)}\n"
                    f"Successful: {len(results)}\n"
                    f"Failed: {len(failed)}"
                )
            except Exception:
                pass

    out = Path("/tmp") / f"gst_results_{update.effective_user.id}.txt"
    out.write_text("\n\n" + "\n\n".join(format_record(r) for r in results), encoding="utf-8")

    bad = Path("/tmp") / f"failed_gst_{update.effective_user.id}.txt"
    bad.write_text("\n".join(invalid + failed), encoding="utf-8")

    await status_msg.edit_text(
        f"📊 COMPLETE\n\n"
        f"Input lines: {len(raw)}\n"
        f"Unique GSTIN: {len(unique)}\n"
        f"Duplicates removed: {duplicates}\n"
        f"Valid: {len(valid)}\n"
        f"Successful: {len(results)}\n"
        f"Failed: {len(failed)}\n"
        f"Invalid format: {len(invalid)}"
    )

    with out.open("rb") as f:
        await update.message.reply_document(InputFile(f, filename="GST_RESULTS.txt"))

    if invalid or failed:
        with bad.open("rb") as f:
            await update.message.reply_document(InputFile(f, filename="FAILED_OR_INVALID_GST.txt"))


async def process_bulk_names(update, lines):
    names = [x.strip() for x in lines if x.strip()]
    unique = list(dict.fromkeys(names))
    duplicates = len(names) - len(unique)

    status_msg = await update.message.reply_text(
        f"📂 File received\n\n"
        f"Total names: {len(names)}\n"
        f"Unique names: {len(unique)}\n"
        f"Duplicates removed: {duplicates}\n\n"
        f"⏳ Processing..."
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    all_records = []
    no_result = []

    async def one(name):
        async with semaphore:
            try:
                code, payload = await name_lookup(name)
                recs = unique_gst(extract_records(payload))
                return name, recs if code == 200 else []
            except Exception:
                return name, []

    tasks = [asyncio.create_task(one(n)) for n in unique]
    done = 0

    for task in asyncio.as_completed(tasks):
        name, recs = await task
        done += 1
        if recs:
            all_records.extend(recs)
        else:
            no_result.append(name)

        if done % 25 == 0 or done == len(tasks):
            try:
                await status_msg.edit_text(
                    f"⏳ Processing names...\n\n"
                    f"Progress: {done}/{len(tasks)}\n"
                    f"GST records found: {len(unique_gst(all_records))}"
                )
            except Exception:
                pass

    all_records = unique_gst(all_records)

    out = Path("/tmp") / f"name_results_{update.effective_user.id}.txt"
    out.write_text("\n\n" + "\n\n".join(format_record(r) for r in all_records), encoding="utf-8")

    no = Path("/tmp") / f"names_no_result_{update.effective_user.id}.txt"
    no.write_text("\n".join(no_result), encoding="utf-8")

    await status_msg.edit_text(
        f"📊 COMPLETE\n\n"
        f"Names searched: {len(unique)}\n"
        f"GST records found: {len(all_records)}\n"
        f"Names with no result: {len(no_result)}"
    )

    with out.open("rb") as f:
        await update.message.reply_document(InputFile(f, filename="NAME_GST_RESULTS.txt"))

    if no_result:
        with no.open("rb") as f:
            await update.message.reply_document(InputFile(f, filename="NAMES_NO_RESULT.txt"))


async def error_handler(update, context):
    log.error("Telegram error: %s", context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    if not RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY is missing")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)

    log.info("Bot starting")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
