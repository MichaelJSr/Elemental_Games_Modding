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
/* ControllerStatePtr below is now a real struct pointer — see
 * ``ControllerState`` + ``AZURIK_CONTROLLER_STATE_VA`` below. */
typedef struct ControllerState *ControllerStatePtr;


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
 * Note: ``walk_speed`` / ``run_speed`` are populated by the
 * CritterData struct default initialiser, NOT from
 * ``critters_critter_data`` (no such config rows for the player).
 * Runtime vanilla value for the player entity is ``run_speed = 7.0``
 * (confirmed via lldb at VA 0x00085F65).  See
 * azurik_mod/patches/player_physics/__init__.py for the independence
 * math that layers walk / run multipliers on top of this baseline.
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
     * matching rows in critters_critter_data); the engine leaves
     * these slots at whatever the struct default initialiser wrote
     * (vanilla player: run_speed = 7.0, confirmed via lldb at VA
     * 0x00085F65).  The player_physics C1 patch rewrites the FLD at
     * VA 0x85F65 to reference a per-game float rather than reading
     * this slot, but shims that DO NOT go through the C1 path and
     * want to change the player's in-memory base-speed value can
     * write here directly at game startup. */
    f32 walk_speed;                   /* +0x38 piVar9[0xE]  — "walkSpeed" (runtime default; not from config) */
    f32 walk_anim_speed;              /* +0x3C piVar9[0xF]  — "walkAnimSpeed"                                 */
    f32 run_speed;                    /* +0x40 piVar9[0x10] — "runSpeed" (vanilla=7.0 for player; see above)  */
    f32 run_anim_speed;               /* +0x44 piVar9[0x11] — "runAnimSpeed"                                  */

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

    /* --- unmapped gap between shadow_texture_res and range[] --- */
    u8  _reserved_A0[0xB8 - 0xA0];

    /* --- critters_critter_data: awareness / attack ranges ---
     * Units are world-space (identical scale to collision_radius and
     * camera distances).  Used by the AI for target-acquisition,
     * line-of-sight, and attack-range gating in FUN_00049480. */
    f32 range;                        /* +0xB8 piVar9[0x2E] — "range" (sight radius)  */
    f32 range_up;                     /* +0xBC piVar9[0x2F] — "range up"              */
    f32 range_down;                   /* +0xC0 piVar9[0x30] — "range down"            */
    f32 attack_range;                 /* +0xC4 piVar9[0x31] — "attackRange"            */

    /* --- unmapped gap; piVar9[0x32]..[0x34] not observed in FUN_00049480 --- */
    u8  _reserved_C8[0xD4 - 0xC8];

    /* --- critters_critter_data: drop tables ---
     * Each ``drop_<n>`` slot is a resource-ID / pointer produced by
     * FUN_000d1630 at config-load time (populated via the "dropN"
     * config keys).  ``drop_chance_<n>`` is a 0..1 probability;
     * ``drop_count_<n>`` is the integer quantity dropped when the
     * chance fires.  A drop slot with a zero pointer means
     * "no drop in this slot" — the engine scans until the first
     * zero or the full 5-slot limit, whichever comes first. */
    u32 drop_1;                       /* +0xD4 piVar9[0x35] — "drop1"                  */
    u32 drop_2;                       /* +0xD8 piVar9[0x36] — "drop2"                  */
    u32 drop_3;                       /* +0xDC piVar9[0x37] — "drop3"                  */
    u32 drop_4;                       /* +0xE0 piVar9[0x38] — "drop4"                  */
    u32 drop_5;                       /* +0xE4 piVar9[0x39] — "drop5"                  */

    u32 drop_count_1;                 /* +0xE8 piVar9[0x3A] — "dropCount1"             */
    u32 drop_count_2;                 /* +0xEC piVar9[0x3B] — "dropCount2"             */
    u32 drop_count_3;                 /* +0xF0 piVar9[0x3C] — "dropCount3"             */
    u32 drop_count_4;                 /* +0xF4 piVar9[0x3D] — "dropCount4"             */
    u32 drop_count_5;                 /* +0xF8 piVar9[0x3E] — "dropCount5"             */

    f32 drop_chance_1;                /* +0xFC  piVar9[0x3F] — "dropChance1"           */
    f32 drop_chance_2;                /* +0x100 piVar9[0x40] — "dropChance2"           */
    f32 drop_chance_3;                /* +0x104 piVar9[0x41] — "dropChance3"           */
    f32 drop_chance_4;                /* +0x108 piVar9[0x42] — "dropChance4"           */
    f32 drop_chance_5;                /* +0x10C piVar9[0x43] — "dropChance5"           */

    /* --- Anything past +0x110 is not yet mapped.  Full struct is
     * several hundred bytes; attack-triggers and more config fields
     * live in that region but stay unnamed until a shim references
     * them. */
} CritterData;


