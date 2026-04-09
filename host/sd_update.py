#!/usr/bin/env python3
"""
sd_update.py — Incremental update of the SD blob.

Rewrites only the slots you pass on the command line (default: initrd),
plus the header sector. Takes ~1 s vs ~166 s for a full sd_pack.py run.

Before writing, reads the on-card header via stage2 mode 'R' and
verifies magic / layout_version / LBA constants match sd_layout.py.
If not, aborts and tells you to run sd_pack.py first.

Usage:
  python3 host/sd_update.py                   # initrd only (default)
  python3 host/sd_update.py --dtb             # dtb + initrd
  python3 host/sd_update.py --kernel          # kernel (+ dtb + initrd? no — just kernel)

Flags are additive. The header is always rewritten with current sizes.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sd_layout as L
from sd_proto import (program_fpga, bootloader_handshake, upload_stage2,
                      reopen_app, multi_seg_write, read_header)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--kernel", action="store_true",
                    help="also rewrite Image slot (rarely needed)")
    ap.add_argument("--dtb", action="store_true",
                    help="also rewrite DTB slot")
    ap.add_argument("--no-initrd", action="store_true",
                    help="skip initrd (e.g. only updating DTB)")
    ap.add_argument("--skip-program", action="store_true")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stage2 = os.path.join(base, "output", "stage2_loader.bin")
    kernel_path = os.path.join(base, "output", "Image")
    dtb_path    = os.path.join(base, "output", "neorv32_ax301.dtb")
    initrd_path = os.path.join(base, "output", "neo_initramfs.cpio.gz")

    with open(kernel_path, "rb") as f: image_d = f.read()
    with open(dtb_path,    "rb") as f: dtb_d   = f.read()
    with open(initrd_path, "rb") as f: ird_d   = f.read()

    # Slot size checks
    checks = [
        ("Image",  (len(image_d) + 511) // L.SECTOR, L.IMG_MAX_SEC),
        ("DTB",    (len(dtb_d)   + 511) // L.SECTOR, L.DTB_MAX_SEC),
        ("initrd", (len(ird_d)   + 511) // L.SECTOR, L.IRD_MAX_SEC),
    ]
    for name, used, cap in checks:
        if used > cap:
            print(f"[!] {name} ({used} sec) overflows slot ({cap}). "
                  f"Run sd_pack.py after bumping sd_layout.py.")
            sys.exit(1)

    do_initrd = not args.no_initrd
    if not (args.kernel or args.dtb or do_initrd):
        print("[!] nothing to update (use --kernel/--dtb or drop --no-initrd)")
        sys.exit(1)

    if not args.skip_program:
        program_fpga(base)

    ser = bootloader_handshake(args.port)
    upload_stage2(ser, stage2)
    ser = reopen_app(ser)

    # Step 1: verify on-card layout via mode 'R'
    hdr_bytes = read_header(ser)
    try:
        h = L.parse_header(hdr_bytes)
        L.verify_layout(h)
    except ValueError as e:
        print(f"[!] layout check FAILED: {e}")
        print("[!] refusing to do a partial update — run sd_pack.py first.")
        ser.close()
        sys.exit(2)

    print(f"[✓] on-card layout v{h['layout_version']}  "
          f"img_lba={h['image_lba']} dtb_lba={h['dtb_lba']} "
          f"ird_lba={h['initrd_lba']}")
    print(f"    old sizes: img={h['image_sz']} dtb={h['dtb_sz']} "
          f"ird={h['initrd_sz']}")
    print(f"    new sizes: img={len(image_d)} dtb={len(dtb_d)} "
          f"ird={len(ird_d)}")

    # Step 2: build segment list
    new_header = L.build_header(len(image_d), len(dtb_d), len(ird_d))
    segments = [(L.HDR_LBA, new_header)]
    if args.kernel:
        segments.append((L.IMG_LBA, L.pad_sector(image_d)))
    if args.dtb:
        segments.append((L.DTB_LBA, L.pad_sector(dtb_d)))
    if do_initrd:
        segments.append((L.IRD_LBA, L.pad_sector(ird_d)))

    total_sec = sum(len(d) // L.SECTOR for _, d in segments)
    print(f"[*] Updating {total_sec} sector(s) "
          f"({total_sec * 512 / 1024:.1f} KB)")

    multi_seg_write(ser, segments)
    ser.close()
    print("=== UPDATE OK ===")


if __name__ == "__main__":
    main()
