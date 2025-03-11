import pandas as pd
import smtplib
import json
import time
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
DOMAIN = "https://9725-2409-40f0-5058-147-fc22-8a70-29d7-2c94.ngrok-free.app"  # Replace with your domain
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

# def initialize_database():
#     """Create database tables if they don't exist."""
#     conn = sqlite3.connect(DATABASE_FILE)
#     cursor = conn.cursor()

#     cursor.execute("""
#         CREATE TABLE IF NOT EXISTS campaigns (
#             campaign_id TEXT PRIMARY KEY,
#             start_time TEXT,
#             end_time TEXT,
#             total_processed INTEGER DEFAULT 0,
#             successful_sends INTEGER DEFAULT 0,
#             failed_sends INTEGER DEFAULT 0
#         )
#     """)

#     cursor.execute("""
#         CREATE TABLE IF NOT EXISTS metrics (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             campaign_id TEXT PRIMARY KEY,
#             open_rate REAL,
#             bounce_rate REAL,
#             reply_rate REAL,
#             unsubscribe_rate REAL,
#             total_opens INTEGER DEFAULT 0,
#             total_bounces INTEGER DEFAULT 0,
#             total_replies INTEGER DEFAULT 0,
#             total_unsubscribes INTEGER DEFAULT 0,
#             FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
#         )
#     """)
    
#     cursor.execute("""
#         CREATE TABLE IF NOT EXISTS tracking (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             campaign_id TEXT,
#             email TEXT,
#             sent_time TEXT,
#             opens INTEGER DEFAULT 0,
#             first_opened TEXT,
#             last_opened TEXT,
#             bounced BOOLEAN DEFAULT FALSE,
#             replied BOOLEAN DEFAULT FALSE,
#             unsubscribed BOOLEAN DEFAULT FALSE,
#             FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
#         )
#     """)

#     conn.commit()
#     conn.close()
def initialize_database():
    """Create database tables if they don't exist."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            campaign_id TEXT PRIMARY KEY,
            start_time TEXT,
            end_time TEXT,
            total_processed INTEGER DEFAULT 0,
            successful_sends INTEGER DEFAULT 0,
            failed_sends INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT UNIQUE,
            open_rate REAL,
            bounce_rate REAL,
            reply_rate REAL,
            unsubscribe_rate REAL,
            total_opens INTEGER DEFAULT 0,
            total_bounces INTEGER DEFAULT 0,
            total_replies INTEGER DEFAULT 0,
            total_unsubscribes INTEGER DEFAULT 0,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT,
            email TEXT,
            sent_time TEXT,
            opens INTEGER DEFAULT 0,
            first_opened TEXT,
            last_opened TEXT,
            bounced BOOLEAN DEFAULT FALSE,
            replied BOOLEAN DEFAULT FALSE,
            unsubscribed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id),
            UNIQUE (campaign_id, email) ON CONFLICT REPLACE  -- Ensures each (campaign_id, email) is unique
        )
    """)
    # cursor.execute("DROP TABLE IF EXISTS metrics")

    conn.commit()
    conn.close()


def insert_campaign(campaign_id, start_time):
    """Insert a new campaign into the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO campaigns (campaign_id, start_time) VALUES (?, ?)",
                   (campaign_id, start_time))
    conn.commit()
    conn.close()

def update_campaign_stats(campaign_id, end_time, successful_sends, failed_sends, total_processed):
    """Update campaign statistics."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE campaigns
        SET end_time = ?, successful_sends = ?, failed_sends = ?, total_processed = ?
        WHERE campaign_id = ?
    """, (end_time, successful_sends, failed_sends, total_processed, campaign_id))
    conn.commit()
    conn.close()

def update_campaign_metrics(campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate,total_opens,total_bounces,total_replies,total_unsubscribes):
    """Update campaign metrics in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    print('got it 14')
    cursor.execute("""
        INSERT INTO metrics (campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate,total_opens,total_bounces,total_replies,total_unsubscribes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET
            open_rate = excluded.open_rate,
            bounce_rate = excluded.bounce_rate,
            reply_rate = excluded.reply_rate,
            unsubscribe_rate = excluded.unsubscribe_rate,
            total_opens = excluded.total_opens,
            total_bounces = excluded.total_bounces,
            total_replies = excluded.total_replies,
            total_unsubscribes = excluded.total_unsubscribes
    """, (campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate,total_opens,total_bounces,total_replies,total_unsubscribes))
    print('got it 15')
    conn.commit()
    conn.close()

