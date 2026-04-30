# NEORV32 patches

Patches against the upstream `neorv32` submodule that we carry locally
because they have not yet been merged upstream.

When/if these are accepted upstream, bump the `neorv32` submodule pointer
and remove the corresponding patch file.

## Status (2026-04-30)

**Submodule pin:** `70393ec4` (`origin/main` HEAD, v1.13.0.1; +24
commits past the `#1540` merge at `9b1acf7e` — image_gen / common.mk
refactor + minor RTL fixes; none touch `rtl/core/neorv32_bus.vhd`, so
`0004` still applies cleanly).

**Active local patch:** only `0004-rvs-address-tracking.patch`. With
this patch applied + `DCACHE_EN => false` in `rtl/ax301_top.vhd`, the
kernel boots to `nommu#` shell in ~20 s wall time (after the SDRAM
controller optims in `rtl/sdram_ctrl.v`; was ~36 s before those).

**Recently dropped (now upstream):**

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

`0004` matters even with D-cache off, because IRQ trap-entry stack
pushes still go through the bus and would still clear the
reservation under the upstream strict policy.

## Apply

```bash
cd neorv32
for p in ../neorv32_patches/*.patch; do
    git apply "$p"
done
```

## Inventory

### `0004-rvs-address-tracking.patch` (the kernel-boot fix)

**Targets:** `rtl/core/neorv32_bus.vhd`, `neorv32_bus_amo_rvs::rvs_control`.

**Bug:** the reservation-set controller's `valid` flag is cleared by
*any* non-LR data memory access:

```vhdl
elsif (core_req_i.stb = '1') and (core_req_i.meta(0) = '0') then
  valid <= lr; -- clear for everything except LR
end if;
```

This is too strict. Linux's RISC-V atomics (`__cmpxchg_release` in
`arch/riscv/include/asm/cmpxchg.h`) use a tight `lr.w / bne / sc.w /
bnez 0b` retry loop that assumes ARM-style address-tracking
reservations: only stores to the reserved location clear `valid`.
Under the strict NEORV32 policy, **every IRQ trap entry pushes 32
registers onto the stack — those 32 stores each clear `valid`**, so
any timer interrupt taken between LR.W and SC.W makes SC.W fail.
Combined with the higher bus traffic from the write-back D-cache,
the kernel's CAS retry never makes forward progress and
`mutex_unlock(&clocksource_mutex)` inside
`clocksource_done_booting()` livelocks indefinitely.

**Bisect / proof:**
- `git diff v1.12.9 origin/main -- rtl/core/neorv32_bus.vhd` is empty
  → bus reservation logic is unchanged. The latent-bug-meets-write-back
  combo is what made it manifest after `f774dac6`.
- The (now-removed) 0003 diagnostic counters showed `amo_hit_dirty`
  plateaus at ~9200 events for the entire boot (not the originally-
  suspected hot path), and direct-UART `!MU` markers around
  `__mutex_unlock_fast`'s CAS loop confirmed `before_fast` fires but
  `after_fast_ok` never does.

**Fix:** record LR.W's word address; clear `valid` only when (a) a
fence fires, or (b) a store hits the reserved word.

```vhdl
signal rvs_addr_tag : std_ulogic_vector(31 downto 2);
…
if (lr = '1') then
  valid        <= '1';
  rvs_addr_tag <= core_req_i.addr(31 downto 2);
elsif (core_req_i.rw = '1') and (valid = '1') and
      (core_req_i.addr(31 downto 2) = rvs_addr_tag) then
  valid <= '0';
end if;
-- reads, and stores to other addresses, leave `valid` untouched
```

The RISC-V Zalrsc spec explicitly permits this relaxation: SC.W is
*required* to fail only on conflicting accesses to the reserved set
and on context switches; spurious clears are permitted but not
mandatory.

**TB:** `neorv32/sim/tb_bus_amo_rvs.vhd` covers 6 scenarios (LR+SC
same address, LR+intermediate read, LR+32 stores to other addresses
simulating IRQ stack push, LR+store-to-reserved-word, LR+I-fetches,
LR+fence). All pass under GHDL.

**Cost:** 30 FF + 30-bit comparator. Negligible on EP4CE6/EP4CE10.

**Status:** verified on hardware. Required for any kernel that uses
LR/SC-based atomics under interrupt load — including with D-cache
off, since IRQ stack pushes still pass through `amo_rvs` whether or
not the cache is enabled. Not yet upstreamed (different scope from
PR #1540); left local for now.

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
