/** Categories with real local APIs/SQLite, and isolated public GoTrue/RPC mocks.
 * Run: node tests/browser-categories.cjs. No production writes/emails/audio.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn, execFileSync} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-category-data-'));
const artifacts = process.env.PLAYLIST_CATEGORY_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-category-browser-'));
const password = 'local-category-fixture-password';
const report = {local: 'real guest/admin FastAPI and disposable SQLite', publicAuth: 'browser GoTrue/RPC interception only',
  emailsSent: 0, audio: 'none', checks: [], pageErrors: [], consoleErrors: [], external: [], screenshots: []};
let env, browser, guestUrl, adminUrl;
const children = [];
const fixture = String.raw`
import json,os,sys
from pathlib import Path
import uvicorn
from playlist25 import db
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'data',session_secret=os.environ['SESSION_SECRET'],admin_password=os.environ['ADMIN_PASSWORD'],
 guest_port=int(os.environ['GUEST_PORT']),admin_port=int(os.environ['ADMIN_PORT']),min_free_mb=0)
db.init_db(cfg)
if sys.argv[1]=='seed':
 a,b,category=sys.argv[2:5]
 with db.connect(cfg) as conn:
  def proposal(item,owner,classified=False):
   source=db.upsert_source(conn,item)
   return conn.execute('INSERT INTO proposals(source_id,guest_id,created_at,category_ids) VALUES(?,?,?,?)',(source,owner,db.now(),json.dumps([category]) if classified else '[]')).lastrowid
  shared=dict(video_id='sameAudio01',url='https://www.youtube.com/watch?v=sameAudio01',title='Morceau partagé Camille',duration=120)
  own=proposal(shared,a,True);other=proposal(shared,b)
  manual=proposal(dict(title='Idée A sans catégorie'),a)
  for index in range(54):proposal(dict(title='Base soirée '+str(index).zfill(3),duration=60),b,True)
  for proposal_id,owner in [(own,a),(other,b),(manual,a)]:conn.execute('INSERT INTO votes VALUES(?,?,?)',(proposal_id,owner,db.now()))
  conn.commit();print(json.dumps(dict(own=own,other=other,manual=manual,count=57)))
elif sys.argv[1]=='snapshot':
 with db.connect(cfg) as conn:print(json.dumps({table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in ['sources','proposals','categories','guests','votes','jobs']}))
else:
 surface=sys.argv[1]
 uvicorn.run(create_app(surface,cfg),host='127.0.0.1',port=cfg.guest_port if surface=='guest' else cfg.admin_port,access_log=False,log_level='warning')
`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function checked(label) {report.checks.push(label); process.stdout.write(`✓ ${label}\n`);}
async function screen(page,name) {const filename=path.join(artifacts,name+'.png');await page.screenshot({path:filename,fullPage:false});report.screenshots.push(filename);}
function local(action, ...args) {return execFileSync(python, ['-c',fixture,action,...args], {cwd:root,env,encoding:'utf8'}).trim();}
async function port() {
  const server=net.createServer();await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const result=server.address().port;await new Promise(resolve=>server.close(resolve));return result;
}
async function waitHealth(child,url) {
  const deadline=Date.now()+20000;
  while(Date.now()<deadline){if(child.exitCode!==null)throw new Error(child.logs);try{if((await fetch(url+'/health')).ok)return;}catch{}await pause(100);}
  throw new Error('Fixture server did not start: '+child.logs);
}
async function isolatedContext(options={}) {
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,...options});
  await context.route('**/*',async route=>{
    const origin=new URL(route.request().url()).origin;
    if([guestUrl,adminUrl].includes(origin))return route.continue();
    report.external.push(route.request().url());await route.abort('blockedbyclient');
  });
  await context.addInitScript(()=>{
    const interval=window.setInterval;
    window.setInterval=(callback,delay,...args)=>delay===30000?123456:interval(callback,delay,...args);
  });
  return context;
}
function observe(page) {
  page.on('pageerror',error=>report.pageErrors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')report.consoleErrors.push(message.text());});
}
async function identity(context) {
  const session=await(await context.request.get(guestUrl+'/api/session')).json();
  const reply=await context.request.post(guestUrl+'/api/identity',{data:{nickname:'Camille'},headers:{'X-CSRF-Token':session.csrf_token}});
  assert.equal(reply.status(),200);const guest=(await reply.json()).guest;
  return {guest,session:await(await context.request.get(guestUrl+'/api/session')).json()};
}
async function categorySave(page) {
  const saved=page.waitForResponse(reply=>reply.request().method()==='PATCH'&&new URL(reply.url()).pathname.endsWith('/categories'));
  await page.locator('#category-assignment-save').focus();await page.keyboard.press('Enter');
  assert.equal((await saved).status(),200);await page.locator('#category-assignment-dialog').waitFor({state:'hidden'});
}
async function localChecks() {
  const a=await isolatedContext(), b=await isolatedContext(), admin=await isolatedContext();
  const A=await identity(a),B=await identity(b);assert.notEqual(A.guest.id,B.guest.id);
  const page=await a.newPage();observe(page);await page.goto(guestUrl);
  await page.locator('#guest-name').filter({hasText:'Camille'}).waitFor();
  assert.equal(await page.locator('#category-filter option').count(),2);
  assert.deepEqual((await(await a.request.get(guestUrl+'/api/proposals')).json()).categories,[]);
  await page.locator('#organisation-button').click();
  assert.equal(await page.locator('#organizer-access').isVisible(),false);
  assert.equal(await page.locator('#category-manager').isVisible(),false);
  assert.equal(await page.locator('#category-suggestions').locator('button').count(),0);
  await page.locator('[data-close-dialog="organisation-dialog"]').click();
  checked('Catalogue initial vide, deux pseudos identiques avec IDs distincts, aucun accès organisation public ni suggestions en mode local');
  const anonymousAdmin=await(await admin.request.get(adminUrl+'/api/session')).json();
  const login=await admin.request.post(adminUrl+'/api/login',{data:{password},headers:{'X-CSRF-Token':anonymousAdmin.csrf_token}});
  assert.equal(login.status(),200);const csrf=(await login.json()).csrf_token;
  async function adminCall(method,route,data) {const reply=await admin.request[method](adminUrl+route,{data,headers:{'X-CSRF-Token':csrf}});assert.equal(reply.status(),200);return reply.json();}
  const aperitif=(await adminCall('post','/api/categories',{name:'Apéritif',sort_order:0,active:true})).category;
  const danse=(await adminCall('post','/api/categories',{name:'Danse',sort_order:1,active:true})).category;
  const inactive=(await adminCall('post','/api/categories',{name:'Moment pour plus tard',sort_order:2,active:false})).category;
  const ids=JSON.parse(local('seed',A.guest.id,B.guest.id,aperitif.id));
  await page.reload();await page.locator('#stat-tracks').filter({hasText:String(ids.count)}).waitFor();
  const visibleA=await(await a.request.get(guestUrl+'/api/proposals')).json();
  assert.equal(await page.locator(`#category-filter option[value="${inactive.id}"]`).count(),0,'Unassigned inactive category must not appear in the guest filter');
  assert.equal(visibleA.items.find(item=>item.id===ids.own).mine,true,'Backend owner marker must match the actual session');
  assert.equal(await page.locator(`[data-proposal-id="${ids.own}"]`).count(),1,'Owner proposal must be on the first page');
  assert.equal(await page.locator(`[data-category-proposal-id="${ids.own}"]`).count(),1);
  assert.equal(await page.locator(`[data-category-proposal-id="${ids.other}"]`).count(),0);
  const otherPage=await b.newPage();observe(otherPage);await otherPage.goto(guestUrl);
  await otherPage.locator('#stat-tracks').filter({hasText:String(ids.count)}).waitFor();
  assert.equal(await otherPage.locator(`[data-category-proposal-id="${ids.other}"]`).count(),1);
  assert.equal(await otherPage.locator(`[data-category-proposal-id="${ids.own}"]`).count(),0);
  await page.locator(`[data-category-proposal-id="${ids.own}"]`).click();
  assert.equal(await page.locator(`[data-category-id="${inactive.id}"]`).count(),0);
  const second=page.locator(`[data-category-id="${danse.id}"]`);await second.focus();await page.keyboard.press('Space');assert.equal(await second.isChecked(),true);
  await screen(page,'category-assignment-desktop');await page.setViewportSize({width:393,height:852});
  await screen(page,'category-assignment-mobile');await page.setViewportSize({width:1440,height:1000});
  await categorySave(page);
  const beforeDenied=JSON.parse(local('snapshot'));
  assert.equal((await a.request.patch(`${guestUrl}/api/proposals/${ids.other}/categories`,{data:{category_ids:[danse.id]},headers:{'X-CSRF-Token':A.session.csrf_token}})).status(),404);
  assert.equal((await b.request.patch(`${guestUrl}/api/proposals/${ids.own}/categories`,{data:{category_ids:[danse.id]},headers:{'X-CSRF-Token':B.session.csrf_token}})).status(),404);
  assert.deepEqual(JSON.parse(local('snapshot')),beforeDenied);
  await adminCall('patch',`/api/proposals/${ids.other}/categories`,{category_ids:[aperitif.id,danse.id]});
  await page.locator('#refresh-proposals').click();
  await page.locator(`[data-proposal-id="${ids.other}"] .category-tag`).filter({hasText:'Danse'}).waitFor();
  checked('Choix multiples au clavier réservés au propriétaire réel, accès croisé refusé malgré le même pseudo, classement de l’autre proposition autorisé à l’admin');
  const duration=await page.locator('#stat-duration').innerText(),durationNote=await page.locator('#stat-duration-note').innerText();
  assert.match(duration,/56 min/);
  await page.locator('#category-filter').selectOption(aperitif.id);
  assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(),48);
  assert.match(await page.locator('#proposal-range-label').innerText(),/sur 56 morceaux/);
  await page.locator('#proposal-next').click();await page.locator('#proposal-page-label').filter({hasText:'Page 2 sur 2'}).waitFor();
  assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(),8);
  await page.locator('#category-filter').selectOption(danse.id);assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(),2);
  await page.locator('#category-filter').selectOption('uncategorized');assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(),1);
  assert.equal(await page.locator('#stat-duration').innerText(),duration);assert.equal(await page.locator('#stat-duration-note').innerText(),durationNote);
  assert.equal(await page.locator('#stat-tracks').innerText(),String(ids.count));
  checked('Filtres de catégories appliqués à toute la collection et aux pages, sans doubler la durée de la source partagée ni changer les compteurs globaux');
  await adminCall('patch',`/api/categories/${danse.id}`,{active:false});await page.locator('#refresh-proposals').click();
  await page.locator('#category-filter option').filter({hasText:'Danse (archivé)'}).waitFor({state:'attached'});
  await page.locator(`[data-category-proposal-id="${ids.manual}"]`).click();
  assert.equal(await page.locator(`[data-category-id="${danse.id}"]`).count(),0);await page.keyboard.press('Escape');
  assert.equal((await a.request.patch(`${guestUrl}/api/proposals/${ids.manual}/categories`,{data:{category_ids:[danse.id]},headers:{'X-CSRF-Token':A.session.csrf_token}})).status(),422);
  await page.locator('#category-filter').selectOption('');await page.locator(`[data-category-proposal-id="${ids.own}"]`).click();
  assert.equal(await page.locator(`[data-category-id="${danse.id}"]`).isChecked(),true);
  await page.locator(`[data-category-id="${danse.id}"]`).uncheck();await categorySave(page);
  assert.deepEqual((JSON.parse(local('snapshot')).proposals.find(row=>row.id===ids.other).category_ids),JSON.stringify([aperitif.id,danse.id]));
  assert.equal(await page.locator(`[data-proposal-id="${ids.other}"] .archived-category`).count(),1);
  checked('Archivage masque les nouveaux choix, conserve l’historique de l’autre invité et permet au propriétaire de retirer explicitement son choix archivé');
  await page.locator('#save-public-list').click();const received=page.waitForEvent('download');await page.locator('#public-export-csv').click();
  const exported=await received,filename=path.join(artifacts,'categories.csv');await exported.saveAs(filename);
  const rows=JSON.parse(execFileSync(python,['-c','import csv,json,sys\nwith open(sys.argv[1],encoding="utf-8-sig",newline="") as f: print(json.dumps(list(csv.DictReader(f,delimiter=";"))))',filename],{encoding:'utf8'}));
  const shared=rows.filter(row=>row.Titre==='Morceau partagé Camille');assert.equal(shared.length,2);
  assert(shared.some(row=>row.Catégories==='Apéritif'));assert(shared.some(row=>row.Catégories==='Apéritif · Danse'));
  assert(!fs.readFileSync(filename,'utf8').includes(aperitif.id));
  await page.locator('[data-close-dialog="public-export-dialog"]').click();await page.setViewportSize({width:393,height:852});
  await page.locator(`[data-category-proposal-id="${ids.manual}"]`).click();
  await page.locator(`[data-category-id="${aperitif.id}"]`).focus();await page.keyboard.press('Space');await categorySave(page);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  await screen(page,'categories-mobile');
  checked('CSV exporte les noms y compris les catégories archivées, sans IDs ; dialogue et sélection au clavier fonctionnent à 393 pixels');
  return {items:(await(await a.request.get(guestUrl+'/api/proposals')).json()).items.map(item=>({...item,mine:false})),
    settings:(await(await a.request.get(guestUrl+'/api/session')).json()).settings,categories:[aperitif,{...danse,active:false}]};
}

