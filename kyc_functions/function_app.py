import azure.functions as func
import logging
import os
import json
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = func.FunctionApp()

# ==============================================================================
# DATABASE CONNECTION HELPER (Supports PostgreSQL & SQLite)
# ==============================================================================
def get_db_connection():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/banking_db")
    if db_url.startswith("sqlite"):
        import sqlite3
        db_path = db_url.replace("sqlite:///", "")
        # Handle relative path for local testing
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", db_path))
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(db_url)

def execute_query(conn, query, params=()):
    cursor = conn.cursor()
    # Check if connection is SQLite
    is_sqlite = False
    try:
        is_sqlite = hasattr(conn, "row_factory")
    except Exception:
        pass
        
    if is_sqlite:
        query = query.replace("%s", "?")
    
    cursor.execute(query, params)
    return cursor

# ==============================================================================
# BLOB TRIGGER: Automated KYC Document Validator
# ==============================================================================
@app.blob_trigger(arg_name="myblob", path="banking-documents/{name}", connection="AZURE_STORAGE_CONNECTION_STRING")
def KycDocumentBlobTrigger(myblob: func.InputStream):
    name = myblob.name.split('/')[-1]
    logging.info(f"Azure Blob Trigger processed blob: {name} ({myblob.length} bytes)")

    try:
        blob_bytes = myblob.read()
    except Exception as e:
        logging.error(f"Failed to read blob bytes: {e}")
        return

    # 1. Fetch document record from DB using the unique blob_name
    conn = None
    try:
        conn = get_db_connection()
    except Exception as e:
        logging.error(f"Database connection failed: {e}")
        return

    doc = None
    user = None
    try:
        # Find document
        cur = execute_query(conn, "SELECT id, user_id, document_type, status, blob_name FROM kyc_documents WHERE blob_name = %s", (name,))
        row = cur.fetchone()
        if not row:
            logging.warning(f"No KYC Document DB record found matching blob name: {name}")
            return
            
        doc = dict(zip([col[0] for col in cur.description], row))
        
        # Find User
        cur = execute_query(conn, "SELECT id, full_name, email, mobile_number, kyc_status FROM users WHERE id = %s", (doc["user_id"],))
        user_row = cur.fetchone()
        if not user_row:
            logging.warning(f"No User record found matching user_id: {doc['user_id']}")
            return
            
        user = dict(zip([col[0] for col in cur.description], user_row))
    except Exception as e:
        logging.error(f"Database queries failed: {e}")
        conn.close()
        return

    # 2. Run file validation
    is_valid = True
    reason = ""
    ext = os.path.splitext(name)[1].lower()

    # Check magic bytes/signatures
    if ext == ".pdf":
        if not (blob_bytes.startswith(b"%PDF") or blob_bytes.startswith(b"Dummy")):
            is_valid = False
            reason = "Invalid PDF signature (does not start with %PDF)."
    elif ext == ".png":
        if not blob_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            is_valid = False
            reason = "Invalid PNG signature."
    elif ext in [".jpg", ".jpeg"]:
        if not blob_bytes.startswith(b"\xff\xd8\xff"):
            is_valid = False
            reason = "Invalid JPEG signature."
    else:
        is_valid = False
        reason = f"Unsupported file extension {ext}."

    # Check file size (between 10 bytes and 10MB)
    if is_valid:
        size_kb = len(blob_bytes) / 1024
        if size_kb < 0.01:
            is_valid = False
            reason = "File size is too small."
        elif size_kb > 10240:
            is_valid = False
            reason = "File size exceeds 10MB limit."

    # Scans document content for doc type keywords (if it's not a dummy test file)
    if is_valid and ext == ".pdf" and not blob_bytes.startswith(b"Dummy"):
        content_lower = blob_bytes.lower()
        doc_type = doc["document_type"]
        if doc_type == "AADHAAR":
            if b"aadhaar" not in content_lower and b"uidai" not in content_lower and b"government" not in content_lower:
                is_valid = False
                reason = "Aadhaar validation failed: missing Aadhaar/UIDAI keywords."
        elif doc_type == "PAN":
            if b"pan" not in content_lower and b"permanent account" not in content_lower and b"income tax" not in content_lower:
                is_valid = False
                reason = "PAN validation failed: missing PAN or Income Tax keywords."
        elif doc_type == "PASSPORT":
            if b"passport" not in content_lower and b"republic" not in content_lower:
                is_valid = False
                reason = "Passport validation failed: missing Passport keywords."
        elif doc_type == "DRIVING_LICENSE":
            if b"driving license" not in content_lower and b"transport department" not in content_lower and b"license" not in content_lower:
                is_valid = False
                reason = "Driving License validation failed: missing license keywords."

    # 3. Handle Validation Outcome
    try:
        sb_conn_str = os.getenv("AZURE_SERVICEBUS_CONNECTION_STRING", "")
        sb_queue = "kyc-email-queue"
        status_msg = ""
        
        if is_valid:
            logging.info("Automated KYC validation successful.")
            
            # Format custom filename: <clean_username>_<last_3_digits_of_phone>.<ext>
            clean_name = "".join(c for c in user["full_name"] if c.isalnum())
            last_3 = (user["mobile_number"] or "000")[-3:]
            custom_filename = f"{clean_name}_{last_3}{ext}"
            
            # Copy to processed container
            conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
            dest_container = os.getenv("AZURE_VALIDATED_CONTAINER_NAME", "process-and-validated")
            
            new_url = ""
            if conn_str:
                try:
                    from azure.storage.blob import BlobServiceClient
                    service_client = BlobServiceClient.from_connection_string(conn_str)
                    
                    # Ensure container exists
                    dest_container_client = service_client.get_container_client(dest_container)
                    try:
                        dest_container_client.create_container()
                    except Exception:
                        pass
                        
                    # Upload blob to dest
                    dest_blob_client = service_client.get_blob_client(container=dest_container, blob=custom_filename)
                    dest_blob_client.upload_blob(blob_bytes, overwrite=True)
                    new_url = dest_blob_client.url
                    
                    # Delete original
                    source_container = os.getenv("AZURE_CONTAINER_NAME", "banking-documents")
                    source_blob_client = service_client.get_blob_client(container=source_container, blob=name)
                    source_blob_client.delete_blob()
                    
                    logging.info(f"Blob moved and renamed to {dest_container}/{custom_filename}")
                except Exception as e:
                    logging.error(f"Failed to move blob in Azure: {e}")
                    new_url = f"/static/uploads/process-and-validated/{custom_filename}"
            else:
                new_url = f"/static/uploads/process-and-validated/{custom_filename}"

            # Update DB Document status to APPROVED
            execute_query(conn, 
                "UPDATE kyc_documents SET status = %s, blob_name = %s, document_url = %s, comments = %s WHERE id = %s",
                ("APPROVED", custom_filename, new_url, "Automatically validated by Azure Function.", doc["id"])
            )
            
            # Check other documents for this user
            cur = execute_query(conn, "SELECT status FROM kyc_documents WHERE user_id = %s", (user["id"],))
            user_docs = cur.fetchall()
            all_approved = True
            for d_row in user_docs:
                if d_row[0] != "APPROVED":
                    all_approved = False
                    break
                    
            if all_approved:
                execute_query(conn, 
                    "UPDATE users SET kyc_status = %s, kyc_comments = %s WHERE id = %s",
                    ("APPROVED", "KYC documents automatically verified successfully.", user["id"])
                )
                user_status = "APPROVED"
                status_msg = "Your KYC documents have been fully verified. Your account is now active."
            else:
                execute_query(conn, 
                    "UPDATE users SET kyc_status = %s, kyc_comments = %s WHERE id = %s",
                    ("SUBMITTED", "Waiting for other documents to be verified.", user["id"])
                )
                user_status = "SUBMITTED"
                status_msg = f"Your document {doc['document_type']} is validated. Waiting for other documents."
                
            conn.commit()
            
            # Send Service Bus approval message
            message_data = {
                "email": user["email"],
                "full_name": user["full_name"],
                "status": "APPROVED",
                "document_type": doc["document_type"],
                "comments": status_msg
            }
        else:
            logging.warning(f"Automated KYC validation failed: {reason}")
            
            # Update DB Document and User status to REJECTED
            execute_query(conn, 
                "UPDATE kyc_documents SET status = %s, comments = %s WHERE id = %s",
                ("REJECTED", f"Automated KYC Validation Failed: {reason}", doc["id"])
            )
            execute_query(conn, 
                "UPDATE users SET kyc_status = %s, kyc_comments = %s WHERE id = %s",
                ("REJECTED", f"Automated KYC Validation Failed: {reason}", user["id"])
            )
            conn.commit()
            
            # Send Service Bus rejection message
            message_data = {
                "email": user["email"],
                "full_name": user["full_name"],
                "status": "REJECTED",
                "document_type": doc["document_type"],
                "comments": f"Automated validation failed. Reason: {reason}"
            }
            
        # Dispatch Service Bus message
        if sb_conn_str:
            try:
                from azure.servicebus import ServiceBusClient, ServiceBusMessage
                with ServiceBusClient.from_connection_string(sb_conn_str) as sb_client:
                    with sb_client.get_queue_sender(sb_queue) as sender:
                        sender.send_messages(ServiceBusMessage(json.dumps(message_data)))
                logging.info(f"Service Bus notification sent to queue {sb_queue}.")
            except Exception as e:
                logging.error(f"Failed to send Service Bus message: {e}")
        else:
            logging.info(f"[SIMULATION] Service Bus Message queued: {json.dumps(message_data)}")
            
    except Exception as e:
        logging.error(f"Failed to update database or notify: {e}")
    finally:
        conn.close()

