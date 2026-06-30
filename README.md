# Color matching example MCP server
## How to use
1. Install required library using uv.
```
uv init
uv add numpy pillow fastmcp
```

2. Start Color matching MCP server
```
uv run server.py
```

3. Design your workflow. Random baseline is provided.
```
uv run client.py
```