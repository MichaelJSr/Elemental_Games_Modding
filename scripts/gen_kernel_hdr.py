"""One-shot generator: produce azurik_kernel.h covering all 151
xboxkrnl imports Azurik references, by zipping our ordinal table
against OpenXDK's xboxkrnl.h.  Output goes to stdout.
"""
import re
import sys
from pathlib import Path

_REPO = Path("/Users/michaelsrouji/Documents/Xemu/tools/Elemental_Games_Modding")
sys.path.insert(0, str(_REPO))
from azurik_mod.patching.xboxkrnl_ordinals import AZURIK_KERNEL_ORDINALS

XBHDR = Path("/Users/michaelsrouji/Documents/Xemu/tools/xbox-includes/include/xboxkrnl.h")
text = XBHDR.read_text()

# Parse all `XBAPI ret NTAPI Name ( args );`
FUNC_PATTERN = re.compile(
    r"XBAPI\s+(?P<ret>[\w\s\*]+?)\s+NTAPI\s+(?P<name>\w+)\s*\(\s*(?P<args>.*?)\)\s*;",
    re.DOTALL,
)

decls: dict[str, tuple[str, str]] = {}
for m in FUNC_PATTERN.finditer(text):
    ret = re.sub(r"\s+", " ", m.group("ret")).strip()
    args = re.sub(r"\s+", " ", m.group("args")).strip()
    # Strip XDK SAL-ish annotations — we don't need them.
    args = re.sub(r"\b(IN|OUT|OPTIONAL|CONST)\b\s*", "", args)
    args = re.sub(r"\s+", " ", args).strip()
    if not args:
        args = "VOID"
    decls[m.group("name")] = (ret, args)

