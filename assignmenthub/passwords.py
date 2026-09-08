"""Password lengths shared by the web API, CLI, and desktop manager."""
import re
import secrets

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
TEMPORARY_PASSWORD_LENGTH = 8


def valid_password_length(value):
    return isinstance(value, str) and MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH


def valid_temporary_password(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{8}", value) is not None


def temporary_password():
    # Each displayed character is one UTF-8 byte; omit easily confused characters.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(TEMPORARY_PASSWORD_LENGTH))
