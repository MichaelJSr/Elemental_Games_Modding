/* Extended xboxkrnl imports (Phase 2 D1-extend).
 *
 * Declares xboxkrnl functions that Azurik's vanilla XBE does NOT
 * import — i.e. that have no pre-resolved slot in the kernel thunk
 * table at VA 0x0018F3A0.  Calling any of these from a shim causes
 * the apply pipeline to:
 *
 *   1. Place the session-shared resolver shim
 *      (``shims/shared/xboxkrnl_resolver.c``) once per session.
 *   2. Generate a 33-byte "resolving stub" unique to each extended
 *      import: first call invokes ``xboxkrnl_resolve_by_ordinal`` +
 *      caches the result inline; subsequent calls jump through the
 *      cache.
 *   3. Resolve the shim's ``CALL _Foo@N`` REL32 relocation to the
 *      stub's VA — same flow as the static D1 thunk stubs.
 *
 * Included in the SAME way as ``azurik_kernel.h`` — just
 * ``#include "azurik_kernel_extend.h"`` in your shim source.  The
 * layout pipeline automatically detects which path (static D1 vs
 * D1-extend) each imported function requires based on the ordinal
 * catalogue in ``azurik_mod/patching/xboxkrnl_ordinals.py``.
 *
 * ABI rules:
 *   - Same as ``azurik_kernel.h``: match the declared calling
 *     convention exactly, match parameter types byte-for-byte on
 *     the 4-byte-aligned i386 stack.
 *   - ``NTAPI`` = ``__stdcall`` (the overwhelming majority of
 *     xboxkrnl exports).  Declare fastcall / cdecl variants
 *     explicitly when required.
 *
 * Runtime cost:
 *   - First call: ~20 instructions (parse PE header + export table +
 *     compute function pointer + cache + jump).  ~microsecond-scale.
 *   - Subsequent calls: 3 instructions (load cache + test + jump) +
 *     the called function's own body.
 *
 * For performance-critical tight loops, prefer static D1 imports
 * (those declared in ``azurik_kernel.h``) when possible — their
 * ``FF 25 <thunk_va>`` stub is a single indirect jump with no
 * cache load or test.
 *
 * Drift guard: ``tests/test_kernel_extend.py::HeaderOrdinalDriftGuard``
 * parses every extern in this file and confirms its name is in the
 * extended ordinal catalogue.  Adding an extern whose name isn't
 * catalogued fails the test.
 */
#ifndef AZURIK_KERNEL_EXTEND_H
#define AZURIK_KERNEL_EXTEND_H

#include "azurik_kernel.h"   /* imports the shared kernel typedefs */

