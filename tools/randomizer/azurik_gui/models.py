"""Data models for the Azurik GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppState:
    """Shared application state across all tabs."""
    iso_path: Path | None = None
    output_dir: Path | None = None
    last_seed: int | None = None
    last_output: Path | None = None


@dataclass
class RandomizerConfig:
    """Configuration for a randomizer run."""
    seed: int = 42
    do_major: bool = True
    do_keys: bool = True
    do_gems: bool = True
    do_barriers: bool = True
    do_connections: bool = False
    do_qol: bool = True
    output_path: Path | None = None
    item_pool: dict[str, int] | None = None
    force_unsolvable: bool = False

    def to_args(self, iso_path: Path) -> list[str]:
        """Build CLI argument list for azurik_mod.py randomize-full."""
        import json
        args = [
            "randomize-full",
            "--iso", str(iso_path),
            "--seed", str(self.seed),
            "--output", str(self.output_path or iso_path.with_name("Azurik_randomized.iso")),
        ]
        if not self.do_major:
            args.append("--no-major")
        if not self.do_keys:
            args.append("--no-keys")
        if not self.do_gems:
            args.append("--no-gems")
        if not self.do_barriers:
            args.append("--no-barriers")
        if not self.do_connections:
            args.append("--no-connections")
        if not self.do_qol:
            args.append("--no-qol")
        if self.item_pool:
            args.extend(["--item-pool", json.dumps(self.item_pool)])
        if self.force_unsolvable:
            args.append("--force")
        return args
