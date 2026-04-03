# See NEORV32 Run Linux

在 **NEORV32** RV32IMAC 软核 FPGA 上启动 nommu Linux（内核 6.6.83）—— 据信是 NEORV32 上首次成功的 Linux 启动。

NEORV32 是一个微控制器级处理器，**没有 MMU**、**没有 S-mode**。让 Linux 在其上运行需要对内核的 arch/riscv、调度器、RCU、init 和驱动子系统进行 16 个补丁。

我们还发现并修复了 [NEORV32 SC.W 指令的一个 bug](https://github.com/stnolting/neorv32/pull/1520) —— store-conditional 返回的是旧数据而非成功/失败状态码。此修复使内核能够使用原生 RISC-V 原子指令（LR/SC + AMO）。

**演示视频：** https://youtu.be/JC6qNcMIWf8

## 文档

本仓库包含三类文档，分别面向不同的读者：

| 文件 | 读者 | 用途 |
|------|------|------|
| `README.md` / `README_zh.md` | **人类** | 项目概述、构建步骤、架构说明 |
| `CLAUDE.md` | **AI 代理** | 供 [Claude Code](https://claude.ai/code) 使用的机器可读构建流程、约束和已知陷阱 |
| `init_prompt.txt` | **Claude Code 启动脚本** | 粘贴到 Claude Code 中，即可自动从源码完成整个项目的构建 |
| `implementation_plan_en.md` / `implementation_plan_zh.md` | **开发者** | 从零开始的详细实施计划，包含硬件验证、内核移植、驱动开发等全部阶段 |
| `BUILD_LOG.md` | **开发者** | 从"内核编译通过"到"shell 提示符"的 105 次构建调试历程 —— 记录了每次失败的原因和修复方法 |

要使用 Claude Code 复现完整构建，在仓库根目录打开终端并运行：
```bash
claude
```
然后将 `init_prompt.txt` 的内容作为第一条消息粘贴。Claude Code 会读取 `CLAUDE.md` 获取详细指令，并执行完整的从源码构建流程。

## 硬件

| 组件 | 规格 |
|------|------|
| **开发板** | 黑金 AX301 |
| **FPGA** | Altera Cyclone IV E EP4CE6F17C8（6,272 LEs） |
| **CPU** | NEORV32 RV32IMAC，50 MHz，仅 M-mode |
| **内存** | 32 MB SDRAM（HY57V2562GTR） |
| **串口** | PL2303 USB-UART，115200 波特率 |
| **烧写器** | USB-Blaster，通过 openFPGALoader |

## 演示

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

## 从源码构建

所有源码均包含在本仓库中，无需额外下载。

### 前置依赖

| 工具 | 用途 |
|------|------|
| Intel Quartus Prime Lite 21.1+ | FPGA 综合 |
| xPack RISC-V GCC 14.2.0 (riscv-none-elf-) | 内核和 stage2 交叉编译器（**必须使用此版本**） |
| Buildroot Linux GCC (riscv32-buildroot-linux-gnu-) | 仅用于 initramfs /init（需要 PIE 支持） |
| CMake + libftdi1-dev + libusb-1.0-0-dev | openFPGALoader 编译 |
| dtc（设备树编译器） | DTB 编译 |
| Python 3 + pyserial | 主机端启动脚本 |

### 步骤 1：编译 openFPGALoader

```bash
cd tools/openFPGALoader
mkdir build && cd build
cmake .. && make -j$(nproc)
```

> **注意：** 系统自带的 openfpgaloader（v0.12.0）不支持 EP4CE6，必须从源码编译。

### 步骤 2：编译 FPGA 比特流

```bash
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off output_files/neorv32_demo.sof ../output/neorv32_demo.rbf
```

### 步骤 3：编译 stage2 加载器

```bash
cd sw/stage2_loader
make NEORV32_HOME=../../neorv32 exe
cp neorv32_exe.bin ../../output/stage2_loader.bin
```

### 步骤 4：编译内核 + initramfs

```bash
# 解压并打补丁
tar xf linux-6.6.83.tar.xz && cd linux-6.6.83
patch -p1 < ../kernel/neorv32_nommu.patch
../board/inject_driver.sh .

# 编译 initramfs（使用 Buildroot Linux 工具链以支持 PIE）
cd ../sw/initramfs
make LINUX_DIR=../../linux-6.6.83
cp neo_initramfs.cpio.gz ../../output/

# 修正 defconfig 中的 initramfs 路径，然后编译内核
cd ../../
sed "s|CONFIG_INITRAMFS_SOURCE=.*|CONFIG_INITRAMFS_SOURCE=\"$(pwd)/output/neo_initramfs.cpio.gz\"|" \
    board/linux_defconfig > linux-6.6.83/arch/riscv/configs/neorv32_ax301_defconfig
cd linux-6.6.83
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)
cp arch/riscv/boot/Image ../output/
```

### 步骤 5：编译设备树

```bash
dtc -I dts -O dtb -o output/neorv32_ax301.dtb board/neorv32_ax301.dts
```

### 步骤 6：启动

```bash
python3 host/boot_linux.py --port /dev/ttyUSB0
```

## 系统架构

### 硬件框图

```
  AX301 开发板 (EP4CE6, 50 MHz)
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

### 启动流程（4 阶段）

```
上电 → NEORV32 内部 bootloader（19200 波特率，ROM 位于 0xFFE00000）
  ↓ 上传 stage2_loader.bin（3.7 KB）
  ↓ 执行 → UART 切换到 115200 波特率
Stage2 加载器（IMEM，115200 波特率）
  ↓ 'l' → Linux 直接启动模式
  ↓ xmodem：内核 Image（1.5 MB） → SDRAM 0x40000000，CRC-32 校验
  ↓ xmodem：DTB（1.4 KB）         → SDRAM 0x41F00000，CRC-32 校验
  ↓ xmodem：initramfs（2.9 KB）   → SDRAM 0x41F80000，CRC-32 校验
  ↓ 跳转到 0x40000000，a0=hartid，a1=DTB 指针
Linux 内核（M-mode，nommu）
  ↓ 约 98 秒启动 → /init（initramfs 中的迷你 shell）
```

### FPGA 内存映射

| 地址 | 大小 | 描述 |
|------|------|------|
| `0x00000000` | 8 KB | IMEM（M9K BRAM — stage2 加载器） |
| `0x40000000` | 32 MB | SDRAM（内核 + 数据，外部） |
| `0x80000000` | 8 KB | DMEM（M9K BRAM — 内核栈/堆） |
| `0xFFE00000` | ~4 KB | Boot ROM（NEORV32 bootloader，只读） |
| `0xFFF40000` | 48 KB | CLINT（mtime 位于 +0xBFF8，mtimecmp 位于 +0x4000） |
| `0xFFF50000` | 8 B | UART0（CTRL + DATA 寄存器） |
| `0xFFFC0000` | 16 B | GPIO（gpio_o[3:0] → LED 低电平有效） |

### Linux 运行时内存布局（SDRAM）

启动后，`0x40000000` 处的 32 MB SDRAM 布局如下：

```
0x40000000 ┌─────────────────────────┐
           │     Linux 内核           │  ~1.4 MB
           │  .text, .rodata, .data  │
           │  （由 stage2 加载）       │
0x40170000 ├─────────────────────────┤  （大约）
           │     内核 BSS             │  ~49 KB
0x4017C000 ├─────────────────────────┤
           │                         │
           │   空闲内存（伙伴系统）     │  ~30 MB
           │   由页分配器管理          │
           │                         │
0x41F00000 ├─────────────────────────┤
           │   设备树 Blob（DTB）      │  ~1.4 KB
           │  （通过 a1 寄存器         │
           │   传递给内核）            │
0x41F80000 ├─────────────────────────┤
           │   initramfs (cpio.gz)   │  ~1.7 KB
           │  （内核解压到 rootfs      │
           │   tmpfs 中）             │
0x42000000 └─────────────────────────┘  32 MB 结束
```

**关键运行时数据**（来自内核日志）：
- 总内存：32,768 KB（32 MB）
- 启动后可用：30,908 KB（约 30 MB 空闲）
- 内核代码：1,076 KB | 读写数据：137 KB | 只读数据：160 KB | Init：99 KB | BSS：52 KB

## 为什么这很难？

NEORV32 是一个微控制器核心 —— 它从未被设计为运行 Linux。以下是我们需要解决的问题：

### 1. 没有 MMU，没有 S-mode

Linux 通常在 S-mode（超级用户模式）下运行，并使用虚拟内存。NEORV32 只有 M-mode（机器模式）和 U-mode。我们使用 `nommu` 配置直接在 M-mode 下运行内核。

**关键配置：** `CONFIG_MMU=n`，`CONFIG_PAGE_OFFSET=0x40000000`（必须与物理 RAM 基址匹配）。

### 2. 调度器死锁

内核调度器的 `need_resched` 循环假设抢占在所有点都能工作。在我们的单核 nommu 环境中，这些会变成线程间的无限乒乓循环。

**解决方案：** 修改 `schedule()` 和 `preempt_schedule_common()`，使 `__schedule()` 只执行一次，而不是循环检查 `need_resched`。为 kthread 启动添加了 `schedule_preempt_disabled_once()`。

### 3. ~~`wfi` 导致 CPU 停机~~ （已解决）

最初我们将 `wfi` 替换为 `nop`，但测试确认 `wfi` 工作正常 —— 定时器中断能正确唤醒 CPU。现已恢复上游的 `wfi` 指令。

### 4. RISCV_ALTERNATIVE 补丁冲突

内核的替代指令补丁框架（`RISCV_ALTERNATIVE`）会在运行时根据检测到的 ISA 扩展替换指令。在 `free_initmem()` 之后，`.alternative` 段的 __init 数据被释放，CPU 跳入已释放的内存执行代码（在 `epc=0x4011c002` 处触发非法指令陷阱）。

**解决方案：** 在 `arch/riscv/Kconfig` 中完全禁用 `RISCV_ALTERNATIVE`。

### 5. RCU / 工作队列停滞

单线程 RCU（`srcutiny`）和异步工作队列假设抢占调度正常工作。在我们的单核 nommu 系统上，宽限期永远无法完成，`synchronize_srcu()` 会永久挂起。

**解决方案：** `srcutiny.c` 中的同步宽限期，`initramfs.c` 中的同步 `populate_rootfs()`，`async_synchronize_full()` 的 120 秒超时。

### 6. UART 驱动

NEORV32 的 UART 不被任何上游 Linux 驱动支持。我们编写了自定义的 `neorv32_uart.c` tty 驱动，使用基于 kthread 的轮询（无 IRQ）并直接进行行规程传递。

## 内核补丁

所有补丁位于 `kernel/neorv32_nommu.patch`（针对原版 6.6.83 修改了 16 个文件）。

**原子操作：** 内核使用**原生 RISC-V 原子指令**（AMO + LR/SC）。这得益于我们发现并修复了 [NEORV32 SC.W 指令的一个 bug](https://github.com/stnolting/neorv32/pull/1520) —— store-conditional 在成功时返回的是 LR.W 载入的旧值而非 0（成功）。修复方法是在 `neorv32_bus_amo_rvs` 中添加 `sc_pend` 信号来正确覆盖响应数据。所有 11 个用户空间 LR/SC 测试通过，内核中包含 810 条原子指令。

### 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `arch/riscv/Kconfig` | 禁用 `RISCV_ALTERNATIVE` |
| `arch/riscv/kernel/traps.c` | M-mode 陷阱处理调整 |
| `kernel/sched/core.c` | 单次 `__schedule()`，不循环检查 `need_resched` |
| `kernel/sched/rt.c` | 实时调度器 nommu 调整 |
| `kernel/kthread.c` | 使用 `schedule_preempt_disabled_once()`，kthreadd 优先级提升 |
| `kernel/rcu/srcutiny.c` | 同步 SRCU 宽限期 |
| `kernel/async.c` | `async_synchronize_full()` 120 秒超时 |
| `include/linux/sched.h` | 声明 `schedule_preempt_disabled_once()` |
| `include/linux/srcutiny.h` | SRCU 结构调整 |
| `init/main.c` | init 的 SCHED_FIFO 提升，禁用 initmem 毒化 |
| `init/initramfs.c` | 同步 `populate_rootfs()` |
| `drivers/tty/serial/Kconfig` | 添加 NEORV32 UART 选项 |
| `drivers/tty/serial/Makefile` | 构建 neorv32_uart.o |
| `drivers/tty/serial/neorv32_uart.c` | **新文件** — 自定义 UART 驱动 |
| `arch/riscv/configs/neorv32_defconfig` | **新文件** — 内核配置 |
| `arch/riscv/configs/neorv32_ax301_defconfig` | **新文件** — AX301 板级配置 |

## FPGA RTL

NEORV32 在 `rtl/ax301_top.vhd` 中使用以下泛型参数配置：

| 泛型参数 | 值 | 原因 |
|---------|------|------|
| `RISCV_ISA_C` | true | 压缩指令（更小的代码） |
| `RISCV_ISA_M` | true | 硬件乘法/除法 |
| `RISCV_ISA_U` | true | Linux 用户空间的 U-mode |
| `RISCV_ISA_Zaamo` | true | 原子内存操作（AMO） |
| `RISCV_ISA_Zalrsc` | true | Load-reserved / store-conditional（LR/SC） |
| `ICACHE_EN` | true | SDRAM 指令取指所需 |
| `DCACHE_EN` | true | 性能优化（SDRAM 数据访问） |
| `IMEM_SIZE` | 8 KB | Stage2 加载器可放入 8 KB |
| `DMEM_SIZE` | 8 KB | 内核栈/堆临时空间 |
| `IO_UART0_RX_FIFO` | 4 | 2^4 = 16 条目 FIFO 用于控制台输入 |
| `IO_UART0_TX_FIFO` | 4 | 2^4 = 16 条目 FIFO 用于控制台输出 |

## 项目结构

```
see_neorv32_run_linux/
├── tools/openFPGALoader/  — openFPGALoader 源码（需从源码编译）
├── neorv32/               — NEORV32 RTL 源码（v1.12.8）
├── linux-6.6.83.tar.xz   — Linux 内核源码压缩包
├── rtl/                   — 自定义 FPGA 设计
│   ├── ax301_top.vhd      — 顶层模块：NEORV32 + SDRAM + UART + GPIO
│   ├── wb_sdram_ctrl.v    — Wishbone → SDRAM 桥接
│   └── sdram_ctrl.v       — SDRAM 控制器状态机（CL=3, 50 MHz）
├── quartus/               — Quartus 项目文件（.qsf, .qpf, .sdc）
├── kernel/
│   └── neorv32_nommu.patch — 16 个内核补丁（基于原版 6.6.83）
├── board/                 — 板级支持文件
│   ├── neorv32_ax301.dts   — 设备树源码
│   ├── linux_defconfig     — 内核配置
│   ├── neorv32_uart.c      — 自定义 UART 驱动源码
│   ├── inject_driver.sh    — 将驱动注入内核源码树的脚本
│   └── buildroot_defconfig — Buildroot 配置（替代构建方式）
├── sw/
│   ├── stage2_loader/      — xmodem 启动加载器（C，从 IMEM 运行）
│   │   ├── main.c
│   │   └── Makefile        — 使用 NEORV32 common.mk
│   └── initramfs/          — Linux 用的最小 /init
│       ├── init.c           — 自定义 shell（纯系统调用，无 libc）
│       └── Makefile
├── host/                  — Python 主机脚本
│   ├── boot_linux.py       — 完整启动流程（烧写 + 上传 + 控制台）
│   └── test_shell.py       — Shell 命令测试
├── output/                — 构建输出（由构建步骤生成）
└── BUILD_LOG.md           — 105 次构建的调试历程
```

## 资源使用（EP4CE6，50 MHz）

| 资源 | 已使用 | 可用 | 百分比 |
|------|--------|------|--------|
| 逻辑单元 | 4,600 | 6,272 | 73% |
| 存储位 | 168,960 | 276,480 | 61% |
| 寄存器 | 2,408 | 6,272 | 38% |
| 嵌入式乘法器 | 0 | 30 | 0% |

## 已知问题

- **内核必须使用 xPack `riscv-none-elf-gcc` 14.2.0 编译：** 使用 Buildroot 的 `riscv32-buildroot-linux-gnu-gcc` 12.4.0 编译出的内核会在 `free_initmem()` 处死锁 —— 最后一个 debug marker `L`（system_state = RUNNING）能打印出来，但执行永远无法到达 `M`（free_initmem 之后）。即使源码、补丁、内核配置和 FPGA 比特流完全相同，仅编译器不同就会导致此问题。根本原因是 GCC 12.4.0 生成的机器码存在细微差异，在 NEORV32 的受限环境中触发死锁。用 Buildroot 编译器构建的内核整体运行也明显更慢（clocksource 切换在 13.8 秒 vs 7.4 秒，并触发 `sched: RT throttling activated`）。**内核必须使用 xPack 裸机工具链编译。**
- **启动时间约 98 秒：** 主要消耗在驱动探测和异步工作队列超时上。50 MHz 单发射核心对内核初始化来说确实很慢。
- **SDRAM 偶发初始化失败：** 首次上电时偶尔会失败。将开发板断电几秒钟后重新上电即可解决。
- **Shell 功能极简：** 仅支持 `uname`、`info`、`amo`、`help`、`exit`。init 二进制文件是自定义 C 程序，而非 busybox（为了保持 initramfs 尽可能小）。
- **无网络、无存储：** 这是一个纯 UART 控制台。EP4CE6 没有多余空间容纳额外外设。

## 许可证

- NEORV32 RTL：BSD 3-Clause（参见 [NEORV32 仓库](https://github.com/stnolting/neorv32)）
- Linux 内核补丁：GPL-2.0（与内核相同）
- SDRAM 控制器、主机脚本、stage2 加载器：MIT
