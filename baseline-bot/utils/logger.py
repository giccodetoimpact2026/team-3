"""Minimal stdout logging.

The baseline logs to stdout only, unstructured. Structured logging (levels,
JSON, correlation) is a maturity upgrade students can make.
"""
from datetime import datetime


def log(message: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)