/* ==========================================================================
 * ControllerState
 * ==========================================================================
 * Per-player gamepad state populated every frame by the XInput
 * polling loop (``FUN_000a2df0`` → ``FUN_000a2880``).  One
 * :class:`ControllerState` per player (up to 4) — the array lives at
 * ``DAT_0037BE98`` and striding is 0x54 bytes
 * (``AZURIK_CONTROLLER_STATE_VA + player_index * 0x54``).
 *
 * All analog axes are normalised into ``[-1.0, 1.0]`` (sticks) or
 * ``[0.0, 1.0]`` (triggers / analog buttons); digital buttons are
 * either exactly ``0.0`` or ``1.0`` — the engine never pushes
 * intermediate values.  D-pad axes are three-valued:
 * ``-1.0 / 0.0 / +1.0``.
 *
 * Edge-detection: the 12 bytes at +0x48..+0x53 are a per-button
 * "was pressed last frame" latch.  One byte per button-like field
 * in the +0x18..+0x47 range (12 fields → 12 latch bytes, matching
 * order).  Engine callers read a rising edge as
 * ``current > 0 && latch == 0`` and write ``latch = 1`` after
 * consuming the press; the polling loop clears ``latch`` when the
 * button goes back to zero.  Shims that hook per-frame logic may
 * read ``edge_state[]`` directly to avoid double-firing on held
 * buttons.
 *
 * ``DAT_001A7AE4`` holds the "active player index" (0..3), or 4 if
 * no controller is connected.  Shims that act on the active player
 * only should dereference
 * ``ControllerState *active = &players[DAT_001A7AE4]`` when
 * ``DAT_001A7AE4 != 4``.
 */

/* Base VA of the 4-player ControllerState array (player 0). */
#define AZURIK_CONTROLLER_STATE_VA    0x0037BE98u

/* Per-player state-object pointer array.  4 × 4-byte slots at
 * VA 0x001BE314; slot ``i`` holds a pointer to player ``i``'s
 * live state struct (or NULL before that player joins).  Used
 * by the XInput polling path ``FUN_000A2880`` as
 * ``[player_idx * 4 + 0x001BE314]`` before writing into the
 * controller-state block.  BSS — zero-filled at load, populated
 * by the engine at runtime as each controller connects. */
#define AZURIK_PLAYER_STATE_PTR_ARRAY_VA  0x001BE314u

/* Active-player index (0..3, or 4 for "no controller connected"). */
#define AZURIK_ACTIVE_PLAYER_INDEX_VA 0x001A7AE4u

/* Stride in bytes between per-player ControllerState entries. */
#define AZURIK_CONTROLLER_STRIDE      0x54u

