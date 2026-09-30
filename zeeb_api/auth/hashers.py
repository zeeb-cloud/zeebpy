"""
Password hashing utilities.

Uses bcrypt for secure password hashing.
"""

from __future__ import annotations

import bcrypt

from zeeb_api.exceptions import PasswordTooLongError

#: bcrypt hashes at most this many bytes of input; bcrypt >= 5 raises beyond it.
MAX_PASSWORD_BYTES = 72


def password_too_long(raw_password: str) -> bool:
    """Whether *raw_password* exceeds :data:`MAX_PASSWORD_BYTES` once UTF-8 encoded."""
    return len(raw_password.encode("utf-8")) > MAX_PASSWORD_BYTES


def validate_password_length(raw_password: str) -> None:
    """Raise :class:`~zeeb_api.exceptions.PasswordTooLongError` over the bcrypt limit."""
    if password_too_long(raw_password):
        raise PasswordTooLongError(MAX_PASSWORD_BYTES)


def make_password(raw_password: str) -> str:
    """
    Hash a password using bcrypt.
    
    Args:
        raw_password: The plain text password
    
    Returns:
        The hashed password string
    
    Example:
        hashed = make_password("mysecretpassword")
    """
    if not raw_password:
        raise ValueError("Password cannot be empty")
    validate_password_length(raw_password)
    
    # Generate salt and hash
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(raw_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def check_password(raw_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.
    
    Args:
        raw_password: The plain text password to check
        hashed_password: The hashed password to check against
    
    Returns:
        True if the password matches, False otherwise
    
    Example:
        if check_password("mysecretpassword", user.password):
            print("Password correct!")
    """
    if not raw_password or not hashed_password:
        return False
    if password_too_long(raw_password):
        return False
    
    try:
        return bcrypt.checkpw(
            raw_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def is_password_usable(hashed_password: str | None) -> bool:
    """
    Check if a password hash is usable (not None or empty).
    
    Args:
        hashed_password: The hashed password to check
    
    Returns:
        True if the password is usable
    """
    return bool(hashed_password and hashed_password.startswith("$2"))
