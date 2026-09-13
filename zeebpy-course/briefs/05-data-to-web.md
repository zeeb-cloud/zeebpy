# Module 5: From the Database to the Web (Serializers + ViewSets + Routers)

Write to: `modules/05-data-to-web.html`
Section wrapper: `<section class="module" id="module-5" style="background: var(--color-bg-warm)">` (odd module = warm background)

## Teaching Arc
- **Metaphor:** An **airport**. The **router** is the terminal signage that reads the address on your ticket and sends you to the right gate. The **viewset** is the gate agent who handles everything for your specific flight (create, read, update, delete). The **serializer** is **passport control / customs** — it inspects everything coming *in* (is this valid data?) and everything going *out* (turn the database row into clean JSON). (Do NOT reuse earlier metaphors.)
- **Opening hook:** "When someone visits `POST /books/` from their browser or app, how does that become a new row in your database — and a tidy JSON reply on the way back? Follow the request through the airport."
- **Key insight:** A web request flows **Browser → Router → ViewSet → Serializer → ORM → Database**, and the response flows back the same way. You define a serializer and a viewset, register them with a router, and zeebpy generates all the standard CRUD web addresses for you.
- **"Why should I care?":** This is the request/response spine of every web app. Debugging becomes targeted: a `422` error → the serializer rejected your data; a `404` → the router did not recognize the address; wrong fields in the JSON → the serializer's field list. Knowing the layers tells you *where* to look and *what* to ask an AI to change.

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — the serializer (passport control). File: `example_api.py` (lines 108-116)
```python
class BookSerializer(ModelSerializer):
    """Serializer for Book model."""
    author_name = SerializerMethodField()

    class Meta:
        model = Book
        fields = ["id", "title", "author_id", "author_name", "published_year",
                  "genre", "price", "in_stock"]
        read_only_fields = ["id"]
```
Plain-English (WHY each line):
- "Define how a Book is checked and shaped as it enters or leaves the web."
- "(docstring) A note about what this does."
- "Add an extra computed field — a friendly author name that is not stored directly."
- "(Meta) The settings panel for this serializer."
- "It is based on the Book model from the database."
- "These are the only fields allowed through the gate — anything else is ignored."
- "(continued field list)…"
- "`id` is read-only — clients can see it but never set it themselves."

### Snippet B — the viewset (the gate agent). File: `example_api.py` (lines 172-179)
```python
class BookViewSet(ModelViewSet):
    """
    ViewSet for Book CRUD operations.

    Query with Q filters via POST /books/query/
    """
    queryset = Book.objects
    serializer_class = BookSerializer
```
Plain-English:
- "Define the handler for everything you can do to Books over the web."
- "(docstring lines) It provides full create/read/update/delete out of the box, plus a query endpoint."
- "…"
- "…"
- "…"
- "Point it at the Book data (which set of rows it manages)."
- "Tell it which serializer to use for checking and shaping data. That is all — the CRUD web addresses come for free."

### Snippet C — the router (the terminal signage). File: `example_api.py` (lines 206-208)
```python
router = DefaultRouter()
router.register("authors", AuthorViewSet, basename="author")
router.register("books", BookViewSet, basename="book")
```
Plain-English:
- "Create a router — the thing that maps web addresses to handlers."
- "Register the Author handler under the `/authors/` addresses."
- "Register the Book handler under the `/books/` addresses. From these two lines, zeebpy generates GET/POST/PUT/PATCH/DELETE routes automatically."

