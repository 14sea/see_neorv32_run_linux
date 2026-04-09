#!/usr/bin/env python3
"""
sd_dump.py — Parametric SD card dump via stage2 mode 'd'.

Protocol (post-Phase-5):
  host:   'd' + u32 start_lba + u32 count   (little-endian)
  stage2: "DUMP_READY\n"
  stage2: count*512 raw bytes
  stage2: "\nDUMP_END\n"  (stage2 returns to dispatcher, so
          --persistent works for chained dumps)

Count is capped at 4096 sectors (2 MB) in stage2.

Examples:
  python3 host/sd_dump.py                          # LBA 0, 512 sec (256 KB)
  python3 host/sd_dump.py --lba 4001 --count 8     # DTB slot
  python3 host/sd_dump.py --lba 0 --count 1 -o -   # dump header to stdout
  python3 host/sd_dump.py --persistent --baud 230400 --lba 4009 --count 6
"""
import argparse, os, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sd_proto import get_session, wait_for, BAUD_CANDIDATES

SECTOR = 512
MAX_COUNT = 4096  # mirrors stage2 cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--skip-program", action="store_true")
    ap.add_argument("--persistent", action="store_true",
                    help="attach to already-running stage2")
    ap.add_argument("--baud", type=int, default=BAUD_CANDIDATES[0])
    ap.add_argument("--lba", type=int, default=0,
                    help="starting LBA (default 0)")
    ap.add_argument("--count", type=int, default=512,
                    help="number of 512-byte sectors (default 512 = 256 KB)")
    ap.add_argument("-o", "--output", default=None,
                    help="output path; '-' writes raw bytes to stdout")
    ap.add_argument("--hex", action="store_true",
                    help="print hex dump of first 256 bytes after read")
    args = ap.parse_args()

    if args.count < 1 or args.count > MAX_COUNT:
        print(f"[!] --count must be 1..{MAX_COUNT}"); sys.exit(1)

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    payload_len = args.count * SECTOR

    if args.output is None:
        args.output = os.path.join(
            base, "output", f"sd_dump_lba{args.lba}_n{args.count}.bin")

    ser = get_session(base, args.port, stage2,
                      skip_program=args.skip_program,
                      target_baud=args.baud,
                      persistent=args.persistent)

    print(f"[*] Dumping LBA {args.lba}..{args.lba + args.count - 1} "
          f"({payload_len} B)")
    ser.write(b"d" + struct.pack("<II", args.lba, args.count)); ser.flush()

    leftover = wait_for(ser, b"DUMP_READY\n", 10, "DUMP_READY")
    if b"DUMP_BAD" in leftover:
        print("[!] stage2 reported DUMP_BAD"); ser.close(); sys.exit(2)

    payload = bytearray(leftover)
    t0 = time.time(); last = 0
    ser.timeout = 2
    while len(payload) < payload_len:
        need = payload_len - len(payload)
        c = ser.read(min(4096, need))
        if not c:
            if time.time() - t0 > 60:
                print(f"\n[!] timeout: {len(payload)}/{payload_len}")
                ser.close(); sys.exit(3)
            continue
        payload += c; t0 = time.time()
        now = time.time()
        if now - last > 0.25 or len(payload) == payload_len:
            pct = 100 * len(payload) // payload_len
            sys.stdout.write(f"\r  {len(payload):>8}/{payload_len} ({pct}%)")
            sys.stdout.flush(); last = now
    print()

    # Trailer
    trailer = b""; t0 = time.time()
    while time.time() - t0 < 5:
        c = ser.read(64)
        if c:
            trailer += c
            if b"DUMP_END\n" in trailer or b"DUMP_ERR" in trailer: break
    ser.close()

    if b"DUMP_ERR" in trailer:
        print("[!] stage2 reported DUMP_ERR"); sys.exit(4)
    if b"DUMP_END\n" not in trailer:
        print("[!] no DUMP_END trailer; data may be truncated")

    data = bytes(payload[:payload_len])
    if args.output == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(args.output, "wb") as f: f.write(data)
        print(f"[*] Wrote {args.output} ({len(data)} B)")

    if args.hex:
        n = min(256, len(data))
        for i in range(0, n, 16):
            row = data[i:i+16]
            hx = " ".join(f"{b:02x}" for b in row)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            print(f"  {args.lba*SECTOR + i:08x}  {hx:<47}  {asc}")


if __name__ == "__main__":
    main()