typedef struct ControllerState {
    /* --- Analog sticks (normalised to [-1.0, 1.0]) ---
     * XInput dead-zone processing in FUN_000a2880:
     *   raw = XINPUT_GAMEPAD.sThumb{LX,LY,RX,RY}
     *   if (raw < 0)  value = max(-1, (raw + 12000) * 4.815332e-5)
     *   else          value = min( 1, (raw - 12000) * 4.815332e-5)
     * — so anything inside ±12000 of centre reads as exactly 0.0.
     * The 4.815332e-5 factor normalises the remaining 20768 units
     * of travel into ±1.0.
     */
    f32 left_stick_x;                 /* +0x00 — sThumbLX normalised */
    f32 left_stick_y;                 /* +0x04 — sThumbLY normalised */
    f32 right_stick_x;                /* +0x08 — sThumbRX normalised */
    f32 right_stick_y;                /* +0x0C — sThumbRY normalised */

    /* --- D-pad (three-valued per axis: -1, 0, +1) ---
     * D-pad-X is derived from bits 0x04 (LEFT) / 0x08 (RIGHT);
     * D-pad-Y from 0x01 (UP) / 0x02 (DOWN) of XINPUT_GAMEPAD.wButtons.
     */
    f32 dpad_y;                       /* +0x10 — +1 up, -1 down, 0 neutral */
    f32 dpad_x;                       /* +0x14 — +1 right, -1 left         */

    /* --- Analog buttons (pressure 0.0..1.0, from XINPUT analog bytes) ---
     * Dead-zone: raw < 30 reads as 0.0.
     * Scale: (raw - 30) * 0.0044444446 ≈ 1/225 gives the 1.0-cap.
     * Order matches XINPUT_GAMEPAD.bAnalogButtons[] exactly. */
    f32 button_a;                     /* +0x18 — A analog pressure        */
    f32 button_b;                     /* +0x1C — B analog pressure        */
    f32 button_x;                     /* +0x20 — X analog pressure        */
    f32 button_y;                     /* +0x24 — Y analog pressure        */
    f32 button_black;                 /* +0x28 — BLACK analog pressure    */
    f32 button_white;                 /* +0x2C — WHITE analog pressure    */
    f32 trigger_left;                 /* +0x30 — LT analog pressure       */
    f32 trigger_right;                /* +0x34 — RT analog pressure       */

    /* --- Digital buttons (exactly 0.0 or 1.0) ---
     * From XINPUT_GAMEPAD.wButtons bit flags:
     *   bit 0x40 → stick_left_click  (LEFT_THUMB)
     *   bit 0x80 → stick_right_click (RIGHT_THUMB)
     *   bit 0x10 → start_button      (START)
     *   bit 0x20 → back_button       (BACK)
     */
    f32 stick_left_click;             /* +0x38 — L-stick press             */
    f32 stick_right_click;            /* +0x3C — R-stick press             */
    f32 start_button;                 /* +0x40 — START                     */
    f32 back_button;                  /* +0x44 — BACK                      */

    /* --- Edge-detect latch bytes (12 × u8) ---
     * One per ``button_*`` / ``trigger_*`` / ``stick_*`` / ``start`` /
     * ``back`` field above (12 total, same order).  The polling loop
     * clears each byte when its corresponding button returns to 0.0,
     * which is how the engine implements "consume rising edge once
     * per press" without dedicated edge-detect state elsewhere.
     */
    u8  edge_state[12];               /* +0x48..+0x53                      */
} ControllerState;


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
 * Entity — global named-object registry entry
 * ==========================================================================
 * Everything the game identifies by name (critters, pickups, shader
 * files, etc.) lives in ``ENTITY_REGISTRY`` — an array of
 * ``Entity *`` between ``AZURIK_ENTITY_REGISTRY_BEGIN_VA`` and
 * ``_END_VA``.  Looked up by the vanilla ``entity_lookup`` function.
 *
 * **Partial layout** — only the ``name`` pointer at offset 0 is
 * currently pinned.  Entity instances are extended by multiple
 * systems (animation, AI, collision) so the tail size isn't fixed.
 * Shim authors needing specific fields should declare a
 * locally-scoped ``extern``, verify via Ghidra, then consider
 * contributing a fix back here.
 */
typedef struct Entity {
    const char *name;          /* +0x00 — NUL-terminated asset key     */
    /* +0x04 .. end: opaque.  Cast to a more-specific struct
       once Ghidra identifies the subtype. */
} Entity;


/* ==========================================================================
 * ConfigTable — keyed-table config section handle (config.xbr)
 * ==========================================================================
 * Runtime handle to one of the 15 keyed-table sections in
 * ``config.xbr`` (critters_critter_data, critters_damage, magic,
 * etc.).  Consumed by the vanilla helpers:
 *
 *   ``config_name_lookup(this, name)``  → column index (row_idx)
 *   ``config_cell_value(this, row, col, default_out)`` → float10
 *
 * **Header layout** (verified from ``FUN_000D1520`` / ``FUN_000D1420``
 * decomp — note the field names here follow the decompiler's
 * orientation; the legacy ``scripts/xbr_parser.py`` uses the opposite
 * row/col naming, kept for backward compat):
 */
typedef struct ConfigTable {
    i32 num_cols;             /* +0x00 — iteration bound used by        */
                              /* config_name_lookup; typically the      */
                              /* per-entity count                       */
    i32 col_hdr_offset;       /* +0x04 — byte offset (relative to this) */
                              /* of the 8-byte-stride column-header     */
                              /* table (``name_ptr``, ``name_off``) */
    i32 num_rows;             /* +0x08 — bound for the row-index arg    */
                              /* of config_cell_value                   */
    i32 total_cells;          /* +0x0C — num_rows * num_cols            */
    i32 cell_data_offset;     /* +0x10 — byte offset (relative to this) */
                              /* of the 16-byte-stride cell data        */
    /* Rows start at ``(u8*)this + 0x14`` */
} ConfigTable;

/* Cell record — one 16-byte slot inside the cell data array.
 * Cell stride in bytes: ``((row * num_cols) + col) * 16``.
 */
typedef struct ConfigCell {
    i32 kind;                 /* +0x00 — 0=null_default / 1=number      */
                              /*          2=string / 3=nested_ptr       */
                              /*          (0, 1 confirmed from decomp;  */
                              /*           2, 3 probable — see xbr doc) */
    u32 _reserved_04;         /* +0x04 — padding / flags                */
    f64 value;                /* +0x08 — 8-byte double when kind==1     */
} ConfigCell;


