#!/usr/bin/env python3
"""
boot_sd.py — Boot Linux by instructing stage2 to load the blob from SD.

Assumes sd_pack.py has already written a blob (magic NEOLNX) to LBA 0.
No xmodem transfer — just program FPGA, upload stage2, send 'b'.
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sd_layout as L
from sd_proto import (get_session, maybe_switch_baud, read_header,
                      BAUD_CANDIDATES, APP_BAUD)
from sd_update import update_sd


def check_build_tag(ser, base):
    """Read on-card header and compare its sizes to local output/ files.
    Prints a status line; returns True if everything matches."""
    try:
        h = L.parse_header(read_header(ser))
        L.verify_layout(h)
    except (ValueError, RuntimeError) as e:
        print(f"[!] on-card header unreadable: {e}")
        return False

    files = {
        "Image":  os.path.join(base, "output", "Image"),
        "DTB":    os.path.join(base, "output", "neorv32_ax301.dtb"),
        "initrd": os.path.join(base, "output", "neo_initramfs.cpio.gz"),
    }
    local = {k: (os.path.getsize(v) if os.path.exists(v) else None)
             for k, v in files.items()}
    on_card = {"Image": h["image_sz"], "DTB": h["dtb_sz"],
               "initrd": h["initrd_sz"]}

    all_match = True
    for name in ("Image", "DTB", "initrd"):
        lo, oc = local[name], on_card[name]
        if lo is None:
            print(f"    {name:6s}: on-card={oc:>8}  local=(missing)")
            continue
        tag = "✓" if lo == oc else "✗ STALE"
        if lo != oc: all_match = False
        print(f"    {name:6s}: on-card={oc:>8}  local={lo:>8}  {tag}")
    if not all_match:
        print("[!] Local output/ differs from SD card — boot may be stale.")
        print("    Hint: re-run with --update (init only) or sd_pack.py "
              "(full).")
    return all_match


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--skip-program", action="store_true")
    ap.add_argument("--baud", type=int, default=APP_BAUD,
                    help="console baud (default 115200; kernel uses 115200)")
    ap.add_argument("--persistent", action="store_true",
                    help="attach to already-running stage2 (skip program/upload)")
    ap.add_argument("--update", action="store_true",
                    help="rewrite init slot (+ header) from local "
                         "output/ before booting — one-shot edit/test loop")
    ap.add_argument("--update-kernel", action="store_true",
                    help="with --update, also rewrite Image slot")
    ap.add_argument("--update-dtb", action="store_true",
                    help="with --update, also rewrite DTB slot")
    ap.add_argument("--update-verify", action="store_true",
                    help="with --update, re-read header after write")
    ap.add_argument("--no-check", action="store_true",
                    help="skip on-card vs local size comparison")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")

    # For --update we need the fast baud during the write, then drop back
    # to 115200 before sending 'b' (kernel UART runs at 115200).
    session_baud = BAUD_CANDIDATES[0] if args.update else args.baud
    ser = get_session(base, args.port, stage2,
                      skip_program=args.skip_program,
                      target_baud=session_baud,
                      persistent=args.persistent)

    if args.update:
        try:
            update_sd(ser, base,
                      do_kernel=args.update_kernel,
                      do_dtb=args.update_dtb,
                      do_initrd=True,
                      verify=args.update_verify)
        except (ValueError, RuntimeError) as e:
            print(f"[!] update failed: {e}")
            ser.close(); sys.exit(2)
        # Drop back to console baud before jumping to kernel.
        if ser.baudrate != args.baud:
            new_ser = maybe_switch_baud(ser, args.baud)
            if not new_ser:
                print("[!] could not drop baud back for console; aborting")
                ser.close(); sys.exit(2)
            ser = new_ser
    elif not args.no_check:
        # No --update: compare on-card sizes to local output/ so the user
        # notices stale SD contents before a 150 s boot cycle.
        print("[*] Build-tag check (on-card vs local output/):")
        check_build_tag(ser, base)

    ser.timeout = 0.5
    print(f"[3] @ {ser.baudrate}, sending 'b' (boot from SD)")
    time.sleep(0.3)
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
