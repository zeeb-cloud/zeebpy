# Module 3: From a Python Class to a Database Table

Write to: `modules/03-class-to-table.html`
Section wrapper: `<section class="module" id="module-3" style="background: var(--color-bg-warm)">` (odd module = warm background)

## Teaching Arc
- **Metaphor:** An **architectural blueprint becoming a real building**. Your Python class is the blueprint; the metaclass is the construction crew that reads it the moment the class is defined; the database table is the finished building. **Migrations** are the renovation permits — a dated logbook of every change you make to the building over time.
- **Opening hook:** "In Module 1 you saw `class User(Model)` turn into a real table. But a plain Python class has no idea what a database is. So who actually builds the table — and when?"
- **Key insight:** A special piece of machinery called a **metaclass** (`ModelBase`) runs the instant your class is defined. It scans the class for `fields`, collects them, and assembles the SQL table blueprint. Then **migrations** (`makemigrations` + `migrate`) turn changes to your models into safe, ordered updates to the real database.
- **"Why should I care?":** Migrations are the #1 thing vibe coders get stuck on. "I added a field and the app crashed with 'no such column'" almost always means *you forgot to make and run a migration*. Understanding this loop is the fix.

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — the model (reuse from Module 1, but now explain the columns). File: `example_basic.py` (lines 22-27)
```python
    name = fields.CharField(max_length=100)
    email = fields.EmailField(unique=True)
    age = fields.IntegerField(null=True)
    is_active = fields.BooleanField(default=True)
    bio = fields.TextField(null=True)
    created_at = fields.DateTimeField(auto_now_add=True)
```
Plain-English (each field = one column with a type):
- "`name` becomes a text column, at most 100 characters."
- "`email` becomes a text column that must be unique across the whole table."
- "`age` becomes a whole-number column that is allowed to be empty."
- "`is_active` becomes a true/false column, defaulting to true."
- "`bio` becomes a long-text column, optional."
- "`created_at` becomes a date-and-time column that stamps itself automatically."

### Snippet B — the metaclass collecting fields. File: `zeeb_orm/models/base.py` (lines 98-106)
```python
        # Collect fields from this class
        for attr_name, attr_value in list(namespace.items()):
            if isinstance(attr_value, Field):
                attr_value.contribute_to_class(new_class, attr_name)
                fields.append(attr_value)
                if attr_value.primary_key:
                    has_pk = True
                    new_class._meta.pk = attr_value
                    new_class._meta.pk_name = attr_name
```
Plain-English (this is the "construction crew reading the blueprint"):
- "(comment) Now gather every field defined on the class."
- "Walk through everything written inside the class, one item at a time."
- "If this item is a Field (like CharField or IntegerField)…"
- "…officially register it as a column of the new table…"
- "…add it to the running list of fields."
- "If this field was marked as the primary key…"
- "…remember that the table has one…"
- "…and record which field it is, so the ORM always knows how to find a single row."

### Snippet C — how makemigrations detects changes. File: `zeeb_orm/migrations/autodetector.py` (lines 55-63, from the docstring)
```
Compare current model state against existing migration state and return operations.

This is the core of makemigrations — it detects what changed.

Instead of comparing against the live database (which would re-detect
already-migrated changes if migrations have not been applied yet), this
replays all existing migration files into an in-memory SQLite database
and compares the model metadata against that state.
```
Present this as a plain quote/explanation (it is a docstring, not runnable code) — you may put it in a callout or a styled note block rather than a translation block.

## Interactive Elements (build ALL of these)
- [x] **Code↔English translation** — Snippet A (fields → columns) and Snippet B (the metaclass loop). Two blocks.
- [x] **Flow / data-flow animation** — the migration loop is the hero visual. Actors (`flow-actor-1`…`flow-actor-4`): 1 = "Your models.py" 📄, 2 = "makemigrations (the detective)" 🔍, 3 = "Migration file (the permit)" 📜, 4 = "migrate → Database" 🗄️. Steps (NO apostrophes in labels):
  1. `{"highlight":"flow-actor-1","label":"You add a new field to your model"}`
  2. `{"highlight":"flow-actor-1","label":"You run: zeeb-manage makemigrations","packet":true,"from":"actor-1","to":"actor-2"}`
  3. `{"highlight":"flow-actor-2","label":"The detective compares your models to the last known state"}`
  4. `{"highlight":"flow-actor-2","label":"It writes down exactly what changed","packet":true,"from":"actor-2","to":"actor-3"}`
  5. `{"highlight":"flow-actor-3","label":"A dated migration file is saved — a permanent record"}`
  6. `{"highlight":"flow-actor-3","label":"You run: zeeb-manage migrate","packet":true,"from":"actor-3","to":"actor-4"}`
  7. `{"highlight":"flow-actor-4","label":"The database applies the change — the new column now exists"}`
- [x] **Quiz** — 3 questions, debugging/decision style:
  1. Debugging (the classic): "You added `phone = fields.CharField(...)` to your model, restarted the app, and now every request errors with 'no such column: phone.' What did you forget?" (correct: run `makemigrations` then `migrate` — the model changed but the database was never updated.)
  2. Tracing: "In what order do the two commands run, and what does each do?" (correct: `makemigrations` writes down the change into a file; `migrate` applies that file to the real database.)
  3. Decision: "Why keep migration files in your project instead of just re-building the tables from scratch each time?" (correct: they are an ordered history that can be replayed safely on any database — dev, test, production — without losing existing data.)
- [x] **Callout (callout-accent)** — "Declarative vs. imperative": you never wrote `CREATE TABLE`. You *declared* what a User is, and the metaclass figured out the imperative steps. Migrations do the same for *changes*.
- [x] **Callout (callout-warning)** — the "no such column" trap: whenever you change a model, you must `makemigrations` then `migrate`. Forgetting is the single most common beginner bug.
- [x] **Glossary tooltips (first use, this module):** metaclass, primary key, column, data type, migration, `makemigrations`, `migrate`, schema, SQLite, CLI / command, in-memory database.

## Reference Files to Read
- `references/interactive-elements.md` → "Code ↔ English Translation Blocks", "Message Flow / Data Flow Animation", "Multiple-Choice Quizzes", "Callout Boxes", "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", "Syntax Highlighting"

## Connections
- **Previous module:** Module 2 mapped the three packages. Open: "We are on the ground floor now — the Database Brain (`zeeb_orm`). Let us watch a class become a table."
- **Next module:** Module 4, "Asking the Database Questions" — once tables exist, you query them. End: "Your data now lives in a table. Next: how do you *ask questions* of it without writing a word of SQL?"
- **Tone/style notes:** Accent = vermillion. ORM stays actor-1. Non-technical audience; ≥50% visual; max 2-3 sentences per text block.
