import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var backend
    required property color ink
    required property color muted
    property var models: backend.modelSettings
    property string editId: ""
    readonly property bool editingPreset: !!panel.models.profile(panel.editId).builtin
    property int revision: 0
    Layout.fillWidth: true
    spacing: 10
    Connections { target: panel.models; function onChanged() { panel.revision++ } }

    Text { text: qsTr("模型与 API"); color: panel.ink; font.pixelSize: 17; font.weight: Font.DemiBold }
    Label {
        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
        text: qsTr("兼容 Chat Completions 的本机、自托管或云端接口。翻译、校阅与问 AI 可分别选择。模型名称请按服务商提供的 Model ID 填写。")
    }
    ComboBox {
        id: editChoice; objectName: "modelEditChoice"; Layout.fillWidth: true
        model: [{id: "", display_label: qsTr("添加模型…")}].concat(panel.models.profiles)
        textRole: "display_label"; valueRole: "id"
        onModelChanged: Qt.callLater(function() {
            editChoice.currentIndex = Math.max(0, editChoice.indexOfValue(panel.editId))
        })
        onActivated: {
            panel.editId = currentValue
            var row = panel.models.profile(currentValue)
            nameField.text = row.label || ""
            endpointField.text = row.api_base || ""
            modelField.text = row.model || ""
            keyField.text = ""
            authRequired.checked = currentValue ? row.auth_required : true
        }
    }
    Label { text: qsTr("名称"); color: panel.ink }
    TextField { id: nameField; objectName: "modelName"; Layout.fillWidth: true; readOnly: panel.editingPreset; placeholderText: qsTr("例如：我的云端模型") }
    Label { text: qsTr("API 地址"); color: panel.ink }
    TextField { id: endpointField; objectName: "modelEndpoint"; Layout.fillWidth: true; readOnly: panel.editingPreset; placeholderText: qsTr("https://服务地址/v1（也可粘贴完整 chat/completions 地址）") }
    Label { text: qsTr("模型名称（Model ID）"); color: panel.ink }
    TextField { id: modelField; objectName: "modelID"; Layout.fillWidth: true; readOnly: panel.editingPreset; placeholderText: qsTr("填写准确模型 ID") }
    CheckBox { id: authRequired; objectName: "modelAuthRequired"; text: qsTr("此接口需要 API Key"); checked: true; enabled: !panel.editingPreset }
    TextField {
        id: keyField; objectName: "modelAPIKey"; Layout.fillWidth: true
        enabled: !panel.editingPreset; echoMode: TextInput.Password
        placeholderText: qsTr("密钥存于系统钥匙串。同地址留空可保留密钥。")
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
        text: qsTr("远端接口需要 HTTPS；本机回环地址可用 HTTP。修改接口或模型会保存为新档案，旧任务继续保留原档案。")
    }
    Button {
        objectName: "saveModelSettings"; text: qsTr("保存模型"); enabled: !panel.editingPreset
        onClicked: {
            var saved = panel.models.save(panel.editId, nameField.text, endpointField.text, modelField.text, keyField.text, authRequired.checked)
            if (saved) {
                keyField.text = ""
                panel.editId = saved
                editChoice.currentIndex = editChoice.indexOfValue(saved)
            }
        }
    }
    Button {
        objectName: "addDgxOcr"; text: qsTr("添加 DGX OCR 服务")
        onClicked: {
            panel.editId = ""
            editChoice.currentIndex = 0
            nameField.text = "DGX · PP-OCRv6"
            endpointField.text = "http://127.0.0.1:18086/v1"
            modelField.text = "pp-ocrv6-small"
            keyField.text = ""
            authRequired.checked = true
        }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
        text: qsTr("PDF OCR 使用鲸读 OCR 服务协议。DGX 私有连接需保持隧道在线，再填写服务密钥并选择 PDF OCR；页面图像会通过隧道发送到 DGX。")
    }
    Button {
        objectName: "forgetModelKey"; text: qsTr("清除此模型的 API Key")
        visible: panel.editId !== "" && !panel.editingPreset
        onClicked: { if (panel.models.forgetKey(panel.editId)) keyField.text = "" }
    }
    Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted; text: panel.models.message }

    Repeater {
        model: [
            {feature: "translation", title: qsTr("翻译模型"), material: qsTr("原文译块、邻段语境、术语表与翻译要求")},
            {feature: "review", title: qsTr("校阅模型"), material: qsTr("待核对的原文、译文、人工标注与校阅要求")},
            {feature: "ask", title: qsTr("问 AI 模型"), material: qsTr("书名、选文、最多五段原文、问题与本轮问答历史")},
            {feature: "ocr", title: qsTr("PDF OCR 服务"), material: qsTr("当前 PDF 页的完整图像；批量时依次发送各页")}
        ]
        delegate: ColumnLayout {
            id: featureRow
            required property var modelData
            Layout.fillWidth: true; spacing: 5
            property string selected: modelData.feature === "translation" ? panel.backend.translationProfile : modelData.feature === "review" ? panel.models.reviewProfile : modelData.feature === "ocr" ? panel.models.ocrProfile : panel.models.askProfile
            property var route: { panel.revision; return panel.models.profile(selected) }
            Label { text: featureRow.modelData.title; color: panel.ink; font.bold: true }
            ComboBox {
                id: featureChoice
                objectName: "modelChoice_" + featureRow.modelData.feature; Layout.fillWidth: true
                model: [{id: "", display_label: qsTr("请先添加并选择模型。") }].concat(panel.models.profiles); textRole: "display_label"; valueRole: "id"
                delegate: ItemDelegate {
                    required property int index
                    required property var modelData
                    width: featureChoice.width
                    text: modelData.display_label
                    enabled: !!modelData.id
                    highlighted: featureChoice.highlightedIndex === index
                }
                currentIndex: {
                    var rows = model || []
                    for (var i = 0; i < rows.length; ++i)
                        if (rows[i].id === featureRow.selected) return i
                    return 0
                }
                enabled: featureRow.modelData.feature !== "translation" || (!panel.backend.running && !panel.backend.taskLocked && !panel.backend.probeBusy)
                onActivated: {
                    if (!currentValue) {
                        currentIndex = indexOfValue(featureRow.selected)
                        return
                    }
                    if (featureRow.modelData.feature === "translation") panel.backend.translationProfile = currentValue
                    else panel.models.select(featureRow.modelData.feature, currentValue)
                    currentIndex = indexOfValue(featureRow.selected)
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
                text: featureRow.route.api_base ? qsTr("目的地：%1\n发送材料：%2").arg(featureRow.route.api_base).arg(featureRow.modelData.material) : qsTr("请先添加并选择模型。")
            }
            CheckBox {
                objectName: "modelConsent_" + featureRow.modelData.feature
                visible: !!featureRow.route.id && (!featureRow.route.builtin || featureRow.modelData.feature === "ocr")
                Layout.fillWidth: true
                text: qsTr("我同意此功能向以上服务发送所列材料")
                checked: { panel.revision; return panel.models.consented(featureRow.modelData.feature, featureRow.selected) }
                onClicked: panel.models.setConsent(featureRow.modelData.feature, featureRow.selected, checked)
            }
            Label {
                visible: featureRow.modelData.feature === "translation" && panel.backend.taskLocked
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
                text: qsTr("当前译本已有安全断点，模型已锁定；如需比较其他模型，请新建译本。")
            }
        }
    }
    Label {
        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: panel.muted
        text: qsTr("只在你启动功能后请求服务。云端处理和留存规则由所选服务商决定，费用由你的 API 账户承担。取消授权会停止此功能的后续请求，已发送的材料无法撤回。本机 HY-MT2 预设不上传正文。自动校阅目前支持简体和繁体中文译本，模型建议需要人工确认。")
    }
}
