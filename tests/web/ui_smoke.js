// run from a directory where `npm i jsdom` was done
const {JSDOM}=require('module').createRequire(process.cwd()+'/')('jsdom');
const BASE=process.argv[2]||'http://localhost:8765';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(f,ms=90000){const t=Date.now();while(Date.now()-t<ms){try{if(f())return true;}catch(e){}await sleep(200);}throw new Error('timeout waiting');}
let fails=0; const ok=(c,m)=>{console.log((c?'PASS':'FAIL')+': '+m); if(!c)fails++;};
(async()=>{
  const html=await (await fetch(BASE+'/')).text();
  ok(html.includes('Run Demo Mission')&&html.includes("Research question"),'homepage has research box + demo button');
  const dom=new JSDOM(html,{runScripts:'dangerously',url:BASE+'/',pretendToBeVisual:true,
    beforeParse(w){w.fetch=(u,o)=>fetch(new URL(u,BASE),o); w.HTMLElement.prototype.scrollIntoView=function(){};}});
  const w=dom.window, d=w.document, $=id=>d.getElementById(id);
  await waitFor(()=>$('webstatus').textContent.includes('available'));
  ok(true,'web status shown: '+$('webstatus').textContent);
  $('research').click();
  await waitFor(()=>!$('needed-wrap').hidden);
  ok(true,'"Parameters needed" shown while researching');
  await waitFor(()=>!$('review-wrap').hidden);
  const txt=$('review').textContent;
  ok(txt.includes('CONFLICTING EVIDENCE'),'epsilon shows CONFLICTING EVIDENCE');
  ok(txt.includes('TEST study A')&&txt.includes('TEST study B'),'both conflicting sources visible');
  ok(txt.includes('Evidence insufficient — no reliable source found.'),'missing params say evidence insufficient');
  ok(txt.includes('0.7054')||txt.includes('0.705'),'f shown in kgCO2e/kWh: '+(txt.match(/= [0-9.]+ kgCO2e\/kWh/)||[''])[0]);
  ok($('review').querySelectorAll('a[href^="https://example.ac.in"]').length>=1&&$('review').querySelector('a').rel.includes('noopener'),'source links clickable with rel=noopener');
  ok($('confirm').disabled===true,'Confirm disabled while decisions missing (conflict + missing)');
  ok(!$('blockers').textContent.includes('xxx') && $('blockers').textContent.includes('epsilon'),'blockers mention epsilon');
  const pick=(name,idx)=>{const r=[...d.querySelectorAll('input[name="dec-'+name+'"]')]; return r;};
  const click=(name,labelPart)=>{const r=[...d.querySelectorAll('input[name="dec-'+name+'"]')].find(i=>i.parentElement.textContent.includes(labelPart)); if(!r) throw new Error('no radio '+name+' '+labelPart); r.checked=true; r.dispatchEvent(new w.Event('change'));};
  click('epsilon','Use this source'); // first candidate
  // choose second candidate explicitly
  const eps=[...d.querySelectorAll('input[name="dec-epsilon"]')].filter(i=>i.parentElement.textContent.includes('Use this source'));
  eps[1].checked=true; eps[1].dispatchEvent(new w.Event('change'));
  for(const p of ['EM0','EM1','eta','phi']) click(p,'illustrative default');
  click('E0','Override');
  const inputs=[...d.querySelectorAll('#review input[type=number]')]; inputs[0].value='1200'; inputs[0].dispatchEvent(new w.Event('input'));
  await sleep(300);
  ok($('confirm').disabled===false,'Confirm enabled after all decisions made: '+$('blockers').textContent.slice(0,80));
  ok($('summary').textContent.includes('USER OVERRIDE')&&$('summary').textContent.includes('Original researched value: none researched'),'summary lists the override with original value');
  $('confirm').click();
  ok(!$('confirmbox').hidden && $('prov-wrap').hidden && $('out').hidden,'confirmation asked; engine NOT yet run');
  $('yes').click();
  try{await waitFor(()=>!$('prov-wrap').hidden,60000);}catch(e){console.log('RMSG:',$('rmsg').textContent,'| running hidden:',$('running').hidden);throw e;}
  const prov=$('prov').textContent;
  ok(prov.includes('USER OVERRIDE')&&prov.includes('1200'),'provenance shows user override');
  ok(prov.includes('TEST study B')&&prov.includes('TEST study A'),'provenance keeps both conflicting sources visible');
  ok(prov.includes('registry sourced'),'f from registry labelled');
  ok(!$('out').hidden&&$('repro').textContent.includes('reproduced_exactly'),'results + reproducibility rendered');
  ok($('cands').textContent.length>50,'candidates rendered');
  const links=[...$('artifacts').querySelectorAll('a')].map(a=>a.getAttribute('href'));
  ok(links.some(h=>h.includes('/api/runs/')&&h.endsWith('parameter_review.json')),'artifact links incl. parameter_review.json');
  const r=await fetch(BASE+links.find(h=>h.endsWith('parameter_review.json'))); ok(r.status===200,'artifact downloadable');
  // demo button still works
  $('run').click(); await waitFor(()=>$('msg').textContent.startsWith('Done'),120000);
  ok($('mission').textContent.includes('m1_rebound_breakeven_india'),'demo mission still runs');
  console.log(fails?('FAILED '+fails):'ALL UI CHECKS PASSED'); process.exit(fails?1:0);
})().catch(e=>{console.log('ERROR',e.message,e.cause&&e.cause.code,(e.stack||'').split('\n').slice(0,4).join(' / '));process.exit(2)});