def get_campaign_metrics_from_db(campaign_id):
    """Get campaign metrics from the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM metrics WHERE campaign_id = ?", (campaign_id,))
    metrics_row = cursor.fetchone()

    cursor.execute("SELECT * FROM tracking WHERE campaign_id = ?", (campaign_id,))
    tracking_rows = cursor.fetchall()

    cursor.execute(
        "SELECT total_processed, successful_sends, failed_sends FROM campaigns WHERE campaign_id = ?",
        (campaign_id,),
    )
    campaign_row = cursor.fetchone()
    conn.close()

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
        metrics = {}

    if tracking_rows:
        tracking = []
        for row in tracking_rows:
            tracking.append(
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
            )
    else:
        tracking = []

    if campaign_row:
        total_processed = campaign_row[0]
        successful_sends = campaign_row[1]
        failed_sends = campaign_row[2]
    else:
        total_processed = 0
        successful_sends = 0
        failed_sends = 0

    return metrics, tracking, total_processed, successful_sends, failed_sends

def track_send_in_db(campaign_id, email, sent_time):
    """Track when an email is sent in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tracking (campaign_id, email, sent_time) VALUES (?, ?, ?)",
        (campaign_id, email, sent_time)
    )
    conn.commit()
    conn.close()

def track_open_in_db(campaign_id, email, now):
    print(campaign_id,email,'get it 143')
    """Track when an email is opened in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE tracking
        SET opens = opens + 1, first_opened = CASE WHEN first_opened IS NULL THEN ? ELSE first_opened END, last_opened = ?
        WHERE campaign_id = ? AND email = ?
        """,
        (now, now, campaign_id, email),
    )
    conn.commit()
    conn.close()

def track_bounce_in_db(campaign_id, email):
    """Track bounced emails in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tracking SET bounced = TRUE WHERE campaign_id = ? AND email = ?",
        (campaign_id, email)
    )
    conn.commit()
    conn.close()

def track_reply_in_db(campaign_id, email):
    """Track email replies in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tracking SET replied = TRUE WHERE campaign_id = ? AND email = ?",
        (campaign_id, email)
    )
    conn.commit()
    conn.close()

def track_unsubscribe_in_db(campaign_id, email):
    """Track unsubscribes in the database."""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE tracking SET unsubscribed = TRUE WHERE campaign_id = ? AND email = ?",
        (campaign_id, email)
    )
    conn.commit()
    conn.close()

