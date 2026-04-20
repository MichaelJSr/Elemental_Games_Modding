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


/* ==================================================================
 * Xbox kernel / XDK re-exports (April 2026 expansion)
 * ==================================================================
 *
 * These live inside Azurik's XBE (the build-time linker inlined
 * the kernel import stubs), so shim calls resolve to the game's
 * own copies without hitting any cross-module thunk.  Every
 * declaration below is ABI-verified against Ghidra decomp +
 * a matching ``VanillaSymbol`` entry in vanilla_symbols.py.
 */

/* ---- SHA-1 (kernel crypto) ----------------------------------- */

/* Vanilla VA 0x000E7E5C  (mangled: _XcSHAInit@4)
 * __stdcall: ``ctx`` points to an 80-byte SHA context buffer. */
__attribute__((stdcall))
void XcSHAInit(unsigned char *ctx);

/* Vanilla VA 0x000E7E56  (mangled: _XcSHAUpdate@12)
 * Feed ``len`` bytes of ``data`` into the accumulator. */
__attribute__((stdcall))
void XcSHAUpdate(unsigned char *ctx, const unsigned char *data,
                 unsigned long len);

/* Vanilla VA 0x000E7E62  (mangled: _XcSHAFinal@8)
 * Finalise and write 20 bytes of SHA-1 output. */
__attribute__((stdcall))
void XcSHAFinal(unsigned char *ctx, unsigned char *out20);

/* ---- Debug output ------------------------------------------- */

/* Vanilla VA 0x000F5EB0  (mangled: _DbgPrint)
 * THE single most useful shim-debugging symbol.  Varargs cdecl.
 * Format specifiers match C stdio.  Output goes to xemu's
 * debug console. */
unsigned long DbgPrint(const char *fmt, ...);

/* Vanilla VA 0x000F5658  (mangled: _OutputDebugStringA@4) */
__attribute__((stdcall))
void OutputDebugStringA(const char *str);

/* ---- C runtime (cdecl) -------------------------------------- */

/* Vanilla VA 0x000EB240  (mangled: _strncmp) */
int strncmp(const char *s1, const char *s2, unsigned int n);

/* Vanilla VA 0x000ECFB1  (mangled: __stricmp)
 * MSVC internal — the leading underscore in the C name is what
 * causes clang to emit ``__stricmp`` as the undefined COFF
 * symbol. */
int _stricmp(const char *a, const char *b);

/* Vanilla VA 0x000EB561  (mangled: __strnicmp) */
int _strnicmp(const char *a, const char *b, unsigned int n);

/* Vanilla VA 0x000EB7C0  (mangled: _strncpy) */
char *strncpy(char *dst, const char *src, unsigned int n);

/* Vanilla VA 0x000EB3C0  (mangled: _strrchr) */
char *strrchr(const char *s, int c);

/* Vanilla VA 0x000ED2E0  (mangled: _strstr) */
char *strstr(const char *hay, const char *needle);

/* Vanilla VA 0x000EBE54  (mangled: _atol) */
long atol(const char *s);

/* ---- Wide-character (UTF-16) -------------------------------- */

/* Xbox filesystem + save metadata paths are UTF-16.
 *
 * Vanilla VA 0x000ECEE6  (mangled: _wcscmp) */
int wcscmp(const unsigned short *a, const unsigned short *b);

/* Vanilla VA 0x000ECE72  (mangled: _wcsstr) */
unsigned short *wcsstr(const unsigned short *hay,
                        const unsigned short *needle);

/* ---- Stdio ----------------------------------------------- */

/* Vanilla VA 0x000EB4E1  (mangled: _fclose) */
int fclose(void *stream);

/* ---- Win32 synchronisation ---------------------------------- */

/* Vanilla VA 0x000E2DA7  (mangled: _GetLastError@0) */
__attribute__((stdcall))
unsigned long GetLastError(void);

/* Vanilla VA 0x000E2DCF  (mangled: _SetLastError@4) */
__attribute__((stdcall))
void SetLastError(unsigned long code);

/* Vanilla VA 0x000E0CA3  (mangled: _CreateEventA@16) */
__attribute__((stdcall))
void *CreateEventA(void *attrs, int manual_reset,
                    int initial_state, const char *name);

/* Vanilla VA 0x000E0D60  (mangled: _SetEvent@4) */
__attribute__((stdcall))
int SetEvent(void *h);

/* Vanilla VA 0x000E0D80  (mangled: _ResetEvent@4) */
__attribute__((stdcall))
int ResetEvent(void *h);

/* ---- Title / launch control --------------------------------- */

/* Vanilla VA 0x000DF948  (mangled: _XGetLaunchInfo@8) */
__attribute__((stdcall))
unsigned long XGetLaunchInfo(unsigned long *flags_out,
                              void *data_out);

/* Vanilla VA 0x000DFA10  (mangled: _XLaunchNewImageA@8) */
__attribute__((stdcall))
unsigned long XLaunchNewImageA(const char *xbe_path, void *data);

/* Vanilla VA 0x000E6A2D  (mangled: _XapiBootToDash@12) */
__attribute__((stdcall))
void XapiBootToDash(unsigned long arg1, unsigned long arg2,
                     unsigned long arg3);



/* ==================================================================
 * Auto-generated bulk coverage (April 2026 expansion)
 * ==================================================================
 *
 * 242 extern declarations auto-generated from the Ghidra snapshot.
 * cdecl for C-runtime / MSVC internals (``_foo`` / ``__foo``) and
 * varargs; stdcall for Xbox SDK / Win32 (X*, Xc*, Xe*, Xapi*, D3D*,
 * Mm*, Ke*, Rtl*, + curated Win32 set).  Ghidra ``undefined`` /
 * ``undefined4`` types normalise to plain C — ABI cares only about
 * byte width, not type spelling.  Cast at the call site as needed.
 */

/* VA 0x000DFE7B  (stdcall) */
__attribute__((stdcall)) int XapiSelectCachePartition(int param_1, void * param_2, void * param_3);

/* VA 0x000E007F  (stdcall) */
__attribute__((stdcall)) void XMountUtilityDrive(int param_1);

/* VA 0x000E016A  (stdcall) */
__attribute__((stdcall)) void XMountAlternateTitleA(void * param_1, unsigned int param_2, void * param_3);

/* VA 0x000E02CF  (stdcall) */
__attribute__((stdcall)) void XUnmountAlternateTitleA(unsigned char param_1);

