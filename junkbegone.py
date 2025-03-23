#!/usr/bin/env python3
import os
import pickle
import argparse
import openai
import json
import csv
from datetime import datetime
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Gmail API scope
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Target labels to create/use
TARGET_LABELS = ["Interactions", "Advertisements", "Notices", "Documents", "Appointments", "Et Cetera"]

# Gmail system labels to skip
SKIP_LABELS = ["Promotions", "Social", "Updates"]

# CSV log file
LOG_FILE = "classification_log.csv"

def get_gmail_service():
    """
    Authenticate with the Gmail API and return a service object.
    Uses token.pickle to store/reuse credentials.
    """
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)
    service = build("gmail", "v1", credentials=creds)
    return service

def get_existing_label(service, label_name):
    """
    Retrieve the label ID for a given label name without creating it.
    """
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])
    for label in labels:
        if label["name"].lower() == label_name.lower():
            return label["id"]
    return None

def create_label(service, label_name):
    """
    Create a label with the given name and return its ID.
    """
    label_body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show"
    }
    created_label = service.users().labels().create(userId="me", body=label_body).execute()
    return created_label["id"]

def initialize_target_labels(service):
    """
    Ensure that all target labels exist.
    Return a dictionary mapping label names (in lowercase) to their IDs.
    """
    label_mapping = {}
    for label in TARGET_LABELS:
        label_id = get_existing_label(service, label)
        if label_id is None:
            label_id = create_label(service, label)
        label_mapping[label.lower()] = label_id
    return label_mapping

