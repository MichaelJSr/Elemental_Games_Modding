/* Azurik shim-authoring header.
 *
 * Freestanding declarations for the Azurik game engine's in-memory
 * data structures, reverse-engineered from Ghidra.  Shim authors
 * should prefer these named fields over hand-counted ``[reg + 0xNN]``
 * offsets — they produce identical machine code but keep the source
 * readable.
 *
 * Every struct documents:
 * - Which Ghidra function(s) we learned the layout from.
 * - What we DO NOT yet know (named ``_reservedNN``).  Touching those
 *   fields is allowed but at your own risk — they may be padding or
 *   genuinely live data.
 *
 * ABI constraints:
 * - i386 little-endian, 4-byte struct alignment (compile.sh enables
 *   ``-ffreestanding -nostdlib`` with clang's default ``-mstackrealign``).
 * - ``float`` is 32-bit IEEE 754, ``double`` is 64-bit.
 * - ``char`` is signed by default; we use explicit ``u8`` / ``i8``
 *   aliases below to stay unambiguous.
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
 * Opaque pointer types
 * ==========================================================================
 * Use these when a shim only needs to pass a pointer through to a
 * vanilla function without accessing any fields.  Prefer the named
 * structs below whenever you DO want to read / write specific fields. */
typedef void *EntityHandle;      /* Anything the game models as an entity */
typedef void *ConfigTableHandle; /* A loaded `tabl` chunk from config.xbr  */
typedef void *ScenePtr;          /* Opaque scene / world graph handle      */


/* ==========================================================================
 * CritterData
 * ==========================================================================
 * The in-memory descriptor Azurik builds for every critter (player
 * included — garret4 is a critter under the hood).  Populated at boot
 * by ``FUN_00049480`` from two config.xbr tables: ``critters_engine``
 * supplies the collision / rendering fields; ``critters_critter_data``
 * supplies the gameplay fields (note: ``walkSpeed`` / ``runSpeed``
 * come out as the default ``1.0`` because that table doesn't actually
 * carry those rows — see azurik_mod/patches/player_physics.py for
 * the full story).
 *
 * Layout derived from Ghidra decompilation of ``FUN_00049480``; each
 * field's Ghidra index is in a comment next to the C field.  Total
 * struct size is larger than the fields we've named — runtime code
 * references offsets up to about 0x500, but the upper region is
 * mostly animation / scripting state we don't need for physics
 * modding.  Use ``_reservedNN`` slots to reach higher offsets if you
 * really need them.
 *
 * Field offsets (hex) below are BYTE offsets from the struct base.
 */
typedef struct CritterData {
    u32 _reserved_00;                 /* piVar9[0]  — parent / vtable ptr        */
    u32 _reserved_04;                 /* piVar9[1]  */
    u32 _reserved_08;                 /* piVar9[2]  */
    u32 flags;                        /* piVar9[3]  — feature bitfield           */
    u32 _reserved_10;                 /* piVar9[4]  */
    u32 damage_sound_id;              /* piVar9[5]  */
    f32 collision_radius;             /* piVar9[6]  0x18                          */
    f32 collision_aspect_ratio;       /* piVar9[7]  0x1C                          */
    f32 player_collision_radius;      /* piVar9[8]  0x20                          */
    f32 scale;                        /* piVar9[9]  0x24                          */
    u32 skin_index;                   /* piVar9[0xA] 0x28                          */
    f32 bound_radius;                 /* piVar9[0xB] 0x2C                          */
    f32 far_clip;                     /* piVar9[0xC] 0x30                          */
    f32 awake_distance;               /* piVar9[0xD] 0x34                          */

    /* --- gameplay speeds ----------------------------------------------
     * These four are populated from ``critters_critter_data`` but
     * that table has no ``walkSpeed`` / ``runSpeed`` rows, so the
     * engine fills them with the default 1.0.  See
     * ``player_physics.py`` for the full dead-data explanation.    */
    f32 walk_speed;                   /* piVar9[0xE] 0x38 — always 1.0 at runtime  */
    f32 walk_anim_speed;              /* piVar9[0xF] 0x3C                          */
    f32 run_speed;                    /* piVar9[0x10] 0x40 — loaded by player_phys */
    f32 run_anim_speed;               /* piVar9[0x11] 0x44                          */

    u32 damage_multiplier;            /* piVar9[0x12] 0x48 — per-damage-type lookup */
    u32 hitpoints;                    /* piVar9[0x13] 0x4C                          */
    u32 damage_vuln_norm;             /* piVar9[0x14] 0x50                          */
    u32 damage_vuln_elem;             /* piVar9[0x15] 0x54                          */
    u32 damage_vuln_misc;             /* piVar9[0x16] 0x58                          */
    u32 _reserved_5C;                 /* piVar9[0x17] 0x5C                          */
    u32 _reserved_60;                 /* piVar9[0x18] 0x60                          */
    u32 engine_flags;                 /* piVar9[0x19] 0x64 — realm/feature bits    */
    u32 flocking_fear;                /* piVar9[0x1A] 0x68                          */
    u32 flocking_follow;              /* piVar9[0x1B] 0x6C                          */
    u32 flocking_attack;              /* piVar9[0x1C] 0x70                          */
    u32 flocking_food;                /* piVar9[0x1D] 0x74                          */
    u32 _reserved_78;                 /* piVar9[0x1E] 0x78                          */
    u32 _reserved_7C;                 /* piVar9[0x1F] 0x7C                          */
    f32 drown_time;                   /* piVar9[0x20] 0x80                          */
    f32 corpse_wait_time;             /* piVar9[0x21] 0x84                          */
    f32 corpse_fade_time;             /* piVar9[0x22] 0x88                          */
    u32 cell_override_ptr;            /* piVar9[0x23] 0x8C                          */
    u32 _reserved_90;                 /* piVar9[0x24] 0x90                          */
    f32 shadow_size;                  /* piVar9[0x25] 0x94                          */
    f32 clip_plane_offset;            /* piVar9[0x26] 0x98                          */
    i32 shadow_texture_res;           /* piVar9[0x27] 0x9C  (-1 = no shadow)       */
    /* Anything past here is not yet named.  The full struct is about
     * 0x300 bytes; pad as needed if your shim has to reach those
     * offsets. */
} CritterData;

