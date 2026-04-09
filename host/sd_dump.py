#!/usr/bin/env python3
"""
sd_dump.py — Read the first 256 KB of the SD card and save locally.

Flow: program FPGA → upload stage2 → send 'd' → read ASCII until
DUMP_BEGIN\n → read exactly 512*512=262144 raw bytes → read DUMP_END → save.

Pure read, no writes. Output: output/sd_backup.bin
"""
import argparse, os, subprocess, sys, time, serial

BOOT_BAUD = 19200
APP_BAUD  = 115200
N_BLOCKS  = 512
BLOCK     = 512
PAYLOAD   = N_BLOCKS * BLOCK  # 262144


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--skip-program", action="store_true")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    rbf    = os.path.join(base, "output", "neorv32_demo.rbf")
    outp   = args.output or os.path.join(base, "output", "sd_backup.bin")

    if not args.skip_program:
        loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
        print(f"[1] Programming FPGA")
        r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("[!]", r.stderr); sys.exit(1)
        time.sleep(1.0)

    # Bootloader handshake
    print(f"[2] Bootloader")
    ser = None
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 20:
        if ser is None:
            try:
                ser = serial.Serial(args.port, BOOT_BAUD, timeout=1)
                ser.dtr = False; ser.rts = False
                time.sleep(0.3); ser.reset_input_buffer()
            except Exception:
                time.sleep(1.0); continue
        try:
            ser.write(b" "); time.sleep(0.3)
            c = ser.read(2000)
            if c: buf += c
            if b"CMD:>" in buf or b"Press any key" in buf: break
        except Exception:
            try: ser.close()
            except: pass
            ser = None; time.sleep(1.0)

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

    ser = serial.Serial(args.port, APP_BAUD, timeout=2)
    ser.dtr = False; ser.rts = False
    print(f"[3] @ {APP_BAUD}, sending 'd' (dump {N_BLOCKS*BLOCK} bytes)")
    time.sleep(0.5)
    ser.write(b"d"); ser.flush()

    # Wait for DUMP_BEGIN marker
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 10:
        c = ser.read(256)
        if c:
            buf += c
            if b"DUMP_BEGIN\n" in buf:
                break
    if b"DUMP_BEGIN\n" not in buf:
        print("[!] no DUMP_BEGIN. Got:", buf[-200:]); sys.exit(2)
    print("[3] DUMP_BEGIN, reading payload")

    # Anything after DUMP_BEGIN\n already in buf is raw payload
    idx = buf.index(b"DUMP_BEGIN\n") + len(b"DUMP_BEGIN\n")
    payload = buf[idx:]

    t0 = time.time()
    last_print = 0
    while len(payload) < PAYLOAD:
        need = PAYLOAD - len(payload)
        c = ser.read(min(4096, need))
        if not c:
            if time.time() - t0 > 60:
                print(f"\n[!] timeout, got {len(payload)}/{PAYLOAD}"); sys.exit(3)
            continue
        payload += c
        t0 = time.time()
        now = time.time()
        if now - last_print > 0.5:
            pct = 100 * len(payload) // PAYLOAD
            sys.stdout.write(f"\r  {len(payload):>7}/{PAYLOAD} ({pct}%)")
            sys.stdout.flush()
            last_print = now
    print(f"\r  {PAYLOAD}/{PAYLOAD} (100%)")

    # Trailer
    trailer = b""
    t0 = time.time()
    while time.time() - t0 < 5:
        c = ser.read(64)
        if c:
            trailer += c
            if b"DUMP_END\n" in trailer or b"DUMP_ERR" in trailer:
                break
    ser.close()

    if b"DUMP_ERR" in trailer:
        print("[!] device reported DUMP_ERR"); sys.exit(4)
    if b"DUMP_END\n" not in trailer:
        print("[!] no DUMP_END trailer; saving anyway")

    with open(outp, "wb") as f: f.write(payload)
    print(f"[4] Wrote {outp} ({len(payload)} bytes)")

    # Quick sanity vs known MLP header
    print(f"[4] First 16 bytes: {payload[:16].hex(' ')}")


if __name__ == "__main__":
    main()
