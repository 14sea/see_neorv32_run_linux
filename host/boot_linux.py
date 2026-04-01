#!/usr/bin/env python3
"""
boot_linux.py — Boot nommu Linux on NEORV32 AX301 (direct boot, no U-Boot)

Sequence:
  1. Program FPGA with neorv32_demo.rbf
  2. Bootloader (19200) → upload stage2_loader → execute
  3. Stage2 (115200) → send 'l' for Linux mode
  4. xmodem kernel to 0x40000000, CRC-32 verify
  5. xmodem DTB to 0x41F00000, CRC-32 verify
  6. xmodem initramfs to 0x41F80000, CRC-32 verify
  7. Stage2 jumps to kernel with a0=0, a1=0x41F00000

Memory layout:
  0x40000000  Linux Image (~1.5 MB)
  0x41F00000  DTB (~1.4 KB)
  0x41F80000  initramfs (~1.7 KB)
"""

import argparse
import binascii
import os
import struct
import sys
import time
import serial

BOOTLOADER_BAUD = 19200
APP_BAUD = 115200
XMODEM_BLOCK_SIZE = 128
SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18


# ── XMODEM ─────────────────────────────────────────────────────────────

def xmodem_send(ser, data, timeout=3.0):
    """Send data via xmodem (checksum mode, 128-byte blocks)."""
    pad_len = (XMODEM_BLOCK_SIZE - len(data) % XMODEM_BLOCK_SIZE) % XMODEM_BLOCK_SIZE
    data += b'\x1a' * pad_len
    num_blocks = len(data) // XMODEM_BLOCK_SIZE

    print(f"  [xmodem] {len(data)} bytes, {num_blocks} blocks")
    ser.reset_input_buffer()
    time.sleep(0.05)

    for blk_idx in range(num_blocks):
        blk_num = (blk_idx + 1) & 0xFF
        offset = blk_idx * XMODEM_BLOCK_SIZE
        block = data[offset:offset + XMODEM_BLOCK_SIZE]
        csum = sum(block) & 0xFF
        packet = bytes([SOH, blk_num, (~blk_num) & 0xFF]) + block + bytes([csum])

        retries = 0
        while retries < 10:
            ser.write(packet)
            ser.flush()
            t0 = time.time()
            while time.time() - t0 < timeout:
                c = ser.read(1)
                if c:
                    if c[0] == ACK:
                        break
                    elif c[0] == NAK:
                        retries += 1
                        break
            else:
                retries += 1
                continue
            if c and c[0] == ACK:
                break
        else:
            print(f"\n  [!] Block {blk_idx} failed after retries")
            return False

        if (blk_idx + 1) % 100 == 0 or blk_idx == num_blocks - 1:
            pct = 100 * (blk_idx + 1) // num_blocks
            print(f"\r  [xmodem] {blk_idx+1}/{num_blocks} ({pct}%)", end="", flush=True)

    print()
    ser.write(bytes([EOT]))
    ser.flush()
    t0 = time.time()
    while time.time() - t0 < 5:
        c = ser.read(1)
        if c and c[0] == ACK:
            print("  [xmodem] Transfer complete")
            return True
    print("  [!] No ACK for EOT")
    return False


def xmodem_send_verified(ser, data, name):
    """Send data via xmodem, then verify CRC-32 with stage2."""
    if not xmodem_send(ser, data):
        print(f"  [!] {name} xmodem FAILED")
        return False

    # Compute expected CRC (over padded data, same as stage2 receives)
    pad_len = (XMODEM_BLOCK_SIZE - len(data) % XMODEM_BLOCK_SIZE) % XMODEM_BLOCK_SIZE
    padded = data + b'\x1a' * pad_len
    expected_crc = binascii.crc32(padded) & 0xFFFFFFFF

    # Wait for CRC from stage2
    crc_buf = b""
    t0 = time.time()
    while time.time() - t0 < 30:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            crc_buf += chunk
            try:
                sys.stdout.write(chunk.decode("ascii", errors="replace"))
                sys.stdout.flush()
            except:
                pass
        if b"CRC:" in crc_buf and b"\n" in crc_buf[crc_buf.index(b"CRC:"):]:
            break

    fpga_crc = None
    if b"CRC:" in crc_buf:
        try:
            idx = crc_buf.index(b"CRC:") + 4
            hex_str = crc_buf[idx:idx+10].split(b"\r")[0].split(b"\n")[0].strip()
            fpga_crc = int(hex_str, 16)
        except (ValueError, IndexError):
            pass

    if fpga_crc is not None and fpga_crc == expected_crc:
        print(f"  [{name}] CRC MATCH: {fpga_crc:08x} ✓")
        ser.write(bytes([ACK]))
        ser.flush()
        return True
    elif fpga_crc is not None:
        print(f"  [{name}] CRC MISMATCH: FPGA={fpga_crc:08x} expected={expected_crc:08x}")
        ser.write(bytes([NAK]))
        ser.flush()
        return False
    else:
        print(f"  [{name}] No CRC from stage2, proceeding...")
        return True


