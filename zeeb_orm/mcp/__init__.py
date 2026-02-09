"""
Zeeb agent server - Model Context Protocol server for LLM API building.

Enables LLMs to create and manage Zeeb projects, models, and APIs programmatically.

Usage:
    # Start MCP server (stdio mode for Claude Desktop / Cursor)
    zeeb agent-server stdio
    
    # Start MCP server (HTTP mode)
    zeeb agent-server serve --port 3000
    
    # List available tools
    zeeb agent-server tools
"""

from zeeb_orm.mcp.server import create_server, run_stdio, run_http

__all__ = [
    "create_server",
    "run_stdio", 
    "run_http",
]
