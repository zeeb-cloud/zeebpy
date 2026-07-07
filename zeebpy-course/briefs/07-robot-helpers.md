# Module 7: The Robot Helpers (zeeb_agents + Its Clever Patterns)

Write to: `modules/07-robot-helpers.html`
Section wrapper: `<section class="module" id="module-7" style="background: var(--color-bg-warm)">` (odd module = warm background)

## Teaching Arc
- **Metaphor:** A **help desk where every request comes back on the same standard form** — and no request is ever allowed to blow up in your face. You never get a stack of confusing errors thrown at you; you always get one tidy result slip saying "done ✓ / not done ✗, here is why, here is the data." (Do NOT reuse earlier metaphors.)
- **Opening hook:** "zeebpy ships about 43 helper functions that an AI assistant can call to scaffold apps, run migrations, and inspect your database. Every single one of them obeys two unbreakable rules — and those rules are a masterclass in defensive design."
- **Key insight:** (1) **Uniform result:** every function returns the same `AgentResult(success, message, data)` shape. (2) **Never raises:** a decorator (`@agent_function`) wraps each function so that *any* error is caught and turned into a friendly failed result instead of crashing the caller. Plus: it is **MCP-ready with zero MCP dependency**, and dangerous operations are gated (read-only SQL, no escaping the project folder).
- **"Why should I care?":** This is *how AI agents safely operate on a codebase.* The "always return a result object, never throw" pattern is something you can explicitly ask an AI to build for you. And understanding a tool's fixed contract is what makes AI tool-calling (MCP) reliable instead of flaky.

## Code Snippets (pre-extracted — use verbatim)

### Snippet A — the decorator that guarantees "never raise." File: `zeeb_agents/_utils/decorators.py` (lines 70-100)
```python
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> AgentResult:
            try:
                if needs_root:
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    given = bound.arguments.get("project_root")
                    if isinstance(given, str):
                        given = Path(given)
                    try:
                        bound.arguments["project_root"] = require_project_root(given)
                    except RuntimeError as exc:
                        logger.info("%s failed: %s", fn.__qualname__, exc)
                        return fail(str(exc), code="no_project_root")
                    args, kwargs = bound.args, bound.kwargs
                return await fn(*args, **kwargs)
            except AgentError as exc:
                # Expected, targeted failure — no traceback needed.
                logger.info("%s failed: %s", fn.__qualname__, exc)
                return exc.result
            except FileNotFoundError as exc:
                logger.exception("%s failed", fn.__qualname__)
                return fail(f"FileNotFoundError: {exc}", code="file_not_found")
            except PermissionError as exc:
                logger.exception("%s failed", fn.__qualname__)
                return fail(f"PermissionError: {exc}", code="permission_denied")
            except Exception as exc:
                logger.exception("%s failed", fn.__qualname__)
                return AgentResult(
                    success=False, message=f"{type(exc).__name__}: {exc}"
                )
```
Plain-English (the safety wrapper — do NOT translate every line; group them):
- "Wrap the real function so callers get its name and docs, but our safety net around the body."
- "`try` = attempt the real work, ready to catch anything that goes wrong."
- "(the needs_root block) First, resolve and sanity-check the project folder; if that fails, return a clean failure — do not crash."
- "Run the actual function and return its result."
- "If it raised an *expected* error, return the friendly failure it prepared."
- "If a file was missing, return a 'file not found' result — not a crash."
- "If a permission was denied, return a 'permission denied' result."
- "And the catch-all: *any* other error at all becomes `AgentResult(success=False, ...)`. Nothing ever escapes as an exception."

### Snippet B — the uniform return shape (describe, do not need real code)
Every function returns `AgentResult(success, message, data)`:
- `success` — did it work? (true/false)
- `message` — a human-readable sentence.
- `data` — a dictionary of structured results (may be empty).
Show this as a small labeled diagram or badge-list, not a paragraph.

## Interactive Elements (build ALL of these)
- [x] **Code↔English translation** — Snippet A (the try/except safety wrapper). One rich block; keep the code exact but the English grouped/plain.
- [x] **Pattern / feature cards** — the clever tricks, one card each (this is a great fit for this module). Suggested cards (use `--color-actor-*` top borders):
  1. **Uniform Result** 📋 — "Every function hands back the same slip: success, message, data. Callers never guess the shape."
  2. **Never Raises** 🛡️ — "A decorator catches every error and converts it to a failed result. One bad call cannot crash the whole agent."
  3. **Zero MCP Dependency** 🔌 — "Ready to plug into an AI tool protocol (MCP), but needs none of it installed. You can import and call these from plain Python."
  4. **Read-Only SQL Gate** 🔒 — "The query helper strips comments, allows a single statement, blocks dangerous keywords, and always rolls back — so an AI can *look* but never *break*."
  5. **No Escaping the Project** 🚧 — "File helpers reject any path that tries to climb out of the project folder."
- [x] **Quiz** — 3 questions, application/design style:
  1. Application: "You are designing your own set of tools for an AI to call. What is one rule from zeebpy worth copying?" (correct: make every tool return a uniform result object and never throw — so one failure cannot crash the agent. Both 'uniform return' and 'never raise' are good answers.)
  2. Reasoning: "Why does the SQL helper *always roll back* and block keywords like DROP?" (correct: so an AI exploring the database can read safely but can never delete or alter data — read-only by construction.)
  3. Tracing: "An agent function hits an unexpected error deep inside. What does the *caller* receive?" (correct: a normal `AgentResult` with `success=False` and a message — never an exception/crash.)
- [x] **Callout (callout-accent)** — "Defensive design": by *guaranteeing* the return shape and *guaranteeing* no exceptions escape, zeebpy turns 43 different functions into one predictable, safe contract. Predictability is what makes automation trustworthy.
- [x] **Course recap** — this is the FINAL module. End with a short victory-lap visual (a horizontal `flow-steps` diagram or step-cards) retracing the whole journey: **Python class → database table → query → web API → auth → agents.** Close with one encouraging line about how the learner can now steer an AI across all these layers with the right vocabulary.
- [x] **Glossary tooltips (first use, this module):** decorator, exception / raise, `try`/`except`, return value, `AgentResult`, MCP (Model Context Protocol), dependency, roll back / rollback, keyword denylist, sanitize, contract (software), scaffolding.

## Reference Files to Read
- `references/interactive-elements.md` → "Code ↔ English Translation Blocks", "Pattern/Feature Cards", "Multiple-Choice Quizzes", "Callout Boxes", "Flow Diagrams" or "Numbered Step Cards" (for the recap), "Glossary Tooltips"
- `references/content-philosophy.md` → all
- `references/gotchas.md` → all
- `references/design-system.md` → "Module Structure", "Syntax Highlighting"

## Connections
- **Previous module:** Module 6 covered auth. Open: "You have seen the whole framework a human uses. Now meet the layer an *AI* uses to build it — safely."
- **Next module:** none — this is the finale. Do the course recap and a warm sign-off.
- **Tone/style notes:** Accent = vermillion. agents = actor-3. Non-technical audience; ≥50% visual; short text blocks. This module should feel like a satisfying conclusion.
