import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: bar
    property string mode: "translated"
    property bool canCompare: false
    property bool showSwap: false
    property color ink: "#373B30"
    property color muted: "#858B7C"
    property color line: "#DDDCCF"
    property color accent: "#2E5A45"
    property color selectedFill: "#DFE8DC"
    property string translatedObjectName: "readingModeTranslated"
    property string originalObjectName: "readingModeOriginal"
    property string bilingualObjectName: "readingModeBilingual"
    property string swapObjectName: "readingSwapSides"
    signal modeRequested(string value)
    signal swapRequested()
    implicitHeight: 42

    component Choice: Button {
        id: choice
        property bool chosen: false
        implicitHeight: 30
        implicitWidth: Math.max(54, label.implicitWidth + 22)
        hoverEnabled: true
        Accessible.name: text
        contentItem: Text {
            id: label
            text: choice.text
            color: choice.enabled ? bar.ink : bar.muted
            font.family: "PingFang SC"; font.pixelSize: 12
            font.weight: choice.chosen ? Font.Medium : Font.Normal
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: choice.chosen ? bar.selectedFill : choice.hovered || choice.down ? Qt.rgba(bar.accent.r, bar.accent.g, bar.accent.b, .08) : "transparent"
            border.width: choice.activeFocus ? 1 : 0
            border.color: bar.accent
        }
    }

    RowLayout {
        anchors.fill: parent; spacing: 3
        Item { Layout.fillWidth: true }
        Choice {
            objectName: bar.translatedObjectName
            text: qsTr("译文"); chosen: bar.mode === "translated"
            onClicked: bar.modeRequested("translated")
        }
        Choice {
            objectName: bar.originalObjectName
            text: qsTr("原文"); enabled: bar.canCompare; chosen: bar.mode === "original"
            onClicked: bar.modeRequested("original")
            ToolTip.visible: hovered && !enabled
            ToolTip.text: qsTr("这本书没有可验证的原译段落映射")
        }
        Choice {
            objectName: bar.bilingualObjectName
            text: qsTr("双语"); enabled: bar.canCompare; chosen: bar.mode === "bilingual"
            onClicked: bar.modeRequested("bilingual")
            ToolTip.visible: hovered && !enabled
            ToolTip.text: qsTr("只在原文与译文能按段落稳定对应时开放")
        }
        Rectangle { visible: bar.showSwap; Layout.preferredWidth: 1; Layout.preferredHeight: 20; color: bar.line; Layout.leftMargin: 5; Layout.rightMargin: 5 }
        Choice {
            objectName: bar.swapObjectName
            visible: bar.showSwap
            text: qsTr("交换左右")
            onClicked: bar.swapRequested()
        }
        Item { Layout.fillWidth: true }
    }
}
