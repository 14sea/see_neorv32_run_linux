# NEORV32 patches

Patches against the upstream `neorv32` submodule that we carry locally
because they have not yet been merged upstream.

When/if these are accepted upstream, bump the `neorv32` submodule pointer
and remove the corresponding patch file.

## Status (2026-06-03)

**Submodule pin:** `644f0d10` (`origin/main` HEAD, v1.13.1-31; +153
commits past the previous `70393ec4` pin — includes the v1.13.1
release, the reservation-station rework, trap-CSR rework, `time[h]`
CSR support, `Zbc`/full-`Zihpm` additions, OCD rework, and a
configurable uncached address space). The last 9 commits past
`b38162eb` are docs / `sw/example` / newlib / OCD only — **zero
`rtl/core/` changes**, so the bitstream and stage2 binary are
byte-identical across `b38162eb..644f0d10`.

**Active local patch (1):** `0005-rvs-strict-clear-on-write.patch`.

The "zero local patches" milestone at `b38162eb` **did not hold**:
upstream's reservation-station rework ([#1556](https://github.com/stnolting/neorv32/pull/1556),
`0d0bb3e6`) replaced the old strict reservation policy with
address-tracking (clear the reservation only on a *write* hitting the
64-byte granule; reads and other-address writes leave it intact; fence
no longer clears it). That is correct for an SMP coherent system, but
**regresses our single-hart, no-coherence core**: a reservation now
survives a context switch / preemption, so an `SC.W` that should fail
spuriously succeeds → lock/refcount corruption → boot hangs in
`vfs_caches_init()`. HW-bisected to exactly `0d0bb3e6` (2026-06-01).

`0005` restores the pre-#1556 **strict policy** — `valid <= lr`: set on
`LR.W`, cleared by *any* other data memory access (including the stores
of a trap-entry register push, which is exactly when a preempted
reservation must die). This is the same strict semantics the old
`0004` patch originally *replaced*; the difference is **D-cache state**
(see the coupling note below). HW-verified to `nommu#` (Uptime 18 s) on
`b38162eb+0005` and again on `644f0d10+0005` (2026-06-03).

> **Strict vs. address-tracking is coupled to D-cache on/off.** With
> D-cache **on**, the strict policy livelocks `ktime`/`mutex` hot paths
> (the original motivation for `0004`'s address-tracking — see the
> historical inventory below). With D-cache **off** (our shipping
> config, `DCACHE_EN => false`), that livelock does not occur, and the
> strict policy is *required* for context-switch safety. Since we ship
> D-cache off, strict (`0005`) is the correct choice. If D-cache is ever
> re-enabled (needs burst SDRAM first), this trade-off must be revisited
> — likely re-adopting upstream address-tracking **plus** an explicit
> reservation-clear on trap entry.

**Candidate upstream report:** #1556 address-tracking may regress
single-hart, no-coherence cores that rely on the bus reservation being
cleared by trap-entry stores for preemption safety.

**Recently dropped (now upstream):**

- `0004-rvs-address-tracking.patch` — the *address-tracking* version is
  now upstream via `0d0bb3e6`
  ([#1556](https://github.com/stnolting/neorv32/pull/1556), 2026-05-16),
  so the patch file is gone. But see above: for our D-cache-off config
  we do **not** want address-tracking, we want strict clearing, hence
  `0005`. The companion commit `98c4ed6d` also made `fence` no longer
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

This applies `0005-rvs-strict-clear-on-write.patch` to
`rtl/core/neorv32_bus.vhd`. Without it the kernel hangs in
`vfs_caches_init()` (see Status above).

## Inventory

### `0005-rvs-strict-clear-on-write.patch` (ACTIVE — the kernel-boot fix)

**Targeted:** `rtl/core/neorv32_bus.vhd`, `neorv32_bus_amo_rvs`.

**What it does:** reverts upstream #1556's address-tracking reservation
station back to the pre-#1556 strict policy. The whole
`neorv32_bus_amo_rvs` architecture is rewritten to the simpler form: a
single global `valid` flag, `valid <= lr` on every data access (set only
by `LR.W`, cleared by everything else), SC issues its bus write only
while `valid` is high, and a local ACK + `rd=1` is generated when SC
fails. No 64-byte granule, no core-ID, no turn-based fairness — none of
which our single-hart core needs.

**Why:** see the Status section — upstream address-tracking lets a
reservation survive a context switch on our no-coherence core, causing
spurious SC success and a hang in `vfs_caches_init()`. Strict clearing
kills the reservation on the trap-entry register-push stores, which is
exactly when a preempted reservation must die. Safe because RISC-V
forbids memory accesses between `LR` and `SC`, so the only in-window
writes come from a trap/preemption.

**Status:** HW-verified `nommu#` (Uptime 18 s) on both `b38162eb+0005`
and `644f0d10+0005` (2026-06-03). Valid only while `DCACHE_EN => false`.

**Future work — the "proper" upstream-mergeable fix (deferred 2026-06-03).**
`0005` works by reverting to strict clearing, which is fine for our
single-hart, D-cache-off config but throws away upstream's
address-tracking (needed for SMP coherence, and for D-cache once we have
burst SDRAM). The root cause is narrower than "address-tracking is
wrong": it is that **#1556 removed every implicit reservation-clear on a
trap / context switch** (the trap-entry register-push stores no longer
hit the reserved granule, and `98c4ed6d` made `fence` stop clearing too),
while RISC-V Linux — unlike ARM, which issues `CLREX` in `__switch_to` —
relies on the *hardware* clearing the reservation across a context
switch. So an LR's reservation can outlive a preemption and let a later
`SC.W` spuriously succeed.

The general fix that keeps address-tracking is therefore: **clear the
reservation on trap entry in hardware** — route a one-cycle pulse from
the CPU's trap/exception-taken logic (`neorv32_cpu_control`, the same
event that vectors to `mtvec`) into `neorv32_bus_amo_rvs` and force
`valid <= '0'`. That satisfies both worlds: address-tracking still gates
SMP/coherent stores, *and* every context switch kills the reservation as
the OS assumes. It is the equivalent of ARM's `CLREX`-on-exception, done
in hardware.

Why deferred, not done now:
- More invasive than `0005`: #1556 deliberately removed the out-of-band
  `bus.fence` side-band (`560e3392`/`794ecad7`/`e0ea1027`), so a new
  trap signal would have to be re-plumbed CPU → bus, against the grain of
  that cleanup.
- Needs upstream buy-in to avoid re-forking; `0005` is the same strict
  semantics the core shipped pre-#1556, so it is the lower-risk hold.
- For our shipping config (single-hart, D-cache off) `0005` is already a
  complete and correct fix for the spurious-SC-success — there is no
  residual failure window to chase.

Revisit this when: (a) re-enabling D-cache (after burst SDRAM lands), at
which point strict clearing livelocks `ktime`/`mutex` and address-tracking
+ trap-clear becomes mandatory; or (b) pursuing the zero-local-patch goal
by upstreaming the trap-clear pulse to stnolting/neorv32.

## Inventory (historical)

### `0004-rvs-address-tracking.patch` (superseded — see `0005`)

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
equivalent address-tracking fix. **However**, with D-cache off the
address-tracking semantics regress preemption safety (reservation
survives context switch), so `0005` reverts upstream back to the strict
policy that `0004` originally replaced. The `0004`→strict→`0005` arc is
not a contradiction: `0004`'s address-tracking was needed *because
D-cache was on at the time*; with D-cache off the strict policy is both
sufficient and required.

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
