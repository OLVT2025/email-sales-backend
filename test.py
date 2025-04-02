import pandas as pd
import smtplib
import json
import time
import asyncio
import logging
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone,timedelta
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD
import os
import uuid
import base64
from fastapi.middleware.cors import CORSMiddleware
from io import BytesIO
import google.generativeai as genai
from PIL import Image
import numpy as np
import requests
from bs4 import BeautifulSoup
import sqlite3  # Import the sqlite3 module
import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://salesdb_po5a_user:SaQt3HZkeuEPTx2v0y5fbAqiLvCOm1Sj@dpg-cvdr13l2ng1s73c9ogc0-a.oregon-postgres.render.com/salesdb_po5a")

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
TRACKING_DIR = "tracking"
DOMAIN = "https://email-sales-backend.onrender.com"  # Replace with your domain
os.makedirs(TRACKING_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Google AI
genai.configure(api_key="AIzaSyDgbb32htJ_IheA2E_ZDAR3CMTe4NJwn3I")

# genai.configure(api_key="AIzaSyCYrU9tHR2hnnNriW4vyqHsW8zWzwBQ9Zw")

# Global storage for campaign results
campaign_results = {}

# Create tracking pixel if it doesn't exist
def create_tracking_pixel():
    pixel_path = os.path.join(TRACKING_DIR, "pixel.png")
    if not os.path.exists(pixel_path):
        # Create a 1x1 transparent PNG
        img = Image.fromarray(np.zeros((1, 1, 4), dtype=np.uint8))
        img.save(pixel_path, 'PNG')
    return pixel_path

PIXEL_PATH = create_tracking_pixel()

# --- Database Setup ---
DATABASE_FILE = "campaign_data.db"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def initialize_database():
    """Create database tables if they don't exist."""
    conn=get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id UUID PRIMARY KEY,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            total_processed INTEGER DEFAULT 0,
            successful_sends INTEGER DEFAULT 0,
            failed_sends INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id SERIAL PRIMARY KEY,
            campaign_id UUID UNIQUE REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            open_rate REAL DEFAULT 0,
            bounce_rate REAL DEFAULT 0,
            reply_rate REAL DEFAULT 0,
            unsubscribe_rate REAL DEFAULT 0,
            total_opens INTEGER DEFAULT 0,
            total_bounces INTEGER DEFAULT 0,
            total_replies INTEGER DEFAULT 0,
            total_unsubscribes INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracking (
            id SERIAL PRIMARY KEY,
            campaign_id UUID REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            sent_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            opens INTEGER DEFAULT 0,
            first_opened TIMESTAMP NULL,
            last_opened TIMESTAMP NULL,
            bounced BOOLEAN DEFAULT FALSE,
            replied BOOLEAN DEFAULT FALSE,
            unsubscribed BOOLEAN DEFAULT FALSE,
            UNIQUE (campaign_id, email) -- Ensures each email per campaign is unique
        )
    """)

    conn.commit()
    conn.close()

def insert_campaign(campaign_id, start_time):
    """Insert a new campaign into the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("INSERT INTO campaigns (campaign_id, start_time) VALUES (%s, %s)", 
                       (campaign_id, start_time))
        conn.commit()
    except Exception as e:
        print(f"Error inserting campaign: {e}")
    finally:
        cursor.close()
        conn.close()

def update_campaign_stats(campaign_id, end_time, successful_sends, failed_sends, total_processed):
    """Update campaign statistics in PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE campaigns
            SET end_time = %s, successful_sends = %s, failed_sends = %s, total_processed = %s
            WHERE campaign_id = %s
        """, (end_time, successful_sends, failed_sends, total_processed, campaign_id))
        
        conn.commit()
    except Exception as e:
        print(f"Error updating campaign stats: {e}")
    finally:
        cursor.close()
        conn.close()

