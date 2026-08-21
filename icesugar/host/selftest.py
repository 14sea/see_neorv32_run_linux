#!/usr/bin/env python3
"""
Non-interactive bring-up self-test for NEORV32 on iCESugar.

Drives the bootloader, uploads the demo, runs it, and checks the answers -
the scripted counterpart of the interactive host/upload.py.
"""

import argparse
import os
import sys
import time

import serial

HERE = os.path.dirname(__file__)
DEFAULT_EXE = os.path.join(HERE, "..", "sw", "hello", "neorv32_exe.bin")


def rd(ser, marker, timeout, label):
    marker = marker.encode() if isinstance(marker, str) else marker
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        c = ser.read(256)
        if c:
            buf += c
            if marker in buf:
                return buf.decode(errors="replace")
    raise TimeoutError(f"timeout waiting for {label!r}; got: {buf[-200:]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=19200)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    args = ap.parse_args()

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        time.sleep(0.3)
        ser.reset_input_buffer()

        # --- bootloader prompt ---
        print("\n=== 1. bootloader ===")
        # The first key press aborts the 8 s auto-boot countdown; the prompt
        # only comes back on the following read.  Right after a reconfiguration
        # the FPGA may also still be coming up - so retry a few times.
        for attempt in range(6):
            ser.write(b"\r"); ser.flush()
            try:
                out = rd(ser, "CMD:>", 3, "CMD prompt")
                if attempt:
                    print(f"  (prompt after {attempt + 1} attempts)")
                break
            except TimeoutError:
                continue
        else:
            raise TimeoutError("no CMD:> prompt - is the FPGA configured?")
        check("bootloader prompt", True)

        # --- bootloader system info ---
        ser.write(b"i"); ser.flush()
        info = rd(ser, "CMD:>", 5, "system info")
        print(info.strip())
        clk_line = [l for l in info.splitlines() if l.startswith("CLK:")]
        clk_hz = None
        if clk_line:
            try:
                clk_hz = int(clk_line[0].split()[1], 16)   # e.g. "0x00B71B00"
            except ValueError:
                pass
        check("clock = 12 MHz", clk_hz == 12_000_000,
              f"{clk_hz} Hz" if clk_hz else "could not parse CLK line")

        # --- upload ---
        print("\n=== 2. upload ===")
        with open(args.exe, "rb") as f:
            data = f.read()
        ser.write(b"u"); ser.flush()
        rd(ser, "Awaiting", 5, "'Awaiting'")
        t0 = time.time()
        for i in range(0, len(data), 64):
            ser.write(data[i:i + 64]); ser.flush()
        out = rd(ser, "\n", 30, "upload result")
        dt = time.time() - t0
        check(f"upload {len(data)} B in {dt:.1f}s", "OK" in out, out.strip())

        # --- run ---
        print("\n=== 3. run ===")
        ser.write(b"e"); ser.flush()
        # the demo prints its banner, sweeps the LED (600 ms) and runs the SPRAM
        # test before showing the prompt - sync on the prompt, not on a timeout
        banner = rd(ser, "icesugar>", 20, "demo prompt")
        print(banner.strip())

        check("demo banner", "NEORV32 on iCESugar" in banner)
        check("IMEM = 65536 B (2x SPRAM)", "IMEM      : 65536 bytes" in banner)
        check("DMEM = 65536 B (2x SPRAM)", "DMEM      : 65536 bytes" in banner)
        check("clock reported as 12000000 Hz", "Clock     : 12000000 Hz" in banner)
        # NOTE: this only proves the firmware RAN the sweep. Whether photons came
        # out cannot be checked over UART - see the manual step at the end.
        check("RGB LED sweep code executed", "RGB LED sweep... done" in banner)
        check("SPRAM byte/half-word test", "SPRAM test... PASS" in banner)

        # --- interactive commands ---
        print("\n=== 4. commands ===")
        ser.write(b"m"); ser.flush()
        out = rd(ser, "icesugar>", 15, "'m' result")
        print(out.strip())
        check("'m' SPRAM re-test", "PASS" in out)

        ser.write(b"t"); ser.flush()
        out = rd(ser, "icesugar>", 20, "'t' result")   # busy-waits a full second
        print(out.strip())
        ticks_ok = False
        for line in out.splitlines():
            if "CLINT ticks" in line:
                try:
                    n = int(line.split(":")[1].split("(")[0].strip())
                    ticks_ok = 11_000_000 < n < 13_000_000
                except (IndexError, ValueError):
                    pass
        check("'t' CLINT ~12M ticks/s", ticks_ok)

    print("\n=== 5. RGB LED (manual) ===")
    print("  Not automatable over UART. Run this and confirm the colours:")
    print("    python3 host/ledcheck.py")

    print("\n" + "=" * 52)
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"  {npass}/{len(results)} checks passed")
    print("=" * 52)
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
