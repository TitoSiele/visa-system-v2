import json
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
    base64url_to_bytes,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
)
from supabase import create_client, Client

router = APIRouter(prefix="/api/users", tags=["biometrics-fingerprint"])

RP_ID = os.getenv("WEBAUTHN_RP_ID", "localhost")
RP_NAME = os.getenv("WEBAUTHN_RP_NAME", "Immigration Visa System")
ORIGIN = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:5500")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BASE_DIR = Path(__file__).resolve().parent
LOCAL_CREDENTIALS_PATH = BASE_DIR / "uploaded_documents" / "fingerprint_credentials.json"

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise HTTPException(status_code=500, detail="Supabase is not configured")
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase


def _ensure_local_store() -> Path:
    LOCAL_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOCAL_CREDENTIALS_PATH.exists():
        LOCAL_CREDENTIALS_PATH.write_text("{}", encoding="utf-8")
    return LOCAL_CREDENTIALS_PATH


def _read_local_credentials() -> dict[str, list[dict]]:
    try:
        return json.loads(_ensure_local_store().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_local_credentials(data: dict[str, list[dict]]) -> None:
    _ensure_local_store().write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_fingerprint_credentials(user_id: int) -> list[dict]:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            supabase = get_supabase()
            creds = supabase.table("webauthn_credentials").select("credential_id", "public_key", "sign_count").eq("user_id", user_id).execute()
            return creds.data or []
        except Exception:
            pass
    store = _read_local_credentials()
    return store.get(str(user_id), [])


def save_fingerprint_credentials(user_id: int, credential_id: str, public_key: str, sign_count: int) -> None:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            supabase = get_supabase()
            supabase.table("webauthn_credentials").insert({
                "user_id": user_id,
                "credential_id": credential_id,
                "public_key": public_key,
                "sign_count": sign_count,
            }).execute()
            return
        except Exception:
            pass
    store = _read_local_credentials()
    entries = store.setdefault(str(user_id), [])
    entries.append({"credential_id": credential_id, "public_key": public_key, "sign_count": sign_count})
    _write_local_credentials(store)


def update_fingerprint_sign_count(user_id: int, credential_id: str, sign_count: int) -> None:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            supabase = get_supabase()
            supabase.table("webauthn_credentials").update({"sign_count": sign_count}).eq("user_id", user_id).eq("credential_id", credential_id).execute()
            return
        except Exception:
            pass
    store = _read_local_credentials()
    entries = store.get(str(user_id), [])
    for entry in entries:
        if entry.get("credential_id") == credential_id:
            entry["sign_count"] = sign_count
            break
    _write_local_credentials(store)


def has_fingerprint_credential(user_id: int) -> bool:
    return bool(get_fingerprint_credentials(user_id))


_challenge_store: dict[int, bytes] = {}


class FinishRegistration(BaseModel):
    credential: dict


class FinishVerification(BaseModel):
    credential: dict


@router.post("/{user_id}/biometrics/fingerprint/register/start")
async def start_registration(user_id: int):
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        supabase = get_supabase()
        user_row = (
            supabase.table("users").select("id, full_name, email").eq("id", user_id).single().execute()
        )
        if not user_row.data:
            raise HTTPException(status_code=404, detail="User not found")
        user_name = user_row.data["email"] or f"user-{user_id}"
        user_display_name = user_row.data.get("full_name") or f"user-{user_id}"
    else:
        user_name = f"user-{user_id}"
        user_display_name = f"user-{user_id}"

    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=str(user_id).encode(),
        user_name=user_name,
        user_display_name=user_display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    _challenge_store[user_id] = options.challenge
    return {"publicKey": json.loads(options_to_json(options))}

@router.post("/{user_id}/biometrics/fingerprint/register/finish")
async def finish_registration(user_id: int, body: FinishRegistration):
    challenge = _challenge_store.pop(user_id, None)
    if not challenge:
        raise HTTPException(status_code=400, detail="No registration in progress for this user (call /register/start first)")

    try:
        verification = verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registration verification failed: {e}")

    try:
        save_fingerprint_credentials(
            user_id=user_id,
            credential_id=verification.credential_id.hex(),
            public_key=verification.credential_public_key.hex(),
            sign_count=verification.sign_count,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verified but failed to store credential: {e}")

    return {"status": "registered", "user_id": user_id}


@router.post("/{user_id}/biometrics/fingerprint/verify/start")
async def start_verification(user_id: int):
    creds = get_fingerprint_credentials(user_id)
    if not creds:
        raise HTTPException(status_code=404, detail="No biometric enrolled for this user yet")

    allow_credentials = [
        PublicKeyCredentialDescriptor(id=bytes.fromhex(c["credential_id"])) for c in creds
    ]

    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _challenge_store[user_id] = options.challenge
    return {"publicKey": json.loads(options_to_json(options))}


@router.post("/{user_id}/biometrics/fingerprint/verify/finish")
async def finish_verification(user_id: int, body: FinishVerification):
    challenge = _challenge_store.pop(user_id, None)
    if not challenge:
        raise HTTPException(status_code=400, detail="No verification in progress for this user (call /verify/start first)")

    cred_id_b64url = body.credential.get("id")
    if not cred_id_b64url:
        raise HTTPException(status_code=400, detail="Malformed credential payload")

    cred_id_hex = base64url_to_bytes(cred_id_b64url).hex()
    stored_entries = get_fingerprint_credentials(user_id)
    stored = None
    for entry in stored_entries:
        if entry.get("credential_id") == cred_id_hex:
            stored = entry
            break
    if not stored:
        raise HTTPException(status_code=404, detail="Credential not recognized for this user")

    try:
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            credential_public_key=bytes.fromhex(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"],
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Biometric verification failed: {e}")

    update_fingerprint_sign_count(user_id, cred_id_hex, verification.new_sign_count)

    return {"status": "verified", "user_id": user_id}