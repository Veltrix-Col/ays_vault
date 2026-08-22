"""Discovery v2 de metadatos Zoho, independiente del transporte."""

from .comparator import compare_snapshots
from .rendering import render_comparison_markdown, render_model_markdown
from .service import DiscoveryFatalError, DiscoveryService
from .storage import SnapshotStore, load_snapshot

__all__ = [
    "DiscoveryService",
    "DiscoveryFatalError",
    "SnapshotStore",
    "compare_snapshots",
    "load_snapshot",
    "render_comparison_markdown",
    "render_model_markdown",
]