class EmailTracker:
    def __init__(self):
        self.tracking_data = {}
        initialize_database()
        
    def initialize_campaign(self, campaign_id):
        """Initialize tracking for a new campaign"""
        start_time = datetime.now(timezone.utc).isoformat()
        # campaign_results[campaign_id] = {
        #     'start_time': datetime.now(timezone.utc).isoformat(),
        #     'tracking': {},
        #     'total_sent': 0,
        #     'metrics': {
        #         'opens': set(),
        #         'bounces': set(),
        #         'replies': set(),
        #         'unsubscribes': set()
        #     }
        # }
        insert_campaign(campaign_id,start_time)
    
    def track_send(self, campaign_id, email):
        """Track when an email is sent"""
        # if campaign_id not in campaign_results:
        #     self.initialize_campaign(campaign_id)
            
        # if email not in campaign_results[campaign_id]['tracking']:
        #     campaign_results[campaign_id]['tracking'][email] = {
        #         'sent_time': datetime.now(timezone.utc).isoformat(),
        #         'opens': 0,
        #         'first_opened': None,
        #         'last_opened': None,
        #         'bounced': False,
        #         'replied': False,
        #         'unsubscribed': False
        #     }
        # campaign_results[campaign_id]['total_sent'] += 1

        sent_time = datetime.now(timezone.utc).isoformat()
        track_send_in_db(campaign_id, email, sent_time)
        
    def track_open(self, campaign_id, email):
        """Track when an email is opened"""
        # if campaign_id in campaign_results:
        #     now = datetime.now(timezone.utc).isoformat()
        #     if email in campaign_results[campaign_id]['tracking']:
        #         tracking = campaign_results[campaign_id]['tracking'][email]
                
        #         tracking['opens'] += 1
        #         if not tracking['first_opened']:
        #             tracking['first_opened'] = now
                
        #         tracking['last_opened'] = now
        #         # tracking['opens'] += 1
        #         campaign_results[campaign_id]['metrics']['opens'].add(email)

        #         total_sent = campaign_results[campaign_id]['total_sent']
        #         if total_sent > 0:
        #             open_rate = (len(campaign_results[campaign_id]['metrics']['opens']) / total_sent) * 100
        #             campaign_results[campaign_id]['metrics']['open_rate'] = open_rate
                
        #         logger.info(f"Updated tracking for {email}: opens={tracking['opens']}, open_rate={open_rate}%")
        #         self._update_metrics(campaign_id)

        now = datetime.now(timezone.utc).isoformat()
        print('121')
        track_open_in_db(campaign_id, email, now)
        print('122')
        self._update_metrics(campaign_id)
        print('123')

    def track_bounce(self, campaign_id, email):
        """Track bounced emails"""
        # if campaign_id in campaign_results:
        #     campaign_results[campaign_id]['metrics']['bounces'].add(email)
        #     if email in campaign_results[campaign_id]['tracking']:
        #         campaign_results[campaign_id]['tracking'][email]['bounced'] = True
        #     self._update_metrics(campaign_id)
        track_bounce_in_db(campaign_id, email)
        self._update_metrics(campaign_id)
            
    def track_reply(self, campaign_id, email):
        """Track email replies"""
        # if campaign_id in campaign_results:
        #     campaign_results[campaign_id]['metrics']['replies'].add(email)
        #     if email in campaign_results[campaign_id]['tracking']:
        #         campaign_results[campaign_id]['tracking'][email]['replied'] = True
        #     self._update_metrics(campaign_id)
        track_reply_in_db(campaign_id, email)
        self._update_metrics(campaign_id)
            
    def track_unsubscribe(self, campaign_id, email):
        """Track unsubscribes"""
        # if campaign_id in campaign_results:
        #     campaign_results[campaign_id]['metrics']['unsubscribes'].add(email)
        #     if email in campaign_results[campaign_id]['tracking']:
        #         campaign_results[campaign_id]['tracking'][email]['unsubscribed'] = True
        #     self._update_metrics(campaign_id)
        track_unsubscribe_in_db(campaign_id, email)
        self._update_metrics(campaign_id)
            
    def _update_metrics(self, campaign_id):
        """Update campaign metrics"""
        # if campaign_id in campaign_results:
        #     campaign = campaign_results[campaign_id]
        #     total_sent = campaign['total_sent']
            
        #     if total_sent > 0:
        #         metrics = {
        #             'open_rate': (len(campaign['metrics']['opens']) / total_sent) * 100,
        #             'bounce_rate': (len(campaign['metrics']['bounces']) / total_sent) * 100,
        #             'reply_rate': (len(campaign['metrics']['replies']) / total_sent) * 100,
        #             'unsubscribe_rate': (len(campaign['metrics']['unsubscribes']) / total_sent) * 100
        #         }
        #         campaign['metrics'].update(metrics)
        print('got it 0')
        conn = sqlite3.connect(DATABASE_FILE)
        print('got it 1')
        cursor = conn.cursor()
        print('got it 2')
        cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = ?", (campaign_id,))
        print('got it 3')
        total_sent = cursor.fetchone()[0]
        print('got it 4',total_sent)
        cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = ? AND opens > 0", (campaign_id,))
        print('got it 5')
        total_opens = cursor.fetchone()[0]
        print('got it 6',total_opens)
        cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = ? AND bounced = TRUE", (campaign_id,))
        print('got it 6')
        total_bounces = cursor.fetchone()[0]
        print('got it 7',total_bounces)
        cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = ? AND replied = TRUE", (campaign_id,))
        print('got it 8')
        total_replies = cursor.fetchone()[0]
        print('got it 9',total_replies)
        cursor.execute("SELECT COUNT(*) FROM tracking WHERE campaign_id = ? AND unsubscribed = TRUE", (campaign_id,))
        print('got it 10')
        total_unsubscribes = cursor.fetchone()[0]
        print('got it 11',total_unsubscribes)
        conn.close()

        if total_sent >0:
            open_rate = (total_opens / total_sent) * 100
            bounce_rate = (total_bounces / total_sent) * 100
            reply_rate = (total_replies / total_sent) * 100
            unsubscribe_rate = (total_unsubscribes / total_sent) * 100
        else:
            open_rate = 0
            bounce_rate = 0
            reply_rate = 0
            unsubscribe_rate = 0
        print('got it 12')
        update_campaign_metrics(campaign_id, open_rate, bounce_rate, reply_rate, unsubscribe_rate, total_opens, total_bounces, total_replies, total_unsubscribes)
        print('got it 13')