/* VA 0x000E0466  (stdcall) */
__attribute__((stdcall)) void XMUNameFromDriveLetter(int param_1, int param_2, int param_3);

/* VA 0x000E06B4  (stdcall) */
__attribute__((stdcall)) void MoveFileA(int param_1, int param_2);

/* VA 0x000E0D04  (stdcall) */
__attribute__((stdcall)) int OpenEventA(int param_1, int param_2, int param_3);

/* VA 0x000E0D9E  (stdcall) */
__attribute__((stdcall)) int PulseEvent(int param_1);

/* VA 0x000E0E9B  (stdcall) */
__attribute__((stdcall)) int CreateMutexA(int param_1, int param_2, int param_3);

/* VA 0x000E0FB3  (stdcall) */
__attribute__((stdcall)) int SignalObjectAndWait(int param_1, int param_2, unsigned int param_3, int param_4);

/* VA 0x000E2BC9  (stdcall) */
__attribute__((stdcall)) void * XCalculateSignatureBegin(int param_1);

/* VA 0x000E2DFD  (stdcall) */
__attribute__((stdcall)) void XapiSetLastNTError(int param_1);

/* VA 0x000E2F40  (stdcall) */
__attribute__((stdcall)) void GetOverlappedResult(int param_1, void * param_2, void * param_3, int param_4);

/* VA 0x000E66A4  (stdcall) */
__attribute__((stdcall)) int XapiMapLetterToDirectory(int param_1, void * param_2, void * param_3, int param_4, void * param_5, void * param_6);

/* VA 0x000E6A92  (stdcall) */
__attribute__((stdcall)) void XapiInitProcess(void);

/* VA 0x000E705E  (stdcall) */
__attribute__((stdcall)) void XapiFormatObjectAttributes(int param_1, int param_2, int param_3);

/* VA 0x000E72DC  (cdecl) */
void cinit(void);

/* VA 0x000E7334  (cdecl) */
void rtinit(void);

/* VA 0x000E735D  (stdcall) */
__attribute__((stdcall)) void XapiCallThreadNotifyRoutines(void);

/* VA 0x000E7395  (stdcall) */
__attribute__((stdcall)) void UnhandledExceptionFilter(int param_1);

/* VA 0x000E73B2  (stdcall) */
__attribute__((stdcall)) void SetThreadPriority(int param_1, int param_2);

/* VA 0x000E7404  (stdcall) */
__attribute__((stdcall)) int GetThreadPriority(int param_1);

/* VA 0x000E7458  (stdcall) */
__attribute__((stdcall)) int SetThreadPriorityBoost(int param_1, int param_2);

/* VA 0x000E752B  (stdcall) */
__attribute__((stdcall)) void RaiseException(int param_1, unsigned int param_2, unsigned int param_3, void * param_4);

/* VA 0x000E75C4  (stdcall) */
__attribute__((stdcall)) void ExitThread(int param_1);

/* VA 0x000E75D6  (stdcall) */
__attribute__((stdcall)) void GetExitCodeThread(int param_1, void * param_2);

/* VA 0x000E76BD  (stdcall) */
__attribute__((stdcall)) void XRegisterThreadNotifyRoutine(int param_1);

/* VA 0x000E77A5  (stdcall) */
__attribute__((stdcall)) unsigned int CreateThread(int param_1, unsigned int param_2, int param_3, int param_4, unsigned int param_5, int param_6);

/* VA 0x000E7A90  (stdcall) */
__attribute__((stdcall)) void XGetSectionSize(int param_1);

/* VA 0x000E7A9A  (stdcall) */
__attribute__((stdcall)) void XAutoPowerDownResetTimer(void);

/* VA 0x000E7E0E  (stdcall) */
__attribute__((stdcall)) int ExQueryNonVolatileSetting(unsigned long ValueIndex, int Type, void * Value, unsigned long ValueLength, int ResultLength);

/* VA 0x000EB278  (cdecl) */
void _onexit_lk(void);

/* VA 0x000EB2F8  (cdecl) */
void __onexitinit(void);

/* VA 0x000EB320  (cdecl) */
void _onexit(void);

/* VA 0x000EB358  (cdecl) */
int atexit(void * func);

/* VA 0x000EB495  (cdecl) */
void _fclose_lk(void * param_1);

/* VA 0x000EBC8E  (cdecl) */
unsigned int _abstract_cw(void);

/* VA 0x000EBD20  (cdecl) */
unsigned int _hw_cw(void);

/* VA 0x000EBE0C  (cdecl) */
unsigned int _control87(unsigned int param_1, unsigned int param_2);

/* VA 0x000EBE3E  (cdecl) */
void _controlfp(unsigned int param_1, unsigned int param_2);

/* VA 0x000EC09F  (cdecl) */
void _fsopen(void);

/* VA 0x000EC190  (cdecl) */
void _dosmaperr(unsigned int param_1);

/* VA 0x000EC203  (cdecl) */
void * _wcsdup(void * param_1);

/* VA 0x000EC288  (cdecl) */
double _copysign(double x, double y);

/* VA 0x000EC2A9  (cdecl) */
int _chgsign(int param_1, unsigned int param_2);

/* VA 0x000EC8E0  (cdecl) */
long long _allmul(unsigned int param_1, int param_2, unsigned int param_3, int param_4);

/* VA 0x000ECC91  (cdecl) */
void _SEH_epilog(void);

/* VA 0x000ECCA4  (cdecl) */
void _global_unwind2(int param_1);

/* VA 0x000ECCE6  (cdecl) */
void _local_unwind2(int param_1, int param_2);

/* VA 0x000ECDA0  (cdecl) */
unsigned long long _aullshr(unsigned char param_1, unsigned int param_2);

/* VA 0x000ECDC0  (cdecl) */
int _aullrem(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4);

/* VA 0x000ECE35  (cdecl) */
void * wcsncpy(void * dest, void * src, unsigned int n);

/* VA 0x000ECF20  (cdecl) */
int _aulldiv(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4);

/* VA 0x000ECF90  (cdecl) */
int _allshr(unsigned char param_1, int param_2);

/* VA 0x000ED000  (cdecl) */
int _alldiv(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4);

/* VA 0x000ED0AA  (cdecl) */
int _wcsicmp(void * param_1, void * param_2);

/* VA 0x000ED15C  (cdecl) */
void * wcscpy(void * dest, void * src);

/* VA 0x000ED399  (cdecl) */
int isalpha(int param_1);

