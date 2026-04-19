/* Shim-accessible xboxkrnl.exe kernel imports (Phase 2 D1).
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


/* =======================================================================
 * Audio / video (Av*)
 * ===================================================================== */

/* Kernel ordinal 1. */
NTAPI PVOID AvGetSavedDataAddress(void);

/* Kernel ordinal 2. */
NTAPI VOID AvSendTVEncoderOption(PVOID RegisterBase, ULONG Option, ULONG Param, PULONG Result);

/* Kernel ordinal 3. */
NTAPI ULONG AvSetDisplayMode(PVOID RegisterBase, ULONG Step, ULONG DisplayMode, ULONG SourceColorFormat, ULONG Pitch, ULONG FrameBuffer);

/* Kernel ordinal 4. */
NTAPI VOID AvSetSavedDataAddress(PVOID Address);


/* =======================================================================
 * Debug / diagnostics
 * ===================================================================== */

/* Kernel ordinal 8. */
CDECL VOID DbgPrint(const char *Format, ...);

/* Kernel ordinal 95. */
CDECL VOID KeBugCheck(ULONG BugCheckCode);


/* =======================================================================
 * Executive services (Ex*)
 * ===================================================================== */

/* Kernel ordinal 14. */
NTAPI PVOID ExAllocatePool(SIZE_T NumberOfBytes);

/* Kernel ordinal 15. */
NTAPI PVOID ExAllocatePoolWithTag(SIZE_T NumberOfBytes, ULONG Tag);

/* Kernel ordinal 17. */
NTAPI VOID ExFreePool(PVOID P);

/* Kernel ordinal 23. */
NTAPI ULONG ExQueryPoolBlockSize(PVOID PoolBlock);

/* Kernel ordinal 24. */
NTAPI NTSTATUS ExQueryNonVolatileSetting(ULONG ValueIndex, PULONG Type, PVOID Value, ULONG ValueLength, PULONG ResultLength);

/* Kernel ordinal 16 — data export (read via &ExEventObjectType). */
extern POBJECT_TYPE ExEventObjectType;

/* Kernel ordinal 22 — data export (read via &ExMutantObjectType). */
extern POBJECT_TYPE ExMutantObjectType;

/* Kernel ordinal 30 — data export (read via &ExSemaphoreObjectType). */
extern POBJECT_TYPE ExSemaphoreObjectType;

/* Kernel ordinal 31 — data export (read via &ExTimerObjectType). */
extern POBJECT_TYPE ExTimerObjectType;


/* =======================================================================
 * File-system cache (Fsc*)
 * ===================================================================== */

/* Kernel ordinal 35. */
NTAPI PFN_COUNT FscGetCacheSize(void);

/* Kernel ordinal 37. */
NTAPI NTSTATUS FscSetCacheSize(PFN_COUNT NumberOfCachePages);


/* =======================================================================
 * HAL (Hal*)
 * ===================================================================== */

/* Kernel ordinal 356 — data export (read via &HalBootSMCVideoMode). */
extern ULONG HalBootSMCVideoMode;

/* Kernel ordinal 40 — data export (read via &HalDiskCachePartitionCount). */
extern ULONG HalDiskCachePartitionCount;

/* Kernel ordinal 44. */
NTAPI ULONG HalGetInterruptVector(ULONG BusInterruptLevel, PKIRQL Irql);

/* Kernel ordinal 360. */
NTAPI VOID HalInitiateShutdown(void);

/* Kernel ordinal 358. */
NTAPI BOOLEAN HalIsResetOrShutdownPending(void);

/* Kernel ordinal 47. */
NTAPI VOID HalRegisterShutdownNotification(PHAL_SHUTDOWN_REGISTRATION ShutdownRegistration, BOOLEAN Register);

/* Kernel ordinal 49. */
NTAPI VOID DECLSPEC_NORETURN HalReturnToFirmware(FIRMWARE_REENTRY Routine);


/* =======================================================================
 * I/O manager (Io*)
 * ===================================================================== */

