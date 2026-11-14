"""pytest setup for the mapgen regression suite: import paths and shared fixtures."""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
for p in (REPO_ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