/* VA 0x000ED419  (cdecl) */
int isdigit(int param_1);

/* VA 0x000ED470  (cdecl) */
int isspace(int param_1);

/* VA 0x000ED4C2  (cdecl) */
int isalnum(int param_1);

/* VA 0x000ED6C9  (cdecl) */
void seh_longjmp_unwind(int param_1);

/* VA 0x000ED997  (cdecl) */
unsigned int _flsbuf(unsigned char param_1, void * param_2);

/* VA 0x000EDAB0  (cdecl) */
void write_char(void * param_1);

/* VA 0x000EDAE3  (cdecl) */
void write_multi_char(int param_1, int param_2, void * param_3);

/* VA 0x000EDB07  (cdecl) */
void write_string(int param_1);

/* VA 0x000EEB6D  (cdecl) */
void _close(void);

/* VA 0x000EEC08  (cdecl) */
void _freebuf(void * param_1);

/* VA 0x000EEC33  (cdecl) */
void _flush(void * param_1);

/* VA 0x000EEC90  (cdecl) */
int _fflush_lk(void * param_1);

/* VA 0x000EECBE  (cdecl) */
void flsall(void);

/* VA 0x000EEDE3  (cdecl) */
void _flushall(void);

/* VA 0x000EEEAF  (cdecl) */
void _lock_file(unsigned int param_1);

/* VA 0x000EEEDE  (cdecl) */
void _lock_file2(int param_1, int param_2);

/* VA 0x000EEF01  (cdecl) */
void _unlock_file(unsigned int param_1);

/* VA 0x000EEF30  (cdecl) */
void _unlock_file2(int param_1, int param_2);

/* VA 0x000EF1EB  (cdecl) */
unsigned int _hextodec(void);

/* VA 0x000EF21D  (cdecl) */
unsigned int _inc(int param_1, void * param_2);

/* VA 0x000F02CE  (cdecl) */
int _errcode(unsigned char param_1);

/* VA 0x000F02FB  (cdecl) */
int _umatherr(int param_1, int param_2);

/* VA 0x000F0399  (cdecl) */
int _handle_qnan1(int param_1, double param_2);

/* VA 0x000F03EC  (cdecl) */
int _handle_qnan2(int param_1, double param_2, double param_3);

/* VA 0x000F05AF  (cdecl) */
int _set_exp(int param_1, short param_2);

/* VA 0x000F0618  (cdecl) */
int _set_bexp(int param_1, short param_2);

/* VA 0x000F063D  (cdecl) */
void _sptype(int param_1, unsigned int param_2);

/* VA 0x000F0765  (cdecl) */
int _ctrlfp(void);

/* VA 0x000F07E2  (cdecl) */
unsigned int _filbuf(void * param_1);

/* VA 0x000F0A8C  (cdecl) */
void _read(void);

/* VA 0x000F0B37  (cdecl) */
void _stbuf(void * param_1);

/* VA 0x000F0BBF  (cdecl) */
void _ftbuf(int param_1, void * param_2);

/* VA 0x000F0D74  (cdecl) */
void _write(void);

/* VA 0x000F1374  (cdecl) */
void * _openfile(int param_1, void * param_2, int param_3, void * param_4);

/* VA 0x000F14DC  (cdecl) */
void * _getstream(void);

/* VA 0x000F1831  (cdecl) */
void _forcdecpt(void * param_1);

/* VA 0x000F18EE  (cdecl) */
void _fassign(unsigned int param_1, void * param_2, void * param_3);

/* VA 0x000F1BDE  (cdecl) */
void _cfltcvt(void * param_1, void * param_2, int param_3, unsigned int param_4, int param_5);

/* VA 0x000F1C30  (cdecl) */
void _trandisp1(int param_1, int param_2);

/* VA 0x000F1C97  (cdecl) */
void _trandisp2(int param_1, int param_2);

/* VA 0x000F1E13  (cdecl) */
int _startOneArgErrorHandling(int param_1, int param_2, unsigned short param_3, int param_4, int param_5, int param_6);

/* VA 0x000F1E95  (cdecl) */
unsigned int _fload_withFB(int param_1, int param_2);

/* VA 0x000F1EFB  (cdecl) */
void _math_exit(int param_1, int param_2, int param_3, int param_4, int param_5);

/* VA 0x000F23F8  (cdecl) */
void _lseek(void);

/* VA 0x000F252B  (cdecl) */
void _getbuf(void * param_1);

/* VA 0x000F256F  (cdecl) */
unsigned char _isatty(unsigned int param_1);

/* VA 0x000F25D0  (cdecl) */
int _aulldvrm(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4);

/* VA 0x000F28B5  (cdecl) */
void _get_osfhandle(unsigned int param_1);

/* VA 0x000F2969  (cdecl) */
void _unlock_fhandle(unsigned int param_1);

/* VA 0x000F34C8  (cdecl) */
void _ZeroTail(int param_1, int param_2);

/* VA 0x000F34FA  (cdecl) */
void _IncMan(int param_1, int param_2);

/* VA 0x000F3547  (cdecl) */
void _RoundMan(int param_1, int param_2);

/* VA 0x000F35B9  (cdecl) */
void _CopyMan(int param_1, void * param_2);

/* VA 0x000F35E0  (cdecl) */
void _IsZeroMan(int param_1);

/* VA 0x000F35F9  (cdecl) */
void _ShrMan(int param_1, int param_2);

/* VA 0x000F3674  (cdecl) */
void _ld12cvt(void * param_1, void * param_2, void * param_3);

/* VA 0x000F37F8  (cdecl) */
void _ld12told(void * param_1, void * param_2);

/* VA 0x000F410B  (cdecl) */
void _sopen(void);

/* VA 0x000F4214  (cdecl) */
void __dtold(void * param_1, void * param_2);

/* VA 0x000F441A  (cdecl) */
void _flswbuf(int param_1, void * param_2);

/* VA 0x000F4542  (cdecl) */
void __addl(unsigned int param_1, unsigned int param_2, void * param_3);

/* VA 0x000F4563  (cdecl) */
void __add_12(void * param_1, void * param_2);

/* VA 0x000F45C1  (cdecl) */
void __shl_12(void * param_1);

/* VA 0x000F45EF  (cdecl) */
void __shr_12(void * param_1);

/* VA 0x000F4F09  (stdcall) */
__attribute__((stdcall)) void MmFreeContiguousMemory(void * BaseAddress);

