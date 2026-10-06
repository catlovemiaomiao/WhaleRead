import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: panel
    objectName: "readingMarginNote"
    property var host
    property var note: ({})
    property string excerpt: ""
    property string colorId: "yellow"
    property bool opened: false
    signal saveRequested(string colorId, string content)
    signal removeRequested(string identity)
    visible: opened
    width: Math.min(318, parent ? parent.width * .42 : 318)
    radius: 12
    color: host ? host.paper : "#FBF8EE"
    border.color: host ? host.line : "#D8DDD6"
    border.width: 1
    layer.enabled: true

    function showNote(value, fallbackExcerpt) {
        note = value || ({})
        excerpt = note.excerpt || fallbackExcerpt || ""
        colorId = note.color || "yellow"
        noteText.text = note.content || ""
        opened = true
        Qt.callLater(function() {
            noteText.cursorPosition = 0
            noteText.forceActiveFocus()
            if (noteScroll.contentItem)
                noteScroll.contentItem.contentY = 0
        })
    }
    function close() { opened = false; note = ({}); noteText.text = "" }
    function colorValue(value) {
        if (value === "rose") return "#E9B9B2"
        if (value === "blue") return "#AECBD9"
        if (value === "green") return "#B8D2BC"
        return "#E8D890"
    }

    component NoteButton: Button {
        id: button
        implicitHeight: 32; leftPadding: 12; rightPadding: 12
        contentItem: Text { text: button.text; color: panel.host ? panel.host.ink : "#373B30"; font.family: "PingFang SC"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 7; color: button.hovered || button.down ? (panel.host && panel.host.night ? "#394245" : "#E7E8DC") : "transparent"; border.color: panel.host ? panel.host.line : "#D8DDD6" }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: panel.host && panel.host.noteDockBottom ? 12 : 15
        spacing: panel.host && panel.host.noteDockBottom ? 6 : 10
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 1
                Text { text: qsTr("页边随笔"); color: panel.host ? panel.host.ink : "#373B30"; font.family: "Songti SC"; font.pixelSize: 18; font.weight: Font.DemiBold }
                Text { text: qsTr("只保存在当前这本书里"); color: panel.host ? panel.host.muted : "#858B7C"; font.pixelSize: 10 }
            }
            NoteButton { objectName: "closeMarginNote"; text: qsTr("收起"); onClicked: panel.close() }
        }
        Text {
            Layout.fillWidth: true; Layout.maximumHeight: panel.host && panel.host.noteDockBottom ? 32 : 76; clip: true
            text: panel.excerpt; color: panel.host ? panel.host.muted : "#858B7C"
            font.family: "Songti SC"; font.pixelSize: 12; wrapMode: Text.Wrap; elide: Text.ElideRight
        }
        Text { text: qsTr("段落标色"); color: panel.host ? panel.host.muted : "#858B7C"; font.pixelSize: 11 }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Repeater {
                model: ["yellow", "rose", "blue", "green"]
                delegate: Button {
                    required property string modelData
                    objectName: "noteColor_" + modelData
                    Layout.fillWidth: true; implicitHeight: 30; checkable: true
                    checked: panel.colorId === modelData
                    onClicked: panel.colorId = modelData
                    contentItem: Item {}
                    background: Rectangle {
                        radius: 7; color: panel.colorValue(modelData); opacity: parent.enabled ? 1 : .5
                        border.width: parent.checked ? 2 : 0; border.color: panel.host ? panel.host.accent : "#2E5A45"
                    }
                }
            }
        }
        ScrollView {
            id: noteScroll
            objectName: "marginNoteScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: panel.host && panel.host.noteDockBottom ? 70 : 120
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded
            TextArea {
                id: noteText
                objectName: "marginNoteText"
                width: noteScroll.availableWidth
                height: Math.max(noteScroll.availableHeight, implicitHeight)
                placeholderText: qsTr("随手记一点东西，也可以只标色不写字。")
                wrapMode: TextEdit.Wrap; selectByMouse: true
                color: panel.host ? panel.host.ink : "#373B30"; font.family: "PingFang SC"; font.pixelSize: 13
                background: Rectangle { radius: 8; color: panel.host && panel.host.night ? "#253033" : "#FFFEF9"; border.color: noteText.activeFocus && panel.host ? panel.host.accent : panel.host ? panel.host.line : "#D8DDD6" }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            NoteButton {
                objectName: "removeMarginNote"; visible: !!panel.note.id
                text: qsTr("删除标记")
                onClicked: panel.removeRequested(panel.note.id)
            }
            Item { Layout.fillWidth: true }
            NoteButton { objectName: "saveMarginNote"; text: qsTr("保存"); onClicked: panel.saveRequested(panel.colorId, noteText.text) }
        }
    }
}
