# omakit block: run 0.2.0
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Maarten Tolhuijs
# Source: omakit blocks/run/run-supervisor.py, commit f41b8671d74dc4caa92b28a3dd56118d264be446
# Body sha256: 07c3d5fa103fcbfc40eb081206fdd8571c6e3852a188389d79bbac12db00170d
# end of omakit block header
#
# The supervisor behind Run.qml. Started by Run.qml as
#   /usr/bin/python3 -I -S -B <this file> [options] -- /absolute/program args...
# and never by hand. It reads a one-line token from stdin, forks the
# program into a new session behind a gate, announces the leader, releases
# the gate on Run.qml's acknowledgement, watches the leader through a pidfd,
# reads stdout and stderr under byte and line caps, keeps an absolute
# deadline, then TERM to the whole group, a grace, KILL to the group, waits
# until the group is empty, and reaps the leader last, so the group number
# cannot be reused while it is signalled. Every protocol line on its own
# stdout starts with the token, which the program never sees (not argv, not
# the environment, consumed from stdin before the fork), so a line the
# program writes into this pipe through /proc carries no token and is
# dropped by Run.qml. docs/BLOCKS.md is the contract.
#
#   --kill-group PGID --grace-ms N   the detached reaper Run.qml starts from
#                                    Component.onDestruction and after a
#                                    supervisor that died without a result:
#                                    TERM, grace, KILL, nothing printed
import json
import os
import re
import select
import signal
import sys
import time

# C0 except tab and newline, DEL, C1, and the bidirectional controls that
# reorder displayed text: removed from the result's text, which is meant
# for Text.PlainText and nothing else.
CONTROL = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f\\u061c\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")

# A shell or an interpreter whose string flag turns the next argument into a
# program the argv does not show: the flag's letters, its long forms, and
# the options that consume the next argument (so `-o pipefail -c` is seen
# through and `-m json.tool` is not a string); and the wrappers that hand
# argv on to another command, with the options that take a value and the
# positionals before the command. Best effort over the forms listed here,
# not a sandbox: refused unless allowShellString.
SHELLS = frozenset(["sh", "bash", "dash", "zsh", "ksh", "fish", "rbash", "ash", "mksh", "busybox"])
SHELL_VALUE_OPTIONS = frozenset(["-o", "+o", "-O", "+O", "--rcfile", "--init-file", "-C", "-d", "--debug-level"])
INTERPRETERS = {
    "python": ("c", frozenset(["-W", "-X", "--check-hash-based-pycs"])),
    "perl": ("eE", frozenset(["-I", "-M", "-m", "-F", "-C", "-x"])),
    "ruby": ("e", frozenset(["-r", "-I", "-E", "-C", "-F", "-x"])),
    "php": ("r", frozenset(["-c", "-d", "-f", "-z", "-B", "-R", "-F"])),
    "lua": ("e", frozenset(["-l"])),
    "node": ("ep", frozenset(["-r", "--require", "--import", "--loader", "--experimental-loader", "-C", "--conditions"])),
}
LONG_STRING_FLAGS = frozenset(["--eval", "--print", "--run"])
WRAPPERS = frozenset(["env", "nice", "timeout", "setsid", "flock", "xargs", "sudo", "doas", "nohup", "stdbuf", "ionice", "chrt", "unbuffer", "busybox"])
WRAPPER_VALUE_OPTIONS = frozenset(["-u", "-C", "-S", "-n", "-s", "-k", "-w", "-E", "-L", "-P", "-a", "-d", "-I", "-i", "-o", "-e", "-p", "-g", "-D", "-h", "-r", "-t", "-U", "-T", "--adjustment", "--signal", "--kill-after", "--timeout", "--conflict-exit-code", "--input", "--output", "--error", "--max-args", "--max-lines", "--max-procs", "--delimiter", "--arg-file", "--replace", "--user", "--group", "--chdir", "--split-string", "--unset", "--class", "--classdata", "--pid"])
POSITIONAL_BEFORE_COMMAND = {"timeout": 1, "flock": 1}

