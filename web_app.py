import json
import locale
import os
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymupdf

from pdf_unredact import (
    _candidate_from_dict,
    _clean_document,
    compute_redaction_stats,
    make_clean_pdf,
    make_side_by_side,
    __version__,
)

from i18n import frontend_catalog, normalize_language, translate


def _system_language():
    try:
        return normalize_language(locale.getlocale()[0])
    except Exception:
        return "en"


HOST = "127.0.0.1"
APP_REPOSITORY = os.environ.get("PDF_UNREDACT_REPOSITORY", "https://github.com/andrea-del-sarto/pdf-unredact")
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
JOB_TTL_SECONDS = 2 * 60 * 60
WORK_ROOT = Path(tempfile.mkdtemp(prefix="pdf-unredact-"))
JOBS = {}
JOBS_LOCK = threading.Lock()


INDEX_HTML = r'''<!doctype html>
<html lang="it" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pdf-unredact</title>
<link rel="icon" href="/favicon.ico">
<style>
:root{--bg:#f5f6f8;--surface:#fff;--surface-2:#f0f2f5;--surface-3:#e8ebef;--text:#15171a;--muted:#68707b;--line:#d9dde3;--line-strong:#c4cad2;--accent:#17191c;--accent-text:#fff;--ok:#147a48;--warn:#a56800;--danger:#b42318;--shadow:0 10px 28px rgba(17,24,39,.08);--preview:#dfe3e8;--focus:#4c7df0;color-scheme:light}
html[data-theme="dark"]{--bg:#0d0f12;--surface:#15181d;--surface-2:#1c2026;--surface-3:#242a32;--text:#f4f6f8;--muted:#9ba5b1;--line:#2c333d;--line-strong:#3b4450;--accent:#f3f5f7;--accent-text:#111317;--ok:#75d69f;--warn:#f2c15c;--danger:#ff8c84;--shadow:0 12px 34px rgba(0,0,0,.28);--preview:#262c34;--focus:#7aa2ff;color-scheme:dark}
*{box-sizing:border-box}html,body{min-height:100%}body{margin:0;font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);transition:background .18s,color .18s}button,input,select{font:inherit}
button,.button{appearance:none;border:1px solid transparent;border-radius:10px;padding:10px 14px;font-weight:650;cursor:pointer;background:var(--accent);color:var(--accent-text);text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:40px}button:hover,.button:hover{filter:brightness(.96)}button:disabled{opacity:.45;cursor:not-allowed}.secondary{background:var(--surface-2);color:var(--text);border-color:var(--line)}
.app{max-width:1380px;margin:0 auto;padding:22px 24px 42px}.topbar{height:58px;display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px}.brand{display:flex;align-items:center;gap:12px}.logo{width:34px;height:34px;border-radius:10px;background:var(--accent);color:var(--accent-text);display:grid;place-items:center;font-weight:800;font-size:15px}.brandText h1{font-size:18px;margin:0;line-height:1.2}.controls{display:flex;align-items:center;gap:8px}.iconButton,.languageButton{height:40px;min-height:40px;padding:0;border-radius:10px;background:var(--surface);color:var(--text);border:1px solid var(--line)}.iconButton{width:40px;display:grid;place-items:center}.languageButton{min-width:48px;padding:0 10px;font-size:12px;letter-spacing:.04em}.iconButton:hover,.languageButton:hover{background:var(--surface-2);filter:none}.iconButton:focus-visible,.languageButton:focus-visible{outline:none;border-color:var(--focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--focus) 18%,transparent)}.iconButton svg{width:19px;height:19px;display:block}
select,.textInput,.pageInput{border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:9px;padding:9px 10px;outline:none}select:focus,.textInput:focus,.pageInput:focus{border-color:var(--focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--focus) 18%,transparent)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.uploadCard{padding:16px;margin-bottom:16px}.drop{border:1.5px dashed var(--line-strong);border-radius:13px;padding:34px 18px;text-align:center;cursor:pointer;background:var(--surface-2);transition:.15s}.drop.drag{border-color:var(--focus);background:color-mix(in srgb,var(--focus) 8%,var(--surface-2))}.dropIcon{font-size:28px;line-height:1}.drop h2{font-size:17px;margin:8px 0 3px}.drop p{margin:0;color:var(--muted)}
.status{color:var(--muted);margin-top:9px;font-size:12px}.error{color:var(--danger);white-space:pre-wrap;margin-top:9px;font-size:12px}.progress{height:5px;border-radius:999px;background:var(--surface-3);overflow:hidden;margin-top:10px}.progress>div{height:100%;width:32%;background:var(--accent);animation:slide 1s infinite ease-in-out}@keyframes slide{from{transform:translateX(-120%)}to{transform:translateX(330%)}}.hidden{display:none!important}
.workspace{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px;align-items:start}.mainCol,.sideCol{display:flex;flex-direction:column;gap:16px}.sideCol{position:sticky;top:16px}.fileCard{padding:15px 16px}.fileline{display:flex;justify-content:space-between;gap:14px;align-items:center}.filename{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:72vw}.small{font-size:12px;color:var(--muted)}
.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.metric{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 15px}.metric b{font-size:23px;line-height:1.1;display:block;margin-bottom:5px}.metric span{font-size:12px;color:var(--muted)}.metric.ok b{color:var(--ok)}.metric.warn b{color:var(--warn)}
.previewCard{padding:15px}.sectionHead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}.sectionHead h2{margin:0;font-size:15px}.sectionHead p{margin:2px 0 0;color:var(--muted);font-size:12px}.pageCtl{display:flex;align-items:center;gap:7px}.pageCtl button{min-height:34px;padding:7px 10px}.pageInput{width:65px;padding:7px 8px;text-align:center}.viewer{display:grid;grid-template-columns:1fr 1fr;gap:10px}.paneWrap{min-width:0}.paneTitle{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;margin:0 0 6px}.pane{background:var(--preview);border-radius:11px;min-height:340px;display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:8px;border:1px solid var(--line)}.pane img{max-width:100%;height:auto;display:block;box-shadow:0 3px 15px rgba(0,0,0,.12)}
.panel{padding:15px}.panel h3{font-size:13px;margin:0 0 10px}.option{display:flex;gap:9px;align-items:flex-start;padding:9px 0;cursor:pointer}.option+.option{border-top:1px solid var(--line)}.option input{margin-top:3px}.option strong{display:block;font-size:13px}.option span{display:block;color:var(--muted);font-size:11px;margin-top:1px}.field{margin-top:12px}.field label{font-size:11px;color:var(--muted);display:block;margin-bottom:5px}.textInput{width:100%}.actions{display:grid;grid-template-columns:1fr;gap:8px;margin-top:14px}.actions .button{width:100%}.exportStatus{text-align:center;min-height:18px;margin-top:7px}.reportActions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.reportActions .button,.reportActions button{width:100%;font-size:12px;padding:8px 9px;min-height:36px}
.findingsCard{padding:15px}.tableWrap{overflow:auto;max-height:330px;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);white-space:nowrap}th{position:sticky;top:0;background:var(--surface-2);z-index:1;color:var(--muted);font-weight:650}tr:last-child td{border-bottom:0}.chip{display:inline-flex;border-radius:999px;padding:2px 7px;background:var(--surface-2);border:1px solid var(--line);font-size:11px}.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}
.modalBackdrop{position:fixed;inset:0;background:rgba(0,0,0,.44);display:flex;align-items:center;justify-content:center;padding:20px;z-index:1000}.modal{width:min(430px,100%);background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,.3);padding:18px}.modalHead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.modalHead h2{font-size:16px;margin:0}.modalClose{width:36px;height:36px;min-height:36px;padding:0}.infoGrid{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px 14px;align-items:start}.infoGrid dt{color:var(--muted);font-size:12px}.infoGrid dd{margin:0;font-weight:650;min-width:0;overflow-wrap:anywhere}.repoLink{color:var(--text);text-decoration:underline;text-underline-offset:3px}.repoLink:hover{opacity:.8}.modalFoot{margin-top:16px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}
@media(max-width:1020px){.workspace{grid-template-columns:1fr}.sideCol{position:static;display:grid;grid-template-columns:1fr 1fr}.sideCol .panel:last-child{grid-column:1/-1}.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.app{padding:14px 12px 28px}.topbar{height:auto}.viewer,.sideCol{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.fileline{align-items:flex-start}.filename{max-width:55vw}.sectionHead{align-items:flex-start;flex-direction:column}.pageCtl{width:100%;justify-content:flex-end}}
</style>
</head>
<body>
<div class="app">
<header class="topbar"><div class="brand"><div class="logo">PU</div><div class="brandText"><h1>pdf-unredact</h1></div></div><div class="controls"><button id="infoButton" class="iconButton" type="button" aria-label="" title=""><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg></button><button id="languageButton" class="languageButton" type="button" aria-label="" title="">IT</button><button id="themeButton" class="iconButton" type="button" aria-label="" title=""></button></div></header>
<section class="card uploadCard" id="uploadCard"><div class="drop" id="drop"><div class="dropIcon">⇧</div><h2 data-i18n="open_pdf"></h2><p data-i18n="drop_pdf"></p><input id="file" type="file" accept="application/pdf,.pdf" hidden></div><div id="uploadStatus" class="status"></div><div id="uploadProgress" class="progress hidden"><div></div></div></section>
<div id="workspace" class="hidden"><div class="workspace">
<main class="mainCol">
<section class="card fileCard"><div class="fileline"><div><div class="filename" id="filename"></div><div class="small" id="filemeta"></div></div><button class="secondary" id="replace" data-i18n="change_pdf"></button></div></section>
<section class="metrics"><div class="metric"><b id="detected">0</b><span data-i18n="detected"></span></div><div class="metric ok"><b id="recoverable">0</b><span data-i18n="recoverable_plural"></span></div><div class="metric ok"><b id="removable">0</b><span data-i18n="removable_plural"></span></div><div class="metric warn"><b id="unsupported">0</b><span data-i18n="unsupported_plural"></span></div><div class="metric warn"><b id="applied">0</b><span data-i18n="probably_applied_plural"></span></div><div class="metric"><b id="uncertain">0</b><span data-i18n="uncertain_plural"></span></div></section>
<section class="card previewCard"><div class="sectionHead"><div><h2 data-i18n="preview"></h2><p data-i18n="preview_desc"></p></div><div class="pageCtl"><button class="secondary" id="prev" aria-label="" title="">←</button><input class="pageInput" id="page" type="number" min="1" value="1"><span class="small" id="pagesLabel">/ 1</span><button class="secondary" id="next" aria-label="" title="">→</button></div></div><div class="viewer"><div class="paneWrap"><p class="paneTitle" data-i18n="original"></p><div class="pane"><img id="originalPreview" alt=""></div></div><div class="paneWrap"><p class="paneTitle" data-i18n="clean"></p><div class="pane"><img id="cleanPreview" alt=""></div></div></div></section>
<section class="card findingsCard"><div class="sectionHead"><div><h2 data-i18n="findings"></h2><p id="findingsSummary"></p></div></div><div class="tableWrap"><table><thead><tr><th data-i18n="page"></th><th data-i18n="type"></th><th data-i18n="status"></th><th data-i18n="removal"></th><th data-i18n="words"></th><th data-i18n="characters"></th></tr></thead><tbody id="findings"></tbody></table></div></section>
</main>
<aside class="sideCol">
<section class="card panel"><h3 data-i18n="output_format"></h3><label class="option"><input type="radio" name="mode" value="clean" checked><span><strong data-i18n="clean_pdf"></strong><span data-i18n="clean_pdf_desc"></span></span></label><label class="option"><input type="radio" name="mode" value="side_by_side"><span><strong>Side-by-side</strong><span data-i18n="side_by_side_desc"></span></span></label></section>
<section class="card panel"><h3 data-i18n="annotations"></h3><label class="option"><input type="radio" name="remove" value="redactions" checked><span><strong data-i18n="redactions_only"></strong><span data-i18n="redactions_only_desc"></span></span></label><label class="option"><input type="radio" name="remove" value="all-annotations"><span><strong data-i18n="all_annotations"></strong><span data-i18n="all_annotations_desc"></span></span></label></section>
<section class="card panel"><h3 data-i18n="export_heading"></h3><div class="field"><label for="outputName" data-i18n="filename"></label><input class="textInput" id="outputName" type="text" autocomplete="off" spellcheck="false"></div><div class="actions"><button id="export" data-i18n="export_pdf"></button></div><div id="exportStatus" class="small exportStatus"></div><div class="reportActions"><a id="jsonReport" class="button secondary" href="#" download data-i18n="json_report"></a><button class="secondary" id="refreshAudit" data-i18n="refresh_analysis"></button></div></section>
</aside>
</div></div>
</div>
<div id="infoModal" class="modalBackdrop hidden" role="dialog" aria-modal="true" aria-labelledby="infoTitle">
  <div class="modal">
    <div class="modalHead"><h2 id="infoTitle" data-i18n="about_title"></h2><button id="infoClose" class="iconButton modalClose" type="button" aria-label="" title=""><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
    <dl class="infoGrid">
      <dt data-i18n="app_name"></dt><dd>pdf-unredact</dd>
      <dt data-i18n="version"></dt><dd>__APP_VERSION__</dd>
      <dt data-i18n="github_repo"></dt><dd><a class="repoLink" href="__APP_REPOSITORY__" target="_blank" rel="noopener noreferrer">__APP_REPOSITORY__</a></dd>
    </dl>
    <div class="modalFoot" data-i18n="license_note"></div>
  </div>
</div>
<script>
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
  $('originalPreview').alt=tr('original_preview');$('cleanPreview').alt=tr('clean_preview');
  updateThemeButton();
  if(currentFilename)$('filemeta').textContent=`${fmt(pages)} ${tr('pages')} · ${human(currentFileSize)}`;
  if(currentStats)renderStats(currentStats);
  if(lastStatusKey)status(tr(lastStatusKey),lastStatusError,false);
  if(lastExportStatusKey)$('exportStatus').textContent=tr(lastExportStatusKey);
}
$('languageButton').addEventListener('click',async()=>{await applyLanguage(language==='it'?'en':'it')});
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
async function upload(f){if(!f)return;if(f.type&&f.type!=='application/pdf'&&!f.name.toLowerCase().endsWith('.pdf')){statusKey('select_pdf',true);return}$('uploadProgress').classList.remove('hidden');statusKey('analyzing');$('workspace').classList.add('hidden');try{const r=await fetch('/api/upload',{method:'POST',headers:apiHeaders({'Content-Type':'application/pdf','X-Filename':encodeURIComponent(f.name)}),body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||tr('open_error'));job=d.job_id;pages=d.pages;current=1;currentFilename=d.filename;currentFileSize=f.size;currentStats=d.stats;outputNameTouched=false;$('filename').textContent=d.filename;$('filemeta').textContent=`${fmt(d.pages)} ${tr('pages')} · ${human(f.size)}`;renderStats(d.stats);$('pagesLabel').textContent=`/ ${pages}`;$('page').max=pages;$('page').value=1;syncOutputName(true);$('jsonReport').href=`/api/report/${job}?lang=${language}`;$('jsonReport').download=currentFilename.replace(/\.pdf$/i,'')+'_audit.json';$('workspace').classList.remove('hidden');statusKey('analysis_complete');await refreshPreview()}catch(e){status(e.message,true)}finally{$('uploadProgress').classList.add('hidden')}}
function renderStats(stats){currentStats=stats;$('detected').textContent=fmt(stats.redaction_boxes_found);$('recoverable').textContent=fmt(stats.recoverable_redactions);$('removable').textContent=fmt(stats.removable_redactions);$('unsupported').textContent=fmt(stats.unsupported_recoverable_redactions);$('applied').textContent=fmt(stats.probable_applied_redactions);$('uncertain').textContent=fmt(stats.uncertain_redactions);renderFindings(stats.findings||[])}
function sourceLabel(source){return source==='annotation:redact'?tr('source_redact'):source==='annotation:highlight'?tr('source_highlight'):source==='annotation:square'?tr('source_square'):source==='dark_rectangle'?tr('source_rectangle'):source}
function removalLabel(x){if(!x.recoverable)return `<span class="chip">${tr('not_applicable')}</span>`;if(x.removal_method==='annotation_delete')return `<span class="chip ok">${tr('removal_annotation')}</span>`;if(x.removal_method==='content_stream_rewrite')return `<span class="chip ok">${tr('removal_stream')}</span>`;return `<span class="chip warn">${tr('not_removable')}</span>`}
function renderFindings(arr){$('findingsSummary').textContent=`${fmt(arr.length)} ${tr('areas_detected')}`;const tbody=$('findings');tbody.innerHTML='';for(const x of arr.slice(0,1000)){const trEl=document.createElement('tr');const st=x.recoverable?`<span class="chip ok">${tr('recoverable')}</span>`:x.probable_applied?`<span class="chip warn">${tr('probably_applied')}</span>`:`<span class="chip">${tr('uncertain')}</span>`;trEl.innerHTML=`<td>${x.page}</td><td>${escapeHtml(sourceLabel(x.source))}</td><td>${st}</td><td>${removalLabel(x)}</td><td>${fmt(x.words_under)}</td><td>${fmt(x.chars_under)}</td>`;tbody.appendChild(trEl)}}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refreshPreview(){if(!job)return;current=Math.max(1,Math.min(pages,parseInt($('page').value)||1));$('page').value=current;const rm=encodeURIComponent(removeMode()),t=Date.now();$('originalPreview').src=`/api/preview/${job}/original/${current}?lang=${language}&t=${t}`;$('cleanPreview').src=`/api/preview/${job}/clean/${current}?remove=${rm}&lang=${language}&t=${t}`}
async function refreshAudit(){if(!job)return;const btn=$('refreshAudit');btn.disabled=true;try{const r=await fetch(`/api/audit/${job}`,{method:'POST',headers:apiHeaders()});const d=await r.json();if(!r.ok)throw new Error(d.error||tr('analysis_error'));renderStats(d.stats)}catch(e){lastExportStatusKey='';$('exportStatus').textContent=e.message}finally{btn.disabled=false}}
async function exportPdf(){if(!job)return;const btn=$('export');btn.disabled=true;lastExportStatusKey='generating';$('exportStatus').textContent=tr('generating');try{const r=await fetch('/api/export',{method:'POST',headers:apiHeaders({'Content-Type':'application/json'}),body:JSON.stringify({job_id:job,mode:outputMode(),remove:removeMode(),filename:$('outputName').value.trim()})});const d=await r.json();if(!r.ok)throw new Error(d.error||tr('export_error'));lastExportStatusKey='pdf_ready';$('exportStatus').textContent=tr('pdf_ready');const link=document.createElement('a');link.href=d.download_url;link.download=d.filename;link.style.display='none';document.body.appendChild(link);link.click();link.remove()}catch(e){lastExportStatusKey='';$('exportStatus').textContent=e.message}finally{btn.disabled=false}}
drop.onclick=()=>file.click();file.onchange=()=>upload(file.files[0]);['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('drag')}));['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('drag')}));drop.addEventListener('drop',e=>upload(e.dataTransfer.files[0]));$('replace').onclick=()=>file.click();$('prev').onclick=()=>{$('page').value=Math.max(1,current-1);refreshPreview()};$('next').onclick=()=>{$('page').value=Math.min(pages,current+1);refreshPreview()};$('page').onchange=refreshPreview;document.querySelectorAll('input[name="remove"]').forEach(x=>x.onchange=refreshPreview);document.querySelectorAll('input[name="mode"]').forEach(x=>x.onchange=()=>syncOutputName(false));$('outputName').addEventListener('input',()=>{outputNameTouched=true});$('export').onclick=exportPdf;$('refreshAudit').onclick=refreshAudit;
applyLanguage(language).catch(err=>{console.error(err);document.documentElement.lang='en'});
</script>
</body></html>'''
INDEX_HTML = INDEX_HTML.replace('__APP_VERSION__', __version__).replace('__APP_REPOSITORY__', APP_REPOSITORY)


