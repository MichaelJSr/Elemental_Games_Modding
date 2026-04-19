/* Azurik shim-authoring header.
 *
 * Freestanding declarations for the Azurik game engine's in-memory
 * data structures and known VA anchors, reverse-engineered from
 * Ghidra.  Shim authors should prefer these named fields over
 * hand-counted ``[reg + 0xNN]`` offsets — they produce identical
 * machine code but keep the source readable and catch struct drift
 * at compile time instead of runtime.
 *
 * Companion headers a shim typically also includes:
 *
 *   - ``azurik_vanilla.h`` — extern declarations for vanilla Azurik
 *     functions (play_movie_fn, poll_movie, ...).  Picked up by the
 *     layout pipeline's A3 vanilla-symbol registry.
 *   - ``azurik_kernel.h`` — extern declarations for xboxkrnl imports
 *     the game already references (DbgPrint, KeQueryPerformance-
 *     Counter, ...).  Shimmed via D1's thunk-table stubs; you do
 *     NOT modify the XBE import table yourself.
 *
 * Documentation conventions:
 *
 * - Every named field carries its byte offset and the Ghidra
 *   decomp the name came from (most commonly ``FUN_00049480`` for
 *   ``CritterData`` and ``FUN_00084f90`` / ``FUN_00084940`` /
 *   ``FUN_00085f50`` for ``PlayerInputState``).
 *
 * - Fields marked ``(speculative)`` have names that fit the observed
 *   access pattern but aren't fully pinned.  Use them at your own
 *   risk; rename them when you find out what they really are.
 *
 * - ``_reservedNN`` slots are genuinely unknown — the offset is real
 *   (runtime code touches it) but its semantics aren't nailed down.
 *
 * ABI constraints:
 *
 * - i386 little-endian, 4-byte struct alignment (``compile.sh`` uses
 *   clang ``-target i386-pc-win32 -ffreestanding -nostdlib``).
 * - ``float`` is 32-bit IEEE 754, ``double`` is 64-bit.
 * - No padding is inserted beyond what's explicitly written below —
 *   every ``_Static_assert`` at the bottom of the file pins the
 *   position of at least one late field so silent drift breaks the
 *   build, not the runtime.
 */
#ifndef AZURIK_SHIM_H
#define AZURIK_SHIM_H

