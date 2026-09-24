# -*- coding: utf-8 -*-
"""
Telegram-бот "аналог PromptX": фото -> вирусное видео (шаблон "Танец на 12 млн" и др.)

Провайдер видео по умолчанию: fal.ai (модель Seedance — та, что использует PromptX).
  - Регистрация: https://fal.ai  (пробные кредиты, карта не нужна)
  - Ключ: Key -> Create Key

Запуск:
  pip install -r requirements.txt
  export BOT_TOKEN="токен от @BotFather"
  export FAL_KEY="ключ с fal.ai"
  python bot.py
"""

import os
import io
import json
import asyncio
import logging

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dance_bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
FAL_KEY   = os.environ.get("FAL_KEY", "")

# Модель Seedance с мультиреференсом (несколько персонажей).
# Если fal переименует endpoint — поменяйте только эту строку:
FAL_MODEL = os.environ.get(
    "FAL_MODEL",
    "fal-ai/bytedance/seedance/v1.5/pro/image-to-video",
)

MAX_PHOTOS = 3
POLL_INTERVAL = 8
POLL_TIMEOUT  = 600

# ---------------------------------------------------------------------------
TEMPLATES = {
    "dance_12m": {
        "title": "Танец на 12 млн",
        "min_photos": 1,
        "roles": ["кто танцует", "кто снимает на телефон", "кто выходит из машины"],
        "prompt": (
            "Пародия на вирусный танцевальный тренд: человек с первого фото танцует "
            "на открытой парковке перед торговым центром, человек со второго фото снимает "
            "его на телефон, человек с третьего фото выходит из машины. Камера в руках "
            "(handheld), слегка трясётся, реалистичная съёмка на телефон, дневной свет."
        ),
    },
    "car_exit": {
        "title": "Эффектный выход из машины",
        "min_photos": 1,
        "roles": ["кто выходит из машины", "кто встречает"],
        "prompt": (
            "Человек с первого фото эффектно выходит из чёрного автомобиля, человек со "
            "второго фото встречает его. Кинематографично, медленное движение камеры, "
            "вечерний свет."
        ),
    },
    "runway": {
        "title": "Подиум",
        "min_photos": 1,
        "roles": ["модель на подиуме", "зрители/фотографы"],
        "prompt": (
            "Человек с первого фото уверенно идёт по подиуму, вспышки фотоаппаратов, "
            "публика. Fashion-съёмка, стильно, реалистично."
        ),
    },
    "custom": {
        "title": "Свой промпт",
        "min_photos": 1,
        "roles": ["главный герой", "второй персонаж", "третий персонаж"],
        "prompt": None,
    },
}

CHOOSE_TEMPLATE, GET_PHOTOS, GET_PROMPT = range(3)


# ---------------------------------------------------------------------------
def kb_templates():
    rows = [[InlineKeyboardButton(t["title"], callback_data=key)] for key, t in TEMPLATES.items()]
    return InlineKeyboardMarkup(rows)


def kb_done(n_photos, min_needed):
    if n_photos >= min_needed:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Готово ({n_photos} фото), сгенерировать", callback_data="generate")
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"⏳ Пришли минимум {min_needed} фото (сейчас: {n_photos})", callback_data="noop")
    ]])


# ---------------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Привет! Сделаю из твоих фото вирусное видео (аналог PromptX).\n\n"
        "1️⃣ Выбери шаблон\n2️⃣ Пришли 1–3 фото в указанном порядке\n3️⃣ Получи видео\n\n"
        "⏱ Генерация занимает 1–5 минут.",
        reply_markup=kb_templates(),
    )
    return CHOOSE_TEMPLATE


