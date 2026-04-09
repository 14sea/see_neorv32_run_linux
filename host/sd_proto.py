#!/usr/bin/env python3
"""
sd_proto.py — Common helpers for talking to stage2_loader over UART:
FPGA program, bootloader handshake, stage2 upload, multi-segment write,
header read-back. Used by sd_pack.py and sd_update.py.
"""
import os, struct, subprocess, sys, time, serial

BOOT_BAUD = 19200
APP_BAUD  = 115200
SECTOR    = 512


def program_fpga(base):
    loader = os.path.join(base, "tools", "openFPGALoader", "build", "openFPGALoader")
    rbf    = os.path.join(base, "output", "neorv32_demo.rbf")
    print("[1] Programming FPGA")
    r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print("[!]", r.stderr); sys.exit(1)
    time.sleep(1.0)


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


def reopen_app(ser):
    """Close the bootloader serial and reopen at APP_BAUD."""
    port = ser.port
    ser.close(); time.sleep(0.3)
    s = serial.Serial(port, APP_BAUD, timeout=2)
    s.dtr = False; s.rts = False
    time.sleep(0.5)
    return s


def wait_for(ser, marker, timeout_s, label):
    """Read until `marker` is seen. Return bytes AFTER the marker so
    the caller can keep parsing any payload the device already sent."""
    buf = b""; t0 = time.time()
    while time.time() - t0 < timeout_s:
        c = ser.read(256)
        if c:
            buf += c
            idx = buf.find(marker)
            if idx >= 0:
                return buf[idx + len(marker):]
    raise RuntimeError(f"[!] no {label}: {buf[-200:]!r}")


def read_header(ser):
    """Send 'R' mode and read back the 512-byte LBA-0 header sector."""
    print("[*] Reading LBA 0 header (mode 'R')")
    ser.write(b"R"); ser.flush()
    leftover = wait_for(ser, b"RD_READY\n", 5, "RD_READY")
    hdr = leftover
    t0 = time.time()
    ser.timeout = 2
    while len(hdr) < SECTOR and time.time() - t0 < 5:
        c = ser.read(SECTOR - len(hdr))
        if c: hdr += c
    if len(hdr) < SECTOR:
        raise RuntimeError(f"header short: {len(hdr)}B")
    # If we got extra bytes, they belong to the trailing "\nRD_DONE\n"
    hdr_data = hdr[:SECTOR]
    tail = hdr[SECTOR:]
    if b"RD_DONE" not in tail:
        wait_for(ser, b"RD_DONE", 5, "RD_DONE")
    return hdr_data


def multi_seg_write(ser, segments):
    """
    segments: list of (start_lba, data_bytes). data_bytes must be a
    multiple of 512. Total sector count is reported; each sector is
    ACKed with 'K' by stage2.
    """
    print(f"[3] Multi-segment write: {len(segments)} segment(s)")
    ser.write(b"W"); ser.flush()
    wait_for(ser, b"MW_READY\n", 10, "MW_READY")

    ser.write(struct.pack("<I", len(segments))); ser.flush()
    wait_for(ser, b"MW_GO\n", 5, "MW_GO")

    total_sec = sum(len(d) // SECTOR for _, d in segments)
    print(f"[3] Streaming {total_sec} sectors total "
          f"({total_sec * SECTOR:,}B)")

    t_start = time.time()
    last_print = 0
    ser.timeout = 10
    done = 0
    for seg_idx, (start_lba, data) in enumerate(segments):
        sec_count = len(data) // SECTOR
        assert len(data) % SECTOR == 0, "segment not sector-aligned"
        print(f"\n  seg {seg_idx}: LBA {start_lba}..{start_lba + sec_count - 1} "
              f"({sec_count} sec)")
        ser.write(struct.pack("<II", start_lba, sec_count)); ser.flush()
        for i in range(sec_count):
            ser.write(data[i * SECTOR:(i + 1) * SECTOR])
            ser.flush()
            ack = ser.read(1)
            if ack != b"K":
                extra = ser.read(256)
                raise RuntimeError(
                    f"no ACK seg={seg_idx} sec={i} lba={start_lba+i} "
                    f"got {ack!r}{extra!r}")
            done += 1
            now = time.time()
            if now - last_print > 0.25 or done == total_sec:
                pct = 100 * done // total_sec
                sys.stdout.write(f"\r  {done:>5}/{total_sec} ({pct}%)")
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
    elapsed = time.time() - t_start
    print(f"\n[4] Write: {elapsed:.2f}s "
          f"({total_sec * SECTOR / max(elapsed, 0.001) / 1024:.1f} KB/s)")
    if b"MW_DONE" not in buf:
        raise RuntimeError("MW did not complete")
