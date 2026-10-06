import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: paper
    required property var position
    required property string original
    required property real pageWidth
    required property real pageHeight
    property var selected: ({})
    height: width * pageHeight / Math.max(1, pageWidth)
    Image {
        id: backgroundPage
        anchors.fill: parent
        source: paper.position.image || paper.original
        asynchronous: true
        fillMode: Image.Stretch
    }
    Repeater {
        model: paper.position.blocks || []
        delegate: Item {
            id: region
            required property var modelData
            objectName: 'positionBlock' + modelData.index
            visible: backgroundPage.status === Image.Ready
            property var box: modelData.bbox
            x: box[0] * paper.width; y: box[1] * paper.height
            width: box[2] * paper.width; height: box[3] * paper.height
            property bool overflow: modelData.translated && copy.truncated
            Text {
                id: copy
                objectName: 'positionText' + region.modelData.index
                anchors.fill: parent
                visible: region.modelData.translated
                text: region.modelData.translation
                textFormat: Text.PlainText
                color: region.modelData.color
                font.family: 'Songti SC'
                font.bold: region.modelData.heading
                font.pixelSize: Math.max(12, region.modelData.font_size * paper.width / Math.max(1, paper.pageWidth))
                minimumPixelSize: 11
                fontSizeMode: Text.Fit
                wrapMode: Text.Wrap
                elide: Text.ElideRight
                clip: true
            }
            Rectangle {
                anchors.fill: parent
                color: 'transparent'
                border.color: '#81988c'
                border.width: hit.containsMouse || region.overflow ? 1 : 0
            }
            Rectangle {
                visible: region.overflow
                anchors.right: parent.right; anchors.bottom: parent.bottom
                width: 52; height: 20; radius: 3; color: '#f4f5ed'
                Text { objectName: "positionFullTextExpand"; anchors.centerIn: parent; text: qsTr("展开全文"); color: '#183e36'; font.pixelSize: 11 }
            }
            MouseArea {
                id: hit
                anchors.fill: parent
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: { paper.selected = region.modelData; fullText.open() }
            }
        }
    }
    Dialog { popupType: Popup.Window;
        id: fullText
        objectName: 'positionFullText'
        parent: Overlay.overlay
        anchors.centerIn: parent
        // The macOS Dialog style derives its own implicit size from the
        // content.  Wrapped text then derives its width from the dialog, which
        // loops forever unless the popup owns an explicit implicit contract.
        implicitWidth: Math.max(320, Math.min(760, parent.width - 60))
        implicitHeight: Math.max(280, Math.min(650, parent.height - 60))
        width: implicitWidth
        height: implicitHeight
        modal: true
        title: qsTr("原文与译文")
        footer: DialogButtonBox {
            Button { text: qsTr("关闭"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            onRejected: fullText.close()
        }
        contentItem: ScrollView {
            clip: true
            contentWidth: availableWidth
            ScrollBar.vertical.policy: ScrollBar.AlwaysOn
            ColumnLayout {
                width: parent.width; spacing: 16
                Label { objectName: "positionFullTextSource"; text: qsTr("原文"); color: '#718780' }
                TextArea {
                    Layout.fillWidth: true
                    text: paper.selected.source || ''
                    textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                    readOnly: true; selectByMouse: true; font.pixelSize: 16
                    background: Item {}
                }
                Label { objectName: "positionFullTextTranslation"; text: paper.selected.translated ? qsTr("译文") : qsTr("本段尚未翻译"); color: '#718780' }
                TextArea {
                    Layout.fillWidth: true
                    text: paper.selected.translation || ''
                    textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                    readOnly: true; selectByMouse: true; font.pixelSize: 19
                    font.family: 'Songti SC'
                    background: Item {}
                }
            }
        }
    }
}
