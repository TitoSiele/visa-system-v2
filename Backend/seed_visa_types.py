# seed_visa_types.py

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.orm import Session
from database import SessionLocal
import models


def seed_visa_types():
    db: Session = SessionLocal()
    try:
        existing = db.query(models.VisaType).count()
        if existing > 0:
            print(f"visa_types already has {existing} rows, skipping seed.")
            return

        visa_types = [
            models.VisaType(
                name="Tourist Visa",
                description="For short-term travel and tourism purposes.",
                required_documents=["Passport", "Bank Statement", "Return Ticket"],
            ),
            models.VisaType(
                name="Work Visa",
                description="For individuals with a confirmed job offer.",
                required_documents=["Passport", "Job Offer Letter", "Work Permit"],
            ),
            models.VisaType(
                name="Student Visa",
                description="For individuals enrolled in an accredited institution.",
                required_documents=["Passport", "Admission Letter", "Bank Statement"],
            ),
        ]

        db.add_all(visa_types)
        db.commit()
        print(f"Seeded {len(visa_types)} visa types.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_visa_types()