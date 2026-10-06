import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: window
    readonly property var modelBackend: backend
    objectName: "hyWindow"
    width: 1500; height: 880
    minimumWidth: readingOnly ? 600 : 1000; minimumHeight: 720
    visible: true
    title: "鲸读"
    AskDialog { id: askDialog; objectName: "askDialog"; assistant: backend.askAI; parent: Overlay.overlay; onSettingsRequested: settingsPopup.open() }
    color: canvas
    property bool readerVisible: true
    property bool readingOnly: false
    // An in-window popup cannot cover macOS native WebViews. Track the popup
    // owner globally so every visible EPUB surface gets out of the way, even
    // when the editor was opened from another reading column.
    property string annotationEditorOwnerColumnId: ""
    readonly property bool nativeEpubViewsHidden: annotationEditorOwnerColumnId.length > 0
        || settingsPopup.visible || welcomePopup.visible || legalPopup.visible
        || reviewPopup.visible || askDialog.visible
    property string immersiveColumnId: ""
    property bool immersiveParallel: false
    property bool soloEpub: readingOnly && immersiveColumnId !== "" && !immersiveParallel
    property var visibleColumnIds: {
        var ids = backend.readingColumns.map(function(c) { return c.columnId })
        if (readingOnly && immersiveColumnId !== "" && ids.indexOf(immersiveColumnId) >= 0) {
            ids = [immersiveColumnId]
        }
        return ids.sort(function(a, b) { return backend.epubColumnOrder[a] - backend.epubColumnOrder[b] })
    }
    property bool researchMode: false
    property bool styleExpanded: false
    property bool darkCanvas: backend.backgroundId === "night"
    property color accent: backend.themeId === "ocean" ? "#315E78" : backend.themeId === "amber" ? "#805B37" : backend.themeId === "sakura" ? "#7C4859" : "#2E5A45"
    property color accentSoft: backend.themeId === "ocean" ? "#DCE9EF" : backend.themeId === "amber" ? "#EEE2D2" : backend.themeId === "sakura" ? "#F0DFE3" : "#DDE9DE"
    property color canvas: darkCanvas ? "#1E2527" : backend.backgroundId === "warm" ? "#EEE9DE" : backend.backgroundId === "mist" ? "#EDF1F2" : "#F3F1EB"
    property color surface: darkCanvas ? "#293134" : backend.backgroundId === "warm" ? "#FAF7EF" : backend.backgroundId === "mist" ? "#F8FAFA" : "#FEFDF9"
    property color ink: darkCanvas ? "#E8E4D8" : "#253E34"
    property color muted: darkCanvas ? "#A8B0AE" : "#7C857C"
    property color line: darkCanvas ? "#424B4D" : "#D8DDD6"
    property string uiFont: "PingFang SC"
    function openReview(showAnnotations) {
        if (!backend.manageReadingBook()) return
        backend.postEditor.load()
        if (showAnnotations) reviewPopup.showAnnotations()
        else reviewPopup.open()
    }
    function setImmersive(value, columnId) {
        window.readerVisible = true
        window.immersiveColumnId = value ? (columnId || "") : ""
        window.immersiveParallel = false
        window.readingOnly = value
        readerViewport.contentX = 0
    }
    onClosing: { backend.shutdown(); Qt.quit() }
    Shortcut { sequences: [StandardKey.Close]; onActivated: window.close() }
    Shortcut { sequence: "Esc"; context: Qt.ApplicationShortcut; enabled: window.readingOnly && !reviewPopup.opened; onActivated: window.setImmersive(false) }

    component Action: Button {
        id: control
        property bool primary: false
        property bool subtle: false
        implicitHeight: 38
        implicitWidth: Math.max(38, label.implicitWidth + 26)
        hoverEnabled: true
        Accessible.name: text
        contentItem: Text {
            id: label
            text: control.text
            color: !control.enabled ? "#92998F" : control.primary ? "#FFFEF4" : window.ink
            font.family: window.uiFont; font.pixelSize: 13; font.weight: Font.Medium
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 9
            color: !control.enabled ? (window.darkCanvas ? "#394144" : "#E4E7DE") : control.primary ? (control.down ? Qt.darker(window.accent, 1.25) : control.hovered ? Qt.lighter(window.accent, 1.18) : window.accent) : control.hovered ? (window.darkCanvas ? "#343E40" : window.accentSoft) : "transparent"
            border.width: control.subtle || control.primary ? 0 : 1
            border.color: control.activeFocus ? window.ink : window.line
        }
    }
    component Caption: Text { color: window.muted; font.family: window.uiFont; font.pixelSize: 12; wrapMode: Text.Wrap }
    component EngineChoice: Rectangle {
        id: engineCard
        property string profileId
        property string titleText
        property string detailText
        property string controlName
        Layout.fillWidth: true; implicitHeight: 72; radius: 12
        color: backend.translationProfile === profileId ? window.accentSoft : window.surface
        border.width: backend.translationProfile === profileId ? 2 : 1
        border.color: backend.translationProfile === profileId ? window.accent : window.line
        opacity: enabled ? 1 : .68
        enabled: !backend.running && !backend.taskLocked
        RowLayout {
            anchors.fill: parent; anchors.margins: 13; spacing: 10
            RadioButton {
                objectName: engineCard.controlName
                checked: backend.translationProfile === engineCard.profileId
                Accessible.name: engineCard.titleText
                palette.windowText: window.ink; palette.text: window.ink; palette.highlight: window.accent
                onClicked: backend.translationProfile = engineCard.profileId
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Text { Layout.fillWidth: true; text: engineCard.titleText; color: window.ink; font.family: window.uiFont; font.pixelSize: 14; font.weight: Font.DemiBold }
                Caption { Layout.fillWidth: true; text: engineCard.detailText; font.pixelSize: 11 }
            }
        }
        TapHandler { enabled: engineCard.enabled; onTapped: backend.translationProfile = engineCard.profileId }
    }

    ColumnLayout {
        anchors.fill: parent; spacing: 0
        Rectangle {
            objectName: "appHeader"
            visible: !window.readingOnly
            Layout.fillWidth: true; Layout.preferredHeight: 84; color: window.surface
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 28; anchors.rightMargin: 28; spacing: 12
                Image { source: "../assets/HyTranslator.png"; Layout.preferredWidth: 54; Layout.preferredHeight: 54; mipmap: true }
                ColumnLayout {
                    spacing: 2
                    Text { text: "鲸 读"; color: window.ink; font.family: "Songti SC"; font.pixelSize: 30; font.weight: Font.DemiBold }
                    Caption { text: qsTr("让故事跨越语言"); font.letterSpacing: 2 }
                }
                Item { Layout.fillWidth: true }
                Text { visible: window.width > 1200; text: qsTr("语言之下，还有一个更大的世界。"); color: window.muted; font.family: "Songti SC"; font.pixelSize: 13; font.letterSpacing: 1; Layout.rightMargin: 26 }
                Action { objectName: "researchModeToggle"; text: window.researchMode ? qsTr("返回普通阅读") : qsTr("PDF · 科研"); subtle: true; onClicked: { window.researchMode = !window.researchMode; window.readingOnly = false } }
                RowLayout {
                    visible: false
                    spacing: 7
                    Rectangle { width: 6; height: 6; radius: 3; color: backend.modelReady ? window.accent : "#BA8255" }
                    Caption { text: backend.modelReady ? qsTr("%1 · 已就绪").arg(backend.modelDisplayName) : qsTr("%1 · 未就绪").arg(backend.modelDisplayName) }
                }
                Rectangle { width: 1; height: 23; color: window.line; Layout.leftMargin: 9; Layout.rightMargin: 8 }
                Action {
                    objectName: "readerToggle"
                    visible: !window.researchMode
                    text: window.readerVisible ? qsTr("收起阅读栏") : qsTr("打开阅读栏"); subtle: true
                    onClicked: { window.readerVisible = !window.readerVisible; window.readingOnly = false }
                }
                Action { objectName: "settingsButton"; text: qsTr("设置"); subtle: true; onClicked: { backend.refreshModelStatus(); backend.refreshReaderCacheStats(); settingsPopup.open() } }
                Action { objectName: "reviewHeader"; visible: !window.researchMode; text: qsTr("译后校对"); subtle: true; onClicked: window.openReview(false) }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: window.line }
        }
        SplitView {
            visible: !window.researchMode
            Layout.fillWidth: true; Layout.fillHeight: true
            handle: Rectangle { implicitWidth: 3; color: SplitHandle.hovered ? window.accent : window.line }
            Item {
                objectName: "translationPanel"
                visible: !window.readingOnly
                SplitView.minimumWidth: 410; SplitView.preferredWidth: 470
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 18; spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: qsTr("我的书架"); color: window.ink; font.family: "Songti SC"; font.pixelSize: 23; font.weight: Font.DemiBold }
                            Caption { objectName: "shelfCount"; text: qsTr("%n 本", "bookshelf count", backend.bookshelf.length) }
                            Item { Layout.fillWidth: true }
                            Action { objectName: "addShelfBook"; text: qsTr("+ 添加图书"); subtle: true; implicitHeight: 30; enabled: !backend.running; onClicked: backend.pickShelfBook() }
                        }
                        ListView {
                            id: shelfList
                            objectName: "bookshelfList"
                            Layout.fillWidth: true; Layout.preferredHeight: 174
                            orientation: ListView.Horizontal; spacing: 9; clip: true
                            model: backend.bookshelf
                            function revealCurrent() {
                                for (var i = 0; i < count; ++i) {
                                    if (backend.bookshelf[i].current) {
                                        positionViewAtIndex(i, ListView.Contain)
                                        return
                                    }
                                }
                            }
                            Component.onCompleted: Qt.callLater(revealCurrent)
                            Connections { target: backend; function onShelfChanged() { Qt.callLater(shelfList.revealCurrent) } }
                            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                            delegate: Rectangle {
                                id: shelfCard
                                required property var modelData
                                required property int index
                                width: 145; height: 164; radius: 6
                                color: modelData.current ? (window.darkCanvas ? "#344640" : "#E6EBDF") : window.surface
                                border.color: modelData.opened ? window.accent : window.line
                                border.width: modelData.current ? 2 : modelData.opened ? 1.5 : 1
                                Rectangle { x: 0; y: 8; width: 4; height: parent.height - 16; radius: 2; color: window.accent; opacity: modelData.current ? 1 : modelData.opened ? .65 : .3 }
                                Button {
                                    objectName: "shelfBook_" + shelfCard.index
                                    anchors.fill: parent; anchors.rightMargin: 0
                                    enabled: shelfCard.modelData.available
                                    Accessible.name: qsTr("打开 %1").arg(shelfCard.modelData.title)
                                    background: Item {}
                                    contentItem: ColumnLayout {
                                        anchors.fill: parent; anchors.margins: 13; spacing: 6
                                        Rectangle {
                                            Layout.fillWidth: true; Layout.preferredHeight: 79; color: window.accent; radius: 2
                                            Image {
                                                anchors.fill: parent; source: shelfCard.modelData.cover || ""
                                                fillMode: Image.PreserveAspectFit; mipmap: true; asynchronous: true
                                                sourceSize.width: 116; sourceSize.height: 79
                                            }
                                            Text { visible: !shelfCard.modelData.cover; anchors.centerIn: parent; width: parent.width - 18; text: shelfCard.modelData.title; color: "#F2EBD7"; font.family: "Songti SC"; font.pixelSize: 15; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; maximumLineCount: 3; elide: Text.ElideRight }
                                        }
                                        Text { Layout.fillWidth: true; Layout.fillHeight: true; text: shelfCard.modelData.title; textFormat: Text.PlainText; color: window.ink; font.family: "Songti SC"; font.pixelSize: 16; font.weight: Font.DemiBold; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight }
                                        Caption { visible: !!shelfCard.modelData.author; text: shelfCard.modelData.author || ""; Layout.fillWidth: true; elide: Text.ElideRight; maximumLineCount: 1; font.pixelSize: 10 }
                                        Caption { text: shelfCard.modelData.available ? shelfCard.modelData.detail : qsTr("文件已移动"); font.pixelSize: 10 }
                                    }
                                    onClicked: backend.openShelfBookAsync(shelfCard.modelData.id)
                                }
                                Action {
                                    objectName: "removeShelfBook_" + shelfCard.index
                                    anchors.right: parent.right; anchors.top: parent.top
                                    implicitWidth: 28; implicitHeight: 28; text: "×"; subtle: true
                                    Accessible.name: qsTr("移出书架：") + shelfCard.modelData.title
                                    ToolTip.visible: hovered; ToolTip.text: qsTr("移出书架，保留所有文件")
                                    onClicked: backend.removeShelfBook(shelfCard.modelData.id)
                                }
                            }
                            Caption { anchors.centerIn: parent; visible: backend.bookshelf.length === 0; text: qsTr("把想读的书放在这里，随时接着上次的进度。") }
                        }
                        Caption { Layout.fillWidth: true; font.pixelSize: 10; text: qsTr("单击图书切换当前阅读栏 · 点 ＋ 栏增加并列阅读 · 移出书架保留全部文件") }
                        Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 3 }
                    }
                            Rectangle {
                                objectName: "sourceInfoCard"
                                Layout.fillWidth: true; implicitHeight: sourceInfoRow.implicitHeight + 28
                                Layout.minimumHeight: implicitHeight
                                radius: 8; color: window.surface; border.color: window.line
                                RowLayout {
                                    id: sourceInfoRow
                                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                                    Rectangle {
                                        Layout.preferredWidth: 46; Layout.preferredHeight: 72; Layout.alignment: Qt.AlignTop
                                        radius: 4; color: window.accent
                                        Rectangle { x: 6; width: 1; height: parent.height; color: "#90A184"; opacity: .6 }
                                        Text { anchors.centerIn: parent; text: backend.hasSource ? qsTr("原著") : qsTr("书"); horizontalAlignment: Text.AlignHCenter; color: "#EDE7CE"; font.family: "Songti SC"; font.pixelSize: 23; lineHeight: 1.35 }
                                        Rectangle { anchors.bottom: parent.bottom; anchors.bottomMargin: 9; anchors.horizontalCenter: parent.horizontalCenter; width: 25; height: 1; color: "#B3B68F" }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 7
                                        Text { objectName: "sourceFullTitle"; Layout.fillWidth: true; text: backend.hasSource ? backend.sourceName : qsTr("从一本书开始"); textFormat: Text.PlainText; color: window.ink; font.family: window.uiFont; font.pixelSize: 14; font.weight: Font.DemiBold; wrapMode: Text.Wrap }
                                        Caption { Layout.fillWidth: true; text: backend.hasSource ? backend.sourceInfo : qsTr("拖入原文，或点击选择文件"); font.pixelSize: 11; maximumLineCount: 2; elide: Text.ElideRight }
                                        RowLayout {
                                            Action {
                                                objectName: "sourceButton"; text: backend.hasSource ? qsTr("更换原文") : qsTr("选择原文")
                                                implicitHeight: 30; enabled: !backend.running
                                                onClicked: backend.pickSource()
                                            }
                                            Item { Layout.fillWidth: true }
                                        }
                                        Action {
                                            objectName: "languageDirectionButton"
                                            visible: backend.hasSource
                                            implicitHeight: 30; subtle: true
                                            Layout.fillWidth: true
                                            text: backend.languageLocked
                                                  ? qsTr("%1 · 已锁定").arg(backend.languageLabel)
                                                  : qsTr("%1 · 调整语言方向").arg(backend.languageLabel)
                                            Accessible.name: qsTr("翻译语言方向：%1").arg(backend.languageLabel)
                                            ToolTip.visible: hovered
                                            ToolTip.text: backend.languageLocked
                                                ? qsTr("已有断点的译本不能原地改方向，可新建另一译本")
                                                : qsTr("设置源语言与目标语言；卡片方向就是写入任务的方向")
                                            onClicked: languageDialog.openForLanguage()
                                        }
                                        Caption {
                                            visible: backend.hasSource && backend.languageNotice.length > 0
                                            Layout.fillWidth: true
                                            text: backend.languageNotice
                                            color: "#9A6B3F"; font.pixelSize: 11
                                            maximumLineCount: 3; elide: Text.ElideRight
                                        }
                                    }
                                }
                                DropArea {
                                    anchors.fill: parent; enabled: !backend.running
                                    onDropped: function(drop) {
                                        if (drop.hasUrls) {
                                            backend.acceptDrop(drop.urls[0])
                                        }
                                    }
                                }
                            }
                    ScrollView {
                        id: setupScroll
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true; contentWidth: availableWidth
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ColumnLayout {
                            width: setupScroll.availableWidth; spacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 10
                                RowLayout {
                                    Text { text: qsTr("先读故事，再斟酌译名"); font.family: window.uiFont; color: window.ink; font.pixelSize: 14; font.weight: Font.DemiBold }
                                    Item { Layout.fillWidth: true }
                                    Action { objectName: "reviewButton"; text: qsTr("译后校对"); subtle: true; implicitHeight: 28; onClicked: window.openReview(false) }
                                }
                                Rectangle {
                                    Layout.fillWidth: true; implicitHeight: glossaryCol.implicitHeight + 26
                                    radius: 12; color: backend.autoGlossary ? window.accentSoft : window.surface; border.color: backend.autoGlossary ? window.accent : window.line
                                    ColumnLayout {
                                        id: glossaryCol
                                        anchors.fill: parent; anchors.margins: 13; spacing: 5
                                        CheckBox {
                                            objectName: "reviewAfterCheck"; text: qsTr("翻译完成后准备校订"); checked: backend.postEditor.reviewAfter
                                            enabled: !backend.postEditor.busy && backend.postEditor.capabilitySupported
                                            Accessible.name: text
                                            ToolTip.visible: hovered && !backend.postEditor.capabilitySupported
                                            ToolTip.text: backend.postEditor.capabilityReason
                                            font.family: window.uiFont; font.pixelSize: 14
                                            palette.windowText: window.ink; palette.text: window.ink; palette.highlight: "#456548"
                                            onToggled: backend.postEditor.reviewAfter = checked
                                        }
                                        Caption {
                                            Layout.fillWidth: true; Layout.leftMargin: 7
                                            text: backend.autoGlossary ? qsTr("此旧译本仍沿用预扫表；新建译本将使用原生翻译。") : backend.hasGlossary ? qsTr("使用你提供的固定表：%1").arg(backend.glossaryName) : qsTr("所选模型先按上下文翻译。之后可对照原文检查不同译法，确认后仅校订相关段落。检查会额外耗时，默认关闭。")
                                            color: "#717F6B"; font.pixelSize: 11
                                        }
                                        RowLayout {
                                            visible: backend.hasGlossary || backend.glossarySummary.length > 0; Layout.fillWidth: true
                                            Caption { Layout.fillWidth: true; Layout.leftMargin: 7; text: backend.glossarySummary }
                                            Action { text: backend.taskLocked ? qsTr("查看表") : qsTr("移除表"); subtle: true; implicitHeight: 28; onClicked: backend.taskLocked ? backend.openGlossary() : backend.clearGlossary() }
                                        }
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 8
                                RowLayout {
                                    Text { text: qsTr("翻译偏好"); color: window.ink; font.family: window.uiFont; font.pixelSize: 14; font.weight: Font.DemiBold }
                                    Caption { text: qsTr("留空也很好") }
                                    Item { Layout.fillWidth: true }
                                    Action { text: window.styleExpanded ? qsTr("收起") : qsTr("展开"); subtle: true; implicitHeight: 28; onClicked: window.styleExpanded = !window.styleExpanded }
                                }
                                ScrollView {
                                    visible: window.styleExpanded || backend.instructions.length > 0
                                    Layout.fillWidth: true; Layout.preferredHeight: 76; clip: true
                                    TextArea {
                                        objectName: "preferencesInput"; text: backend.instructions; readOnly: backend.running || backend.taskLocked
                                        placeholderText: qsTr("例如：对话自然一些，保留人物称谓的年代感。")
                                        wrapMode: TextEdit.Wrap; selectByMouse: true
                                        font.family: window.uiFont; font.pixelSize: 12
                                        color: window.ink; placeholderTextColor: "#969E91"; padding: 13
                                        onTextChanged: if (!readOnly && text !== backend.instructions) backend.instructions = text
                                        background: Rectangle { radius: 10; color: window.surface; border.color: window.line }
                                    }
                                }
                            }
                            RowLayout {
                                visible: backend.taskLocked; Layout.fillWidth: true
                                Caption { Layout.fillWidth: true; text: qsTr("续译沿用本书原规则。想改模式，可另建译本。"); font.pixelSize: 11 }
                                Action { objectName: "newEditionButton"; text: qsTr("新建译本"); subtle: true; enabled: !backend.running; onClicked: editionDialog.open() }
                            }
                            Item { Layout.preferredHeight: 2 }
                        }
                    }
                    Rectangle {
                        objectName: "translationProgressCard"
                        Layout.fillWidth: true; implicitHeight: progressColumn.implicitHeight + 20
                        radius: 8; color: window.surface; border.color: window.line
                        ColumnLayout {
                            id: progressColumn
                            anchors.fill: parent; anchors.margins: 10; spacing: 5
                            RowLayout {
                                Text { Layout.fillWidth: true; text: backend.postEditor.busy ? qsTr("正在校对译文") : backend.statusTitle.replace(/^Hy\s*/, ""); color: window.ink; font.family: window.uiFont; font.pixelSize: 14; font.weight: Font.Medium }
                                Action {
                                    objectName: "readTranslationButton"; implicitHeight: 28
                                    enabled: backend.outputPath.length > 0
                                    text: qsTr("阅读译文")
                                    onClicked: { window.readerVisible = true; backend.followTranslationAsync() }
                                    ToolTip.visible: hovered; ToolTip.text: qsTr("打开已保存的译文，不暂停翻译")
                                }
                                Text { text: backend.scanning ? Math.round(backend.scanProgress * 100) + "%" : backend.progressText; color: window.accent; font.family: "Georgia"; font.pixelSize: 22 }
                            }
                            ProgressBar {
                                id: progressBar
                                Layout.fillWidth: true; value: backend.scanning ? backend.scanProgress : backend.progress
                                background: Rectangle { implicitHeight: 3; radius: 1; color: window.line }
                                contentItem: Item { implicitHeight: 3; Rectangle { width: parent.width * progressBar.visualPosition; height: parent.height; radius: 1; color: window.accent } }
                            }
                            Caption { Layout.fillWidth: true; text: backend.postEditor.busy ? backend.postEditor.status : backend.statusDetail; font.pixelSize: 11; maximumLineCount: 2; elide: Text.ElideRight }
                            RowLayout {
                                Caption { text: qsTr("用时 %1").arg(backend.elapsedText); font.pixelSize: 11 }
                                Item { Layout.fillWidth: true }
                                Caption { text: backend.scanning ? qsTr("译名预扫 · 不计正文速度") : backend.speedText; font.pixelSize: 11 }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Action { text: qsTr("在 Finder 中显示"); enabled: backend.jobPath.length > 0; subtle: true; onClicked: backend.revealOutput() }
                        Item { Layout.fillWidth: true }
                        Action { objectName: "pauseButton"; visible: backend.running; text: qsTr("暂停"); onClicked: backend.pauseTranslation() }
                        Action {
                            objectName: "startButton"; primary: true; implicitHeight: 44
                            text: backend.postEditor.busy ? qsTr("校对中…") : backend.running ? qsTr("翻译中…") : backend.finished ? qsTr("阅读译文") : backend.progress > 0 || backend.taskLocked ? qsTr("继续翻译") : qsTr("开始翻译")
                            enabled: backend.finished || backend.canStart
                            onClicked: {
                                if (backend.finished) { window.readerVisible = true; backend.followTranslationAsync() }
                                else backend.startTranslation()
                            }
                        }
                    }
                }
            }
            Item {
                id: readerArea
                visible: window.readerVisible
                SplitView.preferredWidth: 940; SplitView.minimumWidth: 400; SplitView.fillWidth: true
                property var askTask: backend.askAI.state
                Item {
                    id: readerViewport
                    objectName: "readerViewport"
                    anchors.fill: parent
                    anchors.bottomMargin: (groupNavigator.visible ? groupNavigator.height : 0)
                                          + (askTaskBar.visible ? askTaskBar.height : 0)
                    property real contentX: 0
                    property real contentWidth: width
                    property int activeIndex: Math.max(0, window.visibleColumnIds.indexOf(backend.activeReadingColumnId))
                    property var activeBook: backend.readingColumns.find(function(c) { return c.columnId === backend.activeReadingColumnId })
                    property int slots: window.readingOnly || width < 880 || (activeBook && activeBook.epubMode === "bilingual") ? 1 : 2
                    property int firstIndex: Math.floor(activeIndex / slots) * slots
                    property int shownCount: Math.min(slots, window.visibleColumnIds.length - firstIndex)
                    function revealActiveColumn() {
                        // Native web views remain in fixed, fully visible slots.
                        // Never slide them through a clipped QML viewport.
                    }
                    Item {
                        id: readerRow
                        objectName: "readerRow"
                        width: readerViewport.width
                        height: readerViewport.height
                        Repeater {
                            id: readerColumns
                            objectName: "readerColumns"
                            model: backend.readingColumns
                            delegate: Item {
                                id: columnFrame
                                objectName: "readerColumn_" + index
                                required property var modelData
                                required property int index
                                property int visualIndex: window.visibleColumnIds.indexOf(modelData.columnId)
                                visible: visualIndex >= readerViewport.firstIndex && visualIndex < readerViewport.firstIndex + readerViewport.shownCount
                                x: visible ? (visualIndex - readerViewport.firstIndex) * width : 0
                                width: readerViewport.width / Math.max(1, readerViewport.shownCount)
                                height: readerRow.height
                                Rectangle { visible: columnFrame.visualIndex > 0; x: 0; width: 3; height: parent.height; color: window.line }
                                ReaderPane {
                                    id: columnReader
                                    objectName: "readerPane_" + columnFrame.index
                                    anchors.fill: parent; anchors.leftMargin: columnFrame.visualIndex > 0 ? 3 : 0
                                    reading: columnFrame.modelData
                                    nativeEpubViewsHidden: window.nativeEpubViewsHidden
                                    focusMode: window.readingOnly
                                    epubPairVisible: !window.soloEpub
                                    onToggleFocus: window.setImmersive(!window.readingOnly, columnFrame.modelData.epubActive ? columnFrame.modelData.columnId : "")
                                    onOpenAnnotations: window.openReview(true)
                                    onAnnotationEditorVisibilityChanged: function(opened) {
                                        if (opened) {
                                            window.annotationEditorOwnerColumnId = columnFrame.modelData.columnId
                                        } else if (window.annotationEditorOwnerColumnId === columnFrame.modelData.columnId) {
                                            window.annotationEditorOwnerColumnId = ""
                                        }
                                    }
                                    onAddColumn: {
                                        scrollColumnsToEnd.restart()
                                        backend.addReadingColumn()
                                    }
                                    onCloseColumn: backend.removeReadingColumn(columnFrame.modelData.columnId)
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    id: askTaskBar
                    objectName: "askTaskBar"
                    visible: readerArea.askTask.taskVisible === true
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.bottomMargin: groupNavigator.visible ? groupNavigator.height : 0
                    height: 42; color: window.surface; z: 4
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: window.line }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 12; spacing: 8
                        Rectangle {
                            Layout.preferredWidth: 7; Layout.preferredHeight: 7; radius: 4
                            color: readerArea.askTask.busy ? "#B58B42"
                                  : readerArea.askTask.error ? "#B46C58" : window.accent
                        }
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("问 AI · %1 · %2 · %3")
                                      .arg(readerArea.askTask.taskTitle || qsTr("当前阅读"))
                                      .arg(readerArea.askTask.taskStatus)
                                      .arg(readerArea.askTask.taskQuestion)
                            textFormat: Text.PlainText; color: window.ink
                            font.family: window.uiFont; font.pixelSize: 12
                            elide: Text.ElideRight; maximumLineCount: 1
                        }
                        Action {
                            objectName: "askTaskOpen"; implicitHeight: 30
                            text: readerArea.askTask.busy ? qsTr("查看") : qsTr("查看回答")
                            onClicked: backend.askAI.reopen()
                        }
                        Action {
                            objectName: "askTaskCancel"; implicitHeight: 30
                            visible: readerArea.askTask.busy === true; text: qsTr("取消")
                            onClicked: backend.askAI.cancel()
                        }
                        Action {
                            objectName: "askTaskDismiss"; implicitHeight: 30
                            visible: readerArea.askTask.busy !== true; text: qsTr("清除")
                            onClicked: backend.askAI.dismissTask()
                        }
                    }
                }
                RowLayout {
                    id: groupNavigator
                    objectName: "readingGroupNavigator"
                    visible: !window.readingOnly && backend.readingColumns.length > 1
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 40
                    Action { objectName: "previousReadingGroup"; text: qsTr("‹ 上一栏"); enabled: readerViewport.activeIndex > 0; onClicked: backend.activateReadingColumn(window.visibleColumnIds[readerViewport.activeIndex - 1]) }
                    Text { Layout.fillWidth: true; text: qsTr("当前第 %1 栏 / %2 栏").arg(readerViewport.activeIndex + 1).arg(backend.readingColumns.length); color: window.muted; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 12 }
                    Action { objectName: "nextReadingGroup"; text: qsTr("下一栏 ›"); enabled: readerViewport.activeIndex + 1 < window.visibleColumnIds.length; onClicked: backend.activateReadingColumn(window.visibleColumnIds[readerViewport.activeIndex + 1]) }
                }
                Rectangle {
                    id: pairedNavigation
                    objectName: "pairedEpubNavigation"
                    visible: false // Paired navigation now belongs to each book workspace.
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    height: 44; color: window.surface
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: window.line }
                    Row {
                        anchors.centerIn: parent; spacing: 12
                        Action {
                            objectName: "epubImmersiveLayout"
                            visible: window.readingOnly && window.immersiveColumnId !== ""
                            text: window.immersiveParallel ? qsTr("只读当前栏") : qsTr("双语沉浸")
                            onClicked: {
                                if (window.immersiveParallel) window.immersiveColumnId = backend.activeReadingColumnId
                                window.immersiveParallel = !window.immersiveParallel
                                readerViewport.contentX = 0
                            }
                        }
                        Action { objectName: "pairedEpubPrevious"; visible: !window.soloEpub; text: qsTr("‹ 一起上一节"); enabled: backend.epubPairNavigation.previous; onClicked: backend.navigateEpubPair(-1) }
                        Action {
                            objectName: "pairedEpubSwap"; text: qsTr("交换左右")
                            visible: !window.soloEpub
                            onClicked: backend.swapEpubSides()
                            ToolTip.visible: hovered
                            ToolTip.text: backend.epubOriginalOnLeft ? qsTr("当前：原文在左，译文在右") : qsTr("当前：译文在左，原文在右")
                        }
                        Action { objectName: "pairedEpubNext"; visible: !window.soloEpub; text: qsTr("一起下一节 ›"); enabled: backend.epubPairNavigation.next; onClicked: backend.navigateEpubPair(1) }
                        Action {
                            objectName: "pairedEpubScroll"
                            visible: !window.soloEpub
                            text: backend.epubScrollLinked ? qsTr("同步滚动") : qsTr("独立滚动")
                            onClicked: backend.epubScrollLinked = !backend.epubScrollLinked
                            ToolTip.visible: hovered
                            ToolTip.text: qsTr("默认同步两栏的章节内进度；按住 Option 可临时只滚当前栏")
                        }
                    }
                }
                Timer {
                    id: scrollColumnsToEnd; interval: 60
                    onTriggered: readerViewport.contentX = Math.max(0, readerViewport.contentWidth - readerViewport.width)
                }
                Timer { id: revealReadingColumn; interval: 0; onTriggered: readerViewport.revealActiveColumn() }
            }
        }
        ResearchPane {
            objectName: "researchPane"
            visible: window.researchMode
            Layout.fillWidth: true; Layout.fillHeight: true
            research: backend.researchModel
            onSettingsRequested: settingsPopup.open()
            ink: window.ink; muted: window.muted; surface: window.surface; line: window.line
        }
    }
    ReviewPane { id: reviewPopup; editor: backend.postEditor }
    Popup {
        id: welcomePopup; objectName: "welcomePopup"; popupType: Popup.Item
        anchors.centerIn: Overlay.overlay
        implicitWidth: Math.min(600, window.width - 40); implicitHeight: 430
        width: implicitWidth; height: implicitHeight; padding: 26
        modal: true; focus: true; closePolicy: Popup.NoAutoClose
        background: Rectangle { radius: 18; color: window.canvas; border.color: window.line }
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: qsTr("欢迎使用鲸读"); color: window.ink; font.pixelSize: 25; font.weight: Font.DemiBold }
            Caption { Layout.fillWidth: true; text: qsTr("先读一本书，再按需要配置翻译与阅读问答。") }
            Caption { Layout.fillWidth: true; text: qsTr("阅读：打开 EPUB 或 TXT 即可开始，无需模型或 API 账户。书库与阅读记录保存在这台 Mac 上。") }
            Caption { Layout.fillWidth: true; text: qsTr("AI 功能：保留本机 HY-MT2 预设，也支持你配置的兼容 API。不同模型的翻译与格式遵循能力不同，建议先试译一小段。") }
            Caption { Layout.fillWidth: true; text: qsTr("隐私：云端功能会展示目的地和发送材料，并需要你的明确授权。API Key 存于系统钥匙串。") }
            Caption { Layout.fillWidth: true; text: qsTr("鲸读使用 Qt/PySide 等第三方组件，可在设置中查看隐私说明与许可。") }
            Item { Layout.fillHeight: true }
            RowLayout {
                Action { objectName: "welcomeRead"; text: qsTr("开始阅读"); primary: true; onClicked: { backend.finishOnboarding(); welcomePopup.close() } }
                Action { objectName: "welcomeModels"; text: qsTr("配置模型"); onClicked: { backend.finishOnboarding(); welcomePopup.close(); settingsPopup.open() } }
            }
        }
        Component.onCompleted: Qt.callLater(function() { if (backend.onboardingPending) welcomePopup.open() })
    }
    Popup {
        id: legalPopup; objectName: "legalPopup"; popupType: Popup.Item
        property string documentText: ""
        anchors.centerIn: Overlay.overlay
        implicitWidth: Math.min(760, window.width - 40); implicitHeight: Math.min(700, window.height - 40)
        width: implicitWidth; height: implicitHeight; padding: 18; modal: true; focus: true
        background: Rectangle { radius: 18; color: window.canvas; border.color: window.line }
        contentItem: ColumnLayout {
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea { text: legalPopup.documentText; readOnly: true; wrapMode: Text.Wrap; color: window.ink; selectByMouse: true; textFormat: TextEdit.PlainText }
            }
            Action { text: qsTr("关闭"); onClicked: legalPopup.close() }
        }
    }
    Popup { popupType: Popup.Item;
        id: settingsPopup
        objectName: "settingsPopup"
        anchors.centerIn: Overlay.overlay
        implicitWidth: Math.min(680, window.width - 40); implicitHeight: Math.min(780, window.height - 40)
        width: implicitWidth; height: implicitHeight
        padding: 0; modal: true; focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { radius: 18; color: window.canvas; border.color: window.line }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 18
                Layout.topMargin: 17; Layout.bottomMargin: 14
                ColumnLayout {
                    spacing: 2
                    Text { text: qsTr("设置"); color: window.ink; font.pixelSize: 21; font.weight: Font.DemiBold; font.family: window.uiFont }
                    Caption { text: qsTr("模型、试译与阅读外观") }
                }
                Item { Layout.fillWidth: true }
                Action { text: qsTr("关闭"); subtle: true; onClicked: settingsPopup.close() }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
            ScrollView {
                id: settingsScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    x: 24; width: settingsScroll.availableWidth - 48; spacing: 12
                    Item { Layout.preferredHeight: 7 }
                    Text { objectName: "interfaceLanguageHeading"; text: qsTr("界面语言"); color: window.ink; font.pixelSize: 17; font.weight: Font.DemiBold }
                    Caption { Layout.fillWidth: true; text: qsTr("只影响界面文字，不会改动书籍、任务、缓存或回答语言。") }
                    ComboBox {
                        id: localeChoice; objectName: "interfaceLanguageChoice"; Layout.fillWidth: true
                        model: backend.localeOptions
                        textRole: "text"; valueRole: "value"
                        currentIndex: Math.max(0, indexOfValue(backend.uiLocale))
                        Accessible.name: qsTr("界面语言")
                        onActivated: {
                            if (!backend.setUiLocale(currentValue))
                                currentIndex = Math.max(0, indexOfValue(backend.uiLocale))
                        }
                    }
                    Caption {
                        Layout.fillWidth: true; visible: backend.localeNotice.length > 0
                        text: backend.localeNotice; color: "#A16B40"
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 4 }
                    ProviderSettings { backend: window.modelBackend; ink: window.ink; muted: window.muted }
                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
                    Text { text: qsTr("书库、任务与导入"); color: window.ink; font.pixelSize: 17; font.weight: Font.DemiBold }
                    Caption { Layout.fillWidth: true; text: qsTr("首次使用的任务保存在应用数据目录。可授权预览版旧任务目录，导入书库偏好，再授权书籍所在目录；原文件保留。") }
                    Caption { Layout.fillWidth: true; text: backend.dataSettings.taskFolder }
                    Action { objectName: "chooseTaskFolder"; text: qsTr("选择任务目录 / 导入旧任务"); onClicked: backend.dataSettings.chooseTaskFolder() }
                    Action { objectName: "importPreviewSettings"; text: qsTr("导入预览版偏好与书库"); onClicked: backend.dataSettings.importPreviewSettings() }
                    Action { objectName: "authorizeBookFolder"; text: qsTr("重新授权旧书籍目录"); onClicked: backend.dataSettings.authorizeBookFolder() }
                    Action { objectName: "importLegacyAskKey"; text: qsTr("导入旧问 AI 密钥到钥匙串"); onClicked: backend.dataSettings.importLegacyAskKey() }
                    Action { objectName: "deleteMigratedKey"; visible: backend.dataSettings.legacyKeyImported; text: qsTr("删除已核验迁移的旧密钥文件"); onClicked: backend.dataSettings.deleteMigratedKey() }
                    Action { objectName: "exportTranslationCopy"; text: qsTr("导出当前译本副本…"); onClicked: backend.dataSettings.exportOutput() }
                    Caption { Layout.fillWidth: true; text: backend.dataSettings.notice }
                    RowLayout {
                        Action { objectName: "privacyNotice"; text: qsTr("隐私说明"); onClicked: { legalPopup.documentText = backend.privacyText; legalPopup.open() } }
                        Action { objectName: "thirdPartyNotices"; text: qsTr("第三方许可"); onClicked: { legalPopup.documentText = backend.licenseText; legalPopup.open() } }
                    }
                    Rectangle {
                        Layout.fillWidth: true; implicitHeight: modelStatusText.implicitHeight + 22; radius: 9
                        color: backend.modelReady ? window.accentSoft : (window.darkCanvas ? "#4A3730" : "#F4E5D8")
                        border.color: backend.modelReady ? window.accent : "#BA8255"
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 11; spacing: 8
                            Rectangle { width: 7; height: 7; radius: 4; color: backend.modelReady ? window.accent : "#BA8255" }
                            Caption { id: modelStatusText; Layout.fillWidth: true; text: backend.modelStatus; color: window.ink }
                            Action { text: qsTr("刷新"); implicitHeight: 28; onClicked: backend.refreshModelStatus() }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 5; Layout.bottomMargin: 3 }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("快速试译"); color: window.ink; font.pixelSize: 15; font.weight: Font.DemiBold; font.family: window.uiFont }
                        Item { Layout.fillWidth: true }
                        Caption { text: qsTr("不创建任务，不写入书库") }
                    }
                    TextArea {
                        id: probeSource; objectName: "probeSource"
                        Layout.fillWidth: true; Layout.preferredHeight: 92
                        text: "The rain had stopped, but the old station still smelled of wet iron. Mara folded the letter and looked toward the last train."
                        placeholderText: qsTr("粘贴一小段原文，最多 3000 字符"); wrapMode: TextEdit.Wrap; selectByMouse: true
                        color: window.ink; placeholderTextColor: window.muted; font.family: window.uiFont; font.pixelSize: 12; padding: 12
                        background: Rectangle { radius: 10; color: window.surface; border.color: window.line }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Caption { Layout.fillWidth: true; text: backend.probeStatus }
                        Action { objectName: "probeButton"; text: backend.probeBusy ? qsTr("试译中…") : qsTr("用当前模型试译"); primary: true; enabled: backend.modelReady && !backend.running && !backend.probeBusy && probeSource.text.trim().length > 0; onClicked: backend.probeTranslation(probeSource.text) }
                    }
                    Caption { objectName: "probeStatusDetail"; Layout.fillWidth: true; visible: backend.probeStatusDetail.length > 0; text: backend.probeStatusDetail; textFormat: Text.PlainText }
                    TextArea {
                        objectName: "probeResult"; visible: backend.probeResult.length > 0
                        Layout.fillWidth: true; Layout.preferredHeight: Math.max(86, implicitHeight)
                        text: backend.probeResult; readOnly: true; wrapMode: TextEdit.Wrap; selectByMouse: true
                        color: window.ink; font.family: "Songti SC"; font.pixelSize: 14; padding: 12
                        background: Rectangle { radius: 10; color: window.surface; border.color: window.accent }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 5; Layout.bottomMargin: 3 }
                    Text { text: qsTr("外观"); color: window.ink; font.pixelSize: 15; font.weight: Font.DemiBold; font.family: window.uiFont }
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; columnSpacing: 14; rowSpacing: 7
                        Caption { text: qsTr("主题色") }
                        ComboBox {
                            objectName: "themeSelector"; Layout.fillWidth: true
                            model: [qsTr("松石绿"), qsTr("海湾蓝"), qsTr("琥珀棕"), qsTr("晚樱红")]
                            currentIndex: ["jade", "ocean", "amber", "sakura"].indexOf(backend.themeId)
                            onActivated: backend.themeId = ["jade", "ocean", "amber", "sakura"][currentIndex]
                        }
                        Caption { text: qsTr("页面背景") }
                        ComboBox {
                            objectName: "backgroundSelector"; Layout.fillWidth: true
                            model: [qsTr("宣纸白"), qsTr("暖米色"), qsTr("雾灰蓝"), qsTr("深夜")]
                            currentIndex: ["paper", "warm", "mist", "night"].indexOf(backend.backgroundId)
                            onActivated: backend.backgroundId = ["paper", "warm", "mist", "night"][currentIndex]
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; implicitHeight: 58; radius: 10; color: window.surface; border.color: window.line
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 12; spacing: 10
                            Rectangle { width: 32; height: 32; radius: 16; color: window.accent }
                            ColumnLayout { Layout.fillWidth: true; spacing: 1
                                Text { text: qsTr("鲸读 · 外观预览"); color: window.ink; font.family: window.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Caption { text: qsTr("主题与背景会立即保存，阅读栏同步生效。"); font.pixelSize: 11 }
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 5; Layout.bottomMargin: 3 }
                    Text { text: qsTr("存储与缓存"); color: window.ink; font.pixelSize: 15; font.weight: Font.DemiBold; font.family: window.uiFont }
                    Rectangle {
                        Layout.fillWidth: true; implicitHeight: Math.max(62, cacheColumn.implicitHeight + 24); radius: 10; color: window.surface; border.color: window.line
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 12; spacing: 10
                            ColumnLayout {
                                id: cacheColumn
                                Layout.fillWidth: true; spacing: 2
                                Text { text: qsTr("EPUB 阅读缓存"); color: window.ink; font.pixelSize: 13; font.weight: Font.DemiBold; font.family: window.uiFont }
                                Caption { objectName: "readerCacheSummary"; Layout.fillWidth: true; text: backend.readerCacheSummary }
                                Caption { objectName: "readerCacheDetail"; Layout.fillWidth: true; visible: backend.readerCacheDetail.length > 0; text: backend.readerCacheDetail; textFormat: Text.PlainText }
                            }
                            Action { objectName: "clearReaderCache"; text: backend.readerCacheBusy ? qsTr("处理中…") : qsTr("清除缓存"); enabled: !backend.readerCacheBusy; onClicked: clearReaderCacheDialog.open() }
                        }
                    }
                    Caption { Layout.fillWidth: true; text: qsTr("缓存按图书和版本管理。清理时会保留当前正在阅读的页面，不会删除书籍、阅读进度、书签或随笔。") }

                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 5; Layout.bottomMargin: 3 }
                    Text { text: qsTr("高级"); color: window.ink; font.pixelSize: 15; font.weight: Font.DemiBold; font.family: window.uiFont }
                    Caption { Layout.fillWidth: true; text: qsTr("本机模型固定顺序翻译，减少内存争用并保留相邻译块的译文锚点。") }
                    Action { text: qsTr("导入已确认的固定译名表…"); enabled: !backend.running && !backend.taskLocked; onClicked: backend.pickGlossary() }
                    Caption { Layout.fillWidth: true; text: qsTr("仅在已有可靠译名表时使用。默认不向正文注入术语。") }
                    Item { Layout.preferredHeight: 13 }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.topMargin: 12; Layout.bottomMargin: 14
                Caption { Layout.fillWidth: true; text: backend.modelDisplayName }
                Action { objectName: "settingsDone"; text: qsTr("完成"); primary: true; onClicked: settingsPopup.close() }
            }
        }
    }
    Dialog { popupType: Popup.Window;
        id: clearReaderCacheDialog
        objectName: "clearReaderCacheDialog"
        anchors.centerIn: Overlay.overlay; modal: true; width: 430
        title: qsTr("清除可重建的 EPUB 缓存？")
        footer: DialogButtonBox {
            Button { objectName: "clearReaderCacheCancel"; text: qsTr("取消"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button { objectName: "clearReaderCacheConfirm"; text: qsTr("确定"); DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
        Label {
            width: parent.width; padding: 12; wrapMode: Text.Wrap
            text: backend.readerCacheSummary + qsTr("\n\n只清除当前未在使用的页面缓存；书籍、原译文、阅读位置、书签、随笔和独立封面均保留。来源待确认的旧缓存也会列入本次清理。")
        }
        onAccepted: backend.clearReaderCache()
    }
    Dialog { popupType: Popup.Window;
        id: editionDialog
        objectName: "editionDialog"
        anchors.centerIn: Overlay.overlay; title: qsTr("新建独立译本？"); modal: true; width: 400
        footer: DialogButtonBox {
            Button { objectName: "editionCancel"; text: qsTr("取消"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button { objectName: "editionConfirm"; text: qsTr("确定"); DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
        Label { width: parent.width; text: qsTr("新译本从头开始，可在设置中选择翻译模型；之后可检查译名。原译文和断点保留。"); wrapMode: Text.Wrap; padding: 12 }
        onAccepted: backend.newEdition()
    }
    Popup { popupType: Popup.Window;
        id: languageDialog
        objectName: "languageDialog"
        anchors.centerIn: Overlay.overlay
        implicitWidth: Math.min(560, window.width - 40)
        implicitHeight: Math.min(600, window.height - 40)
        width: implicitWidth; height: implicitHeight
        padding: 0; modal: true; focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        property bool sourceCustom: false
        property bool targetCustom: false
        function openForLanguage() {
            var source = backend.sourceLanguage
            sourceCustom = backend.sourceLanguageId === "custom"
            sourceCombo.currentIndex = Math.max(0, sourceCombo.indexOfValue(backend.sourceLanguageId))
            sourceInput.text = sourceCustom ? source : ""
            var target = backend.targetLanguage
            targetCustom = backend.targetLanguageId === "custom"
            targetCombo.currentIndex = Math.max(0, targetCombo.indexOfValue(backend.targetLanguageId))
            targetInput.text = targetCustom ? target : ""
            open()
        }
        function sourceAuto() { return sourceCombo.currentValue === "auto" }
        function chosenSource() {
            if (sourceCombo.currentValue === "auto") return ""
            if (sourceCombo.currentValue === "custom") return sourceInput.text.trim()
            return sourceCombo.currentValue
        }
        function chosenTarget() {
            if (targetCombo.currentValue === "custom") return targetInput.text.trim()
            return targetCombo.currentValue
        }
        function apply() {
            if (backend.setLanguageDirection(chosenSource(), chosenTarget(), sourceAuto()))
                close()
        }
        function startNewEdition() {
            if (backend.newEditionWithLanguage(chosenSource(), chosenTarget(), sourceAuto()))
                close()
        }
        background: Rectangle { radius: 18; color: window.canvas; border.color: window.line }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 18
                Layout.topMargin: 17; Layout.bottomMargin: 14
                ColumnLayout {
                    spacing: 2
                    Text { text: qsTr("翻译语言方向"); color: window.ink; font.pixelSize: 20; font.weight: Font.DemiBold; font.family: window.uiFont }
                    Caption { text: qsTr("卡片显示的方向就是写入任务并交给翻译器的方向") }
                }
                Item { Layout.fillWidth: true }
                Action { text: qsTr("关闭"); subtle: true; onClicked: languageDialog.close() }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
            ScrollView {
                id: languageScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: availableWidth; ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    x: 24; width: languageScroll.availableWidth - 48; spacing: 8
                    Item { Layout.preferredHeight: 4 }
                    Caption { text: qsTr("源语言") }
                    ComboBox {
                        id: sourceCombo
                        objectName: "languageSourceSelector"
                        Layout.fillWidth: true
                        model: backend.languageSourceOptions
                        textRole: "text"
                        valueRole: "value"
                        Accessible.name: qsTr("源语言")
                    }
                    TextField {
                        id: sourceInput
                        objectName: "languageSourceInput"
                        Layout.fillWidth: true
                        visible: sourceCombo.currentValue === "custom"
                        placeholderText: qsTr("直接输入语言名称，例如 瑞典语 / Swedish")
                        color: window.ink; font.family: window.uiFont; font.pixelSize: 13; padding: 9
                        background: Rectangle { radius: 8; color: window.surface; border.color: window.line }
                    }
                    Caption {
                        Layout.fillWidth: true
                        text: backend.languageAuto && backend.detectedLanguage.length > 0
                              ? qsTr("当前自动判断：%1").arg(backend.detectedLanguage)
                              : qsTr("选择“自动识别”时，程序按正文文字判断，而不是把拉丁字母一律当作英语；判断不确定会提示你手动指定。")
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: window.line; Layout.topMargin: 6; Layout.bottomMargin: 2 }
                    Caption { text: qsTr("目标语言") }
                    ComboBox {
                        id: targetCombo
                        objectName: "languageTargetSelector"
                        Layout.fillWidth: true
                        model: backend.languageTargetOptions
                        textRole: "text"
                        valueRole: "value"
                        Accessible.name: qsTr("目标语言")
                    }
                    TextField {
                        id: targetInput
                        objectName: "languageTargetInput"
                        Layout.fillWidth: true
                        visible: targetCombo.currentValue === "custom"
                        placeholderText: qsTr("直接输入语言名称，例如 瑞典语 / Swedish")
                        color: window.ink; font.family: window.uiFont; font.pixelSize: 13; padding: 9
                        background: Rectangle { radius: 8; color: window.surface; border.color: window.line }
                    }
                    Caption { Layout.fillWidth: true; text: qsTr("输出文件名会跟随目标语言，例如 .zh-CN.txt、.en.txt、.fr.txt；不同目标不会共用同一路径。") }
                    Rectangle {
                        visible: backend.languageNotice.length > 0
                        Layout.fillWidth: true; implicitHeight: languageNoticeText.implicitHeight + 18
                        radius: 9; color: window.darkCanvas ? "#4A3730" : "#F7EBDA"; border.color: "#BA8255"
                        Caption { id: languageNoticeText; anchors.fill: parent; anchors.margins: 9; text: backend.languageNotice; color: window.ink }
                    }
                    Rectangle {
                        visible: backend.languageLocked
                        Layout.fillWidth: true; implicitHeight: lockedText.implicitHeight + 18
                        radius: 9; color: window.accentSoft; border.color: window.accent
                        Caption {
                            id: lockedText; anchors.fill: parent; anchors.margins: 9; color: window.ink
                            text: qsTr("这个译本已有安全断点，不能原地修改语言方向，也不会覆盖已有译文。请点“新建另一译本”，用新方向从头开始；原译本与断点保留。")
                        }
                    }
                    Item { Layout.preferredHeight: 8 }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: window.line }
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24
                Layout.topMargin: 12; Layout.bottomMargin: 14
                Caption {
                    Layout.fillWidth: true
                    text: backend.languageLocked ? qsTr("方向已随任务保存") : qsTr("保存后显示在原文卡片上，开始翻译时写入任务 YAML")
                }
                Action {
                    objectName: "languageNewEdition"
                    visible: backend.languageLocked
                    text: qsTr("新建另一译本")
                    onClicked: languageDialog.startNewEdition()
                }
                Action {
                    objectName: "languageApply"
                    primary: true; enabled: !backend.languageLocked
                    text: qsTr("保存方向")
                    onClicked: languageDialog.apply()
                }
            }
        }
    }
    Dialog { popupType: Popup.Window;
        id: errorDialog
        objectName: "errorDialog"
        anchors.centerIn: Overlay.overlay; title: qsTr("需要处理一下"); modal: true
        implicitWidth: 440; width: implicitWidth
        footer: DialogButtonBox {
            Button { objectName: "errorDialogAcknowledge"; text: qsTr("确定"); DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
        ColumnLayout {
            width: errorDialog.availableWidth; spacing: 0
            Label {
                objectName: "errorDialogMessage"
                Layout.fillWidth: true
                text: backend.errorMessage; wrapMode: Text.Wrap; padding: 12
            }
            // The raw diagnostic is never translated and never hidden: an
            // unrecognized failure shows a localized summary above it.
            Label {
                objectName: "errorDialogDetail"
                Layout.fillWidth: true
                visible: backend.errorDetail.length > 0
                       && backend.errorDetail !== backend.errorMessage
                text: visible ? qsTr("原始错误详情：%1").arg(backend.errorDetail) : ""
                color: window.muted; font.pixelSize: 11
                wrapMode: Text.Wrap; padding: 12; textFormat: Text.PlainText
            }
        }
        onAccepted: backend.dismissError()
        onRejected: backend.dismissError()
    }
    Connections {
        target: backend
        function onChanged() { if (backend.errorMessage.length > 0 && !errorDialog.opened) errorDialog.open() }
        function onReadingFileOpened() {
            window.readerVisible = true
        }
        function onShelfBookOpened() {
            window.readerVisible = true
            window.readingOnly = false
            revealReadingColumn.restart()
        }
    }
    Component.onCompleted: window.readerVisible = true
}