/* Kernel ordinal 65. */
NTAPI NTSTATUS IoCreateDevice(PDRIVER_OBJECT DriverObject, ULONG DeviceExtensionSize, POBJECT_STRING DeviceName , DEVICE_TYPE DeviceType, BOOLEAN Exclusive, PDEVICE_OBJECT *DeviceObject);

/* Kernel ordinal 67. */
NTAPI NTSTATUS IoCreateSymbolicLink(POBJECT_STRING SymbolicLinkName, POBJECT_STRING DeviceName);

/* Kernel ordinal 69. */
NTAPI NTSTATUS IoDeleteSymbolicLink(POBJECT_STRING SymbolicLinkName);

/* Kernel ordinal 74. */
NTAPI NTSTATUS IoInvalidDeviceRequest(PDEVICE_OBJECT DeviceObject, PIRP Irp);

/* Kernel ordinal 359. */
NTAPI VOID IoMarkIrpMustComplete(PIRP Irp);

/* Kernel ordinal 81. */
NTAPI VOID IoStartNextPacket(PDEVICE_OBJECT DeviceObject);

/* Kernel ordinal 83. */
NTAPI VOID IoStartPacket(PDEVICE_OBJECT DeviceObject, PIRP Irp, PULONG Key);

/* Kernel ordinal 87. */
FASTCALL VOID IofCompleteRequest(PIRP Irp, CCHAR PriorityBoost);


/* =======================================================================
 * Kernel services (Ke*)
 * ===================================================================== */

/* Kernel ordinal 97. */
NTAPI BOOLEAN KeCancelTimer(PKTIMER Timer);

/* Kernel ordinal 98. */
NTAPI BOOLEAN KeConnectInterrupt(PKINTERRUPT Interrupt);

/* Kernel ordinal 99. */
NTAPI NTSTATUS KeDelayExecutionThread(KPROCESSOR_MODE WaitMode, BOOLEAN Alertable, PLARGE_INTEGER Interval);

/* Kernel ordinal 100. */
NTAPI BOOLEAN KeDisconnectInterrupt(PKINTERRUPT Interrupt);

/* Kernel ordinal 107. */
NTAPI VOID KeInitializeDpc(KDPC *Dpc, PKDEFERRED_ROUTINE DeferredRoutine, PVOID DeferredContext);

/* Kernel ordinal 109. */
NTAPI VOID KeInitializeInterrupt(PKINTERRUPT Interrupt, PKSERVICE_ROUTINE ServiceRoutine, PVOID ServiceContext, ULONG Vector, KIRQL Irql, KINTERRUPT_MODE InterruptMode, BOOLEAN ShareVector);

/* Kernel ordinal 113. */
NTAPI VOID KeInitializeTimerEx(PKTIMER Timer, TIMER_TYPE Type);

/* Kernel ordinal 119. */
NTAPI BOOLEAN KeInsertQueueDpc(PRKDPC Dpc, PVOID SystemArgument1, PVOID SystemArgument2);

/* Kernel ordinal 124. */
NTAPI LONG KeQueryBasePriorityThread(PKTHREAD Thread);

/* Kernel ordinal 125. */
NTAPI ULONGLONG KeQueryInterruptTime(void);

/* Kernel ordinal 126. */
NTAPI ULONGLONG KeQueryPerformanceCounter(void);

/* Kernel ordinal 127. */
NTAPI ULONGLONG KeQueryPerformanceFrequency(void);

/* Kernel ordinal 128. */
NTAPI VOID KeQuerySystemTime(PLARGE_INTEGER CurrentTime);

/* Kernel ordinal 129. */
NTAPI KIRQL KeRaiseIrqlToDpcLevel(void);

/* Kernel ordinal 137. */
NTAPI BOOLEAN KeRemoveQueueDpc(PRKDPC Dpc);

/* Kernel ordinal 139. */
NTAPI NTSTATUS KeRestoreFloatingPointState(PKFLOATING_SAVE FloatSave);

/* Kernel ordinal 142. */
NTAPI NTSTATUS KeSaveFloatingPointState(PKFLOATING_SAVE FloatSave);

/* Kernel ordinal 143. */
NTAPI LONG KeSetBasePriorityThread(PKTHREAD Thread, LONG Increment);

