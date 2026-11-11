"""
backend/core/pqsign.py — Post-Quantum Signing für LogpyClaw Audit-Logs.

Verwendet ML-DSA-65 (NIST FIPS 204, früher Dilithium-3). Quantenresistent,
Level 3 (≈ AES-192 Security).

Public-Key:  1952 bytes
Secret-Key:  4032 bytes
Signature:   3309 bytes

Keys werden in `keys/signer-*.{pub,sk}` gespeichert. Der erste Boot
generiert ein Keypair, alle weiteren laden den existierenden.
Public-Key wird via `GET /api/keys/signer` exponiert (Trust-Anchor).

Hash-Chain pro Mission: SHA-256(prev_hash || canonical_json(message)).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from pqcrypto.sign import ml_dsa_65 as _mldsa

ALGORITHM      = "ML-DSA-65"
HASH_ALG       = "SHA-256"
GENESIS_HASH   = "0" * 64
KEYS_DIR       = Path(__file__).resolve().parent.parent.parent / "keys"


# ── Canonical JSON ────────────────────────────────────────────────────────────

def canonical_json(obj) -> bytes:
    """Deterministische JSON-Serialisierung für stabile Hashes/Signaturen.

    - Keys alphabetisch sortiert
    - Keine Whitespace
    - UTF-8 bytes
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ── Hash-Chain Helpers ────────────────────────────────────────────────────────

def hash_message(prev_hash: str, canonical_payload: bytes) -> str:
    """SHA-256(prev_hash || canonical_payload) → hex digest."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical_payload)
    return h.hexdigest()


# ── Keypair Management ────────────────────────────────────────────────────────

@dataclass
class Keypair:
    signer_id: str
    public_key: bytes
    secret_key: bytes
    created_at: float

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode("ascii")

    def to_metadata(self) -> dict:
        """Public-only Metadata für API/Trust-Anchor."""
        return {
            "signer_id": self.signer_id,
            "algorithm": ALGORITHM,
            "public_key_b64": self.public_key_b64,
            "public_key_sha256": hashlib.sha256(self.public_key).hexdigest(),
            "created_at": self.created_at,
        }


_current: Keypair | None = None


def get_or_create_keypair() -> Keypair:
    """Singleton: lädt existierenden Signer-Key oder generiert neuen."""
    global _current
    if _current is not None:
        return _current

    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    # Existierende Keys auflisten (letzte wins — wir signieren immer mit
    # dem neuesten; alte bleiben für Verifikation alter Logs erhalten)
    existing = sorted(KEYS_DIR.glob("signer-*.sk"))
    if existing:
        sk_path = existing[-1]
        signer_id = sk_path.stem  # "signer-2026-05-26T..."
        pk_path   = sk_path.with_suffix(".pub")
        sk = sk_path.read_bytes()
        pk = pk_path.read_bytes()
        try:
            meta_path = sk_path.with_suffix(".json")
            created_at = json.loads(meta_path.read_text())["created_at"]
        except Exception:
            created_at = sk_path.stat().st_mtime
        _current = Keypair(signer_id, pk, sk, created_at)
        return _current

    # Neu generieren
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    signer_id = f"signer-{ts}"
    pk, sk = _mldsa.generate_keypair()
    created_at = time.time()

    sk_path = KEYS_DIR / f"{signer_id}.sk"
    pk_path = KEYS_DIR / f"{signer_id}.pub"
    meta_path = KEYS_DIR / f"{signer_id}.json"

    sk_path.write_bytes(sk)
    pk_path.write_bytes(pk)
    os.chmod(sk_path, 0o600)
    meta = {
        "signer_id":     signer_id,
        "algorithm":     ALGORITHM,
        "created_at":    created_at,
        "public_key_b64": base64.b64encode(pk).decode("ascii"),
        "public_key_sha256": hashlib.sha256(pk).hexdigest(),
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    _current = Keypair(signer_id, pk, sk, created_at)
    return _current


def load_public_key(signer_id: str) -> bytes | None:
    """Lädt einen Public-Key per signer_id für Verifikation alter Signaturen."""
    pk_path = KEYS_DIR / f"{signer_id}.pub"
    if pk_path.exists():
        return pk_path.read_bytes()
    return None


def list_signers() -> list[dict]:
    """Listet alle bekannten Signer (public-only Metadata)."""
    out = []
    if not KEYS_DIR.exists():
        return out
    for meta_path in sorted(KEYS_DIR.glob("signer-*.json")):
        try:
            out.append(json.loads(meta_path.read_text()))
        except Exception:
            pass
    return out


# ── Sign / Verify ─────────────────────────────────────────────────────────────

def sign(payload: bytes, kp: Keypair | None = None) -> tuple[str, str]:
    """Signiert payload mit dem aktuellen Keypair.

    Returns (signer_id, sig_b64).
    """
    kp = kp or get_or_create_keypair()
    sig = _mldsa.sign(kp.secret_key, payload)
    return kp.signer_id, base64.b64encode(sig).decode("ascii")


def verify(payload: bytes, sig_b64: str, signer_id: str) -> bool:
    """Verifiziert payload + Signatur gegen Public-Key des signer_id."""
    pk = load_public_key(signer_id)
    if pk is None:
        return False
    try:
        sig = base64.b64decode(sig_b64)
        return _mldsa.verify(pk, payload, sig)
    except Exception:
        return False
