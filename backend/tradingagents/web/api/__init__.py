"""Versioned web contracts with no model, credential, or storage initialization."""

from .router import create_api_router, create_schema_app

__all__ = ["create_api_router", "create_schema_app"]
