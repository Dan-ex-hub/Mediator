"""Small shared helpers."""

from __future__ import annotations

import re
from pathlib import Path

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp", ".cs": "csharp",
    ".sh": "bash", ".sql": "sql", ".html": "html", ".css": "css", ".kt": "kotlin",
}


def detect_language(path: Path) -> str:
    """Return a markdown code-fence language hint for a file, or ''."""
    return _EXT_LANG.get(path.suffix.lower(), "")


def extract_last_code_block(text: str) -> str | None:
    """Return the contents of the last fenced code block in ``text``, if any."""
    blocks = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.DOTALL)
    return blocks[-1].strip() if blocks else None
