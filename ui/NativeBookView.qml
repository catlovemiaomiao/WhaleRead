import QtQuick
import QtWebView
import "BookBridge.js" as Bridge

Item {
    id: view
    property url url
    property real zoomFactor: 1
    property size contentsSize: Qt.size(0, 0)
    property point scrollPosition: Qt.point(0, 0)
    property bool linked: false
    // Set by the EPUB pane while immersive reading owns this surface.  The
    // page then maps the common reading keys itself, because a focused native
    // WKWebView consumes key events before Qt Quick can see them.
    property bool readingKeys: false
    property bool ready: false
    property bool polling: false
    property int activePolls: 12
    property real lastPolledX: -1
    property real lastPolledY: -1
    // Esc exits immersion through the bridge event instead of a QML Shortcut:
    // while the native view holds first responder, Qt never sees the key.
    signal escapeRequested()
    // The native view keeps a tight poll while it owns the keyboard so bridge
    // events (escape, notes, links) are not delayed by the idle cadence.
    property int pollInterval: (activePolls > 0 || nativeView.activeFocus) ? (linked ? 60 : 120) : 500
    signal loaded()
    signal failed(string message)
    signal contextRequested(string quote, real x, real y)
    signal noteRequested(string identity, real x, real y)
    signal selectionChanged(real at)
    signal linkRequested(string url)
    signal userScrolled(real fraction)
    signal interacted()
    // Same call shape as the previous reader, with no Chromium world dependency.
    function runJavaScript(script, world, callback) {
        nativeView.runJavaScript(script, function(result) {
            if (callback) callback(result)
        })
    }
    function copySelection() {
        nativeView.runJavaScript("window.getSelection().toString()", function(text) {
            if (text) backend.copyReadingText(text)
        })
    }
    function pushBridgeState() {
        if (!ready) return
        runJavaScript("if(window.whaleBridge){window.whaleBridge.linked=" + (linked ? "true" : "false")
                      + ";window.whaleBridge.readingKeys=" + (readingKeys ? "true" : "false") + "}", 0)
    }
    // QQuickWebView forwards item focus to the native WKWebView, which is what
    // lets real keyboard events reach the page at all.  The hand-off only
    // happens on a real focus change: forceActiveFocus() is a no-op while this
    // item already owns QML focus, so drop focus before taking it again.
    function takeReadingFocus() {
        if (nativeView.activeFocus || nativeView.focus) nativeView.focus = false
        nativeView.forceActiveFocus()
    }
    onZoomFactorChanged: if (ready) runJavaScript("document.documentElement.style.zoom='" + zoomFactor + "'", 0)
    function wakePolling() { activePolls = 12 }
    onLinkedChanged: {
        wakePolling()
        pushBridgeState()
    }
    onReadingKeysChanged: pushBridgeState()
    onUrlChanged: { ready = false; wakePolling(); lastPolledX = -1; lastPolledY = -1 }
    WebView {
        id: nativeView
        anchors.fill: parent
        url: view.url
        settings.localStorageEnabled: false
        settings.javaScriptEnabled: true
        settings.allowFileAccess: true
        settings.localContentCanAccessFileUrls: true
        onLoadingChanged: function(request) {
            // The native view can report a failed empty URL during creation,
            // or finish a previous chapter after the reading target changed.
            // Neither event belongs to the chapter currently being displayed.
            var expected = view.url.toString()
            if (!expected || request.url.toString() !== expected) return
            if (request.status === WebView.LoadSucceededStatus) {
                runJavaScript(Bridge.install(), function(ok) {
                    if (view.url.toString() !== expected) return
                    if (ok !== 'whale-ready') { view.failed(qsTr("阅读交互初始化失败，请重新打开此节。")); return }
                    view.ready = true
                    view.wakePolling()
                    view.pushBridgeState()
                    view.runJavaScript("document.documentElement.style.zoom='" + view.zoomFactor + "'", 0)
                    view.loaded()
                })
            } else if (request.status === WebView.LoadFailedStatus) {
                view.ready = false
                view.failed(qsTr("此节暂时无法打开，请重新打开图书。"))
            }
        }
    }
    Timer {
        // Linked dual-pane scrolling needs a tighter cadence; a single reader
        // does not need to wake WebKit 25 times per second while idle.
        interval: view.pollInterval; repeat: true; running: view.ready && view.visible
        onTriggered: {
            if (view.polling) return
            view.polling = true
            var expected = view.url.toString()
            nativeView.runJavaScript(Bridge.poll(view.linked), function(raw) {
                view.polling = false
                if (!raw || !view.visible || view.url.toString() !== expected) return
                var data
                try { data = JSON.parse(raw) } catch (e) { return }
                var moved = Math.abs(data.x - view.lastPolledX) > 0.5 || Math.abs(data.y - view.lastPolledY) > 0.5
                if (moved || data.events.length > 0) view.wakePolling()
                else if (view.activePolls > 0) view.activePolls -= 1
                view.lastPolledX = data.x; view.lastPolledY = data.y
                view.contentsSize = Qt.size(data.width, data.height)
                view.scrollPosition = Qt.point(data.x, data.y)
                for (var event of data.events) {
                    if (event.type === 'activate') { view.wakePolling(); view.interacted() }
                    else if (event.type === 'context') view.contextRequested(event.quote, event.x, event.y)
                    else if (event.type === 'note') view.noteRequested(event.id, event.x, event.y)
                    else if (event.type === 'selection') view.selectionChanged(event.at)
                    else if (event.type === 'link') view.linkRequested(event.url)
                    else if (event.type === 'scroll') view.userScrolled(event.fraction)
                    else if (event.type === 'escape') view.escapeRequested()
                }
            })
        }
    }
}
