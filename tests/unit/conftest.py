"""Shared fixtures for the collection unit tests."""

from __future__ import annotations

from ansible.plugins.loader import init_plugin_loader

# Install the collection finder at import time: the test modules import
# ansible_collections.* while being collected, before any fixture runs.
init_plugin_loader()