#ifdef __cplusplus
extern "C" {
#endif


/* ==========================================================================
 * Fixed-width integer aliases (no stdint.h — we're freestanding)
 * ======================================================================== */
typedef unsigned char  u8;
typedef unsigned short u16;
typedef unsigned int   u32;
typedef signed char    i8;
typedef signed short   i16;
typedef signed int     i32;
typedef float          f32;
typedef double         f64;


/* ==========================================================================
 * Opaque handle types
 * ==========================================================================
 * Use these when a shim only needs to pass a pointer through to a
 * vanilla function without accessing any fields.  Prefer the named
 * structs below whenever you DO want to read / write specific fields. */
typedef void *EntityHandle;         /* Anything the game models as an entity  */
typedef void *ConfigTableHandle;    /* A loaded `tabl` chunk from config.xbr  */
typedef void *ScenePtr;             /* Opaque scene / world graph handle      */
typedef void *ControllerStatePtr;   /* Per-player input block (4 players)     */


/* ==========================================================================
 * CritterData
 * ==========================================================================
 * The engine's in-memory descriptor for every critter — player
 * included (garret4 is a critter under the hood).  Populated at boot
 * by ``FUN_00049480`` across three config.xbr tables:
 *
 *   1. ``critters_engine``       collision + rendering fields
 *   2. ``critters_critter_data``  gameplay fields
 *   3. ``critters_sounds`` etc.   audio / drop tables (indices 0x17+)
 *
 * Note: ``walk_speed`` / ``run_speed`` come out as the default ``1.0``
 * because ``critters_critter_data`` doesn't actually carry those
 * rows — see azurik_mod/patches/player_physics.py for the full
 * dead-data story and the C1 fix that bypasses the slot.
 *
 * Offset column (hex) is BYTE offset from the struct base.  The
 * ``piVar9[N]`` column is the Ghidra decomp index — ``piVar9`` is
 * the entity struct pointer in ``FUN_00049480``.  Properties
 * populated from ``FUN_000d1420("name")`` carry their config-key
 * name in the comment.
 */
typedef struct CritterData {
    /* --- critters_engine identifying fields --- */
    u32 _reserved_00;                 /* +0x00 piVar9[0] — parent / vtable ptr         */
    u32 _reserved_04;                 /* +0x04 piVar9[1] — identity token              */
    u32 _reserved_08;                 /* +0x08 piVar9[2] — "sound dir" token           */
    u32 feature_class_id;             /* +0x0C piVar9[3] — from FUN_000493d0           */
    u32 use_skeleton_collision_word;  /* +0x10 piVar9[4] — contains bool at +0x13      */
    u32 _reserved_14;                 /* +0x14 piVar9[5] — set from a later file pass  */
    f32 collision_radius;             /* +0x18 piVar9[6] — "collisionRadius"           */
    f32 collision_aspect_ratio;       /* +0x1C piVar9[7] — "collisionAspectRatio"      */
    f32 player_collision_radius;      /* +0x20 piVar9[8] — "playerCollisionRadius"     */
    f32 scale;                        /* +0x24 piVar9[9] — "scale"                     */
    u32 skin_index;                   /* +0x28 piVar9[0xA] — "skinIndex"               */
    f32 bound_radius;                 /* +0x2C piVar9[0xB] — "boundRadius"             */
    f32 far_clip;                     /* +0x30 piVar9[0xC] — "farClip"                 */
    f32 awake_distance;               /* +0x34 piVar9[0xD] — "awakeDistance"           */

    /* --- critters_critter_data: movement ---
     * The config values for walk_speed / run_speed are dead data (no
     * matching rows in critters_critter_data); the engine falls back
     * to the default 1.0 in both slots.  The player_physics C1 patch
     * rewrites the FLD at VA 0x85F65 to reference a per-game float
     * rather than reading this slot, but shims that DO NOT go through
     * the C1 path and want to change the player's in-memory
     * base-speed value can write here directly at game startup. */
    f32 walk_speed;                   /* +0x38 piVar9[0xE] — "walkSpeed" (= 1.0)       */
    f32 walk_anim_speed;              /* +0x3C piVar9[0xF] — "walkAnimSpeed"           */
    f32 run_speed;                    /* +0x40 piVar9[0x10] — "runSpeed" (= 1.0)       */
    f32 run_anim_speed;               /* +0x44 piVar9[0x11] — "runAnimSpeed"           */

    /* --- critters_critter_data: damage thresholds / knockback ---
     * ouch1 is the low-damage threshold; the engine uses ouch2 and
     * ouch3 to select heavier-hit reactions.  All three threshold
     * fields are stored via the int-token wrapper FUN_000f5a40 and
     * so appear as u32 here, but they carry the float value. */
    f32 ouch2_threshold;              /* +0x48 piVar9[0x12] — "ouch2Threshold"         */
    f32 ouch3_threshold;              /* +0x4C piVar9[0x13] — "ouch3Threshold"         */
    f32 ouch1_knockback;              /* +0x50 piVar9[0x14] — "ouch1Knockback"         */
    f32 ouch2_knockback;              /* +0x54 piVar9[0x15] — "ouch2Knockback"         */
    f32 ouch3_knockback;              /* +0x58 piVar9[0x16] — "ouch3Knockback"         */

    /* --- critters_sounds / critters_mutate: string refs --- */
    u32 victory_anim_token;           /* +0x5C piVar9[0x17] — "victory anim" ref       */
    u32 shadow_texture_ref;           /* +0x60 piVar9[0x18] — transformed string       */
    u32 realm_feature_flags;          /* +0x64 piVar9[0x19] — realm | other feat | ... */

    /* --- critters_critter_data: flocking behaviour --- */
    u32 flocking_fear;                /* +0x68 piVar9[0x1A] — "f.fear"                 */
    u32 flocking_follow;              /* +0x6C piVar9[0x1B] — "f.follow"               */
    u32 flocking_attack;              /* +0x70 piVar9[0x1C] — "f.attack"               */
    u32 flocking_food;                /* +0x74 piVar9[0x1D] — "f.food"                 */

    /* --- critters_critter_data: bool flags (byte-typed) ---
     * These four bools are all written via `*(bool *)(base + N) =
     * value != 0.0` in FUN_00049480.  The surrounding bytes are
     * nominally 32-bit words but the game only reads the low byte. */
    u8  _reserved_78;                 /* +0x78 — low byte of piVar9[0x1E]              */
    u8  use_center_basis;             /* +0x79 — "useCenterBasis"                      */
    u8  always_glued;                 /* +0x7A — "alwaysGlued"                         */
    u8  no_freeze;                    /* +0x7B — "noFreeze"                            */
    u8  hits_through_walls;           /* +0x7C piVar9[0x1F] (byte) — "hitsThroughWalls" */
    u8  _reserved_7D[3];              /* +0x7D..+0x7F — tail of piVar9[0x1F]           */

    /* --- critters_critter_data: timers --- */
    f32 drown_time;                   /* +0x80 piVar9[0x20] — "drownTime"              */
    f32 corpse_wait_time;             /* +0x84 piVar9[0x21] — "corpseWaitTime"         */
    f32 corpse_fade_time;             /* +0x88 piVar9[0x22] — "corpseFadeTime"         */

    /* --- misc scratch / links --- */
    u32 per_type_data_ptr;            /* +0x8C piVar9[0x23] — per-damage-type array     */
    u32 _reserved_90;                 /* +0x90 piVar9[0x24]                             */
    f32 shadow_size;                  /* +0x94 piVar9[0x25] — "shadowSize"             */
    f32 clip_plane_offset;            /* +0x98 piVar9[0x26] — "clipPlaneOffset"        */
    i32 shadow_texture_res;           /* +0x9C piVar9[0x27] — -1 = no shadow           */

    /* --- Anything past here is not yet mapped.  Full struct is
     * several hundred bytes; walk range / drop tables / attack
     * triggers live in the 0xB8..0x120 range (range, range up, range
     * down, attackRange, drop1..drop5, dropChance1..5) but we don't
     * surface them until a shim actually needs them. */
} CritterData;


/* ==========================================================================
 * PlayerInputState
 * ==========================================================================
 * Per-frame player-movement state the engine fills from stick input
 * and the current critter's ``CritterData`` fields.  Populated by
 * ``FUN_00084f90``; the magnitude + direction outputs at the end
 * are written by ``FUN_00084940`` and consumed by ``FUN_00085f50``
 * which computes
 *
 *     velocity = critter->run_speed * magnitude * unit_direction
 *
 * Observed writes reach at least +0x17C (animation state).  Offsets
 * we understand are named; gaps are ``_reservedNN``.  Shim authors
 * who only need the output fields (magnitude + direction_*) can
 * safely ignore everything else.
 */

/* Flag bits at offset 0x20 of PlayerInputState.flags (u8).  Both
 * bits are tested by ``FUN_00084940`` — FALLING selects the
 * physics-only branch (stick ignored, magnitude from +0x0C);
 * RUNNING multiplies the final magnitude by 3.0 (at the shared
 * constant site in vanilla; player_physics C1 redirects that to a
 * per-game constant). */
#define PLAYER_FLAG_FALLING  0x01u
#define PLAYER_FLAG_RUNNING  0x40u
/* Other bits (0x02 / 0x04 / 0x08 / 0x10 / 0x20 / 0x80) exist in the
 * engine but aren't fully classified.  Shims should treat them as
 * read-only and not clobber them on write. */

typedef struct PlayerInputState {
    u32 entity_class_ptr;             /* +0x00 piVar1 — class vtable ptr             */
    u32 frame_dt_fixed;               /* +0x04 — constant 0x3D088889 (float 1/30 s)  */
    f32 stick_x;                      /* +0x08 — raw stick X component               */
    f32 stick_y;                      /* +0x0C — raw stick Y component (also used
                                       *          as the falling-state "delta")      */
    f32 fall_angle;                   /* +0x10 — angle used in falling branch        */
    f32 idle_angle;                   /* +0x14 — angle used when stick is neutral    */
    f32 walk_angle;                   /* +0x18 — angle used when stick is pushed     */
    f32 stick_magnitude;              /* +0x1C — sqrt(x²+y²), in [0, 1]              */

    u8  flags;                        /* +0x20 — PLAYER_FLAG_*                       */
    u8  dead;                         /* +0x21 — nonzero after death                 */
    u8  _reserved_22;                 /* +0x22                                       */
    u8  _reserved_23;                 /* +0x23                                       */

    /* +0x24..+0x2C — 3D world-space reference point, used as the
     * "self" anchor in the dead-state fpatan that faces the player
     * toward +0x3C..+0x44 when dead.  Speculative but plausibly the
     * live position; shims that need real position should prefer the
     * entity struct's +0x24..+0x2C fields. */
    f32 ref_x;                        /* +0x24 (speculative)                         */
    f32 ref_y;                        /* +0x28 (speculative)                         */
    f32 ref_z;                        /* +0x2C (speculative)                         */

    u8  _reserved_30[0x04];           /* +0x30 — orientation / rotation scratch      */
    CritterData *critter_data;        /* +0x34 — ptr to the player's CritterData     */
    u8  _reserved_38[0x04];           /* +0x38                                       */

    /* +0x3C..+0x44 — reference point used as the target-of-facing in
     * the dead-state fpatan.  Mirrors +0x24..+0x2C. */
    f32 target_x;                     /* +0x3C (speculative)                         */
    f32 target_y;                     /* +0x40 (speculative)                         */
    f32 target_z;                     /* +0x44 (speculative)                         */

    /* +0x48..+0x120 — interior of the struct we haven't reverse-
     * engineered in detail.  Animation / IK / step-detection scratch
     * lives here; FUN_00084f90 copies the entity's position into
     * +0x48..+0x50.  Leave untouched unless your shim has a reason
     * to poke a specific offset — if so, document it and move it
     * out of this gap into a named field. */
    u8  _reserved_48[0x120 - 0x48];

    /* --- magnitude + direction outputs of FUN_00084940 ---
     * The only fields player_physics C1 touches.  magnitude is the
     * final scalar (stick magnitude * 3.0 when PLAYER_FLAG_RUNNING
     * is set, otherwise just stick magnitude).  direction_xyz is a
     * unit vector derived from direction_angle, with direction_z
     * always 0 because Azurik's player movement is 2D horizontal. */
    f32 direction_angle;              /* +0x120 — output angle (radians)             */
    f32 magnitude;                    /* +0x124 — walking = stick mag; running = ×3  */
    f32 direction_x;                  /* +0x128 — -sin(direction_angle)              */
    f32 direction_y;                  /* +0x12C —  cos(direction_angle)              */
    f32 direction_z;                  /* +0x130 — always 0 (Azurik is 2D horizontal) */
} PlayerInputState;


/* ==========================================================================
 * BootState
 * ==========================================================================
 * The global boot state machine at ``DAT_001bf61c``, stepped by
 * ``FUN_0005f620`` (the function that plays the boot movies and
 * transitions to the main menu / in-game state).  Enum values are
 * the ``case N`` labels in the switch at 0x5F635.  Useful if a shim
 * wants to change boot flow (e.g. skip all movies, land directly in
 * the menu).
 */
typedef enum BootState {
    BOOT_STATE_INIT          = 0,     /* initial dispatch / resource loading        */
    BOOT_STATE_PLAY_LOGO     = 1,     /* play AdreniumLogo.bik (skip_logo sits here) */
    BOOT_STATE_POLL_LOGO     = 2,     /* polling the logo movie                     */
    BOOT_STATE_PLAY_PROPHECY = 3,     /* play prophecy.bik                          */
    BOOT_STATE_POLL_PROPHECY = 4,     /* polling prophecy                           */
    BOOT_STATE_FADE_IN       = 5,     /* post-movie transition                      */
    BOOT_STATE_MENU_ENTER    = 6,     /* enter the main menu                        */
    BOOT_STATE_MENU          = 7,     /* main menu active                           */
    BOOT_STATE_LOAD_SAVE     = 8,     /* save-selection / load flow                 */
    BOOT_STATE_INGAME        = 9,     /* in-game: engine update loop runs here      */
} BootState;

/* Global state VA.  Read / write via
 *   *(BootState *)AZURIK_BOOT_STATE_VA
 * from within a shim.  Writing advances the state machine; the game
 * tolerates arbitrary forward jumps but unpredictable backward ones
 * will produce visible glitches (e.g. jumping from INGAME back to
 * PLAY_LOGO mid-game). */
#define AZURIK_BOOT_STATE_VA         0x001BF61Cu


/* ==========================================================================
 * Known VA anchors
 * ==========================================================================
 * Fixed addresses a shim may reference when patching or calling into
 * the vanilla game.  Pair these with DIR32 relocations (write into a
 * pointer variable) or the vanilla-function registry for calls.
 */

/* .rdata float, baseline 9.8 m/s² — the single source of gravity for
 * every falling entity in the world.  The player_physics gravity
 * slider rewrites the bytes here directly. */
#define AZURIK_GRAVITY_VA            0x001980A8u

/* .rdata float, baseline 3.0 — the "run speed multiplier" the engine
 * reads at 45 different call sites across collision, AI, audio, etc.
 * The player-movement FMUL that USED to reference it is redirected
 * by player_physics C1 to a per-game constant; touching the shared
 * constant below would affect every other reader. */
#define AZURIK_SHARED_RUN_MULT_VA    0x001A25BCu

/* Player character name — 12-byte ASCII buffer in .rdata at VA
 * 0x0019EA68 (vanilla bytes: ``"garret4\0d:\\\0"``).  The
 * player_character patch overwrites this to swap models (experimental,
 * animations may not match the target skeleton).
 *
 * NB: the pre-reorganisation code used the FILE OFFSET (0x001976C8)
 * as if it were a VA.  It happened to work only because the runtime
 * Python code indexes ``xbe_data[offset:]`` directly, not via
 * ``va_to_file``.  The name below is the real VA — shims that
 * access the string via ``DIR32`` relocation must use THIS value.
 * The file offset lives in ``_player_character.py`` as
 * ``PLAYER_CHAR_OFFSET = 0x001976C8``.
 */
#define AZURIK_PLAYER_CHAR_NAME_VA         0x0019EA68u
#define AZURIK_PLAYER_CHAR_NAME_FILE_OFF   0x001976C8u  /* for Python/byte-indexed callers */


/* ==========================================================================
 * Time / frame pacing
 * ========================================================================== */

/* Nominal simulation step in seconds — 1/30 = 0.03333... as a 32-bit
 * float, stored as the IEEE 754 bit pattern 0x3D088889.  The engine
 * runs its tick function with this delta regardless of render FPS;
 * the FPS-unlock patch changes the CAP on consecutive steps per
 * frame (at VA 0x059AFD / 0x059B37), not this constant.  Shims that
 * want to scale per-frame effects — velocity integrators, timers —
 * should multiply by this, never hardcode 1/30. */
#define AZURIK_SIM_DT_SECONDS_BITS   0x3D088889u
#define AZURIK_SIM_DT_SECONDS_F32    (1.0f / 30.0f)

/* Kernel tick counter — xboxkrnl exports ``KeTickCount`` via the
 * thunk table (ordinal 156).  Shim code that wants a monotonic
 * "ticks since boot" value should ``#include "azurik_kernel.h"``
 * and call ``KeTickCount()``; the D1 layout pass inserts the
 * ``JMP [thunk_va]`` stub automatically, so hardcoding the thunk
 * VA here would be brittle (it moves between builds). */


/* ==========================================================================
 * Conveniences
 * ========================================================================== */

/* ``CONTAINER_OF(ptr, type, member)`` — get a pointer to the enclosing
 * struct from a pointer to one of its members.  Lets a shim that
 * hooks on (say) a velocity field reach the rest of the
 * PlayerInputState. */
#define CONTAINER_OF(ptr, type, member) \
    ((type *)((u8 *)(ptr) - (u32)&((type *)0)->member))


/* ==========================================================================
 * Drift-guard static asserts
 * ==========================================================================
 * Every late named field is pinned with `offsetof` so re-ordering
 * anything above it breaks the build instead of silently producing
 * wrong machine code.  If you add a new named field to a struct,
 * ADD A MATCHING ASSERT HERE. */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(u32) == 4,  "u32 must be 4 bytes");
