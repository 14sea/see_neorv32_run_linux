# NEORV32 patches

Patches against the upstream `neorv32` submodule that we carry locally
because they have not yet been merged upstream.

When/if these are accepted upstream, bump the `neorv32` submodule pointer
and remove the corresponding patch file.

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

### `0002-cache-amo-flush.patch` (companion to 0001)

**Targets:** `rtl/core/neorv32_cache.vhd`, `S_CHECK` / `S_BYPASS` / `S_WRITE_DONE`.

**Bug:** AMO requests bypass the cache (jump straight to the bus), but with
a write-back D-cache that breaks coherence: a regular store can leave
dirty data in the cache while the AMO reads stale memory, and after the
AMO writes memory the cache still holds the pre-AMO value, so a later
plain load returns the stale cached copy. Linux uses AMO for every
spinlock / atomic / rwsem update, so this manifests as random scheduler
errors (`bad: scheduling from the idle thread!` loops) and lock
deadlocks during boot once the regression is in play.

**Fix:** before bypassing for an AMO/uncached request, look at the cache
line at the same index:
- HIT, dirty → write the line back to memory first, invalidate, then
  bypass (so the AMO sees the latest memory and no stale copy is left).
- HIT, clean → invalidate inline, then bypass (memory is already current,
  but the cached copy would go stale once the AMO writes memory).
- MISS → bypass directly (no cache state to repair).

`pnd_bp` becomes a sticky flag so the bypass survives the write-back
detour, and is cleared on `S_BYPASS` ACK.

**Status (2026-04-26):** functionally correct *for early boot* (no more
"scheduling from idle" oops loop, kernel reaches `start_kernel →
do_initcalls → fs_initcall level 5`), but introduces a hard hang at
`mutex_unlock(&clocksource_mutex)` inside `clocksource_done_booting()`
(initcall #46 on a current build). Boot reliably reaches the marker
sequence `ABCDE` (function entry, mutex_lock, finished_booting=1,
__clocksource_watchdog_kthread, clocksource_select-with-Switched
printk) and then never emits `F` (the marker right after mutex_unlock).

**Verified NOT the cause** (each tested by reverting one workaround at
a time on top of `kernel/neorv32_nommu.patch`):
- single-shot `schedule()` / `schedule_preempt_disabled()` workaround
- synchronous `synchronize_srcu()` rewrite (TINY_SRCU)
- 120s `wait_event_timeout` in `async_synchronize_cookie_domain()`

Bypassing the offending `mutex_unlock()` with a raw
`atomic_long_set(&clocksource_mutex.owner, 0)` lets the function
return, but each subsequent initcall then takes ~70–100 s of *kernel
time* to print its END marker (4× wall slowdown on top of that).

**Hypothesis:** the bypass-and-flush strategy makes every AMO/uncached
request invalidate-then-bypass an entire 16-word line, and on hot
locking paths (`mutex_unlock`'s `wake_up_q` → `try_to_wake_up`) the
combined LR/SC + repeated cacheline write-back/invalidate creates a
livelock-shaped hot loop that stalls for many minutes. The patch is
therefore a *diagnostic stepping stone*, not a real fix.

**Next step (planned for the next session):** rework as a
cache-coherent AMO that runs *through* the cache (lookup → if hit,
update line in place + write-through to bus; if miss, refill or
promote bypass to a single read-modify-write that doesn't evict the
whole line). Until then, do **not** try to bump the `neorv32`
submodule with this patch in production — boot will hang at
clocksource_done_booting.

Detailed diagnostic walk (initcall trace, A-F markers in
`clocksource_done_booting`, mutex slowpath markers, raw UART bypass
via direct `0xFFF50000` writes) lives in the conversation log of the
2026-04-25/26 session, not in-tree.

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
to the same memory (boot loaders, JIT, kernel module loading, ...).

**Bisect:** `git bisect` between `v1.12.9` (good) and `origin/main`
(bad) on `see_neorv32_run_linux`'s xmodem boot identifies
`f774dac6` as the first bad commit.

**Reproducer (without this patch):**
1. Build a NEORV32 SoC with both I-cache and D-cache enabled, write-back
   cache (the only mode in current upstream), and external RAM via XBUS.
2. From software running in IMEM/local memory, write a few RV32 instructions
   to a cached external-RAM address, issue `fence.i`, then `jalr` to that
   address. Observe the CPU hangs on the first I-fetch from external RAM.

**Fix:** make `fence.i` also drain the D-cache. One-line change in the
decoder (always assert `lsu_fence` for `opcode_fence_c`; `if_fence`
remains gated on `funct3 LSB` so plain `fence` still doesn't bother
the I-cache).

**Status:** local-only; not yet sent upstream. Holding for the AMO
coherence fix (`0002-cache-amo-flush.patch`) and a kernel-side audit.
