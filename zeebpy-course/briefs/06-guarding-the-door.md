# Module 6: Guarding the Door (Auth: JWT, Middleware, Permissions, Throttling)

Write to: `modules/06-guarding-the-door.html`
Section wrapper: `<section class="module" id="module-6" style="background: var(--color-bg)">` (even module = plain background)

## Teaching Arc
- **Metaphor:** **Getting into a nightclub.** The **bouncer** checks your ID at the door (the middleware decoding your token). Your **wristband** is the token you carry — proof you were already checked, so you do not re-show ID at every bar. The **guest list** is permissions (are you allowed in the VIP room / to delete a book?). The **capacity limit** is throttling (only so many requests per minute). (Do NOT reuse earlier metaphors.)
- **Opening hook:** "Should *anyone* be able to `DELETE /books/5/`? Obviously not. So how does the API know who you are, and what you are allowed to do? Meet the bouncer."
- **Key insight:** After you log in, the server hands you a **JWT** — a tamper-proof signed token. On every later request, **middleware** checks that token and attaches the matching user to `request.state.user`. **Permission classes** then decide if that user may perform the action. **Throttling** caps how often anyone can call.
- **"Why should I care?":** Security is where vibe coders get burned. Understanding tokens vs. sessions, and where the "who are you / what can you do" checks live, lets you ask an AI for the *right* protection — and spot when it suggests something insecure (like storing a password in plain text, or trusting the client).

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — minting a token when you log in. File: `zeeb_api/auth/jwt.py` (lines 186-201)
```python
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": now + timedelta(minutes=config.access_token_expire_minutes),
        "iat": now,
        "jti": str(uuid.uuid4()),
    }

    if config.issuer:
        payload["iss"] = config.issuer
    if config.audience:
        payload["aud"] = config.audience
    if claims:
        payload["claims"] = claims

    return jwt.encode(payload, config.secret_key, algorithm=config.algorithm)
```
Plain-English (the wristband is stamped with facts + a tamper-proof seal):
- "Start building the wristband — a small bundle of facts."
- "`sub` = who this is (the user id)."
- "`type` = what kind of pass this is (an access pass)."
- "`exp` = when it expires — wristbands do not last forever."
- "`iat` = when it was issued."
- "`jti` = a unique serial number for this exact token."
- "(the ifs) Optionally stamp who issued it and who it is for."
- "Optionally attach extra facts."
- "Finally, seal it with a secret signature. Anyone can *read* the facts, but no one can forge or change them without the secret."

### Snippet B — the bouncer on every request. File: `zeeb_api/auth/middleware.py` (lines 184-198)
```python
        request.state.user = None

        # Try to extract and validate token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix

            try:
                payload = decode_token(token, token_type="access")

                # Load user (custom loader or default)
                if self.user_loader:
                    request.state.user = await self.user_loader(payload)
                else:
                    request.state.user = AuthenticatedUser(payload)
```
Plain-English:
- "Assume nobody until proven otherwise — start with no user."
- "(comment) Look for a token on the request."
- "Check the request for an Authorization header."
- "If it starts with the word Bearer, the token follows it…"
- "…snip off the Bearer prefix to get the raw token."
- "Try to open and verify the wristband…"
- "…decode it and check the signature and expiry."
- "If a custom user-lookup is set, use it to fetch the full user…"
- "…otherwise build a lightweight user straight from the token facts. Either way, `request.state.user` is now set for everything downstream."

### Snippet C — a permission check (the guest list). File: `zeeb_api/permissions/base.py` (lines 68-72)
```python
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        # Check if user is authenticated
        # FastAPI stores user in request.state after authentication
        user = getattr(request.state, "user", None)
        return user is not None and getattr(user, "is_authenticated", True)
```
Plain-English:
- "This yes/no gate runs before the action."
- "(comments) It looks at who the bouncer already identified."
- "…"
- "Grab the user the middleware attached (or nothing, if none)."
- "Allow the action only if there *is* a user and they count as logged in. No wristband → no entry."

