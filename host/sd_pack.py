#!/usr/bin/env python3
"""
sd_pack.py — Pack Linux Image+DTB+initramfs into an SD blob and write
it via stage2's multi-block write mode 'W'.

Layout (raw sectors, 512 B each):
  LBA 0            : header (magic "NEOLNX\0\0", sizes, LBAs)
  LBA 1..N1        : Image  (padded)
  LBA N1..N2       : DTB    (padded)
  LBA N2..N3       : initramfs (padded)

One-shot: no intermediate .bin file. Sends everything over UART →
stage2 streams to SD card.
"""
import argparse, os, struct, subprocess, sys, time, serial

BOOT_BAUD = 19200
APP_BAUD  = 115200
SECTOR    = 512
MAGIC     = b"NEOLNX\x00\x00"


def pad_sector(b: bytes) -> bytes:
    r = len(b) % SECTOR
    return b + b"\x00" * ((SECTOR - r) if r else 0)


def build_blob(image: bytes, dtb: bytes, initrd: bytes) -> bytes:
    img_p = pad_sector(image)
    dtb_p = pad_sector(dtb)
    ird_p = pad_sector(initrd)

    image_lba  = 1
    dtb_lba    = image_lba  + len(img_p) // SECTOR
    initrd_lba = dtb_lba    + len(dtb_p) // SECTOR

    hdr = MAGIC + struct.pack("<IIIIII",
        len(image), len(dtb), len(initrd),
        image_lba, dtb_lba, initrd_lba)
    hdr += b"\x00" * 16    # reserved[4]
    hdr = pad_sector(hdr)
    assert len(hdr) == SECTOR

    return hdr + img_p + dtb_p + ird_p


def bootloader_handshake(port):
    ser = None; buf = b""
    t0 = time.time()
    while time.time() - t0 < 20:
        if ser is None:
            try:
                ser = serial.Serial(port, BOOT_BAUD, timeout=1)
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
        raise RuntimeError(f"no prompt: {buf[-200:]!r}")
    return ser


def upload_stage2(ser, stage2_path):
    with open(stage2_path, "rb") as f: data = f.read()
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
        raise RuntimeError("stage2 upload failed")
    ser.write(b"e"); ser.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--kernel", default=None)
    ap.add_argument("--dtb", default=None)
    ap.add_argument("--initrd", default=None)
    ap.add_argument("--skip-program", action="store_true")
    ap.add_argument("--save-blob", default=None,
                    help="Also save the packed blob to this file")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    rbf    = os.path.join(base, "output", "neorv32_demo.rbf")
    kernel = args.kernel or os.path.join(base, "output", "Image")
    dtb    = args.dtb    or os.path.join(base, "output", "neorv32_ax301.dtb")
    initrd = args.initrd or os.path.join(base, "output", "neo_initramfs.cpio.gz")

    with open(kernel, "rb") as f: image_d = f.read()
    with open(dtb,    "rb") as f: dtb_d = f.read()
    with open(initrd, "rb") as f: ird_d = f.read()
    blob = build_blob(image_d, dtb_d, ird_d)
    n_sec = len(blob) // SECTOR

    print(f"[0] Image {len(image_d):,}B  DTB {len(dtb_d):,}B  initrd {len(ird_d):,}B")
    print(f"[0] Blob  {len(blob):,}B  ({n_sec} sectors)")
    eta = n_sec * 512 * 10 / APP_BAUD
    print(f"[0] UART eta ~{eta:.0f}s")

    if args.save_blob:
        with open(args.save_blob, "wb") as f: f.write(blob)
        print(f"[0] Saved blob to {args.save_blob}")

    if not args.skip_program:
        loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
        print("[1] Programming FPGA")
        r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print("[!]", r.stderr); sys.exit(1)
        time.sleep(1.0)

    ser = bootloader_handshake(args.port)
    upload_stage2(ser, stage2)
    port = ser.port
    ser.close(); time.sleep(0.3)

    ser = serial.Serial(port, APP_BAUD, timeout=2)
    ser.dtr = False; ser.rts = False
    print(f"[3] @ {APP_BAUD}, sending 'W'")
    time.sleep(0.5)
    ser.write(b"W"); ser.flush()

    # Wait MW_READY
    buf = b""; t0 = time.time()
    while time.time() - t0 < 10:
        c = ser.read(256)
        if c:
            buf += c
            if b"MW_READY" in buf: break
    if b"MW_READY" not in buf:
        print("[!] no MW_READY:", buf[-200:]); sys.exit(2)

    # Send sector count
    print(f"[3] Sending sector count {n_sec}")
    ser.write(struct.pack("<I", n_sec)); ser.flush()

    # Wait MW_GO
    buf = b""; t0 = time.time()
    while time.time() - t0 < 5:
        c = ser.read(256)
        if c:
            buf += c
            if b"MW_GO" in buf: break
    if b"MW_GO" not in buf:
        print("[!] no MW_GO:", buf[-200:]); sys.exit(2)

    # Stream blob — 512 B per block, wait for 'K' ACK after each
    print(f"[3] Streaming {len(blob):,}B ({n_sec} sectors, per-block ACK)")
    t_start = time.time()
    last_print = 0
    ser.timeout = 10
    for i in range(n_sec):
        ser.write(blob[i * SECTOR:(i + 1) * SECTOR])
        ser.flush()
        ack = ser.read(1)
        if ack != b"K":
            # Might be 'X' (error) or start of MW_FAIL message
            extra = ser.read(256)
            print(f"\n[!] no ACK at sector {i}, got {ack!r}{extra!r}")
            sys.exit(3)
        now = time.time()
        if now - last_print > 0.25 or i == n_sec - 1:
            pct = 100 * (i + 1) // n_sec
            sys.stdout.write(f"\r  sector {i+1:>5}/{n_sec} ({pct}%)")
            sys.stdout.flush()
            last_print = now
    print()

    # Wait MW_DONE / MW_FAIL
    buf = b""; t0 = time.time()
    while time.time() - t0 < 60:
        c = ser.read(256)
        if c:
            buf += c
            sys.stdout.write(c.decode("ascii", errors="replace"))
            sys.stdout.flush()
            if b"MW_DONE" in buf or b"MW_FAIL" in buf: break
    ser.close()
    elapsed = time.time() - t_start
    print(f"\n[4] Transfer + write: {elapsed:.1f}s ({len(blob)/elapsed/1024:.1f} KB/s)")

    if b"MW_DONE" in buf:
        print("=== PACK + WRITE OK ===")
    else:
        print("=== FAIL ===")
        sys.exit(3)


if __name__ == "__main__":
    main()