/* Kernel ordinal 144. */
NTAPI LOGICAL KeSetDisableBoostThread(PKTHREAD Thread, LOGICAL Disable);

/* Kernel ordinal 145. */
NTAPI LONG KeSetEvent(PRKEVENT Event, KPRIORITY Increment, BOOLEAN Wait);

/* Kernel ordinal 149. */
NTAPI BOOLEAN KeSetTimer(PKTIMER Timer, LARGE_INTEGER DueTime, PKDPC Dpc);

/* Kernel ordinal 151. */
NTAPI VOID KeStallExecutionProcessor(ULONG MicroSeconds);

/* Kernel ordinal 153. */
NTAPI BOOLEAN KeSynchronizeExecution(PKINTERRUPT Interrupt, PKSYNCHRONIZE_ROUTINE SynchronizeRoutine, PVOID SynchronizeContext);

/* Kernel ordinal 156 — data export (read via &KeTickCount). */
extern ULONG KeTickCount;

/* Kernel ordinal 157 — data export (read via &KeTimeIncrement). */
extern ULONG KeTimeIncrement;

/* Kernel ordinal 159. */
NTAPI NTSTATUS KeWaitForSingleObject(PVOID Object, KWAIT_REASON WaitReason, KPROCESSOR_MODE WaitMode, BOOLEAN Alertable, PLARGE_INTEGER Timeout);


/* =======================================================================
 * Kernel fastcall (Kf*)
 * ===================================================================== */

/* Kernel ordinal 161. */
FASTCALL VOID KfLowerIrql(UCHAR NewIrql);

/* Kernel ordinal 160. */
FASTCALL UCHAR KfRaiseIrql(UCHAR NewIrql);


/* =======================================================================
 * Launch / image loading
 * ===================================================================== */

/* Kernel ordinal 164 — data export (read via &LaunchDataPage). */
extern PVOID LaunchDataPage;

/* Kernel ordinal 327. */
NTAPI NTSTATUS XeLoadSection(PXBEIMAGE_SECTION Section);

/* Kernel ordinal 328. */
NTAPI NTSTATUS XeUnloadSection(PXBEIMAGE_SECTION Section);


/* =======================================================================
 * Memory manager (Mm*)
 * ===================================================================== */

/* Kernel ordinal 165. */
NTAPI PVOID MmAllocateContiguousMemory(SIZE_T NumberOfBytes);

/* Kernel ordinal 166. */
NTAPI PVOID MmAllocateContiguousMemoryEx(SIZE_T NumberOfBytes, ULONG_PTR LowestAcceptableAddress, ULONG_PTR HighestAcceptableAddress, ULONG_PTR Alignment, ULONG Protect);

/* Kernel ordinal 167. */
NTAPI PVOID MmAllocateSystemMemory(SIZE_T NumberOfBytes, ULONG Protect);

/* Kernel ordinal 168. */
NTAPI PVOID MmClaimGpuInstanceMemory(SIZE_T NumberOfBytes, SIZE_T *NumberOfPaddingBytes);

/* Kernel ordinal 171. */
NTAPI VOID MmFreeContiguousMemory(PVOID BaseAddress);

/* Kernel ordinal 172. */
NTAPI ULONG MmFreeSystemMemory(PVOID BaseAddress, SIZE_T NumberOfBytes);

/* Kernel ordinal 173. */
NTAPI ULONG_PTR MmGetPhysicalAddress(PVOID BaseAddress);

/* Kernel ordinal 175. */
NTAPI VOID MmLockUnlockBufferPages(PVOID BaseAddress, SIZE_T NumberOfBytes, BOOLEAN UnlockPages);

/* Kernel ordinal 176. */
NTAPI VOID MmLockUnlockPhysicalPage(ULONG_PTR PhysicalAddress, BOOLEAN UnlockPage);

/* Kernel ordinal 178. */
NTAPI VOID MmPersistContiguousMemory(PVOID BaseAddress, SIZE_T NumberOfBytes, BOOLEAN Persist);

/* Kernel ordinal 179. */
NTAPI ULONG MmQueryAddressProtect(PVOID VirtualAddress);