# Categorisation (same as before)
GROUPS = [
    ("Audio / video (Av*)",                ["AvGetSavedDataAddress","AvSendTVEncoderOption","AvSetDisplayMode","AvSetSavedDataAddress"]),
    ("Debug / diagnostics",                ["DbgPrint","KeBugCheck"]),
    ("Executive services (Ex*)",           ["ExAllocatePool","ExAllocatePoolWithTag","ExFreePool","ExQueryPoolBlockSize","ExQueryNonVolatileSetting","ExEventObjectType","ExMutantObjectType","ExSemaphoreObjectType","ExTimerObjectType"]),
    ("File-system cache (Fsc*)",           ["FscGetCacheSize","FscSetCacheSize"]),
    ("HAL (Hal*)",                         ["HalBootSMCVideoMode","HalDiskCachePartitionCount","HalGetInterruptVector","HalInitiateShutdown","HalIsResetOrShutdownPending","HalRegisterShutdownNotification","HalReturnToFirmware"]),
    ("I/O manager (Io*)",                  ["IoCreateDevice","IoCreateSymbolicLink","IoDeleteSymbolicLink","IoInvalidDeviceRequest","IoMarkIrpMustComplete","IoStartNextPacket","IoStartPacket","IofCompleteRequest"]),
    ("Kernel services (Ke*)",              ["KeCancelTimer","KeConnectInterrupt","KeDelayExecutionThread","KeDisconnectInterrupt","KeInitializeDpc","KeInitializeInterrupt","KeInitializeTimerEx","KeInsertQueueDpc","KeQueryBasePriorityThread","KeQueryInterruptTime","KeQueryPerformanceCounter","KeQueryPerformanceFrequency","KeQuerySystemTime","KeRaiseIrqlToDpcLevel","KeRemoveQueueDpc","KeRestoreFloatingPointState","KeSaveFloatingPointState","KeSetBasePriorityThread","KeSetDisableBoostThread","KeSetEvent","KeSetTimer","KeStallExecutionProcessor","KeSynchronizeExecution","KeTickCount","KeTimeIncrement","KeWaitForSingleObject"]),
    ("Kernel fastcall (Kf*)",              ["KfLowerIrql","KfRaiseIrql"]),
    ("Launch / image loading",             ["LaunchDataPage","XeLoadSection","XeUnloadSection"]),
    ("Memory manager (Mm*)",               ["MmAllocateContiguousMemory","MmAllocateContiguousMemoryEx","MmAllocateSystemMemory","MmClaimGpuInstanceMemory","MmFreeContiguousMemory","MmFreeSystemMemory","MmGetPhysicalAddress","MmLockUnlockBufferPages","MmLockUnlockPhysicalPage","MmPersistContiguousMemory","MmQueryAddressProtect","MmQueryAllocationSize","MmQueryStatistics","MmSetAddressProtect"]),
    ("Native NT services (Nt*)",           ["NtAllocateVirtualMemory","NtCancelTimer","NtClearEvent","NtClose","NtCreateEvent","NtCreateFile","NtCreateIoCompletion","NtCreateMutant","NtCreateSemaphore","NtCreateTimer","NtDeviceIoControlFile","NtDuplicateObject","NtFlushBuffersFile","NtFreeVirtualMemory","NtFsControlFile","NtOpenFile","NtOpenSymbolicLinkObject","NtProtectVirtualMemory","NtPulseEvent","NtQueueApcThread","NtQueryDirectoryFile","NtQueryFullAttributesFile","NtQueryInformationFile","NtQuerySymbolicLinkObject","NtQueryVirtualMemory","NtQueryVolumeInformationFile","NtReadFile","NtReadFileScatter","NtReleaseMutant","NtReleaseSemaphore","NtRemoveIoCompletion","NtResumeThread","NtSetEvent","NtSetInformationFile","NtSetIoCompletion","NtSetTimerEx","NtSignalAndWaitForSingleObjectEx","NtSuspendThread","NtUserIoApcDispatcher","NtWaitForSingleObject","NtWaitForSingleObjectEx","NtWaitForMultipleObjectsEx","NtWriteFile","NtWriteFileGather","NtYieldExecution"]),
    ("Object manager (Ob*)",               ["ObOpenObjectByName","ObReferenceObjectByHandle","ObfDereferenceObject"]),
    ("Process / thread (Ps*)",             ["PsCreateSystemThreadEx","PsTerminateSystemThread","PsThreadObjectType"]),
    ("Runtime library (Rtl*)",             ["RtlAnsiStringToUnicodeString","RtlCompareMemory","RtlCompareMemoryUlong","RtlEnterCriticalSection","RtlEqualString","RtlFillMemoryUlong","RtlFreeAnsiString","RtlInitAnsiString","RtlInitUnicodeString","RtlInitializeCriticalSection","RtlLeaveCriticalSection","RtlNtStatusToDosError","RtlRaiseException","RtlTimeFieldsToTime","RtlTimeToTimeFields","RtlUnicodeStringToAnsiString","RtlUnwind"]),
    ("Xbox-specific (Xbox*)",              ["XboxHDKey","XboxHardwareInfo","XboxSignatureKey"]),
    ("Cryptography (Xc*)",                 ["XcSHAInit","XcSHAUpdate","XcSHAFinal"]),
]

# Hand-written signatures for exports not in OpenXDK's xboxkrnl.h.
HANDWRITTEN: dict[str, tuple[str, str, bool]] = {  # name -> (ret, args, is_data)
    "ExEventObjectType":            ("POBJECT_TYPE", "", True),
    "ExMutantObjectType":           ("POBJECT_TYPE", "", True),
    "ExSemaphoreObjectType":        ("POBJECT_TYPE", "", True),
    "ExTimerObjectType":            ("POBJECT_TYPE", "", True),
    "PsThreadObjectType":           ("POBJECT_TYPE", "", True),
    "XboxHardwareInfo":             ("XBOX_HARDWARE_INFO", "", True),
    "XboxHDKey":                    ("BYTE", "[16]", True),
    "XboxSignatureKey":             ("BYTE", "[16]", True),
    "HalDiskCachePartitionCount":   ("ULONG", "", True),
    "KeTickCount":                  ("ULONG", "", True),
    "KeTimeIncrement":              ("ULONG", "", True),
    "LaunchDataPage":               ("PVOID", "", True),
    "HalBootSMCVideoMode":          ("ULONG", "", True),
    "DbgPrint":                     ("VOID", "const char *Format, ...", False),
    "KeBugCheck":                   ("VOID", "ULONG BugCheckCode", False),
    "KfRaiseIrql":                  ("UCHAR", "UCHAR NewIrql", False),
    "KfLowerIrql":                  ("VOID", "UCHAR NewIrql", False),
    "ObfDereferenceObject":         ("VOID", "PVOID Object", False),
    "IofCompleteRequest":           ("VOID", "PIRP Irp, CCHAR PriorityBoost", False),
    "KeRestoreFloatingPointState":  ("NTSTATUS", "PKFLOATING_SAVE FloatSave", False),
}

