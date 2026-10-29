"""toolbag-mcp: an MCP server for Marmoset Toolbag 5.

Two parts, one repository:

* ``plugin/`` runs inside Toolbag (standard library only) and executes named actions on
  Toolbag's main thread, reachable over HTTP on 127.0.0.1.
* ``toolbag_mcp`` (this package) runs outside Toolbag, talks to the plugin and exposes the
  actions to MCP clients as typed tools.
"""

__version__ = "0.1.0.dev0"