def update_campaign_metrics(campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate, total_opens, total_bounces, total_replies, total_unsubscribes):
    """Update campaign metrics in PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        print("Executing update for campaign metrics")

        cursor.execute("""
            INSERT INTO metrics (campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate, total_opens, total_bounces, total_replies, total_unsubscribes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_id) 
            DO UPDATE SET
                open_rate = EXCLUDED.open_rate,
                bounce_rate = EXCLUDED.bounce_rate,
                reply_rate = EXCLUDED.reply_rate,
                unsubscribe_rate = EXCLUDED.unsubscribe_rate,
                total_opens = EXCLUDED.total_opens,
                total_bounces = EXCLUDED.total_bounces,
                total_replies = EXCLUDED.total_replies,
                total_unsubscribes = EXCLUDED.total_unsubscribes
        """, (campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate, total_opens, total_bounces, total_replies, total_unsubscribes))

        print("Metrics updated successfully")
        conn.commit()
    except Exception as e:
        print(f"Error updating campaign metrics: {e}")
    finally:
        cursor.close()
        conn.close()

def get_campaign_metrics_from_db(campaign_id):
    """Get campaign metrics from the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Fetch campaign metrics
        cursor.execute("SELECT * FROM metrics WHERE campaign_id = %s", (campaign_id,))
        metrics_row = cursor.fetchone()

        # Fetch tracking data
        cursor.execute("SELECT * FROM tracking WHERE campaign_id = %s", (campaign_id,))
        tracking_rows = cursor.fetchall()

        # Fetch campaign statistics
        cursor.execute("""
            SELECT total_processed, successful_sends, failed_sends 
            FROM campaigns WHERE campaign_id = %s
        """, (campaign_id,))
        campaign_row = cursor.fetchone()

        # Process metrics data
        if metrics_row:
            metrics = {
                "open_rate": metrics_row[2],
                "bounce_rate": metrics_row[3],
                "reply_rate": metrics_row[4],
                "unsubscribe_rate": metrics_row[5],
                "total_opens": metrics_row[6],
                "total_bounces": metrics_row[7],
                "total_replies": metrics_row[8],
                "total_unsubscribes": metrics_row[9],
            }
        else:
            metrics = {
                "open_rate": 0,
                "bounce_rate": 0,
                "reply_rate": 0,
                "unsubscribe_rate": 0,
                "total_opens": 0,
                "total_bounces": 0,
                "total_replies": 0,
                "total_unsubscribes": 0,
            }

        # Process tracking data
        tracking = [
            {
                "email": row[2],
                "sent_time": row[3],
                "opens": row[4],
                "first_opened": row[5],
                "last_opened": row[6],
                "bounced": row[7],
                "replied": row[8],
                "unsubscribed": row[9],
            }
            for row in tracking_rows
        ] if tracking_rows else []

        # Process campaign statistics
        if campaign_row:
            total_processed, successful_sends, failed_sends = campaign_row
        else:
            total_processed, successful_sends, failed_sends = 0, 0, 0

        return metrics, tracking, total_processed, successful_sends, failed_sends

    except Exception as e:
        print(f"Error fetching campaign metrics: {e}")
        return {}, [], 0, 0, 0

    finally:
        cursor.close()
        conn.close()

def track_send_in_db(campaign_id, email, sent_time):
    """Track when an email is sent in the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO tracking (campaign_id, email, sent_time) VALUES (%s, %s, %s)",
            (campaign_id, email, sent_time)
        )
        conn.commit()
    except Exception as e:
        print(f"Error tracking email send: {e}")
    finally:
        cursor.close()
        conn.close()

def track_open_in_db(campaign_id, email, now):
    """Track when an email is opened in the PostgreSQL database."""
    print(campaign_id, email, "get it 143")
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE tracking
            SET opens = opens + 1, 
                first_opened = CASE WHEN first_opened IS NULL THEN %s ELSE first_opened END, 
                last_opened = %s
            WHERE campaign_id = %s AND email = %s
            """,
            (now, now, campaign_id, email),
        )
        conn.commit()
    except Exception as e:
        print(f"Error tracking email open: {e}")
    finally:
        cursor.close()
        conn.close()

def track_bounce_in_db(campaign_id, email):
    """Track bounced emails in the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE tracking SET bounced = TRUE WHERE campaign_id = %s AND email = %s",
            (campaign_id, email)
        )
        conn.commit()
    except Exception as e:
        print(f"Error tracking email bounce: {e}")
    finally:
        cursor.close()
        conn.close()

def track_reply_in_db(campaign_id, email):
    """Track email replies in the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE tracking SET replied = TRUE WHERE campaign_id = %s AND email = %s",
            (campaign_id, email)
        )
        conn.commit()
    except Exception as e:
        print(f"Error tracking email reply: {e}")
    finally:
        cursor.close()
        conn.close()

