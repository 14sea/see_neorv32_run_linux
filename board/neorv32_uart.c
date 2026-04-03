// SPDX-License-Identifier: GPL-2.0
/*
 * NEORV32 UART serial driver for Linux
 *
 * Register layout (8 bytes per UART):
 *   +0x0  CTRL  [0]:EN  [5:3]:PRSC  [15:6]:BAUD_DIV
 *               [16]:RX_NEMPTY  [17]:RX_FULL  [18]:TX_EMPTY  [19]:TX_NFULL
 *               [20]:IRQ_RX_NEMPTY  [21]:IRQ_RX_FULL
 *               [22]:IRQ_TX_EMPTY   [23]:IRQ_TX_NFULL
 *               [30]:RX_OVER  [31]:TX_BUSY
 *   +0x4  DATA  [7:0]: TX/RX byte
 *
 * Polling mode with kthread (not timer) to ensure tty flip buffer flush
 * runs in process context — required on UP PREEMPT_NONE where unbound
 * work queue kworkers may not get scheduled promptly.
 */

#include <linux/console.h>
#include <linux/init.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/serial.h>
#include <linux/serial_core.h>
#include <linux/tty_flip.h>
#include <linux/timer.h>
#include <linux/delay.h>
#include <linux/io.h>

/* Forward declare debug function */
static void neo_uart_dbg(char c);

/* tty_ldisc_receive_buf from drivers/tty/tty_buffer.c */
#include <linux/tty_ldisc.h>

#define DRIVER_NAME	"neorv32-uart"
#define DEV_NAME	"ttyNEO"
#define NEORV32_NR	1

/* Register offsets */
#define NEO_CTRL	0x00
#define NEO_DATA	0x04

/* CTRL register bits */
#define CTRL_EN			BIT(0)
#define CTRL_PRSC_SHIFT		3
#define CTRL_BAUD_SHIFT		6
#define CTRL_RX_NEMPTY		BIT(16)
#define CTRL_RX_FULL		BIT(17)
#define CTRL_TX_EMPTY		BIT(18)
#define CTRL_TX_NFULL		BIT(19)
#define CTRL_TX_BUSY		BIT(31)

struct neorv32_port {
	struct uart_port port;
	struct task_struct *poll_thread;
	bool port_open;
};

#define to_neo_port(p) container_of(p, struct neorv32_port, port)

static struct neorv32_port neo_ports[NEORV32_NR];

/* ── Low-level I/O ─────────────────────────────────────────────────────── */

static void neorv32_putchar(struct uart_port *port, unsigned char ch)
{
	while (!(readl(port->membase + NEO_CTRL) & CTRL_TX_NFULL))
		cpu_relax();
	writel(ch, port->membase + NEO_DATA);
}

/* neorv32_rx_chars is replaced by neorv32_rx_direct + tty_ldisc_receive_buf
 * in the kthread.  No flip buffer needed.
 */

static void neorv32_tx_chars(struct uart_port *port)
{
	u8 ch;

	uart_port_tx(port, ch,
		readl(port->membase + NEO_CTRL) & CTRL_TX_NFULL,
		writel(ch, port->membase + NEO_DATA));
}

/* ── Polling kthread ──────────────────────────────────────────────────── */

/*
 * Read RX chars directly into a local buffer (not flip buffer).
 * Returns number of chars read.
 */
static int neorv32_rx_direct(struct uart_port *port, u8 *buf, int max)
{
	int n = 0;

	while (n < max && (readl(port->membase + NEO_CTRL) & CTRL_RX_NEMPTY)) {
		buf[n++] = readl(port->membase + NEO_DATA) & 0xFF;
		port->icount.rx++;
	}
	return n;
}

static int neorv32_poll_thread(void *data)
{
	struct neorv32_port *neo = data;
	struct uart_port *port = &neo->port;
	struct tty_struct *tty;
	struct tty_ldisc *ld;
	unsigned long flags;
	u8 rxbuf[64];
	u8 flags_buf[64];
	int got;

	while (!kthread_should_stop()) {
		/* RX: read chars from UART hardware */
		spin_lock_irqsave(&port->lock, flags);
		got = neorv32_rx_direct(port, rxbuf, sizeof(rxbuf));
		spin_unlock_irqrestore(&port->lock, flags);

		if (got && port->state) {
			tty = port->state->port.tty;
			if (tty) {
				ld = tty_ldisc_ref(tty);
				if (ld) {
					memset(flags_buf, TTY_NORMAL, got);
					tty_ldisc_receive_buf(ld, rxbuf,
							      flags_buf, got);
					tty_ldisc_deref(ld);
				}
			}
		}

		/* TX: drain any pending output */
		spin_lock_irqsave(&port->lock, flags);
		neorv32_tx_chars(port);
		spin_unlock_irqrestore(&port->lock, flags);

		/* Yield CPU so other tasks (e.g. shell) can run */
		schedule_timeout_interruptible(1);
	}

	return 0;
}

