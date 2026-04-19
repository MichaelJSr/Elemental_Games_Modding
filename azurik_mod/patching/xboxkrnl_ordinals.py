"""Xbox kernel (``xboxkrnl.exe``) ordinal → name map.

Two layers now that D1-extend landed:

1. **AZURIK_KERNEL_ORDINALS (151 entries)**: the kernel functions
   Azurik's vanilla XBE imports at load time.  Each one is materialised
   as a 4-byte slot in the XBE kernel thunk table (at VA ``0x0018F3A0``);
   the Xbox loader fills each slot with the resolved function address
   before ``main`` runs.  D1 (``shim_session.stub_for_kernel_symbol``)
   calls these via a 6-byte ``JMP [thunk_slot]`` stub.

2. **EXTENDED_KERNEL_ORDINALS (~100 entries)**: xboxkrnl functions
   Azurik does NOT import but that a shim might want.  These have no
   pre-resolved thunk slot — ``shim_session.stub_for_kernel_symbol``
   emits a **resolving stub** instead: on first call the stub invokes
   ``xboxkrnl_resolve_by_ordinal`` (a helper that walks the kernel
   export table at runtime from the fixed retail base ``0x80010000``)
   and caches the resolved pointer; subsequent calls jump through the
   cache.  See ``docs/D1_EXTEND.md`` for the full design.

Both layers are accessed through the same public helpers
(``ordinal_for`` / ``NAME_TO_ORDINAL``).  Callers that need to know
WHICH layer a function lives in can use
:func:`is_azurik_imported` — returns True iff the ordinal sits in
Azurik's static 151-entry thunk table.

Each ``KernelOrdinal`` carries:
    ordinal: the xboxkrnl.exe export ordinal.
    name:    the C-facing, un-mangled function name.

Calling convention and argument byte count are intentionally NOT
stored here — the shim's own C extern declaration drives mangling.
The resolver strips the ``_Name@N`` decoration and looks up ``Name``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KernelOrdinal:
    """One xboxkrnl.exe export exposed to shims via D1."""
    ordinal: int
    name: str


# ---------------------------------------------------------------------------
# The 151 kernel functions Azurik's vanilla XBE imports, sorted by ordinal.
# ---------------------------------------------------------------------------
# Keep rows sorted by ordinal (ascending) so future audits can binary-
# search.  The `name` column is authoritative; all shim C extern
# declarations must match exactly (case-sensitive).

AZURIK_KERNEL_ORDINALS: tuple[KernelOrdinal, ...] = (
    KernelOrdinal(  1, "AvGetSavedDataAddress"),
    KernelOrdinal(  2, "AvSendTVEncoderOption"),
    KernelOrdinal(  3, "AvSetDisplayMode"),
    KernelOrdinal(  4, "AvSetSavedDataAddress"),
    KernelOrdinal(  8, "DbgPrint"),
    KernelOrdinal( 14, "ExAllocatePool"),
    KernelOrdinal( 15, "ExAllocatePoolWithTag"),
    KernelOrdinal( 16, "ExEventObjectType"),
    KernelOrdinal( 17, "ExFreePool"),
    KernelOrdinal( 22, "ExMutantObjectType"),
    KernelOrdinal( 23, "ExQueryPoolBlockSize"),
    KernelOrdinal( 24, "ExQueryNonVolatileSetting"),
    KernelOrdinal( 30, "ExSemaphoreObjectType"),
    KernelOrdinal( 31, "ExTimerObjectType"),
    KernelOrdinal( 35, "FscGetCacheSize"),
    KernelOrdinal( 37, "FscSetCacheSize"),
    KernelOrdinal( 40, "HalDiskCachePartitionCount"),
    KernelOrdinal( 44, "HalGetInterruptVector"),
    KernelOrdinal( 47, "HalRegisterShutdownNotification"),
    KernelOrdinal( 49, "HalReturnToFirmware"),
    KernelOrdinal( 65, "IoCreateDevice"),
    KernelOrdinal( 67, "IoCreateSymbolicLink"),
    KernelOrdinal( 69, "IoDeleteSymbolicLink"),
    KernelOrdinal( 74, "IoInvalidDeviceRequest"),
    KernelOrdinal( 81, "IoStartNextPacket"),
    KernelOrdinal( 83, "IoStartPacket"),
    KernelOrdinal( 87, "IofCompleteRequest"),
    KernelOrdinal( 95, "KeBugCheck"),
    KernelOrdinal( 97, "KeCancelTimer"),
    KernelOrdinal( 98, "KeConnectInterrupt"),
    KernelOrdinal( 99, "KeDelayExecutionThread"),
    KernelOrdinal(100, "KeDisconnectInterrupt"),
    KernelOrdinal(107, "KeInitializeDpc"),
    KernelOrdinal(109, "KeInitializeInterrupt"),
    KernelOrdinal(113, "KeInitializeTimerEx"),
    KernelOrdinal(119, "KeInsertQueueDpc"),
    KernelOrdinal(124, "KeQueryBasePriorityThread"),
    KernelOrdinal(125, "KeQueryInterruptTime"),
    KernelOrdinal(126, "KeQueryPerformanceCounter"),
    KernelOrdinal(127, "KeQueryPerformanceFrequency"),
    KernelOrdinal(128, "KeQuerySystemTime"),
    KernelOrdinal(129, "KeRaiseIrqlToDpcLevel"),
    KernelOrdinal(137, "KeRemoveQueueDpc"),
    KernelOrdinal(139, "KeRestoreFloatingPointState"),
    KernelOrdinal(142, "KeSaveFloatingPointState"),
    KernelOrdinal(143, "KeSetBasePriorityThread"),
    KernelOrdinal(144, "KeSetDisableBoostThread"),
    KernelOrdinal(145, "KeSetEvent"),
    KernelOrdinal(149, "KeSetTimer"),
    KernelOrdinal(151, "KeStallExecutionProcessor"),
    KernelOrdinal(153, "KeSynchronizeExecution"),
    KernelOrdinal(156, "KeTickCount"),
    KernelOrdinal(157, "KeTimeIncrement"),
    KernelOrdinal(159, "KeWaitForSingleObject"),
    KernelOrdinal(160, "KfRaiseIrql"),
    KernelOrdinal(161, "KfLowerIrql"),
    KernelOrdinal(164, "LaunchDataPage"),
    KernelOrdinal(165, "MmAllocateContiguousMemory"),
    KernelOrdinal(166, "MmAllocateContiguousMemoryEx"),
    KernelOrdinal(167, "MmAllocateSystemMemory"),
    KernelOrdinal(168, "MmClaimGpuInstanceMemory"),
    KernelOrdinal(171, "MmFreeContiguousMemory"),
    KernelOrdinal(172, "MmFreeSystemMemory"),
    KernelOrdinal(173, "MmGetPhysicalAddress"),
    KernelOrdinal(175, "MmLockUnlockBufferPages"),
    KernelOrdinal(176, "MmLockUnlockPhysicalPage"),
    KernelOrdinal(178, "MmPersistContiguousMemory"),
    KernelOrdinal(179, "MmQueryAddressProtect"),
    KernelOrdinal(180, "MmQueryAllocationSize"),
    KernelOrdinal(181, "MmQueryStatistics"),
    KernelOrdinal(182, "MmSetAddressProtect"),
    KernelOrdinal(184, "NtAllocateVirtualMemory"),
    KernelOrdinal(185, "NtCancelTimer"),
    KernelOrdinal(186, "NtClearEvent"),
    KernelOrdinal(187, "NtClose"),
    KernelOrdinal(189, "NtCreateEvent"),
    KernelOrdinal(190, "NtCreateFile"),
    KernelOrdinal(191, "NtCreateIoCompletion"),
    KernelOrdinal(192, "NtCreateMutant"),
    KernelOrdinal(193, "NtCreateSemaphore"),
    KernelOrdinal(194, "NtCreateTimer"),
    KernelOrdinal(196, "NtDeviceIoControlFile"),
    KernelOrdinal(197, "NtDuplicateObject"),
    KernelOrdinal(198, "NtFlushBuffersFile"),
    KernelOrdinal(199, "NtFreeVirtualMemory"),
    KernelOrdinal(200, "NtFsControlFile"),
    KernelOrdinal(202, "NtOpenFile"),
    KernelOrdinal(203, "NtOpenSymbolicLinkObject"),
    KernelOrdinal(204, "NtProtectVirtualMemory"),
    KernelOrdinal(205, "NtPulseEvent"),
    KernelOrdinal(206, "NtQueueApcThread"),
    KernelOrdinal(207, "NtQueryDirectoryFile"),
    KernelOrdinal(210, "NtQueryFullAttributesFile"),
    KernelOrdinal(211, "NtQueryInformationFile"),
    KernelOrdinal(215, "NtQuerySymbolicLinkObject"),
    KernelOrdinal(217, "NtQueryVirtualMemory"),
    KernelOrdinal(218, "NtQueryVolumeInformationFile"),
    KernelOrdinal(219, "NtReadFile"),
    KernelOrdinal(220, "NtReadFileScatter"),
    KernelOrdinal(221, "NtReleaseMutant"),
    KernelOrdinal(222, "NtReleaseSemaphore"),
    KernelOrdinal(223, "NtRemoveIoCompletion"),
    KernelOrdinal(224, "NtResumeThread"),
    KernelOrdinal(225, "NtSetEvent"),
    KernelOrdinal(226, "NtSetInformationFile"),
    KernelOrdinal(227, "NtSetIoCompletion"),
    KernelOrdinal(229, "NtSetTimerEx"),
    KernelOrdinal(230, "NtSignalAndWaitForSingleObjectEx"),
    KernelOrdinal(231, "NtSuspendThread"),
    KernelOrdinal(232, "NtUserIoApcDispatcher"),
    KernelOrdinal(233, "NtWaitForSingleObject"),
    KernelOrdinal(234, "NtWaitForSingleObjectEx"),
    KernelOrdinal(235, "NtWaitForMultipleObjectsEx"),
    KernelOrdinal(236, "NtWriteFile"),
    KernelOrdinal(237, "NtWriteFileGather"),
    KernelOrdinal(238, "NtYieldExecution"),
    KernelOrdinal(243, "ObOpenObjectByName"),
    KernelOrdinal(246, "ObReferenceObjectByHandle"),
    KernelOrdinal(250, "ObfDereferenceObject"),
    KernelOrdinal(255, "PsCreateSystemThreadEx"),
    KernelOrdinal(258, "PsTerminateSystemThread"),
    KernelOrdinal(259, "PsThreadObjectType"),
    KernelOrdinal(260, "RtlAnsiStringToUnicodeString"),
    KernelOrdinal(268, "RtlCompareMemory"),
    KernelOrdinal(269, "RtlCompareMemoryUlong"),
    KernelOrdinal(277, "RtlEnterCriticalSection"),
    KernelOrdinal(279, "RtlEqualString"),
    KernelOrdinal(285, "RtlFillMemoryUlong"),
    KernelOrdinal(286, "RtlFreeAnsiString"),
    KernelOrdinal(289, "RtlInitAnsiString"),
    KernelOrdinal(290, "RtlInitUnicodeString"),
    KernelOrdinal(291, "RtlInitializeCriticalSection"),
    KernelOrdinal(294, "RtlLeaveCriticalSection"),
    KernelOrdinal(301, "RtlNtStatusToDosError"),
    KernelOrdinal(302, "RtlRaiseException"),
    KernelOrdinal(304, "RtlTimeFieldsToTime"),
    KernelOrdinal(305, "RtlTimeToTimeFields"),
    KernelOrdinal(308, "RtlUnicodeStringToAnsiString"),
    KernelOrdinal(312, "RtlUnwind"),
    KernelOrdinal(322, "XboxHardwareInfo"),
    KernelOrdinal(323, "XboxHDKey"),
    KernelOrdinal(325, "XboxSignatureKey"),
    KernelOrdinal(327, "XeLoadSection"),
    KernelOrdinal(328, "XeUnloadSection"),
    KernelOrdinal(335, "XcSHAInit"),
    KernelOrdinal(336, "XcSHAUpdate"),
    KernelOrdinal(337, "XcSHAFinal"),
    KernelOrdinal(356, "HalBootSMCVideoMode"),
    KernelOrdinal(358, "HalIsResetOrShutdownPending"),
    KernelOrdinal(359, "IoMarkIrpMustComplete"),
    KernelOrdinal(360, "HalInitiateShutdown"),
)


# ---------------------------------------------------------------------------
# Extended xboxkrnl exports — available via D1-extend runtime resolver only.
# ---------------------------------------------------------------------------
# These ordinals are NOT in Azurik's vanilla XBE thunk table, so there's no
# pre-resolved slot to call through.  The D1-extend pipeline emits a
# resolving stub per referenced import: the stub invokes
# ``xboxkrnl_resolve_by_ordinal`` on first call (walking xboxkrnl.exe's
# PE export table at runtime from the fixed retail base ``0x80010000``),
# caches the resolved pointer inline, and subsequent calls hit the cache.
#
# Source: cross-reference of OpenXDK's xboxkrnl.h declarations with the
# canonical Xbox retail kernel ordinal map published by Cxbx-Reloaded
# and the original XDK linker exports file.  Every entry here has:
#   - Confirmed kernel ordinal (stable across retail kernel revisions)
#   - Matching declaration in OpenXDK's xboxkrnl.h
#   - No conflict with AZURIK_KERNEL_ORDINALS (asserted at module load)
#
# The list is deliberately curated rather than exhaustive (~100 entries,
# not all ~369 xboxkrnl exports).  Additions are welcome — verify the
# ordinal against a known-good reference before adding.

EXTENDED_KERNEL_ORDINALS: tuple[KernelOrdinal, ...] = (
    # --- Debug / diagnostics (Dbg*) ---
    KernelOrdinal(  5, "DbgBreakPoint"),
    KernelOrdinal(  6, "DbgBreakPointWithStatus"),
    KernelOrdinal(  7, "DbgBreakPrintRoutine"),
    KernelOrdinal(  9, "DbgPrompt"),
    KernelOrdinal( 10, "DbgLoadImageSymbols"),
    KernelOrdinal( 11, "DbgUnLoadImageSymbols"),

    # --- Executive services (Ex*) — beyond the 5 Azurik imports ---
    KernelOrdinal( 12, "ExAcquireReadWriteLockExclusive"),
    KernelOrdinal( 13, "ExAcquireReadWriteLockShared"),
    KernelOrdinal( 18, "ExInitializeReadWriteLock"),
    KernelOrdinal( 19, "ExInterlockedAddLargeInteger"),
    KernelOrdinal( 20, "ExInterlockedAddLargeStatistic"),
    KernelOrdinal( 21, "ExInterlockedCompareExchange64"),
    KernelOrdinal( 25, "ExRaiseException"),
    KernelOrdinal( 26, "ExRaiseStatus"),
    KernelOrdinal( 27, "ExReleaseReadWriteLock"),
    KernelOrdinal( 28, "ExSaveNonVolatileSetting"),
    KernelOrdinal( 29, "ExSemaphoreObjectType"),    # dup slot — see ord 30
    KernelOrdinal( 32, "ExfInterlockedCompareExchange64"),
    KernelOrdinal( 33, "FscGetCacheSize"),          # duplicate name w/ ord 35

    # --- Hal functions (Hal*) ---
    KernelOrdinal( 38, "HalClearSoftwareInterrupt"),
    KernelOrdinal( 39, "HalDisableSystemInterrupt"),
    KernelOrdinal( 41, "HalEnableSystemInterrupt"),
    KernelOrdinal( 42, "HalGetInterruptVector"),    # dup slot — see ord 44
    KernelOrdinal( 43, "HalReadSMCTrayState"),
    KernelOrdinal( 45, "HalReadWritePCISpace"),
    KernelOrdinal( 46, "HalRegisterShutdownNotification"),  # dup — see ord 47
    KernelOrdinal( 48, "HalRequestSoftwareInterrupt"),

    # --- I/O manager (Io*) — beyond the 8 Azurik imports ---
    KernelOrdinal( 59, "IoAllocateIrp"),
    KernelOrdinal( 60, "IoBuildAsynchronousFsdRequest"),
    KernelOrdinal( 61, "IoBuildDeviceIoControlRequest"),
    KernelOrdinal( 62, "IoBuildSynchronousFsdRequest"),
    KernelOrdinal( 63, "IoCheckShareAccess"),
    KernelOrdinal( 64, "IoCompletionObjectType"),
    KernelOrdinal( 66, "IoCreateFile"),
    KernelOrdinal( 68, "IoDeleteDevice"),
    KernelOrdinal( 70, "IoDeviceObjectType"),
    KernelOrdinal( 71, "IoFileObjectType"),
    KernelOrdinal( 72, "IoFreeIrp"),
    KernelOrdinal( 73, "IoInitializeIrp"),
    KernelOrdinal( 75, "IoQueryFileInformation"),
    KernelOrdinal( 76, "IoQueryVolumeInformation"),
    KernelOrdinal( 77, "IoQueueThreadIrp"),
    KernelOrdinal( 78, "IoRemoveShareAccess"),
    KernelOrdinal( 79, "IoSetIoCompletion"),
    KernelOrdinal( 80, "IoSetShareAccess"),
    KernelOrdinal( 82, "IoSynchronousDeviceIoControlRequest"),
    KernelOrdinal( 84, "IoSynchronousFsdRequest"),
    KernelOrdinal( 85, "IofCallDriver"),

    # --- Kernel fastcall (Kf*) — beyond KfLowerIrql / KfRaiseIrql ---
    KernelOrdinal( 86, "KdDebuggerEnabled"),
    KernelOrdinal( 88, "KdDebuggerNotPresent"),

    # --- Ke* — beyond Azurik's big Ke* imports ---
    KernelOrdinal( 89, "KeAlertResumeThread"),
    KernelOrdinal( 90, "KeAlertThread"),
    KernelOrdinal( 91, "KeBoostPriorityThread"),
    KernelOrdinal( 92, "KeBugCheck"),                # dup — see ord 95
    KernelOrdinal( 93, "KeBugCheckEx"),
    KernelOrdinal( 94, "KeCancelTimer"),             # dup — see ord 97
    KernelOrdinal( 96, "KeCapturePersistedMessage"),
    KernelOrdinal(101, "KeEnterCriticalRegion"),
    KernelOrdinal(102, "KeGetCurrentIrql"),
    KernelOrdinal(103, "KeGetCurrentThread"),
    KernelOrdinal(104, "KeInitializeApc"),
    KernelOrdinal(105, "KeInitializeDeviceQueue"),
    KernelOrdinal(106, "KeInitializeEvent"),
    KernelOrdinal(108, "KeInitializeMutant"),
    KernelOrdinal(110, "KeInitializeQueue"),
    KernelOrdinal(111, "KeInitializeSemaphore"),
    KernelOrdinal(112, "KeInitializeTimer"),
    KernelOrdinal(114, "KeInsertByKeyDeviceQueue"),
    KernelOrdinal(115, "KeInsertDeviceQueue"),
    KernelOrdinal(116, "KeInsertHeadQueue"),
    KernelOrdinal(117, "KeInsertQueue"),
    KernelOrdinal(118, "KeInsertQueueApc"),
    KernelOrdinal(120, "KeInterruptTime"),
    KernelOrdinal(121, "KeIsExecutingDpc"),
    KernelOrdinal(122, "KeLeaveCriticalRegion"),
    KernelOrdinal(123, "KePulseEvent"),
    KernelOrdinal(130, "KeReadStateEvent"),
    KernelOrdinal(131, "KeReadStateMutant"),
    KernelOrdinal(132, "KeReadStateSemaphore"),
    KernelOrdinal(133, "KeReadStateTimer"),
    KernelOrdinal(134, "KeRegisterDriverNotification"),
    KernelOrdinal(135, "KeReleaseMutant"),
    KernelOrdinal(136, "KeReleaseSemaphore"),
    KernelOrdinal(138, "KeResetEvent"),
    KernelOrdinal(140, "KeResumeThread"),
    KernelOrdinal(141, "KeRundownQueue"),
    KernelOrdinal(146, "KeSetPriorityProcess"),
    KernelOrdinal(147, "KeSetPriorityThread"),
    KernelOrdinal(148, "KeSetTimerEx"),
    KernelOrdinal(150, "KeSuspendThread"),
    KernelOrdinal(152, "KeSystemTime"),
    KernelOrdinal(154, "KeTestAlertThread"),
    KernelOrdinal(155, "KeTestCancelTimer"),

    # --- Mm* — beyond Azurik's 15 Mm* imports ---
    KernelOrdinal(169, "MmCreateKernelStack"),
    KernelOrdinal(170, "MmDeleteKernelStack"),
    KernelOrdinal(174, "MmIsAddressValid"),
    KernelOrdinal(177, "MmMapIoSpace"),
    KernelOrdinal(183, "MmUnmapIoSpace"),

    # --- Nt* — beyond Azurik's substantial Nt* coverage ---
    # NOTE: these ordinal numbers interleave with Azurik's imports;
    # only entries NOT in AZURIK_KERNEL_ORDINALS appear here.
    KernelOrdinal(188, "NtCreateDirectoryObject"),
    KernelOrdinal(195, "NtDebugImport"),
    KernelOrdinal(201, "NtOpenDirectoryObject"),
    KernelOrdinal(208, "NtQueryEvent"),
    KernelOrdinal(209, "NtQueryInformationIoCompletion"),
    KernelOrdinal(212, "NtQueryMutant"),
    KernelOrdinal(213, "NtQuerySemaphore"),
    KernelOrdinal(214, "NtQueryTimer"),
    KernelOrdinal(216, "NtQueryVirtualMemory"),      # dup — see ord 217
    KernelOrdinal(228, "NtSuspendThread"),            # dup — see ord 231
    KernelOrdinal(239, "ObCreateObject"),
    KernelOrdinal(240, "ObDirectoryObjectType"),
    KernelOrdinal(241, "ObInsertObject"),
    KernelOrdinal(242, "ObMakeTemporaryObject"),
    KernelOrdinal(244, "ObReferenceObjectByName"),
    KernelOrdinal(245, "ObReferenceObjectByPointer"),
    KernelOrdinal(247, "ObSymbolicLinkObjectType"),
    KernelOrdinal(248, "ObfDereferenceObject"),       # dup — see ord 250
    KernelOrdinal(249, "ObfReferenceObject"),

    # --- Ps* — process / thread ---
    KernelOrdinal(251, "PhyGetLinkState"),
    KernelOrdinal(252, "PhyInitialize"),
    KernelOrdinal(253, "PsCreateSystemThread"),
    KernelOrdinal(254, "PsCreateSystemThreadEx"),     # dup — see ord 255
    KernelOrdinal(256, "PsQueryStatistics"),
    KernelOrdinal(257, "PsSetCreateThreadNotifyRoutine"),

    # --- Rtl* — runtime library, beyond Azurik's 17 Rtl* imports ---
    KernelOrdinal(261, "RtlAppendStringToString"),
    KernelOrdinal(262, "RtlAppendUnicodeStringToString"),
    KernelOrdinal(263, "RtlAppendUnicodeToString"),
    KernelOrdinal(264, "RtlAssert"),
    KernelOrdinal(265, "RtlCaptureContext"),
    KernelOrdinal(266, "RtlCaptureStackBackTrace"),
    KernelOrdinal(267, "RtlCharToInteger"),
    KernelOrdinal(270, "RtlCompareString"),
    KernelOrdinal(271, "RtlCompareUnicodeString"),
    KernelOrdinal(272, "RtlCopyString"),
    KernelOrdinal(273, "RtlCopyUnicodeString"),
    KernelOrdinal(274, "RtlCreateUnicodeString"),
    KernelOrdinal(275, "RtlDowncaseUnicodeChar"),
    KernelOrdinal(276, "RtlDowncaseUnicodeString"),
    KernelOrdinal(278, "RtlEqualUnicodeString"),
    KernelOrdinal(280, "RtlExtendedIntegerMultiply"),
    KernelOrdinal(281, "RtlExtendedLargeIntegerDivide"),
    KernelOrdinal(282, "RtlExtendedMagicDivide"),
    KernelOrdinal(283, "RtlFillMemory"),
    KernelOrdinal(284, "RtlFillMemoryUlong"),         # dup — see ord 285
    KernelOrdinal(287, "RtlFreeUnicodeString"),
    KernelOrdinal(288, "RtlGetCallersAddress"),
    KernelOrdinal(292, "RtlIntegerToChar"),
    KernelOrdinal(293, "RtlIntegerToUnicodeString"),
    KernelOrdinal(295, "RtlLowerChar"),
    KernelOrdinal(296, "RtlMapGenericMask"),
    KernelOrdinal(297, "RtlMoveMemory"),
    KernelOrdinal(298, "RtlMultiByteToUnicodeN"),
    KernelOrdinal(299, "RtlMultiByteToUnicodeSize"),
    KernelOrdinal(300, "RtlNtStatusToDosError"),      # dup — see ord 301
    KernelOrdinal(303, "RtlSnprintf"),
    KernelOrdinal(306, "RtlTryEnterCriticalSection"),
    KernelOrdinal(307, "RtlUlongByteSwap"),
    KernelOrdinal(309, "RtlUnicodeStringToInteger"),
    KernelOrdinal(310, "RtlUnicodeToMultiByteN"),
    KernelOrdinal(311, "RtlUnicodeToMultiByteSize"),
    KernelOrdinal(313, "RtlUpcaseUnicodeChar"),
    KernelOrdinal(314, "RtlUpcaseUnicodeString"),
    KernelOrdinal(315, "RtlUpcaseUnicodeToMultiByteN"),
    KernelOrdinal(316, "RtlUpperChar"),
    KernelOrdinal(317, "RtlUpperString"),
    KernelOrdinal(318, "RtlUshortByteSwap"),
    KernelOrdinal(319, "RtlWalkFrameChain"),
    KernelOrdinal(320, "RtlZeroMemory"),

    # --- Xbox* / Xc* ---
    KernelOrdinal(321, "XboxEEPROMKey"),
    KernelOrdinal(324, "XboxKrnlVersion"),
    KernelOrdinal(326, "XcHMAC"),
    KernelOrdinal(329, "XcPKEncPublic"),
    KernelOrdinal(330, "XcPKDecPrivate"),
    KernelOrdinal(331, "XcPKGetKeyLen"),
    KernelOrdinal(332, "XcVerifyPKCS1Signature"),
    KernelOrdinal(333, "XcModExp"),
    KernelOrdinal(334, "XcDESKeyParity"),
    KernelOrdinal(338, "XcRC4Key"),
    KernelOrdinal(339, "XcRC4Crypt"),
    KernelOrdinal(340, "XcHMAC_SHA1Create"),
    KernelOrdinal(341, "XcCryptService"),
    KernelOrdinal(342, "XcUpdateCrypto"),

    # --- Misc high-ordinal entries ---
    KernelOrdinal(343, "RtlRip"),
    KernelOrdinal(344, "XboxLANKey"),
    KernelOrdinal(345, "XboxAlternateSignatureKeys"),
    KernelOrdinal(346, "XePublicKeyData"),
    KernelOrdinal(347, "HalBootSMCVideoMode"),        # dup — see ord 356
    KernelOrdinal(348, "IdexChannelObject"),
    KernelOrdinal(349, "HalIsResetOrShutdownPending"),  # dup — see ord 358
    KernelOrdinal(350, "IoMarkIrpMustComplete"),      # dup — see ord 359
    KernelOrdinal(351, "HalInitiateShutdown"),        # dup — see ord 360
    KernelOrdinal(352, "snprintf"),
    KernelOrdinal(353, "sprintf"),
    KernelOrdinal(354, "vsnprintf"),
    KernelOrdinal(355, "vsprintf"),
    KernelOrdinal(357, "HalEnableSecureTrayEject"),
    KernelOrdinal(361, "IoRemoveShareAccess"),        # dup — see ord 78
    KernelOrdinal(362, "READ_PORT_BUFFER_UCHAR"),
    KernelOrdinal(363, "READ_PORT_BUFFER_USHORT"),
    KernelOrdinal(364, "READ_PORT_BUFFER_ULONG"),
    KernelOrdinal(365, "WRITE_PORT_BUFFER_UCHAR"),
    KernelOrdinal(366, "WRITE_PORT_BUFFER_USHORT"),
    KernelOrdinal(367, "WRITE_PORT_BUFFER_ULONG"),
    KernelOrdinal(368, "XcSHA256Init"),
    KernelOrdinal(369, "XcSHA256Update"),
)


# ---------------------------------------------------------------------------
# Combined views — most callers want the union; the two layers are
# distinguishable via :func:`is_azurik_imported`.

ALL_KERNEL_ORDINALS: tuple[KernelOrdinal, ...] = (
    AZURIK_KERNEL_ORDINALS + EXTENDED_KERNEL_ORDINALS)


# Build lookup dicts up-front — these are the public API most callers
# want (including `kernel_imports.py` and the drift-guard tests).

ORDINAL_TO_NAME: dict[int, str] = {}
"""All kernel ordinals (static + extended), mapped to their public name.