/* ==========================================================================
 * IndexEntry — index.xbr-backed asset dispatcher entry
 * ==========================================================================
 * One slot inside the index.xbr lookup machinery, consumed by
 * ``load_asset_by_fourcc``.  Azurik keeps a runtime wrapper around
 * each indx asset with load-state flags and a pointer to the packed
 * record array.
 *
 * Partial layout — pinned enough for a shim to check load state
 * + access the record table without crashing; full size (~0x40+)
 * isn't confirmed.
 */
typedef struct IndexEntry {
    u32 _reserved_00;         /* +0x00                                  */
    u32 _reserved_04;         /* +0x04                                  */
    u32 first_record_idx;     /* +0x08 — record_table[0] starts at this */
                              /* global asset index                     */
    struct IndexRecord *records; /* +0x0C — packed 20-byte records     */
    u32 file_base_offset;     /* +0x10 — offset correction for string-  */
                              /* pool relative file refs                */
    u8  _reserved_14[6];      /* +0x14 .. +0x19                         */
    u16 flags;                /* +0x1A — load state:                    */
                              /*          (flags & 0x3000) == 0x1000 ?  */
                              /*          (flags & 0x3000) == 0x2000 ⇒ loading */
                              /*          (flags & 0x3000) == 0x3000 ⇒ loaded  */
    /* +0x1C .. end: opaque */
} IndexEntry;

/* One index.xbr record — 20 bytes, documented per-field in
 * ``docs/LEARNINGS.md`` § index.xbr. */
typedef struct IndexRecord {
    u32 length;               /* +0x00 — string length for ``off1`` */
    u32 off1;                 /* +0x04 — pool offset: file name     */
    char fourcc[4];           /* +0x08 — asset type (body, banm, …) */
    u8  disc;                 /* +0x0C — subtype discriminator      */
    u8  pad[3];               /* +0x0D .. +0x0F — zero              */
    u32 off2;                 /* +0x10 — pool offset: asset key     */
} IndexRecord;


/* ==========================================================================
 * MovieContext — Bink playback state (opaque w/ vtable)
 * ==========================================================================
 * Pointed to by ``AZURIK_MOVIE_CONTEXT_PTR_VA``.  Allocated by
 * ``FUN_000D00F0`` (Bink init), stepped by ``poll_movie`` each frame.
 * The internals are allocated by the Bink library and depend on
 * frame count / resolution; all the game code needs is the vtable
 * at offset 0.
 *
 * Observed vtable slots (from ``poll_movie`` + ``boot_state_tick``
 * case 2):
 *
 *   vtable[0x00] = advance(ctx, dt_seconds)     — called every tick
 *   vtable[0x04] = is_done(ctx) -> bool          — true when finished
 *   vtable[0x10] = destroy(ctx, release_flag)    — tear down
 *
 * Shims that want to observe or abort playback should call through
 * the vtable (so Bink-internal state stays coherent) rather than
 * touching struct fields directly.
 */
/* Forward declare so the vtable can name its receiver type. */
struct MovieContext;

typedef struct MovieContextVTable {
    void (*advance)(struct MovieContext *ctx, float dt_seconds);
    u32 (*is_done)(struct MovieContext *ctx);
    void (*_reserved_08)(void);
    void (*_reserved_0C)(void);
    void (*destroy)(struct MovieContext *ctx, int release_flag);
} MovieContextVTable;

typedef struct MovieContext {
    const MovieContextVTable *vtable;  /* +0x00 */
    /* +0x04 .. end: Bink-library-owned opaque state.  Don't touch. */
} MovieContext;


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
 * Shared numerical constants (.rdata / .data)
 * ==========================================================================
 * Widely-read float constants.  Declared as VA anchors so shims can
 * reference them via DIR32 without each hardcoding the literal.
 *
 * NB: these constants are shared across dozens of call sites.  DO NOT
 * patch their values — use the player-physics C1-style redirect
 * (rewrite individual instruction operands to new VAs) if you need
 * per-site constants.
 */
#define AZURIK_FLOAT_ZERO_VA        0x001A2508u  /* f32 0.0   (.rdata, 1 reader) */
#define AZURIK_FLOAT_HALF_VA        0x001A9C84u  /* f32 0.5   (.data, ~15 readers) */
#define AZURIK_FLOAT_ONE_VA         0x001A9C88u  /* f32 1.0   (.data, ~15 readers) */
#define AZURIK_FLOAT_RUN_MULT_VA    0x001A25BCu  /* f32 3.0 run-multiplier (45 readers; alias of AZURIK_SHARED_RUN_MULT_VA) */


