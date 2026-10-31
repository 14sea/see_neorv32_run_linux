#!/usr/bin/env python3
"""
fencei_test.py — fence.i vs D-cache coherence test via stage2 mode 'f'.

Only meaningful on a bitstream with DCACHE_EN => true (the default build has
the D-cache off, so both counts are always 0). The stage2 test assumes a
4 KB direct-mapped D-cache with 64-byte blocks (DCACHE_NUM_BLOCKS => 64).

Each of the 32 rounds dirties D-cache blocks 0..62, stores "li a0,k; ret"
into a block that maps to index 63 (written back last by the D-cache sync),
then calls it after either a plain fence.i or fence + load + fence.i.
A stale instruction fetch returns an older round's k and is counted.

Build the test stage2 (the default stage2 does not contain mode 'f'):
  make -C sw/stage2_loader NEORV32_HOME=../../neorv32 clean
  make -C sw/stage2_loader NEORV32_HOME=../../neorv32 \\
       USER_FLAGS+=-DFENCEI_TEST USER_FLAGS+=-Wl,--defsym,__neorv32_ram_size=4k exe
(ram_size=4k also runs on the 8 KB-DMEM build; the D-cache experiment needs
DMEM cut to 4 KB to free M9Ks.)

Example:
  python3 host/fencei_test.py --rbf dcache.rbf \\
          --stage2 sw/stage2_loader/neorv32_exe.bin --runs 3
Expected: unpatched NEORV32 v1.13.7 with D-cache: "fence.i=0x00000020"
(32/32 stale); "fence+lw+fence.i" always 0.
"""
import argparse, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sd_proto import setup_session

REPO = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--rbf", default=os.path.join(REPO, "output", "neorv32_demo.rbf"),
                    help="bitstream to program (should have DCACHE_EN => true)")
    ap.add_argument("--stage2", required=True,
                    help="stage2 built with -DFENCEI_TEST")
    ap.add_argument("--runs", type=int, default=1, help="how many times to run mode 'f'")
    args = ap.parse_args()

    loader = os.path.join(REPO, "tools", "openFPGALoader", "build", "openFPGALoader")
    print(f"[1] Programming FPGA: {args.rbf}")
    r = subprocess.run([loader, "-c", "usb-blaster", args.rbf],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        print("[!]", r.stderr[-300:]); sys.exit(1)
    time.sleep(1.0)

    ser = setup_session(REPO, args.port, stage2_path=args.stage2, skip_program=True)
    ser.timeout = 0.2
    # stage2 waits only ~3 s for the first mode byte: send 'f' right away
    for n in range(args.runs):
        ser.write(b"f"); ser.flush()
        buf = b""; t0 = time.time()
        while time.time() - t0 < 20 and b"ready for next cmd" not in buf:
            buf += ser.read(256)
        txt = buf.decode("ascii", "replace").replace("\r", "")
        lines = [l for l in txt.split("\n") if "[fencei]" in l]
        if not lines:
            print(f"run {n + 1}: [!] no result (stage2 without -DFENCEI_TEST, or hung): {txt[-200:]!r}")
            sys.exit(1)
        print(f"run {n + 1}: {lines[0]}")
    ser.close()


if __name__ == "__main__":
    main()