/* Shared scratch constant the engine treats as the "run speed
 * multiplier" at some call sites.  ``.rdata`` float.  PLAYER-MOVEMENT
 * code does NOT use this constant directly anymore (the
 * player_physics pack rewrites the player's FMUL to reference a
 * per-game injected float); every other reader still does.  Do NOT
 * mutate this from a shim. */
#define AZURIK_SHARED_RUN_MULT_VA  0x001A25BCu


/* ==========================================================================
 * PlayerInputState
 * ==========================================================================
 * Per-frame player-movement state the engine fills from stick input
 * and the current critter's critter-data fields.  Populated by
 * ``FUN_00084f90`` and consumed by ``FUN_00084940`` (which writes the
 * final magnitude + direction fields) and ``FUN_00085f50`` (which
 * turns those into a world-space velocity via
 * ``vel = critter->run_speed * magnitude * unit_direction``).
 *
 * The struct is large (> 0x140 bytes; observed writes go up to at
 * least 0x17C for animation state).  Offsets we understand are named;
 * the gaps are filled with ``_reservedNN`` slots so shims can take
 * pointers into known fields safely.
 */

/* Flag bits at offset 0x20 of PlayerInputState.flags (u8). */
#define PLAYER_FLAG_FALLING  0x01u  /* Physics-only motion (no stick input) */
#define PLAYER_FLAG_RUNNING  0x40u  /* Run button held                      */
/* Other bits at 0x02 / 0x04 / 0x08 / 0x10 / 0x20 / 0x80 exist but
 * their semantics aren't fully nailed down.  Read-only from shims. */

typedef struct PlayerInputState {
    u32 entity_class_ptr;             /* +0x00 — pointer to the critter's class     */
    u32 dt_scale;                     /* +0x04 — (float) per-frame dt scaler         */
    f32 stick_x;                      /* +0x08 — raw stick X component               */
    f32 stick_y;                      /* +0x0C — raw stick Y component (also used as
                                       *          the falling-state "delta")         */
    f32 stick_angle;                  /* +0x10 — atan2(stick_y, stick_x)             */
    f32 stick_angle_base;             /* +0x14 — angle at idle (relative base)       */
    f32 stick_angle_running;          /* +0x18 — angle used when running             */
    f32 stick_magnitude;              /* +0x1C — sqrt(x²+y²), in [0, 1]              */

    u8  flags;                        /* +0x20 — PLAYER_FLAG_*                      */
    u8  dead;                         /* +0x21 — nonzero once player died            */
    u8  _reserved_22;                 /* +0x22                                       */
    u8  _reserved_23;                 /* +0x23                                       */

    f32 position_x;                   /* +0x24 — world-space position X              */
    f32 position_y;                   /* +0x28 — world-space position Y              */
    f32 position_z;                   /* +0x2C — world-space position Z              */

    u8  _reserved_30[0x04];           /* +0x30 — orientation / rotation scratch     */
    CritterData *critter_data;        /* +0x34 — ptr to the player's CritterData    */
    u8  _reserved_38[0x04];           /* +0x38 */

    f32 previous_position_x;          /* +0x3C                                       */
    f32 previous_position_y;          /* +0x40                                       */
    f32 previous_position_z;          /* +0x44                                       */

    u8  _reserved_48[0x120 - 0x48];   /* +0x48 .. +0x120 — animation / IK scratch   */

    /* --- magnitude + direction outputs of FUN_00084940 ---
     * These three fields are the ONLY ones player_physics touches:
     * magnitude is our walk_scale / run_scale target, direction is
     * the unit vector the final velocity rides on. */
    f32 direction_angle;              /* +0x120 — output angle (radians)             */
    f32 magnitude;                    /* +0x124 — walking = stick mag, running = * 3 */
    f32 direction_x;                  /* +0x128 — cos-based unit-direction X         */
    f32 direction_y;                  /* +0x12C — unit-direction Y                   */
    f32 direction_z;                  /* +0x130 — unit-direction Z (always 0 today)  */
} PlayerInputState;


/* ==========================================================================
 * Conveniences
 * ========================================================================== */

/* ``CONTAINER_OF(ptr, type, member)`` — get a pointer to the enclosing
 * struct from a pointer to one of its members.  Lets shims that
 * hook on a specific field reach the rest of the PlayerInputState. */
#define CONTAINER_OF(ptr, type, member) \
    ((type *)((u8 *)(ptr) - (u32)&((type *)0)->member))


/* Sanity check: if the compiler / target ever drifts away from a
 * 4-byte struct alignment the shim pipeline will silently produce
 * wrong offsets.  Pin it with a classic static assert at the bottom
 * of the header so the compile fails loudly if something regresses. */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
_Static_assert(sizeof(u32) == 4, "u32 must be 4 bytes");
_Static_assert(sizeof(f32) == 4, "f32 must be 4 bytes");
_Static_assert(sizeof(CritterData) >= 0xA0,
               "CritterData layout regressed");
_Static_assert(sizeof(PlayerInputState) >= 0x134,
               "PlayerInputState layout regressed");
#endif


#ifdef __cplusplus
}
#endif

#endif /* AZURIK_SHIM_H */
