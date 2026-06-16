
"""Shared utilities for the research agent."""

from datetime import datetime
from pathlib import Path


def get_today_str() -> str:
    """Get current date in a human-readable format.

    Builds the string without the platform-specific ``%-d`` directive, which is
    invalid on Windows (where it would raise ``ValueError``).
    """
    now = datetime.now()
    return f"{now.strftime('%a %b')} {now.day}, {now.year}"


def get_current_dir() -> Path:
    """Get the current directory of the module.

    This function is compatible with Jupyter notebooks and regular Python scripts.

    Returns:
        Path object representing the current directory
    """
    try:
        return Path(__file__).resolve().parent
    except NameError:  # __file__ is not defined
        return Path.cwd()
