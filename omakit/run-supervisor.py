# omakit block: run 0.1.0
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Maarten Tolhuijs
# Source: omakit blocks/run/run-supervisor.py, commit eb95b39b196e61e592cb9b703600f2bc37ee381a
# Body sha256: 3a1c8e18c56592e046e1ac1dfe35df5745c39cc60405dfb6d408ee4eb287ef5f
# end of omakit block header
#
# The supervisor behind Run.qml. Started by Run.qml as
#   /usr/bin/python3 -I -S -B <this file> [options] -- /absolute/program args...
# and never by hand. It forks the program into a new session, watches it
# through a pidfd, reads stdout and stderr under byte and line caps, keeps
# an absolute deadline, then TERM to the whole group, a grace, KILL to the
# group, waits until the group is empty, and reaps the leader last, so the
# group number cannot be reused while it is signalled. One JSON line per
# event on its own stdout; the last line is the result. docs/BLOCKS.md is
# the contract; docs/BLOCKS_SPIKE.md is the measurement it came from.
#
#   --kill-group PGID --grace-ms N   the detached reaper Run.qml starts from
#                                    Component.onDestruction: TERM, grace,
#                                    KILL, nothing printed
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

# A shell or an interpreter whose -c turns the next argument into a program
# the argv does not show. Refused unless the caller says allowShellString.
INTERPRETERS = frozenset(["sh", "bash", "zsh", "dash", "fish", "ksh", "python", "python3", "perl", "ruby", "php", "lua", "node"])

DEFAULTS = {"deadline_ms": 10000, "grace_ms": 1000, "max_bytes": 1048576, "max_lines": 10000,
            "keep_bytes": 65536, "kill_group": 0, "allow_shell_string": 0}


class SpawnFailed(Exception):
    pass


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def plain(data, keep):
    """The first `keep` bytes as text with every control character removed."""
    return CONTROL.sub("", bytes(data[:keep]).decode("utf-8", "replace"))


def interpreter_name(path):
    return re.sub(r"[\d.]+$", "", os.path.basename(path))


def is_shell_string(cmd):
    """`bash -c`, `sh -lc`, `python3 -c`: an interpreter and a -c among its leading options."""
    if interpreter_name(cmd[0]) not in INTERPRETERS:
        return False
    for arg in cmd[1:]:
        if not arg.startswith("-"):
            return False
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
            return True
    return False


def refusal(cmd, allow_shell_string):
    """Why the command is not started, or None."""
    if not cmd:
        return "command is empty"
    if not os.path.isabs(cmd[0]):
        return "command[0] is not an absolute path: %s" % cmd[0]
    if not allow_shell_string and is_shell_string(cmd):
        return "command is a shell string (%s -c); pass argv, or set allowShellString: true" % interpreter_name(cmd[0])
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


def child_exec(cmd, devnull, w_out, w_err, e_w):
    """In the child: a new session, the three descriptors, nothing else open, then exec."""
    try:
        os.setsid()
        os.dup2(devnull, 0)
        os.dup2(w_out, 1)
        os.dup2(w_err, 2)
        os.closerange(3, e_w)
        os.closerange(e_w + 1, os.sysconf("SC_OPEN_MAX"))
        os.execv(cmd[0], cmd)
    except OSError as error:
        os.write(e_w, str(error.errno or 0).encode())
    os._exit(127)


def spawn(cmd):
    """Fork and exec; (pid, stdout fd, stderr fd), or SpawnFailed with the exec's errno text."""
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    e_r, e_w = os.pipe()          # close-on-exec: a successful exec closes it unwritten
    devnull = os.open(os.devnull, os.O_RDONLY)
    pid = os.fork()
    if pid == 0:
        child_exec(cmd, devnull, w_out, w_err, e_w)
    for fd in (w_out, w_err, e_w, devnull):
        os.close(fd)
    failure = os.read(e_r, 32)
    os.close(e_r)
    if failure:
        os.waitpid(pid, 0)
        os.close(r_out)
        os.close(r_err)
        raise SpawnFailed(os.strerror(int(failure)) if failure.isdigit() and int(failure) else "exec failed")
    for fd in (r_out, r_err):
        os.set_blocking(fd, False)
    return pid, r_out, r_err


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
        self.streams = {}
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        signal.set_wakeup_fd(self.wake_w, warn_on_full_buffer=False)
        signal.signal(signal.SIGTERM, lambda *_: None)
        signal.signal(signal.SIGINT, lambda *_: None)

    def now_ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def start(self):
        self.pid, r_out, r_err = spawn(self.cmd)
        self.pidfd = os.pidfd_open(self.pid)
        self.streams = {r_out: Stream(r_out, self.opts["keep_bytes"]), r_err: Stream(r_err, self.opts["keep_bytes"])}
        self.out, self.err = self.streams[r_out], self.streams[r_err]
        emit({"ev": "leader", "pid": self.pid, "pgid": self.pid, "atMs": self.now_ms()})

    def watch(self):
        fds = [fd for fd, stream in self.streams.items() if stream.open]
        fds.append(self.wake_r)
        if not self.leader_exited:
            fds.append(self.pidfd)
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
        """The run is over: TERM the group, unless nothing is left to signal."""
        if self.state != "running":
            return
        self.state, self.reason = "ending", reason
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

    def handle(self, fd):
        if fd == self.pidfd:
            self.leader_exit()
        elif fd == self.wake_r:
            os.read(self.wake_r, 4096)
            self.begin_end("cancelled")
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
        return {"ev": "result", "state": self.reason, "exitCode": self.exit_code, "termSignal": self.term_signal,
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
    try:
        run.start()
    except SpawnFailed as failure:
        emit({"ev": "result", "state": "spawn-failed", "reason": "%s: %s" % (cmd[0], failure), "ms": run.now_ms()})
        return 0
    while not run.done():
        run.step()
    run.finish()
    return 0


def main():
    opts, cmd = parse(sys.argv[1:])
    if opts["kill_group"]:
        return reap_group(opts["kill_group"], opts["grace_ms"])
    return supervise(opts, cmd)


if __name__ == "__main__":
    sys.exit(main())
