<!-- vinc-export
doc_id: vinc.emails.architecture
title: Vinc Web-Account & Email Subsystem — Architecture & Security Spec
origin: Vinc/emails/ARCHITECTURE.md@a63b064
chunks: 26
verified: true
content_hash: dc773407a8e0d1c954c9bc85f886e805710cb30002cfe82c821eb463a631dbe6
updated_at: 2026-08-01T16:20:47.973Z
-->

# Vinc Web-Account & Email Subsystem — Architecture & Security Spec

**Status:** authoritative merged spec (supersedes the four expert drafts on any conflict)
**Scope:** the vincs.app website account + newsletter/email subsystem **only**. The desktop Vinc app is out of scope and, by architecture, never participates.
**Golden rule:** nothing in this document may weaken the offline app's promise — *no account, no login, no telemetry, vault stays local*. Any change that couples the app to this subsystem is a security regression.

---

## 1. Overview & Separation Invariant

The website offers an **optional** magic-link web account (for newsletter today, Pro later) and a double-opt-in newsletter. It is a physically and credentially separate system from the desktop app and its local vault.

### 1.1 Hard separation boundaries (non-negotiable)

- **B1 — App never authenticates.** The desktop app NEVER calls the account API, NEVER creates a session, and renders NO email/login/account UI. Pro is mentioned in-app only as an outbound link that opens vincs.app in the browser.
- **B2 — API never reads the vault.** The account API is a separate process with its own MySQL schema and credentials. It NEVER reads the local vault or the sidecar DB (loopback `:8787`) or vinc-mcp (`:8790`), and shares no secrets with them.
- **B3 — No silent sync, ever.** Data leaves the device ONLY via an explicit, user-initiated `.vincpack` upload (content-only pack). "Sharing" is a deliberate file action, never background replication.
- **B4 — Web-only PII, disclosed.** The server stores email (+ optional name) and, for abuse control, request IP/timestamps on `auth_tokens`/`subscribers`. This is website PII disclosed in the Privacy Policy; it is never described as, or connected to, app behavior.
- **B5 — `/api/me` is whitelisted** to `{email, name?, plan, newsletter, created_at}`. It exposes nothing device- or vault-derived.
- **B6 — Passwordless only.** No password field or `password` column anywhere in UI or DB.

### 1.2 Claim matrix (canonical source for all site/store/README copy)

| Surface | Claim |
|---|---|
| **APP** | "No account. No login. No telemetry. Your notes and vault never leave your device." |
| **WEB** | "Optional account for the newsletter and (later) Pro. The only thing we store is the email you type here." |
| **BANNED** | "Vinc never connects to the internet", "fully air-gapped", or any phrase that erases the optional website. |

### 1.3 CI guards (run before every release)

- **App-isolation grep:** fail the build if desktop-app source references the account host or any `/api/*` account endpoint.
- **Banned-phrase lint:** fail if marketing/README copy contains a banned absolute claim.
- **Security snapshot tests:** token single-use race, expiry boundary, enumeration-timing parity, cookie-flag assertions, open-redirect rejection, and a response-header snapshot (HSTS/CSP/Referrer-Policy).

---

## 2. Hosting & Process Architecture

- **Single origin.** The site and the API are served from the **same origin**: static pages at `https://vincs.app/...` and the API at `https://vincs.app/api/...`. Caddy terminates TLS and `reverse_proxy /api/*` to the Node/Bun account service on a loopback port. **There is no `api.vincs.app`.** (This is what lets the session cookie be host-only with the `__Host-` prefix and keeps every request first-party so `SameSite=Lax` fully protects it.)
- **Process isolation.** The account service runs under its **own dedicated OS user**, isolated from `vinc-sidecar` and `vinc-mcp`.
- **DB isolation.** Dedicated schema `vinc_account` on the managed Cloud DB for MySQL, reachable **only** over the VPC private network (`require_secure_transport=ON`, no public endpoint). See §4.7.
- **Connection pool.** Bounded pool (~5–10) with keepalive/validation; the managed tier caps `max_connections` low.

---

## 3. Authentication (passwordless magic-link)

### 3.1 Token

- **Entropy:** 32 bytes (256-bit) from a CSPRNG, base64url. Never a UUID/timestamp/counter.
- **At rest:** store **only** `token_hash = HMAC-SHA256(server_pepper, token)`. The pepper is a 32-byte key in the service secrets store, never in the DB. The raw token exists only in the email link and the user's browser.
- **TTL:** 15 minutes (unified with user copy). Single-use.
- **Single active token:** on each `/auth/request`, invalidate all prior unused tokens for that email.
- **Atomic single-use consume** (sole replay/double-submit guard):
  ```sql
  UPDATE auth_tokens SET used_at = CURRENT_TIMESTAMP(3)
   WHERE token_hash = ? AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP(3);
  -- proceed only if ROW_COUNT() = 1
  ```
- **Timing-safe:** lookup by indexed `token_hash` equality; any in-process comparison uses `crypto.timingSafeEqual`. Never string-compare raw tokens.

### 3.2 Verify flow (anti-prefetch, anti-log, anti-leak)

