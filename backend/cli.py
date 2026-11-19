"""Operations CLI.
  python -m backend.cli migrate
  python -m backend.cli create-admin --email you@company.com --name "Full Name"   (password prompted)
  python -m backend.cli outbox            # dev: print queued emails (verification/reset links)
  python -m backend.cli send-outbox       # send via SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM
"""
import argparse, getpass, os, smtplib, sys
from email.message import EmailMessage
from backend.core.config import get_settings
from backend.db.database import connect, migrate, utcnow
from backend.services import accounts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="backend.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    ca = sub.add_parser("create-admin"); ca.add_argument("--email", required=True); ca.add_argument("--name", required=True)
    sub.add_parser("outbox")
    sub.add_parser("send-outbox")
    b = sub.add_parser("backup"); b.add_argument("--out", default="backups"); b.add_argument("--keep", type=int, default=14)
    r = sub.add_parser("restore"); r.add_argument("path")
    sub.add_parser("ai-check")
    a = ap.parse_args(argv)
    s = get_settings()
    con = connect(s.db_path)
    applied = migrate(con)
    if a.cmd == "migrate":
        print("applied:", applied or "nothing (up to date)")
    elif a.cmd == "create-admin":
        pw = os.environ.get("SUNITE_ADMIN_PASSWORD") or getpass.getpass("Password: ")
        print(accounts.create_system_admin(con, email=a.email, full_name=a.name, password=pw))
    elif a.cmd == "outbox":
        for r in con.execute("SELECT to_email, subject, body, created_utc FROM email_outbox WHERE sent_utc IS NULL ORDER BY created_utc"):
            print(f"[{r['created_utc']}] To: {r['to_email']} | {r['subject']}\n  {r['body']}\n")
    elif a.cmd == "ai-check":  # pragma: no cover - needs network + key
        import time
        from backend.ai.providers import GeminiProvider, ProviderUnavailable, ProviderError
        try:
            p = GeminiProvider()
        except ProviderUnavailable as e:
            sys.exit(f"Not configured: {e}. Put GEMINI_API_KEY in the server environment (.env), never in code or the browser.")
        try:
            models = p.list_models()
            flash = [m for m in models if "flash" in m]
            print(f"Key OK. {len(models)} models support generateContent; flash models: {', '.join(flash[:12]) or '-'}")
            print(f"Configured GEMINI_MODEL = {p.model} -> {'available' if p.model in models else 'NOT in list (alias may still work)'}")
            t = time.time(); out = p.complete("Reply with the single word OK.", [{"role": "user", "content": "ping"}])
            print(f"Test call: {out.get('text')!r} in {time.time() - t:.1f}s, tokens {out.get('usage', {}).get('totalTokenCount')}")
        except ProviderError as e:
            sys.exit(str(e))
    elif a.cmd == "backup":
        from backend.ops import backup
        print(backup(s.db_path, a.out, a.keep))
    elif a.cmd == "restore":
        from backend.ops import restore
        con.close(); restore(a.path, s.db_path); print("restored from", a.path); return
    elif a.cmd == "send-outbox":  # pragma: no cover - needs an SMTP server
        host = os.environ.get("SMTP_HOST")
        if not host:
            sys.exit("SMTP_HOST not configured")
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=20) as smtp:
            smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            for r in con.execute("SELECT * FROM email_outbox WHERE sent_utc IS NULL AND attempts < 5").fetchall():
                msg = EmailMessage(); msg["From"] = os.environ.get("SMTP_FROM", "no-reply@localhost")
                msg["To"], msg["Subject"] = r["to_email"], r["subject"]; msg.set_content(r["body"])
                try:
                    smtp.send_message(msg)
                    con.execute("UPDATE email_outbox SET sent_utc=?, attempts=attempts+1 WHERE id=?", (utcnow(), r["id"]))
                except smtplib.SMTPException as e:
                    con.execute("UPDATE email_outbox SET attempts=attempts+1, last_error=? WHERE id=?", (str(e)[:300], r["id"]))
    con.close()


if __name__ == "__main__":
    main()
