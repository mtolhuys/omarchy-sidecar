// omakit block: run 0.1.0
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Maarten Tolhuijs
// Source: omakit blocks/run/Run.qml, commit eb95b39b196e61e592cb9b703600f2bc37ee381a
// Body sha256: 7055007d082b81f328b8c3e7f41e648ed27c111ef546220277f0fb7f18332e28
// end of omakit block header
//
// Run: starts one program for a plugin and always ends it. The program is
// started by run-supervisor.py, next to this file, through
// /usr/bin/python3 -I -S -B: absolute path, argv only, a closed environment,
// a hard deadline, byte and line caps while reading, TERM then grace then
// KILL to the whole process group, the leader reaped last, cancel on
// destruction and on supersession, one result object. docs/BLOCKS.md is
// the contract and says which review comments each line answers.
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

    // argv; command[0] an absolute path. A shell string (`bash -c`, or any
    // interpreter with -c) is refused as spawn-failed unless allowShellString.
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
    property string _state: "idle"
    property bool _started: false
    property bool _pending: false
    property int _pgid: 0
    property double _t0: 0
    property var _result: null
    property string _supervisorErr: ""

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
        _t0 = Date.now()
        _state = "running"
        _started = false
        _result = null
        _supervisorErr = ""
        _pgid = 0
        _proc.command = _argv()
        _proc.environment = Object.assign({}, _baseEnvironment, environment)
        _backstop.interval = deadlineMs + graceMs + 3000
        _backstop.start()
        _proc.running = true
    }

    /** End the run now: TERM to the group, the grace, KILL; the result comes back as cancelled. */
    function cancel() {
        if (_state === "running") _proc.signal(15)
    }

    function _argv() {
        const options = ["--deadline-ms", String(deadlineMs), "--grace-ms", String(graceMs),
            "--max-bytes", String(maxBytes), "--max-lines", String(maxLines), "--keep-bytes", String(keepBytes)]
        if (allowShellString) options.push("--allow-shell-string")
        return ["/usr/bin/python3", "-I", "-S", "-B", supervisor].concat(options, ["--"], command)
    }

    function _line(line) {
        let event
        try { event = JSON.parse(line) } catch (error) { return }
        if (event.ev === "leader") _pgid = event.pgid
        if (event.ev === "result") _result = event
    }

    function _plain(text) {
        return String(text).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, "")
    }

    function _lost(reason) {
        return { state: "supervisor-lost", reason: reason, stderr: _plain(_supervisorErr).slice(0, 4096) }
    }

    function _onExited(code) {
        if (_state !== "running") return
        _backstop.stop()
        if (_result) { _finish(_result); return }
        if (code === 2 && /can't open file/.test(_supervisorErr)) { _finish(_lost("run-supervisor.py is not readable at " + supervisor)); return }
        _finish(_lost("the supervisor exited " + code + " without a result"))
    }

    function _onRunningChanged() {
        // Never started: the interpreter itself could not be run (stock
        // Omarchy has it; docs/BLOCKS.md says why it is checked anyway).
        if (!_proc.running && _state === "running" && !_started) {
            _backstop.stop()
            _finish({ state: "python-missing", reason: "/usr/bin/python3 could not be started" })
        }
    }

    function _finish(result) {
        _state = "done"
        result.pgid = result.pgid || _pgid
        result.ms = _now()
        finished(result)
        if (_pending) { _pending = false; start() }
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
        stdinEnabled: false
        stdout: SplitParser { splitMarker: "\n"; onRead: line => run._line(line) }
        stderr: SplitParser { splitMarker: "\n"; onRead: line => { if (run._supervisorErr.length < 4096) run._supervisorErr += line + "\n" } }
        onStarted: run._started = true
        onExited: (code, status) => run._onExited(code)
        onRunningChanged: run._onRunningChanged()
    }

    Component.onDestruction: {
        // The Process destructor SIGKILLs the supervisor right after this, so
        // the group is ended by a detached reaper: TERM, the grace, KILL.
        if (_state === "running" && _pgid !== 0) {
            Quickshell.execDetached(["/usr/bin/python3", "-I", "-S", "-B", supervisor, "--kill-group", String(_pgid), "--grace-ms", String(graceMs)])
        }
    }
}
