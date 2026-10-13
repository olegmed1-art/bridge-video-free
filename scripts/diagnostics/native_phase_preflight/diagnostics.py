"""Bounded diagnostic vocabulary. Never serialize exception messages or raw stderr."""
import errno
from pathlib import Path
import re

MAX_STDERR = 8192
MAX_LINES = 16
MAX_LINE = 256
FILES = ('guard.c', 'shim.c', 'runner.py', 'native_child.py', 'contract.py',
         'phase_observer.py', 'evidence.py', 'diagnostics.py', 'process_runner.py',
         'connection.py', '_connection_base.py', 'waiting.py', 'generators.pyx')
EXCEPTIONS = ('ValueError', 'TypeError', 'AssertionError', 'RuntimeError', 'OSError',
              'FileNotFoundError', 'PermissionError', 'ImportError', 'ModuleNotFoundError',
              'AttributeError', 'KeyError', 'IndexError', 'MemoryError', 'OverflowError',
              'UnicodeError', 'UnicodeDecodeError', 'JSONDecodeError', 'TimeoutExpired')
ERRNOS = tuple(sorted(set(getattr(errno, x) for x in
               ('ENOENT', 'EACCES', 'EPERM', 'ENOEXEC', 'ENOMEM', 'ENOSPC', 'EMFILE',
                'ENFILE', 'EIO', 'EINVAL', 'EPIPE', 'ECHILD', 'ESRCH'))))
# Finite public vocabulary, not a general secret-pattern blacklist.
WORDS = frozenset('error warning note fatal cc gcc cc1 collect2 ld as linker assembler '
    'compilation terminated all warnings being treated as errors implicit declaration '
    'of function undeclared first use in this function expected before token identifier '
    'incompatible pointer type types passing argument makes integer from without cast '
    'initialization assignment return comparison signedness unused variable parameter '
    'defined but not used unknown type name storage size is not known too few many '
    'arguments to invalid operands binary has no member named cannot find such file or '
    'directory Permission denied permission No space left on device read-only filesystem '
    'unrecognized command-line option undefined reference multiple definition relocation '
    'against can not be when making a shared object recompile with fPIC failed execute '
    'execvp execv fatal_error killed signal terminated program internal compiler '
    'missing terminating character header include requested operation unsupported '
    'conflicting declaration previous here at top level statement expression required '
    'lvalue must constant braces scalar initializer excess elements array'.split())
SYMBOLS = frozenset('sock_filter sock_fprog seccomp_data rlimit prctl syscall execv '
    'setrlimit getuid geteuid strcmp install np_lockdown main offsetof code count arch nr '
    'BPF_JUMP BPF_STMT BPF_JMP BPF_JEQ BPF_K BPF_RET BPF_LD BPF_W BPF_ABS BPF_JSET '
    'SECCOMP_RET_KILL_PROCESS SECCOMP_RET_ALLOW SECCOMP_MODE_FILTER AUDIT_ARCH_X86_64 '
    'PR_SET_NO_NEW_PRIVS PR_SET_SECCOMP RLIMIT_CORE RLIMIT_CPU RLIMIT_FSIZE RLIMIT_AS '
    'NP_SHARED DENY HEADER real_start real_status real_socket real_finish PGconn '
    'PQconnectStart PQstatus PQsocket PQfinish PQconnectPoll PQsetnonblocking '
    'np_init np_counter dlsym dladdr dlopen Dl_info realpath PATH_MAX memcpy '
    'RTLD_NOW RTLD_LOCAL int void char unsigned short long size_t uint32_t '
    'linux audit filter seccomp sys resource syscall unistd stddef stdint string '
    '__NR_close_range __NR_connect __NR_clone __NR_clone3 __NR_fork __NR_vfork '
    '__NR_execve __NR_execveat __NR_ptrace __NR_process_vm_writev'.split())
