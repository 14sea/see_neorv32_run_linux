# See NEORV32 Run Linux

Booting nommu Linux (kernel 6.6.83) on a **NEORV32** RV32IMAC soft-core FPGA — believed to be the first successful Linux boot on NEORV32.

The NEORV32 is a microcontroller-class processor with **no MMU** and **no S-mode**. Getting Linux to run on it required 16 patches across the kernel's arch/riscv, scheduler, RCU, init, and driver subsystems.

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
| `BUILD_LOG.md` | **Developers** | Chronicle of 105 builds from "kernel compiled" to "shell prompt" — what broke, why, and how it was fixed |

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
[4] Sending kernel (1,513,100 bytes) via xmodem...
  [xmodem] Transfer complete
  [kernel] CRC MATCH: xxxxxxxx ✓
...
[    0.000000] Linux version 6.6.83 (riscv32)
[    0.000000] Kernel command line: earlycon=neorv32,0xfff50000 console=ttyNEO0,115200
[    0.000000] Memory: 30908K/32768K available (1076K kernel code, ...)
[   85.896116] printk: console [ttyNEO0] enabled
[   98.580187] Run /init as init process

========================================
 NEORV32 nommu Linux — mini shell
========================================
Linux (none) 6.6.83 riscv32
Uptime:    97 s
Total RAM: 31004 KB
Free RAM:  30264 KB
Processes: 14

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

All source code is included in this repository. No external downloads needed.

### Prerequisites

| Tool | Purpose |
|------|---------|
| Intel Quartus Prime Lite 21.1+ | FPGA synthesis |
| xPack RISC-V GCC 14.2.0 (riscv-none-elf-) | Kernel & stage2 cross-compiler (**required version**) |
| Buildroot Linux GCC (riscv32-buildroot-linux-gnu-) | Initramfs /init only (needs PIE support) |
| CMake + libftdi1-dev + libusb-1.0-0-dev | openFPGALoader build |
| dtc (device tree compiler) | DTB compilation |
| Python 3 + pyserial | Host boot script |

### Step 1: Build openFPGALoader

```bash
cd tools/openFPGALoader
mkdir build && cd build
cmake .. && make -j$(nproc)
```

> **Note:** The system-installed openfpgaloader (v0.12.0) does NOT support EP4CE6. Must build from source.

### Step 2: Build FPGA bitstream

```bash
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

### Step 4: Build kernel + initramfs

```bash
# Extract and patch kernel
tar xf linux-6.6.83.tar.xz && cd linux-6.6.83
patch -p1 < ../kernel/neorv32_nommu.patch
../board/inject_driver.sh .

# Build initramfs (uses Buildroot Linux toolchain for PIE support)
cd ../sw/initramfs
make LINUX_DIR=../../linux-6.6.83
cp neo_initramfs.cpio.gz ../../output/

# Fix initramfs path in defconfig, then build kernel
cd ../../
sed "s|CONFIG_INITRAMFS_SOURCE=.*|CONFIG_INITRAMFS_SOURCE=\"$(pwd)/output/neo_initramfs.cpio.gz\"|" \
    board/linux_defconfig > linux-6.6.83/arch/riscv/configs/neorv32_ax301_defconfig
