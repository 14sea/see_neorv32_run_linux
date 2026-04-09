// SPDX-License-Identifier: GPL-2.0+
#ifndef STAGE2_SD_H
#define STAGE2_SD_H

#include <stdint.h>

int  sd_init(void);
int  sd_read_block(uint32_t lba, uint8_t *dst);
int  sd_write_block(uint32_t lba, const uint8_t *src);
int  sd_is_sdhc(void);
void sd_smoke(void);
void sd_dump(uint32_t n_blocks);
void sd_write_test(void);
void sd_write_multi(void);
int  sd_read_many(uint32_t lba, uint32_t n_blocks, uint8_t *dst);

#endif
