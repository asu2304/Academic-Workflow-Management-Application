import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User

_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


class LoginRequired(Exception):
    """Raised when a page needs a signed-in user; handled by redirecting to /login."""


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def current_user(user: Optional[User] = Depends(current_user_optional)) -> User:
    if user is None:
        raise LoginRequired()
    return user


def instructor_required(user: User = Depends(current_user)) -> User:
    if not user.is_instructor:
        raise HTTPException(status_code=403, detail="Instructors only")
    return user


def student_required(user: User = Depends(current_user)) -> User:
    if user.is_instructor:
        raise HTTPException(status_code=403, detail="Students only")
    return user
