# demo_blog

A Zeeb project.

## Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Initialize and run migrations (REQUIRED before starting)
python manage.py init           # Initialize migrations folder
python manage.py makemigrations  # Create migrations for all models
python manage.py migrate         # Apply migrations to database
```

## Running

```bash
# Run development server
python manage.py runserver

# Or use zeeb-manage from anywhere in the project
zeeb-manage runserver

# Or with uvicorn directly
uvicorn demo_blog.asgi:app --reload
```

## Management Commands

All commands work with `python manage.py`, `zeeb-manage`, or `zeeb`:

```bash
# Create a new app
zeeb-manage startapp myapp

# Initialize migrations (first time only)
zeeb-manage init

# Create migrations after model changes
zeeb-manage makemigrations

# Apply migrations
zeeb-manage migrate

# Check project configuration
zeeb-manage check

# Create a superuser
zeeb-manage createsuperuser

# Show migration status
zeeb-manage showmigrations

# Rollback migrations
zeeb-manage migrate --rollback 1

# Interactive shell
zeeb-manage shell
```

## Project Structure

```
demo_blog/
├── manage.py
├── demo_blog/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
├── apps/
│   └── (your apps here)
├── migrations/
└── requirements.txt
```
