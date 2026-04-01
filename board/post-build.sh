#!/bin/bash
# Post-build script for NEORV32 AX301 nommu Linux
# Called by Buildroot after building all packages

set -e

BOARD_DIR="$(dirname "$0")"
TARGET_DIR="$1"

# Nothing to do yet — initramfs is built by Buildroot
echo "NEORV32 post-build: done"
