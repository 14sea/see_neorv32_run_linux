# See NEORV32 Run Linux

在 **NEORV32** RV32IMAC 软核 FPGA 上启动 nommu Linux（内核 **6.18.60 LTS**）—— 据信是 NEORV32 上首次成功的 Linux 启动。

NEORV32 是一个微控制器级处理器，**没有 MMU**、**没有 S-mode**。最初在 Linux 6.6.83 上移植时，需要对内核的 arch/riscv、调度器、RCU、init 和驱动子系统打 16 个补丁。在 6.18 上这些补丁都不再需要：实机测量把调度器 / RCU / 工作队列的卡死追溯到同一个问题 —— 在这颗核上，定时器 tick 的开销对 HZ=250 来说太大 —— 所以现在的内核是**原版 6.18.60**，只加了一个树外 UART 驱动和一份设置了 `CONFIG_HZ_100` 的 defconfig。

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
| `BUILD_LOG.md` | **开发者** | 最初 Linux 6.6.83 移植时，从"内核编译通过"到"shell 提示符"的 105 次构建调试历程 —— 记录了每次失败的原因和修复方法（历史记录；后来查明的共同根因见"为什么这很难？"） |

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

除一个例外，所有源码均包含在本仓库中：Linux 内核源码压缩包 `linux-6.18.60.tar.xz`（约 150 MB）超出 GitHub 大小限制，**未纳入 git 跟踪** —— 需从 kernel.org 下载一次（见步骤 5）。

### 前置依赖

| 工具 | 用途 |
|------|------|
| Intel Quartus Prime Lite 21.1+ | FPGA 综合 |
| xPack RISC-V GCC 14.2.0 (riscv-none-elf-) | 内核和 stage2 交叉编译器（**必须使用此版本**） |
| Buildroot Linux GCC (riscv32-buildroot-linux-gnu-) | 仅用于 initramfs /init（需要 PIE 支持） |
| CMake + libftdi1-dev + libusb-1.0-0-dev | openFPGALoader 编译 |
| dtc（设备树编译器） | DTB 编译 |
| Python 3 + pyserial | 主机端启动脚本 |

### 步骤 1：初始化 NEORV32 子模块

```bash
git submodule update --init --recursive
```

