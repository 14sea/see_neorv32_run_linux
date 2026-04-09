# Nommu Linux 移植到 NEORV32 AX301 — 詳細實施計劃

## 概述

將 nommu Linux (M-mode) 移植到黑金 AX301 上的 NEORV32 RV32IMAC 軟核，使用 32 MB SDRAM，通過 UART 控制台達到 BusyBox shell。

### 當前硬體狀態

| 組件 | 地址 | 狀態 |
|------|------|------|
| IMEM | `0x00000000` (8 KB) | ✅ 可用 |
| SDRAM | `0x40000000` (32 MB) | ✅ 已驗證 |
| DMEM | `0x80000000` (8 KB) | ✅ 可用 |
| CLINT | `0xFFF40000` | ✅ 已啟用 |
| UART0 | `0xFFF50000` | ✅ 已啟用 |
| GPIO | `0xFFFC0000` | ✅ 已啟用 |
| SPI | `0xFFF80000` | ❌ 未啟用 |
| ICACHE | 64B blocks, non-burst | ✅ 已啟用 |
| DCACHE | — | ❌ 未啟用 |
| U-Boot | SDRAM `0x40000000` | ✅ 已跑通 |
| FPGA LE 使用率 | 4,136 / 6,272 (66%) | ⚠️ 剩餘 34% |

---

## Phase 1：RTL 硬體準備

### 1.1 驗證 CLINT 兼容性（無需改動）

> [!TIP]
> 這一步不需要修改 RTL，只需驗證。

Linux 的 `riscv,clint0` 驅動期望的 CLINT 記憶體佈局：

```
BASE + 0x0000 : MSIP[0]     (4 bytes, 軟件中斷 pending)
BASE + 0x0004 : MSIP[1]     (下一個 hart，不用管)
...
BASE + 0x4000 : MTIMECMP[0] (8 bytes, hart 0 的定時器比較值)
BASE + 0x4008 : MTIMECMP[1] (hart 1, 不用管)
...
BASE + 0xBFF8 : MTIME       (8 bytes, 全局定時器)
```

