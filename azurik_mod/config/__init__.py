"""config.xbr registry data and keyed-table parser.

Package-data files shipped alongside this module:
    registry.json         — variant-record property registry
    schema.json           — property schema (type flags, encoding)
    entity_values.json    — vanilla baseline values
"""

from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = CONFIG_DIR / "registry.json"
SCHEMA_PATH = CONFIG_DIR / "schema.json"
ENTITY_VALUES_PATH = CONFIG_DIR / "entity_values.json"

__all__ = [
    "CONFIG_DIR",
    "ENTITY_VALUES_PATH",
    "REGISTRY_PATH",
    "SCHEMA_PATH",
]
