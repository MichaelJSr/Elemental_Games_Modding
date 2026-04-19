/* Azurik config-table lookup helpers for shims.
 *
 * Declares the two config-system entry points exposed via the
 * vanilla-symbol registry: ``config_name_lookup`` (FUN_000d1420)
 * and ``config_cell_value`` (FUN_000d1520).
 *
 * Unlike ``azurik_gravity.h``'s inline-asm wrapper (needed because
 * ``FUN_00085700`` has a non-standard MSVC-RVO ABI), both config
 * functions use classic calling conventions that clang supports
 * natively:
 *
 *   - ``config_name_lookup``:  __thiscall int(void *, const char *)
 *   - ``config_cell_value``:   __cdecl   double(const int *, int,
 *                                               int, double *)
 *
 * So shim authors can call them like any other C function — no
 * asm, no wrapper.  The layout pipeline resolves the CALL's REL32
 * through ``vanilla_symbols.py`` at apply time.
 *
 * Typical use: a shim that wants to read a specific critter stat
 * without walking the raw byte representation.  The function
 * panics (via an INT3) on out-of-range indices, so callers MUST
 * verify the table's bounds before calling.
 */
#ifndef AZURIK_CONFIG_H
#define AZURIK_CONFIG_H

#include "azurik.h"

#ifdef __cplusplus
extern "C" {
#endif


/* Look up a named entry in a config table (``FUN_000d1420``).
 *
 * ``this_table`` — the config-table object.  The layout we've
 * partially reversed from the prologue + inner loop:
 *
 *    +0x00  u32  entry_count (must be > 0 to enter the loop body)
 *    +0x04  ?    entries[]  (variable-size records, each ends
 *                              with a null-terminated ASCII name
 *                              at some fixed offset within the
 *                              record)
 *
 * ``name`` — ASCII needle, null-terminated.
 *
 * Returns an ``int`` (observed: probably either the matching
 * entry's index, or a byte offset into the entries array).
 * Exact semantics need more Ghidra work to pin down — treat the
 * return value as opaque and use it with other config functions
 * that accept it.
 *
 * __thiscall: ECX = ``this_table``; ``name`` pushed on stack;
 * callee cleans up (RET 4).
 *
 * Vanilla VA: 0x000D1420  (mangled: _config_name_lookup) */
__attribute__((thiscall))
int config_name_lookup(void *this_table, const char *name);


/* Look up a cell value in a 2-D config grid (``FUN_000d1520``).
 *
 * ``grid`` — pointer to the grid descriptor.  Structure known
 *   partly from the bounds-check reads:
 *       +0x00  u32  col_count  (tested against ``col`` parameter)
 *       +0x08  u32  row_count  (tested against ``row`` parameter)
 *
 * ``row`` / ``col`` — 0-based indices.  Function panics (logs +
 *   INT3) if either is out of range, so shims MUST validate
 *   against the grid's stored sizes first.
 *
 * ``default_out`` — output pointer for the default value when
 *   the cell is empty / unset.  May be NULL if the caller doesn't
 *   care about the default.
 *
 * Returns a ``double`` (the function natively returns 80-bit
 * ``float10`` in ST(0); clang truncates to 64-bit for C callers).
 *
 * __cdecl: all args on stack; caller cleans up.
 *
 * Vanilla VA: 0x000D1520  (mangled: _config_cell_value) */
double config_cell_value(const int *grid, int row, int col,
                         double *default_out);


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_CONFIG_H */