/* Kernel ordinal 180. */
NTAPI SIZE_T MmQueryAllocationSize(PVOID BaseAddress);

/* Kernel ordinal 181. */
NTAPI NTSTATUS MmQueryStatistics(PMM_STATISTICS MemoryStatistics);

/* Kernel ordinal 182. */
NTAPI VOID MmSetAddressProtect(PVOID BaseAddress, ULONG NumberOfBytes, ULONG NewProtect);


/* =======================================================================
 * Native NT services (Nt*)
 * ===================================================================== */

/* Kernel ordinal 184. */
NTAPI NTSTATUS NtAllocateVirtualMemory(PVOID *BaseAddress, ULONG_PTR ZeroBits, PSIZE_T RegionSize, ULONG AllocationType, ULONG Protect);

/* Kernel ordinal 185. */
NTAPI NTSTATUS NtCancelTimer(HANDLE TimerHandle, PBOOLEAN CurrentState);

/* Kernel ordinal 186. */
NTAPI NTSTATUS NtClearEvent(HANDLE EventHandle);

/* Kernel ordinal 187. */
NTAPI NTSTATUS NtClose(HANDLE Handle);

/* Kernel ordinal 189. */
NTAPI NTSTATUS NtCreateEvent(PHANDLE EventHandle, POBJECT_ATTRIBUTES ObjectAttributes , EVENT_TYPE EventType, BOOLEAN InitialState);

/* Kernel ordinal 190. */
NTAPI NTSTATUS NtCreateFile(PHANDLE FileHandle, ACCESS_MASK DesiredAccess, POBJECT_ATTRIBUTES ObjectAttributes, PIO_STATUS_BLOCK IoStatusBlock, PLARGE_INTEGER AllocationSize , ULONG FileAttributes, ULONG ShareAccess, ULONG CreateDisposition, ULONG CreateOptions);

/* Kernel ordinal 191. */
NTAPI NTSTATUS NtCreateIoCompletion(PHANDLE IoCompletionHandle, ACCESS_MASK DesiredAccess, POBJECT_ATTRIBUTES ObjectAttributes , ULONG Count);

/* Kernel ordinal 192. */
NTAPI NTSTATUS NtCreateMutant(PHANDLE MutantHandle, POBJECT_ATTRIBUTES ObjectAttributes , BOOLEAN InitialOwner);

/* Kernel ordinal 193. */
NTAPI NTSTATUS NtCreateSemaphore(PHANDLE SemaphoreHandle, POBJECT_ATTRIBUTES ObjectAttributes , LONG InitialCount, LONG MaximumCount);

/* Kernel ordinal 194. */
NTAPI NTSTATUS NtCreateTimer(PHANDLE TimerHandle, POBJECT_ATTRIBUTES ObjectAttributes, TIMER_TYPE TimerType);

/* Kernel ordinal 196. */
NTAPI NTSTATUS NtDeviceIoControlFile(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, ULONG IoControlCode, PVOID InputBuffer , ULONG InputBufferLength, PVOID OutputBuffer , ULONG OutputBufferLength);

/* Kernel ordinal 197. */
NTAPI NTSTATUS NtDuplicateObject(HANDLE SourceHandle, PHANDLE TargetHandle, ULONG Options);

/* Kernel ordinal 198. */
NTAPI NTSTATUS NtFlushBuffersFile(HANDLE FileHandle, PIO_STATUS_BLOCK IoStatusBlock);

/* Kernel ordinal 199. */
NTAPI NTSTATUS NtFreeVirtualMemory(PVOID *BaseAddress, PSIZE_T RegionSize, ULONG FreeType);

/* Kernel ordinal 200. */
NTAPI NTSTATUS NtFsControlFile(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, ULONG FsControlCode, PVOID InputBuffer , ULONG InputBufferLength, PVOID OutputBuffer , ULONG OutputBufferLength);

/* Kernel ordinal 202. */
NTAPI NTSTATUS NtOpenFile(PHANDLE FileHandle, ACCESS_MASK DesiredAccess, POBJECT_ATTRIBUTES ObjectAttributes, PIO_STATUS_BLOCK IoStatusBlock, ULONG ShareAccess, ULONG OpenOptions);

