/* Shared typedefs and declarations for Azurik shims.
 *
 * Phase 1 keeps this intentionally empty — the first shim needs no
 * game types or external calls.  Phase 2 will populate this with the
 * minimum surface the shim library needs to talk to vanilla Azurik:
 *
 *   - Forward declarations for vanilla game functions (callable from
 *     shims via VA-bound `extern` prototypes).
 *   - Struct layouts for entities, config tables, etc. that a shim
 *     must understand byte-for-byte (extracted from Ghidra).
 *   - Kernel / D3D API imports (requires import-table rewriting in
 *     the apply pipeline).
 *
 * Keep this header freestanding-clean: no stdlib includes, no host
 * assumptions.  The shim compiler flags are `-ffreestanding -nostdlib`.
 */
#ifndef AZURIK_SHIM_H
#define AZURIK_SHIM_H

#ifdef __cplusplus
extern "C" {
#endif

/* -- fixed-width integer aliases without stdint.h --------------------- */
typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;
typedef signed char    i8;
typedef signed short   i16;
typedef signed int     i32;

#ifdef __cplusplus
}
#endif

#endif /* AZURIK_SHIM_H */
