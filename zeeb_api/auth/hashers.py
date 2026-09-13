"""
Password hashing utilities.

Uses bcrypt for secure password hashing.
"""

from __future__ import annotations

import bcrypt


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