/* VA 0x000F53D1  (stdcall) */
__attribute__((stdcall)) void GetTimeZoneInformation(void * param_1);

/* VA 0x000F568A  (stdcall) */
__attribute__((stdcall)) void OutputDebugStringW(int param_1);

/* VA 0x000F56DC  (stdcall) */
__attribute__((stdcall)) void RtlUnwind(void * TargetFrame, void * TargetIp, int ExceptionRecord, void * ReturnValue);

/* VA 0x000F5717  (stdcall) */
__attribute__((stdcall)) void XGWriteSurfaceOrTextureToXPR(void * param_1, unsigned int param_2, int param_3);

/* VA 0x000F5AF3  (cdecl) */
void * _itoa(int param_1, void * param_2, unsigned int param_3);

/* VA 0x000F5B1D  (cdecl) */
void * _ltoa(int param_1, void * param_2, unsigned int param_3);

/* VA 0x000F5C18  (cdecl) */
void longjmp(void * env, int val);

/* VA 0x000F5C94  (cdecl) */
void _setjmp3(void * param_1, int param_2, int param_3, int param_4);

/* VA 0x000F5D0F  (cdecl) */
void rt_probe_read4(void);

/* VA 0x000F5FDC  (cdecl) */
void _cintrindisp2(int param_1, int param_2);

/* VA 0x000F601A  (cdecl) */
void _cintrindisp1(int param_1, int param_2);

/* VA 0x000F6057  (cdecl) */
void _ctrandisp2(unsigned int param_1, int param_2, unsigned int param_3, int param_4);

/* VA 0x000F61ED  (cdecl) */
void _ctrandisp1(unsigned int param_1, int param_2);

/* VA 0x000F6220  (cdecl) */
int _fload(unsigned int param_1, int param_2);

/* VA 0x0011D5D0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetDeviceCaps(void * param_1);

/* VA 0x0011D630  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetGammaRamp(unsigned char param_1, void * param_2);

/* VA 0x0011D690  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetGammaRamp(void * param_1);

/* VA 0x0011D6C0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateTexture(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, unsigned int param_5, int param_6, void * param_7);

/* VA 0x0011D6F0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateVolumeTexture(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, unsigned int param_5, unsigned int param_6, int param_7, void * param_8);

/* VA 0x0011D720  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateCubeTexture(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, int param_5, void * param_6);

/* VA 0x0011D7B0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTransform(int param_1, void * param_2);

/* VA 0x0011D8F0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_MultiplyTransform(int param_1, void * param_2);

/* VA 0x0011DB30  (stdcall) */
__attribute__((stdcall)) void D3DDevice_Release(void);

/* VA 0x0011DB80  (stdcall) */
__attribute__((stdcall)) void D3DDevice_BlockOnFence(unsigned int param_1);

/* VA 0x0011DC10  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetVisibilityTestResult(unsigned int param_1, void * param_2, void * param_3);

/* VA 0x0011DC90  (stdcall) */
__attribute__((stdcall)) void D3DDevice_BlockUntilVerticalBlank(void);

/* VA 0x0011DF00  (stdcall) */
__attribute__((stdcall)) void D3DDevice_InsertFence(void);

/* VA 0x0011E530  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetDisplayMode(void * param_1);

/* VA 0x0011E590  (stdcall) */
__attribute__((stdcall)) int D3DDevice_Reset(void * param_1);

/* VA 0x0011E650  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderTarget(void * param_1, void * param_2);

/* VA 0x0011E8B0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetBackBuffer(int param_1, int param_2, void * param_3);

/* VA 0x0011E940  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CopyRects(int param_1, void * param_2, unsigned int param_3, int param_4, void * param_5);

/* VA 0x0011EDB0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetRenderTarget(void * param_1);

/* VA 0x0011EDD0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetDepthStencilSurface(void * param_1);

/* VA 0x0011EE00  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetViewport(void * param_1);

/* VA 0x0011EF60  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetLight(float param_1, void * param_2);

/* VA 0x0011F260  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTexture(int param_1, void * param_2);

/* VA 0x0011F480  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetIndices(void * param_1, int param_2);

/* VA 0x0011F5A0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_BeginVisibilityTest(void);

/* VA 0x0011F5D0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_EndVisibilityTest(unsigned int param_1);

/* VA 0x0011F630  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetDisplayFieldStatus(void * param_1);

/* VA 0x0011F890  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTile(unsigned int param_1, void * param_2);

/* VA 0x0011FB60  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetScissors(unsigned int param_1, unsigned int param_2, void * param_3);

/* VA 0x0011FCF0  (stdcall) */
__attribute__((stdcall)) int D3DDevice_PersistDisplay(void);

/* VA 0x0011FEB0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_Simple(int param_1, int param_2);

/* VA 0x0011FEE0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_Deferred(int param_1, int param_2);

/* VA 0x0011FF00  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderStateNotInline(int param_1, int param_2);

/* VA 0x001201E0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_FogColor(unsigned int param_1);

/* VA 0x00120230  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_CullMode(int param_1);

/* VA 0x00120310  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_TextureFactor(int param_1);

/* VA 0x00120360  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_LineWidth(float param_1);

/* VA 0x001203C0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_Dxt1NoiseEnable(unsigned int param_1);

/* VA 0x00120510  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_FillMode(int param_1);

/* VA 0x00120640  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTextureState_TexCoordIndex(int param_1, unsigned int param_2);

/* VA 0x00120720  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTextureState_BumpEnv(unsigned int param_1, int param_2, int param_3);

/* VA 0x00120780  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTextureState_BorderColor(int param_1, int param_2);

/* VA 0x001207C0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTextureState_ColorKeyColor(int param_1, int param_2);

/* VA 0x00120810  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetTextureStageStateNotInline(unsigned int param_1, int param_2, unsigned int param_3);

/* VA 0x00120D20  (stdcall) */
__attribute__((stdcall)) void D3D_CommonSetDebugRegisters(void);

/* VA 0x00120DF0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_ZEnable(int param_1);

/* VA 0x00120F80  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_OcclusionCullEnable(int param_1);

/* VA 0x00120FE0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_StencilCullEnable(int param_1);

/* VA 0x00121040  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_RopZCmpAlwaysRead(int param_1);

/* VA 0x00121060  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_RopZRead(int param_1);

/* VA 0x00121080  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetRenderState_DoNotCullUncompressed(int param_1);

/* VA 0x00121150  (stdcall) */
__attribute__((stdcall)) void D3DTexture_GetSurfaceLevel(void * param_1, unsigned int param_2, void * param_3);

