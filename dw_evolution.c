/* Native evolution engine, step 1: prove the primitive.
 *
 * WHAT THIS IS. The game's evolution limits are all enforced in CODE, not
 * data: six paths per source (int8 to[6]), evolution-graph ids capped at 127,
 * a 62-record table with no row for types 63+, and only four In-Training
 * sources with sixteen free slots between them. No amount of table relocation
 * removes any of those. Replacing the chooser functions with native C does.
 *
 * HOW IT WORKS, AND WHY NOT THE OBVIOUS WAY. The obvious approach -- patch the
 * prologue to "jr $ra" and set $v0 from an entry hook -- cannot work, for three
 * independent reasons. Writing guest code marks the range executable-dirty;
 * dispatch compares the function's emitted range against the pristine text
 * image, refuses the compiled body and runs the interpreter instead -- but the
 * hook lives INSIDE the body it just refused, so it never fires. Even with that
 * guard defeated, the compiled body holds the original build-time instructions,
 * so the patch is inert. And the entry hook is emitted as a bare void
 * statement, so any $v0 it writes is clobbered by the prologue moments later.
 *
 * So this uses a CLAIMING hook and patches nothing:
 *
 *     if (psx_mod_function_replace(cpu, ADDR)) return;
 *
 * emitted at the top of the generated body (see game.toml
 * mod_function_replace_funcs). Returning 1 means "I computed the result": the
 * callback writes $v0 and publishes $ra into cpu->pc, and the caller resumes
 * exactly as from a normal return. Leaving the guest image byte-identical is
 * precisely what keeps the function on the native path where the hook exists.
 *
 * THE INVARIANT THAT FOLLOWS: a replaced function must carry no code patch
 * from any mod. dw.vanilla.roster currently puts 42 retarget patches inside
 * four of the five evolution choosers, which is why this step starts with the
 * fifth -- getFreshEvolutionTarget is the only one it leaves alone.
 */
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "dw_evolution.h"
#include "mod_plugins.h"

#define FN_GET_FRESH_EVOLUTION_TARGET 0x800E2544u
#define VA_PARTNER_PARA_EVO_TIMER 0x801384B6u   /* PARTNER_PARA + 0x56 */

static int evo_enabled = 0;

int32_t dw_evo_fresh_target(int32_t current, int32_t fallthrough) {
    int32_t v = fallthrough;
    if (current == 0x01) v = 0x02;
    if (current == 0x0F) v = 0x10;
    if (current == 0x1D) v = 0x1E;
    if (current == 0x2B) v = 0x2C;
    return v;
}

/* Faithful transcription of getFreshEvolutionTarget (evolution.c:44), checked
 * against the disassembly at 0x800E2544:
 *
 *   four compare/assign pairs, then
 *   lui $at,0x8014 ; sll $v0,$v0,16 ; sh $zero,-31562($at) ; jr $ra ;
 *   sra $v0,$v0,16
 *
 * Two details a naive port gets wrong, both deliberate here:
 *
 *  1. $v0 IS NEVER INITIALISED on the fall-through. The decomp even flags it
 *     ("'result' may be uninitialized"). It is not undefined in practice: the
 *     function returns whatever $v0 held on entry, i.e. the caller's leftover.
 *     A plugin can see that register, so the quirk is reproducible exactly --
 *     and it must be reproduced, because it is behaviour twenty-five years of
 *     players have already felt.
 *  2. The sll/sra pair sign-extends the low 16 bits, so the result is
 *     (int16_t) of that value, not the full word.
 *
 * The evoTimer store happens on EVERY call, including the fall-through -- it is
 * the zero point every later evolution threshold is measured from, so skipping
 * it would quietly change when everything else evolves.
 */
unsigned long dw_evo_fresh_calls = 0;   /* observable proof the hook fires */

static int evo_fresh_replace(struct CPUState* cpu, uint32_t address) {
    int32_t current;
    uint32_t v0, in;
    (void)address;
    if (!evo_enabled)
        return 0;                       /* feature off: run the guest body */

    current = (int32_t)cpu->gpr[4];      /* $a0 */
    in = cpu->gpr[2];
    /* fall-through returns incoming $v0 */
    v0 = (uint32_t)dw_evo_fresh_target(current, (int32_t)in);

    psx_mod_write_half(VA_PARTNER_PARA_EVO_TIMER, 0);

    cpu->gpr[2] = (uint32_t)(int32_t)(int16_t)(v0 & 0xFFFFu);
    cpu->pc = cpu->gpr[31];              /* publish $ra; caller resumes */

    dw_evo_fresh_calls++;
    printf("dw_evolution: getFresh(a0=%d) v0_in=0x%08X -> %d  [call %lu]\n",
           (int)current, in, (int)(int16_t)(v0 & 0xFFFFu), dw_evo_fresh_calls);
    fflush(stdout);
    return 1;
}

