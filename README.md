# See NEORV32 Run Linux

Booting nommu Linux (kernel 6.6.83) on a **NEORV32** RV32IMC soft-core FPGA — believed to be the first successful Linux boot on NEORV32.

The NEORV32 is a microcontroller-class processor with **no MMU**, **no S-mode**, and **no atomic instructions**. Getting Linux to run on it required 22 patches across the kernel's arch/riscv, scheduler, RCU, init, and driver subsystems.

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
| **Board** | Heijin (黑金) AX301 |
| **FPGA** | Altera Cyclone IV E EP4CE6F17C8 (6,272 LEs) |
| **CPU** | NEORV32 RV32IMC, 50 MHz, M-mode only |
| **RAM** | 32 MB SDRAM (HY57V2562GTR) |
| **UART** | PL2303 USB-UART, 115200 baud |
| **Programmer** | USB-Blaster via openFPGALoader |

## Demo

```
[stage2] Linux direct boot mode
[4] Sending kernel (1,451,452 bytes) via xmodem...
  [xmodem] Transfer complete
  [kernel] CRC MATCH: xxxxxxxx ✓
...
[    0.000000] Linux version 6.6.83 (riscv32)
[    0.000000] Kernel command line: earlycon=neorv32,0xfff50000 console=ttyNEO0,115200
[    0.000000] Memory: 30972K/32768K available (1035K kernel code, ...)
[   86.283041] printk: console [ttyNEO0] enabled
[  118.106999] Freeing unused kernel image (initmem) memory: 96K
[  118.219101] Run /init as init process

========================================
 NEORV32 nommu Linux — mini shell
========================================
Linux (none) 6.6.83 riscv32
Uptime:    118 s
Total RAM: 31068 KB
Free RAM:  30548 KB
Processes: 13

Type 'help' for commands.

nommu# uname
Linux (none) 6.6.83-g7d6073865396-dirty #105 riscv32

nommu# info
Uptime:    310 s
Total RAM: 31068 KB
Free RAM:  30548 KB
Processes: 13
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

## System Architecture

### Block Diagram

```
  AX301 Board (EP4CE6, 50 MHz)
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │  ┌───────────────────────────────────────────────┐  │
  │  │            NEORV32 SoC (RV32IMC)              │  │
  │  │                                               │  │
  │  │  ┌───────┐  ┌──────┐  ┌──────┐  ┌─────────┐  │  │
  │  │  │  CPU  │  │ IMEM │  │ DMEM │  │ Boot ROM│  │  │
  │  │  │RV32IMC│  │ 8 KB │  │ 8 KB │  │  ~4 KB  │  │  │
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
  ↓ xmodem: kernel Image (1.4 MB) → SDRAM 0x40000000, CRC-32 verify
  ↓ xmodem: DTB (1.4 KB)          → SDRAM 0x41F00000, CRC-32 verify
  ↓ xmodem: initramfs (1.7 KB)    → SDRAM 0x41F80000, CRC-32 verify
  ↓ jump to 0x40000000 with a0=hartid, a1=DTB pointer
Linux kernel (M-mode, nommu)
  ↓ ~118s boot → /init (mini shell from initramfs)
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
- Available after boot: 30,972 KB (~30 MB free)
- Kernel code: 1,035 KB | RW data: 125 KB | RO data: 155 KB | Init: 96 KB | BSS: 49 KB

## Why Is This Hard?

NEORV32 is a microcontroller core — it was never designed to run Linux. Here's what we had to work around:

### 1. No Atomic Instructions

NEORV32 has no LR/SC (load-reserved / store-conditional) and no AMO (atomic memory operations). The Linux kernel uses these **everywhere**: spinlocks, atomic counters, cmpxchg, bitops.

**Solution:** Replace all LR/SC sequences with IRQ-safe load/modify/store. This is safe because NEORV32 is single-core and we disable interrupts around critical sections. Files: `cmpxchg.h`, `atomic.h`, `bitops.h`.

### 2. No MMU, No S-mode

Linux normally runs in S-mode (supervisor) with virtual memory. NEORV32 only has M-mode (machine) and U-mode. We run the kernel directly in M-mode using the `nommu` configuration.

**Key config:** `CONFIG_MMU=n`, `CONFIG_PAGE_OFFSET=0x40000000` (must match physical RAM base).

### 3. Scheduler Deadlocks

The kernel scheduler uses `need_resched` loops that rely on atomic test-and-set operations. Without atomics, these become infinite ping-pong loops between threads.

**Solution:** Modified `schedule()` and `preempt_schedule_common()` to execute `__schedule()` exactly once instead of looping on `need_resched`. Added `schedule_preempt_disabled_once()` for kthread startup.

### 4. `wfi` Halts the CPU

NEORV32's `wfi` (wait-for-interrupt) instruction halts the CPU if no interrupt is pending. The kernel uses `wfi` in idle loops, which can permanently freeze the system.

**Solution:** `wfi` → `nop` in `arch/riscv/include/asm/processor.h`.