CDECL = {"DbgPrint", "KeBugCheck"}
FASTCALL = {"KfRaiseIrql", "KfLowerIrql", "ObfDereferenceObject", "IofCompleteRequest"}
ORD = {e.ordinal: e.name for e in AZURIK_KERNEL_ORDINALS}
NAME_TO_ORD = {e.name: e.ordinal for e in AZURIK_KERNEL_ORDINALS}

HEADER = """/* Shim-accessible xboxkrnl.exe kernel imports (Phase 2 D1).
 *
 * This header is auto-generated from OpenXDK's xboxkrnl.h (see
 * ``xbox-includes/include/xboxkrnl.h`` and
 * ``scripts/gen_kernel_hdr.py``) by zipping it against the 151
 * kernel ordinals Azurik's vanilla XBE imports (listed in
 * ``azurik_mod/patching/xboxkrnl_ordinals.py``).  Every declaration
 * here has a matching thunk slot in the XBE at load time; the shim
 * layout pipeline generates a 6-byte ``JMP [thunk_va]`` stub per
 * referenced import and resolves the shim's ``call _Foo@N`` REL32
 * relocation to the stub's VA.
 *
 * Coverage: ALL 151 kernel imports Azurik's vanilla XBE references.
 *
 * What this header does NOT give you:
 *   - Kernel functions Azurik does NOT import (most of xboxkrnl has
 *     ~230 more exports).  Adding one would require extending the
 *     XBE's thunk table — tracked as D1-extend in ``docs/SHIMS.md``.
 *   - D3D8 / DSound / XGraphics / XOnline APIs.  Those are
 *     statically linked into Azurik's XBE — call them via the A3
 *     vanilla-symbol registry when a shim needs them.
 *
 * ABI rules (shim author MUST follow):
 *   - Match the declared calling convention exactly.  ``NTAPI`` =
 *     ``__stdcall``; ``FASTCALL`` = ``__fastcall``; ``CDECL`` =
 *     clang's i386 default.  Getting this wrong silently corrupts
 *     the stack — the symptoms typically start with weird values
 *     in unrelated locals after the call returns.
 *   - All pointer / ULONG / LONG / HANDLE args are 4 bytes.
 *     LARGE_INTEGER is 8 bytes as a struct, but every kernel API
 *     takes it BY POINTER, so the arg contributes 4 bytes to the
 *     stdcall arg-byte count.
 *   - BOOLEAN / UCHAR / CCHAR are 1 byte; the stdcall mangler
 *     still counts them as 4 bytes each (i386 stacks are 4-byte
 *     aligned).  E.g. ``NTAPI NTSTATUS KeWaitForSingleObject(PVOID,
 *     ULONG, UCHAR, BOOLEAN, PLARGE_INTEGER)`` = 4+4+4+4+4 = 20
 *     bytes = mangled ``_KeWaitForSingleObject@20``.
 *
 * Drift guard: ``tests/test_kernel_imports.py::HeaderOrdinalDriftGuard``
 * parses every extern in this file and confirms its name is
 * catalogued in ``xboxkrnl_ordinals.py``.  Regenerating the header
 * keeps the two files consistent.
 */
#ifndef AZURIK_KERNEL_H
#define AZURIK_KERNEL_H

#include "azurik.h"

#ifdef __cplusplus
extern "C" {
#endif


/* =======================================================================
 * Primitive + alias typedefs
 * =======================================================================
 * Matches OpenXDK's xboxkrnl.h typedef family.  Shim code that wants
 * stricter typing can ``#include "xboxkrnl.h"`` instead, but for
 * most shims these opaque-pointer aliases are enough — the caller
 * passes through whatever it received from a previous kernel call
 * or struct field. */

/* Freestanding stand-in for wchar_t — clang's ``-ffreestanding`` build
 * doesn't provide ``<wchar.h>``.  Xbox kernel UTF-16 APIs just need
 * a 16-bit type. */
typedef unsigned short          wchar_t;

typedef int                     NTSTATUS;
typedef unsigned int            ULONG, *PULONG, *PDWORD;
typedef int                     LONG, *PLONG;
typedef unsigned short          USHORT, *PUSHORT;
typedef short                   SHORT;
typedef unsigned char           UCHAR, *PUCHAR;
typedef signed char             CCHAR;
typedef unsigned char           BYTE;
typedef unsigned char           BOOLEAN, *PBOOLEAN;
typedef unsigned int            DWORD;
typedef unsigned long long      ULONGLONG;
typedef unsigned int            SIZE_T, *PSIZE_T;
typedef void                    VOID;
typedef void *                  PVOID;
typedef const void *            PCVOID;
typedef void *                  HANDLE, *PHANDLE;
typedef unsigned int            ACCESS_MASK;
typedef unsigned int            KPRIORITY;
typedef unsigned int            LOGICAL;
typedef const char *            PCSZ;
typedef const wchar_t *         PCWSTR;
typedef unsigned int            ULONG_PTR;
typedef unsigned char           KIRQL, *PKIRQL;
typedef signed int              KPROCESSOR_MODE;
typedef signed int              KWAIT_REASON;
typedef signed int              KINTERRUPT_MODE;
typedef signed int              WAIT_TYPE;
typedef signed int              EVENT_TYPE;
typedef signed int              TIMER_TYPE;
typedef signed int              DEVICE_TYPE;
typedef signed int              FIRMWARE_REENTRY;
typedef signed int              FILE_INFORMATION_CLASS;
typedef signed int              FS_INFORMATION_CLASS;
typedef signed int              MEMORY_INFORMATION_CLASS;
typedef unsigned int            POOL_TYPE;

/* Struct / opaque pointer aliases — shim code passes these through
 * as black boxes.  Add stricter types to your local shim source if
 * your logic inspects the structs; these aliases default to void*
 * so every signature below compiles without xboxkrnl.h itself. */
typedef unsigned int            PFN_COUNT;     /* page-frame counts */
typedef void *                  KDPC;
typedef void *                  LARGE_INTEGER;
typedef void *                  PFILE_NETWORK_OPEN_INFORMATION;
typedef void *                  PFILE_SEGMENT_ELEMENT;
typedef void *                  PIO_APC_ROUTINE;
typedef void *                  PHAL_SHUTDOWN_REGISTRATION;
typedef void *                  POBJECT_TYPE;
typedef void *                  PDEVICE_OBJECT;
typedef void *                  PDRIVER_OBJECT;
typedef void *                  PIRP;
typedef void *                  PKEVENT, *PRKEVENT;
typedef void *                  PKTIMER;
typedef void *                  PKTHREAD;
typedef void *                  PKMUTANT;
typedef void *                  PKSEMAPHORE;
typedef void *                  PKINTERRUPT;
typedef void *                  PKDPC, *PRKDPC;
typedef void *                  PKQUEUE;
typedef void *                  POBJECT_ATTRIBUTES;
typedef void *                  PIO_STATUS_BLOCK;
typedef void *                  PLARGE_INTEGER;
typedef void *                  PKFLOATING_SAVE;
typedef void *                  PSTRING;
typedef void *                  PANSI_STRING, *PCANSI_STRING;
typedef void *                  PUNICODE_STRING, *PCUNICODE_STRING;
typedef void *                  POBJECT_STRING;
typedef void *                  PTIME_FIELDS;
typedef void *                  PEXCEPTION_RECORD;
typedef void *                  PCONTEXT;
typedef void *                  PMM_STATISTICS;
typedef void *                  PMEMORY_BASIC_INFORMATION;
typedef void *                  PRTL_CRITICAL_SECTION;
typedef void *                  PFILE_OBJECT;
typedef void *                  PXBEIMAGE_SECTION;
typedef void *                  XBOX_HARDWARE_INFO;

/* Callback prototypes — shim code that actually registers a kernel
 * callback can provide a matching-typed function pointer. */
typedef void     (*PKSTART_ROUTINE)(PVOID);
typedef void     (*PKDEFERRED_ROUTINE)(PKDPC, PVOID, PVOID, PVOID);
typedef void     (*PKSYSTEM_ROUTINE)(PKSTART_ROUTINE, PVOID);
typedef BOOLEAN  (*PKSYNCHRONIZE_ROUTINE)(PVOID);
typedef BOOLEAN  (*PKSERVICE_ROUTINE)(PKINTERRUPT, PVOID);
typedef void     (*PKINTERRUPT_ROUTINE)(void);
typedef NTSTATUS (*PDRIVER_STARTIO)(PDEVICE_OBJECT, PIRP);
typedef void     (*PPS_APC_ROUTINE)(PVOID, PVOID, PVOID);
typedef void     (*PTIMER_APC_ROUTINE)(PVOID, ULONG, ULONG);
typedef void     (*PSHUTDOWN_REGISTRATION)(void);

/* Calling-convention spelling — the layout_coff resolver keys off
 * the COFF symbol name, which in turn comes from these decorations. */
#define NTAPI           __attribute__((stdcall))
#define FASTCALL        __attribute__((fastcall))
#define CDECL           /* clang i386 default */
#define DECLSPEC_NORETURN __attribute__((noreturn))
"""