cd linux-6.6.83
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)
cp arch/riscv/boot/Image ../output/
```

### Step 5: Compile device tree

```bash
dtc -I dts -O dtb -o output/neorv32_ax301.dtb board/neorv32_ax301.dts
```

### Step 6: Boot

```bash
python3 host/boot_linux.py --port /dev/ttyUSB0
```

This is the **default path** and requires only FPGA + SDRAM + UART — **no SD card needed**. Kernel / DTB / initramfs are streamed to SDRAM over UART via xmodem on every boot (~145 s transfer + ~98 s kernel boot ≈ 243 s to shell).

## Fast Boot from SD Card (optional)

> **Optional** — only use this if you have an SD card wired to the FPGA's SPI pins. The xmodem path in Step 6 above works without any SD card.

UART xmodem transfer takes ~145 s every boot. To skip it, the stage2 loader can read the kernel blob directly from an SD card over NEORV32's hardware SPI peripheral. Linux still runs from SDRAM — **the SD card is only read-only bulk storage at boot time**, so no kernel-side driver is required.

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

Reaches the shell prompt in **~150 s** (vs ~243 s for xmodem boot, saving ~90 s per boot cycle).

**Decoupled kernel / initramfs:** The kernel is built with `CONFIG_INITRAMFS_SOURCE=""` — initramfs is **not embedded** in the Image. Stage2 loads Image / DTB / initramfs as three independent sections from the SD blob, then patches the DTB's `chosen/linux,initrd-end` sentinel (`0xC0DEDEAD`) in RAM with the real end address before jumping to the kernel. Linux then unpacks the initramfs from the address passed via DT `chosen` properties.

This means **changing init or userspace apps no longer requires rebuilding the kernel**.

### Incremental SD updates (`sd_update.py`)

For the common case of "I just tweaked `/init`", rewriting all 2966 sectors of the blob is overkill. The SD blob uses fixed LBA slots (see `host/sd_layout.py`):

| Slot   | Start LBA | Reserved | Current use |
|--------|-----------|----------|-------------|
| header | 0         | 1 sec    | magic + sizes + LBAs + `layout_version` |
| Image  | 1         | 4000 sec (2 MB) | ~1.5 MB |
| DTB    | 4001      | 8 sec (4 KB)    | ~1.5 KB |
| initrd | 4009      | 4000 sec (2 MB) | ~3 KB — lots of room for apps |

Because LBAs are fixed, `sd_update.py` can rewrite only the header + the slot(s) that changed — typically just **7 sectors (~10 s total, ~1 s of actual SD write)** instead of 163 s. Before writing, it reads the on-card header via a new stage2 `R` mode and verifies `magic` / `layout_version` / LBA constants match `sd_layout.py`; if the layout has drifted (e.g. you bumped a slot size) it refuses and tells you to run `sd_pack.py` first.

```bash
# One-shot /init edit-test loop (default; uses 230400 baud + persistent stage2):
vim sw/initramfs/init.c
make -C sw/initramfs LINUX_DIR=../../linux-6.6.83
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
| xmodem | `boot_linux.py` | ~243 s | No SD card, or debugging stage2 |
| SD blob | `boot_sd.py` | ~150 s | Daily development (after one-time `sd_pack.py`) |

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
  │  │  │Wishbone│  │ICACHE│  │DCACHE│  │  CLINT  │  │  │
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
  ↓ upload stage2_loader.bin (3.7 KB)
  ↓ execute → UART switches to 115200 baud
Stage2 loader (IMEM, 115200 baud)
  ↓ 'l' → Linux direct boot mode
  ↓ xmodem: kernel Image (1.5 MB) → SDRAM 0x40000000, CRC-32 verify
  ↓ xmodem: DTB (1.4 KB)          → SDRAM 0x41F00000, CRC-32 verify
  ↓ xmodem: initramfs (2.9 KB)    → SDRAM 0x41F80000, CRC-32 verify
  ↓ jump to 0x40000000 with a0=hartid, a1=DTB pointer
Linux kernel (M-mode, nommu)
  ↓ ~98s boot → /init (mini shell from initramfs)
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
           │     Linux Kernel        │  ~1.4 MB
           │  .text, .rodata, .data  │
           │  (loaded by stage2)     │
0x40170000 ├─────────────────────────┤  (approx)
           │     Kernel BSS          │  ~49 KB
0x4017C000 ├─────────────────────────┤
           │                         │
           │   Free Memory (buddy)   │  ~30 MB
           │   managed by page alloc │
           │                         │
0x41F00000 ├─────────────────────────┤
           │   Device Tree Blob      │  ~1.4 KB
           │   (passed to kernel     │
           │    via a1 register)     │
0x41F80000 ├─────────────────────────┤
           │   initramfs (cpio.gz)   │  ~1.7 KB
           │   (unpacked by kernel   │
           │    into rootfs tmpfs)   │
