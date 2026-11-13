#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

enum launcher_stage {
    LAUNCHER_SESSION = 1,
    LAUNCHER_DESCRIPTORS = 2,
    LAUNCHER_SIGNALS = 3,
    LAUNCHER_DIRECTORY = 4,
    LAUNCHER_READY = 5,
    LAUNCHER_GATE = 6,
    LAUNCHER_EXEC = 7,
};

struct launcher_error {
    int stage;
    int error;
};

struct owned_reap_result {
    int complete;
    int code;
    int status;
};


int verjson_owned_pipe(int slots[2]) {
    if (slots == NULL || slots[0] != -1 || slots[1] != -1)
        return -EINVAL;
    if (pipe2(slots, O_CLOEXEC) < 0)
        return -errno;
    return 0;
}

int verjson_owned_close(int *slot) {
    if (slot == NULL || *slot < -1)
        return -EINVAL;
    if (*slot == -1)
        return 0;
    int result = close(*slot);
    int error = errno;
    *slot = -1;
    return result < 0 ? -error : 0;
}

int verjson_owned_dup(int *slot, int descriptor) {
    if (slot == NULL || *slot != -1 || descriptor < 0)
        return -EINVAL;
    int duplicate = fcntl(descriptor, F_DUPFD_CLOEXEC, 0);
    if (duplicate < 0)
        return -errno;
    *slot = duplicate;
    return 0;
}

int verjson_owned_openat(int *slot, int directory, const char *path, int flags, unsigned int mode) {
    if (slot == NULL || *slot != -1 || path == NULL)
        return -EINVAL;
    int descriptor = openat(directory, path, flags | O_CLOEXEC, (mode_t)mode);
    if (descriptor < 0)
        return -errno;
    *slot = descriptor;
    return 0;
}

int verjson_owned_pidfd_open(int *slot, int pid) {
    if (slot == NULL || *slot != -1 || pid <= 1)
        return -EINVAL;
    int descriptor = (int)syscall(SYS_pidfd_open, pid, 0U);
    if (descriptor < 0)
        return -errno;
    *slot = descriptor;
    return 0;
}

int verjson_owned_pid_namespace(int *slot, int pid) {
    if (slot == NULL || *slot != -1 || pid <= 1)
        return -EINVAL;
    char path[64];
    int length = snprintf(path, sizeof(path), "/proc/%d/ns/pid", pid);
    if (length < 0 || (size_t)length >= sizeof(path))
        return -EINVAL;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC);
    if (descriptor < 0)
        return -errno;
    *slot = descriptor;
    return 0;
}

int verjson_owned_reap(int pidfd, struct owned_reap_result *result) {
    if (pidfd < 0 || result == NULL || result->complete != 0)
        return -EINVAL;
    siginfo_t info = {0};
    if (waitid(P_PIDFD, (id_t)pidfd, &info, WEXITED | WNOHANG) < 0)
        return -errno;
    if (info.si_pid != 0) {
        result->code = info.si_code;
        result->status = info.si_status;
        result->complete = 1;
    }
    return 0;
}

static _Noreturn void child_failure(int fd, int stage, int error) {
    struct launcher_error record = {stage, error};
    const char *cursor = (const char *)&record;
    size_t remaining = sizeof(record);
    while (remaining != 0) {
        ssize_t written = write(fd, cursor, remaining);
        if (written < 0 && errno == EINTR)
            continue;
        if (written <= 0)
            break;
        cursor += written;
        remaining -= (size_t)written;
    }
    _exit(125);
}

static int reset_child_signals(void) {
    struct sigaction action = {0};
    action.sa_handler = SIG_DFL;
    if (sigemptyset(&action.sa_mask) < 0)
        return -1;
    for (int signum = 1; signum < NSIG; ++signum) {
        if (signum == SIGKILL || signum == SIGSTOP)
            continue;
        struct sigaction previous;
        if (sigaction(signum, NULL, &previous) < 0) {
            if (errno == EINVAL)
                continue;
            return -1;
        }
        if (previous.sa_handler != SIG_IGN || signum == SIGPIPE || signum == SIGXFSZ ||
            signum == SIGINT || signum == SIGTERM || signum == SIGCHLD) {
            if (sigaction(signum, &action, NULL) < 0)
                return -1;
        }
    }
    return 0;
}

/* The caller owns all descriptors through return. A negative result can still
 * carry an owned pidfd if restoring the parent signal mask failed. */