1. **Link format:** `https://vincs.app/auth/verify#<token>` — token in the **URL fragment**, never sent to the server, never logged, never in `Referer`.
2. **GET `/auth/verify`** returns a **static** interstitial only (no state change → survives scanner prefetch). Client JS reads the fragment, calls `history.replaceState` to strip it, and requires an explicit user click.
3. **POST `/api/auth/verify`** with the token in the JSON body performs the atomic consume above, checks flow-binding (§3.3), find-or-creates the user by normalized email, and issues the session.
4. Interstitial headers: `Referrer-Policy: no-referrer`; zero third-party assets.

### 3.3 Flow-binding (anti-forward / login-CSRF)

- On `POST /api/auth/request`, set a `__Host-lc` cookie (32-byte random, HttpOnly, Secure, SameSite=Lax, 15-min) and store its **hash** beside the token.
- On verify, if `__Host-lc` **matches** the token's bound challenge → proceed automatically (same browser).
- If the nonce is **absent or mismatched** (legit cross-device, or a forwarded/attacker-initiated link) → fall through to a **confirm-context screen** showing request time + approximate IP/location and "only continue if you started this," before allowing consume. (This replaces a hard 409, preserving cross-device use while blocking silent forwarded-link login.)

### 3.4 Sessions (opaque, server-side — no JWT)

- **Session id:** 32-byte CSPRNG; store only `HMAC-SHA256(pepper, id)` + `user_id` + `expires_at`.
- **Cookie:** `__Host-session`; `HttpOnly; Secure; SameSite=Lax; Path=/`; **no Domain** (host-only).
- **Lifetime:** rolling 30-day idle, absolute max 90 days. **Rotate the session id on each new login** (session-fixation defense).
- **Revocation:** logout and account-delete DELETE the row → immediate invalidation.
- **CSRF token:** `__Host-csrf` (JS-readable) echoed in `X-CSRF-Token` on every authenticated state-changing request.

---

## 4. Database Schema (MySQL 8.0, managed Cloud DB)

Dedicated schema, isolated from vinc-sidecar/vinc-mcp. InnoDB, utf8mb4, `DATETIME(3)` UTC, hash-only secrets, forward-only migrations. Email is lowercase-normalized by the app and uniqueness is enforced case-insensitively via `utf8mb4_0900_ai_ci`.

```sql
CREATE DATABASE IF NOT EXISTS vinc_account
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE vinc_account;
```

### 4.1 users — created on first verify (never on request)

