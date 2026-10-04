"""Country selection and presentation-only currency conversion."""

import json
import math
from dataclasses import dataclass
from typing import Mapping
from urllib.request import Request, urlopen


BASE_CURRENCY = "USD"
AUTO_DETECT = "Auto Detect"
MANUAL_SELECTION = "Manual country selection"
EXCHANGE_RATE_PROVIDER = "ExchangeRate-API"
EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/USD"
DEFAULT_COUNTRY = "India"

COUNTRY_DETAILS = {
    "Argentina": ("AR", "ARS"),
    "Australia": ("AU", "AUD"),
    "Austria": ("AT", "EUR"),
    "Bahrain": ("BH", "BHD"),
    "Bangladesh": ("BD", "BDT"),
    "Belgium": ("BE", "EUR"),
    "Brazil": ("BR", "BRL"),
    "Bulgaria": ("BG", "EUR"),
    "Canada": ("CA", "CAD"),
    "Chile": ("CL", "CLP"),
    "China": ("CN", "CNY"),
    "Colombia": ("CO", "COP"),
    "Croatia": ("HR", "EUR"),
    "Cyprus": ("CY", "EUR"),
    "Czechia": ("CZ", "CZK"),
    "Denmark": ("DK", "DKK"),
    "Egypt": ("EG", "EGP"),
    "Estonia": ("EE", "EUR"),
    "Finland": ("FI", "EUR"),
    "France": ("FR", "EUR"),
    "Germany": ("DE", "EUR"),
    "Ghana": ("GH", "GHS"),
    "Greece": ("GR", "EUR"),
    "Hong Kong": ("HK", "HKD"),
    "Hungary": ("HU", "HUF"),
    "Iceland": ("IS", "ISK"),
    "India": ("IN", "INR"),
    "Indonesia": ("ID", "IDR"),
    "Ireland": ("IE", "EUR"),
    "Israel": ("IL", "ILS"),
    "Italy": ("IT", "EUR"),
    "Japan": ("JP", "JPY"),
    "Kenya": ("KE", "KES"),
    "Kuwait": ("KW", "KWD"),
    "Malaysia": ("MY", "MYR"),
    "Mexico": ("MX", "MXN"),
    "Nepal": ("NP", "NPR"),
    "Netherlands": ("NL", "EUR"),
    "New Zealand": ("NZ", "NZD"),
    "Nigeria": ("NG", "NGN"),
    "Norway": ("NO", "NOK"),
    "Oman": ("OM", "OMR"),
    "Pakistan": ("PK", "PKR"),
    "Philippines": ("PH", "PHP"),
    "Poland": ("PL", "PLN"),
    "Portugal": ("PT", "EUR"),
    "Qatar": ("QA", "QAR"),
    "Romania": ("RO", "RON"),
    "Saudi Arabia": ("SA", "SAR"),
    "Singapore": ("SG", "SGD"),
    "Slovakia": ("SK", "EUR"),
    "Slovenia": ("SI", "EUR"),
    "South Africa": ("ZA", "ZAR"),
    "South Korea": ("KR", "KRW"),
    "Spain": ("ES", "EUR"),
    "Sri Lanka": ("LK", "LKR"),
    "Sweden": ("SE", "SEK"),
    "Switzerland": ("CH", "CHF"),
    "Taiwan": ("TW", "TWD"),
    "Thailand": ("TH", "THB"),
    "Turkey": ("TR", "TRY"),
    "UAE": ("AE", "AED"),
    "UK": ("GB", "GBP"),
    "USA": ("US", "USD"),
    "Vietnam": ("VN", "VND"),
}
COUNTRY_CURRENCIES = {
    country: details[1] for country, details in COUNTRY_DETAILS.items()
}
COUNTRY_CODES = {
    details[0]: country for country, details in COUNTRY_DETAILS.items()
}
COUNTRY_OPTIONS = tuple(sorted(COUNTRY_DETAILS))

CURRENCY_SYMBOLS = {
    "AED": "د.إ",
    "ARS": "AR$",
    "AUD": "A$",
    "BHD": "BD",
    "BDT": "৳",
    "BRL": "R$",
    "CAD": "C$",
    "CHF": "CHF ",
    "CLP": "CLP$",
    "CNY": "CN¥",
    "COP": "CO$",
    "CZK": "Kč ",
    "DKK": "kr ",
    "EGP": "E£",
    "EUR": "€",
    "GBP": "£",
    "GHS": "GH₵",
    "HKD": "HK$",
    "HUF": "Ft ",
    "IDR": "Rp ",
    "ILS": "₪",
    "INR": "₹",
    "ISK": "kr ",
    "JPY": "¥",
    "KES": "KSh ",
    "KRW": "₩",
    "KWD": "KD",
    "LKR": "රු",
    "MXN": "MX$",
    "MYR": "RM ",
    "NGN": "₦",
    "NOK": "kr ",
    "NPR": "रू",
    "NZD": "NZ$",
    "OMR": "ر.ع.",
    "PHP": "₱",
    "PKR": "₨",
    "PLN": "zł ",
    "QAR": "QR",
    "RON": "lei ",
    "SAR": "ر.س",
    "SEK": "kr ",
    "SGD": "S$",
    "THB": "฿",
    "TRY": "₺",
    "TWD": "NT$",
    "USD": "$",
    "VND": "₫",
    "ZAR": "R",
}
CURRENCY_DECIMAL_PLACES = {
    "BHD": 3,
    "CLP": 0,
    "HUF": 0,
    "ISK": 0,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "OMR": 3,
}


@dataclass(frozen=True)
class CountryResolution:
    country: str
    method: str


