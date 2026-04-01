# Build Log: 105 Builds to Boot Linux on NEORV32

This document chronicles the debugging journey from "kernel compiled" to "shell prompt" — 105 builds over ~5 days of iterative debugging on real hardware. Each section describes what broke, why, and how it was fixed.

The NEORV32 is a microcontroller-class RISC-V soft-core with no MMU, no S-mode, and no atomic instructions. No one has run Linux on it before, so there was no reference implementation to follow.

---

## Phase 1: RTL Preparation

### DCACHE and UART FIFO

Before any kernel work, the FPGA design needed changes for Linux:

- **DCACHE enabled:** Without data cache, every SDRAM access takes ~15 CPU cycles (CL=3, inverted clock, two 16-bit reads per 32-bit word). The kernel's data-heavy init would take hours.
- **UART RX FIFO increased to 16:** The original 1-byte FIFO loses characters when the CPU is busy. Linux console input requires buffering.
- **RISCV_ISA_U enabled:** Linux userspace runs in U-mode; the kernel needs U-mode support for `ecall` from init.

## Phase 2: QEMU Validation

Tested on `qemu-system-riscv32 -machine virt -nographic` with a nommu kernel config. Confirmed the kernel boots and a custom init shell works — proving the approach is viable before touching hardware.

## Phase 3: External Tree + Driver

Set up a Buildroot external tree for out-of-tree kernel config and custom UART driver.

### The NEORV32 UART Driver Problem

No upstream Linux driver supports NEORV32's UART. The register layout:
- `0xFFF50000` (CTRL): bits [31:28] = TX FIFO free count, bit 3 = TX busy, bit 2 = RX available
- `0xFFF50004` (DATA): TX/RX data register

We wrote `neorv32_uart.c` — a tty driver with kthread-based polling. No IRQ is used because NEORV32's external interrupt routing would require additional RTL changes.

## Phase 4: Hardware Debugging — The Long Road

This is where the real work happened. Each build was compiled, transferred via xmodem (~20s for 1.4 MB at 115200 baud), and tested on hardware.

---

### Issue 1: PAGE_OFFSET Mismatch (Builds #1–5)

**Symptom:** Kernel crashes immediately on boot, no output.

**Root cause:** `CONFIG_PAGE_OFFSET` defaulted to `0xC0000000` (the MMU kernel default). On nommu, `PAGE_OFFSET` must equal `PHYS_RAM_BASE` (`0x40000000`) because there's no virtual→physical translation.

**Fix:** Patched `arch/riscv/Kconfig` to allow `PAGE_OFFSET=0x40000000`.

---

### Issue 2: PMP CSR Access Trap (Builds #6–8)

**Symptom:** Illegal instruction trap in early boot (`head.S`).

**Root cause:** `head.S` probes PMP (Physical Memory Protection) CSRs. NEORV32 with `PMP_NUM_REGIONS=0` traps on any PMP CSR access.

**Fix:** None needed — `head.S` already has a trap handler that skips failed CSR probes. The issue was a red herring; the real crash was elsewhere.

---

### Issue 3: LR/SC and AMO Instructions (Builds #9–20)

**Symptom:** Illegal instruction traps scattered throughout boot.

**Root cause:** The kernel's spinlocks, atomic operations, and cmpxchg all use LR/SC or AMO instructions. NEORV32 doesn't implement the A (atomic) extension — these are all illegal.

**Fix:** Replaced three header files:
- `cmpxchg.h`: `lr/sc` loops → `disable_irq; load; compare; store; enable_irq`
- `atomic.h`: `amoadd/amoswap/amoor/amoand` → plain C with IRQ disable
- `bitops.h`: `amoor/amoand` for `set_bit/clear_bit/change_bit` → same pattern

This was the largest single patch. Every atomic operation in the kernel goes through these headers.

---

### Issue 4: `wfi` Freezes the CPU (Builds #21–22)

**Symptom:** Kernel hangs in idle loop after timer init.

**Root cause:** NEORV32's `wfi` halts the CPU until an interrupt arrives. If the timer interrupt hasn't been configured yet, or if `wfi` executes with interrupts masked, the CPU stops forever.

**Fix:** `wfi` → `nop` in `processor.h`. The idle loop now busy-waits, which is fine for a 50 MHz single-core system.

---

### Issue 5: `memmap_init()` Hangs (Builds #23–30)

**Symptom:** Boot stalls during memory zone initialization. No output after "Memory:" line.