```sql
CREATE TABLE users (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id   BINARY(16)      NOT NULL,                       -- UUIDv7, external refs
  email       VARCHAR(254)    NOT NULL,                       -- lowercase-normalized by app
  name        VARCHAR(120)    NULL,                           -- optional PII — minimize
  created_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                              ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email     (email),                      -- ai_ci => case-insensitive identity
  UNIQUE KEY uq_users_public_id (public_id)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

### 4.2 auth_tokens — magic link, KEYED BY EMAIL (user may not exist yet)

```sql
CREATE TABLE auth_tokens (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  email         VARCHAR(254)    NOT NULL,
  token_hash    BINARY(32)      NOT NULL,   -- HMAC-SHA256(pepper, 256-bit token)
  challenge_hash BINARY(32)     NOT NULL,   -- HMAC-SHA256(pepper, __Host-lc nonce) : flow-binding
  purpose       ENUM('login','delete') NOT NULL DEFAULT 'login',
  expires_at    DATETIME(3)     NOT NULL,   -- 15 min
  used_at       DATETIME(3)     NULL,       -- single-use; set atomically on verify
  request_ip    VARBINARY(16)   NULL,       -- optional abuse control; purged with row
  created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_auth_tokens_hash          (token_hash),
  KEY        ix_auth_tokens_email_created (email, created_at),  -- rate-limit lookups
  KEY        ix_auth_tokens_expires       (expires_at)          -- purge scan
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
-- No FK to users by design. On verify: atomic consume, verify challenge, then
-- find-or-create user by normalized email, then create a session.
```

### 4.3 sessions — server-side, revocable

```sql
CREATE TABLE sessions (
  token_hash   BINARY(32)      NOT NULL,   -- HMAC-SHA256(pepper, 256-bit cookie id)
  user_id      BIGINT UNSIGNED NOT NULL,
  created_at   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  last_seen_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),  -- sliding renewal app-side
  expires_at   DATETIME(3)     NOT NULL,   -- absolute cap (<=90d); idle cap 30d app-side
  PRIMARY KEY (token_hash),
  KEY ix_sessions_user    (user_id),
  KEY ix_sessions_expires (expires_at),
  CONSTRAINT fk_sessions_user FOREIGN KEY (user_id)
    REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

### 4.4 subscribers — newsletter, DECOUPLED from users, single source of truth

```sql
CREATE TABLE subscribers (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  email           VARCHAR(254)    NOT NULL,
  status          ENUM('pending','confirmed','unsubscribed','bounced','complained')
                                  NOT NULL DEFAULT 'pending',
  confirm_hash    BINARY(32)      NULL,       -- HMAC-SHA256(pepper, single-use confirm token)
  confirm_sent_at DATETIME(3)     NULL,       -- drives pending-cleanup
  confirmed_at    DATETIME(3)     NULL,
  unsub_token     CHAR(43)        NOT NULL,   -- base64url 256-bit bearer, stored USABLY (perpetual, no login)
  unsubscribed_at DATETIME(3)     NULL,
  source          VARCHAR(60)     NULL,       -- e.g. 'site_footer'
  created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                  ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_subscribers_email (email),
  UNIQUE KEY uq_subscribers_unsub (unsub_token),
  KEY        ix_subscribers_status (status),
  KEY        ix_subscribers_pending_cleanup (status, confirm_sent_at)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
-- Send-eligibility = status='confirmed'. Sends MUST exclude
-- status IN ('unsubscribed','bounced','complained') and status='pending'.
-- The /account "Release news" toggle reads/writes THIS row (never a boolean on users).
```

### 4.5 subscriptions — FUTURE (Pro); provider-backed, no card data (PCI SAQ-A)

```sql
CREATE TABLE subscriptions (
  id                       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id                  BIGINT UNSIGNED NOT NULL,
  plan                     VARCHAR(40)  NOT NULL,
  status                   ENUM('trialing','active','past_due','canceled',
                                'incomplete','incomplete_expired') NOT NULL,
  provider                 VARCHAR(20)  NOT NULL,          -- 'stripe' | 'paddle'
  provider_customer_id     VARCHAR(64)  NULL,
  provider_subscription_id VARCHAR(64)  NULL,
  current_period_end       DATETIME(3)  NULL,
  cancel_at                DATETIME(3)  NULL,
  canceled_at              DATETIME(3)  NULL,
  created_at               DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at               DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                        ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_sub_provider (provider, provider_subscription_id),
  KEY        ix_sub_user     (user_id),
  CONSTRAINT fk_sub_user FOREIGN KEY (user_id)
    REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
-- Account-delete MUST cancel at the provider BEFORE the DB cascade.
```

### 4.6 shared_packs — FUTURE; METADATA ONLY (pack content lives in object storage)

```sql
CREATE TABLE shared_packs (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  public_id     BINARY(16)      NOT NULL,        -- unguessable share slug
  owner_user_id BIGINT UNSIGNED NOT NULL,
  title         VARCHAR(200)    NULL,
  meta_json     JSON            NOT NULL,        -- metadata ONLY — never vault content
  byte_size     INT UNSIGNED    NULL,            -- sanity cap on uploaded pack
  created_at    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at    DATETIME(3)     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_packs_public (public_id),
  KEY        ix_packs_owner  (owner_user_id),
  KEY        ix_packs_expires(expires_at),
  CONSTRAINT fk_packs_owner FOREIGN KEY (owner_user_id)
    REFERENCES users (id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT ck_packs_meta CHECK (JSON_VALID(meta_json))
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC
  DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE schema_migrations (
  version    VARCHAR(20) NOT NULL,
  checksum   CHAR(64)    NOT NULL,
  applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

### 4.7 Access / isolation (run once by an admin)

```sql
-- Global (parameter group): require_secure_transport = ON
-- Network: DB on VPC private subnet, no public endpoint; ACG allows ONLY the VM private IP.

-- Runtime least-privilege user (DML only, no DDL), host-locked to private subnet:
CREATE USER 'vinc_acct_app'@'10.%' IDENTIFIED BY '<strong-secret>' REQUIRE SSL;
GRANT SELECT, INSERT, UPDATE, DELETE ON vinc_account.* TO 'vinc_acct_app'@'10.%';
-- NO grants on sidecar/mcp schemas => isolation from the offline vault DB.

-- Migration user (DDL), used ONLY by the deploy pipeline:
CREATE USER 'vinc_acct_ddl'@'10.%' IDENTIFIED BY '<strong-secret>' REQUIRE SSL;
GRANT ALL PRIVILEGES ON vinc_account.* TO 'vinc_acct_ddl'@'10.%';
```

**Migrations:** forward-only `.sql` files tracked in `schema_migrations` (dbmate/Flyway/Knex), applied by the DDL user on deploy. No ORM auto-sync/destructive diff against production.

### 4.8 Expiry cleanup

Primary path: the always-on app service runs the DELETEs on an interval (managed MySQL often disables `event_scheduler` and withholds the EVENT privilege; the runtime DML user is sufficient). Ship the EVENT DDL only as an optional accelerator after confirming the parameter group permits it.

```sql
-- App-cron (primary):
DELETE FROM auth_tokens WHERE expires_at < (CURRENT_TIMESTAMP(3) - INTERVAL 1 DAY);
DELETE FROM sessions    WHERE expires_at < CURRENT_TIMESTAMP(3);
DELETE FROM subscribers WHERE status='pending' AND confirm_sent_at < (CURRENT_TIMESTAMP(3) - INTERVAL 7 DAY);
```

---

## 5. API Contracts

### 5.0 Conventions

- **Base:** `https://vincs.app/api` (Caddy TLS; HTTP→HTTPS; HSTS). JSON is UTF-8. Interstitial pages are static `text/html` served under `https://vincs.app/...`.
- **Session cookie:** `__Host-session` (opaque, server-side). **Login-flow cookie:** `__Host-lc` (HttpOnly, 15-min). **CSRF:** `__Host-csrf` (JS-readable) echoed in `X-CSRF-Token` on all authenticated state-changing requests; mismatch → `403 csrf_failed`. All state-changing POSTs additionally validate `Origin`/`Referer` against a `vincs.app` allowlist and require `Content-Type: application/json` (webhook/one-click endpoints excepted).
- **Error envelope:** `{ "error": { "code": "snake_case", "message": "...", "request_id": "req_…" } }`.
- **Rate-limit headers:** `RateLimit-Limit/Remaining/Reset`; 429 carries `Retry-After`.
- **Idempotency:** `POST /auth/request` and `POST /subscribe` accept an optional `Idempotency-Key` (10-min dedupe window).
- **Tokens:** 256-bit CSPRNG base64url; DB stores `HMAC-SHA256(pepper, token)` only. TTLs — login **15m**, subscribe-confirm **24h**, delete-confirm **15m**. All single-use.
- **Enumeration parity:** `/auth/request`, `/subscribe`, `/unsubscribe` always return the same generic 200 regardless of existence/rate-limit/suppression; sends are async so timing never branches on existence. Public forms carry a hidden honeypot field.

### 5.1 `POST /api/auth/request` (mint magic link; unauthenticated)

- Req: `{ "email": "...", "purpose": "login" }`
- Rate: per-email **1/60s** and **5/h** (silent drop past cap), per-IP **20/h**.
- Behavior: validate email **syntax only**; create no user; mint token bound to a fresh `__Host-lc` challenge; enqueue `magic_link` send async (Resend `Idempotency-Key = "magic:<tokenId>"`).
- Resp: `200 {status:"ok", message:"If that address exists, a sign-in link has been sent."}` + `Set-Cookie: __Host-lc=…` (identical body when throttled/suppressed) · `400 invalid_email` (malformed syntax only) · `429 too_many_requests` (per-IP abuse only).

### 5.2 `GET /auth/verify` (static interstitial; token in fragment)

- No state change (survives scanner prefetch). Renders "Confirm sign-in"; JS reads `#<token>`, strips it via `history.replaceState`, and POSTs on explicit click. `Referrer-Policy: no-referrer`, zero third-party assets. Always `200 text/html`.

### 5.3 `POST /api/auth/verify` (consume; unauthenticated → authenticated)

- Req: `{ "token":"..." }` + Cookie `__Host-lc`.
- Behavior: atomic consume (§3.1); verify `challenge_hash` vs `__Host-lc` — match → proceed; absent/mismatch → return `confirm_context` payload (request time + approx IP/location) for a user-confirmed retry (not a hard failure); find-or-create user by normalized email; rotate/create session.
- Resp: `200 {status:"ok", user:{id,email,name}}` + `Set-Cookie __Host-session` (new), clear `__Host-lc`, set `__Host-csrf` · `400 invalid_or_expired_token` · `409 confirm_required` (carries confirm-context; client shows the confirm screen).

### 5.4 `POST /api/auth/logout` (authenticated; CSRF)

- DELETE current session row; clear `__Host-session`. `204` · `401 not_authenticated` · `403 csrf_failed`.

### 5.5 `GET /api/me` (authenticated)

- `200 { user: { email, name?, plan:"free|pro", newsletter:boolean, created_at } }` (whitelist per B5) · `401 not_authenticated`.

### 5.6 `POST /api/subscribe` (double opt-in; unauthenticated)

- Req: `{ "email":"...", "source":"website", "hp":"" }` (`hp` = honeypot).
- Rate: per-email **3/h** (silent), per-IP **20/h**.
- Behavior: upsert subscriber (`status='pending'`); mint 24h confirm token; enqueue `subscribe_confirm` async. No welcome yet.
- Resp: `200 {status:"ok", message:"Check your inbox to confirm your subscription."}` (identical if already confirmed) · `400 invalid_email`.

### 5.7 `GET /subscribe/confirm` + `POST /api/subscribe/confirm`

- GET: static "Confirm subscription" interstitial (token in fragment). `200 text/html`.
- POST `{ "token":"..." }`: atomic set `confirmed_at`; generate `unsub_token` if absent; enqueue `welcome`. `200 {status:"confirmed"}` · `400 invalid_or_expired_token`.

### 5.8 Unsubscribe (RFC 8058 one-click + human interstitial)

- **`POST /api/unsubscribe?token=<unsub_token>`** — mailbox-provider one-click target and the interstitial's POST button. Unauthenticated, idempotent. `200 {status:"unsubscribed"}` · `400 invalid_token`. (unsub_token is a low-sensitivity bearer, so it rides in the query string as RFC 8058 requires.)
- **`GET /unsubscribe?token=…`** — static "Confirm unsubscribe" interstitial; removal happens only on the POST (scanner prefetch cannot silently unsubscribe).
- Newsletter headers on every marketing send:
  ```
  List-Unsubscribe: <https://vincs.app/api/unsubscribe?token=UNSUB>, <mailto:unsub@vincs.app?subject=UNSUB>
  List-Unsubscribe-Post: List-Unsubscribe=One-Click
  ```

### 5.9 Account deletion (two-step; irreversible)

- **`POST /api/account/delete`** (authenticated; CSRF): mint 15m delete-confirm token; enqueue `account_delete_confirm`. `202 {status:"confirmation_sent"}` · `401` · `403 csrf_failed`.
- **`POST /api/account/delete/confirm` `{token}`**: single transaction — cancel provider subscription first (if any), then:
  ```sql
  START TRANSACTION;
    DELETE FROM auth_tokens WHERE email = :email;
    DELETE FROM users       WHERE id    = :user_id;  -- cascades sessions/subscriptions/shared_packs
    DELETE FROM subscribers WHERE email = :email;    -- only if newsletter removal also requested
  COMMIT;
  ```
  Also purge the identity from Resend. Clear the session cookie. **The local vault is out of scope by architecture and is never touched.** `200 {status:"deleted"}` · `400 invalid_or_expired_token`.

### 5.10 `POST /api/webhooks/resend` (Svix-signed)

- Verify `svix-id/svix-timestamp/svix-signature` with the endpoint signing secret; reject unsigned/invalid → `400`.
- `email.bounced` (hard) → add to suppression table. `email.complained` → suppress + mark subscriber `complained/unsubscribed`. `email.delivered/opened` → optional website-only metrics (never fed to the app).
- Suppression is consulted before every outbound send. `200` ack.

---

## 6. Email / Resend Integration

- **From:** `Vinc <news@vincs.app>`. **SPF + DKIM + DMARC `p=reject`** aligned on vincs.app (prevents login-mail spoofing).
- **Separate streams** so a marketing complaint/unsub never blocks auth mail:
  - **TRANSACTIONAL** → `magic_link`, `subscribe_confirm`, `welcome`, `account_delete_confirm`.
  - **MARKETING** → `product_update` (newsletter, via Resend Broadcasts/Audience).
- **Idempotency:** every send carries `Idempotency-Key = "<type>:<tokenId>"` (or broadcast id) → retries never double-send; one flow = at most one token = at most one email.
- **Suppression:** check the suppression table (fed by webhooks) **before** any send; skip + log if suppressed.
- **Error handling:** 3 tries with exponential backoff; on total failure log + enqueue for retry but **still return the generic 200** to the caller (a visible send error would be an enumeration oracle). Alert on failure-rate threshold.
- **Content rule:** emails contain ONLY the opaque token link. No vault data, no app data, ever.

---

## 7. Security Controls Summary

- **Sessions:** opaque, server-side, HMAC-hashed, `__Host-session`, rotate-on-login, instant revocation. No JWT.
- **CSRF:** `SameSite=Lax` + Origin/Referer allowlist + `__Host-csrf` double-submit on every authenticated state-changing POST.
- **Rate-limiting/abuse:** layered per-email + per-IP limits (§5), a **global daily send ceiling that fails closed**, uniform-200 throttling, honeypot fields. CAPTCHA/PoW added only reactively to preserve the no-tracking ethos.
- **Enumeration resistance:** always-200, async sends, no existence branch (first-verify user creation makes this natural).
- **Phishing:** 15-min single-use + same-browser flow-binding + confirm-context screen; DMARC `p=reject`; email states what triggered it + expiry.
- **Open-redirect:** post-login redirect is a fixed `/account`. If a return path is ever needed, accept only a relative path matching `^/[^/]` (reject `//` or any scheme) against a small allowlist.
- **Secrets on the VM:** dedicated OS user; Resend key (send-only), MySQL creds, token pepper, session/CSRF secret in a systemd `EnvironmentFile` (chmod 0600, owned by the service user); never in the repo or process args; rotate on a schedule; verify Resend webhook signatures. Never log tokens/secrets/PII.
- **TLS / headers (Caddy):** auto-TLS, TLS 1.2+ only, HTTP→HTTPS. `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` (enable `includeSubDomains`/`preload` only after every vincs.app subdomain is HTTPS). `Content-Security-Policy` with `frame-ancestors 'none'` and no third-party asset origins; `X-Content-Type-Options: nosniff`; `Referrer-Policy: strict-origin-when-cross-origin` globally and `no-referrer` on `/auth/*`. Caddy access logs omit query strings on `/auth/*`.
- **Audit logging:** log auth events (request/verify/logout/delete) with hashed identifiers + outcome — never tokens or secrets — so takeover attempts are detectable without a new PII honeypot.

---

## 8. UI Pages & Copy (EN / KO)

### 8.1 Page / state inventory

1. Newsletter signup block (homepage footer + `/news`): default · submitting · success(check-inbox) · error.
2. `/subscribe/confirm` landing: confirmed · expired/invalid.
3. `/unsubscribe` landing (one-click, no login): unsubscribed · resubscribe-offer · invalid.
4. `/login` (magic-link): default · submitting · check-inbox · rate-limited.
5. `/verify`: verifying · success→`/account` · **expired-or-used** · **invalid** · **confirm-context** (cross-device/forwarded-link confirm screen).
6. `/account` (auth required): view · newsletter-toggling · delete-confirm · deleted. Unauthenticated → redirect `/login`.
7. Transactional emails: sign-in link, double-opt-in confirm, account-delete confirm, welcome.

### 8.2 UX rules

- Magic-link expiry **15 min**, single-use. Resend cooldown ≥ 30s.
- Check-inbox never confirms whether the email exists ("If that email is valid, we sent a link.") — enumeration parity with the API.
- `/account` newsletter toggle is optimistic-with-rollback and writes to the **subscribers** row (single source of truth). An authenticated matching email toggling ON sets `confirmed_at` immediately (no second opt-in); toggling OFF clears eligibility. State reconciles on every login.
- Delete is the only destructive control; explicit typed/confirm dialog; always reassures about local data.
- Every screen that could read as "the app has an account" carries the boundary note/banner.

### 8.3 Copy — Newsletter signup block

- **EN** — Heading: "Vinc release news" · Sub: "An occasional email when we ship something new. No account needed. Unsubscribe anytime." · Placeholder: "you@example.com" · Button: "Subscribe" · Submitting: "Subscribing…" · Success: "Almost there — check your inbox to confirm." · Fine print: "We only use your email for release news. Nothing from the app is ever sent to us." · Error: "Something went wrong. Please try again."
- **KO** — 제목: "Vinc 소식 받기" · 설명: "새 기능을 출시하면 가끔 이메일로 알려드려요. 계정은 필요 없고, 언제든 해지할 수 있어요." · placeholder: "you@example.com" · 버튼: "구독하기" · 전송 중: "구독하는 중…" · 성공: "거의 다 됐어요 — 받은 편지함에서 구독을 확인해 주세요." · 안내: "이메일은 오직 출시 소식 전달에만 사용해요. 앱의 데이터는 저희에게 전송되지 않아요." · 오류: "문제가 발생했어요. 다시 시도해 주세요."

### 8.4 Copy — Double opt-in confirm email + landing

- **EN** — Subject: "Confirm your Vinc newsletter subscription" · Body: "Tap the button to confirm you'd like Vinc release news. If this wasn't you, just ignore this email." · Button: "Confirm subscription" · Landing (confirmed): "You're subscribed. Thanks!" · Landing (expired/invalid): "This link has expired. Please sign up again."
- **KO** — 제목: "Vinc 뉴스레터 구독을 확인해 주세요" · 본문: "아래 버튼을 눌러 Vinc 출시 소식 구독을 확인해 주세요. 본인이 아니라면 이 메일을 무시하셔도 돼요." · 버튼: "구독 확인" · 확인 완료: "구독이 완료되었어요. 감사합니다!" · 만료/오류: "이 링크는 만료되었어요. 다시 구독해 주세요."

### 8.5 Copy — Unsubscribe landing

- **EN** — "You've been unsubscribed. You won't get any more release news." · Link: "Changed your mind? Resubscribe"
- **KO** — "구독이 해지되었어요. 더 이상 출시 소식을 보내지 않아요." · 링크: "다시 받고 싶으세요? 재구독하기"

### 8.6 Copy — `/login` (magic-link)

- **EN** — Title: "Sign in to vincs.app" · Boundary note: "The Vinc app doesn't need an account. Sign in only to manage your Vinc account and Pro plan on the web." · Label: "Email" · Placeholder: "you@example.com" · Button: "Continue with email" · Legal: "By continuing you agree to the Terms and Privacy Policy." · Submitting: "Sending link…" · Check-inbox heading: "Check your inbox" · Check-inbox body: "We sent a sign-in link to {email}. Open it on this device to continue — it expires in 15 minutes." · Resend: "Didn't get it? Resend link" · Cooldown: "You can resend in {n}s" · Change email: "Use a different email" · Rate-limited: "Too many attempts. Please wait a minute and try again."
- **KO** — 제목: "vincs.app 로그인" · 경계 안내: "Vinc 앱은 계정이 필요 없어요. 웹에서 Vinc 계정과 Pro 플랜을 관리할 때만 로그인하세요." · 라벨: "이메일" · placeholder: "you@example.com" · 버튼: "이메일로 계속하기" · 법적 고지: "계속하면 이용약관과 개인정보 처리방침에 동의하는 것으로 간주해요." · 전송 중: "링크 보내는 중…" · 받은편지함 제목: "받은 편지함을 확인하세요" · 받은편지함 본문: "{email}로 로그인 링크를 보냈어요. 이 기기에서 링크를 열어 계속하세요 — 15분 후에 만료돼요." · 재전송: "메일이 오지 않았나요? 링크 다시 보내기" · 대기: "{n}초 후에 다시 보낼 수 있어요" · 이메일 변경: "다른 이메일 사용하기" · 요청 과다: "시도가 너무 많아요. 잠시 후 다시 시도해 주세요."

### 8.7 Copy — Sign-in link email

- **EN** — Subject: "Your Vinc sign-in link" · Body: "Tap to sign in to vincs.app. This link works once and expires in 15 minutes. If you didn't request it, ignore this email — nothing will happen." · Button: "Sign in"
- **KO** — 제목: "Vinc 로그인 링크" · 본문: "아래 버튼을 눌러 vincs.app에 로그인하세요. 이 링크는 한 번만 사용할 수 있고 15분 후 만료돼요. 요청하지 않으셨다면 무시하셔도 돼요 — 아무 일도 일어나지 않아요." · 버튼: "로그인"

### 8.8 Copy — `/verify`

- **EN** — Verifying: "Signing you in…" · Success: (redirect to `/account`) · Confirm-context: "You're signing in from a new browser. This request started at {time} from {approx location}. Only continue if that was you." · Button: "Yes, sign me in" · Expired/used: "This link has expired or was already used." · Button: "Back to sign in" · Invalid: "This sign-in link isn't valid." · Button: "Back to sign in"
- **KO** — 확인 중: "로그인 중…" · 성공: (`/account` 으로 이동) · 확인 안내: "새로운 브라우저에서 로그인하고 있어요. 이 요청은 {time}에 {approx location}에서 시작됐어요. 본인이 맞을 때만 계속하세요." · 버튼: "네, 로그인할게요" · 만료/사용됨: "이 링크는 만료되었거나 이미 사용되었어요." · 버튼: "로그인으로 돌아가기" · 무효: "유효하지 않은 로그인 링크예요." · 버튼: "로그인으로 돌아가기"

### 8.9 Copy — `/account`

- **EN** — Title: "Your Vinc account" · Boundary banner: "This is your web account. It's separate from the Vinc app on your device — your notes and vault never leave your computer and are not stored here." · Email row: "Email" → {email} (read-only) · Plan row: "Plan" → "Free" / (future) "Upgrade to Pro" · Newsletter row: "Release news email" (toggle) · Help: "An occasional email when we ship updates." · Toggling: "Saving…" · On: "You'll get release news." · Off: "You won't get release news." · "Sign out" · "Delete account" · Delete dialog title: "Delete your web account?" · Delete dialog body: "This removes your email and account data from our servers. It does NOT touch anything in the Vinc app on your device. This can't be undone." · Delete confirm: "Delete account" · Cancel: "Cancel" · Deleted: "Your web account has been deleted."
- **KO** — 제목: "내 Vinc 계정" · 경계 배너: "이 계정은 웹 전용이에요. 기기에 있는 Vinc 앱과는 분리되어 있으며, 메모와 보관함(vault)은 컴퓨터를 벗어나지 않고 여기에 저장되지 않아요." · 이메일: "이메일" → {email} (읽기 전용) · 플랜: "플랜" → "무료" / (추후) "Pro로 업그레이드" · 뉴스레터: "출시 소식 이메일" (토글) · 도움말: "업데이트를 출시하면 가끔 보내드려요." · 저장 중: "저장하는 중…" · 켬: "출시 소식을 받아요." · 끔: "출시 소식을 받지 않아요." · 로그아웃: "로그아웃" · 삭제: "계정 삭제" · 삭제 대화상자 제목: "웹 계정을 삭제할까요?" · 삭제 대화상자 본문: "서버에서 이메일과 계정 데이터가 삭제돼요. 기기의 Vinc 앱에는 아무 영향이 없어요. 되돌릴 수 없어요." · 삭제 확인: "계정 삭제" · 취소: "취소" · 삭제 완료: "웹 계정이 삭제되었어요."

---

## 9. Data Lifecycle & Privacy

- **PII minimization:** only email (+ optional name). No passwords, ever. IP is optional (`VARBINARY(16)`) and lives/dies with its token/session row — no separate long-lived audit copy; user-agent is omitted.
- **Retention:** expired auth tokens purged (24h grace), expired sessions purged hourly, unconfirmed subscribers purged after 7 days.
- **Deletion:** two-step, email-confirmed, single-transaction hard-delete (§5.9) + provider cancel + Resend identity purge. No soft-delete, no PII tombstone. `shared_packs` are metadata-only and removed via cascade. The vault is never touched by this subsystem.
- **Newsletter vs account are separate consents:** deleting the web account removes the subscriber row only if newsletter removal was also requested; unsubscribing never requires login and never enumerates.

---

## 10. What Is Deferred

- **Pro / subscriptions** (`subscriptions` table, `/account` upgrade flow, Stripe/Paddle integration) — schema included for review; not launched.
- **Shared packs / `.vincpack` upload** (`shared_packs` metadata + object-storage backend) — schema included; the only sanctioned device→server data flow, user-initiated, content-only, never the vault.
- **HSTS `preload` / `includeSubDomains`** — enable only after every vincs.app subdomain is HTTPS.
- **DB `EVENT` scheduler purge** — optional accelerator, contingent on the managed provider permitting `event_scheduler`; app-cron is the primary mechanism.
- **CAPTCHA / proof-of-work** — added reactively only if abuse appears, to preserve the no-friction/no-tracking ethos.

---

## Appendix A — Top risks (adversarial review)

1. Same-origin coupling (serving the API at vincs.app/api) means any XSS on the marketing/site pages shares an origin with the __Host-session cookie. Strict CSP, zero third-party assets, and HttpOnly cookies are load-bearing mitigations, not optional.
2. Cross-device magic-link redemption relies on the confirm-context fallback as its only guard when the link opens in a different browser than it was requested. If approximate IP/geo is unavailable or an inattentive user clicks through, a socially-engineered forward can still complete. Short TTL + explicit 'only continue if you started this' copy are the compensating controls.
3. The global daily outbound-email ceiling fails closed, so a mail-bomb aimed at exhausting it could deny legitimate logins/newsletters for the rest of the day. Per-email (1/60s, 5/h) and per-IP limits must trip first, and ceiling approach must alert operators before it is reached.
4. Service/DB/secret isolation from vinc-sidecar and vinc-mcp is enforced operationally (dedicated OS user, least-privilege MySQL user, 0600 EnvironmentFile), not by code. A single deploy misconfiguration can re-couple the account service to the offline vault data path, so deploy-time verification of user/grants/network scope is mandatory.
5. Managed Cloud DB for MySQL commonly disables event_scheduler, so expiry purge (tokens, sessions, pending subscribers) depends on the always-on app-cron. If that service is down, single-use token rows and PII accumulate. Purge execution must be monitored and alerted.
6. Marketing/store/README copy can drift into absolute claims ('never connects to the internet', 'fully air-gapped') that the optional web account makes literally false, creating a trust/legal exposure for a privacy-branded product. The claim matrix and a banned-phrase CI lint must be authoritative.

## Appendix B — Hardening decisions log

1. Serve the account API SAME-ORIGIN at vincs.app/api (Caddy reverse_proxy /api/* to a loopback Node/Bun port), NOT on api.vincs.app. This keeps the session cookie host-only with the __Host- prefix and keeps every request first-party so SameSite=Lax fully applies. All api.vincs.app references in the API draft are superseded.
2. Use opaque, revocable server-side sessions (256-bit CSPRNG id) — never a stateless JWT — so logout and account deletion invalidate immediately. Supersedes every 'sessions OR JWT' and 128-bit variant in the drafts.
3. Persist ALL secret tokens as HMAC-SHA256(server_pepper, token) with the pepper held outside the DB (systemd EnvironmentFile), not bare SHA-256 and never a slow password hash. Only the perpetual unsub_token is stored in usable bearer form. Supersedes the 'SHA-256(token)' notes in the DB/API drafts.
4. Deliver the magic-link token in the URL FRAGMENT (vincs.app/auth/verify#<token>); a static interstitial reads it in JS, strips it via history.replaceState, and consumes it only on an explicit user-clicked POST /api/auth/verify. Supersedes the query-string ?token= verify link. One-click unsubscribe keeps its low-sensitivity unsub_token in the query string because RFC 8058 provider POSTs require it.
5. Unify magic-link and delete-confirm TTL at 15 minutes (matches the already-localized EN/KO user copy); subscribe-confirm stays 24h. All single-use with atomic conditional-UPDATE consume (proceed only if ROW_COUNT()=1).
6. Merge the SECURITY 'flow nonce' and the API 'vinc_lc login-challenge' into ONE mechanism: a __Host-lc cookie (HttpOnly, Secure, SameSite=Lax, 15-min) whose hash is bound to the token. Same-browser redemption auto-proceeds; a missing/mismatched nonce falls through to a confirm-context screen (showing request time + approximate IP/location) rather than a hard 409, preserving legit cross-device use while blocking silent forwarded-link login.
7. Unify cookie names: __Host-session (opaque session), __Host-lc (login-flow binding, HttpOnly), __Host-csrf (double-submit CSRF token, JS-readable). All host-only, Secure, Path=/, no Domain.
8. Make the subscribers table (keyed by email) the SINGLE source of truth for newsletter send-eligibility. The /account 'Release news' toggle only reads/writes that row; because a magic-link user has proven email ownership, toggling ON sets confirmed_at immediately (no second opt-in). Never store newsletter opt-in as a boolean on users.
9. Key auth_tokens by email with NO FK to users; create the user row via find-or-create only on first successful verify. Never create a user on /auth/request (prevents orphan rows + existence leakage).
10. Account deletion is two-step and email-confirmed: authenticated POST /api/account/delete mints a 15-min delete-confirm token; POST /api/account/delete/confirm performs a single-transaction hard-delete (users cascade + explicit auth_tokens/subscribers DELETE), after cancelling any provider subscription. The local vault is out of scope by architecture and is never touched.
11. Rate limits are layered and fail-safe: /auth/request 1/60s + 5/h per-email and 20/h per-IP; /auth/verify 30/h per-IP; /subscribe 3/h per-email and 20/h per-IP; plus a global daily send ceiling that fails closed. Throttled and non-existent addresses return the identical uniform 200; sends are async so timing cannot branch on existence. A honeypot field is added to public forms.
12. Isolate the subsystem: dedicated OS user, dedicated least-privilege MySQL runtime user (DML only, host-locked to the private subnet, REQUIRE SSL), a separate DDL user for migrations, secrets in a chmod-0600 EnvironmentFile, DB on the VPC private network with require_secure_transport=ON and no public endpoint, and zero shared credentials with vinc-sidecar/vinc-mcp.
13. Run expiry purge primarily from the always-on app service (managed MySQL often disables the event scheduler); ship the EVENT DDL only as an optional accelerator after confirming event_scheduler is permitted.
14. Add RFC 8058 one-click unsubscribe (List-Unsubscribe + List-Unsubscribe-Post) served by an idempotent POST /api/unsubscribe, a Svix-verified POST /api/webhooks/resend feeding a suppression table checked before every send, and separate Resend transactional vs marketing streams so a marketing complaint can never suppress auth mail.
15. Freeze /api/me to a whitelist {email, name?, plan, newsletter, created_at}; expose nothing device- or vault-derived. Publish SPF + DKIM + DMARC p=reject on vincs.app.
