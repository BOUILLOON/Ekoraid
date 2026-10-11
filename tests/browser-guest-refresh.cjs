/** Targeted guest UX on real HTTP + temporary SQLite; only YouTube is simulated.
 * Run: node tests/browser-guest-refresh.cjs. Requires Playwright and Chromium.
 * The periodic callback is invoked explicitly, without waiting thirty seconds.
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
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-guest-refresh-data-'));
const artifacts = process.env.PLAYLIST_GUEST_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-guest-refresh-'));
const report = { backend: 'real FastAPI HTTP and disposable SQLite', youtube: 'deterministic metadata only', audio: 'none',
  periodicRefresh: 'actual application interval callback invoked explicitly', checks: [], errors: [], external: [] };
let env, child, browser, url;
const fixture = `
import json,os,sys,time
from pathlib import Path
import uvicorn
from playlist25 import db,providers
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'data',session_secret=os.environ['SESSION_SECRET'],
 admin_password='temporary-fixture-password',guest_port=int(os.environ['GUEST_PORT']),admin_port=int(os.environ['ADMIN_PORT']),min_free_mb=0)
def entry(video_id,title):
 return dict(position=1,video_id=video_id,url='https://www.youtube.com/watch?v='+video_id,title=title,
 artist='',channel='Chaîne simulée',duration=183,thumbnail='',available=True,reason='')
first=[entry('alphaVid001','Connu Live Remix'),entry('betaVid0001','Même titre'),entry('gammaVid001','Même titre'),
 entry('deltaVid001','Chanson cover'),entry('epsilon0001','Chanson instrumental')]
second=[first[0],entry('zetaVid0001','Une autre version'),first[1],entry('thetaVid001','Reprise en concert'),first[2]]
def search_page(query,cfg,offset=0,limit=5):
 return dict(items=first if offset==0 else second,has_more=offset==0,next_offset=5 if offset==0 else None)
def analyze(url,cfg,mode=None):
 video_id=providers.parse_youtube_url(url,'video')['video_id']
 if video_id=='slowVid0001':time.sleep(.8)
 item=next((item.copy() for item in first+second if item['video_id']==video_id),entry(video_id,'Lien simulé '+video_id))
 return dict(kind='video',title=item['title'],source_url=item['url'],complete=True,warnings=[],entries=[item])
db.init_db(cfg)
action=sys.argv[1]
if action=='seed':
 with db.connect(cfg) as conn:
  conn.execute("INSERT INTO guests(id,nickname,created_at) VALUES('00000000-0000-4000-8000-000000000001','Bob',?)",(db.now(),))
  def proposal(item):
   source=db.upsert_source(conn,item)
   return conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,'00000000-0000-4000-8000-000000000001',?)",(source,db.now())).lastrowid
  known=proposal(first[0])
  for index in range(27):proposal(dict(title='Catalogue initial '+str(index)))
  conn.commit();print(json.dumps(dict(known=known,count=28)))
elif action=='setting':
 with db.connect(cfg) as conn:db.set_setting(conn,'offline_mode',sys.argv[2]=='true');conn.commit()
elif action=='add':
 with db.connect(cfg) as conn:
  source=db.upsert_source(conn,dict(title='Nouvelle proposition extérieure',duration=90))
  proposal=conn.execute("INSERT INTO proposals(source_id,guest_id,created_at) VALUES(?,'00000000-0000-4000-8000-000000000001',?)",(source,db.now())).lastrowid
  conn.commit();print(json.dumps(dict(id=proposal)))
elif action=='count':
 with db.connect(cfg) as conn:print(conn.execute('SELECT COUNT(*) FROM proposals').fetchone()[0])
else:
 providers.search_page=search_page;providers.analyze=analyze
 uvicorn.run(create_app('guest',cfg),host='127.0.0.1',port=cfg.guest_port,access_log=False,log_level='warning')
`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
function local(action, ...args) { return execFileSync(python, ['-c', fixture, action, ...args], { cwd: root, env, encoding: 'utf8' }).trim(); }
async function port() {
  const server = net.createServer(); await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const value = server.address().port; await new Promise(resolve => server.close(resolve)); return value;
}
async function tick(page) {
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/proposals');
  await page.evaluate(() => window.__catalogueTick()); assert.equal((await response).status(), 200);
  await page.waitForFunction(() => !document.querySelector('#stat-tracks').textContent.includes('…'));
}
async function search(page) {
  await page.locator('#tab-search').click(); await page.locator('#search-input').fill('versions');
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/search');
  await page.locator('#search-form button[type="submit"]').click(); assert.equal((await response).status(), 200);
  await page.locator('[data-search-id="alphaVid001"]').waitFor();
}
async function main() {
  fs.mkdirSync(artifacts, { recursive: true });
  const guestPort = await port(), adminPort = await port(); url = `http://127.0.0.1:${guestPort}`;
  env = { ...process.env, PLAYLIST25_HOME: home, DATA_DIR: path.join(home, 'data'), GUEST_PORT: String(guestPort), ADMIN_PORT: String(adminPort),
    SESSION_SECRET: randomBytes(48).toString('base64url'), SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '' };
  const seeded = JSON.parse(local('seed'));
  child = spawn(python, ['-c', fixture, 'serve'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.logs = ''; for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.logs = (child.logs + chunk).slice(-5000); });
  const deadline = Date.now() + 20000;
  while (true) {
    if (child.exitCode !== null) throw new Error(child.logs);
    try { if ((await fetch(`${url}/health`)).ok) break; } catch {}
    if (Date.now() > deadline) throw new Error(`Guest server did not start: ${child.logs}`); await pause(100);
  }
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  await context.route('**/*', async route => {
    if (new URL(route.request().url()).origin === url) return route.continue();
    report.external.push(route.request().url()); await route.abort('blockedbyclient');
  });
  await context.addInitScript(() => {
    const interval = window.setInterval;
    window.setInterval = (callback, delay, ...args) => {
      if (delay === 30000) { window.__catalogueTick = () => callback(...args); return 123456; }
      return interval(callback, delay, ...args);
    };
  });
  const session = await (await context.request.get(`${url}/api/session`)).json();
  assert.equal((await context.request.post(`${url}/api/identity`, { data: { nickname: 'Alice' }, headers: { 'X-CSRF-Token': session.csrf_token } })).status(), 200);
  const authenticated = await (await context.request.get(`${url}/api/session`)).json();
  const page = await context.newPage(), posts = [], searches = [];
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/proposals') posts.push(request.postDataJSON());
    if (new URL(request.url()).pathname === '/api/search') searches.push(Object.fromEntries(new URL(request.url()).searchParams));
  });
  await page.goto(url); await page.locator('#stat-tracks').filter({ hasText: String(seeded.count) }).waitFor();
  await search(page); assert.equal(await page.locator('#search-results article').count(), 5);
  const next = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/search'); await page.locator('#search-more').click(); assert.equal((await next).status(), 200);
  await page.locator('[data-search-id="thetaVid001"]').waitFor();
  assert.equal(await page.locator('#search-results article').count(), 7);
  assert.equal(await page.locator('[data-search-id="betaVid0001"]').count(), 1); assert.equal(await page.locator('[data-search-id="gammaVid001"]').count(), 1);
  assert.equal(await page.locator('#search-more').isVisible(), false);
  assert.deepEqual(searches.slice(0, 2), [{ q: 'versions', offset: '0', limit: '5' }, { q: 'versions', offset: '5', limit: '5' }]);
  for (const label of ['Live', 'Reprise', 'Remix', 'Instrumental']) {
    const tag = page.locator('#search-results .version-tag').filter({ hasText: label }).first(); await tag.waitFor(); assert.match(await tag.getAttribute('title'), /indicatif/);
  }
  checked('Deux pages demandées, vidéos répétées dédupliquées, mêmes titres avec IDs distincts conservés, fin et labels indicatifs affichés');

  const draft = { search: 'versions', link: 'https://youtu.be/newlink0001', title: 'Une idée en cours', artist: 'Artiste brouillon' };
  await page.locator('#tab-link').click(); await page.locator('#link-input').fill(draft.link);
  await page.locator('#tab-manual').click(); await page.locator('#manual-title').fill(draft.title); await page.locator('#manual-artist').fill(draft.artist);
  const beforeRestore = posts.length, count = Number(local('count'));
  await page.reload(); await page.locator('#guest-name').filter({ hasText: 'Alice' }).waitFor();
  assert.equal(await page.locator('#search-input').inputValue(), draft.search); assert.equal(await page.locator('#link-input').inputValue(), draft.link);
  assert.equal(await page.locator('#manual-title').inputValue(), draft.title); assert.equal(await page.locator('#manual-artist').inputValue(), draft.artist);
  local('setting', 'true'); await page.reload(); await page.locator('#global-notice').filter({ hasText: 'hors ligne' }).waitFor();
  assert.equal(await page.locator('#search-input').isDisabled(), true); assert.equal(await page.locator('#search-input').inputValue(), draft.search);
  assert.equal(await page.locator('#link-input').inputValue(), draft.link); assert.equal(await page.locator('#manual-title').inputValue(), draft.title);
  assert.equal(posts.length, beforeRestore); assert.equal(Number(local('count')), count);
  checked('Brouillons de chaque mode restaurés après rechargement et hors ligne sans proposition envoyée');
  const manual = page.waitForResponse(reply => reply.request().method() === 'POST' && new URL(reply.url()).pathname === '/api/proposals');
  await page.locator('#manual-form button[type="submit"]').click(); assert.equal((await manual).status(), 200);
  await page.waitForFunction(() => document.querySelector('#manual-title').value === '');
  assert.equal(await page.locator('#manual-artist').inputValue(), ''); assert.equal(await page.locator('#search-input').inputValue(), draft.search); assert.equal(await page.locator('#link-input').inputValue(), draft.link);
  local('setting', 'false'); await page.reload(); await page.locator('#guest-name').filter({ hasText: 'Alice' }).waitFor();
  assert.equal(await page.locator('#manual-title').inputValue(), ''); assert.equal(await page.locator('#search-input').inputValue(), draft.search); assert.equal(await page.locator('#link-input').inputValue(), draft.link);
  checked('Créer une idée efface uniquement son mode de brouillon, et conserve les autres après rechargement');

  await page.locator('#tab-link').click(); await page.locator('#link-input').fill('https://youtu.be/slowVid0001');
  const slowRequest = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/proposals');
  const slowResponse = page.waitForResponse(reply => reply.request().method() === 'POST' && new URL(reply.url()).pathname === '/api/proposals');
  await page.locator('#link-form button[type="submit"]').click(); await slowRequest;
  await page.locator('#link-input').fill('https://youtu.be/nextVid0001'); assert.equal((await slowResponse).status(), 200);
  await page.waitForFunction(() => !document.querySelector('#link-form button[type="submit"]').disabled);
  assert.equal(await page.locator('#link-input').inputValue(), 'https://youtu.be/nextVid0001');
  checked('Une réponse de proposition retardée conserve le nouveau lien déjà saisi');

  await search(page);
  const knownCard = page.locator(`[data-proposal-id="${seeded.known}"]`), vote = knownCard.locator('.vote-button');
  const oldVote = await vote.elementHandle(), oldSearch = await page.locator('[data-search-id="alphaVid001"]').elementHandle();
  await page.locator('#search-input').focus();
  await page.locator('#search-input').evaluate(input => input.setSelectionRange(1, 5));
  const input = await page.locator('#search-input').elementHandle();
  await page.evaluate(() => window.scrollTo({ top: 420, behavior: 'instant' }));
  const beforeScroll = await page.evaluate(() => scrollY);
  const added = JSON.parse(local('add'));
  assert.equal((await context.request.post(`${url}/api/proposals/${seeded.known}/vote`, { data: { active: true }, headers: { 'X-CSRF-Token': authenticated.csrf_token } })).status(), 200);
  await tick(page); await page.locator('#catalogue-updates').waitFor({ state: 'visible' });
  assert.equal(await page.locator(`[data-proposal-id="${added.id}"]`).count(), 0);
  assert.equal(await vote.getAttribute('aria-pressed'), 'true'); assert.equal(await page.locator('#stat-tracks').innerText(), String(count + 3));
  assert.equal(await page.evaluate(node => document.activeElement === node && node.selectionStart === 1 && node.selectionEnd === 5, input), true);
  assert.equal(await page.evaluate(node => node === document.querySelector(`[data-proposal-id="${node.closest('[data-proposal-id]').dataset.proposalId}"] .vote-button`), oldVote), true);
  assert.equal(await page.evaluate(node => node === document.querySelector('[data-search-id="alphaVid001"]'), oldSearch), true);
  assert.ok(Math.abs(await page.evaluate(() => scrollY) - beforeScroll) <= 1);
  checked('Tick conserve les nœuds, focus/sélection et scroll ; votes/compteurs changent mais les nouvelles cartes attendent le clic');
  const remove = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/proposals/${seeded.known}/vote`);
  await vote.click(); assert.equal((await remove).status(), 200); await page.waitForFunction(id => document.querySelector(`[data-proposal-id="${id}"] .vote-button`).getAttribute('aria-pressed') === 'false', seeded.known);
  await page.locator('#apply-catalogue-updates').click(); await page.locator(`[data-proposal-id="${added.id}"]`).waitFor();
  assert.equal(await page.locator('#catalogue-updates').isVisible(), false);
  const addVote = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/proposals/${seeded.known}/vote`);
  await vote.click(); assert.equal((await addVote).status(), 200); await tick(page);
  const removeAgain = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/proposals/${seeded.known}/vote`);
  await vote.click(); assert.equal((await removeAgain).status(), 200); await page.waitForFunction(id => document.querySelector(`[data-proposal-id="${id}"] .vote-button`).getAttribute('aria-pressed') === 'false', seeded.known);
  checked('Afficher les nouveautés ajoute les cartes ; les boutons conservés savent ajouter puis retirer le vote même après un tick');
  for (const selector of [`[data-proposal-id="${seeded.known}"] .vote-button`, '#search-results [data-preview-id="alphaVid001"]']) {
    await page.locator(selector).evaluate(node => {
      node.scrollIntoView({ block: 'center', behavior: 'instant' });
      node.focus({ preventScroll: true });
    });
    const control = await page.locator(selector).elementHandle(), scroll = await page.evaluate(() => scrollY);
    const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/proposals');
    await page.evaluate(() => window.__catalogueTick()); assert.equal((await response).status(), 200);
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(node => document.activeElement === node && node.isConnected, control), true);
    const after = await page.evaluate(() => scrollY);
    assert.ok(Math.abs(after - scroll) <= 1, `${selector}: tick moved scroll from ${scroll} to ${after}`);
  }
  checked('Un tick conserve aussi le focus et le défilement sur les boutons de vote et de résultat de recherche');
  for (const directory of ['audio', 'temporary', 'exports']) assert.deepEqual(fs.readdirSync(path.join(home, 'data', directory)), []);
  assert.deepEqual(report.errors, []); assert.deepEqual(report.external, []);
  await page.screenshot({ path: path.join(artifacts, 'guest-refresh.png'), fullPage: true });
  checked('Aucun fichier audio, worker, appel externe ou exception JavaScript');
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
    fs.rmSync(home, { recursive: true, force: true }); process.stdout.write(`Rapport invité : ${path.join(artifacts, 'report.json')}\n`);
  }
})();