TOKENS = WORDS | SYMBOLS | frozenset(('redacted',))
CLASSIFIERS = {
    'missing_file': r'No such file or directory|cannot find',
    'permission': r'Permission denied|Operation not permitted',
    'space': r'No space left on device|Read-only file system',
    'subprogram': r'execvp|execv:|cannot execute|cannot find.*(?:cc1|as|ld)',
    'undeclared': r'undeclared|implicit declaration',
    'type_mismatch': r'incompatible|conflicting types|unknown type',
    'syntax': r'expected|invalid operands|missing terminating',
    'unused': r'unused|not used',
    'warnings_as_errors': r'Werror|warnings being treated as errors',
    'link': r'undefined reference|multiple definition|relocation|collect2:',
    'killed': r'Killed|killed|terminated program',
}
SUBJECTS = ('linux/audit.h', 'linux/filter.h', 'linux/seccomp.h', 'sys/prctl.h', 'sys/resource.h',
            'sys/syscall.h', 'stddef.h', 'stdint.h', 'string.h', 'unistd.h', 'dlfcn.h', 'limits.h',
            'cc1', 'collect2', 'ld', 'as', '__NR_close_range', '__NR_clone3', '__NR_io_uring_setup',
            '__NR_io_uring_enter', '__NR_io_uring_register', 'SECCOMP_RET_KILL_PROCESS',
            'PR_SET_SECCOMP', 'sock_filter', 'Dl_info')


def safe_errno(exc):
    value = exc.errno if isinstance(exc, OSError) else None
    return value if type(value) is int and value in ERRNOS else None


def exception_summary(exc, root):
    # No str/repr(exc), exception args, globals, locals, or formatted traceback.
    name = type(exc).__name__
    frames = []
    tb = exc.__traceback__
    while tb is not None:
        path = Path(tb.tb_frame.f_code.co_filename)
        try:
            public = path.resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):
            public = False
        if public and path.name in FILES and 1 <= tb.tb_lineno <= 10000:
            frames.append([path.name, tb.tb_lineno])
        tb = tb.tb_next
    return {'exception': name if name in EXCEPTIONS else 'other',
            'errno': safe_errno(exc), 'frames': frames[-8:]}


def validate_exception(value):
    if type(value) is not dict or set(value) != {'exception', 'errno', 'frames'}:
        raise ValueError('invalid_exception_summary')
    if value['exception'] not in (*EXCEPTIONS, 'other') or (value['errno'] is not None and
            (type(value['errno']) is not int or value['errno'] not in ERRNOS)):
        raise ValueError('invalid_exception_summary')
    frames = value['frames']
    if type(frames) is not list or len(frames) > 8 or any(type(f) is not list or len(f) != 2 or
            f[0] not in FILES or type(f[1]) is not int or not 1 <= f[1] <= 10000 for f in frames):
        raise ValueError('invalid_exception_summary')


