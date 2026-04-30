# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project boots nommu Linux (kernel 6.6.83) on a NEORV32 RV32IMAC soft-core FPGA — the first known Linux boot on NEORV32. The NEORV32 has no MMU and no S-mode. Getting Linux running required 16 kernel patches across arch/riscv, scheduler, RCU, init, and drivers. We also found and fixed a [SC.W return value bug](https://github.com/stnolting/neorv32/pull/1520) in NEORV32's bus reservation station, enabling native atomic instructions in the kernel; the fix is now merged upstream and included in v1.12.9.

The `neorv32` submodule tracks `origin/main` HEAD (currently `70393ec4`, v1.13.0.1, +24 commits past the `#1540` merge) plus one local RTL patch in `neorv32_patches/` (`0004-rvs-address-tracking.patch`). The two D-cache correctness fixes (formerly `0001` + `0002`) are now upstream as part of [PR #1540](https://github.com/stnolting/neorv32/pull/1540). See `neorv32_patches/README.md` for the full inventory and rationale.

**D-cache is disabled** (`DCACHE_EN => false` in `rtl/ax301_top.vhd`). The new write-back D-cache architecture (PR #1513) requires burst-capable memory to be a net win; against the simple non-burst SDRAM controller in this project it is a net loss and triggers a `ktime_get_coarse_real_ts64` seqcount-retry livelock on hot kernel paths. With D-cache off the kernel boots cleanly to `nommu#` in ~20 s wall time (6× faster than the v1.12.9 baseline of ~118 s) thanks to a 3-phase SDRAM controller rewrite (200 µs init spec fix, pipelined back-to-back READ with 1-NOP gap, and open-row tracking that skips ACT for same-row hits).

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
├── neorv32/                 — NEORV32 RTL source (git submodule → stnolting/neorv32, post-v1.12.9 origin/main)
├── linux-6.6.83.tar.xz     — Linux kernel tarball
├── rtl/                     — Custom RTL (ax301_top.vhd, sdram_ctrl.v, wb_sdram_ctrl.v)
├── quartus/                 — Quartus project (neorv32_demo.qsf/qpf/sdc)
├── kernel/                  — neorv32_nommu.patch (16 kernel patches)
├── board/                   — DTS, defconfig, UART driver, inject_driver.sh
├── sw/stage2_loader/        — Stage2 xmodem loader (C, must fit 8 KB)
├── sw/initramfs/            — Minimal init (C, builds neo_initramfs.cpio.gz)
├── host/                    — boot_linux.py, test_shell.py
├── sim/                     — iverilog testbench for sdram_ctrl (`make` to run)
└── output/                  — Build outputs go here (initially empty)
```

## Complete Build-from-Source Flow

All source code is included. Build order matters — later steps depend on earlier outputs.

**Submodule:** `neorv32/` is a git submodule pointing to `stnolting/neorv32` at `origin/main` HEAD (`70393ec4`, v1.13.0.1). After cloning this repo, run `git submodule update --init --recursive` and then apply the patches in `neorv32_patches/` before building:

```bash
git submodule update --init --recursive
cd neorv32
for p in ../neorv32_patches/*.patch; do git apply "$p"; done
cd ..
```

### Prerequisites

- Intel Quartus Prime Lite 21.1+ (`~/intelFPGA_lite/21.1/quartus/bin` in PATH)
- xPack RISC-V GCC 14.2.0 (`riscv-none-elf-gcc`) — for kernel and stage2. **MUST use this specific version** — see "Compiler constraint" below. Install from https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack and put the `bin/` directory on `$PATH`.
- A glibc/musl RISC-V Linux toolchain (`riscv32-buildroot-linux-gnu-gcc` or similar) — for initramfs init ONLY (needs static-PIE support; the bare-metal `riscv-none-elf-` toolchain cannot produce PIE executables). Buildroot is the easiest source; its `output/host/bin/` prefix works as-is. Override with `make RISCV_LINUX_PREFIX=...` when building `sw/initramfs/`.
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
quartus_cpf -c -o bitstream_compression=off neorv32_demo.sof ../output/neorv32_demo.rbf
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

# Apply nommu patches (19 files modified)
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

# Build kernel — ensure xPack riscv-none-elf-gcc 14.2.0 is on $PATH first:
#   export PATH=$PATH:/path/to/xpack-riscv-none-elf-gcc-14.2.0-3/bin
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

After ~145s of xmodem transfer + ~20s of kernel boot = ~165s total:
```
========================================
 NEORV32 nommu Linux — mini shell
========================================
Linux (none) 6.6.83-... riscv32
Uptime:    31 s
Total RAM: 31000 KB
Free RAM:  30240 KB
Processes: 15

Type 'help' for commands.

nommu#
```

### Test shell commands on running system
```bash
python3 host/test_shell.py /dev/ttyUSB0
```

### Fast boot from SD card (optional path)

The stage2 loader can boot Linux directly from an SD card via NEORV32's hardware SPI, skipping the 145 s xmodem transfer. **Linux still runs from SDRAM** — the SD card is purely read-only bulk storage at boot. No Linux kernel driver is involved.

AX301 SD pins: `PIN_J15=SD_CLK`, `PIN_K16=SD_DI`, `PIN_J16=SD_DO`, `PIN_K15=SD_NCS`. Requires `IO_SPI_EN=true` in `rtl/ax301_top.vhd` (already set).

**One-time** — pack Image + DTB + initramfs into a `NEOLNX`-magic blob (header at LBA 0, each section sector-padded) and stream-write it to SD:
```bash
python3 host/sd_pack.py --port /dev/ttyUSB0    # ~160s one-time
```

**Every boot** — stage2 reads the blob from SD into SDRAM and jumps to the kernel:
```bash
python3 host/boot_sd.py --port /dev/ttyUSB0    # ~150s to shell (saves ~90s vs xmodem)
```

Stage2 UART command dispatch (`sw/stage2_loader/main.c`):
| Cmd | Mode |
|-----|------|
| `l` | xmodem Linux boot (legacy, `boot_linux.py`) |
| `s` | SD smoke: init + read LBA 0 + magic check |
| `d` | SD dump: stream first N blocks to host |
| `w` | SD single-block write test |
| `W` | SD multi-block write with per-block `K` ACK (used by `sd_pack.py`) |
| `b` | SD boot: read blob → load kernel → jump (used by `boot_sd.py`) |

Per-block ACK is required because NEORV32 UART RX FIFO is only 16 B and single-block SD writes take ~2 ms — without flow control the host overruns the FIFO during multi-block writes.

Stage2 size budget is tight: current SD-aware build is **6960 / 8192 B**. Any new stage2 code must stay under the 8 KB IMEM cap.

Re-run `sd_pack.py` only when `output/Image`, `output/neorv32_ax301.dtb`, or `output/neo_initramfs.cpio.gz` change.

**Decoupled kernel / initramfs (current design):** `board/linux_defconfig` sets `CONFIG_INITRAMFS_SOURCE=""` — initramfs is NOT embedded in the Image. `board/neorv32_ax301.dts` declares under `chosen`:
```dts
linux,initrd-start = <0x41F80000>;
linux,initrd-end   = <0xC0DEDEAD>;   /* sentinel, patched by stage2 */
```
At boot, `mode_sd_boot()` in `sw/stage2_loader/main.c` scans the loaded DTB for the 4-byte big-endian sentinel `C0 DE DE AD` and overwrites it with `0x41F80000 + initrd_sz`. The kernel then picks up the initramfs via standard `early_init_dt_check_for_initrd()`. If the sentinel is not found (exactly once), stage2 halts with an error.

**What this unlocks:** iterating on `/init` or userspace apps only requires rebuilding `sw/initramfs/`, re-running `sd_pack.py`, and re-booting. The kernel Image is untouched. Full workflow:
```bash
make -C sw/initramfs LINUX_DIR=../../linux-6.6.83
cp sw/initramfs/neo_initramfs.cpio.gz output/
python3 host/sd_pack.py --port /dev/ttyUSB0
python3 host/boot_sd.py  --port /dev/ttyUSB0
```

If you ever re-add `CONFIG_INITRAMFS_SOURCE=...` to the defconfig, you must also remove the `linux,initrd-*` properties from the DTS (or Linux will try to unpack both and fail).

### Incremental SD updates (`sd_update.py`)

The SD blob uses **fixed LBA slots** defined in `host/sd_layout.py`:

```
LBA 0            header (magic + sizes + LBAs + layout_version)
LBA 1..4000      Image   (reserve 2 MB)
LBA 4001..4008   DTB     (reserve 4 KB)
LBA 4009..8008   initrd  (reserve 2 MB)
```

Fixed LBAs mean `host/sd_update.py` can rewrite only the header + the slot(s) that changed — init-only update is **7 sectors ≈ 10 s total** vs 163 s for a full `sd_pack.py`. The header records `layout_version=1` plus the slot LBAs and max sizes, so updates are self-describing and layout drift is caught.

**Safety checks** in `sd_update.py` (do not remove):
1. **Slot overflow** — refuses to write if any section exceeds its reserved sectors
2. **Layout verification** — reads the on-card header via stage2 mode `R` first, aborts with "run sd_pack.py first" if magic / `layout_version` / LBAs don't match `sd_layout.py`

**New stage2 pieces** (`sw/stage2_loader/`):
- `sd.c:sd_write_multi()` — protocol bumped: host sends `u32 seg_count`, then per segment `{u32 start_lba, u32 sec_count, sec_count*512 B}` with per-block `K` ACK. Backward-incompatible with the old single-count protocol (but host is in this repo, so not an issue).
- `sd.c:sd_read_header()` — mode `R` reads LBA 0 and streams it back wrapped in `RD_READY\n…512 B…\nRD_DONE\n`.
- `main.c` — mode dispatcher is now a loop; non-terminal modes (`R`, `W`, `s`, `w`) return to the loop so the host can chain `R → W` in one UART session. Terminal modes (`l`, `b`, `d`, `u`) still never return.

**Host tools** (`host/`):
- `sd_layout.py` — single source of truth for slot constants. Changing these is a layout bump → all cards must be re-flashed with `sd_pack.py`.
- `sd_proto.py` — shared FPGA-program / bootloader-handshake / stage2-upload / multi-segment-write / read-header helpers. `wait_for()` returns the bytes AFTER the marker so callers don't lose raw payload that arrived early (critical for `read_header`).
- `sd_pack.py` — 4 segments: header, Image, DTB, initrd. Slot overflow checks.
- `sd_update.py` — default writes header + initrd. `--dtb` adds DTB, `--kernel` adds Image, `--no-initrd` skips initrd. Always rewrites header so sizes stay truthful.

**Stage2 size budget** is now **~7832 / 8192 B** (~360 B margin).

### SD workflow speed-ups (Phase 1-6)

Six optimizations layered on top of the baseline SD path. All host tools share `host/sd_proto.py`.

1. **UART baud bump** — stage2 mode `B`: host sends `'B' + u32 baud` at 115200, stage2 acks `BAUD_SWITCH`, reconfigures, then **waits for a host probe byte** at the new baud before replying `BAUD_OK`. The probe sync avoids the PL2303 reopen window (~350 ms) dropping stage2's early TX. Default target = **230400** (50 MHz / (2·109) = 229358, 0.45% error — stable; 460800 has ~1.4% error and is not reliable on PL2303). `sd_proto.setup_session()` auto-falls back to 115200 on any failure (re-init from FPGA program). Effect: **`sd_pack.py` 165 s → 99 s**.

2. **Persistent stage2** — dispatcher's post-command timeout is now effectively infinite (`uart_getc_timeout(3600000)` in a loop). `host/sd_proto.persistent_session()` attaches to an already-running stage2 at the baud it's currently at, skipping FPGA program + bootloader handshake + stage2 upload. All SD host tools accept `--persistent --baud <N>`. Effect: **`sd_update.py` 39 s → 17 s** wall-clock for init-only update.

3. **`boot_sd.py --update`** — one-shot edit/test: fast-baud session → `update_sd()` → drop back to console baud via `maybe_switch_baud` → send `'b'`. Flags: `--update-kernel`, `--update-dtb`, `--update-verify`. Single command for the `/init` iteration loop.

4. **`sd_update.py --verify`** — after the multi-seg write, re-read LBA 0 via mode `R` and compare every header field against what was written. Propagated to `boot_sd.py --update-verify`. Also available from the reusable `update_sd(ser, base, ..., verify=True)` helper.

5. **Parametric `sd_dump.py`** — stage2 mode `d` now reads `u32 lba + u32 count` from UART (cap 4096 sectors = 2 MB), prints `DUMP_READY\n`, streams data, prints `\nDUMP_END\n`, then returns to the dispatcher (no more `while(1)` halt). Host script supports `--lba/--count/--hex/--persistent/--baud`; `-o -` streams to stdout. Chainable with `--persistent`.

6. **`boot_sd.py` build-tag check** — before sending `'b'`, reads header via mode `R` and prints `on-card vs local` sizes per slot (`✓` or `✗ STALE`). Warns with a hint to re-run `--update` or `sd_pack.py`. Skip with `--no-check`. Catches stale SD before the ~150 s boot cycle.

**Key shared helpers** (`host/sd_proto.py`):
- `get_session(..., persistent=False, target_baud=...)` — unified entry for fresh or attached sessions. Handles baud bump + fallback internally.
- `maybe_switch_baud(ser, target)` — in-session up/down switch; uses `ser.baudrate` (not a hardcoded APP_BAUD shortcut) so it works in both directions.
- `wait_for(ser, marker, timeout, label)` — returns bytes AFTER the marker, critical for `read_header` which streams raw payload right after the `RD_READY\n` line.

**Typical dev loop** (post-Phase 3):
```bash
# edit sw/initramfs/init.c, then:
make -C sw/initramfs LINUX_DIR=../../linux-6.6.83
cp sw/initramfs/neo_initramfs.cpio.gz output/
python3 host/boot_sd.py --update         # ~17 s update + boot
```

## Architecture

### Boot sequence (4 stages)
1. **NEORV32 bootloader** (ROM at 0xFFE00000, 19200 baud) — uploads stage2_loader.bin
2. **Stage2 loader** (IMEM, 115200 baud) — receives 'l' for Linux mode, then xmodem-transfers kernel+DTB+initramfs to SDRAM with CRC-32 verification
3. **Kernel jump** — stage2 jumps to 0x40000000 with a0=hartid, a1=DTB pointer
4. **Linux console** — ~20s boot to mini shell (custom /init, not busybox)

### Memory map
- `0x00000000` — 8 KB IMEM (stage2 loader, M9K BRAM)
- `0x40000000` — 32 MB SDRAM (kernel at base, DTB at +0x1F00000, initramfs at +0x1F80000)
- `0x80000000` — 8 KB DMEM (M9K BRAM)
- `0xFFF40000` — CLINT (timer), `0xFFF50000` — UART0

### Key technical decisions
- **Atomics use native RISC-V instructions** — NEORV32 has Zaamo + Zalrsc enabled. After fixing a [SC.W return value bug](https://github.com/stnolting/neorv32/pull/1520) in the RTL plus the LR/SC reservation policy in `neorv32_patches/0004-rvs-address-tracking.patch`, the kernel uses upstream unmodified `cmpxchg.h`, `atomic.h` with native LR/SC and AMO instructions (810 atomic instructions in the kernel binary). Verified with 11 userspace LR/SC tests.
- **D-cache disabled** (`DCACHE_EN => false`) — see Project Overview at top. Re-enabling requires either burst-capable SDRAM or kernel patches to make hot reads (e.g., `ktime_get_coarse_real_ts64`) resilient to slow read windows.
- **Scheduler modified to single-shot `__schedule()`** — prevents infinite `need_resched` loops caused by non-atomic `test_and_clear` racing with timer interrupts. Safe because single-core.
- **`wfi` is upstream (not patched)** — testing confirmed that `wfi` works correctly; the timer interrupt wakes the CPU as expected.
- **RISCV_ALTERNATIVE disabled** — runtime instruction patching conflicts with non-atomic replacements; causes illegal instruction trap after `free_initmem()`.
- **UART driver uses kthread polling** — no IRQ, no work queues (unreliable with modified scheduler). Direct line discipline delivery.
- **RCU/async made synchronous** — `srcutiny` grace periods forced synchronous; `populate_rootfs()` called directly; `async_synchronize_full()` has 120s timeout.

### Cross-compiler toolchain
Prefix: `riscv-none-elf-` (xPack 14.2.0). The stage2 Makefile defaults to this on `$PATH`; override with `make RISCV_PREFIX=/abs/path/to/riscv-none-elf-` if not installed system-wide. Kernel uses the same `riscv-none-elf-` prefix as `CROSS_COMPILE`.

## Important Constraints
- Stage2 loader **must fit in 8 KB** IMEM (set via linker flags in Makefile)
- The FPGA has only 6,272 LEs — no room for additional peripherals
- SDRAM can intermittently fail on first power-on; power-cycle to resolve
- The DTS advertises `riscv,isa-extensions = "a"` — NEORV32 has hardware atomic support (Zaamo + Zalrsc). The kernel uses native AMO/LR/SC instructions after a [SC.W RTL bug fix](https://github.com/stnolting/neorv32/pull/1520)

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
10. **Kernel size must be close to 1,513,100 bytes**: If the Image is significantly larger (>1.55 MB), unwanted features got auto-enabled. Check the config items in pitfall #9.
11. **Kernel compiler: xPack riscv-none-elf-gcc 14.2.0 only**: Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 compiles the kernel without errors, but the resulting Image hangs at `free_initmem()` on NEORV32 hardware. The hang is caused by a subtle code generation difference in GCC 12.4.0. Do NOT substitute compilers for the kernel build.
12. **Don't enable D-cache without burst memory**: Setting `DCACHE_EN => true` in `rtl/ax301_top.vhd` against the current non-burst `wb_sdram_ctrl.v` is a net performance loss (every dirty eviction emits 16 single-word stores) AND triggers a `ktime_get_coarse_real_ts64` seqcount-retry livelock on hot kernel paths during fs_initcall. See `neorv32_patches/README.md` "Status" section for details. To enable D-cache, first add burst support to the SDRAM controller (or move to a burst-capable backend).
13. **Apply RTL patches after submodule init**: After `git submodule update --init --recursive`, apply `neorv32_patches/*.patch` to the `neorv32/` working tree. The current pin (`70393ec4`) requires `0004-rvs-address-tracking.patch` for kernel boot — without it, `mutex_unlock()` livelocks under IRQ load because every IRQ trap-entry stack push clears the LR/SC reservation. The former `0001` (fence.i) and `0002` (AMO bypass) D-cache fixes are now upstream and must NOT be re-applied.