### 5. RISCV_ALTERNATIVE Patching Conflict

The kernel's alternative instruction patching framework (`RISCV_ALTERNATIVE`) replaces instructions at runtime based on detected ISA extensions. This conflicts with our non-atomic replacements — after `free_initmem()`, the CPU jumps into the `.alternative` section and executes data as code (illegal instruction trap at `epc=0x4011c002`).

**Solution:** Disable `RISCV_ALTERNATIVE` entirely in `arch/riscv/Kconfig`. This was the final fix (Build #105) after 104 failed attempts.

### 6. RCU / Work Queue Stalls

Single-threaded RCU (`srcutiny`) and async work queues assume preemptive scheduling works correctly. On our non-atomic core, grace periods never complete and `synchronize_srcu()` hangs forever.

**Solution:** Synchronous grace period in `srcutiny.c`, synchronous `populate_rootfs()` in `initramfs.c`, 120s timeout for `async_synchronize_full()`.

### 7. UART Driver

NEORV32's UART is not supported by any upstream Linux driver. We wrote a custom `neorv32_uart.c` tty driver with kthread-based polling (no IRQ) and direct line discipline delivery.

## Kernel Patches

All patches are in `kernel/neorv32_nommu.patch` (2,280 lines, 15 files modified against vanilla 6.6.83).

### Files Modified

| File | Change |
|------|--------|
| `arch/riscv/Kconfig` | Disable `RISCV_ALTERNATIVE` |
| `arch/riscv/include/asm/cmpxchg.h` | LR/SC → non-atomic load/store |
| `arch/riscv/include/asm/atomic.h` | AMO → non-atomic C operations |
| `arch/riscv/include/asm/bitops.h` | AMO bitops → IRQ-safe load/modify/store |
| `arch/riscv/include/asm/processor.h` | `wfi` → `nop` |
| `kernel/sched/core.c` | Single-shot `__schedule()`, no `need_resched` loop |
| `kernel/kthread.c` | Use `schedule_preempt_disabled_once()` |
| `kernel/rcu/srcutiny.c` | Synchronous SRCU grace period |
| `kernel/async.c` | 120s timeout for `async_synchronize_full()` |
| `include/linux/sched.h` | Declare `schedule_preempt_disabled_once()` |
| `init/main.c` | SCHED_FIFO boost for init, disable initmem poison |
| `init/initramfs.c` | Synchronous `populate_rootfs()` |
| `fs/exec.c` | Boot-time debug markers (non-functional) |
| `fs/binfmt_elf_fdpic.c` | Boot-time debug markers (non-functional) |
| `drivers/tty/serial/neorv32_uart.c` | **New file** — custom UART driver |

## FPGA RTL

The NEORV32 is configured with these generics in `rtl/ax301_top.vhd`:

| Generic | Value | Why |
|---------|-------|-----|
| `RISCV_ISA_C` | true | Compressed instructions (smaller code) |
| `RISCV_ISA_M` | true | Hardware multiply/divide |
| `RISCV_ISA_U` | true | U-mode for Linux userspace |
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
│   └── neorv32_nommu.patch — 22 kernel patches (vs vanilla 6.6.83)
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
| Logic Elements | 4,592 | 6,272 | 73% |
| Memory bits | 168,960 | 276,480 | 61% |
| Registers | 2,354 | 6,272 | 38% |
| Embedded Multipliers | 0 | 30 | 0% |

## Known Issues

- **Kernel must be built with xPack `riscv-none-elf-gcc` 14.2.0:** Building the kernel with Buildroot's `riscv32-buildroot-linux-gnu-gcc` 12.4.0 produces a binary that hangs in `free_initmem()` — the last debug marker `L` (system_state = RUNNING) prints, but execution never reaches `M` (after free_initmem). This happens despite identical source code, patches, kernel config, and FPGA bitstream. The only variable is the compiler. Root cause is a subtle code generation difference in GCC 12.4.0 that triggers a hang on NEORV32's constrained environment. The Buildroot-built kernel also runs noticeably slower overall (clocksource switch at 13.8s vs 7.4s, triggers `sched: RT throttling activated`). **Always use the xPack bare-metal toolchain for the kernel.**
- **Boot time ~118s:** Mostly spent in driver probing and async work queue timeouts. The 50 MHz single-issue core is genuinely slow for kernel init.
- **SDRAM intermittent init failure:** Occasionally fails on first power-on. Power-cycle the board (off for a few seconds) to resolve.
- **Shell is minimal:** Only `uname`, `info`, `help`, `exit`. The init binary is a custom C program, not busybox (to keep initramfs tiny).
- **No network, no storage:** This is a bare UART console. The EP4CE6 has no room for additional peripherals.

## License

- NEORV32 RTL: BSD 3-Clause (see [NEORV32 repo](https://github.com/stnolting/neorv32))
- Linux kernel patches: GPL-2.0 (same as the kernel)
- SDRAM controller, host scripts, stage2 loader: MIT