def track_unsubscribe_in_db(campaign_id, email):
    """Track unsubscribes in the PostgreSQL database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE tracking SET unsubscribed = TRUE WHERE campaign_id = %s AND email = %s",
            (campaign_id, email)
        )
        conn.commit()
    except Exception as e:
        print(f"Error tracking email unsubscribe: {e}")
    finally:
        cursor.close()
        conn.close()

class EmailTracker:
    def __init__(self):
        self.tracking_data = {}
        initialize_database()
        
    def initialize_campaign(self, campaign_id):
        """Initialize tracking for a new campaign"""
        start_time = datetime.now(timezone.utc).isoformat()
        insert_campaign(campaign_id,start_time)
    
    def track_send(self, campaign_id, email):
        """Track when an email is sent"""
        sent_time = datetime.now(timezone.utc).isoformat()
        track_send_in_db(campaign_id, email, sent_time)
        
    def track_open(self, campaign_id, email):
        """Track when an email is opened"""
        now = datetime.now(timezone.utc).isoformat()
        print('121')
        track_open_in_db(campaign_id, email, now)
        print('122')
        self._update_metrics(campaign_id)
        print('123')

    def track_bounce(self, campaign_id, email):
        """Track bounced emails"""
        track_bounce_in_db(campaign_id, email)
        self._update_metrics(campaign_id)
            
    def track_reply(self, campaign_id, email):
        """Track email replies"""
        track_reply_in_db(campaign_id, email)
        self._update_metrics(campaign_id)
            
    def track_unsubscribe(self, campaign_id, email):
        """Track unsubscribes"""
        track_unsubscribe_in_db(campaign_id, email)
        self._update_metrics(campaign_id)

    def _update_metrics(self, campaign_id):
        """Update campaign metrics"""
        print("Updating campaign metrics...")
        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            print("Fetching total emails sent...")
            cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = %s", (campaign_id,))
            total_sent = cursor.fetchone()[0]
            print(f"Total sent: {total_sent}")

            print("Fetching total opens...")
            cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = %s AND opens > 0", (campaign_id,))
            total_opens = cursor.fetchone()[0]
            print(f"Total opens: {total_opens}")

            print("Fetching total bounces...")
            cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = %s AND bounced = TRUE", (campaign_id,))
            total_bounces = cursor.fetchone()[0]
            print(f"Total bounces: {total_bounces}")

            print("Fetching total replies...")
            cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = %s AND replied = TRUE", (campaign_id,))
            total_replies = cursor.fetchone()[0]
            print(f"Total replies: {total_replies}")

            print("Fetching total unsubscribes...")
            cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = %s AND unsubscribed = TRUE", (campaign_id,))
            total_unsubscribes = cursor.fetchone()[0]
            print(f"Total unsubscribes: {total_unsubscribes}")

            conn.close()

            if total_sent > 0:
                open_rate = (total_opens / total_sent) * 100
                bounce_rate = (total_bounces / total_sent) * 100
                reply_rate = (total_replies / total_sent) * 100
                unsubscribe_rate = (total_unsubscribes / total_sent) * 100
            else:
                open_rate = 0
                bounce_rate = 0
                reply_rate = 0
                unsubscribe_rate = 0

            print("Updating campaign metrics in database...")
            update_campaign_metrics(campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate, total_opens, total_bounces, total_replies, total_unsubscribes)
            print("Campaign metrics updated successfully.")

        except Exception as e:
            print(f"Error updating campaign metrics: {e}")
        finally:
            cursor.close()
            conn.close()

email_tracker = EmailTracker()

class AIColdEmailAgent:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.sender_email = SENDER_EMAIL
        self.sender_password = SENDER_PASSWORD
        self.imap_server = "imap.gmail.com"

    def generate_email_content(self, industry, name):
        """Generate personalized email content using AI with rate limiting"""
        
        prompt = f"""Generate ONLY a JSON object for a cold email. No placeholders or dynamic fields should remain. The email should be fully written with complete sentences.

        CONTEXT:
        - Industry: {industry}
        - Recipient Name: {name}
        - Sender: Oliva, an AI assistant working with Nitin Katke, the co-founder of OLV Technologies Pvt Ltd.
        - Purpose: Offering software services
        - Style: Professional, friendly, and engaging with emojis.
        - Recent Event in Industry
        - Specific Challenges in Industry
        - Focus: Highlight the value proposition of software services.
        - ROI Hint: Mention that software services can lead to increased efficiency, reduced costs, and improved productivity.
        - Emojis: Include relevant emojis to make the email engaging
        - Mandatory Link: Check ROI manually here - https://ROI.olvtechnologies.com/
        - Calendly Link: Book a 15/30 min meeting - https://cal.com/nitinkatke

        EMAIL BODY STRUCTURE:
        - Paragraph 1 (Greeting): Start with a friendly greeting to {name}. Include a relevant industry emoji.
        - Paragraph 2 (Introduction):I am Oliva, an AI assistant working with Nitin Katke, the co-founder of Orange League Ventures Pvt Ltd.
        - Paragraph 3 (Relevance): Briefly mention recent_event related to their industry and transition into how their industry may be experiencing specific_challenges.
        - Paragraph 4 (Value Proposition): Explain how our software services can help businesses in the {industry} industry. Highlight value propositions, such as improved efficiency, streamlined operations, cost savings, or better customer engagement. Be specific about the benefits.
        - Paragraph 5 (ROI Hint): Briefly mention that adopting new software solutions can often result in a positive ROI, due to increased efficiency, reduced costs, and improved productivity.
        - paragraph 6 : "Our founders are alums of Premier Institutes such as IIT, NMIMS and NUS, Singapore. Our team of seasoned professionals from prestigious institutions like NITs and top tech colleges has helped clients across US, Europe, Canada, Australia, Hong Kong and Singapore" add this in proffesional way where it represnts our profile strong.
        - Paragraph 7 (Call to Action): Invite them to book a 15/30-minute call via the calendly link to discuss their needs.
        - Paragraph 8 (Closing): End with a professional closing.

        IMPORTANT:
        - Hey {name}, Subject line with an emoji 🎉,
        - Introduction like this "I am Oliva, an AI assistant working with Nitin Katke, the co-founder of OLV Technologies Pvt Ltd."
        - Ensure all content is complete and fully written.
        - Always include the Calendly link and ROI link in the body.
        - Always make the content as dynamic as possible.
        - Emojis: Include relevant emojis to make the email engaging
        - Always provide complete sentences. Avoid using incomplete phrases like '[mention...]' or '[insert...]' and this is mandatory.Make sure this is very important to follow.
        - Only include content related to the software industry.
        - DO NOT INCLUDE TEXT OUTSIDE THE JSON.

        JSON FORMAT:
        {{
            "subject": "Hey {name}, Subject line with an emoji 🎉",
            "body": "Email body here\\n\\nBest regards,\\nNitin Katke\\nco-founder,OLV Technologies Pvt Ltd"
        }}
        """

        model = genai.GenerativeModel("gemini-1.5-pro-001")
        
        retries = 5  # Maximum retries
        delay = 5  # Initial delay in seconds

        for attempt in range(retries):
            try:
                response = model.generate_content(prompt)
                content = response.text
                
                # Clean and parse the response
                content = content.replace("```json", "").replace("```", "").strip()
                json_data = json.loads(content)
                
                result = {
                    "subject": json_data["subject"].strip(),
                    "body": json_data["body"].replace("\\n", "\n").strip()
                }
                
                # Validate the result
                if not result["subject"] or not result["body"]:
                    raise ValueError("Empty subject or body")
                    
                logger.info(f"Generated content for {name} in {industry}")
                return result

            except Exception as e:
                logger.error(f"Content generation error: {str(e)}")
                
                # if "Resource has been exhausted" in str(e):
                print('entered...')
                if "429" in str(e) or "quota" in str(e).lower():
                    print('entered...1')
                    if attempt < retries - 1:
                        print('entered...11')
                        logger.warning(f"Quota limit reached, retrying in {delay} seconds...")
                        time.sleep(delay)  # Wait before retrying
                        delay *= 2  # Exponential backoff (5s, 10s, 20s, 40s...)
                    else:
                        print('entered...12')
                        logger.error("Max retries reached. Returning fallback email template.")
                        return {
                            "subject": f"Software Services for {industry} Companies",
                            "body": f"Dear {name},\n\nI hope this email finds you well. I wanted to reach out regarding our software services...\n\nBest regards,\nNitin\nOrange League Ventures pvt ltd"
                        }
                else:
                    print('entered...2')
                    return {
                        "subject": f"Software Services for {industry} Companies",
                        "body": f"Dear {name},\n\nI hope this email finds you well. I wanted to reach out regarding our software services...\n\nBest regards,\nNitin\nOrange League Ventures pvt ltd"
                    }
    def send_email(self, to_email, subject, body, campaign_id):
        """Send email with tracking"""
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.sender_email
            msg['To'] = to_email
            msg['Subject'] = subject
            msg['Message-ID'] = f"<{campaign_id}-{to_email}>"
            
            email_b64 = base64.urlsafe_b64encode(to_email.encode()).decode()
            tracking_url = f"{DOMAIN}/track/{campaign_id}/{email_b64}"
            print(tracking_url,'tracking_urlsdsdds')
            tracking_pixel = f'<img src="{tracking_url}" width="1" height="1" alt="" style="display:none !important"/>'
            print(tracking_pixel,'tracking_pixelsdfsdf')
            
            # Create unsubscribe link
            unsubscribe_link = f'{DOMAIN}/unsubscribe/{campaign_id}/{base64.urlsafe_b64encode(to_email.encode()).decode()}'
            
            # Plain text version
            text_part = MIMEText(body, 'plain')
            
            formatted_body = body.replace('\n', '<br>')

            # Then use the pre-formatted body in the f-string
            html_content = f"""
            <div>{formatted_body}</div>
            <br>
            <div><a href="{unsubscribe_link}">Unsubscribe</a></div>
            <div>{tracking_pixel}</div>
            """
            html_part = MIMEText(html_content, 'html')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
                
            # Track successful send
            email_tracker.track_send(campaign_id, to_email)
            return True
            
        except smtplib.SMTPRecipientsRefused:
            email_tracker.track_bounce(campaign_id, to_email)
            return False
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    def check_replies(self, campaign_id):
        """Check for email replies"""
        try:
            with imaplib.IMAP4_SSL(self.imap_server) as imap:
                imap.login(self.sender_email, self.sender_password)
                imap.select('INBOX')
                
                # Search for recent emails
                since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
                _, messages = imap.search(None, 'SINCE', since_date)

                processed_emails = set()  # Keep track of processed emails

                for num in messages[0].split():
                    if num in processed_emails:
                        continue

                    _, msg_data = imap.fetch(num, '(RFC822)')
                    email_body = msg_data[0][1]
                    msg = email.message_from_bytes(email_body)
                    
                    # Check if it's a reply to our campaign
                    references = msg.get('References', '') + msg.get('In-Reply-To', '')
                    if references:
                        for ref in references.split():
                            if campaign_id in ref:
                                reply_from = email.utils.parseaddr(msg['From'])[1]
                                email_tracker.track_reply(campaign_id, reply_from)
                                break
                    processed_emails.add(num)

        except Exception as e:
            logger.error(f"Error checking replies: {str(e)}")

# @app.post("/send-emails/")
# async def send_emails(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
#     """Handle Excel upload and send emails"""
#     try:
#         # Generate unique campaign ID
#         campaign_id = str(uuid.uuid4())
        
#         # Read Excel file
#         file_contents = await file.read()
#         df = pd.read_excel(BytesIO(file_contents))

#         if df.empty:
#             return {
#                 "status": "success",
#                 "campaign_id": campaign_id,
#                 "message": "Excel sheet is empty. No emails sent.",
#                 "summary": {
#                     "total_processed": 0,
#                     "successful_sends": 0,
#                     "failed_sends": 0
#                 }
#             }
#         # Detect type of emails (individuals or company emails)
#         if "Emails" in df.columns:
#             email_column = "Emails"   # Individuals
#             name_column = "Name"
#         elif "Contact Email" in df.columns:
#             email_column = "Contact Email"  # Companies
#             name_column = "Name"  # Company name
#         else:
#             return {"status": "error", "message": "No valid email column found."}
        
#         # Initialize campaign tracking
#         email_tracker.initialize_campaign(campaign_id)
        
#         # Initialize email agent
#         email_agent = AIColdEmailAgent()
        
#         successful_sends = 0
#         failed_sends = 0
        
#         # Process each row in the Excel file
#         for _, row in df.iterrows():
#             try:
#                 # Generate personalized email content
#                 email_content = email_agent.generate_email_content(
#                     industry=row.get("Industry", "Unknown"),
#                     name=row[name_column]  # Person or Company Name
#                 )
#                 if "Hey {name}" in email_content["subject"]:
#                     email_content["subject"] = email_content["subject"].replace("{name}",row["Name"])
                
#                 # Send email with tracking
#                 success = email_agent.send_email(
#                     to_email=row[email_column],
#                     subject=email_content["subject"],
#                     body=email_content["body"],
#                     campaign_id=campaign_id
#                 )
                
#                 if success:
#                     successful_sends += 1
#                     logger.info(f"Successfully sent email to {row[email_column]}")
#                 else:
#                     failed_sends += 1
#                     logger.error(f"Failed to send email to {row[email_column]}")
                
#                 # Add delay to avoid spam detection
#                 time.sleep(2)
                
#             except Exception as e:
#                 failed_sends += 1
#                 logger.error(f"Error processing row for {row.get(email_column, 'unknown')}: {str(e)}")
#                 continue
        
#         # Update campaign statistics
#         update_campaign_stats(
#             campaign_id=campaign_id,
#             end_time=datetime.now(timezone.utc).isoformat(),
#             successful_sends=successful_sends,
#             failed_sends=failed_sends,
#             total_processed=len(df)
#         )
        
#         # Schedule background tasks
#         background_tasks.add_task(email_agent.check_replies, campaign_id)
        
#         return {
#             "status": "success",
#             "campaign_id": campaign_id,
#             "message": "Email campaign started",
#             "summary": {
#                 "total_processed": len(df),
#                 "successful_sends": successful_sends,
#                 "failed_sends": failed_sends
#             }
#         }
        
#     except Exception as e:
#         logger.error(f"Campaign error: {str(e)}")
#         return {
#             "status": "error",
#             "error": str(e),
#             "message": "Failed to process email campaign"
#         }

@app.post("/send-emails/")
async def send_emails(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    """Handle Excel upload and send emails in batches"""
    try:
        # Generate unique campaign ID
        campaign_id = str(uuid.uuid4())
        
        # Read Excel file
        file_contents = await file.read()
        df = pd.read_excel(BytesIO(file_contents))

        if df.empty:
            return {
                "status": "success",
                "campaign_id": campaign_id,
                "message": "Excel sheet is empty. No emails sent.",
                "summary": {
                    "total_processed": 0,
                    "successful_sends": 0,
                    "failed_sends": 0
                }
            }
        
        # Detect type of emails (individuals or company emails)
        if "Emails" in df.columns:
            email_column = "Emails"   # Individuals
            name_column = "Name"
        elif "Contact Email" in df.columns:
            email_column = "Contact Email"  # Companies
            name_column = "Name"  # Company name
        else:
            return {"status": "error", "message": "No valid email column found."}
        
        # Initialize campaign tracking
        email_tracker.initialize_campaign(campaign_id)
        
        # Initialize email agent
        email_agent = AIColdEmailAgent()
        
        successful_sends = 0
        failed_sends = 0
        
        # Process emails in batches of 3
        for batch_start in range(0, len(df), 3):
            batch = df.iloc[batch_start:batch_start + 3]
            
            # Process current batch
            for _, row in batch.iterrows():
                try:
                    # Generate personalized email content
                    email_content = email_agent.generate_email_content(
                        industry=row.get("Industry", "Unknown"),
                        name=row[name_column]  # Person or Company Name
                    )
                    
                    if "Hey {name}" in email_content["subject"]:
                        email_content["subject"] = email_content["subject"].replace("{name}", row["Name"])
                    
                    # Send email with tracking
                    success = email_agent.send_email(
                        to_email=row[email_column],
                        subject=email_content["subject"],
                        body=email_content["body"],
                        campaign_id=campaign_id
                    )
                    
                    if success:
                        successful_sends += 1
                        logger.info(f"Successfully sent email to {row[email_column]}")
                    else:
                        failed_sends += 1
                        logger.error(f"Failed to send email to {row[email_column]}")
                    
                    # Add delay between emails in a batch
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    failed_sends += 1
                    logger.error(f"Error processing row for {row.get(email_column, 'unknown')}: {str(e)}")
                    continue
            
            # Add longer delay between batches
            await asyncio.sleep(30)
        
        # Update campaign statistics
        update_campaign_stats(
            campaign_id=campaign_id,
            end_time=datetime.now(timezone.utc).isoformat(),
            successful_sends=successful_sends,
            failed_sends=failed_sends,
            total_processed=len(df)
        )
        
        # Schedule background tasks
        background_tasks.add_task(email_agent.check_replies, campaign_id)
        
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "message": "Email campaign started",
            "summary": {
                "total_processed": len(df),
                "successful_sends": successful_sends,
                "failed_sends": failed_sends
            }
        }
        
    except Exception as e:
        logger.error(f"Campaign error: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "message": "Failed to process email campaign"
        }
    
@app.get("/track/{campaign_id}/{email_b64}")
async def track_open(campaign_id: str, email_b64: str):
    """Track email opens"""
    try:
        email = base64.urlsafe_b64decode(email_b64.encode()).decode()
        logger.info(f"Tracking open for campaign {campaign_id}, email {email}")
        print('entered1111')
        email_tracker.track_open(campaign_id, email)
        print('entered222')
        # Return tracking pixel with proper headers
        return FileResponse(
            PIXEL_PATH,
            media_type="image/png",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    except Exception as e:
        logger.error(f"Tracking error: {str(e)}")
        return {"error": "Tracking failed"}

@app.get("/unsubscribe/{campaign_id}/{email_b64}")
async def unsubscribe(campaign_id: str, email_b64: str):
    """Handle unsubscribe requests"""
    try:
        email = base64.urlsafe_b64decode(email_b64.encode()).decode()
        email_tracker.track_unsubscribe(campaign_id, email)
        return HTMLResponse("<h1>You have been successfully unsubscribed.</h1>")
    except Exception as e:
        logger.error(f"Unsubscribe error: {str(e)}")
        return {"error": "Unsubscribe failed"}

@app.get("/campaign-metrics/{campaign_id}")
async def get_campaign_metrics(campaign_id: str):
    """Get campaign metrics from database"""
    try:
        metrics, tracking, total_processed, successful_sends, failed_sends = get_campaign_metrics_from_db(campaign_id)
        
        if not metrics and not tracking and not total_processed and not successful_sends and not failed_sends:
            return {"error": "Campaign not found"}
        
        return {
            "campaign_id": campaign_id,
            "total_processed": total_processed,
            "successful_sends": successful_sends,
            "failed_sends": failed_sends,
            "metrics": {
                "open_rate": round(metrics.get('open_rate', 0), 2),
                "bounce_rate": round(metrics.get('bounce_rate', 0), 2),
                "reply_rate": round(metrics.get('reply_rate', 0), 2),
                "unsubscribe_rate": round(metrics.get('unsubscribe_rate', 0), 2),
                "total_opens": metrics.get('total_opens',0),
                "total_bounces": metrics.get('total_bounces',0),
                "total_replies": metrics.get('total_replies',0),
                "total_unsubscribes": metrics.get('total_unsubscribes',0)
            },
            "detailed_tracking": tracking
        }

    except Exception as e:
        logger.error(f"Campaign metrics error: {str(e)}")
        return {"error": "Failed to retrieve campaign metrics"}

def get_all_campaigns_from_db(page: int, page_size: int):
    """Get paginated campaigns from PostgreSQL."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        offset = (page - 1) * page_size

        # Get total count of campaigns
        cursor.execute("SELECT COUNT(*) FROM campaigns")
        total_count = cursor.fetchone()[0]

        # Get paginated campaign details
        cursor.execute(
            """
            SELECT campaign_id, start_time, end_time, total_processed, successful_sends, failed_sends 
            FROM campaigns
            ORDER BY start_time DESC
            LIMIT %s OFFSET %s
            """,
            (page_size, offset),
        )
        campaigns_rows = cursor.fetchall()

        campaigns = []
        for row in campaigns_rows:
            campaign_id = row[0]

            # Fetch campaign metrics
            cursor.execute("SELECT * FROM metrics WHERE campaign_id = %s", (campaign_id,))
            metrics_row = cursor.fetchone()

            metrics = {
                "open_rate": metrics_row[2] if metrics_row else 0,
                "bounce_rate": metrics_row[3] if metrics_row else 0,
                "reply_rate": metrics_row[4] if metrics_row else 0,
                "unsubscribe_rate": metrics_row[5] if metrics_row else 0,
                "total_opens": metrics_row[6] if metrics_row else 0,
                "total_bounces": metrics_row[7] if metrics_row else 0,
                "total_replies": metrics_row[8] if metrics_row else 0,
                "total_unsubscribes": metrics_row[9] if metrics_row else 0,
            }

            campaigns.append(
                {
                    "campaign_id": campaign_id,
                    "start_time": row[1],
                    "end_time": row[2],
                    "total_processed": row[3],
                    "successful_sends": row[4],
                    "failed_sends": row[5],
                    "metrics": metrics,
                }
            )

        return campaigns, total_count

    except Exception as e:
        print(f"Error fetching campaigns: {e}")
        return [], 0

    finally:
        cursor.close()
        conn.close()


