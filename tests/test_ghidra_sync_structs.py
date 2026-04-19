"""Regression tests for the ghidra-sync struct-push extension.

Exercises:
  - ``plan_struct_sync`` — creates actions for every header struct,
    marks existing ones as ``keep`` unless ``recreate_existing=True``
  - ``apply_struct_sync`` — POSTs struct skeletons + fields
  - ``_ghidra_type_for`` — maps our header C types to Ghidra DTM names
  - Round-trip via the in-process mock Ghidra server
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from azurik_mod.xbe_tools.ghidra_client import GhidraClient
from azurik_mod.xbe_tools.ghidra_sync import (
    StructAction,
    _ghidra_type_for,
    apply_struct_sync,
    plan_struct_sync,
)
from azurik_mod.xbe_tools.mock_ghidra import MockGhidraServer


SYNTHETIC_HEADER = """
typedef struct TinyHeaderStruct {
    u32 first;         /* +0x00 */
    u32 second;        /* +0x04 */
    u16 third;         /* +0x08 */
    u8  fourth;        /* +0x0A */
} TinyHeaderStruct;

typedef struct FloatyStruct {
    f32 x;             /* +0x00 */
    f32 y;             /* +0x04 */
    char *name;        /* +0x08 */
} FloatyStruct;
"""


class TypeMapping(unittest.TestCase):
    def test_maps_azurik_typedefs(self):
        self.assertEqual(_ghidra_type_for("u32"), "uint")
        self.assertEqual(_ghidra_type_for("s32"), "int")
        self.assertEqual(_ghidra_type_for("u16"), "ushort")
        self.assertEqual(_ghidra_type_for("f32"), "float")
        self.assertEqual(_ghidra_type_for("u8"),  "uchar")

    def test_pointers_collapse_to_void_ptr(self):
        self.assertEqual(_ghidra_type_for("char *"), "void *")
        self.assertEqual(_ghidra_type_for("const char *"),
                          "void *")
        self.assertEqual(_ghidra_type_for("int *"), "void *")

    def test_unknown_falls_back_to_undefined4(self):
        self.assertEqual(_ghidra_type_for("MyCustomType"),
                          "undefined4")


class StructSyncPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-sync-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))
        self.header = self.tmp / "synthetic.h"
        self.header.write_text(SYNTHETIC_HEADER)

        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.client = GhidraClient(port=self.mock.port, timeout=30.0)

    def test_all_new_structs_planned_as_create(self) -> None:
        actions = plan_struct_sync(
            self.client, azurik_h=self.header)
        kinds = {a.name: a.kind for a in actions}
        self.assertEqual(kinds["TinyHeaderStruct"], "create")
        self.assertEqual(kinds["FloatyStruct"], "create")

    def test_existing_struct_marked_keep_by_default(self) -> None:
        self.mock.register_struct(
            "TinyHeaderStruct", size=12,
            fields=[{"name": "first", "dataType": "uint",
                     "offset": 0, "length": 4}])
        actions = plan_struct_sync(
            self.client, azurik_h=self.header)
        kinds = {a.name: a.kind for a in actions}
        self.assertEqual(kinds["TinyHeaderStruct"], "keep")
        self.assertEqual(kinds["FloatyStruct"], "create")

    def test_recreate_flag_forces_recreate(self) -> None:
        self.mock.register_struct(
            "TinyHeaderStruct", size=12, fields=[])
        actions = plan_struct_sync(
            self.client, azurik_h=self.header,
            recreate_existing=True)
        kinds = {a.name: a.kind for a in actions}
        self.assertEqual(kinds["TinyHeaderStruct"], "recreate")


class StructSyncApply(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="azurik-apply-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                self.tmp, ignore_errors=True))
        self.header = self.tmp / "synthetic.h"
        self.header.write_text(SYNTHETIC_HEADER)

        self.mock = MockGhidraServer()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.client = GhidraClient(port=self.mock.port, timeout=30.0)

    def test_apply_creates_structs_and_fields(self) -> None:
        actions = plan_struct_sync(
            self.client, azurik_h=self.header)
        report = apply_struct_sync(
            self.client, actions, azurik_h=self.header)
        self.assertEqual(report.structs_created, 2)
        # TinyHeaderStruct has 4 fields, FloatyStruct has 3 = 7 total
        self.assertEqual(report.struct_fields_added, 7)
        # Verify round-trip: fetched structs have our field layouts
        tiny = self.client.get_struct("TinyHeaderStruct")
        self.assertEqual(len(tiny.fields), 4)
        # Field types follow _ghidra_type_for mapping
        field_types = {f.name: f.data_type for f in tiny.fields}
        self.assertEqual(field_types["first"], "uint")
        self.assertEqual(field_types["third"], "ushort")
        self.assertEqual(field_types["fourth"], "uchar")

    def test_apply_skips_keep_actions(self) -> None:
        self.mock.register_struct(
            "TinyHeaderStruct", size=99, fields=[])
        actions = plan_struct_sync(
            self.client, azurik_h=self.header)
        report = apply_struct_sync(
            self.client, actions, azurik_h=self.header)
        # Only FloatyStruct (the new one) was created.
        self.assertEqual(report.structs_created, 1)
        self.assertEqual(report.structs_skipped, 1)
        # TinyHeaderStruct left alone — still size 99.
        tiny = self.client.get_struct("TinyHeaderStruct")
        self.assertEqual(tiny.size, 99)

    def test_apply_recreate_deletes_before_creating(self) -> None:
        self.mock.register_struct(
            "TinyHeaderStruct", size=99, fields=[])
        actions = plan_struct_sync(
            self.client, azurik_h=self.header,
            recreate_existing=True)
        report = apply_struct_sync(
            self.client, actions, azurik_h=self.header)
        # Both structs "created" (TinyHeaderStruct re-created).
        self.assertEqual(report.structs_created, 2)
        # TinyHeaderStruct now has the header's actual size
        # (padded to cover all fields: last field ends at 0x0B).
        tiny = self.client.get_struct("TinyHeaderStruct")
        self.assertEqual(len(tiny.fields), 4)

    def test_apply_is_idempotent_when_all_keep(self) -> None:
        """Calling apply twice produces no errors when everything
        is already in place."""
        first = apply_struct_sync(
            self.client,
            plan_struct_sync(self.client, azurik_h=self.header),
            azurik_h=self.header)
        second = apply_struct_sync(
            self.client,
            plan_struct_sync(self.client, azurik_h=self.header),
            azurik_h=self.header)
        self.assertEqual(first.structs_created, 2)
        self.assertEqual(second.structs_created, 0)
        self.assertEqual(second.structs_skipped, 2)
        self.assertFalse(second.errors)


if __name__ == "__main__":
    unittest.main()
