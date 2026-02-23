"""Authentication: JWT, bcrypt, login, signup, me, trial & plan management"""
import os
from datetime import datetime, timedelta, timezone
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
TRIAL_DAYS = 7

# Plan quotas (requêtes/mois)
PLAN_QUOTAS = {
    "trial": 50,
    "essentiel": 200,
    "pro": 1000,
    "cabinet": 999999,  # Illimité
}


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


def create_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)},
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


async def check_quota(user_id: str) -> dict:
    """Check user plan, trial status, and quota. Returns user info or raises HTTPException."""
    async with database.pool.acquire() as conn:
        user = await conn.fetchrow(
            """SELECT id, email, name, plan, trial_expires_at, queries_this_month, created_at
               FROM users WHERE id = $1::uuid""",
            user_id,
        )
    if not user:
        raise HTTPException(404, "Utilisateur non trouvé")

    plan = user["plan"] or "trial"
    now = datetime.now(timezone.utc)

    # Check trial expiration
    if plan == "trial":
        trial_end = user["trial_expires_at"]
        if trial_end and now > trial_end:
            raise HTTPException(402, {
                "error": "trial_expired",
                "message": "Votre essai gratuit de 7 jours est terminé. Passez à un abonnement pour continuer.",
                "upgrade_url": "/pricing",
            })

    # Check quota
    quota = PLAN_QUOTAS.get(plan, 50)
    used = user["queries_this_month"] or 0
    if used >= quota:
        raise HTTPException(429, {
            "error": "quota_exceeded",
            "message": f"Quota mensuel atteint ({used}/{quota} requêtes). Passez au plan supérieur pour plus de requêtes.",
            "plan": plan,
            "used": used,
            "limit": quota,
        })

    # Calculate remaining trial days
    trial_days_left = None
    if plan == "trial" and user["trial_expires_at"]:
        delta = user["trial_expires_at"] - now
        trial_days_left = max(0, delta.days)

    return {
        "user_id": str(user["id"]),
        "plan": plan,
        "quota_used": used,
        "quota_limit": quota,
        "trial_days_left": trial_days_left,
    }


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

    trial_end = datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)

    async with database.pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE email = $1", req.email)
        if exists:
            raise HTTPException(409, "Un compte avec cet email existe déjà")
        user_id = await conn.fetchval(
            """INSERT INTO users (email, name, password_hash, plan, trial_expires_at)
               VALUES ($1, $2, $3, 'trial', $4) RETURNING id""",
            req.email, req.name, pwd.hash(req.password), trial_end,
        )
    return {"token": create_token(str(user_id))}


@router.get("/me")
async def get_me(request: Request):
    user_id = await get_current_user_id(request)
    async with database.pool.acquire() as conn:
        user = await conn.fetchrow(
            """SELECT id, email, name, plan, trial_expires_at, queries_this_month, created_at
               FROM users WHERE id = $1::uuid""",
            user_id,
        )
    if not user:
        raise HTTPException(404, "Utilisateur non trouvé")

    plan = user["plan"] or "trial"
    now = datetime.now(timezone.utc)
    quota = PLAN_QUOTAS.get(plan, 50)
    used = user["queries_this_month"] or 0

    # Trial info
    trial_days_left = None
    trial_expired = False
    if plan == "trial" and user["trial_expires_at"]:
        delta = user["trial_expires_at"] - now
        trial_days_left = max(0, delta.days)
        trial_expired = now > user["trial_expires_at"]

    return {
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "plan": plan,
        "queries_used": used,
        "queries_limit": quota,
        "trial_days_left": trial_days_left,
        "trial_expired": trial_expired,
        "created_at": str(user["created_at"]),
    }