/* VA 0x001211A0  (stdcall) */
__attribute__((stdcall)) void D3DTexture_LockRect(void * param_1, int param_2, void * param_3, void * param_4, unsigned int param_5);

/* VA 0x001211E0  (stdcall) */
__attribute__((stdcall)) void D3DCubeTexture_GetCubeMapSurface(void * param_1, int param_2, unsigned int param_3, void * param_4);

/* VA 0x00121240  (stdcall) */
__attribute__((stdcall)) void D3DCubeTexture_LockRect(void * param_1, unsigned int param_2, int param_3, void * param_4, void * param_5, unsigned int param_6);

/* VA 0x00121300  (stdcall) */
__attribute__((stdcall)) void D3D_CreateTexture(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, unsigned int param_5, unsigned int param_6, char param_7, unsigned int param_8, void * param_9);

/* VA 0x00121740  (stdcall) */
__attribute__((stdcall)) void D3D_CheckDeviceFormat(int param_1, int param_2, int param_3, unsigned char param_4, int param_5, int param_6);

/* VA 0x00121DF0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateVertexShader(void * this, void * param_1, void * param_2, void * param_3, int param_4);

/* VA 0x00122110  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetStreamSource(int param_1, void * param_2, int param_3);

/* VA 0x00122240  (stdcall) */
__attribute__((stdcall)) void D3DDevice_LoadVertexShader(int param_1, int param_2);

/* VA 0x001222A0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_LoadVertexShaderProgram(void * param_1, int param_2);

/* VA 0x00122310  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SelectVertexShader(unsigned int param_1, int param_2);

/* VA 0x001223D0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetShaderConstantMode(unsigned int param_1);

/* VA 0x00122510  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DeleteVertexShader(int param_1);

/* VA 0x00122630  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetVertexShader(unsigned int param_1);

/* VA 0x00122710  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetVertexShaderConstant(int param_1, void * param_2, int param_3);

/* VA 0x00122F60  (stdcall) */
__attribute__((stdcall)) void D3DDevice_Clear(int param_1, void * param_2, unsigned int param_3, unsigned int param_4, float param_5, unsigned int param_6);

/* VA 0x00123590  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DrawVerticesUP(int param_1, unsigned int param_2, unsigned int param_3, int param_4);

/* VA 0x001236D0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DrawIndexedVerticesUP(int param_1, unsigned int param_2, void * param_3, unsigned int param_4, int param_5);

/* VA 0x00123810  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DrawVertices(int param_1, unsigned int param_2, unsigned int param_3);

/* VA 0x001238B0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DrawIndexedVertices(void * param_1, unsigned int param_2, void * param_3);

/* VA 0x00123E00  (stdcall) */
__attribute__((stdcall)) void D3DSurface_GetDesc(void * param_1, void * param_2);

/* VA 0x00123E20  (stdcall) */
__attribute__((stdcall)) void D3DSurface_LockRect(void * param_1, void * param_2, void * param_3, unsigned int param_4);

/* VA 0x00124140  (stdcall) */
__attribute__((stdcall)) void D3D_SetPushBufferSize(int param_1, int param_2);

/* VA 0x00124200  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateIndexBuffer(void);

/* VA 0x00124320  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreateVertexBuffer(int param_1);

/* VA 0x00124380  (stdcall) */
__attribute__((stdcall)) void D3DVertexBuffer_Lock(void * param_1, int param_2, int param_3, void * param_4, unsigned char param_5);

/* VA 0x001244F0  (stdcall) */
__attribute__((stdcall)) char D3DResource_GetType(void * param_1);

/* VA 0x001245A0  (stdcall) */
__attribute__((stdcall)) void D3DResource_BlockUntilNotBusy(void * param_1);

/* VA 0x00124740  (stdcall) */
__attribute__((stdcall)) void D3D_DestroyResource(void * param_1);

/* VA 0x00124860  (stdcall) */
__attribute__((stdcall)) unsigned int D3DResource_AddRef(void * param_1);

/* VA 0x001248A0  (stdcall) */
__attribute__((stdcall)) unsigned int D3DResource_Release(void * param_1);

/* VA 0x001249A0  (stdcall) */
__attribute__((stdcall)) void D3DResource_Register(void * param_1, int param_2);

/* VA 0x00125250  (stdcall) */
__attribute__((stdcall)) void D3DDevice_RunPushBuffer(void * param_1, int param_2);

/* VA 0x00125530  (stdcall) */
__attribute__((stdcall)) void D3DDevice_GetPushBufferOffset(void * param_1);

/* VA 0x001263C0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_Present(void * param_1);

/* VA 0x00126570  (stdcall) */
__attribute__((stdcall)) void D3D_AllocContiguousMemory(int param_1, int param_2);

/* VA 0x001268B0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_CreatePixelShader(void * param_1, void * param_2);

/* VA 0x00126900  (stdcall) */
__attribute__((stdcall)) void D3DDevice_DeletePixelShader(void * param_1);

/* VA 0x00126DC0  (stdcall) */
__attribute__((stdcall)) void D3DDevice_SetPixelShader(unsigned int param_1);

/* VA 0x00127680  (stdcall) */
__attribute__((stdcall)) void D3D_UpdateProjectionViewportTransform(void);

/* VA 0x00127990  (stdcall) */
__attribute__((stdcall)) void D3D_LazySetPointParams(void * param_1);

/* VA 0x0012A410  (stdcall) */
__attribute__((stdcall)) unsigned int D3D_SetFence(unsigned char param_1);

/* VA 0x0012A4B0  (stdcall) */
__attribute__((stdcall)) void D3D_BlockOnTime(unsigned int param_1, int param_2);

/* VA 0x0012A790  (stdcall) */
__attribute__((stdcall)) void D3D_KickOffAndWaitForIdle(void);

/* VA 0x0012A7B0  (stdcall) */
__attribute__((stdcall)) void D3D_BlockOnResource(void * param_1);

/* VA 0x0012A840  (stdcall) */
__attribute__((stdcall)) void XMETAL_StartPush(void * param_1);

/* VA 0x0015CFED  (stdcall) */
__attribute__((stdcall)) void XGSwizzleRect(void * param_1, int param_2, void * param_3, void * param_4, unsigned int param_5, unsigned int param_6, void * param_7, void * param_8);