@dataclass(frozen=True)
class ExchangeRates:
    base_code: str
    rates: Mapping[str, float]
    updated_at_utc: str
    provider: str = EXCHANGE_RATE_PROVIDER


@dataclass(frozen=True)
class CurrencyDisplay:
    country: str
    target_currency_code: str
    display_currency_code: str
    usd_to_display_rate: float
    rate_updated_at_utc: str | None
    rate_source: str
    fallback_message: str | None = None

    @property
    def symbol(self) -> str:
        return CURRENCY_SYMBOLS.get(self.display_currency_code, f"{self.display_currency_code} ")

    @property
    def converted(self) -> bool:
        return self.display_currency_code != BASE_CURRENCY

    def convert_amount(self, amount_in_usd: float) -> float:
        if not math.isfinite(amount_in_usd):
            raise ValueError("currency amount must be finite")
        return amount_in_usd * self.usd_to_display_rate

    def format_money(self, amount_in_usd: float) -> str:
        amount = self.convert_amount(amount_in_usd)
        return format_currency(amount, self.display_currency_code)


def resolve_country(
    selection: str,
    accept_language: str | None,
    manual_country: str | None = None,
) -> CountryResolution:
    if selection == MANUAL_SELECTION:
        if manual_country not in COUNTRY_CURRENCIES:
            raise ValueError("select a supported country")
        return CountryResolution(manual_country, "Manual selection")
    if selection != AUTO_DETECT:
        raise ValueError(f"unsupported country selection: {selection}")

    country = country_from_accept_language(accept_language)
    if country is not None:
        return CountryResolution(country, "Browser language/region")
    return CountryResolution(DEFAULT_COUNTRY, "Fallback default")


def country_from_accept_language(accept_language: str | None) -> str | None:
    if not accept_language:
        return None
    language_preferences = []
    for entry in accept_language.split(","):
        parts = entry.strip().split(";")
        locale = parts[0].strip().replace("_", "-")
        quality = 1.0
        for parameter in parts[1:]:
            key, separator, value = parameter.strip().partition("=")
            if key == "q" and separator:
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        language_preferences.append((quality, locale))

    for _, locale in sorted(language_preferences, reverse=True):
        for part in reversed(locale.split("-")):
            country = COUNTRY_CODES.get(part.upper())
            if country is not None:
                return country
    return None


def fetch_usd_exchange_rates(timeout_seconds: float = 3.0) -> ExchangeRates:
    request = Request(
        EXCHANGE_RATE_URL,
        headers={"User-Agent": "QuantumRouteDashboard/1.0"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("result") != "success" or payload.get("base_code") != BASE_CURRENCY:
        raise ValueError("exchange-rate provider returned an invalid USD rate set")
    raw_rates = payload.get("rates")
    if not isinstance(raw_rates, dict):
        raise ValueError("exchange-rate provider did not return a rate table")

    rates: dict[str, float] = {}
    for code, raw_rate in raw_rates.items():
        if not isinstance(code, str) or len(code) != 3:
            continue
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(rate) and rate > 0:
            rates[code.upper()] = rate
    if rates.get(BASE_CURRENCY) != 1.0:
        raise ValueError("exchange-rate provider returned an invalid USD base rate")
    updated_at = payload.get("time_last_update_utc")
    if not isinstance(updated_at, str) or not updated_at:
        raise ValueError("exchange-rate provider did not include its update time")
    return ExchangeRates(BASE_CURRENCY, rates, updated_at)


def build_currency_display(
    country: str,
    exchange_rates: ExchangeRates | None,
    fallback_message: str | None = None,
) -> CurrencyDisplay:
    if country not in COUNTRY_CURRENCIES:
        raise ValueError(f"unsupported country: {country}")
    target_currency = COUNTRY_CURRENCIES[country]
    if target_currency == BASE_CURRENCY:
        return CurrencyDisplay(
            country, target_currency, BASE_CURRENCY, 1.0, None, "USD base currency"
        )

    rate = None
    if exchange_rates is not None and exchange_rates.base_code == BASE_CURRENCY:
        rate = exchange_rates.rates.get(target_currency)
    if rate is not None and math.isfinite(rate) and rate > 0:
        return CurrencyDisplay(
            country=country,
            target_currency_code=target_currency,
            display_currency_code=target_currency,
            usd_to_display_rate=rate,
            rate_updated_at_utc=exchange_rates.updated_at_utc,
            rate_source=exchange_rates.provider,
        )

    reason = fallback_message or f"No USD to {target_currency} exchange rate is available."
    return CurrencyDisplay(
        country=country,
        target_currency_code=target_currency,
        display_currency_code=BASE_CURRENCY,
        usd_to_display_rate=1.0,
        rate_updated_at_utc=None,
        rate_source="USD base currency; conversion unavailable",
        fallback_message=f"{reason} Showing amounts in USD instead.",
    )


def format_currency(amount: float, currency_code: str) -> str:
    if not math.isfinite(amount):
        raise ValueError("currency amount must be finite")
    decimals = CURRENCY_DECIMAL_PLACES.get(currency_code, 2)
    symbol = CURRENCY_SYMBOLS.get(currency_code, f"{currency_code} ")
    formatted_amount = f"{amount:,.{decimals}f}"
    if currency_code in {"AED", "BHD", "CHF", "CZK", "DKK", "HUF", "ISK", "KWD", "MYR", "NOK", "OMR", "PLN", "RON", "SAR", "SEK"}:
        return f"{formatted_amount} {symbol.strip()}"
    return f"{symbol}{formatted_amount}"