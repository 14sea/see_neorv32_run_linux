#!/usr/bin/env python3
"""
sd_pack.py — Pack Linux Image+DTB+initramfs into a fixed-slot SD blob
and write it via stage2's multi-segment write mode 'W'.

Layout (see sd_layout.py):
  LBA 0            : header (magic + sizes + LBAs + layout_version)
  LBA 1..4000      : Image  reserved slot
  LBA 4001..4008   : DTB    reserved slot
  LBA 4009..8008   : initrd reserved slot

Fixed LBAs let sd_update.py later rewrite just the initrd slot without
disturbing the kernel, even after Image changes size.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sd_layout as L
from sd_proto import (program_fpga, bootloader_handshake, upload_stage2,
                      reopen_app, multi_seg_write)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--kernel", default=None)
    ap.add_argument("--dtb", default=None)
    ap.add_argument("--initrd", default=None)
    ap.add_argument("--skip-program", action="store_true")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    kernel = args.kernel or os.path.join(base, "output", "Image")
    dtb    = args.dtb    or os.path.join(base, "output", "neorv32_ax301.dtb")
    initrd = args.initrd or os.path.join(base, "output", "neo_initramfs.cpio.gz")

    with open(kernel, "rb") as f: image_d = f.read()
    with open(dtb,    "rb") as f: dtb_d = f.read()
    with open(initrd, "rb") as f: ird_d = f.read()

    img_pad = L.pad_sector(image_d)
    dtb_pad = L.pad_sector(dtb_d)
    ird_pad = L.pad_sector(ird_d)

    # Slot size checks (don't silently overflow the next slot!)
    checks = [
        ("Image",  len(img_pad) // L.SECTOR, L.IMG_MAX_SEC),
        ("DTB",    len(dtb_pad) // L.SECTOR, L.DTB_MAX_SEC),
        ("initrd", len(ird_pad) // L.SECTOR, L.IRD_MAX_SEC),
    ]
    print(f"[0] Image {len(image_d):,}B  DTB {len(dtb_d):,}B  "
          f"initrd {len(ird_d):,}B")
    for name, used, cap in checks:
        print(f"[0] {name:6s}: {used}/{cap} sectors "
              f"({100*used/cap:.1f}% of slot)")
        if used > cap:
            print(f"[!] {name} overflows its {cap}-sector slot. "
                  f"Bump {name.upper()}_MAX_SEC in sd_layout.py and "
                  f"rebuild stage2 layout.")
            sys.exit(1)

    header = L.build_header(len(image_d), len(dtb_d), len(ird_d))

    segments = [
        (L.HDR_LBA, header),
        (L.IMG_LBA, img_pad),
        (L.DTB_LBA, dtb_pad),
        (L.IRD_LBA, ird_pad),
    ]
    total_sec = sum(len(d) // L.SECTOR for _, d in segments)
    eta = total_sec * 512 * 10 / 115200
    print(f"[0] Total {total_sec} sectors, UART eta ~{eta:.0f}s")

    if not args.skip_program:
        program_fpga(base)

    ser = bootloader_handshake(args.port)
    upload_stage2(ser, stage2)
    ser = reopen_app(ser)

    multi_seg_write(ser, segments)
    ser.close()
    print("=== PACK + WRITE OK ===")


if __name__ == "__main__":
    main()
