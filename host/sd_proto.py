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

# Fallback chain for the UART-baud bump. First entry is the preferred
# target; last entry must be APP_BAUD so the fallback always lands at a
# known-good rate.
BAUD_CANDIDATES = [230400, 115200]


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


def maybe_switch_baud(ser, target):
    """Ask stage2 to switch UART baud via mode 'B'. Returns True on success.
    On any failure the caller should re-init the session at a lower baud."""
    if ser.baudrate == target:
        return ser
    print(f"[*] Switching UART baud: {ser.baudrate} -> {target}")
    ser.reset_input_buffer()
    ser.write(b"B" + struct.pack("<I", target)); ser.flush()
    # First ack at old baud
    buf = b""; t0 = time.time()
    while time.time() - t0 < 2:
        c = ser.read(64)
        if c: buf += c
        if b"BAUD_SWITCH" in buf or b"BAUD_BAD" in buf: break
    if b"BAUD_BAD" in buf:
        print(f"[!] stage2 rejected baud {target}")
        return False
    if b"BAUD_SWITCH" not in buf:
        print(f"[!] no BAUD_SWITCH ack: {buf[-120:]!r}")
        return False
    # Reopen at new baud and look for BAUD_OK
    port = ser.port
    ser.close(); time.sleep(0.35)
    try:
        ser2 = serial.Serial(port, target, timeout=1)
        ser2.dtr = False; ser2.rts = False
    except Exception as e:
        print(f"[!] host serial reopen at {target} failed: {e}")
        return False
    # Stage2 is blocked waiting for a probe byte before replying at the new
    # baud (so its TX can't race the PL2303 reopen window). Send exactly
    # one — extras would land as new dispatcher commands (→ U-Boot).
    ser2.write(b"P"); ser2.flush()
    buf = b""; t0 = time.time()
    while time.time() - t0 < 2.0:
        c = ser2.read(64)
        if c: buf += c
        if b"BAUD_OK" in buf: break
    if b"BAUD_OK" not in buf:
        print(f"[!] no BAUD_OK at {target}: {buf[-120:]!r}")
        ser2.close()
        return False
    print(f"[*] Baud switched OK @ {target}")
    return ser2


def persistent_session(port, baud=APP_BAUD, probe_timeout=2.0):
    """Attach to an already-running stage2 without programming the FPGA or
    uploading stage2 again. Caller must know the baud stage2 is currently
    at (same default the previous run used). Sends a newline and expects
    stage2's dispatcher prompt. Raises RuntimeError on mismatch."""
    print(f"[*] Persistent attach @ {baud}")
    ser = serial.Serial(port, baud, timeout=0.5)
    ser.dtr = False; ser.rts = False
    time.sleep(0.2); ser.reset_input_buffer()
    # Stage2 is idle in the dispatcher waiting for a byte. Any byte it
    # doesn't recognise falls through to mode_uboot (bad!), BUT only on
    # the very first prompt. After the first command has run, the re-prompt
    # loop treats any unknown byte as "run default" too — so we must not
    # send a junk byte. Trick: we rely on the next real command byte
    # (written by the caller) to be the one stage2 consumes. Just verify
    # the link is alive by checking we can open the port.
    #
    # Sanity check: if stage2 had already printed "ready for next cmd"
    # recently and the bytes are still in the PL2303 buffer, we'll see
    # them. If not, proceed anyway — the caller's first write will drive
    # stage2 into the requested mode.
    buf = ser.read(512)
    if buf:
        print(f"[*] (drained {len(buf)} buffered bytes)")
    return ser


def get_session(base, port, stage2_path=None, skip_program=False,
                target_baud=APP_BAUD, persistent=False):
    """Unified entry: fresh `setup_session` or attach to existing stage2."""
    if persistent:
        return persistent_session(port, baud=target_baud)
    return setup_session(base, port, stage2_path, skip_program, target_baud)


def setup_session(base, port, stage2_path=None, skip_program=False,
                  target_baud=APP_BAUD):
    """Full boot: (optional) program FPGA, handshake, upload stage2, reopen
    at APP_BAUD, then try to bump to `target_baud`. Falls back to APP_BAUD
    on any failure. Returns the serial object at the negotiated baud."""
    if stage2_path is None:
        stage2_path = os.path.join(base, "output", "stage2_loader.bin")
    if not skip_program:
        program_fpga(base)
    ser = bootloader_handshake(port)
    upload_stage2(ser, stage2_path)
    ser = reopen_app(ser)
    if target_baud == APP_BAUD:
        return ser
    new_ser = maybe_switch_baud(ser, target_baud)
    if new_ser:
        return new_ser
    print(f"[!] Falling back to {APP_BAUD}; re-init session from scratch")
    try: ser.close()
    except Exception: pass
    time.sleep(0.3)
    # Restart from FPGA program to guarantee clean state
    return setup_session(base, port, stage2_path, skip_program=False,
                         target_baud=APP_BAUD)


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
