"""Single source of truth for the Wortlaut application version.

This is the user-facing application release version (``0.1.0``). It is
deliberately distinct from the immutable dictionary release identities
(``dictionary-v2``, ``dictionary-online-v2``) and from historical
internal maintenance labels: dictionary assets carry their own
version/token namespace and are never treated as the application
version.
"""

from __future__ import annotations

__version__ = "0.1.0"
