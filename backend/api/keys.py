"""
backend/api/keys.py — Trust-Anchor: Public-Keys des Audit-Log-Signers.

Exposed:
  GET /api/keys/signer        — aktueller Signer (für neue Logs)
  GET /api/keys/signers       — alle bekannten Signer (auch alte für Verify)
  GET /api/keys/signer/{id}   — einzelner Signer

Public-Keys können extern abgegriffen werden (z.B. dillenberg.net) und
als Trust-Anchor dienen, um Logs offline zu verifizieren.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.core import pqsign

router = APIRouter(prefix="/api/keys")


@router.get("/signer")
async def current_signer():
    """Aktiv signierender Keypair (Public-Anteil)."""
    kp = pqsign.get_or_create_keypair()
    return kp.to_metadata()


@router.get("/signers")
async def list_all_signers():
    """Alle bekannten Signer-Public-Keys (für Verifikation alter Logs)."""
    return {"signers": pqsign.list_signers()}


@router.get("/signer/{signer_id}")
async def get_signer(signer_id: str):
    pk = pqsign.load_public_key(signer_id)
    if pk is None:
        raise HTTPException(404, f"Signer not found: {signer_id}")
    import base64
    import hashlib
    return {
        "signer_id":         signer_id,
        "algorithm":         pqsign.ALGORITHM,
        "public_key_b64":    base64.b64encode(pk).decode("ascii"),
        "public_key_sha256": hashlib.sha256(pk).hexdigest(),
    }
