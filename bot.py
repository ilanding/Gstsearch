import os
import re
import io
import asyncio
import requests
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY") or os.getenv("X_RAPIDAPI_KEY")

RAPIDAPI_HOST = "gst-insights-api.p.rapidapi.com"
BASE_URL = f"https://{RAPIDAPI_HOST}"

HEADERS = {
    "Content-Type": "application/json",
    "x-rapidapi-host": RAPIDAPI_HOST,
    "x-rapidapi-key": RAPIDAPI_KEY or "",
}

# Keeps bulk searches below the Pro plan's 10 requests/second rate limit.
REQUEST_CONCURRENCY = 8
REQUEST_TIMEOUT = 30

SEARCH_GSTIN = "search_gstin"
SEARCH_NAME = "search_name"
BULK_GSTIN = "bulk_gstin"
BULK_NAME = "bulk_name"


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔎 Search GSTIN", callback_data=SEARCH_GSTIN),
            InlineKeyboardButton("🏢 Search by Name", callback_data=SEARCH_NAME),
        ],
        [
            InlineKeyboardButton("📂 Bulk GSTIN", callback_data=BULK_GSTIN),
            InlineKeyboardButton("📄 Bulk Name Search", callback_data=BULK_NAME),
        ],
    ])


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def format_address(address):
    if not address:
        return ""

    parts = []
    # Same useful order as the requested output format.
    for key in [
        "buildingNumber",
        "buildingName",
        "floorNumber",
        "street",
        "locality",
        "location",
        "district",
        "stateCode",
        "pincode",
    ]:
        value = clean(address.get(key))
        if value and value.upper() != "NULL" and value not in parts:
            parts.append(value)

    return ", ".join(parts)


