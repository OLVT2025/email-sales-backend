from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
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

# Initialize FastAPI
app = FastAPI()

# Configure Gemini AI
genai.configure(api_key=OPENAI_API_KEY)

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# Set up logging
log_filename = f"logs/email_campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(filename=log_filename, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


class EmailRequest(BaseModel):
    industry: str
    email: str


class AIColdEmailAgent:
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.sender_email = SENDER_EMAIL
        self.sender_password = SENDER_PASSWORD
        openai.api_key = OPENAI_API_KEY

    def generate_email_content(self, industry):
        """Generates personalized email using AI."""
        try:
            prompt = f"""
            Create a personalized cold email for:
            - Business: {industry}
            
            Requirements:
            1. Professional and conversational tone.
            2. Offer software development services.
            3. Reference the industry and pain points.
            4. Include a clear call to action.
            5. Keep within 150-200 words.
            6. Return a valid JSON object with "subject" and "body" fields.
            7. Format the response as a JSON object wrapped in ```json``` markers.
            """

            # Sending request to Gemini AI
            model = genai.GenerativeModel("gemini-pro")
            response = model.generate_content(prompt)
            raw_text = response.candidates[0].content.parts[0].text

            # Extract JSON from response
            email_content = self.clean_and_extract_json(raw_text)
            if email_content:
                return email_content["subject"], email_content["body"]
            else:
                return "Default Subject", "Default email content."

        except Exception as e:
            logging.error(f"Error generating email content: {str(e)}")
            return "Default Subject", "Default email content."

    def clean_and_extract_json(self, text):
        """Extract and clean JSON content from AI response."""
        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

            # Extract subject and body
            subject_match = re.search(r'"subject":\s*"([^"]+)"', text)
            body_match = re.search(r'"body":\s*"(.*?)"(?=\s*}$)', text, re.DOTALL)

            if not subject_match or not body_match:
                raise ValueError("Could not extract subject or body from JSON")

            return {"subject": subject_match.group(1), "body": body_match.group(1)}

        except Exception as e:
            logging.error(f"JSON Cleaning Error: {str(e)}")
            return None

    def send_email(self, to_email, subject, body):
        """Sends email via SMTP."""
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


# Initialize AI Agent
email_agent = AIColdEmailAgent()

@app.post("/generate-email/")
async def generate_email(request: EmailRequest):
    """API to generate email content"""
    try:
        subject, body = email_agent.generate_email_content(request.industry)
        return {"subject": subject, "body": body}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send-email/")
async def send_email(request: EmailRequest):
    """API to send an email"""
    try:
        subject, body = email_agent.generate_email_content(request.industry)
        success = email_agent.send_email(request.email, subject, body)
        if success:
            return {"message": f"Email sent to {request.email}"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send email")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