/* ==========================================================================
 * Entity registry (runtime vector)
 * ==========================================================================
 * A ``std::vector<Entity *>``-shaped registry that ``entity_lookup``
 * (``FUN_0004B510``) scans to resolve names → entity descriptors.
 * Runtime-initialised from config files, then grown as critters
 * load.  Shims can read directly or call the exposed vanilla
 * function (prefer the latter — cheaper + matches the game's own
 * lookup semantics including the fallback-registration branch).
 *
 * The BEGIN / END anchors are BSS — vanilla XBE bytes are zero;
 * the pointers are set during init by the game's constructors.
 * Useful for shims that want to iterate every registered entity.
 */
#define AZURIK_ENTITY_REGISTRY_BEGIN_VA  0x0038C1E4u  /* Entity **begin */
#define AZURIK_ENTITY_REGISTRY_END_VA    0x0038C1E8u  /* Entity **end (one past last) */
#define AZURIK_ENTITY_REGISTRY_CAP_VA    0x0038C1ECu  /* Entity **capacity */


/* ==========================================================================
 * Boot-time movie subsystem state (BSS)
 * ==========================================================================
 * Movie state machine globals the boot code reads on every tick.
 * All BSS — vanilla XBE bytes are zero; initialised by ``play_movie_fn``
 * on first use and updated by ``poll_movie`` each frame.
 *
 * Exposed so shims that intercept the movie pipeline (e.g. a
 * ``qol_skip_prophecy`` shim) can inspect or reset the state
 * without going through the full vanilla call path.
 */
#define AZURIK_MOVIE_CONTEXT_PTR_VA  0x001BCDC8u  /* PVOID — current movie context (0 when idle) */
#define AZURIK_MOVIE_IDLE_FLAG_VA    0x001BCDB4u  /* u8    — AL-styled return byte of play_movie_fn */

/* Walking-state flag byte (tested by FUN_00085F50 and FUN_0008CCC0
 * at LAB_000863E4; set during the ground-walk state transition). */
#define AZURIK_WALKING_STATE_FLAG_VA 0x0037ADECu

/* Current / next movie path strings (pointed to by BSS globals).
 *
 *   AZURIK_MOVIE_STAGED_PATH_VA      — char * — the movie the boot
 *                                      state machine is about to
 *                                      play / currently playing.
 *                                      Read by ``boot_state_tick``
 *                                      case 0; non-null means
 *                                      "there's a movie queued".
 *                                      NULL during normal gameplay.
 *
 *   AZURIK_MOVIE_SKIP_TARGET_VA      — char * — fallback path to
 *                                      pass to ``play_movie_fn`` when
 *                                      the staged path isn't prefixed
 *                                      with ``movies/scenes/``.
 *                                      Defaults to ``0x001A1E74`` when
 *                                      the global is NULL.
 *
 * A ``qol_force_cutscene`` shim would write both VAs + set
 * ``AZURIK_BOOT_STATE_VA`` to ``BOOT_STATE_INIT`` to trigger
 * playback of any Bink movie by path. */
#define AZURIK_MOVIE_STAGED_PATH_VA  0x001BCDD0u
#define AZURIK_MOVIE_SKIP_TARGET_VA  0x001BCDD4u

/* FeatureClassRegistry — parallel lookup table to ENTITY_REGISTRY_*
 * above but keyed by name string → u32 value.  Layout:
 *
 *     struct FeatureClass { char *name;  u32 value; };
 *     FeatureClass *begin = *AZURIK_FEATURE_CLASS_REGISTRY_BEGIN_VA;
 *     FeatureClass *end   = *AZURIK_FEATURE_CLASS_REGISTRY_END_VA;
 *     count               = (end - begin);
 *
 * Looked up by ``FUN_000493D0`` (the helper that populates
 * ``CritterData.feature_class_id`` from a name string).  Useful for
 * shims that want to add new feature classes to the runtime
 * registry at boot. */
#define AZURIK_FEATURE_CLASS_REGISTRY_BEGIN_VA 0x0038C1F4u
#define AZURIK_FEATURE_CLASS_REGISTRY_END_VA   0x0038C1F8u


/* ==========================================================================
 * Developer level-select hub gate (BSS)
 * ==========================================================================
 * ``selector.xbr`` is a developer cheat-menu level that portals to
 * every live level + direct cutscene playback.  The loader at
 * ``FUN_00052F50`` checks this BSS flag during boot:
 *
 *     mov  esi, [AZURIK_DEV_MENU_FLAG_VA]  ; i32 — 0xFFFFFFFF = disabled
 *     cmp  esi, -1
 *     jnz  dev_menu_enabled                ; non-``-1`` ⇒ selector loads
 *     mov  esi, 0x3                        ; default code path
 *     mov  ebp, 0x001A1E3C                 ; "levels/selector" string
 *
 * A single DIR32 store of any non-``-1`` value into this flag during
 * boot force-enables the cheat menu — no trampoline needed.  See
 * docs/LEARNINGS.md § selector.xbr + the 20-line shim template at
 * docs/SHIMS.md § qol_enable_dev_menu (deferred).
 */