email_tracker = EmailTracker()

class AIColdEmailAgent:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.sender_email = SENDER_EMAIL
        self.sender_password = SENDER_PASSWORD
        self.imap_server = "imap.gmail.com"

    # def generate_email_content(self, industry, name):
    #     """Generate personalized email content using AI"""
    #     try:
    #         # prompt = f'''Generate ONLY a JSON object for a cold email. No other text.

    #         # CONTEXT:
    #         # - Industry: {industry}
    #         # - Recipient Name: {name}
    #         # - Sender: Nitin from Orange League Ventures pvt ltd
    #         # - Purpose: Offering software services
    #         # - Style: Professional with clear call to action

    #         # FORMAT:
    #         # {{
    #         #     "subject": "Subject line here",
    #         #     "body": "Email body here\\n\\nBest regards,\\nNitin\\nOrange League Ventures pvt ltd"
    #         # }}'''
            
    #         prompt = f'''Generate ONLY a JSON object for a cold email. No other text.

    #         CONTEXT:
    #         - Industry: {industry}
    #         - Recipient Name: {name}
    #         - Sender: Nitin from Orange League Ventures Pvt Ltd
    #         - Purpose: Offering software services
    #         - Style: Professional yet friendly with emojis
    #         - Framework: Short trigger-based outreach
    #         - Relevant Trigger: Personalization based on recent activity or news
    #         - Validation: Highlight a specific challenge or opportunity
    #         - Value Proposition: Explain how our services address the challenge
    #         - CTA: Invite to schedule a call or demo
    #         - Emojis: Include relevant emojis to make the email engaging
    #         - ROI Details: Include basic ROI calculations specific to the industry and also make changes on FORMAT...get the basic roi details it is enough and i want that give this in better repreaentation without table...after this if they want to to chekck manually about their roi,they can cehck here https://ROI.olvtechnologies.com/ ,add this to check manually and this is mandatory and dont forget about this
    #         FORMAT:
    #         -Please incluse my calendlylink https://calendly.com/nitinkatke,where they can book meetings with me and the duration is 15min and 30min..include this also in mail
    #         -send me the clear mail dont include any in-complete sentences like [...this]
    #         {{
    #             "subject": "Subject line with an emoji 🎉",
    #             "body": "Email body here\\n\\nBest regards,\\nNitin\\nOrange League Ventures pvt ltd"
    #         }}'''

    #         available_models = genai.list_models()
    #         # for model in available_models:
    #         #     print(model.name,'testingg')
    #         model = genai.GenerativeModel("gemini-1.5-pro-latest")
    #         response = model.generate_content(prompt)
    #         content = response.text
            
    #         # Clean and parse the response
    #         content = content.replace("```json", "").replace("```", "").strip()
    #         json_data = json.loads(content)
            
    #         result = {
    #             "subject": json_data["subject"].strip(),
    #             "body": json_data["body"].replace("\\n", "\n").strip()
    #         }
            
    #         # Validate the result
    #         if not result["subject"] or not result["body"]:
    #             raise ValueError("Empty subject or body")
                
    #         logger.info(f"Generated content for {name} in {industry}")
    #         return result

    #     except Exception as e:
    #         logger.error(f"Content generation error: {str(e)}")
    #         return {
    #             "subject": f"Software Services for {industry} Companies",
    #             "body": f"Dear {name},\n\nI hope this email finds you well. I wanted to reach out regarding our software services...\n\nBest regards,\nNitin\nOrange League Ventures pvt ltd"
    #         }

    def generate_email_content(self, industry, name):
        """Generate personalized email content using AI with rate limiting"""
        
        # prompt = f'''Generate ONLY a JSON object for a cold email. No other text.

        # CONTEXT:
        # - Industry: {industry}
        # - Recipient Name: {name}
        # - Sender: Oliva, an AI assistant working with Nitin Katke, the co-founder of Orange League Ventures Pvt Ltd.
        # - Purpose: Offering software services
        # - Style: Professional yet friendly with emojis
        # - Framework: Short trigger-based outreach
        # - Relevant Trigger: Personalization based on recent activity or news
        # - Validation: Highlight a specific challenge or opportunity
        # - Value Proposition: Explain how our services address the challenge
        # - CTA: Invite to schedule a call or demo
        # - Emojis: Include relevant emojis to make the email engaging
        # - ROI Details: Include basic ROI calculations specific to the industry
        # - Mandatory Link: Check ROI manually here - https://ROI.olvtechnologies.com/
        # - Calendly Link: Book a 15/30 min meeting - https://calendly.com/nitinkatke
        # - Don't include incomplete sentences like [mention some recent activity/news related to student engagement/challenges]...give complete sentenced mail and it is mandatory

        # {{
        #     "subject": "Subject line with an emoji 🎉",
        #     "body": "Email body here\\n\\nBest regards,\\nNitin\\nOrange League Ventures pvt ltd"
        # }}'''
        prompt = f"""Generate ONLY a JSON object for a cold email. No other text.

        CONTEXT:
        - Industry: {industry}
        - Recipient Name: {name}
        - Sender: Oliva, an AI assistant working with Nitin Katke, the co-founder of Orange League Ventures Pvt Ltd.
        - Purpose: Offering software services
        - Style: Professional, friendly, and engaging with emojis.
        - Recent Event in Industry
        - Specific Challenges in Industry
        - Focus: Highlight the value proposition of software services.
        - ROI Hint: Mention that software services can lead to increased efficiency, reduced costs, and improved productivity.
        - Emojis: Include relevant emojis to make the email engaging
        - Mandatory Link: Check ROI manually here - https://ROI.olvtechnologies.com/
        - Calendly Link: Book a 15/30 min meeting - https://calendly.com/nitinkatke

        EMAIL BODY STRUCTURE:
        - Paragraph 1 (Greeting): Start with a friendly greeting to {name}. Include a relevant industry emoji.
        - Paragraph 2 (Introduction):I am Oliva, an AI assistant working with Nitin Katke, the co-founder of Orange League Ventures Pvt Ltd.
        - Paragraph 3 (Relevance): Briefly mention recent_event related to their industry and transition into how their industry may be experiencing specific_challenges.
        - Paragraph 4 (Value Proposition): Explain how our software services can help businesses in the {industry} industry. Highlight value propositions, such as improved efficiency, streamlined operations, cost savings, or better customer engagement. Be specific about the benefits.
        - Paragraph 5 (ROI Hint): Briefly mention that adopting new software solutions can often result in a positive ROI, due to increased efficiency, reduced costs, and improved productivity.
        - Paragraph 6 (Call to Action): Invite them to book a 15/30-minute call via the calendly link to discuss their needs.
        - Paragraph 7 (Closing): End with a professional closing.

        IMPORTANT:
        - Hey {name}, Subject line with an emoji 🎉,
        - Introduction like this "I am Oliva, an AI assistant working with Nitin Katke, the co-founder of Orange League Ventures Pvt Ltd."
        - Ensure all content is complete and fully written.
        - Always include the Calendly link and ROI link in the body.
        - Always make the content as dynamic as possible.
        - Emojis: Include relevant emojis to make the email engaging
        - Always provide complete sentences. Avoid using incomplete phrases like '[mention...]' or '[insert...]'.
        - Only include content related to the software industry.
        - DO NOT INCLUDE TEXT OUTSIDE THE JSON.

        JSON FORMAT:
        {{
            "subject": "Hey {name}, Subject line with an emoji 🎉",
            "body": "Email body here\\n\\nBest regards,\\nNitin\\nOrange League Ventures pvt ltd"
        }}
        """

        model = genai.GenerativeModel("gemini-1.5-pro-latest")
        
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
                
                if "Resource has been exhausted" in str(e):
                    if attempt < retries - 1:
                        logger.warning(f"Quota limit reached, retrying in {delay} seconds...")
                        time.sleep(delay)  # Wait before retrying
                        delay *= 2  # Exponential backoff (5s, 10s, 20s, 40s...)
                    else:
                        logger.error("Max retries reached. Returning fallback email template.")
                        return {
                            "subject": f"Software Services for {industry} Companies",
                            "body": f"Dear {name},\n\nI hope this email finds you well. I wanted to reach out regarding our software services...\n\nBest regards,\nNitin\nOrange League Ventures pvt ltd"
                        }
                else:
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
            
            # Create tracking pixel
            # tracking_pixel = f'<img src="{DOMAIN}/track/{campaign_id}/{base64.urlsafe_b64encode(to_email.encode()).decode()}" width="1" height="1" />'
            email_b64 = base64.urlsafe_b64encode(to_email.encode()).decode()
            tracking_url = f"{DOMAIN}/track/{campaign_id}/{email_b64}"
            print(tracking_url,'tracking_urlsdsdds')
            tracking_pixel = f'<img src="{tracking_url}" width="1" height="1" alt="" style="display:none !important"/>'
            print(tracking_pixel,'tracking_pixelsdfsdf')
            
            # Create unsubscribe link
            unsubscribe_link = f'{DOMAIN}/unsubscribe/{campaign_id}/{base64.urlsafe_b64encode(to_email.encode()).decode()}'
            
            # Plain text version
            text_part = MIMEText(body, 'plain')
            
            # HTML version with tracking pixel and unsubscribe link
            # Instead of using .replace('\n', '<br>'), let's pre-format the body
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
            track_send_in_db(campaign_id, to_email, datetime.now(timezone.utc).isoformat())
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
#                     industry=row["Industry"],
#                     name=row["Name"]
#                 )
                
