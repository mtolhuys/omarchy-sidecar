// omakit block: run 0.2.0
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Maarten Tolhuijs
// Source: omakit blocks/run/Run.qml, commit f41b8671d74dc4caa92b28a3dd56118d264be446
// Body sha256: 0e54a5a82aacbdcd2e61163091bbeebb597787abb285df86bf85d7d835451419
// end of omakit block header
//
// Run: starts one program for a plugin and always ends it. The program is
// started by run-supervisor.py, next to this file, through
// /usr/bin/python3 -I -S -B: absolute path, argv only, a closed environment,
// a hard deadline, byte and line caps while reading, TERM then grace then
// KILL to the whole process group, the leader reaped last, cancel on
// destruction and on supersession, one result object. The protocol between
// this file and the supervisor is a per-run token this file writes to the
// supervisor's stdin: every line the supervisor reports carries it, and a
// line without it, which is what the program can write into the same pipe,
// changes nothing. docs/BLOCKS.md is the contract and says which review
// comments each line answers.
//
//   Run {
//     id: catalog
//     command: [Quickshell.shellDir + "/catalog.sh", "--refresh"]
//     deadlineMs: 30000
//     onFinished: result => { if (result.state === "ok") parse(result.stdout) }
//   }
//   catalog.start()
import QtQuick
import Quickshell
import Quickshell.Io

