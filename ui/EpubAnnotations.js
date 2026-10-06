.pragma library

function contextScript(translated, labels) {
    return `(function(){
        const s=window.getSelection();
        const r=s && s.rangeCount && !s.isCollapsed ? s.getRangeAt(0)
                : window.whaleBridge && window.whaleBridge.selection;
        if(!r || !r.startContainer.isConnected) return '';
        const e=r.startContainer.nodeType===1?r.startContainer:r.startContainer.parentElement;
        const translated=` + (translated ? 'true' : 'false') + `;
        const labels=` + JSON.stringify(labels || {}) + `;
        const selector=translated?'[data-whale-source-text]':'p,li,blockquote,h1,h2,h3,h4,h5,h6';
        const p=e.closest(selector), all=Array.from(document.querySelectorAll(selector));
        const i=all.indexOf(p);if(i<0)return '';
        return all.slice(Math.max(0,i-2),i+3).map(x=>'['+(x===p?labels.current:labels.neighbor)+']\\n'+
            (translated?x.getAttribute('data-whale-source-text'):x.textContent)).join('\\n\\n');
    })()`;
}

function selectionScript() {
    return `(function(){
        const s=window.getSelection();
        const r=s && s.rangeCount && !s.isCollapsed ? s.getRangeAt(0)
                : window.whaleBridge && window.whaleBridge.selection;
        if(!r || r.collapsed || !r.startContainer.isConnected) return {reason:'empty'};
        const element=n=>n.nodeType===1?n:n.parentElement;
        const a=element(r.startContainer).closest('[data-whale-source-text]');
        const b=element(r.endContainer).closest('[data-whale-source-text]');
        if(!a || !b) return {reason:'unmapped'};
        if(a!==b) return {reason:'cross-paragraph'};
        if(a.hasAttribute('data-whale-pending')) return {reason:'pending'};
        return {source:a.getAttribute('data-whale-source-text'),
                ids:a.getAttribute('data-whale-segments')||'',quote:r.toString()};
    })()`;
}

function paragraphScript(x, y, translated, chapter) {
    return `(function(){
        const translated=` + (translated ? 'true' : 'false') + `;
        const selector=translated?'[data-whale-source-text]':'p,li,blockquote,h1,h2,h3,h4,h5,h6';
        const s=window.getSelection();
        const r=s && s.rangeCount && !s.isCollapsed ? s.getRangeAt(0)
                : window.whaleBridge && window.whaleBridge.selection;
        let element=r && r.startContainer && r.startContainer.isConnected
            ? (r.startContainer.nodeType===1?r.startContainer:r.startContainer.parentElement)
            : document.elementFromPoint(` + Number(x || 0) + `,` + Number(y || 0) + `);
        const block=element && element.closest ? element.closest(selector) : null;
        if(!block)return {reason:'no-paragraph'};
        const all=Array.from(document.querySelectorAll(selector)),index=all.indexOf(block);
        if(index<0)return {reason:'no-paragraph'};
        const source=translated?(block.getAttribute('data-whale-source-text')||''):block.textContent.trim();
        const segments=translated?(block.getAttribute('data-whale-segments')||'').trim():'';
        const first=segments.split(/\s+/).find(Boolean);
        if(translated && !first)return {reason:'unmapped'};
        return translated
            ? {kind:'source-segment',segment:Number(first),offset:0,segments:segments,source:source,
               chapter:` + JSON.stringify(chapter || '') + `,block:index,edition:'translation'}
            : {kind:'epub-block',source:source,chapter:` + JSON.stringify(chapter || '') + `,
               block:index,edition:'original'};
    })()`;
}