# ── Main ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Boot nommu Linux on NEORV32 AX301")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--rbf", default=None, help="FPGA bitstream")
    ap.add_argument("--stage2", default=None, help="Stage2 loader binary")
    ap.add_argument("--kernel", default=None, help="Linux kernel Image")
    ap.add_argument("--dtb", default=None, help="Device Tree Blob")
    ap.add_argument("--initrd", default=None, help="initramfs cpio.gz")
    ap.add_argument("--skip-program", action="store_true",
                    help="Skip FPGA programming")
    ap.add_argument("--skip-stage2", action="store_true",
                    help="Skip stage2 upload (assume already running in Linux mode)")
    args = ap.parse_args()

    # Resolve paths — all relative to project root (parent of host/)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    stage2 = args.stage2 or os.path.join(base, "output", "stage2_loader.bin")
    kernel = args.kernel or os.path.join(base, "output", "Image")
    dtb = args.dtb or os.path.join(base, "output", "neorv32_ax301.dtb")
    initrd = args.initrd or os.path.join(base, "output", "neo_initramfs.cpio.gz")

    # Verify files
    for f, name in [(kernel, "kernel"), (dtb, "DTB"), (initrd, "initramfs")]:
        if not os.path.exists(f):
            print(f"[!] {name} not found: {f}")
            sys.exit(1)

    with open(kernel, "rb") as f:
        kernel_data = f.read()
    with open(dtb, "rb") as f:
        dtb_data = f.read()
    with open(initrd, "rb") as f:
        initrd_data = f.read()

    print(f"[*] Kernel:    {len(kernel_data):,} bytes → 0x40000000")
    print(f"[*] DTB:       {len(dtb_data):,} bytes → 0x41f00000")
    print(f"[*] Initramfs: {len(initrd_data):,} bytes → 0x41f80000")

    total_bytes = len(kernel_data) + len(dtb_data) + len(initrd_data)
    eta_sec = total_bytes * 1.1 / (APP_BAUD / 10)
    print(f"[*] Total: {total_bytes:,} bytes, ~{eta_sec:.0f}s at {APP_BAUD} baud")

    if not args.skip_stage2:
        # ── Step 1: Program FPGA ──
        if not args.skip_program:
            rbf = args.rbf or os.path.join(base, "output", "neorv32_demo.rbf")
            loader = "openFPGALoader"  # must be in PATH or specify full path
            print(f"\n[1] Programming FPGA: {rbf}")
            import subprocess
            r = subprocess.run([loader, "-c", "usb-blaster", rbf],
                               capture_output=True, text=True, timeout=30)
            print(r.stdout.strip())
            if r.returncode != 0:
                print(f"[!] Programming failed: {r.stderr}")
                sys.exit(1)
            # PL2303 disconnects/reconnects during JTAG programming via usbipd
            time.sleep(1.0)

        # ── Step 2: Bootloader → upload stage2 ──
        print(f"\n[2] Connecting to bootloader at {BOOTLOADER_BAUD} baud...")
        ser = None
        buf = b""
        t0 = time.time()
        while time.time() - t0 < 20:
            # Try to open serial port
            if ser is None:
                try:
                    ser = serial.Serial(args.port, BOOTLOADER_BAUD, timeout=1,
                                        xonxoff=False, rtscts=False, dsrdtr=False)
                    ser.dtr = False
                    ser.rts = False
                    time.sleep(0.3)
                    ser.reset_input_buffer()
                except (serial.SerialException, OSError):
                    time.sleep(1.0)
                    continue
            # Try to read
            try:
                ser.write(b' ')
                time.sleep(0.3)
                chunk = ser.read(2000)
                if chunk:
                    buf += chunk
                    print(f"  [{time.time()-t0:.1f}s] got {len(chunk)}B")
                if b"CMD:>" in buf or b"Press any key" in buf:
                    break
            except (serial.SerialException, OSError):
                print(f"  [{time.time()-t0:.1f}s] serial error, reconnecting...")
                try:
                    ser.close()
                except:
                    pass
                ser = None
                time.sleep(1.0)

        if b"Press any key" in buf:
            ser.write(b" ")
            time.sleep(0.3)
            buf += ser.read(1000)
        elif b"CMD:>" not in buf:
            ser.write(b" ")
            time.sleep(0.5)
            buf += ser.read(1000)

        if b"CMD:>" not in buf:
            print(f"[!] No bootloader prompt. Got: {buf[-200:]!r}")
            ser.close()
            sys.exit(1)
        print("[2] Bootloader ready")

        # Load stage2
        if not os.path.exists(stage2):
            print(f"[!] stage2 not found: {stage2}")
            sys.exit(1)
        with open(stage2, "rb") as f:
            stage2_data = f.read()
        print(f"[2] Stage2: {len(stage2_data):,} bytes")

        ser.reset_input_buffer()
        ser.write(b"u")
        time.sleep(0.5)
        resp = ser.read(1000)
        if b"bin" not in resp:
            print(f"[!] No upload prompt: {resp!r}")
            ser.close()
            sys.exit(1)

        time.sleep(0.2)
        ser.write(stage2_data)
        ser.flush()

        resp = b""
        t0 = time.time()
        while time.time() - t0 < 15:
            resp += ser.read(500)
            if b"OK" in resp:
                break
        if b"OK" not in resp:
            print(f"[!] Stage2 upload failed: {resp!r}")
            ser.close()
            sys.exit(1)
        print(f"[2] Stage2 uploaded OK")

        # Execute stage2
        ser.write(b"e")
        ser.flush()

        # ── Step 3: Switch to 115200 ──
        port_name = ser.port
        ser.close()
        time.sleep(0.3)
        ser = serial.Serial(port_name, APP_BAUD, timeout=0.5,
                            xonxoff=False, rtscts=False, dsrdtr=False)
        ser.dtr = False
        ser.rts = False
        print(f"\n[3] Switched to {APP_BAUD} baud")

        # Send 'l' immediately — stage2 buffers it in UART RX FIFO.
        # When stage2's uart_getc_timeout(3000) runs, 'l' is already waiting.
        time.sleep(0.5)  # Let stage2 start up
        ser.write(b"l")
        ser.flush()
        print("[3] Sent 'l' for Linux direct boot mode")

        # Wait for stage2 to confirm Linux mode
        buf = b""
        t0 = time.time()
        while time.time() - t0 < 15:
            chunk = ser.read(500)
            if chunk:
                buf += chunk
                try:
                    sys.stdout.write(chunk.decode("ascii", errors="replace"))
                    sys.stdout.flush()
                except:
                    pass
            if b"Linux direct boot" in buf:
                break

        if b"Linux direct boot" not in buf:
            if b"U-Boot loader" in buf:
                print("\n[!] Stage2 entered U-Boot mode instead. 'l' not received in time.")
                ser.close()
                sys.exit(1)
            print(f"\n[!] Stage2 not ready. Got: {buf[-200:]!r}")
            ser.close()
            sys.exit(1)

    else:
        # skip_stage2: open serial at app baud directly
        ser = serial.Serial(args.port, APP_BAUD, timeout=0.5,
                            xonxoff=False, rtscts=False, dsrdtr=False)
        ser.dtr = False
        ser.rts = False

    # ── Wait for stage2 to be ready for kernel xmodem ──
    print("\n[4] Waiting for kernel xmodem prompt...")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 15:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            try:
                sys.stdout.write(chunk.decode("ascii", errors="replace"))
                sys.stdout.flush()
            except:
                pass
        if bytes([NAK]) in buf:
            break
    if bytes([NAK]) not in buf:
        print(f"\n[!] No NAK for kernel xmodem. Got: {buf[-200:]!r}")
        ser.close()
        sys.exit(1)

    # ── Step 4: Send kernel ──
    print(f"\n[4] Sending kernel ({len(kernel_data):,} bytes) via xmodem...")
    if not xmodem_send_verified(ser, kernel_data, "kernel"):
        print("[!] Kernel transfer failed")
        ser.close()
        sys.exit(1)

    # ── Wait for DTB xmodem prompt ──
    print("\n[5] Waiting for DTB xmodem prompt...")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 15:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            try:
                sys.stdout.write(chunk.decode("ascii", errors="replace"))
                sys.stdout.flush()
            except:
                pass
        if bytes([NAK]) in buf:
            break
    if bytes([NAK]) not in buf:
        print(f"\n[!] No NAK for DTB xmodem. Got: {buf[-200:]!r}")
        ser.close()
        sys.exit(1)

    # ── Step 5: Send DTB ──
    print(f"\n[5] Sending DTB ({len(dtb_data):,} bytes) via xmodem...")
    if not xmodem_send_verified(ser, dtb_data, "DTB"):
        print("[!] DTB transfer failed")
        ser.close()
        sys.exit(1)

    # ── Wait for initramfs xmodem prompt ──
    print("\n[6] Waiting for initramfs xmodem prompt...")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 15:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            try:
                sys.stdout.write(chunk.decode("ascii", errors="replace"))
                sys.stdout.flush()
            except:
                pass
        if bytes([NAK]) in buf:
            break
    if bytes([NAK]) not in buf:
        print(f"\n[!] No NAK for initramfs xmodem. Got: {buf[-200:]!r}")
        ser.close()
        sys.exit(1)

    # ── Step 6: Send initramfs ──
    print(f"\n[6] Sending initramfs ({len(initrd_data):,} bytes) via xmodem...")
    if not xmodem_send_verified(ser, initrd_data, "initramfs"):
        print("[!] Initramfs transfer failed")
        ser.close()
        sys.exit(1)

    # ── Watch kernel output ──
    print("\n" + "="*60)
    print(" Linux console output (Ctrl+C to exit)")
    print("="*60 + "\n")

    try:
        while True:
            data = ser.read(ser.in_waiting or 1)
            if data:
                try:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                except:
                    pass
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                user_input = sys.stdin.readline()
                ser.write(user_input.encode())
                ser.flush()
    except KeyboardInterrupt:
        print("\n\n[*] Exiting.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
