# NEORV32 on iCESugar (iCE40UP5K)

A bare-metal NEORV32 port to the [iCESugar](https://github.com/wuxx/icesugar) board,
built entirely with the open-source toolchain (GHDL → yosys → nextpnr-ice40 → icepack).

This is **not** the Linux target. See [Why not Linux](#why-not-linux) below.

## Status

**Hardware-verified on 2026-08-21: 11/11 automated checks + manual RGB confirmation.**

| Step | Result |
|------|--------|
| GHDL VHDL→Verilog conversion | ✅ 29,624 lines |
| yosys synthesis | ✅ |
| nextpnr-ice40 place & route | ✅ **4480 / 5280 LC (84 %)**, Fmax **22.49 MHz** (PASS @ 12 MHz) |
| SPRAM mapping | ✅ **4 / 4 SB_SPRAM256KA** — IMEM 64 KB + DMEM 64 KB |
| icepack bitstream | ✅ 104,090 bytes |
| Bare-metal demo build | ✅ 5,352 B text / 1,280 B bss |
| FPGA configuration | ✅ iCELink reports `@cdone:1` |
| Bootloader on hardware | ✅ `HWV 0x01130109`, `CLK 0x00B71B00` (12 MHz), `MISA 0x40801104` (rv32imc) |
| UART upload | ✅ 5,368 B in 2.9 s @ 19200 baud |
| IMEM/DMEM from SPRAM | ✅ 65,536 B each (`MISC 0x59011010`) |
| SPRAM byte/half-word test | ✅ PASS — MASKWREN nibble mapping correct |
| CLINT timer | ✅ 12,000,148 ticks/s vs 12,000,000 nominal (12 ppm) |
| RGB LED via SB_RGBA_DRV | ✅ all three channels confirmed by eye (see below) |

`host/selftest.py` covers everything except the LED: whether photons actually come
out cannot be seen over UART, so it only reports that the sweep *code ran* and defers
the real check to `host/ledcheck.py`, which lights one channel at a time and asks you.

## SoC configuration

Defined in [`rtl/neorv32_icesugar_wrapper.vhd`](rtl/neorv32_icesugar_wrapper.vhd):

```
ISA       rv32imc_zicsr_zicntr_zifencei   (serial mul/div + serial shifter, area-optimised)
IMEM      64 KB   -> 2x SB_SPRAM256KA
DMEM      64 KB   -> 2x SB_SPRAM256KA
BOOTROM   4 KB    -> EBR, NEORV32 bootloader, UART console @ 19200 baud
Clock     12 MHz  (iCELink MCO, pad 35)
IO        UART0, CLINT, GPIO[2:0] (RGB LED), SPI (on-board W25Q64)
Caches    none (IMEM/DMEM are already single-cycle on-chip)
```

Everything else — bit-manip, crypto, FPU, PMP, HPM, OCD, XBUS, DMA, TRNG … — is off.
That is what makes it fit: the maximal upstream demo config does not come close to
5280 LCs.

### Why the memories needed custom IP

The UP5K's 128 KB of RAM lives in four `SB_SPRAM256KA` hard macros that yosys will
**not** infer from behavioural Verilog. Upstream NEORV32 anticipates exactly this: the
memory primitives are instantiated as VHDL *components*, so excluding their source
files from the GHDL conversion leaves black boxes that bind to our Verilog:

```
make VHDL_EXCLUDE="neorv32_imem_ram.vhd neorv32_dmem_ram.vhd"
```

[`rtl/neorv32_imem_ram.v`](rtl/neorv32_imem_ram.v) / [`rtl/neorv32_dmem_ram.v`](rtl/neorv32_dmem_ram.v)
each pair two SPRAMs into one 16384 × 32-bit memory. The fiddly part is `MASKWREN`,
which is **nibble** granular, so one NEORV32 byte-enable has to drive two mask bits:

```verilog
.MASKWREN({en_i[1], en_i[1], en_i[0], en_i[0]})   // low half-word
.MASKWREN({en_i[3], en_i[3], en_i[2], en_i[2]})   // high half-word
```

Getting this wrong corrupts sub-word stores only — which is why the demo's `m` command
runs an explicit byte- and half-word-enable test.

### The RGB LED pad mapping

Pads 39/40/41 are the UP5K's dedicated RGB current-sink pads and must go through
`SB_RGBA_DRV` (nextpnr confirms with `not creating SB_IO`). The pad → colour mapping
was established **on hardware**, lighting one channel at a time:

| Driver output | Pad | Colour |
|---|---|---|
| `RGB0` | 39 | blue |
| `RGB1` | 40 | red |
| `RGB2` | 41 | green |

This agrees with `src/common/io.pcf` in wuxx/icesugar. It does **not** agree with that
repo's `src/basic/verilog/hard_rgb/hard_led.v`, whose

```verilog
`define BLUEPWM RGB0PWM   //  wrong for this board
`define REDPWM  RGB1PWM
`define GREENPWM RGB2PWM
```

permutes the channels — its demo drives all three at full brightness simultaneously,
so it never exposes the error. Half-current mode at the minimum step (~2 mA) is
plainly visible; there is no need for the 8 mA the reference uses.

The bootloader's own heartbeat LED (`STATUS_LED_PIN 0` in `neorv32/sw/bootloader/config.h`)
blinks GPIO bit 0, so a **blinking red LED means the bootloader is running** — a free
sign of life before any UART traffic.

## Build

Prerequisites (all already present on this machine):

```
ghdl 4.1.0 · yosys 0.33 · nextpnr-ice40 · icepack · xPack riscv-none-elf-gcc 14.2.0
```

No `ghdl-yosys-plugin` is needed — `ghdl synth --out=verilog` does the VHDL front end.

```bash
cd icesugar
make          # bitstream -> build/icesugar_top.bin  (+ resource/timing report)
make sw       # bare-metal demo -> sw/hello/neorv32_exe.bin
```

## Program the FPGA

The iCESugar has no FTDI chip, so `iceprog` does **not** apply. The iCELink MCU
exposes a USB mass-storage device; programming is a file copy.

**From Windows (simplest — no `usbipd` needed):** plug the board in, an `iCELink`
drive appears, drag `build/icesugar_top.bin` onto it. The FPGA reconfigures immediately.

**From WSL2** (this is what was used for the verification above): attach the board
first, then mount the MSC device. Note `usbipd` needs its Windows *service* running —
if it is stopped, the device binds to the usbipd stub driver and becomes invisible to
*both* Windows and WSL (`Start-Service usbipd` from an elevated shell fixes it).

```powershell
# Windows PowerShell (admin)
usbipd list
usbipd attach --wsl --busid <ID>
```

```bash
lsblk                                   # look for LABEL=iCELink, ~64 MiB vfat
sudo mkdir -p /mnt/icelink
sudo mount -o uid=$(id -u),gid=$(id -g),flush /dev/sdX /mnt/icelink
cp build/icesugar_top.bin /mnt/icelink/
```

The drive re-mounts itself after each write (`detected capacity change` in `dmesg`) —
that is the "programmed" signal. **That re-enumeration also invalidates the kernel's
cached FAT, and `errors=remount-ro` then flips the mount read-only**, so the *next*
`cp` fails with `Read-only file system`. Re-mount before each programming cycle:

```bash
sudo umount /mnt/icelink 2>/dev/null
sudo mount -o uid=$(id -u),gid=$(id -g),flush /dev/sdd /mnt/icelink
cp build/icesugar_top.bin /mnt/icelink/ && sync
```
 A `FAIL.TXT` appearing on the drive means it was
rejected. The iCELink firmware also announces the result on the CDC port itself:

```
@start
@cdone:1        <- FPGA configuration done
```

## Run

The FPGA UART is bridged to USB CDC by the same iCELink MCU, so the port is
**`/dev/ttyACM*`**, not `/dev/ttyUSB*` like the AX301 board.

```bash
make upload                       # build sw, upload over UART, run, attach terminal
make upload PORT=/dev/ttyACM1
python3 host/upload.py --info     # also dump the bootloader's system info first
python3 host/upload.py --terminal # just attach a terminal

python3 host/selftest.py          # non-interactive: upload, run, check 11 things
```

`host/selftest.py` is the scripted regression: it drives the bootloader, uploads,
runs the demo, and verifies the clock, both memory sizes, the SPRAM byte-enable
behaviour and the CLINT tick rate. Use it after any RTL change.

> The first key press only aborts the bootloader's 8 s auto-boot countdown; the
> `CMD:>` prompt arrives on the following read. Both host scripts retry for this.

Expected output:

```
CMD:>
[*] uploading neorv32_exe.bin (5368 bytes) @ 19200 baud ~2.8s
    5368/5368 bytes (100%)
[+] upload OK
[*] starting executable ('e')

+----------------------------------------------+
|  NEORV32 on iCESugar (iCE40UP5K-SG48)        |
+----------------------------------------------+
Clock     : 12000000 Hz
IMEM      : 65536 bytes (SPRAM)
DMEM      : 65536 bytes (SPRAM)
...
RGB LED sweep... done
SPRAM test... PASS

icesugar>
```

Commands at the `icesugar>` prompt: `i` info · `m` SPRAM test · `t` CLINT timer check ·
`r`/`g`/`b` toggle LED · `h` help.

### Standalone boot from SPI flash

At the bootloader's `CMD:>` prompt, `s` stores the uploaded executable into the
on-board W25Q64 at **0x00400000**, and `l` loads it back. That offset is 4 MB into
the flash, far above the ~104 KB FPGA bitstream at offset 0, so this does not
endanger the configuration image. After storing, the bootloader auto-boots the
application on power-up.

## Why not Linux

The parent project boots nommu Linux on a Cyclone IV with **32 MB of external SDRAM**.
The iCESugar has **no external DRAM at all** — 128 KB of SPRAM plus 16 KB of EBR,
144 KB total. `output/Image` alone is 1,513,284 bytes, more than ten times the entire
on-chip memory, and the booted system reports 30 MB of free RAM. Even an XIP kernel
executing `.text` from the 8 MB SPI flash still needs megabytes of RAM for
`.bss`/slab/stack. This is a two-orders-of-magnitude gap, not a tuning problem.

Logic was never the blocker — the CPU fits in 84 % of the UP5K with room to spare.

The board that *would* work is **[iCESugar-pro](https://github.com/wuxx/icesugar-pro)**
(ECP5 LFE5U-25F, 24 K LUT, IS42S16160B **32 MB SDRAM**, SD card slot). Its SDRAM has
the same 256 Mbit / 16 M × 16 organisation as the AX301's HY57V2562GTR, so
`rtl/sdram_ctrl.v` and its three timing optimisations would largely carry over.

## Layout

```
icesugar/
├── Makefile                            build flow + convenience targets
├── icesugar.pcf                        pin constraints (pads from wuxx/icesugar)
├── rtl/
│   ├── neorv32_icesugar_wrapper.vhd    trimmed SoC config, converted by GHDL
│   ├── icesugar_top.v                  clock/reset/IO, SB_RGBA_DRV for the LED
│   ├── neorv32_imem_ram.v              64 KB IMEM  -> 2x SB_SPRAM256KA
│   └── neorv32_dmem_ram.v              64 KB DMEM  -> 2x SB_SPRAM256KA
├── sw/hello/                           bare-metal bring-up demo
└── host/
    ├── upload.py                       UART upload + interactive terminal
    ├── selftest.py                     scripted 11-check regression
    └── ledcheck.py                     manual RGB confirmation (asks you)
```
