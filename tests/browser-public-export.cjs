/** Public CSV/TXT downloads on real guest HTTP and disposable SQLite.
 * Run: node tests/browser-public-export.cjs. Requires Playwright and Chromium.
 * No worker, cloud write, provider request or external audio is used.
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
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-public-export-data-'));
const artifacts = process.env.PLAYLIST_EXPORT_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-public-export-browser-'));
const report = { backend: 'real FastAPI HTTP and disposable SQLite', exports: 'actual browser Blob downloads',
  checks: [], errors: [], external: [], screenshots: [], audio: 'none', cloud: 'none' };
let env, child, browser, url;
const fixture = String.raw`
import json,os,sys,base64,io
from pathlib import Path
from PIL import Image
import uvicorn
from playlist25 import db
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'data',session_secret=os.environ['SESSION_SECRET'],
 admin_password='private-password-export-marker',guest_port=int(os.environ['GUEST_PORT']),admin_port=int(os.environ['ADMIN_PORT']),min_free_mb=0)
db.init_db(cfg)
action=sys.argv[1]
if action=='seed':
 alice=sys.argv[2];bob='00000000-0000-4000-8000-000000000002'
 image=Image.new('RGB',(64,64),(60,90,180));buffer=io.BytesIO();image.save(buffer,format='PNG')
 photo='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
 (home/'.env.local').write_text('ADMIN_PASSWORD=private-password-export-marker\nSUPABASE_SERVICE_ROLE_KEY=private-config-export-marker\n',encoding='utf-8')
 with db.connect(cfg) as conn:
  conn.execute('UPDATE guests SET avatar_data=?,avatar_version=? WHERE id=?',(photo,'a'*64,alice))
  conn.execute('INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES(?,?,?,?,?)',(bob,'+Bob; "DJ"',db.now(),photo,'b'*64))
  def proposal(item,author=bob,state='pending',withdrawn=False):
   source=db.upsert_source(conn,item)
   return conn.execute('INSERT INTO proposals(source_id,guest_id,state,created_at,withdrawn_at) VALUES(?,?,?,?,?)',(source,author,state,db.now(),db.now() if withdrawn else None)).lastrowid
  first=dict(video_id='BaW_jenozKc',url='https://youtu.be/BaW_jenozKc?t=8',title='=SUM(1;2)',artist='+Chanteuse',duration=123.5,file_path='private-local-audio-marker.mp3')
  first_id=proposal(first,alice);proposal(first,bob)
  category_id='00000000-0000-4000-8000-000000000099'
  conn.execute('INSERT INTO categories(id,name,sort_order,active) VALUES(?,?,?,1)',(category_id,'@Électro; "remix"',999))
  conn.execute('UPDATE proposals SET category_ids=? WHERE id=?',(json.dumps([category_id]),first_id))
  proposal(dict(video_id='alphaVid001',url='https://music.youtube.com/watch?v=alphaVid001&list=PLpublicExport12345',title='-Live version',artist='@Auteur',duration=183))
  playlist=dict(url='https://m.youtube.com/playlist?list=PLpublicExport12345&feature=share',title='Playlist; «Fanny»')
  playlist_source=db.upsert_source(conn,playlist)
  for author in (alice,bob):conn.execute('INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)',(playlist_source,author,db.now()))
  formulas=[chr(0x200b)+'+SUM(1;2)',' @table',' -42','=HYPERLINK("https://fixture.invalid";"é")']
  for title in formulas:proposal(dict(title=title))
  special='Été; "Fanny" 🎉'+chr(10)+'ligne suivante'
  proposal(dict(title=special,artist='Auteure; "é"'))
  for index in range(55):proposal(dict(title='Catalogue initial '+str(index).zfill(3)),alice if index%2 else bob)
  for marker,state,withdrawn in [('HIDDEN_REJECTED_EXCLUDED','rejected',False),('HIDDEN_ARCHIVED_EXCLUDED','archived',False),('HIDDEN_WITHDRAWN_EXCLUDED','pending',True)]:proposal(dict(title=marker),bob,state,withdrawn)
  conn.execute('INSERT INTO votes VALUES(?,?,?)',(first_id,alice,db.now()));conn.execute('INSERT INTO votes VALUES(?,?,?)',(first_id,bob,db.now()))
  conn.commit();print(json.dumps(dict(visible=65,alice=alice,bob=bob,special=special,formulas=formulas,category_id=category_id)))
elif action=='add':
 with db.connect(cfg) as conn:
  for item in [dict(title='Fraîche idée ajoutée ailleurs',artist='Auteur récent'),dict(video_id='freshVid001',url='https://youtu.be/freshVid001',title='Fresh version ailleurs')]:
   source=db.upsert_source(conn,item);conn.execute('INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)',(source,sys.argv[2],db.now()))
  conn.commit()
elif action=='add-unseen':
 with db.connect(cfg) as conn:
  source=db.upsert_source(conn,dict(title='UNSEEN_AFTER_SNAPSHOT'))
  conn.execute('INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,?,?)',(source,sys.argv[2],db.now()));conn.commit()
elif action=='snapshot':
 with db.connect(cfg) as conn:
  print(json.dumps({table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in ['sources','proposals','guests','votes','jobs','job_events']}))
else:uvicorn.run(create_app('guest',cfg),host='127.0.0.1',port=cfg.guest_port,access_log=False,log_level='warning')
`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
function local(action, ...args) { return execFileSync(python, ['-c', fixture, action, ...args], { cwd: root, env, encoding: 'utf8' }).trim(); }
async function port() {
  const server = net.createServer(); await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const value = server.address().port; await new Promise(resolve => server.close(resolve)); return value;
}
function csv(file) {
  const bytes = fs.readFileSync(file); assert.deepEqual([...bytes.subarray(0, 3)], [239, 187, 191]);
  const text = bytes.toString('utf8'); assert(text.includes('\r\n')); assert(text.startsWith('\uFEFF"Titre";'));
  return JSON.parse(execFileSync(python, ['-c', 'import csv,json,sys\nwith open(sys.argv[1],encoding="utf-8-sig",newline="") as f: print(json.dumps(list(csv.DictReader(f,delimiter=";"))))', file], { encoding: 'utf8' }));
}
async function download(page, format, name) {
  const received = page.waitForEvent('download'); await page.locator(`#public-export-${format}`).click();
  const result = await received; assert.equal(await result.failure(), null);
  const file = path.join(artifacts, `${name}.${format}`); await result.saveAs(file);
  return { file, suggested: result.suggestedFilename(), text: fs.readFileSync(file, 'utf8') };
}
async function closeExport(page) {
  await page.locator('[data-close-dialog="public-export-dialog"]').click();
  await page.locator('#public-export-dialog').waitFor({ state: 'hidden' });
}
function assertConfidential(text, seeded) {
  for (const value of ['private-password-export-marker', 'private-config-export-marker', 'private-local-audio-marker', 'data:image/',
    seeded.alice, seeded.bob, seeded.category_id, env.SESSION_SECRET, 'HIDDEN_REJECTED_EXCLUDED', 'HIDDEN_ARCHIVED_EXCLUDED', 'HIDDEN_WITHDRAWN_EXCLUDED'])
    assert(!text.includes(value), `Public export leaked ${value}`);
}
async function main() {
  fs.mkdirSync(artifacts, { recursive: true });
  const guestPort = await port(), adminPort = await port(); url = `http://127.0.0.1:${guestPort}`;
  env = { ...process.env, PLAYLIST25_HOME: home, DATA_DIR: path.join(home, 'data'), GUEST_PORT: String(guestPort), ADMIN_PORT: String(adminPort),
    SESSION_SECRET: randomBytes(48).toString('base64url'), SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '' };
  child = spawn(python, ['-c', fixture, 'serve'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.logs = ''; for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.logs = (child.logs + chunk).slice(-5000); });
  const deadline = Date.now() + 20000;
  while (true) {
    if (child.exitCode !== null) throw new Error(child.logs);
    try { if ((await fetch(`${url}/health`)).ok) break; } catch {}
    if (Date.now() > deadline) throw new Error(`Guest server did not start: ${child.logs}`); await pause(100);
  }
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  await context.route('**/*', async route => {
    if (new URL(route.request().url()).origin === url) return route.continue();
    report.external.push(route.request().url()); await route.abort('blockedbyclient');
  });
  await context.addInitScript(() => {
    const interval = window.setInterval;
    window.setInterval = (callback, delay, ...args) => delay === 30000 ? 123456 : interval(callback, delay, ...args);
  });
  const anonymous = await (await context.request.get(`${url}/api/session`)).json();
  const identity = await context.request.post(`${url}/api/identity`, { data: { nickname: '=Alice; "DJ"' }, headers: { 'X-CSRF-Token': anonymous.csrf_token } });
  assert.equal(identity.status(), 200); const alice = (await identity.json()).guest.id;
  const seeded = JSON.parse(local('seed', alice)), initial = JSON.parse(local('snapshot'));
  const page = await context.newPage(), requests = [];
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => requests.push({ method: request.method(), path: new URL(request.url()).pathname, query: new URL(request.url()).search }));
  await page.goto(url); await page.locator('#stat-tracks').filter({ hasText: String(seeded.visible) }).waitFor();
  assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(), 48);
  await page.locator('#proposal-next').click(); await page.locator('#proposal-page-label').filter({ hasText: 'Page 2 sur 2' }).waitFor();
  const pageLabel = await page.locator('#proposal-page-label').innerText(), cards = await page.locator('#proposal-list [data-proposal-id]').evaluateAll(nodes => nodes.map(node => node.dataset.proposalId));
  local('add', alice); const afterAdd = JSON.parse(local('snapshot'));
  assert.equal(await page.getByText('Fraîche idée ajoutée ailleurs', { exact: true }).count(), 0);
  await page.locator('#save-public-list').click(); await page.locator('#public-export-dialog').waitFor({ state: 'visible' });
  const online = await download(page, 'csv', 'fresh-list'); const rows = csv(online.file);
  assert.equal(rows.length, 67);
  assert(rows.some(row => row.Titre === 'Fraîche idée ajoutée ailleurs' && row['Lien YouTube'] === '' && row.Type === 'Idée sans lien'));
  assert.equal(await page.locator('#proposal-page-label').innerText(), pageLabel);
  assert.deepEqual(await page.locator('#proposal-list [data-proposal-id]').evaluateAll(nodes => nodes.map(node => node.dataset.proposalId)), cards);
  assertConfidential(online.text, seeded);
  checked('CSV réel contient la collecte fraîche complète au-delà des 48 cartes, y compris idées manuelles, sans changer la page affichée');
  const first = rows.find(row => row.Titre === "'=SUM(1;2)");
  assert(first); assert.equal(first.Artiste, "'+Chanteuse"); assert.equal(first['Votes'], '2'); assert.equal(first['Durée (secondes)'], '123.5');
  assert.equal(first['Proposé par'], "'=Alice; \"DJ\"");
  assert.equal(first['Catégories'], "'@Électro; \"remix\"");
  assert(rows.some(row => row.Titre === seeded.special));
  for (const title of seeded.formulas) assert(rows.some(row => row.Titre === "'" + title));
  assert(rows.some(row => row.Artiste === "'@Auteur"));
  assert(online.text.includes('"Été; ""Fanny"" 🎉'));
  assert(!Object.keys(first).some(key => /(?:avatar|photo|secret|csrf|token|source_id|file_path|guest_id)/i.test(key)));
  assert(rows.every(row => row['Collecte récupérée le'] === first['Collecte récupérée le']));
  checked('UTF-8 BOM, séparateur point-virgule, guillemets/newlines/unicode, auteurs/votes/durée et neutralisation des formules vérifiés avec csv standard');
  await closeExport(page); await page.locator('#filter-mine').click(); await page.locator('#catalogue-query').fill('Catalogue initial');
  await page.waitForTimeout(300); const filtered = await page.locator('#proposal-list [data-proposal-id]').count(); assert(filtered < 48);
  await page.locator('#save-public-list').click();
  const links = await download(page, 'txt', 'canonical-links');
  const expected = ['https://www.youtube.com/watch?v=BaW_jenozKc', 'https://www.youtube.com/watch?v=alphaVid001',
    'https://www.youtube.com/playlist?list=PLpublicExport12345', 'https://www.youtube.com/watch?v=freshVid001'];
  assert.deepEqual(links.text.split(/\r?\n/).filter(Boolean).sort(), expected.sort());
  assertConfidential(links.text, seeded);
  assert.equal(await page.locator('#catalogue-query').inputValue(), 'Catalogue initial');
  assert.match(await page.locator('#filter-mine').getAttribute('class'), /active/);
  assert.equal(await page.locator('#proposal-list [data-proposal-id]').count(), filtered);
  const textSnapshot = await download(page, 'csv', 'filter-independent-list'); const cachedRows = csv(textSnapshot.file);
  assert.equal(cachedRows.length, 67);
  checked('TXT exporte tous les liens vidéo et playlists canoniques, une fois chacun, en ignorant filtre personnel/recherche et pagination');
  assert.deepEqual(JSON.parse(local('snapshot')), afterAdd);
  local('add-unseen', alice); const offlineDatabase = JSON.parse(local('snapshot'));
  await context.setOffline(true); const beforeOffline = requests.length;
  const offline = await download(page, 'csv', 'offline-copy'); const offlineRows = csv(offline.file);
  assert.equal(offlineRows.length, 67); assert(!offline.text.includes('UNSEEN_AFTER_SNAPSHOT'));
  assert.deepEqual(offlineRows, cachedRows); assert.match(offline.suggested, /-copie\.csv$/);
  assert.equal(requests.length, beforeOffline);
  assert.match(await page.locator('#public-export-feedback').innerText(), /Connexion indisponible.*copie récupérée le.*peuvent manquer/);
  assertConfidential(offline.text, seeded);
  checked('Hors ligne le navigateur sauve uniquement son dernier instantané visible, daté et marqué copie, sans lire les ajouts ultérieurs');
  await context.setOffline(false);
  await page.route('**/api/proposals?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Provider fixture unavailable' }) }));
  const fallback = await download(page, 'txt', 'failed-read-copy'); assert.match(fallback.suggested, /-copie\.txt$/);
  assert.deepEqual(fallback.text.split(/\r?\n/).filter(Boolean).sort(), expected.sort());
  await closeExport(page);
  const freshPage = await context.newPage(); freshPage.on('pageerror', error => report.errors.push(error.message));
  await freshPage.route('**/api/proposals?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'No loaded collection fixture' }) }));
  let unexpectedDownloads = 0; freshPage.on('download', () => { unexpectedDownloads++; });
  await freshPage.goto(url); await freshPage.locator('#save-public-list').click(); await freshPage.locator('#public-export-csv').click();
  await freshPage.locator('#public-export-feedback').filter({ hasText: 'aucune liste' }).waitFor();
  await freshPage.waitForTimeout(150); assert.equal(unexpectedDownloads, 0);
  checked('Échec réseau utilise une copie existante avec avertissement ; aucune copie ne produit aucun faux fichier ni faux succès');
  await page.unroute('**/api/proposals?*');
  let requestStarted, releaseResponse;
  const started = new Promise(resolve => { requestStarted = resolve; });
  const gate = new Promise(resolve => { releaseResponse = resolve; });
  await page.route('**/api/proposals?*', async route => {
    const response = await route.fetch(); requestStarted(); await gate;
    try { await route.fulfill({ response }); } catch { /* The real browser request was cancelled by closing its dialog. */ }
  });
  let cancelledDownloads = 0; page.on('download', () => { cancelledDownloads++; });
  await page.locator('#save-public-list').click(); await page.locator('#public-export-csv').click(); await started;
  await closeExport(page); releaseResponse(); await page.waitForTimeout(200);
  assert.equal(cancelledDownloads, 0);
  await page.unroute('**/api/proposals?*');
  await context.setOffline(true); await page.locator('#save-public-list').click();
  const afterCancel = await download(page, 'csv', 'unchanged-cache-after-cancel');
  assert.deepEqual(csv(afterCancel.file), cachedRows); assert(!afterCancel.text.includes('UNSEEN_AFTER_SNAPSHOT'));
  await context.setOffline(false);
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileScreenshot = path.join(artifacts, 'public-export-dialog-mobile.png');
  await page.screenshot({ path: mobileScreenshot, fullPage: false }); report.screenshots.push(mobileScreenshot);
  checked('Fermer le dialogue annule une réponse HTTP retardée sans téléchargement ni remplacement de la copie déjà chargée');
  assert.deepEqual(JSON.parse(local('snapshot')), offlineDatabase);
  assert(requests.every(request => request.method !== 'POST'));
  for (const folder of ['audio', 'temporary', 'exports']) assert.deepEqual(fs.readdirSync(path.join(home, 'data', folder)), []);
  assert.deepEqual(report.errors, []); assert.deepEqual(report.external, []);
  await page.screenshot({ path: path.join(artifacts, 'public-export-copy.png'), fullPage: true });
  checked('Exports publics sans modification SQLite, proposition/vote envoyé, fichier audio, appel externe ou exception JavaScript');
}
(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    if (child && child.exitCode === null && child.signalCode === null) {
      const ended = new Promise(resolve => child.once('exit', resolve)); let timer; child.kill('SIGTERM');
      await Promise.race([ended, new Promise(resolve => { timer = setTimeout(resolve, 5000); })]); clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await ended; }
    }
    fs.mkdirSync(artifacts, { recursive: true }); fs.writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(home, { recursive: true, force: true }); process.stdout.write(`Rapport export public : ${path.join(artifacts, 'report.json')}\n`);
  }
})();
