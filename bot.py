import os
import re
import html
import asyncio
from urllib.parse import quote

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# Railway Variables required:
# BOT_TOKEN=your_telegram_bot_token
# RAPIDAPI_KEY=your_rapidapi_key
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

RAPIDAPI_HOST = "gst-insights-api.p.rapidapi.com"
GSTIN_URL = f"https://{RAPIDAPI_HOST}/getGSTDetailsUsingGST"
NAME_URL = f"https://{RAPIDAPI_HOST}/getGSTDetailsUsingCompanyName"

HEADERS = {
    "Content-Type": "application/json",
    "x-rapidapi-host": RAPIDAPI_HOST,
    "x-rapidapi-key": RAPIDAPI_KEY or "",
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway variable is missing")

if not RAPIDAPI_KEY:
    raise RuntimeError("RAPIDAPI_KEY Railway variable is missing")


# ------------------------- Helpers -------------------------

def clean(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value if x)
    return str(value).strip()


def address_to_text(item):
    """
    Converts the API's principalAddress object into a readable address.
    """
    if not item:
        return "Not available"

    a = item.get("address", item) or {}

    parts = []

    # Keep the same practical order used in the requested output.
    for key in [
        "buildingNumber",
        "buildingName",
        "floorNumber",
        "street",
        "location",
        "locality",
        "district",
        "stateCode",
    ]:
        value = clean(a.get(key))
        if value and value.upper() != "NULL" and value not in parts:
            parts.append(value)

    pincode = clean(a.get("pincode"))
    if pincode:
        parts.append(f"- {pincode}")

    return ", ".join(parts) if parts else "Not available"


def format_gst_record(record):
    """
    EXACT requested user-facing format.
    """
    gst = clean(record.get("gstNumber"))
    legal = clean(record.get("legalName"))
    trade = clean(record.get("tradeName"))
    status = clean(record.get("status")) or "Not available"
    registration = clean(record.get("registrationDate"))
    constitution = clean(record.get("constitutionOfBusiness"))
    nature = clean(record.get("natureOfBusinessActivity"))

    address = address_to_text(record.get("principalAddress"))

    status_icon = "✅" if status.lower() == "active" else "❌" if status.lower() == "cancelled" else "⚠️"

    return (
        "🔎 GST DETAILS\n\n"
        f"GST: {gst or 'Not available'}\n\n"
        f"Legal Name:\n{legal or 'Not available'}\n\n"
        f"Trade Name:\n{trade or 'Not available'}\n\n"
        f"Status:\n{status_icon} {status}\n\n"
        f"Principal Address:\n{address}\n\n"
        f"Registration Date:\n{registration or 'Not available'}\n\n"
        f"Constitution:\n{constitution or 'Not available'}\n\n"
        f"Nature:\n{nature or 'Not available'}"
    )


def extract_records(payload):
    """
    GST Insights API normally returns:
    {"success": true, "data": [...]}

    This also handles a single object in data.
    """
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        # Sometimes a single result may be returned as an object.
        if data.get("gstNumber") or data.get("legalName"):
            return [data]

    # Fallback if the API returns the record directly.
    if payload.get("gstNumber") or payload.get("legalName"):
        return [payload]

    return []


def api_get(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    # Helpful for debugging without exposing the API key.
    response.raise_for_status()

    return response.json()


def search_gstin(gstin):
    gstin = gstin.strip().upper()
    url = f"{GSTIN_URL}/{quote(gstin, safe='')}"
    return extract_records(api_get(url))


def search_name(name):
    name = re.sub(r"\s+", " ", name.strip())
    url = f"{NAME_URL}/{quote(name, safe='')}"
    return extract_records(api_get(url))


# ------------------------- Telegram handlers -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 GST Search Bot\n\n"
        "Single GSTIN:\n"
        "Send a GSTIN directly.\n\n"
        "Name Search:\n"
        "/name Reliance Jio Infocomm Limited\n\n"
        "Bulk Name Search:\n"
        "Use /bulkname and put one company name per line.\n\n"
        "Example:\n"
        "/bulkname\n"
        "Vahan\n"
        "Reliance Jio\n"
        "Tata Motors"
    )
    await update.message.reply_text(text)


async def name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()

    if not name:
        await update.message.reply_text(
            "Example:\n/name Vahan"
        )
        return

    await update.message.reply_text("🔎 Searching GST by name...")

    try:
        records = await asyncio.to_thread(search_name, name)

        if not records:
            await update.message.reply_text(
                f"❌ No GST record found for:\n{name}"
            )
            return

        # Send each matching GST separately.
        for record in records:
            await update.message.reply_text(format_gst_record(record))

    except requests.HTTPError as e:
        await update.message.reply_text(
            f"❌ GST API HTTP error: {e}"
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error while searching name:\n{e}"
        )


async def bulk_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Everything after /bulkname, including new lines.
    raw = update.message.text or ""
    raw = raw.split("\n", 1)[1] if "\n" in raw else " ".join(context.args)

    names = [
        re.sub(r"\s+", " ", x.strip())
        for x in raw.splitlines()
        if x.strip()
    ]

    if not names:
        await update.message.reply_text(
            "Format:\n"
            "/bulkname\n"
            "Vahan\n"
            "Reliance Jio\n"
            "Tata Motors"
        )
        return

    # Safety/practical limit for one Telegram message.
    names = names[:100]

    await update.message.reply_text(
        f"🔎 Bulk name search started...\n"
        f"Names: {len(names)}"
    )

    all_results = []
    not_found = []

    for index, name in enumerate(names, start=1):
        try:
            records = await asyncio.to_thread(search_name, name)

            if records:
                all_results.append((name, records))
            else:
                not_found.append(name)

        except Exception:
            not_found.append(name)

        # Small pause avoids hammering the API.
        await asyncio.sleep(0.15)

    # Send results individually so Telegram message limits are avoided.
    for searched_name, records in all_results:
        await update.message.reply_text(
            f"🔍 Search: {searched_name}\n\n"
            + "\n\n".join(format_gst_record(r) for r in records)
        )

    summary = (
        "✅ BULK SEARCH COMPLETE\n\n"
        f"Total names: {len(names)}\n"
        f"Found: {len(all_results)}\n"
        f"Not found/error: {len(not_found)}"
    )

    if not_found:
        summary += "\n\n❌ Not found:\n" + "\n".join(not_found[:100])

    await update.message.reply_text(summary)


async def gstin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # GSTIN pattern: 15 alphanumeric characters.
    gstin = text.upper().replace(" ", "")

    if not re.fullmatch(r"[0-9A-Z]{15}", gstin):
        await update.message.reply_text(
            "GSTIN 15 characters ka hona chahiye.\n"
            "Name search ke liye:\n"
            "/name Company Name"
        )
        return

    await update.message.reply_text("🔎 Searching GSTIN...")

    try:
        records = await asyncio.to_thread(search_gstin, gstin)

        if not records:
            await update.message.reply_text(
                f"❌ No GST record found for:\n{gstin}"
            )
            return

        for record in records:
            await update.message.reply_text(format_gst_record(record))

    except requests.HTTPError as e:
        await update.message.reply_text(
            f"❌ GST API HTTP error: {e}"
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error while searching GSTIN:\n{e}"
        )


# ------------------------- Main -------------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("name", name_command))
    app.add_handler(CommandHandler("bulkname", bulk_name_command))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, gstin_message)
    )

    print("GST Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
