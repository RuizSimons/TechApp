# main.py
# To run this:
# 1. Install the new required library:
#    pip install sendgrid
#
# 2. Set your environment variables (including the new SendGrid ones) and run.

import base64
import os
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from supabase import create_client, Client
from weasyprint import HTML
# --- NEW: SendGrid Imports ---
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (Mail, Attachment, FileContent, FileName, FileType, Disposition)

# --- Pydantic Model for Incoming Data ---
class WorkOrder(BaseModel):
    customerName: str
    customerEmail: str
    workPerformed: str
    signatureImage: str

# --- Load credentials from environment variables ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
# --- NEW: SendGrid and Email Environment Variables ---
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") # The email you verified with SendGrid
COMPANY_EMAIL = os.environ.get("COMPANY_EMAIL") # Your internal email for receiving copies

# Check for all required environment variables
if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, SENDGRID_API_KEY, SENDER_EMAIL, COMPANY_EMAIL]):
    raise RuntimeError("One or more required environment variables are not set.")

# --- Initialize Clients ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
app = FastAPI(
    title="Field Technician Backend",
    description="API for handling work orders, with Supabase, PDF generation, and email."
)

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# --- Create a directory for output files ---
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- Function to Generate PDF ---
def create_pdf_report(order_data: dict, signature_url: str) -> bytes:
    """Generates a PDF report and returns it as bytes, without saving to a file."""
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Work Order</title>
        <style>
            body {{ font-family: sans-serif; color: #333; }}
            .container {{ width: 80%; margin: auto; border: 1px solid #eee; padding: 20px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #444; border-bottom: 2px solid #4A90E2; padding-bottom: 10px; }}
            .details p {{ margin: 5px 0; }} .details strong {{ display: inline-block; width: 150px; }}
            .signature-box {{ margin-top: 40px; border-top: 1px solid #ccc; padding-top: 20px; }}
            .signature-box img {{ max-width: 250px; max-height: 150px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Work Order Sign-Off</h1>
            <div class="details">
                <p><strong>Customer Name:</strong> {order_data['customer_name']}</p>
                <p><strong>Customer Email:</strong> {order_data['customer_email']}</p>
                <p><strong>Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
            </div>
            <div class="details"><h3>Work Performed:</h3><p>{order_data['work_performed']}</p></div>
            <div class="signature-box"><h3>Customer Signature:</h3><img src="{signature_url}" alt="Customer Signature"></div>
        </div>
    </body>
    </html>
    """
    # Return the PDF content as bytes
    return HTML(string=html_template).write_pdf()


# --- NEW: Function to Send Email ---
def send_email_with_attachment(to_email: str, subject: str, body: str, pdf_content: bytes, pdf_filename: str):
    """Sends an email with a PDF attachment using SendGrid."""
    message = Mail(
        from_email=SENDER_EMAIL,
        to_emails=to_email,
        subject=subject,
        html_content=body
    )
    
    # Encode the PDF bytes to Base64
    encoded_file = base64.b64encode(pdf_content).decode()
    
    # Create the attachment object
    attached_file = Attachment(
        FileContent(encoded_file),
        FileName(pdf_filename),
        FileType('application/pdf'),
        Disposition('attachment')
    )
    message.attachment = attached_file
    
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"Email sent to {to_email}. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")


# --- API Endpoint for Submission (Updated) ---
@app.post("/submit-work-order/")
async def submit_work_order(order: WorkOrder):
    """
    Receives work order, saves to Supabase, generates PDF, and emails it.
    """
    try:
        # 1. Handle signature and save to Supabase
        header, encoded_data = order.signatureImage.split(",", 1)
        image_data = base64.b64decode(encoded_data)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_customer_name = re.sub(r'[^a-zA-Z0-9_]', '_', order.customerName)
        file_path_in_bucket = f"signature_{safe_customer_name}_{timestamp}.png"
        supabase.storage.from_("signatures").upload(path=file_path_in_bucket, file=image_data, file_options={"content-type": "image/png"})
        
        public_url = supabase.storage.from_("signatures").get_public_url(file_path_in_bucket)
        work_order_data = {"customer_name": order.customerName, "customer_email": order.customerEmail, "work_performed": order.workPerformed, "signature_url": public_url}
        supabase.table("work_orders").insert(work_order_data).execute()
        
        # 2. Generate PDF in memory
        pdf_filename = f"WorkOrder_{safe_customer_name}_{timestamp}.pdf"
        pdf_bytes = create_pdf_report(work_order_data, public_url)
        
        # --- 3. NEW: Send Emails ---
        email_subject = f"Work Order Confirmation for {order.customerName}"
        email_body = f"Dear {order.customerName},<br><br>Thank you for your business. Please find your signed work order attached.<br><br>Sincerely,<br>The Team"
        
        # Send to customer
        send_email_with_attachment(order.customerEmail, email_subject, email_body, pdf_bytes, pdf_filename)
        
        # Send to company
        send_email_with_attachment(COMPANY_EMAIL, f"COPY: {email_subject}", email_body, pdf_bytes, pdf_filename)
        
        return {"message": "Work order processed and emails sent successfully!"}

    except Exception as e:
        print(f"An error occurred: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")
