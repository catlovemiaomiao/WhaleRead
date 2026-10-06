import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Popup { popupType: Popup.Item;
    id: panel
    objectName: "reviewPanel"
    required property var editor
    anchors.centerIn: Overlay.overlay
    // Explicit sizing keeps the review viewport stable inside the main window.
    implicitWidth: Math.min(parent.width - 36,
                            Screen.desktopAvailableWidth > 0 ? Screen.desktopAvailableWidth - 48 : 1030,
                            1030)
    implicitHeight: Math.min(parent.height - 36,
                             Screen.desktopAvailableHeight > 0 ? Screen.desktopAvailableHeight - 48 : 810,
                             810)
    width: implicitWidth
    height: implicitHeight
    padding: 24; modal: true; focus: true
    property bool nightMode: backend.backgroundId === "night"
    property color accent: backend.themeId === "ocean" ? "#315E78" : backend.themeId === "amber" ? "#805B37" : backend.themeId === "sakura" ? "#7C4859" : "#2E5A45"
    property color accentSoft: backend.themeId === "ocean" ? "#DCE9EF" : backend.themeId === "amber" ? "#EEE2D2" : backend.themeId === "sakura" ? "#F0DFE3" : "#DDE9DE"
    property color surface: nightMode ? "#293134" : backend.backgroundId === "warm" ? "#FBF3E5" : backend.backgroundId === "mist" ? "#F8FAFA" : "#FFFEF9"
    property color canvas: nightMode ? "#1E2527" : backend.backgroundId === "warm" ? "#F1E7D5" : backend.backgroundId === "mist" ? "#EDF1F2" : "#F8F7F0"
    property color ink: nightMode ? "#E8E4D8" : "#344B3E"
    property color muted: nightMode ? "#A8B0AE" : "#768170"
    property color line: nightMode ? "#424B4D" : "#CDD6C8"
    property string editingAnnotationId: ""
    property string editingAnnotationSource: ""
    property string editingAnnotationOriginal: ""
    property string editingAnnotationSuggestion: ""
    property bool editingAnnotationHasDraft: false
    property bool editingAnnotationSaved: false
    property bool editingAnnotationUnverified: false
    property bool resolvedAnnotationsExpanded: false
    readonly property var activeAnnotations: panel.editor.annotations.filter(function(row) { return row.status !== "resolved" })
    readonly property var resolvedAnnotations: panel.editor.annotations.filter(function(row) { return row.status === "resolved" })
    readonly property int resolvedAnnotationCount: resolvedAnnotations.length
    readonly property real annotationViewportHeight: annotationScroll.availableHeight
    readonly property real annotationBodyHeight: annotationContent.implicitHeight
    readonly property real annotationContentY: annotationScroll.contentItem ? annotationScroll.contentItem.contentY : 0
    background: Rectangle { color: panel.canvas; radius: 18; border.color: panel.line }
    function showAnnotations() { panel.resolvedAnnotationsExpanded = false; tabs.currentIndex = 2; panel.open() }
    function scrollAnnotationsTo(value) {
        if (annotationScroll.contentItem)
            annotationScroll.contentItem.contentY = Math.max(0, Math.min(value, annotationBodyHeight - annotationViewportHeight))
    }
    function editAnnotation(row) {
        panel.editingAnnotationId = row.id
        panel.editingAnnotationSource = row.source_text || ""
        panel.editingAnnotationOriginal = row.translation || ""
        panel.editingAnnotationSaved = !!row.proposed_text
        panel.editingAnnotationUnverified = !panel.editingAnnotationSaved && !!row.has_unverified_candidate
        panel.editingAnnotationSuggestion = row.result && row.result.suggestion ? row.result.suggestion
                                            : row.unverified_suggestion || ""
        annotationEditText.text = row.proposed_text || row.default_draft || row.translation || ""
        panel.editingAnnotationHasDraft = annotationEditText.text !== panel.editingAnnotationOriginal
        annotationEditor.open()
    }
    function highlightDraftDifference() {
        var before = panel.editingAnnotationOriginal
        var after = annotationEditText.text
        var prefix = 0
        while (prefix < before.length && prefix < after.length && before.charAt(prefix) === after.charAt(prefix))
            prefix++
        var suffix = 0
        while (suffix < before.length - prefix && suffix < after.length - prefix
               && before.charAt(before.length - 1 - suffix) === after.charAt(after.length - 1 - suffix))
            suffix++
        var end = after.length - suffix
        annotationEditText.forceActiveFocus()
        if (end > prefix)
            annotationEditText.select(prefix, end)
        else if (after.length > 0 && panel.editingAnnotationHasDraft)
            annotationEditText.select(Math.max(0, prefix - 1), Math.min(after.length, prefix + 1))
        else
            annotationEditText.deselect()
    }
    function annotationStatus(row) {
        if (row.status === "resolved") return qsTr("已解决")
        if (row.status === "stale") return qsTr("原译已变动")
        if (row.status === "failed")
            return row.has_unverified_candidate ? qsTr("模型草稿待确认")
                 : row.error_bucket === "evidence" ? qsTr("证据待确认") : qsTr("复查未完成")
        if (!row.result) return qsTr("待复查")
        return row.result.verdict === "issue" ? qsTr("建议修改") : row.result.verdict === "keep" ? qsTr("建议保留") : qsTr("待你确认")
    }
    function annotationError(message, code) {
        if (code === "annotation.evidence_not_exact")
            return qsTr("模型返回的原文证据无法逐字核验，本条已转人工确认；译文没有被自动修改。")
        if (code === "annotation.reason_not_specific")
            return qsTr("模型没有给出聚焦于标注的有效复核依据，本条需人工确认。")
        return message || ""
    }
    component Copy: TextEdit {
        id: copyText
        color: panel.ink; font.family: "PingFang SC"; font.pixelSize: 13
        wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText
        readOnly: true; selectByMouse: true; selectByKeyboard: true; persistentSelection: true
        activeFocusOnTab: false; cursorVisible: false; padding: 0
        selectionColor: panel.nightMode ? "#596563" : "#D9E3D6"
        selectedTextColor: panel.ink
        TapHandler {
            acceptedButtons: Qt.RightButton
            onTapped: copyMenu.popup()
        }
        Menu { popupType: Popup.Window;
            id: copyMenu
            objectName: "reviewCopyMenu"
            MenuItem { text: qsTr("复制"); enabled: copyText.selectedText.length > 0; onTriggered: copyText.copy() }
            MenuItem { text: qsTr("全选"); onTriggered: copyText.selectAll() }
        }
    }
    component Command: Button {
        id: command
        font.family: "PingFang SC"; font.pixelSize: 13
        contentItem: Text { text: command.text; font: command.font; color: command.enabled ? panel.ink : "#8E988A"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 8; color: command.down ? Qt.darker(panel.accentSoft, 1.12) : command.hovered ? Qt.lighter(panel.accentSoft, 1.04) : panel.accentSoft }
        leftPadding: 16; rightPadding: 16
        implicitHeight: 38
    }
    component PageTab: TabButton {
        id: pageTab
        contentItem: Text { text: pageTab.text; color: pageTab.checked ? "#FAFCF6" : panel.ink; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 7; color: pageTab.checked ? panel.accent : panel.surface }
        implicitHeight: 36
    }
    component AnnotationCard: Rectangle {
        id: noteCard
        required property var modelData
        property bool expanded: false
        Layout.fillWidth: true; implicitHeight: noteColumn.implicitHeight + 28
        color: modelData.status === "resolved" ? (panel.nightMode ? "#333A3B" : "#F0F1E9") : panel.surface; radius: 12; border.color: panel.line
        ColumnLayout {
            id: noteColumn; anchors.fill: parent; anchors.margins: 14; spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Copy { Layout.fillWidth: true; font.weight: Font.DemiBold; text: qsTr("第 %1 段 · %2").arg(modelData.segment_id).arg(modelData.category) }
                Copy { color: "#826545"; text: panel.annotationStatus(modelData) }
            }
            Copy { Layout.fillWidth: true; text: qsTr("标注：“%1”").arg(modelData.quote); color: "#7B6354" }
            Copy { Layout.fillWidth: true; visible: modelData.note.length > 0; text: qsTr("你的备注：%1").arg(modelData.note) }
            Copy { Layout.fillWidth: true; visible: !!modelData.result; text: modelData.result ? qsTr("复查：%1").arg(modelData.result.reason) : "" }
            Copy { Layout.fillWidth: true; visible: !!modelData.reviewer_label; text: qsTr("复核模型：%1").arg(modelData.reviewer_label || ""); color: panel.muted; font.pixelSize: 11 }
            Copy { Layout.fillWidth: true; visible: !!modelData.previous_review_text; text: modelData.previous_review_text || ""; color: "#826545" }
            Copy { Layout.fillWidth: true; visible: !!modelData.result && !!modelData.result.suggestion; text: modelData.result ? qsTr("建议：%1").arg(modelData.result.suggestion) : ""; color: "#31543B" }
            Copy {
                Layout.fillWidth: true; visible: !!modelData.has_unverified_candidate && !modelData.proposed_text
                text: qsTr("模型曾生成一份修改稿，但原文证据字段未通过程序核验。该稿仅供你查看，不算复核通过，也不会自动应用。")
                color: "#A16B40"
            }
            Copy { Layout.fillWidth: true; visible: !!modelData.has_unverified_candidate && !!modelData.unverified_suggestion && !modelData.proposed_text; text: qsTr("未验证草稿说明：") + modelData.unverified_suggestion; color: panel.muted }
            Copy { Layout.fillWidth: true; visible: !!modelData.proposed_text; text: modelData.proposed_text ? qsTr("已保存修改稿：\n%1").arg(modelData.proposed_text) : ""; color: panel.accent }
            Copy { Layout.fillWidth: true; visible: !!modelData.error; text: panel.annotationError(modelData.error, modelData.error_code); color: "#A16B40" }
            RowLayout {
                Command { text: noteCard.expanded ? qsTr("收起语境") : qsTr("原文与译文"); onClicked: noteCard.expanded = !noteCard.expanded }
                Item { Layout.fillWidth: true }
                Command { objectName: "recheck_" + modelData.id; text: qsTr("重新复查")
                          enabled: panel.editor.canRun && panel.editor.capabilitySupported
                          onClicked: panel.editor.reviewAnnotation(modelData.id)
                          ToolTip.visible: hovered && !panel.editor.capabilitySupported
                          ToolTip.text: panel.editor.capabilityReason }
                Command { objectName: "editAnnotation_" + modelData.id; text: modelData.proposed_text ? qsTr("继续修改…") : modelData.has_unverified_candidate ? qsTr("查看未验证草稿…") : modelData.default_draft !== modelData.translation ? qsTr("查看建议修改…") : qsTr("修改译文…"); enabled: !panel.editor.busy && modelData.status !== "stale"; onClicked: panel.editAnnotation(modelData) }
                Command { objectName: "applyAnnotation_" + modelData.id; visible: !!modelData.proposed_text && modelData.status !== "resolved"; text: panel.editor.exportCopy ? qsTr("另存并打开") : qsTr("应用并热更新"); enabled: panel.editor.canRun; onClicked: panel.editor.publishAnnotation(modelData.id) }
                Command { objectName: "resolve_" + modelData.id; visible: !modelData.proposed_text || modelData.status === "resolved"; text: modelData.status === "resolved" ? qsTr("重新打开") : qsTr("标记已解决"); enabled: !panel.editor.busy; onClicked: panel.editor.resolveAnnotation(modelData.id, modelData.status !== "resolved") }
            }
            Copy { visible: noteCard.expanded; Layout.fillWidth: true; text: qsTr("原文\n%1").arg(modelData.source_text); color: "#687868" }
            Copy { visible: noteCard.expanded; Layout.fillWidth: true; text: qsTr("译文\n%1").arg(modelData.translation) }
            Copy { visible: noteCard.expanded && !!modelData.result; Layout.fillWidth: true; text: modelData.result ? qsTr("复查证据：%1").arg(modelData.result.source_evidence || qsTr("未找到确定证据")) : ""; color: "#31543B" }
        }
    }
    onClosed: {
        resolvedAnnotationsExpanded = false
        annotationEditor.close()
    }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Copy { text: qsTr("给好译文，再一点斟酌"); font.family: "Songti SC"; font.pixelSize: 25; Layout.fillWidth: true }
            Command { text: qsTr("关闭"); onClicked: panel.close() }
        }
        Copy { objectName: "reviewIntroCopy"; Layout.fillWidth: true; text: qsTr("先列出重复实体，再由所选校阅模型核对译名。人工标注由 %1 复核。").arg(panel.editor.annotationReviewerLabel); color: panel.muted }
        RowLayout {
            Layout.fillWidth: true
            TextField {
                objectName: "reviewFocusInput"; Layout.fillWidth: true
                placeholderText: qsTr("定点核对原文名称或短语（可选，如 white raven）；留空检查常见专名")
                text: panel.editor.focusTerm; enabled: !panel.editor.busy; selectByMouse: true
                onTextEdited: panel.editor.focusTerm = text
            }
            Command { objectName: "auditButton"; text: qsTr("一键准备校订")
                      enabled: panel.editor.canRun && panel.editor.capabilitySupported
                      onClicked: panel.editor.prepare()
                      ToolTip.visible: hovered && !panel.editor.capabilitySupported
                      ToolTip.text: panel.editor.capabilityReason }
            Command { text: qsTr("暂停"); visible: panel.editor.busy; onClicked: panel.editor.stop() }
        }
        Copy { Layout.fillWidth: true; text: panel.editor.status; font.weight: Font.Medium }
        Copy { objectName: "capabilityNotice"; Layout.fillWidth: true
               visible: !panel.editor.capabilitySupported
               text: panel.editor.capabilityReason; color: "#A16B40"; font.pixelSize: 11 }
        Copy { Layout.fillWidth: true; visible: panel.editor.statusDetail.length > 0; text: panel.editor.statusDetail; color: panel.muted }
        Copy { Layout.fillWidth: true; text: panel.editor.coverage || qsTr("全书检查最多 160 个高频专名，每项抽查最多六处语境；没有报告不代表全书无误。可输入原词定点检查。"); font.pixelSize: 11; color: "#7B8575" }
        TabBar {
            id: tabs; Layout.fillWidth: true
            PageTab { text: qsTr("译名清单 · %1").arg(panel.editor.issues.length) }
            PageTab { text: qsTr("改动详情 · %1").arg(panel.editor.edits.length) }
            PageTab { objectName: "annotationTab"; text: qsTr("人工标注 · 待处理 %1").arg(panel.activeAnnotations.length) }
        }
        StackLayout {
            id: reviewPages
            objectName: "reviewPages"
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.minimumHeight: 0; Layout.preferredHeight: 1
            clip: true
            ScrollView {
                id: issueScroll; clip: true; contentWidth: availableWidth
                contentHeight: issueContent.implicitHeight
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ColumnLayout {
                    id: issueContent
                    width: issueScroll.availableWidth; spacing: 12
                    Copy { visible: panel.editor.issues.length === 0; Layout.fillWidth: true; text: qsTr("检查后，有原文与译文证据的建议会出现在这里。合理的语境变化可以保持原样。") }
                    Repeater {
                        model: panel.editor.issues
                        delegate: Rectangle {
                            id: issueCard
                            required property var modelData
                            property bool expanded: false
                            Layout.fillWidth: true; implicitHeight: issueColumn.implicitHeight + 28
                            color: panel.surface; radius: 12; border.color: panel.line
                            ColumnLayout {
                                id: issueColumn; anchors.fill: parent; anchors.margins: 14; spacing: 9
                                RowLayout {
                                    CheckBox {
                                        objectName: "selectIssue_" + modelData.id
                                        text: qsTr("统一此名称"); checked: modelData.selected; enabled: !panel.editor.busy
                                        onToggled: panel.editor.selectIssue(modelData.id, checked)
                                    }
                                    Copy { text: modelData.source; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                    Copy { text: qsTr("建议译名"); font.pixelSize: 11; color: "#7B8575" }
                                    TextField {
                                        objectName: "preferred_" + modelData.id
                                        text: modelData.preferred; enabled: !panel.editor.busy; selectByMouse: true
                                        Layout.preferredWidth: 150
                                        onEditingFinished: panel.editor.setPreferred(modelData.id, text)
                                    }
                                }
                                Copy {
                                    Layout.fillWidth: true
                                    text: qsTr("现有译法：%1").arg(modelData.variants.map(function(v) {
                                        return v + (modelData.variant_counts
                                                    ? qsTr("（%n 段）", "variant passage count",
                                                           modelData.variant_counts[v] || 0) : "")
                                    }).join(" / "))
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Copy {
                                        Layout.fillWidth: true; color: "#687868"
                                        text: (modelData.user_preference ? qsTr("已保留你的偏好 · ") : "")
                                              + (modelData.decision === "uncertain" ? qsTr("拿不准，暂不改 · ") : modelData.decision === "skip" ? qsTr("保持原译 · ") : "")
                                              + (modelData.display_reason || modelData.group_reason || modelData.reason)
                                    }
                                    Command { text: issueCard.expanded ? qsTr("收起语境") : qsTr("查看语境"); onClicked: issueCard.expanded = !issueCard.expanded }
                                }
                                Repeater {
                                    model: issueCard.expanded ? modelData.evidence : []
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true; spacing: 4
                                        Copy { text: qsTr("第 %1 段 · 现译「%2」").arg(modelData.id).arg(modelData.target); color: "#826545"; font.pixelSize: 11 }
                                        Copy { Layout.fillWidth: true; text: modelData.source_text; color: "#687868"; font.pixelSize: 12 }
                                        Copy { Layout.fillWidth: true; text: modelData.translation }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            ScrollView {
                id: editScroll; clip: true; contentWidth: availableWidth
                contentHeight: editContent.implicitHeight
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ColumnLayout {
                    id: editContent
                    width: editScroll.availableWidth; spacing: 12
                    Copy { Layout.fillWidth: true; text: qsTr("纯译名替换已为你勾选，不用逐段点选。含额外改写的段落不勾选；可查看后决定。没有勾选的内容保留原译。") }
                    Repeater {
                        model: panel.editor.edits
                        delegate: Rectangle {
                            id: editCard
                            required property var modelData
                            property bool expanded: !modelData.safe
                            Layout.fillWidth: true; implicitHeight: editColumn.implicitHeight + 28
                            color: panel.surface; radius: 12; border.color: panel.line
                            ColumnLayout {
                                id: editColumn; anchors.fill: parent; anchors.margins: 14; spacing: 8
                                RowLayout {
                                    Layout.fillWidth: true
                                    CheckBox {
                                        objectName: "acceptEdit_" + modelData.id
                                        text: qsTr("第 %1 段 · %2").arg(modelData.id).arg(modelData.safe ? qsTr("仅改译名") : qsTr("有额外改写，请核对"))
                                        checked: modelData.accepted; enabled: !panel.editor.busy
                                        onToggled: panel.editor.selectEdit(modelData.id, checked)
                                    }
                                    Item { Layout.fillWidth: true }
                                    Command { text: editCard.expanded ? qsTr("收起对照") : qsTr("展开对照"); onClicked: editCard.expanded = !editCard.expanded }
                                }
                                Copy { Layout.fillWidth: true; text: (modelData.rules || []).map(function(r) { return r.previous.join(" / ") + " → " + r.target }).join("；"); color: "#31543B" }
                                Copy { visible: editCard.expanded; Layout.fillWidth: true; text: qsTr("原文\n%1").arg(modelData.source_text); color: "#6C7A69"; font.pixelSize: 12 }
                                RowLayout {
                                    visible: editCard.expanded; Layout.fillWidth: true; spacing: 18
                                    Copy { Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; text: qsTr("现有译文\n\n%1").arg(modelData.before); color: "#7B6354" }
                                    Copy { Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop; text: qsTr("建议校订\n\n%1").arg(modelData.after); color: "#31543B" }
                                }
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.minimumHeight: 0; Layout.preferredHeight: 1
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: qsTr("人工标注复核"); font.weight: Font.DemiBold }
                    ComboBox {
                        objectName: "annotationReviewerChoice"
                        Layout.preferredWidth: 240
                        model: backend.modelSettings.profiles
                        textRole: "display_label"; valueRole: "id"
                        currentIndex: indexOfValue(panel.editor.annotationReviewer)
                        enabled: !panel.editor.busy
                        onActivated: panel.editor.annotationReviewer = currentValue
                    }
                    Copy { Layout.fillWidth: true; text: panel.editor.annotationReviewerLabel; color: panel.muted; font.pixelSize: 11 }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Copy { Layout.fillWidth: true; text: qsTr("选中阅读文字即可标注。复查会给出原文证据和修改建议，由你判断是否处理。") }
                    Command { objectName: "reviewAnnotationsButton"; text: qsTr("复查待处理标注")
                              enabled: panel.editor.canRun && panel.editor.capabilitySupported
                                      && panel.activeAnnotations.length > 0
                              onClicked: panel.editor.reviewAnnotation("")
                              ToolTip.visible: hovered && !panel.editor.capabilitySupported
                              ToolTip.text: panel.editor.capabilityReason }
                }
                Copy { Layout.fillWidth: true; visible: panel.editor.annotationMessage.length > 0; text: panel.editor.annotationMessage; color: panel.accent }
                Copy { Layout.fillWidth: true; visible: panel.editor.annotationMessageDetail.length > 0; text: panel.editor.annotationMessageDetail; color: panel.muted }
                ScrollView {
                    id: annotationScroll
                    objectName: "annotationScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Layout.minimumHeight: 0; Layout.preferredHeight: 1
                    clip: true; contentWidth: availableWidth
                    contentHeight: annotationContent.implicitHeight
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ScrollBar.vertical.policy: ScrollBar.AlwaysOn
                    ColumnLayout {
                        id: annotationContent
                        objectName: "annotationContent"
                        width: annotationScroll.availableWidth; spacing: 12
                        Copy { visible: panel.editor.annotations.length === 0; Layout.fillWidth: true; text: qsTr("还没有人工标注。在本书译文中选中文字，点击“标注…”或右键选择标注，即可保存疑问。") }
                        Copy { visible: panel.editor.annotations.length > 0 && panel.activeAnnotations.length === 0; Layout.fillWidth: true; text: qsTr("当前没有待处理标注。"); color: panel.muted }
                        Repeater {
                            model: panel.activeAnnotations
                            delegate: AnnotationCard {}
                        }
                        Command {
                            objectName: "resolvedAnnotationsToggle"
                            visible: panel.resolvedAnnotationCount > 0
                            Layout.fillWidth: true
                            text: (panel.resolvedAnnotationsExpanded ? "⌄ " : "› ")
                                  + qsTr("已解决标注 · %n", "resolved annotation count",
                                         panel.resolvedAnnotationCount)
                                  + (panel.resolvedAnnotationsExpanded ? qsTr("（点击收起）") : qsTr("（点击展开）"))
                            onClicked: panel.resolvedAnnotationsExpanded = !panel.resolvedAnnotationsExpanded
                        }
                        Repeater {
                            model: panel.resolvedAnnotationsExpanded ? panel.resolvedAnnotations : []
                            delegate: AnnotationCard {}
                        }
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: panel.line }
        RowLayout {
            Layout.fillWidth: true
            CheckBox {
                id: exportCopyToggle
                objectName: "exportCopyCheck"
                text: qsTr("另存为独立校订版")
                checked: panel.editor.exportCopy; enabled: !panel.editor.busy
                onToggled: panel.editor.exportCopy = checked
                palette.windowText: panel.ink; palette.text: panel.ink; palette.highlight: panel.accent
            }
            Copy { Layout.fillWidth: true; text: exportCopyToggle.checked ? qsTr("本次生成新文件并打开，不更新当前阅读版。") : qsTr("默认更新当前阅读版并立即在阅读栏显示；机器译文断点与外语源文件不变。"); color: panel.muted; font.pixelSize: 11 }
        }
        RowLayout {
            Layout.fillWidth: true
            Command { objectName: "previewButton"; visible: tabs.currentIndex !== 2; text: qsTr("按我的调整重新预览"); enabled: panel.editor.canRun && panel.editor.issues.length > 0; onClicked: { panel.editor.preview(); tabs.currentIndex = 1 } }
            Item { Layout.fillWidth: true }
            Command { objectName: "readEditedButton"; text: qsTr("打开上次校订"); visible: panel.editor.output.length > 0; onClicked: { panel.editor.readOutput(); panel.close() } }
            Command { objectName: "publishEditsButton"; visible: tabs.currentIndex !== 2; text: qsTr("%1 · %n 段", "ready passage count", panel.editor.readyCount).arg(panel.editor.exportCopy ? qsTr("另存并打开") : qsTr("应用并热更新")); enabled: panel.editor.canRun && panel.editor.readyCount > 0; onClicked: panel.editor.publish() }
        }
    }
    Popup { popupType: Popup.Item;
        id: annotationEditor
        objectName: "annotationEditor"
        anchors.centerIn: Overlay.overlay
        implicitWidth: Math.min(720, panel.width - 54)
        implicitHeight: Math.min(650, panel.height - 54)
        width: implicitWidth; height: implicitHeight
        padding: 22; modal: true; focus: true
        closePolicy: Popup.CloseOnEscape
        onOpened: Qt.callLater(panel.highlightDraftDifference)
        background: Rectangle { color: panel.canvas; radius: 16; border.color: panel.line }
        contentItem: ColumnLayout {
            spacing: 11
            RowLayout {
                Layout.fillWidth: true
                Copy { text: qsTr("修改整段译文"); font.family: "Songti SC"; font.pixelSize: 22; Layout.fillWidth: true }
                Command { text: qsTr("取消"); onClicked: annotationEditor.close() }
            }
            Copy {
                Layout.fillWidth: true
                text: panel.editingAnnotationSaved
                      ? qsTr("这是你已保存的修改稿；当前选中的是新旧差异。继续编辑后仍需保存，再点击“应用并热更新”。")
                      : panel.editingAnnotationUnverified
                        ? qsTr("已预填模型草稿，但它的原文证据未通过程序核验。请先对照原文判断；只有你点“保存修改稿”后，才会进入可应用状态。")
                        : panel.editingAnnotationHasDraft
                          ? qsTr("已预填通过格式与证据校验的建议稿，当前选中的是新旧差异；可以直接保存，也可以继续改。保存后需点击“应用并热更新”。")
                          : qsTr("这条记录没有可安全套用的完整建议稿，请直接修改。保存后需点击“应用并热更新”。")
                color: panel.editingAnnotationUnverified ? "#A16B40" : panel.muted
            }
            Rectangle {
                visible: panel.editingAnnotationSuggestion.length > 0
                Layout.fillWidth: true; implicitHeight: suggestionText.implicitHeight + 22
                radius: 9; color: panel.accentSoft; border.color: panel.accent
                Copy { id: suggestionText; anchors.fill: parent; anchors.margins: 11; text: qsTr("%1%2").arg(panel.editingAnnotationUnverified ? qsTr("未验证草稿说明：") : qsTr("复查建议：")).arg(panel.editingAnnotationSuggestion); color: panel.nightMode ? "#24312B" : panel.accent }
            }
            Copy { Layout.fillWidth: true; Layout.maximumHeight: 92; clip: true; text: qsTr("原文\n%1").arg(panel.editingAnnotationSource); color: panel.muted; font.pixelSize: 12 }
            TextArea {
                id: annotationEditText
                objectName: "annotationEditText"
                Layout.fillWidth: true; Layout.fillHeight: true
                wrapMode: TextEdit.Wrap; selectByMouse: true; persistentSelection: true; color: panel.ink
                selectionColor: panel.nightMode ? "#4B5552" : "#E5DFC8"; selectedTextColor: panel.ink
                font.family: "Songti SC"; font.pixelSize: 15; padding: 13
                background: Rectangle { radius: 10; color: panel.surface; border.color: annotationEditText.activeFocus ? panel.accent : panel.line }
            }
            RowLayout {
                Layout.fillWidth: true
                Copy { Layout.fillWidth: true; text: qsTr("请直接改完整段落，不能留空。"); color: panel.muted; font.pixelSize: 11 }
                Command {
                    objectName: "saveAnnotationEdit"
                    text: qsTr("保存修改稿"); enabled: annotationEditText.text.trim().length > 0 && !panel.editor.busy
                    onClicked: if (panel.editor.saveAnnotationEdit(panel.editingAnnotationId, annotationEditText.text)) annotationEditor.close()
                }
            }
        }
    }
    Connections {
        target: panel.editor
        function onEditionApplied() { annotationEditor.close(); panel.close() }
    }
}
