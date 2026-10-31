# See NEORV32 Run Linux

Booting nommu Linux (kernel **6.18.60 LTS**) on a **NEORV32** RV32IMAC soft-core FPGA — believed to be the first successful Linux boot on NEORV32.

The NEORV32 is a microcontroller-class processor with **no MMU** and **no S-mode**. The first bring-up on Linux 6.6.83 needed 16 kernel patches across arch/riscv, the scheduler, RCU, init and drivers. On 6.18 none of them are needed: hardware measurements traced the scheduler / RCU / work-queue hangs to a single problem — on this core the timer tick is too expensive for HZ=250 — so the current kernel is **vanilla 6.18.60** plus an out-of-tree UART driver and a defconfig with `CONFIG_HZ_100`.

We also discovered and fixed a [bug in NEORV32's SC.W instruction](https://github.com/stnolting/neorv32/pull/1520) — the store-conditional was returning stale data instead of a success/failure status code. This fix enables the kernel to use native RISC-V atomic instructions (LR/SC + AMO).

**Demo video:** https://youtu.be/JC6qNcMIWf8

## Documentation

This repository includes three types of documentation, each for a different audience:

| File | Audience | Purpose |
|------|----------|---------|
| `README.md` / `README_zh.md` | **Humans** | Project overview, build steps, architecture explanation |
| `CLAUDE.md` | **AI agents** | Machine-readable build flow, constraints, and known pitfalls for [Claude Code](https://claude.ai/code) |
| `init_prompt.txt` | **Claude Code bootstrap** | Paste this into Claude Code to have it build the entire project from source automatically |
| `implementation_plan_en.md` / `implementation_plan_zh.md` | **Developers** | Detailed implementation plan from scratch, covering hardware verification, kernel porting, driver development, and all phases |
| `BUILD_LOG.md` | **Developers** | Chronicle of the 105 builds of the original Linux 6.6.83 bring-up, from "kernel compiled" to "shell prompt" — what broke, why, and how it was fixed (historical; see "Why Is This Hard?" for what turned out to be the common root cause) |

To reproduce the full build with Claude Code, open a terminal in the repo root and run:
```bash
claude
```
Then paste the contents of `init_prompt.txt` as your first message. Claude Code will read `CLAUDE.md` for detailed instructions and execute the complete build-from-source flow.

## Hardware

| Component | Spec |
|-----------|------|
| **Board** | Heijin AX301 |
| **FPGA** | Altera Cyclone IV E EP4CE6F17C8 (6,272 LEs) |
| **CPU** | NEORV32 RV32IMAC, 50 MHz, M-mode only |
| **RAM** | 32 MB SDRAM (HY57V2562GTR) |
| **UART** | PL2303 USB-UART, 115200 baud |
| **Programmer** | USB-Blaster via openFPGALoader |

## Demo

```
[stage2] Linux direct boot mode
[4] Sending kernel (1,717,184 bytes) via xmodem...
  [xmodem] Transfer complete
  [kernel] CRC MATCH: xxxxxxxx ✓
...
[    0.000000] Linux version 6.18.60 (riscv32)
[    0.000000] Kernel command line: earlycon=neorv32,0xfff50000 console=ttyNEO0,115200
[    0.489351] Memory: 30496K/32768K available (1249K kernel code, ...)
[    4.888356] printk: legacy console [ttyNEO0] enabled
[    5.675141] Run /init as init process

========================================
 NEORV32 nommu Linux — mini shell
========================================
Linux (none) 6.18.60 riscv32
Uptime:    6 s
Total RAM: 30808 KB
Free RAM:  29988 KB
Processes: 15

Type 'help' for commands.

nommu# amo
=== AMO test (Zaamo) ===
amoadd.w: old=0x00000064 new=0x00000096   [PASS]
...
=== LR/SC detailed tests ===
A) basic: lr=0x0000002a sc.rd=0x00000000 mem=0x00000063   [PASS]
...
Result: 11/11 ALL PASSED
```

## Build from Source

All source code is included in this repository, with one exception: the Linux kernel tarball `linux-6.18.60.tar.xz` (~150 MB) is too large for GitHub and is **not tracked in git** — download it once from kernel.org (see Step 5).

### Prerequisites

| Tool | Purpose |
|------|---------|
| Intel Quartus Prime Lite 21.1+ | FPGA synthesis |
| xPack RISC-V GCC 14.2.0 (riscv-none-elf-) | Kernel & stage2 cross-compiler (**required version**) |
| Buildroot Linux GCC (riscv32-buildroot-linux-gnu-) | Initramfs /init only (needs PIE support) |
| CMake + libftdi1-dev + libusb-1.0-0-dev | openFPGALoader build |
| dtc (device tree compiler) | DTB compilation |
| Python 3 + pyserial | Host boot script |

### Step 1: Initialize NEORV32 submodule

```bash
git submodule update --init --recursive
```

> No local RTL patches are needed: our last fix (a successful `SC.W` must stay a write for the whole bus transfer) is upstream since release v1.13.7 ([PR #1661](https://github.com/stnolting/neorv32/pull/1661)). Do not use NEORV32 v1.13.2–v1.13.6 with this SDRAM controller — atomic `cmpxchg` stores get silently dropped and the kernel hangs in `vfs_caches_init()`. See `neorv32_patches/README.md` for details.

### Step 2: Build openFPGALoader

```bash
cd tools/openFPGALoader
mkdir build && cd build
cmake .. && make -j$(nproc)
```

> **Note:** The system-installed openfpgaloader (v0.12.0) does NOT support EP4CE6. Must build from source.

### Step 3: Build FPGA bitstream

```bash
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off output_files/neorv32_demo.sof ../output/neorv32_demo.rbf
```

### Step 4: Build stage2 loader

```bash
cd sw/stage2_loader
make NEORV32_HOME=../../neorv32 exe
cp neorv32_exe.bin ../../output/stage2_loader.bin
```

### Step 5: Build kernel + initramfs

The kernel is **vanilla 6.18.60** — no kernel patches; only the UART driver is injected and the board defconfig copied in.

```bash
# Download the kernel tarball first if not already present (not tracked in git)
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.60.tar.xz
tar xf linux-6.18.60.tar.xz

# Inject the NEORV32 UART driver and the board defconfig
board/inject_driver.sh linux-6.18.60
cp board/linux_defconfig linux-6.18.60/arch/riscv/configs/neorv32_ax301_defconfig

# Build initramfs (Buildroot Linux toolchain for PIE support). It is NOT
# embedded in the kernel: stage2 loads it and points the DTB at it.
make -C sw/initramfs LINUX_DIR=../../linux-6.18.60
cp sw/initramfs/neo_initramfs.cpio.gz output/

# Build kernel (xPack riscv-none-elf-gcc 14.2.0 on $PATH)
cd linux-6.18.60
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
grep '^CONFIG_HZ=' .config        # must print CONFIG_HZ=100
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)
cp arch/riscv/boot/Image ../output/
```

### Step 6: Compile device tree

```bash
dtc -I dts -O dtb -o output/neorv32_ax301.dtb board/neorv32_ax301.dts
```

### Step 7: Boot

```bash
python3 host/boot_linux.py --port /dev/ttyUSB0
```

This is the **default path** and requires only FPGA + SDRAM + UART — **no SD card needed**. Kernel / DTB / initramfs are streamed to SDRAM over UART via xmodem on every boot (measured 2026-10-30: **~221 s** from `boot_linux.py` start to the shell prompt — the FPGA programming, stage2 upload and 1.7 MB xmodem transfer take all but the last ~6 s, which are the kernel boot).

## Fast Boot from SD Card (optional)

> **Optional** — only use this if you have an SD card wired to the FPGA's SPI pins. The xmodem path in Step 7 above works without any SD card.

The UART xmodem transfer takes ~3.5 min every boot. To skip it, the stage2 loader can read the kernel blob directly from an SD card over NEORV32's hardware SPI peripheral. Linux still runs from SDRAM — **the SD card is only read-only bulk storage at boot time**, so no kernel-side driver is required.

**Wiring** (AX301 on-board SD slot): `PIN_J15=SD_CLK`, `PIN_K16=SD_DI (MOSI)`, `PIN_J16=SD_DO (MISO)`, `PIN_K15=SD_NCS`. The FPGA bitstream must be rebuilt with `IO_SPI_EN=true` (already set in `rtl/ax301_top.vhd`).

**One-time: pack + write the blob to SD**

```bash
# Packs Image + DTB + initramfs into a NEOLNX-magic blob and streams it to SD
python3 host/sd_pack.py --port /dev/ttyUSB0
```

This takes ~160 s (one-time). The blob lives at raw LBA 0 — no MBR / no filesystem.

**Every boot: load from SD**

```bash
python3 host/boot_sd.py --port /dev/ttyUSB0
```

With the old 6.6 kernel this reached the shell in **~150 s**, about 90 s faster than xmodem at the time. It has not been re-measured with 6.18 (no SD card in the test board); the kernel's own share is now ~6 s.

**Decoupled kernel / initramfs:** The kernel is built with `CONFIG_INITRAMFS_SOURCE=""` — initramfs is **not embedded** in the Image. Stage2 loads Image / DTB / initramfs as three independent sections from the SD blob, then patches the DTB's `chosen/linux,initrd-end` sentinel (`0xC0DEDEAD`) in RAM with the real end address before jumping to the kernel. Linux then unpacks the initramfs from the address passed via DT `chosen` properties.

This means **changing init or userspace apps no longer requires rebuilding the kernel**.

### Incremental SD updates (`sd_update.py`)

For the common case of "I just tweaked `/init`", rewriting all 2966 sectors of the blob is overkill. The SD blob uses fixed LBA slots (see `host/sd_layout.py`):

| Slot   | Start LBA | Reserved | Current use |
|--------|-----------|----------|-------------|
| header | 0         | 1 sec    | magic + sizes + LBAs + `layout_version` |
| Image  | 1         | 4000 sec (2 MB) | ~1.7 MB |
| DTB    | 4001      | 8 sec (4 KB)    | ~1.5 KB |
| initrd | 4009      | 4000 sec (2 MB) | ~3 KB — lots of room for apps |

Because LBAs are fixed, `sd_update.py` can rewrite only the header + the slot(s) that changed — typically just **7 sectors (~10 s total, ~1 s of actual SD write)** instead of 163 s. Before writing, it reads the on-card header via a new stage2 `R` mode and verifies `magic` / `layout_version` / LBA constants match `sd_layout.py`; if the layout has drifted (e.g. you bumped a slot size) it refuses and tells you to run `sd_pack.py` first.

```bash
# One-shot /init edit-test loop (default; uses 230400 baud + persistent stage2):
vim sw/initramfs/init.c
make -C sw/initramfs LINUX_DIR=../../linux-6.18.60
cp sw/initramfs/neo_initramfs.cpio.gz output/
python3 host/boot_sd.py --update        # update init slot + boot, ~17s write
# Add --update-dtb / --update-kernel for other slots; --update-verify
# re-reads the header after writing to sanity-check.

# Just update without booting:
python3 host/sd_update.py --port /dev/ttyUSB0 --verify

# Full rewrite (kernel changed, layout changed, or first time on a card):
python3 host/sd_pack.py --port /dev/ttyUSB0     # ~99s @ 230400
```

| Mode | Host tool | Time to shell | When to use |
|------|-----------|---------------|-------------|
| xmodem | `boot_linux.py` | ~221 s (≈215 s transfer + 6 s boot) | No SD card, or debugging stage2 |
| SD blob | `boot_sd.py` | ~150 s (6.6-era, not re-measured) | Daily development (after one-time `sd_pack.py`) |

### Host/stage2 optimizations (Phase 1-6)

The SD path above is further sped up by six stacked optimizations:

1. **UART baud bump to 230400** with host probe-byte sync and auto-fallback to 115200. Stage2 mode `B`; shared via `host/sd_proto.setup_session()`. `sd_pack.py`: **165 s → 99 s**.
2. **Persistent stage2** — dispatcher idles forever between commands; `--persistent --baud 230400` on any host tool skips FPGA program + handshake + upload. `sd_update.py`: **39 s → 17 s**.
3. **`boot_sd.py --update`** — one-shot edit-update-boot with automatic fast-baud → console-baud handoff before the kernel jump.
4. **`sd_update.py --verify`** — re-reads header via mode `R` after writing and compares every field.
5. **Parametric `sd_dump.py --lba/--count/--hex`** — stage2 mode `d` now takes LBA+count over UART (cap 2 MB) and returns to the dispatcher, so dumps chain under `--persistent`.
6. **`boot_sd.py` build-tag check** — prints `on-card vs local` sizes per slot before sending `'b'`, marking each `✓` or `✗ STALE`. Catches a stale SD before a ~150 s boot cycle. Skip with `--no-check`.

Stage2 loader modes (UART command after stage2 is uploaded): `l`=xmodem, `s`=SD smoke test, `d`=SD dump (parametric), `w`/`W`=write test / multi-segment write, `R`=read header, `B`=set baud, `b`=boot from SD blob.

## System Architecture

### Block Diagram

```
  AX301 Board (EP4CE6, 50 MHz)
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │  ┌───────────────────────────────────────────────┐  │
  │  │            NEORV32 SoC (RV32IMAC)              │  │
  │  │                                               │  │
  │  │  ┌───────┐  ┌──────┐  ┌──────┐  ┌─────────┐  │  │
  │  │  │  CPU  │  │ IMEM │  │ DMEM │  │ Boot ROM│  │  │
  │  │  │RV32IMAC│  │ 8 KB │  │ 8 KB │  │  ~4 KB  │  │  │
  │  │  │ M+U   │  │ BRAM │  │ BRAM │  │(bootldr)│  │  │
  │  │  └───┬───┘  └──────┘  └──────┘  └─────────┘  │  │
  │  │      │                                        │  │
  │  │  ┌───┴───┐  ┌──────┐  ┌──────┐  ┌─────────┐  │  │
  │  │  │Wishbone│  │I$:4KB│  │D$:off│  │  CLINT  │  │  │
  │  │  │  XBUS  │  │      │  │      │  │(timer)  │  │  │
  │  │  └───┬───┘  └──────┘  └──────┘  └─────────┘  │  │
  │  │      │                                        │  │
  │  │  ┌───┴───┐               ┌──────────┐         │  │
  │  │  │UART0  │               │   GPIO   │         │  │
  │  │  │115200 │               │  4 LEDs  │         │  │
  │  │  └───┬───┘               └────┬─────┘         │  │
  │  └──────┼────────────────────────┼───────────────┘  │
  │         │                        │                  │
  │  ┌──────┴──────┐           ┌─────┴─────┐           │
  │  │wb_sdram_ctrl│           │   LEDs    │           │
  │  │ (Wishbone   │           └───────────┘           │
  │  │  → SDRAM)   │                                   │
  │  └──────┬──────┘                                   │
  │         │                                          │
  │  ┌──────┴──────┐                                   │
  │  │  sdram_ctrl │                                   │
  │  │  (FSM, CL=3)│                                   │
  │  └──────┬──────┘                                   │
  └─────────┼──────────────────────────────────────────┘
            │
     ┌──────┴──────┐       ┌────────────┐
     │  HY57V2562  │       │  PL2303    │
     │  32 MB SDRAM│       │  USB-UART  │
     └─────────────┘       └────────────┘
```

### Boot Sequence (4 stages)

```
Power on → NEORV32 internal bootloader (19200 baud, ROM at 0xFFE00000)
  ↓ upload stage2_loader.bin (7.9 KB)
  ↓ execute → UART switches to 115200 baud
Stage2 loader (IMEM, 115200 baud)
  ↓ 'l' → Linux direct boot mode
  ↓ xmodem: kernel Image (1.7 MB) → SDRAM 0x40000000, CRC-32 verify
  ↓ xmodem: DTB (1.4 KB)          → SDRAM 0x41F00000, CRC-32 verify
  ↓ xmodem: initramfs (2.9 KB)    → SDRAM 0x41F80000, CRC-32 verify
  ↓ jump to 0x40000000 with a0=hartid, a1=DTB pointer
Linux kernel (M-mode, nommu)
  ↓ ~6 s boot → /init (mini shell from initramfs)
```

### FPGA Memory Map

| Address | Size | Description |
|---------|------|-------------|
| `0x00000000` | 8 KB | IMEM (M9K BRAM — stage2 loader) |
| `0x40000000` | 32 MB | SDRAM (kernel + data, external) |
| `0x80000000` | 8 KB | DMEM (M9K BRAM — kernel stack/heap) |
| `0xFFE00000` | ~4 KB | Boot ROM (NEORV32 bootloader, read-only) |
| `0xFFF40000` | 48 KB | CLINT (mtime at +0xBFF8, mtimecmp at +0x4000) |
| `0xFFF50000` | 8 B | UART0 (CTRL + DATA registers) |
| `0xFFFC0000` | 16 B | GPIO (gpio_o[3:0] → LEDs active-low) |

### Linux Runtime Memory Layout (SDRAM)

After boot, the 32 MB SDRAM at `0x40000000` is used as follows:

```
0x40000000 ┌─────────────────────────┐
           │     Linux Kernel        │  ~1.7 MB
           │  .text, .rodata, .data  │
           │  (loaded by stage2)     │
0x401A4000 ├─────────────────────────┤  (approx)
           │     Kernel BSS          │  ~51 KB
0x401B1000 ├─────────────────────────┤
           │                         │
           │   Free Memory (buddy)   │  ~30 MB
           │   managed by page alloc │
           │                         │
0x41F00000 ├─────────────────────────┤
           │   Device Tree Blob      │  ~1.4 KB
           │   (passed to kernel     │
           │    via a1 register)     │
0x41F80000 ├─────────────────────────┤
           │   initramfs (cpio.gz)   │  ~2.9 KB
           │   (unpacked by kernel   │
           │    into rootfs tmpfs)   │
0x42000000 └─────────────────────────┘  End of 32 MB
```

**Key runtime numbers** (from kernel log):
- Total RAM: 32,768 KB (32 MB)
- Available after boot: 30,496 KB (~30 MB free)
- Kernel code: 1,249 KB | RW data: 141 KB | RO data: 178 KB | Init: 102 KB | BSS: 51 KB

## Why Is This Hard?

NEORV32 is a microcontroller core — it was never designed to run Linux. Here's what we had to work around:

### 1. No MMU, No S-mode

Linux normally runs in S-mode (supervisor) with virtual memory. NEORV32 only has M-mode (machine) and U-mode. We run the kernel directly in M-mode using the `nommu` configuration.

**Key config:** `CONFIG_MMU=n`, `CONFIG_PHYS_RAM_BASE=0x40000000` (on 6.18 nommu, `PAGE_OFFSET` follows the physical RAM base automatically).

### 2. The Timer Tick Is Too Expensive for HZ=250

On 6.6 we saw `need_resched` ping-pong loops, RCU grace periods that never finished and work queues that never ran, and patched each one separately (single-shot `__schedule()`, synchronous SRCU, synchronous `populate_rootfs()`, …). Instrumenting the 6.18 scheduler and timer interrupt on hardware showed what was behind all of them:

| I-cache | 256 B (NEORV32 default) | 256 B | **4 KB** | **4 KB** |
|---|---|---|---|---|
| HZ | 250 (kernel default) | 100 | 250 | 100 |
| Tick period (cycles) | 200,000 | 500,000 | 200,000 | 500,000 |
| Timer-interrupt handler, average | **~170,000 (85 % of a tick)** | ~109,000 (22 %) | ~86,000 (43 %) | ~74,000 (15 %) |
| `__schedule()` incl. the tick serviced when IRQs come back on | ~357,000 (1.8 ticks) | ~82,000 | ~68,000 | ~55,000 |
| Switches longer than one tick | 98.7 % | 0 % | 5 % | 0 % |
| `while (need_resched())` iterations in one `schedule()` call | **>4,096 — livelock** | < 32 | < 32 | < 32 |
| Result | **hang** | boots, 9 s | boots, 10 s | **boots, 6 s** |

At HZ=250 with the default 256-byte I-cache, the tick that becomes pending during every context switch sets `need_resched` again, so `schedule()` keeps switching between the same few tasks (init, `ksoftirqd/0`, `kworker/u4:0`) and none of them does any work: kworkers never run, and boot hangs a few seconds after the clocksource switch. The handler is that slow mainly because 256 bytes of I-cache cannot hold the kernel's hot paths — every miss refills a 64-byte line from SDRAM one word at a time.

**Solution:** `CONFIG_HZ_100` (no scheduler, RCU, kthread or init patches) plus a **4 KB I-cache** (`ICACHE_NUM_BLOCKS => 64`, +6 LE and +4 M9K). Either change alone is enough to boot; HZ=100 is kept because even with 4 KB the tick still costs 43 % of an HZ=250 tick.

### 3. ~~`wfi` Halts the CPU~~ (Resolved)

Initially we replaced `wfi` with `nop`, but testing confirmed that `wfi` works correctly — the timer interrupt wakes the CPU as expected. The upstream `wfi` instruction is now restored.

### 4. ~~RISCV_ALTERNATIVE Patching Conflict~~ (Resolved)

On 6.6 we disabled `RISCV_ALTERNATIVE` after an illegal-instruction trap following `free_initmem()`. On 6.18 with native atomics and HZ=100 it runs with the upstream default (enabled); none of the alternatives are patched in, because the DT ISA string is `acim`.

### 5. ~~RCU / Work Queue Stalls~~ (Resolved)

Same root cause as #2 — at HZ=100 SRCU, async init and work queues run unmodified.

### 6. UART Driver

NEORV32's UART is not supported by any upstream Linux driver. We wrote a custom `neorv32_uart.c` tty driver with kthread-based polling (no IRQ) and direct line discipline delivery.

## Kernel Changes

The kernel is **vanilla Linux 6.18.60** — there is no kernel patch file. Everything NEORV32-specific lives in `board/`:

| File | What it does |
|------|--------------|
| `board/neorv32_uart.c` | Custom UART tty driver (kthread polling, earlycon), copied into `drivers/tty/serial/` |
| `board/inject_driver.sh` | Copies the driver and adds its Kconfig/Makefile entries |
| `board/linux_defconfig` | nommu RV32 M-mode config: `CONFIG_HZ_100`, `PHYS_RAM_BASE=0x40000000`, external initramfs, no block/net/modules |
| `board/neorv32_ax301.dts` | Device tree (`riscv,isa-extensions` = `i m a c` …, CLINT, UART, initrd location patched by stage2) |

**Atomics:** The kernel uses **native RISC-V atomic instructions** (AMO + LR/SC). This was made possible by finding and fixing a [bug in NEORV32's SC.W instruction](https://github.com/stnolting/neorv32/pull/1520) — the store-conditional was returning the value loaded by LR.W in rd instead of 0 (success) — and later a [request-stability bug](https://github.com/stnolting/neorv32/pull/1661) in the reservation station. Both are upstream. All 11 userspace LR/SC/AMO tests pass.

The original 16-file patch for 6.6.83 (`kernel/neorv32_nommu.patch`) and its story are in the git history and `BUILD_LOG.md`.

## FPGA RTL

The NEORV32 is configured with these generics in `rtl/ax301_top.vhd`:

| Generic | Value | Why |
|---------|-------|-----|
| `RISCV_ISA_C` | true | Compressed instructions (smaller code) |
| `RISCV_ISA_M` | true | Hardware multiply/divide |
| `RISCV_ISA_U` | true | U-mode for Linux userspace |
| `RISCV_ISA_Zaamo` | true | Atomic memory operations (AMO) |
| `RISCV_ISA_Zalrsc` | true | Load-reserved / store-conditional (LR/SC) |
| `ICACHE_EN` | true | Required for SDRAM instruction fetch |
| `ICACHE_NUM_BLOCKS` | 64 | 64 × 64 B = **4 KB** I-cache (default 4 = 256 B): halves the timer-tick cost, boot 9 s → 6 s |
| `DCACHE_EN` | **false** | Write-back D-cache against the non-burst SDRAM controller is a net loss. Re-tested 2026-10-30 with the 4 KB I-cache (DMEM cut to 4 KB to free M9Ks): the old `ktime` seqcount livelock is gone, but boot is slower (7 s vs 6 s) and `fence.i` turned out to be broken with the D-cache on (an upstream NEORV32 bug: instruction fetch can read memory before the D-cache has written new code back) — kept off (see `neorv32_patches/README.md`) |
| `IMEM_SIZE` | 8 KB | Stage2 loader fits in 8 KB |
| `DMEM_SIZE` | 8 KB | Kernel stack/heap scratch space |
| `IO_UART0_RX_FIFO` | 4 | 4-entry FIFO for console input (generic is the depth, not log2) |
| `IO_UART0_TX_FIFO` | 4 | 4-entry FIFO for console output |

## Project Structure

```
see_neorv32_run_linux/
├── tools/openFPGALoader/  — openFPGALoader source (build from source)
├── neorv32/               — NEORV32 RTL (git submodule → stnolting/neorv32 release tag, no local patches)
├── linux-6.18.60.tar.xz  — Linux kernel tarball (not tracked — download from kernel.org)
├── rtl/                   — Custom FPGA design
│   ├── ax301_top.vhd      — top-level: NEORV32 + SDRAM + UART + GPIO
│   ├── wb_sdram_ctrl.v    — Wishbone → SDRAM bridge
│   └── sdram_ctrl.v       — SDRAM controller FSM (CL=3, 50 MHz)
├── quartus/               — Quartus project files (.qsf, .qpf, .sdc)
├── board/                 — Board support files
│   ├── neorv32_ax301.dts   — device tree source
│   ├── linux_defconfig     — kernel config (Linux 6.18, CONFIG_HZ_100)
│   ├── neorv32_uart.c      — custom UART driver source
│   ├── inject_driver.sh    — injects driver into kernel tree
│   └── buildroot_defconfig — Buildroot config used for the Linux toolchain that builds /init (its kernel settings are legacy 6.6.83, unused)
├── sw/
│   ├── stage2_loader/      — xmodem boot loader (C, runs from IMEM)
│   │   ├── main.c
│   │   └── Makefile        — uses NEORV32 common.mk
│   └── initramfs/          — minimal /init for Linux
│       ├── init.c           — custom shell (syscalls only, no libc)
│       └── Makefile
├── host/                  — Python host scripts
│   ├── boot_linux.py       — full xmodem boot sequence (program + upload + console)
│   ├── boot_sd.py          — boot from SD blob (optionally --update first)
│   ├── sd_pack.py / sd_update.py — write full SD blob / only changed slots
│   ├── sd_dump.py / sd_smoke.py / sd_write_test.py — SD debug tools
│   ├── sd_layout.py / sd_proto.py — shared SD slot layout + stage2 protocol helpers
│   ├── fencei_test.py      — fence.i vs D-cache coherence test (stage2 built with -DFENCEI_TEST)
│   └── test_shell.py       — shell command tester
├── neorv32_patches/       — notes on our (now upstream) NEORV32 RTL fixes
├── sim/                   — iverilog testbench for sdram_ctrl (`make` to run)
├── icesugar/              — bare-metal NEORV32 port to iCESugar (iCE40UP5K)
├── output/                — build outputs (populated by build steps)
└── BUILD_LOG.md           — 105-build debugging chronicle of the 6.6 bring-up
```

## Resource Usage (EP4CE6, 50 MHz)

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic Elements | 4,525 | 6,272 | 72% |
| Memory bits | 198,912 | 276,480 | 72% |
| M9K blocks | 27 | 30 | 90% |
| Registers | 2,103 | 6,272 | 34% |
| Embedded Multipliers | 0 | 30 | 0% |

(Quartus fits for the EP4CE10 die with high-effort placement; the bitstream runs on the AX301's EP4CE6. Numbers from NEORV32 v1.13.7.)

## Known Issues

- **Kernel must be built with xPack `riscv-none-elf-gcc` 14.2.0:** On the old 6.6.83 kernel, building with Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 produced a binary that hung in `free_initmem()` (not re-tested on 6.18) — the last debug marker `L` (system_state = RUNNING) prints, but execution never reaches `M` (after free_initmem). This happens despite identical source code, patches, kernel config, and FPGA bitstream. The only variable is the compiler. Root cause is a subtle code generation difference in GCC 12.4.0 that triggers a hang on NEORV32's constrained environment. The Buildroot-built kernel also runs noticeably slower overall (clocksource switch at 13.8s vs 7.4s, triggers `sched: RT throttling activated`). **Always use the xPack bare-metal toolchain for the kernel.**
- **Boot time ~6 s** (kernel start → shell, 6.18 + HZ=100 + 4 KB I-cache); the xmodem transfer before it takes ~3.5 min — use the SD path for fast iteration.
- **SDRAM intermittent init failure:** Occasionally fails on first power-on. Power-cycle the board (off for a few seconds) to resolve.
- **Shell is minimal:** Only `uname`, `info`, `amo`, `help`, `exit`. The init binary is a custom C program, not busybox (to keep initramfs tiny).
- **No network, no storage:** This is a bare UART console. The EP4CE6 has no room for additional peripherals.

## License

- NEORV32 RTL: BSD 3-Clause (see [NEORV32 repo](https://github.com/stnolting/neorv32))
- Linux UART driver and kernel config: GPL-2.0 (same as the kernel)
- SDRAM controller, host scripts, stage2 loader: MIT
