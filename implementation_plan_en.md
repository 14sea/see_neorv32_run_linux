# Nommu Linux Port to NEORV32 AX301 — Detailed Implementation Plan

## Overview

Port nommu Linux (M-mode) to the NEORV32 RV32IMAC soft-core on the Heijin AX301, using 32 MB SDRAM, reaching a BusyBox shell via UART console.

### Current Hardware Status

| Component | Address | Status |
|-----------|---------|--------|
| IMEM | `0x00000000` (8 KB) | ✅ Available |
| SDRAM | `0x40000000` (32 MB) | ✅ Verified |
| DMEM | `0x80000000` (8 KB) | ✅ Available |
| CLINT | `0xFFF40000` | ✅ Enabled |
| UART0 | `0xFFF50000` | ✅ Enabled |
| GPIO | `0xFFFC0000` | ✅ Enabled |
| SPI | `0xFFF80000` | ❌ Not enabled |
| ICACHE | 64B blocks, non-burst | ✅ Enabled |
| DCACHE | — | ❌ Not enabled |
| U-Boot | SDRAM `0x40000000` | ✅ Running |
| FPGA LE Usage | 4,136 / 6,272 (66%) | ⚠️ 34% remaining |

---

## Phase 1: RTL Hardware Preparation

### 1.1 Verify CLINT Compatibility (No Changes Needed)

> [!TIP]
> This step does not require any RTL modifications — just verification.

The Linux `riscv,clint0` driver expects the following CLINT memory layout:

```
BASE + 0x0000 : MSIP[0]     (4 bytes, software interrupt pending)
BASE + 0x0004 : MSIP[1]     (next hart, ignore)
...
BASE + 0x4000 : MTIMECMP[0] (8 bytes, timer compare value for hart 0)
BASE + 0x4008 : MTIMECMP[1] (hart 1, ignore)
...
BASE + 0xBFF8 : MTIME       (8 bytes, global timer)
```

NEORV32's CLINT structure ([neorv32_clint.h](neorv32/sw/lib/include/neorv32_clint.h#L25-L29)):

```c
typedef volatile struct {
  uint32_t    MSWI[4096];        // offset 0x0000, 4096 x 4 = 0x4000 bytes
  subwords64_t MTIMECMP[4095];   // offset 0x4000, 4095 x 8 = 0x7FF8 bytes
  subwords64_t MTIME;            // offset 0x4000 + 0x7FF8 = 0xBFF8
} neorv32_clint_t;
```

**Layout calculation:**
- `MSWI[0]` at `BASE + 0x0000` → ✅ Maps to Linux's `MSIP[0]`
- `MTIMECMP[0]` at `BASE + 4096*4 = BASE + 0x4000` → ✅ Exact match
- `MTIME` at `BASE + 0x4000 + 4095*8 = BASE + 0xBFF8` → ✅ Exact match

> [!IMPORTANT]
> **The CLINT layout is 100% compatible with Linux's `riscv,clint0` driver! No modifications needed.**
> Your CLINT base address is `0xFFF40000`, so:
> - MSIP[0] = `0xFFF40000`
> - MTIMECMP[0] = `0xFFF44000`
> - MTIME = `0xFFF4BFF8`

**Verification method** (test in U-Boot):
```
U-Boot> md 0xFFF4BFF8 2    # Read MTIME, should see an incrementing value
U-Boot> md 0xFFF4BFF8 2    # Read again, value should be larger
```

---

### 1.2 Increase UART TX/RX FIFO (Recommended)

