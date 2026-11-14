/* dw_evolution.h -- the evolution chooser as native C, shared by the Native
 * Evolution Engine (dw_evolution.c) and the Evolution Guide (dw_evoguide.c).
 *
 * dw_evo_model() runs the game's own chooser over the LIVE tables -- found by
 * decoding the instructions the chooser itself uses to address them, so a mod
 * that relocates or rewrites a table is followed -- and records every
 * intermediate value. It reads guest memory only; it writes nothing. */
#ifndef DW_EVOLUTION_H
#define DW_EVOLUTION_H

#include <stdint.h>

#define DW_EVO_SLOTS 6

/* EVO_REQ_DATA row fields, in table order (include/dw/evl.h EvoRequirements). */
enum { DW_REQ_FROM, DW_REQ_HP, DW_REQ_MP, DW_REQ_OFF, DW_REQ_DEF, DW_REQ_SPEED,
       DW_REQ_BRAIN, DW_REQ_CARE, DW_REQ_WEIGHT, DW_REQ_DISC, DW_REQ_HAPPY,
       DW_REQ_BATTLES, DW_REQ_TECHS, DW_REQ_COUNT };

/* Bonus bits (bonus_present / bonus_hit). */
enum { DW_BONUS_FROM = 1, DW_BONUS_DISC = 2, DW_BONUS_HAPPY = 4,
       DW_BONUS_BATTLES = 8, DW_BONUS_TECHS = 16 };

typedef struct {
    int16_t target;
    char name[21];
    uint8_t level;
    int16_t req[DW_REQ_COUNT];
    uint8_t flags;            /* 0x10 care is a maximum, 0x01 battles is a maximum */
    uint8_t care_met, weight_met, stats_met, bonus_met;
    uint8_t stat_met[6];      /* per stat, targets that are not Rookies */
    int8_t highest;           /* Rookie targets: the partner's highest stat (last tie) */
    uint8_t bonus_present, bonus_hit;
    int8_t points;            /* groups met, 0..4 */
    int8_t score;             /* what the chooser compared, after the raised rule */
    int8_t raised_adj;        /* 0, -1 (zeroed: already raised), +1 (unraised vs raised best) */
    uint8_t qualifies;        /* score >= 3 */
    int16_t mean;             /* Rookie/Champion qualifiers: the mean compared; -1 otherwise */
    char from_name[21];       /* name of req[DW_REQ_FROM] when it is set */
} DwEvoCandidate;

typedef struct {
    int ok;                   /* 0: the chooser code or tables were not recognised */
    int32_t partner;
    char name[21];
    uint8_t level;
    int16_t timer, threshold; /* threshold 0: this level never evolves */
    int16_t stats[6];         /* as the chooser sees them: HP/10, MP/10, Off, Def, Speed, Brains */
    int16_t care, weight, discipline, happiness, battles;
    int8_t techs;
    int n;
    DwEvoCandidate c[DW_EVO_SLOTS];
    int16_t pick;             /* the chooser's result right now, -1 none */
    char pick_name[21];
    int pick_defined;         /* 0: a Fresh fall-through returns a stale register */
} DwEvoModel;

int dw_evo_model(DwEvoModel* m);

/* getFreshEvolutionTarget's mapping; `fallthrough` is returned unchanged for
 * any other species (the guest returns whatever $v0 held). */
int32_t dw_evo_fresh_target(int32_t current, int32_t fallthrough);

/* hasDigimonRaised, as whichever implementation is live (dw_roster_flags.c). */
int dw_species_raised(uint32_t species);

#endif
