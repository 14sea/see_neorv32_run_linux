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

static void my_puthex(unsigned long v) {
    char buf[12];
    int i;
    buf[0] = '0'; buf[1] = 'x';
    for (i = 0; i < 8; i++)
        buf[2+i] = "0123456789abcdef"[(v >> (28 - i*4)) & 0xf];
    buf[10] = 0;
    my_puts(buf);
}

static void cmd_amo(void) {
    volatile int val __attribute__((aligned(4)));
    int result, tmp;
    int pass = 0, fail = 0;

    my_puts("=== AMO test (Zaamo) ===\n");

    /* Test 1: amoadd.w */
    val = 100;
    __asm__ volatile("amoadd.w %0, %1, (%2)"
        : "=r"(result) : "r"(50), "r"(&val) : "memory");
    my_puts("amoadd.w: old="); my_puthex(result);
    my_puts(" new="); my_puthex(val); my_puts("\n");
    if (result == 100 && val == 150) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test 2: amoswap.w */
    val = 0xDEAD;
    __asm__ volatile("amoswap.w %0, %1, (%2)"
        : "=r"(result) : "r"(0xBEEF), "r"(&val) : "memory");
    my_puts("amoswap.w: old="); my_puthex(result);
    my_puts(" new="); my_puthex(val); my_puts("\n");
    if (result == 0xDEAD && val == 0xBEEF) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test 3: amoor.w */
    val = 0xF0;
    __asm__ volatile("amoor.w %0, %1, (%2)"
        : "=r"(result) : "r"(0x0F), "r"(&val) : "memory");
    my_puts("amoor.w: old="); my_puthex(result);
    my_puts(" new="); my_puthex(val); my_puts("\n");
    if (result == 0xF0 && val == 0xFF) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test 4: amoand.w */
    val = 0xFF;
    __asm__ volatile("amoand.w %0, %1, (%2)"
        : "=r"(result) : "r"(0x0F), "r"(&val) : "memory");
    my_puts("amoand.w: old="); my_puthex(result);
    my_puts(" new="); my_puthex(val); my_puts("\n");
    if (result == 0xFF && val == 0x0F) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    my_puts("\n=== LR/SC detailed tests ===\n");

    /* Test A: Basic lr.w/sc.w (reproduce original bug) */
    val = 42;
    __asm__ volatile(
        "lr.w %0, (%2)\n"
        "sc.w %1, %3, (%2)\n"
        : "=&r"(result), "=&r"(tmp)
        : "r"(&val), "r"(99)
        : "memory");
    my_puts("A) basic: lr="); my_puthex(result);
    my_puts(" sc.rd="); my_puthex(tmp);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0x2a sc.rd=0x00 mem=0x63\n");
    if (result == 42 && tmp == 0 && val == 99) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test B: Explicit register test — use %0/%1 but with early-clobber,
     * and print which registers the compiler actually chose */
    val = 0x100;
    result = -1;
    tmp = -1;
    __asm__ volatile(
        "lr.w %0, (%2)\n"
        "sc.w %1, %3, (%2)\n"
        : "=&r"(result), "=&r"(tmp)
        : "r"(&val), "r"(0x200)
        : "memory");
    my_puts("B) val=0x100: lr="); my_puthex(result);
    my_puts(" sc.rd="); my_puthex(tmp);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0x100 sc.rd=0x00 mem=0x200\n");
    if (result == 0x100 && tmp == 0 && val == 0x200) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test C: sc.w with val=0 — distinguish "returns old val" from "returns 0=success" */
    val = 0;  /* lr.w will load 0 — if sc.w returns old val, it's also 0 (ambiguous!) */
    __asm__ volatile(
        "lr.w %0, (%2)\n"
        "sc.w %1, %3, (%2)\n"
        : "=&r"(result), "=&r"(tmp)
        : "r"(&val), "r"(77)
        : "memory");
    my_puts("C) val=0: lr="); my_puthex(result);
    my_puts(" sc.rd="); my_puthex(tmp);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0x00 sc.rd=0x00 mem=0x4d\n");
    my_puts("   (ambiguous if bug returns old val which is also 0)\n");
    if (result == 0 && tmp == 0 && val == 77) { my_puts("  [PASS/ambiguous]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test D: sc.w with val=1 — if sc.w returns old val (1), it looks like "failure" */
    val = 1;
    __asm__ volatile(
        "lr.w %0, (%2)\n"
        "sc.w %1, %3, (%2)\n"
        : "=&r"(result), "=&r"(tmp)
        : "r"(&val), "r"(88)
        : "memory");
    my_puts("D) val=1: lr="); my_puthex(result);
    my_puts(" sc.rd="); my_puthex(tmp);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0x01 sc.rd=0x00 mem=0x58\n");
    my_puts("   (if bug: sc.rd=0x01 = old val, looks like sc failed)\n");
    if (result == 1 && tmp == 0 && val == 88) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    /* Test E: sc.w should FAIL — store between lr.w and sc.w breaks reservation */
    val = 42;
    __asm__ volatile(
        "lr.w %0, (%2)\n"
        "sw   %3, 0(%2)\n"       /* intervening store — should break reservation */
        "sc.w %1, %4, (%2)\n"
        : "=&r"(result), "=&r"(tmp)
        : "r"(&val), "r"(55), "r"(99)
        : "memory");
    my_puts("E) broken resv: lr="); my_puthex(result);
    my_puts(" sc.rd="); my_puthex(tmp);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0x2a sc.rd=nonzero mem=0x37(55, sc failed)\n");
    if (result == 42 && tmp != 0 && val == 55) { my_puts("  [PASS]\n"); pass++; }
    else {
        my_puts("  [FAIL]");
        if (tmp == 0) my_puts(" (sc.w wrongly succeeded!)");
        if (val == 99) my_puts(" (sc.w wrote despite broken resv!)");
        my_puts("\n");
        fail++;
    }

    /* Test F: second sc.w should FAIL — reservation consumed by first sc.w */
    val = 42;
    {
        int tmp2;
        __asm__ volatile(
            "lr.w %0, (%3)\n"
            "sc.w %1, %4, (%3)\n"    /* first sc.w — should succeed */
            "sc.w %2, %5, (%3)\n"    /* second sc.w — should fail */
            : "=&r"(result), "=&r"(tmp), "=&r"(tmp2)
            : "r"(&val), "r"(99), "r"(200)
            : "memory");
        my_puts("F) double sc: lr="); my_puthex(result);
        my_puts(" sc1.rd="); my_puthex(tmp);
        my_puts(" sc2.rd="); my_puthex(tmp2);
        my_puts(" mem="); my_puthex(val); my_puts("\n");
        my_puts("   expect: lr=0x2a sc1.rd=0x00 sc2.rd=nonzero mem=0x63\n");
        if (result == 42 && tmp == 0 && tmp2 != 0 && val == 99) { my_puts("  [PASS]\n"); pass++; }
        else {
            my_puts("  [FAIL]");
            if (tmp2 == 0) my_puts(" (2nd sc.w wrongly succeeded!)");
            if (val == 200) my_puts(" (2nd sc.w wrote!)");
            my_puts("\n");
            fail++;
        }
    }

    /* Test G: lr.w only (no sc.w) — confirm lr.w works in isolation */
    val = 0xCAFE;
    __asm__ volatile("lr.w %0, (%1)" : "=r"(result) : "r"(&val) : "memory");
    my_puts("G) lr.w only: lr="); my_puthex(result);
    my_puts(" mem="); my_puthex(val); my_puts("\n");
    my_puts("   expect: lr=0xcafe mem=0xcafe\n");
    if (result == 0xCAFE && val == 0xCAFE) { my_puts("  [PASS]\n"); pass++; }
    else { my_puts("  [FAIL]\n"); fail++; }

    my_puts("\nResult: ");
    my_putnum(pass); my_puts("/"); my_putnum(pass + fail);
    my_puts(pass + fail == pass ? " ALL PASSED\n" : " SOME FAILED\n");
}

static void cmd_help(void) {
    my_puts("Commands:\n");
    my_puts("  uname  - kernel info\n");
    my_puts("  info   - memory & uptime\n");
    my_puts("  amo    - test AMO/LR/SC instructions\n");
    my_puts("  help   - this message\n");
    my_puts("  exit   - halt system\n");
}

/* Diagnostic: direct UART write from userspace, bypassing the kernel
 * write() syscall path. On nommu Linux there's no MMU and no protection,
 * so userspace can poke MMIO directly. Lets us see if /init started even
 * if syscall path is wedged.
 */
static inline void __attribute__((always_inline)) diag_putc(char c)
{
    volatile unsigned int *uart = (volatile unsigned int *)0xFFF50000UL;
    while (!(uart[0] & (1u << 19)))
        ;
    uart[1] = (unsigned int)(unsigned char)c;
}

static inline void diag_puts(const char *s)
{
    while (*s) diag_putc(*s++);
}

void _start(void) __attribute__((section(".text.init")));
void _start(void) {
    char buf[128];
    int n;

    diag_putc('!');  /* tiny direct-UART proof we started */

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
        else if (my_strcmp(buf, "amo") == 0) cmd_amo();
        else if (my_strcmp(buf, "help") == 0) cmd_help();
        else if (my_strcmp(buf, "exit") == 0) break;
        else { my_puts("unknown: "); my_puts(buf); my_puts("\n"); }
    }

    my_puts("Halting.\n");
    my_syscall(__NR_exit, 0, 0, 0);
}
