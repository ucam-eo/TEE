"""
Progress tracking utility for long-running operations.

Writes progress JSON to PROGRESS_DIR/<operation_id>.json for frontend polling.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

from lib.config import PROGRESS_DIR


class ProgressTracker:
    """Track and persist progress to JSON file."""

    def __init__(self, operation_id: str):
        """
        Initialize progress tracker.

        Args:
            operation_id: Unique operation identifier (e.g., 'iitdellhi_embeddings')
        """
        self.operation_id = operation_id
        self.progress_file = PROGRESS_DIR / f"{operation_id}_progress.json"
        self.start_time = datetime.now(timezone.utc).isoformat()

    def update(self, status: str, message: str = "", current_value: int = 0,
               total_value: int = 0, current_file: str = ""):
        """
        Update progress and write to JSON file.

        Args:
            status: 'downloading', 'processing', 'complete', 'error'
            message: Human-readable message
            current_value: Current progress (e.g., MB downloaded)
            total_value: Total to complete (e.g., total MB)
            current_file: Name of file currently being processed
        """
        percent = 0
        if total_value > 0:
            percent = min(100, int((current_value / total_value) * 100))

        progress_data = {
            "operation_id": self.operation_id,
            "status": status,
            "message": message,
            "current_value": current_value,
            "total_value": total_value,
            "percent": percent,
            "current_file": current_file,
            "start_time": self.start_time,
            "last_update": datetime.now(timezone.utc).isoformat()
        }

        # Write to JSON file
        with open(self.progress_file, 'w') as f:
            json.dump(progress_data, f)

    def complete(self, message: str = "Complete"):
        """Mark operation as complete."""
        self.update("complete", message, 100, 100)

    def error(self, message: str):
        """Mark operation as failed."""
        self.update("error", message)

    def cleanup(self):
        """Remove progress file."""
        if self.progress_file.exists():
            self.progress_file.unlink()
