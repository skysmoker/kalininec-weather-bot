import os
import time
import logging
import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

from weather_kalininec import (
    MONTHS_RU,
    get_weather_data,
    parse_hourly_data,
    format_message,
    weather_emoji,
    weather_description,
    get_dominant_weather_code,
    average,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_FILE = BASE_DIR / "listener.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8"
)

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "🌤 Сегодня"}, {"text": "📅 Завтра"}],
        [{"text": "🗓 На 3 дня"}, {"text": "⏰ Почасовой (12 ч)"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}


def tg_request(method: str, payload: dict, timeout: int = 35) -> dict | None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logging.error(f"Ошибка Telegram API ({method}): {e}")
        return None


def send_private_reply(chat_id: int, text: str) -> None:
    tg_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": MAIN_KEYBOARD,
        },
        timeout=15,
    )


def build_3days_summary(data: dict) -> str:
    today = datetime.date.today()
    day_labels = ["Сегодня", "Завтра", "Послезавтра"]
    lines = ["🗓 <b>Прогноз в Калининце на 3 дня:</b>"]

    for offset in range(3):
        hours = parse_hourly_data(data, day_offset=offset)
        if not hours:
            continue
        day_h = [h for h in hours if 9 <= h["hour"] < 21]
        night_h = [h for h in hours if h["hour"] >= 21 or h["hour"] < 9]

        t_day = average([h["temp"] for h in day_h])
        t_night = average([h["temp"] for h in night_h])
        precip = sum(h["precip"] for h in hours)
        wind_max = max((h["wind_speed"] for h in hours), default=0)

        code = get_dominant_weather_code(day_h or hours)
        emoji = weather_emoji(code)
        desc = weather_description(code)

        d = today + datetime.timedelta(days=offset)
        date_str = f"{d.day} {MONTHS_RU[d.month - 1]}"

        lines.extend([
            "",
            f"{emoji} <b>{day_labels[offset]} ({date_str})</b> — <i>{desc}</i>",
            f"   • Днём {t_day:+.0f}°C / Ночью {t_night:+.0f}°C",
            f"   • Осадки: {precip:.1f} мм • Ветер: до {wind_max:.1f} м/с",
        ])

    return "\n".join(lines)


def build_hourly_12h(data: dict) -> str:
    now = datetime.datetime.now()
    times = [datetime.datetime.fromisoformat(t) for t in data["hourly"]["time"]]
    hourly = data["hourly"]

    lines = ["⏰ <b>Почасовой прогноз в Калининце (ближайшие 12 ч):</b>", ""]
    count = 0

    for i, t in enumerate(times):
        if t >= now.replace(minute=0, second=0, microsecond=0):
            temp = hourly["temperature_2m"][i]
            precip = hourly["precipitation"][i] or 0
            wind = hourly["wind_speed_10m"][i]
            code = hourly["weathercode"][i]
            emoji = weather_emoji(code)

            precip_str = f" • 💧 {precip:.1f} мм" if precip > 0 else ""
            lines.append(f"<b>{t.strftime('%H:%M')}</b> {emoji} {temp:+.0f}°C • 💨 {wind:.1f} м/с{precip_str}")
            count += 1
            if count >= 12:
                break

    return "\n".join(lines)


def handle_private_message(chat_id: int, user_name: str, text: str) -> None:
    cmd = text.strip()
    logging.info(f"Запрос от {user_name} ({chat_id}): {cmd}")

    if cmd in ("/start", "/help"):
        welcome = (
            f"Привет, <b>{user_name}</b>! 👋\n\n"
            "Я персональный погодный информер для Калининца.\n"
            "Выберите нужный прогноз на кнопках ниже — ответы приходят только сюда, не засоряя общий чат."
        )
        send_private_reply(chat_id, welcome)
        return

    try:
        if cmd == "🌤 Сегодня":
            data = get_weather_data(forecast_days=2)
            hours = parse_hourly_data(data, day_offset=0)
            msg = format_message(hours, target_date=datetime.date.today(), is_morning=False)
            send_private_reply(chat_id, msg)

        elif cmd == "📅 Завтра":
            data = get_weather_data(forecast_days=2)
            tomorrow = datetime.date.today() + datetime.timedelta(days=1)
            hours = parse_hourly_data(data, day_offset=1)
            msg = format_message(hours, target_date=tomorrow, is_morning=False)
            send_private_reply(chat_id, msg)

        elif cmd == "🗓 На 3 дня":
            data = get_weather_data(forecast_days=4)
            msg = build_3days_summary(data)
            send_private_reply(chat_id, msg)

        elif cmd == "⏰ Почасовой (12 ч)":
            data = get_weather_data(forecast_days=2)
            msg = build_hourly_12h(data)
            send_private_reply(chat_id, msg)

        else:
            send_private_reply(chat_id, "Выберите нужный вариант прогноза на кнопках ниже 👇")

    except Exception as e:
        logging.error(f"Ошибка при формировании ответа пользователю {chat_id}: {e}")
        send_private_reply(chat_id, "⚠️ Не удалось получить свежие данные от метеослужбы. Попробуйте через минуту.")


def run_polling() -> None:
    if not BOT_TOKEN:
        raise ValueError("Не задан BOT_TOKEN в .env")

    logging.info("Запуск личного погодного бота (режим long-polling)...")
    offset = 0

    while True:
        updates_resp = tg_request(
            "getUpdates",
            {"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
            timeout=35,
        )
        if not updates_resp or not updates_resp.get("ok"):
            time.sleep(5)
            continue

        for update in updates_resp.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue

            chat = message.get("chat", {})
            # Строго игнорируем группы, супергруппы и каналы
            if chat.get("type") != "private":
                continue

            text = message.get("text")
            if not text:
                continue

            chat_id = chat["id"]
            user_name = message.get("from", {}).get("first_name", "друг")
            handle_private_message(chat_id, user_name, text)


if __name__ == "__main__":
    run_polling()
