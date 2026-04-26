# NEORV32 patches

Patches against the upstream `neorv32` submodule that we carry locally
because they have not yet been merged upstream.

When/if these are accepted upstream, bump the `neorv32` submodule pointer
and remove the corresponding patch file.

## Status (2026-04-26)

**Goal 1 reached.** Bumped `neorv32` submodule (detached at `29739a83`,
post-`origin/main`) + patches `0001`, `0002`, `0004` boot Linux to
`nommu#` shell with `DCACHE_EN => false`, in ~36 s wall time (3× faster
than the pre-bump v1.12.9 baseline of ~118 s).

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

The three RTL patches below are still **needed** at the bumped
submodule pointer because the failure modes they fix are CPU/bus
issues independent of D-cache: `0001` fixes a `fence.i` semantics
bug, `0002` fixes AMO/uncached coherence, and `0004` fixes the LR/SC
reservation policy. With D-cache off, `0001` and `0002` are no-ops
in practice (no dirty lines means no drain or flush is ever needed),
but they're correctness fixes that should be upstreamed regardless.
`0004` matters even with D-cache off, because IRQ trap-entry stack
pushes still go through the bus and would still clear the
reservation under the strict policy.

## Apply

The submodule is currently pinned at `v1.12.9`, which doesn't need any
of these patches. The patches here apply on top of `origin/main` (commits
after `v1.12.9`) and are required to bump the submodule.

```bash
cd neorv32
for p in ../neorv32_patches/*.patch; do
    git apply "$p"
done
```

## Inventory

### `0001-cpu-fence-i-drain-dcache.patch`

**Targets:** `rtl/core/neorv32_cpu_control.vhd`, opcode_fence_c decode.

**Bug:** Since commit `f774dac6 [cache] first draft of write-back
architecture`, the cache implements write-back. The CPU's `fence.i`
decoder asserts only `if_fence` (I-cache invalidate) but not
`lsu_fence` (D-cache drain). This violates the RISC-V `Zifencei`
guarantee that *stores to instruction memory are made visible to
subsequent instruction fetches* — with a write-back D-cache, the
dirty stores never reach memory before the I-cache refills, so the
I-cache reads stale memory and the CPU executes garbage.

In `v1.12.9` the cache was write-through, so the missing D-cache
drain happened to be harmless. Once write-back landed, this latent
bug became fatal for any code that writes to memory and then jumps
to the same memory (boot loaders, JIT, kernel module loading, …).

**Bisect:** `git bisect` between `v1.12.9` (good) and `origin/main`
(bad) on `see_neorv32_run_linux`'s xmodem boot identifies
`f774dac6` as the first bad commit.

**Fix:** make `fence.i` also drain the D-cache. One-line change in
the decoder (always assert `lsu_fence` for `opcode_fence_c`;
`if_fence` remains gated on `funct3 LSB` so plain `fence` still
doesn't bother the I-cache).

**Status:** verified on hardware. Independently correct, should be
upstreamed regardless of which D-cache write policy is in effect.
With our production `DCACHE_EN => false` it is a no-op (no D-cache
to drain), but with D-cache on it is required for correctness.

### `0002-cache-amo-flush.patch`

**Targets:** `rtl/core/neorv32_cache.vhd`, `S_CHECK` / `S_BYPASS` /
`S_WRITE_DONE`.

**Bug:** AMO requests bypass the cache (jump straight to the bus),
but with a write-back D-cache that breaks coherence: a regular store
can leave dirty data in the cache while the AMO reads stale memory,
and after the AMO writes memory the cache still holds the pre-AMO
value, so a later plain load returns the stale cached copy.

**Fix:** before bypassing for an AMO/uncached request, look at the
cache line at the same index:
- HIT, dirty → write the line back to memory first, invalidate, then
  bypass (so the AMO sees the latest memory and no stale copy is left).
- HIT, clean → invalidate inline, then bypass (memory is already current,
  but the cached copy would go stale once the AMO writes memory).
- MISS → bypass directly (no cache state to repair).

`pnd_bp` becomes a sticky flag so the bypass survives the write-back
detour, and is cleared on `S_BYPASS` ACK.

**Status:** verified on hardware. Like `0001`, this is independently
correct and should be upstreamed. With our production
`DCACHE_EN => false` it is a no-op (`pnd_bp` is always a clean miss);
with D-cache on it is required for correctness.

### `0003-cache-amo-counters.patch` (diagnostic, optional)

**Targets:** `rtl/core/neorv32_cache.vhd`.

**Purpose:** Six 32-bit performance counters + a memory-mapped read
aperture at `0xFFAA0000` to quantify the AMO/uncached coherence-flush
hot path that `0002` introduces. Diagnostic-only; this patch does not
change boot behaviour by itself.

**Counters** (incremented in cache `S_CHECK` / writeback FSM):

| Slot (offset) | Name              | Trigger                                                                  |
| ------------- | ----------------- | ------------------------------------------------------------------------ |
| `0xFFAA0000`  | `amo_total`       | Any S_CHECK with `pnd_bp=1` (AMO or uncached load/store)                 |
| `0xFFAA0004`  | `amo_hit_dirty`   | `pnd_bp=1` AND cache hit AND dirty → goes through write-back-then-bypass |
| `0xFFAA0008`  | `amo_hit_clean`   | `pnd_bp=1` AND cache hit AND clean → invalidated then bypassed           |
| `0xFFAA000C`  | `amo_miss`        | `pnd_bp=1` AND cache miss → direct bypass                                |
| `0xFFAA0010`  | `wb_total`        | Any entry into `S_WRITE_START` (AMO flush + ordinary dirty eviction)     |
| `0xFFAA0014`  | `wb_cycles`       | One per cycle while FSM is in any `S_WRITE_*` state                      |

**Aperture protocol:** the cache decodes `0xFFAA0000..0xFFAA001F` in
`S_CHECK` before falling through to `S_BYPASS`. Reads return the
indexed counter (mux on `addr(4:2)`) and ack in one cycle without
issuing a bus request. Any write to that range resets all six counters.
The address is in NEORV32 uncached space (`UC_BEGIN=0xF`) so it always
hits the bypass decision and never pollutes the cache.

**Status:** this patch was instrumental in disproving the original
"AMO+dirty hot path causes livelock" hypothesis — `amo_hit_dirty`
plateaued at ~9200 events for the entire boot, far below what a hot
path would show. It then helped confirm the actual root cause (LR/SC
livelock from strict reservation policy, fixed by `0004`) and the
later D-cache-vs-seqcount story. Optional in production; drop if
the SoC is bus-error-free without it.

**Cost:** ~150 LE on EP4CE10 (6×32 FF + 32-bit 4:1 mux + 27-bit
prefix comparator + writeback-state OR).

**Removal:** Once the diagnostic is no longer wanted, drop this
patch. `0xFFAA0000` returns to "uncached space that no SoC peripheral
decodes" → bus error on access, harmless if nothing reads it.

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
- 0003 diagnostic counters showed `amo_hit_dirty` plateaus at ~9200
  events for the entire boot (not the originally-suspected hot path),
  and direct-UART `!MU` markers around `__mutex_unlock_fast`'s CAS
  loop confirmed `before_fast` fires but `after_fast_ok` never does.

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
not the cache is enabled.

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
