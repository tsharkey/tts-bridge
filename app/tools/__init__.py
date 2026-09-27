"""
Hub tools. Each is a package here with:

    TOOL = {"name": ..., "description": ...}   # its card on the homepage
    router                                     # its API routes (from app.core.api.router())
    static/index.html                          # its page, served at /tools/<name-with-dashes>/

and one line in app/server.py's TOOLS to mount it.
"""
