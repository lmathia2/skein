CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)


def truncate_to_tokens(text: str, token_limit: int) -> tuple[str, bool]:
    """Deterministically retain the beginning and end of oversized context."""

    if token_limit <= 0:
        return "", bool(text)
    character_limit = token_limit * CHARS_PER_TOKEN_ESTIMATE
    if len(text) <= character_limit:
        return text, False

    marker = "\n... [section truncated by context compiler] ...\n"
    if character_limit <= len(marker):
        return text[:character_limit], True
    available = character_limit - len(marker)
    head = available * 2 // 3
    tail = available - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}", True
