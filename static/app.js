if(new URL(window.location.href).searchParams.has('token')){history.replaceState(null,'',window.location.pathname);}
let job=null,pages=1,current=1,currentFilename='',outputNameTouched=false,currentStats=null,currentFileSize=0,lastStatusKey='',lastStatusError=false,lastExportStatusKey='';
const $=id=>document.getElementById(id),drop=$('drop'),file=$('file');
const translations={};
async function loadTranslations(lang){
  if(translations[lang])return translations[lang];
  const response=await fetch(`/api/i18n/${lang}`,{cache:'no-store'});
  if(!response.ok)throw new Error(`Unable to load locale: ${lang}`);
  translations[lang]=await response.json();
  return translations[lang];
}
let language=localStorage.getItem('pdf-unredact-language');
if(!['it','en'].includes(language))language=(navigator.language||'en').toLowerCase().startsWith('it')?'it':'en';
function tr(key){return (translations[language]&&translations[language][key])||(translations.en&&translations.en[key])||key}
async function applyLanguage(next){
  language=['it','en'].includes(next)?next:'en';
  await Promise.all([loadTranslations('en'),loadTranslations(language)]);
  localStorage.setItem('pdf-unredact-language',language);
  document.documentElement.lang=language;
  document.querySelectorAll('[data-i18n]').forEach(el=>{const key=el.dataset.i18n;el.textContent=tr(key)});
  const lb=$('languageButton');lb.textContent=language.toUpperCase();const langLabel=tr('language')+': '+(language==='it'?tr('italian'):tr('english'));lb.title=langLabel;lb.setAttribute('aria-label',langLabel);
  const ib=$('infoButton');ib.title=tr('info');ib.setAttribute('aria-label',tr('info'));const ic=$('infoClose');ic.title=tr('close');ic.setAttribute('aria-label',tr('close'));
  $('prev').title=tr('previous_page');$('prev').setAttribute('aria-label',tr('previous_page'));
  $('next').title=tr('next_page');$('next').setAttribute('aria-label',tr('next_page'));
  updateFullscreenButton();
  $('originalPreview').alt=tr('original_preview');$('cleanPreview').alt=tr('clean_preview');
  updateThemeButton();
  if(currentFilename)$('filemeta').textContent=`${fmt(pages)} ${tr('pages')} · ${human(currentFileSize)}`;
  if(currentStats)renderStats(currentStats);
  if(lastStatusKey)status(tr(lastStatusKey),lastStatusError,false);
  if(lastExportStatusKey)$('exportStatus').textContent=tr(lastExportStatusKey);
}
$('languageButton').addEventListener('click',async()=>{await applyLanguage(language==='it'?'en':'it')});

