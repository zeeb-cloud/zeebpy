# Module 2: The Cast of Characters — Three Packages, One Framework

Write to: `modules/02-three-packages.html`
Section wrapper: `<section class="module" id="module-2" style="background: var(--color-bg)">` (even module = plain background)

## Teaching Arc
- **Metaphor:** A **three-story building with a one-way elevator**. The top floor can call down to the floors below it, but no floor can ever call *up*. Each floor has one job and depends only on the floors beneath it. (Do NOT reuse restaurant/interpreter.)
- **Opening hook:** "zeebpy isn't one giant blob of code — it's three separate packages stacked like floors of a building. And there's one strict rule about who is allowed to call whom."
- **Key insight:** The three packages are **strictly layered**: `zeeb_api` uses `zeeb_orm`; `zeeb_agents` uses both; nothing ever depends *upward*. Each package has a single clear responsibility. Engineers call this **separation of concerns**.
- **"Why should I care?":** When you can name which layer owns what, you can steer an AI precisely ("put this database logic in the ORM layer, not the API layer") and debug faster ("data is wrong → look in the ORM; the web request 404s → look in the API").

## The three characters (give each personality + a color)
- **`zeeb_orm` — the Database Brain** 🧠 (`--color-actor-1`, vermillion). Knows how to talk to the database. Turns Python classes into tables and queries into SQL. Depends on nothing above it.
- **`zeeb_api` — the Web Front Desk** 🏢 (`--color-actor-2`, teal). Faces the outside world. Turns your data into a REST API — web addresses other apps can call. Uses the ORM to fetch/save data.
- **`zeeb_agents` — the Robot Helpers** 🤖 (`--color-actor-3`, plum). ~43 helper functions an AI assistant can call to scaffold code, run migrations, inspect the database. Sits on top and uses both siblings.

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — the layering rule, in one real import block. File: `example_api.py` (lines 24-33, lightly shown)
```python
# Zeeb ORM imports
from zeeb_orm import (
    Model, fields, Q,
    configure, setup_database, close_all_connections,
)

# Zeeb API imports
from zeeb_api import (
    ModelSerializer, ModelViewSet, DefaultRouter,
    ...
)
```
Plain-English translation (show WHY, not just what):
- "The comment marks where the database-layer tools come from."
- "From `zeeb_orm` we borrow the building blocks for data: Model, fields, and query tools."
- (blank) "…"
- (blank) "…"
- "The comment marks the web-layer tools."
- "From `zeeb_api` we borrow the tools that turn that data into a web API."
- "Notice the direction: the API file reaches *down* into the ORM. The ORM never reaches *up* into the API — that is the one-way elevator rule."

(Note to writer: the real import list is longer; it is fine to show this representative slice — keep the lines you show exact. Do NOT invent code.)

## Interactive Elements (build ALL of these)
- [x] **Interactive architecture diagram** — three stacked zones (Robot Helpers on top → Web Front Desk → Database Brain on the bottom), each `arch-component` with a `data-desc`. This is the hero visual. Show the one-way arrows pointing downward.
- [x] **Group-chat animation** — THIS MODULE HOSTS THE MANDATORY GROUP CHAT. Give the `.chat-window` a unique `id` (e.g. `chat-packages`). Frame it as the three packages introducing themselves when a web request arrives. Suggested message order (sender → text):
  1. Web Front Desk (`zeeb_api`, actor-2): "Someone just asked for a list of books. I handle the web side — but I do not touch the database myself."
  2. Web Front Desk → Database Brain: "Hey ORM, can you fetch all the books?"
  3. Database Brain (`zeeb_orm`, actor-1): "On it. I will turn that into SQL, ask the database, and hand you back the results."
  4. Database Brain: "Here you go — 12 books."
  5. Web Front Desk: "Thanks. I will package these as JSON and send them back out to the browser."
  6. Robot Helper (`zeeb_agents`, actor-3): "And when you build a brand-new app, I am the one who scaffolds these files for you — I sit on top and use you both."
  - Use `--color-actor-1/2/3` for the three avatars consistently.
- [x] **Code↔English translation** — Snippet A (the import block showing the one-way dependency).
- [x] **Quiz** — 3 questions, architecture/debugging style:
  1. Debugging: "Your API returns the *wrong data* — the numbers are off, but the web request itself works fine. Which layer do you investigate first?" (correct: the ORM / database layer.)
  2. Architecture: "Could `zeeb_orm` import something from `zeeb_api`?" (correct: no — that would break the one-way rule; lower layers never depend on higher ones.)
  3. Application: "You ask an AI to add a rate limit to your web endpoints. Which package should that live in?" (correct: `zeeb_api`, the web layer.)
- [x] **Callout (callout-accent)** — "Separation of concerns": splitting a system into layers that each do one job — and only depend downward — is one of the most important ideas in software. It is why you can change how data is stored without touching how the web API looks.
- [x] **Glossary tooltips (first use, this module):** package, dependency / depend on, import, layer, separation of concerns, JSON, REST API, scaffold, module.

## Reference Files to Read
- `references/interactive-elements.md` → "Interactive Architecture Diagram", "Group Chat Animation", "Code ↔ English Translation Blocks", "Multiple-Choice Quizzes", "Callout Boxes", "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", actor colors (only if needed)

## Connections
- **Previous module:** Module 1 showed the ORM saving a record. Open by saying "you already met one of the three — the ORM. Here is the whole family."
- **Next module:** Module 3, "From a Python Class to a Database Table" — dives into the Database Brain (`zeeb_orm`). End by handing off: "Let us go down to the ground floor — the Database Brain — and see how a plain Python class becomes a real table."
- **Tone/style notes:** Accent = vermillion. Keep the three actor colors consistent for the rest of the course: ORM = actor-1, API = actor-2, agents = actor-3. Non-technical audience; ≥50% visual per screen.
