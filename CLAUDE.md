# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project boots nommu Linux (kernel 6.6.83) on a NEORV32 RV32IMC soft-core FPGA — the first known Linux boot on NEORV32. The NEORV32 has no MMU, no S-mode, and no atomic instructions. Getting Linux running required 22 kernel patches across arch/riscv, scheduler, RCU, init, and drivers.

Target hardware: Heijin AX301 board with Altera Cyclone IV EP4CE6 FPGA, 32 MB SDRAM, 50 MHz.

## Hardware

**Board:** AX301 (Cyclone IV EP4CE6F17C8)
**Programmer:** USB-Blaster (`09fb:6001`), attached to WSL2 via `usbipd`
**UART:** PL2303 at `/dev/ttyUSB0`
**Key peripherals:** 32 MB SDRAM (HY57V2562GTR), SPI Flash (M25P16)

## Repository Structure

```
see_neorv32_run_linux/
├── tools/openFPGALoader/    — openFPGALoader source (build from source)
├── neorv32/                 — NEORV32 RTL source (v1.12.8)
├── linux-6.6.83.tar.xz     — Linux kernel tarball
├── rtl/                     — Custom RTL (ax301_top.vhd, sdram_ctrl.v, wb_sdram_ctrl.v)
├── quartus/                 — Quartus project (neorv32_demo.qsf/qpf/sdc)
├── kernel/                  — neorv32_nommu.patch (22 kernel patches)
├── board/                   — DTS, defconfig, UART driver, inject_driver.sh
├── sw/stage2_loader/        — Stage2 xmodem loader (C, must fit 8 KB)
├── sw/initramfs/            — Minimal init (C, builds neo_initramfs.cpio.gz)
├── host/                    — boot_linux.py, test_shell.py
└── output/                  — Build outputs go here (initially empty)
```

## Complete Build-from-Source Flow

All source code is included. Build order matters — later steps depend on earlier outputs.

### Prerequisites

- Intel Quartus Prime Lite 21.1+ (`~/intelFPGA_lite/21.1/quartus/bin` in PATH)
- xPack RISC-V GCC 14.2.0 (`/home/test/xpack-riscv-none-elf-gcc-14.2.0-3/bin/`) — for kernel and stage2. **MUST use this specific toolchain** — see "Compiler constraint" below.
- Buildroot Linux GCC (`/home/test/buildroot/output/host/bin/riscv32-buildroot-linux-gnu-`) — for initramfs init ONLY (needs PIE support). Do NOT use this for the kernel.
- CMake, libftdi1-dev, libusb-1.0-0-dev (for openFPGALoader)
- Device tree compiler: `dtc`
- Python 3 with `pyserial`

**Two toolchains are required:** The bare-metal `riscv-none-elf-` toolchain cannot produce PIE executables. The initramfs `/init` is a Linux userspace binary that must be built as static-PIE with the Buildroot Linux toolchain. Do NOT substitute one for the other.

**Compiler constraint (critical):** The kernel MUST be built with xPack `riscv-none-elf-gcc` 14.2.0. Building with Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 produces a kernel that hangs in `free_initmem()` — identical source, patches, and .config, but GCC 12.4.0 generates machine code that deadlocks on NEORV32. Symptoms: debug marker `L` prints (system_state = RUNNING), but `M` (after free_initmem) never appears; the Buildroot-built kernel also runs ~2x slower and triggers `sched: RT throttling activated`.

### Step 1: Build openFPGALoader

The system-installed `openfpgaloader` (v0.12.0) does **NOT** recognise EP4CE6. Must build from source.

```bash
cd tools/openFPGALoader
mkdir build && cd build
cmake ..
make -j$(nproc)
# Binary: tools/openFPGALoader/build/openFPGALoader
```

### Step 2: Build FPGA bitstream

The Quartus project references NEORV32 RTL at `../neorv32/` (relative to `quartus/`).

```bash
export PATH=$PATH:$HOME/intelFPGA_lite/21.1/quartus/bin

cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off output_files/neorv32_demo.sof ../output/neorv32_demo.rbf
```