/* ── uart_ops ──────────────────────────────────────────────────────────── */

static unsigned int neorv32_tx_empty(struct uart_port *port)
{
	return (readl(port->membase + NEO_CTRL) & CTRL_TX_EMPTY)
		? TIOCSER_TEMT : 0;
}

static void neorv32_set_mctrl(struct uart_port *port, unsigned int mctrl) {}
static unsigned int neorv32_get_mctrl(struct uart_port *port)
{
	return TIOCM_CTS | TIOCM_DSR | TIOCM_CAR;
}
static void neorv32_stop_tx(struct uart_port *port) {}
static void neorv32_stop_rx(struct uart_port *port) {}

static void neorv32_start_tx(struct uart_port *port)
{
	neorv32_tx_chars(port);
}

static int neorv32_startup(struct uart_port *port)
{
	struct neorv32_port *neo = to_neo_port(port);
	u32 ctrl;

	neo_uart_dbg('S');  /* startup called */

	/* Ensure UART is enabled (U-Boot already set baud rate) */
	ctrl = readl(port->membase + NEO_CTRL);
	writel(ctrl | CTRL_EN, port->membase + NEO_CTRL);

	neo->port_open = true;

	/* Start polling kthread */
	neo->poll_thread = kthread_run(neorv32_poll_thread, neo,
				       "neorv32_poll");
	if (IS_ERR(neo->poll_thread)) {
		neo->poll_thread = NULL;
		neo_uart_dbg('!');
		return PTR_ERR(neo->poll_thread);
	}

	neo_uart_dbg('s');  /* startup done, kthread started */
	return 0;
}

static void neorv32_shutdown(struct uart_port *port)
{
	struct neorv32_port *neo = to_neo_port(port);

	neo->port_open = false;
	if (neo->poll_thread) {
		kthread_stop(neo->poll_thread);
		neo->poll_thread = NULL;
	}
}

static void neorv32_set_termios(struct uart_port *port,
				struct ktermios *new,
				const struct ktermios *old)
{
	unsigned int baud;
	unsigned long flags;

	spin_lock_irqsave(&port->lock, flags);
	baud = uart_get_baud_rate(port, new, old, 9600, 115200);
	uart_update_timeout(port, new->c_cflag, baud);
	spin_unlock_irqrestore(&port->lock, flags);
}

static const char *neorv32_type(struct uart_port *port)
{
	return "neorv32-uart";
}

static void neorv32_config_port(struct uart_port *port, int flags)
{
	port->type = 1;
}

static int neorv32_verify_port(struct uart_port *port,
			       struct serial_struct *ser)
{
	return 0;
}

static const struct uart_ops neorv32_uart_ops = {
	.tx_empty	= neorv32_tx_empty,
	.set_mctrl	= neorv32_set_mctrl,
	.get_mctrl	= neorv32_get_mctrl,
	.stop_tx	= neorv32_stop_tx,
	.start_tx	= neorv32_start_tx,
	.stop_rx	= neorv32_stop_rx,
	.startup	= neorv32_startup,
	.shutdown	= neorv32_shutdown,
	.set_termios	= neorv32_set_termios,
	.type		= neorv32_type,
	.config_port	= neorv32_config_port,
	.verify_port	= neorv32_verify_port,
};

/* ── Console ───────────────────────────────────────────────────────────── */

#ifdef CONFIG_SERIAL_NEORV32_CONSOLE

static struct uart_driver neorv32_uart_driver;

static void neorv32_console_write(struct console *co, const char *s,
				  unsigned int count)
{
	struct uart_port *port = &neo_ports[co->index].port;
	unsigned long flags;

	spin_lock_irqsave(&port->lock, flags);
	uart_console_write(port, s, count, neorv32_putchar);
	spin_unlock_irqrestore(&port->lock, flags);
}

static int neorv32_console_setup(struct console *co, char *options)
{
	struct uart_port *port;
	int baud = 115200;
	int bits = 8;
	int parity = 'n';
	int flow = 'n';

	if (co->index >= NEORV32_NR || co->index < 0)
		co->index = 0;

	port = &neo_ports[co->index].port;
	if (!port->membase)
		return -ENODEV;

	if (options)
		uart_parse_options(options, &baud, &parity, &bits, &flow);

	return uart_set_options(port, co, baud, parity, bits, flow);
}