**Root cause:** `memmap_init()` iterates over all pages (8,192 pages for 32 MB) calling `__init_single_page()` which uses `INIT_LIST_HEAD` — an atomic operation on our non-atomic core. With 8K iterations and IRQ disable/enable per page, this takes ~60 seconds at 50 MHz.

**Fix:** It wasn't actually hanging — it was just extremely slow. Added a patience timeout and confirmed it completes eventually. Later optimizations to the IRQ disable path helped.

---

### Issue 6: `parse_early_param` Hangs (Builds #31–44)

**Symptom:** Kernel hangs after "Kernel command line:" output.

**Root cause:** `parse_early_param()` calls string processing functions that, on this platform, trigger repeated scheduler calls. The scheduler's `need_resched` loop was spinning forever because the non-atomic `test_and_clear` of `TIF_NEED_RESCHED` could race with the timer interrupt setting it.

**Fix:** First major scheduler modification — `schedule()` calls `__schedule()` exactly once instead of looping on `need_resched`. Safe because we're single-core.

---

### Issue 7: `wait_for_completion` Hangs (Builds #45–55)

**Symptom:** Boot stalls at various points waiting for kernel threads to signal completion.

**Root cause:** `wait_for_completion()` calls `schedule()` expecting to be woken by a worker thread. But the worker's `need_resched` flag is set by the timer interrupt, and without atomic test-and-clear, the worker re-enters the scheduler infinitely instead of doing work.

**Fix:** Extended the single-shot `schedule()` approach to `preempt_schedule_common()`. Also gave init task `SCHED_FIFO` priority to prevent CFS starvation.

---

### Issue 8: `synchronize_srcu` Hangs (Builds #56–62)

**Symptom:** Boot stalls in RCU grace period wait.

**Root cause:** `srcutiny` (the single-CPU SRCU implementation) uses a polling loop waiting for a grace period to complete. The grace period requires a context switch to advance, but the context switch mechanism is broken by our single-shot scheduler.

**Fix:** Modified `srcutiny.c` to force a synchronous grace period — directly calling the SRCU callbacks instead of waiting for the poll mechanism.

---

### Issue 9: kthread `schedule()` Loop (Builds #63–70)

**Symptom:** kworker threads spin in `schedule()` without making progress.

**Root cause:** `kthread()` uses `schedule_preempt_disabled()` which calls the standard `schedule()`. Our single-shot change fixed userspace scheduling but broke the kthread startup sequence — kthreads need to reschedule differently.

**Fix:** Created `schedule_preempt_disabled_once()` — a variant that does exactly one `__schedule()` call with preemption disabled. Used by `kthread()` for its initial schedule-away.

---

### Issue 10: `populate_rootfs` / Async Work (Builds #71–78)

**Symptom:** Kernel stalls after "Freeing unused kernel memory" or during rootfs population.

**Root cause:** `populate_rootfs()` runs as an async work item. `async_synchronize_full()` waits for all async work to complete, but the async framework relies on work queues that need properly functioning preemption.

**Fix:** Two changes:
1. `initramfs.c`: Made `populate_rootfs()` synchronous (call directly instead of `async_schedule`)
2. `async.c`: Added 120-second timeout to `async_synchronize_full()` to prevent permanent hangs on other async items

---

### Issue 11: CFS Scheduler Starvation (Builds #79–83)

**Symptom:** init process starves — kworker threads get all CPU time.

**Root cause:** After fixing the scheduler loops, CFS (Completely Fair Scheduler) gives equal time slices to all tasks. With many kworker threads and one init, init rarely gets scheduled.

**Fix:** Keep init at `SCHED_FIFO` priority throughout boot, not just during early init. The `SCHED_FIFO` class gets strict priority over CFS tasks.

---

### Issue 12: TTY / stdin Not Working (Builds #84–92)

**Symptom:** Shell prompt appears but doesn't accept input.

**Root cause:** The UART driver was using work queues to deliver received characters to the TTY layer. But work queue execution depends on kworker threads being scheduled, which was unreliable with our scheduler modifications.

**Fix:** Rewrote the UART driver to use a dedicated kthread that polls the UART RX FIFO and delivers characters directly to the line discipline, bypassing the work queue entirely.

---

### Issue 13: UART TX FIFO Busy-Wait Hang (Builds #93–95)

**Symptom:** Shell hangs after first output, or kernel hangs mid-boot.