> 不再需要本地 RTL 补丁：我们最后一个修复（成功的 `SC.W` 必须在整个总线传输期间保持为写操作）已从 v1.13.7 起进入上游（[PR #1661](https://github.com/stnolting/neorv32/pull/1661)）。不要在本项目的 SDRAM 控制器上使用 NEORV32 v1.13.2–v1.13.6——原子 `cmpxchg` 的写入会被悄悄丢弃，内核卡在 `vfs_caches_init()`。详情见 `neorv32_patches/README.md`。

### 步骤 2：编译 openFPGALoader

```bash
cd tools/openFPGALoader
mkdir build && cd build
cmake .. && make -j$(nproc)
```

> **注意：** 系统自带的 openfpgaloader（v0.12.0）不支持 EP4CE6，必须从源码编译。

### 步骤 3：编译 FPGA 比特流

```bash
cd quartus
quartus_sh --flow compile neorv32_demo
quartus_cpf -c -o bitstream_compression=off output_files/neorv32_demo.sof ../output/neorv32_demo.rbf
```

### 步骤 4：编译 stage2 加载器

```bash
cd sw/stage2_loader
make NEORV32_HOME=../../neorv32 exe
cp neorv32_exe.bin ../../output/stage2_loader.bin
```

### 步骤 5：编译内核 + initramfs

内核是**原版 6.18.60** —— 不打任何内核补丁，只注入 UART 驱动并拷入板级 defconfig。

```bash
# 若本地没有内核压缩包，先下载（未纳入 git 跟踪）
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.60.tar.xz
tar xf linux-6.18.60.tar.xz

# 注入 NEORV32 UART 驱动和板级 defconfig
board/inject_driver.sh linux-6.18.60
cp board/linux_defconfig linux-6.18.60/arch/riscv/configs/neorv32_ax301_defconfig

# 编译 initramfs（使用 Buildroot Linux 工具链以支持 PIE）。它不嵌入内核：
# 由 stage2 加载，并通过 DTB 告诉内核位置。
make -C sw/initramfs LINUX_DIR=../../linux-6.18.60
cp sw/initramfs/neo_initramfs.cpio.gz output/

# 编译内核（$PATH 中需有 xPack riscv-none-elf-gcc 14.2.0）
cd linux-6.18.60
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- neorv32_ax301_defconfig
grep '^CONFIG_HZ=' .config        # 必须输出 CONFIG_HZ=100
make ARCH=riscv CROSS_COMPILE=riscv-none-elf- -j$(nproc)
cp arch/riscv/boot/Image ../output/
```

### 步骤 6：编译设备树

```bash
dtc -I dts -O dtb -o output/neorv32_ax301.dtb board/neorv32_ax301.dts
```

### 步骤 7：启动

```bash
python3 host/boot_linux.py --port /dev/ttyUSB0
```

这是**默认路径**，只需要 FPGA + SDRAM + UART —— **不需要 SD 卡**。每次启动时，内核 / DTB / initramfs 都会通过 UART xmodem 流式传输到 SDRAM（2026-10-30 实测：从 `boot_linux.py` 启动到 shell 提示符共 **约 221 s** —— 除最后约 6 s 的内核启动外，其余时间都用于 FPGA 烧写、stage2 上传和 1.7 MB 的 xmodem 传输）。

## 从 SD 卡快速启动（可选）

> **可选** —— 仅当你把 SD 卡接到 FPGA 的 SPI 引脚时使用。上面步骤 7 的 xmodem 路径不需要任何 SD 卡。

UART xmodem 传输每次启动都要约 3.5 分钟。为了跳过这一步，stage2 loader 可以通过 NEORV32 的硬件 SPI 外设直接从 SD 卡读取内核 blob。Linux 仍然运行在 SDRAM 中 —— **SD 卡只在启动时作为只读批量存储使用**，因此无需任何内核侧驱动。

**接线**（AX301 板载 SD 槽）：`PIN_J15=SD_CLK`、`PIN_K16=SD_DI (MOSI)`、`PIN_J16=SD_DO (MISO)`、`PIN_K15=SD_NCS`。FPGA 位流需要在启用 `IO_SPI_EN=true` 的情况下重新编译（`rtl/ax301_top.vhd` 中已设好）。

**一次性：将 blob 打包并写入 SD**

```bash
# 将 Image + DTB + initramfs 打包成带 NEOLNX 魔数的 blob 并流式写入 SD
python3 host/sd_pack.py --port /dev/ttyUSB0
```

耗时 ~160 s（仅此一次）。blob 存放在 raw LBA 0 —— 没有 MBR，也没有文件系统。

**之后每次启动：从 SD 加载**

```bash
python3 host/boot_sd.py --port /dev/ttyUSB0
```

在旧的 6.6 内核上，大约 **~150 s** 到达 shell 提示符，当时比 xmodem 快约 90 s。尚未用 6.18 复测（测试板上没有 SD 卡）；内核自身的启动时间现在约 6 s。

**内核与 initramfs 解耦：** 内核现在以 `CONFIG_INITRAMFS_SOURCE=""` 编译 —— initramfs **不再嵌入** Image。stage2 从 SD blob 中把 Image / DTB / initramfs 作为三段独立数据读入 SDRAM，然后在跳转前 patch DTB `chosen/linux,initrd-end` 的 sentinel (`0xC0DEDEAD`) 为真实的结束地址。内核再根据 DT `chosen` 里的地址解包 initramfs。

这意味着**修改 init 或用户态应用不再需要重新编译内核**。

### 增量 SD 更新（`sd_update.py`）

"我只是改了一下 /init" 这种最常见的场景，重写整个 2966 sector 的 blob 太浪费了。SD blob 使用固定 LBA 槽位（见 `host/sd_layout.py`）：

| 槽位   | 起始 LBA | 预留        | 目前使用 |
|--------|----------|-------------|----------|
| header | 0        | 1 sec       | magic + 尺寸 + LBAs + `layout_version` |
| Image  | 1        | 4000 sec (2 MB) | ~1.7 MB |
| DTB    | 4001     | 8 sec (4 KB)    | ~1.5 KB |
| initrd | 4009     | 4000 sec (2 MB) | ~3 KB — 有大把空间留给应用 |

由于 LBA 固定，`sd_update.py` 可以只重写 header + 发生变动的槽位 —— 通常只要 **7 个 sector（~10 s 总时间，其中实际写 SD 只用 ~1 s）**，而不是 163 s。写入前，它会通过新增的 stage2 `R` 模式读回卡上的 header，验证 `magic` / `layout_version` / LBA 常数是否和 `sd_layout.py` 一致；如果布局漂移了（例如你改了某个槽位大小），它会拒绝执行并提醒你先跑 `sd_pack.py`。

```bash
# 一键 /init 改-测回圈（默认 230400 baud + persistent stage2）：
vim sw/initramfs/init.c
make -C sw/initramfs LINUX_DIR=../../linux-6.18.60
cp sw/initramfs/neo_initramfs.cpio.gz output/
python3 host/boot_sd.py --update        # 更新 init 槽 + 启动，~17s 写入
# 追加 --update-dtb / --update-kernel 更新其它槽；--update-verify
# 会在写完后重读 header 做自检。

# 仅更新不启动：
python3 host/sd_update.py --port /dev/ttyUSB0 --verify

# 完整重写（内核变了、布局变了、或第一次用这张卡）：
python3 host/sd_pack.py --port /dev/ttyUSB0     # ~99s @ 230400
```

| 模式 | 主机脚本 | 到 shell 的时间 | 使用场景 |
|------|----------|-----------------|----------|
| xmodem | `boot_linux.py` | 约 221 s（传输约 215 s + 启动 6 s） | 没有 SD 卡，或调试 stage2 |
| SD blob | `boot_sd.py` | ~150 s（6.6 时期，未复测） | 日常开发（一次性 `sd_pack.py` 之后） |

### 主机 / stage2 优化（Phase 1–6）

在上面的 SD 路径之上，又叠加了六项优化：

1. **UART baud 提升到 230400** — 主机送 probe byte 同步，任何失败自动回退到 115200。stage2 新增 `B` 模式；共用 `host/sd_proto.setup_session()`。`sd_pack.py`：**165 s → 99 s**。
2. **Persistent stage2** — dispatcher 命令之间永不超时；任何主机工具加 `--persistent --baud 230400` 即可复用已运行的 stage2，省掉 FPGA 烧录 + bootloader 握手 + stage2 上传。`sd_update.py`：**39 s → 17 s**。
3. **`boot_sd.py --update`** — 一键改 → 更新 → 启动。写入阶段用快 baud，进 kernel 前自动切回 115200。
4. **`sd_update.py --verify`** — 写完后再用 `R` 模式读回 header，和写入内容逐栏比对。
5. **参数化 `sd_dump.py --lba/--count/--hex`** — stage2 `d` 模式现在从 UART 读 LBA + count（上限 2 MB）然后回到 dispatcher，可以在 `--persistent` 模式下链式 dump。
6. **`boot_sd.py` build-tag 检查** — 送 `'b'` 之前先读 header，逐槽打印 `on-card vs local` 大小，`✓` 或 `✗ STALE`。能在 ~150 s 启动前就发现 SD 内容过期。`--no-check` 可跳过。

Stage2 loader 命令（上传 stage2 之后通过 UART 发送）：`l`=xmodem、`s`=SD smoke、`d`=SD dump（参数化）、`w`/`W`=写测试 / 多段写、`R`=读 header、`B`=切 baud、`b`=从 SD blob 启动。

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

### 启动流程（4 阶段）

```
上电 → NEORV32 内部 bootloader（19200 波特率，ROM 位于 0xFFE00000）
  ↓ 上传 stage2_loader.bin（7.9 KB）
  ↓ 执行 → UART 切换到 115200 波特率
Stage2 加载器（IMEM，115200 波特率）
  ↓ 'l' → Linux 直接启动模式
  ↓ xmodem：内核 Image（1.7 MB） → SDRAM 0x40000000，CRC-32 校验
  ↓ xmodem：DTB（1.4 KB）         → SDRAM 0x41F00000，CRC-32 校验
  ↓ xmodem：initramfs（2.9 KB）   → SDRAM 0x41F80000，CRC-32 校验
  ↓ 跳转到 0x40000000，a0=hartid，a1=DTB 指针
Linux 内核（M-mode，nommu）
  ↓ 约 6 秒启动 → /init（initramfs 中的迷你 shell）
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
           │     Linux 内核           │  ~1.7 MB
           │  .text, .rodata, .data  │
           │  （由 stage2 加载）       │
0x401A4000 ├─────────────────────────┤  （大约）
           │     内核 BSS             │  ~51 KB
0x401B1000 ├─────────────────────────┤
           │                         │
           │   空闲内存（伙伴系统）     │  ~30 MB
           │   由页分配器管理          │
           │                         │
0x41F00000 ├─────────────────────────┤
           │   设备树 Blob（DTB）      │  ~1.4 KB
           │  （通过 a1 寄存器         │
           │   传递给内核）            │
0x41F80000 ├─────────────────────────┤
           │   initramfs (cpio.gz)   │  ~2.9 KB
           │  （内核解压到 rootfs      │
           │   tmpfs 中）             │
0x42000000 └─────────────────────────┘  32 MB 结束
```

**关键运行时数据**（来自内核日志）：
- 总内存：32,768 KB（32 MB）
- 启动后可用：30,496 KB（约 30 MB 空闲）
- 内核代码：1,249 KB | 读写数据：141 KB | 只读数据：178 KB | Init：102 KB | BSS：51 KB

## 为什么这很难？

NEORV32 是一个微控制器核心 —— 它从未被设计为运行 Linux。以下是我们需要解决的问题：

### 1. 没有 MMU，没有 S-mode

Linux 通常在 S-mode（超级用户模式）下运行，并使用虚拟内存。NEORV32 只有 M-mode（机器模式）和 U-mode。我们使用 `nommu` 配置直接在 M-mode 下运行内核。

**关键配置：** `CONFIG_MMU=n`，`CONFIG_PHYS_RAM_BASE=0x40000000`（在 6.18 nommu 下，`PAGE_OFFSET` 会自动跟随物理 RAM 基址）。

### 2. 定时器 tick 对 HZ=250 来说太贵

在 6.6 上，我们先后遇到 `need_resched` 乒乓循环、RCU 宽限期永远完成不了、工作队列永远不执行，并且逐个打了补丁（单次 `__schedule()`、同步 SRCU、同步 `populate_rootfs()` ……）。在实机上给 6.18 的调度器和定时器中断插桩测量后，才看清它们背后的共同原因：

| I-cache | 256 B（NEORV32 默认） | 256 B | **4 KB** | **4 KB** |
|---|---|---|---|---|
| HZ | 250（内核默认） | 100 | 250 | 100 |
| tick 周期（周期数） | 200,000 | 500,000 | 200,000 | 500,000 |
| 定时器中断处理函数平均耗时 | **约 170,000（一个 tick 的 85%）** | 约 109,000（22%） | 约 86,000（43%） | 约 74,000（15%） |
| `__schedule()` 耗时（含开中断时处理的 tick） | 约 357,000（1.8 个 tick） | 约 82,000 | 约 68,000 | 约 55,000 |
| 超过一个 tick 的切换比例 | 98.7% | 0% | 5% | 0% |
| 单次 `schedule()` 调用中 `while (need_resched())` 的循环次数 | **>4,096 —— 活锁** | < 32 | < 32 | < 32 |
| 结果 | **卡死** | 可启动，9 秒 | 可启动，10 秒 | **可启动，6 秒** |

在默认 256 字节 I-cache、HZ=250 的情况下，每次上下文切换期间挂起的那个 tick 都会再次置位 `need_resched`，于是 `schedule()` 在同几个任务（init、`ksoftirqd/0`、`kworker/u4:0`）之间不停切换，谁都干不了活：kworker 永远不执行，clocksource 切换后几秒内启动就卡死了。中断处理之所以这么慢，主要是 256 字节的 I-cache 根本装不下内核的热路径 —— 每次缺失都要从 SDRAM 逐字重新填充一行 64 字节。

**解决方案：** `CONFIG_HZ_100`（无需任何调度器、RCU、kthread 或 init 补丁），再加上 **4 KB I-cache**（`ICACHE_NUM_BLOCKS => 64`，多用 6 个 LE 和 4 个 M9K）。两者任选其一就能启动；保留 HZ=100，是因为即使有 4 KB I-cache，tick 仍要占 HZ=250 一个 tick 的 43%。

### 3. ~~`wfi` 导致 CPU 停机~~ （已解决）

最初我们将 `wfi` 替换为 `nop`，但测试确认 `wfi` 工作正常 —— 定时器中断能正确唤醒 CPU。现已恢复上游的 `wfi` 指令。

### 4. ~~RISCV_ALTERNATIVE 补丁冲突~~ （已解决）

在 6.6 上，我们因为 `free_initmem()` 之后出现非法指令陷阱而禁用了 `RISCV_ALTERNATIVE`。在 6.18 上，有了原生原子操作和 HZ=100，使用上游默认值（启用）也能正常运行；由于 DT 的 ISA 字符串是 `acim`，所有 alternative 都不会被打入。

### 5. ~~RCU / 工作队列停滞~~ （已解决）

与第 2 点根因相同 —— 在 HZ=100 下，SRCU、异步 init 和工作队列无需修改即可正常运行。

### 6. UART 驱动

NEORV32 的 UART 不被任何上游 Linux 驱动支持。我们编写了自定义的 `neorv32_uart.c` tty 驱动，使用基于 kthread 的轮询（无 IRQ）并直接进行行规程传递。

## 内核改动

内核是**原版 Linux 6.18.60** —— 没有内核补丁文件。所有 NEORV32 相关内容都在 `board/` 里：

| 文件 | 作用 |
|------|------|
| `board/neorv32_uart.c` | 自定义 UART tty 驱动（kthread 轮询、earlycon），拷入 `drivers/tty/serial/` |
| `board/inject_driver.sh` | 拷入驱动并添加 Kconfig/Makefile 条目 |
| `board/linux_defconfig` | nommu RV32 M-mode 配置：`CONFIG_HZ_100`、`PHYS_RAM_BASE=0x40000000`、外部 initramfs、无块设备/网络/模块 |
| `board/neorv32_ax301.dts` | 设备树（`riscv,isa-extensions` = `i m a c` …、CLINT、UART，initrd 位置由 stage2 修补） |

**原子操作：** 内核使用**原生 RISC-V 原子指令**（AMO + LR/SC）。这得益于我们发现并修复了 [NEORV32 SC.W 指令的一个 bug](https://github.com/stnolting/neorv32/pull/1520) —— store-conditional 在成功时返回的是 LR.W 载入的旧值而非 0（成功）—— 以及后来 reservation station 里的一个 [请求稳定性 bug](https://github.com/stnolting/neorv32/pull/1661)。两者都已合入上游。全部 11 个用户空间 LR/SC/AMO 测试通过。

针对 6.6.83 的原始 16 文件补丁（`kernel/neorv32_nommu.patch`）及其来龙去脉保存在 git 历史和 `BUILD_LOG.md` 中。

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
| `ICACHE_NUM_BLOCKS` | 64 | 64 × 64 B = **4 KB** I-cache（默认 4 块 = 256 B）：tick 开销减半，启动 9 秒 → 6 秒 |
| `DCACHE_EN` | **false** | 写回式 D-cache 配合非突发 SDRAM 控制器得不偿失。2026-10-30 在 4 KB I-cache 基础上复测（把 DMEM 减到 4 KB 以腾出 M9K）：旧的 `ktime` seqcount 活锁已不再出现，但启动更慢（7 秒 vs 6 秒），且三次启动中有一次在 `fence.i` 之后卡死 —— 保持关闭（见 `neorv32_patches/README.md`） |
| `IMEM_SIZE` | 8 KB | Stage2 加载器可放入 8 KB |
| `DMEM_SIZE` | 8 KB | 内核栈/堆临时空间 |
| `IO_UART0_RX_FIFO` | 4 | 4 条目 FIFO 用于控制台输入（generic 是深度，不是 log2） |
| `IO_UART0_TX_FIFO` | 4 | 4 条目 FIFO 用于控制台输出 |

## 项目结构

```
see_neorv32_run_linux/
├── tools/openFPGALoader/  — openFPGALoader 源码（需从源码编译）
├── neorv32/               — NEORV32 RTL（git 子模块 → stnolting/neorv32 release tag，无本地补丁）
├── linux-6.18.60.tar.xz  — Linux 内核源码压缩包（未纳入 git —— 从 kernel.org 下载）
├── rtl/                   — 自定义 FPGA 设计
│   ├── ax301_top.vhd      — 顶层模块：NEORV32 + SDRAM + UART + GPIO
│   ├── wb_sdram_ctrl.v    — Wishbone → SDRAM 桥接
│   └── sdram_ctrl.v       — SDRAM 控制器状态机（CL=3, 50 MHz）
├── quartus/               — Quartus 项目文件（.qsf, .qpf, .sdc）
├── board/                 — 板级支持文件
│   ├── neorv32_ax301.dts   — 设备树源码
│   ├── linux_defconfig     — 内核配置（Linux 6.18，CONFIG_HZ_100）
│   ├── neorv32_uart.c      — 自定义 UART 驱动源码
│   ├── inject_driver.sh    — 将驱动注入内核源码树的脚本
│   └── buildroot_defconfig — 用于构建 /init 所需 Linux 工具链的 Buildroot 配置（其中的内核设置是遗留的 6.6.83，未使用）
├── sw/
│   ├── stage2_loader/      — xmodem 启动加载器（C，从 IMEM 运行）
│   │   ├── main.c
│   │   └── Makefile        — 使用 NEORV32 common.mk
│   └── initramfs/          — Linux 用的最小 /init
│       ├── init.c           — 自定义 shell（纯系统调用，无 libc）
│       └── Makefile
├── host/                  — Python 主机脚本
│   ├── boot_linux.py       — 完整 xmodem 启动流程（烧写 + 上传 + 控制台）
│   ├── boot_sd.py          — 从 SD blob 启动（可先 --update）
│   ├── sd_pack.py / sd_update.py — 写入完整 SD blob / 只写有变化的槽位
│   ├── sd_dump.py / sd_smoke.py / sd_write_test.py — SD 调试工具
│   ├── sd_layout.py / sd_proto.py — 共享的 SD 槽位布局和 stage2 协议辅助
│   └── test_shell.py       — Shell 命令测试
├── neorv32_patches/       — 我们的 NEORV32 RTL 修复说明（均已合入上游）
├── sim/                   — sdram_ctrl 的 iverilog 测试平台（`make` 运行）
├── icesugar/              — NEORV32 裸机移植到 iCESugar（iCE40UP5K）
├── output/                — 构建输出（由构建步骤生成）
└── BUILD_LOG.md           — 6.6 移植时 105 次构建的调试历程
```

## 资源使用（EP4CE6，50 MHz）

| 资源 | 已使用 | 可用 | 百分比 |
|------|--------|------|--------|
| 逻辑单元 | 4,525 | 6,272 | 72% |
| 存储位 | 198,912 | 276,480 | 72% |
| M9K 块 | 27 | 30 | 90% |
| 寄存器 | 2,103 | 6,272 | 34% |
| 嵌入式乘法器 | 0 | 30 | 0% |

（Quartus 以 EP4CE10 裸片、高强度布局进行适配；比特流在 AX301 的 EP4CE6 上运行。数据基于 NEORV32 v1.13.7。）

## 已知问题

- **内核必须使用 xPack `riscv-none-elf-gcc` 14.2.0 编译：** 在旧的 6.6.83 内核上，使用 Buildroot 的 `riscv32-buildroot-linux-gnu-gcc` 12.4.0 编译出的内核会在 `free_initmem()` 处死锁（尚未在 6.18 上复测） —— 最后一个 debug marker `L`（system_state = RUNNING）能打印出来，但执行永远无法到达 `M`（free_initmem 之后）。即使源码、补丁、内核配置和 FPGA 比特流完全相同，仅编译器不同就会导致此问题。根本原因是 GCC 12.4.0 生成的机器码存在细微差异，在 NEORV32 的受限环境中触发死锁。用 Buildroot 编译器构建的内核整体运行也明显更慢（clocksource 切换在 13.8 秒 vs 7.4 秒，并触发 `sched: RT throttling activated`）。**内核必须使用 xPack 裸机工具链编译。**
- **启动时间约 6 秒**（从内核开始执行到 shell，6.18 + HZ=100 + 4 KB I-cache）；在此之前的 xmodem 传输约需 3.5 分钟 —— 快速迭代请使用 SD 卡启动路径。
- **SDRAM 偶发初始化失败：** 首次上电时偶尔会失败。将开发板断电几秒钟后重新上电即可解决。
- **Shell 功能极简：** 仅支持 `uname`、`info`、`amo`、`help`、`exit`。init 二进制文件是自定义 C 程序，而非 busybox（为了保持 initramfs 尽可能小）。
- **无网络、无存储：** 这是一个纯 UART 控制台。EP4CE6 没有多余空间容纳额外外设。

## 许可证

- NEORV32 RTL：BSD 3-Clause（参见 [NEORV32 仓库](https://github.com/stnolting/neorv32)）
- Linux UART 驱动和内核配置：GPL-2.0（与内核相同）
- SDRAM 控制器、主机脚本、stage2 加载器：MIT