FAVICON_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#17191c"/><text x="32" y="40" text-anchor="middle" font-family="Arial,sans-serif" font-size="25" font-weight="700" fill="white">PU</text></svg>'''


def _cleanup_old_jobs():
    now = time.time()
    expired = []
    with JOBS_LOCK:
        for job_id, meta in JOBS.items():
            if now - meta["created"] > JOB_TTL_SECONDS:
                expired.append(job_id)
        for job_id in expired:
            meta = JOBS.pop(job_id, None)
            if meta:
                shutil.rmtree(meta["dir"], ignore_errors=True)


def _job(job_id):
    _cleanup_old_jobs()
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _safe_filename(name):
    name = os.path.basename(name or "document.pdf")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    cleaned = "".join(c for c in name if c.isalnum() or c in " ._-").strip()
    return cleaned or "document.pdf"


def _message(key, lang="en"):
    return translate(key, lang, section="backend")


def _render_preview(path, page_number, kind, remove_mode, stats_dict=None, lang="en"):
    doc = pymupdf.open(path)
    try:
        if page_number < 1 or page_number > doc.page_count:
            raise ValueError(_message("invalid_page", lang))
        if kind == "clean":
            findings = [_candidate_from_dict(x) for x in (stats_dict or {}).get("findings", [])]
            _clean_document(doc, findings, remove=remove_mode, page_numbers=[page_number])
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.35, 1.35), alpha=False, annots=True)
        return pix.tobytes("png")
    finally:
        doc.close()


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdf-unredact/{__version__}"

    def log_message(self, fmt, *args):
        print("[web] " + (fmt % args))

    def _lang(self, parsed=None):
        header = (self.headers.get("X-Language", "") or "").lower()
        if header.startswith("it"):
            return "it"
        if header.startswith("en"):
            return "en"
        if parsed is not None:
            qs = urllib.parse.parse_qs(parsed.query)
            query_lang = (qs.get("lang", [""])[0] or "").lower()
            if query_lang in {"it", "en"}:
                return query_lang
        accept = (self.headers.get("Accept-Language", "") or "").lower()
        return normalize_language(accept)

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, _json_bytes(obj), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(200, FAVICON_SVG, "image/svg+xml; charset=utf-8")
            return
        if path.startswith("/api/i18n/"):
            lang = normalize_language(path.rsplit("/", 1)[-1])
            requested = path.rsplit("/", 1)[-1].lower()
            if requested not in {"it", "en"}:
                self._json(404, {"error": _message("not_found", self._lang(parsed))})
                return
            self._json(200, frontend_catalog(lang))
            return
        if path.startswith("/api/preview/"):
            parts = path.strip("/").split("/")
            if len(parts) != 5:
                self._json(404, {"error": _message("invalid_endpoint", self._lang(parsed))}); return
            _, _, job_id, kind, page_text = parts
            meta = _job(job_id)
            if not meta or kind not in {"original", "clean"}:
                self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                page_no = int(page_text)
                qs = urllib.parse.parse_qs(parsed.query)
                remove_mode = qs.get("remove", ["redactions"])[0]
                if remove_mode not in {"redactions", "all-annotations"}:
                    remove_mode = "redactions"
                png = _render_preview(meta["input"], page_no, kind, remove_mode, meta.get("stats"), self._lang(parsed))
                self._send(200, png, "image/png")
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return
        if path.startswith("/api/report/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                self._json(404, {"error": _message("invalid_endpoint", self._lang(parsed))}); return
            _, _, job_id = parts
            meta = _job(job_id)
            if not meta or not meta.get("stats"):
                self._json(404, {"error": _message("report_unavailable", self._lang(parsed))}); return
            data = json.dumps(meta["stats"], ensure_ascii=False, indent=2).encode("utf-8")
            filename = Path(meta["filename"]).stem + "_audit.json"
            self._send(200, data, "application/json; charset=utf-8", {"Content-Disposition": f'attachment; filename="{filename}"'})
            return
        if path.startswith("/api/download/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                self._json(404, {"error": _message("invalid_endpoint", self._lang(parsed))}); return
            _, _, job_id = parts
            meta = _job(job_id)
            if not meta or not meta.get("last_output") or not os.path.exists(meta["last_output"]):
                self._json(404, {"error": _message("file_unavailable", self._lang(parsed))}); return
            data = Path(meta["last_output"]).read_bytes()
            filename = os.path.basename(meta["last_output"])
            self._send(200, data, "application/pdf", {"Content-Disposition": f'attachment; filename="{filename}"'})
            return
        self._json(404, {"error": _message("not_found", self._lang(parsed))})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/upload":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                self._json(400, {"error": _message("empty_file", self._lang(parsed))}); return
            if length > MAX_UPLOAD_BYTES:
                self._json(413, {"error": _message("too_large", self._lang(parsed))}); return
            raw = self.rfile.read(length)
            if not raw.startswith(b"%PDF-"):
                self._json(400, {"error": _message("invalid_pdf", self._lang(parsed))}); return
            name = urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf"))
            filename = _safe_filename(name)
            job_id = uuid.uuid4().hex
            job_dir = WORK_ROOT / job_id
            job_dir.mkdir(parents=True)
            input_path = job_dir / "input.pdf"
            input_path.write_bytes(raw)
            try:
                doc = pymupdf.open(input_path)
                if doc.needs_pass:
                    doc.close(); raise ValueError(_message("password", self._lang(parsed)))
                pages = doc.page_count
                doc.close()
                stats = compute_redaction_stats(str(input_path))
            except Exception as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                self._json(400, {"error": f"{_message("cannot_analyze", self._lang(parsed))}: {exc}"}); return
            with JOBS_LOCK:
                JOBS[job_id] = {"dir": str(job_dir), "input": str(input_path), "filename": filename, "pages": pages, "created": time.time(), "last_output": None, "stats": stats.to_dict()}
            self._json(200, {"job_id": job_id, "filename": filename, "pages": pages, "stats": stats.to_dict()})
            return

        if parsed.path.startswith("/api/audit/"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) != 3:
                self._json(404, {"error": _message("invalid_endpoint", self._lang(parsed))}); return
            _, _, job_id = parts
            meta = _job(job_id)
            if not meta:
                self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                stats = compute_redaction_stats(meta["input"])
                meta["stats"] = stats.to_dict()
                self._json(200, {"stats": meta["stats"]})
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/export":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                job_id = data.get("job_id", "")
                mode = data.get("mode", "clean")
                remove = data.get("remove", "redactions")
                if mode not in {"clean", "side_by_side"}:
                    raise ValueError(_message("invalid_mode", self._lang(parsed)))
                if remove not in {"redactions", "all-annotations"}:
                    raise ValueError(_message("invalid_annotations", self._lang(parsed)))
                meta = _job(job_id)
                if not meta:
                    raise ValueError(_message("job_not_found", self._lang(parsed)))
                stem = Path(meta["filename"]).stem
                suffix = "_clean.pdf" if mode == "clean" else "_side_by_side.pdf"
                requested_name = str(data.get("filename", "")).strip()
                output_name = _safe_filename(requested_name) if requested_name else stem + suffix
                output = os.path.join(meta["dir"], output_name)
                if mode == "clean":
                    result = make_clean_pdf(meta["input"], output, remove=remove)
                else:
                    result = make_side_by_side(meta["input"], output, remove=remove)
                meta["last_output"] = output
                self._json(200, {"filename": os.path.basename(output), "download_url": f"/api/download/{job_id}", "clean_result": result.to_dict()})
            except Exception as exc:
                self._json(400, {"error": str(exc)})
            return

        self._json(404, {"error": _message("not_found", self._lang(parsed))})


def _create_server(preferred_port=8765):
    """Create the local HTTP server, falling back to a free port when needed."""
    try:
        return ThreadingHTTPServer((HOST, preferred_port), Handler), preferred_port
    except OSError as exc:
        # EADDRINUSE is 98 on Linux and maps to errno.EADDRINUSE on supported
        # Python platforms. If the preferred port is busy, let the OS choose an
        # available ephemeral port instead of aborting with a traceback.
        import errno

        if exc.errno != errno.EADDRINUSE:
            raise

    server = ThreadingHTTPServer((HOST, 0), Handler)
    return server, int(server.server_address[1])


def _open_browser_silent(url: str) -> None:
    """Open the local UI without forwarding browser launcher output to the terminal."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        if sys.platform == "darwin":
            subprocess.Popen(
                ["open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
            return
    except (OSError, subprocess.SubprocessError):
        pass

    # Portable fallback for uncommon environments.
    webbrowser.open(url)


def run_web(port=8765, open_browser=True):
    server, actual_port = _create_server(port)
    url = f"http://{HOST}:{actual_port}/"
    console_lang = _system_language()
    print("pdf-unredact")
    if actual_port != port:
        print(translate("port_in_use", console_lang, port=port, actual=actual_port))
    print(translate("open_url", console_lang, url=url))
    print(translate("press_stop", console_lang))
    if open_browser:
        threading.Timer(0.6, lambda: _open_browser_silent(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(WORK_ROOT, ignore_errors=True)
        print(translate("stopped", console_lang))


if __name__ == "__main__":
    run_web()
