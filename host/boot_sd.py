#!/usr/bin/env python3
"""
boot_sd.py — Boot Linux by instructing stage2 to load the blob from SD.

Assumes sd_pack.py has already written a blob (magic NEOLNX) to LBA 0.
No xmodem transfer — just program FPGA, upload stage2, send 'b'.
"""
import argparse, os, subprocess, sys, time, serial

BOOT_BAUD = 19200
APP_BAUD  = 115200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--skip-program", action="store_true")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    rbf    = os.path.join(base, "output", "neorv32_demo.rbf")

    if not args.skip_program:
        loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
        print("[1] Programming FPGA")
        r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("[!]", r.stderr); sys.exit(1)
        time.sleep(1.0)

    # Bootloader handshake
    print("[2] Bootloader handshake")
    ser = None; buf = b""
    t0 = time.time()
    while time.time() - t0 < 20:
        if ser is None:
            try:
                ser = serial.Serial(args.port, BOOT_BAUD, timeout=1)
                ser.dtr = False; ser.rts = False
                time.sleep(0.3); ser.reset_input_buffer()
            except Exception:
                time.sleep(1.0); continue
        ser.write(b" "); time.sleep(0.3)
        c = ser.read(2000)
        if c: buf += c
        if b"CMD:>" in buf or b"Press any key" in buf: break
    if b"Press any key" in buf:
        ser.write(b" "); time.sleep(0.3); buf += ser.read(1000)
    if b"CMD:>" not in buf:
        print("[!] no prompt"); sys.exit(1)

    with open(stage2, "rb") as f: data = f.read()
    print(f"[2] Upload stage2 ({len(data)}B)")
    ser.reset_input_buffer()
    ser.write(b"u"); time.sleep(0.5); ser.read(1000)
    time.sleep(0.2)
    ser.write(data); ser.flush()
    t0 = time.time(); resp = b""
    while time.time() - t0 < 15:
        resp += ser.read(500)
        if b"OK" in resp: break
    if b"OK" not in resp:
        print("[!] upload failed"); sys.exit(1)

    ser.write(b"e"); ser.flush()
    ser.close(); time.sleep(0.3)

    ser = serial.Serial(args.port, APP_BAUD, timeout=0.5)
    ser.dtr = False; ser.rts = False
    print(f"[3] @ {APP_BAUD}, sending 'b' (boot from SD)")
    time.sleep(0.5)
    ser.write(b"b"); ser.flush()

    # Stream console forever
    print("[3] Streaming console... (Ctrl+C to detach)\n")
    try:
        while True:
            c = ser.read(ser.in_waiting or 1)
            if c:
                sys.stdout.write(c.decode("ascii", errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n[detached]")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
