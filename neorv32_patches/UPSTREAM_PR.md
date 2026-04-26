# PR: Two write-back D-cache correctness fixes

Two coherence bugs in the cache architecture introduced by #1513
(write-back & write-allocate). Found while bumping the `neorv32`
submodule in [see_neorv32_run_linux](https://github.com/14sea/see_neorv32_run_linux)
from `v1.12.9` to `origin/main` for an FPGA Linux boot.

The bugs are independent and small. Squashed into one PR because
they both stem from the same write-back-vs-bypass interaction and
neither boots Linux without the other.

---

## 1. `fence.i` must drain the D-cache (Zifencei)

**File:** `rtl/core/neorv32_cpu_control.vhd`, `opcode_fence_c` decode.

`Zifencei` requires that prior stores to instruction memory are made
visible to subsequent instruction fetches. The current decode asserts
`if_fence` (I-cache invalidate) but **not** `lsu_fence` (D-cache
drain) for `fence.i`:

```vhdl
when opcode_fence_c =>
  ctrl_nxt.lsu_fence <= not exec.ir(instr_funct3_lsb_c);  -- data fence
  ctrl_nxt.if_fence  <=     exec.ir(instr_funct3_lsb_c);  -- instruction fence
```

Pre-#1513 this was harmless because the D-cache was write-through.
Post-#1513 dirty stores can sit in the D-cache indefinitely, so the
I-cache refill reads stale memory and the CPU executes garbage.

### Bisect

`git bisect` between `v1.12.9` (good) and `origin/main` (bad) on a
write-then-jump-to-RAM testcase identifies `f774dac6 [cache] first
draft of write-back architecture` as the first bad commit. The bus
reservation logic, CPU control, and instruction fetch are byte-identical
across the bisect window — only the cache write policy changed.

### Reproducer

1. Build a NEORV32 SoC with `ICACHE_EN=true`, `DCACHE_EN=true`,
   write-back cache (current default), and external memory via XBUS.
2. From software running in IMEM/local memory: write a few RV32
   instructions to a cached external-RAM address, issue `fence.i`,
   `jalr` to that address.
3. CPU hangs on the first I-fetch from external RAM.

Disabling D-cache makes the test pass — confirms the dirty stores
never reach memory before the I-cache refills.

### Fix

Always assert `lsu_fence` for `opcode_fence_c`; leave `if_fence`
gated on `funct3 LSB` so plain `fence` still does not bother I.

```vhdl
when opcode_fence_c =>
  ctrl_nxt.lsu_fence <= '1';                            -- always drain D
  ctrl_nxt.if_fence  <= exec.ir(instr_funct3_lsb_c);    -- only fence.i invalidates I
```

One-line cost; no FF/LE change.

---

## 2. AMO/uncached bypass must coordinate with dirty cache lines

**File:** `rtl/core/neorv32_cache.vhd`, `S_CHECK` / `S_BYPASS` /
`S_WRITE_DONE`.

AMO and uncached requests bypass the cache (`pnd_bp = 1` →
`S_BYPASS`). With a write-back D-cache that breaks coherence in
two directions:

1. **Bypass-read sees stale memory.** A regular store can leave the
   target word dirty in the cache; the AMO bypass reads memory and
   gets the pre-store value.
2. **Cache holds stale value after bypass-write.** The AMO bypass
   writes memory; the cache still holds the pre-AMO value, so a
   later plain load returns it.

Linux exercises this on every spinlock / atomic / rwsem update.
Without the fix the kernel hits `bad: scheduling from the idle
thread!` loops and lock deadlocks during early boot; with D-cache
disabled the same kernel boots cleanly.

### Fix

Before falling through to `S_BYPASS` from `S_CHECK`, look at the
cache line at the same index:

| State                | Action                                                           |
| -------------------- | ---------------------------------------------------------------- |
| HIT, dirty           | Write the line back to memory (`S_WRITE_START` → `S_WRITE_DONE`), invalidate, then bypass. |
| HIT, clean           | Invalidate the line inline, then bypass. (Memory is already current.) |
| MISS                 | Bypass directly. (No cache state to repair.)                     |

`pnd_bp` becomes a sticky flag (default `<= ctrl.pnd_bp` instead of
`<= '0'`) so it survives the write-back detour, and is cleared in
`S_BYPASS` on ACK. `S_WRITE_DONE` gains an `elsif (ctrl.pnd_bp = '1')`
branch that invalidates the line and re-arms the bypass.

`READ_ONLY=true` (I-cache) is untouched: there is no bypass-write
case to fix, and the I-cache never has dirty lines to write back.

### Cost

A handful of LE for the new `S_WRITE_DONE → S_BYPASS` transition and
one extra mux on `pnd_bp`. No new RAM, no new ports.

---

## Validation

- `processor_check` build with both fixes applied: pass (no regression
  on the existing AMO and fence tests).
- The full diff against `origin/main` plus a kernel-side rvs fix lets
  Linux 6.6.83 boot to a userspace shell on a Cyclone-IV EP4CE6
  FPGA, ~36 s wall time.
- `wb_active` plumbing was investigated as a possible third fix
  (mask cache writebacks in the rvs check) and ruled out by hardware
  test — not part of this PR.

## Carry path

Both patches live as standalone diffs against current `origin/main`
in [see_neorv32_run_linux/neorv32_patches/](https://github.com/14sea/see_neorv32_run_linux/tree/main/neorv32_patches):

- `0001-cpu-fence-i-drain-dcache.patch`
- `0002-cache-amo-flush.patch`

Happy to split into two PRs if preferred — the fixes are independent
and either applies cleanly without the other.