### Step 3: Build stage2 loader

```bash
cd sw/stage2_loader
make NEORV32_HOME=../../neorv32 exe
cp neorv32_exe.bin ../../output/stage2_loader.bin
```

**CRITICAL:** The bootloader expects `neorv32_exe.bin` format (with NEORV32 header), NOT raw `main.bin`. The `exe` target in common.mk produces this. Stage2 must fit in **8 KB** IMEM.

### Step 4: Build Linux kernel

```bash
# Extract kernel source (at repo root)
tar xf linux-6.6.83.tar.xz

# Apply nommu patches (22 patches)
cd linux-6.6.83
patch -p1 < ../kernel/neorv32_nommu.patch

# Inject NEORV32 UART driver into kernel tree
../board/inject_driver.sh .

# Build initramfs first (kernel embeds it)
cd ../sw/initramfs
make LINUX_DIR=../../linux-6.6.83
cp neo_initramfs.cpio.gz ../../output/

# Update defconfig to point to the initramfs
cd ../../
sed "s|CONFIG_INITRAMFS_SOURCE=.*|CONFIG_INITRAMFS_SOURCE=\"$(pwd)/output/neo_initramfs.cpio.gz\"|" \
    board/linux_defconfig > linux-6.6.83/arch/riscv/configs/neorv32_ax301_defconfig

# Build kernel
export PATH=$PATH:/home/test/xpack-riscv-none-elf-gcc-14.2.0-3/bin
cd linux-6.6.83
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)
cp arch/riscv/boot/Image ../output/
```

### Step 5: Compile device tree

```bash
dtc -I dts -O dtb -o output/neorv32_ax301.dtb board/neorv32_ax301.dts
```

### Step 6: Program FPGA and boot Linux

```bash
# Program FPGA
tools/openFPGALoader/build/openFPGALoader -c usb-blaster output/neorv32_demo.rbf

# Boot Linux (handles bootloader, stage2, xmodem transfer, console)
python3 host/boot_linux.py --port /dev/ttyUSB0 --skip-program
```

Or in one shot (boot_linux.py programs FPGA too):
```bash
python3 host/boot_linux.py --port /dev/ttyUSB0
```

**Note:** `boot_linux.py` looks for openFPGALoader at `tools/openFPGALoader/build/openFPGALoader`. If you built it elsewhere, either move the binary or use `--skip-program` and program manually.

### Expected output

After ~139s of xmodem transfer + ~118s of kernel boot = ~257s total:
```
========================================
 NEORV32 nommu Linux — mini shell
========================================
Linux (none) 6.6.83-... riscv32
Uptime:    118 s
Total RAM: 31068 KB
Free RAM:  30548 KB
Processes: 13

Type 'help' for commands.

nommu#
```

### Test shell commands on running system
```bash
python3 host/test_shell.py /dev/ttyUSB0
```

## Architecture

### Boot sequence (4 stages)
1. **NEORV32 bootloader** (ROM at 0xFFE00000, 19200 baud) — uploads stage2_loader.bin
2. **Stage2 loader** (IMEM, 115200 baud) — receives 'l' for Linux mode, then xmodem-transfers kernel+DTB+initramfs to SDRAM with CRC-32 verification
3. **Kernel jump** — stage2 jumps to 0x40000000 with a0=hartid, a1=DTB pointer
4. **Linux console** — ~118s boot to mini shell (custom /init, not busybox)

### Memory map
- `0x00000000` — 8 KB IMEM (stage2 loader, M9K BRAM)
- `0x40000000` — 32 MB SDRAM (kernel at base, DTB at +0x1F00000, initramfs at +0x1F80000)
- `0x80000000` — 8 KB DMEM (M9K BRAM)
- `0xFFF40000` — CLINT (timer), `0xFFF50000` — UART0

