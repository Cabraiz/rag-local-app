let token=null, pending=null, timer=null, feedTimer=null, feedLoading=false, epoch=0, activeTenant=null, activeProfile=null, activeRole=null;
let catalog=null, selectedId=null, busy=false, refreshing=false;
let historySignature=null, answerSignature=null, feedSignature=null;
const $=id=>document.getElementById(id);
const profiles={ana:{name:'Ana',company:'Aurora',role:'client'},bruno:{name:'Bruno',company:'Aurora',role:'operator'}};
const allowedViews=()=>activeRole==='operator'?['documentos','integracoes','embeddings']:['consultar'];
const profileButtons=Array.from(document.querySelectorAll('[data-profile]'));
const terminal=new Set(['SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED']);
const labels={ACCEPTED:'Pedido registrado',RUNNING:'Buscando nos documentos',RETRY_WAIT:'Aguardando nova tentativa',SUCCEEDED:'Consulta concluída',FAILED_FINAL:'Falha na consulta',EXPIRED:'Prazo esgotado',CANCELLED:'Consulta cancelada'};
const examples=[['Alimentação em viagem','Qual o teto de gastos com comida durante uma viagem?'],['Prazo das notas fiscais','Quando devo mandar as notas fiscais ao financeiro?'],['Horário do suporte','Em quais dias e horários consigo falar com a assistência?'],['Trabalhar de casa','O que preciso conectar antes de usar os sistemas fora da empresa?'],['Algo fora da base','Qual é o orçamento aprovado para hotel?']];
const storageKey=()=>'rag.lab.pending.'+activeTenant;
const node=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const viewInfo={consultar:['Consultar a base','Faça uma pergunta e confira o documento que sustenta a resposta.'],documentos:['Documentos','Leia as fontes disponíveis para a empresa selecionada.'],historico:['Histórico','Acompanhe resultados, estados e recibos das consultas.'],integracoes:['Integrações','Entenda onde Jira e GitHub entram — e o que ainda não está conectado.'],funcionamento:['Como funciona','Veja o fluxo do RAG e os limites desta demonstração.'],embeddings:['Embeddings 3D','Explore os vetores locais, os trechos e suas similaridades.']};
function activeView(){const name=location.hash.slice(1);return Object.hasOwn(viewInfo,name)?name:'consultar';}
function activateView(name,focusHeading=false){
  if(!allowedViews().includes(name))name=allowedViews()[0];
  for(const key of Object.keys(viewInfo))$('view-'+key).hidden=key!==name;
  for(const link of document.querySelectorAll('nav [data-view]')){if(link.dataset.view===name)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
  $('page-title').textContent=viewInfo[name][0];$('page-description').textContent=viewInfo[name][1];
  document.title=token?viewInfo[name][0]+' · '+profiles[activeProfile].name+' / Aurora / RAG':'Entrar · RAG local';
  window.EmbeddingView?.activate(Boolean(token&&name==='embeddings'));
  if(token&&activeRole==='operator'&&name==='integracoes')loadIntegrations();
  if(focusHeading)$('page-title').focus({preventScroll:true});
  return name;
}
function navigate(name,focusHeading=true){name=activateView(name,focusHeading);if(location.hash!=='#'+name)location.hash=name;}
for(const link of document.querySelectorAll('nav [data-view]'))link.onclick=event=>{event.preventDefault();navigate(link.dataset.view);};
window.addEventListener('hashchange',()=>navigate(activeView(),false));
activateView(activeView());
const integrationTabs=Array.from(document.querySelectorAll('.integration-tabs [role="tab"]'));
function selectIntegrationTab(tab,focus=false){
  for(const item of integrationTabs){const selected=item===tab;item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;$(item.getAttribute('aria-controls')).hidden=!selected;}
  if(focus)tab.focus({preventScroll:true});
}
for(const tab of integrationTabs){
  tab.onclick=()=>selectIntegrationTab(tab);
  tab.onkeydown=event=>{
    const index=integrationTabs.indexOf(tab);let next;
    if(event.key==='ArrowRight')next=(index+1)%integrationTabs.length;
    else if(event.key==='ArrowLeft')next=(index+integrationTabs.length-1)%integrationTabs.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=integrationTabs.length-1;
    else return;
    event.preventDefault();selectIntegrationTab(integrationTabs[next],true);
  };
}
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
function report(error){if(error.message!=='SESSION_CHANGED')message(error.message,true);}
function clearIntent(){pending=null;sessionStorage.removeItem(storageKey());$('retry').hidden=true;}
async function api(path,options={}){
  const scope=epoch;
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`} : {}),...options.headers}});
  const body=await response.json().catch(()=>({code:'INVALID_RESPONSE'}));
  if(scope!==epoch)throw new Error('SESSION_CHANGED');
  if(!response.ok)throw new Error(body.code||(Array.isArray(body.detail)?body.detail.map(item=>item.msg).join('; '):body.detail)||`HTTP ${response.status}`);
  return body;
}
function stateLabel(row){return row.result?.kind==='ABSTAIN'?'Sem evidência suficiente':(labels[row.state]||row.state);}
function renderCorpus(){
  $('documents').replaceChildren();
  $('corpus-status').textContent=`${catalog.documents.length} documentos · ${catalog.embedding_version?.startsWith('minilm-')?'embeddings locais':'busca lexical local'}`;
  for(const doc of catalog.documents){const card=node('details',undefined,'document');card.id='doc-'+doc.id;card.append(node('summary',doc.title));for(const chunk of doc.chunks)card.append(node('p',chunk.quote));card.append(node('small','Fonte indexada · '+doc.id));$('documents').append(card);}
  if(!catalog.documents.length)$('documents').append(node('p','Esta empresa ainda não possui documentos disponíveis. Não haverá resposta factual sem uma fonte válida.'));
}
function renderAnswer(row){
  selectedId=row.id;$('request-id').value=row.id;
  if(terminal.has(row.state)&&$('message').textContent.startsWith('Pedido registrado.'))message(row.result?.kind==='ABSTAIN'?'Consulta concluída, sem evidência suficiente.':'Consulta concluída. O resultado está ao lado.');
  const signature=JSON.stringify(row);if(signature===answerSignature)return;answerSignature=signature;
  $('answer-status').textContent=stateLabel(row);$('answer').replaceChildren();$('sources').replaceChildren();
  $('detail').textContent=JSON.stringify(row,null,2);
  if(row.result?.model)$('answer').append(node('small',row.result.kind==='EXTRACTIVE'?'Gemini consultado · citação validada pela aplicação':'Gemini consultado · evidência insuficiente','note'));
  if(row.result?.kind==='ABSTAIN'){
    $('answer').append(node('p',row.result.text||'Não encontrei informação suficiente nos documentos desta empresa.'));
    $('sources').append(node('p','Peça ao responsável pela base para revisar ou publicar a informação necessária.','note'));
  }else if(row.result?.text){
    $('answer').append(node('p',row.result.text));
    for(const cite of row.result.citations||[]){
      const doc=catalog?.documents.find(d=>d.id===cite.document_id);
      const box=node('div',undefined,'source');box.append(node('strong','Fonte verificada'),node('p',cite.quote));
      $('sources').append(box);
    }
  }else{$('answer').append(node('p',terminal.has(row.state)?'Esta consulta terminou sem uma resposta factual. Veja o estado e o recibo técnico.':'A consulta está registrada. Aguardando o processamento local…'));}
}
async function show(id,openView=false){try{renderAnswer(await api('/v1/requests/'+encodeURIComponent(id)));if(openView)navigate('consultar');}catch(e){report(e);}}
async function refresh(){
  if(!token||activeRole!=='client'||refreshing)return;const scope=epoch;refreshing=true;
  try{
    // Public receipts intentionally omit internal source_snapshot. The API already
    // scopes history to the authenticated company; never infer an absent field.
    const rows=await api('/v1/requests');const visible=rows;
    const signature=JSON.stringify(rows);
    if(signature!==historySignature){
    historySignature=signature;const fragment=document.createDocumentFragment();
    if(!visible.length)fragment.append(node('p','Nenhuma consulta registrada nesta empresa. Experimente uma das perguntas acima.'));
    for(const row of visible){
      const card=node('article',undefined,'request'),body=node('div');
      body.append(node('strong',stateLabel(row)),node('p',row.result?.kind==='ABSTAIN'?'A base não oferece evidência suficiente para esta consulta.':row.result?.text||'Pedido em processamento…'),node('small',new Date(row.created_at).toLocaleString('pt-BR')));
      const actions=node('div'),view=node('button','Ver resposta');view.onclick=()=>{renderAnswer(row);navigate('consultar');};actions.append(view);
      if(!terminal.has(row.state)){const cancel=node('button','Cancelar');cancel.onclick=async()=>{try{await api(`/v1/requests/${row.id}/cancel`,{method:'POST',body:'{}'});await refresh();}catch(e){report(e);}};actions.append(cancel);}
      card.append(body,actions);fragment.append(card);
    }
    $('requests').replaceChildren(fragment);
    }
    const selected=rows.find(row=>row.id===selectedId);
    if(selected)renderAnswer(selected);
    else if(!selectedId){$('answer').replaceChildren(node('p','Escreva sua pergunta. A resposta aparecerá aqui com a fonte que a sustenta.'));$('answer-status').textContent='Pronto para consultar';}
  }catch(e){report(e);}finally{if(scope===epoch)refreshing=false;}
}
function logout(focus=true){
  window.EmbeddingView?.clear();
  ++epoch;clearInterval(timer);clearInterval(feedTimer);timer=null;feedTimer=null;feedLoading=false;token=null;pending=null;activeTenant=null;activeProfile=null;activeRole=null;
  catalog=null;selectedId=null;busy=false;refreshing=false;historySignature=null;answerSignature=null;feedSignature=null;
  for(const id of ['requests','documents','sources','integrations'])$(id).replaceChildren();
  for(const id of ['send','refresh','find'])$(id).disabled=true;
  $('question').value='';$('request-id').value='';$('detail').textContent='';$('answer').replaceChildren();
  $('technical').open=false;$('retry').hidden=true;message('');
  $('publication').reset();$('publication-message').textContent='';$('publish').disabled=true;
  $('integration-refresh').disabled=false;$('integration-sync-status').textContent='';
  $('active-profile').textContent='Perfil local';$('profile-scope').textContent='';
  $('online-count').textContent='Integrações não consultadas';$('connection').textContent='Desconectado';
  $('login-message').textContent='';$('app-shell').hidden=true;$('login-view').hidden=false;
  for(const button of profileButtons)button.disabled=false;
  document.title='Entrar · RAG local';if(focus)$('login-title').focus({preventScroll:true});
}
async function connect(profile){
  if(!Object.hasOwn(profiles,profile))return;
  for(const button of profileButtons)button.disabled=true;
  $('login-message').textContent='Abrindo o perfil de '+profiles[profile].name+'…';
  const scope=++epoch;token=null;pending=null;busy=false;refreshing=false;catalog=null;selectedId=null;clearInterval(timer);
  historySignature=null;answerSignature=null;
  for(const id of ['send','refresh','find'])$(id).disabled=true;
  for(const id of ['requests','documents','sources'])$(id).replaceChildren();$('detail').textContent='';$('answer').replaceChildren(node('p','Carregando consultas desta empresa…'));$('corpus-status').textContent='Carregando documentos…';
  $('answer-status').textContent='Carregando…';$('technical').open=false;
  $('question').value='';$('request-id').value='';message('');$('retry').hidden=true;$('connection').textContent='Conectando…';
  activeProfile=profile;activeTenant='demo-a';activeRole=null;
  try{
    const session=await api('/v1/lab/session',{method:'POST',body:JSON.stringify({profile})});
    if(session.profile!==profile||session.role!==profiles[profile].role||session.tenant!==activeTenant)throw new Error('INVALID_PROFILE');
    token=session.token;activeRole=session.role;
    const client=activeRole==='client';$('app-shell').classList.toggle('client-shell',client);
    document.querySelector('.sidebar').hidden=client;
    for(const link of document.querySelectorAll('nav [data-view]'))link.hidden=!allowedViews().includes(link.dataset.view);
    $('technical').hidden=client;$('examples').hidden=client;
    document.querySelector('.question-panel>.note').hidden=client;
    $('embedding-query-form').hidden=true;$('embedding-query-submit').disabled=true;
    if(!client){catalog=await api('/v1/lab/corpus');renderCorpus();$('publish').disabled=false;await loadIntegrations();}
    if(client){
    try{pending=JSON.parse(sessionStorage.getItem(storageKey()));if(pending&&(!/^\d+\.[0-9a-f-]{36}$/.test(pending.key)||!/^[0-9a-f]{64}$/.test(pending.questionHash)))pending=null;}catch{pending=null;}
    if(pending){try{const old=await api('/v1/requests/resolve',{method:'POST',headers:{'Idempotency-Key':pending.key},body:'{}'});selectedId=old.request_id;clearIntent();message('Recibo da consulta anterior recuperado.');}catch(e){if(e.message==='SESSION_CHANGED')throw e;message('Existe uma consulta sem confirmação. Reintroduza a mesma pergunta para repetir sem duplicar.');$('retry').hidden=false;}}
    for(const id of ['send','refresh','find'])$(id).disabled=false;
    await refresh();
    }
    $('connection').textContent='Demonstração local · sem senha';
    $('active-profile').textContent=profiles[profile].name+' · Aurora';
    $('profile-scope').textContent=client?'Cliente · somente consulta':'Operador · publica a base que Ana consulta';
    if(scope===epoch){
      $('login-view').hidden=true;$('app-shell').hidden=false;$('login-message').textContent='';
      navigate(client?'consultar':'documentos');if(client)timer=setInterval(refresh,3000);
      else feedTimer=setInterval(()=>{if(activeView()==='integracoes'&&!document.hidden)loadIntegrations();},10000);
    }
  }catch(e){if(scope===epoch){logout(false);$('login-message').textContent='Não foi possível abrir o perfil. Tente novamente.';}}
}
async function prepareAndSend(){
  if(busy||activeRole!=='client')return;const scope=epoch,question=$('question').value;
  if(!token||!question.trim()||question.length>4000){message('Informe uma pergunta de 1 a 4.000 caracteres.',true);return;}
  busy=true;$('send').disabled=true;
  try{
    const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(question));if(scope!==epoch)return;
    const questionHash=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
    if(pending&&pending.questionHash!==questionHash)throw new Error('Reintroduza a pergunta original para resolver a consulta pendente.');
    if(!pending)pending={key:`${Math.floor(Date.now()/1000)}.${crypto.randomUUID()}`,questionHash};
    sessionStorage.setItem(storageKey(),JSON.stringify({key:pending.key,questionHash}));
    const result=await api('/v1/requests',{method:'POST',headers:{'Idempotency-Key':pending.key},body:JSON.stringify({question})});
    selectedId=result.request_id;clearIntent();message('Pedido registrado. Buscando uma resposta com fonte…');await show(selectedId);await refresh();
  }catch(e){if(e.message!=='SESSION_CHANGED'){message('Consulta sem confirmação: '+e.message,true);$('retry').hidden=!pending;}}
  finally{if(scope===epoch){busy=false;$('send').disabled=!token;}}
}
for(const [label,question] of examples){const button=node('button',label);button.type='button';button.onclick=()=>{$('question').value=question;$('question').focus();};$('examples').append(button);}
$('submit').onsubmit=event=>{event.preventDefault();prepareAndSend();};
for(const button of profileButtons)button.onclick=()=>connect(button.dataset.profile);
$('switch-profile').onclick=()=>logout();
$('publication').onsubmit=async event=>{
  event.preventDefault();if(activeRole!=='operator'||$('publish').disabled)return;
  const scope=epoch;$('publish').disabled=true;$('publication-message').textContent='Validando e indexando localmente…';
  try{
    const document={source_key:$('source-key').value.trim(),title:$('document-title').value.trim(),text:$('document-text').value,media_type:'text/plain',valid_until:null};
    if(catalog.documents.some(d=>d.source_key===document.source_key))throw new Error('Esta chave já existe. Use uma chave nova para não substituir uma fonte.');
    await api('/v1/lab/corpus/documents',{method:'POST',body:JSON.stringify({expected_generation:catalog.generation,document})});
    catalog=await api('/v1/lab/corpus');renderCorpus();$('publication').reset();
    window.EmbeddingView?.clear();$('publication-message').textContent='Publicado. Ana já pode buscar este conteúdo na base da Aurora.';
  }catch(e){if(scope!==epoch)return;$('publication-message').textContent=e.message==='CORPUS_PROMOTION_CONFLICT'?'A base mudou. Atualize os documentos antes de tentar novamente.':e.message;
    try{catalog=await api('/v1/lab/corpus');renderCorpus();}catch{}
  }finally{if(scope===epoch)$('publish').disabled=false;}
};
window.EmbeddingView.configure({request:api,openDocument:id=>{
  const source=$('doc-'+id);if(!source)return;
  navigate('documentos',false);source.open=true;source.querySelector('summary').focus();
  source.scrollIntoView({behavior:'smooth',block:'center'});
}});
$('retry').onclick=()=>pending&&prepareAndSend();$('refresh').onclick=refresh;
$('lookup').onsubmit=event=>{event.preventDefault();show($('request-id').value.trim(),true);};
logout(false);
async function loadIntegrations(){
  if(activeRole!=='operator'||feedLoading)return;
  const scope=epoch;feedLoading=true;
  try{
    const data=await api('/v1/lab/integrations/feed');
    if(data.mode!=='lab'||data.verification!=='live_remote_mcp'||!Array.isArray(data.providers)||data.providers.length!==2||new Set(data.providers.map(p=>p.id)).size!==2)throw new Error('INTEGRATION_STATUS_INVALID');
    const descriptors={
      jira:{name:'Jira / Atlassian',url:'https://rag-local-lab-mateus.atlassian.net/jira/software/projects/KAN/boards/2',link:'Abrir o Jira do laboratório',current:'Adaptador MCP + ferramenta ADK em laboratório separado. Não faz parte do workflow de respostas.',missing:'Resolver a leitura autenticada e depois integrar o gateway ao RAG. As últimas provas registradas de leitura falharam com 401; esta tela não repete o teste online.'},
      github:{name:'GitHub',url:'https://github.com/Cabraiz/rag-mcp-lab',link:'Abrir GitHub privado do laboratório',current:'Adaptador MCP de leitura disponível e credencial configurada no backend isolado. Ainda não alimenta a base consultada por Ana.',missing:'Integrar a ingestão autorizada ao RAG. O teste isolado anterior não prova que as respostas já consultam GitHub; esta tela não testa a conexão online.'}
    };
    for(const provider of data.providers)if(!Object.hasOwn(descriptors,provider.id)||!['connected','error','not_checked'].includes(provider.state)||!Array.isArray(provider.items)||provider.items.length>500)throw new Error('INTEGRATION_STATUS_INVALID');
    const signature=JSON.stringify(data.providers);
    $('online-count').textContent=data.providers.filter(p=>p.state==='connected').length+' fonte(s) com leitura MCP validada';
    $('integration-sync-status').textContent='Gateway consulta a cada 60 s. Tela atualiza a cada 10 s enquanto aberta. Não publica automaticamente no RAG.';
    if(signature===feedSignature)return;
    const scrollPositions=Object.fromEntries(Array.from($('integrations').querySelectorAll('.integration-card-body')).map(body=>[body.getAttribute('aria-label'),body.scrollTop]));
    $('integrations').replaceChildren();
    for(const provider of data.providers){
      const description=descriptors[provider.id],card=node('section',undefined,'panel integration-card');
      const header=node('header',undefined,'integration-card-header'),heading=node('h2',description.name);
      heading.id='integration-'+provider.id+'-title';card.setAttribute('aria-labelledby',heading.id);
      const stateText=provider.state==='connected'?(provider.stale?'Leitura anterior · desatualizada':'MCP conectado · somente leitura'):provider.state==='error'?'Conexão bloqueada':'Aguardando primeira leitura';
      header.append(node('span',stateText,'badge'),heading);
      const body=node('div',undefined,'integration-card-body');body.tabIndex=0;body.setAttribute('role','region');body.setAttribute('aria-label','Detalhes '+description.name);
      body.append(node('p',provider.id==='jira'?'Kanban KAN · RAG Local Lab':'PRs de Cabraiz/rag-mcp-lab · aberto, fechado ou mergeado','integration-description'));
      const stamp=provider.last_success?new Date(provider.last_success).toLocaleString('pt-BR'):'Ainda não houve leitura bem-sucedida';
      body.append(node('p','Última leitura: '+stamp,'note'));
      if(provider.state==='error'){
        const reasons={REMOTE_401:'Credencial recusada (401). Revalidar o token Jira no backend.',REMOTE_403:'Permissão insuficiente (403). Verifique o acesso de leitura autorizado.',REMOTE_429:'Limite do provedor. A próxima tentativa respeita o intervalo.',TOKEN_LOCAL_EXPIRY:'A credencial expirou. Renove privadamente no backend.'};
        body.append(node('p',reasons[provider.reason]||'Falha de leitura: '+(provider.reason||'MCP_FAILURE'),'error'));
        body.append(node('p','Nenhum dado antigo é apresentado como atualizado. Acesso negado pausa a consulta automática por uma hora; Atualizar solicita uma nova tentativa.','note'));
      }else if(provider.state==='connected'){
        if(!provider.complete)body.append(node('p','Cobertura parcial: limite de 500 itens atingido. Não representa todo o histórico.','error'));
        if(!provider.items.length)body.append(node('p','Leitura autenticada concluída. Nenhum '+(provider.id==='jira'?'card':'PR')+' encontrado nesta fonte.','note'));
        const rank={'To Do':0,'Tarefas pendentes':0,'In Progress':1,'Em andamento':1,'In Review':2,'Em revisão':2,'Done':3,'Concluído':3};
        const groups=provider.id==='jira'?Array.from(new Set(provider.items.map(i=>i.status))).sort((a,b)=>(rank[a]??4)-(rank[b]??4)||a.localeCompare(b)):['open','Draft','closed','Merged'];
        for(const item of provider.items)if(!groups.includes(item.status))groups.push(item.status);
        const board=node('div',undefined,'remote-board');
        for(const status of groups){const rows=provider.items.filter(i=>i.status===status);const column=node('section',undefined,'remote-column');column.append(node('h3',status+' · '+rows.length));
          for(const item of rows){const url=provider.id==='jira'?'https://rag-local-lab-mateus.atlassian.net/browse/'+item.id:'https://github.com/Cabraiz/rag-mcp-lab/pull/'+item.id;
            if(!/^(?:KAN-[1-9][0-9]{0,9}|[1-9][0-9]{0,9})$/.test(item.id)||item.url!==url||typeof item.title!=='string')throw new Error('INTEGRATION_ITEM_INVALID');
            const entry=node('article',undefined,'remote-item'),link=node('a',(provider.id==='github'?'PR #':'')+item.id+' · '+item.title);link.href=url;link.target='_blank';link.rel='noopener noreferrer';entry.append(link,node('small',item.updated_at?'Atualizado '+new Date(item.updated_at).toLocaleString('pt-BR'):'Data de atualização não informada pela fonte'));column.append(entry);
          }board.append(column);
        }body.append(board);
      }
      const footer=node('footer',undefined,'integration-card-footer');
      const link=node('a',description.link,'action-link secondary');link.href=description.url;link.target='_blank';link.rel='noopener noreferrer';footer.append(link);
      footer.append(node('p','Somente leitura · dados externos não são instruções nem regras aprovadas para Ana.','note'));
      card.append(header,body,footer);$('integrations').append(card);
      body.scrollTop=scrollPositions[body.getAttribute('aria-label')]||0;
    }
    feedSignature=signature;
  }catch(e){if(scope!==epoch)return;feedSignature=null;$('online-count').textContent='Estado online não verificado';$('integrations').replaceChildren(node('section','Gateway MCP indisponível. Nenhuma leitura foi confirmada.','panel'));}
  finally{if(scope===epoch)feedLoading=false;}
}
$('integration-refresh').onclick=async()=>{
  if(activeRole!=='operator')return;const scope=epoch;$('integration-refresh').disabled=true;
  try{const result=await api('/v1/lab/integrations/refresh',{method:'POST',body:'{}'});$('integration-sync-status').textContent=result.queued?'Atualização solicitada. Os dados aparecerão após a leitura MCP.':'Aguarde 15 segundos antes de solicitar outra atualização.';}
  catch(e){if(scope===epoch)$('integration-sync-status').textContent='Não foi possível solicitar a atualização.';}
  finally{if(scope===epoch)$('integration-refresh').disabled=false;}
};
