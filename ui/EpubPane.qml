import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "EpubAnnotations.js" as Notes
import "ReadingAppearance.js" as Appearance

Rectangle {
    id: pane
    objectName: "epubPane"
    required property var reading
    required property color ink
    required property color surface
    property var host: null
    property color muted: host ? host.muted : "#7F887F"
    property color line: host ? host.line : "#D8DDD6"
    property color accent: host ? host.accent : "#2E5A45"
    property bool night: host ? host.night : false
    property bool focusMode: host ? host.focusMode : false
    property string backgroundId: host ? host.backgroundId : "paper"
    property string effectiveBackgroundId: night ? "night" : backgroundId
    property real readingBrightness: host ? host.readingBrightness : 1.0
    property real readingLineHeight: translatedEdition && host ? host.readingLineHeight : 1.85
    property real epubLineHeight: readingLineHeight
    property color paper: night ? "#202729"
                                : backgroundId === "warm" ? "#F7EDDA"
                                : backgroundId === "mist" ? "#F1F5F5"
                                : "#FBF8EE"
    property color focusBackdrop: night ? "#182123"
                                        : backgroundId === "warm" ? "#EEE4D2"
                                        : backgroundId === "mist" ? "#E8EDEF"
                                        : "#EEE9DE"
    property color quietFill: night ? "#313B3D" : "#ECEDE5"
    property var session: reading.epubSession
    property var state: session.state
    property real progress: web.contentsSize.height > web.height
                            ? Math.max(0, Math.min(1, web.scrollPosition.y / (web.contentsSize.height - web.height)))
                            : 0
    property bool ready: false
    property bool translatedEdition: reading.epubTranslated
    property bool grouped: !!host && host.grouped === true
    property bool pairedReading: grouped ? host.epubPairVisible : typeof backend !== "undefined" && backend.epubPairNavigation.active && (!host || host.epubPairVisible)
    // Immersive keyboard paging: only the visible sheet of the active reading
    // column handles reading keys, and the in-window annotation editor keeps
    // the keyboard for its Chinese IME while it is open.
    readonly property bool readingKeys: pane.host ? pane.host.readingKeysEnabled === true : pane.focusMode
    readonly property bool surfaceVisible: !pane.host
                                           || (pane.reading === pane.host.reading ? pane.host.primaryVisible === true
                                                                                 : pane.host.peerVisible === true)
    property bool pendingReadingFocus: false
    property string selectedQuote: ""
    property string annotationError: ""
    property double annotationNoticeAt: 0
    property string dismissedReadError: ""
    property var selectionSnapshot: ({})
    property var paragraphSnapshot: ({})
    property string selectionUrl: ""
    property string selectionContext: ""
    property real resizeFraction: 0
    property int notesRevision: reading.notesRevision
    property string currentChapterTitle: state.chapters.length > 0 && state.chapter >= 0
                                         ? state.chapters[state.chapter].title : ""
    property string currentChapterPath: state.chapters.length > 0 && state.chapter >= 0
                                        ? state.chapters[state.chapter].path : ""
    color: focusMode ? focusBackdrop : surface

    function toggleImmersive() {
        reading.activate()
        if (grouped && !focusMode) host.mode(translatedEdition ? "translated" : "original")
        if (host) host.toggleFocus()
    }

    // True when the given item, or one of its descendants, owns the keyboard.
    function focusWithin(item) {
        var current = pane.Window.activeFocusItem
        while (current) {
            if (current === item) return true
            current = current.parentItem
        }
        return false
    }
    // Grouped panes defer to the workspace so the visible/primary sheet wins
    // instead of whichever sheet was created last.
    function takeReadingFocus() {
        if (pane.host) pane.host.takeReadingFocus()
        else pane.claimReadingFocus()
    }
    // Hand the keyboard to the native WKWebView so it receives real key events.
    // The chapter script may still be installing, hence pendingReadingFocus.
    function claimReadingFocus() {
        if (!pane.readingKeys || !pane.surfaceVisible) { pane.pendingReadingFocus = false; return }
        if (!web.ready) { pane.pendingReadingFocus = true; return }
        pane.pendingReadingFocus = false
        web.takeReadingFocus()
        if (!pane.focusWithin(web)) readingFocusRetry.restart()
    }
    onReadingKeysChanged: if (pane.readingKeys) Qt.callLater(pane.takeReadingFocus)
    onSurfaceVisibleChanged: if (pane.readingKeys && pane.surfaceVisible) Qt.callLater(pane.takeReadingFocus)
    Component.onCompleted: if (pane.readingKeys) Qt.callLater(pane.takeReadingFocus)

    function selectBackground(value) {
        if (host) host.setBackground(value)
    }

    function selectLineHeight(value) {
        if (host) host.setReadingLineHeight(value)
    }

    function flagSelection() {
        if (!host || !translatedEdition) return
        reading.activate()
        var requestedUrl = web.url.toString()
        web.runJavaScript(Notes.selectionScript(), 0, function(selection) {
            if (requestedUrl !== web.url.toString()) return
            pane.flagCapturedSelection(selection)
        })
    }

    function flagCapturedSelection(selection) {
        reading.activate()
        annotationNoticeAt = Date.now()
        if (!selection || !selection.quote) {
            var messages = {
                "empty": qsTr("还没有选中文字。请在译文里拖选一个词或一句话，再点“划线校阅”。"),
                "cross-paragraph": qsTr("这次选择跨了两个段落。请只选一个段落内的文字，分次提交校阅。"),
                "unmapped": qsTr("这处文字没有可核对的原文段落，暂不能提交校阅。可以复制或问 AI。"),
                "pending": qsTr("这一段还没翻译完成。请等本段出现译文后，再选词提交校阅。")
            }
            pane.annotationError = messages[selection ? selection.reason : "empty"] || messages.empty
            return
        }
        var anchor = backend.postEditor.locateEpubQuote(selection.source, selection.quote, selection.ids)
        pane.annotationError = backend.postEditor.annotationMessage
                + (backend.postEditor.annotationMessageDetail.length > 0
                   ? ": " + backend.postEditor.annotationMessageDetail : "")
        if (anchor.quote) host.flagText(anchor.row, anchor.start, anchor.end, anchor.quote)
    }

    function showSelectionTools(quote, px, py) {
        reading.activate()
        selectedQuote = quote
        selectionUrl = web.url.toString()
        selectionSnapshot = ({})
        paragraphSnapshot = ({})
        selectionContext = ""
        var expected = selectionUrl
        web.runJavaScript(Notes.paragraphScript(px, py, pane.translatedEdition, pane.currentChapterPath), 0, function(paragraphValue) {
            if (web.url.toString() !== expected || !pane.visible) return
            pane.paragraphSnapshot = paragraphValue || ({reason: "no-paragraph"})
            web.runJavaScript(Notes.selectionScript(), 0, function(value) {
                if (web.url.toString() !== expected || !pane.visible) return
                pane.selectionSnapshot = value || ({reason: "empty"})
                web.runJavaScript(Notes.contextScript(pane.translatedEdition, {
                    "current": qsTr("当前段"), "neighbor": qsTr("相邻原文段")
                }), 0, function(context) {
                    if (web.url.toString() !== expected || !pane.visible) return
                    pane.selectionContext = context || ""
                    var point = web.mapToItem(pane, px, py)
                    selectionMenu.x = Math.max(8, Math.min(point.x, pane.width - selectionMenu.width - 8))
                    selectionMenu.y = Math.max(8, Math.min(point.y + 8, pane.height - selectionMenu.height - 8))
                    selectionMenu.open()
                })
            })
        })
    }

    function paintNotes() {
        if (!host || !ready) return
        if (translatedEdition)
            web.runJavaScript(Notes.marksScript(backend.postEditor.epubMarks(reading.readingJobPath)), 0)
        web.runJavaScript(Notes.paragraphMarksScript(reading.paragraphNotes, translatedEdition, currentChapterPath,
            {"open": qsTr("打开这段的页边随笔"), "title": qsTr("页边随笔")}), 0)
    }
    function applyPaper(preservePosition) {
        if (!web.ready) return
        var heldFraction = progress
        var spacing = Number(epubLineHeight).toFixed(2)
        var brightness = Math.max(0.6, Math.min(1.1, Number(readingBrightness)))
        var primaryInk = night ? "#DBDED5" : ink.toString()
        web.runJavaScript(Appearance.script(
            paper.toString(), primaryInk, muted.toString(), accent.toString(),
            spacing, brightness, state.fixed, preservePosition === true, heldFraction), 0)
    }
    onPaperChanged: applyPaper(false)
    onInkChanged: applyPaper(false)
    onReadingBrightnessChanged: applyPaper(false)
    onEpubLineHeightChanged: applyPaper(true)
    onWidthChanged: if (ready) { resizeFraction = progress; resizeRestore.restart() }
    onHeightChanged: if (ready) { resizeFraction = progress; resizeRestore.restart() }
    onNotesRevisionChanged: paintNotes()
    onVisibleChanged: if (!visible) { selectionMenu.close(); if (host) host.closeAppearance() }
    Connections {
        target: pane.host ? backend.postEditor : null
        function onAnnotationsChanged(job) {
            if (job === pane.reading.readingJobPath) pane.paintNotes()
        }
    }

    component QuietButton: Button {
        id: control
        property bool selected: false
        implicitHeight: 32
        implicitWidth: Math.max(34, buttonLabel.implicitWidth + 18)
        hoverEnabled: true
        Accessible.name: text
        contentItem: Text {
            id: buttonLabel
            text: control.text
            color: control.enabled ? pane.ink : pane.muted
            font.family: "PingFang SC"
            font.pixelSize: 12
            font.weight: control.selected ? Font.Medium : Font.Normal
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: control.selected ? (pane.night ? "#3A4947" : "#E1E7DC")
                                    : control.hovered || control.down ? pane.quietFill : "transparent"
            border.width: control.activeFocus ? 1 : 0
            border.color: pane.accent
        }
    }

    Popup { popupType: Popup.Window;
        id: selectionMenu
        objectName: "epubSelectionTools"
        // Popup.Window negotiates its native size from implicit dimensions.
        // Explicit width alone is replaced by the content's implicit width.
        implicitWidth: 248; width: implicitWidth; padding: 10
        implicitHeight: selectionActions.implicitHeight + topPadding + bottomPadding
        height: implicitHeight
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: if (pane.focusMode) Qt.callLater(pane.takeReadingFocus)
        background: Rectangle { color: pane.paper; radius: 8; border.color: pane.line }
        contentItem: ColumnLayout {
            id: selectionActions
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                Text { Layout.fillWidth: true; text: pane.selectedQuote; elide: Text.ElideRight; maximumLineCount: 1; textFormat: Text.PlainText; color: pane.muted; font.pixelSize: 12 }
                QuietButton { objectName: "closeEpubSelectionTools"; text: qsTr("关闭"); onClicked: selectionMenu.close() }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
            QuietButton { objectName: "copyEpubSelection"; Layout.fillWidth: true; text: qsTr("复制"); enabled: pane.selectedQuote.length > 0; onClicked: { backend.copyReadingText(pane.selectedQuote); selectionMenu.close() } }
            QuietButton { objectName: "noteEpubParagraph"; Layout.fillWidth: true; text: qsTr("标记本段与随笔…"); enabled: !!pane.paragraphSnapshot.kind; onClicked: {
                selectionMenu.close()
                if (pane.selectionUrl === web.url.toString() && host) host.openEpubNote(pane.paragraphSnapshot)
            } }
            QuietButton { objectName: "reviewEpubSelection"; Layout.fillWidth: true; text: qsTr("划线校阅…"); enabled: pane.translatedEdition && pane.selectedQuote.length > 0; onClicked: {
                selectionMenu.close()
                if (pane.selectionUrl === web.url.toString()) pane.flagCapturedSelection(pane.selectionSnapshot)
            } }
            QuietButton { objectName: "askEpubSelection"; Layout.fillWidth: true; text: qsTr("问 AI…"); enabled: pane.selectedQuote.length > 0 && !!pane.paragraphSnapshot.kind && !!host; onClicked: {
                selectionMenu.close()
                if (pane.selectionUrl === web.url.toString())
                    backend.askAI.openAnchored(host.reading.columnId, pane.paragraphSnapshot,
                                               pane.selectedQuote, pane.selectionContext, pane.state.title)
            } }
        }
    }

    function go(chapter, fraction) {
        if (host) reading.activate()
        ready = false
        session.navigate(chapter, fraction)
        if (web.url.toString() === state.url) restore.restart()
    }

    function restorePosition() {
        if (web.url.toString().indexOf("#") < 0)
            web.runJavaScript("window.scrollTo(0, Math.max(0,document.documentElement.scrollHeight-window.innerHeight)*" + state.fraction + ")", 0)
        ready = true
        paintNotes()
    }

    Timer { id: restore; interval: 250; onTriggered: pane.restorePosition() }
    Timer {
        // Same bounded retry as the flow reader: a closing Popup.Window can
        // leave an active main window without a focused reading surface.
        id: readingFocusRetry; interval: 60; repeat: true
        property int attempts: 0
        onRunningChanged: if (running) attempts = 0
        onTriggered: {
            if (!pane.readingKeys || !pane.surfaceVisible || !web.ready) { stop(); return }
            var win = pane.Window.window
            if (!win || !win.active) { stop(); return }
            if (pane.focusWithin(web) || pane.Window.activeFocusItem) { stop(); return }
            attempts += 1
            if (attempts > 8) { stop(); return }
            web.takeReadingFocus()
        }
    }
    Timer {
        id: resizeRestore; interval: 180
        onTriggered: if (pane.ready) web.runJavaScript(
            "window.scrollTo(0, Math.max(0,document.documentElement.scrollHeight-window.innerHeight)*" + pane.resizeFraction + ")", 0)
    }
    Timer { id: save; interval: 250; onTriggered: if (pane.ready && pane.visible) pane.session.savePosition(pane.progress) }
    onProgressChanged: if (ready && visible) save.restart()
    Connections {
        target: pane.session
        function onWheelRequested(delta) {
            var requestedUrl = web.url.toString()
            web.runJavaScript("(function(){const e=document.scrollingElement;const h=Math.max(0,e.scrollHeight-window.innerHeight);if(!h)return -1;window.scrollTo({top:e.scrollTop+" + delta + ",behavior:'instant'});return e.scrollTop/h;})()", 0, function(fraction) {
                if (pane.ready && web.url.toString() === requestedUrl && typeof fraction === "number" && fraction >= 0)
                    backend.scrollEpubPair(reading.columnId, fraction)
            })
        }
        function onScrollRequested(fraction) {
            web.runJavaScript("window.scrollTo(0, Math.max(0,document.documentElement.scrollHeight-window.innerHeight)*" + fraction + ")", 0)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            objectName: "epubTopChrome"
            Layout.fillWidth: true
            Layout.preferredHeight: pane.focusMode ? 54 : 64
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: pane.focusMode ? 26 : 20
                anchors.rightMargin: pane.focusMode ? 22 : 16
                spacing: 6
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        visible: !pane.focusMode
                        text: pane.translatedEdition ? qsTr("译本阅读") : qsTr("原版阅读")
                        color: pane.muted
                        font.family: "PingFang SC"
                        font.pixelSize: 10
                        font.letterSpacing: 2
                    }
                    Text {
                        Layout.fillWidth: true
                        text: pane.state.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: pane.ink
                        font.family: "Songti SC"
                        font.pixelSize: pane.focusMode ? 16 : 18
                        font.weight: Font.DemiBold
                    }
                }
                QuietButton { visible: !pane.focusMode && !pane.grouped; text: qsTr("换书"); onClicked: reading.pickReadingFile() }
                QuietButton { visible: pane.translatedEdition; text: qsTr("划线校阅"); onClicked: pane.flagSelection() }
                QuietButton { visible: pane.translatedEdition; text: qsTr("标注"); onClicked: { reading.activate(); host.openAnnotations() } }
                QuietButton {
                    visible: pane.state.updateAvailable === true
                    text: qsTr("新译文 · 更新")
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("当前书页保持不动；翻节时自动载入新译文，也可点此更新本节。")
                    onClicked: { pane.ready = false; pane.session.updateChapter() }
                }
                QuietButton { visible: !pane.focusMode && !pane.grouped; text: qsTr("+ 栏"); onClicked: { reading.activate(); backend.addReadingColumn() } }
                QuietButton { visible: !pane.focusMode && !pane.grouped; text: qsTr("× 栏"); onClicked: backend.removeReadingColumn(reading.columnId) }
                QuietButton {
                    id: focusButton
                    objectName: "epubFocusButton"
                    visible: !pane.grouped
                    text: pane.focusMode ? qsTr("退出沉浸") : qsTr("沉浸阅读")
                    selected: pane.focusMode
                    onClicked: pane.toggleImmersive()
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: pane.line; opacity: pane.focusMode ? .55 : 1 }

        Item {
            objectName: "epubAuxToolbar"
            visible: !pane.focusMode
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? 50 : 0
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 6
                QuietButton { text: pane.pairedReading ? qsTr("本栏上一节") : qsTr("上一节"); enabled: pane.state.chapter > 0; onClicked: pane.go(pane.state.chapter - 1, 0) }
                ComboBox {
                    id: chapters
                    objectName: "epubChapters"
                    readonly property color menuTitleColor: pane.ink
                    Layout.fillWidth: true
                    implicitHeight: 34
                    model: pane.state.chapters
                    textRole: "title"
                    currentIndex: pane.state.chapter
                    onActivated: pane.go(currentIndex, 0)
                    contentItem: Text {
                        text: chapters.displayText
                        textFormat: Text.PlainText
                        color: pane.ink
                        font.family: "PingFang SC"
                        font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        leftPadding: 11
                        rightPadding: 30
                    }
                    background: Rectangle { radius: 6; color: pane.quietFill; border.width: chapters.activeFocus ? 1 : 0; border.color: pane.accent }
                    delegate: ItemDelegate {
                        required property var modelData
                        required property int index
                        width: chapters.popup.width - 16
                        height: 46
                        highlighted: index === chapters.currentIndex
                        background: Rectangle { radius: 4; color: parent.highlighted || parent.hovered ? pane.quietFill : "transparent" }
                        contentItem: RowLayout {
                            spacing: 12
                            Text { objectName: "epubChapterNumber"; text: String(index + 1).padStart(2, '0'); color: pane.muted; font.pixelSize: 10; Layout.preferredWidth: 23 }
                            Text { objectName: "epubChapterTitle"; Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; color: chapters.menuTitleColor; font.family: "Songti SC"; font.pixelSize: 14; elide: Text.ElideRight }
                            Text { objectName: "epubChapterState"; text: modelData.translationState || ""; color: pane.muted; font.pixelSize: 10 }
                        }
                    }
                    popup: Popup { objectName: "epubChaptersPopup"; popupType: Popup.Window;
                        y: chapters.height + 8
                        width: Math.max(chapters.width, Math.min(400, pane.width - 32))
                        height: Math.min(390, chapterMenu.contentHeight + 16)
                        padding: 8
                        background: Rectangle { color: pane.paper; radius: 8; border.color: pane.line }
                        contentItem: ListView {
                            id: chapterMenu
                            clip: true
                            model: chapters.popup.visible ? chapters.delegateModel : null
                            currentIndex: chapters.currentIndex
                            onCountChanged: if (count) positionViewAtIndex(currentIndex, ListView.Contain)
                            ScrollBar.vertical: ScrollBar {}
                        }
                    }
                }
                QuietButton { text: pane.pairedReading ? qsTr("本栏下一节") : qsTr("下一节"); enabled: pane.state.chapter + 1 < pane.state.chapters.length; onClicked: pane.go(pane.state.chapter + 1, 0) }
                QuietButton {
                    objectName: "epubBookmarksButton"
                    text: qsTr("%n 书签", "bookmark count", pane.state.marks.length)
                    onClicked: bookmarks.open()
                }
            }
        }

        Rectangle { visible: !pane.focusMode; Layout.fillWidth: true; Layout.preferredHeight: 1; color: pane.line }

        RowLayout {
            objectName: "epubNotice"
            visible: (pane.state.error.length > 0 && pane.state.error !== pane.dismissedReadError) || pane.annotationError.length > 0
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            Layout.topMargin: 9
            Layout.bottomMargin: 9
            Text {
                Layout.fillWidth: true
                text: (pane.state.error !== pane.dismissedReadError ? pane.state.error : "") || pane.annotationError
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: "#A66D4D"
                font.family: "PingFang SC"
                font.pixelSize: 12
            }
            QuietButton { objectName: "dismissEpubNotice"; text: qsTr("关闭"); onClicked: { pane.annotationError = ""; pane.dismissedReadError = pane.state.error } }
        }

        Item {
            id: pageWell
            Layout.fillWidth: true
            Layout.fillHeight: true
            Rectangle {
                id: readingSheet
                objectName: "epubReadingSheet"
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.topMargin: pane.focusMode ? 18 : 0
                anchors.bottomMargin: pane.focusMode ? 14 : 0
                width: pane.focusMode && !pane.state.fixed
                       ? Math.min(parent.width - 48, 1120) : parent.width
                color: pane.paper
                radius: pane.focusMode ? 1 : 0
                border.width: pane.focusMode ? 1 : 0
                border.color: pane.line
                clip: true
                NativeBookView {
                    id: web
                    objectName: "epubWebView"
                    property string readingColumnId: pane.host ? reading.columnId : ""
                    property bool scrollReady: pane.ready
                    anchors.fill: parent
                    anchors.margins: readingSheet.border.width
                    url: pane.state.url
                    onUrlChanged: { pane.ready = false; pane.annotationError = ""; pane.dismissedReadError = ""; selectionMenu.close() }
                    linked: pane.pairedReading && backend.epubScrollLinked
                    readingKeys: pane.readingKeys
                    onEscapeRequested: if (pane.focusMode && host) host.toggleFocus()
                    onInteracted: reading.activate()
                    onUserScrolled: function(fraction) { backend.scrollEpubPair(reading.columnId, fraction) }
                    onSelectionChanged: function(at) { if (at > pane.annotationNoticeAt) pane.annotationError = "" }
                    onContextRequested: function(quote, px, py) {
                        pane.showSelectionTools(quote, px, py)
                    }
                    onNoteRequested: function(identity, px, py) { if (host) host.openExistingEpubNote(identity) }
                    onLoaded: {
                        pane.annotationError = ""
                        pane.applyPaper(false)
                        restore.restart()
                        pane.session.releasePreviousCache()
                        if (pane.pendingReadingFocus) pane.takeReadingFocus()
                    }
                    onFailed: function(message) { pane.annotationError = message }
                    onLinkRequested: function(url) {
                        if (url.startsWith(pane.state.root) && pane.state.root && url !== pane.state.url) {
                            pane.ready = false
                            pane.session.openLink(url)
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: pane.line; opacity: pane.focusMode ? .55 : 1 }

        Item {
            objectName: "epubFooter"
            Layout.fillWidth: true
            Layout.preferredHeight: pane.focusMode ? 48 : 58
            RowLayout {
                visible: !pane.focusMode
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 5
                QuietButton { text: "A−"; onClicked: web.zoomFactor = Math.max(.5, web.zoomFactor - .1) }
                QuietButton { text: "A+"; onClicked: web.zoomFactor = Math.min(3, web.zoomFactor + .1) }
                QuietButton { text: qsTr("原始比例"); onClicked: web.zoomFactor = 1 }
                QuietButton { objectName: "epubAppearanceButton"; text: qsTr("排版"); onClicked: if (host) host.openAppearance(false) }
                QuietButton {
                    objectName: "epubAddBookmark"
                    text: qsTr("添加书签")
                    onClicked: {
                        pane.session.bookmark(pane.progress)
                        bookmarks.open()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("%1 · 离线 · %2/%3 节 · %4%").arg(pane.translatedEdition ? qsTr("EPUB 译本") : qsTr("EPUB 原版")).arg(pane.state.chapter + 1).arg(pane.state.chapters.length).arg(Math.round(pane.progress * 100))
                    color: pane.muted
                    font.family: "PingFang SC"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
                QuietButton { visible: pane.translatedEdition; text: qsTr("原文对照"); onClicked: { reading.activate(); backend.openEpubOriginal(reading.readingJobPath) } }
                QuietButton { visible: !pane.translatedEdition; text: qsTr("翻译此书…"); onClicked: translationNotice.open() }
            }
            RowLayout {
                visible: pane.focusMode
                anchors.fill: parent
                anchors.leftMargin: 24
                anchors.rightMargin: 24
                spacing: 8
                QuietButton { text: pane.pairedReading ? qsTr("‹ 本栏上一节") : qsTr("‹ 上一节"); enabled: pane.state.chapter > 0; onClicked: pane.go(pane.state.chapter - 1, 0) }
                Text {
                    Layout.fillWidth: true
                    text: pane.currentChapterTitle + "  ·  " + Math.round(pane.progress * 100) + "%"
                    textFormat: Text.PlainText
                    color: pane.muted
                    font.family: "Songti SC"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }
                QuietButton { text: pane.pairedReading ? qsTr("本栏下一节 ›") : qsTr("下一节 ›"); enabled: pane.state.chapter + 1 < pane.state.chapters.length; onClicked: pane.go(pane.state.chapter + 1, 0) }
                QuietButton { objectName: "epubFocusAppearanceButton"; text: qsTr("排版"); onClicked: if (host) host.openAppearance(false) }
            }
        }
    }

    Dialog { popupType: Popup.Window;
        id: translationNotice
        objectName: "epubTranslationNotice"
        title: qsTr("现有翻译流程")
        modal: true
        anchors.centerIn: parent
        width: Math.min(460, pane.width - 30)
        height: 220
        footer: DialogButtonBox {
            Button { objectName: "epubTranslationCancel"; text: qsTr("取消"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button { objectName: "epubTranslationConfirm"; text: qsTr("确定"); DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
        onClosed: if (pane.focusMode) Qt.callLater(pane.takeReadingFocus)
        Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: qsTr("生成保留章节、图片与书内样式的 EPUB 译本，并在同一文件夹保存 TXT 副本。已译部分可直接阅读，剩余部分保留原文。准备翻译这本书？")
        }
        onAccepted: backend.prepareEpubTranslation(reading.readingPath)
    }

    Dialog { popupType: Popup.Window;
        id: bookmarks
        objectName: "epubBookmarksPopup"
        title: qsTr("本书书签")
        modal: true
        anchors.centerIn: parent
        // Popup.Window negotiates its native window from implicit dimensions.
        // Keep both contracts explicit so the list cannot collapse to the
        // standard Close button on macOS.
        implicitWidth: Math.max(300, Math.min(460, pane.width - 30))
        width: implicitWidth
        implicitHeight: Math.max(240, Math.min(420, pane.height - 30))
        height: implicitHeight
        padding: 12
        footer: DialogButtonBox {
            Button { objectName: "epubBookmarksClose"; text: qsTr("关闭"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        }
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: if (pane.focusMode) Qt.callLater(pane.takeReadingFocus)
        contentItem: ColumnLayout {
            spacing: 8
            Text {
                objectName: "epubBookmarksEmpty"
                visible: bookmarkList.count === 0
                Layout.fillWidth: true
                text: qsTr("还没有书签。点击“添加书签”保存当前章节和位置。")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: pane.muted
                font.family: "PingFang SC"
                font.pixelSize: 13
            }
            ListView {
                id: bookmarkList
                objectName: "epubBookmarkList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: pane.state.marks
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    required property var modelData
                    required property int index
                    objectName: "epubBookmarkRow_" + index
                    width: ListView.view.width
                    height: Math.max(42, implicitHeight)
                    Button {
                        id: bookmarkJump
                        objectName: "epubBookmarkJump_" + parent.index
                        Layout.fillWidth: true
                        text: parent.modelData.title
                        contentItem: Text {
                            text: bookmarkJump.text
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: {
                            pane.go(parent.modelData.chapter, parent.modelData.fraction)
                            bookmarks.close()
                        }
                    }
                    Button {
                        id: removeBookmark
                        objectName: "epubRemoveBookmark_" + parent.index
                        text: qsTr("移除")
                        onClicked: pane.session.removeBookmark(parent.index)
                    }
                }
            }
        }
    }
}
