"""Aggregates every v1 router behind the configured API prefix."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import auth, chat, comments, documents, files, shares

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(shares.document_shares_router)
api_router.include_router(shares.public_shares_router)
api_router.include_router(comments.router)
api_router.include_router(chat.router)
# Serves PDFs for both storage backends — see app/storage/signed_links.py.
api_router.include_router(files.router)