NEORV32 的 CLINT 結構體 ([neorv32_clint.h](neorv32/sw/lib/include/neorv32_clint.h#L25-L29))：

```c
typedef volatile struct {
  uint32_t    MSWI[4096];        // offset 0x0000, 4096 × 4 = 0x4000 bytes
  subwords64_t MTIMECMP[4095];   // offset 0x4000, 4095 × 8 = 0x7FF8 bytes
  subwords64_t MTIME;            // offset 0x4000 + 0x7FF8 = 0xBFF8
} neorv32_clint_t;
```

**佈局計算：**
- `MSWI[0]` 在 `BASE + 0x0000` → ✅ 對應 Linux 的 `MSIP[0]`
- `MTIMECMP[0]` 在 `BASE + 4096*4 = BASE + 0x4000` → ✅ 完全匹配
- `MTIME` 在 `BASE + 0x4000 + 4095*8 = BASE + 0xBFF8` → ✅ 完全匹配

> [!IMPORTANT]
> **CLINT 佈局與 Linux 的 `riscv,clint0` 驅動 100% 兼容！不需要任何修改。**
> 你的 CLINT 基址是 `0xFFF40000`，所以：
> - MSIP[0] = `0xFFF40000`
> - MTIMECMP[0] = `0xFFF44000`
> - MTIME = `0xFFF4BFF8`

**驗證方法**（在 U-Boot 中測試）：
```
U-Boot> md 0xFFF4BFF8 2    # 讀取 MTIME，應該看到遞增的值
U-Boot> md 0xFFF4BFF8 2    # 再讀一次，值應該更大
```

---

### 1.2 增大 UART TX/RX FIFO（推薦改動）

當前配置 FIFO 深度為 1（[ax301_top.vhd](rtl/ax301_top.vhd#L139-L140)）：
```vhdl
IO_UART0_RX_FIFO => 1,   -- FIFO 深度 = 2^1 = 2 entries
IO_UART0_TX_FIFO => 1,   -- FIFO 深度 = 2^1 = 2 entries
```

Linux kernel 啟動時會輸出大量文字。浅 FIFO 會導致 UART 成為瓶頸，拖慢整個啟動過程。

**建議修改** `rtl/ax301_top.vhd`：
```vhdl
IO_UART0_RX_FIFO => 4,   -- FIFO 深度 = 2^4 = 16 entries
IO_UART0_TX_FIFO => 4,   -- FIFO 深度 = 2^4 = 16 entries
```

> [!NOTE]
> 每增加 1 級 FIFO，大約消耗 16-32 個 LE。從 2^1 到 2^4 大約多用 100-200 LE。你有 ~2100 LE 剩餘，完全夠。

---

### 1.3 啟用 DCACHE（強烈推薦）

目前只有 ICACHE，data bus 每次訪問 SDRAM 都是直接走 XBUS → wb_sdram_ctrl → SDRAM。Linux 的 kernel data（棧、頁表結構、kmalloc 分配等）會頻繁讀寫 SDRAM，沒有 DCACHE 會**極其慢**。

**修改** `rtl/ax301_top.vhd`：
```vhdl
DCACHE_EN        => true,    -- 啟用 DCACHE
-- DCACHE 預設使用和 ICACHE 相同的配置：
-- CACHE_BLOCK_SIZE => 64 (16 words per line)
-- 直接映射，1K 或 2K cache
```

> [!WARNING]
> **DCACHE 的資源消耗估算：**
> - DCACHE 需要額外的 M9K blocks 存儲 tag + data
> - 你目前 M9K 使用 60%（166,912 / 276,480 bits）
> - 一個 1KB 的 DCACHE 大約需要 8,192 bits (1 M9K block) data + ~1K tag
> - 如果 M9K 不夠，可能需要縮小 IMEM（從 8KB 改為 4KB）
>
> **先嘗試加 DCACHE，看 Quartus 編譯是否通過。如果 M9K 不夠，再考慮取捨。**

---

### 1.4 （可選）啟用 SPI

如果想在後續階段從 SPI Flash 或 SD 卡啟動 Linux（避免每次 xmodem），可以啟用 SPI：

```vhdl
IO_SPI_EN => true,
```

SPI 大約消耗 200-300 LE。目前有 2100+ LE 剩餘，可行。
但**初期先不啟用**，等 Linux 跑通後再考慮。

---

### 1.5 RTL 修改摘要

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

**完成後：重新編譯 bitstream**
```bash
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off neorv32_demo.sof ../neorv32_demo_linux.rbf
```

**驗證：** 重新燒寫後，用 `boot_uboot.py` 確認 U-Boot 仍然能正常啟動。

---

## Phase 2：構建 Linux 交叉編譯環境和 QEMU 驗證

### 2.1 安裝依賴

```bash
sudo apt-get update
sudo apt-get install -y \
    git make gcc g++ bison flex libncurses-dev \
    bc cpio rsync unzip python3 perl wget \
    qemu-system-misc libssl-dev
```

### 2.2 下載 Buildroot

```bash
git clone https://github.com/buildroot/buildroot.git
cd buildroot
git checkout 2024.02.x   # LTS 分支，穩定
```

### 2.3 QEMU 上跑通 RV32 Nommu Linux

```bash
# 使用現成的 RISC-V 32 nommu 配置
make qemu_riscv32_nommu_virt_defconfig

# 構建（約 30-60 分鐘）
make -j$(nproc)

# 驗證構建成功
ls -la output/images/
# 應該看到: Image, loader, rootfs.cpio 等

# 啟動 QEMU
output/host/bin/qemu-system-riscv32 \
    -M virt -m 128M -nographic \
    -kernel output/images/loader \
    -device loader,file=output/images/Image,addr=0x80400000
```

**成功標誌：** 看到 Linux 啟動日誌，最終出現 `~ #` BusyBox shell prompt。

> [!IMPORTANT]
> **必須先在 QEMU 上成功後再進入下一步。** 如果 QEMU 上都跑不通，硬體上更不可能。QEMU 階段的學習要點：
> - 理解 `make menuconfig` 的 kernel 配置
> - 理解 Buildroot 的目錄結構：`output/build/linux-xxx/` 是 kernel 源碼
> - 理解 DT（Device Tree）如何描述硬體

### 2.4 研究 QEMU Nommu 配置

QEMU 跑通後，提取關鍵配置作為後續參考：

```bash
# 查看 kernel 的 .config
cat output/build/linux-*/\.config | grep -E "RISCV|MMU|CLINT|NOMMU|M_MODE|UART"

# 查看 QEMU virt 的 DT
# 在 QEMU 中：
cat /proc/device-tree/compatible
cat /sys/firmware/devicetree/base/chosen/bootargs
```

---

## Phase 3：為 NEORV32 創建 Buildroot 外部樹

### 3.1 目錄結構

```bash
mkdir -p ${PROJECT_DIR}
cd ${PROJECT_DIR}

mkdir -p board/neorv32_ax301/
mkdir -p configs/
mkdir -p linux/
mkdir -p package/neorv32-uart-driver/
```

最終結構：
```
${PROJECT_DIR}/
├── board/neorv32_ax301/
│   ├── neorv32_ax301.dts          ← Device Tree 源碼
│   ├── linux.config               ← Kernel 配置片段
│   └── post-build.sh              ← 構建後處理腳本
├── configs/
│   └── neorv32_ax301_defconfig    ← Buildroot 配置
├── linux/
│   └── linux.config               ← Kernel defconfig 覆蓋
└── Config.in                       ← Buildroot external tree
```

### 3.2 Device Tree（核心文件）

建立 `board/neorv32_ax301/neorv32_ax301.dts`：

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
        timebase-frequency = <50000000>;  /* MTIME 以 CPU 時鐘頻率計數 */

        cpu0: cpu@0 {
            device_type = "cpu";
            compatible = "riscv";
            riscv,isa = "rv32imac";
            riscv,isa-base = "rv32i";
            riscv,isa-extensions = "i", "m", "a", "c", "zicsr", "zifencei";
            mmu-type = "riscv,none";      /* 無 MMU = nommu */
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

    /* CLINT — 與 SiFive CLINT 佈局 100% 兼容 */
    clint: clint@fff40000 {
        compatible = "riscv,clint0";
        reg = <0xfff40000 0x10000>;       /* 64 KB 空間 */
        interrupts-extended = <&cpu0_intc 3>,   /* M-mode 軟件中斷 */
                              <&cpu0_intc 7>;   /* M-mode 定時器中斷 */
    };

    /* UART0 — 使用自定義 NEORV32 UART 驅動 */
    uart0: serial@fff50000 {
        compatible = "neorv32,uart";
        reg = <0xfff50000 0x08>;          /* CTRL + DATA = 8 bytes */
        clock-frequency = <50000000>;
        current-speed = <115200>;
        /*
         * UART0 使用 FIRQ #2 (cpu_firq[2])
         * FIRQ 映射到 mie/mip 的 bit [16+n]
         * 在 M-mode 下，外部中斷走 mcause
         * 先用 polling 方式，中斷支持在後期添加
         */
    };

    /* GPIO — 4-bit, LED 控制 */
    gpio: gpio@fffc0000 {
        compatible = "neorv32,gpio";
        reg = <0xfffc0000 0x08>;
        /* 初期不需要 GPIO 驅動，僅作為調試用 */
    };
};
```

### 3.3 Linux Kernel 配置片段

建立 `board/neorv32_ax301/linux.config`：

```
# ── 架構 ──
CONFIG_RISCV=y
CONFIG_ARCH_RV32I=y
CONFIG_RISCV_ISA_C=y
CONFIG_RISCV_ISA_M=y

# ── M-mode Nommu ──
CONFIG_MMU=n
CONFIG_RISCV_M_MODE=y

# ── 記憶體 ──
CONFIG_PAGE_OFFSET=0x40000000
CONFIG_PHYS_RAM_BASE=0x40000000
CONFIG_DEFAULT_MEM_START=0x40000000
CONFIG_DEFAULT_MEM_SIZE=0x02000000

# ── 定時器 ──
CONFIG_CLINT_TIMER=y

# ── 控制台（初期用 earlycon polling） ──
CONFIG_SERIAL_EARLYCON=y
CONFIG_EARLY_PRINTK=y
CONFIG_PRINTK=y

# ── 文件系統 ──
CONFIG_BLK_DEV_INITRD=y
CONFIG_INITRAMFS_SOURCE=""
# Buildroot 會自動設置 initramfs

# ── 精簡配置（關閉不需要的） ──
CONFIG_NET=n
CONFIG_BLOCK=n
CONFIG_MODULES=n
CONFIG_SMP=n
CONFIG_SWAP=n
CONFIG_PROC_FS=y
CONFIG_SYSFS=y
CONFIG_TMPFS=y

# ── 調試 ──
CONFIG_DEBUG_KERNEL=y
CONFIG_DEBUG_INFO=y
CONFIG_PANIC_TIMEOUT=0
CONFIG_STACKTRACE=y
```

### 3.4 Buildroot defconfig

建立 `configs/neorv32_ax301_defconfig`：

```
# 架構
BR2_riscv=y
BR2_RISCV_32=y
BR2_riscv_custom=y
BR2_RISCV_ISA_RVM=y
BR2_RISCV_ISA_RVA=y
BR2_RISCV_ISA_RVC=y
BR2_RISCV_ABI_ILP32=y

# 工具鏈
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

# BusyBox (默認配置即可)
BR2_PACKAGE_BUSYBOX=y

# 其他
BR2_TARGET_GENERIC_HOSTNAME="neorv32"
BR2_TARGET_GENERIC_ISSUE="Welcome to NEORV32 Linux"
BR2_SYSTEM_DEFAULT_PATH="/bin:/sbin:/usr/bin:/usr/sbin"
```

---

## Phase 4：Linux Kernel 驅動開發

### 4.1 UART earlycon（最先實現，最關鍵）

earlycon 是 kernel 啟動最早期的輸出通道。沒有它，你看不到任何啟動日誌，等於盲調。

earlycon 只需要一個 `putchar()` 函數，**不需要中斷**，純 polling。

NEORV32 UART 寄存器佈局（[neorv32_uart.h](neorv32/sw/lib/include/neorv32_uart.h#L26-L29)）：
```
offset 0x00: CTRL 寄存器 (32-bit)
  bit 0:     EN (全局使能)
  bit 18:    TX_EMPTY (TX FIFO 空)
  bit 19:    TX_NFULL (TX FIFO 未滿)
  bit 16:    RX_NEMPTY (RX FIFO 非空)
  bit 31:    TX_BUSY (發送忙)

offset 0x04: DATA 寄存器 (32-bit)
  bit [7:0]: 讀 = 收到的字節, 寫 = 要發送的字節
```

**earlycon 驅動實現** — 新建 `drivers/tty/serial/earlycon-neorv32.c`：

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
    /* 等待 TX FIFO 有空位 */
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

### 4.2 完整 UART tty 驅動

完整驅動需要實現 `struct uart_ops`。**初期用純 polling 方式**，不用中斷：

新建 `drivers/tty/serial/neorv32_uart.c`：

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

/* 寄存器偏移 */
#define NEORV32_UART_CTRL    0x00
#define NEORV32_UART_DATA    0x04

/* CTRL 寄存器位 */
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

/* DATA 寄存器 */
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
    /* UART 已由 U-Boot 初始化，here 只需確保 EN 位 */
    uint32_t ctrl = readl(port->membase + NEORV32_UART_CTRL);
    writel(ctrl | CTRL_EN, port->membase + NEORV32_UART_CTRL);
    return 0;
}

static void neorv32_shutdown(struct uart_port *port) {}

static void neorv32_set_termios(struct uart_port *port,
                                 struct ktermios *new,
                                 const struct ktermios *old)
{
    /* 暫時不支持動態改 baud rate，使用 U-Boot 設好的 115200 */
    unsigned int baud = uart_get_baud_rate(port, new, old, 9600, 115200);
    uart_update_timeout(port, new->c_cflag, baud);
}

static const char *neorv32_type(struct uart_port *port)
{
    return "NEORV32_UART";
}

static void neorv32_config_port(struct uart_port *port, int flags)
{
    port->type = PORT_UNKNOWN;  /* 或定義一個 PORT_NEORV32 */
}

/* polling 定時器回調：檢查 RX */
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
/* 這裡省略了完整的 platform_driver 註冊代碼 */
/* 你需要參考 drivers/tty/serial/uartlite.c 作為模板 */
```

> [!TIP]
> **推薦參考的 Linux serial 驅動**（結構簡單、適合學習）：
> - `drivers/tty/serial/uartlite.c` — Xilinx UARTLite，最簡單的 UART 驅動
> - `drivers/tty/serial/liteuart.c` — LiteX UART，也是 FPGA 軟核的驅動
> - U-Boot 的 `drivers/serial/serial_neorv32.c` — NEORV32 UART 在 U-Boot 中的實現

### 4.3 FIRQ 中斷控制器驅動（Phase 4 後期）

NEORV32 的 FIRQ（Fast Interrupt Request）系統：
- 16 個通道，通過 `mie` CSR 的 bit [16..31] 控制啟用
- 通過 `mip` CSR 的 bit [16..31] 檢測 pending
- `mcause` 的異常碼：FIRQ #n = `0x80000010 + n`
- UART0 = FIRQ #2（`mie` bit 18，`mcause` = `0x80000012`）

**初期先跳過中斷驅動，全部用 polling。** 等 Linux 成功啟動到 shell 後，再添加 FIRQ irqchip 驅動以提升性能。

---

## Phase 5：Kernel 構建與啟動調試

### 5.1 將驅動整合到 Kernel 源碼

```bash
# 假設 kernel 源碼在 buildroot output 中
KERN_SRC=${BUILDROOT}/output/build/linux-6.6/

# 複製驅動文件
cp earlycon-neorv32.c $KERN_SRC/drivers/tty/serial/
cp neorv32_uart.c     $KERN_SRC/drivers/tty/serial/
```

修改 `$KERN_SRC/drivers/tty/serial/Makefile`：
```makefile
obj-y += earlycon-neorv32.o
obj-y += neorv32_uart.o
```

修改 `$KERN_SRC/drivers/tty/serial/Kconfig`：
```
config SERIAL_NEORV32
    bool "NEORV32 UART support"
    depends on RISCV
    select SERIAL_CORE
    help
      NEORV32 RISC-V SoC UART driver.
```

### 5.2 構建 Kernel + initramfs

```bash
cd ${BUILDROOT}

# 如果使用外部樹：
# make BR2_EXTERNAL=${PROJECT_DIR} neorv32_ax301_defconfig

# 或者手動配置
make menuconfig
# 設置 kernel 配置、DT 路徑等

make -j$(nproc)
```

構建産物：
- `output/images/Image` — Linux kernel 二進制（~1-3 MB）
- `output/images/rootfs.cpio` — 根文件系統（BusyBox, ~500 KB-1 MB）
- 需要自己編譯 DTS → DTB

```bash
# 編譯 Device Tree
dtc -I dts -O dtb -o neorv32_ax301.dtb neorv32_ax301.dts
```

### 5.3 通過 U-Boot 加載並啟動

記憶體佈局規劃：
```
0x40000000 : U-Boot（已佔用，boot 後不再需要）
0x40200000 : Linux kernel Image
0x40F00000 : DTB (Device Tree Blob)
0x41000000 : initramfs (rootfs.cpio)
```

**方法 A：使用 U-Boot loady + bootm**
```bash
# 終端 1：minicom 連接板子
minicom -b 115200 -D /dev/ttyUSB0

# 在 U-Boot> 提示符下：
U-Boot> loady 0x40200000      # 然後用 minicom 的 ymodem 傳送 Image
U-Boot> loady 0x40F00000      # 傳送 neorv32_ax301.dtb
U-Boot> loady 0x41000000      # 傳送 rootfs.cpio

# 啟動
U-Boot> bootm 0x40200000 0x41000000 0x40F00000
```

**方法 B：修改 boot_uboot.py 自動化**

擴展你的 `boot_uboot.py`，在 U-Boot prompt 後自動發送 kernel：
```python
# 在 U-Boot> 後：
# 1. 通過 U-Boot 的 loady 命令 + ymodem 發送 Image
# 2. 發送 DTB
# 3. 發送 initramfs
# 4. 發送 bootm 命令啟動
```

**方法 C：將 initramfs 編入 kernel**

最簡單的方式 — 在 kernel 配置中指定 initramfs 源：
```
CONFIG_INITRAMFS_SOURCE="/path/to/rootfs.cpio"
```

這樣只需傳送 **一個文件**（kernel Image 已包含 rootfs），加上一個 DTB：
```
U-Boot> loady 0x40200000      # Image (含 initramfs)
U-Boot> loady 0x40F00000      # DTB
U-Boot> bootm 0x40200000 - 0x40F00000
```

### 5.4 啟動調試檢查清單

```
[ ] earlycon 有輸出（最早的 "Booting Linux..." 字樣）
[ ] 看到 "Linux version X.X.X ..."
[ ] 看到 "Machine model: NEORV32 on AX301"
[ ] 看到 "Memory: 32MB ..."
[ ] 看到 "CLINT: ..." 定時器初始化
[ ] 看到 "console [ttyNEO0] enabled"
[ ] 看到 "Freeing unused kernel memory..."
[ ] 看到 "~ #" BusyBox shell prompt  🎉
```

#### 常見問題排查

| 現象 | 可能原因 | 解決方案 |
|------|---------|---------|
| 完全無輸出 | earlycon 未工作 | 檢查 DT `chosen/stdout-path`，確認 `earlycon=` 參數 |
| 亂碼 | baud rate 不對 | 確認 kernel 和 UART 都是 115200 |
| 卡在 "Booting Linux..." | kernel entry point 錯誤 | 檢查 `CONFIG_PHYS_RAM_BASE`，確認 Image 加載地址 |
| "Unable to handle kernel paging request" | 記憶體地址配置錯誤 | 檢查 DT `memory` 節點，`CONFIG_PAGE_OFFSET` |
| 定時器不工作（卡住不動） | CLINT 地址或佈局問題 | 在 U-Boot 中 `md 0xFFF4BFF8` 確認 MTIME 在跑 |
| "Kernel panic - not syncing: No init found" | initramfs 未正確加載 | 用方法 C 編入 kernel |
| 非常慢（幾分鐘沒反應） | 無 DCACHE | 回到 Phase 1.3 啟用 DCACHE |

---

## Phase 6：優化與擴展（Linux 跑通後）

### 6.1 添加 FIRQ 中斷支持

寫 `irq-neorv32-firq.c` irqchip 驅動，使 UART RX 可以用中斷而非 polling。

### 6.2 啟用 SPI 並接 SD 卡

- RTL 中啟用 `IO_SPI_EN => true`
- 寫/移植 SPI 驅動
- 掛載 FAT 文件系統
- 從 SD 卡加載 kernel（告別 xmodem）

### 6.3 性能調優

- 調整 ICACHE/DCACHE 大小
- 嘗試啟用 `CACHE_BURSTS_EN`（需修改 SDRAM 控制器支持 burst）
- 增大 UART FIFO

---

## 開放問題（需你確認）

> [!IMPORTANT]
> 1. **DCACHE 資源**：你願意先嘗試啟用 DCACHE 看 Quartus 能否編譯通過嗎？如果 M9K 不夠，是否願意把 IMEM 從 8 KB 縮小到 4 KB？（stage2_loader 是否能放進 4 KB？）
>
> 2. **Kernel 版本**：Buildroot 2024.02 默認 kernel 6.6 LTS。你有偏好的 kernel 版本嗎？
>
> 3. **啟動方式**：你偏好哪種方式傳送 kernel 到板子？
>    - (A) 手動用 minicom + ymodem（最靈活，但慢）
>    - (B) 修改 boot_uboot.py 自動化（一鍵啟動）
>    - (C) initramfs 編入 kernel，只傳一個文件

## 驗證計劃

### 自動化測試
- 在 QEMU 上用 Buildroot 的 `qemu_riscv32_nommu_virt_defconfig` 構建並啟動
- 在 QEMU 中執行 `cat /proc/cpuinfo`, `free`, `ps`, `ls /` 確認功能正常

### 硬體測試
- 修改 RTL 後用 `quartus_sh --flow compile` 編譯，檢查資源使用率
- 燒寫新 bitstream 後確認 U-Boot 仍正常
- 在 U-Boot 中用 `md` 驗證 CLINT MTIME 遞增
- 加載 kernel + DTB，觀察 earlycon 輸出
