/* Linux x86_64 only. No server, no sockets, no fallback. */
#define _GNU_SOURCE
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>

#if !defined(__x86_64__) || defined(__ILP32__)
#error Unsupported architecture
#endif
#define DENY(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, __NR_##n, 0, 1), BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_KILL_PROCESS)
#define HEADER BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, arch)), BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, AUDIT_ARCH_X86_64, 1, 0), BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_KILL_PROCESS), BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, nr)), BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K, 0x40000000U, 0, 1), BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_KILL_PROCESS)

static int install(struct sock_filter *code, unsigned short count) {
    struct sock_fprog p = {count, code};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return -1;
    return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &p);
}

/* Applied by the already-execed Python child before importing the client. */
int np_lockdown(void) {
    struct sock_filter f[] = {HEADER,
        DENY(clone), DENY(clone3), DENY(fork), DENY(vfork),
        DENY(execve), DENY(execveat), DENY(ptrace), DENY(process_vm_writev),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ALLOW)};
    return install(f, sizeof(f)/sizeof(f[0]));
}

#ifndef NP_SHARED
int main(int argc, char **argv) {
    struct rlimit core = {0, 0}, cpu = {3, 3}, size = {65536, 65536};
    struct rlimit memory = {1073741824, 1073741824};
    struct sock_filter f[] = {HEADER,
        DENY(socket), DENY(socketpair), DENY(connect), DENY(bind),
        DENY(listen), DENY(accept), DENY(accept4), DENY(sendto), DENY(recvfrom),
        DENY(sendmsg), DENY(recvmsg), DENY(sendmmsg), DENY(recvmmsg),
        DENY(shutdown), DENY(getsockname), DENY(getpeername),
        DENY(setsockopt), DENY(getsockopt),
        DENY(io_uring_setup), DENY(io_uring_enter), DENY(io_uring_register),
        BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ALLOW)};
    if (argc < 2 || getuid() == 0 || geteuid() == 0) return 90;
    if (setrlimit(RLIMIT_CORE, &core) || setrlimit(RLIMIT_CPU, &cpu) ||
        setrlimit(RLIMIT_FSIZE, &size) || setrlimit(RLIMIT_AS, &memory)) return 91;
    if (syscall(__NR_close_range, 3U, ~0U, 0U)) return 92;
    if (install(f, sizeof(f)/sizeof(f[0]))) return 93;
    if (argc == 2 && !strcmp(argv[1], "--canary")) {
        /* Invalid fd, no sockaddr: cannot connect even if the filter is absent. */
        syscall(__NR_connect, -1, (void *)0, 0);
        return 94;
    }
    if (argv[1][0] != '/') return 95;
    execv(argv[1], argv + 1);
    return 96;
}
#endif