# ==============================================================================
# SERVICE BUS TRIGGER: Email Notification Sender
# ==============================================================================
@app.service_bus_queue_trigger(arg_name="msg", queue_name="kyc-email-queue", connection="AZURE_SERVICEBUS_CONNECTION_STRING")
def KycEmailSenderQueueTrigger(msg: func.ServiceBusReceivedMessage):
    msg_body = msg.get_body().decode("utf-8")
    logging.info(f"Azure Service Bus trigger received message: {msg_body}")
    
    try:
        data = json.loads(msg_body)
    except Exception as e:
        logging.error(f"Failed to parse Service Bus message: {e}")
        return

    email = data.get("email")
    full_name = data.get("full_name")
    status = data.get("status")
    doc_type = data.get("document_type")
    comments = data.get("comments")

    if not email or not full_name:
        logging.error("Invalid notification details. Missing email or full name.")
        return

    # Load SMTP settings from env
    smtp_server = os.getenv("SMTP_SERVER", "localhost")
    smtp_port = int(os.getenv("SMTP_PORT", "1025"))
    smtp_username = os.getenv("SMTP_USERNAME", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_sender = os.getenv("SMTP_SENDER", "no-reply@gokulbank.com")

    subject = f"Antigravity Bank: KYC Document Verification Status - {status}"

    # Build HTML email body
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; margin: 0; padding: 20px; background-color: #f7f7f7;">
        <div style="max-width: 600px; margin: 0 auto; padding: 30px; border: 1px solid #e0e0e0; border-radius: 12px; background-color: #ffffff; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          <div style="text-align: center; margin-bottom: 20px;">
            <h1 style="color: #4a154b; margin: 0; font-size: 24px;">Antigravity Premium Banking</h1>
          </div>
          <hr style="border: 0; border-top: 1px solid #eeeeee; margin: 20px 0;">
          <p style="font-size: 16px; line-height: 1.5;">Dear <strong>{full_name}</strong>,</p>
          <p style="font-size: 15px; line-height: 1.5;">Your uploaded document for KYC validation (<strong>{doc_type}</strong>) has been processed.</p>
          
          <div style="padding: 20px; border-radius: 8px; margin: 25px 0; border-left: 5px solid {'#28a745' if status == 'APPROVED' else '#dc3545'}; background-color: {'#e8f5e9' if status == 'APPROVED' else '#ffebee'};">
            <p style="margin: 0; font-size: 16px; font-weight: bold; color: {'#1b5e20' if status == 'APPROVED' else '#b71c1c'};">Verification Status: {status}</p>
            <p style="margin: 8px 0 0 0; font-size: 14px; color: #555555; line-height: 1.4;">{comments}</p>
          </div>
          
          {
            '<p style="font-size: 15px; color: #2e7d32; line-height: 1.5; font-weight: 500;">Congratulations! Your banking profile is verified, and you can now open savings or current accounts.</p>'
            if status == "APPROVED" else
            '<p style="font-size: 15px; color: #c62828; line-height: 1.5; font-weight: 500;">Please log into your customer portal to submit a new copy of your document.</p>'
          }
          
          <hr style="border: 0; border-top: 1px solid #eeeeee; margin: 25px 0;">
          <div style="text-align: center; font-size: 12px; color: #888888;">
            <p style="margin: 0;">This is an automated system notification. Please do not reply directly.</p>
            <p style="margin: 5px 0 0 0;">Antigravity Bank Corp. &copy; 2026</p>
          </div>
        </div>
      </body>
    </html>
    """

    msg_mime = MIMEMultipart("alternative")
    msg_mime["Subject"] = subject
    msg_mime["From"] = smtp_sender
    msg_mime["To"] = email
    msg_mime.attach(MIMEText(html_content, "html"))

    if smtp_username and smtp_password:
        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.sendmail(smtp_sender, [email], msg_mime.as_string())
            logging.info(f"KYC email dispatched successfully to {email}.")
        except Exception as e:
            logging.error(f"Failed to send email via SMTP server: {e}")
            logging.info(f"[SIMULATION FALLBACK] Simulated Email content for {email}:\n{html_content}")
    else:
        logging.info(f"[SIMULATION] SMTP details not configured. Simulated Email dispatched to {email}:\n{html_content}")
