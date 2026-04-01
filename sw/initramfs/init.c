/* Minimal nommu init for RISC-V 32 — static PIE, no libc
 * For NEORV32 RV32IMAC nommu Linux on EP4CE6 + 32MB SDRAM
 *
 * Build: riscv32-gcc -nostdlib -nostartfiles -fpie -mcmodel=medany
 *        -fno-plt -static -Wl,-pie -Wl,--no-dynamic-linker
 *        -Os -fno-stack-protector -fno-builtin -o init init.c -e _start
 *
 * Bug fix: struct utsname MUST have 6 fields (including domainname).
 * The kernel's uname() writes 390 bytes; a 5-field struct (325 bytes)
 * causes stack corruption, overwriting saved ra with zeroes.
 */

static inline __attribute__((always_inline)) long
my_syscall(long n, long a0, long a1, long a2) {
    register long _a7 __asm__("a7") = n;
    register long _a0 __asm__("a0") = a0;
    register long _a1 __asm__("a1") = a1;
    register long _a2 __asm__("a2") = a2;
    __asm__ volatile(
        "ecall"
        : "+r"(_a0)
        : "r"(_a1), "r"(_a2), "r"(_a7)
        : "memory", "t0", "t1", "t2", "t3", "t4", "t5", "t6"
    );
    return _a0;
}

/* syscall numbers (RISC-V 32) */
#define __NR_exit       93
#define __NR_read       63
#define __NR_write      64
#define __NR_uname      160
#define __NR_sysinfo    179

struct utsname {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65]; /* MUST include — kernel writes all 6 fields */
};

struct sysinfo {
    long uptime;
    unsigned long loads[3];
    unsigned long totalram;
    unsigned long freeram;
    unsigned long sharedram;
    unsigned long bufferram;
    unsigned long totalswap;
    unsigned long freeswap;
    unsigned short procs;
    unsigned short pad;
    unsigned long totalhigh;
    unsigned long freehigh;
    unsigned int mem_unit;
    char _f[8]; /* padding */
};

static int my_strlen(const char *s) { int n=0; while(s[n])n++; return n; }
static void my_puts(const char *s) { my_syscall(__NR_write, 1, (long)s, my_strlen(s)); }

static void my_putnum(unsigned long v) {
    char buf[12];
    int i = 11;
    buf[i] = 0;
    if (v == 0) { my_puts("0"); return; }
    do { buf[--i] = '0' + (v % 10); v /= 10; } while (v);
    my_puts(buf + i);
}

static int my_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a - *b;
}

static void chomp(char *s) {
    int n = my_strlen(s);
    while (n > 0 && (s[n-1] == '\n' || s[n-1] == '\r')) s[--n] = 0;
}

static void cmd_uname(void) {
    struct utsname u;
    if (my_syscall(__NR_uname, (long)&u, 0, 0) == 0) {
        my_puts(u.sysname); my_puts(" ");
        my_puts(u.nodename); my_puts(" ");
        my_puts(u.release); my_puts(" ");
        my_puts(u.version); my_puts(" ");
        my_puts(u.machine); my_puts("\n");
    }
}

static void cmd_info(void) {
    struct sysinfo si;
    if (my_syscall(__NR_sysinfo, (long)&si, 0, 0) == 0) {
        unsigned long unit = si.mem_unit ? si.mem_unit : 1;
        my_puts("Uptime:    "); my_putnum(si.uptime); my_puts(" s\n");
        my_puts("Total RAM: "); my_putnum((si.totalram * unit) >> 10); my_puts(" KB\n");
        my_puts("Free RAM:  "); my_putnum((si.freeram * unit) >> 10); my_puts(" KB\n");
        my_puts("Processes: "); my_putnum(si.procs); my_puts("\n");
    }
}

static void cmd_help(void) {
    my_puts("Commands:\n");
    my_puts("  uname  - kernel info\n");
    my_puts("  info   - memory & uptime\n");
    my_puts("  help   - this message\n");
    my_puts("  exit   - halt system\n");
}

void _start(void) __attribute__((section(".text.init")));
void _start(void) {
    char buf[128];
    int n;

    my_puts("\n");
    my_puts("========================================\n");
    my_puts(" NEORV32 nommu Linux — mini shell       \n");
    my_puts("========================================\n");
    cmd_uname();
    cmd_info();
    my_puts("\nType 'help' for commands.\n\n");

    for (;;) {
        my_puts("nommu# ");
        n = my_syscall(__NR_read, 0, (long)buf, 127);
        if (n <= 0) break;
        buf[n] = 0;
        chomp(buf);
        if (buf[0] == 0) continue;

        if (my_strcmp(buf, "uname") == 0) cmd_uname();
        else if (my_strcmp(buf, "info") == 0) cmd_info();
        else if (my_strcmp(buf, "help") == 0) cmd_help();
        else if (my_strcmp(buf, "exit") == 0) break;
        else { my_puts("unknown: "); my_puts(buf); my_puts("\n"); }
    }

    my_puts("Halting.\n");
    my_syscall(__NR_exit, 0, 0, 0);
}