0x42000000 └─────────────────────────┘  End of 32 MB
```

**Key runtime numbers** (from kernel log):
- Total RAM: 32,768 KB (32 MB)
- Available after boot: 30,908 KB (~30 MB free)
- Kernel code: 1,076 KB | RW data: 137 KB | RO data: 160 KB | Init: 99 KB | BSS: 52 KB

## Why Is This Hard?

NEORV32 is a microcontroller core — it was never designed to run Linux. Here's what we had to work around:

### 1. No MMU, No S-mode

Linux normally runs in S-mode (supervisor) with virtual memory. NEORV32 only has M-mode (machine) and U-mode. We run the kernel directly in M-mode using the `nommu` configuration.

**Key config:** `CONFIG_MMU=n`, `CONFIG_PAGE_OFFSET=0x40000000` (must match physical RAM base).

### 2. Scheduler Deadlocks

The kernel scheduler's `need_resched` loop assumes preemption works at all points. In our single-core nommu environment, these can become infinite ping-pong loops between threads.

**Solution:** Modified `schedule()` and `preempt_schedule_common()` to execute `__schedule()` exactly once instead of looping on `need_resched`. Added `schedule_preempt_disabled_once()` for kthread startup.

### 3. ~~`wfi` Halts the CPU~~ (Resolved)

Initially we replaced `wfi` with `nop`, but testing confirmed that `wfi` works correctly — the timer interrupt wakes the CPU as expected. The upstream `wfi` instruction is now restored.

### 4. RISCV_ALTERNATIVE Patching Conflict

The kernel's alternative instruction patching framework (`RISCV_ALTERNATIVE`) replaces instructions at runtime based on detected ISA extensions. After `free_initmem()`, the `.alternative` section's __init data is freed, causing the CPU to execute freed memory as code (illegal instruction trap at `epc=0x4011c002`).

**Solution:** Disable `RISCV_ALTERNATIVE` entirely in `arch/riscv/Kconfig`.

### 5. RCU / Work Queue Stalls

Single-threaded RCU (`srcutiny`) and async work queues assume preemptive scheduling works correctly. On our single-core nommu system, grace periods never complete and `synchronize_srcu()` hangs forever.

**Solution:** Synchronous grace period in `srcutiny.c`, synchronous `populate_rootfs()` in `initramfs.c`, 120s timeout for `async_synchronize_full()`.

### 6. UART Driver

NEORV32's UART is not supported by any upstream Linux driver. We wrote a custom `neorv32_uart.c` tty driver with kthread-based polling (no IRQ) and direct line discipline delivery.

## Kernel Patches

All patches are in `kernel/neorv32_nommu.patch` (16 files against vanilla 6.6.83).

**Atomics:** The kernel uses **native RISC-V atomic instructions** (AMO + LR/SC). This was made possible by finding and fixing a [bug in NEORV32's SC.W instruction](https://github.com/stnolting/neorv32/pull/1520) — the store-conditional was returning the value loaded by LR.W in rd instead of 0 (success). The fix adds a `sc_pend` signal to `neorv32_bus_amo_rvs` that overrides the response data correctly. All 11 userspace LR/SC tests pass, and the kernel boots with 810 atomic instructions.

### Files Modified

| File | Change |
|------|--------|
| `arch/riscv/Kconfig` | Disable `RISCV_ALTERNATIVE` |
| `arch/riscv/kernel/traps.c` | M-mode trap handling adjustments |
| `kernel/sched/core.c` | Single-shot `__schedule()`, no `need_resched` loop |
| `kernel/sched/rt.c` | RT scheduler adjustments for nommu |
| `kernel/kthread.c` | Use `schedule_preempt_disabled_once()`, kthreadd priority boost |
| `kernel/rcu/srcutiny.c` | Synchronous SRCU grace period |
| `kernel/async.c` | 120s timeout for `async_synchronize_full()` |
| `include/linux/sched.h` | Declare `schedule_preempt_disabled_once()` |
| `include/linux/srcutiny.h` | SRCU structure adjustments |
| `init/main.c` | SCHED_FIFO boost for init, disable initmem poison |
| `init/initramfs.c` | Synchronous `populate_rootfs()` |
| `drivers/tty/serial/Kconfig` | Add NEORV32 UART option |
| `drivers/tty/serial/Makefile` | Build neorv32_uart.o |
| `drivers/tty/serial/neorv32_uart.c` | **New file** — custom UART driver |
| `arch/riscv/configs/neorv32_defconfig` | **New file** — kernel config |
| `arch/riscv/configs/neorv32_ax301_defconfig` | **New file** — AX301 board config |

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
| `DCACHE_EN` | true | Performance (SDRAM data access) |
| `IMEM_SIZE` | 8 KB | Stage2 loader fits in 8 KB |
| `DMEM_SIZE` | 8 KB | Kernel stack/heap scratch space |
| `IO_UART0_RX_FIFO` | 4 | 2^4 = 16-entry FIFO for console input |
| `IO_UART0_TX_FIFO` | 4 | 2^4 = 16-entry FIFO for console output |

## Project Structure

```
see_neorv32_run_linux/
├── tools/openFPGALoader/  — openFPGALoader source (build from source)
├── neorv32/               — NEORV32 RTL source (v1.12.8)
├── linux-6.6.83.tar.xz   — Linux kernel tarball
├── rtl/                   — Custom FPGA design
│   ├── ax301_top.vhd      — top-level: NEORV32 + SDRAM + UART + GPIO
│   ├── wb_sdram_ctrl.v    — Wishbone → SDRAM bridge
│   └── sdram_ctrl.v       — SDRAM controller FSM (CL=3, 50 MHz)
├── quartus/               — Quartus project files (.qsf, .qpf, .sdc)
├── kernel/
│   └── neorv32_nommu.patch — 16 kernel patches (vs vanilla 6.6.83)
├── board/                 — Board support files
│   ├── neorv32_ax301.dts   — device tree source
│   ├── linux_defconfig     — kernel config
│   ├── neorv32_uart.c      — custom UART driver source
│   ├── inject_driver.sh    — injects driver into kernel tree
│   └── buildroot_defconfig — Buildroot config (alternative build)
├── sw/
│   ├── stage2_loader/      — xmodem boot loader (C, runs from IMEM)
│   │   ├── main.c
│   │   └── Makefile        — uses NEORV32 common.mk
│   └── initramfs/          — minimal /init for Linux
│       ├── init.c           — custom shell (syscalls only, no libc)
│       └── Makefile
├── host/                  — Python host scripts
│   ├── boot_linux.py       — full boot sequence (program + upload + console)
│   └── test_shell.py       — shell command tester
├── output/                — build outputs (populated by build steps)
└── BUILD_LOG.md           — 105-build debugging chronicle
```

## Resource Usage (EP4CE6, 50 MHz)

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic Elements | 4,600 | 6,272 | 73% |
| Memory bits | 168,960 | 276,480 | 61% |
| Registers | 2,408 | 6,272 | 38% |
| Embedded Multipliers | 0 | 30 | 0% |

## Known Issues

- **Kernel must be built with xPack `riscv-none-elf-gcc` 14.2.0:** Building the kernel with Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 produces a binary that hangs in `free_initmem()` — the last debug marker `L` (system_state = RUNNING) prints, but execution never reaches `M` (after free_initmem). This happens despite identical source code, patches, kernel config, and FPGA bitstream. The only variable is the compiler. Root cause is a subtle code generation difference in GCC 12.4.0 that triggers a hang on NEORV32's constrained environment. The Buildroot-built kernel also runs noticeably slower overall (clocksource switch at 13.8s vs 7.4s, triggers `sched: RT throttling activated`). **Always use the xPack bare-metal toolchain for the kernel.**
- **Boot time ~98s:** Mostly spent in driver probing and async work queue timeouts. The 50 MHz single-issue core is genuinely slow for kernel init.
- **SDRAM intermittent init failure:** Occasionally fails on first power-on. Power-cycle the board (off for a few seconds) to resolve.
- **Shell is minimal:** Only `uname`, `info`, `amo`, `help`, `exit`. The init binary is a custom C program, not busybox (to keep initramfs tiny).
- **No network, no storage:** This is a bare UART console. The EP4CE6 has no room for additional peripherals.

## License

- NEORV32 RTL: BSD 3-Clause (see [NEORV32 repo](https://github.com/stnolting/neorv32))
- Linux kernel patches: GPL-2.0 (same as the kernel)
- SDRAM controller, host scripts, stage2 loader: MIT
