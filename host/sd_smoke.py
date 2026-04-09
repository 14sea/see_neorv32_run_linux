#!/usr/bin/env python3
"""
sd_smoke.py — Run stage2's SD SPI smoke test.

Sequence:
  1. (optional) Program FPGA
  2. Bootloader (19200) → upload stage2 → execute
  3. Switch to 115200, send 's' (instead of 'l') → stage2 runs sd_smoke()
  4. Print whatever stage2 prints until it halts.

This is strictly a READ test. stage2_loader has no sd_write_block,
so the SD card content is safe.

Usage:
    python3 host/sd_smoke.py --port /dev/ttyUSB0
"""
import argparse, os, sys, time, subprocess, serial

BOOT_BAUD = 19200
APP_BAUD  = 115200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--rbf", default=None)
    ap.add_argument("--stage2", default=None)
    ap.add_argument("--skip-program", action="store_true")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = args.stage2 or os.path.join(base, "output", "stage2_loader.bin")
    rbf    = args.rbf    or os.path.join(base, "output", "neorv32_demo.rbf")

    if not args.skip_program:
        loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
        print(f"[1] Programming FPGA: {rbf}")
        r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("[!] programming failed:", r.stderr); sys.exit(1)
        time.sleep(1.0)

    # Bootloader handshake
    print(f"[2] Bootloader @ {BOOT_BAUD}")
    ser = None
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 20:
        if ser is None:
            try:
                ser = serial.Serial(args.port, BOOT_BAUD, timeout=1,
                                    xonxoff=False, rtscts=False, dsrdtr=False)
                ser.dtr = False; ser.rts = False
                time.sleep(0.3); ser.reset_input_buffer()
            except Exception:
                time.sleep(1.0); continue
        try:
            ser.write(b" "); time.sleep(0.3)
            chunk = ser.read(2000)
            if chunk: buf += chunk
            if b"CMD:>" in buf or b"Press any key" in buf: break
        except Exception:
            try: ser.close()
            except: pass
            ser = None; time.sleep(1.0)

    if b"Press any key" in buf:
        ser.write(b" "); time.sleep(0.3); buf += ser.read(1000)
    if b"CMD:>" not in buf:
        print("[!] no prompt:", buf[-200:]); sys.exit(1)

    # Upload stage2
    with open(stage2, "rb") as f: data = f.read()
    print(f"[2] Uploading stage2 ({len(data)}B)")
    ser.reset_input_buffer()
    ser.write(b"u"); time.sleep(0.5); ser.read(1000)
    time.sleep(0.2)
    ser.write(data); ser.flush()
    t0 = time.time(); resp = b""
    while time.time() - t0 < 15:
        resp += ser.read(500)
        if b"OK" in resp: break
    if b"OK" not in resp:
        print("[!] stage2 upload:", resp[-200:]); sys.exit(1)

    ser.write(b"e"); ser.flush()
    ser.close(); time.sleep(0.3)

    # App baud — send 's' to trigger smoke test
    ser = serial.Serial(args.port, APP_BAUD, timeout=0.5)
    ser.dtr = False; ser.rts = False
    print(f"[3] Switched to {APP_BAUD}, sending 's'")
    time.sleep(0.5)
    ser.write(b"s"); ser.flush()

    # Drain stage2 output for 15s or until 'smoke done'
    t0 = time.time()
    buf = b""
    while time.time() - t0 < 15:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            sys.stdout.write(chunk.decode("ascii", errors="replace"))
            sys.stdout.flush()
        if b"smoke done" in buf:
            break
    ser.close()
    print()

    if b"[sd] init OK" in buf and b"magic=" in buf:
        print("=== SMOKE TEST PASS ===")
    else:
        print("=== SMOKE TEST FAIL ===")
        sys.exit(2)


if __name__ == "__main__":
    main()
