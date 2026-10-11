/** Real private API + SQLite browser integration. Only YouTube metadata is fake.
 * Run: node tests/browser-admin-search.cjs (Playwright + Chromium required).
 * No worker, audio, cloud configuration, or external network is used.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-admin-search-data-'));
const artifacts = process.env.PLAYLIST_ADMIN_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-admin-search-'));
const password = randomBytes(24).toString('base64url');
const report = { backend: 'real FastAPI HTTP and disposable SQLite', youtube: 'deterministic metadata provider only',
  audio: 'none', workerStarted: false, checks: [], pageErrors: [], externalRequests: [], failedRequests: [], screenshots: [] };
let browser, child, env, adminUrl;

const fixture = `
import json,os,sys,time
from pathlib import Path
import uvicorn
from playlist25 import db,providers
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'data',admin_password=os.environ['ADMIN_PASSWORD'],
 session_secret=os.environ['SESSION_SECRET'],admin_port=int(os.environ['ADMIN_PORT']),
 guest_port=int(os.environ['GUEST_PORT']),min_free_mb=0,search_limit=5)
def entry(video_id,title,duration=183):
 return dict(position=1,video_id=video_id,url='https://www.youtube.com/watch?v='+video_id,
 title=title,artist='',channel='Chaîne simulée',duration=duration,thumbnail='',available=True,reason='')
entries=[entry('adminPick01','Résultat recherché — studio'),entry('takenVid001','Version déjà proposée'),
 entry('otherVid001','Version partagée'),entry('moreVid0001','Version live',201),
 entry('moreVid0002','Version remix',204),entry('moreVid0003','Version instrumentale',207)]
def record(action,**fields):
 with (home/'provider-calls.jsonl').open('a',encoding='utf-8') as out:
  out.write(json.dumps(dict(action=action,**fields),ensure_ascii=False)+'\\n')
def search_page(query,cfg,offset=0,limit=5):
 record('search',query=query,offset=offset,limit=limit)
 if query=='lent':time.sleep(.6)
 items=entries[offset:offset+limit]
 return dict(items=items,has_more=offset+len(items)<len(entries),next_offset=offset+len(items) if offset+len(items)<len(entries) else None)
def analyze(url,cfg,mode=None):
 parsed=providers.parse_youtube_url(url,mode)
 item=next(item.copy() for item in entries if item['video_id']==parsed.get('video_id'))
 record('analyze',video_id=item['video_id'])
 return dict(kind='video',title=item['title'],source_url=item['url'],entries=[item],complete=True,warnings=[])
db.init_db(cfg)
if sys.argv[1]=='seed':
 with db.connect(cfg) as conn:
  for guest,name in [('alice','Alice'),('bob','Bob')]:
   conn.execute('INSERT INTO guests(id,nickname,created_at) VALUES(?,?,?)',(guest,name,db.now()))
  def proposal(source,guest='alice',state='pending',withdrawn=None):
   return conn.execute('INSERT INTO proposals(source_id,guest_id,state,sort_order,created_at,withdrawn_at) VALUES(?,?,?,13,?,?)',(source,guest,state,db.now(),withdrawn)).lastrowid
  original=db.upsert_source(conn,dict(title='Chanson demandée',artist='Artiste demandé'))
  requested=proposal(original,state='approved')
  taken=proposal(db.upsert_source(conn,entries[1]))
  shared=proposal(db.upsert_source(conn,entries[2]),guest='bob')
  withdrawn=proposal(db.upsert_source(conn,dict(title='Idée retirée')),state='archived',withdrawn=db.now())
  for target,guest in [(requested,'alice'),(requested,'bob'),(taken,'bob'),(shared,'alice')]:
   conn.execute('INSERT INTO votes(proposal_id,guest_id,created_at) VALUES(?,?,?)',(target,guest,db.now()))
  conn.commit()
  print(json.dumps(dict(requested=requested,original=original,taken=taken,shared=shared,withdrawn=withdrawn)))
elif sys.argv[1]=='snapshot':
 with db.connect(cfg) as conn:
  print(json.dumps({table:[dict(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in ['sources','proposals','votes','jobs','job_events','guests']},ensure_ascii=False))
else:
 providers.search_page=search_page
 providers.analyze=analyze
 uvicorn.run(create_app('admin',cfg),host='127.0.0.1',port=cfg.admin_port,access_log=False,log_level='warning')
`;

function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function port() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const result = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return result;
}
function snapshot() { return JSON.parse(execFileSync(python, ['-c', fixture, 'snapshot'], { cwd: root, env, encoding: 'utf8' })); }
function providerCalls() {
  const file = path.join(home, 'provider-calls.jsonl');
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
}
function noAudio() {
  for (const directory of ['audio', 'temporary', 'exports']) assert.deepEqual(fs.readdirSync(path.join(home, 'data', directory)), [], `${directory} must remain empty`);
  const data = snapshot();
  assert.deepEqual(data.jobs, []); assert.deepEqual(data.job_events, []);
  assert.ok(data.sources.every(source => source.file_path === null));
}
async function waitHealth() {
  const until = Date.now() + 20000;
  while (Date.now() < until) {
    if (child.exitCode !== null) throw new Error(`Test server exited ${child.exitCode}: ${child.logs}`);
    try { if ((await fetch(`${adminUrl}/health`)).ok) return; } catch {}
    await pause(100);
  }
  throw new Error(`Test server did not start: ${child.logs}`);
}
async function openEdit(page, title, nickname = null) {
  let row = page.locator('#admin-proposals tr').filter({ hasText: title });
  if (nickname) row = row.filter({ hasText: nickname });
  await row.getByRole('button', { name: 'Modifier', exact: true }).click();
  await page.locator('#edit-dialog').waitFor({ state: 'visible' });
}
async function search(page, query) {
  await page.locator('#edit-search-query').fill(query);
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/search');
  await page.locator('#edit-search-button').click();
  assert.equal((await response).status(), 200);
  await page.locator('[data-edit-source-id="adminPick01"]').waitFor();
}
async function cancel(page) {
  await page.locator('#edit-form').getByRole('button', { name: 'Annuler', exact: true }).click();
  await page.locator('#edit-dialog').waitFor({ state: 'hidden' });
}
async function save(page, id, expectedStatus) {
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/proposals/${id}` && reply.request().method() === 'PATCH');
  await page.locator('#edit-form button[type="submit"]').click();
  const result = await response; assert.equal(result.status(), expectedStatus);
  return result.json();
}
async function screen(page, name) {
  const target = path.join(artifacts, `${name}.png`);
  await page.screenshot({ path: target, fullPage: true }); report.screenshots.push(target);
}

async function main() {
  fs.mkdirSync(artifacts, { recursive: true });
  const adminPort = await port(), guestPort = await port();
  adminUrl = `http://127.0.0.1:${adminPort}`;
  env = { ...process.env, PLAYLIST25_HOME: home, DATA_DIR: path.join(home, 'data'), ADMIN_PORT: String(adminPort),
    GUEST_PORT: String(guestPort), ADMIN_PASSWORD: password, SESSION_SECRET: randomBytes(48).toString('base64url'),
    SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '' };
  const ids = JSON.parse(execFileSync(python, ['-c', fixture, 'seed'], { cwd: root, env, encoding: 'utf8' }));
  const initial = snapshot(); noAudio();
  child = spawn(python, ['-c', fixture, 'serve'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.logs = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.logs = (child.logs + chunk).slice(-5000); });
  await waitHealth();
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined),
    args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', async route => {
    if (new URL(route.request().url()).origin === adminUrl) return route.continue();
    report.externalRequests.push(route.request().url()); await route.abort('blockedbyclient');
  });
  const anonymousSession = await (await context.request.get(`${adminUrl}/api/session`)).json();
  assert.equal((await context.request.post(`${adminUrl}/api/search`, { data: { query: 'chanson', offset: 0 }, headers: { 'X-CSRF-Token': anonymousSession.csrf_token } })).status(), 401);
  assert.deepEqual(providerCalls(), []); assert.deepEqual(snapshot(), initial);
  checked('Recherche privée refusée sans connexion, avant tout appel du fournisseur et sans écriture');
  const page = await context.newPage();
  page.on('pageerror', error => report.pageErrors.push(error.message));
  page.on('requestfailed', request => report.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await page.goto(adminUrl); await page.locator('#admin-password').fill(password);
  await page.locator('#login-form button[type="submit"]').click();
  await page.locator('#admin-layout').waitFor({ state: 'visible' });
  await page.locator('[data-view="proposals"]').click(); await page.locator('#admin-proposals tr').first().waitFor();
  assert.equal((await context.request.post(`${adminUrl}/api/search`, { data: { query: 'chanson', offset: 0 } })).status(), 403);
  assert.deepEqual(providerCalls(), []);
  checked('Session connectée également soumise au contrôle CSRF');

  await openEdit(page, 'Chanson demandée');
  assert.equal(await page.locator('#edit-search-query').inputValue(), 'Artiste demandé Chanson demandée');
  await search(page, 'chanson studio');
  assert.equal(await page.locator('#edit-search-results article').count(), 5);
  const first = page.locator('#edit-search-results article').filter({ has: page.locator('[data-edit-source-id="adminPick01"]') });
  assert.match(await first.innerText(), /Chaîne simulée/); assert.match(await first.innerText(), /3:03/);
  const moreResponse = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/search');
  await page.locator('#edit-search-more').click(); assert.equal((await moreResponse).status(), 200);
  await page.locator('[data-edit-source-id="moreVid0003"]').waitFor();
  assert.equal(await page.locator('#edit-search-results article').count(), 6);
  assert.equal(await page.locator('#edit-search-more').isVisible(), false);
  assert.deepEqual(providerCalls().filter(call => call.action === 'search').map(call => [call.query, call.offset, call.limit]), [['chanson studio', 0, 5], ['chanson studio', 5, 5]]);
  assert.deepEqual(snapshot(), initial);
  checked('Recherche préremplie, titres/chaînes/durées et page suivante réels dans le contrat du fournisseur simulé');
  await page.locator('[data-edit-source-id="adminPick01"]').click();
  assert.equal(await page.locator('#edit-url').inputValue(), 'https://www.youtube.com/watch?v=adminPick01');
  assert.equal(await page.locator('#edit-title').inputValue(), 'Résultat recherché — studio');
  assert.equal(await page.locator('#edit-artist').inputValue(), 'Artiste demandé');
  assert.equal(await page.locator('[data-edit-source-id="adminPick01"]').getAttribute('aria-pressed'), 'true');
  assert.deepEqual(snapshot(), initial); assert.equal(providerCalls().filter(call => call.action === 'analyze').length, 0);
  await screen(page, 'admin-search-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile dialog must not overflow');
  await screen(page, 'admin-search-mobile');
  await cancel(page); noAudio(); assert.deepEqual(snapshot(), initial);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openEdit(page, 'Chanson demandée');
  assert.equal(await page.locator('#edit-title').inputValue(), 'Chanson demandée');
  assert.equal(await page.locator('#edit-url').inputValue(), ''); assert.equal(await page.locator('#edit-search-results article').count(), 0);
  checked('Choisir remplit les champs provisoires ; Annuler ne crée ni source, ni fichier, ni tâche et efface le choix');

  await search(page, 'chanson studio'); await page.locator('[data-edit-source-id="adminPick01"]').click();
  const saved = await save(page, ids.requested, 200); await page.locator('#edit-dialog').waitFor({ state: 'hidden' });
  assert.equal(saved.item.id, ids.requested); assert.equal(saved.item.guest_id, 'alice'); assert.equal(saved.item.nickname, 'Alice');
  assert.equal(saved.item.state, 'approved'); assert.equal(saved.item.sort_order, 13); assert.equal(saved.item.duration, 183);
  assert.equal(saved.item.video_id, 'adminPick01'); assert.equal(saved.item.channel, 'Chaîne simulée'); assert.equal(saved.item.votes, 2);
  const afterSave = snapshot(); assert.deepEqual(afterSave.votes, initial.votes); assert.deepEqual(afterSave.guests, initial.guests);
  assert.ok(afterSave.sources.some(source => source.id === ids.original && source.title === 'Chanson demandée'));
  assert.equal(afterSave.sources.filter(source => source.video_id === 'adminPick01').length, 1); noAudio();
  checked('Enregistrer vérifie la source et sa durée, conserve proposition/propriétaire/votes/ordre/ancienne source, sans téléchargement');

  await openEdit(page, 'Résultat recherché — studio'); await search(page, 'doublon');
  await page.locator('[data-edit-source-id="takenVid001"]').click();
  const duplicate = await save(page, ids.requested, 409);
  assert.equal(duplicate.detail.duplicate_id, ids.taken);
  await page.locator('#edit-feedback').filter({ hasText: 'Fusionnez' }).waitFor();
  assert.deepEqual(snapshot(), afterSave); noAudio(); await cancel(page);
  checked('Changer vers une source déjà proposée par la même personne refuse la sauvegarde atomiquement et demande une fusion explicite');

  await openEdit(page, 'Résultat recherché — studio'); await search(page, 'partagée');
  await page.locator('[data-edit-source-id="otherVid001"]').click();
  const shared = await save(page, ids.requested, 200); await page.locator('#edit-dialog').waitFor({ state: 'hidden' });
  const afterShared = snapshot();
  assert.equal(shared.item.source_id, initial.proposals.find(item => item.id === ids.shared).source_id);
  assert.equal(shared.item.guest_id, 'alice'); assert.equal(shared.item.votes, 2);
  assert.match(shared.warnings.join(' '), /fusion reste une action manuelle/);
  assert.deepEqual(afterShared.votes, initial.votes);
  assert.equal(afterShared.proposals.find(item => item.id === ids.shared).guest_id, 'bob');
  assert.equal(afterShared.proposals.find(item => item.id === ids.shared).state, 'pending');
  assert.equal(afterShared.proposals.length, initial.proposals.length); noAudio();
  checked('Source partagée entre invités réutilisée sans fusion automatique ni déplacement des votes');

  await openEdit(page, 'Idée retirée'); assert.equal(await page.locator('#edit-search').isVisible(), false); await cancel(page);
  await openEdit(page, 'Version partagée', 'Alice');
  await page.locator('#edit-search-query').fill('lent');
  const requested = page.waitForRequest(request => new URL(request.url()).pathname === '/api/search');
  await page.locator('#edit-search-button').click(); await requested; await page.keyboard.press('Escape');
  await page.locator('#edit-dialog').waitFor({ state: 'hidden' }); await pause(800);
  assert.equal(await page.locator('#edit-search-results article').count(), 0); assert.deepEqual(snapshot(), afterShared); noAudio();
  checked('Recherche masquée pour un retrait ; fermer une recherche en cours empêche tout résultat tardif et toute écriture');
  assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.externalRequests, []);
  assert.ok(report.failedRequests.every(request => new URL(request.url).pathname === '/api/search' && /ERR_ABORTED/.test(request.error)), 'Only the deliberately cancelled search may fail');
  checked('Aucune connexion externe, exception JavaScript ou création audio ; seule la recherche annulée est interrompue');
}

(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      let timer;
      child.kill('SIGTERM');
      await Promise.race([exited, new Promise(resolve => { timer = setTimeout(resolve, 5000); })]);
      clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; }
    }
    fs.mkdirSync(artifacts, { recursive: true });
    fs.writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(home, { recursive: true, force: true });
    process.stdout.write(`Rapport navigateur : ${path.join(artifacts, 'report.json')}\n`);
  }
})();
