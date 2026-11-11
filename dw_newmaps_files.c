#include <string.h>

#include "dw_newmaps_files.h"
#include "mod_plugins.h"

/* Rows are { "NAME", lba, size }, generated; the sentinel ends the table (and keeps it
 * non-empty while the .inc is). */
static const DwNewmapsFile k_newmaps_files[] = {
#include "dw_newmaps_files.inc"
    { 0, 0, 0 }
};

int dw_newmaps_file(const char* name, uint32_t* lba, uint32_t* size) {
    for (const DwNewmapsFile* f = k_newmaps_files; f->name; f++) {
        if (!strcmp(name, f->name)) {
            *lba = f->lba;
            *size = f->size;
            return 1;
        }
    }
    return 0;
}

const char* dw_newmaps_basename(uint32_t addr, char out[65]) {
    uint32_t n = 0, start = 0;
    for (; n < 64; n++) {
        const uint8_t c = psx_mod_read_byte(addr + n);
        out[n] = (char)c;
        if (!c)
            return out + start;
        if (c == 0x5C)    /* backslash */
            start = n + 1;
    }
    out[n] = 0;
    return out + start;
}

uint32_t dw_newmaps_cdloc(uint32_t lba) {
    const uint32_t l = lba + 150, m = l / 4500, s = (l / 75) % 60, f = l % 75;
#define BCD(v) (((v) / 10) << 4 | (v) % 10)
    return BCD(m) | BCD(s) << 8 | BCD(f) << 16;
#undef BCD
}