#                 # Send email with tracking
#                 success = email_agent.send_email(
#                     to_email=row["Emails"],
#                     subject=email_content["subject"],
#                     body=email_content["body"],
#                     campaign_id=campaign_id
#                 )
                
#                 if success:
#                     successful_sends += 1
#                     logger.info(f"Successfully sent email to {row['Emails']}")
#                 else:
#                     failed_sends += 1
#                     logger.error(f"Failed to send email to {row['Emails']}")
                
#                 # Add delay to avoid spam detection
#                 time.sleep(2)
                
#             except Exception as e:
#                 failed_sends += 1
#                 logger.error(f"Error processing row for {row.get('Emails', 'unknown')}: {str(e)}")
#                 continue
        
#         # Update campaign statistics
#         campaign_results[campaign_id].update({
#             'end_time': datetime.now(timezone.utc).isoformat(),
#             'successful_sends': successful_sends,
#             'failed_sends': failed_sends,
#             'total_processed': len(df)
#         })
        
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
    """Handle Excel upload and send emails"""
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
        
        # Initialize campaign tracking
        email_tracker.initialize_campaign(campaign_id)
        
        # Initialize email agent
        email_agent = AIColdEmailAgent()
        
        successful_sends = 0
        failed_sends = 0
        
        # Process each row in the Excel file
        for _, row in df.iterrows():
            try:
                # Generate personalized email content
                email_content = email_agent.generate_email_content(
                    industry=row["Industry"],
                    name=row["Name"]
                )
                if "Hey {name}" in email_content["subject"]:
                    email_content["subject"] = email_content["subject"].replace("{name}",row["Name"])
                
                # Send email with tracking
                success = email_agent.send_email(
                    to_email=row["Emails"],
                    subject=email_content["subject"],
                    body=email_content["body"],
                    campaign_id=campaign_id
                )
                
                if success:
                    successful_sends += 1
                    logger.info(f"Successfully sent email to {row['Emails']}")
                else:
                    failed_sends += 1
                    logger.error(f"Failed to send email to {row['Emails']}")
                
                # Add delay to avoid spam detection
                time.sleep(2)
                
            except Exception as e:
                failed_sends += 1
                logger.error(f"Error processing row for {row.get('Emails', 'unknown')}: {str(e)}")
                continue
        
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

