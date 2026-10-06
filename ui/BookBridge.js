.pragma library

// Single source of truth for the in-page reading key map.  Both the injected
// page script below and the isolated Node contract test consume this text.
// It is kept as source text instead of functions because QML's V4 engine does
// not implement Function.prototype.toString() (it yields "[native code]").
var readingKeySource = [
    "function readingKeyDelta(key, shift, viewportHeight) {",
    "    var page = Math.max(80, viewportHeight * 0.85);",
    "    var line = Math.max(48, viewportHeight * 0.12);",
    "    if (key === ' ' || key === 'Spacebar') return shift ? -page : page;",
    "    if (key === 'PageDown') return page;",
    "    if (key === 'PageUp') return -page;",
    "    if (key === 'ArrowDown' && !shift) return line;",
    "    if (key === 'ArrowUp' && !shift) return -line;",
    "    return null;",
    "}",
    "function readingKeyEditable(tagName, contentEditable) {",
    "    if (contentEditable) return true;",
    "    var tag = (tagName || '').toLowerCase();",
    "    return tag === 'input' || tag === 'textarea' || tag === 'select';",
    "}"
].join("\n");

// Reading keys handled inside the page.  The native macOS WKWebView is a real
// Cocoa view: once it holds first responder, key events never reach Qt Quick,
// so immersive paging has to be decided here and reported back through the
// polled event queue.
function install() {
    return `(function(){
        if(window.whaleBridge)return 'whale-ready';
        const bridge=window.whaleBridge={events:[],linked:false,readingKeys:false};
        ` + readingKeySource + `
        bridge.selection=null;
        document.addEventListener('selectionchange',()=>{
            const s=window.getSelection();
            if(s && s.rangeCount && !s.isCollapsed){
                bridge.selection=s.getRangeAt(0).cloneRange();
                bridge.events.push({type:'selection',at:Date.now()});
            } else if(document.hasFocus()) bridge.selection=null;
        });
        document.addEventListener('pointerdown',e=>{
            if(e.button===0) bridge.selection=null;
        },true);
        document.addEventListener('pointerdown',()=>bridge.events.push({type:'activate'}),true);
        document.addEventListener('focusin',()=>bridge.events.push({type:'activate'}),true);
        document.addEventListener('contextmenu',e=>{
            e.preventDefault();
            const s=window.getSelection();
            if(s && s.rangeCount && !s.isCollapsed) bridge.selection=s.getRangeAt(0).cloneRange();
            bridge.events.push({type:'context',quote:s.toString(),x:e.clientX,y:e.clientY});
        });
        document.addEventListener('click',e=>{
            const note=e.target.closest('[data-whale-note-id]');
            if(note){
                e.preventDefault();e.stopPropagation();
                bridge.events.push({type:'note',id:note.getAttribute('data-whale-note-id'),x:e.clientX,y:e.clientY});
                return;
            }
            const a=e.target.closest('a[href]');if(!a)return;
            e.preventDefault();bridge.events.push({type:'link',url:a.href});
        });
        document.addEventListener('wheel',e=>{
            bridge.events.push({type:'activate'});
            if(!bridge.linked || e.altKey || e.ctrlKey)return;
            e.preventDefault();
            const scale=e.deltaMode===1?24:e.deltaMode===2?window.innerHeight:1;
            window.scrollBy({top:e.deltaY*scale,behavior:'instant'});
            const h=Math.max(0,document.scrollingElement.scrollHeight-window.innerHeight);
            bridge.events.push({type:'scroll',fraction:h?window.scrollY/h:0});
        },{passive:false});
        document.addEventListener('keydown',e=>{
            // Only immersive reading enables these keys, and never while the
            // reader is typing (search, notes, IME) or holding a system chord.
            if(!bridge.readingKeys)return;
            if(e.defaultPrevented||e.isComposing||e.metaKey||e.ctrlKey||e.altKey)return;
            const target=e.target;
            if(readingKeyEditable(target&&target.tagName,target&&target.isContentEditable))return;
            if(e.key==='Escape'||e.key==='Esc'){
                bridge.events.push({type:'activate'});
                bridge.events.push({type:'escape'});
                return;
            }
            const scroller=document.scrollingElement;
            if(!scroller)return;
            const h=Math.max(0,scroller.scrollHeight-window.innerHeight);
            if(!h)return;
            const delta=readingKeyDelta(e.key,e.shiftKey,window.innerHeight);
            if(delta===null)return;
            e.preventDefault();
            const top=Math.max(0,Math.min(h,window.scrollY+delta));
            window.scrollTo({top:top,behavior:'instant'});
            bridge.events.push({type:'activate'});
            if(bridge.linked)bridge.events.push({type:'scroll',fraction:top/h});
        });
        return 'whale-ready';
    })()`;
}

function poll(linked) {
    return `(function(){
        const b=window.whaleBridge;if(!b)return null;b.linked=` + (linked ? 'true' : 'false') + `;
        const events=b.events.splice(0);
        return JSON.stringify({events:events,x:window.scrollX,y:window.scrollY,
            width:document.scrollingElement.scrollWidth,height:document.scrollingElement.scrollHeight});
    })()`;
}
