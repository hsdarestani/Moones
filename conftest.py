"""Repository-wide pytest configuration.

Safety invariant: importing or starting pytest must never call a paid external
provider. Live provider checks are intentionally outside pytest and require an
explicit operator opt-in; see ``scripts/live_image_smoke.py``.
"""

# Intentionally no pytest hooks with network/provider side effects.