QtObject {
    id: run

    // argv; command[0] an absolute path. A shell string (an interpreter with
    // its string flag, through a wrapper or not) is refused as spawn-failed
    // unless allowShellString; the forms it knows are in docs/BLOCKS.md.
    property list<string> command: []
    property bool allowShellString: false
    // Added to the base environment, never replacing it. The base is
    // PATH=/usr/bin, HOME, LANG=C.UTF-8 and XDG_RUNTIME_DIR; nothing else
    // of the shell's environment reaches the program.
    property var environment: ({})
    property int deadlineMs: 10000
    property int graceMs: 1000
    // Per stream, counted while reading; over either the run ends as overflow.
    property int maxBytes: 1048576
    property int maxLines: 10000
    // Per stream, what the result carries; the rest is counted and dropped.
    property int keepBytes: 65536

    readonly property bool running: _state === "running"
    // One object: state is one of ok, exit, timeout, overflow, cancelled,
    // spawn-failed, supervisor-lost, python-missing; stdout and stderr are
    // plain text of at most keepBytes each, control characters removed,
    // meant for Text.PlainText; outBytes, outLines, errBytes, errLines as
    // counted; exitCode (null when signalled), termSignal, ms, pgid,
    // survivors, and reason for the three failure states.
    signal finished(var result)

    readonly property string supervisor: decodeURIComponent(Qt.resolvedUrl("run-supervisor.py").toString().replace(/^file:\/\//, ""))
    readonly property var states: ["ok", "exit", "timeout", "overflow", "cancelled", "spawn-failed", "supervisor-lost", "python-missing"]
    property string _state: "idle"
    property bool _started: false
    property bool _pending: false
    property int _pgid: 0
    property double _t0: 0
    property var _result: null
    property string _supervisorErr: ""
    property string _token: ""
    property string _buffer: ""

    readonly property var _baseEnvironment: ({
        PATH: "/usr/bin",
        HOME: Quickshell.env("HOME"),
        LANG: "C.UTF-8",
        XDG_RUNTIME_DIR: Quickshell.env("XDG_RUNTIME_DIR")
    })

    function _now() { return Date.now() - _t0 }

    /** Start the program. A run already live is cancelled first, reports cancelled, and this start follows it. */
    function start() {
        if (_state === "running") { _pending = true; cancel(); return }
        if (_proc.running) { _pending = true; return }    // the last supervisor is still being reaped (a backstop); it follows on runningChanged
        _t0 = Date.now()
        _state = "running"
        _started = false
        _result = null
        _supervisorErr = ""
        _buffer = ""
        _pgid = 0
        _token = _newToken()
        _proc.command = _argv()
        _proc.environment = Object.assign({}, _baseEnvironment, environment)
        _proc.stdinEnabled = true
        _backstop.interval = deadlineMs + graceMs + 3000
        _backstop.start()
        _proc.running = true
    }

    /** End the run now: TERM to the group, the grace, KILL; the result comes back as cancelled. */
    function cancel() {
        if (_state === "running") _proc.signal(15)
    }

    // 128 bits from the engine's securely seeded generator; the program never
    // sees a value of it (stdin is consumed by the supervisor before the fork).
    function _newToken() {
        let token = ""
        for (let i = 0; i < 8; i += 1) token += ("000" + Math.floor(Math.random() * 65536).toString(16)).slice(-4)
        return token
    }

    function _argv() {
        const options = ["--deadline-ms", String(deadlineMs), "--grace-ms", String(graceMs),
            "--max-bytes", String(maxBytes), "--max-lines", String(maxLines), "--keep-bytes", String(keepBytes)]
        if (allowShellString) options.push("--allow-shell-string")
        return ["/usr/bin/python3", "-I", "-S", "-B", supervisor].concat(options, ["--"], command)
    }

    // The supervisor's stdout, delivered as it arrives: lines are cut here,
    // and the unfinished line is bounded, so a stream without a newline
    // cannot grow inside the shell process.
    function _chunk(text) {
        _buffer += text
        let end = _buffer.indexOf("\n")
        while (end >= 0) {
            _line(_buffer.slice(0, end))
            _buffer = _buffer.slice(end + 1)
            end = _buffer.indexOf("\n")
        }
        const bound = 6 * keepBytes + 65536
        if (_buffer.length > bound) _buffer = _buffer.slice(-bound)
    }

    // A protocol line carries the token; anything else on the pipe is the
    // program's and is dropped. The first leader and the first result count.
    function _line(line) {
        const at = _token ? line.indexOf(_token + " ") : -1
        if (at < 0) return
        let event
        try { event = JSON.parse(line.slice(at + _token.length + 1)) } catch (error) { return }
        if (event.ev === "leader") _leader(event)
        else if (event.ev === "result" && !_result) _result = _checked(event)
    }

    // The first leader line names the group; the acknowledgement releases the
    // supervisor's gate, and stdin closes behind it.
    function _leader(event) {
        if (_pgid !== 0 || _int(event.pgid) === 0) return
        _pgid = _int(event.pgid)
        _proc.write("go\n")
        _proc.stdinEnabled = false
    }

    function _int(value) { return Number.isInteger(value) && value >= 0 ? value : 0 }
    function _intOrNull(value) { return Number.isInteger(value) ? value : null }
    function _text(value, max) { return _plain(String(value == null ? "" : value)).slice(0, max) }

    /** The result as this file reports it: the closed set of states, the numbers as integers, the text stripped here as well. */
    function _checked(event) {
        if (!states.includes(event.state)) return null
        return {
            state: event.state, exitCode: _intOrNull(event.exitCode), termSignal: _intOrNull(event.termSignal),
            outBytes: _int(event.outBytes), outLines: _int(event.outLines), errBytes: _int(event.errBytes), errLines: _int(event.errLines),
            survivors: _int(event.survivors), stdout: _text(event.stdout, keepBytes), stderr: _text(event.stderr, keepBytes),
            reason: event.reason == null ? null : _text(event.reason, 4096),
            signals: Array.isArray(event.signals) ? event.signals.filter(s => s && typeof s.sig === "string").map(s => ({ sig: s.sig.slice(0, 8), atMs: _int(s.atMs), esrch: s.esrch === true })) : []
        }
    }

    function _plain(text) {
        return String(text).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, "")
    }

    function _lost(reason) {
        return { state: "supervisor-lost", reason: reason, stderr: _plain(_supervisorErr).slice(0, 4096) }
    }

    // The supervisor is gone without a valid result: whatever it started is
    // ended by the detached reaper, and the result says so.
    function _reap(reason) {
        if (_pgid !== 0) {
            Quickshell.execDetached(["/usr/bin/python3", "-I", "-S", "-B", supervisor, "--kill-group", String(_pgid), "--grace-ms", String(graceMs)])
            reason += "; the group " + _pgid + " was sent TERM and, after the grace, KILL by a detached reaper"
        }
        _finish(_lost(reason))
    }

    function _onExited(code, status) {
        if (_state !== "running") return
        _backstop.stop()
        if (_result) { _finish(_result); return }
        if (code === 2 && /can.t open file/.test(_supervisorErr)) { _reap("run-supervisor.py is not readable at " + supervisor); return }
        _reap("the supervisor " + (status === 1 ? "died on signal " : "exited ") + code + " without a result")
    }

    function _onRunningChanged() {
        // Never started: the interpreter itself could not be run (stock
        // Omarchy has it; docs/BLOCKS.md says why it is checked anyway).
        if (!_proc.running && _state === "running" && !_started) {
            _backstop.stop()
            _finish({ state: "python-missing", reason: "/usr/bin/python3 could not be started" })
        } else if (!_proc.running && _pending) { _pending = false; start() }
    }

    function _finish(result) {
        _state = "done"
        result.pgid = _pgid
        result.ms = _now()
        finished(result)
        if (_pending && !_proc.running) { _pending = false; start() }
    }

    property Timer _backstop: Timer {
        onTriggered: {
            if (run._pgid !== 0) Quickshell.execDetached(["/usr/bin/kill", "-s", "KILL", "--", "-" + run._pgid])
            run._proc.signal(9)
            run._finish(run._lost("no result " + run._backstop.interval + " ms after start; the group was sent KILL"))
        }
    }

    property Process _proc: Process {
        clearEnvironment: true
        stdinEnabled: true
        stdout: SplitParser { splitMarker: ""; onRead: text => run._chunk(text) }
        stderr: SplitParser { splitMarker: ""; onRead: text => { if (run._supervisorErr.length < 4096) run._supervisorErr += String(text).slice(0, 4096) } }
        onStarted: { run._started = true; run._proc.write(run._token + "\n") }
        onExited: (code, status) => run._onExited(code, status)
        onRunningChanged: run._onRunningChanged()
    }

    Component.onDestruction: {
        // The Process destructor SIGKILLs the supervisor right after this, so
        // the group is ended by a detached reaper: TERM, the grace, KILL. A
        // run whose leader this file never learned is still behind the
        // supervisor's gate and exits unrun when the supervisor dies.
        if (_state === "running" && _pgid !== 0) {
            Quickshell.execDetached(["/usr/bin/python3", "-I", "-S", "-B", supervisor, "--kill-group", String(_pgid), "--grace-ms", String(graceMs)])
        }
    }
}
