'use strict';
const $ = id => document.getElementById(id);
const state = {slug:'', board:null, revision:'', film:'A', selected:null, tab:'shot', dirty:false, loading:0};
const labels = {planned:'Por preparar',working:'En trabajo',review:'Para revisar',approved:'Elegida'};
function el(tag, text, cls) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function message(text, error=false) {$('notice').textContent=text;$('notice').className=error?'error':'';$('notice').hidden=!text;}
function dirty() {state.dirty=true;$('save-state').textContent='Cambios sin guardar';}
function allowNavigation(){return !state.dirty || window.confirm('Hay cambios sin guardar. ¿Querés dejarlos y cambiar de vista?');}
async function api(path, body, method='GET') {
 const options={method};if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
 const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
}
const base=()=>`/api/project/${encodeURIComponent(state.slug)}/studio`;
const media=path=>`/api/project/${encodeURIComponent(state.slug)}/media/${path.split('/').map(encodeURIComponent).join('/')}`;
const shots=()=>state.board.scenes.filter(s=>(s.video||'A')===state.film);
const shot=()=>state.board.scenes.find(s=>s.id===state.selected);
function field(label, input){const l=el('label',undefined,'field');l.append(el('span',label),input);return l;}
function input(id,value,type='text'){const n=el('input');n.id=id;n.type=type;n.value=value??'';if(type==='number'){n.min='0';n.step='0.05';}if(type!=='file')n.addEventListener('input',dirty);return n;}
function area(id,value,large=false){const n=el('textarea');n.id=id;n.value=value||'';if(large)n.className='large';n.addEventListener('input',dirty);return n;}
function select(id,options,value){const n=el('select');n.id=id;for(const [val,label] of options){const o=el('option',label);o.value=val;n.append(o);}n.value=value??'';n.addEventListener('change',dirty);return n;}
function button(text,fn,cls){const n=el('button',text,cls);n.type='button';n.onclick=()=>guard(fn);return n;}
async function guard(fn){try{await fn();}catch(e){message(e.message,true);}}
async function load(slug, preserve=true){
 const token=++state.loading;$('save-state').textContent='Abriendo…';
 const data=await api(`/api/project/${encodeURIComponent(slug)}/studio`);
 if(token!==state.loading)return;
 state.slug=slug;state.board=data.storyboard;state.revision=data.revision;state.dirty=false;
 if(!preserve||!shot()||(shot().video||'A')!==state.film)state.selected=shots()[0]?.id;
 $('project').value=slug;$('project-title').textContent=state.board.title||slug;$('host').textContent=`● ${state.board.host_body||'Cuerpo por elegir'}`;
 $('save-state').textContent='Guardado';$('montage').textContent=state.board.montage_direction||'';
 const count=Object.values(state.board.continuity?.characters||{}).filter(c=>c.versions.some(v=>v.status==='accepted')).length;
 $('reference-summary').textContent=`${count} personajes con referencia elegida · ${state.board.scenes.length} tomas`;
 history.replaceState(null,'',`/studio?project=${encodeURIComponent(slug)}&film=${state.film}`);render();
}
function render(){
 for(const film of ['A','B']){$('film-'+film).classList.toggle('active',film===state.film);$('film-'+film).textContent=state.board.film_titles?.[film]||'Video '+film;}
 $('shot-count').textContent=shots().length;const rail=$('shots');rail.replaceChildren();let block='';
 for(const s of shots()){
  if(s.block!==block){block=s.block;const entry=state.board.edit_blocks?.[state.film]?.find(b=>b[0]===block);rail.append(el('div',entry?`${block} · ${entry[1]}`:block,'block-label'));}
  const n=button('',()=>chooseShot(s.id),'shot-item'+(s.id===state.selected?' active':''));n.dataset.sceneId=s.id;
  const h=el('span',undefined,'shot-item-head');h.append(el('strong',s.shot_id||s.id),el('small',s.edit_duration_secs?`${s.edit_duration_secs} s`:'Sin duración'));
  n.append(h,el('span',s.framing||s.text?.slice(0,75),'shot-description'));rail.append(n);
 }
 const sequence=$('sequence');sequence.replaceChildren();for(const s of shots().filter(s=>s.included!==false)){const n=button(s.shot_id||s.id,()=>chooseShot(s.id),s.id===state.selected?'active':'');n.title=s.text;sequence.append(n);}
 const included=shots().filter(s=>s.included!==false);const defined=included.filter(s=>s.edit_duration_secs);const total=defined.reduce((n,s)=>n+s.edit_duration_secs,0);
 $('edit-summary').textContent=`${total.toFixed(1)} s definidos · ${included.length-defined.length} tomas sin tiempo`;
 const s=shot();if(!s){$('preview').replaceChildren(el('p','Este proyecto todavía no tiene tomas.'));$('panel').replaceChildren();return;}
 $('shot-label').textContent=`${s.shot_id||s.id} / ${s.block||'Escena'} · ${s.location||''}`;
 $('framing-preview').textContent=s.framing||'';$('action-preview').textContent=s.text||'';$('shot-review').textContent=labels[s.review_status]||'Por preparar';
 const preview=$('preview');preview.replaceChildren();const path=s.selected_asset;
 if(path){const video=/\.(mp4|webm|mov)$/i.test(path);const m=el(video?'video':'img');m.src=media(path);if(video){m.controls=true;m.muted=true;m.onloadedmetadata=()=>{m.currentTime=s.source_trim_in||0;};m.ontimeupdate=()=>{if(s.edit_duration_secs && m.currentTime>(s.source_trim_in||0)+s.edit_duration_secs)m.pause();};}else{m.alt=s.text;}preview.append(m);}
 else{const n=el('div',undefined,'empty-frame');n.append(el('span','Referencia por construir'),el('div',s.shot_id||s.id,'frame-id'),el('p',s.still||s.framing||'La toma todavía no tiene una imagen o clip elegido.'));preview.append(n);}
 const pos=shots().findIndex(x=>x.id===s.id);$('move-up').disabled=pos<=0;$('move-down').disabled=pos===shots().length-1;renderPanel();
}
function chooseShot(id){if(!allowNavigation())return;state.selected=id;state.dirty=false;$('save-state').textContent='Guardado';render();}
function renderPanel(){for(const n of document.querySelectorAll('[data-tab]')){const active=n.dataset.tab===state.tab;n.classList.toggle('active',active);n.setAttribute('aria-selected',String(active));}const p=$('panel');p.replaceChildren();if(state.tab==='shot')renderShot(p);else if(state.tab==='music')renderMusic(p);else if(state.tab==='export')renderExport(p);else if(state.tab==='generate')renderGeneration(p);else renderReferences(p,state.tab);}
function renderShot(p){
 const s=shot();const include=input('included',null,'checkbox');include.checked=s.included!==false;p.append(field('Usar esta toma en el montaje',include),field('Acción / relato',area('action',s.text)),field('Encuadre',input('framing',s.framing)));
 const row=el('div',undefined,'row');row.append(field('Duración de uso (s)',input('duration',s.edit_duration_secs,'number')),field('Entrada en clip fuente (s)',input('source-in',s.source_trim_in||0,'number')));p.append(row,el('p','Los tiempos quedan abiertos hasta elegir el corte. Detalles 0,5–1,5 s; acciones 1,5–3 s.','note'));
 const assets=[...new Set([s.selected_asset,...(s.takes||[]).map(a=>a.path),...(s.asset_timeline||[]).map(a=>a.path)].filter(Boolean))];
 p.append(field('Imagen o clip elegido',select('selected-asset',[['','Sin material elegido'],...assets.map(a=>[a,a])],s.selected_asset||'')));
 const takeFile=input('take-upload',null,'file');takeFile.accept='image/png,image/jpeg,image/webp,video/mp4,video/webm,video/quicktime';takeFile.onchange=()=>guard(async()=>{const f=takeFile.files[0];if(!f)return;if(!allowNavigation())return;const form=new FormData();form.append('file',f);form.append('type',f.type.startsWith('video/')?'video':'still');const r=await fetch(`/api/project/${encodeURIComponent(state.slug)}/storyboard/${s.id}/add-version`,{method:'POST',body:form});const d=await r.json();if(!r.ok)throw Error(d.detail||'No se pudo subir la toma');await load(state.slug);message('Material agregado. Elegí la toma y su duración antes de guardar.');});
 p.append(field('Agregar material local a esta toma',takeFile),field('Estado de revisión',select('review',Object.entries(labels),s.review_status||'planned')),field('Dirección de imagen',area('still-prompt',s.visual_direction,true)),field('Movimiento del video',area('video-prompt',s.video_prompt_en)));
 const continuity=el('details');continuity.append(el('summary','Continuidad de esta toma'),el('p',s.continuity||'Sin notas de continuidad.','note'));p.append(continuity,el('h3','Versiones fijadas en esta toma','section-label'));
 for(const [key,version] of Object.entries(s.reference_versions||{})){const item=state.board.continuity?.characters?.[key]||state.board.continuity?.locations?.[key];if(item)p.append(field(item.name,select('pin-'+key,item.versions.map(v=>[v.id,`${v.id} · ${v.status==='accepted'?'Elegida':'Borrador'}`]),version)));}
 p.append(el('h3','Diálogo opcional','section-label'),el('p','Este proyecto usa las canciones, casi sin diálogo añadido.','note'));
 const dialogues=el('div');dialogues.id='dialogues';p.append(dialogues);for(const d of s.dialogue||[])addDialogue(dialogues,d);
 p.append(button('+ Agregar intervención',()=>{addDialogue(dialogues,{});dirty();},'small-action'),field('Notas de producción',area('production-notes',s.production_notes)),button('Guardar toma',saveShot,'primary'),button('+ Nueva cobertura de este momento',async()=>{if(!allowNavigation())return;const result=await api(`${base()}/shot/${s.id}/coverage`,{revision:state.revision},'POST');state.selected=result.scene_id;await load(state.slug);message('Cobertura creada. Elegí su encuadre y acción; la toma original se conserva.');},'secondary'));
}
function addDialogue(container,d){const item=el('div',undefined,'dialogue-item');item.append(field('Hablante',input('',d.speaker||'')),field('Texto',area('',d.text||'')));const row=el('div',undefined,'row');row.append(field('Entrada (s)',input('',d.time_in??0,'number')),field('Salida (s)',input('',d.time_out??1,'number')));item.append(row,button('Quitar',()=>{item.remove();dirty();},'small-action'));container.append(item);}
async function saveShot(){
 const s=shot();const pins={};for(const key of Object.keys(s.reference_versions||{}))pins[key]=$('pin-'+key).value;
 const dialogue=[...$('dialogues').children].map(n=>{const fields=n.querySelectorAll('input,textarea');return{speaker:fields[0].value,text:fields[1].value,time_in:Number(fields[2].value),time_out:Number(fields[3].value)};});
 const body={revision:state.revision,text:$('action').value,framing:$('framing').value,visual_direction:$('still-prompt').value,video_prompt_en:$('video-prompt').value,edit_duration_secs:$('duration').value===''?null:Number($('duration').value),source_trim_in:Number($('source-in').value),selected_asset:$('selected-asset').value||null,review_status:$('review').value,production_notes:$('production-notes').value,reference_versions:pins,dialogue,included:$('included').checked};
 await api(`${base()}/shot/${s.id}`,body,'PUT');await load(state.slug);message('Toma guardada. La versión anterior se conserva.');
}
function renderReferences(p,kind){
 const library=state.board.continuity?.[kind]||{};const keys=Object.keys(library);if(!keys.length){p.append(el('p','Este proyecto no tiene fichas de continuidad.'));return;}
 const relevant=kind==='characters'?shot().characters:[shot().location];let key=relevant?.[0]||keys[0];
 const picker=select('reference-key',keys.map(k=>[k,library[k].name]),key);picker.removeEventListener('change',dirty);picker.onchange=()=>{if(!allowNavigation()){picker.value=key;return;}key=picker.value;state.dirty=false;draw();};p.append(field(kind==='characters'?'Personaje / edad':'Lugar',picker));const body=el('div');p.append(body);
 function draw(){body.replaceChildren();const item=library[key];const last=item.versions.at(-1);const using=state.board.scenes.filter(s=>key in (s.reference_versions||{}));body.append(el('p',`${using.length} tomas usan esta ficha. Crear una versión conserva las anteriores; cada toma sigue usando la versión que eligió.`,'note'));
  for(const v of item.versions){const card=el('div',undefined,'version-card');card.append(el('strong',v.id+' · '),el('span',v.status==='accepted'?'Referencia elegida':'Borrador / por generar','reference-status'));const imgs=el('div',undefined,'image-pair');for(const path of v.image_paths||[]){const img=el('img');img.src=media(path);img.alt=item.name+' '+v.id;imgs.append(img);}card.append(imgs,el('p',v.appearance||v.es||v.notes));body.append(card);}
  body.append(el('h3','Preparar una nueva versión','section-label'),field('Identidad / descripción',area('ref-appearance',last.appearance||last.es||'',true)),field('Vestuario / invariantes',area('ref-outfit',last.outfit||'')),field('Descripción para generación',area('ref-en',last.en||'')),field('Notas de mejora',area('ref-notes','')));
  let images=[...(last.image_paths||[])];const list=el('p',images.length?images.join('\n'):'Todavía no hay imágenes.','upload-list');const upload=input('reference-upload',null,'file');upload.accept='image/png,image/jpeg,image/webp';upload.onchange=()=>guard(async()=>{const file=upload.files[0];if(!file)return;const f=new FormData();f.append('file',file);const r=await fetch(`${base()}/reference-upload`,{method:'POST',body:f});const d=await r.json();if(!r.ok)throw Error(d.detail||'No se pudo subir');images.push(d.path);list.textContent=images.join('\n');dirty();});
  body.append(field('Agregar imagen a la versión',upload),list,button('Vaciar imágenes de esta nueva versión',()=>{images=[];list.textContent='Sin imágenes en la nueva versión.';dirty();},'small-action'),field('Estado de la nueva versión',select('ref-status',[['draft','Borrador'],['accepted','Elegida para usar']], 'draft')),button('Guardar nueva versión',async()=>{const result=await api(`${base()}/references/${kind}/${encodeURIComponent(key)}/versions`,{revision:state.revision,appearance:$('ref-appearance').value,outfit:$('ref-outfit').value,en:$('ref-en').value,notes:$('ref-notes').value,image_paths:images,status:$('ref-status').value},'POST');await load(state.slug);message(`Versión ${result.version_id} creada. Las tomas conservan sus versiones anteriores; elegí la nueva en cada toma que quieras mejorar.`);},'primary'));
 }
 draw();
}
function renderMusic(p){
 p.append(el('p','Escuchá las fuentes y elegí cortes reales. La música marca el tiempo; las imágenes se montan sobre ella.','note'));
 const sources=state.board.music_sources||[];for(const source of sources){const card=el('div',undefined,'source-card');const player=el('audio');player.controls=true;player.preload='metadata';player.src=media(source.path);card.append(el('strong',source.path),el('small',`${source.duration_secs.toFixed(2)} s · original conservado`),player);p.append(card);}
 p.append(el('h3',`Cortes de audio · Video ${state.film}`,'section-label'));const segments=el('div');segments.id='sound-segments';p.append(segments);
 function add(s={}){const n=el('div',undefined,'soundtrack-row');n.append(select('',sources.map(s=>[s.path,s.path]),s.path||sources[0]?.path));const row=el('div',undefined,'row');row.append(field('Entrada fuente (s)',input('',s.source_in??'','number')),field('Salida fuente (s)',input('',s.source_out??'','number')));n.append(row,field('Estrofa / motivo del corte',input('',s.notes||'')),button('Quitar corte',()=>{n.remove();dirty();},'small-action'));segments.append(n);}
 for(const s of state.board.soundtracks?.[state.film]||[])add(s);
 p.append(button('+ Agregar corte',()=>{add();dirty();},'small-action'),button('Guardar edición de audio',async()=>{const parts=[...segments.children].map(n=>{const inputs=n.querySelectorAll('input');if(inputs[0].value===''||inputs[1].value==='')throw Error('Completá entrada y salida con tiempos de la fuente.');return{path:n.querySelector('select').value,source_in:Number(inputs[0].value),source_out:Number(inputs[1].value),notes:inputs[2].value};});await api(`${base()}/soundtrack/${state.film}`,{revision:state.revision,segments:parts},'PUT');await load(state.slug);message('Cortes guardados. Los archivos originales siguen intactos.');},'primary'));
 p.append(button('Escuchar montaje guardado',async()=>{if(state.dirty)throw Error('Guardá los cortes antes de escuchar el montaje.');message('Preparando el audio local…');const result=await api(`${base()}/soundtrack/${state.film}/preview`,{revision:state.revision},'POST');const player=el('audio');player.controls=true;player.src=media(result.path);player.style.width='100%';p.append(player);message('Montaje preparado. Escuchá las uniones antes de elegirlo.');},'secondary'));
 const blocks=state.board.edit_blocks?.[state.film]||[];p.append(el('h3','Mapa narrativo de la música','section-label'));for(const b of blocks){const n=el('div',undefined,'version-card');n.append(el('strong',b[0]+' · '+b[1]),el('p',b[2]),el('small',b[3]));p.append(n);}
}
function renderExport(p){
 p.append(el('h3',`Salida · Video ${state.film}`,'section-label'),el('p','Se exporta únicamente este video con su música editada. Guardá cada toma y hacé coincidir el tiempo de imagen con el de la canción; el estudio no estira el audio ni rellena huecos.','note'));
 const included=shots().filter(s=>s.included!==false);const ready=included.filter(s=>s.selected_asset&&s.edit_duration_secs);const segments=state.board.soundtracks?.[state.film]||[];
 const music=segments.reduce((n,s)=>n+s.source_out-s.source_in,0);const picture=ready.reduce((n,s)=>n+s.edit_duration_secs,0);
 p.append(el('p',`${ready.length} / ${included.length} tomas con material y tiempo`, 'note'),el('p',`Imagen ${picture.toFixed(2)} s · Música ${music.toFixed(2)} s`,'note'));
 const quality=select('export-quality',[['draft','Borrador · 640 × 360'],['review','Revisión · 1280 × 720'],['full','Full HD · 1920 × 1080']],'review');quality.removeEventListener('change',dirty);p.append(field('Calidad de la copia',quality));
 const status=el('div');status.id='export-status';p.append(button(`Exportar video ${state.film}`,async()=>{
  if(state.dirty)throw Error('Guardá los cambios antes de exportar.');
  const preset=quality.value==='draft'?{width:640,height:360,crf:28,preset:'ultrafast'}:quality.value==='full'?{width:1920,height:1080,crf:20,preset:'medium'}:{width:1280,height:720,crf:23,preset:'veryfast'};
  const result=await api(`${base()}/film/${state.film}/export`,{revision:state.revision,...preset},'POST');
  sessionStorage.setItem(`video-export:${state.slug}:${state.film}`,result.job_id);await watchExport(result.job_id,status);
 },'primary'),status);
 const previous=sessionStorage.getItem(`video-export:${state.slug}:${state.film}`);
 if(previous)guard(()=>watchExport(previous,status));
}
async function watchExport(jobId,container){
 const slug=state.slug;
 while(container.isConnected&&state.slug===slug){
  const data=await api(`/api/project/${encodeURIComponent(slug)}/studio/exports/${jobId}`);container.replaceChildren();
  if(data.status==='completed'){
   container.append(el('h3','Copia lista para revisar','section-label'));
   const player=el('video');player.controls=true;player.preload='metadata';player.src=media(data.output);player.style.width='100%';container.append(player);
   const link=el('a','Descargar MP4');link.href=media(data.output);link.download='';container.append(link,el('p','Falta ver y escuchar la copia antes de aprobar el video.','note'));return;
  }
  if(['failed','interrupted'].includes(data.status)){
   container.append(el('p',data.error||'El render quedó interrumpido.','note'),button('Retomar este render',async()=>{await api(`/api/project/${encodeURIComponent(slug)}/studio/exports/${jobId}/resume`,{},'POST');await watchExport(jobId,container);},'secondary'));return;
  }
  container.append(el('p',`Render en curso · ${(data.completed_shots||[]).length} tomas terminadas`,'note'));
  await new Promise(resolve=>setTimeout(resolve,1000));
 }
}
async function move(direction){if(!allowNavigation())return;const list=[...state.board.scenes];const i=list.findIndex(s=>s.id===state.selected);const j=i+direction;if(!list[j]||(list[j].video||'A')!==state.film)return;[list[i],list[j]]=[list[j],list[i]];await api(`${base()}/order`,{revision:state.revision,scene_ids:list.map(s=>s.id)},'PUT');await load(state.slug);}
for(const film of ['A','B'])$('film-'+film).onclick=()=>guard(async()=>{if(!allowNavigation())return;state.film=film;await load(state.slug,false);});
for(const n of document.querySelectorAll('[data-tab]'))n.onclick=()=>{if(!allowNavigation())return;state.tab=n.dataset.tab;state.dirty=false;$('save-state').textContent='Guardado';renderPanel();};
$('move-up').onclick=()=>guard(()=>move(-1));$('move-down').onclick=()=>guard(()=>move(1));
$('project').onchange=()=>guard(async()=>{if(!allowNavigation()){$('project').value=state.slug;return;}await load($('project').value,false);});
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
guard(async()=>{const projects=await api('/api/projects');$('project').replaceChildren(...projects.map(name=>{const n=el('option',name);n.value=name;return n;}));const params=new URLSearchParams(location.search);state.film=params.get('film')==='B'?'B':'A';const target=params.get('project')||projects[0];if(target)await load(target,false);else message('Creá un proyecto desde el editor de capas.');});

