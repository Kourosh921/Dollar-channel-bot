import os
import math
import requests
import jdatetime

from datetime import datetime
from zoneinfo import ZoneInfo

TGJU = "http://localhost:8080"
TIMEOUT = 20
TEHRAN = ZoneInfo("Asia/Tehran")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NabzDollarBot/2.0"})


def fa_digits(value):
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fmt(number):
    return fa_digits(f"{int(round(float(number))):,}".replace(",", "٬"))


def get_json(url, params=None):
    response = SESSION.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_items(market, keys):
    url = f"{TGJU}/v1/markets/{market}/items"
    data = get_json(url, params={"keys": ",".join(keys)})

    items = data.get("items", [])
    if isinstance(items, dict):
        return items

    return {
        item["key"]: item
        for item in items
        if isinstance(item, dict) and item.get("key")
    }


def get_price(item):
    if not isinstance(item, dict):
        raise ValueError("Market item is missing")

    price_data = item.get("price") or {}
    value = price_data.get("value")

    if value is None:
        raise ValueError("Price is missing")

    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid market price")

    # TGJU price.value is in rial; channel displays toman.
    toman = value / 10
    if toman <= 0:
        raise ValueError("Invalid toman conversion")
    return toman


def format_change(item):
    change = (item or {}).get("change") or {}
    if not isinstance(change, dict):
        return "⚪ نامشخص"

    percent = change.get("percent")
    status = str(change.get("status") or "").lower()

    try:
        percent = float(percent)
    except (TypeError, ValueError):
        return "⚪ نامشخص"

    if not math.isfinite(percent):
        return "⚪ نامشخص"

    if status in ("high", "up", "positive", "increase"):
        percent = abs(percent)
    elif status in ("low", "down", "negative", "decrease"):
        percent = -abs(percent)

    if percent > 0:
        return f"▲ +{fa_digits(f'{percent:.2f}')}٪"
    if percent < 0:
        return f"▼ {fa_digits(f'{percent:.2f}')}٪"
    return "➖ ۰٫۰۰٪"


def get_bitcoin():
    """CoinGecko first, Kraken second. Never invent a price."""
    # Source 1: CoinGecko, quoted directly in USDT.
    try:
        data = get_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "usdt",
                "include_24hr_change": "true",
            },
        )
        btc = data.get("bitcoin") or {}
        price = float(btc["usdt"])
        change = float(btc["usdt_24h_change"])

        if math.isfinite(price) and price > 0 and math.isfinite(change):
            print("BITCOIN SOURCE: CoinGecko")
            return price, change
        raise ValueError("CoinGecko returned invalid BTC data")
    except Exception as error:
        print(f"CoinGecko BTC source failed: {type(error).__name__}: {error}")

    # Source 2: Kraken XBT/USDT. Change is calculated from 24h open.
    try:
        data = get_json(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": "XBTUSDT"},
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))

        result = data.get("result") or {}
        ticker = next(iter(result.values()))
        price = float(ticker["c"][0])
        opening = float(ticker["o"])

        if price <= 0 or opening <= 0:
            raise ValueError("Kraken returned invalid BTC data")

        change = ((price - opening) / opening) * 100
        print("BITCOIN SOURCE: Kraken")
        return price, change
    except Exception as error:
        print(f"Kraken BTC source failed: {type(error).__name__}: {error}")

    # BTC failure must not stop the Iranian market post.
    return None, None


def send_telegram(message):
    token = os.environ.get("BOT_TOKEN", "").strip()
    channel = os.environ.get("CHANNEL_ID", "").strip()

    if not token:
        raise RuntimeError("BOT_TOKEN secret is missing")
    if not channel:
        raise RuntimeError("CHANNEL_ID is missing")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = SESSION.post(
            url,
            json={
                "chat_id": channel,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        raise RuntimeError(
            f"Telegram network error: {type(error).__name__}: {error}"
        ) from error

    try:
        result = response.json()
    except ValueError:
        result = {}

    if response.status_code >= 400 or not result.get("ok"):
        description = result.get("description") or response.text[:500]
        error_code = result.get("error_code", response.status_code)
        raise RuntimeError(
            f"Telegram rejected the message. "
            f"HTTP={response.status_code}, Telegram={error_code}, "
            f"chat_id={channel}, description={description}"
        )

    print("TELEGRAM MESSAGE SENT")


def build_message():
    currency = get_items(
        "currency",
        ["price_dollar_rl", "price_eur", "price_aed"],
    )
    gold = get_items("gold", ["geram18"])
    coins = get_items("coin", ["sekee", "nim", "rob"])

    assets = [
        ("دلار آزاد", currency.get("price_dollar_rl")),
        ("یورو", currency.get("price_eur")),
        ("درهم امارات", currency.get("price_aed")),
        ("طلای ۱۸ عیار", gold.get("geram18")),
        ("سکه امامی", coins.get("sekee")),
        ("نیم‌سکه", coins.get("nim")),
        ("ربع‌سکه", coins.get("rob")),
    ]

    now = datetime.now(TEHRAN)
    jalali = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))

    lines = [
        "📊 نبض دلار | قیمت بازار",
        "━━━━━━━━━━━━━━━━━━",
    ]

    valid_count = 0
    for name, item in assets:
        if not item:
            print(f"SKIPPED MISSING ASSET: {name}")
            continue

        try:
            price = get_price(item)
            change = format_change(item)
            lines.append(
                f"💰 {name}: {fmt(price)} تومان\n"
                f"   {change}"
            )
            valid_count += 1
        except Exception as error:
            print(f"SKIPPED INVALID ASSET {name}: {error}")

    if valid_count == 0:
        raise RuntimeError("No valid Iranian market prices were received")

    btc_price, btc_change = get_bitcoin()
    lines.extend(["", "🪙 بازار ارز دیجیتال"])

    if btc_price is not None:
        btc_sign = "+" if btc_change > 0 else ""
        lines.append(f"₿ بیت‌کوین: {fmt(btc_price)} تتر")
        lines.append(
            f"   تغییر ۲۴ ساعته: {fa_digits(f'{btc_sign}{btc_change:.2f}')}٪"
        )
    else:
        lines.append("₿ بیت‌کوین: ⚪ نامشخص (منابع در دسترس نیستند)")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"🗓 تاریخ: {fa_digits(jalali.strftime('%Y/%m/%d'))}",
        f"🕒 ساعت تهران: {fa_digits(now.strftime('%H:%M:%S'))}",
        "",
        "⚠️ قیمت‌ها ممکن است تغییر کنند.",
        "صرفاً جهت اطلاع‌رسانی؛ نه توصیه خرید یا فروش.",
        "",
        "@dollariran5",
    ])

    return "\n".join(lines)


def main():
    message = build_message()
    print("MESSAGE PREPARED")
    send_telegram(message)


if __name__ == "__main__":
    main()