async function publicChecks(data) {
  const cloudUrl='https://browser-fixture.supabase.co',key=`playlist25-organizer-session-v1:${cloudUrl}`;
  const adminToken='fixture.organizer.signature',visitorToken='fixture.visitor.signature',refreshedToken='fixture.refreshed.signature';
  let categories=data.categories.slice(),authorized=true,authEmail='',mutationCount=0;
  const calls=[];
  const context=await browser.newContext({viewport:{width:393,height:852}});
  const cors={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,apikey,content-type','Access-Control-Allow-Methods':'POST,OPTIONS'};
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin===guestUrl&&url.pathname==='/static/cloud-config.js')return route.fulfill({contentType:'application/javascript',body:`window.PLAYLIST_CLOUD=Object.freeze(${JSON.stringify({url:cloudUrl,publicKey:'sb_publishable_browser_fixture'})});`});
    if([guestUrl,adminUrl].includes(url.origin))return route.continue();
    if(url.origin!==cloudUrl){report.external.push(request.url());return route.abort('blockedbyclient');}
    if(request.method()==='OPTIONS')return route.fulfill({status:204,headers:cors,body:''});
    const body=request.postDataJSON()||{},bearer=request.headers().authorization||'';
    calls.push({path:url.pathname,grant:url.searchParams.get('grant_type'),bearer});
    const response=value=>route.fulfill({status:200,headers:cors,contentType:'application/json',body:JSON.stringify(value)});
    if(url.pathname==='/auth/v1/signup')return response({user:{id:'signup-fixture',email:body.email},session:null});
    if(url.pathname==='/auth/v1/token'){
      if(url.searchParams.get('grant_type')==='refresh_token')return response({access_token:refreshedToken,refresh_token:'refreshed-fixture',expires_in:3600});
      authEmail=body.email;return response({access_token:body.email==='organizer@example.invalid'?adminToken:visitorToken,refresh_token:'refresh-fixture',expires_in:3600});
    }
    if(url.pathname==='/auth/v1/logout')return route.fulfill({status:204,headers:cors,body:''});
    const rpc=url.pathname.split('/').pop();
    if(rpc==='playlist25_session')return response({guest:null,csrf_token:'public-csrf-fixture',settings:data.settings});
    if(rpc==='playlist25_list')return response({items:data.items,categories});
    if(rpc==='playlist25_categories_list')return response({categories});
    const admin=authorized&&[`Bearer ${adminToken}`,`Bearer ${refreshedToken}`].includes(bearer);
    if(rpc==='playlist25_organizer_session')return response({authenticated:admin,organizer:admin?{id:'organizer-fixture',email:authEmail}:null});
    if(!admin)return response({error:'Droits organisation requis.',status:403});
    if(rpc==='playlist25_category_suggestions')return response({items:[{name:'Un moment suggéré privé',reason:'Aide accessible à l’organisation'}]});
    if(rpc==='playlist25_organizer_category'){
      mutationCount++;const item=body.p_category;
      if(item.id)categories=categories.map(category=>category.id===item.id?{...category,...item}:category);
      else categories.push({...item,id:'c'.repeat(32)});
      return response({categories});
    }
    throw new Error('Unexpected mocked RPC '+rpc);
  });
  await context.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,delay,...args)=>delay===30000?123456:original(fn,delay,...args);});
  const page=await context.newPage();observe(page);await page.goto(guestUrl);await page.locator('#stat-tracks').filter({hasText:'57'}).waitFor();
  await page.locator('#organisation-button').click();await page.locator('#organizer-login-panel').waitFor({state:'visible'});
  await screen(page,'public-organizer-login-mobile');
  assert.equal(await page.locator('#category-manager').isVisible(),false);assert.equal(await page.locator('[data-category-proposal-id]').count(),0);
  const beforePrivate=calls.length;
  const denied=await page.evaluate(async()=>{try{await window.PlaylistCloudProvider.request('/api/categories/suggestions');return null;}catch(error){return error.status;}});
  assert.equal(denied,401);assert.equal(calls.length,beforePrivate);assert.equal(await page.locator('#category-suggestions button').count(),0);
  checked('Page publique anonyme sans CRUD/classification globale/suggestions, et adapter bloque la requête privée avant réseau sans JWT');
  await page.locator('#organizer-auth-mode').click();assert.equal(await page.locator('#organizer-password').getAttribute('minlength'),'12');
  await page.locator('#organizer-email').fill('new-account@example.invalid');await page.locator('#organizer-password').fill('only-a-fixture-password');
  await page.locator('#organizer-auth-submit').click();await page.locator('#organizer-auth-feedback').filter({hasText:'confirmez'}).waitFor();
  assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null);assert.equal(await page.locator('#category-manager').isVisible(),false);
  assert.equal(await page.locator('#organizer-password').inputValue(),'');
  checked('Inscription simulée demande confirmation, ne connecte pas automatiquement, n’accorde aucun rôle et vide le mot de passe');
  await page.locator('#organizer-email').fill('visitor@example.invalid');await page.locator('#organizer-password').fill('fixture-password');
  await page.locator('#organizer-auth-submit').click();await page.locator('#organizer-auth-feedback').filter({hasText:'pas autorisé'}).waitFor();
  assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null);assert.equal(await page.locator('#category-manager').isVisible(),false);
  async function login() {
    await page.locator('#organizer-email').fill('organizer@example.invalid');await page.locator('#organizer-password').fill('fixture-password');
    await page.locator('#organizer-auth-submit').click();await page.locator('#category-manager').waitFor({state:'visible'});
  }
  await login();assert.equal(JSON.parse(await page.evaluate(key=>sessionStorage.getItem(key),key)).access_token,adminToken);
  assert.equal(await page.evaluate(token=>Object.values(localStorage).some(value=>String(value).includes(token)),adminToken),false);
  await page.locator('#category-show-suggestions').click();await page.locator('#category-suggestions button').waitFor();
  await page.locator('#category-submit').scrollIntoViewIfNeeded();await screen(page,'public-category-manager-mobile');
  const beforeSuggestion=mutationCount;await page.locator('#category-suggestions button').click();
  assert.equal(await page.locator('#category-name').inputValue(),'Un moment suggéré privé');assert.equal(mutationCount,beforeSuggestion);
  checked('Le rôle est vérifié côté RPC après GoTrue, visiteur refusé ; JWT limité à sessionStorage et suggestion privée remplit seulement le formulaire');
  await page.locator('#category-name').fill('Moment public créé');await page.locator('#category-order').fill('2');await page.locator('#category-submit').click();
  await page.locator(`[data-edit-category="${'c'.repeat(32)}"]`).waitFor();
  await page.locator(`[data-archive-category="${'c'.repeat(32)}"]`).click();
  await page.locator('.category-manager-row').filter({has:page.locator(`[data-edit-category="${'c'.repeat(32)}"]`)}).getByText('Archivé · choix existants conservés',{exact:false}).waitFor();
  assert.equal(categories.find(category=>category.id==='c'.repeat(32)).active,false);assert.equal(mutationCount,2);
  assert.equal(await page.locator(`#category-filter option[value="${'c'.repeat(32)}"]`).count(),0);
  await page.evaluate(key=>{const session=JSON.parse(sessionStorage.getItem(key));session.expires_at=0;sessionStorage.setItem(key,JSON.stringify(session));},key);
  await page.locator('#category-show-suggestions').click();await page.waitForFunction(({key,token})=>JSON.parse(sessionStorage.getItem(key)||'null')?.access_token===token,{key,token:refreshedToken});
  assert(calls.some(call=>call.path==='/auth/v1/token'&&call.grant==='refresh_token'));
  assert(calls.some(call=>call.path.endsWith('playlist25_category_suggestions')&&call.bearer===`Bearer ${refreshedToken}`));
  checked('CRUD et archivage publics utilisent le JWT autorisé, puis une expiration provoque un vrai flux navigateur de renouvellement simulé');
  await page.locator('#organizer-logout').click();await page.locator('#organizer-login-panel').waitFor({state:'visible'});
  assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null);assert.equal(await page.locator('#category-manager').isVisible(),false);
  assert.equal(await page.locator('#organizer-email').inputValue(),'');assert.equal(await page.locator('[data-category-proposal-id]').count(),0);
  await login();authorized=false;await page.reload();await page.locator('#stat-tracks').filter({hasText:'57'}).waitFor();await page.locator('#organisation-button').click();
  await page.locator('#organizer-login-panel').waitFor({state:'visible'});assert.equal(await page.locator('#category-manager').isVisible(),false);
  assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  await screen(page,'public-auth-mobile');
  checked('Déconnexion efface le JWT/rôle/champs sensibles ; rechargement revérifie un rôle révoqué et ne fait pas confiance à une session stockée');
}

