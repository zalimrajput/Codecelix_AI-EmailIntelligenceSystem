from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import admin, assistant, auth, emails, integrations, insights, users

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    yield


app = FastAPI(
    title="AI Email Intelligence API",
    version="1.0.0",
    description="Secure email ingestion, intelligence, Gmail sync, and private semantic search.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

for router in (auth.router, users.router, emails.router, assistant.router,
               integrations.router, insights.router, admin.router):
    app.include_router(router)

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
