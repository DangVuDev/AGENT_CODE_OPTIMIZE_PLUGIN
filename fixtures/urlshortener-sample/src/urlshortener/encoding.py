from __future__ import annotations

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE = len(_ALPHABET)


def encode(number: int) -> str:
    """Encode a non-negative integer as a base62 string."""
    if number < 0:
        raise ValueError("cannot encode a negative number")
    digits: list[str] = []
    remaining = number
    while remaining > 0:
        remaining, remainder = divmod(remaining, _BASE)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


def decode(code: str) -> int:
    """Decode a base62 string back into its integer value."""
    number = 0
    for char in code:
        number = number * _BASE + _ALPHABET.index(char)
    return number
