from urlshortener.encoding import decode, encode


def test_encode_zero_is_a_visible_character() -> None:
    # BUG (deliberate): encode(0) returns "" instead of "0" because the
    # while-loop in encoding.py never special-cases zero. A URL shortener
    # cannot hand out an empty slug, so this is a real, reproducible
    # failure Lane 1 should discover -- not a synthetic stand-in.
    assert encode(0) == "0"


def test_encode_decode_roundtrip_for_positive_ids() -> None:
    for value in (1, 61, 62, 12345, 999999):
        assert decode(encode(value)) == value


def test_encode_rejects_negative_numbers() -> None:
    try:
        encode(-1)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a negative input")
