.pragma library

function script(paper, ink, muted, accent, lineHeight, brightness, fixed, preserve, fraction) {
    const css = "html,body{background:" + paper + "!important;color:" + ink + "!important;}" +
        "[data-whale-pending=\"true\"]::before{color:" + muted + "!important;}" +
        "::selection{background:" + accent + "66!important;color:" + ink + "!important;}" +
        "[data-whale-contrast-adjusted=\"true\"]{color:var(--whale-safe-color)!important;}" +
        (fixed ? "" :
            "body{box-sizing:border-box!important;max-width:760px!important;margin:0 auto!important;" +
            "padding:36px clamp(24px,6vw,64px) 64px!important;line-height:" + lineHeight +
            "!important;font-family:Georgia,\"Songti SC\",serif!important;}" +
            "p,li,blockquote{line-height:" + lineHeight + "!important;}" +
            "[data-whale-source-text],[data-whale-source-text] *{line-height:" + lineHeight +
            "!important;}img{max-width:100%;height:auto;}"
        );
    const veilColor = brightness > 1.0 ? "#FFFFFF" : "#000000";
    const veilOpacity = brightness > 1.0 ? (brightness - 1.0) * 0.7 : 1.0 - brightness;
    const veilCss = "position:fixed;inset:0;background:" + veilColor + ";opacity:" +
        Number(veilOpacity).toFixed(3) + ";pointer-events:none;z-index:2147483647;";
    const restoreScript = preserve
        ? "requestAnimationFrame(function(){const e=document.scrollingElement;window.scrollTo(0,Math.max(0,e.scrollHeight-window.innerHeight)*" + Number(fraction) + ");});"
        : "";

    return `(function(){
        let s=document.getElementById('whale-reading-style');
        if(!s){s=document.createElementNS('http://www.w3.org/1999/xhtml','style');s.id='whale-reading-style';document.head.appendChild(s);}
        s.textContent=` + JSON.stringify(css) + `;
        let d=document.getElementById('whale-reading-brightness');
        if(!d){d=document.createElementNS('http://www.w3.org/1999/xhtml','div');d.id='whale-reading-brightness';document.documentElement.appendChild(d);}
        d.setAttribute('aria-hidden','true');d.style.cssText=` + JSON.stringify(veilCss) + `;
        const parse=value=>{const v=String(value).trim();if(v==='transparent')return {r:0,g:0,b:0,a:0};if(/^#[0-9a-f]{8}$/i.test(v))return {r:parseInt(v.slice(3,5),16),g:parseInt(v.slice(5,7),16),b:parseInt(v.slice(7,9),16),a:parseInt(v.slice(1,3),16)/255};if(/^#[0-9a-f]{6}$/i.test(v))return {r:parseInt(v.slice(1,3),16),g:parseInt(v.slice(3,5),16),b:parseInt(v.slice(5,7),16),a:1};if(/^#[0-9a-f]{3}$/i.test(v))return {r:parseInt(v[1]+v[1],16),g:parseInt(v[2]+v[2],16),b:parseInt(v[3]+v[3],16),a:1};const m=v.match(/[\\d.]+/g)||[];return {r:+m[0]||0,g:+m[1]||0,b:+m[2]||0,a:m.length>3?+m[3]:1};};
        const lum=c=>{const f=v=>{v/=255;return v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4);};return .2126*f(c.r)+.7152*f(c.g)+.0722*f(c.b);};
        const contrast=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
        const blend=(front,back)=>({r:front.r*front.a+back.r*(1-front.a),g:front.g*front.a+back.g*(1-front.a),b:front.b*front.a+back.b*(1-front.a),a:1});
        const page=parse(` + JSON.stringify(paper) + `), preferred=parse(` + JSON.stringify(ink) + `);
        const light=parse('#f7f7f2'),dark=parse('#171b1c');
        const background=element=>{let result=page,node=element;const layers=[];while(node&&node.nodeType===1){const value=parse(getComputedStyle(node).backgroundColor);if(value.a>0)layers.push(value);node=node.parentElement;}for(let i=layers.length-1;i>=0;i--)result=blend(layers[i],result);return result;};
        const choose=bg=>{if(contrast(preferred,bg)>=4.5)return ` + JSON.stringify(ink) + `;return contrast(dark,bg)>=contrast(light,bg)?'#171b1c':'#f7f7f2';};
        const selector='p,li,blockquote,td,th,dd,dt,h1,h2,h3,h4,h5,h6,figcaption,caption,a,span,strong,em,small';
        for(const element of document.querySelectorAll('[data-whale-contrast-adjusted="true"]')){
            const old=element.getAttribute('data-whale-original-color')||'';
            const priority=element.getAttribute('data-whale-original-color-priority')||'';
            if(element.hasAttribute('data-whale-original-color-present'))element.style.setProperty('color',old,priority);
            else element.style.removeProperty('color');
            element.removeAttribute('data-whale-contrast-adjusted');element.removeAttribute('data-whale-original-color');
            element.removeAttribute('data-whale-original-color-priority');element.removeAttribute('data-whale-original-color-present');
            element.style.removeProperty('--whale-safe-color');
        }
        for(const element of document.querySelectorAll(selector)){
            if(element.closest('svg')||!String(element.textContent||'').trim())continue;
            const bg=background(element),fg=parse(getComputedStyle(element).color);
            if(contrast(fg,bg)<4.5){const old=element.style.getPropertyValue('color'),priority=element.style.getPropertyPriority('color');element.setAttribute('data-whale-contrast-adjusted','true');if(old)element.setAttribute('data-whale-original-color-present','true');element.setAttribute('data-whale-original-color',old);element.setAttribute('data-whale-original-color-priority',priority);element.style.setProperty('--whale-safe-color',choose(bg));element.style.setProperty('color','var(--whale-safe-color)','important');}
        }
        ` + restoreScript + `
        return {adjusted:document.querySelectorAll('[data-whale-contrast-adjusted="true"]').length};
    })()`;
}