# @app.get("/track/{campaign_id}/{email_b64}")
# async def track_open(campaign_id: str, email_b64: str):
#     """Track email opens"""
#     try:
#         email = base64.urlsafe_b64decode(email_b64.encode()).decode()
#         logger.info(f"Tracking open for campaign {campaign_id}, email {email}")
#         email_tracker.track_open(campaign_id, email)
#         # Return tracking pixel with proper headers
#         return FileResponse(
#             PIXEL_PATH,
#             media_type="image/png",
#             headers={
#                 "Cache-Control": "no-cache, no-store, must-revalidate",
#                 "Pragma": "no-cache",
#                 "Expires": "0"
#             }
#         )
#     except Exception as e:
#         logger.error(f"Tracking error: {str(e)}")
#         return {"error": "Tracking failed"}

# @app.get("/unsubscribe/{campaign_id}/{email_b64}")
# async def unsubscribe(campaign_id: str, email_b64: str):
#     """Handle unsubscribe requests"""
#     try:
#         email = base64.urlsafe_b64decode(email_b64.encode()).decode()
#         email_tracker.track_unsubscribe(campaign_id, email)
#         return HTMLResponse("<h1>You have been successfully unsubscribed.</h1>")
#     except Exception as e:
#         logger.error(f"Unsubscribe error: {str(e)}")
#         return {"error": "Unsubscribe failed"}

