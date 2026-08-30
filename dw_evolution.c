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

#include "cpu_state.h"
#include "mod_plugins.h"

#define FN_GET_FRESH_EVOLUTION_TARGET 0x800E2544u
#define VA_PARTNER_PARA_EVO_TIMER 0x801384B6u   /* PARTNER_PARA + 0x56 */

static int evo_enabled = 0;

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
    v0 = in;                             /* fall-through returns incoming $v0 */
    if (current == 0x01) v0 = 0x02;
    if (current == 0x0F) v0 = 0x10;
    if (current == 0x1D) v0 = 0x1E;
    if (current == 0x2B) v0 = 0x2C;

    psx_mod_write_half(VA_PARTNER_PARA_EVO_TIMER, 0);

    cpu->gpr[2] = (uint32_t)(int32_t)(int16_t)(v0 & 0xFFFFu);
    cpu->pc = cpu->gpr[31];              /* publish $ra; caller resumes */

    dw_evo_fresh_calls++;
    printf("dw_evolution: getFresh(a0=%d) v0_in=0x%08X -> %d  [call %lu]\n",
           (int)current, in, (int)(int16_t)(v0 & 0xFFFFu), dw_evo_fresh_calls);
    fflush(stdout);
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
