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

**Status:** local-only; not yet sent upstream. Holding because we still
have to debug a separate kernel "scheduling from idle thread" issue
that surfaces on the post-`v1.12.9` tree once this patch lets the
kernel start.
