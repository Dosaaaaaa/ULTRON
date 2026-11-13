"""Permission levels (from the ULTRON security architecture)."""

from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    P0 = 0  # read approved information
    P1 = 1  # low-risk, reversible (open an approved application)
    P2 = 2  # create/edit files, change settings: requires approval
    P3 = 3  # destructive / privileged: requires explicit typed confirmation
    P4 = 4  # sensitive transmission, financial, security-critical: confirmation + extra checks

    @property
    def label(self) -> str:
        return self.name


# Automatic execution (no human in the loop) is capped here no matter what the config says.
MAX_AUTOMATIC = Level.P1
