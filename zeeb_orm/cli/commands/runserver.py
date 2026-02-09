"""runserver command - Start FastAPI development server."""

import sys
import os
from pathlib import Path


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()
    
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    
    return None


def find_asgi_app(project_root: Path) -> str | None:
    """Find the ASGI application path."""
    # Look for asgi.py in project subdirectory
    for item in project_root.iterdir():
        if item.is_dir() and (item / "asgi.py").exists():
            return f"{item.name}.asgi:app"
    return None


def run_server(addrport: str, reload: bool) -> int:
    """Start the development server using uvicorn."""
    project_root = find_project_root()
    
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        return 1
    
    # Find ASGI app
    asgi_app = find_asgi_app(project_root)
    if asgi_app is None:
        print("Error: Could not find ASGI application")
        print("Expected: <project_name>/asgi.py with 'app' variable")
        return 1
    
    # Parse host:port
    if ":" in addrport:
        host, port_str = addrport.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            print(f"Error: Invalid port number: {port_str}")
            return 1
    else:
        try:
            port = int(addrport)
            host = "127.0.0.1"
        except ValueError:
            host = addrport
            port = 8000
    
    # Add project root to path
    sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    
    print(f"Starting development server at http://{host}:{port}/")
    print(f"ASGI app: {asgi_app}")
    print("Quit with CTRL+C")
    print()
    
    try:
        import uvicorn
        
        uvicorn.run(
            asgi_app,
            host=host,
            port=port,
            reload=reload,
            reload_dirs=[str(project_root)] if reload else None,
        )
        return 0
        
    except ImportError:
        print("Error: uvicorn is not installed")
        print("Install it with: pip install uvicorn[standard]")
        return 1
    except KeyboardInterrupt:
        print("\nServer stopped")
        return 0
    except Exception as e:
        print(f"Error starting server: {e}")
        return 1
