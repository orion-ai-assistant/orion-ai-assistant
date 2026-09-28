"""Shared browser automation infrastructure, independent of individual tools."""

from .cdp import CDPConnectionLost, CDPError, CDPSession, CDPTimeout

__all__ = ['CDPConnectionLost', 'CDPError', 'CDPSession', 'CDPTimeout']
