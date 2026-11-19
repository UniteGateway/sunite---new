# Security model (v0.4)

**Identity.** Email + password (scrypt, n=2^14, r=8, p=1, per-user salt). Policy: ≥10 chars, not common, not containing
the email name, ≥5 distinct characters. Email verification required before sign-in.

**Sessions.** 256-bit random bearer tokens; only SHA-256 digests stored. Absolute expiry (12 h) and idle timeout (2 h).
Revoked on logout, logout-everywhere, password change/reset, and removal from an organisation. Bearer header (not
cookies) means no CSRF surface; the browser keeps the token in sessionStorage (cleared when the tab closes).

**Abuse controls.** Per-account lockout after 5 failed logins in 15 min; per-IP token bucket (20/min) on all auth
endpoints → HTTP 429. Unknown accounts and wrong passwords return the same message and similar timing; password-reset
requests always return the same response.

**Tenancy.** Every project access goes through `services.projects._access`. A project is visible only to (a) members of
its owning organisation whose role is not grant-only, or (b) holders of an explicit user/org grant. All other cases return
404, so ids cannot be probed. Customers are grant-only. Only the owner organisation can delete, share or change status.

**Authorisation.** Role → permission matrix in `auth/rbac.py`, enforced in services (not only the UI). Engineering
approvals (`design.electrical.approve`, `design.structural.approve`) are withheld from company admins on purpose.
Approved projects are locked until reopened.

**Audit.** Auth events, membership changes, project create/update/status/share/delete written to `audit_logs`
with request id and IP; secret-looking fields are stripped.

**Headers.** nosniff, frame-deny, no-referrer, no-store, CSP (inline scripts still allowed until the Next.js rebuild).

**Known gaps before public launch.** PostgreSQL row-level security as defence-in-depth; Redis-backed rate limiting
for multi-instance; MFA for admins; SMTP delivery tested; penetration test; backups; HTTPS termination.
