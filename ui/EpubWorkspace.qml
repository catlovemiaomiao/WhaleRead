import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: workspace
    objectName: "epubWorkspace"
    required property var reading
    required property var host
    // Loader.setSource() values are snapshots, not live bindings.  Resolve the
    // EPUB chrome palette from the host so the header/footer follow background
    // changes just like the WebView document does.
    readonly property color ink: host.ink
    readonly property color surface: host.color
    property color muted: host.muted
    property color line: host.line
    property color accent: host.accent
    property bool night: host.night
    property string backgroundId: host.backgroundId
    property real readingBrightness: host.readingBrightness
    property real readingLineHeight: host.readingLineHeight
    property real epubLineHeight: readingLineHeight
    property bool focusMode: host.focusMode
    property bool epubPairVisible: reading.epubMode === "bilingual"
    property bool grouped: true
    property bool current: backend.activeReadingColumnId === reading.columnId
    property var peer: reading.epubPeer
    // Which sheet actually renders this mode; the hidden one must never react
    // to reading keys even though its native view still exists.
    readonly property bool primaryVisible: epubPairVisible || (reading.epubTranslated ? reading.epubMode !== "original" : true)
    readonly property bool peerVisible: !!peer && reading.epubMode !== "translated"
    readonly property bool nativeEpubViewsHidden: host.nativeEpubViewsHidden === true
    // Immersive keyboard paging belongs to the visible reading surface of the
    // active column only, and steps aside for the in-window annotation editor.
    readonly property bool readingKeysEnabled: focusMode && current && !nativeEpubViewsHidden
    color: surface
    function flagText(row, start, end, quote) { reading.activate(); host.flagText(row, start, end, quote) }
    function openAnnotations() { reading.activate(); host.openAnnotations() }
    function toggleFocus() { reading.activate(); host.toggleFocus() }
    function mode(value) { reading.setEpubMode(value) }
    function setBackground(value) { reading.activate(); host.setBackground(value) }
    function setReadingBrightness(value) { reading.activate(); host.setReadingBrightness(value) }
    function setReadingLineHeight(value) { reading.activate(); host.setReadingLineHeight(value) }
    function setEpubLineHeight(value) { setReadingLineHeight(value) }
    function openAppearance(textControls) { reading.activate(); host.openAppearance(textControls) }
    function closeAppearance() { if (host) host.closeAppearance() }
    function openEpubNote(anchor) { reading.activate(); host.openEpubNote(anchor) }
    function openExistingEpubNote(identity) { reading.activate(); host.openExistingEpubNote(identity) }
    // Move the keyboard into the visible sheet so its native WKWebView becomes
    // first responder; each sheet self-guards and only a visible one claims it.
    function takeReadingFocus() {
        if (primary.surfaceVisible) primary.claimReadingFocus()
        else if (original.item) original.item.claimReadingFocus()
    }
    Component.onCompleted: if (reading.epubMode === "bilingual" && !reading.epubPeer) reading.setEpubMode("bilingual")
    component Choice: Button {
        property bool chosen: false
        implicitHeight: 30
        font.family: "PingFang SC"; font.pixelSize: 12
        contentItem: Text { text: parent.text; color: workspace.ink; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font: parent.font }
        background: Rectangle { radius: 5; color: parent.chosen ? (workspace.night ? "#3A5148" : "#DFE8DC") : "transparent"; border.color: workspace.line; border.width: parent.hovered ? 1 : 0 }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 36; Layout.leftMargin: 8; Layout.rightMargin: 8; spacing: 2
            Item { Layout.fillWidth: true }
            Choice { text: qsTr("换书"); visible: !workspace.focusMode; onClicked: reading.pickReadingFile() }
            Choice { objectName: "addEpubGroup"; text: qsTr("+ 栏"); visible: !workspace.focusMode; onClicked: { reading.activate(); backend.addReadingColumn() } }
            Choice { text: qsTr("× 栏"); visible: !workspace.focusMode; onClicked: backend.removeReadingColumn(reading.columnId) }
            Choice { objectName: "epubGroupFocus"; text: workspace.focusMode ? qsTr("退出沉浸") : qsTr("沉浸阅读"); onClicked: workspace.toggleFocus() }
        }
        ReadingModeBar {
            Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10
            mode: reading.readingMode; canCompare: true
            ink: workspace.ink; muted: workspace.muted; line: workspace.line; accent: workspace.accent
            selectedFill: workspace.night ? "#3A5148" : "#DFE8DC"
            translatedObjectName: "epubModeTranslated"
            originalObjectName: "epubModeOriginal"
            bilingualObjectName: "epubModeBilingual"
            onModeRequested: function(value) { workspace.mode(value) }
        }
        Rectangle { Layout.fillWidth: true; height: 2; color: workspace.current ? workspace.accent : workspace.line }
        Item {
            id: sheets
            Layout.fillWidth: true; Layout.fillHeight: true
            EpubPane {
                id: primary
                reading: workspace.reading; host: workspace; ink: workspace.ink; surface: workspace.surface
                visible: workspace.epubPairVisible || (reading.epubTranslated ? reading.epubMode !== "original" : true)
                x: workspace.epubPairVisible && backend.epubOriginalOnLeft ? parent.width / 2 : 0
                width: workspace.epubPairVisible ? parent.width / 2 : parent.width; height: parent.height
            }
            Loader {
                id: original
                objectName: "epubOriginalSheet"
                active: !!workspace.peer
                visible: !!workspace.peer && reading.epubMode !== "translated"
                x: workspace.epubPairVisible && !backend.epubOriginalOnLeft ? parent.width / 2 : 0
                width: workspace.epubPairVisible ? parent.width / 2 : parent.width; height: parent.height
                sourceComponent: EpubPane { reading: workspace.peer; host: workspace; ink: workspace.ink; surface: workspace.surface }
            }
        }
        RowLayout {
            visible: workspace.epubPairVisible
            Layout.fillWidth: true; Layout.preferredHeight: 40; spacing: 2
            Choice { objectName: "bookPreviousTogether"; text: qsTr("‹ 一起上一节"); enabled: reading.epubSession.state.chapter > 0 && workspace.peer && workspace.peer.epubSession.state.chapter > 0; onClicked: { reading.activate(); backend.navigateEpubPair(-1) } }
            Item { Layout.fillWidth: true }
            Choice { objectName: "bookSwapSides"; text: qsTr("交换左右"); onClicked: { reading.activate(); backend.swapEpubSides() } }
            Choice { objectName: "bookLinkScroll"; text: backend.epubScrollLinked ? qsTr("同步滚动") : qsTr("独立滚动"); onClicked: backend.epubScrollLinked = !backend.epubScrollLinked }
            Item { Layout.fillWidth: true }
            Choice { objectName: "bookNextTogether"; text: qsTr("一起下一节 ›"); enabled: workspace.peer && reading.epubSession.state.chapter + 1 < reading.epubSession.state.chapters.length && workspace.peer.epubSession.state.chapter + 1 < workspace.peer.epubSession.state.chapters.length; onClicked: { reading.activate(); backend.navigateEpubPair(1) } }
        }
    }
}
