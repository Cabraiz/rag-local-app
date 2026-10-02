/* Real 3D coordinates, camera projection and picking; no CDN or cloud calls. */
window.EmbeddingView=(()=>{
  const el=id=>document.getElementById(id), canvas=()=>el('embedding-canvas');
  const colors=['#ef6687','#80d5a1','#b39bff','#f4bf63','#66cbd4','#ed92d2','#a4d467','#819df6'];
  let adapter=null,data=null,selected=null,hovered=null,active=false,serial=0,controller=null;
  let yaw=.4,pitch=-.18,zoom=1,spin=false,frame=null,projected=[],drag=null,width=0,height=0;
  const format=v=>Number(v).toLocaleString('pt-BR',{minimumFractionDigits:3,maximumFractionDigits:3});
  const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function clear(){
    ++serial;controller?.abort();controller=null;data=null;selected=null;hovered=null;projected=[];drag=null;
    stop();active=false;yaw=.4;pitch=-.18;zoom=1;
    for(const id of ['embedding-legend','embedding-points','embedding-query-matches','embedding-selection'])el(id)?.replaceChildren();
    for(const id of ['embedding-question','embedding-query-text']){const n=el(id);if(n){if(n.tagName==='INPUT')n.value='';else n.textContent='';}}
    el('embedding-query-result').hidden=true;el('embedding-message').textContent='';
    el('embedding-meta').textContent='Nenhum perfil carregado.';el('embedding-count').textContent='Sem dados';
    const c=canvas();c.getContext('2d')?.clearRect(0,0,c.width,c.height);c.removeAttribute('data-rendered-points');
    el('embedding-query-submit').disabled=false;el('embedding-reload').disabled=false;
  }
  function stop(){spin=false;if(frame!==null)cancelAnimationFrame(frame);frame=null;el('embedding-spin')?.setAttribute('aria-pressed','false');}
  function schedule(){if(!active||frame!==null)return;frame=requestAnimationFrame(tick);}
  function tick(){frame=null;if(!active)return;draw();if(spin&&!document.hidden){yaw+=.004;schedule();}}
  function activate(value){active=value;if(!value){stop();return;}if(adapter&&!data&&!controller)load();schedule();}
  async function load(question=null){
    if(!adapter||!active)return;
    const requestSerial=++serial;controller?.abort();controller=new AbortController();stop();
    const signal=controller.signal;el('embedding-message').textContent=question?'Projetando pergunta localmente…':'Lendo vetores reais do Qdrant…';
    el('embedding-query-submit').disabled=true;el('embedding-reload').disabled=true;
    try{
      const result=await adapter.request(question===null?'/v1/lab/embeddings':'/v1/lab/embeddings/query',question===null?{signal}:{method:'POST',signal,body:JSON.stringify({question})});
      if(requestSerial!==serial)return;
      if(!Array.isArray(result.points)||result.points.length>64||!Array.isArray(result.edges)||result.cloud_calls!==0)throw new Error('Formato do mapa inválido.');
      data=result;selected=result.query?.matches[0]?.id||result.points[0]?.id||null;render();
      el('embedding-message').textContent=result.points.length?'Mapa atualizado. Similaridades são calculadas nos vetores originais.':'Esta base ainda não tem vetores disponíveis.';
      schedule();
    }catch(error){if(requestSerial===serial&&error.name!=='AbortError'&&error.message!=='SESSION_CHANGED'){
      data=null;selected=null;projected=[];el('embedding-points').replaceChildren();el('embedding-legend').replaceChildren();el('embedding-selection').replaceChildren();el('embedding-query-matches').replaceChildren();el('embedding-query-result').hidden=true;
      el('embedding-count').textContent='Mapa indisponível';el('embedding-meta').textContent='Não foi possível validar a base atual.';
      el('embedding-message').textContent='Não foi possível carregar: '+error.message;schedule();
    }}finally{if(requestSerial===serial){controller=null;el('embedding-query-submit').disabled=false;el('embedding-reload').disabled=false;}}
  }
  function documentColors(){const ids=[...new Set(data.points.map(p=>p.document_id))];return new Map(ids.map((id,i)=>[id,colors[i%colors.length]]));}
  function select(id){if(!data?.points.some(p=>p.id===id))return;selected=id;renderSelection();for(const b of el('embedding-points').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.chunk===id));schedule();}
  function pointButton(point,score){const b=make('button',point.title+(score===undefined?'':` · cosseno ${format(score)}`));b.type='button';b.dataset.chunk=point.id;b.onclick=()=>select(point.id);return b;}
  function render(){
    el('embedding-count').textContent=`${data.points.length} pontos reais · ${new Set(data.points.map(p=>p.document_id)).size} documentos`;
    const retained=data.projection?Math.round(data.projection.retained_variance*100)+'% da variância preservada':'sem projeção';
    el('embedding-meta').textContent=`${data.original_dimensions||0} dimensões → PCA 3D · ${retained}${data.sampled?` · amostra de ${data.points.length}/${data.total_chunks} trechos`:''}`;
    el('embedding-legend').replaceChildren();el('embedding-points').replaceChildren();
    if(!data.points.length){el('embedding-selection').replaceChildren(make('p','Nenhum trecho disponível.'));return;}
    const palette=documentColors(),used=new Set();
    for(const [i,p] of data.points.entries()){
      if(!used.has(p.document_id)){used.add(p.document_id);const label=make('span',p.title);const dot=make('i');dot.style.background=palette.get(p.document_id);label.prepend(dot);el('embedding-legend').append(label);}
      const b=pointButton(p);b.prepend(make('small',String(i+1).padStart(2,'0')+' · '));b.setAttribute('aria-pressed',String(p.id===selected));el('embedding-points').append(b);
    }
    el('embedding-query-result').hidden=!data.query;el('embedding-query-matches').replaceChildren();
    if(data.query){el('embedding-query-text').textContent=data.query.text;for(const match of data.query.matches.slice(0,3)){const li=make('li'),p=data.points.find(p=>p.id===match.id);li.append(pointButton(p,match.cosine));el('embedding-query-matches').append(li);}}
    renderSelection();
  }
  function renderSelection(){
    const box=el('embedding-selection');box.replaceChildren();const p=data?.points.find(p=>p.id===selected);if(!p)return;
    box.append(make('h3',p.title),make('blockquote',p.quote));
    const open=make('button','Abrir documento');open.className='secondary';open.type='button';open.onclick=()=>adapter.openDocument(p.document_id);box.append(open);
    const neighbors=make('div'),title=make('h4','Vizinhos no vetor original');neighbors.append(title);
    for(const match of p.neighbors){const other=data.points.find(p=>p.id===match.id);if(other)neighbors.append(pointButton(other,match.cosine));}box.append(neighbors);
    const technical=make('details');technical.append(make('summary','Inspecionar vetor e versão'));
    const text=make('pre',`Modelo: ${data.embedding_version}\nDimensões: ${data.original_dimensions}\nVersão: ${data.release_id}\nTrecho: ${p.id}\nPrimeiras 12 dimensões:\n[${p.vector_preview.join(', ')}]\nCoordenadas PCA:\n[${p.position.join(', ')}]`);technical.append(text);box.append(technical);
  }
  function draw(){
    const c=canvas(),rect=c.getBoundingClientRect();if(rect.width<1||rect.height<1)return;
    width=rect.width;height=rect.height;const ratio=Math.min(devicePixelRatio||1,2);
    if(c.width!==Math.round(width*ratio)||c.height!==Math.round(height*ratio)){c.width=Math.round(width*ratio);c.height=Math.round(height*ratio);}
    const ctx=c.getContext('2d');if(!ctx){el('embedding-message').textContent='Canvas indisponível. Use a lista de trechos.';return;}
    ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle='#10191e';ctx.fillRect(0,0,width,height);
    c.dataset.cameraYaw=yaw.toFixed(3);c.dataset.zoom=zoom.toFixed(2);
    if(!data?.points.length){ctx.fillStyle='#9baeb4';ctx.font='13px system-ui';ctx.textAlign='center';ctx.fillText('Os vetores deste perfil aparecerão aqui.',width/2,height/2);return;}
    const all=data.query?[...data.points,{position:data.query.position}]:data.points;
    const extent=Math.max(.15,...all.flatMap(p=>p.position.map(Math.abs)))*1.25;
    // Reserve real plot space above the legend and camera controls, including
    // the stacked mobile toolbar. Default framing must not hide data under UI.
    const plotHeight=Math.max(80,height-244),centerY=64+plotHeight/2;
    const scale=Math.max(60,Math.min(width-40,plotHeight))*.38*zoom,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
    function camera(position){const [x,y,z]=position.map(v=>v/extent),xx=x*cy+z*sy,zz=-x*sy+z*cy,yy=y*cp-zz*sp,depth=y*sp+zz*cp,f=3.5/(3.5+depth);return {x:width/2+xx*scale*f,y:centerY-yy*scale*f,z:depth,f};}
    function line(a,b,color,lineWidth=1){ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.stroke();}
    for(let grid=-1;grid<=1.01;grid+=.5){line(camera([-extent,grid*extent,0]),camera([extent,grid*extent,0]),'#223139');line(camera([grid*extent,-extent,0]),camera([grid*extent,extent,0]),'#223139');}
    const origin=camera([0,0,0]);for(const [i,label,color] of [[0,'PC1','#b48293'],[1,'PC2','#80ac99'],[2,'PC3','#8c90b6']]){const a=[0,0,0];a[i]=extent;const end=camera(a);line(origin,end,color);ctx.fillStyle=color;ctx.font='10px system-ui';ctx.textAlign='center';ctx.fillText(label,end.x,end.y-9);}
    const palette=documentColors();projected=data.points.map(p=>({...camera(p.position),point:p}));const byId=new Map(projected.map(p=>[p.point.id,p]));
    if(el('embedding-show-lines').checked){const threshold=Number(el('embedding-threshold').value);for(const edge of data.edges){if(edge.cosine<threshold)continue;const a=byId.get(edge.source),b=byId.get(edge.target),highlight=edge.source===selected||edge.target===selected;ctx.globalAlpha=highlight?.8:.32;line(a,b,palette.get(a.point.document_id),highlight?1.5:1);ctx.globalAlpha=1;}}
    if(data.query){const q=camera(data.query.position);for(const m of data.query.matches.slice(0,3)){const p=byId.get(m.id);ctx.setLineDash([4,5]);line(q,p,'rgba(250,237,166,.6)');ctx.setLineDash([]);}ctx.fillStyle='#fff2ab';ctx.beginPath();ctx.moveTo(q.x,q.y-9);ctx.lineTo(q.x+8,q.y);ctx.lineTo(q.x,q.y+9);ctx.lineTo(q.x-8,q.y);ctx.closePath();ctx.fill();ctx.font='11px system-ui';ctx.textAlign='center';ctx.fillText('PERGUNTA',q.x,q.y-18);}
    projected.sort((a,b)=>b.z-a.z);
    for(const p of projected){const chosen=p.point.id===selected,over=p.point.id===hovered,r=(chosen?8:6)*p.f,color=palette.get(p.point.document_id);
      if(chosen||over){ctx.beginPath();ctx.arc(p.x,p.y,r+7,0,Math.PI*2);ctx.strokeStyle=chosen?'#edf7f9':'#708b95';ctx.lineWidth=1.5;ctx.stroke();}
      const fill=ctx.createRadialGradient(p.x-r*.3,p.y-r*.3,0,p.x,p.y,r);fill.addColorStop(0,'#f3f7fa');fill.addColorStop(.3,color);fill.addColorStop(1,color);ctx.fillStyle=fill;ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='#c6d8dd';ctx.font='11px system-ui';ctx.textAlign='center';const index=data.points.indexOf(p.point)+1;ctx.fillText(String(index).padStart(2,'0'),p.x,p.y+r+15);
      if(chosen||over){const title=p.point.title;const tw=ctx.measureText(title).width+18,x=clamp(p.x-tw/2,4,width-tw-4),y=clamp(p.y-38,18,height-14);ctx.fillStyle='#263b44';ctx.fillRect(x,y-13,tw,22);ctx.fillStyle='#fff';ctx.fillText(title,x+tw/2,y+2);}
    }
    c.dataset.renderedPoints=String(projected.length);
  }
  function hit(x,y){for(const p of [...projected].reverse())if(Math.hypot(p.x-x,p.y-y)<Math.max(14,9*p.f))return p.point.id;return null;}
  function bind(){
    const c=canvas();new ResizeObserver(schedule).observe(c);
    c.addEventListener('pointerdown',event=>{stop();c.setPointerCapture(event.pointerId);drag={id:event.pointerId,x:event.clientX,y:event.clientY,total:0};});
    c.addEventListener('pointermove',event=>{const r=c.getBoundingClientRect();if(drag){const dx=event.clientX-drag.x,dy=event.clientY-drag.y;drag.total+=Math.abs(dx)+Math.abs(dy);yaw+=dx*.008;pitch=clamp(pitch+dy*.008,-1.35,1.35);drag.x=event.clientX;drag.y=event.clientY;}else hovered=hit(event.clientX-r.left,event.clientY-r.top);schedule();});
    c.addEventListener('pointerup',event=>{if(drag?.id!==event.pointerId)return;if(drag.total<6){const r=c.getBoundingClientRect();select(hit(event.clientX-r.left,event.clientY-r.top));}drag=null;c.releasePointerCapture(event.pointerId);});
    c.addEventListener('pointercancel',()=>{drag=null;});c.addEventListener('pointerleave',()=>{hovered=null;schedule();});
    c.addEventListener('wheel',event=>{event.preventDefault();zoom=clamp(zoom*Math.exp(-event.deltaY*.001),.45,3);schedule();},{passive:false});
    c.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','0'].includes(event.key))return;event.preventDefault();stop();if(event.key==='ArrowLeft')yaw-=.15;if(event.key==='ArrowRight')yaw+=.15;if(event.key==='ArrowUp')pitch=clamp(pitch+.1,-1.35,1.35);if(event.key==='ArrowDown')pitch=clamp(pitch-.1,-1.35,1.35);if(['+','='].includes(event.key))zoom=clamp(zoom*1.15,.45,3);if(event.key==='-')zoom=clamp(zoom/1.15,.45,3);if(event.key==='0'){yaw=.4;pitch=-.18;zoom=1;}schedule();});
    el('embedding-left').onclick=()=>{stop();yaw-=.2;schedule();};el('embedding-right').onclick=()=>{stop();yaw+=.2;schedule();};
    el('embedding-zoom-in').onclick=()=>{zoom=clamp(zoom*1.2,.45,3);schedule();};el('embedding-zoom-out').onclick=()=>{zoom=clamp(zoom/1.2,.45,3);schedule();};
    el('embedding-reset').onclick=()=>{stop();yaw=.4;pitch=-.18;zoom=1;schedule();};
    el('embedding-spin').onclick=()=>{if(spin)stop();else if(!matchMedia('(prefers-reduced-motion: reduce)').matches){spin=true;el('embedding-spin').setAttribute('aria-pressed','true');schedule();}else el('embedding-message').textContent='Giro automático desativado por sua preferência de movimento reduzido. Use os controles.';};
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
    el('embedding-threshold').oninput=()=>{el('embedding-threshold-value').value=Number(el('embedding-threshold').value).toLocaleString('pt-BR',{minimumFractionDigits:2});schedule();};el('embedding-show-lines').onchange=schedule;
    el('embedding-reload').onclick=()=>load();
    el('embedding-query-form').onsubmit=event=>{event.preventDefault();const question=el('embedding-question').value.trim();if(question&&question.length<=4000)load(question);};
    el('embedding-clear-query').onclick=()=>{if(data){data.query=null;el('embedding-question').value='';render();schedule();}};
  }
  document.addEventListener('DOMContentLoaded',bind,{once:true});
  return {configure:value=>{adapter=value;},activate,clear};
})();