def append_log(mail_id, mail_title, summary, category):
    """
    Append a CSV log entry for the classified email.
    The log includes Mail ID, Mail Title, Summary, Category, and Timestamp.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["Mail ID", "Mail Title", "Summary", "Category", "Timestamp"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "Mail ID": mail_id,
            "Mail Title": mail_title,
            "Summary": summary,
            "Category": category,
            "Timestamp": timestamp
        })

def classify_email_with_chatgpt(mail_title, email_content):
    """
    Use the latest OpenAI API with model "gpt-4o-mini" to classify the email.
    
    The LLM prompt instructs:
      - First, provide a very short summary (within 10 tokens) of the email.
      - Then, using the mail title, summary, and email content, classify the email into one of:
          Interactions, Advertisements, Notices, Documents, Appointments, or Et Cetera.
      - Rules:
          • Appointments: If a scheduled event is mentioned, label it as Appointments.
          • Documents: If the email shares files, links, or resources, label it as Documents (unless it’s an appointment).
          • Interactions: For actual conversations between people (excluding appointments), label it as Interactions.
          • Advertisements: For promotional material, newsletters, or subscriptions.
          • Notices: If the email is similar to Advertisements but is more about conveying information than promotion, label it as Notices.
          • Et Cetera: For emails that don’t clearly fall under any of the above.
      - Output the answer as a JSON object with keys "summary" and "category" (no extra text).
    """
    openai.api_key = ""
    prompt = (
        """
        You are an email categorization assistant. Follow these instructions carefully:

        Step 1: Provide a very short summary (within 10 tokens) of the email.

        Step 2: Classify the email into exactly ONE of these labels: Appointments, Documents, Interactions, Notices, Advertisements, or Et Cetera.

        Rules (in strict priority order):
        1. Appointments: Label as "Appointments" if the email mentions scheduling or an event date/time (this has absolute priority).
        2. Documents: Label as "Documents" if the email contains or refers to any attachments, files, documents, or external resources (unless it explicitly involves scheduling an event—then it must be "Appointments").
        3. Interactions: Label as "Interactions" only if the email is a conversation or direct interaction between people/entities (excluding scheduling).
        4. Notices: Label as "Notices" if the email primarily provides informational updates without direct conversation or personal interaction, but is not promotional.
        5. Advertisements: Label as "Advertisements" only if the email is promotional, a newsletter, subscription-based, or marketing material.
        6. Et Cetera: Label as "Et Cetera" only if the email clearly does NOT fit any of the above categories.

        Additional Instructions:
        - Follow the priority strictly. For example, emails with attachments must always be labeled as "Documents" unless explicitly about scheduling (then "Appointments").
        - If the email content is in Korean, double-check carefully. Do NOT guess; ensure accuracy in classification based on these rules.
        - Output your answer strictly as a JSON object with exactly two keys: "summary" and "category". Do not include any extra text.

        Here is the email to classify:\n
        """
        f"Mail Title: {mail_title}\n"
        f"Email Content:\n{email_content}"
        
    )
    messages = [
        {"role": "system", "content": "You are an email categorization assistant."},
        {"role": "user", "content": prompt}
    ]
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.0,
            max_tokens=100,
        )
        response_text = response.choices[0].message.content.strip()
        result = json.loads(response_text)
        if "summary" in result and "category" in result:
            category = result["category"].strip()
            # If the returned category is not one of our target labels, default to "Et Cetera".
            if category.lower() not in [lbl.lower() for lbl in TARGET_LABELS]:
                category = "Et Cetera"
            result["category"] = category
            return result
        else:
            print("LLM response did not contain required keys.")
            return None
    except Exception as e:
        print(f"Error during OpenAI API call: {e}")
        return None

def get_uncategorized_messages(service):
    """
    Retrieve messages that have no user-applied labels (has:nouserlabels)
    and skip messages with Gmail's system labels (Promotions, Social, Updates).
    Then sort messages by newest first.
    """
    query = "has:nouserlabels " + " ".join([f"-label:{lbl}" for lbl in SKIP_LABELS])
    results = service.users().messages().list(userId="me", q=query).execute()
    messages = results.get("messages", [])
    detailed_messages = []
    for msg in messages:
        m = service.users().messages().get(userId="me", id=msg["id"], format="metadata").execute()
        internal_date = int(m.get("internalDate", 0))
        detailed_messages.append({"id": msg["id"], "internalDate": internal_date})
    detailed_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    sorted_messages = [{"id": m["id"]} for m in detailed_messages]
    return sorted_messages

def process_message(service, msg_id, label_mapping):
    """
    For a given Gmail message ID, retrieve its content, classify it using OpenAI,
    apply the corresponding label, and log the classification.
    """
    message = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = message.get("payload", {})
    
    # Extract mail title (subject)
    headers = payload.get("headers", [])
    mail_title = ""
    for header in headers:
        if header.get("name", "").lower() == "subject":
            mail_title = header.get("value", "")
            break
    snippet = message.get("snippet", "")
    email_content = f"Subject: {mail_title}\nSnippet: {snippet}"
    
    result = classify_email_with_chatgpt(mail_title, email_content)
    if result is None:
        print(f"Could not classify message {msg_id}. Skipping.")
        return
    
    summary = result.get("summary", "").strip()
    category = result.get("category", "").strip()
    
    # Retrieve the label ID from our mapping (labels are ensured to exist).
    label_id = label_mapping.get(category.lower())
    if label_id is None:
        print(f"Label '{category}' not found. Skipping message {msg_id}.")
        return
    
    # If the message already has this label, skip it.
    current_labels = message.get("labelIds", [])
    if label_id in current_labels:
        print(f"Message {msg_id} already labeled as {category}. Skipping.")
        return
    
    # Apply the label to the message.
    msg_labels = {"addLabelIds": [label_id]}
    service.users().messages().modify(userId="me", id=msg_id, body=msg_labels).execute()
    print(f"Message {msg_id} categorized as {category}.")
    
    # Log the classification.
    append_log(msg_id, mail_title, summary, category)

def main():
    parser = argparse.ArgumentParser(
        description="Classify Gmail emails into Interactions, Advertisements, Notices, Documents, Appointments, or Et Cetera."
    )
    parser.add_argument(
        "--count", type=int, default=None,
        help="Number of emails to process in one execution. Default processes all eligible emails."
    )
    args = parser.parse_args()
    
    service = get_gmail_service()
    # Ensure all target labels exist.
    label_mapping = initialize_target_labels(service)
    messages = get_uncategorized_messages(service)
    
    if not messages:
        print("No eligible messages found.")
        return
    
    print(f"Found {len(messages)} eligible messages (newest first).")
    count = args.count if args.count is not None else len(messages)
    for msg in messages[:count]:
        process_message(service, msg["id"], label_mapping)

if __name__ == "__main__":
    main()
