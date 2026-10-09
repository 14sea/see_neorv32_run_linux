# NEORV32 patches

Patches against the upstream `neorv32` submodule that we carry locally
because they have not yet been merged upstream.

When/if these are accepted upstream, bump the `neorv32` submodule pointer
and remove the corresponding patch file.

## Status (2026-10-09)

**Submodule pin:** release tag `v1.13.6` (`e25059f2`; +449 commits past
the previous `99fb4165` pin). From this bump on we track **release
tags**, not `origin/main` HEAD. This range touches `rtl/core/`
extensively, so the bitstream and stage2 binary are **regenerated**.
Integration changes needed:

- **RTL file list changed**: upstream added `neorv32_core_complex.vhd`
  and `neorv32_smc.vhd` and removed the memory-IP wrappers
  (`neorv32_{imem_rom,imem_ram,dmem_ram,bootrom_rom}.vhd`).
  `quartus/neorv32_demo.qsf` is now regenerated in the order of upstream
  `rtl/file_list_core.f`.
- **`0005` replaced by `0006`** after the real root cause of the #1556
  boot hang was found (see below).

Quartus: 4,480 LE on EP4CE10, timing in the usual range (CLOCK setup
−0.55 ns, S_CLK hold −0.97 ns — both on the SDRAM I/O pins; internal
paths have > +6 ns slack). stage2: 7,908 B.

**Active local patch (1):** `0006-rvs-stable-sc-request.patch` — a
one-line fix that **keeps upstream's #1556 address-tracking reservation
station** and only makes the SC bus request stable for the whole
transfer.

### Root cause of the #1556 boot hang (found 2026-10-09)