#define AZURIK_DEV_MENU_FLAG_VA      0x001BCDD8u


/* ==========================================================================
 * Known level / asset string anchors
 * ==========================================================================
 * Useful for shims that want to PUSH a string VA into code (e.g.
 * trigger a level load from a debug hotkey) without pinning the
 * string bytes themselves. */
#define AZURIK_STR_LEVELS_SELECTOR_VA 0x001A1E3Cu  /* "levels/selector"     */
#define AZURIK_STR_LEVELS_TRAINING_VA 0x001A1E4Cu  /* "levels/training_room"*/
#define AZURIK_STR_INDEX_XBR_PATH_VA  0x0019ADB0u  /* "index\\index.xbr"    */

/* ---- Save-slot UI (UTF-16LE wide strings in .rdata) ----
 *
 * Useful for shims that want to rename save slots, localise the
 * "X days / 1 day" playtime formatting, or hijack the "Scratch
 * Game" developer entry.  All four are wchar_t[] / ``unsigned
 * short *`` — dereference with the matching width, not ``char *``.
 */
#define AZURIK_STR_SAVEGAME_FMT_W_VA  0x0019E054u  /* L"SaveGame #%d"      */
#define AZURIK_STR_START_NEW_GAME_W_VA 0x0019E070u /* L"Start New Game"    */
#define AZURIK_STR_SCRATCH_GAME_W_VA  0x0019E54Cu  /* L"scratch game"      */
#define AZURIK_STR_DUMMY_TEMP_GAME_W_VA 0x0019E56Cu /* L"DummyTempGame"    */
#define AZURIK_STR_DAYS_FMT_W_VA      0x0019E038u  /* L"%d days"           */
#define AZURIK_STR_ONE_DAY_W_VA       0x0019E048u  /* L"1 day"             */


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
_Static_assert(__builtin_offsetof(CritterData, range) == 0xB8,
               "CritterData.range drifted — _reserved_A0 gap size wrong?");
_Static_assert(__builtin_offsetof(CritterData, attack_range) == 0xC4,
               "CritterData.attack_range drifted — range[] layout broke");
_Static_assert(__builtin_offsetof(CritterData, drop_1) == 0xD4,
               "CritterData.drop_1 drifted — _reserved_C8 gap size wrong?");
_Static_assert(__builtin_offsetof(CritterData, drop_5) == 0xE4,
               "CritterData.drop_5 drifted");
_Static_assert(__builtin_offsetof(CritterData, drop_count_1) == 0xE8,
               "CritterData.drop_count_1 drifted");
_Static_assert(__builtin_offsetof(CritterData, drop_count_5) == 0xF8,
               "CritterData.drop_count_5 drifted");
_Static_assert(__builtin_offsetof(CritterData, drop_chance_1) == 0xFC,
               "CritterData.drop_chance_1 drifted");
_Static_assert(__builtin_offsetof(CritterData, drop_chance_5) == 0x10C,
               "CritterData.drop_chance_5 drifted");

/* ControllerState (from XInput polling in FUN_000a2880). */
_Static_assert(sizeof(ControllerState) == 0x54,
               "ControllerState must be exactly 0x54 bytes — the "
               "per-player stride the XInput poll uses");
_Static_assert(__builtin_offsetof(ControllerState, left_stick_x) == 0x00,
               "ControllerState.left_stick_x drifted");
_Static_assert(__builtin_offsetof(ControllerState, right_stick_y) == 0x0C,
               "ControllerState.right_stick_y drifted");
_Static_assert(__builtin_offsetof(ControllerState, dpad_y) == 0x10,
               "ControllerState.dpad_y drifted");
_Static_assert(__builtin_offsetof(ControllerState, button_a) == 0x18,
               "ControllerState.button_a drifted");
_Static_assert(__builtin_offsetof(ControllerState, trigger_right) == 0x34,
               "ControllerState.trigger_right drifted");
_Static_assert(__builtin_offsetof(ControllerState, stick_left_click) == 0x38,
               "ControllerState.stick_left_click drifted");
_Static_assert(__builtin_offsetof(ControllerState, start_button) == 0x40,
               "ControllerState.start_button drifted");
_Static_assert(__builtin_offsetof(ControllerState, back_button) == 0x44,
               "ControllerState.back_button drifted");
_Static_assert(__builtin_offsetof(ControllerState, edge_state) == 0x48,
               "ControllerState.edge_state drifted");

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