_Static_assert(sizeof(u8)  == 1,  "u8 must be 1 byte");
_Static_assert(sizeof(f32) == 4,  "f32 must be 4 bytes");

_Static_assert(__builtin_offsetof(CritterData, collision_radius) == 0x18,
               "CritterData.collision_radius drifted");
_Static_assert(__builtin_offsetof(CritterData, walk_speed) == 0x38,
               "CritterData.walk_speed drifted");
_Static_assert(__builtin_offsetof(CritterData, run_speed) == 0x40,
               "CritterData.run_speed drifted");
_Static_assert(__builtin_offsetof(CritterData, ouch1_knockback) == 0x50,
               "CritterData.ouch1_knockback drifted — is ouch2_threshold "
               "still at +0x48?");
_Static_assert(__builtin_offsetof(CritterData, hits_through_walls) == 0x7C,
               "CritterData.hits_through_walls drifted (byte layout around "
               "useCenterBasis / alwaysGlued / noFreeze broke)");
_Static_assert(__builtin_offsetof(CritterData, drown_time) == 0x80,
               "CritterData.drown_time drifted");
_Static_assert(__builtin_offsetof(CritterData, shadow_size) == 0x94,
               "CritterData.shadow_size drifted");
_Static_assert(__builtin_offsetof(CritterData, shadow_texture_res) == 0x9C,
               "CritterData tail drifted past shadow_texture_res");

_Static_assert(__builtin_offsetof(PlayerInputState, stick_magnitude) == 0x1C,
               "PlayerInputState.stick_magnitude drifted");
_Static_assert(__builtin_offsetof(PlayerInputState, flags) == 0x20,
               "PlayerInputState.flags drifted");
_Static_assert(__builtin_offsetof(PlayerInputState, critter_data) == 0x34,
               "PlayerInputState.critter_data drifted");
_Static_assert(__builtin_offsetof(PlayerInputState, direction_angle) == 0x120,
               "PlayerInputState.direction_angle drifted");
_Static_assert(__builtin_offsetof(PlayerInputState, magnitude) == 0x124,
               "PlayerInputState.magnitude drifted — C1 patch would "
               "silently clobber the wrong field");
_Static_assert(__builtin_offsetof(PlayerInputState, direction_x) == 0x128,
               "PlayerInputState.direction_x drifted");
_Static_assert(__builtin_offsetof(PlayerInputState, direction_z) == 0x130,
               "PlayerInputState.direction_z drifted");
#endif


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_SHIM_H */
