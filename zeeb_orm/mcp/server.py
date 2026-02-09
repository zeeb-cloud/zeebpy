"""
MCP Server for Zeeb API building.

Provides tools, resources, and prompts for LLMs to create and manage APIs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    Resource,
    ResourceTemplate,
)


# Create the MCP server instance
server = Server("zeeb-mcp")


# Tool registry - maps tool names to handler functions
_tool_handlers: dict[str, Callable] = {}


def register_tool(name: str, description: str, input_schema: dict[str, Any]):
    """Decorator to register a tool handler."""
    def decorator(func: Callable):
        _tool_handlers[name] = {
            "handler": func,
            "description": description,
            "input_schema": input_schema,
        }
        return func
    return decorator


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    from zeeb_orm.mcp.tools import project, models, serializers, viewsets, migrations, data, server as srv
    
    tools = []
    for name, info in _tool_handlers.items():
        tools.append(Tool(
            name=name,
            description=info["description"],
            inputSchema=info["input_schema"],
        ))
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a tool and return results."""
    if name not in _tool_handlers:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Unknown tool: {name}"})
        )]
    
    handler = _tool_handlers[name]["handler"]
    
    try:
        # Call the handler (may be sync or async)
        if asyncio.iscoroutinefunction(handler):
            result = await handler(**arguments)
        else:
            result = handler(**arguments)
        
        return [TextContent(
            type="text",
            text=json.dumps(result, indent=2, default=str)
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": str(e),
                "error_type": type(e).__name__,
            })
        )]


# Resource registry
_resources: dict[str, Callable] = {}


def register_resource(uri: str, name: str, description: str):
    """Decorator to register a resource handler."""
    def decorator(func: Callable):
        _resources[uri] = {
            "handler": func,
            "name": name,
            "description": description,
        }
        return func
    return decorator


@server.list_resources()
async def list_resources() -> list[Resource]:
    """List available resources."""
    from zeeb_orm.mcp.resources import project as proj_resources
    
    resources = []
    for uri, info in _resources.items():
        resources.append(Resource(
            uri=uri,
            name=info["name"],
            description=info["description"],
            mimeType="application/json",
        ))
    return resources


@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read a resource by URI."""
    if uri not in _resources:
        return json.dumps({"error": f"Unknown resource: {uri}"})
    
    handler = _resources[uri]["handler"]
    
    try:
        if asyncio.iscoroutinefunction(handler):
            result = await handler()
        else:
            result = handler()
        
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def create_server() -> Server:
    """Create and configure the MCP server."""
    # Import tools to register them
    from zeeb_orm.mcp.tools import project, models, serializers, viewsets, migrations, data
    from zeeb_orm.mcp.tools import server as srv
    from zeeb_orm.mcp.resources import project as proj_resources
    
    return server


async def run_stdio():
    """Run the MCP server in stdio mode."""
    srv = create_server()
    
    async with stdio_server() as (read_stream, write_stream):
        await srv.run(
            read_stream,
            write_stream,
            srv.create_initialization_options(),
        )


def run_http(host: str = "127.0.0.1", port: int = 3000):
    """Run the MCP server in HTTP mode."""
    # HTTP mode would require additional setup with SSE
    # For now, we'll focus on stdio mode which is most common
    raise NotImplementedError(
        "HTTP mode not yet implemented. Use stdio mode with 'zeeb mcp stdio'"
    )


def main():
    """Main entry point for zeeb-mcp command."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Zeeb MCP Server")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # stdio command
    stdio_parser = subparsers.add_parser("stdio", help="Run in stdio mode")
    
    # serve command
    serve_parser = subparsers.add_parser("serve", help="Run HTTP server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=3000, help="Port to bind to")
    
    # tools command
    tools_parser = subparsers.add_parser("tools", help="List available tools")
    
    args = parser.parse_args()
    
    if args.command == "stdio":
        asyncio.run(run_stdio())
    elif args.command == "serve":
        run_http(args.host, args.port)
    elif args.command == "tools":
        # Import tools to register them
        from zeeb_orm.mcp.tools import project, models, serializers, viewsets, migrations, data
        from zeeb_orm.mcp.tools import server as srv
        
        print("Available Zeeb MCP Tools:")
        print("=" * 60)
        for name, info in sorted(_tool_handlers.items()):
            print(f"\n{name}")
            print(f"  {info['description']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
