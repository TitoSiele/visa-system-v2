from dotenv import load_dotenv
load_dotenv()

import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from biometrics_routes import router as biometrics_router, user_router
from biometrics_fingerprint_routes import router as fingerprint_router
# Ensure this directory is on sys.path so sibling modules
# (models, crud, schemas, etc.) resolve correctly under Vercel's
# runtime, where main.py may be imported without Backend/ on the path.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from pydantic import BaseModel

import models, crud, schemas
import payments as mpesa
import notifications
import chatbot
from database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Immigration System API")
app.include_router(biometrics_router)
app.include_router(user_router)
app.include_router(fingerprint_router)

UPLOAD_DIR = "uploaded_documents"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "https://visa-system-delta.vercel.app",
],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- JWT Configuration ---
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login", auto_error=True)
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="api/login", auto_error=False)


class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict


class TokenData(BaseModel):
    email: str


class ChatMessage(BaseModel):
    message: str


class BiometricLoginRequest(BaseModel):
    email: str
    method: str


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        return TokenData(email=email)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")


def get_authenticated_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    token_data = verify_token(token)
    user = crud.get_user_by_email(db, email=token_data.email)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_optional_user(
    token: str = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
):
    if not token:
        return None
    try:
        token_data = verify_token(token)
        return crud.get_user_by_email(db, email=token_data.email)
    except HTTPException:
        return None


def require_officer(user=Depends(get_authenticated_user)):
    if user.role not in ("officer", "admin"):
        raise HTTPException(status_code=403, detail="Officer or admin access required")
    return user


# --- Upload safety helpers ---
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def safe_upload_filename(original_filename: str) -> str:
    ext = os.path.splitext(original_filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}",
        )
    return f"{uuid.uuid4().hex}{ext}"


@app.get("/")
def root():
    return {"message": "Immigration System Backend is Live"}


@app.post("/api/register", response_model=schemas.User)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = crud.create_user(db=db, user=user)
    notifications.send_welcome_email(new_user)
    return new_user


@app.get("/api/users/by-email/{email}")
def get_user_by_email(email: str, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=email)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": db_user.id,
        "email": db_user.email,
        "full_name": db_user.full_name,
        "role": db_user.role,
    }