FOOTER = """

#ifdef __cplusplus
}
#endif

#endif /* AZURIK_KERNEL_H */
"""

out = [HEADER]

for title, names in GROUPS:
    out.append(f"\n/* =======================================================================\n"
               f" * {title}\n"
               f" * ===================================================================== */\n")
    for name in names:
        ord_num = NAME_TO_ORD.get(name)
        if ord_num is None:
            continue
        if name in HANDWRITTEN:
            ret, args, is_data = HANDWRITTEN[name]
            if is_data:
                if args == "[16]":
                    out.append(f"/* Kernel ordinal {ord_num} — data export (16-byte buffer). */")
                    out.append(f"extern {ret} {name}[16];\n")
                elif args == "":
                    out.append(f"/* Kernel ordinal {ord_num} — data export (read via &{name}). */")
                    out.append(f"extern {ret} {name};\n")
                continue
            out.append(f"/* Kernel ordinal {ord_num}. */")
            if name in CDECL:
                out.append(f"CDECL {ret} {name}({args});\n")
            elif name in FASTCALL:
                out.append(f"FASTCALL {ret} {name}({args});\n")
            else:
                out.append(f"NTAPI {ret} {name}({args});\n")
        elif name in decls:
            ret, args = decls[name]
            out.append(f"/* Kernel ordinal {ord_num}. */")
            if name in CDECL:
                out.append(f"CDECL {ret} {name}({args});\n")
            elif name in FASTCALL:
                out.append(f"FASTCALL {ret} {name}({args});\n")
            else:
                out.append(f"NTAPI {ret} {name}({args});\n")
        else:
            sys.stderr.write(f"WARN: no signature for {name}\n")
            out.append(f"/* TODO: no upstream signature for {name} (ordinal {ord_num}). */\n")

out.append(FOOTER)
print("\n".join(out))
