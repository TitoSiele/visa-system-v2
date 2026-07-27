import os
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from supabase import create_client, Client

router = APIRouter(prefix="/api/applications", tags=["biometrics"])
user_router = APIRouter(prefix="/api/users", tags=["user-biometrics"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BUCKET_NAME = "biometrics"
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_documents"
UPLOAD_DIR.mkdir(exist_ok=True)

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise HTTPException(
                status_code=500,
                detail="Supabase storage is not configured (missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY in .env)",
            )
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase


def save_face_photo_bytes(user_id: int, contents: bytes, content_type: str, upload_dir: str | os.PathLike[str] | None = None) -> str:
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Face photo must be JPEG, PNG, or WEBP")

    ext = "jpg"
    if content_type == "image/png":
        ext = "png"
    elif content_type == "image/webp":
        ext = "webp"

    target_dir = Path(upload_dir or UPLOAD_DIR)
    target_dir.mkdir(exist_ok=True, parents=True)
    target_path = target_dir / f"user_{user_id}_face.{ext}"
    target_path.write_bytes(contents)
    return str(target_path)


def has_face_photo(user_id: int, upload_dir: str | os.PathLike[str] | None = None) -> bool:
    target_dir = Path(upload_dir or UPLOAD_DIR)
    for ext in ("jpg", "jpeg", "png", "webp"):
        if (target_dir / f"user_{user_id}_face.{ext}").exists():
            return True
    return False


@user_router.post("/{user_id}/biometrics/face/register")
async def register_face_biometric(user_id: int, photo: UploadFile = File(...)):
    contents = await photo.read()
    save_face_photo_bytes(user_id, contents, photo.content_type)
    return {"status": "registered", "user_id": user_id}


@user_router.post("/{user_id}/biometrics/face/verify")
async def verify_face_biometric(user_id: int, photo: UploadFile | None = File(None)):
    if not has_face_photo(user_id):
        raise HTTPException(status_code=404, detail="No face biometric enrolled for this user yet")
    return {"status": "verified", "user_id": user_id}


@router.post("/{application_id}/biometrics/photo")
async def upload_biometric_photo(application_id: int, photo: UploadFile = File(...)):
    if photo.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Photo must be JPEG, PNG, or WEBP")

    contents = await photo.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Photo must be under 5MB")

    ext = photo.filename.split(".")[-1] if "." in photo.filename else "jpg"
    storage_path = f"applications/{application_id}/photo_{uuid.uuid4().hex}.{ext}"

    supabase = get_supabase()

    try:
        supabase.storage.from_(BUCKET_NAME).upload(
            storage_path,
            contents,
            {"content-type": photo.content_type},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload to storage failed: {e}")

    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)

    try:
        supabase.table("applications").update({"photo_url": public_url}).eq(
            "id", application_id
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Saved photo but failed to update application record: {e}")

    return {"application_id": application_id, "photo_url": public_url}


@router.get("/{application_id}/biometrics/photo")
async def get_biometric_photo(application_id: int):
    supabase = get_supabase()
    try:
        result = (
            supabase.table("applications")
            .select("photo_url")
            .eq("id", application_id)
            .single()
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Application not found: {e}")

    return {"application_id": application_id, "photo_url": result.data.get("photo_url")}