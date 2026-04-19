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

/* Boot state-machine tick — runs one iteration of the logo / splash
 * / prophecy sequencer.  Called from the main boot loop at
 * VA 0x59BA5 with dt in seconds.  Returns a boolean-ish value in AL;
 * the caller does ``TEST AL, AL; JNZ ...`` to branch on "boot still
 * in progress" vs "boot complete — enter title screen".
 *
 * Return type declared ``unsigned char`` on the shim side because
 * only AL is observed by the vanilla caller, even though the
 * callee technically returns a full ``undefined4`` per Ghidra.
 *
 * Safe to wrap from shims that want to intercept boot-state
 * transitions without replacing the whole state machine (e.g. an
 * extension of ``qol_skip_logo`` that also skips the prophecy
 * intro cutscene).
 *
 * Vanilla VA: 0x0005F620  (mangled: _boot_state_tick@4) */
__attribute__((stdcall))
unsigned char boot_state_tick(float dt);


/* ------------------------------------------------------------------
 * Entity registry
 * ---------------------------------------------------------------- */

/* Look up an entity descriptor by name (byte-level strcmp).  If the
 * name isn't in the registry AND ``fallback != NULL``, the function
 * registers the fallback as a new entry and returns it; if
 * ``fallback == NULL`` and the name isn't found, returns NULL.
 *
 * Scans the global registry at ``DAT_0038C1E4..DAT_0038C1E8``
 * (array of descriptor-pointers).  Name comparison is case-
 * sensitive, terminates at the first 0x00 byte.
 *
 * __fastcall convention — clang emits the mangled name
 * ``@entity_lookup@8``:
 *   ECX = name (null-terminated ASCII byte pointer)
 *   EDX = fallback (optional registration payload, or NULL)
 *   EAX (return) = descriptor pointer, or NULL on miss+no-fallback
 *
 * Vanilla VA: 0x0004B510  (mangled: @entity_lookup@8) */
__attribute__((fastcall))
int *entity_lookup(const char *name, int *fallback);


/* ------------------------------------------------------------------
 * index.xbr / dev-menu — NEW (April 2026 RE pass)
 * ------------------------------------------------------------------ */

/* Boot dispatcher that picks which level to load.  Reads the BSS
 * flag at ``AZURIK_DEV_MENU_FLAG_VA`` (0x001BCDD8) and loads the
 * ``levels/selector`` developer hub when the flag is anything
 * other than ``-1`` (the vanilla default).
 *
 * Exposed so a future ``qol_enable_dev_menu`` shim can reference
 * the dispatcher by name rather than by raw VA.  The shim itself
 * only needs a single DIR32 store into the flag; this extern is
 * purely documentation.  See docs/LEARNINGS.md § selector.xbr.
 *
 * Vanilla VA: 0x00052F50  (mangled: _dev_menu_flag_check@8) */
__attribute__((stdcall))
int dev_menu_flag_check(int context, int init_flag);

/* Look up an asset in the global index.xbr table by fourcc.
 *
 * **Do NOT call directly from shim C code** — this function's
 * real ABI uses a Watcom-ish convention with the asset INDEX
 * passed in EAX as an implicit register argument alongside the
 * stack args.  Clang can't express that natively.
 *
 * The ``stdcall(8)`` signature below is a deliberate lie to the
 * mangler so the REL32 resolves to VA 0x000A67A0; a call-through
 * wrapper (like ``shims/shared/gravity_integrate.c``) will be
 * needed when a real shim uses this.
 *
 * See ``load_asset_by_fourcc`` in vanilla_symbols.py for the
 * full ABI note + fourcc constant table.
 *
 * Vanilla VA: 0x000A67A0  (mangled: _load_asset_by_fourcc@8) */
__attribute__((stdcall))
int load_asset_by_fourcc(int fourcc, int flags);


/* ------------------------------------------------------------------
 * Save-slot signature entry points (April 2026 RE pass)
 * ------------------------------------------------------------------ */

/* Entry point for Azurik's save-slot sign / verify.
 * __thiscall: save-slot context in ECX; the flag byte at
 * ``[ECX+0x20A]`` gates signing (0x7A = bypass).
 *
 * Exposed so a future ``qol_skip_save_signature`` shim can patch
 * the function prologue to unconditionally set the bypass flag.
 * See docs/SAVE_FORMAT.md § 7 for the algorithm trace.
 *
 * Vanilla VA: 0x0005C920  (mangled: _calculate_save_signature) */
__attribute__((thiscall))
void calculate_save_signature(void);

/* XDK re-exports: Xbox SDK's HMAC-SHA1 signature helpers.
 * Exposed so a (future) shim that wants to bypass / override
 * Azurik's signature flow can intercept at the XDK boundary
 * rather than at Azurik's caller.  Both follow the standard
 * XDK stdcall convention.
 *
 * Vanilla VA: 0x000E2BC9  (mangled: _xcalculate_signature_begin@4) */
__attribute__((stdcall))
void *xcalculate_signature_begin(unsigned int flags);

/* Vanilla VA: 0x000E2C21  (mangled: _xcalculate_signature_end@8) */
__attribute__((stdcall))
int xcalculate_signature_end(unsigned int *ctx, unsigned char *out20);


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_VANILLA_H */
