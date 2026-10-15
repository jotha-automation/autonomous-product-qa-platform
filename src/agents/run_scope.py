"""Per-async-task run scope via contextvars — safe for concurrent pipeline runs."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional

_memory_scope_var: ContextVar[Optional[str]] = ContextVar("aptp_memory_scope", default=None)
_pipeline_ctx_var: ContextVar[Optional[Any]] = ContextVar("aptp_pipeline_ctx", default=None)


def get_memory_scope() -> Optional[str]:
    return _memory_scope_var.get()


def bind_memory_scope(scope: Optional[str]) -> Token:
    normalized = (scope or "").strip(":")
    return _memory_scope_var.set(normalized or None)


def reset_memory_scope(token: Token) -> None:
    _memory_scope_var.reset(token)


def get_pipeline_ctx() -> Optional[Any]:
    return _pipeline_ctx_var.get()


def bind_pipeline_ctx(ctx: Any) -> Token:
    return _pipeline_ctx_var.set(ctx)


def reset_pipeline_ctx(token: Token) -> None:
    _pipeline_ctx_var.reset(token)
