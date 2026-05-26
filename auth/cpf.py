"""CPF validation helper.

Pure stdlib implementation of the Brazilian CPF check-digit algorithm.
"""

from __future__ import annotations

import re


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def validate_cpf(cpf: str) -> bool:
    """Return True when ``cpf`` (digits or formatted) has a valid check digit."""
    digits = _only_digits(cpf)
    if len(digits) != 11:
        return False
    # Reject the well-known invalid all-equal sequences (000..., 111..., ...).
    if digits == digits[0] * 11:
        return False

    def _calc(slice_len: int) -> int:
        total = 0
        for i, ch in enumerate(digits[:slice_len]):
            total += int(ch) * (slice_len + 1 - i)
        remainder = (total * 10) % 11
        return 0 if remainder == 10 else remainder

    return _calc(9) == int(digits[9]) and _calc(10) == int(digits[10])
