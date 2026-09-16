(function(){const e=document.createElement("link").relList;if(e&&e.supports&&e.supports("modulepreload"))return;for(const r of document.querySelectorAll('link[rel="modulepreload"]'))i(r);new MutationObserver(r=>{for(const a of r)if(a.type==="childList")for(const n of a.addedNodes)n.tagName==="LINK"&&n.rel==="modulepreload"&&i(n)}).observe(document,{childList:!0,subtree:!0});function s(r){const a={};return r.integrity&&(a.integrity=r.integrity),r.referrerPolicy&&(a.referrerPolicy=r.referrerPolicy),r.crossOrigin==="use-credentials"?a.credentials="include":r.crossOrigin==="anonymous"?a.credentials="omit":a.credentials="same-origin",a}function i(r){if(r.ep)return;r.ep=!0;const a=s(r);fetch(r.href,a)}})();/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const G=globalThis,Y=G.ShadowRoot&&(G.ShadyCSS===void 0||G.ShadyCSS.nativeShadow)&&"adoptedStyleSheets"in Document.prototype&&"replace"in CSSStyleSheet.prototype,W=Symbol(),Z=new WeakMap;let ge=class{constructor(e,s,i){if(this._$cssResult$=!0,i!==W)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=e,this.t=s}get styleSheet(){let e=this.o;const s=this.t;if(Y&&e===void 0){const i=s!==void 0&&s.length===1;i&&(e=Z.get(s)),e===void 0&&((this.o=e=new CSSStyleSheet).replaceSync(this.cssText),i&&Z.set(s,e))}return e}toString(){return this.cssText}};const ke=t=>new ge(typeof t=="string"?t:t+"",void 0,W),$e=(t,...e)=>{const s=t.length===1?t[0]:e.reduce((i,r,a)=>i+(n=>{if(n._$cssResult$===!0)return n.cssText;if(typeof n=="number")return n;throw Error("Value passed to 'css' function must be a 'css' function result: "+n+". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.")})(r)+t[a+1],t[0]);return new ge(s,t,W)},we=(t,e)=>{if(Y)t.adoptedStyleSheets=e.map(s=>s instanceof CSSStyleSheet?s:s.styleSheet);else for(const s of e){const i=document.createElement("style"),r=G.litNonce;r!==void 0&&i.setAttribute("nonce",r),i.textContent=s.cssText,t.appendChild(i)}},X=Y?t=>t:t=>t instanceof CSSStyleSheet?(e=>{let s="";for(const i of e.cssRules)s+=i.cssText;return ke(s)})(t):t;/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const{is:Se,defineProperty:De,getOwnPropertyDescriptor:Ce,getOwnPropertyNames:_e,getOwnPropertySymbols:Ee,getPrototypeOf:Re}=Object,w=globalThis,ee=w.trustedTypes,Ae=ee?ee.emptyScript:"",z=w.reactiveElementPolyfillSupport,x=(t,e)=>t,B={toAttribute(t,e){switch(e){case Boolean:t=t?Ae:null;break;case Object:case Array:t=t==null?t:JSON.stringify(t)}return t},fromAttribute(t,e){let s=t;switch(e){case Boolean:s=t!==null;break;case Number:s=t===null?null:Number(t);break;case Object:case Array:try{s=JSON.parse(t)}catch{s=null}}return s}},Q=(t,e)=>!Se(t,e),te={attribute:!0,type:String,converter:B,reflect:!1,useDefault:!1,hasChanged:Q};Symbol.metadata??(Symbol.metadata=Symbol("metadata")),w.litPropertyMetadata??(w.litPropertyMetadata=new WeakMap);let E=class extends HTMLElement{static addInitializer(e){this._$Ei(),(this.l??(this.l=[])).push(e)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(e,s=te){if(s.state&&(s.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(e)&&((s=Object.create(s)).wrapped=!0),this.elementProperties.set(e,s),!s.noAccessor){const i=Symbol(),r=this.getPropertyDescriptor(e,i,s);r!==void 0&&De(this.prototype,e,r)}}static getPropertyDescriptor(e,s,i){const{get:r,set:a}=Ce(this.prototype,e)??{get(){return this[s]},set(n){this[s]=n}};return{get:r,set(n){const p=r==null?void 0:r.call(this);a==null||a.call(this,n),this.requestUpdate(e,p,i)},configurable:!0,enumerable:!0}}static getPropertyOptions(e){return this.elementProperties.get(e)??te}static _$Ei(){if(this.hasOwnProperty(x("elementProperties")))return;const e=Re(this);e.finalize(),e.l!==void 0&&(this.l=[...e.l]),this.elementProperties=new Map(e.elementProperties)}static finalize(){if(this.hasOwnProperty(x("finalized")))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(x("properties"))){const s=this.properties,i=[..._e(s),...Ee(s)];for(const r of i)this.createProperty(r,s[r])}const e=this[Symbol.metadata];if(e!==null){const s=litPropertyMetadata.get(e);if(s!==void 0)for(const[i,r]of s)this.elementProperties.set(i,r)}this._$Eh=new Map;for(const[s,i]of this.elementProperties){const r=this._$Eu(s,i);r!==void 0&&this._$Eh.set(r,s)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(e){const s=[];if(Array.isArray(e)){const i=new Set(e.flat(1/0).reverse());for(const r of i)s.unshift(X(r))}else e!==void 0&&s.push(X(e));return s}static _$Eu(e,s){const i=s.attribute;return i===!1?void 0:typeof i=="string"?i:typeof e=="string"?e.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){var e;this._$ES=new Promise(s=>this.enableUpdating=s),this._$AL=new Map,this._$E_(),this.requestUpdate(),(e=this.constructor.l)==null||e.forEach(s=>s(this))}addController(e){var s;(this._$EO??(this._$EO=new Set)).add(e),this.renderRoot!==void 0&&this.isConnected&&((s=e.hostConnected)==null||s.call(e))}removeController(e){var s;(s=this._$EO)==null||s.delete(e)}_$E_(){const e=new Map,s=this.constructor.elementProperties;for(const i of s.keys())this.hasOwnProperty(i)&&(e.set(i,this[i]),delete this[i]);e.size>0&&(this._$Ep=e)}createRenderRoot(){const e=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return we(e,this.constructor.elementStyles),e}connectedCallback(){var e;this.renderRoot??(this.renderRoot=this.createRenderRoot()),this.enableUpdating(!0),(e=this._$EO)==null||e.forEach(s=>{var i;return(i=s.hostConnected)==null?void 0:i.call(s)})}enableUpdating(e){}disconnectedCallback(){var e;(e=this._$EO)==null||e.forEach(s=>{var i;return(i=s.hostDisconnected)==null?void 0:i.call(s)})}attributeChangedCallback(e,s,i){this._$AK(e,i)}_$ET(e,s){var a;const i=this.constructor.elementProperties.get(e),r=this.constructor._$Eu(e,i);if(r!==void 0&&i.reflect===!0){const n=(((a=i.converter)==null?void 0:a.toAttribute)!==void 0?i.converter:B).toAttribute(s,i.type);this._$Em=e,n==null?this.removeAttribute(r):this.setAttribute(r,n),this._$Em=null}}_$AK(e,s){var a,n;const i=this.constructor,r=i._$Eh.get(e);if(r!==void 0&&this._$Em!==r){const p=i.getPropertyOptions(r),u=typeof p.converter=="function"?{fromAttribute:p.converter}:((a=p.converter)==null?void 0:a.fromAttribute)!==void 0?p.converter:B;this._$Em=r;const m=u.fromAttribute(s,p.type);this[r]=m??((n=this._$Ej)==null?void 0:n.get(r))??m,this._$Em=null}}requestUpdate(e,s,i,r=!1,a){var n;if(e!==void 0){const p=this.constructor;if(r===!1&&(a=this[e]),i??(i=p.getPropertyOptions(e)),!((i.hasChanged??Q)(a,s)||i.useDefault&&i.reflect&&a===((n=this._$Ej)==null?void 0:n.get(e))&&!this.hasAttribute(p._$Eu(e,i))))return;this.C(e,s,i)}this.isUpdatePending===!1&&(this._$ES=this._$EP())}C(e,s,{useDefault:i,reflect:r,wrapped:a},n){i&&!(this._$Ej??(this._$Ej=new Map)).has(e)&&(this._$Ej.set(e,n??s??this[e]),a!==!0||n!==void 0)||(this._$AL.has(e)||(this.hasUpdated||i||(s=void 0),this._$AL.set(e,s)),r===!0&&this._$Em!==e&&(this._$Eq??(this._$Eq=new Set)).add(e))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(s){Promise.reject(s)}const e=this.scheduleUpdate();return e!=null&&await e,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){var i;if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??(this.renderRoot=this.createRenderRoot()),this._$Ep){for(const[a,n]of this._$Ep)this[a]=n;this._$Ep=void 0}const r=this.constructor.elementProperties;if(r.size>0)for(const[a,n]of r){const{wrapped:p}=n,u=this[a];p!==!0||this._$AL.has(a)||u===void 0||this.C(a,void 0,n,u)}}let e=!1;const s=this._$AL;try{e=this.shouldUpdate(s),e?(this.willUpdate(s),(i=this._$EO)==null||i.forEach(r=>{var a;return(a=r.hostUpdate)==null?void 0:a.call(r)}),this.update(s)):this._$EM()}catch(r){throw e=!1,this._$EM(),r}e&&this._$AE(s)}willUpdate(e){}_$AE(e){var s;(s=this._$EO)==null||s.forEach(i=>{var r;return(r=i.hostUpdated)==null?void 0:r.call(i)}),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(e)),this.updated(e)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(e){return!0}update(e){this._$Eq&&(this._$Eq=this._$Eq.forEach(s=>this._$ET(s,this[s]))),this._$EM()}updated(e){}firstUpdated(e){}};E.elementStyles=[],E.shadowRootOptions={mode:"open"},E[x("elementProperties")]=new Map,E[x("finalized")]=new Map,z==null||z({ReactiveElement:E}),(w.reactiveElementVersions??(w.reactiveElementVersions=[])).push("2.1.2");/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const T=globalThis,se=t=>t,q=T.trustedTypes,ie=q?q.createPolicy("lit-html",{createHTML:t=>t}):void 0,me="$lit$",$=`lit$${Math.random().toFixed(9).slice(2)}$`,fe="?"+$,Me=`<${fe}>`,_=document,I=()=>_.createComment(""),O=t=>t===null||typeof t!="object"&&typeof t!="function",J=Array.isArray,xe=t=>J(t)||typeof(t==null?void 0:t[Symbol.iterator])=="function",H=`[ 	
\f\r]`,M=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,re=/-->/g,ae=/>/g,S=RegExp(`>|${H}(?:([^\\s"'>=/]+)(${H}*=${H}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`,"g"),oe=/'/g,ne=/"/g,ve=/^(?:script|style|textarea|title)$/i,Te=t=>(e,...s)=>({_$litType$:t,strings:e,values:s}),o=Te(1),R=Symbol.for("lit-noChange"),h=Symbol.for("lit-nothing"),de=new WeakMap,D=_.createTreeWalker(_,129);function ye(t,e){if(!J(t)||!t.hasOwnProperty("raw"))throw Error("invalid template strings array");return ie!==void 0?ie.createHTML(e):e}const Fe=(t,e)=>{const s=t.length-1,i=[];let r,a=e===2?"<svg>":e===3?"<math>":"",n=M;for(let p=0;p<s;p++){const u=t[p];let m,g,v=-1,y=0;for(;y<u.length&&(n.lastIndex=y,g=n.exec(u),g!==null);)y=n.lastIndex,n===M?g[1]==="!--"?n=re:g[1]!==void 0?n=ae:g[2]!==void 0?(ve.test(g[2])&&(r=RegExp("</"+g[2],"g")),n=S):g[3]!==void 0&&(n=S):n===S?g[0]===">"?(n=r??M,v=-1):g[1]===void 0?v=-2:(v=n.lastIndex-g[2].length,m=g[1],n=g[3]===void 0?S:g[3]==='"'?ne:oe):n===ne||n===oe?n=S:n===re||n===ae?n=M:(n=S,r=void 0);const k=n===S&&t[p+1].startsWith("/>")?" ":"";a+=n===M?u+Me:v>=0?(i.push(m),u.slice(0,v)+me+u.slice(v)+$+k):u+$+(v===-2?p:k)}return[ye(t,a+(t[s]||"<?>")+(e===2?"</svg>":e===3?"</math>":"")),i]};class L{constructor({strings:e,_$litType$:s},i){let r;this.parts=[];let a=0,n=0;const p=e.length-1,u=this.parts,[m,g]=Fe(e,s);if(this.el=L.createElement(m,i),D.currentNode=this.el.content,s===2||s===3){const v=this.el.content.firstChild;v.replaceWith(...v.childNodes)}for(;(r=D.nextNode())!==null&&u.length<p;){if(r.nodeType===1){if(r.hasAttributes())for(const v of r.getAttributeNames())if(v.endsWith(me)){const y=g[n++],k=r.getAttribute(v).split($),P=/([.?@])?(.*)/.exec(y);u.push({type:1,index:a,name:P[2],strings:k,ctor:P[1]==="."?Oe:P[1]==="?"?Le:P[1]==="@"?Ne:j}),r.removeAttribute(v)}else v.startsWith($)&&(u.push({type:6,index:a}),r.removeAttribute(v));if(ve.test(r.tagName)){const v=r.textContent.split($),y=v.length-1;if(y>0){r.textContent=q?q.emptyScript:"";for(let k=0;k<y;k++)r.append(v[k],I()),D.nextNode(),u.push({type:2,index:++a});r.append(v[y],I())}}}else if(r.nodeType===8)if(r.data===fe)u.push({type:2,index:a});else{let v=-1;for(;(v=r.data.indexOf($,v+1))!==-1;)u.push({type:7,index:a}),v+=$.length-1}a++}}static createElement(e,s){const i=_.createElement("template");return i.innerHTML=e,i}}function A(t,e,s=t,i){var n,p;if(e===R)return e;let r=i!==void 0?(n=s._$Co)==null?void 0:n[i]:s._$Cl;const a=O(e)?void 0:e._$litDirective$;return(r==null?void 0:r.constructor)!==a&&((p=r==null?void 0:r._$AO)==null||p.call(r,!1),a===void 0?r=void 0:(r=new a(t),r._$AT(t,s,i)),i!==void 0?(s._$Co??(s._$Co=[]))[i]=r:s._$Cl=r),r!==void 0&&(e=A(t,r._$AS(t,e.values),r,i)),e}class Ie{constructor(e,s){this._$AV=[],this._$AN=void 0,this._$AD=e,this._$AM=s}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(e){const{el:{content:s},parts:i}=this._$AD,r=((e==null?void 0:e.creationScope)??_).importNode(s,!0);D.currentNode=r;let a=D.nextNode(),n=0,p=0,u=i[0];for(;u!==void 0;){if(n===u.index){let m;u.type===2?m=new N(a,a.nextSibling,this,e):u.type===1?m=new u.ctor(a,u.name,u.strings,this,e):u.type===6&&(m=new Pe(a,this,e)),this._$AV.push(m),u=i[++p]}n!==(u==null?void 0:u.index)&&(a=D.nextNode(),n++)}return D.currentNode=_,r}p(e){let s=0;for(const i of this._$AV)i!==void 0&&(i.strings!==void 0?(i._$AI(e,i,s),s+=i.strings.length-2):i._$AI(e[s])),s++}}class N{get _$AU(){var e;return((e=this._$AM)==null?void 0:e._$AU)??this._$Cv}constructor(e,s,i,r){this.type=2,this._$AH=h,this._$AN=void 0,this._$AA=e,this._$AB=s,this._$AM=i,this.options=r,this._$Cv=(r==null?void 0:r.isConnected)??!0}get parentNode(){let e=this._$AA.parentNode;const s=this._$AM;return s!==void 0&&(e==null?void 0:e.nodeType)===11&&(e=s.parentNode),e}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(e,s=this){e=A(this,e,s),O(e)?e===h||e==null||e===""?(this._$AH!==h&&this._$AR(),this._$AH=h):e!==this._$AH&&e!==R&&this._(e):e._$litType$!==void 0?this.$(e):e.nodeType!==void 0?this.T(e):xe(e)?this.k(e):this._(e)}O(e){return this._$AA.parentNode.insertBefore(e,this._$AB)}T(e){this._$AH!==e&&(this._$AR(),this._$AH=this.O(e))}_(e){this._$AH!==h&&O(this._$AH)?this._$AA.nextSibling.data=e:this.T(_.createTextNode(e)),this._$AH=e}$(e){var a;const{values:s,_$litType$:i}=e,r=typeof i=="number"?this._$AC(e):(i.el===void 0&&(i.el=L.createElement(ye(i.h,i.h[0]),this.options)),i);if(((a=this._$AH)==null?void 0:a._$AD)===r)this._$AH.p(s);else{const n=new Ie(r,this),p=n.u(this.options);n.p(s),this.T(p),this._$AH=n}}_$AC(e){let s=de.get(e.strings);return s===void 0&&de.set(e.strings,s=new L(e)),s}k(e){J(this._$AH)||(this._$AH=[],this._$AR());const s=this._$AH;let i,r=0;for(const a of e)r===s.length?s.push(i=new N(this.O(I()),this.O(I()),this,this.options)):i=s[r],i._$AI(a),r++;r<s.length&&(this._$AR(i&&i._$AB.nextSibling,r),s.length=r)}_$AR(e=this._$AA.nextSibling,s){var i;for((i=this._$AP)==null?void 0:i.call(this,!1,!0,s);e!==this._$AB;){const r=se(e).nextSibling;se(e).remove(),e=r}}setConnected(e){var s;this._$AM===void 0&&(this._$Cv=e,(s=this._$AP)==null||s.call(this,e))}}class j{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(e,s,i,r,a){this.type=1,this._$AH=h,this._$AN=void 0,this.element=e,this.name=s,this._$AM=r,this.options=a,i.length>2||i[0]!==""||i[1]!==""?(this._$AH=Array(i.length-1).fill(new String),this.strings=i):this._$AH=h}_$AI(e,s=this,i,r){const a=this.strings;let n=!1;if(a===void 0)e=A(this,e,s,0),n=!O(e)||e!==this._$AH&&e!==R,n&&(this._$AH=e);else{const p=e;let u,m;for(e=a[0],u=0;u<a.length-1;u++)m=A(this,p[i+u],s,u),m===R&&(m=this._$AH[u]),n||(n=!O(m)||m!==this._$AH[u]),m===h?e=h:e!==h&&(e+=(m??"")+a[u+1]),this._$AH[u]=m}n&&!r&&this.j(e)}j(e){e===h?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,e??"")}}class Oe extends j{constructor(){super(...arguments),this.type=3}j(e){this.element[this.name]=e===h?void 0:e}}class Le extends j{constructor(){super(...arguments),this.type=4}j(e){this.element.toggleAttribute(this.name,!!e&&e!==h)}}class Ne extends j{constructor(e,s,i,r,a){super(e,s,i,r,a),this.type=5}_$AI(e,s=this){if((e=A(this,e,s,0)??h)===R)return;const i=this._$AH,r=e===h&&i!==h||e.capture!==i.capture||e.once!==i.once||e.passive!==i.passive,a=e!==h&&(i===h||r);r&&this.element.removeEventListener(this.name,this,i),a&&this.element.addEventListener(this.name,this,e),this._$AH=e}handleEvent(e){var s;typeof this._$AH=="function"?this._$AH.call(((s=this.options)==null?void 0:s.host)??this.element,e):this._$AH.handleEvent(e)}}class Pe{constructor(e,s,i){this.element=e,this.type=6,this._$AN=void 0,this._$AM=s,this.options=i}get _$AU(){return this._$AM._$AU}_$AI(e){A(this,e)}}const V=T.litHtmlPolyfillSupport;V==null||V(L,N),(T.litHtmlVersions??(T.litHtmlVersions=[])).push("3.3.3");const Ue=(t,e,s)=>{const i=(s==null?void 0:s.renderBefore)??e;let r=i._$litPart$;if(r===void 0){const a=(s==null?void 0:s.renderBefore)??null;i._$litPart$=r=new N(e.insertBefore(I(),a),a,void 0,s??{})}return r._$AI(t),r};/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const C=globalThis;class F extends E{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){var s;const e=super.createRenderRoot();return(s=this.renderOptions).renderBefore??(s.renderBefore=e.firstChild),e}update(e){const s=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(e),this._$Do=Ue(s,this.renderRoot,this.renderOptions)}connectedCallback(){var e;super.connectedCallback(),(e=this._$Do)==null||e.setConnected(!0)}disconnectedCallback(){var e;super.disconnectedCallback(),(e=this._$Do)==null||e.setConnected(!1)}render(){return R}}var pe;F._$litElement$=!0,F.finalized=!0,(pe=C.litElementHydrateSupport)==null||pe.call(C,{LitElement:F});const K=C.litElementPolyfillSupport;K==null||K({LitElement:F});(C.litElementVersions??(C.litElementVersions=[])).push("4.2.2");/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const Ge=t=>(e,s)=>{s!==void 0?s.addInitializer(()=>{customElements.define(t,e)}):customElements.define(t,e)};/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */const Be={attribute:!0,type:String,converter:B,reflect:!1,hasChanged:Q},qe=(t=Be,e,s)=>{const{kind:i,metadata:r}=s;let a=globalThis.litPropertyMetadata.get(r);if(a===void 0&&globalThis.litPropertyMetadata.set(r,a=new Map),i==="setter"&&((t=Object.create(t)).wrapped=!0),a.set(s.name,t),i==="accessor"){const{name:n}=s;return{set(p){const u=e.get.call(this);e.set.call(this,p),this.requestUpdate(n,u,t,!0,p)},init(p){return p!==void 0&&this.C(n,void 0,t,p),p}}}if(i==="setter"){const{name:n}=s;return function(p){const u=this[n];e.call(this,p),this.requestUpdate(n,u,t,!0,p)}}throw Error("Unsupported decorator location: "+i)};function je(t){return(e,s)=>typeof s=="object"?qe(t,e,s):((i,r,a)=>{const n=r.hasOwnProperty(a);return r.constructor.createProperty(a,i),n?Object.getOwnPropertyDescriptor(r,a):void 0})(t,e,s)}/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function c(t){return je({...t,state:!0,attribute:!1})}class b extends Error{constructor(e,s,i,r,a,n,p,u){const m=r||`API request failed with status ${e} (${s})`;super(m),this.name="ApiError",this.status=e,this.statusText=s,this.body=i,this.detail=r,this.pickerToken=a,this.activeToken=n,this.code=p,this.dictionaryKey=u,Object.setPrototypeOf(this,b.prototype)}get isConflict(){return this.status===409}get isNotFound(){return this.status===404}get isUnprocessable(){return this.status===422}get isForbidden(){return this.status===403}get isBadRequest(){return this.status===400}}async function ze(t){const e=t.status,s=t.statusText;let i=null,r,a,n,p,u;try{if((t.headers.get("content-type")||"").includes("application/json")){const g=await t.json();i=g,typeof g.detail=="string"?r=g.detail:Array.isArray(g.detail)&&g.detail.length>0&&(r=g.detail.map(v=>v.msg||JSON.stringify(v)).join("; ")),typeof g.picker_token=="string"&&(a=g.picker_token),typeof g.active_token=="string"&&(n=g.active_token),typeof g.code=="string"&&(p=g.code),typeof g.dictionary_key=="string"&&(u=g.dictionary_key)}else{const g=await t.text();i=g,r=g||void 0}}catch{}return new b(e,s,i,r,a,n,p,u)}class He{constructor(e={}){this.baseUrl=e.baseUrl?e.baseUrl.replace(/\/+$/,""):"",this._fetch=e.fetch??globalThis.fetch.bind(globalThis)}async request(e,s={}){const i=s.method??"GET",r=i==="GET";let a=`${this.baseUrl}${e.startsWith("/")?e:`/${e}`}`;if(s.params){const m=new URLSearchParams;for(const[v,y]of Object.entries(s.params))y!=null&&m.append(v,String(y));const g=m.toString();g&&(a+=(a.includes("?")?"&":"?")+g)}const n={...s.headers};r||(n["X-Flashcards-Request"]="1");let p;s.body!==void 0&&s.body!==null&&(s.body instanceof FormData||s.body instanceof Blob||s.body instanceof ArrayBuffer||ArrayBuffer.isView(s.body)?p=s.body:(n["Content-Type"]="application/json",p=JSON.stringify(s.body)));const u=await this._fetch(a,{method:i,headers:n,body:p});if(!u.ok)throw await ze(u);if(s.responseType==="text")return await u.text();if(s.responseType==="blob")return await u.blob();if(!(u.status===204||u.headers.get("content-length")==="0"))return await u.json()}async lookup(e){return this.request("/vocab/lookup",{method:"GET",params:{q:e}})}async lookupPost(e){return this.request("/vocab/lookup",{method:"POST",body:{query:e}})}async activateDictionary(e){return this.request("/vocab/dictionary/activate",{method:"POST",body:e})}async highlight(e){return this.request("/vocab/highlight",{method:"POST",body:e})}async captureCards(e){return this.request("/vocab/cards",{method:"POST",body:e})}async importCsv(e){return this.request("/vocab/import/csv",{method:"POST",body:e})}async createNote(e){return this.request("/vocab/notes",{method:"POST",body:e})}async getNextCard(e){return this.request("/vocab/cards/next",{method:"GET",params:{deck_id:e}})}async reviewCard(e,s){return this.request(`/vocab/cards/${e}/review`,{method:"POST",body:{confidence:s}})}async setGloss(e,s,i){return this.request(`/vocab/notes/${e}/gloss`,{method:"POST",body:{language:s,meaning_text:i}})}async deleteGloss(e,s){return this.request(`/vocab/notes/${e}/gloss`,{method:"DELETE",params:{language:s}})}async uploadAudio(e,s,i){const r={};return i&&!(s instanceof FormData)&&(r["Content-Type"]=i),this.request(`/vocab/notes/${e}/audio`,{method:"POST",body:s,headers:r})}async revertAudio(e){return this.request(`/vocab/notes/${e}/audio`,{method:"DELETE"})}getAudioUrl(e){const s=encodeURIComponent(String(e));return`${this.baseUrl}/vocab/audio/${s}`}async fetchAudio(e){const s=encodeURIComponent(String(e));return this.request(`/vocab/audio/${s}`,{method:"GET",responseType:"blob"})}async getDecks(){return this.request("/vocab/decks",{method:"GET"})}async createDeck(e){const s={name:e};return this.request("/vocab/decks",{method:"POST",body:s})}async deleteDeck(e){return this.request(`/vocab/decks/${e}`,{method:"DELETE"})}async getDeckCards(e){return this.request(`/vocab/decks/${e}/cards`,{method:"GET"})}async removeNoteFromDeck(e,s){return this.request(`/vocab/decks/${e}/notes/${s}`,{method:"DELETE"})}async moveNoteBetweenDecks(e,s,i){const r={deck_id:i};return this.request(`/vocab/decks/${e}/notes/${s}`,{method:"PATCH",body:r})}async renameDeck(e,s){const i={name:s};return this.request(`/vocab/decks/${e}`,{method:"PATCH",body:i})}async listFolders(){return this.request("/vocab/folders",{method:"GET"})}async createFolder(e){const s={name:e};return this.request("/vocab/folders",{method:"POST",body:s})}async renameFolder(e,s){const i={name:s};return this.request(`/vocab/folders/${e}`,{method:"PATCH",body:i})}async deleteFolder(e){return this.request(`/vocab/folders/${e}`,{method:"DELETE"})}async setDeckFolder(e,s){const i={folder_id:s};return this.request(`/vocab/decks/${e}/folder`,{method:"PUT",body:i})}async setMeaningLanguages(e,s){const i={languages:s};return this.request(`/vocab/notes/${e}/meaning-languages`,{method:"PUT",body:i})}async restoreOrphanedNote(e,s){const i={deck_id:s};return this.request(`/vocab/notes/${e}/restore`,{method:"POST",body:i})}async changeNoteSense(e,s){return this.request(`/vocab/notes/${e}/sense`,{method:"PUT",body:s})}async exportAnki(e){return this.request("/vocab/export/anki",{method:"GET",params:{deck_id:e},responseType:"text"})}async exportApkg(e){return this.request("/vocab/export/apkg",{method:"GET",params:{deck_id:e},responseType:"blob"})}async getDictionarySettings(){return this.request("/vocab/settings/dictionary",{method:"GET"})}async installOffline(e={}){return this.request("/vocab/settings/dictionary/install-offline",{method:"POST",body:e})}async removeOffline(e={}){return this.request("/vocab/settings/dictionary/remove-offline",{method:"POST",body:e})}async clearOnlineCache(){return this.request("/vocab/settings/dictionary/clear-online-cache",{method:"POST",body:{}})}async useOnline(){return this.request("/vocab/settings/dictionary/use-online",{method:"POST",body:{}})}async useOffline(){return this.request("/vocab/settings/dictionary/use-offline",{method:"POST",body:{}})}}function Ve(t){return new He(t)}const be="wortlaut.study.alwaysShowExtraInfo";function Ke(t){if(!t)return!1;try{return t.getItem(be)==="true"}catch{return!1}}function Ye(t,e){if(t)try{t.setItem(be,e?"true":"false")}catch{}}function le(){return!1}function We(t){return t.isRevealed?t.newPreference:!1}function Qe(t,e){if(!t||e.trim().toUpperCase()!=="NOUN")return null;const s=t.trim().toLowerCase();return["der","m","masculine","maskulin"].includes(s)?"der":["die","f","feminine","feminin"].includes(s)?"die":["das","n","neuter","neutral","sächlich"].includes(s)?"das":null}function Je(t){var i;const e=t.gender??((i=t.grammar)==null?void 0:i.gender)??null,s=Qe(e,t.pos);return s&&!t.lemma.toLowerCase().startsWith(s.toLowerCase()+" ")?`${s} ${t.lemma}`:t.lemma}function ce(t){if(t.senses){for(const e of t.senses)if(e.meanings){for(const s of e.meanings)if(s.language==="en"&&s.text&&s.text.trim())return s.text.trim()}}return null}function he(t){var i,r,a,n,p,u;const e=(a=(r=(i=t.meanings)==null?void 0:i.find(m=>{var g;return m.language==="en"&&((g=m.text)==null?void 0:g.trim())}))==null?void 0:r.text)==null?void 0:a.trim(),s=(u=(p=(n=t.meanings)==null?void 0:n.find(m=>{var g;return m.language==="de"&&((g=m.text)==null?void 0:g.trim())}))==null?void 0:p.text)==null?void 0:u.trim();return e||t.gloss||s||`Meaning ${t.ord}`}function Ze(t,e){return e?!1:t==="resolved"||t==="needs_gloss"}var Xe=Object.defineProperty,et=Object.getOwnPropertyDescriptor,l=(t,e,s,i)=>{for(var r=i>1?void 0:i?et(e,s):e,a=t.length-1,n;a>=0;a--)(n=t[a])&&(r=(i?n(e,s,r):n(r))||r);return i&&r&&Xe(e,s,r),r};const U="Orphaned",tt=[["1","Not at all"],["2","Barely"],["3","With effort"],["4","Comfortably"],["5","Without doubt"]],f=Ve();function ue(){try{return window.localStorage}catch{return null}}let d=class extends F{constructor(){super(...arguments),this.decks=[],this.deckStatus="loading",this.errorMessage="",this.successMessage="",this.newDeckName="",this.selectedDeckId=null,this.pendingDeleteDeckId=null,this.isCreating=!1,this.isDeleting=!1,this.lookupQuery="",this.lookupStatus="idle",this.lookupCandidates=[],this.lookupAssetToken="",this.selectedCandidate=null,this.selectedSenseRef=null,this.selectedMeaningLanguages=["de","en"],this.userMeaningDe="",this.userMeaningEn="",this.manualDeckId=null,this.lastSavedNote=null,this.isSavingNote=!1,this.importDeckId=null,this.importText="",this.importFileName="",this.isReadingImportFile=!1,this.isImporting=!1,this.exportingFormat=null,this.captureSentence="",this.captureLessonLabel="",this.captureSpanStart=0,this.captureSpanEnd=0,this.captureStatus="idle",this.captureCandidates=[],this.captureAssetToken="",this.captureContext=null,this.captureSelections={},this.captureMeaningLanguages=["de","en"],this.captureUserMeaningDe="",this.captureUserMeaningEn="",this.captureDeckId=null,this.captureError="",this.captureDictionaryChanged=!1,this.isCapturing=!1,this.view="decks",this.studyDeckId=null,this.studyStatus="idle",this.studyCard=null,this.isRevealed=!1,this.isReviewing=!1,this.studyError="",this.extraInfoOpen=!1,this.alwaysShowExtraInfo=Ke(ue()),this.glossDrafts={de:"",en:""},this.glossState="",this.glossError="",this.glossSavingLanguage=null,this.audioStatus="idle",this.audioMessage="",this.recordingStatus="idle",this.recordingBlob=null,this.recordingNoteId=null,this.recordingPreviewUrl="",this.recordingError="",this.showRecordingControls=!1,this.revertConfirmation=!1,this.hasCustomAudio=!1,this.dictionaryMode="unconfigured",this.dictionarySettings=null,this.dictionarySettingsStatus="loading",this.dictionaryAction="idle",this.dictionaryActionMessage="",this.dictionaryActionError="",this.confirmRemoveOffline=!1,this.deckTab="overview",this.deckCards=[],this.deckCardsStatus="idle",this.deckCardsError="",this.editingCard=null,this.editLanguages=[],this.editGlossDrafts={de:"",en:""},this.editGlossBusy={de:!1,en:!1},this.editState="idle",this.editError="",this.moveTarget=null,this.moveDestinationDeckId=null,this.moveState="idle",this.moveError="",this.removeTarget=null,this.removeState="idle",this.restoreTarget=null,this.restoreDestinationDeckId=null,this.restoreState="idle",this.restoreError="",this.senseTarget=null,this.senseCandidates=[],this.senseLookupAssetToken="",this.senseLookupStatus="idle",this.senseSelectedRef=null,this.senseState="idle",this.senseError="",this.renameOpen=!1,this.renameDraft="",this.renameState="idle",this.renameError="",this.folders=[],this.folderStatus="loading",this.folderError="",this.createFolderOpen=!1,this.newFolderName="",this.createFolderState="idle",this.createFolderError="",this.renameFolderTarget=null,this.renameFolderDraft="",this.renameFolderState="idle",this.renameFolderError="",this.deleteFolderTarget=null,this.deleteFolderState="idle",this.deleteFolderError="",this.moveDeckTarget=null,this.moveDeckFolderId=null,this.moveDeckState="idle",this.moveDeckError="",this.mgmtAudioStatus="idle",this.mgmtAudioMessage="",this.mgmtRecordingStatus="idle",this.mgmtRecordingBlob=null,this.mgmtRecordingNoteId=null,this.mgmtRecordingPreviewUrl="",this.mgmtRevertConfirmation=!1,this.mgmtRecordingError="",this.mgmtShowRecordingControls=!1,this.focusTarget=null,this.audioPlayer=null,this.mgmtAudioPlayer=null,this.mediaRecorder=null,this.recordingChunks=[],this.lastDeckTabByDeck=new Map,this.handleStudyKeydown=t=>{if(this.view!=="study")return;const e=t.target;if(!(e!=null&&e.closest('input, textarea, select, [contenteditable="true"]'))){if(t.code==="Space"&&!this.isRevealed){t.preventDefault(),this.revealCard();return}if(t.key>="1"&&t.key<="5"&&this.isRevealed){t.preventDefault(),this.submitConfidence(Number(t.key));return}t.key.toLowerCase()==="r"&&(t.preventDefault(),this.playPronunciation())}},this.handleGlobalKeydown=t=>{if(t.key==="Escape"){if(this.editingCard){if(this.editState==="saving-languages"||this.editState==="saving-gloss")return;t.preventDefault(),this.closeEditDialog();return}if(this.moveTarget){if(this.moveState==="saving")return;t.preventDefault(),this.closeMoveDialog();return}if(this.removeTarget){if(this.removeState==="saving")return;t.preventDefault(),this.closeRemoveConfirm();return}if(this.renameOpen){if(this.renameState==="saving")return;t.preventDefault(),this.closeRenameDialog();return}if(this.restoreTarget){if(this.restoreState==="saving")return;t.preventDefault(),this.closeRestoreDialog();return}if(this.createFolderOpen){if(this.createFolderState==="saving")return;t.preventDefault(),this.closeCreateFolderDialog();return}if(this.renameFolderTarget){if(this.renameFolderState==="saving")return;t.preventDefault(),this.closeRenameFolderDialog();return}if(this.deleteFolderTarget){if(this.deleteFolderState==="saving")return;t.preventDefault(),this.closeDeleteFolderDialog();return}if(this.moveDeckTarget){if(this.moveDeckState==="saving")return;t.preventDefault(),this.closeMoveDeckDialog()}}}}connectedCallback(){super.connectedCallback(),this.loadDecks(),this.loadFolders(),this.loadDictionarySettings(),window.addEventListener("keydown",this.handleStudyKeydown),window.addEventListener("keydown",this.handleGlobalKeydown)}disconnectedCallback(){window.removeEventListener("keydown",this.handleStudyKeydown),window.removeEventListener("keydown",this.handleGlobalKeydown),this.stopAudio(),this.stopManagementAudio(),this.releaseRecordingPreview(),this.releaseManagementRecordingPreview(),super.disconnectedCallback()}updated(){if(!this.focusTarget)return;const t=this.focusTarget;let e="";switch(t){case"answer":e="[data-study-answer]";break;case"empty":e="[data-study-empty]";break;case"edit-dialog":e="[data-edit-dialog]";break;case"move-dialog":e="[data-move-dialog]";break;case"remove-dialog":e="[data-remove-dialog]";break;case"rename-dialog":e="[data-rename-dialog]";break;case"restore-dialog":e="[data-restore-dialog]";break;case"create-folder-dialog":e="[data-create-folder-dialog]";break;case"rename-folder-dialog":e="[data-rename-folder-dialog]";break;case"delete-folder-dialog":e="[data-delete-folder-dialog]";break;case"move-deck-dialog":e="[data-move-deck-dialog]";break}const s=e?this.renderRoot.querySelector(e):null;s&&s.focus(),(s||t==="answer"||t==="empty")&&(this.focusTarget=null)}async loadDecks(){this.deckStatus="loading",this.errorMessage="",this.successMessage="";try{const t=await f.getDecks();return this.decks=t,this.selectedDeckId!==null&&!t.some(e=>e.id===this.selectedDeckId)&&(this.selectedDeckId=null),this.manualDeckId!==null&&!t.some(e=>e.id===this.manualDeckId)&&(this.manualDeckId=null),this.captureDeckId!==null&&!t.some(e=>e.id===this.captureDeckId)&&(this.captureDeckId=null),this.importDeckId!==null&&!t.some(e=>e.id===this.importDeckId)&&(this.importDeckId=null),this.deckStatus="ready",t}catch(t){return this.deckStatus="error",this.errorMessage=this.messageFor(t,"Decks could not be loaded."),null}}async createDeck(t){t.preventDefault();const e=this.newDeckName.trim();if(!e){this.successMessage="",this.errorMessage="Enter a deck name before creating it.";return}this.isCreating=!0,this.errorMessage="",this.successMessage="";try{const s=await f.createDeck(e);this.newDeckName="";const i=await this.loadDecks();if(i===null){this.errorMessage=`“${s.name}” may have been created, but the deck list could not be refreshed.`;return}const r=i.find(a=>a.id===s.id);if(!r){this.errorMessage=`The server did not return “${s.name}” after creation. It was not opened.`;return}this.selectedDeckId=r.id,this.manualDeckId=r.id,this.captureDeckId=r.id,this.importDeckId=r.id,this.successMessage=`Created and opened “${r.name}”.`}catch(s){this.successMessage="",this.errorMessage=this.messageFor(s,"Deck could not be created.")}finally{this.isCreating=!1}}async deleteDeck(t){this.isDeleting=!0,this.errorMessage="",this.successMessage="";try{if(!(await f.deleteDeck(t.id)).deleted)throw new Error("The server did not confirm deletion.");this.pendingDeleteDeckId=null;const s=await this.loadDecks();if(s===null){this.errorMessage=`“${t.name}” may have been deleted, but the deck list could not be refreshed.`;return}if(s.some(i=>i.id===t.id)){this.errorMessage=`The server still returned “${t.name}” after deletion. The deletion was not confirmed.`;return}this.selectedDeckId===t.id&&(this.selectedDeckId=null),this.successMessage=`Deleted “${t.name}”. Notes with review history were preserved by the server.`}catch(e){this.successMessage="",this.errorMessage=this.messageFor(e,"Deck could not be deleted.")}finally{this.isDeleting=!1}}messageFor(t,e){return t instanceof b&&t.detail?t.detail:t instanceof Error&&t.message?t.message:e}isOrphanedDeck(t){return t.name===U}async loadFolders(){this.folderStatus="loading",this.folderError="";try{const t=await f.listFolders();return this.folders=t,this.folderStatus="ready",t}catch(t){return this.folderStatus="error",this.folderError=this.messageFor(t,"Folders could not be loaded."),null}}groupedDeckSections(){const t=this.folders.map(s=>({folder:s,decks:[]})),e=[];for(const s of this.decks){const i=s.folder_id===null?void 0:t.find(r=>r.folder.id===s.folder_id);i?i.decks.push(s):e.push(s)}return[...t,{folder:null,decks:e}]}folderById(t){if(t!==null)return this.folders.find(e=>e.id===t)}openCreateFolderDialog(){this.createFolderOpen=!0,this.newFolderName="",this.createFolderError="",this.createFolderState="idle",this.focusTarget="create-folder-dialog"}closeCreateFolderDialog(){this.createFolderState!=="saving"&&(this.createFolderOpen=!1,this.newFolderName="",this.createFolderError="",this.createFolderState="idle")}async performCreateFolder(){const t=this.newFolderName.trim();if(!t){this.createFolderError="Enter a folder name.";return}this.createFolderState="saving",this.createFolderError="";try{const e=await f.createFolder(t),s=await this.loadFolders();if(s===null){this.createFolderError=`“${e.name}” may have been created, but the folder list could not be refreshed.`;return}if(!s.some(i=>i.id===e.id)){this.createFolderError="The server did not return the created folder. It was not confirmed.";return}this.createFolderOpen=!1,this.newFolderName="",this.successMessage=`Created folder “${e.name}”.`}catch(e){e instanceof b&&e.code==="folder_name_conflict"?this.createFolderError=`A folder named “${t}” already exists. Choose another name.`:this.createFolderError=this.messageFor(e,"Folder could not be created.")}finally{this.createFolderState="idle"}}openRenameFolderDialog(t){this.renameFolderTarget=t,this.renameFolderDraft=t.name,this.renameFolderState="idle",this.renameFolderError="",this.focusTarget="rename-folder-dialog"}closeRenameFolderDialog(){this.renameFolderState!=="saving"&&(this.renameFolderTarget=null,this.renameFolderDraft="",this.renameFolderError="",this.renameFolderState="idle")}async performRenameFolder(){const t=this.renameFolderTarget;if(!t)return;const e=this.renameFolderDraft.trim();if(!e){this.renameFolderError="Folder name must not be blank.";return}if(e===t.name){this.renameFolderTarget=null,this.renameFolderDraft="";return}this.renameFolderState="saving",this.renameFolderError="";try{const s=await f.renameFolder(t.id,e),i=await this.loadFolders();if(i===null){this.renameFolderError="Folder may have been renamed, but the folder list could not be refreshed.";return}const r=i.find(n=>n.id===s.id&&n.name===s.name);if(!r){this.renameFolderError="The server did not confirm the renamed folder. The change was not confirmed.";return}if(await this.loadDecks()===null){this.renameFolderError="Folder was renamed, but the deck list could not be refreshed.";return}this.renameFolderTarget=null,this.renameFolderDraft="",this.successMessage=`Renamed folder to “${r.name}”.`}catch(s){s instanceof b&&s.code==="folder_name_conflict"?this.renameFolderError=`A folder named “${e}” already exists. Choose another name.`:this.renameFolderError=this.messageFor(s,"Folder could not be renamed.")}finally{this.renameFolderState="idle"}}openDeleteFolderDialog(t){this.deleteFolderTarget=t,this.deleteFolderState="idle",this.deleteFolderError="",this.focusTarget="delete-folder-dialog"}closeDeleteFolderDialog(){this.deleteFolderState!=="saving"&&(this.deleteFolderTarget=null,this.deleteFolderError="",this.deleteFolderState="idle")}async performDeleteFolder(){const t=this.deleteFolderTarget;if(t){this.deleteFolderState="saving",this.deleteFolderError="";try{if(!(await f.deleteFolder(t.id)).deleted)throw new Error("The server did not confirm deletion.");const s=await this.loadFolders();if(s===null){this.deleteFolderError="Folder may have been deleted, but the folder list could not be refreshed.";return}if(s.some(r=>r.id===t.id)){this.deleteFolderError="The server still returned the folder after deletion. The deletion was not confirmed.";return}if(await this.loadDecks()===null){this.deleteFolderError="Folder was deleted, but the deck list could not be refreshed.";return}this.deleteFolderTarget=null,this.successMessage=`Deleted folder “${t.name}”. Its decks are now unassigned.`}catch(e){this.deleteFolderError=this.messageFor(e,"Folder could not be deleted.")}finally{this.deleteFolderState="idle"}}}openMoveDeckDialog(t){this.isOrphanedDeck(t)||(this.moveDeckTarget=t,this.moveDeckFolderId=t.folder_id,this.moveDeckState="idle",this.moveDeckError="",this.focusTarget="move-deck-dialog")}closeMoveDeckDialog(){this.moveDeckState!=="saving"&&(this.moveDeckTarget=null,this.moveDeckFolderId=null,this.moveDeckError="",this.moveDeckState="idle")}async performMoveDeck(){const t=this.moveDeckTarget;if(!t)return;const e=this.moveDeckFolderId;if(e===t.folder_id){this.moveDeckTarget=null;return}this.moveDeckState="saving",this.moveDeckError="";try{const s=await f.setDeckFolder(t.id,e),i=await this.loadDecks();if(i===null){this.moveDeckError="Move may have succeeded, but the deck list could not be refreshed.";return}if(!i.find(n=>n.id===s.id&&n.folder_id===s.folder_id)){this.moveDeckError="The server did not confirm the deck’s folder. The move was not confirmed.";return}const a=this.folderById(e);this.moveDeckTarget=null,this.moveDeckFolderId=null,this.successMessage=a?`Moved “${t.name}” to “${a.name}”.`:`Moved “${t.name}” to Not in a folder.`}catch(s){this.moveDeckError=this.messageFor(s,"Deck could not be moved.")}finally{this.moveDeckState="idle"}}setDeckTab(t){this.deckTab=t,this.selectedDeckId!==null&&this.lastDeckTabByDeck.set(this.selectedDeckId,t),t==="cards"&&this.selectedDeckId!==null&&this.loadDeckCards(this.selectedDeckId)}async loadDeckCards(t){this.deckCardsStatus="loading",this.deckCardsError="";try{const e=await f.getDeckCards(t);return this.deckCards=e.cards,this.deckCardsStatus="ready",e.cards}catch(e){return this.deckCardsStatus="error",this.deckCardsError=this.messageFor(e,"The cards in this deck could not be loaded."),null}}openEditDialog(t){var e,s;this.stopManagementAudio(),this.releaseManagementRecordingPreview(),this.editingCard=t,this.editLanguages=[...t.selected_languages],this.editGlossDrafts={de:((e=t.user_meanings.de)==null?void 0:e.trim())??"",en:((s=t.user_meanings.en)==null?void 0:s.trim())??""},this.editGlossBusy={de:!1,en:!1},this.editState="idle",this.editError="",this.mgmtAudioStatus="idle",this.mgmtAudioMessage="",this.mgmtRecordingStatus="idle",this.mgmtRecordingBlob=null,this.mgmtRecordingNoteId=t.note_id,this.mgmtRecordingError="",this.mgmtShowRecordingControls=!1,this.mgmtRevertConfirmation=!1,this.focusTarget="edit-dialog"}closeEditDialog(){this.editState==="saving-languages"||this.editState==="saving-gloss"||(this.mgmtRecordingStatus==="recording"&&this.stopManagementRecording(),this.stopManagementAudio(),this.releaseManagementRecordingPreview(),this.editingCard=null,this.editState="idle",this.editError="",this.mgmtAudioStatus="idle",this.mgmtAudioMessage="",this.mgmtRecordingStatus="idle",this.mgmtRecordingBlob=null,this.mgmtRecordingNoteId=null,this.mgmtRecordingError="",this.mgmtShowRecordingControls=!1,this.mgmtRevertConfirmation=!1)}toggleEditMeaningLanguage(t,e){if(e.checked){this.editLanguages=[...new Set([...this.editLanguages,t])];return}if(this.editLanguages.length===1){e.checked=!0;return}this.editLanguages=this.editLanguages.filter(s=>s!==t)}async saveEditGloss(t){var r,a;const e=this.editingCard;if(!e)return;const s=this.editGlossDrafts[t].trim();if(!(!s||(((r=e.user_meanings[t])==null?void 0:r.trim())??"")===s)){this.editGlossBusy={...this.editGlossBusy,[t]:!0},this.editState="saving-gloss",this.editError="";try{await f.setGloss(e.note_id,t,s);const n=await this.loadDeckCards(this.selectedDeckId??-1);if(n===null){this.editState="error",this.editError="Custom meaning may have been saved, but the deck cards could not be refreshed.";return}const p=n.find(u=>u.note_id===e.note_id);if(!p){this.editState="error",this.editError="The server no longer reports this card in the current deck.";return}this.editingCard=p,this.editGlossDrafts={...this.editGlossDrafts,[t]:((a=p.user_meanings[t])==null?void 0:a.trim())??""},this.editState="saved"}catch(n){this.editState="error",this.editError=this.messageFor(n,"Custom meaning could not be saved.")}finally{this.editGlossBusy={...this.editGlossBusy,[t]:!1}}}}async deleteEditGloss(t){var i;const e=this.editingCard;if(!(!e||!(((i=e.user_meanings[t])==null?void 0:i.trim())??""))){this.editGlossBusy={...this.editGlossBusy,[t]:!0},this.editState="saving-gloss",this.editError="";try{if(!(await f.deleteGloss(e.note_id,t)).deleted)throw new Error("The server did not confirm removal.");const a=await this.loadDeckCards(this.selectedDeckId??-1);if(a===null){this.editState="error",this.editError="Custom meaning may have been removed, but the deck cards could not be refreshed.";return}const n=a.find(p=>p.note_id===e.note_id);if(!n){this.editState="error",this.editError="The server no longer reports this card in the current deck.";return}this.editingCard=n,this.editGlossDrafts={...this.editGlossDrafts,[t]:""},this.editState="saved"}catch(r){this.editState="error",this.editError=this.messageFor(r,"Custom meaning could not be removed.")}finally{this.editGlossBusy={...this.editGlossBusy,[t]:!1}}}}async commitEditDialog(){var a,n;const t=this.editingCard;if(!t)return;if(this.editError="",!this.editLanguages.length){this.editState="error",this.editError="Select German, English, or both meaning languages.";return}const e=[...t.selected_languages].sort(),s=[...this.editLanguages].sort();if(e.length!==s.length||e.some((p,u)=>p!==s[u])){this.editState="saving-languages";try{await f.setMeaningLanguages(t.note_id,this.editLanguages);const p=await this.loadDeckCards(this.selectedDeckId??-1);if(p===null){this.editState="error",this.editError="Languages may have been saved, but the deck cards could not be refreshed.";return}const u=p.find(m=>m.note_id===t.note_id);if(!u){this.editState="error",this.editError="The server no longer reports this card in the current deck.";return}this.editingCard=u}catch(p){this.editState="error",this.editError=this.messageFor(p,"Meaning languages could not be saved.");return}finally{}}const r=this.editingCard;if(r){for(const p of["de","en"]){const u=this.editGlossDrafts[p].trim(),m=((a=r.user_meanings[p])==null?void 0:a.trim())??"";if(!(!u&&!m)&&!(u&&u===m)){this.editState="saving-gloss",this.editGlossBusy={...this.editGlossBusy,[p]:!0};try{if(u)await f.setGloss(r.note_id,p,u);else if(!(await f.deleteGloss(r.note_id,p)).deleted)throw new Error("The server did not confirm removal.");const g=await this.loadDeckCards(this.selectedDeckId??-1);if(g===null){this.editState="error",this.editError="Custom meaning may have been saved, but the deck cards could not be refreshed.";return}const v=g.find(y=>y.note_id===r.note_id);if(!v){this.editState="error",this.editError="The server no longer reports this card in the current deck.";return}this.editingCard=v,this.editGlossDrafts={...this.editGlossDrafts,[p]:((n=v.user_meanings[p])==null?void 0:n.trim())??""}}catch(g){this.editState="error",this.editError=this.messageFor(g,u?"Custom meaning could not be saved.":"Custom meaning could not be removed.");return}finally{this.editGlossBusy={...this.editGlossBusy,[p]:!1}}}}this.editState="saved",this.editError=""}}openMoveDialog(t){this.isOrphanedDeckByName(this.currentDeckName())||(this.moveTarget=t,this.moveDestinationDeckId=null,this.moveState="idle",this.moveError="",this.focusTarget="move-dialog")}closeMoveDialog(){this.moveState!=="saving"&&(this.moveTarget=null,this.moveDestinationDeckId=null,this.moveError="")}currentDeckName(){var t;return((t=this.selectedDeck())==null?void 0:t.name)??""}isOrphanedDeckByName(t){return t===U}moveDestinations(){const t=this.currentDeckName();return this.decks.filter(e=>e.name!==t)}async performMove(){const t=this.moveTarget,e=this.moveDestinationDeckId,s=this.selectedDeckId;if(!(!t||e===null||s===null)){this.moveState="saving",this.moveError="";try{await f.moveNoteBetweenDecks(s,t.note_id,e);const i=await this.loadDeckCards(s);if(i===null){this.moveError="Move may have succeeded, but the deck cards could not be refreshed.";return}if(i.some(p=>p.note_id===t.note_id)){this.moveError="The server still reports the card in the source deck. The move was not confirmed.";return}const a=await this.loadDecks();if(a===null){this.moveError="Move succeeded, but the deck list could not be refreshed.";return}const n=a.find(p=>p.id===e);this.successMessage=n?`Moved “${t.headword}” to “${n.name}”.`:`Moved “${t.headword}”.`,this.moveTarget=null,this.moveDestinationDeckId=null}catch(i){this.moveError=this.messageFor(i,"Move could not be completed.")}finally{this.moveState="idle"}}}openRemoveConfirm(t){this.removeTarget=t,this.removeState="idle",this.focusTarget="remove-dialog"}closeRemoveConfirm(){this.removeState!=="saving"&&(this.removeTarget=null)}async performRemove(){const t=this.removeTarget,e=this.selectedDeckId;if(!(!t||e===null)){this.removeState="saving";try{const s=await f.removeNoteFromDeck(e,t.note_id);if(!s.removed)throw new Error("The server did not confirm removal.");const i=await this.loadDeckCards(e);if(i===null){this.errorMessage="Remove may have succeeded, but the deck cards could not be refreshed.";return}if(i.some(n=>n.note_id===t.note_id)){this.errorMessage="The server still reports the card in this deck. Removal was not confirmed.";return}const a=await this.loadDecks();if(a===null){this.errorMessage="Remove succeeded, but the deck list could not be refreshed.";return}if(this.removeTarget=null,s.orphaned){const n=a.find(p=>p.name===U);this.successMessage=n?`Removed “${t.headword}” from this deck. Its study history is preserved in “${n.name}”.`:`Removed “${t.headword}” from this deck. Its study history is preserved.`}else this.successMessage=`Removed “${t.headword}” from this deck. Its study history is preserved.`}catch(s){this.errorMessage=this.messageFor(s,"Remove could not be completed.")}finally{this.removeState="idle"}}}openRenameDialog(){const t=this.selectedDeck();!t||this.isOrphanedDeck(t)||(this.renameOpen=!0,this.renameDraft=t.name,this.renameState="idle",this.renameError="",this.focusTarget="rename-dialog")}restoreDestinations(){return this.decks.filter(t=>t.name!==U)}openRestoreDialog(t){this.isOrphanedDeckByName(this.currentDeckName())&&(this.restoreTarget=t,this.restoreDestinationDeckId=null,this.restoreState="idle",this.restoreError="",this.focusTarget="restore-dialog")}closeRestoreDialog(){this.restoreState!=="saving"&&(this.restoreTarget=null,this.restoreDestinationDeckId=null,this.restoreError="")}async performRestore(){const t=this.restoreTarget,e=this.restoreDestinationDeckId,s=this.selectedDeckId;if(!(!t||e===null||s===null)){this.restoreState="saving",this.restoreError="";try{await f.restoreOrphanedNote(t.note_id,e);const i=await this.loadDecks();if(i===null){this.restoreError="Restore may have succeeded, but the deck list could not be refreshed.";return}const r=i.find(u=>u.id===e),a=r?r.name:"",n=await this.loadDeckCards(s);if(n===null){this.restoreError="Restore may have succeeded, but the Orphaned deck cards could not be refreshed.";return}if(n.some(u=>u.note_id===t.note_id)){this.restoreError="The server still reports the card in the Orphaned deck. The restore was not confirmed.";return}a?this.successMessage=`Restored “${t.headword}” to “${a}”.`:this.successMessage=`Restored “${t.headword}”.`,this.restoreTarget=null,this.restoreDestinationDeckId=null}catch(i){this.restoreError=this.messageForRestore(i)}finally{this.restoreState="idle"}}}messageForRestore(t){if(t instanceof b){if(t.code==="note_not_orphaned")return"This note is no longer orphaned. Reload to see the current state.";if(t.code==="deck_not_found")return"The destination deck could not be found. Reload and try again.";if(t.code==="orphaned_deck_protected")return"The Orphaned deck cannot be used as a destination.";if(t.code==="inconsistent_orphan_state")return"This note cannot be safely restored from Orphaned. Open it from its current deck instead."}return this.messageFor(t,"Restore could not be completed.")}closeSenseDialog(){this.senseState!=="saving"&&(this.senseTarget=null,this.senseCandidates=[],this.senseLookupAssetToken="",this.senseLookupStatus="idle",this.senseSelectedRef=null,this.senseError="")}async openSenseDialog(t){var s;this.senseTarget=t,this.senseCandidates=[],this.senseLookupAssetToken="",this.senseLookupStatus="loading",this.senseSelectedRef=null,this.senseError="",this.focusTarget="sense-dialog";const e=t.headword.trim();if(!e){this.senseLookupStatus="error",this.senseError="Could not read the dictionary word from this card.";return}try{const i=await f.lookup(e),r=i.candidates.map(u=>{var m,g;return{...u,status:u.status??((m=u.senses)!=null&&m.length?"resolved":"needs_gloss"),senses:(g=u.senses)==null?void 0:g.map(v=>{var y,k;return{...v,gloss:v.gloss??((k=(y=v.meanings)==null?void 0:y[0])==null?void 0:k.text)??""}})}});this.senseCandidates=r,this.senseLookupAssetToken=i.asset_token,this.senseLookupStatus="ready";const a=r.find(u=>u.lemma_semantic_ref===t.lemma_semantic_ref);if(!a){this.senseLookupStatus="error",this.senseError="This card’s lemma is no longer in the active dictionary. Reload and try again.";return}this.senseCandidates=[a];const n=(s=a.senses)!=null&&s.length?a.senses:[],p=t.sense_semantic_ref?n.find(u=>u.sense_semantic_ref===t.sense_semantic_ref):void 0;p?this.senseSelectedRef=p.sense_semantic_ref:n.length===1&&n[0]?this.senseSelectedRef=n[0].sense_semantic_ref:this.senseSelectedRef=null}catch(i){this.senseLookupStatus="error",this.senseError=this.messageFor(i,"The dictionary could not be looked up.")}}async performSenseChange(){const t=this.senseTarget;if(!t)return;const e=this.senseSelectedRef;if(!e){this.senseError="Choose a dictionary sense to use for this card.";return}if(!this.senseLookupAssetToken){this.senseError="The dictionary token is missing. Close this dialog and retry.";return}this.senseState="saving",this.senseError="";try{if(await f.changeNoteSense(t.note_id,{asset_token:this.senseLookupAssetToken,sense_semantic_ref:e}),await this.loadDecks()===null){this.senseState="error",this.senseError="Sense change succeeded, but the deck list could not be refreshed.";return}const i=this.selectedDeckId,r=i!==null?await this.loadDeckCards(i):null;if(i!==null&&r===null){this.senseState="error",this.senseError="Sense change succeeded, but the deck cards could not be refreshed.";return}this.successMessage=`Updated dictionary sense for “${t.headword}”.`,this.senseTarget=null,this.senseCandidates=[],this.senseLookupAssetToken="",this.senseSelectedRef=null,this.senseState="saved"}catch(s){this.senseState="error",this.senseError=this.messageForSense(s)}finally{this.senseState!=="error"&&this.senseState!=="saved"&&(this.senseState="idle")}}messageForSense(t){if(t instanceof b){if(t.code==="dictionary_changed")return"The dictionary changed since this dialog opened. Close it and try again.";if(t.code==="invalid_sense_ref")return"The chosen sense is no longer in the active dictionary. Close this dialog and try again.";if(t.code==="same_lemma_required")return"The chosen sense belongs to a different word. Pick a sense for this card’s own word.";if(t.code==="selected_sense_conflict")return"Another card already uses that dictionary sense. Pick a different sense or open that card and change it first.";if(t.code==="legacy_duplicate_conflict")return"Existing duplicate vocabulary needs attention before this card can change its dictionary sense.";if(t.code==="inconsistent_note_identity")return"This card’s persisted identity is inconsistent. Reload and try again.";if(t.code==="unsupported_selected_sense_edit")return"This card cannot change its selected sense here.";if(t.code==="promotion_gates_failed")return"This card has review history and cannot be promoted to a resolved dictionary sense.";if(t.code==="note_not_found")return"This card no longer exists. Reload to see the current state."}return this.messageFor(t,"The dictionary sense could not be changed.")}closeRenameDialog(){this.renameState!=="saving"&&(this.renameOpen=!1,this.renameDraft="",this.renameError="")}async performRename(){const t=this.selectedDeck();if(!t||this.isOrphanedDeck(t))return;const e=this.renameDraft.trim();if(!e){this.renameError="Deck name must not be blank.";return}if(e===t.name){this.renameOpen=!1,this.renameDraft="";return}this.renameState="saving",this.renameError="";try{const s=await f.renameDeck(t.id,e),i=await this.loadDecks();if(i===null){this.renameError="Deck may have been renamed, but the deck list could not be refreshed.";return}const r=i.find(a=>a.id===s.id&&a.name===s.name);if(!r){this.renameError="The server did not return the renamed deck. The change was not confirmed.";return}this.selectedDeckId=r.id,this.manualDeckId=r.id,this.captureDeckId=r.id,this.importDeckId=r.id,this.successMessage=`Renamed deck to “${r.name}”.`,this.renameOpen=!1,this.renameDraft=""}catch(s){s instanceof b&&s.code==="deck_name_conflict"?this.renameError=`A deck named “${e}” already exists. Choose another name.`:this.renameError=this.messageFor(s,"Deck could not be renamed.")}finally{this.renameState="idle"}}stopManagementAudio(){this.mgmtAudioPlayer&&(this.mgmtAudioPlayer.pause(),this.mgmtAudioPlayer.src="",this.mgmtAudioPlayer=null),this.mgmtAudioStatus==="playing"&&(this.mgmtAudioStatus="idle")}async playManagementPronunciation(){const t=this.editingCard;if(!(!t||this.mgmtAudioStatus==="loading")){this.stopManagementAudio(),this.mgmtAudioStatus="loading",this.mgmtAudioMessage="Loading pronunciation…";try{const e=t.has_custom_audio?t.note_id:t.headword,s=await f.fetchAudio(e),i=URL.createObjectURL(s),r=new Audio(i);this.mgmtAudioPlayer=r,r.onended=()=>{URL.revokeObjectURL(i),this.mgmtAudioPlayer=null,this.mgmtAudioStatus="idle",this.mgmtAudioMessage=""},await r.play(),this.mgmtAudioStatus="playing",this.mgmtAudioMessage="Playing pronunciation…"}catch(e){this.mgmtAudioStatus="unavailable",this.mgmtAudioMessage=this.messageFor(e,"Pronunciation is unavailable right now.")}}}releaseManagementRecordingPreview(){this.mgmtRecordingPreviewUrl&&URL.revokeObjectURL(this.mgmtRecordingPreviewUrl),this.mgmtRecordingPreviewUrl=""}setManagementLocalRecording(t){var e;this.releaseManagementRecordingPreview(),this.mgmtRecordingBlob=t,this.mgmtRecordingNoteId=((e=this.editingCard)==null?void 0:e.note_id)??null,this.mgmtRecordingPreviewUrl=URL.createObjectURL(t),this.mgmtRecordingStatus="ready",this.mgmtRecordingError=""}async startManagementRecording(){var t;if(!((t=navigator.mediaDevices)!=null&&t.getUserMedia)||typeof MediaRecorder>"u"){this.mgmtRecordingError="Recording is not available in this browser. You can choose an audio file instead.";return}try{const e=await navigator.mediaDevices.getUserMedia({audio:!0}),s=new MediaRecorder(e);this.recordingChunks=[],s.ondataavailable=i=>{i.data.size&&this.recordingChunks.push(i.data)},s.onstop=()=>{e.getTracks().forEach(i=>i.stop()),this.setManagementLocalRecording(new Blob(this.recordingChunks,{type:s.mimeType||"audio/webm"}))},s.start(),this.mediaRecorder=s,this.mgmtRecordingStatus="recording",this.mgmtRecordingError=""}catch(e){this.mgmtRecordingError=this.messageFor(e,"Microphone access was not granted. You can choose an audio file instead.")}}stopManagementRecording(){var t;((t=this.mediaRecorder)==null?void 0:t.state)==="recording"&&this.mediaRecorder.stop(),this.mediaRecorder=null}selectManagementAudioFile(t){var s;const e=(s=t.target.files)==null?void 0:s[0];e&&this.setManagementLocalRecording(e)}discardManagementRecording(){this.releaseManagementRecordingPreview(),this.mgmtRecordingBlob=null,this.mgmtRecordingNoteId=null,this.mgmtRecordingStatus="idle",this.mgmtRecordingError=""}async saveManagementRecording(){const t=this.editingCard,e=this.mgmtRecordingBlob;if(!(!t||!e||this.mgmtRecordingNoteId!==t.note_id)){this.mgmtRecordingStatus="saving",this.mgmtRecordingError="";try{await f.uploadAudio(t.note_id,e,e.type||"audio/webm"),this.discardManagementRecording(),this.mgmtShowRecordingControls=!1;const s=await this.loadDeckCards(this.selectedDeckId??-1);if(s){const i=s.find(r=>r.note_id===t.note_id);i&&(this.editingCard=i)}this.mgmtAudioMessage="Custom pronunciation saved."}catch(s){this.mgmtRecordingStatus="save-error",this.mgmtRecordingError=this.messageFor(s,"The recording was not saved. Your local take is still available.")}}}async revertManagementCustomAudio(){const t=this.editingCard;if(t){this.mgmtAudioMessage="";try{if(!(await f.revertAudio(t.note_id)).reverted)throw new Error("The server did not confirm the change.");this.mgmtRevertConfirmation=!1;const s=await this.loadDeckCards(this.selectedDeckId??-1);if(s){const i=s.find(r=>r.note_id===t.note_id);i&&(this.editingCard=i)}this.mgmtAudioMessage="Automatic pronunciation restored."}catch(e){this.mgmtAudioMessage=this.messageFor(e,"Automatic pronunciation could not be restored.")}}}openDeck(t){this.selectedDeckId=t.id,this.manualDeckId=t.id,this.captureDeckId=t.id,this.importDeckId=t.id,this.view="deck",this.deckTab=this.lastDeckTabByDeck.get(t.id)??"overview",this.successMessage=""}async openStudy(t){if(this.recordingBlob||this.recordingStatus==="recording"){this.view="study",this.errorMessage="Save or discard the local recording before changing study sessions.";return}this.view="study",this.studyDeckId=t??null,this.studyCard=null,this.isRevealed=!1,this.extraInfoOpen=le(),this.studyError="",this.clearPronunciationState(),await this.loadStudyCard()}backFromStudy(){if(this.recordingBlob||this.recordingStatus==="recording"){this.studyError="Save or discard the local recording before leaving this study session.";return}if(this.studyError="",this.studyDeckId!==null){this.selectedDeckId=this.studyDeckId,this.importDeckId=this.studyDeckId,this.view="deck";return}this.selectedDeckId=null,this.view="decks"}clearPronunciationState(){this.stopAudio(),this.audioMessage="",this.audioStatus="idle",this.showRecordingControls=!1,this.revertConfirmation=!1}async loadStudyCard(){var t,e;this.studyStatus="loading",this.studyError="";try{const s=await f.getNextCard(this.studyDeckId??void 0);this.studyCard=s.card,this.isRevealed=!1,this.extraInfoOpen=le(),this.hasCustomAudio=!!((e=(t=s.card)==null?void 0:t.front.audio_trigger.token)!=null&&e.startsWith("custom:")),this.glossDrafts={de:this.userGlossValue(s.card,"de"),en:this.userGlossValue(s.card,"en")},this.glossState="",this.glossError="",this.studyStatus=s.card?"ready":"empty",s.card||(this.focusTarget="empty")}catch(s){this.studyCard=null,this.studyStatus="error",this.studyError=this.messageFor(s,"The next card could not be loaded.")}}revealCard(){!this.studyCard||this.isRevealed||this.isReviewing||(this.isRevealed=!0,this.extraInfoOpen=this.alwaysShowExtraInfo,this.focusTarget="answer")}toggleExtraInfo(){this.extraInfoOpen=!this.extraInfoOpen}setAlwaysShowExtraInfo(t){this.alwaysShowExtraInfo=t,Ye(ue(),t),this.extraInfoOpen=We({isRevealed:this.isRevealed,newPreference:t})}async submitConfidence(t){const e=this.studyCard;if(!(!e||!this.isRevealed||this.isReviewing)){if(this.recordingBlob){this.studyError="Save or discard the local recording before continuing to the next card.";return}this.isReviewing=!0,this.studyError="";try{await f.reviewCard(e.card_id,t),await this.loadStudyCard()}catch(s){this.studyError=this.messageFor(s,"Your confidence could not be saved. Try the same rating again.")}finally{this.isReviewing=!1}}}meaningFor(t,e){return t==null?void 0:t.back.meanings.find(s=>s.language===e)}userGlossValue(t,e){const s=this.meaningFor(t,e);return s!=null&&s.is_user_authored?s.lines.join(" "):""}async saveGloss(t){const e=this.studyCard,s=this.glossDrafts[t].trim();if(!(!e||!s)){this.glossSavingLanguage=t,this.glossError="",this.glossState="";try{const i=await f.setGloss(e.note_id,t,s);this.glossDrafts={...this.glossDrafts,[t]:i.meaning_text},this.glossState=`${t==="de"?"German":"English"} meaning saved.`,await this.refreshStudyFace(e.card_id)}catch(i){this.glossError=this.messageFor(i,"That meaning could not be saved.")}finally{this.glossSavingLanguage=null}}}async deleteGloss(t){const e=this.studyCard;if(e){this.glossSavingLanguage=t,this.glossError="",this.glossState="";try{if(!(await f.deleteGloss(e.note_id,t)).deleted)throw new Error("The server did not confirm removal.");this.glossDrafts={...this.glossDrafts,[t]:""},this.glossState=`${t==="de"?"German":"English"} meaning removed.`,await this.refreshStudyFace(e.card_id)}catch(s){this.glossError=this.messageFor(s,"That meaning could not be removed.")}finally{this.glossSavingLanguage=null}}}async refreshStudyFace(t){var e,s;try{const i=await f.getNextCard(this.studyDeckId??void 0);((e=i.card)==null?void 0:e.card_id)===t&&(this.studyCard=i.card,this.hasCustomAudio=!!((s=i.card.front.audio_trigger.token)!=null&&s.startsWith("custom:")))}catch{}}audioRequestId(t){return this.hasCustomAudio?t.note_id:t.front.audio_trigger.lemma}stopAudio(){this.audioPlayer&&(this.audioPlayer.pause(),this.audioPlayer.src="",this.audioPlayer=null),this.audioStatus==="playing"&&(this.audioStatus="idle")}async playPronunciation(){const t=this.studyCard;if(!(!t||!t.front.audio_trigger.available||this.audioStatus==="loading")){this.stopAudio(),this.audioStatus="loading",this.audioMessage="Loading pronunciation…";try{const e=await f.fetchAudio(this.audioRequestId(t)),s=URL.createObjectURL(e),i=new Audio(s);this.audioPlayer=i,i.onended=()=>{URL.revokeObjectURL(s),this.audioPlayer=null,this.audioStatus="idle",this.audioMessage=""},await i.play(),this.audioStatus="playing",this.audioMessage="Playing pronunciation…"}catch(e){this.audioStatus="unavailable",this.audioMessage=this.messageFor(e,"Pronunciation is unavailable right now.")}}}releaseRecordingPreview(){this.recordingPreviewUrl&&URL.revokeObjectURL(this.recordingPreviewUrl),this.recordingPreviewUrl=""}setLocalRecording(t){var e;this.releaseRecordingPreview(),this.recordingBlob=t,this.recordingNoteId=((e=this.studyCard)==null?void 0:e.note_id)??null,this.recordingPreviewUrl=URL.createObjectURL(t),this.recordingStatus="ready",this.recordingError=""}async startRecording(){var t;if(!((t=navigator.mediaDevices)!=null&&t.getUserMedia)||typeof MediaRecorder>"u"){this.recordingError="Recording is not available in this browser. You can choose an audio file instead.";return}try{const e=await navigator.mediaDevices.getUserMedia({audio:!0}),s=new MediaRecorder(e);this.recordingChunks=[],s.ondataavailable=i=>{i.data.size&&this.recordingChunks.push(i.data)},s.onstop=()=>{e.getTracks().forEach(i=>i.stop()),this.setLocalRecording(new Blob(this.recordingChunks,{type:s.mimeType||"audio/webm"}))},s.start(),this.mediaRecorder=s,this.recordingStatus="recording",this.recordingError=""}catch(e){this.recordingError=this.messageFor(e,"Microphone access was not granted. You can choose an audio file instead.")}}stopRecording(){var t;((t=this.mediaRecorder)==null?void 0:t.state)==="recording"&&this.mediaRecorder.stop(),this.mediaRecorder=null}selectAudioFile(t){var s;const e=(s=t.target.files)==null?void 0:s[0];e&&this.setLocalRecording(e)}discardRecording(){this.releaseRecordingPreview(),this.recordingBlob=null,this.recordingNoteId=null,this.recordingStatus="idle",this.recordingError=""}async saveRecording(){const t=this.studyCard,e=this.recordingBlob;if(!(!t||!e||this.recordingNoteId!==t.note_id)){this.recordingStatus="saving",this.recordingError="";try{await f.uploadAudio(t.note_id,e,e.type||"audio/webm"),this.discardRecording(),this.showRecordingControls=!1,this.hasCustomAudio=!0,this.audioMessage="Custom pronunciation saved.",await this.refreshStudyFace(t.card_id)}catch(s){this.recordingStatus="save-error",this.recordingError=this.messageFor(s,"The recording was not saved. Your local take is still available.")}}}async revertCustomAudio(){const t=this.studyCard;if(t){this.audioMessage="";try{if(!(await f.revertAudio(t.note_id)).reverted)throw new Error("The server did not confirm the change.");this.hasCustomAudio=!1,this.revertConfirmation=!1,this.audioMessage="Automatic pronunciation restored.",await this.refreshStudyFace(t.card_id)}catch(e){this.audioMessage=this.messageFor(e,"Automatic pronunciation could not be restored.")}}}selectedDeck(){return this.decks.find(t=>t.id===this.selectedDeckId)}manualDeck(){const t=this.manualDeckId??this.selectedDeckId;return this.decks.find(e=>e.id===t)}resetManualSelection(){this.selectedCandidate=null,this.selectedSenseRef=null,this.selectedMeaningLanguages=["de","en"],this.userMeaningDe="",this.userMeaningEn=""}selectCandidate(t){var s;if(this.selectedCandidate=t,t.status!=="resolved"||!((s=t.senses)!=null&&s.length)){this.selectedSenseRef=null;return}const e=t.senses.filter(i=>{var r;return(r=i.meanings)==null?void 0:r.some(a=>{var n;return a.language==="en"&&((n=a.text)==null?void 0:n.trim())})});e.length===1&&e[0]?this.selectedSenseRef=e[0].sense_semantic_ref:e.length>1?this.selectedSenseRef=null:t.senses.length===1&&t.senses[0]?this.selectedSenseRef=t.senses[0].sense_semantic_ref:this.selectedSenseRef=null}toggleMeaningLanguage(t,e){this.selectedMeaningLanguages=e?[...new Set([...this.selectedMeaningLanguages,t])]:this.selectedMeaningLanguages.filter(s=>s!==t)}async lookup(t){t.preventDefault();const e=this.lookupQuery.trim();if(!e){this.errorMessage="Enter a German word before looking it up.",this.successMessage="";return}this.lookupStatus="loading",this.lookupCandidates=[],this.lookupAssetToken="",this.lastSavedNote=null,this.resetManualSelection(),this.errorMessage="",this.successMessage="";try{const s=await f.lookup(e),i=s.candidates.map(a=>{var n,p;return{...a,status:a.status??((n=a.senses)!=null&&n.length?"resolved":"needs_gloss"),senses:(p=a.senses)==null?void 0:p.map(u=>{var m,g;return{...u,gloss:u.gloss??((g=(m=u.meanings)==null?void 0:m[0])==null?void 0:g.text)??""}})}});this.lookupCandidates=i,this.lookupAssetToken=s.asset_token,this.lookupStatus="ready";const r=i.length===1?i[0]:void 0;r&&this.selectCandidate(r)}catch(s){this.lookupStatus="error",this.errorMessage=this.messageFor(s,"German vocabulary could not be looked up.")}}userMeanings(){const t={};return this.userMeaningDe.trim()&&(t.de=this.userMeaningDe.trim()),this.userMeaningEn.trim()&&(t.en=this.userMeaningEn.trim()),Object.keys(t).length?t:void 0}async saveManualNote(t){var i;t.preventDefault();const e=this.selectedCandidate,s=this.manualDeck();if(!e||!this.lookupAssetToken){this.errorMessage="Look up and select a German vocabulary candidate before saving.",this.successMessage="";return}if(!s){this.errorMessage="Select a deck before saving this vocabulary.",this.successMessage="";return}if(!this.selectedMeaningLanguages.length){this.errorMessage="Select German, English, or both meaning languages.",this.successMessage="";return}if(e.status==="resolved"&&!this.selectedSenseRef){this.errorMessage="Select a meaning for this resolved dictionary entry.",this.successMessage="";return}if(e.status==="derived_compound"&&!((i=e.component_refs)!=null&&i.length)){this.errorMessage="This derived compound has no supported component bindings to save.",this.successMessage="";return}this.isSavingNote=!0,this.errorMessage="",this.successMessage="";try{const r=await f.createNote({asset_token:this.lookupAssetToken,lemma_semantic_ref:e.lemma_semantic_ref,sense_semantic_ref:this.selectedSenseRef,status:e.status,component_refs:e.component_refs,meaning_languages:this.selectedMeaningLanguages,deck_name:s.name,user_meanings:this.userMeanings()}),a=await this.loadDecks();if(a===null){this.errorMessage=`“${e.lemma}” may have been saved, but the deck list could not be refreshed.`;return}const n=a.find(p=>p.id===r.deck_id);if(r.deck_id!==s.id||!n){this.errorMessage=`The server did not confirm “${e.lemma}” in the selected deck. It was not reported as saved.`;return}this.selectedDeckId=n.id,this.manualDeckId=n.id,this.lastSavedNote={lemma:e.lemma,deckId:n.id,deckName:n.name},this.successMessage=`Saved “${e.lemma}” to “${n.name}”.`,this.lookupQuery="",this.lookupCandidates=[],this.lookupAssetToken="",this.lookupStatus="idle",this.resetManualSelection()}catch(r){this.successMessage="",this.errorMessage=this.messageFor(r,"Vocabulary could not be saved.")}finally{this.isSavingNote=!1}}captureKey(t){return`${t.lemma_semantic_ref}:${t.status}`}updateCaptureSpan(t){const e=t.target;this.captureSpanStart=e.selectionStart??0,this.captureSpanEnd=e.selectionEnd??0}resetCapturePicker(){this.captureCandidates=[],this.captureAssetToken="",this.captureContext=null,this.captureSelections={},this.captureDictionaryChanged=!1}async highlightCapture(t){t==null||t.preventDefault();const e=this.captureSentence,s=this.captureLessonLabel.trim(),i={start:this.captureSpanStart,end:this.captureSpanEnd};if(!e.trim()){this.captureStatus="error",this.captureError="Enter the sentence you want this card to remember.";return}if(i.start===i.end){this.captureStatus="error",this.captureError="Select the German word or phrase in the sentence before finding candidates.";return}if(!s){this.captureStatus="error",this.captureError="Add a lesson label so this capture keeps its provenance.";return}this.captureStatus="loading",this.captureError="",this.resetCapturePicker();try{const r=await f.highlight({sentence_text:e,selected_span:i,lesson_label:s});this.captureCandidates=r.candidates,this.captureAssetToken=r.asset_token,this.captureContext=r.capture_context,this.captureStatus="ready";const a=r.candidates.length===1?r.candidates[0]:void 0;a&&this.toggleCaptureCandidate(a,!0)}catch(r){this.captureStatus="error",this.captureError=this.messageFor(r,"Candidates could not be found.")}}toggleCaptureCandidate(t,e){var r,a;const s=this.captureKey(t),i={...this.captureSelections};e?i[s]={candidate:t,senseRef:t.status==="resolved"?((a=(r=t.senses)==null?void 0:r[0])==null?void 0:a.sense_semantic_ref)??null:null}:delete i[s],this.captureSelections=i}setCaptureSense(t,e){const s=this.captureKey(t),i=this.captureSelections[s];i&&(this.captureSelections={...this.captureSelections,[s]:{...i,senseRef:e}})}toggleCaptureMeaningLanguage(t,e){if(e.checked){this.captureMeaningLanguages=[...new Set([...this.captureMeaningLanguages,t])];return}if(this.captureMeaningLanguages.length===1){e.checked=!0;return}this.captureMeaningLanguages=this.captureMeaningLanguages.filter(s=>s!==t)}captureUserMeanings(){const t={};return this.captureUserMeaningDe.trim()&&(t.de=this.captureUserMeaningDe.trim()),this.captureUserMeaningEn.trim()&&(t.en=this.captureUserMeaningEn.trim()),Object.keys(t).length?t:void 0}async saveCapture(t){t.preventDefault();const e=this.decks.find(r=>r.id===this.captureDeckId),s=Object.values(this.captureSelections);if(!s.length)return;if(!e){this.captureError="Choose a destination deck before creating cards.";return}if(!this.captureContext||!this.captureAssetToken){this.captureError="Find candidates again before creating cards.";return}if(s.some(({candidate:r,senseRef:a})=>r.status==="resolved"&&!a)){this.captureError="Choose a dictionary meaning for every selected candidate.";return}this.isCapturing=!0,this.captureError="",this.captureDictionaryChanged=!1,this.successMessage="";try{const r=await f.captureCards({asset_token:this.captureAssetToken,deck:{name:e.name,lesson_label:this.captureContext.lesson_label},capture_context:this.captureContext,selections:s.map(({candidate:m,senseRef:g})=>({lemma_semantic_ref:m.lemma_semantic_ref,sense_semantic_ref:g,status:m.status,component_refs:m.component_refs,overrides:{meaning_langs:this.captureMeaningLanguages,user_meanings:this.captureUserMeanings()}}))}),a=await this.loadDecks(),n=a==null?void 0:a.find(m=>m.id===r.deck_id);if(!n||n.id!==e.id){this.captureError="The server did not confirm the selected destination deck. Cards were not reported as created.";return}const p=r.notes.filter(m=>m.created).length,u=r.notes.length-p;this.selectedDeckId=n.id,this.manualDeckId=n.id,this.captureDeckId=n.id,this.successMessage=`Server confirmed ${p} ${p===1?"card":"cards"} created and ${u} ${u===1?"card":"cards"} reused in “${n.name}”.`,this.captureStatus="idle",this.captureSentence="",this.captureLessonLabel="",this.captureSpanStart=0,this.captureSpanEnd=0,this.captureUserMeaningDe="",this.captureUserMeaningEn="",this.resetCapturePicker()}catch(r){r instanceof b&&r.code==="legacy_duplicate_conflict"?this.captureError="Capture stopped because existing duplicate vocabulary needs attention. No words from this capture were added.":r instanceof b&&r.isConflict?(this.captureDictionaryChanged=!0,this.captureError=""):this.captureError=this.messageFor(r,"Cards could not be created.")}finally{this.isCapturing=!1}}async readImportFile(t){var s;const e=(s=t.target.files)==null?void 0:s[0];if(e){this.isReadingImportFile=!0,this.errorMessage="",this.successMessage="";try{this.importText=await e.text(),this.importFileName=e.name}catch(i){this.importFileName="",this.errorMessage=this.messageFor(i,"The selected file could not be read.")}finally{this.isReadingImportFile=!1}}}async importCsv(t){t.preventDefault();const e=this.decks.find(i=>i.id===this.importDeckId),s=this.importText.trim();if(!e){this.errorMessage="Select a destination deck before importing.",this.successMessage="";return}if(!s){this.errorMessage="Paste vocabulary lines or choose a CSV/text file before importing.",this.successMessage="";return}this.isImporting=!0,this.errorMessage="",this.successMessage="";try{const i=await f.importCsv({csv_text:s,deck_name:e.name}),r=await this.loadDecks();if(r===null){this.errorMessage="The import may have completed, but the deck list could not be refreshed.";return}const a=r.find(n=>n.id===i.deck_id);if(!a){this.errorMessage="The server did not return the import deck after completion. The import was not reported as successful.";return}this.selectedDeckId=a.id,this.manualDeckId=a.id,this.importDeckId=a.id,this.successMessage=`Import complete: ${i.notes_created} added, ${i.notes_reused} already existed, ${i.total_words} total in “${a.name}”.`,this.importText="",this.importFileName=""}catch(i){this.successMessage="",i instanceof b&&i.code==="legacy_duplicate_conflict"?this.errorMessage="Import stopped because existing duplicate vocabulary needs attention. No words from this import were added.":this.errorMessage=this.messageFor(i,"CSV import could not be completed.")}finally{this.isImporting=!1}}async exportTsv(t){this.exportingFormat="tsv",this.errorMessage="",this.successMessage="";try{const e=await f.exportAnki(t.id),s=URL.createObjectURL(new Blob([e],{type:"text/tab-separated-values;charset=utf-8"})),i=document.createElement("a");i.href=s,i.download=`${t.name.replace(/[^a-z0-9._-]+/gi,"-")||"flashcards"}.tsv`,i.click(),URL.revokeObjectURL(s),this.successMessage=`Prepared a TSV export for “${t.name}”.`}catch(e){this.errorMessage=this.messageFor(e,"TSV export could not be prepared.")}finally{this.exportingFormat=null}}async exportApkg(t){this.exportingFormat="apkg",this.errorMessage="",this.successMessage="";try{const e=await f.exportApkg(t.id),s=URL.createObjectURL(e),i=document.createElement("a");i.href=s,i.download=`${t.name.replace(/[^a-z0-9._-]+/gi,"-")||"flashcards"}.apkg`,i.click(),URL.revokeObjectURL(s),this.successMessage=`Prepared an APKG export for “${t.name}”.`}catch(e){this.errorMessage=this.messageFor(e,"APKG export could not be prepared.")}finally{this.exportingFormat=null}}renderNotices(){return o`
      ${this.errorMessage?o`<div class="notice error" role="alert">${this.errorMessage}</div>`:h}
      ${this.successMessage?o`<div class="notice success" role="status">${this.successMessage}</div>`:h}
    `}renderNoDecksNote(){return o`<div class="empty"><p>No decks yet. Create one to begin organizing German vocabulary.</p></div>`}renderDeckRow(t,e){const s=this.isOrphanedDeck(t),i=`${t.card_count} ${t.card_count===1?"card":"cards"} · ${t.due_count} due · ${t.mastery_percent}% mastered`;return o`
      <li class="deck">
        <button class="deck-open" @click=${()=>this.openDeck(t)} aria-label=${`Open ${t.name}`}>
          <span class="deck-name">${t.name}</span>
          <span class="deck-stats">${i}</span>
        </button>
        <div class="deck-row-actions">
          ${e&&!s?o`
            <button type="button" @click=${()=>this.openMoveDeckDialog(t)} aria-label=${`Move ${t.name} to folder`}>Move to folder</button>
          `:h}
          ${this.pendingDeleteDeckId===t.id?o`
            <div class="actions confirm" aria-label=${`Confirm deletion of ${t.name}`}>
              <button class="danger" ?disabled=${this.isDeleting} @click=${()=>void this.deleteDeck(t)}>${this.isDeleting?"Deleting…":"Confirm delete"}</button>
              <button ?disabled=${this.isDeleting} @click=${()=>{this.pendingDeleteDeckId=null}}>Cancel</button>
            </div>
          `:o`
            <button class="danger" @click=${()=>{this.pendingDeleteDeckId=t.id,this.successMessage=""}}>Delete</button>
          `}
        </div>
      </li>
    `}renderFlatDeckList(){return o`
      <ul class="deck-list" aria-label="Your decks">
        ${this.decks.map(t=>this.renderDeckRow(t,!1))}
      </ul>
    `}renderGroupedDeckList(){const t=this.folders.length>0;return o`
      ${this.groupedDeckSections().map(e=>o`
        <section class="folder-group" aria-label=${e.folder?`Folder ${e.folder.name}`:"Not in a folder"}>
          <div class="folder-heading-row">
            <h3 class="folder-heading">${e.folder?e.folder.name:"Not in a folder"}</h3>
            ${e.folder?o`
              <div class="folder-actions">
                <button type="button" aria-label=${`Rename folder ${e.folder.name}`} @click=${()=>this.openRenameFolderDialog(e.folder)}>Rename</button>
                <button class="danger" type="button" aria-label=${`Delete folder ${e.folder.name}`} @click=${()=>this.openDeleteFolderDialog(e.folder)}>Delete folder</button>
              </div>
            `:h}
          </div>
          ${e.decks.length?o`
            <ul class="deck-list" aria-label=${e.folder?`Decks in ${e.folder.name}`:"Decks not in a folder"}>
              ${e.decks.map(s=>this.renderDeckRow(s,t))}
            </ul>
          `:o`
            <p class="muted folder-empty">${e.folder?"No decks in this folder yet.":"No decks outside folders yet."}</p>
          `}
        </section>
      `)}
    `}renderDeckList(){if(this.deckStatus==="loading")return o`<p class="loading" role="status">Loading decks…</p>`;if(this.deckStatus==="error")return o`<div class="empty"><p>We could not reach your deck list.</p><button @click=${this.loadDecks}>Try again</button></div>`;if(this.folderStatus==="loading")return o`
        <p class="loading" aria-live="polite">Loading folders…</p>
        ${this.decks.length?this.renderFlatDeckList():this.renderNoDecksNote()}
      `;if(this.folderStatus==="error")return o`
        <div class="notice error" role="alert">
          ${this.folderError||"Folders could not be loaded."}
          ${" "}<button @click=${()=>void this.loadFolders()}>Try again</button>
        </div>
        ${this.decks.length?this.renderFlatDeckList():this.renderNoDecksNote()}
      `;if(this.decks.length===0&&this.folders.length===0)return this.renderNoDecksNote();const t=this.decks.some(e=>!this.isOrphanedDeck(e));return o`
      ${t?h:this.renderNoDecksNote()}
      ${this.renderGroupedDeckList()}
    `}renderCreateFolderDialog(){if(!this.createFolderOpen)return h;const t=this.createFolderState==="saving";return o`
      <div class="dialog-backdrop" @click=${e=>{e.target===e.currentTarget&&this.closeCreateFolderDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="create-folder-title" data-create-folder-dialog tabindex="-1">
          <h2 id="create-folder-title">New folder</h2>
          <p class="muted">Folders group your decks. A deck belongs to at most one folder.</p>
          <form @submit=${e=>{e.preventDefault(),this.performCreateFolder()}}>
            <label>Folder name
              <input
                .value=${this.newFolderName}
                @input=${e=>{this.newFolderName=e.target.value,this.createFolderError=""}}
                ?disabled=${t}
                maxlength="200"
                autocomplete="off"
              />
            </label>
            ${this.createFolderError?o`<p class="inline-status error" role="alert">${this.createFolderError}</p>`:h}
            <div class="actions">
              <button type="button" @click=${this.closeCreateFolderDialog} ?disabled=${t}>Cancel</button>
              <button class="primary" type="submit" ?disabled=${t}>${t?"Creating…":"Create folder"}</button>
            </div>
          </form>
        </div>
      </div>
    `}renderRenameFolderDialog(){if(!this.renameFolderTarget)return h;const e=this.renameFolderState==="saving";return o`
      <div class="dialog-backdrop" @click=${s=>{s.target===s.currentTarget&&this.closeRenameFolderDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rename-folder-title" data-rename-folder-dialog tabindex="-1">
          <h2 id="rename-folder-title">Rename folder</h2>
          <label>Folder name
            <input
              .value=${this.renameFolderDraft}
              @input=${s=>{this.renameFolderDraft=s.target.value,this.renameFolderError=""}}
              ?disabled=${e}
              maxlength="200"
              autocomplete="off"
            />
          </label>
          ${this.renameFolderError?o`<p class="inline-status error" role="alert">${this.renameFolderError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${this.closeRenameFolderDialog} ?disabled=${e}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.performRenameFolder()} ?disabled=${e}>${e?"Renaming…":"Rename folder"}</button>
          </div>
        </div>
      </div>
    `}renderDeleteFolderDialog(){const t=this.deleteFolderTarget;if(!t)return h;const e=this.deleteFolderState==="saving";return o`
      <div class="dialog-backdrop" @click=${s=>{s.target===s.currentTarget&&this.closeDeleteFolderDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-folder-title" data-delete-folder-dialog tabindex="-1">
          <h2 id="delete-folder-title">Delete folder</h2>
          <p>Delete the folder “<strong>${t.name}</strong>”? Deleting the folder does not delete its decks; those decks become unassigned.</p>
          ${this.deleteFolderError?o`<p class="inline-status error" role="alert">${this.deleteFolderError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${this.closeDeleteFolderDialog} ?disabled=${e}>Cancel</button>
            <button class="danger" type="button" @click=${()=>void this.performDeleteFolder()} ?disabled=${e}>${e?"Deleting…":"Delete folder"}</button>
          </div>
        </div>
      </div>
    `}renderMoveDeckDialog(){const t=this.moveDeckTarget;if(!t)return h;const e=this.moveDeckState==="saving";return o`
      <div class="dialog-backdrop" @click=${s=>{s.target===s.currentTarget&&this.closeMoveDeckDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="move-deck-title" data-move-deck-dialog tabindex="-1">
          <h2 id="move-deck-title">Move to folder</h2>
          <p>Move the deck “<strong>${t.name}</strong>” to a folder. Choosing “Not in a folder” removes it from its current folder.</p>
          <label>Folder
            <select
              .value=${this.moveDeckFolderId===null?"":String(this.moveDeckFolderId)}
              @change=${s=>{const i=s.target.value;this.moveDeckFolderId=i?Number(i):null}}
              ?disabled=${e}
              data-move-deck-folder-select
            >
              <option value="">Not in a folder</option>
              ${this.folders.map(s=>o`<option value=${s.id}>${s.name}</option>`)}
            </select>
          </label>
          ${this.moveDeckError?o`<p class="inline-status error" role="alert">${this.moveDeckError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${this.closeMoveDeckDialog} ?disabled=${e}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.performMoveDeck()} ?disabled=${e}>${e?"Moving…":"Move deck"}</button>
          </div>
        </div>
      </div>
    `}renderSenseChoices(t){const e=t.filter(i=>{var r;return(r=i.meanings)==null?void 0:r.some(a=>{var n;return a.language==="en"&&((n=a.text)==null?void 0:n.trim())})}),s=e.length?e:t;return o`
      <fieldset class="selection">
        <legend>Dictionary meaning</legend>
        <ul class="choice-list">
          ${s.map(i=>o`
            <li>
              <label class="choice">
                <input
                  type="radio"
                  name="sense"
                  .value=${i.sense_semantic_ref}
                  .checked=${this.selectedSenseRef===i.sense_semantic_ref}
                  @change=${()=>{this.selectedSenseRef=i.sense_semantic_ref}}
                />
                <span>${he(i)}</span>
              </label>
            </li>
          `)}
        </ul>
      </fieldset>
    `}renderManualCreation(){var s;const t=this.selectedCandidate,e=this.manualDeck();return o`
      <section class="workflow" aria-labelledby="manual-title">
        <h3 id="manual-title">Add German vocabulary</h3>
        <p class="muted">Look up a German word, choose its dictionary meaning, then let the server create the note.</p>
        ${this.lastSavedNote?o`
          <div class="save-success-banner" role="region" aria-label="Vocabulary saved">
            <p>Saved “${this.lastSavedNote.lemma}” to “${this.lastSavedNote.deckName}”.</p>
            <div class="save-success-actions">
              <button class="primary" type="button" @click=${()=>void this.openStudy(this.lastSavedNote.deckId)}>Study this deck</button>
              <button class="secondary" type="button" @click=${()=>{this.selectedDeckId=this.lastSavedNote.deckId,this.importDeckId=this.lastSavedNote.deckId,this.view="deck"}}>Open deck</button>
              <button class="secondary" type="button" @click=${()=>{this.lastSavedNote=null,this.lookupQuery="",this.resetManualSelection()}}>Add another word</button>
            </div>
          </div>
        `:h}
        <form @submit=${this.lookup}>
          <label>German word
            <input
              .value=${this.lookupQuery}
              @input=${i=>{this.lookupQuery=i.target.value,this.lastSavedNote=null}}
              ?disabled=${this.lookupStatus==="loading"||this.isSavingNote}
              autocomplete="off"
              placeholder="e.g. anrufen"
            />
          </label>
          <div class="actions"><button class="primary" type="submit" ?disabled=${this.lookupStatus==="loading"||this.isSavingNote}>${this.lookupStatus==="loading"?"Looking up…":"Look up"}</button></div>
        </form>
        ${this.lookupStatus==="loading"?o`<p class="result" role="status">Looking up the active dictionary…</p>`:h}
        ${this.lookupStatus==="ready"&&!this.lookupCandidates.length?o`<p class="result">No dictionary candidate was returned. Try a different German form.</p>`:h}
        ${this.lookupCandidates.length?o`
          <fieldset class="selection">
            <legend>Select vocabulary</legend>
            <ul class="candidate-list">
              ${this.lookupCandidates.map(i=>o`
                <li>
                  <button
                    class="candidate ${t===i?"selected":""}"
                    type="button"
                    @click=${()=>this.selectCandidate(i)}
                    aria-pressed=${t===i?"true":"false"}
                  >
                    <div class="candidate-header">
                      <span class="candidate-headword">${Je(i)}</span>
                      <span class="muted">· ${i.pos}</span>
                    </div>
                    ${ce(i)?o`<div class="candidate-gloss">${ce(i)}</div>`:h}
                    <small>${i.status==="resolved"?"Dictionary entry":i.status.replace("_"," ")}</small>
                  </button>
                </li>
              `)}
            </ul>
          </fieldset>
        `:h}
        ${t?o`
          <form @submit=${this.saveManualNote}>
            ${t.status==="resolved"?(s=t.senses)!=null&&s.length?this.renderSenseChoices(t.senses):o`<p class="result">This result has no selectable sense and cannot be saved as a resolved note.</p>`:h}
            ${t.status==="derived_compound"?o`<p class="result">The server will retain this compound’s supported component bindings.</p>`:h}
            <label>Deck
              <select
                .value=${e?String(e.id):""}
                @change=${i=>{const r=i.target.value;this.manualDeckId=r?Number(r):null}}
                ?disabled=${this.isSavingNote}
              >
                <option value="">Select a deck</option>
                ${this.decks.map(i=>o`<option value=${i.id}>${i.name}</option>`)}
              </select>
            </label>
            <fieldset class="selection">
              <legend>Meaning languages</legend>
              <label class="choice"><input type="checkbox" .checked=${this.selectedMeaningLanguages.includes("de")} @change=${i=>this.toggleMeaningLanguage("de",i.target.checked)} /> German (DE)</label>
              <label class="choice"><input type="checkbox" .checked=${this.selectedMeaningLanguages.includes("en")} @change=${i=>this.toggleMeaningLanguage("en",i.target.checked)} /> English (EN)</label>
            </fieldset>
            <details class="optional-meanings">
              <summary>Optional custom meanings</summary>
              <label>Your German meaning <span class="muted">(optional)</span>
                <input .value=${this.userMeaningDe} @input=${i=>{this.userMeaningDe=i.target.value}} ?disabled=${this.isSavingNote} autocomplete="off" />
              </label>
              <label>Your English meaning <span class="muted">(optional)</span>
                <input .value=${this.userMeaningEn} @input=${i=>{this.userMeaningEn=i.target.value}} ?disabled=${this.isSavingNote} autocomplete="off" />
              </label>
            </details>
            <div class="actions"><button class="primary" type="submit" ?disabled=${this.isSavingNote}>${this.isSavingNote?"Saving…":"Save vocabulary"}</button></div>
          </form>
        `:h}
      </section>
    `}renderCaptureCreation(t){const e=Object.keys(this.captureSelections).length,s=this.decks.find(r=>r.id===this.captureDeckId),i=this.captureSentence.slice(this.captureSpanStart,this.captureSpanEnd);return o`
      <section class="workflow capture-workflow" aria-labelledby="capture-title">
        <h3 id="capture-title">Capture from a sentence</h3>
        <p class="muted">Paste or type a sentence, select its German word or phrase, then choose the cards to create.</p>
        <form @submit=${this.highlightCapture}>
          <label>Sentence text
            <textarea
              .value=${this.captureSentence}
              @input=${r=>{this.captureSentence=r.target.value,this.updateCaptureSpan(r),this.resetCapturePicker(),this.captureStatus="idle",this.captureError=""}}
              @select=${this.updateCaptureSpan}
              @keyup=${this.updateCaptureSpan}
              @click=${this.updateCaptureSpan}
              ?disabled=${this.captureStatus==="loading"||this.isCapturing}
              placeholder="Ich rufe dich morgen an."
            ></textarea>
          </label>
          <p class="selection-preview" aria-live="polite">${i?o`Selected: <strong>“${i}”</strong>`:"Select a German word or phrase in the sentence."}</p>
          <label>Lesson label
            <input
              .value=${this.captureLessonLabel}
              @input=${r=>{this.captureLessonLabel=r.target.value,this.resetCapturePicker(),this.captureStatus="idle",this.captureError=""}}
              ?disabled=${this.captureStatus==="loading"||this.isCapturing}
              autocomplete="off"
              placeholder="Lesson 4 · Telephone calls"
            />
          </label>
          <div class="actions">
            <button class="primary" type="submit" ?disabled=${this.captureStatus==="loading"||this.isCapturing}>${this.captureStatus==="loading"?"Finding candidates…":"Find candidates"}</button>
          </div>
        </form>
        ${this.captureStatus==="loading"?o`<p class="result" role="status">Checking the active dictionary…</p>`:h}
        ${this.captureStatus==="error"?o`<div class="capture-state error" role="alert"><p>${this.captureError}</p><button @click=${()=>void this.highlightCapture()}>Try again</button></div>`:h}
        ${this.captureStatus==="ready"&&this.captureCandidates.length===0?o`<div class="capture-state"><p>No dictionary candidates were found for “${i}”. Adjust the selected text and try again.</p></div>`:h}
        ${this.captureCandidates.length?o`
          <form class="capture-picker" @submit=${this.saveCapture}>
            <fieldset class="selection">
              <legend>Choose vocabulary <span class="muted">(select one or more)</span></legend>
              <p class="result">Each checked German candidate becomes its own card. You can select multiple candidates.</p>
              <ul class="candidate-list">
                ${this.captureCandidates.map(r=>{var p;const a=this.captureKey(r),n=this.captureSelections[a];return o`
                    <li class="capture-candidate ${n?"chosen":""}">
                      <label class="candidate-choice">
                        <input
                          type="checkbox"
                          .checked=${!!n}
                          @change=${u=>this.toggleCaptureCandidate(r,u.target.checked)}
                          ?disabled=${this.isCapturing}
                        />
                        <span><strong class="lemma">${r.lemma}</strong> <span class="caption">${r.pos}</span></span>
                      </label>
                      ${n&&r.status==="resolved"?(p=r.senses)!=null&&p.length?o`
                        <fieldset class="sense-choices">
                          <legend>Dictionary meaning for ${r.lemma}</legend>
                          ${r.senses.map(u=>o`
                            <label class="choice">
                              <input type="radio" name=${`capture-sense-${a}`} .value=${u.sense_semantic_ref} .checked=${n.senseRef===u.sense_semantic_ref} @change=${()=>this.setCaptureSense(r,u.sense_semantic_ref)} ?disabled=${this.isCapturing} />
                              ${u.gloss||`Meaning ${u.ord}`}
                            </label>
                          `)}
                        </fieldset>
                      `:o`<p class="result">This entry has no selectable dictionary meaning.</p>`:h}
                      ${n&&r.status==="derived_compound"?o`<p class="result">The server will preserve the compound’s dictionary component bindings.</p>`:h}
                    </li>
                  `})}
              </ul>
            </fieldset>
            ${this.captureDictionaryChanged?o`
              <div class="capture-state warning" role="alert">
                <p>The dictionary changed while you were choosing cards. Your selections have not been saved.</p>
                <button type="button" @click=${()=>void this.highlightCapture()}>Find fresh candidates</button>
              </div>
            `:h}
            ${this.captureError?o`<div class="capture-state error" role="alert"><p>${this.captureError}</p></div>`:h}
            <fieldset class="selection">
              <legend>Meaning languages</legend>
              <p class="result">Choose German, English, or both. At least one language stays selected.</p>
              <label class="choice"><input type="checkbox" .checked=${this.captureMeaningLanguages.includes("de")} @change=${r=>this.toggleCaptureMeaningLanguage("de",r.target)} ?disabled=${this.isCapturing} /> German (DE)</label>
              <label class="choice"><input type="checkbox" .checked=${this.captureMeaningLanguages.includes("en")} @change=${r=>this.toggleCaptureMeaningLanguage("en",r.target)} ?disabled=${this.isCapturing} /> English (EN)</label>
            </fieldset>
            ${this.captureMeaningLanguages.includes("de")?o`<label>Your German meaning <span class="muted">(optional)</span><input .value=${this.captureUserMeaningDe} @input=${r=>{this.captureUserMeaningDe=r.target.value}} ?disabled=${this.isCapturing} autocomplete="off" /></label>`:h}
            ${this.captureMeaningLanguages.includes("en")?o`<label>Your English meaning <span class="muted">(optional)</span><input .value=${this.captureUserMeaningEn} @input=${r=>{this.captureUserMeaningEn=r.target.value}} ?disabled=${this.isCapturing} autocomplete="off" /></label>`:h}
            <label>Destination deck
              <select .value=${String(s?s.id:t.id)} @change=${r=>{const a=r.target.value;this.captureDeckId=a?Number(a):null}} ?disabled=${this.isCapturing}>
                <option value="">Select a deck</option>
                ${this.decks.map(r=>o`<option value=${r.id}>${r.name}</option>`)}
              </select>
            </label>
            <div class="actions create-actions">
              <button class="primary" type="submit" ?disabled=${e===0||this.isCapturing||this.captureDictionaryChanged}>${this.isCapturing?"Creating cards…":`Create ${e||""} card${e===1?"":"s"}`}</button>
              ${e===0?o`<p class="disabled-explanation">Select at least one candidate to create cards.</p>`:h}
            </div>
          </form>
        `:h}
      </section>
    `}renderImportExport(t){const e=this.decks.find(i=>i.id===this.importDeckId),s=(e==null?void 0:e.id)??t.id;return o`
      <section class="workflow" aria-labelledby="import-export-title">
        <h3 id="import-export-title">Import & export</h3>
        <form @submit=${this.importCsv}>
          <label>Destination deck
            <select
              .value=${String(s)}
              @change=${i=>{const r=i.target.value;this.importDeckId=r?Number(r):null}}
              ?disabled=${this.isImporting||this.isReadingImportFile}
            >
              <option value="">Select a deck</option>
              ${this.decks.map(i=>o`<option value=${i.id} ?selected=${i.id===s}>${i.name}</option>`)}
            </select>
          </label>
          <label>Vocabulary lines
            <textarea .value=${this.importText} @input=${i=>{this.importText=i.target.value}} ?disabled=${this.isImporting||this.isReadingImportFile} placeholder="Haus&#10;anrufen&#10;Feierabend"></textarea>
          </label>
          <label>Or choose a CSV/text file
            <input type="file" accept=".csv,.txt,text/csv,text/plain" @change=${this.readImportFile} ?disabled=${this.isImporting||this.isReadingImportFile} />
          </label>
          ${this.isReadingImportFile?o`<p class="result" role="status">Reading file…</p>`:h}
          ${this.importFileName?o`<p class="result">Using text from ${this.importFileName}.</p>`:h}
          <div class="actions"><button class="primary" type="submit" ?disabled=${this.isImporting||this.isReadingImportFile||!e}>${this.isImporting?"Importing vocabulary…":"Import CSV"}</button></div>
          ${this.isImporting?o`
            <div class="import-busy" role="status" aria-live="polite">
              <progress class="import-progress"></progress>
              <p>Importing vocabulary…</p>
            </div>
          `:h}
        </form>
        <div class="workflow-grid">
          <div>
            <h3>APKG export</h3>
            <p class="muted">Recommended — includes audio and richer card data.</p>
            <button class="primary" aria-label=${`Export “${t.name}” to Anki (.apkg)`} @click=${()=>void this.exportApkg(t)} ?disabled=${this.exportingFormat!==null}>${this.exportingFormat==="apkg"?"Preparing APKG…":"Export to Anki (.apkg)"}</button>
          </div>
          <div>
            <h3>TSV export</h3>
            <p class="muted">Text-only backup/interchange — audio is not included.</p>
            <button aria-label=${`Export “${t.name}” as TSV`} @click=${()=>void this.exportTsv(t)} ?disabled=${this.exportingFormat!==null}>${this.exportingFormat==="tsv"?"Preparing TSV…":"Export as TSV"}</button>
          </div>
        </div>
      </section>
    `}renderSimplePronunciation(){return o`
      <div class="pronunciation-simple">
        <div class="audio-actions">
          <button type="button" @click=${()=>void this.playPronunciation()} ?disabled=${this.audioStatus==="loading"}>
            ${this.audioStatus==="loading"?"Loading pronunciation…":this.audioStatus==="playing"?"Playing pronunciation…":"Play pronunciation"}
          </button>
          <span class="caption">Press R to replay</span>
        </div>
        ${this.audioMessage?o`<p class="inline-status ${this.audioStatus==="unavailable"?"error":""}" role=${this.audioStatus==="unavailable"?"alert":"status"}>${this.audioMessage}</p>`:h}
      </div>
    `}renderPronunciationManagement(){const t=this.recordingStatus==="save-error";return o`
      <section class="pronunciation" aria-labelledby="pronunciation-title">
        <h3 id="pronunciation-title">Custom pronunciation</h3>
        ${this.hasCustomAudio?o`
          <div class="audio-actions">
            <button type="button" @click=${()=>{this.showRecordingControls=!this.showRecordingControls,this.revertConfirmation=!1}}>
              ${this.showRecordingControls?"Keep current pronunciation":"Replace pronunciation"}
            </button>
            ${this.revertConfirmation?o`
              <span class="caption">Replace your custom pronunciation with automatic pronunciation?</span>
              <button class="danger" type="button" @click=${()=>void this.revertCustomAudio()}>Confirm revert to automatic</button>
              <button type="button" @click=${()=>{this.revertConfirmation=!1}}>Cancel</button>
            `:o`<button class="danger" type="button" @click=${()=>{this.revertConfirmation=!0,this.showRecordingControls=!1}}>Revert to automatic</button>`}
          </div>
        `:o`<button type="button" @click=${()=>{this.showRecordingControls=!this.showRecordingControls}}>Add your pronunciation</button>`}
        ${this.showRecordingControls?o`
          <div class="local-take">
            <p class="muted">Record a take or choose an audio file. It stays only in this browser until you save it.</p>
            ${this.recordingBlob?o`
              <p class="inline-status">Local recording ready to preview and save.</p>
              <audio class="audio-preview" controls src=${this.recordingPreviewUrl}></audio>
            `:h}
            ${t?o`
              <p class="inline-status error" role="alert">${this.recordingError}</p>
              <div class="recording-actions">
                <button class="primary" type="button" @click=${()=>void this.saveRecording()}>Try again</button>
                <button class="danger" type="button" @click=${this.discardRecording}>Discard recording</button>
              </div>
            `:o`
              <div class="recording-actions">
                ${this.recordingStatus==="recording"?o`<button class="danger" type="button" @click=${this.stopRecording}>Stop recording</button>`:o`<button type="button" @click=${()=>void this.startRecording()} ?disabled=${this.recordingStatus==="saving"}>Record pronunciation</button>`}
                <label>Choose audio file
                  <input type="file" accept="audio/*" @change=${this.selectAudioFile} ?disabled=${this.recordingStatus==="recording"||this.recordingStatus==="saving"} />
                </label>
                ${this.recordingBlob?o`
                  <button class="primary" type="button" @click=${()=>void this.saveRecording()} ?disabled=${this.recordingStatus==="saving"}>${this.recordingStatus==="saving"?"Saving pronunciation…":"Save recording"}</button>
                  <button class="danger" type="button" @click=${this.discardRecording} ?disabled=${this.recordingStatus==="saving"}>Discard recording</button>
                `:h}
              </div>
              ${this.recordingError?o`<p class="inline-status error" role="alert">${this.recordingError}</p>`:h}
            `}
          </div>
        `:h}
      </section>
    `}renderMeaningEditor(t){return o`
      <section class="edit-meanings" aria-labelledby="meaning-edit-title">
        <h3 id="meaning-edit-title">Your meanings</h3>
        <p class="muted">Save your wording for either language, or remove an existing personal meaning to return to the card’s available meaning.</p>
        ${["de","en"].map(e=>{const s=this.meaningFor(t,e),i=!!(s!=null&&s.is_user_authored);return o`
            <div class="gloss-row">
              <label>Your ${e==="de"?"German":"English"} meaning
                <input
                  .value=${this.glossDrafts[e]}
                  @input=${a=>{this.glossDrafts={...this.glossDrafts,[e]:a.target.value}}}
                  ?disabled=${this.glossSavingLanguage===e}
                  autocomplete="off"
                />
              </label>
              <button type="button" @click=${()=>void this.saveGloss(e)} ?disabled=${this.glossSavingLanguage===e||!this.glossDrafts[e].trim()}>
                ${this.glossSavingLanguage===e?"Saving…":"Save"}
              </button>
              ${i?o`<button class="danger" type="button" @click=${()=>void this.deleteGloss(e)} ?disabled=${this.glossSavingLanguage===e}>Remove</button>`:h}
            </div>
          `})}
        ${this.glossState?o`<p class="inline-status" role="status">${this.glossState}</p>`:h}
        ${this.glossError?o`<p class="inline-status error" role="alert">${this.glossError}</p>`:h}
      </section>
    `}renderStudyCard(t){const e=this.meaningFor(t,"de"),s=this.meaningFor(t,"en"),i=t.back.meanings.flatMap(r=>r.lines.slice(1).map(a=>`${r.heading}: ${a}`));return o`
      <div class="card-stage">
        <div class="card-side">
          <span class="front-label">German vocabulary</span>
          <h2 class="study-lemma">${t.front.display_headword}</h2>
          <p class="study-meta">${t.front.pos}${t.front.ipa?` · ${t.front.ipa}`:""}</p>
          <div class="front-audio">${this.renderSimplePronunciation()}</div>
          ${this.isRevealed?o`
            <div class="card-side" data-study-answer tabindex="-1">
              <hr class="answer-rule" />
              <span class="front-label">Answer</span>
              ${s?o`
                <p class="meaning primary-meaning"><span class="meaning-label">English</span><br />${s.lines[0]??""}</p>
                ${e!=null&&e.lines[0]?o`<p class="meaning"><span class="meaning-label">German</span><br />${e.lines[0]}</p>`:h}
              `:e!=null&&e.lines[0]?o`
                <p class="meaning primary-meaning"><span class="meaning-label">German</span><br />${e.lines[0]}</p>
              `:h}
              <p class="compact-grammar">${t.back.grammar.lines.join(" · ")||t.back.pos}</p>
              ${t.back.examples.slice(0,2).map(r=>o`
                <p class="example">${r.de}${r.en?o`<span class="example-translation">${r.en}</span>`:h}</p>
              `)}
              <div class="extra-info-row">
                <button
                  type="button"
                  aria-expanded=${this.extraInfoOpen?"true":"false"}
                  aria-controls="extra-info-panel"
                  @click=${this.toggleExtraInfo}
                >${this.extraInfoOpen?"Hide extra info":"Show extra info"}</button>
                <label class="always-extra-toggle">
                  <input
                    type="checkbox"
                    .checked=${this.alwaysShowExtraInfo}
                    @change=${r=>this.setAlwaysShowExtraInfo(r.target.checked)}
                  />
                  Always show extra info
                </label>
              </div>
              ${this.extraInfoOpen?o`
                <div class="extra-info" id="extra-info-panel">
                  ${i.length?o`<div class="detail-block"><span class="meaning-label">Extended notes</span><ul>${i.map(r=>o`<li>${r}</li>`)}</ul></div>`:h}
                  ${this.renderPronunciationManagement()}
                  ${this.renderMeaningEditor(t)}
                </div>
              `:h}
              <div>
                <p class="front-label">How well did you know it?</p>
                <div class="confidence-grid">
                  ${tt.map(([r,a])=>o`
                    <button class="confidence" type="button" ?disabled=${this.isReviewing||!!this.recordingBlob} @click=${()=>void this.submitConfidence(Number(r))}>
                      <span class="confidence-number">${r}</span><span class="confidence-text">${a}</span>
                    </button>
                  `)}
                </div>
              </div>
              ${this.isReviewing?o`<p class="inline-status" role="status">Saving your confidence…</p>`:h}
              ${this.recordingBlob?o`<p class="inline-status">Save or discard the local recording before choosing a confidence.</p>`:h}
            </div>
          `:o`
            <button class="primary reveal-action" type="button" @click=${this.revealCard}>Reveal answer <span class="caption">Space</span></button>
          `}
        </div>
      </div>
    `}renderStudy(){const t=this.decks.find(e=>e.id===this.studyDeckId);return o`
      <main class="study" aria-labelledby="study-title">
        <div class="study-heading">
          <div>
            <button
              class="study-back"
              type="button"
              aria-label=${t?`Back to ${t.name}`:"Back to decks"}
              @click=${this.backFromStudy}
            >← ${t?`Back to ${t.name}`:"Back to decks"}</button>
            <p class="caption">Study</p>
            <h2 id="study-title">${t?t.name:"All due cards"}</h2>
          </div>
          <button type="button" @click=${()=>void this.loadStudyCard()} ?disabled=${this.studyStatus==="loading"}>${this.studyStatus==="loading"?"Loading…":"Refresh"}</button>
        </div>
        ${this.studyStatus==="ready"&&this.studyError?o`<p class="inline-status error" role="alert">${this.studyError}</p>`:h}
        ${this.studyStatus==="loading"?o`<div class="card-stage study-state" role="status">Loading the next due card…</div>`:h}
        ${this.studyStatus==="error"?o`<div class="card-stage study-state"><div><h2>Could not load a card</h2><p class="inline-status error" role="alert">${this.studyError}</p><button class="primary" type="button" @click=${()=>void this.loadStudyCard()}>Try again</button></div></div>`:h}
        ${this.studyStatus==="empty"?o`
          <div class="card-stage study-state" data-study-empty tabindex="-1">
            <div>
              <h2>Study complete</h2>
              <p class="muted">
                ${t?`Nothing else is due in “${t.name}” right now.`:"Nothing else is due right now."}
              </p>
              <div class="study-complete-actions">
                ${t?o`
                  <button class="primary" type="button" @click=${()=>{this.selectedDeckId=t.id,this.importDeckId=t.id,this.view="deck"}}>Back to ${t.name}</button>
                  <button type="button" @click=${()=>{this.selectedDeckId=null,this.view="decks"}}>Decks</button>
                  <button type="button" @click=${()=>void this.loadStudyCard()}>Check again</button>
                  <button type="button" @click=${()=>{this.selectedDeckId=t.id,this.manualDeckId=t.id,this.importDeckId=t.id,this.view="deck"}}>Add vocabulary</button>
                `:o`
                  <button class="primary" type="button" @click=${()=>{this.selectedDeckId=null,this.view="decks"}}>Back to decks</button>
                  <button type="button" @click=${()=>void this.loadStudyCard()}>Check again</button>
                `}
              </div>
            </div>
          </div>
        `:h}
        ${this.studyStatus==="ready"&&this.studyCard?this.renderStudyCard(this.studyCard):h}
      </main>
    `}async loadDictionarySettings(){this.dictionarySettingsStatus="loading",this.dictionaryActionError="";try{const t=await f.getDictionarySettings();this.dictionarySettings=t,this.dictionaryMode=t.mode,this.dictionarySettingsStatus="ready",t.mode==="unconfigured"&&this.view!=="study"&&this.view!=="chooser"&&(this.view="chooser"),t.mode!=="unconfigured"&&this.view==="chooser"&&(this.view="decks")}catch(t){this.dictionarySettingsStatus="error",this.dictionaryActionError=this.messageFor(t,"Could not read the dictionary settings.")}}async useOnline(){this.dictionaryAction="switching-online",this.dictionaryActionMessage="",this.dictionaryActionError="";try{await f.useOnline(),this.dictionaryActionMessage="Now using Online for this session. The canonical Offline dictionary will not be removed.",await this.loadDictionarySettings()}catch(t){this.dictionaryActionError=this.messageFor(t,"Could not switch to Online for this session.")}finally{this.dictionaryAction="idle"}}async useOffline(){this.dictionaryAction="switching-offline",this.dictionaryActionMessage="",this.dictionaryActionError="";try{await f.useOffline(),this.dictionaryActionMessage="Now using Offline for this session.",await this.loadDictionarySettings()}catch(t){this.dictionaryActionError=this.messageFor(t,"Could not switch to Offline for this session.")}finally{this.dictionaryAction="idle"}}async installOffline(){this.dictionaryAction="installing",this.dictionaryActionMessage="",this.dictionaryActionError="";try{const t=await f.installOffline();t.status==="started"?(this.dictionaryActionMessage="Download started. Progress is shown below; the Settings view refreshes automatically.",await this.pollInstallProgress()):this.dictionaryActionMessage=`Installed full Offline dictionary (status: ${t.status}).`,await this.loadDictionarySettings()}catch(t){this.dictionaryActionError=this.messageFor(t,"Could not install the full Offline dictionary.")}finally{this.dictionaryAction="idle"}}async pollInstallProgress(){for(let t=0;t<120;t++){await new Promise(e=>setTimeout(e,1e3));try{const e=await f.getDictionarySettings();this.dictionarySettings=e,this.dictionaryMode=e.mode;const s=e.install_progress;if(!s||s.status==="idle")return;if(s.status==="installed"){this.dictionaryActionMessage="Installed full Offline dictionary.";return}if(s.status==="failed"){this.dictionaryActionError=s.error||"Offline download failed.";return}const i=s.percent.toFixed(1),r=s.downloaded_bytes.toLocaleString(),a=s.total_bytes?s.total_bytes.toLocaleString():"unknown";this.dictionaryActionMessage=`Downloading… ${r} / ${a} bytes (${i}%).`}catch{return}}}async removeOffline(){this.dictionaryAction="removing",this.dictionaryActionMessage="",this.dictionaryActionError="";try{const t=await f.removeOffline();this.dictionaryActionMessage=`Removed Offline dictionary: ${t.detail}`,this.confirmRemoveOffline=!1,await this.loadDictionarySettings()}catch(t){this.dictionaryActionError=this.messageFor(t,"Could not remove the Offline dictionary.")}finally{this.dictionaryAction="idle"}}async clearOnlineCache(){this.dictionaryAction="clearing",this.dictionaryActionMessage="",this.dictionaryActionError="";try{await f.clearOnlineCache(),this.dictionaryActionMessage="Online cache cleared.",await this.loadDictionarySettings()}catch(t){this.dictionaryActionError=this.messageFor(t,"Could not clear the Online cache.")}finally{this.dictionaryAction="idle"}}renderChooser(){return o`
      <main class="panel" aria-labelledby="chooser-title">
        <h2 id="chooser-title">Choose how to use the dictionary</h2>
        <p class="muted">
          No canonical full Offline dictionary is available yet. Pick how this
          process should serve the vocabulary:
        </p>
        <div class="workflow-grid">
          <section class="workflow" aria-labelledby="chooser-online-title">
            <h3 id="chooser-online-title">Use Online</h3>
            <p>Start now without downloading the full dictionary. Online applies
              to the current session only.</p>
            <button class="primary" type="button" @click=${()=>void this.useOnline()} ?disabled=${this.dictionaryAction!=="idle"}>
              ${this.dictionaryAction==="switching-online"?"Switching…":"Use Online"}
            </button>
          </section>
          <section class="workflow" aria-labelledby="chooser-offline-title">
            <h3 id="chooser-offline-title">Download for Offline use</h3>
            <p>Download ~945 MB and work without internet afterward. The
              free-space preflight happens before any download begins.</p>
            <button type="button" @click=${()=>void this.installOffline()} ?disabled=${this.dictionaryAction!=="idle"}>
              ${this.dictionaryAction==="installing"?"Starting install…":"Download for Offline use"}
            </button>
          </section>
        </div>
        ${this.dictionaryActionError?o`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>`:h}
      </main>
    `}renderSettings(){const t=this.dictionarySettings,e=this.dictionaryMode==="unconfigured"&&(t==null?void 0:t.canonical_offline_valid)!==!0;return o`
      <main class="panel" aria-labelledby="settings-title">
        <div class="toolbar"><h2 id="settings-title">Dictionary</h2><button @click=${()=>void this.loadDictionarySettings()} ?disabled=${this.dictionarySettingsStatus==="loading"}>${this.dictionarySettingsStatus==="loading"?"Refreshing…":"Refresh"}</button></div>
        ${this.dictionarySettingsStatus==="error"?o`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>`:h}
        ${e?this.renderChooserInline():h}
        ${t?o`
          <dl class="settings-meta">
            <dt>Mode</dt><dd data-testid="dictionary-mode">${t.mode}</dd>
            <dt>Canonical Offline</dt><dd><code>${t.canonical_offline_path}</code></dd>
            <dt>Present</dt><dd>${t.canonical_offline_present?"yes":"no"}</dd>
            <dt>Valid</dt><dd>${t.canonical_offline_valid?"yes":"no"}</dd>
            ${t.online_info?o`
              <dt>Online dataset token</dt><dd><code>${t.online_info.dataset_token.slice(0,16)}…</code></dd>
            `:h}
            ${t.install_progress&&t.install_progress.status!=="idle"?o`
              <dt>Download progress</dt><dd data-testid="install-progress">
                ${t.install_progress.downloaded_bytes.toLocaleString()} /
                ${t.install_progress.total_bytes?t.install_progress.total_bytes.toLocaleString():"unknown"} bytes
                (${t.install_progress.percent.toFixed(1)}%) — ${t.install_progress.status}
              </dd>
            `:h}
          </dl>
        `:h}
        <div class="workflow-grid">
          <section class="workflow" aria-labelledby="online-action-title">
            <h3 id="online-action-title">Online</h3>
            <p>${(t==null?void 0:t.mode)==="online"?"Online is active for this session.":"Use the trusted Online dictionary for this session only."}</p>
            <button class="primary" type="button" @click=${()=>void this.useOnline()} ?disabled=${this.dictionaryAction!=="idle"||(t==null?void 0:t.mode)==="online"}>
              ${this.dictionaryAction==="switching-online"?"Switching…":"Use Online for this session"}
            </button>
            <button type="button" @click=${()=>void this.clearOnlineCache()} ?disabled=${this.dictionaryAction!=="idle"||!(t!=null&&t.online_active)}>
              ${this.dictionaryAction==="clearing"?"Clearing…":"Clear Online cache"}
            </button>
          </section>
          <section class="workflow" aria-labelledby="offline-action-title">
            <h3 id="offline-action-title">Offline</h3>
            <p>${(t==null?void 0:t.mode)==="offline"?"Offline is active for this session.":"Activate the trusted full Offline dictionary for this session."}</p>
            <button class="primary" type="button" @click=${()=>void this.useOffline()} ?disabled=${this.dictionaryAction!=="idle"||(t==null?void 0:t.mode)==="offline"||!(t!=null&&t.canonical_offline_valid)}>
              ${this.dictionaryAction==="switching-offline"?"Switching…":"Use Offline"}
            </button>
            <button type="button" @click=${()=>void this.installOffline()} ?disabled=${this.dictionaryAction!=="idle"||(t==null?void 0:t.canonical_offline_valid)===!0}>
              ${this.dictionaryAction==="installing"?"Starting install…":"Download for Offline use"}
            </button>
            ${this.confirmRemoveOffline?h:o`
              <button class="danger" type="button" @click=${()=>{this.confirmRemoveOffline=!0}} ?disabled=${this.dictionaryAction!=="idle"}>
                Remove Offline dictionary
              </button>
            `}
            ${this.confirmRemoveOffline?o`
              <div class="confirm" role="alertdialog">
                <p>Remove the canonical Offline dictionary while Online is active? Choose another mode (Online for this session) first if Offline is in use.</p>
                <button class="danger" type="button" @click=${()=>void this.removeOffline()} ?disabled=${this.dictionaryAction!=="idle"}>Confirm remove Offline</button>
                <button type="button" @click=${()=>{this.confirmRemoveOffline=!1}}>Cancel</button>
              </div>
            `:h}
          </section>
        </div>
        ${this.dictionaryActionMessage?o`<p class="inline-status" role="status">${this.dictionaryActionMessage}</p>`:h}
        ${this.dictionaryActionError?o`<p class="inline-status error" role="alert">${this.dictionaryActionError}</p>`:h}
      </main>
    `}renderChooserInline(){return o`
      <section class="panel" aria-labelledby="inline-chooser-title">
        <h3 id="inline-chooser-title">Choose how to use the dictionary</h3>
        <p class="muted">No canonical full Offline dictionary is available. Online applies to this session only.</p>
        <div class="actions">
          <button class="primary" type="button" @click=${()=>void this.useOnline()} ?disabled=${this.dictionaryAction!=="idle"}>
            ${this.dictionaryAction==="switching-online"?"Switching…":"Use Online"}
          </button>
          <button type="button" @click=${()=>void this.installOffline()} ?disabled=${this.dictionaryAction!=="idle"}>
            ${this.dictionaryAction==="installing"?"Starting install…":"Download for Offline use"}
          </button>
        </div>
      </section>
    `}renderDeckDetail(t){const e=this.isOrphanedDeck(t),s=!e;return this.deckTab==="cards"&&this.deckCardsStatus==="idle"&&this.selectedDeckId!==null&&this.loadDeckCards(this.selectedDeckId),o`
      <section class="panel" aria-labelledby="deck-title">
        <div class="deck-heading">
          <div>
            <h2 id="deck-title">${t.name}</h2>
            <p class="muted">${t.card_count} ${t.card_count===1?"card":"cards"} · ${t.due_count} due · ${t.mastery_percent}% mastered</p>
          </div>
          <div class="actions">
            <button class="primary" @click=${()=>void this.openStudy(t.id)}>Study this deck</button>
            <button @click=${()=>{this.selectedDeckId=null,this.view="decks"}}>All decks</button>
          </div>
        </div>
        <div class="actions" aria-label="Deck actions">
          <button @click=${()=>this.openRenameDialog()} ?disabled=${!s||this.renameOpen}>Rename deck</button>
          ${e?o`<span class="muted">Rename is disabled for the protected recovery deck.</span>`:h}
        </div>
        <div class="deck-tabs" role="tablist" aria-label="Deck sections">
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab==="overview"?"true":"false"} @click=${()=>this.setDeckTab("overview")}>Overview</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab==="cards"?"true":"false"} @click=${()=>this.setDeckTab("cards")}>Cards</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab==="add"?"true":"false"} @click=${()=>this.setDeckTab("add")}>Add</button>
          <button class="deck-tab" role="tab" aria-selected=${this.deckTab==="import"?"true":"false"} @click=${()=>this.setDeckTab("import")}>Import & Export</button>
        </div>
        ${this.deckTab==="overview"?this.renderDeckOverview(t):h}
        ${this.deckTab==="cards"?this.renderDeckCards(t):h}
        ${this.deckTab==="add"?this.renderDeckAdd(t):h}
        ${this.deckTab==="import"?this.renderDeckImport(t):h}
      </section>
      ${this.renderEditDialog()}
      ${this.renderMoveDialog()}
      ${this.renderRemoveDialog()}
      ${this.renderRenameDialog()}
      ${this.renderRestoreDialog()}
      ${this.renderSenseDialog()}
    `}renderDeckOverview(t){return o`
      <div>
        <p>Card data and review scheduling remain on the server.</p>
        <p class="muted">${t.card_count} ${t.card_count===1?"card":"cards"} · ${t.due_count} due · ${t.mastery_percent}% mastered</p>
        <div class="actions">
          <button @click=${()=>this.setDeckTab("cards")}>Manage cards</button>
          <button @click=${()=>this.setDeckTab("add")}>Add vocabulary</button>
          <button @click=${()=>this.setDeckTab("import")}>Import &amp; export</button>
        </div>
      </div>
    `}renderDeckAdd(t){return o`
      <div class="workflow-grid">
        ${this.renderCaptureCreation(t)}
        ${this.renderManualCreation()}
      </div>
    `}renderDeckImport(t){return this.renderImportExport(t)}renderDeckCards(t){const e=this.isOrphanedDeck(t);return o`
      <div aria-labelledby="cards-section-title">
        <h3 id="cards-section-title">Cards</h3>
        ${e?o`
          <p class="orphan-banner">
            <strong>Protected recovery deck.</strong>
            ${" "}Notes here are not organised by lesson and cannot be moved out. Use a regular deck for routine study.
          </p>
        `:h}
        ${this.deckCardsStatus==="loading"?o`<p class="loading" role="status">Loading cards…</p>`:h}
        ${this.deckCardsStatus==="error"?o`
          <div class="empty">
            <p>${this.deckCardsError}</p>
            <button @click=${()=>this.selectedDeckId!==null&&void this.loadDeckCards(this.selectedDeckId)}>Try again</button>
          </div>
        `:h}
        ${this.deckCardsStatus==="ready"&&this.deckCards.length===0?o`
          <p class="empty">This deck has no cards yet.</p>
        `:h}
        ${this.deckCardsStatus==="ready"&&this.deckCards.length>0?o`
          <ul class="deck-card-list" aria-label="Cards in this deck">
            ${this.deckCards.map(s=>this.renderDeckCardRow(s,e))}
          </ul>
        `:h}
      </div>
    `}renderDeckCardRow(t,e){const s=[t.pos,t.gender].filter(Boolean).join(" "),i=[];if(t.review_count>0&&i.push(`${t.review_count} review${t.review_count===1?"":"s"}`),t.review_count>0&&t.last_confidence!==null){const a=t.last_confidence.toFixed(1);i.push(`last confidence ${a}`)}else t.last_confidence!==null&&i.push(`confidence ${t.last_confidence.toFixed(1)}`);if(t.due_at){const a=this.formatDue(t.due_at);a&&i.push(`due ${a}`)}i.push(t.selected_languages.length===0?"no display languages":`languages: ${t.selected_languages.map(a=>a.toUpperCase()).join(" + ")}`),t.has_custom_audio&&i.push("custom pronunciation"),t.other_deck_ids.length>0&&i.push(`also in ${t.other_deck_ids.length} other deck${t.other_deck_ids.length===1?"":"s"}`);const r=Ze(t.status,e);return o`
      <li class="deck-card-row" data-note-id=${t.note_id}>
        <div>
          <span class="deck-card-headword">${t.headword}</span>
          ${s?o`<span class="muted"> · ${s}</span>`:h}
          <span class="deck-card-meta">${i.join(" · ")}</span>
        </div>
        <div class="deck-card-actions">
          <button type="button" @click=${()=>this.openEditDialog(t)}>Edit</button>
          ${r?o`<button type="button" @click=${()=>this.openSenseDialog(t)} data-testid=${`edit-sense-${t.note_id}`}>Edit sense</button>`:h}
          <button type="button" @click=${()=>this.openMoveDialog(t)} ?disabled=${e}>Move</button>
          ${e?o`<button type="button" @click=${()=>this.openRestoreDialog(t)} data-testid=${`restore-${t.note_id}`}>Restore to deck</button>`:h}
          <button class="danger" type="button" @click=${()=>this.openRemoveConfirm(t)}>Remove from deck</button>
        </div>
      </li>
    `}formatDue(t){const e=new Date(t);return Number.isNaN(e.getTime())?"":e.toLocaleDateString()}renderEditDialog(){const t=this.editingCard;if(!t)return h;const e=this.editState==="saving-languages"||this.editState==="saving-gloss",s=()=>{const a=[...t.selected_languages].sort(),n=[...this.editLanguages].sort();return a.length!==n.length||a.some((p,u)=>p!==n[u])},i=a=>{var u;const n=this.editGlossDrafts[a].trim(),p=((u=t.user_meanings[a])==null?void 0:u.trim())??"";return n!==p},r=s()||i("de")||i("en");return o`
      <div class="dialog-backdrop" @click=${a=>{a.target===a.currentTarget&&this.closeEditDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="edit-card-title" data-edit-dialog tabindex="-1">
          <h2 id="edit-card-title">Edit card</h2>
          <p class="muted">Headword (read-only): <strong>${t.headword}</strong>${t.pos?` · ${t.pos}`:""}${t.gender?` · ${t.gender}`:""}</p>
          <fieldset class="selection">
            <legend>Meaning languages</legend>
            <p class="muted">Choose German, English, or both. At least one language stays selected.</p>
            <label class="choice">
              <input
                type="checkbox"
                .checked=${this.editLanguages.includes("de")}
                @change=${a=>this.toggleEditMeaningLanguage("de",a.target)}
                ?disabled=${e}
              />
              German (DE)
            </label>
            <label class="choice">
              <input
                type="checkbox"
                .checked=${this.editLanguages.includes("en")}
                @change=${a=>this.toggleEditMeaningLanguage("en",a.target)}
                ?disabled=${e}
              />
              English (EN)
            </label>
          </fieldset>
          <section aria-labelledby="edit-gloss-title">
            <h3 id="edit-gloss-title">Your meanings</h3>
            <p class="muted">Optional overrides for this note. Saved values appear in study; cleared values return to the card's available meaning.</p>
            ${["de","en"].map(a=>{var u;const n=a==="de"?"German":"English",p=((u=t.user_meanings[a])==null?void 0:u.trim())??"";return o`
                <div class="edit-gloss-row">
                  <label>Your ${n} meaning
                    <input
                      .value=${this.editGlossDrafts[a]}
                      @input=${m=>{this.editGlossDrafts={...this.editGlossDrafts,[a]:m.target.value}}}
                      ?disabled=${this.editGlossBusy[a]}
                      autocomplete="off"
                    />
                  </label>
                  <div class="actions">
                    <button type="button" @click=${()=>void this.saveEditGloss(a)} ?disabled=${this.editGlossBusy[a]||!this.editGlossDrafts[a].trim()||this.editGlossDrafts[a].trim()===p}>${this.editGlossBusy[a]?"Saving…":"Save meaning"}</button>
                    <button class="danger" type="button" @click=${()=>void this.deleteEditGloss(a)} ?disabled=${this.editGlossBusy[a]||!p}>Remove meaning</button>
                  </div>
                </div>
              `})}
          </section>
          ${this.renderManagementPronunciation(t)}
          ${this.editError?o`<p class="inline-status error" role="alert">${this.editError}</p>`:h}
          ${this.editState==="saved"?o`<p class="inline-status" role="status">Changes saved.</p>`:h}
          <div class="actions">
            <button type="button" @click=${()=>this.closeEditDialog()} ?disabled=${e}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.commitEditDialog()} ?disabled=${e||!r}>${e?"Saving…":"Save changes"}</button>
          </div>
        </div>
      </div>
    `}renderManagementPronunciation(t){const e=this.mgmtRecordingStatus==="save-error",s=t.has_custom_audio;return o`
      <section class="pronunciation" aria-labelledby="mgmt-pronunciation-title">
        <h3 id="mgmt-pronunciation-title">Custom pronunciation</h3>
        <div class="audio-actions">
          <button type="button" @click=${()=>void this.playManagementPronunciation()} ?disabled=${this.mgmtAudioStatus==="loading"}>
            ${this.mgmtAudioStatus==="loading"?"Loading pronunciation…":this.mgmtAudioStatus==="playing"?"Playing pronunciation…":"Play pronunciation"}
          </button>
          ${s?o`
            <button type="button" @click=${()=>{this.mgmtShowRecordingControls=!this.mgmtShowRecordingControls,this.mgmtRevertConfirmation=!1}}>
              ${this.mgmtShowRecordingControls?"Keep current pronunciation":"Replace pronunciation"}
            </button>
            ${this.mgmtRevertConfirmation?o`
              <span class="caption">Replace your custom pronunciation with automatic pronunciation?</span>
              <button class="danger" type="button" @click=${()=>void this.revertManagementCustomAudio()}>Confirm revert to automatic</button>
              <button type="button" @click=${()=>{this.mgmtRevertConfirmation=!1}}>Cancel</button>
            `:o`<button class="danger" type="button" @click=${()=>{this.mgmtRevertConfirmation=!0,this.mgmtShowRecordingControls=!1}}>Revert to automatic</button>`}
          `:o`<button type="button" @click=${()=>{this.mgmtShowRecordingControls=!this.mgmtShowRecordingControls}}>Add your pronunciation</button>`}
        </div>
        ${this.mgmtAudioMessage?o`<p class="inline-status ${this.mgmtAudioStatus==="unavailable"?"error":""}" role=${this.mgmtAudioStatus==="unavailable"?"alert":"status"}>${this.mgmtAudioMessage}</p>`:h}
        ${this.mgmtShowRecordingControls?o`
          <div class="local-take">
            <p class="muted">Record a take or choose an audio file. It stays only in this browser until you save it.</p>
            ${this.mgmtRecordingBlob?o`
              <p class="inline-status">Local recording ready to preview and save.</p>
              <audio class="audio-preview" controls src=${this.mgmtRecordingPreviewUrl}></audio>
            `:h}
            ${e?o`
              <p class="inline-status error" role="alert">${this.mgmtRecordingError}</p>
              <div class="recording-actions">
                <button class="primary" type="button" @click=${()=>void this.saveManagementRecording()}>Try again</button>
                <button class="danger" type="button" @click=${()=>this.discardManagementRecording()}>Discard recording</button>
              </div>
            `:o`
              <div class="recording-actions">
                ${this.mgmtRecordingStatus==="recording"?o`<button class="danger" type="button" @click=${()=>this.stopManagementRecording()}>Stop recording</button>`:o`<button type="button" @click=${()=>void this.startManagementRecording()} ?disabled=${this.mgmtRecordingStatus==="saving"}>Record pronunciation</button>`}
                <label>Choose audio file
                  <input type="file" accept="audio/*" @change=${this.selectManagementAudioFile} ?disabled=${this.mgmtRecordingStatus==="recording"||this.mgmtRecordingStatus==="saving"} />
                </label>
                ${this.mgmtRecordingBlob?o`
                  <button class="primary" type="button" @click=${()=>void this.saveManagementRecording()} ?disabled=${this.mgmtRecordingStatus==="saving"}>${this.mgmtRecordingStatus==="saving"?"Saving pronunciation…":"Save recording"}</button>
                  <button class="danger" type="button" @click=${()=>this.discardManagementRecording()} ?disabled=${this.mgmtRecordingStatus==="saving"}>Discard recording</button>
                `:h}
              </div>
              ${this.mgmtRecordingError?o`<p class="inline-status error" role="alert">${this.mgmtRecordingError}</p>`:h}
            `}
          </div>
        `:h}
      </section>
    `}renderMoveDialog(){const t=this.moveTarget;if(!t)return h;const e=this.moveDestinations(),s=this.moveState==="saving";return o`
      <div class="dialog-backdrop" @click=${i=>{i.target===i.currentTarget&&this.closeMoveDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="move-title" data-move-dialog tabindex="-1">
          <h2 id="move-title">Move to another deck</h2>
          <p>Move “<strong>${t.headword}</strong>” from this deck to one of your other decks.</p>
          <label>Destination deck
            <select
              .value=${this.moveDestinationDeckId===null?"":String(this.moveDestinationDeckId)}
              @change=${i=>{const r=i.target.value;this.moveDestinationDeckId=r?Number(r):null}}
              ?disabled=${s||e.length===0}
            >
              <option value="">Select a deck</option>
              ${e.map(i=>o`<option value=${i.id}>${i.name}</option>`)}
            </select>
          </label>
          ${e.length===0?o`<p class="muted">Create another deck first; there is nowhere else to move this card.</p>`:h}
          ${this.moveError?o`<p class="inline-status error" role="alert">${this.moveError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${()=>this.closeMoveDialog()} ?disabled=${s}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.performMove()} ?disabled=${s||this.moveDestinationDeckId===null}>${s?"Moving…":"Move"}</button>
          </div>
        </div>
      </div>
    `}renderRemoveDialog(){const t=this.removeTarget;if(!t)return h;const e=this.removeState==="saving";return o`
      <div class="dialog-backdrop" @click=${s=>{s.target===s.currentTarget&&this.closeRemoveConfirm()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="remove-title" data-remove-dialog tabindex="-1">
          <h2 id="remove-title">Remove “${t.headword}” from this deck?</h2>
          <p>Its study history and saved vocabulary data are preserved.</p>
          <div class="actions">
            <button type="button" @click=${()=>this.closeRemoveConfirm()} ?disabled=${e}>Cancel</button>
            <button class="danger" type="button" @click=${()=>void this.performRemove()} ?disabled=${e}>${e?"Removing…":"Remove from deck"}</button>
          </div>
        </div>
      </div>
    `}renderRenameDialog(){if(!this.renameOpen)return h;const t=this.renameState==="saving";return o`
      <div class="dialog-backdrop" @click=${e=>{e.target===e.currentTarget&&this.closeRenameDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="rename-title" data-rename-dialog tabindex="-1">
          <h2 id="rename-title">Rename deck</h2>
          <label>New deck name
            <input
              .value=${this.renameDraft}
              @input=${e=>{this.renameDraft=e.target.value,this.renameError=""}}
              ?disabled=${t}
              maxlength="200"
              autocomplete="off"
            />
          </label>
          ${this.renameError?o`<p class="inline-status error" role="alert">${this.renameError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${()=>this.closeRenameDialog()} ?disabled=${t}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.performRename()} ?disabled=${t}>${t?"Renaming…":"Rename deck"}</button>
          </div>
        </div>
      </div>
    `}renderRestoreDialog(){const t=this.restoreTarget;if(!t)return h;const e=this.restoreDestinations(),s=this.restoreState==="saving";return o`
      <div class="dialog-backdrop" @click=${i=>{i.target===i.currentTarget&&this.closeRestoreDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="restore-title" data-restore-dialog tabindex="-1">
          <h2 id="restore-title">Restore to deck</h2>
          <p>Restore “<strong>${t.headword}</strong>” from the protected Orphaned deck into one of your normal decks. Review history and saved vocabulary data are preserved.</p>
          <label>Destination deck
            <select
              .value=${this.restoreDestinationDeckId===null?"":String(this.restoreDestinationDeckId)}
              @change=${i=>{const r=i.target.value;this.restoreDestinationDeckId=r?Number(r):null}}
              ?disabled=${s}
            >
              <option value="">Select a deck</option>
              ${e.map(i=>o`<option value=${i.id}>${i.name}</option>`)}
            </select>
          </label>
          ${e.length===0?o`<p class="muted">Create a normal deck first.</p>`:h}
          ${this.restoreError?o`<p class="inline-status error" role="alert">${this.restoreError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${()=>this.closeRestoreDialog()} ?disabled=${s}>Cancel</button>
            <button class="primary" type="button" @click=${()=>void this.performRestore()} ?disabled=${s||this.restoreDestinationDeckId===null||e.length===0}>${s?"Restoring…":"Restore"}</button>
          </div>
        </div>
      </div>
    `}renderSenseDialog(){var r;const t=this.senseTarget;if(!t)return h;const e=this.senseState==="saving",s=this.senseCandidates[0],i=(r=s==null?void 0:s.senses)!=null&&r.length?s.senses:[];return o`
      <div class="dialog-backdrop" @click=${a=>{a.target===a.currentTarget&&this.closeSenseDialog()}}>
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="sense-title" data-sense-dialog tabindex="-1">
          <h2 id="sense-title">Change selected dictionary sense</h2>
          <p class="muted">
            Headword (read-only): <strong>${t.headword}</strong>
            ${t.pos?` · ${t.pos}`:""}${t.gender?` · ${t.gender}`:""}
          </p>
          <p>Choose a different dictionary meaning for “<strong>${t.headword}</strong>”.</p>
          <p class="muted">Learning history, your meanings, and custom pronunciation stay with this card. Dictionary meaning/grammar/examples may change.</p>
          ${this.senseLookupStatus==="loading"?o`<p class="result" role="status">Looking up the active dictionary…</p>`:h}
          ${this.senseLookupStatus==="ready"&&i.length===0?o`<p class="result">This word has no selectable direct senses in the active dictionary.</p>`:h}
          ${i.length>0?o`
            <fieldset class="selection">
              <legend>Dictionary meaning</legend>
              <ul class="choice-list">
                ${i.map(a=>o`
                  <li>
                    <label class="choice">
                      <input
                        type="radio"
                        name="sense"
                        .value=${a.sense_semantic_ref}
                        .checked=${this.senseSelectedRef===a.sense_semantic_ref}
                        @change=${()=>{this.senseSelectedRef=a.sense_semantic_ref}}
                      />
                      <span>${he(a)}</span>
                    </label>
                  </li>
                `)}
              </ul>
            </fieldset>
          `:h}
          ${this.senseError?o`<p class="inline-status error" role="alert">${this.senseError}</p>`:h}
          <div class="actions">
            <button type="button" @click=${()=>this.closeSenseDialog()} ?disabled=${e}>Cancel</button>
            <button
              class="primary"
              type="button"
              data-testid=${`confirm-sense-${t.note_id}`}
              @click=${()=>void this.performSenseChange()}
              ?disabled=${e||this.senseLookupStatus!=="ready"||!this.senseSelectedRef}
            >${e?"Saving…":"Change sense"}</button>
          </div>
        </div>
      </div>
    `}render(){const t=this.selectedDeck(),e=this.deckStatus!=="ready"||!t;return o`
      <div class="shell">
        <header>
          <div>
            <h1>Wortlaut</h1>
            <div class="subtitle">German vocabulary</div>
          </div>
          <nav class="primary-nav" aria-label="Main navigation">
            <button type="button" aria-current=${this.view==="study"?"false":"page"} @click=${()=>{this.view="decks",this.selectedDeckId=null}}>Decks</button>
            <button type="button" aria-current=${this.view==="study"?"page":"false"} @click=${()=>void this.openStudy()}>Study due</button>
            <button type="button" aria-current=${this.view==="settings"||this.view==="chooser"?"page":"false"} @click=${()=>{this.view="settings",this.loadDictionarySettings()}}>Settings</button>
            <button type="button" @click=${this.loadDecks} ?disabled=${this.deckStatus==="loading"}>${this.deckStatus==="loading"?"Refreshing…":"Refresh decks"}</button>
          </nav>
        </header>
        ${this.renderNotices()}
        ${this.view==="chooser"?this.renderChooser():this.view==="settings"?this.renderSettings():this.view==="study"?this.renderStudy():e?o`
          <main class="panel">
            <div class="toolbar">
              <h2>Your decks</h2>
              <div class="actions">
                <span class="muted" aria-live="polite">${this.deckStatus==="ready"?"Server-synced":""}</span>
                <button type="button" @click=${this.openCreateFolderDialog}>New folder</button>
              </div>
            </div>
            <form class="form-row" @submit=${this.createDeck}>
              <label>New deck name
                <input .value=${this.newDeckName} @input=${s=>{this.newDeckName=s.target.value}} ?disabled=${this.isCreating} maxlength="200" autocomplete="off" />
              </label>
              <button class="primary" type="submit" ?disabled=${this.isCreating}>${this.isCreating?"Creating…":"Create deck"}</button>
            </form>
            ${this.renderDeckList()}
          </main>
        `:this.renderDeckDetail(t)}
        ${this.renderCreateFolderDialog()}
        ${this.renderRenameFolderDialog()}
        ${this.renderDeleteFolderDialog()}
        ${this.renderMoveDeckDialog()}
      </div>
      <nav class="bottom-nav" aria-label="Main navigation">
        <button type="button" aria-current=${this.view==="study"?"false":"page"} @click=${()=>{this.view="decks",this.selectedDeckId=null}}>Decks</button>
        <button type="button" aria-current=${this.view==="study"?"page":"false"} @click=${()=>void this.openStudy()}>Study due</button>
      </nav>
    `}};d.styles=$e`
    :host { display: block; min-height: 100vh; color: var(--fg); background: var(--bg); font-family: var(--font-sans); }
    .shell { max-width: 1280px; margin: 0 auto; padding: var(--space-48) var(--space-16); }
    header { display: flex; align-items: end; justify-content: space-between; gap: var(--space-16); margin-bottom: var(--space-32); }
    h1, h2, h3, p { margin-top: 0; }
    h1, h2, h3 { font-family: var(--font-display); font-weight: 600; letter-spacing: -.02em; }
    h1 { margin-bottom: var(--space-4); font-size: clamp(2rem, 5vw, 3.25rem); }
    h2 { margin-bottom: var(--space-8); font-size: 1.75rem; }
    .subtitle, .muted, .result, .caption { color: var(--muted); }
    .caption { font-family: var(--font-mono); font-size: .75rem; letter-spacing: .04em; text-transform: uppercase; }
    .panel { padding: var(--space-32); border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); box-shadow: var(--shadow-sm); }
    .toolbar, .deck-heading, .form-row, .actions { display: flex; gap: var(--space-12); align-items: center; }
    .toolbar, .deck-heading { justify-content: space-between; }
    .form-row { margin: var(--space-24) 0; align-items: end; }
    label { display: grid; gap: var(--space-4); flex: 1; font-size: .875rem; font-weight: 600; }
    input, select, textarea { width: 100%; padding: 10px var(--space-12); color: var(--fg); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-control); font: inherit; }
    textarea { min-height: 8rem; resize: vertical; }
    button { min-height: 2.6rem; padding: var(--space-8) var(--space-16); color: var(--fg); border: 1px solid var(--border); border-radius: var(--radius-control); background: var(--surface); cursor: pointer; font: inherit; font-weight: 600; }
    button:hover:not(:disabled) { border-color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 3px solid color-mix(in oklch, var(--accent), white 65%); outline-offset: 2px; }
    button.primary { color: white; border-color: var(--accent); background: var(--accent); }
    button.primary:hover:not(:disabled) { filter: brightness(.94); }
    button.danger { color: var(--danger); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    .notice, .capture-state { margin-bottom: var(--space-16); padding: var(--space-12); border: 1px solid var(--border); border-radius: var(--radius-control); overflow-wrap: anywhere; }
    .notice.error, .capture-state.error { color: var(--danger); background: color-mix(in oklch, var(--danger), white 94%); }
    .notice.success { color: var(--success); background: color-mix(in oklch, var(--success), white 94%); }
    .capture-state.warning { border-color: var(--warning); background: color-mix(in oklch, var(--warning), white 91%); }
    .capture-state p { margin-bottom: var(--space-8); }
    .capture-state p:last-child { margin-bottom: 0; }
    .deck-list { display: grid; gap: var(--space-12); padding: 0; margin: var(--space-24) 0 0; list-style: none; }
    .deck { display: grid; grid-template-columns: 1fr auto; gap: var(--space-16); align-items: center; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); }
    .deck-row-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); align-items: center; justify-content: flex-end; }
    .folder-group { margin-top: var(--space-24); }
    .folder-heading-row { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-8); margin-bottom: var(--space-8); padding-bottom: var(--space-8); border-bottom: 1px solid var(--border); }
    .folder-heading { margin: 0; font-size: 1.05rem; overflow-wrap: anywhere; }
    .folder-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .folder-empty { margin: var(--space-8) 0 0; }
    .deck-open { min-height: 0; padding: 0; border: 0; background: transparent; text-align: left; }
    .deck-open:hover:not(:disabled) { background: transparent; text-decoration: underline; }
    .deck-name { display: block; font-family: var(--font-display); font-size: 1.2rem; font-weight: 600; }
    .deck-stats { display: block; margin-top: var(--space-4); color: var(--muted); font-family: var(--font-mono); font-size: .75rem; }
    .empty, .loading { padding: var(--space-48) 0; text-align: center; color: var(--muted); }
    .confirm { border-color: var(--warning); background: color-mix(in oklch, var(--warning), white 92%); }
    .workflow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr)); gap: var(--space-32); margin-top: var(--space-32); }
    .workflow { padding-top: var(--space-24); border-top: 1px solid var(--border); }
    .capture-workflow { grid-column: 1 / -1; }
    .workflow h3 { margin: 0 0 var(--space-8); font-size: 1.4rem; }
    .workflow form { display: grid; gap: var(--space-12); }
    .choice-list, .candidate-list { display: grid; gap: var(--space-8); margin: 0; padding: 0; list-style: none; }
    .choice, .candidate-choice { display: flex; align-items: center; gap: var(--space-8); font-weight: 500; }
    .choice input, .candidate-choice input { width: auto; }
    .candidate { width: 100%; min-height: 0; text-align: left; }
    .candidate.selected { border-color: var(--accent); background: color-mix(in oklch, var(--accent), white 94%); }
    .candidate small { display: block; margin-top: var(--space-4); color: var(--muted); }
    .candidate-header { display: flex; align-items: baseline; gap: var(--space-8); flex-wrap: wrap; }
    .candidate-headword { font-family: var(--font-display); font-size: 1.15rem; font-weight: 600; }
    .candidate-gloss { margin-top: var(--space-4); font-size: 0.95rem; color: var(--fg); font-weight: 500; }
    .save-success-banner { margin-bottom: var(--space-16); padding: var(--space-16); border: 1px solid var(--success); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--success), white 94%); display: grid; gap: var(--space-12); }
    .save-success-banner p { margin: 0; font-weight: 600; color: var(--success); }
    .save-success-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .primary-meaning { font-size: 1.4rem; font-weight: 600; }
    .compact-grammar { margin: 0; font-size: 0.95rem; color: var(--muted); }
    .study-complete-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); justify-content: center; margin-top: var(--space-16); }
    .front-audio { margin-bottom: var(--space-8); }
    .selection { margin: 0; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); }
    .selection legend { padding: 0 var(--space-4); font-family: var(--font-display); font-weight: 600; }
    .selection-preview { margin: 0; padding: var(--space-8) var(--space-12); border-left: 3px solid var(--accent); color: var(--muted); }
    .capture-picker { margin-top: var(--space-24); }
    .capture-candidate { padding: var(--space-12); border: 1px solid var(--border); border-radius: var(--radius-control); }
    .capture-candidate.chosen { border-color: var(--accent); }
    .lemma { font-family: var(--font-display); font-size: 1.25rem; }
    .sense-choices { display: grid; gap: var(--space-8); margin: var(--space-12) 0 0 var(--space-24); border: 0; padding: 0; }
    .sense-choices legend { margin-bottom: var(--space-4); color: var(--muted); font-size: .8rem; }
    .optional-meanings { border: 1px solid var(--border); border-radius: var(--radius-control); padding: var(--space-8) var(--space-12); }
    .optional-meanings > summary { cursor: pointer; font-size: .875rem; font-weight: 600; color: var(--muted); }
    .optional-meanings[open] > summary { margin-bottom: var(--space-8); }
    .optional-meanings label { margin-top: var(--space-8); }
    .import-busy { display: grid; gap: var(--space-8); align-items: center; }
    .import-busy p { margin: 0; color: var(--muted); }
    .import-progress { width: 100%; height: .6rem; }
    .create-actions { flex-wrap: wrap; }
    .disabled-explanation { margin: 0; color: var(--muted); font-size: .875rem; }
    .primary-nav, .bottom-nav { display: flex; gap: var(--space-8); }
    .primary-nav button[aria-current="page"], .bottom-nav button[aria-current="page"] { color: white; border-color: var(--accent); background: var(--accent); }
    .study { max-width: 760px; margin: 0 auto; }
    .study-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--space-12) var(--space-16); margin-bottom: var(--space-16); }
    .study-heading h2 { margin: 0; }
    .study-back { min-height: 0; margin-bottom: var(--space-4); padding: 0; border: 0; background: transparent; color: var(--muted); font-size: .875rem; overflow-wrap: anywhere; }
    .study-back:hover:not(:disabled) { background: transparent; text-decoration: underline; }
    .card-stage { min-height: 25rem; display: grid; align-content: center; gap: var(--space-24); padding: clamp(var(--space-24), 7vw, var(--space-72)); border: 1px solid var(--border); border-radius: var(--radius-dialog); background: var(--surface); box-shadow: var(--shadow-sm); }
    .card-stage:focus { outline: none; }
    .card-stage:focus-visible { outline: 3px solid color-mix(in oklch, var(--accent), white 65%); outline-offset: 3px; }
    .card-side { display: grid; gap: var(--space-16); }
    .front-label, .meaning-label { color: var(--muted); font-family: var(--font-mono); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }
    .study-lemma { margin: 0; font-family: var(--font-display); font-size: clamp(3rem, 10vw, 6rem); font-weight: 600; line-height: .98; letter-spacing: -.045em; overflow-wrap: anywhere; }
    .study-meta { margin: 0; color: var(--muted); font-family: var(--font-mono); font-size: .82rem; }
    .reveal-action { justify-self: start; }
    .answer-rule { border: 0; border-top: 1px solid var(--border); width: 100%; margin: var(--space-8) 0; }
    .meaning { margin: 0; font-size: 1.25rem; }
    .example { margin: 0; padding-left: var(--space-16); border-left: 3px solid var(--accent); font-size: 1.05rem; }
    .example-translation { display: block; margin-top: var(--space-4); color: var(--muted); font-size: .9rem; }
    .pronunciation-simple { display: grid; gap: var(--space-8); }
    .extra-info-row { display: flex; flex-wrap: wrap; gap: var(--space-12); align-items: center; }
    .always-extra-toggle { display: flex; flex-direction: row; align-items: center; gap: var(--space-8); font-size: .875rem; font-weight: 500; }
    .always-extra-toggle input { width: auto; }
    .extra-info { display: grid; gap: var(--space-16); border-top: 1px solid var(--border); padding-top: var(--space-16); }
    .detail-block { margin-top: 0; }
    .detail-block p, .detail-block ul { margin-bottom: 0; }
    .detail-block ul { padding-left: var(--space-24); }
    .confidence-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: var(--space-8); }
    .confidence { min-height: 5rem; display: grid; align-content: center; justify-items: start; gap: var(--space-4); border-top: 4px solid var(--border); text-align: left; }
    .confidence:nth-child(1) { border-top-color: var(--danger); }
    .confidence:nth-child(2) { border-top-color: var(--warning); }
    .confidence:nth-child(3) { border-top-color: oklch(65% .1 95); }
    .confidence:nth-child(4) { border-top-color: oklch(62% .12 155); }
    .confidence:nth-child(5) { border-top-color: var(--accent); }
    .confidence-number { font-family: var(--font-mono); font-size: 1.1rem; }
    .confidence-text { font-size: .75rem; line-height: 1.15; }
    .study-state { min-height: 25rem; display: grid; place-content: center; text-align: center; }
    .study-state h2 { margin-bottom: var(--space-8); }
    .inline-status { margin: 0; color: var(--muted); }
    .inline-status.error { color: var(--danger); }
    .edit-meanings, .pronunciation { padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--bg), white 45%); }
    .edit-meanings h3, .pronunciation h3 { margin-bottom: var(--space-8); font-size: 1.25rem; }
    .gloss-row { display: grid; grid-template-columns: 1fr auto auto; gap: var(--space-8); align-items: end; margin-top: var(--space-12); }
    .audio-actions, .recording-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); }
    .audio-preview { width: 100%; margin-top: var(--space-12); }
    .local-take { margin-top: var(--space-12); padding: var(--space-12); border: 1px dashed var(--accent); border-radius: var(--radius-control); }
    .deck-tabs { display: flex; flex-wrap: wrap; gap: var(--space-8); margin: var(--space-24) 0 var(--space-16); padding: 0; border-bottom: 1px solid var(--border); }
    .deck-tab { min-height: 0; padding: var(--space-8) var(--space-12); border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--muted); font-weight: 600; cursor: pointer; }
    .deck-tab[aria-selected="true"] { color: var(--fg); border-bottom-color: var(--accent); background: transparent; }
    .deck-tab:hover:not(:disabled) { color: var(--fg); }
    .deck-card-list { display: grid; gap: var(--space-12); margin: var(--space-16) 0 0; padding: 0; list-style: none; }
    .deck-card-row { display: grid; grid-template-columns: 1fr auto; gap: var(--space-12) var(--space-16); align-items: center; padding: var(--space-16); border: 1px solid var(--border); border-radius: var(--radius-panel); background: var(--surface); }
    .deck-card-headword { display: block; font-family: var(--font-display); font-size: 1.15rem; font-weight: 600; }
    .deck-card-meta { display: block; margin-top: var(--space-4); color: var(--muted); font-family: var(--font-mono); font-size: .75rem; letter-spacing: .03em; }
    .deck-card-actions { display: flex; flex-wrap: wrap; gap: var(--space-8); align-items: center; justify-content: flex-end; }
    .orphan-banner { padding: var(--space-16); border: 1px solid var(--warning); border-radius: var(--radius-panel); background: color-mix(in oklch, var(--warning), white 92%); color: var(--fg); }
    .dialog-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: var(--space-16); background: color-mix(in oklch, var(--fg), transparent 75%); overflow-y: auto; }
    .dialog { box-sizing: border-box; width: min(40rem, 100%); padding: clamp(var(--space-16), 4vw, var(--space-32)); border: 1px solid var(--border); border-radius: var(--radius-dialog); background: var(--surface); box-shadow: var(--shadow-sm); display: grid; gap: var(--space-16); max-height: calc(100vh - var(--space-32)); overflow-y: auto; }
    .dialog h2 { margin: 0; }
    .dialog .actions { justify-content: flex-end; flex-wrap: wrap; }
    .edit-gloss-row { display: grid; grid-template-columns: 1fr auto; gap: var(--space-8); align-items: end; margin-top: var(--space-8); }
    .edit-gloss-row label { grid-column: 1 / -1; }
    .edit-gloss-row button { min-height: 2.6rem; }
    .bottom-nav { display: none; }
    @media (max-width: 800px) {
      .shell { padding: var(--space-24) var(--space-16) calc(var(--space-72) + var(--space-24)); }
      header, .form-row, .deck-heading { align-items: stretch; flex-direction: column; }
      header { align-items: flex-start; }
      header > .primary-nav { display: none; }
      .toolbar { flex-wrap: wrap; }
      .deck { grid-template-columns: 1fr; }
      .deck-row-actions { justify-content: flex-start; }
      .folder-heading-row { align-items: flex-start; flex-direction: column; }
      .workflow-grid { grid-template-columns: 1fr; }
      .card-stage { min-height: 20rem; padding: var(--space-24); }
      .confidence-grid { grid-template-columns: 1fr; }
      .confidence { min-height: 3.6rem; grid-template-columns: 2rem 1fr; align-items: center; justify-items: start; }
      .confidence-text { font-size: .9rem; }
      .gloss-row { grid-template-columns: 1fr auto; }
      .gloss-row label { grid-column: 1 / -1; }
      .bottom-nav { position: fixed; z-index: 10; right: 0; bottom: 0; left: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 0; padding: var(--space-8) var(--space-16) calc(var(--space-8) + env(safe-area-inset-bottom)); border-top: 1px solid var(--border); background: color-mix(in oklch, var(--surface), white 12%); box-shadow: 0 -8px 24px oklch(20% .02 240 / 6%); }
      .bottom-nav button { min-height: 3rem; border: 0; background: transparent; }
      .deck-card-row { grid-template-columns: 1fr; }
      .deck-card-actions { justify-content: flex-start; }
      .edit-gloss-row { grid-template-columns: 1fr; }
      .edit-gloss-row button { width: 100%; }
      .dialog { padding: var(--space-16); }
    }
  `;l([c()],d.prototype,"decks",2);l([c()],d.prototype,"deckStatus",2);l([c()],d.prototype,"errorMessage",2);l([c()],d.prototype,"successMessage",2);l([c()],d.prototype,"newDeckName",2);l([c()],d.prototype,"selectedDeckId",2);l([c()],d.prototype,"pendingDeleteDeckId",2);l([c()],d.prototype,"isCreating",2);l([c()],d.prototype,"isDeleting",2);l([c()],d.prototype,"lookupQuery",2);l([c()],d.prototype,"lookupStatus",2);l([c()],d.prototype,"lookupCandidates",2);l([c()],d.prototype,"lookupAssetToken",2);l([c()],d.prototype,"selectedCandidate",2);l([c()],d.prototype,"selectedSenseRef",2);l([c()],d.prototype,"selectedMeaningLanguages",2);l([c()],d.prototype,"userMeaningDe",2);l([c()],d.prototype,"userMeaningEn",2);l([c()],d.prototype,"manualDeckId",2);l([c()],d.prototype,"lastSavedNote",2);l([c()],d.prototype,"isSavingNote",2);l([c()],d.prototype,"importDeckId",2);l([c()],d.prototype,"importText",2);l([c()],d.prototype,"importFileName",2);l([c()],d.prototype,"isReadingImportFile",2);l([c()],d.prototype,"isImporting",2);l([c()],d.prototype,"exportingFormat",2);l([c()],d.prototype,"captureSentence",2);l([c()],d.prototype,"captureLessonLabel",2);l([c()],d.prototype,"captureSpanStart",2);l([c()],d.prototype,"captureSpanEnd",2);l([c()],d.prototype,"captureStatus",2);l([c()],d.prototype,"captureCandidates",2);l([c()],d.prototype,"captureAssetToken",2);l([c()],d.prototype,"captureContext",2);l([c()],d.prototype,"captureSelections",2);l([c()],d.prototype,"captureMeaningLanguages",2);l([c()],d.prototype,"captureUserMeaningDe",2);l([c()],d.prototype,"captureUserMeaningEn",2);l([c()],d.prototype,"captureDeckId",2);l([c()],d.prototype,"captureError",2);l([c()],d.prototype,"captureDictionaryChanged",2);l([c()],d.prototype,"isCapturing",2);l([c()],d.prototype,"view",2);l([c()],d.prototype,"studyDeckId",2);l([c()],d.prototype,"studyStatus",2);l([c()],d.prototype,"studyCard",2);l([c()],d.prototype,"isRevealed",2);l([c()],d.prototype,"isReviewing",2);l([c()],d.prototype,"studyError",2);l([c()],d.prototype,"extraInfoOpen",2);l([c()],d.prototype,"alwaysShowExtraInfo",2);l([c()],d.prototype,"glossDrafts",2);l([c()],d.prototype,"glossState",2);l([c()],d.prototype,"glossError",2);l([c()],d.prototype,"glossSavingLanguage",2);l([c()],d.prototype,"audioStatus",2);l([c()],d.prototype,"audioMessage",2);l([c()],d.prototype,"recordingStatus",2);l([c()],d.prototype,"recordingBlob",2);l([c()],d.prototype,"recordingNoteId",2);l([c()],d.prototype,"recordingPreviewUrl",2);l([c()],d.prototype,"recordingError",2);l([c()],d.prototype,"showRecordingControls",2);l([c()],d.prototype,"revertConfirmation",2);l([c()],d.prototype,"hasCustomAudio",2);l([c()],d.prototype,"dictionaryMode",2);l([c()],d.prototype,"dictionarySettings",2);l([c()],d.prototype,"dictionarySettingsStatus",2);l([c()],d.prototype,"dictionaryAction",2);l([c()],d.prototype,"dictionaryActionMessage",2);l([c()],d.prototype,"dictionaryActionError",2);l([c()],d.prototype,"confirmRemoveOffline",2);l([c()],d.prototype,"deckTab",2);l([c()],d.prototype,"deckCards",2);l([c()],d.prototype,"deckCardsStatus",2);l([c()],d.prototype,"deckCardsError",2);l([c()],d.prototype,"editingCard",2);l([c()],d.prototype,"editLanguages",2);l([c()],d.prototype,"editGlossDrafts",2);l([c()],d.prototype,"editGlossBusy",2);l([c()],d.prototype,"editState",2);l([c()],d.prototype,"editError",2);l([c()],d.prototype,"moveTarget",2);l([c()],d.prototype,"moveDestinationDeckId",2);l([c()],d.prototype,"moveState",2);l([c()],d.prototype,"moveError",2);l([c()],d.prototype,"removeTarget",2);l([c()],d.prototype,"removeState",2);l([c()],d.prototype,"restoreTarget",2);l([c()],d.prototype,"restoreDestinationDeckId",2);l([c()],d.prototype,"restoreState",2);l([c()],d.prototype,"restoreError",2);l([c()],d.prototype,"senseTarget",2);l([c()],d.prototype,"senseCandidates",2);l([c()],d.prototype,"senseLookupAssetToken",2);l([c()],d.prototype,"senseLookupStatus",2);l([c()],d.prototype,"senseSelectedRef",2);l([c()],d.prototype,"senseState",2);l([c()],d.prototype,"senseError",2);l([c()],d.prototype,"renameOpen",2);l([c()],d.prototype,"renameDraft",2);l([c()],d.prototype,"renameState",2);l([c()],d.prototype,"renameError",2);l([c()],d.prototype,"folders",2);l([c()],d.prototype,"folderStatus",2);l([c()],d.prototype,"folderError",2);l([c()],d.prototype,"createFolderOpen",2);l([c()],d.prototype,"newFolderName",2);l([c()],d.prototype,"createFolderState",2);l([c()],d.prototype,"createFolderError",2);l([c()],d.prototype,"renameFolderTarget",2);l([c()],d.prototype,"renameFolderDraft",2);l([c()],d.prototype,"renameFolderState",2);l([c()],d.prototype,"renameFolderError",2);l([c()],d.prototype,"deleteFolderTarget",2);l([c()],d.prototype,"deleteFolderState",2);l([c()],d.prototype,"deleteFolderError",2);l([c()],d.prototype,"moveDeckTarget",2);l([c()],d.prototype,"moveDeckFolderId",2);l([c()],d.prototype,"moveDeckState",2);l([c()],d.prototype,"moveDeckError",2);l([c()],d.prototype,"mgmtAudioStatus",2);l([c()],d.prototype,"mgmtAudioMessage",2);l([c()],d.prototype,"mgmtRecordingStatus",2);l([c()],d.prototype,"mgmtRecordingBlob",2);l([c()],d.prototype,"mgmtRecordingNoteId",2);l([c()],d.prototype,"mgmtRecordingPreviewUrl",2);l([c()],d.prototype,"mgmtRevertConfirmation",2);l([c()],d.prototype,"mgmtRecordingError",2);l([c()],d.prototype,"mgmtShowRecordingControls",2);d=l([Ge("flashcard-app")],d);
