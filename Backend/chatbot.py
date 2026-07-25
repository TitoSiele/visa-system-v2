import re
from sqlalchemy.orm import Session
import models


# ---------- FAQ content (no login required) ----------

FAQ_ANSWERS = {
    "greeting": "Hello! I can help with visa types, required documents, application status, and payments. What would you like to know?",
    "how_to_apply": (
        "To apply: 1) Register an account, 2) Log in and choose a visa type, "
        "3) Upload the required documents, 4) Pay the application fee via M-Pesa, "
        "5) Track your status on your dashboard."
    ),
    "fees": "Application fees vary by visa type and are paid securely via M-Pesa once your application is created. Check your dashboard for the exact amount.",
    "contact": "For further help, please reach out to our support team through the contact details on our homepage.",
    "fallback": "I'm not sure about that yet. Try asking about visa types, required documents, how to apply, fees, or (if logged in) your application status.",
}


def get_visa_types_answer(db: Session) -> str:
    visa_types = db.query(models.VisaType).all()
    if not visa_types:
        return "We don't have any visa types listed at the moment. Please check back soon."
    lines = [f"- {vt.name}: {vt.description or 'No description available.'}" for vt in visa_types]
    return "Here are our available visa types:\n" + "\n".join(lines)


def get_required_documents_answer(db: Session, message: str) -> str:
    visa_types = db.query(models.VisaType).all()
    message_lower = message.lower()

    for vt in visa_types:
        if vt.name.lower() in message_lower:
            docs = vt.required_documents or []
            if not docs:
                return f"No document list is set for {vt.name} yet — please contact support."
            return f"For {vt.name}, you'll need: " + ", ".join(docs)

    # No specific visa type matched — list all
    lines = []
    for vt in visa_types:
        docs = vt.required_documents or []
        lines.append(f"- {vt.name}: {', '.join(docs) if docs else 'not specified'}")
    return "Required documents by visa type:\n" + "\n".join(lines)


# ---------- Intent detection ----------

INTENT_PATTERNS = [
    ("greeting", r"\b(hi|hello|hey)\b"),
    ("visa_types", r"\b(visa types|what visas|which visas|available visas)\b"),
    ("required_documents", r"\b(document|documents|paperwork|required)\b"),
    ("how_to_apply", r"\b(how.*apply|apply.*how|process|steps)\b"),
    ("fees", r"\b(fee|fees|cost|price|how much)\b"),
    ("contact", r"\b(contact|support|help|human|agent)\b"),
    ("status", r"\b(status|progress|where is my|track)\b"),
    ("documents_personal", r"\b(my documents|uploaded|verified|missing)\b"),
    ("payment", r"\b(paid|payment|mpesa|receipt)\b"),
]


def detect_intent(message: str) -> str:
    message_lower = message.lower()
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, message_lower):
            return intent
    return "fallback"


# ---------- Personalized answers (login required) ----------

def get_status_answer(db: Session, user) -> str:
    applications = db.query(models.Application).filter(
        models.Application.user_id == user.id
    ).order_by(models.Application.submitted_at.desc()).all()

    if not applications:
        return "You don't have any applications yet. Start one from your dashboard."

    lines = []
    for app in applications:
        visa_name = app.visa_type.name if app.visa_type else "Unknown visa"
        lines.append(f"- {visa_name}: {app.status}")
    return "Here's your application status:\n" + "\n".join(lines)


def get_documents_answer(db: Session, user) -> str:
    applications = db.query(models.Application).filter(
        models.Application.user_id == user.id
    ).all()

    if not applications:
        return "You don't have any applications yet, so there are no documents to show."

    lines = []
    for app in applications:
        docs = db.query(models.Document).filter(
            models.Document.application_id == app.id
        ).all()
        visa_name = app.visa_type.name if app.visa_type else "Unknown visa"
        if not docs:
            lines.append(f"- {visa_name}: no documents uploaded yet")
        else:
            doc_lines = [f"{d.document_type} ({'verified' if d.verified else 'pending verification'})" for d in docs]
            lines.append(f"- {visa_name}: " + ", ".join(doc_lines))
    return "Here's your document status:\n" + "\n".join(lines)


def get_payment_answer(db: Session, user) -> str:
    applications = db.query(models.Application).filter(
        models.Application.user_id == user.id
    ).all()

    if not applications:
        return "You don't have any applications yet, so there's no payment history."

    lines = []
    for app in applications:
        payments = db.query(models.Payment).filter(
            models.Payment.application_id == app.id
        ).all()
        visa_name = app.visa_type.name if app.visa_type else "Unknown visa"
        if not payments:
            lines.append(f"- {visa_name}: no payment made yet")
        else:
            latest = payments[-1]
            lines.append(f"- {visa_name}: {latest.status} (KES {latest.amount})")
    return "Here's your payment status:\n" + "\n".join(lines)


# ---------- Main entry point ----------

def get_chatbot_response(message: str, db: Session, user=None) -> str:
    intent = detect_intent(message)

    if intent == "visa_types":
        return get_visa_types_answer(db)
    if intent == "required_documents":
        return get_required_documents_answer(db, message)
    if intent in FAQ_ANSWERS and intent not in ("status", "documents_personal", "payment"):
        return FAQ_ANSWERS[intent]

    # Personalized intents require login
    if intent in ("status", "documents_personal", "payment"):
        if not user:
            return "Please log in to check your personal application status, documents, or payments."
        if intent == "status":
            return get_status_answer(db, user)
        if intent == "documents_personal":
            return get_documents_answer(db, user)
        if intent == "payment":
            return get_payment_answer(db, user)

    return FAQ_ANSWERS["fallback"]