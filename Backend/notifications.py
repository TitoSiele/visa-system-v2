import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@yourdomain.com")

AT_USERNAME = os.getenv("AT_USERNAME")
AT_API_KEY = os.getenv("AT_API_KEY")


def send_email(to_email: str, subject: str, body: str) -> bool:
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[email skipped - no SMTP config] to={to_email} subject={subject}")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def send_sms(phone_number: str, message: str) -> bool:
    if not AT_USERNAME or not AT_API_KEY:
        print(f"[sms skipped - no Africa's Talking config] to={phone_number} message={message}")
        return False
    try:
        import africastalking
        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        africastalking.SMS.send(message, [phone_number])
        return True
    except Exception as e:
        print(f"SMS send failed: {e}")
        return False


def send_welcome_email(user):
    subject = "Welcome to the Immigration Visa System"
    body = (
        f"Dear {user.full_name},\n\n"
        f"Thank you for registering with the Immigration Visa System.\n"
        f"You can now log in, start a visa application, upload your documents, "
        f"and track your application status from your dashboard.\n\n"
        f"If you have any questions, feel free to reach out to our support team.\n\n"
        f"Regards,\nVisa Consultancy Team"
    )
    if user.email:
        send_email(user.email, subject, body)


def notify_status_change(user, application, new_status: str):
    subject = f"Application #{application.id} — Status Update"
    body = (
        f"Dear {user.full_name},\n\n"
        f"Your application (ID: {application.id}) status has been updated to: {new_status}.\n"
        f"Log in to your account for details.\n\nRegards,\nVisa Consultancy Team"
    )
    if user.email:
        send_email(user.email, subject, body)
    if user.phone_number:
        send_sms(user.phone_number, f"Application #{application.id} status: {new_status}")


def notify_payment_result(user, application, amount, success: bool):
    if success:
        subject = f"Payment Received — Application #{application.id}"
        body = f"Dear {user.full_name},\n\nWe've received your payment of KES {amount} for application #{application.id}.\n\nRegards,\nVisa Consultancy Team"
        sms_msg = f"Payment of KES {amount} received for App #{application.id}. Thank you."
    else:
        subject = f"Payment Failed — Application #{application.id}"
        body = f"Dear {user.full_name},\n\nYour M-Pesa payment for application #{application.id} did not go through. Please try again.\n\nRegards,\nVisa Consultancy Team"
        sms_msg = f"Payment for App #{application.id} failed. Please retry."
    if user.email:
        send_email(user.email, subject, body)
    if user.phone_number:
        send_sms(user.phone_number, sms_msg)