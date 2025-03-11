import pandas as pd
import smtplib
import json
import openai
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, OPENAI_API_KEY
import os
import google.generativeai as genai
import re
from io import BytesIO

genai.configure(api_key="AIzaSyDgbb32htJ_IheA2E_ZDAR3CMTe4NJwn3I")  # Replace with your actual API key

class AIColdEmailAgent:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.sender_email = SENDER_EMAIL
        self.sender_password = SENDER_PASSWORD
        openai.api_key = OPENAI_API_KEY

        # Ensure the 'logs' directory exists
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        
        # Set up logging
        log_filename = f"{log_dir}/email_campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            filename=log_filename,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def read_contacts(self, excel_path):
        """Reads contacts from Excel file."""
        try:
            df = pd.read_excel(excel_path)
            required_columns = ['Emails', 'Industry']
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

            # Extract JSON part from Markdown format
            match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if match:
                text = match.group(1).strip()
            
            print("🔍 Extracted JSON String Before Fixing:", text)

            # Parse the text to extract subject and body
            subject_match = re.search(r'"subject":\s*"([^"]+)"', text)
            body_match = re.search(r'"body":\s*"(.*?)"(?=\s*}$)', text, re.DOTALL)

            if not subject_match or not body_match:
                raise ValueError("Could not extract subject or body from JSON")

            # Extract the values
            subject = subject_match.group(1)
            body = body_match.group(1)

            # Clean the body text
            body = body.replace('\n', '\\n')  # Escape newlines
            body = body.replace('"', '\\"')   # Escape quotes
            body = body.replace('*', '\\*')   # Escape asterisks
            body = body.replace("'", "\\'")   # Escape single quotes

            # Construct clean JSON
            clean_json = {
                "subject": subject,
                "body": body
            }

            print("✅ Successfully Cleaned JSON:", clean_json)
            return clean_json

        except Exception as e:
            logging.error(f"❌ JSON Cleaning Error: {str(e)}")
            print(f"❌ JSON Cleaning Error: {str(e)}")
            return None

    def generate_email_content(self, row):
        """Generates personalized email using OpenAI."""
        try:
            prompt = f"""
            Create a personalized cold email for:
            - Business: {row['Industry']}
            
            Requirements:
            1. Professional and conversational tone.
            2. Offer software development services.
            3. Reference the industry and pain points.
            4. Include a clear call to action.
            5. Keep within 150-200 words.
            6. Return a valid JSON object with "subject" and "body" fields.
            7. Format the response as a JSON object wrapped in ```json``` markers.
            """
            
            print("🟢 Sending Prompt:", prompt)

            # Sending request to Gemini AI
            model = genai.GenerativeModel("gemini-pro")
            response = model.generate_content(prompt)
            raw_text = response.candidates[0].content.parts[0].text

            print("🟢 Raw Response:", raw_text)

            # Extract and clean JSON from response
            email_content = self.clean_and_extract_json(raw_text)
            
            if email_content:
                # Replace escaped newlines back with actual newlines for the email body
                body = email_content['body'].replace('\\n', '\n')
                return email_content['subject'], body
            else:
                print("⚠️ Failed to extract valid email content, returning defaults")
                return "Default Subject", "Default email content."

        except Exception as e:
            logging.error(f"Error generating email content: {str(e)}")
            return "Default Subject", "Default email content."
    def send_email(self, to_email, subject, body):
        """Sends email via SMTP."""
        try:
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
            
            logging.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            logging.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    def run_campaign(self, excel_path, delay_seconds=60, test_mode=False):
        """Runs the email campaign."""
        try:
            contacts_df = self.read_contacts(excel_path)
            
            for _, row in contacts_df.iterrows():
                subject, body = self.generate_email_content(row)
                print(f"{subject,body}testing")
                if test_mode:
                    print(f"\nEmail to {row['Industry']}:")
                    print(f"Subject: {subject}\nBody:\n{body}\n")
                else:
                    success = self.send_email(row['Emails'], subject, body)
                    if success:
                        logging.info(f"Sent to {row['Industry']}")
                    time.sleep(delay_seconds)
        except Exception as e:
            logging.error(f"Campaign error: {str(e)}")
            raise
