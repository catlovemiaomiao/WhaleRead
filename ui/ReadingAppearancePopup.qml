import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup { popupType: Popup.Window;
    id: popup
    property var host
    property bool showTextControls: false
    property color paper: host ? host.paper : "#FBF8EE"
    property color ink: host ? host.ink : "#373B30"
    property color muted: host ? host.muted : "#858B7C"
    property color line: host ? host.line : "#DDDCCF"
    property color accent: host ? host.accent : "#2E5A45"
    property bool night: host ? host.night : false
    property string effectiveBackgroundId: host ? host.effectiveBackgroundId : "paper"
    property real brightness: host ? host.readingBrightness : 1.0
    property real lineHeight: host ? host.readingLineHeight : 1.85
    implicitWidth: 370; width: implicitWidth; padding: 14
    implicitHeight: appearanceContent.implicitHeight + topPadding + bottomPadding
    height: implicitHeight
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: popup.paper; radius: 10; border.color: popup.line }

    component QuietButton: Button {
        id: control
        property bool selected: false
        implicitHeight: 32
        implicitWidth: Math.max(34, controlLabel.implicitWidth + 18)
        hoverEnabled: true
        Accessible.name: text
        contentItem: Text {
            id: controlLabel; text: control.text; color: control.enabled ? popup.ink : popup.muted
            font.family: "PingFang SC"; font.pixelSize: 12
            font.weight: control.selected ? Font.Medium : Font.Normal
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: control.selected ? (popup.night ? "#3A4947" : "#E1E7DC")
                                    : control.hovered || control.down ? (popup.night ? "#313B3D" : "#ECEDE5") : "transparent"
            border.width: control.activeFocus ? 1 : 0; border.color: popup.accent
        }
    }

    contentItem: ColumnLayout {
        id: appearanceContent; spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Text { Layout.fillWidth: true; text: qsTr("阅读排版"); color: popup.ink; font.family: "Songti SC"; font.pixelSize: 17; font.weight: Font.DemiBold }
            QuietButton { objectName: "closeReadingAppearance"; text: qsTr("关闭"); onClicked: popup.close() }
        }
        Text { text: qsTr("纸张背景"); color: popup.muted; font.family: "PingFang SC"; font.pixelSize: 11 }
        RowLayout {
            Layout.fillWidth: true; spacing: 4
            QuietButton { objectName: "readingBackgroundPaper"; Layout.fillWidth: true; text: qsTr("宣纸"); selected: popup.effectiveBackgroundId === "paper"; onClicked: popup.host.setBackground("paper") }
            QuietButton { objectName: "readingBackgroundWarm"; Layout.fillWidth: true; text: qsTr("暖米"); selected: popup.effectiveBackgroundId === "warm"; onClicked: popup.host.setBackground("warm") }
            QuietButton { objectName: "readingBackgroundMist"; Layout.fillWidth: true; text: qsTr("雾灰"); selected: popup.effectiveBackgroundId === "mist"; onClicked: popup.host.setBackground("mist") }
            QuietButton { objectName: "readingBackgroundNight"; Layout.fillWidth: true; text: qsTr("深夜"); selected: popup.effectiveBackgroundId === "night"; onClicked: popup.host.setBackground("night") }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: popup.line }
        Text { text: qsTr("阅读亮度"); color: popup.muted; font.family: "PingFang SC"; font.pixelSize: 11 }
        RowLayout {
            Layout.fillWidth: true; spacing: 5
            QuietButton {
                objectName: "readingBrightnessDecrease"; text: "−"; enabled: popup.brightness > 0.6
                onClicked: popup.host.setReadingBrightness(popup.brightness - 0.05)
            }
            Slider {
                id: brightnessSlider
                objectName: "readingBrightnessSlider"
                Layout.fillWidth: true; implicitHeight: 30
                from: 0.6; to: 1.1; stepSize: 0.05; value: popup.brightness
                onMoved: popup.host.setReadingBrightness(value)
                ToolTip.visible: pressed; ToolTip.text: Math.round(value * 100) + "%"
            }
            QuietButton {
                objectName: "readingBrightnessIncrease"; text: "+"; enabled: popup.brightness < 1.1
                onClicked: popup.host.setReadingBrightness(popup.brightness + 0.05)
            }
            Text { Layout.preferredWidth: 38; text: Math.round(popup.brightness * 100) + "%"; color: popup.ink; font.family: "Georgia"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter }
            QuietButton { objectName: "readingBrightnessReset"; text: qsTr("默认"); onClicked: popup.host.setReadingBrightness(1.0) }
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("只调节正文画布，不改变系统屏幕亮度或应用按钮。")
            color: popup.muted; font.family: "PingFang SC"; font.pixelSize: 10; wrapMode: Text.Wrap
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: popup.line }
        Text { text: qsTr("译文行距"); color: popup.muted; font.family: "PingFang SC"; font.pixelSize: 11 }
        RowLayout {
            Layout.fillWidth: true; spacing: 5
            QuietButton {
                objectName: "readingLineSpacingDecrease"; text: "−"; enabled: popup.lineHeight > 1.3
                onClicked: popup.host.setReadingLineHeight(popup.lineHeight - 0.1)
            }
            Slider {
                id: lineSpacingSlider
                objectName: "readingLineSpacingSlider"
                Layout.fillWidth: true; implicitHeight: 30
                from: 1.3; to: 2.6; stepSize: 0.05; value: popup.lineHeight
                onMoved: popup.host.setReadingLineHeight(value)
                ToolTip.visible: pressed; ToolTip.text: Number(value).toFixed(2)
            }
            QuietButton {
                objectName: "readingLineSpacingIncrease"; text: "+"; enabled: popup.lineHeight < 2.6
                onClicked: popup.host.setReadingLineHeight(popup.lineHeight + 0.1)
            }
            Text { Layout.preferredWidth: 34; text: Number(popup.lineHeight).toFixed(2); color: popup.ink; font.family: "Georgia"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter }
            QuietButton { objectName: "readingLineSpacingReset"; text: qsTr("默认"); onClicked: popup.host.setReadingLineHeight(1.85) }
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("同一行距同时用于 TXT 与 EPUB 译文；调整时保留当前阅读位置。")
            color: popup.muted; font.family: "PingFang SC"; font.pixelSize: 10; wrapMode: Text.Wrap
        }
        Rectangle { visible: popup.showTextControls; Layout.fillWidth: true; height: visible ? 1 : 0; color: popup.line }
        RowLayout {
            visible: popup.showTextControls; Layout.fillWidth: true
            Text { text: qsTr("TXT 字号"); color: popup.muted; font.pixelSize: 11 }
            Item { Layout.fillWidth: true }
            QuietButton { text: "A−"; enabled: backend.readerFontSize > 15; onClicked: backend.readerFontSize -= 1 }
            Text { text: backend.readerFontSize; color: popup.ink; font.pixelSize: 12 }
            QuietButton { text: "A+"; enabled: backend.readerFontSize < 30; onClicked: backend.readerFontSize += 1 }
        }
    }
}