/* VA 0x0015E0B0  (stdcall) */
__attribute__((stdcall)) void XGUnswizzleBox(int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, void * param_5, void * param_6, unsigned int param_7, int param_8, void * param_9, unsigned int param_10);

/* VA 0x0015E597  (stdcall) */
__attribute__((stdcall)) void XGSetTextureHeader(unsigned int param_1, unsigned int param_2, unsigned int param_3, unsigned int param_4, unsigned int param_5, int param_6, void * param_7, int param_8, unsigned int param_9);




/* ==================================================================
 * SDK / Xbox-API additions — batch #2
 * Only entries whose first byte parses as a valid x86 prologue in
 * Azurik's embedded-library XBE sections.  Many Ghidra-named
 * XAPILIB / XG* / DSound / XAudio entry points turned out to be
 * IAT thunk labels that land mid-instruction inside import stubs
 * (``FF 15 <abs32>`` CALL [mem] is 6 bytes but Ghidra tagged
 * ``XGetDevices`` at +5, i.e. inside the operand).  Those are
 * dropped here pending a stubs pass that re-identifies the true
 * thunk table.  See ``docs/LEARNINGS.md`` for the dead-list.
 * ================================================================== */

/* --- Xbox device init (XAPILIB) --- */

/* VA 0x00187E87  (stdcall — XInput / XAPILIB) */
__attribute__((stdcall)) void XInitDevices(int reserved);

/* --- DirectSound top-level (DSOUND) --- */

/* VA 0x0013807C  (stdcall — DirectSound (DSOUND)) */
__attribute__((stdcall)) int DirectSoundCreate(void * guid, void * out_dsound);

/* VA 0x00137205  (stdcall — DirectSound (DSOUND)) */
__attribute__((stdcall)) void DirectSoundDoWork(void);

/* --- Direct3D (D3D) --- */

/* VA 0x00124160  (stdcall — Direct3D (D3D)) */
__attribute__((stdcall)) int Direct3D_CreateDevice(void);



/* ==================================================================
 * C-runtime + compiler intrinsics - batch #3
 * 64-bit arithmetic helpers + string/file stdlib
 * ================================================================== */

/* VA 0x000ED000  (cdecl) — 64-bit signed divide (compiler intrinsic). Clang emits CALL  */
long long __alldiv(long long a, long long b);

/* VA 0x000EC8E0  (cdecl) — 64-bit multiply (compiler intrinsic). */
long long __allmul(long long a, long long b);

/* VA 0x000ECF90  (cdecl) — 64-bit signed shift-right (intrinsic). */
long long __allshr(long long a, int shift);

/* VA 0x000ECF20  (cdecl) — 64-bit unsigned divide (intrinsic). */
unsigned long long __aulldiv(unsigned long long a, unsigned long long b);

/* VA 0x000ECDC0  (cdecl) — 64-bit unsigned remainder (intrinsic). */
unsigned long long __aullrem(unsigned long long a, unsigned long long b);

/* VA 0x000ECDA0  (cdecl) — 64-bit unsigned shift-right (intrinsic). */
unsigned long long __aullshr(unsigned long long a, int shift);

/* VA 0x000ED4C2  (cdecl) — _isalnum(ch) -> bool. */
int _isalnum(int ch);

/* VA 0x000ED399  (cdecl) — _isalpha(ch). */
int _isalpha(int ch);

/* VA 0x000ED419  (cdecl) — _isdigit(ch). */
int _isdigit(int ch);

/* VA 0x000ED470  (cdecl) — _isspace(ch). */
int _isspace(int ch);


/* ==================================================================
 * Player physics / movement functions (April 2026 late RE pass)
 * All __thiscall unless noted.  Exposed so future shims can
 * intercept specific physics states by name rather than raw VA.
 *
 * The player's physics state machine is dispatched from
 * ``player_physics_tick`` (FUN_0008CCC0) based on
 * ``entity.state`` (``[ESI+0x94]``):
 *
 *     0 = ground (walking / idle)        → walk
 *     1 = climbing                       → climb
 *     2 = airborne (post-jump, pre-land) → airborne_frame
 *     3 = slope-slide (slow / entry)     → slope_slide
 *     4 = slope-slide (fast / momentum)  → slope_slide
 *     5 = pre-jump (1-frame transition)  → prejump
 *     6 = swim                           → swim
 *     7-8 = gameover / dead
 *   256-261 = cutscene / cinematic
 * ================================================================== */

/* Main walking-state velocity computation (state 0).  Reads
 * ``CritterData.run_speed`` × ``entity.magnitude`` × direction
 * to produce horizontal velocity.  The ``FLD [EAX+0x40]`` at
 * VA 0x85F62 is our ``walk_speed_scale`` patch site.
 *
 * Vanilla VA: 0x00085F50  (mangled: _player_walk_state — cdecl has
 * no @N suffix) */
__attribute__((cdecl))
void player_walk_state(int *entity);

/* First-jump-from-ground function (state 5 → 2 transition).
 * Initialises ``entity.jump_height = 1.1`` (``[ESI+0x144]``),
 * ``entity.air_control = 9.0`` (``[ESI+0x140]``), then computes
 * initial vertical velocity via ``v0 = sqrt(2 × gravity ×
 * jump_height)``.  The ``FLD [0x001980A8]`` at VA 0x89160 is
 * our ``jump_speed_scale`` patch site.  Calls
 * ``airborne_re_init`` (FUN_00083F90) on the air-power path.
 *
 * Vanilla VA: 0x00089060  (mangled: _player_jump_init) */
__attribute__((cdecl))
unsigned char player_jump_init(void);

/* Per-frame airborne physics (state 2).  Reads
 * ``entity[+0x140]`` (air control) × ``entity[+0x124]``
 * (magnitude) for horizontal velocity; applies gravity to
 * vertical.  Calls ``wing_flap`` (FUN_00089300) when the flap
 * button is pressed.  VA 0x896EA is a legacy "dive boost" FADD
 * that was formerly (and mistakenly) the ``flap_height_scale``
 * patch target — see docs/LEARNINGS.md.
 *
 * Vanilla VA: 0x00089480  (mangled: _player_airborne_tick — cdecl
 * has no @N suffix) */
__attribute__((cdecl))
void player_airborne_tick(int *entity);

