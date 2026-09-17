// omakit block: store 0.1.0
// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Maarten Tolhuijs
// Source: omakit blocks/store/Store.qml, commit eb95b39b196e61e592cb9b703600f2bc37ee381a
// Body sha256: c71b5646e0b6ccc4b45e5c57a8c7ed08a089319f4ee7d3ce74ef5e28d8af3801
// end of omakit block header
//
// Store: one private file for a plugin, read and written the way the
// review asks for. The file lives in the plugin's own 0700 directory under
// the XDG state or cache base; store-helper.py, next to this file and
// started through the Run block (Run.qml, the same directory), reaches it
// by a descriptor walk with O_NOFOLLOW at every step, checks owner, kind
// and mode on the descriptor after every open and never before, caps a
// read in bytes, parses it against a schema, and writes through an
// exclusive 0600 staging file renamed into place. docs/BLOCKS.md is the
// contract and says which review comments each line answers.
//
//   Store {
//     id: memory
//     pluginId: "io.github.me.plugin"
//     name: "memory.json"
//     schema: ({ type: "object", required: ["version"], properties: { version: { type: "integer" } } })
//     onFinished: result => { if (result.op === "read" && result.state === "ok") apply(result.value) }
//   }
//   memory.read(); memory.write({ version: 1, themes: {} }); memory.remove()
import QtQuick
import Quickshell

QtObject {
    id: store

    // The plugin's id, which names its private directory: letters, digits,
    // dots, dashes and underscores. The file name inside it, the same set.
    property string pluginId: ""
    property string name: ""
    // "state" for $XDG_STATE_HOME (~/.local/state), "cache" for
    // $XDG_CACHE_HOME (~/.cache); the base has to be inside HOME.
    property string kind: "state"
    // A read over this many bytes is overflow; a write is refused over it too.
    property int maxBytes: 1048576
    // The schema subset a read and a write are checked against: type,
    // properties, required, additionalProperties: false, items, enum,
    // maxLength, maxItems, maxProperties, minimum, maximum, pattern. Null
    // checks nothing but that the file is JSON.
    property var schema: null

    readonly property bool busy: _queue.length > 0 || _helper.running
    // One object per operation: op is read, write or remove; state is one
    // of ok, missing, invalid, refused, overflow, failed, helper-failed;
    // value (a read), bytes, mtime, path, and reason for every state that
    // is not ok or missing.
    signal finished(var result)

    // A write goes to the helper as one argument; the kernel bounds one
    // argument at 128 KiB, so a write is refused above this before anything
    // starts. A cache a helper of the plugin downloads itself uses the
    // helper's own transaction; Store is for the plugin's state.
    readonly property int maxWriteBytes: 65536
    readonly property string helper: decodeURIComponent(Qt.resolvedUrl("store-helper.py").toString().replace(/^file:\/\//, ""))
    property var _queue: []
    property var _current: null

    readonly property var _environment: {
        const environment = {}
        for (const name of ["XDG_STATE_HOME", "XDG_CACHE_HOME"]) {
            const value = Quickshell.env(name)
            if (value) environment[name] = value
        }
        return environment
    }

    function read() { _enqueue({ op: "read" }) }
    function remove() { _enqueue({ op: "remove" }) }

    function write(value) {
        let text
        try { text = JSON.stringify(value) } catch (error) { _report({ op: "write", state: "invalid", reason: "the value is not JSON: " + error }); return }
        if (typeof text !== "string") { _report({ op: "write", state: "invalid", reason: "the value is undefined" }); return }
        if (text.length > maxWriteBytes) { _report({ op: "write", state: "overflow", reason: "the value is " + text.length + " characters, over " + maxWriteBytes }); return }
        _enqueue({ op: "write", value: text })
    }

    function _enqueue(operation) {
        _queue = _queue.concat([operation])
        _next()
    }

    function _next() {
        if (_helper.running || _queue.length === 0) return
        _current = _queue[0]
        _queue = _queue.slice(1)
        _helper.command = _argv(_current)
        _helper.start()
    }

    function _argv(operation) {
        const argv = ["/usr/bin/python3", "-I", "-S", "-B", helper, operation.op, "--kind", kind, "--plugin", pluginId, "--name", name, "--max-bytes", String(maxBytes)]
        if (schema) argv.push("--schema", JSON.stringify(schema))
        if (operation.op === "write") argv.push("--value", operation.value)
        return argv
    }

    function _report(result) {
        finished(result)
    }

    function _parsed(run) {
        if (run.state !== "ok") return null
        try {
            const result = JSON.parse(String(run.stdout || "").trim().split("\n").pop())
            return result && result.ev === "result" ? result : null
        } catch (error) {
            return null
        }
    }

    function _lost(op, run) {
        const detail = (run.reason ? ": " + run.reason : "") + (run.stderr ? "; " + String(run.stderr).slice(0, 512) : "")
        return { op: op, state: "helper-failed", reason: "the helper ended as " + run.state + detail }
    }

    function _onHelper(run) {
        const operation = _current || { op: "unknown" }
        _current = null
        const result = _parsed(run) || _lost(operation.op, run)
        delete result.ev
        _report(result)
        _next()
    }

    // The helper: a descriptor walk, one read or write, a few fsyncs: 10 s
    // covers a slow disk; its one line carries the value, so the cap is the
    // file cap plus the line around it.
    property Run _helper: Run {
        environment: store._environment
        deadlineMs: 10000
        maxBytes: store.maxBytes + 65536
        keepBytes: store.maxBytes + 65536
        onFinished: function(result) { store._onHelper(result) }
    }
}
