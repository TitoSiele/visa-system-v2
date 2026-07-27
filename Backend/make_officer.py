from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
import models

db = SessionLocal()

email = "titussiele5@gmail.com"  # change this to your actual registered email
user = db.query(models.User).filter(models.User.email == email).first()

if user:
    user.role = "officer"
    db.commit()
    print(f"Updated {email} to role: {user.role}")
else:
    print("No user found with that email")

db.close()