int verjson_pidfd_launch(
    int *pidfd, int input_fd, int output_fd, int ready_fd, int gate_fd,
    int error_fd, const int *inherited_fds, size_t inherited_count,
    const char *cwd, char *const argv[], char *const envp[]
) {
    if (pidfd == NULL || argv == NULL || argv[0] == NULL || envp == NULL ||
        input_fd < 3 || output_fd < 3 || ready_fd < 3 || gate_fd < 3 || error_fd < 3 ||
        (inherited_count != 0 && inherited_fds == NULL))
        return -EINVAL;
    *pidfd = -1;

    int roles[] = {input_fd, output_fd, ready_fd, gate_fd, error_fd};
    for (size_t index = 0; index < sizeof(roles) / sizeof(roles[0]); ++index) {
        for (size_t prior = 0; prior < index; ++prior)
            if (roles[index] == roles[prior])
                return -EINVAL;
        for (size_t inherited = 0; inherited < inherited_count; ++inherited)
            if (roles[index] == inherited_fds[inherited])
                return -EINVAL;
    }
    for (size_t index = 0; index < inherited_count; ++index) {
        if (inherited_fds[index] < 3)
            return -EINVAL;
        for (size_t prior = 0; prior < index; ++prior)
            if (inherited_fds[index] == inherited_fds[prior])
                return -EINVAL;
    }

    sigset_t blocked, original;
    if (sigfillset(&blocked) < 0)
        return -errno;
    int mask_error = pthread_sigmask(SIG_SETMASK, &blocked, &original);
    if (mask_error != 0)
        return -mask_error;
    struct sigaction child_disposition;
    int disposition_error = 0;
    if (sigaction(SIGCHLD, NULL, &child_disposition) < 0)
        disposition_error = errno;
    else if (child_disposition.sa_handler != SIG_DFL || (child_disposition.sa_flags & SA_NOCLDWAIT))
        disposition_error = EINVAL;
    if (disposition_error != 0) {
        mask_error = pthread_sigmask(SIG_SETMASK, &original, NULL);
        return -(mask_error != 0 ? mask_error : disposition_error);
    }
    struct clone_args args = {0};
    int child_pidfd = -1;
    args.flags = CLONE_PIDFD;
    args.pidfd = (uint64_t)(uintptr_t)&child_pidfd;
    args.exit_signal = SIGCHLD;
    long child = syscall(SYS_clone3, &args, sizeof(args));
    if (child < 0) {
        int error = errno;
        mask_error = pthread_sigmask(SIG_SETMASK, &original, NULL);
        return -(mask_error != 0 ? mask_error : error);
    }
    if (child != 0) {
        *pidfd = child_pidfd;
        mask_error = pthread_sigmask(SIG_SETMASK, &original, NULL);
        return mask_error == 0 ? 0 : -mask_error;
    }

    if (setsid() < 0)
        child_failure(error_fd, LAUNCHER_SESSION, errno);
    if (dup2(input_fd, STDIN_FILENO) < 0 || dup2(output_fd, STDOUT_FILENO) < 0)
        child_failure(error_fd, LAUNCHER_DESCRIPTORS, errno);
    unsigned int first = 3;
    for (;;) {
        unsigned int next = UINT_MAX;
        int controls[] = {ready_fd, gate_fd, error_fd};
        for (size_t index = 0; index < sizeof(controls) / sizeof(controls[0]); ++index)
            if ((unsigned int)controls[index] >= first && (unsigned int)controls[index] < next)
                next = (unsigned int)controls[index];
        for (size_t index = 0; index < inherited_count; ++index)
            if ((unsigned int)inherited_fds[index] >= first && (unsigned int)inherited_fds[index] < next)
                next = (unsigned int)inherited_fds[index];
        if (next == UINT_MAX) {
            if (syscall(SYS_close_range, first, UINT_MAX, 0U) < 0)
                child_failure(error_fd, LAUNCHER_DESCRIPTORS, errno);
            break;
        }
        if (next > first && syscall(SYS_close_range, first, next - 1, 0U) < 0)
            child_failure(error_fd, LAUNCHER_DESCRIPTORS, errno);
        first = next + 1;
    }
    int controls[] = {ready_fd, gate_fd, error_fd};
    for (size_t index = 0; index < sizeof(controls) / sizeof(controls[0]); ++index) {
        int flags = fcntl(controls[index], F_GETFD);
        if (flags < 0 || fcntl(controls[index], F_SETFD, flags | FD_CLOEXEC) < 0)
            child_failure(error_fd, LAUNCHER_DESCRIPTORS, errno);
    }
    for (size_t index = 0; index < inherited_count; ++index) {
        int fd = inherited_fds[index];
        if (fd < 3 || fd == ready_fd || fd == gate_fd || fd == error_fd)
            child_failure(error_fd, LAUNCHER_DESCRIPTORS, EINVAL);
        int flags = fcntl(fd, F_GETFD);
        if (flags < 0 || fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) < 0)
            child_failure(error_fd, LAUNCHER_DESCRIPTORS, errno);
    }
    if (reset_child_signals() < 0 || sigdelset(&original, SIGINT) < 0 ||
        sigdelset(&original, SIGTERM) < 0 || sigdelset(&original, SIGCHLD) < 0 ||
        sigprocmask(SIG_SETMASK, &original, NULL) < 0)
        child_failure(error_fd, LAUNCHER_SIGNALS, errno);
    if (cwd != NULL && chdir(cwd) < 0)
        child_failure(error_fd, LAUNCHER_DIRECTORY, errno);
    char ready = 'R';
    if (write(ready_fd, &ready, 1) != 1)
        child_failure(error_fd, LAUNCHER_READY, errno);
    char release;
    ssize_t received;
    do {
        received = read(gate_fd, &release, 1);
    } while (received < 0 && errno == EINTR);
    if (received != 1 || release != 'G')
        child_failure(error_fd, LAUNCHER_GATE, received < 0 ? errno : ECANCELED);
    execve(argv[0], argv, envp);
    child_failure(error_fd, LAUNCHER_EXEC, errno);
}
