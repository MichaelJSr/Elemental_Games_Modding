/* Test fixture — NOT a shipping shim.
 *
 * Exercises Phase-2 A3 (vanilla function thunk resolution) by
 * forwarding to the Azurik boot-time movie player declared in
 * azurik_vanilla.h.  The test suite compiles this on demand and
 * confirms the REL32 at the CALL site resolves to the vanilla
 * `play_movie_fn` virtual address recorded in vanilla_symbols.py.
 *
 * If this test fails, either:
 *   - The vanilla_symbols.py registry entry and this extern
 *     declaration's mangled-name signature disagree (check
 *     calling convention + argument bytes), OR
 *   - The COFF loader stopped consulting vanilla_symbols during
 *     _resolve_symbol_va.
 */
#include "azurik_vanilla.h"

__attribute__((stdcall))
unsigned char c_calls_vanilla(const char *name)
{
    return play_movie_fn(name, 0);
}