### Key technical decisions
- **All atomics replaced with IRQ-disable/load/modify/store** — no LR/SC or AMO exists on this core. See `cmpxchg.h`, `atomic.h`, `bitops.h` in the kernel patch.
- **Scheduler modified to single-shot `__schedule()`** — prevents infinite `need_resched` loops caused by non-atomic `test_and_clear` racing with timer interrupts. Safe because single-core.
- **`wfi` replaced with `nop`** — NEORV32's wfi halts permanently if no interrupt pending.
- **RISCV_ALTERNATIVE disabled** — runtime instruction patching conflicts with non-atomic replacements; causes illegal instruction trap after `free_initmem()`.
- **UART driver uses kthread polling** — no IRQ, no work queues (unreliable with modified scheduler). Direct line discipline delivery.
- **RCU/async made synchronous** — `srcutiny` grace periods forced synchronous; `populate_rootfs()` called directly; `async_synchronize_full()` has 120s timeout.

### Cross-compiler toolchain
Path: `/home/test/xpack-riscv-none-elf-gcc-14.2.0-3/bin/riscv-none-elf-`
The stage2 Makefile has this hardcoded; override with `RISCV_PREFIX`. Kernel uses `riscv-none-elf-` as `CROSS_COMPILE` (must be in PATH).

## Important Constraints
- Stage2 loader **must fit in 8 KB** IMEM (set via linker flags in Makefile)
- The FPGA has only 6,272 LEs — no room for additional peripherals
- SDRAM can intermittently fail on first power-on; power-cycle to resolve
- The DTS advertises `riscv,isa-extensions = "a"` even though hardware has no atomics — this is intentional to satisfy kernel DT parsing requirements

## Known Pitfalls

1. **openFPGALoader version**: System `openfpgaloader` v0.12.0 does NOT support EP4CE6. Must use locally built version from `tools/openFPGALoader/build/`.
2. **USB-Blaster in WSL2**: Must attach via `usbipd attach --wsl --busid 2-9` from Windows PowerShell (admin). Verify with `lsusb | grep 09fb`.
3. **PL2303 UART stale bytes**: After JTAG programming, the PL2303 buffer may have glitch bytes. `boot_linux.py` handles this automatically.
4. **SDRAM intermittent failure**: If Linux fails to boot or shows memory errors, power-cycle the board (not just reprogram). This is a known hardware issue.
5. **Bootloader baud**: NEORV32 internal bootloader runs at **19200** baud. Stage2 and Linux run at **115200** baud. `boot_linux.py` handles the switch.
6. **Kernel CONFIG_INITRAMFS_SOURCE path**: The `board/linux_defconfig` has a hardcoded path for initramfs. The build steps above fix it with `sed`. If the kernel builds but Linux panics with "No init found", check this path.
7. **boot_linux.py openFPGALoader path**: The script looks for `tools/openFPGALoader/build/openFPGALoader` relative to the repo root. If not found, use `--skip-program` and program the FPGA manually.
8. **initramfs /init must be built with Linux toolchain as static-PIE**: The bare-metal `riscv-none-elf-gcc` does NOT support `-fpie`. Use the Buildroot Linux toolchain (`riscv32-buildroot-linux-gnu-gcc`). If init is built without PIE, the kernel will hang after "Run /init as init process" with no error message.
9. **CONFIG_RISCV_ISA_V, CONFIG_FPU, CONFIG_RISCV_ISA_FALLBACK must be disabled**: The defconfig explicitly disables them. NEORV32 has no FPU — `CONFIG_FPU=y` causes the kernel to hang after `free_initmem()` due to illegal instruction traps. Always verify with `grep -E 'RISCV_ISA_V|FPU|ISA_FALLBACK' .config` after `make defconfig`.
10. **Kernel size must be close to 1,451,452 bytes**: If the Image is significantly larger (>1.48 MB), unwanted features got auto-enabled. Check the config items in pitfall #9.
11. **Kernel compiler: xPack riscv-none-elf-gcc 14.2.0 only**: Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 compiles the kernel without errors, but the resulting Image hangs at `free_initmem()` on NEORV32 hardware. Both compilers produce identical instruction-level kernel_init flow, and both leave exactly 8 AMO/LR/SC instructions in the binary (in unused futex/fallback paths). The hang is caused by a subtle code generation difference in GCC 12.4.0. Do NOT substitute compilers for the kernel build.
