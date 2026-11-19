import secrets
import string

ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def generate_slug(length: int = 7) -> str:
    """Return a random Base62 slug of the requested length."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
