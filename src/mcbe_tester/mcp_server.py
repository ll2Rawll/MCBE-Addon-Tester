"""Compatibility entrypoint: the default MCP server is now the independent simulator."""

from .sim.mcp import main

if __name__ == "__main__":
    main()
