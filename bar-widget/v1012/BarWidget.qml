import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

BarWidget {
  id: root

  moduleName: "io.github.mtolhuys.sidecar"
  readonly property string buildIdentity: "sidecar-widget-v1012"
  readonly property var sidecarService: bar && bar.shell
    ? bar.shell.serviceFor("io.github.mtolhuys.sidecar") : null
  readonly property var status: sidecarService && sidecarService.status
    ? sidecarService.status : ({ state: "starting", pending: [], devices: [] })
  readonly property var securityReceipt: status.securityReceipt || ({
    healthy: false,
    uid: -1,
    listener: "127.0.0.1:47991",
    serveRoute: "unavailable -> http://127.0.0.1:47991",
    recovery: "Wait for the Sidecar helper to publish its local security receipt."
  })
  readonly property bool securityReceiptPending: Number(securityReceipt.uid) < 0
  readonly property string state: sidecarService
    ? String(sidecarService.helperState || status.state || "starting") : "unavailable"
  readonly property int pendingCount: status.pending ? status.pending.length : 0
  readonly property int capabilityRequestCount: status.capabilityRequests ? status.capabilityRequests.length : 0
  readonly property int approvalCount: pendingCount + capabilityRequestCount
  readonly property int onlineCount: Number(status.onlineCount || 0)
  readonly property var inboxState: status.inbox || ({ attentionSeq: 0, receivedCount: 0, lastKind: "" })
  readonly property int inboxAttentionSeq: Number(inboxState.attentionSeq || 0)
  readonly property var displayDevices: status.devices || []
  readonly property int deviceCount: displayDevices.length
  readonly property bool hasDevices: deviceCount > 0
  readonly property string errorCode: status.lastError ? String(status.lastError.code || "") : ""
  readonly property bool tailscaleMissing: errorCode === "tailscale-unavailable"
  readonly property bool tailscaleDisconnected: errorCode === "tailscale-disconnected"
  readonly property bool tailscaleHttpsUnavailable: errorCode === "tailscale-https-unavailable"
  readonly property bool tailscaleSetupFailure: tailscaleMissing || tailscaleDisconnected || tailscaleHttpsUnavailable
  readonly property string tailscaleRecoveryLabel: tailscaleMissing
    ? "Open Service menu" : (tailscaleDisconnected ? "Open Tailscale" : "Open HTTPS settings")
  readonly property bool tailscaleRecoveryOpening: sidecarService && (
    (tailscaleMissing && sidecarService.installMenuOpening)
    || (tailscaleDisconnected && sidecarService.tailscalePanelOpening)
    || (tailscaleHttpsUnavailable && sidecarService.httpsSettingsOpening)
  )
  property bool popupOpen: false
  property string revokeConfirmingId: ""
  property string managingDeviceId: ""
  property bool securityExpanded: false
  property int previousDeviceCount: -1
  property int previousInboxAttentionSeq: -1
  property real approvalGlow: 0

  onDeviceCountChanged: {
    if (previousDeviceCount >= 0 && deviceCount > previousDeviceCount)
      approvalPulseAnimation.restart()
    previousDeviceCount = deviceCount
  }
  onInboxAttentionSeqChanged: {
    if (previousInboxAttentionSeq >= 0 && inboxAttentionSeq > previousInboxAttentionSeq)
      approvalPulseAnimation.restart()
    previousInboxAttentionSeq = inboxAttentionSeq
  }

  SequentialAnimation {
    id: approvalPulseAnimation
    loops: 2
    NumberAnimation { target: root; property: "approvalGlow"; from: 0; to: 1; duration: 180; easing.type: Easing.OutCubic }
    NumberAnimation { target: root; property: "approvalGlow"; from: 1; to: 0; duration: 420; easing.type: Easing.InOutCubic }
  }

  component PhoneMark: Item {
    id: phoneMark

    property color outlineColor: Color.foreground
    property color stateColor: Color.accent
    property color surroundColor: Color.background
    property string badgeText: ""

    Rectangle {
      id: phoneBody
      anchors.centerIn: parent
      width: Math.max(Style.space(8), parent.width * 0.48)
      height: Math.max(Style.space(14), parent.height * 0.76)
      radius: Math.max(Style.spaceReal(1.5), width * 0.18)
      color: "transparent"
      border.width: Math.max(1, Style.spaceReal(1))
      border.color: phoneMark.outlineColor

      Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Math.max(Style.spaceReal(2), parent.height * 0.12)
        width: Math.max(Style.spaceReal(2), parent.width * 0.28)
        height: Math.max(1, Style.spaceReal(1))
        radius: height / 2
        color: phoneMark.outlineColor
      }

      Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.max(Style.spaceReal(1.5), parent.height * 0.09)
        width: Math.max(Style.spaceReal(2), parent.width * 0.24)
        height: Math.max(1, Style.spaceReal(1))
        radius: height / 2
        color: phoneMark.outlineColor
      }
    }

    Rectangle {
      id: stateBadge
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      width: Math.max(Style.space(6), parent.width * 0.32)
      height: width
      radius: width / 2
      color: phoneMark.stateColor
      border.width: Math.max(1, Style.spaceReal(1))
      border.color: phoneMark.surroundColor

      Text {
        anchors.centerIn: parent
        visible: phoneMark.badgeText !== ""
        textFormat: Text.PlainText
        text: phoneMark.badgeText
        color: phoneMark.surroundColor
        font.family: root.bar.fontFamily
        font.pixelSize: Math.max(Style.space(6), stateBadge.width * 0.72)
        font.bold: true
      }
    }
  }

  function close() {
    popupOpen = false
    managingDeviceId = ""
    securityExpanded = false
  }
  function openTailscaleRecovery() {
    if (!sidecarService) return
    if (tailscaleMissing) sidecarService.openTailscaleInstallMenu()
    else if (tailscaleDisconnected) sidecarService.openTailscalePanel()
    else if (tailscaleHttpsUnavailable) sidecarService.openTailscaleHttpsSettings()
  }
  function stateColor() {
    if (approvalCount > 0) return Color.urgent
    if (state === "paused") return Color.muted
    if (state === "ready") return Color.accent
    if (state === "starting" || state === "restarting") return Color.accent
    return Color.urgent
  }
  function stateLabel() {
    if (state === "paused") return "Sidecar paused"
    if (approvalCount > 0) return "Sidecar approval waiting"
    if (state === "ready" && onlineCount > 0) return "Sidecar online"
    if (state === "ready") return "Sidecar ready"
    if (state === "starting" || state === "restarting") return "Sidecar starting"
    return "Sidecar unavailable"
  }
  function panelStateLabel() {
    if (approvalCount > 0) return "Approval waiting"
    if (state === "paused") return "Connections paused"
    if (state === "ready" && onlineCount > 0)
      return onlineCount + (onlineCount === 1 ? " phone online" : " phones online")
    if (state === "ready") return hasDevices ? "Phones offline" : "Ready to pair"
    if (state === "starting" || state === "restarting") return "Starting…"
    return "Needs attention"
  }
  function accessLabel(device) {
    if ((device.scopes || []).includes("control:theme")) return "Portal + Morph"
    return (device.scopes || []).length > 1 ? "Custom access" : "Read only"
  }
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: " "
    labelVisible: false
    tooltipText: root.stateLabel()
    fixedWidth: root.barSize
    fixedHeight: root.barSize
    onPressed: root.popupOpen = !root.popupOpen

    PhoneMark {
      anchors.centerIn: parent
      width: Style.space(18)
      height: Style.space(20)
      outlineColor: root.bar.foreground
      stateColor: root.stateColor()
      surroundColor: Color.bar.background
      badgeText: root.approvalCount > 0 ? String(Math.min(root.approvalCount, 9)) : ""
    }
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(390))
    contentHeight: popup.fittedContentHeight(Math.min(contentColumn.implicitHeight, Style.space(690)))

    Flickable {
      anchors.fill: parent
      contentWidth: width
      contentHeight: contentColumn.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds

      Column {
        id: contentColumn
        width: parent.width
        spacing: Style.space(10)

        Row {
          width: parent.width
          spacing: Style.space(10)

          BorderSurface {
            width: Style.space(42)
            height: width
            radius: width / 2
            color: root.state === "ready"
              ? Style.selectedFillFor(root.bar.foreground, Color.accent)
              : Style.normalFillFor(root.bar.foreground, Color.accent)
            Rectangle {
              anchors.fill: parent
              anchors.margins: -Style.space(4)
              radius: width / 2
              color: "transparent"
              border.width: Math.max(1, Style.spaceReal(1))
              border.color: Color.accent
              opacity: root.approvalGlow
              scale: 1 + root.approvalGlow * 0.2
            }
            PhoneMark {
              anchors.centerIn: parent
              width: Style.space(22)
              height: Style.space(26)
              outlineColor: root.bar.foreground
              stateColor: root.stateColor()
              surroundColor: parent.color
              badgeText: root.approvalCount > 0 ? String(Math.min(root.approvalCount, 9)) : ""
            }
          }

          Column {
            width: parent.width - Style.space(52)
            Text {
              textFormat: Text.PlainText
              text: "Sidecar"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              textFormat: Text.PlainText
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.panelStateLabel()
              color: Qt.darker(root.bar.foreground, 1.35)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          visible: !!(root.sidecarService && root.sidecarService.helperError) && !root.tailscaleSetupFailure
          wrapMode: Text.WordWrap
          text: root.sidecarService ? root.sidecarService.helperError : ""
          color: Color.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        BorderSurface {
          width: parent.width
          height: inboxColumn.implicitHeight + Style.space(18)
          visible: Number(root.inboxState.receivedCount || 0) > 0
          color: Style.normalFillFor(root.bar.foreground, Color.accent)
          radius: Style.cornerRadius

          Row {
            id: inboxColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(9)
            spacing: Style.space(8)

            Column {
              width: parent.width - openInboxButton.width - Style.space(8)
              Text {
                textFormat: Text.PlainText
                text: String(root.inboxState.lastKind || "File") + " received"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                font.bold: true
              }
              Text {
                textFormat: Text.PlainText
                text: "Saved to Sidecar Inbox"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            Button {
              id: openInboxButton
              anchors.verticalCenter: parent.verticalCenter
              text: "Open inbox"
              focusable: true
              selected: true
              foreground: root.bar.foreground
              onClicked: root.sidecarService.openInbox()
            }
          }
        }

        BorderSurface {
          width: parent.width
          height: tailscaleSetupColumn.implicitHeight + Style.space(22)
          visible: root.tailscaleSetupFailure
          color: Style.normalFillFor(root.bar.foreground, Color.accent)
          radius: Style.cornerRadius

          Column {
            id: tailscaleSetupColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(11)
            spacing: Style.space(7)

            Text {
              textFormat: Text.PlainText
              text: "PRIVATE TRANSPORT SETUP"
              color: Color.accent
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 0.6
            }
            Text {
              textFormat: Text.PlainText
              text: root.tailscaleMissing ? "Tailscale is needed"
                : (root.tailscaleDisconnected ? "Connect Tailscale" : "Enable Tailscale HTTPS")
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Text {
              textFormat: Text.PlainText
              width: parent.width
              wrapMode: Text.WordWrap
              text: root.tailscaleMissing
                ? "Choose Tailscale in Omarchy’s Service menu and finish signing in. Sidecar will notice automatically; it never installs Tailscale or joins a tailnet for you."
                : (root.tailscaleDisconnected
                  ? "Tailscale is installed but not connected. Finish sign-in in Omarchy’s Tailscale panel. Sidecar will retry automatically."
                  : "Tailscale is connected, but its exact Sidecar HTTPS route is not active. Enable HTTPS certificates in the official Tailscale DNS settings; Sidecar will retry automatically.")
              color: Qt.darker(root.bar.foreground, 1.25)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            Column {
              id: tailscaleSetupActions
              width: parent.width
              spacing: Style.space(5)

              Button {
                width: parent.width
                text: root.tailscaleRecoveryOpening
                  ? "Opening…" : root.tailscaleRecoveryLabel
                iconText: "→"
                focusable: true
                selected: true
                leftAlign: true
                enabled: root.sidecarService && !root.tailscaleRecoveryOpening
                foreground: root.bar.foreground
                onClicked: root.openTailscaleRecovery()
              }
              Button {
                width: parent.width
                text: "Try again now"
                iconText: "↻"
                focusable: true
                bordered: true
                leftAlign: true
                foreground: root.bar.foreground
                onClicked: root.sidecarService.retryTailscale()
              }
            }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: root.pendingCount > 0

          Text {
            textFormat: Text.PlainText
            text: "Check this phone"
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }

          Repeater {
            model: root.status.pending || []
            BorderSurface {
              required property var modelData
              width: contentColumn.width
              height: approvalColumn.implicitHeight + Style.space(18)
              color: Style.normalFillFor(root.bar.foreground, Color.accent)
              radius: Style.cornerRadius
              Column {
                id: approvalColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(9)
                spacing: Style.space(6)
                Text {
                  textFormat: Text.PlainText
                  text: String(modelData.name || "Phone") + " · " + String(modelData.platform || "web")
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  text: (modelData.verificationPhrase || []).join("  ·  ")
                  color: Color.accent
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.subtitle
                }
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: "Portal workspaces and apps · Morph themes · media Beam · hold to lock"
                  color: Qt.darker(root.bar.foreground, 1.3)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Row {
                  spacing: Style.space(6)
                  Button {
                    text: "Allow"
                    focusable: true
                    foreground: root.bar.foreground
                    selected: true
                    onClicked: root.sidecarService.pairApprove(modelData.id)
                  }
                  Button {
                    text: "Deny"
                    focusable: true
                    foreground: root.bar.foreground
                    onClicked: root.sidecarService.pairDeny(modelData.id)
                  }
                }
              }
            }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: root.capabilityRequestCount > 0

          Text {
            textFormat: Text.PlainText
            text: "Unlock new Sidecar abilities"
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }

          Repeater {
            model: root.status.capabilityRequests || []
            BorderSurface {
              required property var modelData
              width: contentColumn.width
              height: capabilityColumn.implicitHeight + Style.space(18)
              color: Style.normalFillFor(root.bar.foreground, Color.accent)
              radius: Style.cornerRadius
              Column {
                id: capabilityColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.margins: Style.space(9)
                spacing: Style.space(6)
                Text {
                  textFormat: Text.PlainText
                  text: String(modelData.deviceName || "Phone")
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  wrapMode: Text.WordWrap
                  text: (modelData.labels || []).join(" · ")
                  color: Qt.darker(root.bar.foreground, 1.25)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
                Row {
                  spacing: Style.space(6)
                  Button {
                    text: "Allow"
                    focusable: true
                    selected: true
                    foreground: root.bar.foreground
                    onClicked: root.sidecarService.capabilityApprove(modelData.id)
                  }
                  Button {
                    text: "Not now"
                    focusable: true
                    foreground: root.bar.foreground
                    onClicked: root.sidecarService.capabilityDeny(modelData.id)
                  }
                }
              }
            }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: !!root.status.pairing && root.pendingCount === 0

          Text {
            textFormat: Text.PlainText
            text: "Scan to pair"
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
          BorderSurface {
            width: parent.width
            height: phoneChecklistColumn.implicitHeight + Style.space(18)
            color: Style.normalFillFor(root.bar.foreground, Color.accent)
            radius: Style.cornerRadius

            Column {
              id: phoneChecklistColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(9)
              spacing: Style.space(4)
              Text {
                textFormat: Text.PlainText
                text: "BEFORE YOU SCAN"
                color: Color.accent
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 0.6
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Open Tailscale on this phone. It must say Connected on the same tailnet as this desktop."
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }
          Image {
            anchors.horizontalCenter: parent.horizontalCenter
            width: Style.space(200)
            height: width
            source: root.status.pairing ? String(root.status.pairing.qrDataUrl || "") : ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true
          }
          Text {
            textFormat: Text.PlainText
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: root.status.pairing
              ? "Fresh for " + Number(root.status.pairing.remainingSeconds || 0) + " seconds"
              : ""
            color: Qt.darker(root.bar.foreground, 1.3)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
          Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(6)
            Button {
              text: "Refresh code"
              focusable: true
              foreground: root.bar.foreground
              onClicked: root.sidecarService.pairOpen()
            }
            Button {
              text: "Cancel"
              focusable: true
              foreground: root.bar.foreground
              onClicked: root.sidecarService.pairCancel()
            }
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(8)
          visible: !root.status.pairing && root.pendingCount === 0 && !root.tailscaleSetupFailure

          Row {
            width: parent.width
            spacing: Style.space(8)
            Text {
              textFormat: Text.PlainText
              width: parent.width - pairButton.width - Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              text: root.hasDevices ? "Your phones" : "Bring your desktop along"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.subtitle
              font.bold: true
            }
            Button {
              id: pairButton
              text: root.hasDevices ? "+ Pair" : "Pair a phone"
              focusable: true
              selected: !root.hasDevices
              foreground: root.bar.foreground
              enabled: root.state === "ready"
              onClicked: root.sidecarService.pairOpen()
            }
          }
          Text {
            textFormat: Text.PlainText
            width: parent.width
            visible: !root.hasDevices
            wrapMode: Text.WordWrap
            text: "Tailscale on both devices. One local approval. Done."
            color: Qt.darker(root.bar.foreground, 1.3)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        Repeater {
          model: root.displayDevices
          BorderSurface {
            required property var modelData
            width: contentColumn.width
            height: deviceColumn.implicitHeight + Style.space(20)
            radius: Style.cornerRadius
            color: Style.normalFillFor(root.bar.foreground, Color.accent)
            Column {
              id: deviceColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(9)
              spacing: Style.space(8)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(7)
                  height: width
                  radius: width / 2
                  color: modelData.online ? Color.accent : Color.muted
                }

                Column {
                  width: parent.width - manageButton.width - Style.space(15)
                  spacing: Style.space(2)
                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    elide: Text.ElideRight
                    text: String(modelData.name || "Phone")
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                  }
                  Text {
                    textFormat: Text.PlainText
                    width: parent.width
                    elide: Text.ElideRight
                    text: (modelData.online ? "Online" : "Offline") + " · " + root.accessLabel(modelData)
                    color: Qt.darker(root.bar.foreground, 1.35)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                  }
                }

                Button {
                  id: manageButton
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.managingDeviceId === String(modelData.id) ? "Done" : "Manage"
                  focusable: true
                  foreground: root.bar.foreground
                  onClicked: {
                    var id = String(modelData.id)
                    root.managingDeviceId = root.managingDeviceId === id ? "" : id
                    root.revokeConfirmingId = ""
                  }
                }
              }

              Column {
                width: parent.width
                spacing: Style.space(7)
                visible: root.managingDeviceId === String(modelData.id)

                PanelSeparator { foreground: root.bar.foreground }

                TextField {
                  id: renameField
                  width: parent.width
                  text: String(modelData.name || "")
                  placeholderText: "Phone name"
                  foreground: root.bar.foreground
                  onAccepted: root.sidecarService.renameDevice(modelData.id, text)
                }

                Row {
                  spacing: Style.space(6)
                  Button {
                    text: "Save name"
                    focusable: true
                    selected: true
                    foreground: root.bar.foreground
                    onClicked: root.sidecarService.renameDevice(modelData.id, renameField.text)
                  }
                  Button {
                    text: (modelData.scopes || []).length > 1 ? "Make read only" : "Restore controls"
                    focusable: true
                    foreground: root.bar.foreground
                    onClicked: {
                      if ((modelData.scopes || []).length > 1) root.sidecarService.setReadOnly(modelData.id)
                      else root.sidecarService.setFullControls(modelData.id, (modelData.scopes || []).includes("write:inbox"))
                    }
                  }
                }

                Button {
                  width: parent.width
                  text: root.revokeConfirmingId === String(modelData.id) ? "Confirm remove phone" : "Remove this phone"
                  focusable: true
                  bordered: true
                  leftAlign: true
                  foreground: Color.urgent
                  onClicked: {
                    var id = String(modelData.id)
                    if (root.revokeConfirmingId === id) {
                      root.revokeConfirmingId = ""
                      revokeTimer.stop()
                      root.sidecarService.revokeDevice(id)
                    } else {
                      root.revokeConfirmingId = id
                      revokeTimer.restart()
                    }
                  }
                }
                Timer {
                  id: revokeTimer
                  interval: 5000
                  onTriggered: {
                    if (root.revokeConfirmingId === String(modelData.id)) root.revokeConfirmingId = ""
                  }
                }
              }
            }
          }
        }

        PanelSeparator { foreground: root.bar.foreground }

        Button {
          width: parent.width
          text: root.status.paused ? "Resume phone connections" : "Pause phone connections"
          focusable: true
          leftAlign: true
          foreground: root.bar.foreground
          onClicked: root.sidecarService.pause(!root.status.paused)
        }

        BorderSurface {
          width: parent.width
          height: securityColumn.implicitHeight + Style.space(20)
          radius: Style.cornerRadius
          color: Style.normalFillFor(root.bar.foreground, Color.accent)

          Column {
            id: securityColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.space(10)
            spacing: Style.space(6)

            Row {
              width: parent.width
              spacing: Style.space(8)

              Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(24)
                height: width
                radius: width / 2
                color: root.securityReceipt.healthy || root.securityReceiptPending
                  ? Style.selectedFillFor(root.bar.foreground, Color.accent)
                  : Style.normalFillFor(root.bar.foreground, Color.urgent)
                Text {
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: root.securityReceiptPending ? "…" : (root.securityReceipt.healthy ? "✓" : "!")
                  color: root.securityReceipt.healthy || root.securityReceiptPending ? root.bar.foreground : Color.urgent
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }

              Column {
                width: parent.width - detailsButton.width - Style.space(32)
                spacing: Style.space(1)
                Text {
                  textFormat: Text.PlainText
                  text: root.securityReceiptPending ? "Checking security" : (root.securityReceipt.healthy ? "Private by design" : "Security check failed")
                  color: root.securityReceipt.healthy || root.securityReceiptPending ? root.bar.foreground : Color.urgent
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  elide: Text.ElideRight
                  text: root.securityReceiptPending
                    ? "Waiting for the local helper receipt"
                    : (root.securityReceipt.healthy
                    ? "Loopback · tailnet · phone scopes"
                    : "Open details for the exact recovery step")
                  color: Qt.darker(root.bar.foreground, 1.35)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Button {
                id: detailsButton
                anchors.verticalCenter: parent.verticalCenter
                text: root.securityExpanded ? "Hide" : "Details"
                focusable: true
                foreground: root.securityReceipt.healthy || root.securityReceiptPending ? root.bar.foreground : Color.urgent
                onClicked: root.securityExpanded = !root.securityExpanded
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.securityExpanded || (!root.securityReceipt.healthy && !root.securityReceiptPending)

              PanelSeparator { foreground: root.bar.foreground }

              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.securityReceiptPending
                  ? "Security Receipt · checking local helper · " + String(root.securityReceipt.listener)
                  : "Security Receipt · UID " + String(root.securityReceipt.uid)
                    + (root.securityReceipt.nonRoot ? " · non-root · " : " · ROOT — STOP · ")
                    + String(root.securityReceipt.listener)
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Existing tailnet only · Tailscale SSH untouched · no package, service, firewall, sudoers, or SUID changes"
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WrapAnywhere
                text: String(root.securityReceipt.serveRoute)
                  .replace(" → ", "\n→ ").replace(" -> ", "\n→ ")
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Tailscale transports privately. Sidecar credentials and scopes authorize each phone."
                color: Qt.darker(root.bar.foreground, 1.2)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                visible: !root.securityReceipt.healthy && !root.securityReceiptPending
                wrapMode: Text.WordWrap
                text: root.tailscaleSetupFailure
                  ? String(root.securityReceipt.recovery || "Finish Tailscale setup; Sidecar will retry automatically.")
                  : String(root.securityReceipt.recovery || "Disable Sidecar and inspect redacted diagnostics.")
                color: Color.urgent
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Button {
                width: parent.width
                visible: root.tailscaleSetupFailure
                text: root.tailscaleRecoveryOpening ? "Opening…" : root.tailscaleRecoveryLabel
                iconText: "→"
                focusable: true
                bordered: true
                leftAlign: true
                foreground: Color.urgent
                enabled: root.sidecarService && !root.tailscaleRecoveryOpening
                onClicked: root.openTailscaleRecovery()
              }
            }
          }
        }
      }
    }
  }

  IpcHandler {
    target: "sidecar"
    function identity(): string {
      return JSON.stringify({
        service: root.sidecarService ? String(root.sidecarService.buildIdentity || "") : "",
        widget: root.buildIdentity,
        helper: root.status.builds ? String(root.status.builds.helper || "") : "",
        web: root.status.builds ? String(root.status.builds.web || "") : "",
        state: root.state,
        pending: root.pendingCount,
        capabilities: root.capabilityRequestCount,
        open: root.popupOpen,
        revokeConfirming: root.revokeConfirmingId !== "",
        managing: root.managingDeviceId !== "",
        securityExpanded: root.securityExpanded,
        inboxAttentionSeq: root.inboxAttentionSeq
      })
    }
  }
}