static struct console neorv32_console = {
	.name	= DEV_NAME,
	.write	= neorv32_console_write,
	.device	= uart_console_device,
	.setup	= neorv32_console_setup,
	.flags	= CON_PRINTBUFFER,
	.index	= -1,
	.data	= &neorv32_uart_driver,
};

#define NEORV32_CONSOLE	(&neorv32_console)

/* earlycon */
static void neorv32_earlycon_write(struct console *con, const char *s,
				   unsigned int n)
{
	struct earlycon_device *dev = con->data;

	uart_console_write(&dev->port, s, n, neorv32_putchar);
}

static int __init neorv32_earlycon_setup(struct earlycon_device *dev,
					 const char *options)
{
	if (!dev->port.membase)
		return -ENODEV;

	dev->con->write = neorv32_earlycon_write;
	return 0;
}

OF_EARLYCON_DECLARE(neorv32, "stnolting,neorv32-uart", neorv32_earlycon_setup);
EARLYCON_DECLARE(neorv32, neorv32_earlycon_setup);

#else
#define NEORV32_CONSOLE	NULL
#endif /* CONFIG_SERIAL_NEORV32_CONSOLE */

/* ── Platform driver ───────────────────────────────────────────────────── */

static struct uart_driver neorv32_uart_driver = {
	.owner		= THIS_MODULE,
	.driver_name	= DRIVER_NAME,
	.dev_name	= DEV_NAME,
	.nr		= NEORV32_NR,
	.cons		= NEORV32_CONSOLE,
};

static int neorv32_probe(struct platform_device *pdev)
{
	struct neorv32_port *neo;
	struct uart_port *port;
	int id, ret;

	neo_uart_dbg('P');  /* probe entry */
	id = of_alias_get_id(pdev->dev.of_node, "serial");
	if (id < 0 || id >= NEORV32_NR)
		id = 0;

	neo = &neo_ports[id];
	port = &neo->port;

	port->membase = devm_platform_get_and_ioremap_resource(pdev, 0, NULL);
	if (IS_ERR(port->membase))
		return PTR_ERR(port->membase);

	port->dev = &pdev->dev;
	port->iotype = UPIO_MEM;
	port->flags = UPF_BOOT_AUTOCONF;
	port->ops = &neorv32_uart_ops;
	port->fifosize = 16;
	port->type = PORT_UNKNOWN;
	port->line = id;
	spin_lock_init(&port->lock);

	platform_set_drvdata(pdev, port);

	neo_uart_dbg('Q');  /* before uart_add_one_port */
	ret = uart_add_one_port(&neorv32_uart_driver, port);
	neo_uart_dbg('R');  /* after uart_add_one_port */
	neo_uart_dbg('0' + ret);  /* return value */
	return ret;
}

static int neorv32_remove(struct platform_device *pdev)
{
	struct uart_port *port = platform_get_drvdata(pdev);

	uart_remove_one_port(&neorv32_uart_driver, port);
	return 0;
}

static const struct of_device_id neorv32_of_match[] = {
	{ .compatible = "stnolting,neorv32-uart" },
	{}
};
MODULE_DEVICE_TABLE(of, neorv32_of_match);

static struct platform_driver neorv32_platform_driver = {
	.probe	= neorv32_probe,
	.remove	= neorv32_remove,
	.driver	= {
		.name		= DRIVER_NAME,
		.of_match_table	= neorv32_of_match,
	},
};

/* Direct UART write for debug - bypasses console framework */
static void neo_uart_dbg(char c)
{
	volatile unsigned int *ctrl = (volatile unsigned int *)0xfff50000;
	volatile unsigned int *data = (volatile unsigned int *)0xfff50004;
	while (!((*ctrl) & (1u << 19))) ;  /* wait TX_NFULL */
	*data = c;
}

static int __init neorv32_uart_init(void)
{
	int ret;

	neo_uart_dbg('(');  /* entering uart_init */
	ret = uart_register_driver(&neorv32_uart_driver);
	neo_uart_dbg('*');  /* uart_register_driver done */
	if (ret)
		return ret;

	ret = platform_driver_register(&neorv32_platform_driver);
	neo_uart_dbg('+');  /* platform_driver_register done */
	if (ret)
		uart_unregister_driver(&neorv32_uart_driver);

	neo_uart_dbg(')');  /* uart_init returning */
	return ret;
}

static void __exit neorv32_uart_exit(void)
{
	platform_driver_unregister(&neorv32_platform_driver);
	uart_unregister_driver(&neorv32_uart_driver);
}

module_init(neorv32_uart_init);
module_exit(neorv32_uart_exit);

MODULE_DESCRIPTION("NEORV32 UART serial driver");
MODULE_LICENSE("GPL");