@app.post("/api/login", response_model=Token)
def login(user: schemas.UserLogin, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=user.email)
    if not db_user or not crud.pwd_context.verify(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(
        data={"sub": db_user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "email": db_user.email,
            "full_name": db_user.full_name,
            "role": db_user.role,
        },
    }


@app.post("/api/login/biometric", response_model=Token)
def login_biometric(body: BiometricLoginRequest, db: Session = Depends(get_db)):
    db_user = crud.get_user_by_email(db, email=body.email)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    method = (body.method or "").lower()
    if method == "face":
        from biometrics_routes import has_face_photo
        if not has_face_photo(db_user.id):
            raise HTTPException(status_code=401, detail="No face biometric enrolled for this user yet")
    elif method == "fingerprint":
        from biometrics_fingerprint_routes import has_fingerprint_credential
        if not has_fingerprint_credential(db_user.id):
            raise HTTPException(status_code=401, detail="No fingerprint biometric enrolled for this user yet")
    else:
        raise HTTPException(status_code=400, detail="Unsupported biometric method")

    access_token = create_access_token(
        data={"sub": db_user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": db_user.id,
            "email": db_user.email,
            "full_name": db_user.full_name,
            "role": db_user.role,
        },
    }


@app.get("/api/visa-types")
def get_visa_types(db: Session = Depends(get_db)):
    visa_types = db.query(models.VisaType).all()
    return [
        {
            "id": vt.id,
            "name": vt.name,
            "description": vt.description,
            "required_documents": vt.required_documents,
        }
        for vt in visa_types
    ]


@app.get("/api/applications")
def get_applications(
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    applications = db.query(models.Application).filter(
        models.Application.user_id == user.id
    ).all()

    return {
        "applications": [
            {
                "id": application.id,
                "visa_type_name": application.visa_type.name if application.visa_type else "Unknown",
                "status": application.status,
                "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
            }
            for application in applications
        ]
    }


@app.post("/api/applications")
def create_application(
    visa_type_id: int,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    visa_type = db.query(models.VisaType).filter(models.VisaType.id == visa_type_id).first()
    if not visa_type:
        raise HTTPException(status_code=400, detail="Invalid visa type selected")

    app_data = schemas.ApplicationCreate(visa_type_id=visa_type_id)
    new_app = crud.create_application(db=db, application=app_data, user_id=user.id)

    return {
        "id": new_app.id,
        "user_id": new_app.user_id,
        "visa_type_id": new_app.visa_type_id,
        "status": new_app.status,
        "submitted_at": new_app.submitted_at.isoformat() if new_app.submitted_at else None,
    }


@app.get("/api/applications/{app_id}")
def get_application(
    app_id: int,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == user.id,
    ).first()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    return {
        "id": application.id,
        "user_id": application.user_id,
        "visa_type_id": application.visa_type_id,
        "visa_type_name": application.visa_type.name if application.visa_type else "Unknown",
        "status": application.status,
        "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
    }


@app.get("/api/applications/{app_id}/documents")
def get_application_documents(
    app_id: int,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == user.id,
    ).first()

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    documents = db.query(models.Document).filter(
        models.Document.application_id == app_id
    ).all()

    return [
        {
            "id": doc.id,
            "document_type": doc.document_type,
            "verified": doc.verified,
        }
        for doc in documents
    ]


@app.post("/api/applications/{app_id}/documents")
async def upload_document(
    app_id: int,
    document_type: str,
    file: UploadFile = File(...),
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == user.id,
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    generated_name = safe_upload_filename(file.filename)
    file_path = os.path.join(UPLOAD_DIR, generated_name)

    size = 0
    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                buffer.close()
                os.remove(file_path)
                raise HTTPException(status_code=400, detail="File too large (max 10 MB)")
            buffer.write(chunk)

    doc = models.Document(
        application_id=app_id,
        document_type=document_type,
        file_path=file_path,
        verified=False,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {"id": doc.id, "document_type": doc.document_type}


@app.get("/api/documents/{doc_id}/file")
def get_document_file(
    doc_id: int,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    doc = crud.get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    application = db.query(models.Application).filter(
        models.Application.id == doc.application_id
    ).first()

    is_owner = application and application.user_id == user.id
    is_staff = user.role in ("officer", "admin")
    if not (is_owner or is_staff):
        raise HTTPException(status_code=403, detail="Not authorized to view this document")

    if not os.path.exists(doc.file_path):
        raise HTTPException(status_code=404, detail="File missing on server")

    return FileResponse(doc.file_path)


# =========================================================
# CHATBOT
# =========================================================

@app.post("/api/chatbot")
def chatbot_reply(
    payload: ChatMessage,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    reply = chatbot.get_chatbot_response(payload.message, db, user=user)
    return {"reply": reply}


# =========================================================
# PAYMENTS (M-Pesa)
# =========================================================

@app.post("/api/applications/{app_id}/pay")
def pay_for_application(
    app_id: int,
    payload: schemas.PaymentInitiate,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == user.id,
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        stk_response = mpesa.initiate_stk_push(
            phone_number=payload.phone_number,
            amount=payload.amount,
            account_reference=f"APP{app_id}",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"MPESA ERROR: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="Could not reach M-Pesa. Try again shortly.")

    checkout_id = stk_response.get("CheckoutRequestID")
    payment = crud.create_payment(
        db, application_id=app_id, amount=payload.amount,
        phone_number=payload.phone_number, checkout_request_id=checkout_id,
    )

    return {
        "payment_id": payment.id,
        "status": payment.status,
        "message": "Check your phone to enter your M-Pesa PIN.",
    }


@app.post("/api/mpesa/callback")
async def mpesa_callback(request: dict, db: Session = Depends(get_db)):
    result = request.get("Body", {}).get("stkCallback", {})
    checkout_id = result.get("CheckoutRequestID")
    result_code = result.get("ResultCode")

    payment = crud.get_payment_by_checkout_id(db, checkout_id)
    if not payment:
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    if result_code == 0:
        receipt = None
        for item in result.get("CallbackMetadata", {}).get("Item", []):
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = item.get("Value")
        crud.update_payment_status(db, payment.id, "Completed", mpesa_receipt_number=receipt)
        success = True
    else:
        crud.update_payment_status(db, payment.id, "Failed")
        success = False

    application = db.query(models.Application).filter(
        models.Application.id == payment.application_id
    ).first()
    if application and application.owner:
        notifications.notify_payment_result(application.owner, application, payment.amount, success)

    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@app.get("/api/applications/{app_id}/payments", response_model=list[schemas.PaymentOut])
def get_application_payments(
    app_id: int,
    user=Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == user.id,
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return db.query(models.Payment).filter(models.Payment.application_id == app_id).all()


# =========================================================
# ADMIN / OFFICER ROUTES
# =========================================================

@app.get("/api/admin/applications")
def admin_list_applications(
    status_filter: str | None = None,
    officer=Depends(require_officer),
    db: Session = Depends(get_db),
):
    applications = crud.get_all_applications(db, status=status_filter)
    return [
        {
            "id": application.id,
            "user_id": application.user_id,
            "applicant_name": application.owner.full_name if application.owner else "Unknown",
            "applicant_email": application.owner.email if application.owner else "Unknown",
            "visa_type_name": application.visa_type.name if application.visa_type else "Unknown",
            "status": application.status,
            "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
        }
        for application in applications
    ]


@app.get("/api/admin/applications/{app_id}/documents")
def admin_get_application_documents(
    app_id: int,
    officer=Depends(require_officer),
    db: Session = Depends(get_db),
):
    application = db.query(models.Application).filter(models.Application.id == app_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    documents = db.query(models.Document).filter(models.Document.application_id == app_id).all()
    return [
        {
            "id": doc.id,
            "document_type": doc.document_type,
            "verified": doc.verified,
        }
        for doc in documents
    ]


@app.patch("/api/admin/documents/{doc_id}/verify")
def admin_verify_document(
    doc_id: int,
    payload: schemas.DocumentVerify,
    officer=Depends(require_officer),
    db: Session = Depends(get_db),
):
    doc = crud.set_document_verified(db, doc_id, payload.verified)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"id": doc.id, "document_type": doc.document_type, "verified": doc.verified}


@app.patch("/api/admin/applications/{app_id}/status")
def admin_update_application_status(
    app_id: int,
    payload: schemas.ApplicationStatusUpdate,
    officer=Depends(require_officer),
    db: Session = Depends(get_db),
):
    allowed = {"Pending", "Approved", "Rejected", "More Info"}
    if payload.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Status must be one of {allowed}")

    application = crud.update_application_status(db, app_id, payload.status)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    if application.owner:
        notifications.notify_status_change(application.owner, application, payload.status)

    return {"id": application.id, "status": application.status}