"""Test package. The automated tests use the public classroom accounts, so they run in
explicit demo mode. Tests that check behaviour outside demo mode switch it off themselves."""
import os

os.environ.setdefault("FOOD_BANK_DEMO_MODE", "1")