Upstream's reservation-station rework
([#1556](https://github.com/stnolting/neorv32/pull/1556), `0d0bb3e6`)
made our kernel hang in `vfs_caches_init()` (HW-bisected 2026-06-01).
The explanation we used until 2026-10 — "a reservation survives a
context switch, so a later `SC.W` spuriously succeeds" — was **wrong**:
Linux clears the reservation itself with a dummy `sc x0, a2, PT_EPC(sp)`
in `ret_from_exception` (`arch/riscv/kernel/entry.S`), and #1556's
"failed SC from the owning core clears the reservation" rule honours
that.

The real bug is in `neorv32_bus_amo_rvs::bus_request`, which is
**combinational** and converts an SC into a READ whenever
`sc_valid = '0'`:

1. In the STB cycle of a *successful* SC, `sc_valid = '1'` → the request
   goes out as a WRITE.
2. On that same clock edge `reservation_station` clears `rsv.set`
   (successful SC consumes the reservation).
3. From STB+1 on `sc_valid = '0'`, so `bus_request` rewrites the
   **still-pending** request to `rw = '0'`, `amo = '0'` — a READ.

`rtl/wb_sdram_ctrl.v` drives `sdram_ctrl.sel` from its registered
`pending` flag, and `sdram_ctrl` latches `wstrb = xbus_we ? xbus_sel : 0`
in `S_IDLE` — i.e. at STB+1 or later (later still if a refresh is
pending). It therefore sees a READ. **The SC returns `rd = 0` (success)
but the store never reaches SDRAM**, so every successful `cmpxchg` is
silently dropped. On hardware the printk ring buffer breaks on the very
first `printk` (the boot shows the `ABCD…S` debug markers but not a
single line of printk text, not even the banner) and the kernel finally
hangs in `vfs_caches_init()`.

This violates the XBUS waveform in upstream's own documentation
(`docs/datasheet/soc_xbus.adoc`: `xbus_adr_o`/`dat_o`/`we_o`/`sel_o`
stable from STB until ACK). It also explains every earlier observation:
the strict policy (`0005`) and our old `0004` worked because they only
gate the one-cycle STB and never touch `rw`; narrowing #1556's granule
to 4 bytes did not help; and a simulation memory that samples in the STB
cycle cannot reproduce it.

**Evidence (2026-10-09):**

| | AX301 hardware | GHDL sim, memory samples at STB+1 |
|---|---|---|
| `v1.13.6` unpatched | hangs after `S`, no printk output (2/2 runs) | only `ABCD`, no banner |
| `v1.13.6` + `0006` | `nommu#` 18 s, `amo` 11/11, `test_shell` 3/3 | banner at cycle 4,704,790 |
| `v1.13.6` + `0005` | `nommu#` 18 s | banner at cycle 4,704,790 |

The repo bitstream built with `0006` is byte-identical (md5
`554c7ad3…`) to the one verified on hardware above.

**Upstream:** not reported yet — on hold pending further analysis.

**Recently dropped (now upstream):**

- `0004-rvs-address-tracking.patch` — the *address-tracking* version is
  now upstream via `0d0bb3e6`
  ([#1556](https://github.com/stnolting/neorv32/pull/1556), 2026-05-16),
  so the patch file is gone. The companion commit `98c4ed6d` also made `fence` no longer
  invalidate the reservation, and the out-of-band `bus.fence` signal was
  removed (`560e3392`, `794ecad7`, `e0ea1027`).
- `0001-cpu-fence-i-drain-dcache.patch` — merged via
  [stnolting/neorv32#1540](https://github.com/stnolting/neorv32/pull/1540)
  (squash commit `9b1acf7e`, 2026-04-29). The maintainer kept the
  semantic fix (always assert `lsu_fence` in `opcode_fence_c` decode),
  comment shortened to one-liner with `#1540` link.
- `0002-cache-amo-flush.patch` — merged via the same PR. Maintainer
  factored the bypass-vs-cache-line decision into `S_CHECK` and
  reorganized the `pnd_bp` clear path; functionally equivalent to
  ours on AX301.
- `0003-cache-amo-counters.patch` — diagnostic counters at
  `0xFFAA0000`. Removed because the upstream cache.vhd refactor moved
  the FSM hook points the patch needed; the diagnostic served its
  purpose (helped pinpoint the seqcount-livelock root cause) and is
  recoverable from git history if a future investigation needs it.

**Why D-cache is off:** the new write-back D-cache architecture (PR
[#1513](https://github.com/stnolting/neorv32/pull/1513), 2026-04) is a
performance win when paired with **burst-capable** memory (the design
intent) but a net loss against our non-burst SDRAM controller. With
bursts disabled (`CACHE_BURSTS_EN = false` in `rtl/ax301_top.vhd`),
each dirty-line eviction emits 16 individual single-word stores, AMO
bypass costs a 16-word flush + invalidate every time, and reader
throughput on hot kernel paths drops far enough that
`ktime_get_coarse_real_ts64`'s seqcount retry loop never converges
(the read window spans a timer IRQ on every iteration → infinite
retry). Disabling D-cache entirely sidesteps the whole class of
problems and the kernel boots cleanly. To re-enable D-cache later,
we'd need to either (a) extend `wb_sdram_ctrl.v` to support burst
transfers, or (b) move to a memory backend that natively supports
bursts.

## Apply

After `git submodule update --init --recursive`, apply the one active
local patch before building the bitstream:

```bash
cd neorv32
for p in ../neorv32_patches/*.patch; do
    git apply "$p"
done
```

This applies `0006-rvs-stable-sc-request.patch` to
`rtl/core/neorv32_bus.vhd`. Without it every successful `SC.W` is
dropped by our SDRAM controller and the kernel hangs in
`vfs_caches_init()` (see Status above).

## Inventory

### `0006-rvs-stable-sc-request.patch` (ACTIVE — the kernel-boot fix)

**Targeted:** `rtl/core/neorv32_bus.vhd`, `neorv32_bus_amo_rvs::bus_request`.

**What it does:** uses the combinational `sc_valid` decision only in the
STB cycle and the already-registered `sc_pend_fail` (set with STB,
cleared with ACK) afterwards, so `rw`/`amo` of an SC request stay
constant until the response:

```vhdl
if (sc = '1') and (((core_req_i.stb = '1') and (sc_valid = '0')) or (sc_pend_fail = '1')) then
```

(plus `sc_pend_fail` in the process sensitivity list). Upstream's
address-tracking semantics, 64-byte granule, core-ID tracking and
turn-based SMP fairness are all kept unchanged.

**Status:** HW-verified on `v1.13.6+0006` (2026-10-09): `nommu#` 18 s,
`amo` 11/11 LR/SC/AMO, `test_shell` 3/3 (xmodem path; SD path not
re-run, no card in the slot).

## Inventory (historical)

### `0005-rvs-strict-clear-on-write.patch` (superseded by `0006`, 2026-10-09)

Reverted the whole `neorv32_bus_amo_rvs` back to the pre-#1556 strict
policy (`valid <= lr` on every data access; SC issues its write only
while `valid` is high, otherwise a local ACK + `rd = 1`). Carried from
2026-06-01 to 2026-10-09 and HW-verified on `b38162eb`, `644f0d10`,
`99fb4165` and `v1.13.6`. It worked because it never modified the
request once issued — **not** for the reason we gave at the time
(reservation surviving a context switch, see Status). It also threw
away upstream's address-tracking and was coupled to `DCACHE_EN => false`
(strict clearing livelocks the `ktime`/`mutex` paths with D-cache on);
`0006` has neither drawback.

### `0004-rvs-address-tracking.patch` (dropped — upstream via #1556)

**Targeted:** `rtl/core/neorv32_bus.vhd`, `neorv32_bus_amo_rvs::rvs_control`.

**Bug:** the reservation-set controller's `valid` flag was cleared by
*any* non-LR data memory access:

```vhdl
elsif (core_req_i.stb = '1') and (core_req_i.meta(0) = '0') then
  valid <= lr; -- clear for everything except LR
end if;
```

This was too strict. Linux's RISC-V atomics (`__cmpxchg_release` in
`arch/riscv/include/asm/cmpxchg.h`) use a tight `lr.w / bne / sc.w /
bnez 0b` retry loop that assumes ARM-style address-tracking
reservations: only stores to the reserved location clear `valid`.
Under the strict NEORV32 policy, **every IRQ trap entry pushes 32
registers onto the stack — those 32 stores each clear `valid`**, so
any timer interrupt taken between LR.W and SC.W made SC.W fail.
Combined with the higher bus traffic from the write-back D-cache,
the kernel's CAS retry never made forward progress and
`mutex_unlock(&clocksource_mutex)` inside
`clocksource_done_booting()` livelocked indefinitely.

**Fix (ours, now superseded by upstream):** record LR.W's word
address; clear `valid` only when (a) a fence fires, or (b) a store
hits the reserved word. The upstream `0d0bb3e6` fix is the same idea
at 64-byte granule, plus core-ID tracking and SMP fairness, and
without the fence-clear (fence no longer invalidates per `98c4ed6d`).

The RISC-V Zalrsc spec explicitly permits this relaxation: SC.W is
*required* to fail only on conflicting accesses to the reserved set
and on context switches; spurious clears are permitted but not
mandatory.

**Status:** verified on hardware while carried locally (2026-04-26 →
2026-05). Dropped 2026-06-01 when upstream `b38162eb` shipped an
equivalent address-tracking fix. Upstream's version then hung the boot
— not because of address-tracking, but because its `bus_request`
rewrites a successful SC into a READ after the STB cycle (see Status,
fixed by `0006`). `0004` never hit this because it only gated STB.

## Investigations that did NOT make it in

### "Cache writeback clears reservation" (rejected hypothesis, 2026-04-26)

After `0004` shipped, fs_initcall #04 (`init_pipe_fs`) still hung
inside `kern_mount`. We hypothesized that cache-writeback bus stores
were spuriously clearing the reservation — when the line containing
the reserved word evicts dirty, one of the 16 writeback stores
matches `rvs_addr_tag`. Spent a session plumbing a `wb_active`
side-band signal from `neorv32_cache` into `neorv32_bus_amo_rvs` to
mask writeback stores in the rvs check. GHDL TB extended with two
new scenarios; both passed. **Hardware test failed** — boot still
hung at the same point.

A brute-force test (modify `rvs_control` to NEVER clear `valid`
except on fence/reset) also did not unstick the boot, conclusively
ruling out the LR/SC reservation as the cause of the second hang.
The actual root cause turned out to be `ktime_get_coarse_real_ts64`'s
seqcount retry loop diverging under D-cache slowdown (see "Status"
section at top). The wb-active plumbing was reverted; this section
is preserved as a record so future debugging doesn't go back down
the same path.
