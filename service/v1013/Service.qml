import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string buildIdentity: "sidecar-service-v1013"

  // The plugin's own directory comes from this component's URL, not from
  // the manifest: the shell of Omarchy 4.0.3 strips `__sourceDir` from a
  // third-party plugin's manifest (publicPluginManifest in shell.qml), so
  // the earlier root read from it was "" on every stock install and the
  // helper path became /helper/sidecarctl. This file is loaded from
  // <plugin>/service/v1013/Service.qml wherever the plugin is installed,
  // so two levels up is the plugin root. A manifest `__sourceDir` is the
  // fallback, only when it is an absolute path. Each candidate has to
  // prove itself before a process is started from it: manifestProbe reads
  // <root>/manifest.json there, and only a root where that loads becomes
  // pluginRoot. No candidate ever yields a relative or bare command.
  readonly property string componentRoot: localPath(Qt.resolvedUrl("../../"))
  readonly property string manifestRoot: manifest && typeof manifest.__sourceDir === "string"
    && manifest.__sourceDir.indexOf("/") === 0
    ? String(manifest.__sourceDir).replace(/\/+$/, "") : ""
  readonly property var rootCandidates: [componentRoot, manifestRoot].filter(function(root, index, all) {
    return root !== "" && all.indexOf(root) === index
  })
  property int probeIndex: 0
  property string pluginRoot: ""
  readonly property string controlPath: pluginRoot === "" ? "" : pluginRoot + "/helper/sidecarctl"

  function localPath(url) {
    var value = String(url || "")
    if (value.indexOf("file://") !== 0) return ""
    value = value.substring(7)
    try {
      value = decodeURIComponent(value)
    } catch (error) {
      return ""
    }
    value = value.replace(/\/+$/, "")
    return value.indexOf("/") === 0 ? value : ""
  }

  function probeNextRoot() {
    if (probeIndex >= rootCandidates.length) {
      helperState = "unavailable"
      helperError = "Sidecar could not find its own files beside the service. Reinstall the plugin."
      return
    }
    var candidate = rootCandidates[probeIndex] + "/manifest.json"
    if (manifestProbe.path === candidate) manifestProbe.reload()
    else manifestProbe.path = candidate
  }

  // The shell assigns `manifest` after the component exists. A probe in
  // flight reads the candidates again when it moves on, so a fallback that
  // arrives during it is tried in turn; one that arrives after every
  // candidate failed starts the probe over.
  onRootCandidatesChanged: {
    if (pluginRoot !== "" || helperState !== "unavailable") return
    probeIndex = 0
    probeNextRoot()
  }

  FileView {
    id: manifestProbe
    path: ""
    printErrors: false
    onLoaded: {
      root.pluginRoot = root.rootCandidates[root.probeIndex]
      root.startHelper()
    }
    onLoadFailed: function(error) {
      // Measured: a path assigned from inside this handler is dropped by
      // the FileView, so the next candidate is probed after it returns.
      root.probeIndex += 1
      Qt.callLater(root.probeNextRoot)
    }
  }

  property bool helperWanted: true
  property string helperState: "starting"
  property string helperError: ""
  property var status: ({
    state: "starting", pending: [], devices: [], route: { state: "starting" }
  })
  property var controlQueue: []
  property int restartCount: 0
  property double restartWindowStarted: 0
  readonly property bool installMenuOpening: installMenuProcess.running
  readonly property bool tailscalePanelOpening: tailscalePanelProcess.running
  readonly property bool httpsSettingsOpening: httpsSettingsProcess.running

  function startHelper() {
    if (!helperWanted || helperProcess.running || pluginRoot === "") return
    helperState = "starting"
    helperError = ""
    helperProcess.command = [
      pluginRoot + "/helper/sidecard",
      "--plugin-root", pluginRoot,
      "--service-build-id", buildIdentity
    ]
    helperProcess.running = true
  }

  function noteExit(exitCode) {
    if (!helperWanted) {
      helperState = "stopped"
      return
    }
    var now = Date.now()
    if (now - restartWindowStarted > 60000) {
      restartWindowStarted = now
      restartCount = 0
    }
    restartCount += 1
    if (restartCount > 3) {
      helperState = "crash-loop"
      helperError = "The Sidecar helper stopped repeatedly. Disable and re-enable the plugin after checking diagnostics."
      return
    }
    helperState = "restarting"
    helperError = "The Sidecar helper stopped unexpectedly (exit " + exitCode + "). Restarting…"
    restartTimer.restart()
  }

  function refresh() {
    if (!helperWanted || controlPath === "" || statusProcess.running || controlProcess.running) return
    statusProcess.command = [controlPath, "status"]
    statusProcess.running = true
  }

  function enqueue(arguments) {
    if (controlQueue.length >= 16) {
      helperError = "Sidecar is still finishing earlier requests. Wait a moment and try again."
      return
    }
    helperError = ""
    var next = controlQueue.slice(0)
    next.push(arguments)
    controlQueue = next
    runNextControl()
  }

  function runNextControl() {
    if (controlPath === "" || controlProcess.running || controlQueue.length === 0) return
    var next = controlQueue.slice(0)
    var arguments = next.shift()
    controlQueue = next
    controlProcess.command = [controlPath].concat(arguments)
    controlProcess.running = true
  }

  function pairOpen() { enqueue(["pair-open"]) }
  function pairCancel() { enqueue(["pair-cancel"]) }
  function pairApprove(requestId) {
    enqueue(["pair-approve", String(requestId), "--scopes",
      "read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock"])
  }
  function pairDeny(requestId) { enqueue(["pair-deny", String(requestId)]) }
  function capabilityApprove(requestId) { enqueue(["capability-approve", String(requestId)]) }
  function capabilityDeny(requestId) { enqueue(["capability-deny", String(requestId)]) }
  function pause(value) { enqueue(["pause", value ? "on" : "off"]) }
  function renameDevice(deviceId, name) {
    var trimmed = String(name || "").trim()
    if (trimmed.length > 0 && trimmed.length <= 64)
      enqueue(["device-rename", String(deviceId), trimmed])
  }
  function setReadOnly(deviceId) {
    enqueue(["device-rescope", String(deviceId), "--scopes", "read:desktop"])
  }
  function setFullControls(deviceId, keepInbox) {
    enqueue(["device-rescope", String(deviceId), "--scopes",
      "read:desktop,control:workspace,control:window-focus,control:window-move,control:media,control:theme,control:lock" + (keepInbox ? ",write:inbox" : "")])
  }
  function revokeDevice(deviceId) { enqueue(["device-revoke", String(deviceId)]) }
  function retryTailscale() { enqueue(["retry"]) }
  function openInbox() { enqueue(["inbox-open"]) }
  function openTailscaleInstallMenu() {
    if (installMenuProcess.running) return
    helperError = ""
    installMenuProcess.command = ["omarchy-menu", "summon", "install.service"]
    installMenuProcess.running = true
  }
  function openTailscalePanel() {
    if (tailscalePanelProcess.running) return
    helperError = ""
    tailscalePanelProcess.command = ["omarchy-shell", "omarchy.tailscale", "open"]
    tailscalePanelProcess.running = true
  }
  function openTailscaleHttpsSettings() {
    if (httpsSettingsProcess.running) return
    helperError = ""
    httpsSettingsProcess.command = ["omarchy-launch-browser", "https://login.tailscale.com/admin/dns"]
    httpsSettingsProcess.running = true
  }
  Process {
    id: helperProcess
    stdout: StdioCollector {
      waitForEnd: false
      onStreamFinished: {}
    }
    stderr: StdioCollector {
      waitForEnd: false
      onStreamFinished: {
        if (text && root.helperState !== "ready")
          root.helperError = String(text).trim().slice(0, 240)
      }
    }
    onRunningChanged: {
      if (running) {
        root.helperState = "starting"
        readyTimer.restart()
      }
    }
    onExited: function(exitCode) { root.noteExit(exitCode) }
  }

  Process {
    id: statusProcess
    property string output: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: statusProcess.output = text
    }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        try {
          var parsed = JSON.parse(output)
          root.status = parsed
          root.helperState = parsed.state || "ready"
          root.helperError = parsed.lastError && parsed.lastError.message
            ? String(parsed.lastError.message) : ""
        } catch (error) {
          root.helperState = "unavailable"
          root.helperError = "The Sidecar helper returned unreadable local status."
        }
      } else if (helperProcess.running) {
        root.helperState = "starting"
      }
    }
  }

  Process {
    id: controlProcess
    property string output: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: controlProcess.output = text
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text) root.helperError = String(text).trim().slice(0, 240)
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.helperError)
        root.helperError = "The local Sidecar action did not complete."
      if (exitCode === 0)
        root.helperError = ""
      root.runNextControl()
      root.refresh()
    }
  }

  Process {
    id: installMenuProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text) root.helperError = String(text).trim().slice(0, 240)
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.helperError)
        root.helperError = "Could not open Omarchy’s Install menu. Press Super + Alt + Space, then choose Install → Service → Tailscale."
    }
  }

  Process {
    id: tailscalePanelProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text) root.helperError = String(text).trim().slice(0, 240)
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.helperError)
        root.helperError = "Could not open Omarchy’s Tailscale panel. Open it from the bar, finish connecting, and return to Sidecar."
    }
  }

  Process {
    id: httpsSettingsProcess
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text) root.helperError = String(text).trim().slice(0, 240)
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && !root.helperError)
        root.helperError = "Could not open Tailscale DNS settings. Visit login.tailscale.com/admin/dns and enable HTTPS certificates."
    }
  }

  Timer {
    id: readyTimer
    interval: 250
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.helperWanted
    onTriggered: root.refresh()
  }

  Timer {
    id: restartTimer
    interval: 1000
    repeat: false
    onTriggered: root.startHelper()
  }

  Component.onCompleted: {
    restartWindowStarted = Date.now()
    probeNextRoot()
  }

  Component.onDestruction: {
    helperWanted = false
    restartTimer.stop()
    readyTimer.stop()
    helperProcess.running = false
  }
}
