"""Autenticação simples por sessão (cookie assinado) e hash PBKDF2 da biblioteca padrão."""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .db import User, get_session

_ITER = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER)
    return f"pbkdf2_sha256${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def create_user(session: Session, username: str, password: str, *, full_name: str = "",
                email: str = "", is_admin: bool | None = None) -> User:
    username = username.strip().lower()
    if not username or len(password) < 6:
        raise HTTPException(400, "Usuário obrigatório e senha com pelo menos 6 caracteres.")
    if session.exec(select(User).where(User.username == username)).first():
        raise HTTPException(409, "Nome de usuário já existe.")
    if is_admin is None:  # primeiro usuário vira administrador
        is_admin = session.exec(select(User)).first() is None
    user = User(username=username, full_name=full_name, email=email,
                password_hash=hash_password(password), is_admin=is_admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Não autenticado.")
    user = session.get(User, uid)
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(401, "Não autenticado.")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Apenas administradores.")
    return user
