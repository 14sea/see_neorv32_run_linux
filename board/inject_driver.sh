#!/bin/bash
# Inject NEORV32 UART driver into kernel source tree
# Usage: ./inject_driver.sh /path/to/linux-source
#
# This copies neorv32_uart.c into drivers/tty/serial/ and patches
# Kconfig + Makefile to include it. Idempotent — safe to run multiple times.

set -e

KERN_DIR="${1:?Usage: $0 /path/to/linux-source}"
SERIAL_DIR="$KERN_DIR/drivers/tty/serial"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SERIAL_DIR/Kconfig" ]; then
    echo "ERROR: $SERIAL_DIR/Kconfig not found"
    exit 1
fi

# 1. Copy driver source
echo "Copying neorv32_uart.c ..."
cp "$SCRIPT_DIR/neorv32_uart.c" "$SERIAL_DIR/neorv32_uart.c"

# 2. Patch Kconfig (add after SERIAL_LITEUART_CONSOLE block)
if ! grep -q "SERIAL_NEORV32" "$SERIAL_DIR/Kconfig"; then
    echo "Patching Kconfig ..."
    sed -i '/^config SERIAL_SUNPLUS$/i\
config SERIAL_NEORV32\
\tbool "NEORV32 UART support"\
\tdepends on HAS_IOMEM\
\tdepends on OF || COMPILE_TEST\
\tselect SERIAL_CORE\
\thelp\
\t  Serial driver for the NEORV32 RISC-V SoC UART peripheral.\
\t  Say '"'"'Y'"'"' here if your system has a NEORV32 UART.\
\
config SERIAL_NEORV32_CONSOLE\
\tbool "NEORV32 UART console support"\
\tdepends on SERIAL_NEORV32\
\tselect SERIAL_CORE_CONSOLE\
\tselect SERIAL_EARLYCON\
\thelp\
\t  Say '"'"'Y'"'"' here to use the NEORV32 UART as the system console.\
\t  Also provides earlycon support via "earlycon=neorv32,<addr>".\
' "$SERIAL_DIR/Kconfig"
    echo "  Kconfig patched."
else
    echo "  Kconfig already patched, skipping."
fi

# 3. Patch Makefile
if ! grep -q "SERIAL_NEORV32" "$SERIAL_DIR/Makefile"; then
    echo "Patching Makefile ..."
    echo 'obj-$(CONFIG_SERIAL_NEORV32) += neorv32_uart.o' >> "$SERIAL_DIR/Makefile"
    echo "  Makefile patched."
else
    echo "  Makefile already patched, skipping."
fi

# 4. Patch arch/riscv/Kconfig to make PAGE_OFFSET user-configurable
RISCV_KCONFIG="$KERN_DIR/arch/riscv/Kconfig"
if grep -q '^config PAGE_OFFSET' "$RISCV_KCONFIG" && \
   ! grep -A1 '^config PAGE_OFFSET' "$RISCV_KCONFIG" | grep -q 'prompt\|Page offset'; then
    echo "Patching arch/riscv/Kconfig: making PAGE_OFFSET user-configurable ..."
    sed -i '/^config PAGE_OFFSET$/,/^$/{
        /^\thex$/c\\thex "Virtual page offset (must match PHYS_RAM_BASE for nommu)"
    }' "$RISCV_KCONFIG"
    echo "  PAGE_OFFSET now configurable via defconfig."
else
    echo "  PAGE_OFFSET already configurable or not found, skipping."
fi

echo "Done. NEORV32 patches applied to $KERN_DIR"
