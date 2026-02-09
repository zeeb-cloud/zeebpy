"""Development server management tools for MCP."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import find_project_root, get_project_name


# Track running server processes
_server_processes: dict[str, subprocess.Popen] = {}


@register_tool(
    name="zeeb_start_server",
    description="Start the development server",
    input_schema={
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port to run on (default: 8000)"},
            "host": {"type": "string", "description": "Host to bind to (default: 127.0.0.1)"},
            "reload": {"type": "boolean", "description": "Enable auto-reload (default: True)"},
            "project_path": {"type": "string", "description": "Project path (optional)"}
        }
    }
)
def zeeb_start_server(
    port: int = 8000,
    host: str = "127.0.0.1",
    reload: bool = True,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Start the development server."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    project_name = get_project_name(root)
    if not project_name:
        return {"success": False, "error": "Could not determine project name"}
    
    # Check if server is already running on this port
    server_key = f"{host}:{port}"
    if server_key in _server_processes:
        proc = _server_processes[server_key]
        if proc.poll() is None:  # Still running
            return {
                "success": False,
                "error": f"Server already running on {server_key}",
                "pid": proc.pid,
            }
        else:
            # Process died, remove it
            del _server_processes[server_key]
    
    # Build uvicorn command
    cmd = [
        "uvicorn",
        f"{project_name}.asgi:app",
        "--host", host,
        "--port", str(port),
    ]
    
    if reload:
        cmd.append("--reload")
    
    # Start server as background process
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # Detach from parent
        )
        
        # Give it a moment to start
        time.sleep(1)
        
        # Check if it's still running
        if proc.poll() is not None:
            # Process died
            stdout, stderr = proc.communicate()
            return {
                "success": False,
                "error": "Server failed to start",
                "stdout": stdout.decode() if stdout else None,
                "stderr": stderr.decode() if stderr else None,
            }
        
        _server_processes[server_key] = proc
        
        url = f"http://{host}:{port}"
        
        return {
            "success": True,
            "url": url,
            "docs_url": f"{url}/docs",
            "pid": proc.pid,
            "server_key": server_key,
            "message": f"Server started at {url}",
        }
        
    except FileNotFoundError:
        return {
            "success": False,
            "error": "uvicorn not found. Install with: pip install uvicorn[standard]",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@register_tool(
    name="zeeb_stop_server",
    description="Stop the development server",
    input_schema={
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port the server is running on"},
            "host": {"type": "string", "description": "Host (default: 127.0.0.1)"},
            "server_key": {"type": "string", "description": "Server key from start_server (optional)"}
        }
    }
)
def zeeb_stop_server(
    port: int = 8000,
    host: str = "127.0.0.1",
    server_key: str | None = None,
) -> dict[str, Any]:
    """Stop the development server."""
    key = server_key or f"{host}:{port}"
    
    if key not in _server_processes:
        return {
            "success": False,
            "error": f"No server found for {key}",
            "active_servers": list(_server_processes.keys()),
        }
    
    proc = _server_processes[key]
    
    if proc.poll() is not None:
        # Already stopped
        del _server_processes[key]
        return {
            "success": True,
            "message": "Server was already stopped",
        }
    
    try:
        # Send SIGTERM
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        
        # Wait a bit
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
        
        del _server_processes[key]
        
        return {
            "success": True,
            "message": f"Server on {key} stopped",
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


@register_tool(
    name="zeeb_server_status",
    description="Check the status of development servers",
    input_schema={
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Check specific port"},
            "host": {"type": "string", "description": "Host (default: 127.0.0.1)"}
        }
    }
)
def zeeb_server_status(
    port: int | None = None,
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    """Check server status."""
    if port:
        key = f"{host}:{port}"
        if key not in _server_processes:
            return {
                "success": True,
                "running": False,
                "message": f"No server on {key}",
            }
        
        proc = _server_processes[key]
        running = proc.poll() is None
        
        if not running:
            del _server_processes[key]
        
        return {
            "success": True,
            "running": running,
            "server_key": key,
            "pid": proc.pid if running else None,
            "url": f"http://{host}:{port}" if running else None,
        }
    
    # Return status of all servers
    servers = []
    to_remove = []
    
    for key, proc in _server_processes.items():
        running = proc.poll() is None
        if not running:
            to_remove.append(key)
        else:
            servers.append({
                "server_key": key,
                "pid": proc.pid,
                "url": f"http://{key}",
                "running": True,
            })
    
    # Clean up dead processes
    for key in to_remove:
        del _server_processes[key]
    
    return {
        "success": True,
        "servers": servers,
        "total_running": len(servers),
    }
