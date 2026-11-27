"""Adapt Python SDK. `from adapt_sdk import Client` — one file, no dependencies."""

from adapt_sdk.client import AdaptError, Client

__all__ = ["AdaptError", "Client"]