## Interactive Elements (build ALL of these)
- [x] **Message-flow / data-flow animation** — THIS MODULE HOSTS THE MANDATORY MESSAGE-FLOW. The full round trip is the hero visual. Actors (`flow-actor-1`…`flow-actor-6`): 1 = "Browser / App" 🌐, 2 = "Router" 🪧, 3 = "ViewSet" 🧑‍✈️, 4 = "Serializer" 🛂, 5 = "ORM" 🔄, 6 = "Database" 🗄️. Steps (NO apostrophes in labels):
  1. `{"highlight":"flow-actor-1","label":"Browser sends POST /books/ with a new book"}`
  2. `{"highlight":"flow-actor-1","label":"The request reaches the router","packet":true,"from":"actor-1","to":"actor-2"}`
  3. `{"highlight":"flow-actor-2","label":"The router reads the address and picks the Book viewset","packet":true,"from":"actor-2","to":"actor-3"}`
  4. `{"highlight":"flow-actor-3","label":"The viewset hands the data to the serializer to check it","packet":true,"from":"actor-3","to":"actor-4"}`
  5. `{"highlight":"flow-actor-4","label":"Passport control: is the data valid? If yes, it passes through","packet":true,"from":"actor-4","to":"actor-5"}`
  6. `{"highlight":"flow-actor-5","label":"The ORM turns it into SQL and saves it","packet":true,"from":"actor-5","to":"actor-6"}`
  7. `{"highlight":"flow-actor-6","label":"The database stores the new row and returns it"}`
  8. `{"highlight":"flow-actor-4","label":"On the way out, the serializer shapes the row into clean JSON"}`
  9. `{"highlight":"flow-actor-1","label":"The browser receives 201 Created with the new book as JSON"}`
- [x] **Group-chat animation** — THIS MODULE HOSTS A SECOND GROUP CHAT (required across course; this and Module 2 satisfy it). Unique `id` (e.g. `chat-request`). Frame it as the actors handling one `GET /books/1/`:
  1. Router (actor-2): "Address is /books/1/ — that is the Book viewset, action: retrieve item 1."
  2. ViewSet (actor-4 or a distinct color): "Got it. ORM, fetch book number 1 please."
  3. ORM (actor-1): "SELECT * FROM books WHERE id = 1 … here is the row."
  4. Serializer (use actor-5 or actor-3): "Let me strip out anything private and format the rest as JSON."
  5. Router (actor-2): "Sending 200 OK back to the browser with the JSON."
  (Keep avatar colors consistent; ORM stays actor-1, API/router stays actor-2.)
- [x] **Code↔English translation** — Snippet A (serializer) and Snippet C (router). Two blocks. (Snippet B can be shown as a small styled code block or a third translation if space allows.)
- [x] **Quiz** — 3 questions, debugging/architecture style:
  1. Debugging: "Your API returns `422 Unprocessable Entity` when a user submits a new book. Which layer rejected it, and why?" (correct: the serializer — the incoming data failed validation, e.g. a missing or wrong-typed field.)
  2. Architecture: "You registered `BookViewSet` under `router.register('books', ...)`. Without writing any more code, which web addresses now exist?" (correct: the standard CRUD set — list/create at `/books/`, retrieve/update/delete at `/books/{id}/` — generated automatically.)
  3. Application: "You want the API response to hide a book's internal `cost` field but keep showing `price`. Where do you make that change?" (correct: the serializer's `fields` list — it controls exactly what goes out.)
- [x] **Callout (callout-accent)** — "One definition, many endpoints": from a serializer + a viewset + one `register()` line, zeebpy generates a whole family of web addresses. That is the payoff of a framework — you describe the shape, it builds the doors.
- [x] **Glossary tooltips (first use, this module):** serializer, viewset, router, endpoint / route, CRUD, HTTP request, JSON, validation, `POST`/`GET`/`PUT`/`PATCH`/`DELETE`, status code (422 / 404 / 201), read-only field.

## Reference Files to Read
- `references/interactive-elements.md` → "Message Flow / Data Flow Animation", "Group Chat Animation", "Code ↔ English Translation Blocks", "Multiple-Choice Quizzes", "Callout Boxes", "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", "Syntax Highlighting"

## Connections
- **Previous module:** Module 4 covered querying. Open: "Those queries ran inside your own program. Now let the outside world trigger them over the web."
- **Next module:** Module 6, "Guarding the Door" — auth. End: "But wait — should *anyone* be allowed to delete any book? Next we put a bouncer on the door."
- **Tone/style notes:** Accent = vermillion. ORM = actor-1, API/router = actor-2, a third color (actor-3/actor-5) for serializer/viewset. Non-technical audience; ≥50% visual per screen.
