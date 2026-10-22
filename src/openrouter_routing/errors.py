from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing errors."""


class ConfigError(AppError):
    """Invalid or unreadable configuration."""


class OpenRouterError(AppError):
    """OpenRouter API failure or unusable response."""


class OpenRouterNotFound(OpenRouterError):
    """An OpenRouter resource (for example an expired generation) is gone."""


class PlanError(AppError):
    """A routing plan cannot be produced."""