function paragraphMarksScript(notes, translated, chapter, labels) {
    return `(function(){
        let style=document.getElementById('whale-paragraph-note-style');
        if(!style){style=document.createElementNS('http://www.w3.org/1999/xhtml','style');style.id='whale-paragraph-note-style';document.head.appendChild(style);}
        style.textContent=` + JSON.stringify(`
            [data-whale-reader-note]{position:relative;border-radius:.22em;box-decoration-break:clone;-webkit-box-decoration-break:clone}
            [data-whale-note-color="yellow"]{background:rgba(232,216,144,.31)!important}
            [data-whale-note-color="rose"]{background:rgba(233,185,178,.28)!important}
            [data-whale-note-color="blue"]{background:rgba(174,203,217,.28)!important}
            [data-whale-note-color="green"]{background:rgba(184,210,188,.30)!important}
            .whale-margin-note{position:absolute;right:-24px;top:.35em;width:10px;height:30px;border-radius:6px;
                box-sizing:border-box;cursor:pointer;opacity:.88;z-index:2147483640;box-shadow:0 0 0 1px rgba(45,70,56,.13)}
            [data-whale-note-color="yellow"]>.whale-margin-note{background:#b99f43}
            [data-whale-note-color="rose"]>.whale-margin-note{background:#c77970}
            [data-whale-note-color="blue"]>.whale-margin-note{background:#6e9cb2}
            [data-whale-note-color="green"]>.whale-margin-note{background:#719f79}
        `) + `;
        for(const old of document.querySelectorAll('[data-whale-reader-note]')){
            old.removeAttribute('data-whale-reader-note');old.removeAttribute('data-whale-note-color');
        }
        for(const old of document.querySelectorAll('.whale-margin-note'))old.remove();
        const translated=` + (translated ? 'true' : 'false') + `;
        const chapter=` + JSON.stringify(chapter || '') + `;
        const notes=` + JSON.stringify(notes || []) + `;
        const labels=` + JSON.stringify(labels || {}) + `;
        const selector=translated?'[data-whale-source-text]':'p,li,blockquote,h1,h2,h3,h4,h5,h6';
        const blocks=Array.from(document.querySelectorAll(selector));let count=0;
        blocks.forEach((block,index)=>{
            const ids=(block.getAttribute('data-whale-segments')||'').split(/\s+/).filter(Boolean).map(Number);
            const note=notes.find(item=>{
                const a=item.anchor||{};
                if(translated)return a.kind==='source-segment' && ids.includes(Number(a.segment));
                return a.kind==='epub-block' && a.chapter===chapter && Number(a.block)===index;
            });
            if(!note)return;
            block.setAttribute('data-whale-reader-note',note.id);
            block.setAttribute('data-whale-note-color',note.color||'yellow');
            const marker=document.createElementNS('http://www.w3.org/1999/xhtml','span');
            marker.className='whale-margin-note';marker.setAttribute('data-whale-note-id',note.id);
            marker.setAttribute('role','button');marker.setAttribute('aria-label',labels.open);
            marker.setAttribute('title',labels.title);block.appendChild(marker);count++;
        });
        return count;
    })()`;
}

function marksScript(marks) {
    return `(function(){
        if(!window.CSS || !CSS.highlights || typeof Highlight==='undefined') return false;
        let style=document.getElementById('whale-annotation-style');
        if(!style){style=document.createElementNS('http://www.w3.org/1999/xhtml','style');style.id='whale-annotation-style';document.head.appendChild(style);}
        style.textContent='::highlight(whale-notes){text-decoration:underline;text-decoration-color:#688878;text-decoration-thickness:1.5px;background-color:rgba(105,139,115,.10);}';
        const ranges=[];
        const marks=` + JSON.stringify(marks) + `;
        for(const block of document.querySelectorAll('[data-whale-source-text]')){
            const source=block.getAttribute('data-whale-source-text');
            const text=block.textContent;
            for(const mark of marks){
                if(source!==mark.source || text.replace(/\\s/g,'')!==mark.translation.replace(/\\s/g,'')) continue;
                const at=text.indexOf(mark.quote);
                if(at<0 || text.indexOf(mark.quote,at+1)>=0) continue;
                const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT);
                const range=document.createRange();let pos=0,node,started=false;
                while(node=walker.nextNode()){
                    const end=pos+node.textContent.length;
                    if(!started && at<end){range.setStart(node,at-pos);started=true;}
                    if(started && at+mark.quote.length<=end){range.setEnd(node,at+mark.quote.length-pos);ranges.push(range);break;}
                    pos=end;
                }
            }
        }
        CSS.highlights.set('whale-notes',new Highlight(...ranges));return ranges.length;
    })()`;
}
