# from email_agent import AIColdEmailAgent

# if __name__ == "__main__":
#     agent = AIColdEmailAgent()
    
#     agent.run_campaign(
#         excel_path="testing.xlsx",
#         test_mode=False  # Change to False to send actual emails
#     )

import pandas as pd
import smtplib
import json
import openai
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, OPENAI_API_KEY
import os
import google.generativeai as genai
import re
from io import BytesIO
from fastapi.middleware.cors import CORSMiddleware
import uuid

app = FastAPI()

# Allow frontend requests (Adjust the `origins` list as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace '*' with specific frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Configure AI Model
genai.configure(api_key="AIzaSyDgbb32htJ_IheA2E_ZDAR3CMTe4NJwn3I")  # Replace with your actual API key
openai.api_key = OPENAI_API_KEY

# Ensure logs directory exists
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Logging Setup
log_filename = f"{log_dir}/email_campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

campaign_results = {}  # Store results using a campaign ID (timestamp-based key)

from fastapi.responses import FileResponse, HTMLResponse
from datetime import datetime, timezone
import base64

# Add these global variables
TRACKING_DIR = "tracking"
DOMAIN = "http://127.0.0.1:8000"  # Replace with your actual domain
os.makedirs(TRACKING_DIR, exist_ok=True)

class EmailTracker:
    def __init__(self):
        self.tracking_data = {}
    
    def create_tracking_id(self, campaign_id, email):
        tracking_id = base64.urlsafe_b64encode(f"{campaign_id}:{email}".encode()).decode()
        
        # Initialize tracking data for new email
        if campaign_id not in campaign_results:
            campaign_results[campaign_id] = {
                'tracking': {},
                'bounces': set(),
                'replies': set(),
                'unsubscribes': set()
            }
        
        if email not in campaign_results[campaign_id]['tracking']:
            campaign_results[campaign_id]['tracking'][email] = {
                'opens': 0,
                'first_opened': None,
                'last_opened': None,
                'bounced': False,
                'replied': False,
                'unsubscribed': False
            }
        
        return tracking_id

    def track_bounce(self, campaign_id, email):
        if campaign_id in campaign_results:
            campaign_results[campaign_id]['bounces'].add(email)
            if email in campaign_results[campaign_id]['tracking']:
                campaign_results[campaign_id]['tracking'][email]['bounced'] = True
            self._update_campaign_metrics(campaign_id)

    def track_reply(self, campaign_id, email):
        if campaign_id in campaign_results:
            campaign_results[campaign_id]['replies'].add(email)
            if email in campaign_results[campaign_id]['tracking']:
                campaign_results[campaign_id]['tracking'][email]['replied'] = True
            self._update_campaign_metrics(campaign_id)

    def track_unsubscribe(self, campaign_id, email):
        if campaign_id in campaign_results:
            campaign_results[campaign_id]['unsubscribes'].add(email)
            if email in campaign_results[campaign_id]['tracking']:
                campaign_results[campaign_id]['tracking'][email]['unsubscribed'] = True
            self._update_campaign_metrics(campaign_id)

    def _update_campaign_metrics(self, campaign_id):
        if campaign_id in campaign_results:
            campaign = campaign_results[campaign_id]
            tracking = campaign.get('tracking', {})
            total_sent = campaign['successfulEmails']

            if total_sent > 0:
                # Calculate metrics
                total_opened = sum(1 for email_data in tracking.values() if email_data['opens'] > 0)
                total_bounces = len(campaign['bounces'])
                total_replies = len(campaign['replies'])
                total_unsubscribes = len(campaign['unsubscribes'])

                campaign['metrics'] = {
                    'unique_opens': total_opened,
                    'open_rate': (total_opened / total_sent * 100),
                    'bounce_rate': (total_bounces / total_sent * 100),
                    'reply_rate': (total_replies / total_sent * 100),
                    'unsubscribe_rate': (total_unsubscribes / total_sent * 100),
                    'total_opens': sum(data['opens'] for data in tracking.values()),
                    'total_bounces': total_bounces,
                    'total_replies': total_replies,
                    'total_unsubscribes': total_unsubscribes,
                    'last_updated': datetime.now(timezone.utc).isoformat()
                }

email_tracker = EmailTracker()

