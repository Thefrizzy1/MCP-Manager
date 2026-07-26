"""Single place for the dashboard / update-check version string."""

VERSION = "6.0.0"

# The MCP protocol revision the selftest client advertises. Bump this together
# with the mcp[cli] pin. Note: the 2026-07-28 spec removes the initialize
# handshake, so once the SDK adopts it the selftest becomes a plain reachability
# probe (see docs/OPERATIONS.md).
MCP_PROTOCOL_VERSION = "2025-11-25"