#ifdef __cplusplus
extern "C" {
#endif

/* Additional typedefs used by the extended declarations below but
 * not already in azurik_kernel.h. */
typedef char        CHAR;
typedef char *      PSZ;
typedef signed long long LONGLONG;


/* =======================================================================
 * Debug / diagnostics extensions
 * ===================================================================== */

/* Software breakpoint.  INT3 under the hood; halts the debugger if
 * one is attached.  No-op on retail builds without a kernel debugger.
 * Kernel ordinal 5. */
NTAPI VOID DbgBreakPoint(void);

/* Variant that carries a status code.  Kernel ordinal 6. */
NTAPI VOID DbgBreakPointWithStatus(ULONG Status);

/* Prompt the kernel debugger with an ASCII string.  Kernel ordinal 9. */
NTAPI ULONG DbgPrompt(PCSZ PromptString, PCSZ ResponseString, ULONG MaximumResponseLength);


/* =======================================================================
 * Executive (Ex*) extensions
 * ===================================================================== */

/* Read-write lock primitives.  Kernel ordinals 12 / 13 / 18 / 27.
 * Paired with RtlInitializeCriticalSection (ord 291) — the RW lock
 * type is built on top of the same scheduler machinery. */
NTAPI VOID ExAcquireReadWriteLockExclusive(PVOID Lock);
NTAPI VOID ExAcquireReadWriteLockShared(PVOID Lock);
NTAPI VOID ExInitializeReadWriteLock(PVOID Lock);
NTAPI VOID ExReleaseReadWriteLock(PVOID Lock);

/* Atomic compare-exchange on 64-bit values.  Kernel ordinal 21.
 * Typed as volatile pointers so callers can use it on lockless
 * queues / counters without undefined-behaviour warnings. */
NTAPI ULONGLONG ExInterlockedCompareExchange64(
    volatile ULONGLONG *Destination,
    ULONGLONG Exchange,
    ULONGLONG Comparand);

/* Persist an EEPROM setting.  Paired with ExQueryNonVolatileSetting
 * (ord 24).  Kernel ordinal 28. */
NTAPI NTSTATUS ExSaveNonVolatileSetting(
    ULONG ValueIndex,
    ULONG Type,
    PVOID Value,
    ULONG ValueLength);


/* =======================================================================
 * I/O manager (Io*) extensions
 * ===================================================================== */

/* IRP allocation / free.  Kernel ordinals 59, 72. */
NTAPI PIRP IoAllocateIrp(CCHAR StackSize, BOOLEAN ChargeQuota);
NTAPI VOID IoFreeIrp(PIRP Irp);

/* High-level IRP creation for FSD requests.  Kernel ordinals 60 / 62. */
NTAPI PIRP IoBuildAsynchronousFsdRequest(
    ULONG MajorFunction, PDEVICE_OBJECT DeviceObject, PVOID Buffer,
    ULONG Length, PLARGE_INTEGER StartingOffset,
    PIO_STATUS_BLOCK IoStatusBlock);
NTAPI PIRP IoBuildSynchronousFsdRequest(
    ULONG MajorFunction, PDEVICE_OBJECT DeviceObject, PVOID Buffer,
    ULONG Length, PLARGE_INTEGER StartingOffset, HANDLE Event,
    PIO_STATUS_BLOCK IoStatusBlock);
NTAPI PIRP IoBuildDeviceIoControlRequest(
    ULONG IoControlCode, PDEVICE_OBJECT DeviceObject,
    PVOID InputBuffer, ULONG InputBufferLength,
    PVOID OutputBuffer, ULONG OutputBufferLength,
    BOOLEAN InternalDeviceIoControl,
    HANDLE Event, PIO_STATUS_BLOCK IoStatusBlock);

/* File creation at the kernel-object level.  Paired with NtCreateFile
 * (ord 190).  Kernel ordinal 66. */
NTAPI NTSTATUS IoCreateFile(
    PHANDLE FileHandle, ACCESS_MASK DesiredAccess,
    POBJECT_ATTRIBUTES ObjectAttributes, PIO_STATUS_BLOCK IoStatusBlock,
    PLARGE_INTEGER AllocationSize, ULONG FileAttributes,
    ULONG ShareAccess, ULONG Disposition, ULONG CreateOptions,
    ULONG Options);

/* Device cleanup.  Kernel ordinal 68. */
NTAPI VOID IoDeleteDevice(PDEVICE_OBJECT DeviceObject);

/* Completion-port helpers.  Ordinal 79. */
NTAPI NTSTATUS IoSetIoCompletion(
    PVOID IoCompletion, PVOID KeyContext, PVOID ApcContext,
    NTSTATUS IoStatus, ULONG_PTR IoStatusInformation);


/* =======================================================================
 * Kernel services (Ke*) extensions
 * ===================================================================== */

/* Critical-region guards — raise / lower APC_LEVEL.  Ordinals 101 / 122. */
NTAPI VOID KeEnterCriticalRegion(void);
NTAPI VOID KeLeaveCriticalRegion(void);

/* Current execution context.  Ordinals 102 / 103. */
NTAPI KIRQL    KeGetCurrentIrql(void);
NTAPI PKTHREAD KeGetCurrentThread(void);

/* Event / semaphore / mutant / timer initialisation.  Ordinals
 * 106 / 108 / 111 / 112. */
NTAPI VOID KeInitializeEvent(PKEVENT Event, EVENT_TYPE Type, BOOLEAN State);
NTAPI VOID KeInitializeMutant(PKMUTANT Mutant, BOOLEAN InitialOwner);
NTAPI VOID KeInitializeSemaphore(PKSEMAPHORE Sem, LONG Count, LONG Limit);
NTAPI VOID KeInitializeTimer(PKTIMER Timer);

/* Read-state helpers.  Ordinals 130 / 131 / 132 / 133. */
NTAPI LONG KeReadStateEvent(PRKEVENT Event);
NTAPI LONG KeReadStateMutant(PKMUTANT Mutant);
NTAPI LONG KeReadStateSemaphore(PKSEMAPHORE Sem);
NTAPI BOOLEAN KeReadStateTimer(PKTIMER Timer);

/* Release / reset.  Ordinals 135 / 136 / 138. */
NTAPI LONG KeReleaseMutant(PKMUTANT Mutant, LONG Increment, BOOLEAN Abandoned, BOOLEAN Wait);
NTAPI LONG KeReleaseSemaphore(PKSEMAPHORE Sem, LONG Increment, LONG Count, BOOLEAN Wait);
NTAPI LONG KeResetEvent(PKEVENT Event);

/* Pulse.  Ordinal 123. */
NTAPI LONG KePulseEvent(PKEVENT Event, LONG Increment, BOOLEAN Wait);


/* =======================================================================
 * Memory manager (Mm*) extensions
 * ===================================================================== */

/* Validate that a VA range is mapped.  Ordinal 174. */
NTAPI BOOLEAN MmIsAddressValid(PVOID VirtualAddress);

/* Map / unmap physical I/O ranges into the current VA space.
 * Ordinals 177 / 183. */
NTAPI PVOID MmMapIoSpace(ULONG_PTR PhysicalAddress, SIZE_T NumberOfBytes, ULONG Protect);
NTAPI VOID  MmUnmapIoSpace(PVOID BaseAddress, SIZE_T NumberOfBytes);


/* =======================================================================
 * Object manager (Ob*) extensions
 * ===================================================================== */

/* Alt reference-by-pointer / by-name.  Ordinals 244 / 245. */
NTAPI NTSTATUS ObReferenceObjectByName(
    POBJECT_STRING ObjectName, ULONG Attributes,
    POBJECT_TYPE ObjectType, PVOID ParseContext,
    PVOID *ReturnedObject);
NTAPI VOID ObReferenceObjectByPointer(PVOID Object, POBJECT_TYPE ObjectType);

/* Fastcall counterparts for the same operations.  Ordinals 248 / 249. */
FASTCALL VOID ObfReferenceObject(PVOID Object);
/* ObfDereferenceObject at ord 250 duplicates the Azurik static slot. */


/* =======================================================================
 * Process / thread (Ps*) extensions
 * ===================================================================== */

/* Plain PsCreateSystemThread (not -Ex).  Ordinal 253. */
NTAPI NTSTATUS PsCreateSystemThread(
    PHANDLE ThreadHandle, POBJECT_ATTRIBUTES ObjectAttributes,
    HANDLE ProcessHandle, PHANDLE ClientId,
    PKSTART_ROUTINE StartRoutine, PVOID StartContext);

/* Query the process-wide thread statistics.  Ordinal 256. */
NTAPI NTSTATUS PsQueryStatistics(PVOID ProcessStatistics);


/* =======================================================================
 * Runtime library (Rtl*) extensions — big table, the most immediately
 * useful ones are string manipulation + memory.
 * ===================================================================== */

/* String manipulation. */
NTAPI NTSTATUS RtlAppendStringToString(PSTRING Destination, PSTRING Source);
NTAPI NTSTATUS RtlCharToInteger(PCSZ String, ULONG Base, PULONG Value);
NTAPI LONG     RtlCompareString(PSTRING String1, PSTRING String2, BOOLEAN CaseInSensitive);
NTAPI LONG     RtlCompareUnicodeString(PUNICODE_STRING s1, PUNICODE_STRING s2, BOOLEAN CaseInSensitive);
NTAPI VOID     RtlCopyString(PSTRING Destination, PSTRING Source);
NTAPI VOID     RtlCopyUnicodeString(PUNICODE_STRING Destination, PUNICODE_STRING Source);
NTAPI BOOLEAN  RtlEqualUnicodeString(PUNICODE_STRING s1, PUNICODE_STRING s2, BOOLEAN CaseInSensitive);
NTAPI NTSTATUS RtlIntegerToChar(ULONG Value, ULONG Base, LONG Length, PSZ String);
NTAPI CHAR     RtlLowerChar(CHAR Char);
NTAPI CHAR     RtlUpperChar(CHAR Char);

/* Memory. */
NTAPI VOID RtlFillMemory(PVOID Destination, SIZE_T Length, UCHAR Fill);
NTAPI VOID RtlMoveMemory(PVOID Destination, PCVOID Source, SIZE_T Length);
NTAPI VOID RtlZeroMemory(PVOID Destination, SIZE_T Length);

/* Big-integer helpers.  Used internally by LARGE_INTEGER arithmetic.
 * Ordinals 280..282. */
NTAPI LONGLONG RtlExtendedIntegerMultiply(LONGLONG Multiplicand, LONG Multiplier);
NTAPI LONGLONG RtlExtendedLargeIntegerDivide(LONGLONG Dividend, ULONG Divisor, PULONG Remainder);

/* Byte swap helpers.  Useful for network code that doesn't want to
 * go through the full ntohl / htons chain.  Ordinals 307 / 318. */
NTAPI ULONG  RtlUlongByteSwap(ULONG Source);
NTAPI USHORT RtlUshortByteSwap(USHORT Source);

/* Misc. */
NTAPI VOID RtlTryEnterCriticalSection(PRTL_CRITICAL_SECTION CriticalSection);


/* =======================================================================
 * Xbox-specific keys / config
 * ===================================================================== */

/* Kernel version info.  Ordinal 324. */
NTAPI VOID XboxKrnlVersion(PVOID Version);

/* =======================================================================
 * Formatted output helpers (ordinals 352..355)
 * ===================================================================== */

CDECL int snprintf(char *buf, SIZE_T n, const char *fmt, ...);
CDECL int sprintf(char *buf, const char *fmt, ...);
/* vsnprintf / vsprintf deliberately omitted — va_list handling on
 * a freestanding i386 compile is fiddly enough that we'd rather
 * shims use the fixed-arg forms above or go through DbgPrint. */


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_KERNEL_EXTEND_H */