/* ---- The chooser, read-only (used by the Evolution Guide) ------------------
 *
 * A transcription of the dispatch in tickConditions (partner_impl.c:2106-2122)
 * and of getInTrainingEvolutionTarget, getRookieEvolutionTarget,
 * getChampionEvolutionTarget and calculateRequirementScore (evolution.c:690-
 * 1036), checked against the disassembly. It keeps the quirks: the raised
 * rule compares against the best SO FAR, Rookie and Champion reset the stat
 * total and count only when a candidate wins (GAME.md 12.2), and a qualifier
 * with no stat requirement divides by zero, which on the R3000 (no break is
 * emitted here) leaves LO = -1 for a non-negative total.
 *
 * Tables are found where the chooser looks for them: each base is decoded
 * from a lui/addiu pair inside the chooser, so the Expanded Roster's arena
 * copies, Evolution Unbound's walls and any other rewrite are what it reads. */
#define SITE_REQ   0x800E26E4u   /* calculateRequirementScore: EVOLUTION_REQS */
#define SITE_GAINS 0x800E2B18u   /* ...: EVOLUTION_STATS_GAINS (raised rule) */
#define SITE_DDATA 0x800E27A4u   /* ...: DIGIMON_DATA[target].level (+0x1D) */
#define SITE_PATHS 0x800E2F40u   /* getChampionEvolutionTarget: EVOLUTION_PATHS */
#define DDATA_LEVEL 0x1Du
#define DDATA_STRIDE 0x34u
#define REQ_STRIDE 0x1Cu
#define GAINS_STRIDE 0x0Eu
#define GAINS_TARGET 0x0Cu
#define PATH_STRIDE 0x0Bu
#define PATH_TO 5u
/* The item-drop roll's lui of DIGIMON_DATA: 0x8013 in the stock executable,
 * 0x801F in Digimon World 1.5's, whose chooser is its own (cheats.txt). */
#define GATE_EXE 0x800ED2A0u
#define GATE_STOCK 0x8013u

#define PARTNER_TYPE 0x801557A8u   /* PARTNER_ENTITY.type */
#define PARTNER_BASE 0x801557E0u   /* Stats.base: off, def, speed, brain, +0x10 hp, +0x12 mp */
#define PARTNER_MOVES 0x80155800u  /* learnedMoves[2] */
#define PARA 0x80138460u
#define PARA_DISC 0x28u
#define PARA_HAPPY 0x2Au
#define PARA_WEIGHT 0x42u
#define PARA_CARE 0x52u
#define PARA_BATTLES 0x54u
#define PARA_TIMER 0x56u
#define NUMEMON 11                 /* getRookieEvolutionTarget's fallback */

static int16_t rd16(uint32_t a) { return (int16_t)psx_mod_read_half(a); }

static int table_addr_ok(uint32_t a) {
    return (a >= 0x80000000u && a < 0x80200000u) ||
           (a >= 0x9F000000u && a < 0x9F100000u);   /* Expansion-1 arena */
}

/* Decode `lui rt,hi` at site and the first following addiu/load off rt
 * within four instructions. 0 when the code is not that shape. */
static uint32_t decode_base(uint32_t site) {
    const uint32_t lui = psx_mod_read_word(site);
    uint32_t rt, i, w, op, base;
    if ((lui >> 26) != 0x0Fu)
        return 0;
    rt = (lui >> 16) & 31u;
    for (i = 1; i <= 4; i++) {
        w = psx_mod_read_word(site + i * 4u);
        op = w >> 26;
        if (((w >> 21) & 31u) == rt && (op == 0x09u || (op >= 0x20u && op <= 0x26u))) {
            base = ((lui & 0xFFFFu) << 16) + (uint32_t)(int32_t)(int16_t)(w & 0xFFFFu);
            return table_addr_ok(base) ? base : 0;
        }
    }
    return 0;
}

typedef struct {
    uint32_t req, gains, ddata, paths;
} EvoTables;

static void read_name(uint32_t ddata, int32_t t, char out[21]) {
    int i;
    for (i = 0; i < 20; i++) {
        uint8_t ch = psx_mod_read_byte(ddata + (uint32_t)t * DDATA_STRIDE + (uint32_t)i);
        out[i] = (ch >= 0x20 && ch < 0x7F) ? (char)ch : (ch ? '?' : 0);
        if (!ch) break;
    }
    out[20] = 0;
}