## Interactive Elements (build ALL of these)
- [x] **Code↔English translation** — Snippet A (minting the token) and Snippet C (the permission gate). Two blocks. Snippet B can be a third translation or a styled code block.
- [x] **Flow / data-flow animation** — an authenticated request is the hero visual. Actors (`flow-actor-1`…`flow-actor-5`): 1 = "Browser (with wristband)" 🎟️, 2 = "Middleware (bouncer)" 💂, 3 = "Permission check (guest list)" 📋, 4 = "ViewSet (the action)" ⚙️, 5 = "Database" 🗄️. Steps (NO apostrophes in labels):
  1. `{"highlight":"flow-actor-1","label":"Browser sends DELETE /books/5/ with its token in the header"}`
  2. `{"highlight":"flow-actor-1","label":"The request hits the bouncer first","packet":true,"from":"actor-1","to":"actor-2"}`
  3. `{"highlight":"flow-actor-2","label":"The bouncer decodes the token and checks the seal and expiry"}`
  4. `{"highlight":"flow-actor-2","label":"Valid — it attaches the user to the request","packet":true,"from":"actor-2","to":"actor-3"}`
  5. `{"highlight":"flow-actor-3","label":"The guest list asks: is this user allowed to delete?"}`
  6. `{"highlight":"flow-actor-3","label":"Allowed — pass it on","packet":true,"from":"actor-3","to":"actor-4"}`
  7. `{"highlight":"flow-actor-4","label":"The viewset performs the delete","packet":true,"from":"actor-4","to":"actor-5"}`
  8. `{"highlight":"flow-actor-5","label":"The row is removed and a 204 No Content goes back"}`
  (Add a note in the surrounding text: if the token were missing or expired, the bouncer stops it at step 3 and the browser gets a 401 Unauthorized; if the user lacked permission, the guest list stops it at step 5 with a 403 Forbidden.)
- [x] **Quiz** — 3 questions, security/decision style:
  1. Debugging: "A logged-out visitor calls `DELETE /books/5/` and gets `401 Unauthorized`. Which guard stopped them?" (correct: the middleware/permission check — no valid token means no attached user.)
  2. Decision: "Why hand out a signed token instead of just trusting a `user_id` the browser sends?" (correct: the browser could lie and send any id; a signed token cannot be forged without the server secret.)
  3. Application: "You want to stop one client from hammering your API 10,000 times a minute. Which of the four guards is that?" (correct: throttling — the capacity limit.)
- [x] **Callout (callout-warning)** — the insecure-secret safety net: zeebpy **refuses to start** with a default/insecure signing secret when `DEBUG=false`. If anyone could guess the secret, they could forge wristbands for *any* user. Never ship a default secret to production.
- [x] **Pattern/feature cards (optional, nice here)** — the four guards as four cards: **Authentication** (who are you? — token), **Middleware** (the bouncer, runs on every request), **Permissions** (what may you do? — guest list), **Throttling** (how often? — capacity). Use `--color-actor-*` for the card top borders.
- [x] **Glossary tooltips (first use, this module):** authentication, authorization, JWT / token, middleware, `request.state`, signature / signed, secret key, permission, throttling / rate limiting, header, Bearer token, expiry, 401 / 403, `DEBUG` flag.

## Reference Files to Read
- `references/interactive-elements.md` → "Code ↔ English Translation Blocks", "Message Flow / Data Flow Animation", "Multiple-Choice Quizzes", "Callout Boxes", "Pattern/Feature Cards", "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", "Syntax Highlighting"

## Connections
- **Previous module:** Module 5 built the request/response spine. Open: "You can now create and delete books over the web. That is exactly why we need a lock on the door."
- **Next module:** Module 7, "The Robot Helpers" (`zeeb_agents`). End: "One layer left — the helper functions an AI assistant uses to build all of this *for* you, safely."
- **Tone/style notes:** Accent = vermillion. API = actor-2. Non-technical audience; ≥50% visual; short text blocks.
