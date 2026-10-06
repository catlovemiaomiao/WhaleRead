import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog { popupType: Popup.Item;
    id: dialog
    required property var assistant
    property var info: assistant.state
    property int shownSession: -1
    signal settingsRequested()
    title: qsTr("页边注 · 阅读释疑")
    modal: false
    implicitWidth: Math.min(480, parent.width - 40)
    implicitHeight: Math.min(620, parent.height - 40)
    width: implicitWidth
    height: implicitHeight
    background: Rectangle { color: "#FBF8F0"; radius: 8; border.color: "#D8DDD6" }
    x: parent.width - width - 20
    y: Math.max(20, (parent.height - height) / 2)
    closePolicy: Popup.CloseOnEscape
    // Hiding a reading annotation is not cancelling a request. Only the explicit
    // cancel action or the bounded deadline terminates it.
    Connections { target: assistant; function onOpened() {
        if (dialog.shownSession !== dialog.info.sessionId) {
            question.text = ""
            dialog.shownSession = dialog.info.sessionId
        }
        dialog.open()
        Qt.callLater(function() {
            var popupWindow = dialog.contentItem.Window.window
            if (popupWindow) { popupWindow.raise(); popupWindow.requestActivate() }
        })
    } }
    contentItem: ColumnLayout {
        Label { Layout.fillWidth: true; text: qsTr("发送到：%1 · 当前段与前后各两段原文（书籍/章节边界处可能不足五段）").arg(info.destination); wrapMode: Text.Wrap; textFormat: Text.PlainText }
        RowLayout {
            Layout.fillWidth: true
            Label { text: qsTr("问答模型") }
            ComboBox {
                id: quickProvider
                objectName: "askQuickProvider"
                Layout.fillWidth: true
                enabled: !info.busy
                model: [qsTr("已保存的自定义接口")]
                currentIndex: 0
                onActivated: {
                    if (!assistant.selectProvider("custom")) {
                        dialog.close()
                        dialog.settingsRequested()
                    }
                }
            }
            Label { text: qsTr("下一次发送生效"); color: "#687868"; font.pixelSize: 10 }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: qsTr("回答语言") }
            ComboBox {
                id: answerLocaleChoice
                objectName: "askAnswerLocale"
                Layout.fillWidth: true
                // Re-read on every notify so an in-place locale switch cannot
                // leave a stale label behind.
                // ``backend.askAI`` is the same object as ``assistant`` but is a
                // typed context property, so Qt resolves its notify signal and
                // the rows really do rebuild on an in-place locale switch.
                property var rows: backend.askAI.answerLocaleOptions
                model: rows
                textRole: "text"; valueRole: "value"
                currentIndex: Math.max(0, indexOfValue(assistant.answerLocale))
                Accessible.name: qsTr("回答语言")
                onActivated: assistant.setAnswerLocale(currentValue)
                Connections {
                    target: backend
                    function onChanged() { answerLocaleChoice.rows = backend.askAI.answerLocaleOptions }
                }
            }
            // The frozen language of a running request is shown so a mid-flight
            // switch is understandable rather than mysterious.
            Label { objectName: "askAnswerLocaleEffective"
                    visible: info.busy
                    text: qsTr("本次用 %1").arg(assistant.effectiveAnswerLocaleLabel)
                    color: "#687868"; font.pixelSize: 10 }
        }
        Label { Layout.fillWidth: true; visible: info.busy || info.notice.length > 0; text: info.notice || qsTr("正在等待回答。可以先返回阅读；底部任务条会保留本次问题，随时查看或取消。"); color: "#557362"; wrapMode: Text.Wrap }
        TextArea { Layout.fillWidth: true; Layout.preferredHeight: 66; text: info.quote; readOnly: true; selectByMouse: true; wrapMode: Text.Wrap; textFormat: TextEdit.PlainText }
        CheckBox { id: showContext; text: qsTr("查看实际发送的原文上下文") }
        ScrollView { visible: showContext.checked; Layout.fillWidth: true; Layout.preferredHeight: 145
            TextArea { text: info.context; readOnly: true; selectByMouse: true; wrapMode: Text.Wrap; textFormat: TextEdit.PlainText }
        }
        TextArea { id: question; objectName: "askQuestion"; Layout.fillWidth: true; Layout.preferredHeight: 76; enabled: !info.busy; placeholderText: qsTr("想了解这个词的文化含义、句子隐喻，还是人物的意思？可继续追问。"); wrapMode: Text.Wrap; selectByMouse: true }
        RowLayout {
            Button { objectName: "askSend"; text: info.busy ? qsTr("回答中…") : qsTr("发送问题"); enabled: !info.busy && info.context.length > 0 && question.text.trim().length > 0; onClicked: assistant.ask(question.text) }
            Button { objectName: "askCancel"; text: qsTr("取消请求"); visible: info.busy; onClicked: assistant.cancel() }
            Label { Layout.fillWidth: true; text: info.error; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        }
        ScrollView { Layout.fillWidth: true; Layout.fillHeight: true
            TextArea { objectName: "askAnswer"; text: info.answer || qsTr("回答仅作阅读解释，不会修改正文。模型可能出错；超出上下文的来历应进一步核实。"); readOnly: true; selectByMouse: true; wrapMode: Text.Wrap; textFormat: TextEdit.PlainText }
        }
        Label {
            Layout.fillWidth: true; visible: info.truncated
            text: qsTr("模型自动续答后仍达到长度上限；这份不完整回答不会被加入随笔。可继续追问，或缩小问题后重试。")
            color: "#A16B40"; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        Label {
            Layout.fillWidth: true; visible: info.noteMessage.length > 0
            text: info.noteMessage; color: "#557362"; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
        RowLayout {
            Layout.fillWidth: true
            Button { objectName: "askAPISettingsShortcut"; text: qsTr("API 接口设置"); onClicked: { dialog.close(); dialog.settingsRequested() } }
            Item { Layout.fillWidth: true }
            Button {
                objectName: "askSaveToNote"
                visible: info.answer.length > 0
                text: info.noteSaved ? qsTr("✓ 已加入随笔") : info.truncated ? qsTr("回答未完整") : qsTr("追加到本段随笔")
                enabled: info.canSaveToNote
                ToolTip.visible: hovered && !enabled && !info.noteSaved
                ToolTip.text: info.truncated ? qsTr("回答仍被模型长度上限截断，未写入随笔") : qsTr("只有从书中稳定段落发起的完整回答才能自动加入随笔")
                onClicked: assistant.saveAnswerToNote()
            }
            Button { objectName: "askReturnToReading"; text: info.busy ? qsTr("返回阅读 · 后台等待") : qsTr("返回阅读"); onClicked: dialog.close() }
        }
    }
}
