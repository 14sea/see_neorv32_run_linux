#!/usr/bin/env python3
"""
Manual RGB LED check for NEORV32 on iCESugar.

The LED cannot be verified over UART, so this lights one channel at a time and
asks you what you actually see. Pad -> colour ground truth (measured this way):

    RGB0 -> pad 39 -> BLUE
    RGB1 -> pad 40 -> RED
    RGB2 -> pad 41 -> GREEN
"""
import argparse
import sys
import time

import serial

# demo command -> expected colour
STEPS = [("r", "RED"), ("g", "GREEN"), ("b", "BLUE")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=19200)
    args = ap.parse_args()

    ok = True
    with serial.Serial(args.port, args.baud, timeout=0.3) as ser:
        time.sleep(0.3)
        ser.reset_input_buffer()
        for key, expect in STEPS:
            ser.write(key.encode())          # on
            ser.flush()
            time.sleep(1.0)
            ser.read(4096)
            ans = input(f"  '{key}' pressed - expecting {expect}. "
                        f"What do you see? [Enter = {expect}, or type it] ").strip()
            if ans and ans.upper() != expect:
                print(f"    MISMATCH: expected {expect}, saw {ans.upper()}")
                ok = False
            ser.write(key.encode())          # off
            ser.flush()
            time.sleep(1.0)
            ser.read(4096)

    print("\n  RGB mapping " + ("OK" if ok else "WRONG - fix rtl/icesugar_top.v"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
