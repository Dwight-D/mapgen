"""Flowgraph editor page: HTML + CSS + vanilla JS (inline SVG, no external URLs). Design: mapgen/FLOWGRAPH_SPEC.md section 14."""
from __future__ import annotations

import json
from typing import Any, Dict


def render_page(boot: Dict[str, Any]) -> str:
    data = json.dumps(boot).replace("<", "\\u003c")
    title = "%s - flowgraph editor" % boot["graph"]["name"].replace("&", "&amp;").replace("<", "&lt;")
    return (_HTML.replace("@@TITLE@@", title).replace("@@CSS@@", _CSS).replace("@@JS@@", _JS).replace("@@BOOT@@", data))


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>@@TITLE@@</title>
@@CSS@@
</head>
<body>
<div id="bar">
  <span class="grp" role="group" aria-label="tools">
    <button id="t-select" data-tool="select" title="Select and drag (Esc)">Select</button>
    <button id="t-node" data-tool="node" title="Click empty canvas to add a node">Add node</button>
    <button id="t-edge" data-tool="edge" title="Click node A then node B (or Shift-click a second node with one selected)">Add edge</button>
    <button id="b-delete" title="Delete the selection (Del)">Delete</button>
  </span>
  <span class="grp">
    <button id="b-undo" title="Ctrl+Z">Undo</button>
    <button id="b-redo" title="Ctrl+Y">Redo</button>
    <button id="b-layout" title="Run the Python layout (place hints included); positions are not saved">Re-layout</button>
  </span>
  <span class="grp">
    <button id="b-save" class="primary">Save</button>
    <span id="savenote">Save rewrites <b id="fname"></b> in canonical form; comments in the file are not preserved.</span>
  </span>
</div>
<div id="main">
  <div id="canvaswrap"><svg id="graph" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="flowgraph"></svg>
    <div id="legend"></div></div>
  <div id="side">
    <div id="status" aria-live="polite"></div>
    <div id="panel"></div>
    <div id="errwrap"><h2 id="errhead">Validation</h2><ul id="errors"></ul></div>
  </div>