async function main() {
  fs.mkdirSync(artifacts,{recursive:true});const gp=await port(),ap=await port();guestUrl=`http://127.0.0.1:${gp}`;adminUrl=`http://127.0.0.1:${ap}`;
  env={...process.env,PLAYLIST25_HOME:home,DATA_DIR:path.join(home,'data'),GUEST_PORT:String(gp),ADMIN_PORT:String(ap),ADMIN_PASSWORD:password,
    SESSION_SECRET:randomBytes(48).toString('base64url'),SUPABASE_URL:'',SUPABASE_SERVICE_ROLE_KEY:'',SUPABASE_ANON_KEY:''};
  for(const surface of ['guest','admin']){const child=spawn(python,['-c',fixture,surface],{cwd:root,env,stdio:['ignore','pipe','pipe']});child.logs='';for(const stream of [child.stdout,child.stderr])stream.on('data',chunk=>{child.logs=(child.logs+chunk).slice(-5000);});children.push(child);await waitHealth(child,surface==='guest'?guestUrl:adminUrl);}
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined),args:process.platform==='linux'?['--no-sandbox']:[]});
  const data=await localChecks(),beforePublic=JSON.parse(local('snapshot'));await publicChecks(data);assert.deepEqual(JSON.parse(local('snapshot')),beforePublic);
  for(const folder of ['audio','temporary','exports'])assert.deepEqual(fs.readdirSync(path.join(home,'data',folder)),[]);
  assert.equal(beforePublic.jobs.length,0);assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.consoleErrors,[]);assert.deepEqual(report.external,[]);
  checked('Aucune écriture locale pendant les mocks publics, email envoyé, audio, appel externe réel ou erreur JavaScript/console');
}
(async()=>{
  try{await main();report.success=true;}catch(error){report.success=false;report.error=error.stack||String(error);process.exitCode=1;process.stderr.write(report.error+'\n');}
  finally{
    if(browser)await browser.close().catch(()=>{});
    for(const child of children)if(child.exitCode===null&&child.signalCode===null){const ended=new Promise(resolve=>child.once('exit',resolve));let timer;child.kill('SIGTERM');await Promise.race([ended,new Promise(resolve=>{timer=setTimeout(resolve,5000);})]);clearTimeout(timer);if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await ended;}}
    fs.mkdirSync(artifacts,{recursive:true});fs.writeFileSync(path.join(artifacts,'report.json'),JSON.stringify(report,null,2));fs.rmSync(home,{recursive:true,force:true});process.stdout.write('Rapport catégories : '+path.join(artifacts,'report.json')+'\n');
  }
})();
