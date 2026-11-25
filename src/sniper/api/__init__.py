"""FastAPI backend API package for CS2 Trading Sniper dashboard."""

from sniper.api.app import AppContainer, create_app

__all__ = [
    "AppContainer",
    "create_app",
]