</div>
<script id="boot" type="application/json">@@BOOT@@</script>
<script>
@@JS@@
</script>
</body>
</html>
"""

_CSS = """<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:12px;background:#fff;color:#0b1020}
#bar{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;margin-bottom:10px}
.grp{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:4px 10px;border:2px solid #0b1020;background:#fff;color:#0b1020;border-radius:4px;cursor:pointer}
button:disabled{opacity:.45;cursor:not-allowed;border-style:dashed}
button.active{background:#0b1020;color:#fff}
button.primary{font-weight:700}
#savenote{font-size:13px;max-width:34ch}
#main{display:flex;gap:14px;align-items:flex-start}
#canvaswrap{flex:1 1 auto;min-width:0}
#graph{width:100%;height:auto;border:1px solid #9aa3b8;touch-action:none;user-select:none;background:#fff}
#side{flex:0 0 330px;font-size:14px}
#status{min-height:2.6em;margin-bottom:6px}
#panel h2,#errwrap h2{font-size:15px;margin:6px 0}
.row{display:flex;flex-direction:column;margin:5px 0}
.row label{font-size:12px;font-weight:700}
.row input,.row select,.row textarea{font:inherit;padding:3px 5px;border:1px solid #5b6478;border-radius:3px;box-sizing:border-box;width:100%}
.btns{display:flex;gap:6px;margin-top:8px}
#errors{margin:0;padding-left:20px}
#errors li{margin:2px 0;word-break:break-word}
.edge .vis{stroke:#0b1020;stroke-width:3}
.edge .hit{stroke:transparent;stroke-width:18;cursor:pointer}
.edge.sel .vis{stroke-width:7}
.elabel{font-size:14px;font-style:italic;fill:#0b1020;paint-order:stroke;stroke:#fff;stroke-width:5px;pointer-events:none}
.nlabel{font-size:15px;fill:#0b1020;paint-order:stroke;stroke:#fff;stroke-width:4px;pointer-events:none}
.nid{font-weight:700}.nteam{font-size:13px}
.team{font-size:20px;font-weight:700;pointer-events:none}
.node{cursor:grab}
.halo{fill:none;stroke:#0b1020;stroke-width:3;stroke-dasharray:2 5;stroke-linecap:round}
.ghost{fill:#fff;fill-opacity:.6;stroke:#0b1020;stroke-width:2.5;stroke-dasharray:3 4;pointer-events:none}
#legend{display:flex;flex-wrap:wrap;gap:2px 16px;margin-top:6px}
.key{display:inline-flex;align-items:center;gap:5px;font-size:13px}
@media (max-width:900px){#main{flex-direction:column}#side{flex-basis:auto;width:100%}}
</style>"""

_JS = r"""(function(){
'use strict';
var BOOT=JSON.parse(document.getElementById('boot').textContent);
var NS='http://www.w3.org/2000/svg',R=28,PAD=70;
var TOKEN=BOOT.token,STYLE=BOOT.style,KINDS=BOOT.kinds,PLACES=BOOT.places,LABELS=BOOT.labels;
var graph=BOOT.graph,pos=BOOT.pos,W=BOOT.w,H=BOOT.h;
var svg=document.getElementById('graph');
var sel=null,tool='select',pending=null,draft=null,down=null;
var undoS=[],redoS=[],errors=[],valid=false,seq=0,note='';
function $(id){return document.getElementById(id);}
function el(tag,attrs,text){var e=document.createElementNS(NS,tag);for(var k in attrs)e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e;}
function h(tag,attrs,text){var e=document.createElement(tag);for(var k in (attrs||{}))e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e;}
function snap(){return JSON.stringify(graph);}
function nodeById(id){for(var i=0;i<graph.nodes.length;i++)if(graph.nodes[i].id===id)return graph.nodes[i];return null;}
function pts(a){return a.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1);}).join(' ');}
function shape(kind,cx,cy,r,extra){
  var st=STYLE[kind],s=st[0],a={fill:st[1],stroke:'#0b1020','stroke-width':2.5};
  if(st[2])a['stroke-dasharray']=st[2];
  for(var k0 in (extra||{}))a[k0]=extra[k0];
  if(s==='square'){a.x=cx-r*0.9;a.y=cy-r*0.9;a.width=r*1.8;a.height=r*1.8;return el('rect',a);}
  if(s==='circle'){a.cx=cx;a.cy=cy;a.r=r;return el('circle',a);}
  var p=[],i,k;
  if(s==='diamond'){k=r*1.2;p=[[cx,cy-k],[cx+k,cy],[cx,cy+k],[cx-k,cy]];}
  else if(s==='hexagon'){k=r*1.1;for(i=0;i<6;i++)p.push([cx+k*Math.cos(Math.PI/3*i),cy+k*Math.sin(Math.PI/3*i)]);}
  else{k=r*1.3;for(i=0;i<10;i++){var q=i%2===0?k:k*0.45;p.push([cx+q*Math.sin(Math.PI/5*i),cy-q*Math.cos(Math.PI/5*i)]);}}
  a.points=pts(p);return el('polygon',a);
}
var TEXT_ON={spawn:'#ffffff',objective:'#ffffff',zone:'#0b1020',choke:'#0b1020',area:'#0b1020'};
function setView(){svg.setAttribute('viewBox','0 -'+PAD+' '+W+' '+(H+2*PAD));}
function ensurePos(){graph.nodes.forEach(function(n,i){if(!pos[n.id])pos[n.id]=[W/2+30*i,H/2];});}
function isSel(id){return (sel&&sel.t==='node'&&sel.id===id)||pending===id;}

function draw(){
  ensurePos();setView();
  while(svg.firstChild)svg.removeChild(svg.firstChild);
  var ge=el('g',{id:'edges'}),gl=el('g',{}),gn=el('g',{id:'nodes'});
  graph.edges.forEach(function(e,i){
    var a=pos[e.a],b=pos[e.b];if(!a||!b)return;
    var g=el('g',{'class':'edge'+(sel&&sel.t==='edge'&&sel.i===i?' sel':''),'data-i':i,'data-a':e.a,'data-b':e.b});
    g.appendChild(el('title',{},e.a+' - '+e.b+(e.name?': '+e.name:'')+(e.desc?'\n'+e.desc:'')));
    g.appendChild(el('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],'class':'vis'}));
    g.appendChild(el('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],'class':'hit'}));
    ge.appendChild(g);
    if(e.name)gl.appendChild(el('text',{'class':'elabel',x:(a[0]+b[0])/2,y:(a[1]+b[1])/2-6,'text-anchor':'middle'},e.name));
  });
  graph.nodes.forEach(function(n){
    var p=pos[n.id],kind=STYLE[n.kind]?n.kind:'area';
    var g=el('g',{'class':'node','data-id':n.id,'data-kind':kind});
    g.appendChild(el('title',{},n.id+' ('+LABELS[kind]+(n.team?', team '+n.team:'')+')'+(n.desc?'\n'+n.desc:'')));
    if(isSel(n.id))g.appendChild(el('circle',{'class':'halo',cx:p[0],cy:p[1],r:R*1.3+10}));
    g.appendChild(shape(kind,p[0],p[1],R,isSel(n.id)?{'stroke-width':5}:null));
    if(n.team&&kind==='spawn')g.appendChild(el('text',{'class':'team',x:p[0],y:p[1]+7,'text-anchor':'middle',fill:TEXT_ON[kind]},n.team));
    var t=el('text',{'class':'nlabel',x:p[0],y:p[1]+R*1.3+22,'text-anchor':'middle'});
    t.appendChild(el('tspan',{'class':'nid'},n.id));
    if(n.name)t.appendChild(el('tspan',{x:p[0],dy:18},n.name));
    if(n.team)t.appendChild(el('tspan',{'class':'nteam',x:p[0],dy:18},'team '+n.team));
    g.appendChild(t);gn.appendChild(g);
  });
  svg.appendChild(ge);svg.appendChild(gl);svg.appendChild(gn);
  if(draft)svg.appendChild(el('circle',{'class':'ghost',cx:draft.x,cy:draft.y,r:R}));
}

function drawLegend(){
  var lg=$('legend');
  KINDS.forEach(function(k){
    var s=document.createElementNS(NS,'svg');s.setAttribute('width',40);s.setAttribute('height',40);s.setAttribute('viewBox','0 0 40 40');
    s.appendChild(shape(k,20,20,12));
    var span=h('span',{'class':'key'});span.appendChild(s);span.appendChild(document.createTextNode(LABELS[k]));lg.appendChild(span);
  });
}

/* ---- panel ---- */
function field(label,type,value,onchange,opts){
  var row=h('div',{'class':'row'}),id='f-'+label.replace(/\W/g,'');
  row.appendChild(h('label',{'for':id},label));
  var inp;
  if(type==='select'){inp=h('select',{id:id});opts.forEach(function(o){inp.appendChild(h('option',{value:o},o===''?'(none)':o));});inp.value=value;}
  else if(type==='textarea'){inp=h('textarea',{id:id,rows:3});inp.value=value;}
  else{inp=h('input',{id:id,type:'text'});inp.value=value;}
  if(onchange)inp.addEventListener('change',function(){onchange(inp.value);});
  row.appendChild(inp);return row;
}
function nextId(){var i=1;while(nodeById('N'+i))i++;return 'N'+i;}
function renderPanel(){
  var p=$('panel');while(p.firstChild)p.removeChild(p.firstChild);
  if(draft){
    p.appendChild(h('h2',{},'New node'));
    var f={id:nextId(),kind:'area',team:'',name:'',desc:'',place:''},teamRow;
    p.appendChild(field('id','text',f.id,function(v){f.id=v;}));
    p.appendChild(field('kind','select',f.kind,function(v){f.kind=v;teamRow.style.display=v==='spawn'?'':'none';},KINDS));
    teamRow=field('team','text','',function(v){f.team=v;});teamRow.style.display='none';p.appendChild(teamRow);
    p.appendChild(field('name','text','',function(v){f.name=v;}));
    p.appendChild(field('desc','textarea','',function(v){f.desc=v;}));
    p.appendChild(field('place','select','',function(v){f.place=v;},[''].concat(PLACES)));
    var b=h('div',{'class':'btns'}),ok=h('button',{id:'n-add','class':'primary'},'Add node'),no=h('button',{id:'n-cancel'},'Cancel');
    ok.addEventListener('click',function(){
      var q=draft;draft=null;
      commit(function(){
        var n={id:f.id,kind:f.kind,team:f.kind==='spawn'?f.team:'',name:f.name,desc:f.desc,bind:'',place:f.place};
        graph.nodes.push(n);pos[n.id]=[q.x,q.y];sel={t:'node',id:n.id};
      });
    });
    no.addEventListener('click',function(){draft=null;note='';draw();renderPanel();syncButtons();});
    b.appendChild(ok);b.appendChild(no);p.appendChild(b);return;
  }
  if(sel&&sel.t==='node'){
    var n=nodeById(sel.id);if(!n){sel=null;return renderPanel();}
    p.appendChild(h('h2',{},'Node '+n.id));
    p.appendChild(field('id','text',n.id,function(v){commit(function(){renameNode(n,v);});}));
    p.appendChild(field('kind','select',n.kind,function(v){commit(function(){n.kind=v;if(v!=='spawn')n.team='';});},KINDS));
    if(n.kind==='spawn')p.appendChild(field('team','text',n.team||'',function(v){commit(function(){n.team=v;});}));
    p.appendChild(field('name','text',n.name,function(v){commit(function(){n.name=v;});}));
    p.appendChild(field('desc','textarea',n.desc,function(v){commit(function(){n.desc=v;});}));
    p.appendChild(field('place','select',n.place||'',function(v){commit(function(){n.place=v;});},[''].concat(PLACES)));
    return;
  }
  if(sel&&sel.t==='edge'){
    var e=graph.edges[sel.i];if(!e){sel=null;return renderPanel();}
    p.appendChild(h('h2',{},'Edge '+e.a+' - '+e.b));
    p.appendChild(field('name','text',e.name,function(v){commit(function(){e.name=v;});}));
    p.appendChild(field('desc','textarea',e.desc,function(v){commit(function(){e.desc=v;});}));
    return;
  }
  p.appendChild(h('p',{},'Select a node or edge to edit it. Add node: click the tool, then empty canvas. Add edge: click the tool, then two nodes. Drag nodes to move them (positions are not saved).'));
  p.appendChild(field('graph name','text',graph.name,function(v){commit(function(){graph.name=v;});}));
  p.appendChild(field('brief','textarea',graph.brief,function(v){commit(function(){graph.brief=v;});}));
}
function renameNode(n,v){
  var old=n.id;if(v===old)return;n.id=v;
  graph.edges.forEach(function(e){if(e.a===old)e.a=v;if(e.b===old)e.b=v;});
  if(pos[old]&&!pos[v])pos[v]=pos[old];
  if(sel&&sel.t==='node'&&sel.id===old)sel.id=v;
}

/* ---- state changes ---- */
function commit(fn){undoS.push(snap());redoS=[];note="";fn();afterEdit();}
function afterEdit(){
  if(sel&&sel.t==='node'&&!nodeById(sel.id))sel=null;
  if(sel&&sel.t==='edge'&&!graph.edges[sel.i])sel=null;
  draw();renderPanel();validate();syncButtons();
}
function undo(){if(!undoS.length)return;redoS.push(snap());graph=JSON.parse(undoS.pop());sel=null;afterEdit();}
function redo(){if(!redoS.length)return;undoS.push(snap());graph=JSON.parse(redoS.pop());sel=null;afterEdit();}
function addEdge(a,b){
  if(a===b){note='An edge needs two different nodes.';return syncButtons();}
  for(var i=0;i<graph.edges.length;i++){var e=graph.edges[i];if((e.a===a&&e.b===b)||(e.a===b&&e.b===a)){note='Edge '+a+' - '+b+' already exists.';return syncButtons();}}
  commit(function(){graph.edges.push({a:a,b:b,name:'',desc:''});sel={t:'edge',i:graph.edges.length-1};});
}
function deleteSel(){
  if(!sel)return;
  if(sel.t==='node'){
    var id=sel.id;
    commit(function(){
      graph.nodes=graph.nodes.filter(function(n){return n.id!==id;});
      graph.edges=graph.edges.filter(function(e){return e.a!==id&&e.b!==id;});sel=null;
    });
  }else{var i=sel.i;commit(function(){graph.edges.splice(i,1);sel=null;});}
}

/* ---- server ---- */
function api(path,body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Token':TOKEN},body:JSON.stringify(body)})
    .then(function(r){return r.json();});
}
function validate(){
  var my=++seq;valid=false;syncButtons();
  return api('/api/validate',{graph:graph}).then(function(r){
    if(my!==seq)return;errors=r.errors||[];valid=!!r.ok;renderErrors();syncButtons();
  }).catch(function(e){if(my!==seq)return;errors=['server unreachable: '+e];valid=false;renderErrors();syncButtons();});
}
function renderErrors(){
  var ul=$('errors');while(ul.firstChild)ul.removeChild(ul.firstChild);
  $('errhead').textContent=valid?'Validation: OK ('+graph.nodes.length+' nodes, '+graph.edges.length+' edges)':'Validation: '+errors.length+' error(s) - cannot save';
  errors.forEach(function(m){ul.appendChild(h('li',{},m));});
}
function syncButtons(){
  $('b-save').disabled=!valid;$('b-undo').disabled=!undoS.length;$('b-redo').disabled=!redoS.length;$('b-delete').disabled=!sel;
  ['select','node','edge'].forEach(function(t){var b=$('t-'+t);b.className=tool===t?'active':'';b.setAttribute('aria-pressed',tool===t?'true':'false');});
  $('status').textContent=note;
}
function relayout(){
  api('/api/layout',{graph:graph}).then(function(r){
    if(!r.ok){note='Re-layout needs a valid graph: '+(r.errors[0]||'');return syncButtons();}
    pos=r.pos;W=r.w;H=r.h;note='Layout applied.';draw();syncButtons();
  });
}
function save(){
  if(!valid)return;
  api('/api/save',{graph:graph}).then(function(r){
    note=r.ok?'Saved '+r.path+' ('+r.bytes+' bytes).':'Save refused: '+(r.errors[0]||'');
    syncButtons();
  });
}

/* ---- pointer handling ---- */
function toSvg(ev){
  var pt=svg.createSVGPoint();pt.x=ev.clientX;pt.y=ev.clientY;var m=svg.getScreenCTM();
  return m?pt.matrixTransform(m.inverse()):{x:ev.clientX,y:ev.clientY};
}
svg.addEventListener('pointerdown',function(ev){
  if(ev.button!==0)return;
  var nd=ev.target.closest('.node'),ed=ev.target.closest('.edge'),p=toSvg(ev),id=nd?nd.getAttribute('data-id'):null;
  down={kind:nd?'node':ed?'edge':'bg',id:id,i:ed?+ed.getAttribute('data-i'):null,sx:ev.clientX,sy:ev.clientY,moved:false,x:p.x,y:p.y,shift:ev.shiftKey,
        ox:id&&pos[id]?pos[id][0]-p.x:0,oy:id&&pos[id]?pos[id][1]-p.y:0};
});
window.addEventListener('pointermove',function(ev){
  if(!down||down.kind!=='node')return;
  if(!down.moved&&Math.hypot(ev.clientX-down.sx,ev.clientY-down.sy)<4)return;
  down.moved=true;var p=toSvg(ev);
  pos[down.id]=[Math.max(20,Math.min(W-20,p.x+down.ox)),Math.max(-PAD+30,Math.min(H+PAD-30,p.y+down.oy))];draw();
});
window.addEventListener('pointerup',function(){
  var d=down;down=null;if(!d||d.moved)return;
  if(d.kind==='node')nodeClick(d.id,d.shift);
  else if(d.kind==='edge'){if(tool==='select'){sel={t:'edge',i:d.i};draft=null;pending=null;note='';draw();renderPanel();syncButtons();}}
  else bgClick(d.x,d.y);
});
function nodeClick(id,shift){
  if(tool==='edge'||(shift&&sel&&sel.t==='node'&&sel.id!==id)){
    var from=tool==='edge'?pending:sel.id;
    if(from==null){pending=id;note='Edge from '+id+': click the second node.';draw();syncButtons();return;}
    pending=null;note='';addEdge(from,id);return;
  }
  if(tool==='node')return;
  sel={t:'node',id:id};draft=null;note='';draw();renderPanel();syncButtons();
}
function bgClick(x,y){
  if(tool==='node'){draft={x:x,y:y};sel=null;note='Fill in the form and press Add node.';draw();renderPanel();syncButtons();return;}
  pending=null;sel=null;draft=null;note='';draw();renderPanel();syncButtons();
}
function setTool(t){
  tool=t;pending=null;if(t!=='node')draft=null;
  note=t==='node'?'Click empty canvas to place a node.':t==='edge'?'Click node A, then node B.':'';
  draw();renderPanel();syncButtons();
}
document.querySelectorAll('[data-tool]').forEach(function(b){b.addEventListener('click',function(){setTool(b.getAttribute('data-tool'));});});
$('b-delete').addEventListener('click',deleteSel);
$('b-undo').addEventListener('click',undo);
$('b-redo').addEventListener('click',redo);
$('b-layout').addEventListener('click',relayout);
$('b-save').addEventListener('click',save);
window.addEventListener('keydown',function(ev){
  var tg=ev.target&&ev.target.tagName,typing=tg==='INPUT'||tg==='TEXTAREA'||tg==='SELECT';
  if(ev.key==='Escape'){setTool('select');sel=null;draw();renderPanel();syncButtons();}
  else if((ev.ctrlKey||ev.metaKey)&&!typing&&(ev.key==='z'||ev.key==='Z')){ev.preventDefault();if(ev.shiftKey)redo();else undo();}
  else if((ev.ctrlKey||ev.metaKey)&&!typing&&(ev.key==='y'||ev.key==='Y')){ev.preventDefault();redo();}
  else if(!typing&&(ev.key==='Delete'||ev.key==='Backspace')){deleteSel();}
});

$('fname').textContent=BOOT.file;
if(!BOOT.exists)note='New graph: '+BOOT.file+' is written on first Save.';
drawLegend();draw();renderPanel();validate();syncButtons();
})();"""