/* ConfigTable (from FUN_000D1420 / FUN_000D1520). */
_Static_assert(__builtin_offsetof(ConfigTable, num_cols) == 0x00,
               "ConfigTable.num_cols must be first field — "
               "config_name_lookup does *(int*)this");
_Static_assert(__builtin_offsetof(ConfigTable, num_rows) == 0x08,
               "ConfigTable.num_rows drifted — config_cell_value "
               "reads param_1[2] as the row-index bound");
_Static_assert(__builtin_offsetof(ConfigTable, cell_data_offset) == 0x10,
               "ConfigTable.cell_data_offset drifted — "
               "config_cell_value reads param_1[4] for it");
_Static_assert(sizeof(ConfigTable) == 0x14,
               "ConfigTable header is exactly 20 bytes; rows start "
               "at this+0x14 per config_cell_value");
_Static_assert(sizeof(ConfigCell) == 0x10,
               "ConfigCell stride MUST be 16 bytes — the "
               "(*param_1 * row + col) * 0x10 arithmetic relies on it");

/* IndexEntry (from FUN_000A67A0). */
_Static_assert(__builtin_offsetof(IndexEntry, first_record_idx) == 0x08,
               "IndexEntry.first_record_idx drifted — "
               "load_asset_by_fourcc uses piVar3[2]");
_Static_assert(__builtin_offsetof(IndexEntry, records) == 0x0C,
               "IndexEntry.records drifted — piVar3[3]");
_Static_assert(__builtin_offsetof(IndexEntry, file_base_offset) == 0x10,
               "IndexEntry.file_base_offset drifted — piVar3[4]");
_Static_assert(__builtin_offsetof(IndexEntry, flags) == 0x1A,
               "IndexEntry.flags drifted — *(ushort*)(piVar3 + 0x1A)");

/* IndexRecord (from docs/LEARNINGS.md § index.xbr). */
_Static_assert(sizeof(IndexRecord) == 0x14,
               "IndexRecord must be exactly 20 bytes — the index.xbr "
               "record table uses a 20-byte stride");
_Static_assert(__builtin_offsetof(IndexRecord, off1) == 0x04,
               "IndexRecord.off1 drifted");
_Static_assert(__builtin_offsetof(IndexRecord, fourcc) == 0x08,
               "IndexRecord.fourcc drifted — load_asset_by_fourcc "
               "compares piVar1[2] against the requested fourcc");
_Static_assert(__builtin_offsetof(IndexRecord, off2) == 0x10,
               "IndexRecord.off2 drifted");

/* MovieContext (vtable-at-0 pattern; size not pinned — Bink-owned). */
_Static_assert(__builtin_offsetof(MovieContext, vtable) == 0x00,
               "MovieContext.vtable must be first field — poll_movie "
               "indirects through *DAT_001bcdb0 at offset 0");
#endif


/* ==================================================================
 * Player entity field offsets (April 2026 late RE pass)
 *
 * The player entity struct is reached as ``entity`` in the physics
 * state-machine dispatch (``FUN_0008CCC0``).  ESI / unaff_ESI in
 * the per-state functions is the same pointer.  Key fields and
 * their physics roles:
 *
 *   +0x20   entity_input_state_ptr (points at PlayerInputState)
 *   +0x24   velocity.x
 *   +0x28   velocity.y
 *   +0x2C   velocity.z
 *   +0x30   entity_type (0 / 0x150 = cinematic; other = gameplay)
 *   +0x60   flags byte (bit 0x4 = jumped_this_frame? etc.)
 *   +0x94   state (0 ground, 1 climb, 2 air, 3/4 slope-slide,
 *                  5 prejump, 6 swim, 7-8 dead, 256+ cutscene)
 *   +0x124  magnitude (WHITE/BACK boost × stick mag — computed
 *                      per frame by player_input_tick)
 *   +0x128..+0x130  direction vector (normalized stick dir)
 *   +0x134  grab_target (-1 = none; else ledge-grab state)
 *   +0x140  air_control_speed (9.0 default, 12.0 w/ air power)
 *   +0x144  jump_height       (1.1 default, 1.2 w/ air power)
 *   +0x148/0x14C  velocity-in-jump (dynamics)
 *   +0x150  "airborne-entered" flag byte
 *   +0x164  peak_z (max height reached this jump; used by
 *                    wing_flap for flap-height budget)
 *   +0x168  prev_z (fall-damage dispatcher input)
 *   +0x16C  misc flags (bit 0x2 = had-landed)
 *   +0xD8   flap_counter (INCREMENTED per flap; compared vs
 *                         armor.flap_count by wing_flap)
 *
 * PlayerInputState (pointed at by entity[+0x20]):
 *   +0x20   flags byte — bit 0x40 = WHITE/BACK held (roll boost)
 *                         bit 0x04 = JUMP button edge-triggered
 *                         bit 0x02, 0x08, 0x10, 0x20 = misc inputs
 *   +0x48..+0x4D   edge-lock bytes for various button bits
 *
 * ArmorMgr chain (reached via entity[+0x20].gameplay_ptr[+0x154][+0xCC]):
 *   armor_mgr +0x20   → level_struct pointer
 *   armor_mgr +0x24   → fuel_current  (float, consumed by wing_flap)
 *   level_struct +0x08 → air_power_level (int; 1/2/3 granted,
 *                                         0/4 = no air power)
 *   level_struct +0x38 → armor.flap_count (1/2/5 vanilla for
 *                                          air power 1/2/3)
 *
 * IMPORTANT: ``DAT_001A7AE4`` is NOT the air_power_level — it's
 * the active XInput CONTROLLER index (0-3 for port, 4 for
 * "no controller").  The wing_flap_count shim dispatches on
 * the level struct chain above, not on this global.
 * ================================================================== */


