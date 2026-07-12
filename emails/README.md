# Vinc — Web account & email (separated from the offline app)

> **Design rule:** the desktop app's promise ("offline-first, no account, no telemetry,
> your data never leaves your machine") is **inviolable**. Everything here lives on the
> **website side only** and is strictly separated from the app. The app never logs in,
> never phones home, and never sends email.

## Three independent modules

| # | Module | Scope | Account? | Touches app data? | Status |
|---|--------|-------|----------|-------------------|--------|
| A | **Desktop app** (Vinc) | Local knowledge tool | ❌ none | — (it *is* the data) | shipping |
| B | **Email newsletter** | `news@vincs.app` release news | opt-in email only | ❌ never | templates ready (this folder) |
| C | **Web member account** | vincs.app login → future Pro / per-account sharing | email + password | ❌ never reads your local vault | UI/spec now, backend deferred |

The three share **nothing** except the brand. B does not require C (you can subscribe
without an account). C does not require the app. The app requires neither.

---

## B. Email newsletter

**Provider:** a transactional/broadcast service — **Resend** (recommended) or Postmark.
Self-hosted SMTP on the VM is discouraged (deliverability/IP-reputation risk).

**Setup checklist**
1. Add domain `vincs.app` in Resend → add the **SPF + DKIM + DMARC** DNS records (Gabia DNS).
2. Sender identity: `Vinc <news@vincs.app>`.
3. Store subscribers (Resend Audiences, or your own table when module C exists).
4. Every send **must** include a working `{{unsubscribe_url}}` (List-Unsubscribe header too) — legal + trust.

**Templates in this folder**
| File | Purpose | Subject | Merge tags |
|---|---|---|---|
| `welcome.html` | Sent right after opt-in confirm | `Welcome to Vinc` | `{{name}} {{email}} {{unsubscribe_url}}` |
| `update.html` | Sent per release | `Vinc {{version}} is out` | `{{name}} {{email}} {{version}} {{h1_title}} {{h1_body}} {{h2_title}} {{h2_body}} {{h3_title}} {{h3_body}} {{unsubscribe_url}}` |

**Design notes:** Ink & Paper palette, light base (email-client reliability) + a
`prefers-color-scheme: dark` block, table layout + inline styles, **no external images**
(deliverability + privacy — no tracking pixels), MSO/Outlook bulletproof button.
Test-send through Resend + a Litmus/Email-on-Acid preview before going live.

**Double opt-in flow (recommended, privacy-consistent):**
`site signup form → POST /api/subscribe → confirm email (link) → confirm → welcome.html`

---

## C. Web member account (future Pro / per-account sharing)

**Purpose (owner, 2026-07-12):** website member accounts so that later a **Pro tier** and
**per-account data sharing** can be offered. Not needed to *install/run* the app.

**Hard boundaries (keep the app's promise):**
- The account lives on the **web** only. Signing in on vincs.app must **never** be
  required to use the desktop app.
- The account backend **never** reads the local vault. Any future "sharing" is an
  **explicit, opt-in `.vincpack` upload** the user initiates — same content-only pack the
  app already exports (D19), never silent sync.
- Marketing copy must stay honest: the app is still "no account, no login." Position the
  web account as *"optional — for the website / Pro, not for your app."*

**Confirmed stack (owner, 2026-07-12):**
- **DB → cloud provider's managed "Cloud DB for MySQL"** (same cloud as the VM, private
  network, currently free). Managed = provider handles backups/patching/HA. **Not**
  co-located with the sidecar's local DB.
- **Auth → passwordless magic-link** (no passwords stored — smaller attack surface; pairs
  with the Resend email service). Account is created on first verified link.
- **API →** a small service on the VM behind Caddy (e.g. `api.vincs.app`), talking to the
  managed MySQL over the cloud's private network. Sessions via httpOnly/Secure/SameSite cookies.
- Backend deploy is **deferred**; UI + this spec first. Full hardened spec: `ARCHITECTURE.md`.

**Screens to design (site + Figma), deferred backend:**
`/login` · `/signup` · `/account` (subscription + newsletter prefs) · password reset ·
plus a lightweight **newsletter signup** block on the marketing pages (email-only, no account).

---

## What's done vs next

- ✅ `welcome.html`, `update.html` — on-brand, provider-ready templates.
- ✅ This separation spec.
- ⏭ Next: newsletter signup UI on the site, `/login` `/signup` `/account` page designs
  (site + Figma), Resend domain setup, then (later) the account API.
