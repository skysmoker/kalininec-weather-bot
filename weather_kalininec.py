import os
import time
import logging
import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- НАСТРОЙКИ TELEGRAM ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# --- НАСТРОЙКИ ПОГОДЫ (Калининец) ---
LATITUDE = 55.563198837177346
LONGITUDE = 36.985105733399116
TIMEZONE = "Europe/Moscow"

DAY_START_HOUR = 9
DAY_END_HOUR = 21

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]

LOG_FILE = BASE_DIR / "weather.log"
STATE_FILE = BASE_DIR / "last_sent.txt"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8"
)


def already_sent_today() -> bool:
    today_str = datetime.date.today().isoformat()
    if STATE_FILE.exists():
        return STATE_FILE.read_text(encoding="utf-8").strip() == today_str
    return False


def mark_sent_today() -> None:
    today_str = datetime.date.today().isoformat()
    STATE_FILE.write_text(today_str + "\n", encoding="utf-8")


def get_weather_data() -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
        f"precipitation_probability,pressure_msl,wind_speed_10m,"
        f"wind_direction_10m,weathercode,uv_index"
        f"&daily=sunrise,sunset"
        f"&timezone={TIMEZONE}&forecast_days=2&wind_speed_unit=ms"
    )
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            last_error = e
            logging.warning(f"Попытка {attempt}/3 не удалась (Open-Meteo): {e}")
            if attempt < 3:
                time.sleep(15)
    raise last_error


def parse_hourly_data(data: dict) -> list[dict]:
    times = [datetime.datetime.fromisoformat(t) for t in data["hourly"]["time"]]
    hourly = data["hourly"]
    today_date = times[0].date()
    today_hours = []

    for i, t in enumerate(times):
        if t.date() == today_date:
            today_hours.append({
                "hour": t.hour,
                "temp": hourly["temperature_2m"][i],
                "feels_like": hourly["apparent_temperature"][i],
                "humidity": hourly["relative_humidity_2m"][i],
                "precip": hourly["precipitation"][i] or 0,
                "precip_prob": hourly["precipitation_probability"][i] or 0,
                "pressure": hourly["pressure_msl"][i],
                "wind_speed": hourly["wind_speed_10m"][i],
                "wind_dir": hourly["wind_direction_10m"][i],
                "weathercode": hourly["weathercode"][i],
                "uv": hourly["uv_index"][i] or 0,
            })
    return today_hours


def average(values: list) -> float:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else 0


def deg_to_compass(deg: float) -> str:
    dirs = [
        "Северный", "Северо-восточный", "Восточный", "Юго-восточный",
        "Южный", "Юго-западный", "Западный", "Северо-западный"
    ]
    idx = round(deg / 45) % 8
    return dirs[idx]


def weather_emoji(code) -> str:
    if code is None:
        return "🌡️"
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 56, 57):
        return "🌦️"
    if code in (61, 63, 65, 66, 67):
        return "🌨️"
    if code in (71, 73, 75, 77):
        return "🌨️"
    if code in (80, 81, 82):
        return "🌦️"
    if code in (85, 86):
        return "🌨️"
    if code in (95, 96, 99):
        return "⛈️"
    return "🌡️"


def format_message(today_hours: list[dict]) -> str:
    day_hours = [h for h in today_hours if DAY_START_HOUR <= h["hour"] < DAY_END_HOUR]
    night_hours = [h for h in today_hours if h["hour"] >= DAY_END_HOUR or h["hour"] < DAY_START_HOUR]

    temp_day = average([h["temp"] for h in day_hours])
    temp_night = average([h["temp"] for h in night_hours])
    humidity_day = average([h["humidity"] for h in day_hours])
    humidity_night = average([h["humidity"] for h in night_hours])

    precip_sum = sum(h["precip"] for h in today_hours)
    precip_prob_max = max((h["precip_prob"] for h in today_hours), default=0)

    pressure_hpa = average([h["pressure"] for h in today_hours])
    pressure_mmhg = round(pressure_hpa * 0.75006) if pressure_hpa else 0

    wind_speed_max = max((h["wind_speed"] for h in today_hours), default=0)
    wind_dirs = [h["wind_dir"] for h in today_hours if h["wind_dir"] is not None]
    wind_dir_avg = average(wind_dirs)
    wind_compass = deg_to_compass(wind_dir_avg) if wind_dirs else "—"

    noon = next((h for h in today_hours if h["hour"] == 12), today_hours[0])
    emoji = weather_emoji(noon["weathercode"])

    today = datetime.date.today()
    date_str = f"{today.day} {MONTHS_RU[today.month - 1]}"

    lines = [
        f"{emoji} <b>Доброе утро!</b>",
        f"Погода в Калининце на {date_str}:",
        "",
        "🌡️ <b>Температура:</b>",
        f"   • Днём {temp_day:+.0f}°C / Ночью {temp_night:+.0f}°C",
        "",
        "🌧️ <b>Осадки:</b>",
        f"   • Количество: {precip_sum:.1f} мм",
        f"   • Вероятность: до {precip_prob_max:.0f}%",
        "",
        "💧 <b>Влажность:</b>",
        f"   • Днём: {humidity_day:.0f}% / Ночью: {humidity_night:.0f}%",
        "",
        "💨 <b>Ветер:</b>",
        f"   • {wind_compass}, до {wind_speed_max:.1f} м/с",
        "",
        "📊 <b>Давление:</b>",
        f"   • {pressure_mmhg} мм рт. ст.",
    ]
    return "\n".join(lines)


def send_telegram_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("Не заданы BOT_TOKEN или CHAT_ID в файле .env")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    response = requests.post(url, data=data, timeout=15)
    response.raise_for_status()


def main() -> None:
    if already_sent_today():
        logging.info("Прогноз за сегодня уже был успешно отправлен. Повторный запуск отменён.")
        return

    try:
        data = get_weather_data()
        today_hours = parse_hourly_data(data)
        message = format_message(today_hours)
        send_telegram_message(message)
        mark_sent_today()
        logging.info("Успешно отправлено в Telegram.")
    except requests.RequestException as e:
        logging.error(f"ОШИБКА СЕТИ: {e}")
    except Exception as e:
        logging.error(f"ОШИБКА: {e}")


if __name__ == "__main__":
    main()
