/* dw.newmaps: new map files live in the unused MMM.DAT area under names the game's own
 * file table does not hold. dw_newmaps_files.inc lists them; tools/gen_factory.py writes it. */
#ifndef DW_NEWMAPS_FILES_H
#define DW_NEWMAPS_FILES_H
#include <stdint.h>

typedef struct { const char* name; uint32_t lba; uint32_t size; } DwNewmapsFile;

/* 1 when name (the part after the last '\', case-sensitive as the game compares) is in the
 * table; lba is the disc sector of its first byte (CdIntToPos adds the 150-sector lead-in). */
int dw_newmaps_file(const char* name, uint32_t* lba, uint32_t* size);

/* The part of the guest string at addr after its last '\' (NUL-terminated, 64 chars at most). */
const char* dw_newmaps_basename(uint32_t addr, char out[65]);

/* A CdlLOC (BCD minute, second, sector, track 0) as the word CdIntToPos writes. */
uint32_t dw_newmaps_cdloc(uint32_t lba);
#endif