/* ==================================================================
 * Player physics isolated constants (April 2026 late RE pass)
 *
 * Single- or few-reader .rdata floats.  Each of these can be
 * patched as a direct 4-byte overwrite without collateral to
 * unrelated systems (shared constants have separate tables).
 * ================================================================== */

/* 0x001AAB68 = 2.0f — slope-slide state-3 velocity scalar.
 * Single reader: FMUL at VA 0x89B76 inside player_slope_slide_tick.
 * Patched by ``slope_slide_speed_scale`` slider. */
#define AZURIK_SLOPE_SLIDE_CONST_VA   0x001AAB68
#define AZURIK_VANILLA_SLOPE_SLIDE    2.0f

/* 0x001980E4 = 2.0f — climbing-state velocity scalar.
 * Exactly 2 readers, both in player_climb_tick (VAs 0x87FA7 +
 * 0x88357).  Patched by ``climb_speed_scale`` slider. */
#define AZURIK_CLIMB_SPEED_CONST_VA   0x001980E4
#define AZURIK_VANILLA_CLIMB_SPEED    2.0f

/* 0x001980A8 = 9.8f — global gravity constant.
 * MANY readers (physics, animation, camera).  Per-site rewrite
 * via FLD-to-injected-constant is the only safe scaling approach
 * (direct overwrite would scale EVERYTHING, not just the chosen
 * site).  Used by ``jump_speed_scale`` and ``flap_height_scale``. */
#define AZURIK_GRAVITY_CONST_VA       0x001980A8
#define AZURIK_VANILLA_GRAVITY        9.8f

/* 0x001A25B8 = 6.0f — subsequent-flap-height threshold.
 * Read once from wing_flap (VA 0x893C0).  When the player has
 * fallen > 6m below their flap peak, the next flap's v0 is
 * halved.  Not currently sliderised but available as an RE
 * anchor. */
#define AZURIK_FLAP_SUBSEQUENT_THRESHOLD_VA  0x001A25B8
#define AZURIK_VANILLA_FLAP_SUB_THRESH       6.0f

/* 0x001A2510 = 0.5f — subsequent-flap halving factor.
 * Shared (read from 260+ sites — generic "half").  Our
 * ``flap_subsequent_scale`` slider rewrites the FMUL at VA
 * 0x893DD to reference an injected float, NOT this constant. */
#define AZURIK_FLAP_HALVING_CONST_VA  0x001A2510
#define AZURIK_VANILLA_FLAP_HALVING   0.5f

/* 0x001A26C4 = 1.5f — wing-flap final boost multiplier.
 * Read at VA 0x893EB (wing_flap) and 10+ unrelated sites.
 * Per-site rewrite only. */
#define AZURIK_FLAP_BOOST_CONST_VA    0x001A26C4
#define AZURIK_VANILLA_FLAP_BOOST     1.5f

/* 0x001A25BC = 3.0f — WHITE/BACK button roll-boost multiplier.
 * Shared (45 readers).  Our ``roll_speed_scale`` slider
 * rewrites the FMUL at VA 0x849E4 (in player_input_tick) to
 * reference an injected float instead of the shared constant. */
#define AZURIK_ROLL_BOOST_CONST_VA    0x001A25BC
#define AZURIK_VANILLA_ROLL_BOOST     3.0f

/* 0x001A25B4 = 10.0f — swim stroke multiplier.
 * Shared, but only one player-physics reader at VA 0x8B7BF.
 * Used by ``swim_speed_scale`` via FMUL-to-injected-constant. */
#define AZURIK_SWIM_BOOST_CONST_VA    0x001A25B4
#define AZURIK_VANILLA_SWIM_BOOST     10.0f


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_SHIM_H */
