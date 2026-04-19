/* Shim-accessible vanilla Azurik functions.
 *
 * Each extern declared here corresponds 1:1 to an entry in
 * `azurik_mod/patching/vanilla_symbols.py`.  The shim platform's
 * layout_coff pass resolves undefined externals to these VAs at
 * apply time, rewriting REL32 / DIR32 relocation fields so the
 * shim's calls land inside vanilla Azurik code.
 *
 * For xboxkrnl (kernel) imports — DbgPrint, KeQueryPerformance-
 * Counter, etc. — see ``azurik_kernel.h`` instead.  Those are
 * resolved via D1's thunk-table-stub path, not the vanilla-symbol
 * registry; do NOT add kernel externs to this file.
 *
 * ABI rules the shim author MUST follow when consuming these:
 *
 *   - Match the calling convention exactly (`__attribute__((stdcall))`
 *     where applicable).  Getting it wrong leaks stack bytes per call.
 *   - Match the parameter types byte-for-byte.  A function declared
 *     `char flag` in the vanilla code must NOT be called with `int`
 *     from the shim — the stack layout diverges.
 *   - Don't add / remove arguments.  The `@N` suffix in the mangled
 *     name encodes the argument-byte count; a mismatch means the
 *     compiler emits a symbol name that vanilla_symbols.py doesn't
 *     recognise and layout_coff refuses the shim.
 *
 * Drift guard: `tests/test_vanilla_thunks.py` compiles this header
 * with every listed extern and confirms every unresolved COFF
 * symbol has a matching VanillaSymbol entry.  Adding a new extern
 * here without adding the matching Python entry fails the test.
 */
#ifndef AZURIK_VANILLA_H
#define AZURIK_VANILLA_H

#include "azurik.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------
 * Boot / movie subsystem
 * ---------------------------------------------------------------- */

/* Start playing `name` (a path like "AdreniumLogo.bik").  `flag`
 * controls the playback style (1 = prophecy-like, 0 = logo-like).
 * Returns AL=1 if the movie loaded and is now playing, AL=0 if the
 * call was a no-op (boot state machine should then advance to the
 * next state without polling).
 *
 * Vanilla VA: 0x00018980  (mangled: _play_movie_fn@8) */
__attribute__((stdcall))
unsigned char play_movie_fn(const char *name, char flag);

/* Advance the currently-playing movie by `dt` seconds.  Called
 * repeatedly from the boot state machine's POLL case.  Returns:
 *   0 = still playing
 *   1 = caller requested early abort (rare)
 *   2 = movie finished, state machine should advance
 *
 * Vanilla VA: 0x00018D30  (mangled: _poll_movie@4) */
__attribute__((stdcall))
int poll_movie(float dt);

#ifdef __cplusplus
}
#endif

#endif /* AZURIK_VANILLA_H */