DEFAULTS = {"deadline_ms": 10000, "grace_ms": 1000, "max_bytes": 1048576, "max_lines": 10000,
            "keep_bytes": 65536, "kill_group": 0, "allow_shell_string": 0}
TOKEN = re.compile(r"^[0-9a-f]{32}$")
token = ""


def emit(obj):
    sys.stdout.write(token + " " + json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def plain(data, keep):
    """The first `keep` bytes as text with every control character removed."""
    return CONTROL.sub("", bytes(data[:keep]).decode("utf-8", "replace"))


def read_token():
    """The per-run token Run.qml writes as the first line of stdin, read a byte at a time so the acknowledgement behind it stays in the pipe; the program never sees it."""
    line = b""
    while not line.endswith(b"\n") and len(line) < 64:
        byte = os.read(0, 1)
        if not byte:
            break
        line += byte
    text = line.decode("ascii", "replace").strip()
    if not TOKEN.match(text):
        raise SystemExit("run-supervisor: no token on stdin; started by Run.qml only")
    return text


def interpreter_name(path):
    return re.sub(r"[\d.]+$", "", os.path.basename(path))


def string_flags(name):
    """(letters, value-taking options) of an interpreter, or None when the name is no interpreter."""
    if name in SHELLS:
        return "c", SHELL_VALUE_OPTIONS
    return INTERPRETERS.get(name)


def flock_command(args):
    """`flock [options] file -c string`: the string flag comes after the lock file, so it is looked for anywhere before `--`."""
    for arg in args:
        if arg == "--":
            return False
        if arg == "--command" or (arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]):
            return True
    return False


def has_string_flag(args, letters, value_options):
    """A string flag among the leading options: clustered (-lc), alone, or a long form; a value-taking option's value is skipped."""
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == "--" or not arg.startswith(("-", "+")) or arg in ("-", "+"):
            return False
        if arg in LONG_STRING_FLAGS or (not arg.startswith("--") and any(letter in arg[1:] for letter in letters)):
            return True
        skip = arg in value_options
    return False


def unwrap(cmd):
    """The command a wrapper hands on: past the wrapper's options, NAME=value words and its leading positionals."""
    name = interpreter_name(cmd[0])
    rest = cmd[1:]
    positionals = POSITIONAL_BEFORE_COMMAND.get(name, 0)
    while rest and (rest[0].startswith("-") or (name == "env" and "=" in rest[0])):
        skip = 2 if rest[0] in WRAPPER_VALUE_OPTIONS else 1
        rest = rest[skip:]
    return rest[positionals:]


def is_shell_string(cmd):
    """`bash -c`, `sh -o pipefail -c`, `perl -e`, `node --eval`, `env bash -c`, `flock -c`: an interpreter and its string flag among the leading options, through up to eight wrappers."""
    for _depth in range(8):
        if not cmd:
            return False
        name = interpreter_name(cmd[0])
        flags = string_flags(name)
        if flags is not None and has_string_flag(cmd[1:], flags[0], flags[1]):
            return True
        if name == "flock" and flock_command(cmd[1:]):
            return True
        if name not in WRAPPERS:
            return False
        cmd = unwrap(cmd)
    return False


def refusal(cmd, allow_shell_string):
    """Why the command is not started, or None."""
    if not cmd:
        return "command is empty"
    if not os.path.isabs(cmd[0]):
        return "command[0] is not an absolute path: %s" % cmd[0]
    if not allow_shell_string and is_shell_string(cmd):
        return "command is a shell string (an interpreter with its string flag, %s); pass argv, or set allowShellString: true" % interpreter_name(cmd[0])
    return None


