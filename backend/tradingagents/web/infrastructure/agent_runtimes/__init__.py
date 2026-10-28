"""Controlled local Agent protocols; never an arbitrary shell execution service."""

from .codex import CodexRuntimeAdapter, RuntimeAdapterError

__all__ = ["CodexRuntimeAdapter", "RuntimeAdapterError"]
