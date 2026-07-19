"""Test configuration for shelly_cloud_diy.

Ensures the repo root is importable so ``custom_components.shelly_cloud_diy``
resolves regardless of how pytest is invoked.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