/* calculateRequirementScore (evolution.c:928-1036). */
static int8_t score_candidate(const EvoTables* tb, const DwEvoModel* m,
                              int32_t current, int8_t target, int32_t best,
                              DwEvoCandidate* c) {
    const uint32_t r = tb->req + (uint32_t)(int32_t)target * REQ_STRIDE;
    const int16_t* st = m->stats;
    int i, j;
    int8_t pts = 0, bonus = 0;

    c->target = target;
    read_name(tb->ddata, target, c->name);
    c->level = psx_mod_read_byte(tb->ddata + (uint32_t)(int32_t)target * DDATA_STRIDE + DDATA_LEVEL);
    for (i = 0; i < DW_REQ_COUNT; i++)
        c->req[i] = rd16(r + (uint32_t)i * 2u);
    c->flags = psx_mod_read_byte(r + 26u);

    if ((c->flags & 0x10) == 0)
        c->care_met = m->care >= c->req[DW_REQ_CARE];
    else
        c->care_met = m->care <= c->req[DW_REQ_CARE];
    c->weight_met = c->req[DW_REQ_WEIGHT] - 5 <= m->weight &&
                    m->weight <= c->req[DW_REQ_WEIGHT] + 5;

    c->highest = -1;
    if (c->level == 3) {
        int hi = 0;
        for (i = 0; i < 6; i++) {
            int is_hi = 1;
            for (j = 0; j < 6; j++)
                if (st[i] < st[j]) is_hi = 0;
            if (is_hi) hi = i;
        }
        c->highest = (int8_t)hi;
        c->stats_met = c->req[DW_REQ_HP + hi] == 1;
        for (i = 0; i < 6; i++)
            c->stat_met[i] = i == hi && c->stats_met;
    } else {
        c->stats_met = 1;
        for (i = 0; i < 6; i++) {
            c->stat_met[i] = st[i] >= c->req[DW_REQ_HP + i];
            c->stats_met &= c->stat_met[i];
        }
    }

    c->bonus_present = c->bonus_hit = 0;
    if (c->req[DW_REQ_FROM] != -1) {
        if (c->req[DW_REQ_FROM] > 0 && c->req[DW_REQ_FROM] < 180)
            read_name(tb->ddata, c->req[DW_REQ_FROM], c->from_name);
        c->bonus_present |= DW_BONUS_FROM;
        if (current == c->req[DW_REQ_FROM]) c->bonus_hit |= DW_BONUS_FROM;
    }
    if (c->req[DW_REQ_DISC] != -1) {
        c->bonus_present |= DW_BONUS_DISC;
        if (m->discipline >= c->req[DW_REQ_DISC]) c->bonus_hit |= DW_BONUS_DISC;
    }
    if (c->req[DW_REQ_HAPPY] != -1) {
        c->bonus_present |= DW_BONUS_HAPPY;
        if (m->happiness >= c->req[DW_REQ_HAPPY]) c->bonus_hit |= DW_BONUS_HAPPY;
    }
    if (c->req[DW_REQ_BATTLES] != -1) {
        c->bonus_present |= DW_BONUS_BATTLES;
        if ((c->flags & 1) == 0 ? m->battles >= c->req[DW_REQ_BATTLES]
                                : m->battles <= c->req[DW_REQ_BATTLES])
            c->bonus_hit |= DW_BONUS_BATTLES;
    }
    if (c->req[DW_REQ_TECHS] != -1) {
        c->bonus_present |= DW_BONUS_TECHS;
        if (m->techs >= c->req[DW_REQ_TECHS]) c->bonus_hit |= DW_BONUS_TECHS;
    }
    bonus = c->bonus_hit ? 1 : 0;
    c->bonus_met = (uint8_t)bonus;

    pts = (int8_t)(c->care_met + c->weight_met + c->stats_met + bonus);
    c->points = pts;
    c->raised_adj = 0;
    if (pts >= 3 && best != -1) {
        const int t_raised = dw_species_raised((uint32_t)(int32_t)rd16(
            tb->gains + (uint32_t)(int32_t)target * GAINS_STRIDE + GAINS_TARGET));
        const int b_raised = dw_species_raised((uint32_t)(int32_t)rd16(
            tb->gains + (uint32_t)best * GAINS_STRIDE + GAINS_TARGET));
        if (t_raised == 1 && b_raised == 0) { pts = 0; c->raised_adj = -1; }
        if (t_raised == 0 && b_raised == 1) { pts++; c->raised_adj = 1; }
    }
    c->score = pts;
    c->qualifies = pts >= 3;
    c->mean = -1;
    return pts;
}

