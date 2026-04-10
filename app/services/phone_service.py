"""
Phone number normalization service.
Converts any local/national format to E.164 (+79991234567).
Uses the Google libphonenumber via the 'phonenumbers' package.
"""
from __future__ import annotations

from typing import Optional

import phonenumbers

from app.core.config import settings
from app.core.exceptions import PhoneNormalizationError
def normalize_phone(raw_phone: str, country: Optional[str] = None) -> str:
    """
    Parse and normalize a phone number to E.164 format.
    Args:
        raw_phone: Raw phone string, e.g. "89991234567", "+7 999 123-45-67"
        country: ISO 3166-1 alpha-2 country code for fallback parsing (default: settings.DEFAULT_PHONE_COUNTRY)
    Returns:
        E.164 formatted string, e.g. "+79991234567"
    Raises:
        PhoneNormalizationError: If the number is invalid or cannot be parsed.
    """
    country_code = country or settings.default_phone_country
    try:
        parsed = phonenumbers.parse(raw_phone, country_code)
    except phonenumbers.NumberParseException as exc:
        raise PhoneNormalizationError(
            f"Cannot parse phone number: {raw_phone!r}",
            detail=str(exc),
        ) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNormalizationError(
            f"Invalid phone number: {raw_phone!r}",
            detail=f"Parsed as {phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)!r} but failed validation",
        )
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)