def parse(argv):
    """The options before `--` as a dict of integers, and the command after it."""
    opts = dict(DEFAULTS)
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            return opts, argv[index + 1:]
        key = arg[2:].replace("-", "_")
        if not arg.startswith("--") or key not in opts:
            raise SystemExit("run-supervisor: unknown option %s" % arg)
        if key == "allow_shell_string":
            opts[key] = 1
            index += 1
            continue
        if index + 1 >= len(argv) or not argv[index + 1].isdigit():
            raise SystemExit("run-supervisor: %s needs an integer" % arg)
        opts[key] = int(argv[index + 1])
        index += 2
    return opts, []


def pgrp_and_state(pid):
    """(pgrp, state) of a process from /proc, or None when it is gone."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            stat = handle.read()
    except OSError:
        return None
    rest = stat[stat.rfind(b")") + 2:].split()
    if len(rest) < 3:
        return None
    return int(rest[2]), rest[0]


def group_members(pgid, leader):
    """The live pids in the group other than the leader; a zombie is not live."""
    live = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == leader:
            continue
        found = pgrp_and_state(int(name))
        if found and found[0] == pgid and found[1] != b"Z":
            live.append(int(name))
    return live


def killpg(pgid, sig):
    """Signal the group; True when the group no longer exists."""
    try:
        os.killpg(pgid, sig)
        return False
    except ProcessLookupError:
        return True


def wait_empty(pgid, leader, budget_s):
    """Poll until the group has no live member or the budget is spent; the live members left."""
    end = time.monotonic() + budget_s
    while True:
        live = group_members(pgid, leader)
        if not live or time.monotonic() >= end:
            return live
        time.sleep(0.02)


def reap_group(pgid, grace_ms):
    """The detached reaper: TERM, grace while polling, KILL, one more wait."""
    if killpg(pgid, signal.SIGTERM):
        return 0
    if not wait_empty(pgid, 0, grace_ms / 1000.0):
        return 0
    killpg(pgid, signal.SIGKILL)
    wait_empty(pgid, 0, 1.0)
    return 0


def close_all_but(keep):
    """Every descriptor from 3 up except the ones kept."""
    low = 3
    for fd in sorted(keep):
        os.closerange(low, fd)
        low = fd + 1
    os.closerange(low, os.sysconf("SC_OPEN_MAX"))


def child_exec(cmd, fds):
    """In the child: a new session, the three descriptors, nothing else open, the gate, then exec."""
    try:
        os.setsid()
        os.dup2(fds["devnull"], 0)
        os.dup2(fds["w_out"], 1)
        os.dup2(fds["w_err"], 2)
        close_all_but([fds["e_w"], fds["g_r"]])
        if os.read(fds["g_r"], 1) != b"1":
            os._exit(0)           # the gate closed unreleased: Run.qml went away before it knew this pid
        os.close(fds["g_r"])
        os.execv(cmd[0], cmd)
    except OSError as error:
        os.write(fds["e_w"], str(error.errno or 0).encode())
    os._exit(127)


def spawn(cmd):
    """Fork behind a gate; (pid, stdout fd, stderr fd, errno fd, gate fd). Exec waits for release()."""
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    e_r, e_w = os.pipe()          # close-on-exec: a successful exec closes it unwritten
    g_r, g_w = os.pipe()          # the gate: one byte releases the exec; EOF ends the child unrun
    devnull = os.open(os.devnull, os.O_RDONLY)
    pid = os.fork()
    if pid == 0:
        child_exec(cmd, {"devnull": devnull, "w_out": w_out, "w_err": w_err, "e_w": e_w, "g_r": g_r})
    for fd in (w_out, w_err, e_w, g_r, devnull):
        os.close(fd)
    for fd in (r_out, r_err, e_r):
        os.set_blocking(fd, False)
    return pid, r_out, r_err, e_r, g_w


class Stream:
    """One pipe: counted in full, kept up to the cap."""

    def __init__(self, fd, keep):
        self.fd = fd
        self.keep = keep
        self.open = True
        self.bytes = 0
        self.lines = 0
        self.kept = bytearray()

    def read(self):
        """One read; False at EOF or when nothing is there."""
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return False
        except OSError:
            data = b""
        if not data:
            self.open = False
            return False
        self.bytes += len(data)
        self.lines += data.count(b"\n")
        room = self.keep - len(self.kept)
        if room > 0:
            self.kept += data[:room]
        return True

    def over(self, max_bytes, max_lines):
        return self.bytes > max_bytes or self.lines > max_lines


class Supervisor:
    def __init__(self, opts, cmd):
        self.opts = opts
        self.cmd = cmd
        self.t0 = time.monotonic()
        self.deadline = self.t0 + opts["deadline_ms"] / 1000.0
        self.state = "running"
        self.reason = None
        self.exit_code = None
        self.term_signal = None
        self.leader_exited = False
        self.next_kill = None
        self.kill_sent = False
        self.signals = []
        self.pid = 0
        self.pidfd = -1
        self.errno_fd = -1
        self.gate_fd = -1
        self.spawn_error = None
        self.streams = {}
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        signal.set_wakeup_fd(self.wake_w, warn_on_full_buffer=False)
        signal.signal(signal.SIGTERM, lambda *_: None)
        signal.signal(signal.SIGINT, lambda *_: None)

    def now_ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def start(self):
        self.pid, r_out, r_err, self.errno_fd, self.gate_fd = spawn(self.cmd)
        self.pidfd = os.pidfd_open(self.pid)
        self.streams = {r_out: Stream(r_out, self.opts["keep_bytes"]), r_err: Stream(r_err, self.opts["keep_bytes"])}
        self.out, self.err = self.streams[r_out], self.streams[r_err]
        emit({"ev": "leader", "pid": self.pid, "pgid": self.pid, "atMs": self.now_ms()})

    def release(self, run):
        """Open or close the gate: the child execs on a byte, exits unrun on EOF."""
        if self.gate_fd < 0:
            return
        if run:
            os.write(self.gate_fd, b"1")
        os.close(self.gate_fd)
        self.gate_fd = -1

    def watch(self):
        fds = [fd for fd, stream in self.streams.items() if stream.open]
        fds.append(self.wake_r)
        if not self.leader_exited:
            fds.append(self.pidfd)
        if self.errno_fd >= 0:
            fds.append(self.errno_fd)
        if self.gate_fd >= 0:
            fds.append(0)
        return fds

    def timeout(self):
        now = time.monotonic()
        if self.state == "running":
            return max(0.0, self.deadline - now)
        if self.next_kill is not None and not self.kill_sent:
            return max(0.0, self.next_kill - now)
        return 0.02

    def send(self, sig, name):
        gone = killpg(self.pid, sig)
        self.signals.append({"sig": name, "atMs": self.now_ms(), "esrch": gone})
        emit({"ev": "signal", "sig": name, "atMs": self.now_ms(), "esrch": gone})
        if name == "TERM":
            self.next_kill = time.monotonic() + self.opts["grace_ms"] / 1000.0
        else:
            self.kill_sent = True

    def begin_end(self, reason):
        """The run is over: close the gate if it is still shut, TERM the group unless nothing is left to signal."""
        if self.state != "running":
            return
        self.state, self.reason = "ending", reason
        self.release(False)
        if self.leader_exited and not group_members(self.pid, self.pid):
            return
        self.send(signal.SIGTERM, "TERM")

    def leader_exit(self):
        """The leader is gone; its status is read without reaping it."""
        info = os.waitid(os.P_PIDFD, self.pidfd, os.WEXITED | os.WNOWAIT)
        self.leader_exited = True
        if info.si_code == os.CLD_EXITED:
            self.exit_code = info.si_status
        else:
            self.term_signal = info.si_status
        emit({"ev": "leader-exited", "code": self.exit_code, "signal": self.term_signal, "atMs": self.now_ms()})
        self.begin_end("ok" if self.exit_code == 0 else "exit")

    def exec_outcome(self):
        """The errno pipe: bytes mean the exec failed; EOF means it succeeded."""
        try:
            data = os.read(self.errno_fd, 32)
        except BlockingIOError:
            return
        os.close(self.errno_fd)
        self.errno_fd = -1
        if data:
            self.spawn_error = os.strerror(int(data)) if data.isdigit() and int(data) else "exec failed"
            self.state, self.reason = "ending", "spawn-failed"   # the child exited 127 on its own; nothing to signal

    def acknowledged(self):
        """Run.qml's `go` on stdin releases the gate; EOF before it means Run.qml is gone."""
        line = os.read(0, 16)
        if line.strip() == b"go":
            self.release(True)
        else:
            self.begin_end("cancelled")

    def handle(self, fd):
        if fd == self.pidfd:
            self.leader_exit()
        elif fd == self.wake_r:
            os.read(self.wake_r, 4096)
            self.begin_end("cancelled")
        elif fd == self.errno_fd:
            self.exec_outcome()
        elif fd == 0:
            self.acknowledged()
        elif self.streams[fd].read() and self.streams[fd].over(self.opts["max_bytes"], self.opts["max_lines"]):
            self.begin_end("overflow")

    def clock(self):
        """The deadline and the grace, checked after every select."""
        now = time.monotonic()
        if self.state == "running" and now >= self.deadline:
            self.begin_end("timeout")
        if self.state != "ending" or self.next_kill is None or self.kill_sent or now < self.next_kill:
            return
        if group_members(self.pid, self.pid) or not self.leader_exited:
            self.send(signal.SIGKILL, "KILL")
        else:
            self.kill_sent = True     # the group emptied during the grace; no KILL needed

    def done(self):
        if self.state != "ending" or not self.leader_exited:
            return False
        if not group_members(self.pid, self.pid):
            return True
        return self.kill_sent and self.now_ms() > self.opts["deadline_ms"] + self.opts["grace_ms"] + 2000

    def step(self):
        fds = self.watch()
        ready = select.select(fds, [], [], self.timeout())[0]
        for fd in ready:
            self.handle(fd)
        self.clock()

    def drain(self):
        """What is still buffered in the pipes after the group is gone, without blocking."""
        for stream in self.streams.values():
            while stream.open and stream.read():
                pass

    def finish(self):
        """The group is empty or given up on: drain, count, reap the leader last, report."""
        self.drain()
        survivors = group_members(self.pid, self.pid)
        os.waitpid(self.pid, 0)
        os.close(self.pidfd)
        emit(self.result(survivors))

    def result(self, survivors):
        spawn_failed = self.reason == "spawn-failed"
        return {"ev": "result", "state": self.reason, "exitCode": None if spawn_failed else self.exit_code, "termSignal": self.term_signal,
                "reason": "%s: %s" % (self.cmd[0], self.spawn_error) if spawn_failed else None,
                "ms": self.now_ms(), "pgid": self.pid, "survivors": len(survivors),
                "outBytes": self.out.bytes, "outLines": self.out.lines, "errBytes": self.err.bytes, "errLines": self.err.lines,
                "stdout": plain(self.out.kept, self.opts["keep_bytes"]), "stderr": plain(self.err.kept, self.opts["keep_bytes"]),
                "signals": self.signals}


def supervise(opts, cmd):
    why = refusal(cmd, opts["allow_shell_string"])
    if why:
        emit({"ev": "result", "state": "spawn-failed", "reason": why, "ms": 0})
        return 0
    run = Supervisor(opts, cmd)
    run.start()
    while not run.done():
        run.step()
    run.finish()
    return 0


def main():
    global token
    opts, cmd = parse(sys.argv[1:])
    if opts["kill_group"]:
        return reap_group(opts["kill_group"], opts["grace_ms"])
    token = read_token()
    return supervise(opts, cmd)


if __name__ == "__main__":
    sys.exit(main())
