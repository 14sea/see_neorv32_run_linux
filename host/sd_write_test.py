#!/usr/bin/env python3
"""
sd_write_test.py — Round-trip write test against LBA 2048 (1 MB offset).

Sends 512 bytes of deterministic payload, stage2 writes them, reads them
back, and byte-compares. Writes to LBA 2048 which is well past
riscv_tpu_demo's MLP image (LBA 0..~235).
"""
import argparse, os, subprocess, sys, time, hashlib, serial

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

    # Deterministic payload: repeatable across runs for inspection
    payload = bytearray()
    for i in range(512):
        payload.append((i * 37 + 0xA5) & 0xFF)
    sig = hashlib.sha1(payload).hexdigest()[:12]
    print(f"[0] Payload SHA1[:12]={sig}")

    if not args.skip_program:
        loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
        print("[1] Programming FPGA")
        r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("[!]", r.stderr); sys.exit(1)
        time.sleep(1.0)

    ser = None; buf = b""
    t0 = time.time()
    print("[2] Bootloader handshake")
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
        print("[!] upload fail"); sys.exit(1)

    ser.write(b"e"); ser.flush()
    ser.close(); time.sleep(0.3)

    ser = serial.Serial(args.port, APP_BAUD, timeout=2)
    ser.dtr = False; ser.rts = False
    print(f"[3] @ {APP_BAUD}, sending 'w'")
    time.sleep(0.5)
    ser.write(b"w"); ser.flush()

    # Wait for SEND_512 marker
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 10:
        c = ser.read(256)
        if c:
            buf += c
            sys.stdout.write(c.decode("ascii", errors="replace"))
            sys.stdout.flush()
            if b"SEND_512\n" in buf:
                break
    if b"SEND_512\n" not in buf:
        print("\n[!] no SEND_512"); sys.exit(2)

    print("[3] Sending 512-byte payload")
    ser.write(bytes(payload)); ser.flush()

    # Wait for WRITE_OK / *_FAIL
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 30:
        c = ser.read(256)
        if c:
            buf += c
            sys.stdout.write(c.decode("ascii", errors="replace"))
            sys.stdout.flush()
            if b"WRITE_OK" in buf or b"FAIL" in buf:
                break
    ser.close()
    print()
    if b"WRITE_OK" in buf:
        print("=== SD WRITE ROUND-TRIP PASS ===")
    else:
        print("=== FAIL ===")
        sys.exit(3)


if __name__ == "__main__":
    main()
