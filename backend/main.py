"""Soluris — FastAPI Backend Entry Point"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.db.database import init_db
from backend.routers import auth, chat, conversations, health, fiscal


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Soluris API", version="1.0.0", lifespan=lifespan)

# CORS — Restricted origins (audit sécurité 23 fév 2026: wildcard Railway retiré)
_cors_origins = [
    "https://soluris.ch",
    "https://www.soluris.ch",
]
if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    _cors_origins.append(f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}")
if os.environ.get("NODE_ENV") != "production":
    _cors_origins.extend(["http://localhost:8000", "http://localhost:3000"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(health.router, tags=["health"])
app.include_router(fiscal.router)  # tAIx internal endpoint

# Static files
frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend):
    app.mount("/css", StaticFiles(directory=os.path.join(frontend, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(frontend, "js")), name="js")
    if os.path.exists(os.path.join(frontend, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(frontend, "assets")), name="assets")

    @app.get("/")
    async def serve_landing():
        return FileResponse(os.path.join(frontend, "index.html"))

    @app.get("/app")
    async def serve_app():
        return FileResponse(os.path.join(frontend, "app.html"))

    @app.get("/login")
    async def serve_login():
        return FileResponse(os.path.join(frontend, "login.html"))