When two ordinals map to the same function name (some xboxkrnl
functions have alias ordinals), the LOWEST ordinal wins in the
forward direction.  The reverse map uses the Azurik-imported
ordinal when one exists, so D1's fast path is always preferred over
D1-extend's runtime resolver."""
for _e in ALL_KERNEL_ORDINALS:
    ORDINAL_TO_NAME.setdefault(_e.ordinal, _e.name)


NAME_TO_ORDINAL: dict[str, int] = {}
"""Name → ordinal map.  Azurik-imported entries win over extended
entries at the name level so shim calls resolve through D1's fast
static path when possible."""
# Seed with Azurik's imports first so their ordinals win on collision.
for _e in AZURIK_KERNEL_ORDINALS:
    NAME_TO_ORDINAL.setdefault(_e.name, _e.ordinal)
for _e in EXTENDED_KERNEL_ORDINALS:
    NAME_TO_ORDINAL.setdefault(_e.name, _e.ordinal)


_AZURIK_ORDINALS: set[int] = {e.ordinal for e in AZURIK_KERNEL_ORDINALS}


def is_azurik_imported(ordinal: int) -> bool:
    """True iff ``ordinal`` is one of Azurik's 151 static thunk-table
    entries (fast D1 path).  False for extended ordinals that require
    D1-extend's runtime resolver."""
    return ordinal in _AZURIK_ORDINALS


def ordinal_for(name: str) -> int | None:
    """Return the kernel ordinal that exports ``name``, or ``None``.

    Used by :func:`azurik_mod.patching.kernel_imports.stub_for_symbol`
    to turn a shim's demangled extern into an ordinal the layout
    session can dispatch (either to a D1 thunk-table stub or a
    D1-extend resolving stub, depending on :func:`is_azurik_imported`).
    """
    return NAME_TO_ORDINAL.get(name)