async def on_template(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data
    if key not in TEMPLATES:
        return CHOOSE_TEMPLATE
    t = TEMPLATES[key]
    ctx.user_data["template"] = t
    ctx.user_data["photos"] = []

    if key == "custom":
        await q.message.reply_text("Напиши, что должно происходить в видео:")
        return GET_PROMPT

    roles = "\n".join(f"{i+1}) {r}" for i, r in enumerate(t["roles"]))
    await q.message.reply_text(
        f"🎭 Шаблон: <b>{t['title']}</b>\n\nПришли фото в таком порядке:\n{roles}",
        parse_mode="HTML",
        reply_markup=kb_done(0, t["min_photos"]),
    )
    return GET_PHOTOS


async def on_custom_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = dict(ctx.user_data["template"])
    t["prompt"] = update.message.text
    ctx.user_data["template"] = t
    await update.message.reply_text("Отлично! Теперь пришли 1–3 фото героев видео.",
                                    reply_markup=kb_done(0, 1))
    return GET_PHOTOS


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photos = ctx.user_data.setdefault("photos", [])
    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text("Максимум 3 фото. Жми «Готово».")
        return GET_PHOTOS
    f = await update.message.photo[-1].get_file()
    buf = io.BytesIO()
    await f.download_to_memory(buf)
    photos.append(buf.getvalue())
    await update.message.reply_text(
        f"📸 Фото {len(photos)} принято.",
        reply_markup=kb_done(len(photos), ctx.user_data["template"]["min_photos"]),
    )
    return GET_PHOTOS


async def on_generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    t = ctx.user_data.get("template", {})
    photos = ctx.user_data.get("photos", [])
    if not photos or not t.get("prompt"):
        await q.message.reply_text("Что-то пошло не так, начни заново: /start")
        return ConversationHandler.END

    msg = await q.message.reply_text("⏫ Загружаю фото...")
    try:
        image_urls = [await upload_to_catbox(p) for p in photos]
        await msg.edit_text("🎬 Отправляю задание в генератор (Seedance)...")
        status_url = await fal_submit(t["prompt"], image_urls)
        await msg.edit_text("⏳ Видео генерируется (1–5 минут)...")
        video_url = await fal_wait(status_url)
        await msg.edit_text("✅ Готово! Скачиваю видео...")
        async with aiohttp.ClientSession() as s:
            async with s.get(video_url) as r:
                video = await r.read()
        await msg.delete()
        await q.message.reply_video(io.BytesIO(video), caption=f"🎬 {t['title']}",
                                    supports_streaming=True)
    except Exception as e:
        log.exception("generation failed")
        await msg.edit_text(f"❌ Ошибка: {e}\n\nНачни заново: /start")
    finally:
        ctx.user_data.clear()
    return ConversationHandler.END


async def on_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ок, отменил. Начать заново: /start")
    ctx.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Бесплатный анонимный хостинг картинок (нужен fal.ai — он принимает только URL)
async def upload_to_catbox(data: bytes) -> str:
    form = aiohttp.FormData()
    form.add_field("reqtype", "fileupload")
    form.add_field("fileToUpload", data, filename="photo.jpg",
                   content_type="image/jpeg")
    async with aiohttp.ClientSession() as s:
        async with s.post("https://catbox.moe/user/api.php",
                          data=form,
                          timeout=aiohttp.ClientTimeout(total=60)) as r:
            url = (await r.text()).strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Загрузка фото не удалась: {url[:200]}")
    return url


# fal.ai queue API
async def fal_headers():
    return {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}


async def fal_submit(prompt: str, image_urls: list[str]) -> str:
    payload = {
        "prompt": prompt,
        "image_url": image_urls[0],          # главный персонаж
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "duration": "5s",
    }
    if len(image_urls) > 1:
        payload["image_urls"] = image_urls   # мультиреференс (остальные персонажи)
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"https://queue.fal.run/{FAL_MODEL}",
            headers=await fal_headers(),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            data = await r.json()
            if r.status >= 400:
                raise RuntimeError(f"fal.ai {r.status}: {json.dumps(data, ensure_ascii=False)[:300]}")
    status_url = data.get("status_url")
    if not status_url:
        raise RuntimeError(f"Неожиданный ответ fal.ai: {json.dumps(data)[:300]}")
    return status_url


async def fal_wait(status_url: str) -> str:
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        async with aiohttp.ClientSession() as s:
            async with s.get(status_url, headers=await fal_headers(),
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        status = data.get("status", "")
        if status == "COMPLETED":
            video_url = (data.get("video", {}) or {}).get("url")
            if not video_url and data.get("videos"):
                video_url = data["videos"][0].get("url")
            if video_url:
                return video_url
            raise RuntimeError(f"Видео готово, но URL не найден: {json.dumps(data)[:300]}")
        if status in ("FAILED", "CANCELED"):
            raise RuntimeError(f"Генерация не удалась: {json.dumps(data, ensure_ascii=False)[:300]}")
    raise TimeoutError("Генерация заняла больше 10 минут")



# ---------------------------------------------------------------------------
# Встроенный веб-сервер для хостинга (Render и т.п.): без него Render "усыпляет"
# бесплатные сервисы. /health пингует UptimeRobot -> бот работает 24/7.
from aiohttp import web


async def _health(request):
    return web.Response(text="ok")


async def main_async():
    webapp = web.Application()
    webapp.router.add_get("/health", _health)
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()

    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSE_TEMPLATE: [CallbackQueryHandler(on_template, pattern="^(dance_12m|car_exit|runway|custom)$")],
            GET_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_custom_prompt)],
            GET_PHOTOS: [
                MessageHandler(filters.PHOTO, on_photo),
                CallbackQueryHandler(on_generate, pattern="^generate$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", on_cancel)],
        per_message=False,
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer()))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Бот запущен (провайдер: fal.ai / %s)", FAL_MODEL)
    await asyncio.Event().wait()  # работаем вечно


if __name__ == "__main__":
    asyncio.run(main_async())
