"""
Firebase Auth — Stub (henüz implemente edilmedi).

TODO: firebase-admin paketini requirements.txt'e ekle ve aşağıyı doldur.

Kurulum:
    pip install firebase-admin

Kullanım (route'da):
    from ..auth import verify_token

    async def my_endpoint(token: str = Header(...)):
        claims = await verify_token(token)
        if claims is None:
            raise HTTPException(status_code=401)
        user_id = claims["uid"]
"""
from __future__ import annotations


async def verify_token(token: str) -> dict | None:
    """
    Firebase ID token'ını doğrular.
    Geçerliyse decoded claims dict'ini döner; geçersizse None.

    TODO: Implement with firebase_admin.auth.verify_id_token(token)
    """
    raise NotImplementedError(
        "Firebase auth henüz yapılandırılmadı. "
        "firebase_auth.py dosyasını doldurun."
    )