def stderr_summary(data, overflow=False):
    """Retain location + finite public word tokens; unknown content is redacted."""
    if type(data) is not bytes or type(overflow) is not bool:
        raise ValueError('invalid_stderr_input')
    truncated = overflow or len(data) > MAX_STDERR
    lines = data[:MAX_STDERR].decode('ascii', errors='replace').splitlines()
    result = []
    classes = [name for name, pattern in CLASSIFIERS.items() if re.search(pattern, data[:MAX_STDERR].decode('ascii', errors='replace'))]
    subjects = [name for name in SUBJECTS if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', data[:MAX_STDERR].decode('ascii', errors='replace'))]
    for raw in lines[:MAX_LINES]:
        if len(raw) > 1024: truncated = True
        raw = raw[:1024]
        # Strip all path-like, URI, numeric, control, and quoted content before words.
        # Source locations only accepted when the whole prefix is an exact public filename.
        match = re.match(r'^(guard\.c|shim\.c):([0-9]{1,4})(?::([0-9]{1,4}))?:\s*(.*)$', raw)
        loc = {'file': None, 'line': None, 'column': None}
        if match:
            loc = {'file': match[1], 'line': int(match[2]),
                   'column': int(match[3]) if match[3] else None}
            raw = match[4]
        raw = re.sub(r'\b(error|warning|note|fatal):', r'\1 ', raw)
        raw = re.sub(r'\S*(?:[/\\:@]|[0-9])\S*', ' redacted ', raw)
        words = re.findall(r'[A-Za-z_][A-Za-z_0-9]*', raw)
        tokens = []
        for word in words:
            token = word if word in TOKENS else 'redacted'
            if not tokens or token != 'redacted' or tokens[-1] != 'redacted':
                tokens.append(token)
        text = ' '.join(tokens)
        if len(text) > MAX_LINE:
            # Cut between whole known tokens so validation remains exact.
            while len(' '.join(tokens)) > MAX_LINE: tokens.pop()
            text = ' '.join(tokens)
            truncated = True
        result.append({**loc, 'text': text})
        if len(raw) >= 1024: truncated = True
    return {'bytes_retained': min(len(data), MAX_STDERR),
            'truncated': truncated or len(lines) > MAX_LINES, 'classifications': classes, 'subjects': subjects, 'lines': result}


def validate_stderr(value):
    if type(value) is not dict or set(value) != {'bytes_retained', 'truncated', 'classifications', 'subjects', 'lines'}:
        raise ValueError('invalid_stderr_summary')
    if type(value['bytes_retained']) is not int or not 0 <= value['bytes_retained'] <= MAX_STDERR or type(value['truncated']) is not bool:
        raise ValueError('invalid_stderr_summary')
    if type(value['lines']) is not list or len(value['lines']) > MAX_LINES:
        raise ValueError('invalid_stderr_summary')
    if type(value['classifications']) is not list or len(value['classifications']) > len(CLASSIFIERS) or any(type(x) is not str or x not in CLASSIFIERS for x in value['classifications']):
        raise ValueError('invalid_stderr_summary')
    if type(value['subjects']) is not list or len(value['subjects']) > len(SUBJECTS) or any(type(x) is not str or x not in SUBJECTS for x in value['subjects']):
        raise ValueError('invalid_stderr_summary')
    for row in value['lines']:
        if type(row) is not dict or set(row) != {'file', 'line', 'column', 'text'}:
            raise ValueError('invalid_stderr_summary')
        if row['file'] not in (None, 'guard.c', 'shim.c') or type(row['text']) is not str or len(row['text']) > MAX_LINE:
            raise ValueError('invalid_stderr_summary')
        if any(token not in TOKENS for token in row['text'].split(' ')) and row['text']:
            raise ValueError('invalid_stderr_summary')
        for key in ('line', 'column'):
            if row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= 9999):
                raise ValueError('invalid_stderr_summary')
        if row['file'] is None and (row['line'] is not None or row['column'] is not None):
            raise ValueError('invalid_stderr_summary')


def validate_child_diagnostic(value):
    if type(value) is not dict or set(value) != {'schema', 'cleanup', 'exception', 'shim_init_result'} or value['schema'] != 'native-child-diagnostic-v1':
        raise ValueError('invalid_child_diagnostic')
    if value['shim_init_result'] is not None and (type(value['shim_init_result']) is not int or value['shim_init_result'] not in (-3,-2,-1,0)):
        raise ValueError('invalid_child_diagnostic')
    cleanup = value['cleanup']
    if type(cleanup) is not dict or set(cleanup) != {'logger_detached', 'observer_closed', 'native_active_zero', 'receipt_closed'}:
        raise ValueError('invalid_child_diagnostic')
    if any(v is not None and type(v) is not bool for v in cleanup.values()):
        raise ValueError('invalid_child_diagnostic')
    if value['exception'] is not None: validate_exception(value['exception'])