class AIColdEmailAgent:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.sender_email = SENDER_EMAIL
        self.sender_password = SENDER_PASSWORD

    def read_contacts(self, file_bytes):
        """Reads contacts from an uploaded Excel file."""
        try:
            df = pd.read_excel(BytesIO(file_bytes))
            required_columns = ["Emails", "Industry"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError(f"Missing required columns: {required_columns}")
            return df
        except Exception as e:
            logging.error(f"Error reading spreadsheet: {str(e)}")
            raise

    def clean_and_extract_json(self, text):
        """Extract and clean JSON content from AI response."""
        try:
            print("🟢 Raw Text Before Cleaning:", text)
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

            subject_match = re.search(r'"subject":\s*"([^"]+)"', text)
            body_match = re.search(r'"body":\s*"(.*?)"(?=\s*}$)', text, re.DOTALL)

            if not subject_match or not body_match:
                raise ValueError("Could not extract subject or body from JSON")

            clean_json = {
                "subject": subject_match.group(1),
                "body": body_match.group(1).replace("\\n", "\n")
            }

            print("✅ Successfully Cleaned JSON:", clean_json)
            return clean_json

        except Exception as e:
            logging.error(f"❌ JSON Cleaning Error: {str(e)}")
            return None

    def generate_email_content(self, industry,name):
        """Generates personalized email using AI."""
        try:
            prompt = f'''Generate ONLY a JSON object for a cold email. No other text.

    CONTEXT:
    - Industry: {industry}
    - Recipient Name: {name}
    - Sender: Nitin from Orange League Ventures pvt ltd
    - Purpose: Offering software services
    - Style: Professional with clear call to action

    FORMAT:
    {{
        "subject": "Subject line here",
        "body": "Email body here\\n\\nBest regards,\\nNitin\\nOrange League Ventures pvt ltd"
    }}'''
            
            model = genai.GenerativeModel("gemini-pro")
            response = model.generate_content(prompt)
            raw_text = response.candidates[0].content.parts[0].text
            
            print("🟢 Raw Response:", raw_text)  # Debug print
            
            # Remove any potential markdown formatting
            clean_text = raw_text.replace("```json", "").replace("```", "").strip()
            
            json_data = json.loads(clean_text)
            result = {
                "subject": json_data["subject"].strip(),
                "body": json_data["body"].replace("\\n", "\n").strip()
            }
            
            # Validate the result
            if not result["subject"] or not result["body"]:
                raise ValueError("Empty subject or body")
                
            print("✅ Parsed Content:", result)  # Debug print
            return result

        except Exception as e:
            logging.error(f"Error ({type(e).__name__}): {str(e)}\nRaw text: {raw_text}")
            return {"subject": "Default Subject", "body": "Default email content."}

    def send_email(self, to_email, subject, body):
        """Sends an email using SMTP."""
        try:
            msg = MIMEMultipart()
            msg["From"] = self.sender_email
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)

            logging.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            logging.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    def run_campaign(self, file_bytes, campaign_id):
        """Runs an email campaign and stores the results."""
        try:
            contacts_df = self.read_contacts(file_bytes)

            total_emails = len(contacts_df)
            successful_emails = 0
            failed_emails = 0
            industry_stats = {}

            for _, row in contacts_df.iterrows():
                # print(row["Name"],'testignsdsdsdfsdfcxvxcweewr')
                email_content = self.generate_email_content(row["Industry"],row["Name"])
                subject, body = email_content["subject"], email_content["body"]

                success = self.send_email(row["Emails"], subject, body)
                
                if success:
                    successful_emails += 1
                else:
                    failed_emails += 1

                # Count emails per industry
                industry = row["Industry"]
                industry_stats[industry] = industry_stats.get(industry, 0) + 1

                time.sleep(2)  # Avoid spam detection

            # Store results
            campaign_results[campaign_id] = {
                "totalEmails": total_emails,
                "successfulEmails": successful_emails,
                "failedEmails": failed_emails,
                "industries": industry_stats,
            }

        except Exception as e:
            logging.error(f"Campaign error: {str(e)}")
            campaign_results[campaign_id] = {
                "error": str(e),
                "totalEmails": 0,
                "successfulEmails": 0,
                "failedEmails": 0,
                "industries": {},
            }

email_agent = AIColdEmailAgent()

@app.post("/upload-excel/")
async def upload_excel(file: UploadFile = File(...)):
    """API to upload an Excel file and extract contacts."""
    file_bytes = await file.read()
    try:
        contacts_df = email_agent.read_contacts(file_bytes)
        return {"message": "File processed successfully", "total_contacts": len(contacts_df)}
    except Exception as e:
        return {"error": str(e)}

@app.post("/send-emails/")
async def send_emails(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    """API to start an email campaign in the background."""
    file_bytes = await file.read()
    campaign_id = str(uuid.uuid4())  # Generate unique campaign ID
    campaign_results[campaign_id] = None  # Initialize result storage

    background_tasks.add_task(email_agent.run_campaign, file_bytes, campaign_id)
    return {"message": "Email campaign started", "campaign_id": campaign_id}

@app.get("/campaign-results/")
async def get_campaign_results(campaign_id: str):
    """API to fetch campaign results."""
    if campaign_id not in campaign_results:
        return {"error": "Invalid campaign ID or results not available yet"}
    
    if campaign_results[campaign_id] is None:
        return {"status": "Processing", "message": "Campaign is still running"}
    
    return campaign_results[campaign_id]
    
@app.get("/track/{tracking_id}")
async def track_email_open(tracking_id: str):
    """Endpoint to track email opens."""
    email_tracker.track_open(tracking_id)
    # Return a transparent 1x1 pixel
    return FileResponse(f"{TRACKING_DIR}/pixel.png")

@app.get("/campaign-metrics/{campaign_id}")
async def get_campaign_metrics(campaign_id: str):
    """Get detailed metrics for a campaign."""
    if campaign_id not in campaign_results:
        return {"error": "Campaign not found"}
    
    campaign = campaign_results[campaign_id]
    
    return {
        "campaign_id": campaign_id,
        "metrics": {
            "total_emails": campaign['totalEmails'],
            "successful_sends": campaign['successfulEmails'],
            "failed_sends": campaign['failedEmails'],
            "unique_opens": campaign['metrics']['unique_opens'],
            "open_rate": round(campaign['metrics']['open_rate'], 2),
            "total_opens": campaign['metrics']['total_opens'],
            "industry_breakdown": campaign['industries']
        },
        "detailed_tracking": {
            email: {
                "opens": data['opens'],
                "first_opened": data['first_opened'],
                "last_opened": data['last_opened']
            } for email, data in campaign['tracking'].items()
        }
    }
    