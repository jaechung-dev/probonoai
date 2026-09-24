"""
API Lambda — auth, intake, cases, conversations, health.
No ML/RAG dependencies; cold start ~1s.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from services.core.middleware import AccessLogMiddleware
from services.core.settings import settings
from services.auth.service import router as auth_router, seed_db
from services.api.routes.health import router as health_router
from services.api.routes.intake import router as intake_router
from services.api.routes.cases import router as cases_router
from services.api.routes.conversations import router as conversations_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Legal Intelligence — API", version="1.0")

_ALLOWED_ORIGINS = list({
    settings.FRONTEND_URL.rstrip("/"),
    "https://www.probonoai.com.au",
    "https://probonoai.com.au",
    "http://localhost:5173",
    "http://localhost:4173",
    "http://localhost:20001",
})

app.add_middleware(AccessLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(health_router)
app.include_router(intake_router)
app.include_router(cases_router)
app.include_router(conversations_router)

seed_db()

handler = Mangum(app, lifespan="off")

if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(
        "services.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "20000")),
        reload=False,
    )
