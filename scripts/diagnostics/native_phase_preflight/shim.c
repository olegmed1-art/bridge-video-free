/* ABI-emulated states. Genuine BAD PGconn allocation; never a DB connection. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct pg_conn PGconn;
static PGconn *(*real_start)(const char *);
static int (*real_status)(const PGconn *);
static int (*real_socket)(const PGconn *);
static void (*real_finish)(PGconn *);
static PGconn *active;
static int mode = -1, state, starts, polls, statuses, finishes, nonblocks;
static void stop(int code) { _exit(code); }

static void *symbol(void *lib, const char *name, const char *path, int failure_code) {
    void *ptr = dlsym(lib, name);
    Dl_info info;
    char actual[PATH_MAX];
    if (!ptr || !dladdr(ptr, &info) || !realpath(info.dli_fname, actual) || strcmp(actual, path)) stop(failure_code);
    return ptr;
}

int np_init(const char *path, int selected) {
    if (mode != -1 || selected < 0 || selected > 5 || path[0] != '/') return -1;
    char exact[PATH_MAX];
    if (!realpath(path, exact) || strcmp(path, exact)) return -2;
    void *lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) return -3;
    _Static_assert(sizeof(real_start) == sizeof(void *), "POSIX ABI required");
    _Static_assert(sizeof(real_status) == sizeof(void *), "POSIX ABI required");
    _Static_assert(sizeof(real_socket) == sizeof(void *), "POSIX ABI required");
    _Static_assert(sizeof(real_finish) == sizeof(void *), "POSIX ABI required");
    void *address = symbol(lib, "PQconnectStart", path, 100);
    memcpy(&real_start, &address, sizeof(real_start));
    address = symbol(lib, "PQstatus", path, 101);
    memcpy(&real_status, &address, sizeof(real_status));
    address = symbol(lib, "PQsocket", path, 102);
    memcpy(&real_socket, &address, sizeof(real_socket));
    address = symbol(lib, "PQfinish", path, 103);
    memcpy(&real_finish, &address, sizeof(real_finish));
    mode = selected;
    return 0;
}

PGconn *PQconnectStart(const char *ignored) {
    (void)ignored;
    if (mode < 0 || active || ++starts > 2) stop(104);
    /* Unknown option fails during conninfo parsing, before defaults/network. */
    active = real_start("incident1911_invalid_option=1");
    if (!active) stop(105);
    if (real_status(active) != 1 || real_socket(active) != -1) {
        real_finish(active); active = NULL; stop(106);
    }
    static const int initial[] = {1, 2, 7, 15, 999, 2};
    state = initial[mode];
    return active;
}

int PQstatus(const PGconn *conn) {
    if (!conn) return 1;
    if (conn != active || mode < 0) stop(107);
    statuses++;
    return mode == 0 ? real_status(conn) : state;
}

int PQconnectPoll(PGconn *conn) {
    if (conn != active || mode <= 0 || ++polls > starts) stop(108);
    state = mode == 5 ? 0 : 1;
    return mode == 5 ? 3 : 0; /* PollingStatus OK/FAILED; never READ/WRITE. */
}

int PQsetnonblocking(PGconn *conn, int value) {
    if (conn != active || mode != 5 || state != 0 || value != 1) stop(109);
    nonblocks++;
    return 0;
}

void PQfinish(PGconn *conn) {
    if (!conn) return;
    if (conn != active) stop(110);
    active = NULL;
    finishes++;
    real_finish(conn);
}

int np_counter(int index) {
    switch (index) {
        case 0: return starts;
        case 1: return polls;
        case 2: return statuses;
        case 3: return finishes;
        case 4: return nonblocks;
        case 5: return active != NULL;
        default: stop(111); return -1;
    }
}