@app.get("/all-campaigns/")
async def get_all_campaigns(page: int = 1, page_size: int = 10):
    """API endpoint to get paginated campaign details."""
    try:
        all_campaigns_data, total_count = get_all_campaigns_from_db(page, page_size)

        return {
            "total_campaigns": total_count,
            "total_pages": (total_count + page_size - 1) // page_size,  # Total pages
            "current_page": page,
            "page_size": page_size,
            "campaigns": all_campaigns_data
        }
    except Exception as e:
        logger.error(f"Error fetching all campaigns: {str(e)}")
        return {"error": "Failed to retrieve campaign details."}
    
def get_roi_data():
    """Get ROI data from the external website."""
    url = "https://orangeleague.github.io/IT-services-ROI-calculator/"
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        initial_investment_element = soup.find(id="initial-investment")
        annual_return_element = soup.find(id="annual-return")
        roi_element = soup.find(id="roi")

        initial_investment = initial_investment_element.text if initial_investment_element else "Data not available"
        annual_return = annual_return_element.text if annual_return_element else "Data not available"
        roi = roi_element.text if roi_element else "Data not available"

        return {
            "initial_investment": initial_investment,
            "annual_return": annual_return,
            "roi": roi
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching or parsing ROI data: {e}")
        return {
            "initial_investment": "Error fetching data",
            "annual_return": "Error fetching data",
            "roi": "Error fetching data",
        }

@app.get("/campaign-details/{campaign_id}")
async def get_campaign_details(
    campaign_id: str = None,
    include_opened: bool = True,
    include_bounced: bool = True,
    include_replied: bool = True,
    include_unsubscribed: bool = True,
    include_all: bool = True,
    include_summary: bool = True
):
    """Get comprehensive campaign details with options to filter what data to include"""
    try:
        # If no campaign_id is provided, return summary of all campaigns
        if not campaign_id or campaign_id.lower() == "all":
            campaigns = get_all_campaigns_from_db()
            
            campaign_summaries = []
            for campaign in campaigns:
                campaign_summaries.append({
                    "campaign_id": campaign["campaign_id"],
                    "start_time": campaign["start_time"],
                    "end_time": campaign["end_time"],
                    "total_processed": campaign["total_processed"],
                    "successful_sends": campaign["successful_sends"],
                    "failed_sends": campaign["failed_sends"],
                    "open_rate": round(campaign["metrics"].get("open_rate", 0), 2),
                    "bounce_rate": round(campaign["metrics"].get("bounce_rate", 0), 2),
                    "reply_rate": round(campaign["metrics"].get("reply_rate", 0), 2),
                    "unsubscribe_rate": round(campaign["metrics"].get("unsubscribe_rate", 0), 2),
                    "total_opens": campaign["metrics"].get("total_opens", 0),
                    "total_bounces": campaign["metrics"].get("total_bounces", 0),
                    "total_replies": campaign["metrics"].get("total_replies", 0),
                    "total_unsubscribes": campaign["metrics"].get("total_unsubscribes", 0)
                })
                
            return {
                "total_campaigns": len(campaign_summaries),
                "campaigns": campaign_summaries
            }
        
        # Get details for a specific campaign
        metrics, tracking, total_processed, successful_sends, failed_sends = get_campaign_metrics_from_db(campaign_id)
        
        if not metrics and not tracking and total_processed == 0 and successful_sends == 0 and failed_sends == 0:
            return {"error": "Campaign not found"}
        
        # Initialize result dictionary
        result = {
            "campaign_id": campaign_id
        }
        
        # Add summary if requested
        if include_summary:
            result["summary"] = {
                "total_processed": total_processed,
                "successful_sends": successful_sends,
                "failed_sends": failed_sends,
                "open_rate": round(metrics.get('open_rate', 0), 2),
                "bounce_rate": round(metrics.get('bounce_rate', 0), 2),
                "reply_rate": round(metrics.get('reply_rate', 0), 2),
                "unsubscribe_rate": round(metrics.get('unsubscribe_rate', 0), 2),
                "total_opens": metrics.get('total_opens', 0),
                "total_bounces": metrics.get('total_bounces', 0),
                "total_replies": metrics.get('total_replies', 0),
                "total_unsubscribes": metrics.get('total_unsubscribes', 0)
            }
        
        # Add detailed tracking data based on requested filters
        if include_opened:
            opened_emails = [t for t in tracking if t.get('opens', 0) > 0]
            result["opened_emails"] = {
                "total": len(opened_emails),
                "emails": opened_emails
            }
            
        if include_bounced:
            bounced_emails = [t for t in tracking if t.get('bounced', False)]
            result["bounced_emails"] = {
                "total": len(bounced_emails),
                "emails": bounced_emails
            }
            
        if include_replied:
            replied_emails = [t for t in tracking if t.get('replied', False)]
            result["replied_emails"] = {
                "total": len(replied_emails),
                "emails": replied_emails
            }
            
        if include_unsubscribed:
            unsubscribed_emails = [t for t in tracking if t.get('unsubscribed', False)]
            result["unsubscribed_emails"] = {
                "total": len(unsubscribed_emails),
                "emails": unsubscribed_emails
            }
            
        if include_all:
            result["all_emails"] = tracking
        
        return result
        
    except Exception as e:
        logger.error(f"Error retrieving campaign details: {str(e)}")
        return {"error": f"Failed to retrieve campaign details: {str(e)}"}

roi_data = get_roi_data()
print(roi_data)