/* Kernel ordinal 203. */
NTAPI NTSTATUS NtOpenSymbolicLinkObject(PHANDLE LinkHandle, POBJECT_ATTRIBUTES ObjectAttributes);

/* Kernel ordinal 204. */
NTAPI NTSTATUS NtProtectVirtualMemory(PVOID *BaseAddress, PSIZE_T RegionSize, ULONG NewProtect, PULONG OldProtect);

/* Kernel ordinal 205. */
NTAPI NTSTATUS NtPulseEvent(HANDLE EventHandle, PLONG PreviousState);

/* Kernel ordinal 206. */
NTAPI NTSTATUS NtQueueApcThread(HANDLE ThreadHandle, PPS_APC_ROUTINE ApcRoutine, PVOID ApcArgument1, PVOID ApcArgument2, PVOID ApcArgument3);

/* Kernel ordinal 207. */
NTAPI NTSTATUS NtQueryDirectoryFile(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, PVOID FileInformation, ULONG Length, FILE_INFORMATION_CLASS FileInformationClass, POBJECT_STRING FileName , BOOLEAN RestartScan);

/* Kernel ordinal 210. */
NTAPI NTSTATUS NtQueryFullAttributesFile(POBJECT_ATTRIBUTES ObjectAttributes, PFILE_NETWORK_OPEN_INFORMATION FileInformation);

/* Kernel ordinal 211. */
NTAPI NTSTATUS NtQueryInformationFile(HANDLE FileHandle, PIO_STATUS_BLOCK IoStatusBlock, PVOID FileInformation, ULONG Length, FILE_INFORMATION_CLASS FileInformationClass);

/* Kernel ordinal 215. */
NTAPI NTSTATUS NtQuerySymbolicLinkObject(HANDLE LinkHandle, POBJECT_STRING LinkTarget, PULONG ReturnedLength);

/* Kernel ordinal 217. */
NTAPI NTSTATUS NtQueryVirtualMemory(PVOID BaseAddress, PMEMORY_BASIC_INFORMATION MemoryInformation);

/* Kernel ordinal 218. */
NTAPI NTSTATUS NtQueryVolumeInformationFile(HANDLE FileHandle, PIO_STATUS_BLOCK IoStatusBlock, PVOID FsInformation, ULONG Length, FS_INFORMATION_CLASS FsInformationClass);

/* Kernel ordinal 219. */
NTAPI NTSTATUS NtReadFile(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, PVOID Buffer, ULONG Length, PLARGE_INTEGER ByteOffset);

/* Kernel ordinal 220. */
NTAPI NTSTATUS NtReadFileScatter(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, PFILE_SEGMENT_ELEMENT SegmentArray, ULONG Length, PLARGE_INTEGER ByteOffset);

/* Kernel ordinal 221. */
NTAPI NTSTATUS NtReleaseMutant(HANDLE MutantHandle, PLONG PreviousCount);

/* Kernel ordinal 222. */
NTAPI NTSTATUS NtReleaseSemaphore(HANDLE SemaphoreHandle, LONG ReleaseCount, PLONG PreviousCount);

/* Kernel ordinal 223. */
NTAPI NTSTATUS NtRemoveIoCompletion(HANDLE IoCompletionHandle, PVOID *KeyContext, PVOID *ApcContext, PIO_STATUS_BLOCK IoStatusBlock, PLARGE_INTEGER Timeout);

/* Kernel ordinal 224. */
NTAPI NTSTATUS NtResumeThread(HANDLE ThreadHandle, PULONG PreviousSuspendCount);

/* Kernel ordinal 225. */
NTAPI NTSTATUS NtSetEvent(HANDLE EventHandle, PLONG PreviousState);

/* Kernel ordinal 226. */
NTAPI NTSTATUS NtSetInformationFile(HANDLE FileHandle, PIO_STATUS_BLOCK IoStatusBlock, PVOID FileInformation, ULONG Length, FILE_INFORMATION_CLASS FileInformationClass);