def format_record(record):
    gst = clean(record.get("gstNumber"))
    legal = clean(record.get("legalName"))
    trade = clean(record.get("tradeName"))
    status = clean(record.get("status"))
    reg_date = clean(record.get("registrationDate"))
    constitution = clean(record.get("constitutionOfBusiness"))

    principal = record.get("principalAddress") or {}
    address = principal.get("address") or {}
    principal_address = format_address(address)

    nature = record.get("natureOfBusinessActivity") or principal.get("nature") or ""
    if isinstance(nature, list):
        nature = ", ".join(clean(x) for x in nature if clean(x))
    else:
        nature = clean(nature)

    return (
        "🔎 GST DETAILS\n\n"
        f"GST: {gst}\n\n"
        f"Legal Name:\n{legal}\n\n"
        f"Trade Name:\n{trade}\n\n"
        f"Status:\n{'✅ ' if status.lower() == 'active' else ''}{status}\n\n"
        f"Principal Address:\n{principal_address}\n\n"
        f"Registration Date:\n{reg_date}\n\n"
        f"Constitution:\n{constitution}\n\n"
        f"Nature:\n{nature}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    )


def extract_records(payload):
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        # Some APIs return a single object in data.
        return [data]

    # Fallback for APIs that return the record directly.
    if any(k in payload for k in ("gstNumber", "legalName", "tradeName")):
        return [payload]

    return []


def api_get(path):
    url = BASE_URL + path
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

    # Return useful API errors instead of crashing the bot.
    try:
        payload = response.json()
    except Exception:
        payload = {}

    if response.status_code != 200:
        message = ""
        if isinstance(payload, dict):
            message = clean(
                payload.get("message")
                or payload.get("error")
                or payload.get("detail")
            )
        raise RuntimeError(f"HTTP {response.status_code}" + (f": {message}" if message else ""))

    return payload


def search_gstin(gstin):
    return extract_records(api_get(
        f"/getGSTDetailsUsingGST/{quote(gstin.strip(), safe='')}"
    ))


def search_name(name):
    # The API endpoint expects the company name as one URL path segment.
    return extract_records(api_get(
        f"/getGSTDetailsUsingCompanyName/{quote(name.strip(), safe='')}"
    ))


async def safe_search_gstin(gstin):
    try:
        return gstin, search_gstin(gstin), None
    except Exception as e:
        return gstin, [], str(e)


async def safe_search_name(name, semaphore):
    async with semaphore:
        try:
            records = await asyncio.to_thread(search_name, name)
            return name, records, None
        except Exception as e:
            return name, [], str(e)


def normalize_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def valid_gstin(value):
    return bool(re.fullmatch(r"[0-9A-Z]{15}", value.upper()))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = None
    await update.message.reply_text(
        "🔎 GST Search Bot\n\nChoose an option:",
        reply_markup=main_keyboard(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = None
    await update.message.reply_text(
        "Choose an option:",
        reply_markup=main_keyboard(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["mode"] = query.data

    prompts = {
        SEARCH_GSTIN: "🔎 Send the GSTIN.\n\nExample:\n27AABCI6363G3ZH",
        SEARCH_NAME: "🏢 Send the company/legal/trade name.\n\nExample:\nReliance Jio Infocomm Limited",
        BULK_GSTIN: "📂 Upload a .txt file with one GSTIN per line.",
        BULK_NAME: "📄 Upload a .txt file with one company name per line.\n\nThe bot will search every name and put all returned records into one TXT file.",
    }

    await query.edit_message_text(
        prompts.get(query.data, "Choose an option."),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu")]
        ]),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")
    text = update.message.text.strip()

    if mode == SEARCH_GSTIN:
        if not valid_gstin(text):
            await update.message.reply_text("❌ GSTIN 15-character format me bhejo.")
            return

        await update.message.reply_text("⏳ GST details search ho rahi hain...")

        try:
            records = await asyncio.to_thread(search_gstin, text)
        except Exception as e:
            await update.message.reply_text(f"❌ API error: {e}")
            return

        if not records:
            await update.message.reply_text(
                "❌ GST record found nahi hua.",
                reply_markup=main_keyboard(),
            )
            return

        output = "\n".join(format_record(r) for r in records)
        await send_txt(update, output, f"GST_{text}.txt")
        return

    if mode == SEARCH_NAME:
        await update.message.reply_text("⏳ Name search ho rahi hai...")

        try:
            records = await asyncio.to_thread(search_name, text)
        except Exception as e:
            await update.message.reply_text(f"❌ API error: {e}")
            return

        if not records:
            await update.message.reply_text(
                "❌ Is name se API ne koi GST record return nahi kiya.",
                reply_markup=main_keyboard(),
            )
            return

        output = "\n".join(format_record(r) for r in records)
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", text)[:60]
        await send_txt(update, output, f"GST_Name_{safe_name}.txt")
        return

    await update.message.reply_text(
        "Pehle menu se option select karo 👇",
        reply_markup=main_keyboard(),
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode")

    if mode not in (BULK_GSTIN, BULK_NAME):
        await update.message.reply_text(
            "Pehle menu se Bulk option select karo 👇",
            reply_markup=main_keyboard(),
        )
        return

    document = update.message.document
    if not document.file_name.lower().endswith(".txt"):
        await update.message.reply_text("❌ Sirf .txt file upload karo.")
        return

    status = await update.message.reply_text("📥 File read ho rahi hai...")
    telegram_file = await document.get_file()
    raw = await telegram_file.download_as_bytearray()

    text = raw.decode("utf-8-sig", errors="ignore")
    items = normalize_lines(text)

    # Remove accidental duplicate inputs while preserving order.
    seen = set()
    unique_items = []
    for item in items:
        key = item.upper() if mode == BULK_GSTIN else item.casefold()
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    if not unique_items:
        await status.edit_text("❌ TXT file empty hai.")
        return

    if mode == BULK_GSTIN:
        # Validate GSTINs first.
        gstins = [x.upper() for x in unique_items if valid_gstin(x)]
        if not gstins:
            await status.edit_text("❌ Valid GSTIN nahi mila.")
            return

        await status.edit_text(
            f"⏳ {len(gstins)} GSTIN search ho rahe hain..."
        )

        results = []
        for i, gstin in enumerate(gstins, 1):
            _, records, error = await safe_search_gstin(gstin)
            if records:
                results.extend(records)
            if i % 25 == 0:
                await status.edit_text(
                    f"⏳ Processing: {i}/{len(gstins)}"
                )

        if not results:
            await status.edit_text("❌ Koi GST record return nahi hua.")
            return

        output = "\n".join(format_record(r) for r in results)
        await status.edit_text(
            f"✅ Complete: {len(gstins)} inputs processed, {len(results)} records found."
        )
        await send_txt(update, output, "Bulk_GST_Results.txt")
        return

    # BULK NAME
    names = unique_items
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)

    await status.edit_text(
        f"⏳ {len(names)} names search ho rahe hain...\n"
        f"Parallel requests: {REQUEST_CONCURRENCY}"
    )

    results = []
    errors = []

    # Process in batches so the bot does not create thousands of tasks at once.
    batch_size = 100
    for start_index in range(0, len(names), batch_size):
        batch = names[start_index:start_index + batch_size]
        batch_results = await asyncio.gather(
            *(safe_search_name(name, semaphore) for name in batch)
        )

        for name, records, error in batch_results:
            if records:
                # Keep the source search name visible before its returned records.
                results.append(
                    f"SEARCH NAME: {name}\n\n" +
                    "\n".join(format_record(r) for r in records)
                )
            elif error:
                errors.append(f"{name} -> {error}")

        done = min(start_index + len(batch), len(names))
        await status.edit_text(
            f"⏳ Processing: {done}/{len(names)}\n"
            f"📌 Records found so far: {sum(1 for _ in results)}"
        )

    if not results:
        await status.edit_text(
            "❌ Kisi bhi name par API ne GST record return nahi kiya."
        )
        return

    output = "\n".join(results)

    if errors:
        output += (
            "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            "API ERRORS\n"
            "━━━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(errors)
        )

    await status.edit_text(
        f"✅ Bulk Name Search complete.\n"
        f"Names processed: {len(names)}\n"
        f"Names with results: {len(results)}"
    )
    await send_txt(update, output, "Bulk_Name_GST_Results.txt")


async def send_txt(update: Update, text: str, filename: str):
    data = text.encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = filename

    await update.effective_chat.send_document(
        document=bio,
        filename=filename,
        caption="📄 GST results",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Don't expose stack traces to users.
    print("Bot error:", context.error)


def run():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN variable missing in Railway.")
    if not RAPIDAPI_KEY:
        raise RuntimeError("RAPIDAPI_KEY variable missing in Railway.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    print("GST Telegram Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
