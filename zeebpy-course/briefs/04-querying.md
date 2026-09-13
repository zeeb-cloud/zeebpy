# Module 4: Asking the Database Questions (QuerySets → SQL)

Write to: `modules/04-querying.html`
Section wrapper: `<section class="module" id="module-4" style="background: var(--color-bg)">` (even module = plain background)

## Teaching Arc
- **Metaphor:** **Refining filters on a shopping site before you press "Show results."** You tick "under $30," then "in stock," then "sort by newest" — nothing loads yet. The page only fetches results when you finally hit the button. A zeebpy **QuerySet** works exactly like that: you stack up conditions, and the database is only asked when you `await` it. (Do NOT reuse earlier metaphors.)
- **Opening hook:** "`User.objects.filter(age__lt=30)` — you have not asked the database anything yet. You have only *written down the question*. It runs the moment you `await` it, and not a nanosecond before."
- **Key insight:** QuerySets are **lazy** (they do nothing until awaited) and **chainable** (each `.filter()` returns a brand-new QuerySet, leaving the old one untouched). A lookup like `age__lt=30` is translated into the SQL `WHERE age < 30`. `Q` objects let you combine conditions with AND (`&`), OR (`|`), and NOT (`~`).
- **"Why should I care?":** Lazy evaluation explains the classic "why is my data not showing up?" bug (you forgot `await`, so the question never ran). Knowing the filter syntax lets you tell an AI *exactly* what to fetch. And understanding that each filter is a database query helps you avoid slow, wasteful code.

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — filters in action. File: `example_basic.py` (lines 91-99)
```python
    # Filter users
    active_users = await User.objects.filter(is_active=True)

    # Filter with lookups
    young_users = await User.objects.filter(age__lt=30)

    adults = await User.objects.filter(age__gte=25, age__lte=40)
```
Plain-English (emphasize the lookup → SQL translation):
- "(comment) Find users matching a condition."
- "Ask for users whose `is_active` switch is on → SQL: WHERE is_active = true."
- "(comment) Conditions can be comparisons, not just equals."
- "`age__lt=30` means 'age less than 30' → SQL: WHERE age < 30. The `__lt` is the magic word for 'less than.'"
- "(comment) You can combine conditions." 
- "`age__gte=25, age__lte=40` means 'at least 25 AND at most 40' → SQL: WHERE age >= 25 AND age <= 40."

### Snippet B — why it is lazy: filter returns a *clone*. File: `zeeb_orm/query/queryset.py` (lines 153-161)
```python
        self._check_combinator("filter")
        clone = self._clone()
        if args:
            for arg in args:
                if isinstance(arg, Q):
                    clone._filters.append(arg)
        if kwargs:
            clone._filters.append(Q(**kwargs))
        return clone
```
Plain-English (this is why nothing runs yet):
- "First a safety check that filtering is allowed here."
- "Make a fresh copy of the question — never modify the original."
- "If you passed any Q objects…"
- "…go through them one by one…"
- "…and add each as a new condition on the copy."
- "If you passed plain keywords like age__lt=30…"
- "…bundle them into a condition and add that too."
- "Hand back the *copy*. Notice: it talked to no database. It just remembered your condition and returned a new question."

### Snippet C — the moment it actually runs. File: `zeeb_orm/query/queryset.py` (lines 1350-1352)
```python
    def __await__(self) -> Any:
        """Allow awaiting QuerySet directly to get list of results."""
        return self._fetch_all().__await__()
```
Plain-English:
- "This special method fires only when you put `await` in front of the query."
- "(the note) Awaiting the query is what turns it into a real list of results."
- "*Now* — and only now — it runs `_fetch_all`, which sends the SQL to the database and brings back the rows."

### Snippet D — combining conditions with Q. File: `zeeb_orm/query/q.py` (lines 22-27, from the docstring)
```
    Q(name='John')
    Q(age__gte=18) & Q(age__lte=65)
    Q(status='active') | Q(status='pending')
    ~Q(deleted=True)
```
Present as small labeled examples (a "badge-list" or styled snippet): `&` = AND, `|` = OR, `~` = NOT.

## Interactive Elements (build ALL of these)
- [x] **Code↔English translation** — Snippet A (lookups → SQL) and Snippet B (the clone/lazy mechanism). Two blocks.
- [x] **"Spot the Bug" challenge** — THIS MODULE HOSTS THE SPOT-THE-BUG. Show a short snippet where the learner clicks the buggy line. Use this 4-line example (the classic missing-`await`):
  ```
  young = User.objects.filter(age__lt=30)
  print("Found:", len(young))
  for u in young:
      print(u.name)
  ```
  The buggy line is line 1 (`bug-target`): there is no `await`, so `young` is an unexecuted question, not a list — `len()` and the loop then misbehave. Feedback on correct: "Found it — without `await`, the query never runs. `young` is still just the *question*. Fix: `young = await User.objects.filter(age__lt=30)`." Wrong-line feedback: gentle nudge toward "where does the query actually talk to the database?"
- [x] **Flow / data-flow animation (optional but recommended)** — show a filter chain building up, then executing on `await`. Actors: 1 = "filter(is_active=True)", 2 = "filter(age__lt=30)", 3 = "await → SQL sent", 4 = "Database returns rows". OR skip if the spot-the-bug + translations already make the module rich — but you MUST have at least one animation OR the spot-the-bug plus the quiz to satisfy interactivity. (Spot-the-bug counts as interactive; a flow animation is a bonus. Prefer including it.)
- [x] **Quiz** — 3 questions, debugging/tracing style:
  1. Debugging: "You wrote `users = User.objects.filter(is_active=True)` and got an empty-looking object instead of a list of users. What is most likely wrong?" (correct: missing `await` — the query is lazy and never executed.)
  2. Tracing: "`User.objects.filter(age__gte=18).filter(is_active=True)` — how many database queries does building this chain run?" (correct: zero, until you await it; then one.)
  3. Application: "You want 'admins OR anyone over 65.' Which tool combines those two conditions?" (correct: a `Q` object with `|` — `Q(role='admin') | Q(age__gte=65)`.)
- [x] **Callout (callout-accent)** — "Lazy evaluation": building a query costs nothing; only `await` (or looping) triggers the database. This is a deliberate design so you can pass unfinished queries around, add filters, and run them once at the end.
- [x] **Glossary tooltips (first use, this module):** QuerySet, lazy evaluation, chainable / method chaining, lookup, `WHERE` clause, `Q` object, clone / immutable, execute a query, keyword argument.

## Reference Files to Read
- `references/interactive-elements.md` → "Code ↔ English Translation Blocks", "\"Spot the Bug\" Challenge", "Message Flow / Data Flow Animation" (if you include it), "Multiple-Choice Quizzes", "Callout Boxes", "Permission/Config Badges" (for the Q operators list), "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", "Syntax Highlighting"

## Connections
- **Previous module:** Module 3 built the table. Open: "Your data lives in a table now. Time to ask it questions — without writing SQL."
- **Next module:** Module 5, "From the Database to the Web" — queries feed the API. End: "So far this all runs *inside* your program. Next: how does a stranger on the internet trigger one of these queries and get JSON back?"
- **Tone/style notes:** Accent = vermillion. ORM = actor-1. Non-technical audience; ≥50% visual; short text blocks.
