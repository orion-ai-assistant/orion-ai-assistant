# Auth package
# Kimlik doğrulama sağlayıcıları buraya eklenir (Firebase, OAuth, JWT…).
from .firebase_auth import verify_token

__all__ = ["verify_token"]
