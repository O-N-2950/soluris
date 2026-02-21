"""Health check for Railway"""
from fastapi import APIRouter
from backend.db.database import pool

router = APIRouter()


@router.get("/health")
async def health():
    db_ok = False
    try:
        if pool is not None:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                db_ok = True
    except Exception:
        pass
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "service": "soluris"}