/* Kernel ordinal 227. */
NTAPI NTSTATUS NtSetIoCompletion(HANDLE IoCompletionHandle, PVOID KeyContext, PVOID ApcContext, NTSTATUS IoStatus, ULONG_PTR IoStatusInformation);

/* Kernel ordinal 229. */
NTAPI NTSTATUS NtSetTimerEx(HANDLE TimerHandle, PLARGE_INTEGER DueTime, PTIMER_APC_ROUTINE TimerApcRoutine , KPROCESSOR_MODE ApcMode, PVOID TimerContext , BOOLEAN ResumeTimer, LONG Period , PBOOLEAN PreviousState);

/* Kernel ordinal 230. */
NTAPI NTSTATUS NtSignalAndWaitForSingleObjectEx(HANDLE SignalHandle, HANDLE WaitHandle, KPROCESSOR_MODE WaitMode, BOOLEAN Alertable, PLARGE_INTEGER Timeout);

/* Kernel ordinal 231. */
NTAPI NTSTATUS NtSuspendThread(HANDLE ThreadHandle, PULONG PreviousSuspendCount);

/* Kernel ordinal 232. */
NTAPI VOID NtUserIoApcDispatcher(PVOID ApcContext, PIO_STATUS_BLOCK IoStatusBlock, ULONG Reserved);

/* Kernel ordinal 233. */
NTAPI NTSTATUS NtWaitForSingleObject(HANDLE Handle, BOOLEAN Alertable, PLARGE_INTEGER Timeout);

/* Kernel ordinal 234. */
NTAPI NTSTATUS NtWaitForSingleObjectEx(HANDLE Handle, KPROCESSOR_MODE WaitMode, BOOLEAN Alertable, PLARGE_INTEGER Timeout);

/* Kernel ordinal 235. */
NTAPI NTSTATUS NtWaitForMultipleObjectsEx(ULONG Count, HANDLE Handles[], WAIT_TYPE WaitType, KPROCESSOR_MODE WaitMode, BOOLEAN Alertable, PLARGE_INTEGER Timeout);

/* Kernel ordinal 236. */
NTAPI NTSTATUS NtWriteFile(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, PVOID Buffer, ULONG Length, PLARGE_INTEGER ByteOffset);

/* Kernel ordinal 237. */
NTAPI BOOLEAN NtWriteFileGather(HANDLE FileHandle, HANDLE Event , PIO_APC_ROUTINE ApcRoutine , PVOID ApcContext , PIO_STATUS_BLOCK IoStatusBlock, PFILE_SEGMENT_ELEMENT SegmentArray, ULONG Length, PLARGE_INTEGER ByteOffset);

/* Kernel ordinal 238. */
NTAPI NTSTATUS NtYieldExecution(void);


/* =======================================================================
 * Object manager (Ob*)
 * ===================================================================== */

/* Kernel ordinal 243. */
NTAPI NTSTATUS ObOpenObjectByName(POBJECT_ATTRIBUTES ObjectAttributes, POBJECT_TYPE ObjectType, PVOID ParseContext , PHANDLE Handle);

/* Kernel ordinal 246. */
NTAPI BOOLEAN ObReferenceObjectByHandle(HANDLE Handle, POBJECT_TYPE ObjectType , PVOID *ReturnedObject);

/* Kernel ordinal 250. */
FASTCALL VOID ObfDereferenceObject(PVOID Object);


/* =======================================================================
 * Process / thread (Ps*)
 * ===================================================================== */

/* Kernel ordinal 255. */
NTAPI NTSTATUS PsCreateSystemThreadEx(PHANDLE ThreadHandle, SIZE_T ThreadExtensionSize, SIZE_T KernelStackSize, SIZE_T TlsDataSize, PHANDLE ThreadId , PKSTART_ROUTINE StartRoutine, PVOID StartContext, BOOLEAN CreateSuspended, BOOLEAN DebuggerThread, PKSYSTEM_ROUTINE SystemRoutine);

/* Kernel ordinal 258. */
NTAPI VOID PsTerminateSystemThread(NTSTATUS ExitStatus);