/* Wing flap / Air-power double jump (state 2, called per frame
 * from ``player_airborne_tick`` when the flap button is edge-
 * triggered).
 *
 * Flow:
 *   1. Checks ``[EBX+0x20] & 0x04`` (flap button edge).
 *   2. Reads ``armor.flap_count`` at ``[EDX+0x38]`` (VA 0x89321,
 *      our ``wing_flap_count`` trampoline site).
 *   3. Compares ``entity.flap_counter`` (``[ESI+0xD8]``) vs max;
 *      returns early (no flap) when exhausted.
 *   4. Calls ``airborne_re_init`` (FUN_00083F90) to reset
 *      ``entity.air_control`` = 12.0 and ``.jump_height`` = 1.2
 *      (air-power mode) or 9.0 / 1.1 (no air-power).
 *   5. Calls ``consume_fuel`` (FUN_000842D0) with cost = 1.0
 *      (first flap) or 100.0 (subsequent flap beyond 6m fall).
 *   6. Computes v0 at VA 0x893AE (``flap_height_scale`` patch
 *      target — ``FLD [0x001980A8]`` gravity, then
 *      ``× flap_height``, ``FADD ST,ST`` = ×2, ``FSQRT``).
 *   7. Halves v0 via ``FMUL [0x001A2510]=0.5`` at VA 0x893DD
 *      (``flap_subsequent_scale`` patch target) when player
 *      has fallen > 6m below peak.
 *   8. Scales by ``FMUL [0x001A26C4]=1.5`` at VA 0x893EB.
 *   9. Caps z-velocity at v0 (``[ESI+0x2C]``).
 *
 * Returns 1 if a flap fired (v0 applied, flap counter
 * incremented, sound played); 0 if gated out.
 *
 * Vanilla VA: 0x00089300  (mangled: _wing_flap@4) */
__attribute__((stdcall))
unsigned char wing_flap(int *input_state);

/* Per-frame airborne re-initialiser.  Called from ``player_jump_init``
 * (default path) and ``wing_flap`` (every flap).  Writes
 * ``entity[+0x140]`` (air control) and ``entity[+0x144]``
 * (jump height) based on active air-power level
 * (``[armor_mgr + 0x20] + 0x8`` ∈ [1, 3]):
 *
 *   Air power 1-3:  air_control = 12.0 (VA 0x83FAA imm32)
 *                   jump_height =  1.2 (VA 0x83FB0 imm32)
 *   Otherwise:      air_control =  9.0 (VA 0x83FCC imm32)
 *                   jump_height =  1.1 (VA 0x83FC2 imm32)
 *
 * The two imm32s at 0x83FAC (12.0) and 0x83FCE (9.0) are patch
 * targets for ``air_control_scale`` (as of April 2026 v2).
 *
 * Vanilla VA: 0x00083F90  (mangled: @player_airborne_reinit@4) */
__attribute__((fastcall))
void player_airborne_reinit(int *air_control_out);

/* Ground-input-state composer.  Runs per-frame; reads raw stick
 * + button inputs and computes ``entity.magnitude``
 * (``[ESI+0x124]``).  When WHITE or BACK is held (bit 0x40 of
 * ``entity.flags[0x20]``), multiplies magnitude by 3.0 at VA
 * 0x849E4 (``FMUL [0x001A25BC]``) — the ``roll_speed_scale``
 * patch target.  The 3.0 is the vanilla WHITE-button / dash
 * boost multiplier.
 *
 * Vanilla VA: 0x00084940  (mangled: @player_input_tick@4) */
__attribute__((fastcall))
void player_input_tick(int entity_input_state);

/* Climbing-state velocity (state 1).  Reads ``[0x001980E4]=2.0``
 * (our ``climb_speed_scale`` patch target) at VA 0x87FA7 +
 * 0x88357.  Called per frame while climbing a rope/ledge.
 *
 * Vanilla VA: 0x00087F80  (mangled: _player_climb_tick — cdecl has
 * no @N suffix) */
__attribute__((cdecl))
void player_climb_tick(int *entity);

/* Slope-slide-state velocity (states 3 & 4).  Triggered when
 * the player lands on a slope steeper than 45° from upright
 * (via ``FUN_0008AE10`` transition).  Reads
 * ``[0x001AAB68]=2.0`` (our ``slope_slide_speed_scale`` patch
 * target) at VA 0x89B76 for the state-3 slow-slide velocity.
 * State 4 uses separate fast-slide physics with dynamic-
 * initialised ``_DAT_003902A0/A4/A8/9C`` constants.
 *
 * Vanilla VA: 0x00089A70  (mangled: _player_slope_slide_tick) */
__attribute__((cdecl))
void player_slope_slide_tick(void);

/* Swim-state velocity (state 6).  Reads ``[0x001A25B4]=10.0``
 * at VA 0x8B7BF (``swim_speed_scale`` patch target).
 *
 * Vanilla VA: 0x0008B700  (mangled: _player_swim_tick@4) */
__attribute__((stdcall))
unsigned char player_swim_tick(int context);

/* Fall-damage dispatcher (surface landing path).  Called from
 * ``player_landing`` at VA 0x8C173 with
 * ``param_1 = fall_height`` and ``param_2 = fall_speed``.
 * Reads 7 cvars from ``config.xbr`` on first call
 * ("fall min velocity", "fall height 1/2/3",
 * "fall damage 1/2/3") cached as static doubles at
 * ``_DAT_00390228..00390290``.  Applies damage via
 * ``apply_damage`` when thresholds are breached.
 *
 * Our ``no_fall_damage`` v2 patch rewrites the prologue to
 * ``XOR AL, AL ; RET 8 ; NOP`` — always return 0 without
 * running the tier selector.
 *
 * Vanilla VA: 0x0008AB70  (mangled: _fall_damage_dispatch@8) */
__attribute__((stdcall))
unsigned char fall_damage_dispatch(float fall_height, float fall_speed);

/* Fall-death dispatcher (no-surface landing path).  Called
 * from ``player_landing`` at VA 0x8C095 when
 * ``[entity+0x38]`` (surface-contact slot) is NULL — i.e.,
 * the player landed without resolving a floor (falling off
 * the world edge, water splash at low surface, etc.).
 *
 * Reads the cached "fall height 4" cvar, computes fall
 * magnitude from (peak_z − current_z) and vertical velocity,
 * calls ``apply_damage`` if magnitude exceeds threshold, plays
 * "fx/sound/player/fallingdeath", and sets the entity death
 * flag (``[entity+0x16C] |= 1``).
 *
 * Our ``no_fall_damage`` v2 patch rewrites the prologue to
 * ``XOR AL, AL ; RET 4`` — closes the second fall-damage
 * leak (user reported "light damage still fires" with v1,
 * which only patched FUN_0008AB70).
 *
 * Vanilla VA: 0x0008BE00  (mangled: _fall_death_dispatch@4) */
