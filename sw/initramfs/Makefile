# Build minimal initramfs for NEORV32 nommu Linux
# Output: neo_initramfs.cpio.gz (contains /init + /dev/console)
#
# gen_init_cpio is built from kernel source (usr/gen_init_cpio.c)
# to create device nodes without root/fakeroot.

# init runs as a Linux userspace binary — must use a Linux toolchain (supports PIE).
# The bare-metal riscv-none-elf- toolchain does NOT support PIE.
RISCV_LINUX_PREFIX ?= /home/test/buildroot/output/host/bin/riscv32-buildroot-linux-gnu-
CC = $(RISCV_LINUX_PREFIX)gcc
STRIP = $(RISCV_LINUX_PREFIX)strip
HOSTCC = gcc

# Path to extracted Linux source (set by caller or default)
LINUX_DIR ?= ../../linux-6.6.83

CFLAGS = -nostdlib -nostartfiles -fpie -mcmodel=medany \
         -fno-plt -static -Wl,-pie -Wl,--no-dynamic-linker \
         -Os -fno-stack-protector -fno-builtin \
         -march=rv32imac_zicsr_zifencei -mabi=ilp32

all: neo_initramfs.cpio.gz

init: init.c
	$(CC) $(CFLAGS) -o $@ $< -e _start
	$(STRIP) $@

gen_init_cpio: $(LINUX_DIR)/usr/gen_init_cpio.c
	$(HOSTCC) -o $@ $<

neo_initramfs.cpio.gz: init gen_init_cpio
	@echo "dir  /dev      0755 0 0"       >  filelist.txt
	@echo "nod  /dev/console 0600 0 0 c 5 1" >> filelist.txt
	@echo "file /init     init 0755 0 0"  >> filelist.txt
	./gen_init_cpio filelist.txt | gzip -9 > $@
	@rm -f filelist.txt
	@echo "Built $@ ($$(wc -c < $@) bytes)"

clean:
	rm -rf init gen_init_cpio filelist.txt neo_initramfs.cpio.gz

.PHONY: all clean