# @app.get("/campaign-metrics/{campaign_id}")
# async def get_campaign_metrics(campaign_id: str):
#     """Get campaign metrics"""
#     if campaign_id not in campaign_results:
#         return {"error": "Campaign not found"}
        
#     campaign = campaign_results[campaign_id]
#     metrics = campaign['metrics']
    
#     return {
#         "campaign_id": campaign_id,
#         "total_sent": campaign['total_sent'],
#         "metrics": {
#             "open_rate": round(metrics.get('open_rate', 0), 2),
#             "bounce_rate": round(metrics.get('bounce_rate', 0), 2),
#             "reply_rate": round(metrics.get('reply_rate', 0), 2),
#             "unsubscribe_rate": round(metrics.get('unsubscribe_rate', 0), 2),
#             "total_opens": len(metrics['opens']),
#             "total_bounces": len(metrics['bounces']),
#             "total_replies": len(metrics['replies']),
#             "total_unsubscribes": len(metrics['unsubscribes'])
#         },
#         "detailed_tracking": campaign['tracking']
#     }

# def get_roi_data():
#     url = "https://orangeleague.github.io/IT-services-ROI-calculator/"
#     response = requests.get(url)
#     print(response,'ressdfsdfsdf')
#     soup = BeautifulSoup(response.content, 'html.parser')

#     # Example: Find specific data points
#     print(soup,'soupsdfsdsdf')
#     initial_investment = soup.find(id="initial-investment")
#     annual_return = soup.find(id="annual-return")
#     roi = soup.find(id="roi")

#     return {
#         "initial_investment": initial_investment,
#         "annual_return": annual_return,
#         "roi": roi
#     }

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

roi_data = get_roi_data()
print(roi_data)
