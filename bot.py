# -*- coding: utf-8 -*-
"""
Telegram-бот "аналог PromptX": фото -> вирусное видео (шаблон "Танец на 12 млн" и др.)

Провайдер видео: PiAPI (Seedance/Kling). Ключ: https://piapi.ai -> Workspace -> API Key
"""

import os
import io
import json
import asyncio
import logging
import traceback
import uuid
from pathlib import Path

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dance_bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
PIAPI_KEY = os.environ.get("PIAPI_KEY", "")

# Модель Seedance 2.0 (та, что использует PromptX). Можно заменить на kling и т.п.
PIAPI_MODEL = os.environ.get("PIAPI_MODEL", "seedance")
PIAPI_TASK_TYPE = os.environ.get("PIAPI_TASK_TYPE", "seedance-2-fast-less-restriction")

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
            "Пародия на вирусный танцевальный тренд: @Image1 танцует "
            "на открытой парковке перед торговым центром, @Image2 снимает "
            "его на телефон, @Image3 выходит из машины. Камера в руках "
            "(handheld), слегка трясётся, реалистичная съёмка на телефон, дневной свет."
        ),
    },
    "car_exit": {
        "title": "Эффектный выход из машины",
        "min_photos": 1,
        "roles": ["кто выходит из машины", "кто встречает"],
        "prompt": (
            "@Image1 эффектно выходит из чёрного автомобиля, человек со "
            "второго фото встречает его. Кинематографично, медленное движение камеры, "
            "вечерний свет."
        ),
    },
    "runway": {
        "title": "Подиум",
        "min_photos": 1,
        "roles": ["модель на подиуме", "зрители/фотографы"],
        "prompt": (
            "@Image1 уверенно идёт по подиуму, вспышки фотоаппаратов, "
            "публика. Fashion-съёмка, стильно, реалистично."
        ),
    },
    "vibe_trip": {
        "title": "Вайб дальних путешествий (Литвин)",
        "min_photos": 1,
        "roles": ["кто танцует/зажигает на заправке", "кто снимает селфи", "кто сидит в машине (ветер в волосах)"],
        "prompt": (
            "Viral road trip vibe video, four scenes. "
            "Scene 1: @Image1 dances energetically and goofs around at a roadside gas station "
            "and truck stop parking lot, truck trailers in the background, handheld phone camera. "
            "Scene 2: @Image2 films himself selfie-style, smiling and waving at the camera, "
            "warm golden hour sunset light at the same gas station, the dancing man visible in the background. "
            "Scene 3: close-up of @Image3 inside a car, wind blows through her hair, "
            "she looks around with a dreamy expression, beige car interior. "
            "Scene 4: the woman gets out of a silver car parked at a roadside and dances playfully "
            "near the car, wearing a black tracksuit, smiling. "
            "Realistic viral TikTok style, natural handheld camera shake."
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
    log.info("cmd_start от user_id=%s", update.effective_user.id)
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
    log.info("on_template: key=%s user=%s", key, q.from_user.id)
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
    log.info("on_photo: всего фото=%d user=%s", len(photos), update.effective_user.id)
    await update.message.reply_text(
        f"📸 Фото {len(photos)} принято. Когда закончишь — нажми кнопку «Готово» под этим сообщением.",
        reply_markup=kb_done(len(photos), ctx.user_data["template"]["min_photos"]),
    )
    return GET_PHOTOS


async def on_generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Работает и от кнопки (callback), и от команды /generate или слова «готово»."""
    q = update.callback_query
    log.info("=== on_generate ВЫЗВАН (callback=%s) user=%s ===", bool(q),
             update.effective_user.id)

    # 1. Сразу пишем пользователю статус — ДО любых других действий
    if q:
        target = q.message
    else:
        target = update.message
    msg = await target.reply_text("⏫ Загружаю фото...")
    log.info("on_generate: стартовое сообщение отправлено")

    # 2. Отвечаем на callback (гасим «часики» на кнопке) — не критично, если упадёт
    if q:
        try:
            await asyncio.wait_for(q.answer(), timeout=5)
        except Exception as e:
            log.warning("q.answer() не удался: %s", e)

    t = ctx.user_data.get("template", {})
    photos = ctx.user_data.get("photos", [])
    if not photos or not t.get("prompt"):
        await msg.edit_text("Что-то пошло не так, начни заново: /start")
        return ConversationHandler.END

    try:
        await msg.edit_text("⏫ Загружаю фото на сервер...")
        image_urls = [await upload_photo(p) for p in photos]
        log.info("on_generate: фото загружены, ссылок=%d", len(image_urls))

        await msg.edit_text("🪪 Регистрация фото в PiAPI (проверка лиц, 1–3 мин)...")
        asset_urls = []
        for u in image_urls:
            aid = await piapi_upload_asset(u)
            await piapi_wait_asset(aid)
            asset_urls.append(f"asset://{aid}")
        log.info("on_generate: ассеты готовы: %s", asset_urls)

        await msg.edit_text("🎬 Отправляю задание в генератор (Seedance)...")
        task_id = await piapi_submit(t["prompt"], asset_urls)
        log.info("on_generate: задача создана, task_id=%s", task_id)

        await msg.edit_text("⏳ Видео генерируется (1–5 минут)...")
        video_url = await piapi_wait(task_id)
        log.info("on_generate: видео готово: %s", video_url)

        await msg.edit_text("✅ Готово! Скачиваю видео...")
        async with aiohttp.ClientSession() as s:
            async with s.get(video_url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                video = await r.read()
        await msg.delete()
        await target.reply_video(io.BytesIO(video), caption=f"🎬 {t['title']}",
                                 supports_streaming=True)
        log.info("on_generate: видео отправлено пользователю")
    except Exception as e:
        log.error("ОШИБКА генерации: %s\n%s", e, traceback.format_exc())
        try:
            await msg.edit_text(f"❌ Ошибка: {e}\n\nНачни заново: /start")
        except Exception:
            pass
    finally:
        ctx.user_data.clear()
    return ConversationHandler.END


async def on_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ок, отменил. Начать заново: /start")
    ctx.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Фото грузятся на сам сервер бота (внешний хостинг не нужен)
UPLOAD_DIR = Path("/tmp/dance_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def public_base_url() -> str:
    """Адрес сервиса: из PUBLIC_BASE_URL либо автоопределение через Render."""
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
    if host:
        return f"https://{host}"
    raise RuntimeError("Не удалось определить адрес сервиса")


async def upload_photo(data: bytes) -> str:
    """Фото -> публичный URL. Сначала telegra.ph, запасной путь — свой сервер."""
    errors = []
    # Путь 1: telegra.ph (свежая форма на КАЖДУЮ попытку)
    for _ in range(2):
        try:
            form = aiohttp.FormData()
            form.add_field("file", data, filename="photo.jpg",
                           content_type="image/jpeg")
            async with aiohttp.ClientSession() as s:
                async with s.post("https://telegra.ph/upload",
                                  data=form,
                                  timeout=aiohttp.ClientTimeout(total=30)) as r:
                    resp = await r.json()
            url = "https://telegra.ph" + resp[0]["src"]
            log.info("upload_photo (telegra.ph): %s", url)
            return url
        except Exception as e:
            errors.append(f"telegra.ph: {str(e)[:120]}")
            await asyncio.sleep(1)
    # Путь 2: хостим на самом боте (инстанс сейчас активен — URL доступен)
    try:
        name = f"{uuid.uuid4().hex}.jpg"
        (UPLOAD_DIR / name).write_bytes(data)
        url = f"{public_base_url()}/img/{name}"
        log.info("upload_photo (self): %s", url)
        return url
    except Exception as e:
        errors.append(f"self: {str(e)[:120]}")
    raise RuntimeError("Загрузка фото не удалась: " + " | ".join(errors))


async def piapi_upload_asset(url: str) -> str:
    """Загружает фото в Asset Library PiAPI, возвращает asset_id."""
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.piapi.ai/api/v1/asset/upload",
            headers=await piapi_headers(),
            json={"url": url, "asset_type": "Image", "name": "ref"},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            data = await r.json()
            if r.status >= 400:
                raise RuntimeError(f"PiAPI asset upload {r.status}: {json.dumps(data, ensure_ascii=False)[:300]}")
    asset_id = data.get("asset_id") or (data.get("data") or {}).get("asset_id")
    if not asset_id:
        raise RuntimeError(f"Неожиданный ответ asset upload: {json.dumps(data, ensure_ascii=False)[:300]}")
    return asset_id


async def piapi_wait_asset(asset_id: str) -> None:
    """Ждём, пока ассет пройдёт проверку (Active)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 480  # до 8 минут: очередь на ревью бывает медленной
    list_url = "https://api.piapi.ai/api/v1/asset/list"
    while loop.time() < deadline:
        async with aiohttp.ClientSession() as s:
            async with s.get(list_url, headers=await piapi_headers(),
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        assets = data.get("assets") or (data.get("data") or {}).get("assets") or []
        for a in assets:
            if a.get("asset_id") == asset_id:
                st = (a.get("status") or "").lower()
                log.info("piapi asset %s: %s", asset_id[:12], st)
                if st == "active":
                    return
                if st == "failed":
                    raise RuntimeError(f"PiAPI отклонил фото (asset {asset_id[:12]}): {a.get('error', '') or json.dumps(a)[:200]}")
        await asyncio.sleep(12)
    raise TimeoutError("Проверка фото заняла больше 8 минут — попробуйте позже")


async def piapi_headers():
    return {"X-API-Key": PIAPI_KEY, "Content-Type": "application/json"}


async def piapi_submit(prompt: str, image_urls: list[str]) -> str:
    """Создаёт задачу на PiAPI (Seedance/Kling). Возвращает task_id."""
    payload = {
        "model": PIAPI_MODEL,
        "task_type": PIAPI_TASK_TYPE,
        "input": {
            "prompt": prompt,
            "image_urls": image_urls,
            "aspect_ratio": "9:16",
            "resolution": "480p",
            "duration": 8,
        },
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://api.piapi.ai/api/v1/task",
            headers=await piapi_headers(),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            data = await r.json()
            if r.status >= 400:
                raise RuntimeError(f"PiAPI {r.status}: {json.dumps(data, ensure_ascii=False)[:300]}")
    log.info("piapi submit ответ: %s", json.dumps(data, ensure_ascii=False)[:300])
    task_id = data.get("task_id") or (data.get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"Неожиданный ответ PiAPI: {json.dumps(data, ensure_ascii=False)[:300]}")
    return task_id


async def piapi_wait(task_id: str) -> str:
    """Опрашивает задачу PiAPI, возвращает URL видео."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + POLL_TIMEOUT
    url = f"https://api.piapi.ai/api/v1/task/{task_id}"
    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=await piapi_headers(),
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
        status = (data.get("status") or (data.get("data") or {}).get("status") or "").lower()
        log.info("piapi_wait: статус=%s", status)
        if status in ("completed", "success", "succeeded"):
            out = data.get("output") or (data.get("data") or {}).get("output") or {}
            video_url = out.get("video_url") or out.get("url") or out.get("video")
            if isinstance(video_url, list) and video_url:
                video_url = video_url[0]
            if video_url:
                return video_url
            raise RuntimeError(f"Видео готово, но URL не найден: {json.dumps(data, ensure_ascii=False)[:300]}")
        if status in ("failed", "error", "cancelled"):
            err = (data.get("error") or (data.get("data") or {}).get("error")
                   or data.get("failed_reason") or (data.get("data") or {}).get("failed_reason"))
            detail = json.dumps(data, ensure_ascii=False)[:250]
            raise RuntimeError(f"Генерация не удалась: {err or detail}")
    raise TimeoutError("Генерация заняла больше 10 минут")


# ---------------------------------------------------------------------------
async def main_async():
    from aiohttp import web

    async def _health(request):
        return web.Response(text="ok")

    async def _img(request):
        name = request.match_info["name"]
        if "/" in name or ".." in name:
            return web.Response(status=400)
        fp = UPLOAD_DIR / name
        if not fp.exists():
            return web.Response(status=404)
        return web.FileResponse(fp)

    webapp = web.Application()
    webapp.router.add_get("/health", _health)
    webapp.router.add_get("/img/{name}", _img)
    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()

    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSE_TEMPLATE: [CallbackQueryHandler(on_template, pattern="^(dance_12m|car_exit|runway|vibe_trip|custom)$")],
            GET_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_custom_prompt)],
            GET_PHOTOS: [
                MessageHandler(filters.PHOTO, on_photo),
                CallbackQueryHandler(on_generate, pattern="^generate$"),
                # Запасные способы запустить генерацию, если кнопка не сработала:
                CommandHandler("generate", on_generate),
                MessageHandler(filters.Regex(r"(?i)^готово"), on_generate),
            ],
        },
        fallbacks=[CommandHandler("cancel", on_cancel)],
        per_message=False,
    )
    app.add_handler(conv)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Бот запущен (провайдер: PiAPI / %s / %s)", PIAPI_MODEL, PIAPI_TASK_TYPE)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main_async())
