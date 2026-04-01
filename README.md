# See NEORV32 Run Linux

Booting nommu Linux (kernel 6.6.83) on a **NEORV32** RV32IMC soft-core FPGA — believed to be the first successful Linux boot on NEORV32.

The NEORV32 is a microcontroller-class processor with **no MMU**, **no S-mode**, and **no atomic instructions**. Getting Linux to run on it required 22 patches across the kernel's arch/riscv, scheduler, RCU, init, and driver subsystems.

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

## Quick Start (pre-built binaries)

Everything needed to boot is in `output/`. No compilation required.

```bash
# 1. Program FPGA
openFPGALoader -c usb-blaster output/neorv32_demo.rbf

# 2. Boot Linux (~20s transfer + ~118s kernel boot)
pip install pyserial
python3 host/boot_linux.py --port /dev/ttyUSB0 --skip-program
```

The boot script handles the full sequence: NEORV32 bootloader → stage2 xmodem loader → kernel + DTB + initramfs transfer → Linux console.

## Boot Sequence

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

## Memory Map

| Address | Size | Description |
|---------|------|-------------|
| `0x00000000` | 8 KB | IMEM (M9K BRAM, stage2 loader) |
| `0x40000000` | 32 MB | SDRAM (kernel + data) |
| `0x80000000` | 8 KB | DMEM (M9K BRAM) |
| `0xFFE00000` | ~4 KB | Boot ROM (NEORV32 bootloader) |
| `0xFFF40000` | — | CLINT (mtime + mtimecmp) |
| `0xFFF50000` | — | UART0 (console) |

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

### Applying the Patch

```bash
# Download vanilla kernel
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.83.tar.xz
tar xf linux-6.6.83.tar.xz
cd linux-6.6.83

# Apply patch
patch -p1 < ../kernel/neorv32_nommu.patch

# Copy defconfig
cp ../board/linux_defconfig arch/riscv/configs/neorv32_ax301_defconfig

# Build (requires riscv32 cross-compiler)
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)

# Output: arch/riscv/boot/Image
```

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
| `IO_UART0_RX_FIFO` | 4 | 16-entry FIFO for console input |

### Building the Bitstream

Requires [NEORV32](https://github.com/stnolting/neorv32) RTL sources and Intel Quartus Prime Lite 21.1+.

```bash
# Clone NEORV32 into project root
git clone https://github.com/stnolting/neorv32.git

# Build
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off neorv32_demo.sof ../output/neorv32_demo.rbf
```

## Project Structure

```
see_neorv32_run_linux/
├── README.md              — this file
├── BUILD_LOG.md           — 105-build debugging chronicle
├── rtl/                   — FPGA design (VHDL/Verilog)
│   ├── ax301_top.vhd      — top-level: NEORV32 + SDRAM + UART + GPIO
│   ├── wb_sdram_ctrl.v    — Wishbone → SDRAM bridge
│   └── sdram_ctrl.v       — SDRAM controller FSM
├── quartus/               — Quartus project files
├── sw/stage2_loader/      — xmodem boot loader (runs from IMEM)
├── host/                  — Python host scripts
│   ├── boot_linux.py       — full boot sequence
│   └── test_shell.py      — shell command tester
├── board/                 — board support files
│   ├── neorv32_ax301.dts   — device tree source
│   ├── linux_defconfig     — kernel config
│   ├── neorv32_uart.c      — custom UART driver source
│   ├── inject_driver.sh    — script to inject driver into kernel tree
│   ├── buildroot_defconfig — Buildroot config (alternative build method)
│   └── post-build.sh
├── kernel/
│   └── neorv32_nommu.patch — all kernel patches (vs vanilla 6.6.83)
└── output/                — pre-built binaries (ready to boot)
    ├── neorv32_demo.rbf    — FPGA bitstream (368 KB)
    ├── stage2_loader.bin   — xmodem boot loader (3.7 KB)
    ├── Image               — Linux kernel (1.4 MB)
    ├── neorv32_ax301.dtb   — compiled device tree (1.4 KB)
    └── neo_initramfs.cpio.gz — root filesystem (1.7 KB)
```

## Resource Usage (EP4CE6, 50 MHz)

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic Elements | ~4,100 | 6,272 | 65% |
| Memory bits | ~230,000 | 276,480 | 83% |
| Embedded Multipliers | 0 | 30 | 0% |

## Known Issues

- **Boot time ~118s:** Mostly spent in driver probing and async work queue timeouts. The 50 MHz single-issue core is genuinely slow for kernel init.
- **SDRAM intermittent init failure:** Occasionally fails on first power-on. Power-cycle the board (off for a few seconds) to resolve.
- **Shell is minimal:** Only `uname`, `info`, `help`, `exit`. The init binary is a custom C program, not busybox (to keep initramfs tiny).
- **No network, no storage:** This is a bare UART console. The EP4CE6 has no room for additional peripherals.

## License

- NEORV32 RTL: BSD 3-Clause (see [NEORV32 repo](https://github.com/stnolting/neorv32))
- Linux kernel patches: GPL-2.0 (same as the kernel)
- SDRAM controller, host scripts, stage2 loader: MIT