Current FIFO depth configuration is 1 ([ax301_top.vhd](rtl/ax301_top.vhd#L139-L140)):
```vhdl
IO_UART0_RX_FIFO => 1,   -- FIFO depth = 2^1 = 2 entries
IO_UART0_TX_FIFO => 1,   -- FIFO depth = 2^1 = 2 entries
```

The Linux kernel outputs a large amount of text during boot. A shallow FIFO will cause UART to become a bottleneck, slowing down the entire boot process.

**Recommended change** to `rtl/ax301_top.vhd`:
```vhdl
IO_UART0_RX_FIFO => 4,   -- FIFO depth = 2^4 = 16 entries
IO_UART0_TX_FIFO => 4,   -- FIFO depth = 2^4 = 16 entries
```

> [!NOTE]
> Each additional FIFO level consumes approximately 16-32 LEs. Going from 2^1 to 2^4 uses roughly 100-200 extra LEs. You have ~2100 LEs remaining — plenty of headroom.

---

### 1.3 Enable DCACHE (Strongly Recommended)

Currently only ICACHE is enabled. Every data bus access to SDRAM goes directly through XBUS → wb_sdram_ctrl → SDRAM. Linux kernel data (stack, page table structures, kmalloc allocations, etc.) frequently reads/writes SDRAM — without DCACHE, this will be **extremely slow**.

**Change** in `rtl/ax301_top.vhd`:
```vhdl
DCACHE_EN        => true,    -- Enable DCACHE
-- DCACHE defaults to the same configuration as ICACHE:
-- CACHE_BLOCK_SIZE => 64 (16 words per line)
-- Direct-mapped, 1K or 2K cache
```

> [!WARNING]
> **DCACHE resource consumption estimate:**
> - DCACHE requires additional M9K blocks for tag + data storage
> - Current M9K usage is 60% (166,912 / 276,480 bits)
> - A 1KB DCACHE needs approximately 8,192 bits (1 M9K block) for data + ~1K for tags
> - If M9K blocks are insufficient, you may need to reduce IMEM (from 8KB to 4KB)
>
> **Try enabling DCACHE first and see if Quartus compilation passes. If M9K blocks are insufficient, then consider trade-offs.**

---

### 1.4 (Optional) Enable SPI

If you want to boot Linux from SPI Flash or SD card in later phases (avoiding xmodem each time), you can enable SPI:

```vhdl
IO_SPI_EN => true,
```

SPI consumes approximately 200-300 LEs. With 2100+ LEs remaining, this is feasible.
However, **do not enable it initially** — wait until Linux is running, then consider it.

---

### 1.5 RTL Changes Summary

#### [MODIFY] [ax301_top.vhd](rtl/ax301_top.vhd)

```diff
  -- Peripherals: only what we need
  IO_GPIO_NUM      => 4,
  IO_CLINT_EN      => true,
  IO_UART0_EN      => true,
- IO_UART0_RX_FIFO => 1,
- IO_UART0_TX_FIFO => 1,
+ IO_UART0_RX_FIFO => 4,   -- 16-entry FIFO for Linux console
+ IO_UART0_TX_FIFO => 4,   -- 16-entry FIFO for Linux console
  -- Everything else off
  IO_SPI_EN        => false,
  ...
  ICACHE_EN        => true,
  CACHE_BLOCK_SIZE => 64,
  CACHE_BURSTS_EN  => false,
- DCACHE_EN        => false
+ DCACHE_EN        => true   -- D-cache critical for SDRAM Linux performance
```

**After completing: recompile bitstream**
```bash
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off neorv32_demo.sof ../neorv32_demo_linux.rbf
```

**Verification:** After reflashing, confirm U-Boot still boots normally with `boot_uboot.py`.

---

## Phase 2: Build Linux Cross-Compilation Environment and QEMU Validation

### 2.1 Install Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    git make gcc g++ bison flex libncurses-dev \
    bc cpio rsync unzip python3 perl wget \
    qemu-system-misc libssl-dev
```

### 2.2 Download Buildroot

```bash
git clone https://github.com/buildroot/buildroot.git
cd buildroot
git checkout 2024.02.x   # LTS branch, stable
```

### 2.3 Run RV32 Nommu Linux on QEMU

```bash
# Use the existing RISC-V 32 nommu configuration
make qemu_riscv32_nommu_virt_defconfig

# Build (approximately 30-60 minutes)
make -j$(nproc)

# Verify build succeeded
ls -la output/images/
# Should see: Image, loader, rootfs.cpio, etc.

# Launch QEMU
output/host/bin/qemu-system-riscv32 \
    -M virt -m 128M -nographic \
    -kernel output/images/loader \
    -device loader,file=output/images/Image,addr=0x80400000
```

**Success indicator:** See Linux boot log, ending with `~ #` BusyBox shell prompt.

> [!IMPORTANT]
> **You must succeed on QEMU before proceeding to the next step.** If it doesn't work on QEMU, it certainly won't work on hardware. Key learnings from the QEMU phase:
> - Understand `make menuconfig` kernel configuration
> - Understand Buildroot directory structure: `output/build/linux-xxx/` is the kernel source
> - Understand how DT (Device Tree) describes hardware

### 2.4 Study the QEMU Nommu Configuration

After QEMU boots successfully, extract key configurations for reference:

```bash
# View kernel .config
cat output/build/linux-*/\.config | grep -E "RISCV|MMU|CLINT|NOMMU|M_MODE|UART"

# View QEMU virt DT
# Inside QEMU:
cat /proc/device-tree/compatible
cat /sys/firmware/devicetree/base/chosen/bootargs
```

---

## Phase 3: Create Buildroot External Tree for NEORV32

### 3.1 Directory Structure

```bash
mkdir -p ${PROJECT_DIR}
cd ${PROJECT_DIR}

mkdir -p board/neorv32_ax301/
mkdir -p configs/
mkdir -p linux/
mkdir -p package/neorv32-uart-driver/
```

Final structure:
```
${PROJECT_DIR}/
├── board/neorv32_ax301/
│   ├── neorv32_ax301.dts          ← Device Tree source
│   ├── linux.config               ← Kernel config fragment
│   └── post-build.sh              ← Post-build script
├── configs/
│   └── neorv32_ax301_defconfig    ← Buildroot configuration
├── linux/
│   └── linux.config               ← Kernel defconfig override
└── Config.in                       ← Buildroot external tree
```

### 3.2 Device Tree (Core File)

Create `board/neorv32_ax301/neorv32_ax301.dts`:

```dts
/dts-v1/;

/ {
    #address-cells = <1>;
    #size-cells = <1>;
    compatible = "neorv32,ax301";
    model = "NEORV32 on AX301 (EP4CE6)";

    chosen {
        bootargs = "earlycon=neorv32,0xfff50000 console=ttyNEO0,115200";
        stdout-path = &uart0;
    };

    cpus {
        #address-cells = <1>;
        #size-cells = <0>;
        timebase-frequency = <50000000>;  /* MTIME counts at CPU clock frequency */

        cpu0: cpu@0 {
            device_type = "cpu";
            compatible = "riscv";
            riscv,isa = "rv32imac";
            riscv,isa-base = "rv32i";
            riscv,isa-extensions = "i", "m", "a", "c", "zicsr", "zifencei";
            mmu-type = "riscv,none";      /* No MMU = nommu */
            clock-frequency = <50000000>;
            reg = <0>;

            cpu0_intc: interrupt-controller {
                #interrupt-cells = <1>;
                compatible = "riscv,cpu-intc";
                interrupt-controller;
            };
        };
    };

    memory@40000000 {
        device_type = "memory";
        reg = <0x40000000 0x02000000>;    /* 32 MB SDRAM */
    };

    /* CLINT — 100% compatible with SiFive CLINT layout */
    clint: clint@fff40000 {
        compatible = "riscv,clint0";
        reg = <0xfff40000 0x10000>;       /* 64 KB space */
        interrupts-extended = <&cpu0_intc 3>,   /* M-mode software interrupt */
                              <&cpu0_intc 7>;   /* M-mode timer interrupt */
    };

    /* UART0 — uses custom NEORV32 UART driver */
    uart0: serial@fff50000 {
        compatible = "neorv32,uart";
        reg = <0xfff50000 0x08>;          /* CTRL + DATA = 8 bytes */
        clock-frequency = <50000000>;
        current-speed = <115200>;
        /*
         * UART0 uses FIRQ #2 (cpu_firq[2])
         * FIRQ maps to mie/mip bits [16+n]
         * In M-mode, external interrupts go through mcause
         * Using polling initially; interrupt support to be added later
         */
    };

    /* GPIO — 4-bit, LED control */
    gpio: gpio@fffc0000 {
        compatible = "neorv32,gpio";
        reg = <0xfffc0000 0x08>;
        /* No GPIO driver needed initially, used for debugging only */
    };
};
```

### 3.3 Linux Kernel Configuration Fragment

Create `board/neorv32_ax301/linux.config`:

```
# ── Architecture ──
CONFIG_RISCV=y
CONFIG_ARCH_RV32I=y
CONFIG_RISCV_ISA_C=y
CONFIG_RISCV_ISA_M=y

# ── M-mode Nommu ──
CONFIG_MMU=n
CONFIG_RISCV_M_MODE=y

# ── Memory ──
CONFIG_PAGE_OFFSET=0x40000000
CONFIG_PHYS_RAM_BASE=0x40000000
CONFIG_DEFAULT_MEM_START=0x40000000
CONFIG_DEFAULT_MEM_SIZE=0x02000000

# ── Timer ──
CONFIG_CLINT_TIMER=y

# ── Console (initially use earlycon polling) ──
CONFIG_SERIAL_EARLYCON=y
CONFIG_EARLY_PRINTK=y
CONFIG_PRINTK=y

# ── Filesystem ──
CONFIG_BLK_DEV_INITRD=y
CONFIG_INITRAMFS_SOURCE=""
# Buildroot will set initramfs automatically

# ── Minimal config (disable unnecessary features) ──
CONFIG_NET=n
CONFIG_BLOCK=n
CONFIG_MODULES=n
CONFIG_SMP=n
CONFIG_SWAP=n
CONFIG_PROC_FS=y
CONFIG_SYSFS=y
CONFIG_TMPFS=y

# ── Debugging ──
CONFIG_DEBUG_KERNEL=y
CONFIG_DEBUG_INFO=y
CONFIG_PANIC_TIMEOUT=0
CONFIG_STACKTRACE=y
```

### 3.4 Buildroot defconfig

Create `configs/neorv32_ax301_defconfig`:

```
# Architecture
BR2_riscv=y
BR2_RISCV_32=y
BR2_riscv_custom=y
BR2_RISCV_ISA_RVM=y
BR2_RISCV_ISA_RVA=y
BR2_RISCV_ISA_RVC=y
BR2_RISCV_ABI_ILP32=y

# Toolchain
BR2_TOOLCHAIN_BUILDROOT=y
BR2_TOOLCHAIN_BUILDROOT_MUSL=y

# Kernel
BR2_LINUX_KERNEL=y
BR2_LINUX_KERNEL_CUSTOM_VERSION=y
BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="6.6"
BR2_LINUX_KERNEL_USE_CUSTOM_CONFIG=y
BR2_LINUX_KERNEL_CUSTOM_CONFIG_FILE="$(BR2_EXTERNAL_NEORV32_PATH)/board/neorv32_ax301/linux.config"
BR2_LINUX_KERNEL_DTS_SUPPORT=y
BR2_LINUX_KERNEL_CUSTOM_DTS_PATH="$(BR2_EXTERNAL_NEORV32_PATH)/board/neorv32_ax301/neorv32_ax301.dts"

# Root filesystem
BR2_TARGET_ROOTFS_CPIO=y
BR2_TARGET_ROOTFS_INITRAMFS=y

# BusyBox (default config is fine)
BR2_PACKAGE_BUSYBOX=y

# Misc
BR2_TARGET_GENERIC_HOSTNAME="neorv32"
BR2_TARGET_GENERIC_ISSUE="Welcome to NEORV32 Linux"
BR2_SYSTEM_DEFAULT_PATH="/bin:/sbin:/usr/bin:/usr/sbin"
```

---

## Phase 4: Linux Kernel Driver Development

### 4.1 UART earlycon (Implement First — Most Critical)

earlycon is the kernel's earliest output channel during boot. Without it, you won't see any boot log — essentially debugging blind.

earlycon only needs a `putchar()` function, **no interrupts**, pure polling.

NEORV32 UART register layout ([neorv32_uart.h](neorv32/sw/lib/include/neorv32_uart.h#L26-L29)):
```
offset 0x00: CTRL register (32-bit)
  bit 0:     EN (global enable)
  bit 18:    TX_EMPTY (TX FIFO empty)
  bit 19:    TX_NFULL (TX FIFO not full)
  bit 16:    RX_NEMPTY (RX FIFO not empty)
  bit 31:    TX_BUSY (transmitting)

offset 0x04: DATA register (32-bit)
  bit [7:0]: read = received byte, write = byte to transmit
```

**earlycon driver implementation** — create `drivers/tty/serial/earlycon-neorv32.c`:

```c
// SPDX-License-Identifier: GPL-2.0
/*
 * Early console driver for NEORV32 UART
 */
#include <linux/console.h>
#include <linux/init.h>
#include <linux/serial_core.h>

#define NEORV32_UART_CTRL    0x00
#define NEORV32_UART_DATA    0x04
#define UART_CTRL_TX_NFULL   (1 << 19)

static void neorv32_earlycon_putchar(struct uart_port *port, unsigned char ch)
{
    /* Wait for TX FIFO to have space */
    while (!(readl(port->membase + NEORV32_UART_CTRL) & UART_CTRL_TX_NFULL))
        ;
    writel(ch, port->membase + NEORV32_UART_DATA);
}

static void neorv32_earlycon_write(struct console *con,
                                    const char *s, unsigned int n)
{
    struct earlycon_device *dev = con->data;
    uart_console_write(&dev->port, s, n, neorv32_earlycon_putchar);
}

static int __init neorv32_earlycon_setup(struct earlycon_device *dev,
                                          const char *options)
{
    if (!dev->port.membase)
        return -ENODEV;

    dev->con->write = neorv32_earlycon_write;
    return 0;
}

EARLYCON_DECLARE(neorv32, neorv32_earlycon_setup);
OF_EARLYCON_DECLARE(neorv32, "neorv32,uart", neorv32_earlycon_setup);
```

### 4.2 Full UART tty Driver

The full driver needs to implement `struct uart_ops`. **Initially use pure polling**, no interrupts:

Create `drivers/tty/serial/neorv32_uart.c`:

```c
// SPDX-License-Identifier: GPL-2.0
/*
 * NEORV32 UART serial driver (polling mode)
 */
#include <linux/console.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/serial_core.h>
#include <linux/tty_flip.h>

#define DRIVER_NAME     "neorv32-uart"
#define DEV_NAME        "ttyNEO"
#define NEORV32_UART_NR 1

/* Register offsets */
#define NEORV32_UART_CTRL    0x00
#define NEORV32_UART_DATA    0x04

/* CTRL register bits */
#define CTRL_EN              (1 << 0)
#define CTRL_PRSC_SHIFT      3
#define CTRL_PRSC_MASK       (0x7 << CTRL_PRSC_SHIFT)
#define CTRL_BAUD_SHIFT      6
#define CTRL_BAUD_MASK       (0x3FF << CTRL_BAUD_SHIFT)
#define CTRL_RX_NEMPTY       (1 << 16)
#define CTRL_RX_FULL         (1 << 17)
#define CTRL_TX_EMPTY        (1 << 18)
#define CTRL_TX_NFULL        (1 << 19)
#define CTRL_TX_BUSY         (1 << 31)

/* DATA register */
#define DATA_MASK            0xFF

/* ----- uart_ops callbacks ----- */

static unsigned int neorv32_tx_empty(struct uart_port *port)
{
    return (readl(port->membase + NEORV32_UART_CTRL) & CTRL_TX_EMPTY)
           ? TIOCSER_TEMT : 0;
}

static void neorv32_set_mctrl(struct uart_port *port, unsigned int mctrl) {}
static unsigned int neorv32_get_mctrl(struct uart_port *port) { return TIOCM_CAR; }
static void neorv32_stop_tx(struct uart_port *port) {}
static void neorv32_stop_rx(struct uart_port *port) {}

static void neorv32_start_tx(struct uart_port *port)
{
    struct circ_buf *xmit = &port->state->xmit;
    while (!uart_circ_empty(xmit)) {
        if (!(readl(port->membase + NEORV32_UART_CTRL) & CTRL_TX_NFULL))
            break;
        writel(xmit->buf[xmit->tail], port->membase + NEORV32_UART_DATA);
        uart_xmit_advance(port, 1);
    }
}

static int neorv32_startup(struct uart_port *port)
{
    /* UART already initialized by U-Boot; just ensure EN bit is set */
    uint32_t ctrl = readl(port->membase + NEORV32_UART_CTRL);
    writel(ctrl | CTRL_EN, port->membase + NEORV32_UART_CTRL);
    return 0;
}

static void neorv32_shutdown(struct uart_port *port) {}

static void neorv32_set_termios(struct uart_port *port,
                                 struct ktermios *new,
                                 const struct ktermios *old)
{
    /* Dynamic baud rate change not supported yet; uses 115200 set by U-Boot */
    unsigned int baud = uart_get_baud_rate(port, new, old, 9600, 115200);
    uart_update_timeout(port, new->c_cflag, baud);
}

static const char *neorv32_type(struct uart_port *port)
{
    return "NEORV32_UART";
}

static void neorv32_config_port(struct uart_port *port, int flags)
{
    port->type = PORT_UNKNOWN;  /* or define a PORT_NEORV32 */
}

/* Polling timer callback: check RX */
static void neorv32_poll_get_char(struct uart_port *port)
{
    uint32_t ctrl = readl(port->membase + NEORV32_UART_CTRL);
    if (ctrl & CTRL_RX_NEMPTY) {
        uint32_t data = readl(port->membase + NEORV32_UART_DATA);
        unsigned char ch = data & DATA_MASK;
        uart_insert_char(port, 0, 0, ch, TTY_NORMAL);
        tty_flip_buffer_push(&port->state->port);
    }
}

static const struct uart_ops neorv32_uart_ops = {
    .tx_empty     = neorv32_tx_empty,
    .set_mctrl    = neorv32_set_mctrl,
    .get_mctrl    = neorv32_get_mctrl,
    .stop_tx      = neorv32_stop_tx,
    .start_tx     = neorv32_start_tx,
    .stop_rx      = neorv32_stop_rx,
    .startup      = neorv32_startup,
    .shutdown     = neorv32_shutdown,
    .set_termios  = neorv32_set_termios,
    .type         = neorv32_type,
    .config_port  = neorv32_config_port,
};

/* ... platform driver probe/remove + console registration ... */
/* Full platform_driver registration code is omitted here */
/* Refer to drivers/tty/serial/uartlite.c as a template */
```

> [!TIP]
> **Recommended reference Linux serial drivers** (simple structure, good for learning):
> - `drivers/tty/serial/uartlite.c` — Xilinx UARTLite, the simplest UART driver
> - `drivers/tty/serial/liteuart.c` — LiteX UART, also an FPGA soft-core driver
> - U-Boot's `drivers/serial/serial_neorv32.c` — NEORV32 UART implementation in U-Boot

### 4.3 FIRQ Interrupt Controller Driver (Late Phase 4)

NEORV32's FIRQ (Fast Interrupt Request) system:
- 16 channels, enabled via `mie` CSR bits [16..31]
- Pending status via `mip` CSR bits [16..31]
- `mcause` exception code: FIRQ #n = `0x80000010 + n`
- UART0 = FIRQ #2 (`mie` bit 18, `mcause` = `0x80000012`)

**Skip the interrupt driver initially — use polling for everything.** After Linux successfully boots to a shell, add the FIRQ irqchip driver to improve performance.

---

## Phase 5: Kernel Build and Boot Debugging

### 5.1 Integrate Drivers into Kernel Source

```bash
# Assuming kernel source is in the buildroot output
KERN_SRC=${BUILDROOT}/output/build/linux-6.6/

# Copy driver files
cp earlycon-neorv32.c $KERN_SRC/drivers/tty/serial/
cp neorv32_uart.c     $KERN_SRC/drivers/tty/serial/
```

Modify `$KERN_SRC/drivers/tty/serial/Makefile`:
```makefile
obj-y += earlycon-neorv32.o
obj-y += neorv32_uart.o
```

Modify `$KERN_SRC/drivers/tty/serial/Kconfig`:
```
config SERIAL_NEORV32
    bool "NEORV32 UART support"
    depends on RISCV
    select SERIAL_CORE
    help
      NEORV32 RISC-V SoC UART driver.
```

### 5.2 Build Kernel + initramfs

```bash
cd ${BUILDROOT}

# If using external tree:
# make BR2_EXTERNAL=${PROJECT_DIR} neorv32_ax301_defconfig

# Or configure manually
make menuconfig
# Set kernel config, DT path, etc.

make -j$(nproc)
```

Build artifacts:
- `output/images/Image` — Linux kernel binary (~1-3 MB)
- `output/images/rootfs.cpio` — Root filesystem (BusyBox, ~500 KB-1 MB)
- DTS needs to be compiled to DTB separately

```bash
# Compile Device Tree
dtc -I dts -O dtb -o neorv32_ax301.dtb neorv32_ax301.dts
```

### 5.3 Load and Boot via U-Boot

Memory layout plan:
```
0x40000000 : U-Boot (occupied, no longer needed after boot)
0x40200000 : Linux kernel Image
0x40F00000 : DTB (Device Tree Blob)
0x41000000 : initramfs (rootfs.cpio)
```

**Method A: Using U-Boot loady + bootm**
```bash
# Terminal 1: connect to board with minicom
minicom -b 115200 -D /dev/ttyUSB0

# At the U-Boot> prompt:
U-Boot> loady 0x40200000      # Then send Image via minicom's ymodem
U-Boot> loady 0x40F00000      # Send neorv32_ax301.dtb
U-Boot> loady 0x41000000      # Send rootfs.cpio

# Boot
U-Boot> bootm 0x40200000 0x41000000 0x40F00000
```

**Method B: Modify boot_uboot.py for automation**

Extend your `boot_uboot.py` to automatically send the kernel after the U-Boot prompt:
```python
# After U-Boot>:
# 1. Send Image via U-Boot's loady command + ymodem
# 2. Send DTB
# 3. Send initramfs
# 4. Send bootm command to boot
```

**Method C: Embed initramfs into kernel**

The simplest approach — specify the initramfs source in kernel config:
```
CONFIG_INITRAMFS_SOURCE="/path/to/rootfs.cpio"
```

This way you only need to transfer **one file** (kernel Image already contains rootfs), plus one DTB:
```
U-Boot> loady 0x40200000      # Image (with embedded initramfs)
U-Boot> loady 0x40F00000      # DTB
U-Boot> bootm 0x40200000 - 0x40F00000
```

### 5.4 Boot Debugging Checklist

```
[ ] earlycon has output (earliest "Booting Linux..." text)
[ ] See "Linux version X.X.X ..."
[ ] See "Machine model: NEORV32 on AX301"
[ ] See "Memory: 32MB ..."
[ ] See "CLINT: ..." timer initialization
[ ] See "console [ttyNEO0] enabled"
[ ] See "Freeing unused kernel memory..."
[ ] See "~ #" BusyBox shell prompt  🎉
```

#### Common Troubleshooting

| Symptom | Possible Cause | Solution |
|---------|---------------|----------|
| No output at all | earlycon not working | Check DT `chosen/stdout-path`, verify `earlycon=` parameter |
| Garbled output | Wrong baud rate | Confirm both kernel and UART are 115200 |
| Stuck at "Booting Linux..." | Wrong kernel entry point | Check `CONFIG_PHYS_RAM_BASE`, verify Image load address |
| "Unable to handle kernel paging request" | Memory address config error | Check DT `memory` node, `CONFIG_PAGE_OFFSET` |
| Timer not working (frozen) | CLINT address or layout issue | Verify MTIME is running with `md 0xFFF4BFF8` in U-Boot |
| "Kernel panic - not syncing: No init found" | initramfs not loaded properly | Use Method C to embed in kernel |
| Very slow (no response for minutes) | No DCACHE | Go back to Phase 1.3 and enable DCACHE |

---

## Phase 6: Optimization and Extensions (After Linux is Running)

### 6.1 Add FIRQ Interrupt Support

Write `irq-neorv32-firq.c` irqchip driver so UART RX can use interrupts instead of polling.

### 6.2 Enable SPI and Connect SD Card

- Enable `IO_SPI_EN => true` in RTL
- Write/port SPI driver
- Mount FAT filesystem
- Load kernel from SD card (no more xmodem)

### 6.3 Performance Tuning

- Adjust ICACHE/DCACHE size
- Try enabling `CACHE_BURSTS_EN` (requires SDRAM controller burst support modification)
- Increase UART FIFO depth

---

## Open Questions (Need Your Confirmation)

> [!IMPORTANT]
> 1. **DCACHE resources**: Are you willing to try enabling DCACHE and see if Quartus compilation passes? If M9K blocks are insufficient, would you be willing to reduce IMEM from 8 KB to 4 KB? (Can stage2_loader fit in 4 KB?)
>
> 2. **Kernel version**: Buildroot 2024.02 defaults to kernel 6.6 LTS. Do you have a preferred kernel version?
>
> 3. **Boot method**: Which method do you prefer for transferring the kernel to the board?
>    - (A) Manual minicom + ymodem (most flexible, but slow)
>    - (B) Modify boot_uboot.py for automation (one-click boot)
>    - (C) Embed initramfs in kernel, transfer only one file

## Verification Plan

### Automated Testing
- Build and boot on QEMU using Buildroot's `qemu_riscv32_nommu_virt_defconfig`
- Run `cat /proc/cpuinfo`, `free`, `ps`, `ls /` in QEMU to verify functionality

### Hardware Testing
- After RTL changes, compile with `quartus_sh --flow compile` and check resource usage
- After flashing new bitstream, confirm U-Boot still works normally
- Verify CLINT MTIME is incrementing with `md` in U-Boot
- Load kernel + DTB and observe earlycon output