/* Kernel ordinal 259 — data export (read via &PsThreadObjectType). */
extern POBJECT_TYPE PsThreadObjectType;


/* =======================================================================
 * Runtime library (Rtl*)
 * ===================================================================== */

/* Kernel ordinal 260. */
NTAPI NTSTATUS RtlAnsiStringToUnicodeString(PUNICODE_STRING DestinationString, PSTRING SourceString, BOOLEAN AllocateDestinationString);

/* Kernel ordinal 268. */
NTAPI SIZE_T RtlCompareMemory(VOID *Source1, VOID *Source2, SIZE_T Length);

/* Kernel ordinal 269. */
NTAPI SIZE_T RtlCompareMemoryUlong(PVOID Source, SIZE_T Length, ULONG Pattern);

/* Kernel ordinal 277. */
NTAPI VOID RtlEnterCriticalSection(PRTL_CRITICAL_SECTION CriticalSection);

/* Kernel ordinal 279. */
NTAPI BOOLEAN RtlEqualString(PSTRING String1, PSTRING String2, BOOLEAN CaseInSensitive);

/* Kernel ordinal 285. */
NTAPI VOID RtlFillMemoryUlong(PVOID Destination, SIZE_T Length, ULONG Pattern);

/* Kernel ordinal 286. */
NTAPI VOID RtlFreeAnsiString(PANSI_STRING AnsiString);

/* Kernel ordinal 289. */
NTAPI VOID RtlInitAnsiString(PANSI_STRING DestinationString, PCSZ SourceString);

/* Kernel ordinal 290. */
NTAPI VOID RtlInitUnicodeString(PUNICODE_STRING DestinationString, PCWSTR SourceString);

/* Kernel ordinal 291. */
NTAPI VOID RtlInitializeCriticalSection(PRTL_CRITICAL_SECTION CriticalSection);

/* Kernel ordinal 294. */
NTAPI VOID RtlLeaveCriticalSection(PRTL_CRITICAL_SECTION CriticalSection);

/* Kernel ordinal 301. */
NTAPI ULONG RtlNtStatusToDosError(NTSTATUS Status);

/* Kernel ordinal 302. */
NTAPI VOID RtlRaiseException(PEXCEPTION_RECORD ExceptionRecord);

/* Kernel ordinal 304. */
NTAPI BOOLEAN RtlTimeFieldsToTime(PTIME_FIELDS TimeFields, PLARGE_INTEGER Time);

/* Kernel ordinal 305. */
NTAPI VOID RtlTimeToTimeFields(PLARGE_INTEGER Time, PTIME_FIELDS TimeFields);

/* Kernel ordinal 308. */
NTAPI NTSTATUS RtlUnicodeStringToAnsiString(PSTRING DestinationString, PUNICODE_STRING SourceString, BOOLEAN AllocateDestinationString);

/* Kernel ordinal 312. */
NTAPI VOID RtlUnwind(PVOID TargetFrame , PVOID TargetIp , PEXCEPTION_RECORD ExceptionRecord , PVOID ReturnValue);


/* =======================================================================
 * Xbox-specific (Xbox*)
 * ===================================================================== */

/* Kernel ordinal 323 — data export (16-byte buffer). */
extern BYTE XboxHDKey[16];

/* Kernel ordinal 322 — data export (read via &XboxHardwareInfo). */
extern XBOX_HARDWARE_INFO XboxHardwareInfo;

/* Kernel ordinal 325 — data export (16-byte buffer). */
extern BYTE XboxSignatureKey[16];


/* =======================================================================
 * Cryptography (Xc*)
 * ===================================================================== */

/* Kernel ordinal 335. */
NTAPI VOID XcSHAInit(PUCHAR pbSHAContext);

/* Kernel ordinal 336. */
NTAPI VOID XcSHAUpdate(PUCHAR pbSHAContext, PUCHAR pbInput, ULONG dwInputLength);

/* Kernel ordinal 337. */
NTAPI VOID XcSHAFinal(PUCHAR pbSHAContext, PUCHAR pbDigest);



#ifdef __cplusplus
}
#endif

#endif /* AZURIK_KERNEL_H */

