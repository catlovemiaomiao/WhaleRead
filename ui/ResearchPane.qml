import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: pane
    objectName: "researchPane"
    required property var research
    required property color ink
    required property color muted
    required property color surface
    required property color line
    property var state: research.state
    property bool pageLayout: true
    property bool positioned: true
    signal settingsRequested()
    Dialog { popupType: Popup.Window;
        id: termDialog
        objectName: "researchTermDialog"
        title: qsTr("术语与公式确认")
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(920, parent.width - 40)
        height: Math.min(740, parent.height - 40)
        modal: true
        footer: DialogButtonBox { Button { text: qsTr("关闭"); DialogButtonBox.buttonRole: DialogButtonBox.RejectRole } onRejected: termDialog.close() }
        contentItem: ColumnLayout {
            Label { Layout.fillWidth: true; text: qsTr("候选来自已解析页，不会自动解析整篇或阻止翻译。全文规则也用于后续页面；单字母和公式默认限本页。已有译文不自动修改。"); wrapMode: Text.Wrap; color: pane.muted }
            Label { objectName: "researchScopeNotice"; Layout.fillWidth: true; text: qsTr("科研译文仅支持简体中文。页面图像交由所选 OCR 服务识别；批量模式可跳过逐页确认，结果仍需人工核对。"); wrapMode: Text.Wrap; color: pane.muted }
            Label { Layout.fillWidth: true; text: pane.state.status; wrapMode: Text.Wrap; color: pane.muted }
            Label { Layout.fillWidth: true; visible: pane.state.statusDetail.length > 0; text: pane.state.statusDetail; wrapMode: Text.Wrap; color: pane.muted; textFormat: Text.PlainText }
            RowLayout {
                TextField { id: manualTerm; Layout.fillWidth: true; placeholderText: qsTr("候选词（也可手动输入）"); selectByMouse: true }
                ComboBox { id: termMode; model: [qsTr("正常翻译"), qsTr("固定译名"), qsTr("原样保留")] }
                ComboBox { id: termScope; model: [qsTr("本篇全文"), qsTr("指定页")] }
                SpinBox { id: rulePage; visible: termScope.currentIndex === 1; from: 1; to: Math.max(1, pane.state.pages); value: pane.state.page; editable: true }
            }
            RowLayout {
                TextField { id: termTarget; Layout.fillWidth: true; enabled: termMode.currentIndex === 1; placeholderText: qsTr("选择固定译名时填写目标译法"); selectByMouse: true }
                ResearchAction { text: qsTr("确认规则"); enabled: !pane.state.busy && manualTerm.text.trim().length > 0 && (termMode.currentIndex !== 1 || termTarget.text.trim().length > 0); onClicked: { research.confirmTerm(manualTerm.text, ["translate", "fixed", "keep"][termMode.currentIndex], termTarget.text, termScope.currentIndex === 0 ? 0 : rulePage.value); manualTerm.text = ""; termTarget.text = "" } }
                ResearchAction { text: qsTr("批量保留缩写"); enabled: !pane.state.busy; onClicked: research.confirmAbbreviations() }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                ColumnLayout {
                    width: parent.width; spacing: 12
                    Label { text: qsTr("待确认 · %1").arg((pane.state.termCandidates || []).length); color: pane.ink }
                    Repeater {
                        model: pane.state.termCandidates || []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            RowLayout {
                                Label { text: modelData.term; color: pane.ink; font.bold: true; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                                ResearchAction { text: qsTr("选择处理方式"); onClicked: { manualTerm.text = modelData.term; termMode.currentIndex = 2; termScope.currentIndex = modelData.page === 0 ? 0 : 1; rulePage.value = modelData.seenPage } }
                            }
                            Label { text: qsTr("%1 · 第 %2 页").arg(modelData.reason).arg(modelData.seenPage); color: pane.muted }
                            TextArea { Layout.fillWidth: true; text: modelData.source; readOnly: true; selectByMouse: true; wrapMode: Text.Wrap; textFormat: TextEdit.PlainText; color: pane.ink; background: Item {} }
                            Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
                        }
                    }
                    Label { text: qsTr("已确认规则 · 可重新选择或撤销"); color: pane.ink }
                    Repeater {
                        model: pane.state.termRules || []
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Label { Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText; color: pane.ink; text: qsTr("%1 → %2%3").arg(modelData.term).arg(modelData.mode === "fixed" ? modelData.target : modelData.mode === "keep" ? qsTr("原样保留") : qsTr("正常翻译")).arg(modelData.page === 0 ? qsTr(" · 全文") : qsTr(" · 第 %1 页").arg(modelData.page)) }
                            ResearchAction { text: qsTr("编辑"); enabled: !pane.state.busy; onClicked: { manualTerm.text = modelData.term; termTarget.text = modelData.target; termMode.currentIndex = ["translate", "fixed", "keep"].indexOf(modelData.mode); termScope.currentIndex = modelData.page === 0 ? 0 : 1; rulePage.value = modelData.page || pane.state.page } }
                            ResearchAction { text: qsTr("撤销"); enabled: !pane.state.busy; onClicked: research.removeTerm(modelData.term, modelData.page) }
                        }
                    }
                }
            }
        }
    }
    onPositionedChanged: translationView.contentY = 0
    onPageLayoutChanged: translationView.contentY = 0
    property bool unsaved: {
        for (var i = 0; i < blocksRepeater.count; ++i) {
            var item = blocksRepeater.itemAt(i)
            if (item && item.needsSave) return true
        }
        return false
    }
    component ResearchAction: Button {
        id: control
        implicitHeight: 36
        padding: 12
        contentItem: Text { text: control.text; color: control.enabled ? pane.ink : pane.muted; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: control.hovered ? pane.line : pane.surface; opacity: control.enabled ? 1 : .5; border.width: 0 }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 18; spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: pane.state.title; color: pane.ink; font.pixelSize: 20; Layout.fillWidth: true; elide: Text.ElideRight }
            ResearchAction { objectName: "researchPositionToggle"; visible: pane.pageLayout; text: pane.positioned ? qsTr("舒适排版") : qsTr("原位对照"); onClicked: pane.positioned = !pane.positioned }
            ResearchAction { text: pane.pageLayout ? qsTr("切换段落校对") : qsTr("返回译文纸张"); enabled: !pane.unsaved; onClicked: pane.pageLayout = !pane.pageLayout }
            ResearchAction { text: qsTr("导入 PDF"); enabled: !pane.state.busy && !pane.unsaved; onClicked: research.pick() }
            ResearchAction { text: qsTr("导出段落对照"); enabled: pane.state.pages > 0 && !pane.state.busy && !pane.unsaved; onClicked: research.export() }
        }
        RowLayout {
            ResearchAction { text: qsTr("上一页"); enabled: !pane.state.busy && !pane.unsaved && pane.state.page > 1; onClicked: research.navigate(pane.state.page - 1) }
            SpinBox { id: pagePicker; from: 1; to: Math.max(1, pane.state.pages); value: pane.state.page; editable: true; enabled: !pane.state.busy && !pane.unsaved && pane.state.pages > 0; onValueModified: research.navigate(value) }
            Label { text: qsTr("/ %n 页", "page count", pane.state.pages); color: pane.muted }
            ResearchAction { text: qsTr("下一页"); enabled: !pane.state.busy && !pane.unsaved && pane.state.page < pane.state.pages; onClicked: research.navigate(pane.state.page + 1) }
            Item { Layout.fillWidth: true }
            ResearchAction { objectName: "researchAnalyze"; text: qsTr("解析本页"); enabled: !pane.state.busy && pane.state.pages > 0 && !pane.state.hasPage; onClicked: research.run("analyze") }
            ResearchAction {
                objectName: "researchCorrectSource"
                text: qsTr("原文有误，逐段修正")
                visible: pane.state.hasPage && !pane.state.approved && pane.pageLayout
                enabled: !pane.state.busy && !pane.unsaved
                onClicked: pane.pageLayout = false
            }
            ResearchAction { objectName: "researchApprove"; text: qsTr("本页核对通过"); visible: pane.state.hasPage && !pane.state.approved; enabled: !pane.state.busy && !pane.unsaved; onClicked: research.approve() }
            ResearchAction { objectName: "researchTranslate"; visible: !pane.state.batchMode; text: qsTr("翻译本页"); enabled: !pane.state.busy && !pane.unsaved && pane.state.hasPage && pane.state.approved; onClicked: research.run("translate") }
            ResearchAction { objectName: "researchPause"; text: qsTr("暂停"); visible: pane.state.busy; onClicked: research.stop() }
        }
        RowLayout {
            Layout.fillWidth: true
            CheckBox { objectName: "researchBatchMode"; text: qsTr("批量直接翻译 · 无需逐页确认"); checked: !!pane.state.batchMode; enabled: !pane.state.busy && !pane.unsaved; onClicked: research.setBatchMode(checked) }
            ResearchAction { objectName: "researchBatchStart"; visible: pane.state.batchMode; text: pane.state.batch.state ? qsTr("继续批量 / 补齐译文") : qsTr("翻译整篇"); enabled: !pane.state.busy && !pane.unsaved && pane.state.pages > 0; onClicked: research.run("batch") }
            Item { Layout.fillWidth: true }
            ResearchAction { objectName: "researchModels"; text: qsTr("OCR 与翻译设置"); enabled: !pane.state.busy; onClicked: pane.settingsRequested() }
        }
        Label { visible: pane.state.batchMode; text: qsTr("依次识别并翻译整篇，已完成段落会跳过；可暂停后继续。页面图像发送至 OCR 服务，正文发送至翻译服务，费用由相应 API 账户承担。"); color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
        Label { visible: !!pane.state.routes.translation; text: qsTr("此文档翻译模型：%1").arg(pane.state.routes.translation ? pane.state.routes.translation.model : ""); color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Label { objectName: "researchUnreviewed"; visible: pane.state.hasPage && !pane.state.approved; text: qsTr("尚未人工核对 · 自动识别和译文可能有误"); color: pane.ink; Layout.fillWidth: true; wrapMode: Text.Wrap }
        Label { text: pane.unsaved ? qsTr("有原文修改尚未保存，请先点击对应段落的保存按钮。") : pane.state.status; color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Label { visible: !pane.unsaved && pane.state.statusDetail.length > 0; text: pane.state.statusDetail; color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Label {
            visible: pane.state.witnessConflicts > 0
            text: qsTr("本机第二识别发现 %n 段存在差异；不会自动替换，请在段落校对中选择或自行修正。",
                       "conflicting passages", pane.state.witnessConflicts)
            color: pane.ink; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap
        }
        Label { visible: pane.pageLayout; text: pane.positioned ? qsTr("原位对照 · 保留原页位置、图框与连线 · 未译区域显示原文，点击段落可查看全文 · 左右独立滚动") : qsTr("舒适排版 · 正文按阅读顺序排版，公式与图表保留原图 · 左右独立滚动"); color: pane.muted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }
        RowLayout {
            ResearchAction { objectName: "researchTerms"; text: qsTr("术语与公式确认 · %1").arg((pane.state.termCandidates || []).length); enabled: pane.state.hasPage && !pane.state.busy && !pane.unsaved; onClicked: termDialog.open() }
            Label { text: qsTr("保留词（每行一个）"); color: pane.muted }
            ScrollView { Layout.fillWidth: true; Layout.preferredHeight: 52
                TextArea { id: terms; text: pane.state.terms; enabled: !pane.state.busy; placeholderText: qsTr("快速添加原样保留词；更多处理方式见左侧确认入口。"); color: pane.ink; selectByMouse: true; wrapMode: Text.Wrap }
            }
            ResearchAction { text: qsTr("保存规则"); enabled: pane.state.pages > 0 && !pane.state.busy && !pane.unsaved; onClicked: research.saveTerms(terms.text) }
        }
        SplitView {
            id: pagesSplit
            Layout.fillWidth: true; Layout.fillHeight: true
            handle: Rectangle { implicitWidth: 3; color: pane.line }
            Flickable {
                id: originalView
                objectName: "researchOriginalScroll"
                readonly property real availableWidth: Math.max(0, width - 14)
                SplitView.preferredWidth: (pagesSplit.width - 3) / 2; SplitView.minimumWidth: 250
                clip: true; contentWidth: availableWidth
                contentHeight: originalPage.height
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                ScrollBar.vertical: ScrollBar { objectName: "researchOriginalBar"; policy: ScrollBar.AlwaysOn; interactive: true }
                WheelHandler {
                    target: null
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    onWheel: function(event) {
                        var delta = event.pixelDelta.y || event.angleDelta.y / 3
                        originalView.contentY = Math.max(0, Math.min(originalView.contentHeight - originalView.height, originalView.contentY - delta))
                        event.accepted = true
                    }
                }
                Image { id: originalPage; source: pane.state.image; width: originalView.availableWidth; height: sourceSize.width > 0 ? width * sourceSize.height / sourceSize.width : 0; fillMode: Image.PreserveAspectFit; asynchronous: true }
            }
            Flickable {
                id: translationView
                objectName: "researchTranslationScroll"
                readonly property real availableWidth: Math.max(0, width - 14)
                SplitView.fillWidth: true; SplitView.minimumWidth: 300
                clip: true; contentWidth: availableWidth
                contentHeight: translationContent.height
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                WheelHandler {
                    target: null
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    onWheel: function(event) {
                        var delta = event.pixelDelta.y || event.angleDelta.y / 3
                        translationView.contentY = Math.max(0, Math.min(translationView.contentHeight - translationView.height, translationView.contentY - delta))
                        event.accepted = true
                    }
                }
                ScrollBar.vertical: ScrollBar { objectName: "researchTranslationBar"; policy: ScrollBar.AlwaysOn; interactive: true }
                Item {
                    id: translationContent
                    width: translationView.availableWidth
                    height: pane.pageLayout ? (pane.positioned ? positionedPage.height : translatedPage.height) : details.implicitHeight
                PositionedPaper {
                    id: positionedPage
                    objectName: "researchPositionedPaper"
                    visible: pane.pageLayout && pane.positioned
                    width: parent.width
                    position: pane.state.position || ({})
                    original: pane.state.image || ""
                    pageWidth: pane.state.pageWidth
                    pageHeight: pane.state.pageHeight
                }
                Rectangle {
                    id: translatedPage
                    visible: pane.pageLayout && !pane.positioned
                    function sourceMarginRatio() {
                        var margin = .20
                        var found = false
                        for (var i = 0; i < pane.state.blocks.length; ++i) {
                            var block = pane.state.blocks[i]
                            if (["header", "footer", "number"].indexOf(block.label) >= 0) continue
                            var box = block.bbox || [0, 0, 1, 0]
                            margin = Math.min(margin, box[0], 1 - box[0] - box[2])
                            found = true
                        }
                        return found ? Math.max(.09, Math.min(.20, margin)) : .105
                    }
                    property real pageMargin: Math.max(34, width * sourceMarginRatio())
                    width: parent.width
                    height: Math.max(width * pane.state.pageHeight / Math.max(1, pane.state.pageWidth), paperFlow.implicitHeight + pageMargin * 2)
                    color: "white"
                    Column {
                        id: paperFlow
                        readonly property bool ready: (pane.state.approved || pane.state.batchDraft)
                                                       && (pane.state.translatedBlocks > 0 || pane.state.pendingBlocks === 0)
                        x: translatedPage.pageMargin; y: translatedPage.pageMargin
                        width: translatedPage.width - translatedPage.pageMargin * 2
                        spacing: Math.max(8, translatedPage.width * .012)
                        Item {
                            objectName: "researchPaperNotice"
                            visible: pane.state.hasPage && (!paperFlow.ready || pane.state.pendingBlocks > 0)
                            width: paperFlow.width
                            height: visible ? paperNotice.implicitHeight + 28 : 0
                            Text {
                                id: paperNotice
                                objectName: "researchPaperNoticeText"
                                anchors.centerIn: parent
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                                color: pane.muted
                                font.family: "Songti SC"
                                font.italic: true
                                font.pixelSize: 14
                                text: !pane.state.approved && !pane.state.batchDraft
                                      ? qsTr("本页原文尚未确认。若识别有误，请点击“原文有误，逐段修正”；核对无误后再确认并翻译。")
                                      : pane.state.translatedBlocks === 0
                                        ? qsTr("本页有 %n 段待翻译。点击“翻译本页”生成译文纸张。",
                                               "pending passages on page", pane.state.pendingBlocks)
                                        : qsTr("本页还有 %n 段待翻译。",
                                               "remaining passages on page", pane.state.pendingBlocks)
                            }
                        }
                        Repeater {
                            model: pane.state.blocks
                            delegate: Item {
                                id: paperBlock
                                required property var modelData
                                required property int index
                                objectName: "researchPaperBlock" + index
                                property var box: modelData.bbox || [0, 0, 1, 0]
                                property bool heading: ["title", "document_title", "paragraph_title", "section_title"].indexOf(modelData.label) >= 0
                                property bool decorative: ["header", "footer", "number"].indexOf(modelData.label) >= 0
                                                          || (modelData.label === "figure_title" && (modelData.source || "").length <= 4)
                                visible: paperFlow.ready && !decorative
                                         && (modelData.visual || !!modelData.translation)
                                width: paperFlow.width
                                height: modelData.visual ? paperImage.height : paperText.implicitHeight
                                Image {
                                    id: paperImage
                                    visible: paperBlock.modelData.visual
                                    source: visible ? paperBlock.modelData.image : ""
                                    width: Math.min(parent.width, Math.max(parent.width * .14, parent.width * paperBlock.box[2] / .79))
                                    height: visible && sourceSize.width > 0 ? width * sourceSize.height / sourceSize.width : 0
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                }
                                Text {
                                    id: paperText
                                    visible: !paperBlock.modelData.visual
                                    width: parent.width
                                    text: paperBlock.modelData.display_translation || paperBlock.modelData.translation
                                    textFormat: Text.PlainText
                                    color: "#182720"
                                    font.family: "Songti SC"
                                    font.bold: paperBlock.heading
                                    font.pixelSize: Math.max(paperBlock.heading ? 17 : 14,
                                                             (paperBlock.modelData.font_size || 11) * translatedPage.width / Math.max(1, pane.state.pageWidth))
                                    horizontalAlignment: paperBlock.heading ? Text.AlignHCenter : Text.AlignLeft
                                    wrapMode: Text.Wrap
                                    lineHeight: 1.32
                                    lineHeightMode: Text.ProportionalHeight
                                }
                            }
                        }
                    }
                    Label { anchors.centerIn: parent; width: parent.width - 40; text: qsTr("解析本页后，在这里按原页位置显示译文。"); wrapMode: Text.Wrap; color: pane.muted; visible: !pane.state.hasPage }
                }
                ColumnLayout {
                    id: details
                    visible: !pane.pageLayout
                    width: translationView.availableWidth; spacing: 15
                    Label { text: pane.state.origin || qsTr("原 PDF 在左侧，解析后的正文、公式和图表将在这里显示。"); color: pane.muted; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    Label {
                        visible: pane.state.hasPage && !pane.state.approved
                        text: qsTr("逐段对照左侧 PDF。识别不一致时，直接修改下方原文并保存；全部处理完后点击顶部“本页核对通过”。")
                        color: pane.ink; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.Wrap
                    }
                    Repeater {
                        id: blocksRepeater
                        model: pane.state.blocks
                        delegate: ColumnLayout {
                            id: block
                            required property var modelData
                            required property int index
                            property var witness: modelData.witness || ({
                                status: "",
                                text: "",
                                similarity: 0,
                                reason: ""
                            })
                            property bool needsSave: !modelData.visual && sourceEditor.text !== modelData.source
                            Layout.fillWidth: true; spacing: 6
                            Image {
                                visible: block.modelData.visual
                                source: visible ? block.modelData.image : ""
                                Layout.fillWidth: true
                                Layout.preferredHeight: visible && sourceSize.width > 0 ? Math.min(translationView.availableWidth, sourceSize.width) * sourceSize.height / sourceSize.width : 0
                                fillMode: Image.PreserveAspectFit
                            }
                            Label {
                                visible: !block.modelData.visual
                                text: block.modelData.reviewed ? qsTr("原文 · 已人工修正")
                                      : block.modelData.origin === "native" ? qsTr("原文 · 文本层（可编辑）")
                                      : qsTr("原文 · OCR 待核对（可编辑）")
                                font.pixelSize: 11
                                color: block.modelData.reviewed ? pane.ink : pane.muted
                            }
                            TextArea {
                                id: sourceEditor
                                visible: !block.modelData.visual; Layout.fillWidth: true
                                text: block.modelData.source; textFormat: TextEdit.PlainText
                                color: pane.muted; font.pixelSize: 14; wrapMode: TextEdit.Wrap; selectByMouse: true
                                readOnly: pane.state.busy; background: Rectangle { color: pane.surface; radius: 5 }
                            }
                            Label {
                                visible: !block.modelData.visual && !!block.witness.status
                                         && block.witness.status !== "skipped"
                                         && block.witness.status !== "unavailable"
                                text: block.modelData.reviewed ? qsTr("第二识别 · 已由人工修正覆盖")
                                      : block.witness.status === "agree"
                                        ? qsTr("第二识别 · 基本一致（%1%）").arg(Math.round((block.witness.similarity || 0) * 100))
                                        : qsTr("第二识别 · 请核对（%1%）：%2").arg(Math.round((block.witness.similarity || 0) * 100)).arg(block.witness.reason || qsTr("存在差异"))
                                color: block.witness.status === "agree" || block.modelData.reviewed ? pane.muted : pane.ink
                                font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap
                            }
                            TextArea {
                                visible: !block.modelData.visual && !block.modelData.reviewed
                                         && !!block.witness.text && block.witness.status !== "agree"
                                Layout.fillWidth: true
                                text: block.witness.text || ""; textFormat: TextEdit.PlainText
                                color: pane.muted; font.pixelSize: 13; wrapMode: TextEdit.Wrap
                                selectByMouse: true; readOnly: true
                                background: Rectangle { color: pane.surface; radius: 5; opacity: .72 }
                            }
                            RowLayout {
                                visible: !block.modelData.visual && !block.modelData.reviewed
                                         && !!block.witness.text && block.witness.status !== "agree"
                                ResearchAction { text: qsTr("采用第二识别到编辑框"); enabled: !pane.state.busy; onClicked: sourceEditor.text = block.witness.text }
                                Item { Layout.fillWidth: true }
                            }
                            RowLayout {
                                visible: block.needsSave
                                ResearchAction { text: qsTr("保存修正后的原文"); enabled: !pane.state.busy; onClicked: research.saveSource(block.index, sourceEditor.text) }
                                ResearchAction { text: qsTr("放弃本段修改"); enabled: !pane.state.busy; onClicked: sourceEditor.text = block.modelData.source }
                                Item { Layout.fillWidth: true }
                            }
                            TextArea {
                                visible: !block.modelData.visual; Layout.fillWidth: true
                                text: block.modelData.translation || block.modelData.translation_error || qsTr("待翻译"); textFormat: TextEdit.PlainText
                                color: block.modelData.translation ? pane.ink : pane.muted
                                font.family: "Songti SC"; font.pixelSize: 20; wrapMode: TextEdit.Wrap
                                selectByMouse: true; readOnly: true; background: Item {}
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: pane.line }
                        }
                    }
                }
                }
            }
        }
    }
}
