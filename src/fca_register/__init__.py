"""Async client for the FCA Financial Services Register API."""
from .client import (
    Client,
    FcaApiError,
    FcaAuthError,
    FcaBadRequestError,
    FcaSystemError,
    RateLimiter,
)

__all__ = [
    "Client",
    "FcaApiError",
    "FcaAuthError",
    "FcaBadRequestError",
    "FcaSystemError",
    "RateLimiter",
]
__version__ = "0.1.2"