**Root cause:** Debug `printk` calls in the UART kthread would try to transmit while the kthread itself was blocked waiting for TX FIFO space — a self-deadlock.

**Fix:** Removed all debug printk from the UART kthread. Console output uses only the earlycon path (polling TX directly) or the tty layer (which has its own buffering).

---

### Issue 14: `mdelay(10)` Blocks Shell (Builds #96–97)

**Symptom:** Shell input has 10ms latency per character.

**Root cause:** The UART driver's kthread used `mdelay(10)` between poll loops. On a 50 MHz core, this wastes 500,000 cycles doing nothing.

**Fix:** Replaced `mdelay(10)` with `schedule_timeout_interruptible(1)` — yields to the scheduler for 1 tick (10ms) but wakes immediately if there's work to do.

---

### Issue 15: Shell Works! But Only With Debug Output (Build #98–102)

**Milestone:** Build #98 was the first time the shell prompt appeared and accepted input.

Builds #99–102 progressively removed debug markers (printk in scheduler, trap handler, syscall path) that were cluttering the console. Build #102 with remaining boot-time-only markers worked perfectly.

---

### Issue 16: Crash After Removing Debug (Builds #103–104)

**Symptom:** After removing hot-path debug printk (scheduler, entry.S, traps.c), kernel crashes at `epc=0x4011c002` with cause=2 (illegal instruction) after `free_initmem()`.

**Root cause:** The crash address `0x4011c002` is inside the `.alternative` section. This section contains `alt_entry` structures — data, not code. The kernel's `RISCV_ALTERNATIVE` framework patches instructions at runtime based on detected ISA extensions. These patches reference the `.alternative` section, and after `free_initmem()` releases init memory, some code paths jump into this section.

The debug markers were masking the bug by changing timing — the `printk` delays allowed async operations to complete before `free_initmem()` released the pages.

**Analysis:**
- `epc=0x4011c002` = `__alt_start + 2`
- `ra=0x40100dc0` = `kernel_init` returning from `free_initmem()`
- The instruction at that address: `0xffee` — not a valid RV32 instruction
- 21 `alt_entry` records exist, patching may conflict with our non-atomic replacements

---

### Issue 17: The Fix — Disable RISCV_ALTERNATIVE (Build #105) ✅

**Fix:** Commented out `select RISCV_ALTERNATIVE if !XIP_KERNEL` in `arch/riscv/Kconfig`.

**Result:** `.alternative` section becomes empty (`__alt_start == __alt_end`), no runtime patching occurs, no illegal instruction trap.

**Build #105 boots successfully:** Shell prompt at ~118s, all commands working, stable over 300+ seconds.

---

## Timeline Summary

| Phase | Builds | Duration | Description |
|-------|--------|----------|-------------|
| RTL + QEMU | — | Day 1 | DCACHE, ICACHE, U-mode, QEMU validation |
| LR/SC + AMO | #1–20 | Day 1–2 | Atomic instruction replacement |
| Scheduler | #21–70 | Day 2–3 | wfi, schedule loops, need_resched, SRCU |
| Work queues | #71–83 | Day 3–4 | Async, populate_rootfs, CFS starvation |
| UART + TTY | #84–97 | Day 4 | Driver rewrite, kthread polling, stdin |
| Debug removal | #98–105 | Day 5 | Shell works → crash → RISCV_ALTERNATIVE fix |

## Key Lessons

1. **Non-atomic cores need non-atomic kernels.** You can't just disable the A extension — you must replace every atomic operation in the kernel with IRQ-safe alternatives. This touches headers used by thousands of call sites.

2. **The scheduler is the hardest part.** Not memory management, not device drivers — the scheduler. Its `need_resched` polling loops, CFS fairness assumptions, and preemption model all assume atomic operations. Fixing one loop often exposed the next.

3. **Debug output changes timing.** The `printk` calls that helped us find bugs were also hiding bugs. Build #102 worked perfectly with debug output; Build #103 crashed without it. The debug `printk` delays gave async operations time to complete naturally.

4. **nommu Linux is not well-tested on RISC-V.** The `nommu` config exists but is primarily tested on QEMU `virt` with atomic extensions. Running on real hardware without atomics exposed latent bugs in the scheduler and RCU subsystems.

5. **Patience with 50 MHz.** At 50 MHz with no cache (initially), some kernel init operations take minutes. `memmap_init()` initializing 8,192 pages: ~60 seconds. Total boot to shell: ~118 seconds. What looks like a hang is often just slowness.
