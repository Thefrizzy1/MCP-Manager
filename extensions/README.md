# Plutus extensions (optional)

Third-party or personal code can register extra MCP tools.

## How to add an extension

1. Create `extensions/__init__.py` in this folder (next to this README).
2. Define a function:

```python
def register(mcp):
    @mcp.tool()
    async def my_custom_tool() -> str:
        return "hello from extension"
```

3. Restart Plutus. The server imports `extensions` and calls `register(mcp)` if it exists.

## Sharing with others

- Publish a small Python package or a git repo that documents the `register(mcp)` contract.
- Users copy your module under `extensions/` or merge your `register` into their `extensions/__init__.py`.
- **Custom dashboard cards** (URLs + API notes) do not require code: use **Settings → Custom integrations** and edit `data/custom_integrations.json` via the UI.

## Security

Only load extensions from sources you trust. Arbitrary Python here runs with the same privileges as Plutus.
