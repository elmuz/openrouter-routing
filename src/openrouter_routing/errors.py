from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(AppError):
    """Invalid or unreadable configuration."""


class OpenRouterError(AppError):
    """OpenRouter API failure or unusable response."""


class PlanError(AppError):
    """A routing plan cannot be produced."""
