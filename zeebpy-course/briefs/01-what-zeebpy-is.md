# Module 1: What zeebpy Is — and What Happens When You Save One Record

Write to: `modules/01-what-zeebpy-is.html`
Section wrapper: `<section class="module" id="module-1" style="background: var(--color-bg-warm)">` (odd module = warm background)

## Teaching Arc
- **Metaphor:** A **simultaneous interpreter** at the United Nations. You speak Python; the database only speaks SQL. zeebpy sits in the booth and translates, live, in both directions — so you never have to learn the database's language. (Do NOT use restaurant/kitchen.)
- **Opening hook:** "You write one line — `await User.objects.create(name='Alice', ...)` — no SQL, no database commands. Here's the journey that single line takes under the hood."
- **Key insight:** zeebpy is a **framework** — a translation layer that lets you describe *what* you want in plain Python while it figures out *how* to talk to the database. You declare; it does the plumbing.
- **"Why should I care?":** Knowing there's a translation layer means you can (1) tell an AI assistant the right thing ("use the ORM, don't hand-write SQL"), (2) debug when the translation misbehaves, and (3) reason about speed — every Python call becomes real database work.

## What this module must cover (product intro first!)
This is Module 1, so OPEN by explaining what zeebpy *is* in plain language before diving into code:
- zeebpy is a **Django-like async framework** for building the back end of a web app. You write Python classes describing your data; zeebpy gives you a **database** and a ready-made **REST API** (a web address other programs can call) almost for free.
- It comes in three packages (name them one line each, they're the subject of Module 2): `zeeb_orm` (talks to the database), `zeeb_api` (turns your data into a web API), `zeeb_agents` (helper functions an AI can call). Keep this to a teaser — Module 2 goes deep.
- Use an **icon-label rows** element for "what you get" (a database, a REST API, migrations, auth) — do not write it as a paragraph.

## Code Snippets (pre-extracted — use verbatim, do NOT re-read the codebase)

### Snippet A — defining a model. File: `example_basic.py` (lines 20-31)
```python
class User(Model):
    """User model with basic fields."""
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    age = fields.IntegerField(null=True)
    is_active = fields.BooleanField(default=True)
    bio = fields.TextField(null=True)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "users"
        ordering = ["-created_at"]
```
Plain-English translation (right side, one line per code line, explain the WHY):
- "Describe a kind of thing we want to store — a User." 
- (docstring) "A human-readable note about what this is."
- "Every user has a name — up to 100 letters long."
- "…and an email, which must be unique — no two users can share one."
- "…and an age, which is optional (`null=True` means 'can be blank')."
- "…and an on/off switch for whether the account is active — on by default."
- "…and a free-text bio, also optional."
- "…and a timestamp that fills itself in the moment the user is created."
- "`Meta` is a settings panel for this model."
- "Store these in a database table literally named 'users'."
- "When listing users, show newest first (the minus means 'descending')."

### Snippet B — creating a record. File: `example_basic.py` (lines 56-61)
```python
alice = await User.objects.create(
    name="Alice Smith",
    email="alice@example.com",
    age=28,
    bio="Software engineer"
)
```
Plain-English translation:
- "Ask the User 'front desk' (`objects`) to create a new user, and wait for it to finish (`await`)."
- "Her name is Alice Smith."
- "Her email is alice@example.com."
- "She's 28."
- "Her bio says 'Software engineer.' zeebpy writes all of this into the database as one new row."

## Interactive Elements (build ALL of these)
- [x] **Code↔English translation** — Snippet A and Snippet B (two separate translation blocks).
- [x] **Message-flow / data-flow animation** — the hero visual of this module. 4 actors, showing the journey of `create()`:
  - Actors (use `id="flow-actor-1"`…`flow-actor-4"`): 1 = "Your Python code" 🐍, 2 = "zeebpy ORM" 🔄, 3 = "SQL" 📝, 4 = "Database" 🗄️.
  - Steps (remember: NO apostrophes inside `data-steps` labels — they break JSON parsing; reword to avoid them):
    1. `{"highlight":"flow-actor-1","label":"You call User.objects.create(name=Alice)"}`
    2. `{"highlight":"flow-actor-1","label":"Your Python code hands the request to the ORM","packet":true,"from":"actor-1","to":"actor-2"}`
    3. `{"highlight":"flow-actor-2","label":"The ORM looks up the table blueprint it built for User"}`
    4. `{"highlight":"flow-actor-2","label":"It translates your Python into a SQL command","packet":true,"from":"actor-2","to":"actor-3"}`
    5. `{"highlight":"flow-actor-3","label":"INSERT INTO users (name, email, age) VALUES (Alice, ...)","packet":true,"from":"actor-3","to":"actor-4"}`
    6. `{"highlight":"flow-actor-4","label":"The database saves a new row and hands back the fresh record"}`
- [x] **Quiz** — 3 questions, scenario/tracing style. Suggested angles:
  1. Tracing: "You ran `User.objects.create(...)` but never see the SQL. Where did the SQL come from?" (correct: zeebpy generated it for you — that is the ORM's whole job.)
  2. Decision: "You want the database to reject two users with the same email. Which part of the model handles that?" (correct: `unique=True` on the email field.)
  3. Application: "An AI assistant offers to 'write raw SQL INSERT statements' for saving users. Is that a good idea here?" (correct: no — the ORM already does this; hand-written SQL bypasses validation and the translation layer.)
- [x] **Callout (callout-accent)** — "Declarative vs. plumbing": you *declared* what a User looks like; you never wrote code to open a database connection, build a SQL string, or handle types. That gap is exactly what a framework fills.
- [x] **Glossary tooltips (first use, this module):** framework, ORM, SQL, database, table, row, REST API, async, `await`, class, field, model, primary key, validation.

## Reference Files to Read
- `references/interactive-elements.md` → "Code ↔ English Translation Blocks", "Message Flow / Data Flow Animation", "Multiple-Choice Quizzes", "Callout Boxes", "Icon-Label Rows", "Glossary Tooltips"
- `references/content-philosophy.md` → all of it (content rules)
- `references/gotchas.md` → all of it (checklist)
- `references/design-system.md` → "Module Structure", "Syntax Highlighting" (only if you need token/class names)

## Connections
- **Previous module:** none — this is the opener. Set the stage for the whole course.
- **Next module:** Module 2, "The Three Packages" — you mentioned the 3 packages here; end Module 1 by teasing "you just met the ORM in action — but it has two siblings. Let us meet the whole family."
- **Tone/style notes:** Accent = vermillion. Actor colors: ORM = `--color-actor-1` (vermillion), database/SQL can use `--color-actor-4`/`--color-actor-2`. Audience = non-technical "vibe coders," zero CS background; tone of a smart friend. Every screen ≥50% visual, max 2-3 sentences per text block.