function generationField(node){node.removeEventListener('input',dirty);node.removeEventListener('change',dirty);return node;}
function renderGeneration(p){
 p.append(el('h3','Preparar una generación','section-label'),el('p','Revisá el pedido, las referencias y su costo. Preparar conserva el plan sin enviarlo; cada generación necesita autorización humana.','note'));
 const targets=[[JSON.stringify({type:'shot',scene_id:shot().id}),`Toma ${shot().shot_id||shot().id}`]];
 for(const kind of ['characters','locations'])for(const [key,item] of Object.entries(state.board.continuity?.[kind]||{})){
  const version=item.versions.at(-1);targets.push([JSON.stringify({type:'reference',kind,key,version:version.id}),item.name+' · nueva imagen desde '+version.id]);
 }
 const target=generationField(select('gen-target',targets,targets[1]?.[0]||targets[0][0]));
 const type=generationField(select('gen-type',[['image','Imagen / referencia'],['video','Clip de video']],'image'));
 const model=generationField(select('gen-model',[['grok-imagine-image-2.0','Imagine Image 2.0 · 1K, calidad low'],['grok-imagine-video','Imagine Video · 720p'],['grok-imagine-video-1.5','Imagine Video 1.5 · 720p']],'grok-imagine-image-2.0'));
 const prompt=generationField(area('gen-prompt','',true));const duration=generationField(input('gen-duration',3,'number'));duration.step='1';duration.min='1';duration.max='15';
 const variant=generationField(input('gen-variant','main'));
 const aspect=generationField(select('gen-aspect',[['16:9','Horizontal · 16:9'],['3:4','Retrato · 3:4'],['1:1','Cuadrado · 1:1'],['9:16','Vertical · 9:16']],'16:9'));
 const refs=el('div');refs.id='gen-inputs';
 function update(){const t=JSON.parse(target.value);const isReference=t.type==='reference';if(isReference){type.value='image';model.value='grok-imagine-image-2.0';}type.disabled=isReference;
  if(type.value==='image'){model.value='grok-imagine-image-2.0';duration.disabled=true;}else{if(model.value==='grok-imagine-image-2.0')model.value='grok-imagine-video';duration.disabled=false;}
  const specification=isReference?state.board.continuity[t.kind][t.key].versions.find(v=>v.id===t.version):shot();
  prompt.value=isReference?specification.en||specification.appearance||specification.es||'':type.value==='video'?[specification.visual_direction,specification.video_prompt_en,'No dialogue. No music.'].filter(Boolean).join('\n'):specification.visual_direction||'';
  refs.replaceChildren();for(const kind of ['characters','locations'])for(const [key,item] of Object.entries(state.board.continuity?.[kind]||{}))for(const v of item.versions){if(v.status!=='accepted'||!v.image_paths?.length)continue;
   const checkbox=el('input');checkbox.type='checkbox';checkbox.value=JSON.stringify({kind,key,version:v.id});checkbox.checked=!isReference&&shot().reference_versions?.[key]===v.id;refs.append(field(`${item.name} · ${v.id} · ${v.image_paths.length} imágenes`,checkbox));
  }
  if(!refs.children.length)refs.append(el('p','Todavía no hay referencias visuales elegidas. Empezá por un personaje o lugar y revisá su imagen antes de generar las tomas.','note'));
 }
 target.onchange=update;type.onchange=update;model.onchange=()=>{type.value=model.value==='grok-imagine-image-2.0'?'image':'video';duration.disabled=type.value==='image';};
 p.append(field('Destino del resultado',target),field('Tipo de material',type),field('Modelo y calidad',model),field('Prompt que se enviará',prompt),field('Duración del clip fuente (s)',duration),field('Variante de esta prueba',variant),field('Formato',aspect),el('h3','Referencias a enviar','section-label'),refs);
 p.append(button('Preparar pedido y ver costo',async()=>{
  if(state.dirty)throw Error('Guardá los cambios del proyecto antes de preparar el pedido.');
  const result=await api(`${base()}/generations/plan`,{revision:state.revision,target:JSON.parse(target.value),media_type:type.value,model:model.value,prompt:prompt.value,duration:Number(duration.value),variant:variant.value,aspect_ratio:aspect.value,input_references:[...refs.querySelectorAll('input:checked')].map(n=>JSON.parse(n.value))},'POST');
  await refreshGenerationJobs(list);message(`Pedido preparado · USD ${result.plan.quote.estimated_cost_usd}. Todavía no fue enviado.`);
 },'primary'));
 const list=el('div');list.id='generation-jobs';p.append(el('h3','Pedidos guardados','section-label'),button('Actualizar estado local',()=>refreshGenerationJobs(list),'secondary'),list);update();guard(()=>refreshGenerationJobs(list));
}
async function refreshGenerationJobs(container){
 const slug=state.slug;const records=await api(`${base()}/generations`);if(!container.isConnected||state.slug!==slug)return;container.replaceChildren();
 const names={withdrawn:'Retirado antes del envío',awaiting_approval:'Falta autorización',approved:'Autorizado, pendiente de envío',submitting:'Enviando',submission_unknown:'Envío incierto · revisar antes de continuar',pending:'Pedido guardado · consultar al proveedor',completed:'Candidato descargado',provider_failed:'El proveedor informó un fallo',download_failed:'Descarga pendiente de recuperar',rejected:'Pedido rechazado por el proveedor'};
 for(const job of records.reverse()){
  const card=el('div',undefined,'version-card');card.dataset.generationJob=job.job_id;const plan=job.plan;
  card.append(el('strong',names[job.status]||job.status),el('p',`${plan.model} · ${plan.parameters.duration?plan.parameters.duration+' s · ':''}USD ${plan.quote.estimated_cost_usd}`),el('small',job.job_id));
  const details=el('details');details.append(el('summary','Ver pedido y costo'),el('p',plan.prompt),el('p',`${plan.quote.input_images} referencias · ${plan.quote.outputs} resultado · un intento. Estimación de tarifa API; crédito OAuth no comprobado; impuestos aparte.`,'note'));
  const pricing=el('a','Tarifa y fecha de consulta');pricing.href=plan.quote.pricing_url;pricing.target='_blank';pricing.rel='noopener';details.append(pricing,el('small',plan.quote.pricing_checked));
  for(const r of plan.input_references){const img=el('img');img.src=media(r.path);img.alt=r.name+' '+r.version;img.style.maxWidth='110px';details.append(img);}
  card.append(details);if(job.error)card.append(el('p',job.error,'note'));
  if(job.status==='awaiting_approval')card.append(button(`Autorizar USD ${plan.quote.estimated_cost_usd} y generar`,async()=>{
   if(!window.confirm(`Se enviará este prompt y sus ${plan.quote.input_images} imágenes a xAI para producir un resultado. Costo estimado: USD ${plan.quote.estimated_cost_usd}, impuestos aparte. ¿Autorizás este único intento?`))return;
   await api(`${base()}/generations/${job.job_id}/approve`,{plan_sha256:plan.plan_sha256,max_cost_usd:plan.quote.estimated_cost_usd,statement:`Autorización humana desde la mesa para ${job.job_id}: un intento por USD ${plan.quote.estimated_cost_usd}, con el prompt y referencias mostrados.`},'POST');
   await api(`${base()}/generations/${job.job_id}/run`,{},'POST');await refreshGenerationJobs(container);message('Envío solicitado. Actualizá el estado local; para recuperar se consulta el mismo pedido.');
  },'primary'));
  else if(job.status==='approved'||job.remote_request_id)card.append(button(job.status==='approved'?'Enviar pedido autorizado':'Consultar / recuperar este pedido',async()=>{await api(`${base()}/generations/${job.job_id}/run`,{},'POST');await refreshGenerationJobs(container);message('Consulta iniciada. Actualizá el estado local en unos segundos.');},'secondary'));
  if(job.status==='submission_unknown')card.append(el('p','No se vuelve a enviar automáticamente. Revisá el pedido en la cuenta del proveedor para evitar un cobro duplicado.','note'));
  if(job.status==='completed'&&job.output){const m=el(plan.media_type==='video'?'video':'img');m.src=media(job.output);m.style.maxWidth='100%';if(plan.media_type==='video'){m.controls=true;m.muted=true;}else m.alt='Candidato generado pendiente de revisión';card.append(m,button('Agregar candidato a la mesa',async()=>{if(state.dirty)throw Error('Guardá los cambios antes de agregar el candidato.');await api(`${base()}/generations/${job.job_id}/register`,{revision:state.revision},'POST');await load(state.slug);message('Candidato agregado. Revisalo y elegí su versión o toma; las selecciones anteriores se conservan.');},'primary'));}
  if(['awaiting_approval','approved'].includes(job.status))card.append(button('Retirar pedido sin enviar',async()=>{await api(`${base()}/generations/${job.job_id}/withdraw`,{},'POST');await refreshGenerationJobs(container);},'small-action'));
  container.append(card);
 }
 if(!records.length)container.append(el('p','No hay pedidos preparados todavía.','note'));
}