function updateFullscreenButton(){
  const btn=$('fullscreenPreview');
  if(!btn)return;
  const active=document.fullscreenElement===$('previewCard');
  const key=active?'exit_full_screen':'full_screen';
  const label=tr(key);
  btn.title=label;
  btn.setAttribute('aria-label',label);
  const text=btn.querySelector('span');
  if(text)text.textContent=label;
}
async function togglePreviewFullscreen(){
  const card=$('previewCard');
  try{
    if(document.fullscreenElement===card){await document.exitFullscreen();return}
    if(document.fullscreenElement)await document.exitFullscreen();
    if(card.requestFullscreen)await card.requestFullscreen();
  }catch(err){console.error(err)}
}
document.addEventListener('fullscreenchange',updateFullscreenButton);
function openInfo(){$('infoModal').classList.remove('hidden');$('infoClose').focus()}
function closeInfo(){$('infoModal').classList.add('hidden');$('infoButton').focus()}
$('infoButton').addEventListener('click',openInfo);
$('infoClose').addEventListener('click',closeInfo);
$('infoModal').addEventListener('click',e=>{if(e.target===$('infoModal'))closeInfo()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('infoModal').classList.contains('hidden'))closeInfo()});
const systemTheme=window.matchMedia('(prefers-color-scheme: dark)');
const themeStates=['system','light','dark'];
const themeIcons={system:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>',light:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"/></svg>',dark:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'};
function updateThemeButton(){const btn=$('themeButton');const choice=btn.dataset.themeChoice||'system';btn.innerHTML=themeIcons[choice];const key=choice==='system'?'theme_system':choice==='light'?'theme_light':'theme_dark';const label=tr('theme')+': '+tr(key);btn.setAttribute('aria-label',label);btn.title=label}
function applyTheme(choice){if(!themeStates.includes(choice))choice='system';const resolved=choice==='system'?(systemTheme.matches?'dark':'light'):choice;document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;localStorage.setItem('pdf-unredact-theme',choice);$('themeButton').dataset.themeChoice=choice;updateThemeButton()}
applyTheme(localStorage.getItem('pdf-unredact-theme')||'system');
$('themeButton').addEventListener('click',()=>{const current=$('themeButton').dataset.themeChoice||'system';applyTheme(themeStates[(themeStates.indexOf(current)+1)%themeStates.length])});
if(systemTheme.addEventListener)systemTheme.addEventListener('change',()=>{if(($('themeButton').dataset.themeChoice||'system')==='system')applyTheme('system')});
function status(msg,err=false,remember=true){$('uploadStatus').textContent=msg;$('uploadStatus').className=err?'error':'status';if(remember){lastStatusKey='';lastStatusError=err}}function statusKey(key,err=false){lastStatusKey=key;lastStatusError=err;status(tr(key),err,false)}
function removeMode(){return document.querySelector('input[name="remove"]:checked').value}function outputMode(){return document.querySelector('input[name="mode"]:checked').value}function fmt(n){return new Intl.NumberFormat(language==='it'?'it-IT':'en-US').format(n||0)}function human(n){if(n<1024)return n+' B';if(n<1048576)return(n/1024).toLocaleString(language==='it'?'it-IT':'en-US',{maximumFractionDigits:1})+' KB';return(n/1048576).toLocaleString(language==='it'?'it-IT':'en-US',{maximumFractionDigits:1})+' MB'}
function outputDefault(){const stem=(currentFilename||'document.pdf').replace(/\.pdf$/i,'');return stem+(outputMode()==='clean'?'_clean.pdf':'_side_by_side.pdf')}function syncOutputName(force=false){if(force||!outputNameTouched)$('outputName').value=outputDefault()}
function apiHeaders(extra={}){return Object.assign({'X-Language':language},extra)}
async function upload(f){if(!f)return;if(f.type&&f.type!=='application/pdf'&&!f.name.toLowerCase().endsWith('.pdf')){statusKey('select_pdf',true);return}const oldJob=job;$('uploadProgress').classList.remove('hidden');statusKey('analyzing');$('workspace').classList.add('hidden');try{const r=await fetch('/api/upload',{method:'POST',headers:apiHeaders({'Content-Type':'application/pdf','X-Filename':encodeURIComponent(f.name)}),body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||tr('open_error'));job=d.job_id;if(oldJob&&oldJob!==job)fetch(`/api/job/${oldJob}`,{method:'DELETE',headers:apiHeaders(),keepalive:true}).catch(()=>{});pages=d.pages;current=1;currentFilename=d.filename;currentFileSize=f.size;currentStats=d.stats;outputNameTouched=false;$('filename').textContent=d.filename;$('filemeta').textContent=`${fmt(d.pages)} ${tr('pages')} · ${human(f.size)}`;renderStats(d.stats);$('pagesLabel').textContent=`/ ${pages}`;$('page').max=pages;$('page').value=1;syncOutputName(true);$('jsonReport').href=`/api/report/${job}?lang=${language}`;$('jsonReport').download=currentFilename.replace(/\.pdf$/i,'')+'_audit.json';$('workspace').classList.remove('hidden');statusKey('analysis_complete');await refreshPreview()}catch(e){status(e.message,true)}finally{$('uploadProgress').classList.add('hidden')}}
function renderStats(stats){currentStats=stats;$('detected').textContent=fmt(stats.redaction_boxes_found);$('recoverable').textContent=fmt(stats.recoverable_redactions);$('removable').textContent=fmt(stats.removable_redactions);$('unsupported').textContent=fmt(stats.unsupported_recoverable_redactions);$('applied').textContent=fmt(stats.probable_applied_redactions);$('uncertain').textContent=fmt(stats.uncertain_redactions);renderFindings(stats.findings||[])}
function sourceLabel(source){return source==='annotation:redact'?tr('source_redact'):source==='annotation:highlight'?tr('source_highlight'):source==='annotation:square'?tr('source_square'):source==='dark_rectangle'?tr('source_rectangle'):source}
function removalLabel(x){if(!x.recoverable)return `<span class="chip">${tr('not_applicable')}</span>`;if(x.removal_method==='annotation_delete')return `<span class="chip ok">${tr('removal_annotation')}</span>`;if(x.removal_method==='content_stream_rewrite')return `<span class="chip ok">${tr('removal_stream')}</span>`;return `<span class="chip warn">${tr('not_removable')}</span>`}
function renderFindings(arr){$('findingsSummary').textContent=`${fmt(arr.length)} ${tr('areas_detected')}`;const tbody=$('findings');tbody.innerHTML='';for(const x of arr.slice(0,1000)){const trEl=document.createElement('tr');const st=x.recoverable?`<span class="chip ok">${tr('recoverable')}</span>`:x.probable_applied?`<span class="chip warn">${tr('probably_applied')}</span>`:`<span class="chip">${tr('uncertain')}</span>`;trEl.innerHTML=`<td>${x.page}</td><td>${escapeHtml(sourceLabel(x.source))}</td><td>${st}</td><td>${removalLabel(x)}</td><td>${fmt(x.words_under)}</td><td>${fmt(x.chars_under)}</td>`;tbody.appendChild(trEl)}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refreshPreview(){if(!job)return;current=Math.max(1,Math.min(pages,parseInt($('page').value)||1));$('page').value=current;const rm=encodeURIComponent(removeMode()),t=Date.now();$('originalPreview').src=`/api/preview/${job}/original/${current}?lang=${language}&t=${t}`;$('cleanPreview').src=`/api/preview/${job}/clean/${current}?remove=${rm}&lang=${language}&t=${t}`}
async function refreshAudit(){if(!job)return;const btn=$('refreshAudit');btn.disabled=true;try{const r=await fetch(`/api/audit/${job}`,{method:'POST',headers:apiHeaders()});const d=await r.json();if(!r.ok)throw new Error(d.error||tr('analysis_error'));renderStats(d.stats)}catch(e){lastExportStatusKey='';$('exportStatus').textContent=e.message}finally{btn.disabled=false}}
function requestedPdfName(){let name=$('outputName').value.trim()||outputDefault();if(!/\.pdf$/i.test(name))name+='.pdf';return name}
async function chooseExportDestination(suggestedName){
  if(typeof window.showSaveFilePicker!=='function')return null;
  lastExportStatusKey='choose_save_location';$('exportStatus').textContent=tr('choose_save_location');
  try{return await window.showSaveFilePicker({suggestedName,types:[{description:tr('pdf_file_type'),accept:{'application/pdf':['.pdf']}}],excludeAcceptAllOption:true})}
  catch(e){if(e&&e.name==='AbortError')return false;throw e}
}
async function saveDownloadToHandle(url,handle){
  const response=await fetch(url,{cache:'no-store'});
  if(!response.ok)throw new Error(tr('download_error'));
  const writable=await handle.createWritable();
  try{
    if(response.body&&typeof response.body.pipeTo==='function')await response.body.pipeTo(writable);
    else{await writable.write(await response.blob());await writable.close()}
  }catch(e){try{await writable.abort()}catch(_ignored){}throw e}
}
async function exportPdf(){if(!job)return;const btn=$('export');btn.disabled=true;let saveHandle=null;try{
  let requestedName=requestedPdfName();
  saveHandle=await chooseExportDestination(requestedName);
  if(saveHandle===false){lastExportStatusKey='save_cancelled';$('exportStatus').textContent=tr('save_cancelled');return}
  if(saveHandle&&saveHandle.name)requestedName=saveHandle.name;
  lastExportStatusKey='generating';$('exportStatus').textContent=tr('generating');
  const r=await fetch('/api/export',{method:'POST',headers:apiHeaders({'Content-Type':'application/json'}),body:JSON.stringify({job_id:job,mode:outputMode(),remove:removeMode(),sanitize_active_content:$('sanitizeActiveContent').checked,filename:requestedName})});
  const d=await r.json();if(!r.ok)throw new Error(d.error||tr('export_error'));
  if(saveHandle){await saveDownloadToHandle(d.download_url,saveHandle);lastExportStatusKey='pdf_saved';$('exportStatus').textContent=tr('pdf_saved')}
  else{lastExportStatusKey='download_started';$('exportStatus').textContent=tr('download_started');const link=document.createElement('a');link.href=d.download_url;link.download=d.filename;link.className='hidden';document.body.appendChild(link);link.click();link.remove()}
}catch(e){lastExportStatusKey='';$('exportStatus').textContent=e.message||tr('export_error')}finally{btn.disabled=false}}
drop.onclick=()=>file.click();file.onchange=()=>upload(file.files[0]);['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('drag')}));['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('drag')}));drop.addEventListener('drop',e=>upload(e.dataTransfer.files[0]));$('replace').onclick=()=>file.click();$('prev').onclick=()=>{$('page').value=Math.max(1,current-1);refreshPreview()};$('next').onclick=()=>{$('page').value=Math.min(pages,current+1);refreshPreview()};$('fullscreenPreview').onclick=togglePreviewFullscreen;$('page').onchange=refreshPreview;document.querySelectorAll('input[name="remove"]').forEach(x=>x.onchange=refreshPreview);document.querySelectorAll('input[name="mode"]').forEach(x=>x.onchange=()=>syncOutputName(false));$('outputName').addEventListener('input',()=>{outputNameTouched=true});$('export').onclick=exportPdf;$('refreshAudit').onclick=refreshAudit;
applyLanguage(language).catch(err=>{console.error(err);document.documentElement.lang='en'});

window.addEventListener('pagehide',()=>{if(job)fetch(`/api/job/${job}`,{method:'DELETE',headers:apiHeaders(),keepalive:true}).catch(()=>{});});
