from datetime import datetime, timedelta, timezone
from typing import Any
from jose import jwt, JWTError
import bcrypt
from cryptography.fernet import Fernet
from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))
    to_encode["exp"] = expire
    to_encode["type"] = "access"
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any]) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    to_encode["exp"] = expire
    to_encode["type"] = "refresh"
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def _get_fernet() -> Fernet:
    key = settings.fernet_key
    if not key:
        # Generate a key for development (NOT for production)
        key = Fernet.generate_key().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plain_text: str) -> str:
    """Encrypt a secret string for database storage."""
    return _get_fernet().encrypt(plain_text.encode()).decode()


def decrypt_secret(encrypted_text: str) -> str:
    """Decrypt a secret string from database storage."""
    return _get_fernet().decrypt(encrypted_text.encode()).decode()
