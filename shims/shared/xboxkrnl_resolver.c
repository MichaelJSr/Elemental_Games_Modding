/* Runtime xboxkrnl export resolver (D1-extend).
 *
 * Exposes a single helper: ``xboxkrnl_resolve_by_ordinal(ordinal)``.
 *
 * Walks the PE export table of ``xboxkrnl.exe`` — which the Xbox
 * loader maps at the fixed retail VA ``0x80010000`` before the game
 * starts — and returns the runtime function pointer for the requested
 * ordinal, or NULL on miss.  Self-contained: no kernel imports, no
 * vanilla-function dependencies, no globals of its own.  Safe to
 * call from any shim at any point after kernel init (i.e. any time
 * the game's own code is already running).
 *
 * Placed in ``shims/shared/`` because this is session-shared
 * infrastructure — multiple feature folders reference it via the
 * E shared-library mechanism.  The ``ShimLayoutSession`` auto-places
 * this file's ``.o`` the first time a feature's stub needs it; per-
 * import resolving stubs then CALL through it.
 *
 * Design notes:
 * - XBOXKRNL_BASE is hard-coded to 0x80010000 (retail kernel).  Debug
 *   and Chihiro kernels use different bases, but those aren't
 *   supported by the shim platform today.
 * - The function uses cdecl (clang's i386 default) rather than
 *   __stdcall so the resolving stubs emitted by
 *   ``shim_session.stub_for_extended_kernel_symbol`` can push the
 *   ordinal + CALL + ADD ESP, 4 (standard cdecl cleanup).  Avoids
 *   the @4 suffix on the mangled name which would complicate stub
 *   generation.
 * - No dependency on C library — hand-rolls the 4-byte reads with
 *   shifts so an uninitialised rodata segment can't break alignment.
 */

typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;

#define XBOXKRNL_BASE_VA  0x80010000u

/* Read a little-endian u32 from `p` without assuming alignment. */
static u32 read_u32(const u8 *p)
{
    return ((u32)p[0])
         | ((u32)p[1] << 8)
         | ((u32)p[2] << 16)
         | ((u32)p[3] << 24);
}

/* Walk xboxkrnl.exe's PE export directory and return the function
 * pointer for the given ordinal, or 0 on miss.
 *
 * Cdecl — standard clang i386 default (mangled ``_xboxkrnl_resolve_by_ordinal``).
 */
void *xboxkrnl_resolve_by_ordinal(unsigned ordinal)
{
    const u8 *base = (const u8 *)XBOXKRNL_BASE_VA;

    /* DOS header: e_lfanew at +0x3C points at the PE header. */
    u32 pe_offset = read_u32(base + 0x3C);
    const u8 *pe  = base + pe_offset;

    /* Layout from the start of the PE header:
     *   +0x00  "PE\0\0" signature             (4 bytes)
     *   +0x04  IMAGE_FILE_HEADER              (20 bytes)
     *   +0x18  IMAGE_OPTIONAL_HEADER32        — data directories at +0x60.
     */
    const u8 *opt = pe + 4 + 20;

    /* IMAGE_DATA_DIRECTORY[0] (export) sits at optional-header + 0x60
     * for a PE32 file.  First 4 bytes = VirtualAddress, next 4 =
     * Size (we don't need the Size). */
    u32 exp_rva = read_u32(opt + 0x60);
    if (exp_rva == 0) {
        return (void *)0;  /* image has no export directory */
    }

    const u8 *exp = base + exp_rva;

    /* IMAGE_EXPORT_DIRECTORY layout (subset we use):
     *   +0x10  Base               (starting ordinal — usually 1)
     *   +0x14  NumberOfFunctions  (entries in AddressOfFunctions)
     *   +0x1C  AddressOfFunctions (RVA of function-pointer array)
     */
    u32 ord_base   = read_u32(exp + 0x10);
    u32 n_funcs    = read_u32(exp + 0x14);
    u32 funcs_rva  = read_u32(exp + 0x1C);

    const u8 *funcs = base + funcs_rva;

    if (ordinal < ord_base) {
        return (void *)0;  /* below the export's starting ordinal */
    }
    u32 idx = ordinal - ord_base;
    if (idx >= n_funcs) {
        return (void *)0;  /* past the last export entry */
    }

    u32 func_rva = read_u32(funcs + idx * 4);
    if (func_rva == 0) {
        return (void *)0;  /* unreserved slot */
    }
    return (void *)(base + func_rva);
}
