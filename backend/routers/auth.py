"""Authentication: JWT, bcrypt, login, signup, me"""
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt, JWTError

from backend.db import database

router = APIRouter()
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET = os.getenv("JWT_SECRET", "soluris-dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_HOURS = 72


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


def create_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS)},
        SECRET, algorithm=ALGORITHM,
    )


async def get_current_user_id(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth:
        raise HTTPException(401, "Token manquant")
    try:
        token = auth.replace("Bearer ", "")
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Token invalide")
        return user_id
    except JWTError:
        raise HTTPException(401, "Token expiré ou invalide")


@router.post("/login")
async def login(req: LoginRequest):
    async with database.pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, password_hash FROM users WHERE email = $1", req.email
        )
    if not user or not pwd.verify(req.password, user["password_hash"]):
        raise HTTPException(401, "Email ou mot de passe incorrect")
    return {"token": create_token(str(user["id"]))}


@router.post("/signup")
async def signup(req: SignupRequest):
    if len(req.password) < 8:
        raise HTTPException(400, "Mot de passe : 8 caractères minimum")
    async with database.pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE email = $1", req.email)
        if exists:
            raise HTTPException(409, "Un compte avec cet email existe déjà")
        user_id = await conn.fetchval(
            "INSERT INTO users (email, name, password_hash) VALUES ($1, $2, $3) RETURNING id",
            req.email, req.name, pwd.hash(req.password),
        )
    return {"token": create_token(str(user_id))}


@router.get("/me")
async def get_me(request: Request):
    user_id = await get_current_user_id(request)
    async with database.pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, email, name, plan, queries_this_month, created_at FROM users WHERE id = $1::uuid",
            user_id,
        )
    if not user:
        raise HTTPException(404, "Utilisateur non trouvé")
    return dict(user)