__attribute__((stdcall))
unsigned char fall_death_dispatch(int entity);

/* Generic damage-apply routine.  Called from ~22 sites spanning
 * combat, enemy impact, and environmental hazards.  For fall
 * damage specifically, called from ``fall_damage_dispatch``
 * (VA 0x8AD9B) and ``fall_death_dispatch`` (VA 0x8BF59).
 * __stdcall (callers push 12 bytes of args and rely on callee
 * to clean — verified by zero ESP adjustment at 0x8BF5E after
 * the call).
 *
 * NOT directly patched by any pack — the ``no_fall_damage``
 * pack bypasses its two fall-related callers instead of
 * touching this shared routine.
 *
 * Vanilla VA: 0x00044640  (mangled: _apply_damage@12) */
__attribute__((stdcall))
void apply_damage(int entity, int kind, unsigned char caller_flag);

/* Player landing handler.  Dispatches based on
 * ``[entity+0x38]`` (surface-contact slot):
 *   - null → ``fall_death_dispatch`` (VA 0x8C095)
 *   - non-null → ``fall_damage_dispatch`` (VA 0x8C173)
 *
 * Both damage paths are bypassed by the ``no_fall_damage`` v2
 * pack (prologue rewrites of each dispatcher).
 *
 * Vanilla VA: 0x0008C080  (mangled: _player_landing@8) */
__attribute__((stdcall))
void player_landing(int entity, int landing_ctx);

/* Fuel consumer.  __thiscall where ``this = armor_mgr``.
 * Decrements ``armor_mgr.fuel_current`` (``[this+0x24]``) by
 * ``cost / armor_mgr.fuel_max`` (``[[this+0x20]+0x38]``) and
 * returns 1 on success, 0 on refuse (no armor / no fuel / out
 * of fuel).  CURRENTLY only called from ``wing_flap`` at VAs
 * 0x89354 (cost=1.0) and 0x893D4 (cost=100.0).
 *
 * NOTE: attack-casting fuel drain uses a SEPARATE consumer path
 * (per-attack "Fuel multiplier" in ``config/attacks_anims``
 * loaded at FUN_0007E2E0).  Our ``infinite_fuel`` patch
 * short-circuits this function only — NOT the attack-cast
 * fuel drain.  See docs/LEARNINGS.md for follow-up notes.
 *
 * Our ``infinite_fuel`` patch rewrites the prologue to
 * ``MOV AL, 1 ; RET 4`` — always return "consumed" without
 * decrementing.
 *
 * Vanilla VA: 0x000842D0  (mangled: _consume_fuel — thiscall has
 * no @N suffix on clang-i386-pe-win32) */
__attribute__((thiscall))
unsigned char consume_fuel(float cost);

/* Player armor-state per-frame tick.  Drives fuel drain,
 * armor-power cooldowns, and level-gated armor features.
 * Home of the VA 0x83DE3 per-frame fuel-drain block that
 * the retired ``infinite_fuel`` pack NOPed.
 *
 * Vanilla VA: 0x00083D80  (mangled: _player_armor_state_tick) */
__attribute__((thiscall))
void player_armor_state_tick(void);

/* Per-frame animation-apply helper.  __thiscall — samples the
 * active ``banm`` clip via ``FUN_000cab80``, writes XYZ
 * translation deltas to ``anim_obj[0x6C..0x71]`` (both
 * delta-mode and absolute-mode output slots), and commits via
 * vtable+0xC0.  Called from every player-state tick (walk /
 * climb / airborne / swim / slope).  Our ``root_motion_roll``
 * and ``root_motion_climb`` shims intercept the CALL sites to
 * this function and post-scale the written deltas.  Ends with
 * ``RET 0x10`` (callee cleans 16 bytes).
 *
 * Vanilla VA: 0x00042E40  (mangled: _anim_apply_translation —
 * thiscall has no @N suffix on clang-i386-pe-win32) */
__attribute__((thiscall))
void anim_apply_translation(int *anim_obj, float blend_time,
                            unsigned char *reference_name,
                            float *ref_pos);

/* Animation-change helper.  __thiscall — switches the active
 * ``banm`` clip via ``load_asset_by_fourcc('banm', 1)`` and
 * binds it.  NOT the per-frame translation applier (that's
 * ``anim_apply_translation`` at 0x42E40).  Called on animation
 * index changes (e.g. walk → roll transition).
 *
 * Vanilla VA: 0x00042910  (mangled: _anim_change — thiscall has
 * no @N suffix on clang-i386-pe-win32) */
__attribute__((thiscall))
void anim_change(int anim_index, int flags, int reserved);

/* ==================================================================
 * Config / cvar loaders (April 2026 RE pass continued)
 * ================================================================== */

/* Config-cell-value reader.  Called from every per-asset config
 * loader (see FUN_0007E2E0 for attack data, FUN_0003C700 for
 * armor properties) to pull a float value out of a ``tabl``
 * asset by (row, column) index.  The result is returned as an
 * x87 float10 on ST(0); Ghidra models it as ``float10``.
 *
 * __cdecl (caller cleans stack): 4 stack args (grid, row, col,
 * default_out).  Already registered in ``vanilla_symbols.py``
 * as name="config_cell_value" with matching ABI.
 *
 * Vanilla VA: 0x000D1520  (mangled: _config_cell_value) */
__attribute__((cdecl))
long double config_cell_value(void *grid, int row, int col,
                              double *default_out);

/* Cvar value fetcher.  Called to read a cvar's current value
 * by name (e.g. "fall min velocity").  Cached-return pattern:
 * the caller maintains a static double + a ``cached`` byte;
 * when the byte is zero, this function runs the lookup and
 * fills the byte.  ABI: __cdecl with the cvar-struct pointer
 * passed in ESI (register) — NOT a clean ABI we can express in
 * portable C, but for pure intercept / read-back purposes a
 * ``void``-signature extern suffices.
 *
 * Vanilla VA: 0x0005E620  (mangled: _cvar_get_double) */
__attribute__((cdecl))
long double cvar_get_double(void);



#ifdef __cplusplus
}
#endif

#endif /* AZURIK_VANILLA_H */