int dw_evo_model(DwEvoModel* m) {
    EvoTables tb;
    uint32_t moves[2];
    int8_t to[DW_EVO_SLOTS];
    int i, b;
    int32_t best = -1;
    int16_t best_score = 0, total = 0, count = 0;

    memset(m, 0, sizeof *m);
    m->pick = -1;
    if (psx_mod_read_half(GATE_EXE) != GATE_STOCK)
        return 0;
    tb.req = decode_base(SITE_REQ);
    tb.gains = decode_base(SITE_GAINS);
    tb.ddata = decode_base(SITE_DDATA);
    tb.paths = decode_base(SITE_PATHS);
    if (!tb.req || !tb.gains || !tb.ddata || !tb.paths)
        return 0;
    tb.ddata -= DDATA_LEVEL;
    m->ok = 1;

    m->partner = (int32_t)psx_mod_read_word(PARTNER_TYPE);
    if (m->partner <= 0 || m->partner >= 180) {
        m->ok = 0;
        return 0;
    }
    read_name(tb.ddata, m->partner, m->name);
    m->level = psx_mod_read_byte(tb.ddata + (uint32_t)m->partner * DDATA_STRIDE + DDATA_LEVEL);
    m->timer = rd16(PARA + PARA_TIMER);
    m->threshold = m->level == 1 ? 6 : m->level == 2 ? 24 : m->level == 3 ? 72
                 : m->level == 4 ? 144 : 0;
    m->stats[0] = (int16_t)(rd16(PARTNER_BASE + 0x10) / 10);
    m->stats[1] = (int16_t)(rd16(PARTNER_BASE + 0x12) / 10);
    for (i = 0; i < 4; i++)
        m->stats[2 + i] = rd16(PARTNER_BASE + (uint32_t)i * 2u);
    m->care = rd16(PARA + PARA_CARE);
    m->weight = rd16(PARA + PARA_WEIGHT);
    m->discipline = rd16(PARA + PARA_DISC);
    m->happiness = rd16(PARA + PARA_HAPPY);
    m->battles = rd16(PARA + PARA_BATTLES);
    moves[0] = psx_mod_read_word(PARTNER_MOVES);
    moves[1] = psx_mod_read_word(PARTNER_MOVES + 4);
    for (i = 0; i < 2; i++)
        for (b = 0; b < 32; b++)
            if (moves[i] & (1u << b)) m->techs++;

    for (i = 0; i < DW_EVO_SLOTS; i++)
        to[i] = (int8_t)psx_mod_read_byte(tb.paths + (uint32_t)(m->partner - 1) * PATH_STRIDE + PATH_TO + (uint32_t)i);

    m->pick_defined = 1;
    if (m->level == 1) {
        /* No score: list the path, pick what getFresh returns. */
        for (i = 0; i < DW_EVO_SLOTS; i++) {
            if (to[i] == -1) continue;
            DwEvoCandidate* c = &m->c[m->n++];
            c->target = to[i];
            read_name(tb.ddata, to[i], c->name);
            c->level = psx_mod_read_byte(tb.ddata + (uint32_t)(int32_t)to[i] * DDATA_STRIDE + DDATA_LEVEL);
            c->mean = -1;
        }
        m->pick = (int16_t)dw_evo_fresh_target(m->partner, -1);
        m->pick_defined = m->pick != -1;
        if (m->pick > 0)
            read_name(tb.ddata, m->pick, m->pick_name);
        return 1;
    }
    if (m->level < 2 || m->level > 4)
        return 1;                            /* no chooser for this level */

    for (i = 0; i < DW_EVO_SLOTS; i++) {
        DwEvoCandidate* c;
        int8_t s;
        if (to[i] == -1) continue;
        c = &m->c[m->n++];
        s = score_candidate(&tb, m, m->partner, to[i], best, c);
        if (m->level == 2) {                 /* getInTrainingEvolutionTarget */
            if ((int8_t)best_score < s && s >= 3) {
                best = to[i];
                best_score = s;
            }
            continue;
        }
        if (s < 3) continue;                 /* Rookie / Champion */
        for (b = 0; b < 6; b++)
            if (c->req[DW_REQ_HP + b] != -1) {
                total = (int16_t)(total + m->stats[b]);
                count++;
            }
        total = count ? (int16_t)(total / count) : (int16_t)(total >= 0 ? -1 : 1);
        c->mean = total;
        if (best_score < total) {
            best = to[i];
            best_score = total;
            total = count = 0;               /* only on a win: the leak */
        }
    }
    if (m->level == 3 && best == -1 && m->timer >= 96)
        best = NUMEMON;
    m->pick = (int16_t)best;
    if (best > 0)
        read_name(tb.ddata, best, m->pick_name);
    return 1;
}

static void evo_activate(void) {
    evo_enabled = 1;
    printf("dw_evolution: native chooser active (getFreshEvolutionTarget)\n");
    fflush(stdout);
}

PSX_MOD_CONSTRUCTOR(dw_evolution_register) {
    psx_mod_register_activation_plugin("dw.evolution", evo_activate);
    psx_mod_register_function_replace_plugin(
        "dw.evolution", FN_GET_FRESH_EVOLUTION_TARGET, evo_fresh_replace);
}
