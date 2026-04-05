"""Gmail IMAP/SMTP skill for fetching and sending emails."""
import json
import os


def _load_providers() -> dict:
    try:
        with open(os.path.join(os.getcwd(), "providers.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run_gmail(action: str, params: dict) -> str:
    """E-Mails abrufen oder senden via Gmail IMAP/SMTP.

    Args:
        action: 'fetch' oder 'send'
        params: {
            'subject': ...,
            'to': ...,
            'body': ...,
            'max_results': 10  # für fetch
        }
    """
    try:
        import imaplib
        import email
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
    except ImportError:
        return "❌ IMAP-Bibliothek nicht verfügbar"

    providers = _load_providers()
    gm = providers.get("gmail", {})

    email_addr = gm.get("email", "")
    app_password = gm.get("app_password", "")

    if not email_addr or not app_password:
        return "❌ Gmail nicht konfiguriert. Bitte E-Mail und App-Password in den Provider-Einstellungen eintragen."

    if action == "send":
        subject = params.get("subject", "Nachricht von AgentClaw")
        to = params.get("to", "")
        body = params.get("body", "")

        if not to:
            return "❌ Kein Empfänger angegeben (to)"

        try:
            import smtplib

            msg = MIMEMultipart()
            msg["From"] = email_addr
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(email_addr, app_password)
            server.send_message(msg)
            server.quit()

            return f"✅ E-Mail gesendet an {to}: {subject}"
        except Exception as e:
            return f"❌ SMTP-Fehler: {str(e)[:100]}"

    elif action == "fetch":
        max_results = params.get("max_results", 10)

        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(email_addr, app_password)
            mail.select("inbox")

            _, data = mail.search(None, "ALL")
            email_ids = data[0].split()[-max_results:][::-1]

            results = []
            for eid in email_ids:
                _, data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                subject = msg.get("subject", "(Kein Betreff)")
                from_addr = msg.get("from", "Unbekannt")
                date = msg.get("date", "")
                results.append(f"Von: {from_addr}\nBetreff: {subject}\nDatum: {date}")

            mail.close()
            mail.logout()

            if not results:
                return "📭 Keine E-Mails gefunden"

            return "📧 Letzte E-Mails:\n\n" + "\n\n".join(results[:5])

        except Exception as e:
            return f"❌ IMAP-Fehler: {str(e)[:100]}"

    return "❌ Unbekannte Aktion"
