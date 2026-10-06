import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pane
    objectName: "readerPane"
    required property var reading
    property bool isEpub: pane.reading.epubActive === true
    // QtWebView is a native macOS view and is composited above Popup.Item.
    // Keep the Loader alive, but hide its native surface while the shared
    // in-window annotation editor is open so Chinese IME focus stays intact.
    property bool nativeEpubViewsHidden: false
    function loadEpub() {
        if (isEpub) epubLoader.setSource("EpubWorkspace.qml", {reading: pane.reading, host: pane})
        else epubLoader.source = ""
    }
    onIsEpubChanged: loadEpub()
    readonly property bool noteDockBottom: width < 640
    readonly property real noteDockWidth: Math.min(318, Math.max(250, width * 0.34))
    readonly property real noteDockHeight: Math.min(330, Math.max(240, height * 0.42))
    readonly property real noteRightInset: marginNote.opened && !noteDockBottom ? noteDockWidth + 24 : 0
    readonly property real noteBottomInset: marginNote.opened && noteDockBottom ? noteDockHeight + 24 : 0
    readonly property real readingContentWidth: Math.max(0, width - noteRightInset)
    readonly property real readingContentHeight: Math.max(0, height - noteBottomInset)
    readonly property string noteDockEdge: noteDockBottom ? "bottom" : "right"
    Loader {
        id: epubLoader
        objectName: "epubLoader"
        anchors.left: parent.left; anchors.top: parent.top
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: pane.noteRightInset
        anchors.bottomMargin: pane.noteBottomInset
        visible: pane.isEpub && !pane.nativeEpubViewsHidden; z: 1
    }
    Rectangle {
        objectName: "readerLoadingBadge"
        visible: pane.reading.loading === true
        z: 30
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 14
        width: loadingRow.implicitWidth + 24
        height: 38
        radius: 19
        color: pane.night ? "#374143" : "#F0F1E9"
        border.color: pane.line
        RowLayout {
            id: loadingRow
            anchors.centerIn: parent
            spacing: 8
            BusyIndicator { running: parent.parent.visible; implicitWidth: 20; implicitHeight: 20 }
            Text {
                text: qsTr("正在打开《%1》…").arg(pane.reading.loadingTitle)
                color: pane.ink
                font.family: "PingFang SC"
                font.pixelSize: 12
            }
        }
    }
    property bool focusMode: false
    property bool epubPairVisible: true
    signal toggleFocus()
    signal openAnnotations()
    signal addColumn()
    signal closeColumn()
    signal annotationEditorVisibilityChanged(bool opened)
    // Immersive paging: one shared key map for the flow-layout (TXT/Markdown)
    // reader.  The list keeps the wheel, scrollbar and chapter behaviour; these
    // are additive steps that never exceed the real content bounds.
    readonly property real readingPageStep: Math.max(80, bookList.height * 0.85)
    readonly property real readingLineStep: Math.max(48, bookList.height * 0.12)
    function readingScroll(delta) {
        if (!delta) return
        pane.following = false
        bookList.contentY = Math.max(bookList.originY,
                                     Math.min(bookList.originY + bookList.contentHeight - bookList.height,
                                              bookList.contentY + delta))
    }
    function handleReadingKey(event) {
        if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) return false
        var shift = (event.modifiers & Qt.ShiftModifier) !== 0
        var delta = 0
        if (event.key === Qt.Key_Space) delta = shift ? -pane.readingPageStep : pane.readingPageStep
        else if (event.key === Qt.Key_PageDown) delta = pane.readingPageStep
        else if (event.key === Qt.Key_PageUp) delta = -pane.readingPageStep
        else if (event.key === Qt.Key_Down && !shift) delta = pane.readingLineStep
        else if (event.key === Qt.Key_Up && !shift) delta = -pane.readingLineStep
        else return false
        pane.readingScroll(delta)
        return true
    }
    // The reader viewport shows exactly one column while immersive; when this
    // pane becomes that column it must own the keyboard again.
    readonly property bool activeColumn: typeof backend !== "undefined" && backend.activeReadingColumnId === pane.reading.columnId
    onActiveColumnChanged: if (pane.focusMode && pane.activeColumn) Qt.callLater(pane.focusReadingSurface)
    // True when the given item, or one of its descendants, owns the keyboard.
    function focusWithin(item) {
        var current = pane.Window.activeFocusItem
        while (current) {
            if (current === item) return true
            current = current.parentItem
        }
        return false
    }
    // Move the keyboard from the immersion button into the visible reading
    // surface.  EPUB needs the native WKWebView to become first responder, so
    // the request goes through its loader item; hidden columns never claim it.
    function focusReadingSurface() {
        if (!pane.focusMode) return
        if (pane.isEpub) {
            if (epubLoader.item) epubLoader.item.takeReadingFocus()
            return
        }
        if (!pane.activeColumn) return
        bookList.forceActiveFocus()
        // A closing Popup.Window or a window that just regained key status can
        // leave the main window without an active focus item; a short bounded
        // retry puts the keyboard back into the reading body.
        if (!pane.focusWithin(bookList)) readingFocusRetry.restart()
    }
    onFocusModeChanged: if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
    property int annotationRow: -1
    property int annotationStart: 0
    property int annotationEnd: 0
    property string annotationQuote: ""
    function flagText(row, start, end, quote) {
        pane.reading.activate()
        annotationRow = row; annotationStart = start; annotationEnd = end; annotationQuote = quote
        annotationNote.text = ""; annotationPopup.open()
    }
    property bool night: backend.backgroundId === "night"
    property string backgroundId: backend.backgroundId
    property string effectiveBackgroundId: night ? "night" : backgroundId
    property real readingBrightness: backend.readingBrightness
    property real readingLineHeight: backend.readingLineHeight
    property real epubLineHeight: readingLineHeight
    property int fontSize: backend.readerFontSize
    property string flowMode: pane.reading.readingMode
    property bool bilingual: flowMode === "bilingual" && pane.reading.readerModel.hasSources
    property bool canCompare: pane.reading.readingJobPath.length > 0 || pane.reading.readerModel.hasSources
    property var bookmarkList: pane.reading.bookmarks
    property bool currentBookmarked: bookmarkList.some(function(mark) { return mark.position === pane.visibleRow })
    function changeStyle(action) {
        var anchor = pane.visibleRow
        action()
        pane.pendingJumpRow = anchor
        deferredJump.restart()
    }
    property bool following: false
    property int visibleRow: 0
    property int resetAnchor: 0
    property int pendingJumpRow: 0
    property bool restoringReset: false
    property var chapterList: pane.reading.readerModel.chapters
    property int currentChapterIndex: chapterIndexFor(visibleRow)
    property string currentChapterTitle: currentChapterIndex >= 0 ? chapterList[currentChapterIndex].title : ""
    property real readingFraction: pane.reading.readerModel.count > 0 ? (visibleRow + 1) / pane.reading.readerModel.count : 0
    property color accent: backend.themeId === "ocean" ? "#315E78" : backend.themeId === "amber" ? "#805B37" : backend.themeId === "sakura" ? "#7C4859" : "#2E5A45"
    property color ink: night ? "#E1E2D7" : "#373B30"
    property color muted: night ? "#9EA9A5" : "#858B7C"
    property color line: night ? "#424B4D" : "#DDDCCF"
    property color paper: pane.color
    function setBackground(value) {
        backend.backgroundId = value
    }
    function setReadingLineHeight(value) {
        if (pane.isEpub) backend.readingLineHeight = value
        else pane.changeStyle(function() { backend.readingLineHeight = value })
    }
    function setReadingBrightness(value) { backend.readingBrightness = value }
    function setEpubLineHeight(value) { setReadingLineHeight(value) }
    function openAppearance(showTextControls) {
        appearancePopup.showTextControls = showTextControls === true
        appearancePopup.open()
    }
    function closeAppearance() { appearancePopup.close() }
    property int marginNoteRow: -1
    property var marginNoteAnchor: ({})
    function noteFill(colorId) {
        if (colorId === "rose") return night ? "#563F40" : "#F7E1DC"
        if (colorId === "blue") return night ? "#344A55" : "#E1EDF2"
        if (colorId === "green") return night ? "#384B3D" : "#E5EFE4"
        return night ? "#544D35" : "#F4EDCE"
    }
    function noteDot(colorId) {
        if (colorId === "rose") return "#C77970"
        if (colorId === "blue") return "#6E9CB2"
        if (colorId === "green") return "#719F79"
        return "#B99F43"
    }
    function openTextNote(row, excerpt) {
        pane.reading.activate()
        marginNoteRow = row; marginNoteAnchor = ({})
        marginNote.showNote(pane.reading.paragraphNoteAt(row), excerpt || qsTr("第 %1 段").arg(row + 1))
    }
    function openEpubNote(anchor) {
        pane.reading.activate()
        marginNoteRow = -1; marginNoteAnchor = anchor || ({})
        marginNote.showNote(pane.reading.paragraphNoteForEpubAnchor(marginNoteAnchor),
                            anchor && anchor.source ? anchor.source : "")
    }
    function openExistingEpubNote(identity) {
        pane.reading.activate()
        var note = pane.reading.paragraphNoteById(identity)
        if (!note || !note.id) return
        marginNoteRow = -1; marginNoteAnchor = note.anchor || ({})
        marginNote.showNote(note, note.excerpt || "")
    }
    readonly property int frameBorderWidth: 0
    // The active column is already identified in its header.  Keep the
    // reading canvas borderless so a single column does not look boxed in.
    border.width: frameBorderWidth
    color: night ? "#202729" : backend.backgroundId === "warm" ? "#F7EDDA" : backend.backgroundId === "mist" ? "#F1F5F5" : "#FAF9F1"
    function escapeText(value) { return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    function jump(row) {
        if (row < 0 || pane.reading.readerModel.count === 0) return
        var target = Math.max(0, Math.min(Math.round(row), pane.reading.readerModel.count - 1))
        following = false
        bookList.positionViewAtIndex(target, ListView.Beginning)
        visibleRow = target
        savePosition.restart()
    }
    function latest() { following = true; bookList.positionViewAtEnd() }
    function updatePosition() {
        if (restoringReset) return
        var row = bookList.indexAt(bookList.width / 2, bookList.contentY + 25)
        if (row >= 0) { visibleRow = row; savePosition.restart() }
    }
    function chapterIndexFor(row) {
        var found = -1
        for (var i = 0; i < chapterList.length; i++) {
            if (chapterList[i].position > row) break
            found = i
        }
        return found
    }
    function jumpChapter(direction) {
        if (chapterList.length === 0) return
        var current = chapterIndexFor(visibleRow)
        var target = direction < 0 ? Math.max(0, current - 1) : Math.min(chapterList.length - 1, current + 1)
        if (current < 0) target = 0
        jump(chapterList[target].position)
    }
    TapHandler { acceptedButtons: Qt.LeftButton; onTapped: pane.reading.activate() }
    component ReaderButton: Button {
        id: b
        implicitHeight: 32
        implicitWidth: Math.max(32, buttonLabel.implicitWidth + 18)
        hoverEnabled: true
        Accessible.name: text
        contentItem: Text { id: buttonLabel; text: b.text; color: b.enabled ? pane.ink : pane.muted; font.family: "PingFang SC"; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 7; color: b.hovered || b.down ? (pane.night ? "#394245" : "#E7E8DC") : "transparent"; border.width: b.activeFocus ? 1 : 0; border.color: pane.muted }
    }
    ColumnLayout {
        id: flowWorkspace
        objectName: "flowWorkspace"
        visible: !pane.isEpub
        anchors.left: parent.left; anchors.top: parent.top
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: pane.noteRightInset
        anchors.bottomMargin: pane.noteBottomInset
        spacing: 0
        Item {
            Layout.fillWidth: true; Layout.preferredHeight: 70
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 25; anchors.rightMargin: 20; spacing: 5
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { objectName: "readerColumnBadge"; text: (backend.activeReadingColumnId === pane.reading.columnId ? qsTr("当前栏 · ") : "") + (pane.reading.readingExternal ? qsTr("我的阅读") : qsTr("边译边读")); color: pane.muted; font.family: "PingFang SC"; font.pixelSize: 10; font.letterSpacing: 2 }
                    Text { objectName: "readerTitleText"; Layout.fillWidth: true; text: pane.reading.readerTitle; color: pane.ink; font.family: "Songti SC"; font.pixelSize: 17; font.weight: Font.DemiBold; elide: Text.ElideRight }
                }
                ReaderButton { objectName: "chooseColumnBook"; text: pane.reading.empty ? qsTr("选择图书") : qsTr("换书"); onClicked: { pane.reading.activate(); openBooksPopup.open() } }
                ReaderButton { objectName: "addParallelColumn"; text: qsTr("+ 栏"); Accessible.name: qsTr("增加并列阅读栏"); onClicked: pane.addColumn() }
                ReaderButton { objectName: "closeParallelColumn"; text: qsTr("× 栏"); Accessible.name: qsTr("关闭当前阅读栏，保留书架和文件"); onClicked: pane.closeColumn() }
                ReaderButton { visible: pane.width > 650; objectName: "openTxtButton"; text: qsTr("打开图书"); onClicked: pane.reading.pickReadingFile() }
                ReaderButton { visible: pane.width > 570; objectName: "readerAnnotations"; text: qsTr("标注"); enabled: !pane.reading.empty; onClicked: { pane.reading.activate(); pane.openAnnotations() } }
                ReaderButton { visible: pane.width > 720; objectName: "readerFocus"; text: pane.focusMode ? qsTr("退出沉浸") : qsTr("沉浸阅读"); onClicked: pane.toggleFocus() }
                ReaderButton {
                    objectName: "readerMenu"; text: "•••"; Accessible.name: qsTr("更多阅读操作"); onClicked: moreMenu.open()
                    Menu { popupType: Popup.Window;
                        id: moreMenu
                        objectName: "readerMoreMenu"
                        MenuItem { text: qsTr("打开 TXT 阅读…"); onTriggered: pane.reading.pickReadingFile() }
                        MenuItem { text: qsTr("人工标注与校对"); enabled: !pane.reading.empty; onTriggered: { pane.reading.activate(); pane.openAnnotations() } }
                        MenuItem { text: pane.focusMode ? qsTr("退出沉浸阅读") : qsTr("沉浸阅读"); onTriggered: pane.toggleFocus() }
                        MenuItem { text: qsTr("回到当前译文"); enabled: backend.hasSource; onTriggered: pane.reading.followTranslation() }
                        MenuItem { text: qsTr("翻译 / 校对此书"); enabled: pane.reading.readingJobPath.length > 0; onTriggered: { pane.reading.activate(); backend.manageReadingBook() } }
                        MenuItem { text: qsTr("跳到开头"); enabled: pane.reading.readerModel.count > 0; onTriggered: pane.jump(0) }
                        MenuItem { text: qsTr("章节目录"); enabled: pane.reading.readerModel.chapters.length > 0; onTriggered: chaptersPopup.open() }
                    }
                }
            }
        }
        ReadingModeBar {
            Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
            mode: pane.flowMode; canCompare: pane.canCompare
            showSwap: pane.bilingual
            ink: pane.ink; muted: pane.muted; line: pane.line; accent: pane.accent
            selectedFill: pane.night ? "#3A5148" : "#DFE8DC"
            translatedObjectName: "readerModeTranslated"
            originalObjectName: "readerModeOriginal"
            bilingualObjectName: "readerBilingual"
            swapObjectName: "readerSwapSides"
            onModeRequested: function(value) { pane.changeStyle(function() { pane.reading.setReadingMode(value) }) }
            onSwapRequested: pane.changeStyle(function() { backend.swapReadingSides() })
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 6; spacing: 4
            Text {
                Layout.fillWidth: true
                text: pane.bilingual ? (backend.readingOriginalOnLeft ? qsTr("原文在左 · 译文在右") : qsTr("译文在左 · 原文在右"))
                                     : pane.flowMode === "original" ? qsTr("原文阅读") : qsTr("译文阅读")
                color: pane.muted; font.pixelSize: 11; elide: Text.ElideRight
            }
            ReaderButton {
                objectName: "addBookmark"; text: pane.currentBookmarked ? qsTr("★ 已收藏") : qsTr("☆ 书签")
                enabled: pane.reading.readerModel.count > 0
                onClicked: { pane.reading.addBookmark(pane.visibleRow, ""); bookmarksPopup.open() }
            }
            ReaderButton { objectName: "readerBookmarks"; text: qsTr("%n 书签", "bookmark count", pane.bookmarkList.length); onClicked: bookmarksPopup.open() }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.topMargin: 9; Layout.bottomMargin: 9; spacing: 3
            TextField {
                id: searchInput
                objectName: "readerSearch"; Layout.fillWidth: true; implicitHeight: 32
                placeholderText: pane.reading.readingExternal ? qsTr("在本书中搜索") : qsTr("在已译文中搜索"); placeholderTextColor: pane.muted; color: pane.ink
                font.family: "PingFang SC"; font.pixelSize: 12; selectByMouse: true
                onTextEdited: searchDelay.restart()
                onAccepted: { searchDelay.stop(); pane.jump(pane.reading.readerModel.search(text)) }
                background: Rectangle { radius: 6; color: pane.night ? "#28362B" : "#F0F1E6" }
            }
            Text { text: pane.reading.readerModel.searchSummary; color: pane.muted; font.pixelSize: 10 }
            ReaderButton { text: "↑"; enabled: searchInput.text.length > 0; onClicked: pane.jump(pane.reading.readerModel.nextMatch(-1)) }
            ReaderButton { text: "↓"; enabled: searchInput.text.length > 0; onClicked: pane.jump(pane.reading.readerModel.nextMatch(1)) }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                id: bookList
                objectName: "bookList"
                anchors.fill: parent
                anchors.leftMargin: Math.max(25, (parent.width - 730) / 2)
                anchors.rightMargin: Math.max(25, (parent.width - 730) / 2)
                topMargin: 28; bottomMargin: 42; clip: true; spacing: 17; cacheBuffer: 700
                model: pane.reading.readerModel
                boundsBehavior: Flickable.StopAtBounds; acceptedButtons: Qt.NoButton
                keyNavigationEnabled: true; activeFocusOnTab: true
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; onPressedChanged: if (pressed) pane.following = false }
                onMovementStarted: pane.following = false
                onContentYChanged: pane.updatePosition()
                WheelHandler { blocking: false; onWheel: function(event) { pane.following = false } }
                Keys.onPressed: function(event) {
                    if (pane.handleReadingKey(event)) event.accepted = true
                }
                delegate: Rectangle {
                    id: pair
                    required property string paragraphText
                    required property string sourceText
                    required property bool isHeading
                    required property int index
                    property int noteRevision: pane.reading.notesRevision
                    property var noteData: {
                        var revision = pair.noteRevision
                        return pane.reading.paragraphNoteAt(pair.index)
                    }
                    property bool marked: !!noteData.id
                    property bool showSource: pane.flowMode === "original" || pane.bilingual
                    property bool showTranslation: pane.flowMode !== "original"
                    width: bookList.width
                    visible: pane.flowMode !== "original" || pair.sourceText.length > 0
                    height: visible ? contentRow.height + 18 : 0
                    radius: 6
                    color: pair.marked ? pane.noteFill(pair.noteData.color) : "transparent"
                    HoverHandler { id: pairHover }
                    Row {
                        id: contentRow
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.leftMargin: 10; anchors.rightMargin: 30
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: pane.bilingual ? 22 : 0
                        layoutDirection: pane.bilingual && !backend.readingOriginalOnLeft ? Qt.RightToLeft : Qt.LeftToRight
                        property real pageWidth: width - (pane.bilingual ? spacing : 0)
                        height: Math.max(sourceColumn.visible ? sourceColumn.implicitHeight : 0,
                                         targetColumn.visible ? targetColumn.implicitHeight : 0)
                        Column {
                            id: sourceColumn
                            visible: pair.showSource
                            width: pane.bilingual ? contentRow.pageWidth / 2 : contentRow.pageWidth
                            spacing: 7
                            Text {
                                visible: pane.bilingual
                                text: pair.sourceText.length > 0 ? qsTr("原文") : qsTr("原文（同段续）")
                                color: pane.muted; font.pixelSize: 10; font.family: "PingFang SC"
                            }
                            TextEdit {
                                id: originalParagraph
                                objectName: "readerSource_" + pair.index
                                visible: pair.sourceText.length > 0
                                width: parent.width; height: visible ? contentHeight : 0
                                readOnly: true; selectByMouse: true; persistentSelection: true
                                textFormat: TextEdit.RichText
                                text: "<p style='line-height:165%;margin:0;white-space:pre-wrap;'>" + pane.escapeText(pair.sourceText) + "</p>"
                                wrapMode: TextEdit.Wrap
                                color: pane.muted; selectionColor: pane.night ? "#485451" : "#DDE3D5"; selectedTextColor: pane.ink
                                font.family: "Georgia"; font.pixelSize: Math.max(14, pane.fontSize - 2)
                                Accessible.name: qsTr("对应原文 %1").arg(pair.index + 1)
                                TapHandler { acceptedButtons: Qt.RightButton; onTapped: sourceMenu.popup() }
                                Menu { popupType: Popup.Window;
                                    id: sourceMenu
                                    objectName: "readerSourceMenu_" + pair.index
                                    MenuItem { text: qsTr("复制原文所选文字"); enabled: originalParagraph.selectedText.length > 0; onTriggered: originalParagraph.copy() }
                                    MenuItem { text: qsTr("问 AI…"); enabled: originalParagraph.selectedText.length > 0; onTriggered: backend.askAI.openSourceSelection(pane.reading.columnId, pair.index, originalParagraph.selectedText) }
                                    MenuItem { text: qsTr("标记本段与随笔…"); onTriggered: pane.openTextNote(pair.index, pair.paragraphText) }
                                }
                            }
                        }
                        Rectangle {
                            visible: pane.bilingual
                            width: visible ? 1 : 0; height: contentRow.height
                            color: pane.line; opacity: .72
                        }
                        Column {
                            id: targetColumn
                            visible: pair.showTranslation
                            width: pane.bilingual ? contentRow.pageWidth / 2 : contentRow.pageWidth
                            spacing: 7
                            Text { visible: pane.bilingual; text: qsTr("译文"); color: pane.muted; font.pixelSize: 10; font.family: "PingFang SC" }
                            TextEdit {
                                id: paragraph
                                objectName: "readerParagraph_" + pair.index
                                property int index: pair.index
                                width: parent.width; height: contentHeight
                                readOnly: true; selectByMouse: true; persistentSelection: true
                                textFormat: TextEdit.RichText
                                text: "<p style='line-height:" + Math.round(pane.readingLineHeight * 100) + "%;margin:0;white-space:pre-wrap;'>" + pane.escapeText(pair.paragraphText) + "</p>"
                                wrapMode: TextEdit.Wrap
                                color: pane.ink; selectionColor: pane.night ? "#485451" : "#DDE3D5"; selectedTextColor: pane.ink
                                font.family: backend.readerFontFamily; font.pixelSize: pane.fontSize + (pair.isHeading ? 4 : 0); font.weight: pair.isHeading ? Font.DemiBold : Font.Normal
                                topPadding: pair.isHeading ? 15 : 0; bottomPadding: pair.isHeading ? 8 : 0
                                Accessible.name: qsTr("译文段落 %1").arg(index + 1)
                                TapHandler { acceptedButtons: Qt.RightButton; onTapped: paragraphMenu.popup() }
                                Menu { popupType: Popup.Window;
                                    id: paragraphMenu
                                    objectName: "readerParagraphMenu_" + paragraph.index
                                    MenuItem { text: qsTr("复制所选文字"); enabled: paragraph.selectedText.length > 0; onTriggered: paragraph.copy() }
                                    MenuItem { objectName: "askAISelection_" + paragraph.index; text: qsTr("问 AI…"); enabled: paragraph.selectedText.length > 0; onTriggered: backend.askAI.openSelection(pane.reading.columnId, paragraph.index, paragraph.selectedText) }
                                    MenuItem { objectName: "marginNoteParagraph_" + paragraph.index; text: qsTr("标记本段与随笔…"); onTriggered: pane.openTextNote(paragraph.index, pair.paragraphText) }
                                    MenuItem {
                                        objectName: "annotateSelection_" + paragraph.index
                                        text: qsTr("标注并交给校对复查…")
                                        enabled: paragraph.selectedText.length > 0 && backend.postEditor.canAnnotate
                                        onTriggered: pane.flagText(paragraph.index, paragraph.selectionStart, paragraph.selectionEnd, paragraph.selectedText)
                                    }
                                }
                                Rectangle {
                                    visible: paragraph.selectedText.length > 0 && backend.postEditor.canAnnotate
                                    anchors.right: parent.right; anchors.top: parent.top; z: 2
                                    width: flagButton.implicitWidth; height: 30; radius: 6
                                    color: pane.night ? "#394245" : "#E7E8DC"
                                    ReaderButton {
                                        id: flagButton; objectName: "flagSelection_" + paragraph.index
                                        text: qsTr("标注…")
                                        onClicked: pane.flagText(paragraph.index, paragraph.selectionStart, paragraph.selectionEnd, paragraph.selectedText)
                                    }
                                }
                            }
                        }
                    }
                    Button {
                        id: noteRail
                        objectName: "paragraphNoteMarker_" + pair.index
                        anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
                        width: 24; height: 42
                        padding: 0; hoverEnabled: true
                        contentItem: Item {}
                        background: Rectangle {
                            anchors.centerIn: parent
                            width: pair.marked ? 9 : 5; height: pair.marked ? 34 : 26
                            radius: width / 2
                            color: pair.marked ? pane.noteDot(pair.noteData.color) : pane.muted
                            opacity: pair.marked ? .92 : pairHover.hovered || noteRail.hovered ? .32 : .08
                        }
                        Accessible.name: pair.marked ? qsTr("打开这段的页边随笔") : qsTr("标记本段")
                        ToolTip.visible: noteRail.hovered
                        ToolTip.text: pair.marked ? qsTr("打开这段的页边随笔") : qsTr("标记本段")
                        onClicked: pane.openTextNote(pair.index, pair.paragraphText)
                    }
                }
            }
            ColumnLayout {
                visible: pane.reading.readerModel.count === 0
                anchors.centerIn: parent; width: Math.min(280, parent.width - 60); spacing: 16
                Image { source: "../assets/HyTranslator.png"; Layout.preferredWidth: 76; Layout.preferredHeight: 76; Layout.alignment: Qt.AlignHCenter; opacity: .8; mipmap: true }
                Text { text: qsTr("译一页，读一页。"); color: pane.ink; font.family: "Songti SC"; font.pixelSize: 24; Layout.alignment: Qt.AlignHCenter }
                Text { Layout.fillWidth: true; text: pane.reading.readingExternal ? qsTr("这个文本文件是空的。\n可以打开另一本 TXT 阅读。") : qsTr("已保存的译文会出现在这里。\n也可以直接打开任意 TXT 阅读。"); color: pane.muted; font.family: "PingFang SC"; font.pixelSize: 12; lineHeight: 1.6; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
                ReaderButton { text: qsTr("打开 TXT 阅读…"); Layout.alignment: Qt.AlignHCenter; onClicked: pane.reading.pickReadingFile() }
            }
            Rectangle {
                objectName: "txtReadingBrightnessVeil"
                anchors.fill: parent; z: 10; enabled: false
                visible: Math.abs(pane.readingBrightness - 1.0) > 0.001
                color: pane.readingBrightness > 1.0 ? "#FFFFFF" : "#000000"
                opacity: pane.readingBrightness > 1.0
                         ? (pane.readingBrightness - 1.0) * 0.7
                         : 1.0 - pane.readingBrightness
            }
        }
        Text { Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; visible: pane.reading.readerModel.error.length > 0; text: pane.reading.readerModel.error; color: "#AC7854"; font.pixelSize: 11; wrapMode: Text.Wrap }
        Text { Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; visible: pane.reading.readerBilingual && pane.reading.bilingualMessage.length > 0; text: pane.reading.bilingualMessage; color: pane.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
        Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
        ColumnLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 82; Layout.leftMargin: 17; Layout.rightMargin: 17; spacing: 1
            RowLayout {
                Layout.fillWidth: true; spacing: 1
                ReaderButton { objectName: "fontSmaller"; text: "A−"; enabled: pane.fontSize > 15; onClicked: pane.changeStyle(function() { backend.readerFontSize = pane.fontSize - 1 }) }
                ReaderButton { objectName: "fontLarger"; text: "A+"; enabled: pane.fontSize < 30; onClicked: pane.changeStyle(function() { backend.readerFontSize = pane.fontSize + 1 }) }
                ReaderButton {
                    objectName: "readerFontPicker"; text: qsTr("字体"); onClicked: fontMenu.open()
                    Menu { popupType: Popup.Window;
                        id: fontMenu
                        objectName: "readerFontMenu"
                        Instantiator {
                            model: backend.readerFonts
                            delegate: MenuItem {
                                required property var modelData
                                objectName: "fontChoice_" + modelData.family
                                text: modelData.label
                                font.family: modelData.family
                                checkable: true; checked: backend.readerFontFamily === modelData.family
                                onTriggered: pane.changeStyle(function() { backend.readerFontFamily = modelData.family })
                            }
                            onObjectAdded: function(index, object) { fontMenu.insertItem(index, object) }
                            onObjectRemoved: function(index, object) { fontMenu.removeItem(object) }
                        }
                    }
                }
                ReaderButton {
                    objectName: "readerTheme"; text: pane.night ? qsTr("日间") : qsTr("夜间")
                    onClicked: {
                        backend.backgroundId = pane.night ? "paper" : "night"
                    }
                }
                ReaderButton { objectName: "readerAppearanceButton"; text: qsTr("排版"); onClicked: pane.openAppearance(true) }
                ReaderButton { objectName: "readerToc"; text: qsTr("目录"); enabled: pane.chapterList.length > 0; onClicked: chaptersPopup.open() }
                Text { Layout.fillWidth: true; Layout.leftMargin: 7; text: pane.currentChapterTitle; color: pane.muted; font.family: "PingFang SC"; font.pixelSize: 10; elide: Text.ElideRight }
                ReaderButton { objectName: "followLatest"; text: (pane.following ? "✓ " : "") + (pane.width < 490 ? qsTr("最新") : qsTr("跟随最新")); enabled: pane.reading.readerModel.count > 0; onClicked: pane.latest() }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                ReaderButton { objectName: "previousChapter"; text: qsTr("上一章"); enabled: pane.currentChapterIndex > 0; onClicked: pane.jumpChapter(-1) }
                Slider {
                    id: readingProgress
                    objectName: "readingProgress"
                    Layout.fillWidth: true; implicitHeight: 25
                    from: 0; to: Math.max(1, pane.reading.readerModel.count - 1); stepSize: 1
                    value: pane.visibleRow; enabled: pane.reading.readerModel.count > 1
                    onMoved: pane.jump(Math.round(value))
                    background: Rectangle {
                        x: readingProgress.leftPadding
                        y: readingProgress.topPadding + readingProgress.availableHeight / 2 - height / 2
                        width: readingProgress.availableWidth; height: 3; radius: 2; color: pane.line
                        Rectangle { width: readingProgress.visualPosition * parent.width; height: parent.height; radius: 2; color: pane.accent }
                    }
                    handle: Rectangle {
                        x: readingProgress.leftPadding + readingProgress.visualPosition * (readingProgress.availableWidth - width)
                        y: readingProgress.topPadding + readingProgress.availableHeight / 2 - height / 2
                        implicitWidth: 12; implicitHeight: 12; radius: 6; color: pane.accent
                        border.width: 2; border.color: pane.color
                    }
                    ToolTip.visible: readingProgress.pressed
                    ToolTip.text: Math.round(pane.readingFraction * 100) + "%"
                }
                Text {
                    objectName: "readingProgressText"
                    text: pane.reading.readerModel.count ? Math.round(pane.readingFraction * 100) + "% · " + (pane.visibleRow + 1) + "/" + pane.reading.readerModel.count : qsTr("等待译文")
                    color: pane.muted; font.family: "PingFang SC"; font.pixelSize: 10
                }
                ReaderButton { objectName: "nextChapter"; text: qsTr("下一章"); enabled: pane.currentChapterIndex >= 0 && pane.currentChapterIndex < pane.chapterList.length - 1; onClicked: pane.jumpChapter(1) }
            }
        }
    }
    ReadingAppearancePopup {
        id: appearancePopup
        objectName: "readingAppearancePopup"
        host: pane
        onClosed: if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
    }
    ReadingMarginNote {
        id: marginNote
        host: pane
        z: 20
        x: pane.noteDockBottom ? 12 : parent.width - width - 12
        y: pane.noteDockBottom ? parent.height - height - 12 : 12
        width: pane.noteDockBottom ? Math.max(0, parent.width - 24) : pane.noteDockWidth
        height: pane.noteDockBottom ? pane.noteDockHeight : Math.max(0, parent.height - 24)
        onOpenedChanged: {
            // TXT delegates reflow when their available width changes.  Keep
            // the same logical paragraph at the top instead of preserving a
            // now-stale pixel offset.  EPUB panes retain their own fraction.
            if (!pane.isEpub) {
                pane.pendingJumpRow = pane.visibleRow
                deferredJump.restart()
            }
            if (!marginNote.opened && pane.focusMode) Qt.callLater(pane.focusReadingSurface)
        }
        onSaveRequested: function(colorId, content) {
            var identity = ""
            if (pane.marginNoteRow >= 0)
                identity = pane.reading.saveParagraphNote(pane.marginNoteRow, colorId, content)
            else
                identity = pane.reading.saveEpubParagraphNote(pane.marginNoteAnchor, colorId, content)
            if (identity) marginNote.showNote(pane.reading.paragraphNoteById(identity), marginNote.excerpt)
        }
        onRemoveRequested: function(identity) {
            if (identity) pane.reading.removeParagraphNote(identity)
            marginNote.close()
        }
    }
    Popup { popupType: Popup.Window;
        id: openBooksPopup; objectName: "openBooksPopup"
        anchors.centerIn: parent; width: Math.min(parent.width - 30, 430); height: Math.min(parent.height - 60, 500); modal: true
        onClosed: if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
        background: Rectangle { radius: 14; color: pane.color; border.color: pane.line }
        contentItem: ColumnLayout {
            Text { objectName: "openBooksPopupTitle"; text: pane.reading.empty ? qsTr("选择此栏内容") : qsTr("更换此栏内容"); color: pane.ink; font.pixelSize: 19; padding: 10 }
            ReaderButton { text: qsTr("打开本机 TXT…"); onClicked: { openBooksPopup.close(); pane.reading.pickReadingFile() } }
            Text { text: qsTr("从书架选择；其他并列阅读栏不会关闭"); color: pane.muted; font.pixelSize: 12 }
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; model: backend.bookshelf; clip: true
                ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    objectName: "openTabBook_" + index
                    width: ListView.view.width; enabled: modelData.available
                    contentItem: Text { text: modelData.title; color: pane.ink; elide: Text.ElideRight; font.pixelSize: 14 }
                    onClicked: { pane.reading.openShelfBookAsync(modelData.id); openBooksPopup.close() }
                }
            }
        }
    }
    Popup { popupType: Popup.Item;
        id: bookmarksPopup; objectName: "bookmarksPopup"
        anchors.centerIn: parent; width: Math.min(parent.width - 30, 440); height: Math.min(parent.height - 60, 490); modal: true
        onAboutToShow: pane.annotationEditorVisibilityChanged(true)
        onClosed: {
            pane.annotationEditorVisibilityChanged(false)
            if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
        }
        background: Rectangle { radius: 14; color: pane.color; border.color: pane.line }
        contentItem: ColumnLayout {
            RowLayout {
                Layout.fillWidth: true
                Text { objectName: "bookmarksPopupTitle"; text: qsTr("本书书签"); color: pane.ink; font.pixelSize: 19; padding: 8 }
                Item { Layout.fillWidth: true }
                ReaderButton { text: qsTr("关闭"); onClicked: bookmarksPopup.close() }
            }
            Text { Layout.fillWidth: true; text: pane.reading.readerTitle; color: pane.muted; font.pixelSize: 12; elide: Text.ElideRight }
            RowLayout {
                Layout.fillWidth: true
                TextField { id: bookmarkTitle; objectName: "bookmarkTitle"; Layout.fillWidth: true; placeholderText: qsTr("书签备注（可选）"); selectByMouse: true }
                ReaderButton { objectName: "saveBookmark"; text: qsTr("保存此处"); enabled: pane.reading.readerModel.count > 0; onClicked: { pane.reading.addBookmark(pane.visibleRow, bookmarkTitle.text); bookmarkTitle.clear() } }
            }
            Text { visible: pane.bookmarkList.length === 0; text: qsTr("还没有书签。保存当前段落，下次一键回到这里。"); color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12 }
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; model: pane.bookmarkList; clip: true; spacing: 5
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    ItemDelegate {
                        objectName: "bookmarkJump_" + parent.index
                        Layout.fillWidth: true; enabled: parent.modelData.position >= 0
                        contentItem: Column {
                            spacing: 4
                            Text { width: parent.width; text: modelData.title; textFormat: Text.PlainText; color: pane.ink; font.pixelSize: 13; elide: Text.ElideRight }
                            Text { text: modelData.position >= 0 ? qsTr("第 %1 阅读段 · %2").arg(modelData.position + 1).arg(modelData.created) : qsTr("原段落已改变，暂无法定位"); color: pane.muted; font.pixelSize: 10 }
                        }
                        onClicked: { pane.jump(parent.modelData.position); bookmarksPopup.close() }
                    }
                    ReaderButton { objectName: "removeBookmark_" + parent.index; text: qsTr("移除"); onClicked: pane.reading.removeBookmark(parent.modelData.id) }
                }
            }
            Text { text: qsTr("仅保存阅读位置；移除书签不会改动正文。"); color: pane.muted; font.pixelSize: 11 }
        }
    }
    Popup { popupType: Popup.Item;
        id: annotationPopup; objectName: "annotationPopup"
        anchors.centerIn: Overlay.overlay; modal: true; focus: true
        width: Math.min(520, pane.width - 30); padding: 22
        onAboutToShow: pane.annotationEditorVisibilityChanged(true)
        onOpened: Qt.callLater(function() { annotationNote.forceActiveFocus() })
        onClosed: {
            pane.annotationEditorVisibilityChanged(false)
            if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
        }
        background: Rectangle { color: pane.color; radius: 8; border.color: pane.line }
        contentItem: ColumnLayout {
            spacing: 12
            Text { objectName: "annotationTitle"; text: qsTr("标注这处译文"); color: pane.ink; font.pixelSize: 20; font.family: "Songti SC" }
            Text { Layout.fillWidth: true; text: pane.annotationQuote; color: pane.ink; wrapMode: Text.Wrap; maximumLineCount: 5; elide: Text.ElideRight; textFormat: Text.PlainText }
            ComboBox { id: annotationCategory; objectName: "annotationCategory"; Layout.fillWidth: true
                       model: backend.postEditor.categoryOptions
                       textRole: "text"; valueRole: "value" }
            TextArea {
                id: annotationNote; objectName: "annotationNote"; Layout.fillWidth: true; implicitHeight: 95
                placeholderText: qsTr("写下你的疑问或建议（可选）"); wrapMode: TextEdit.Wrap; selectByMouse: true
                activeFocusOnTab: true; color: pane.ink; placeholderTextColor: pane.muted
                selectionColor: pane.night ? "#4B5552" : "#DDE9DE"; selectedTextColor: pane.ink
                font.family: "PingFang SC"; font.pixelSize: 13; padding: 11
                background: Rectangle {
                    radius: 8; color: pane.night ? "#253033" : "#FFFEF9"
                    border.color: annotationNote.activeFocus ? pane.accent : pane.line
                }
            }
            Text { Layout.fillWidth: true; text: backend.postEditor.annotationMessage; color: pane.muted; font.pixelSize: 12; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; visible: backend.postEditor.annotationMessageDetail.length > 0; text: backend.postEditor.annotationMessageDetail; color: pane.muted; font.pixelSize: 11; wrapMode: Text.Wrap }
            RowLayout {
                Item { Layout.fillWidth: true }
                ReaderButton { text: qsTr("取消"); onClicked: annotationPopup.close() }
                ReaderButton {
                    objectName: "saveAndReviewAnnotationButton"; text: qsTr("保存并校阅")
                    onClicked: {
                        pane.reading.activate()
                        if (backend.postEditor.addAnnotation(pane.annotationRow, pane.annotationStart, pane.annotationEnd,
                                                            pane.annotationQuote, annotationNote.text, annotationCategory.currentValue)) {
                            annotationPopup.close()
                            pane.openAnnotations()
                        }
                    }
                }
                ReaderButton {
                    objectName: "saveAnnotationButton"; text: qsTr("保存标注")
                    onClicked: {
                        if (backend.postEditor.addAnnotation(pane.annotationRow, pane.annotationStart, pane.annotationEnd,
                                                            pane.annotationQuote, annotationNote.text, annotationCategory.currentValue)) annotationPopup.close()
                    }
                }
            }
        }
    }
    Popup { popupType: Popup.Window;
        id: chaptersPopup
        objectName: "chaptersPopup"
        anchors.centerIn: parent; width: Math.min(parent.width - 40, 400); height: Math.min(parent.height - 80, 520); modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: if (pane.currentChapterIndex >= 0) chapterView.positionViewAtIndex(pane.currentChapterIndex, ListView.Center)
        onClosed: if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
        background: Rectangle { radius: 14; color: pane.color; border.color: pane.line }
        contentItem: ColumnLayout {
            Text { objectName: "chaptersPopupTitle"; text: (pane.reading.readingExternal ? qsTr("章节目录") : qsTr("已译章节")) + " · " + pane.chapterList.length; color: pane.ink; font.pixelSize: 17; padding: 8 }
            ListView {
                id: chapterView
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: pane.chapterList
                ScrollBar.vertical: ScrollBar {}
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    objectName: "chapterItem_" + index
                    width: ListView.view.width
                    contentItem: Text { text: modelData.title; color: pane.ink; font.family: "PingFang SC"; font.pixelSize: 13; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { radius: 7; color: index === pane.currentChapterIndex ? (pane.night ? "#394245" : "#E7E8DC") : "transparent" }
                    onClicked: { pane.jump(modelData.position); chaptersPopup.close() }
                }
            }
        }
    }
    Connections {
        // macOS clears the active focus item when the window is deactivated.
        // Coming back to an immersive reader must not leave the keyboard dead,
        // but a user who left focus in a toolbar field keeps their caret.
        target: pane.Window.window
        function onActiveChanged() {
            if (pane.Window.window && pane.Window.window.active && pane.focusMode) activationFocus.restart()
        }
    }
    Timer {
        // Deferred one turn: Qt may restore the previously focused item (for
        // example the search field) as the window becomes active again.
        id: activationFocus; interval: 0
        onTriggered: {
            if (pane.focusMode && pane.Window.window && pane.Window.window.active && !pane.Window.activeFocusItem)
                pane.focusReadingSurface()
        }
    }
    Timer {
        // A closing Popup.Window can leave the active main window without an
        // active focus item, so retry briefly without activating the app.
        id: readingFocusRetry; interval: 60; repeat: true
        property int attempts: 0
        onRunningChanged: if (running) attempts = 0
        onTriggered: {
            if (!pane.focusMode || pane.isEpub || !pane.activeColumn) { stop(); return }
            var win = pane.Window.window
            if (!win || !win.active) { stop(); return }
            // Stop as soon as anybody owns the keyboard: the reader must never
            // fight a field or popup the user just focused.
            if (pane.focusWithin(bookList) || pane.Window.activeFocusItem) { stop(); return }
            attempts += 1
            if (attempts > 8) { stop(); return }
            bookList.forceActiveFocus()
        }
    }
    Timer { id: searchDelay; interval: 300; onTriggered: pane.jump(pane.reading.readerModel.search(searchInput.text)) }
    Timer { id: savePosition; interval: 600; onTriggered: pane.reading.saveReadingPosition(pane.visibleRow) }
    Timer {
        id: deferredJump; interval: 0
        onTriggered: { pane.restoringReset = false; pane.jump(pane.pendingJumpRow) }
    }
    Timer { id: deferredLatest; interval: 0; onTriggered: bookList.positionViewAtEnd() }
    Connections {
        target: pane.reading
        function onReadingAboutToChange() {
            savePosition.stop()
            pane.reading.saveReadingPosition(pane.visibleRow)
            annotationPopup.close()
            bookmarksPopup.close()
            appearancePopup.close()
            marginNote.close()
            searchDelay.stop()
        }
    }
    Connections {
        target: pane.reading.readerModel
        function onResetStarting() {
            pane.resetAnchor = pane.visibleRow
            pane.restoringReset = true
        }
        function onUpdated(newDocument) {
            if (newDocument) {
                pane.following = false; searchInput.text = ""
                pane.pendingJumpRow = pane.reading.savedReadingPosition
                deferredJump.restart()
            }
            else if (pane.restoringReset) {
                pane.pendingJumpRow = pane.resetAnchor
                deferredJump.restart()
            }
            else if (pane.following) deferredLatest.restart()
        }
    }
    Component.onCompleted: {
        pane.loadEpub()
        pane.pendingJumpRow = pane.reading.savedReadingPosition
        deferredJump.restart()
        if (pane.focusMode) Qt.callLater(pane.focusReadingSurface)
    }
    Component.onDestruction: pane.reading.saveReadingPosition(pane.visibleRow)
}
