// 서비스워커: 오프라인에서도 앱이 열리게 화면을 저장해 두되,
// 인터넷이 될 때는 항상 서버의 최신 화면(index.html)을 먼저 받는다.
// → index.html만 고쳤을 때는 CACHE_NAME을 올리지 않아도 새 버전이 적용된다.
//   (이 파일 자체나 아이콘·manifest를 바꿀 때만 올린다)
const CACHE_NAME = 'my-life-v30';
const PAGE = './index.html';
const ASSETS = [PAGE, './manifest.json', './icon-192.png', './icon-512.png'];
const PAGE_WAIT_MS = 3000; // 연결이 느리면 이만큼만 기다리고 저장된 화면을 보여준다
const PAGE_TAG = './__page-tag'; // 마지막으로 받은 화면의 표식(ETag 등)을 적어 두는 자리
function tagOf(res) { return res.headers.get('etag') || res.headers.get('last-modified') || ''; }

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(async c => {
    await c.addAll(ASSETS.map(u => new Request(u, { cache: 'reload' })));
    const page = await c.match(PAGE); // 처음 설치할 때 받은 화면의 표식도 적어 둔다
    const tag = page ? tagOf(page) : '';
    if (tag) await c.put(PAGE_TAG, new Response(tag));
  }));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))));
  self.clients.claim();
});

// 서버에서 최신 화면을 받아 저장한다. changed: 저장돼 있던 것과 내용이 다른가
async function fetchPage() {
  const cache = await caches.open(CACHE_NAME);
  const res = await fetch(PAGE, { cache: 'no-store' });
  if (!res || !res.ok) throw new Error('page fetch failed: ' + (res && res.status));
  const old = await cache.match(PAGE);
  const [fresh, saved] = await Promise.all([res.clone().text(), old ? old.text() : null]);
  await cache.put(PAGE, res.clone());
  const tag = tagOf(res);
  if (tag) await cache.put(PAGE_TAG, new Response(tag)); else await cache.delete(PAGE_TAG);
  return { res, changed: saved !== null && saved !== fresh };
}

// 가벼운 확인: 서버의 표식이 저장해 둔 것과 같으면 화면 파일을 내려받지 않는다 (데이터를 거의 쓰지 않음)
async function pageUnchanged() {
  try {
    const cache = await caches.open(CACHE_NAME);
    const saved = await cache.match(PAGE_TAG);
    if (!saved) return false;
    const tag = await saved.text();
    const head = await fetch(PAGE, { method: 'HEAD', cache: 'no-store' });
    return head.ok && !!tag && tagOf(head) === tag;
  } catch (e) { return false; }
}

async function tellClients() {
  const list = await self.clients.matchAll({ type: 'window' });
  list.forEach(c => c.postMessage({ type: 'app-updated' }));
}

async function pageResponse() {
  const cache = await caches.open(CACHE_NAME);
  const saved = await cache.match(PAGE);
  const fresh = fetchPage();
  if (!saved) return (await fresh).res;
  let timer;
  const timeout = new Promise(resolve => { timer = setTimeout(() => resolve(null), PAGE_WAIT_MS); });
  try {
    const got = await Promise.race([fresh, timeout]);
    clearTimeout(timer);
    if (got) return got.res; // 최신 화면을 바로 보여줌
  } catch (err) {
    clearTimeout(timer);
    return saved; // 오프라인 등: 저장된 화면
  }
  // 느린 연결: 일단 저장된 화면을 보여주고, 뒤늦게 받은 것이 다르면 앱에 알린다
  fresh.then(got => { if (got.changed) tellClients(); }).catch(() => {});
  return saved;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // Firebase 등 외부 요청은 건드리지 않는다
  const isPage = req.mode === 'navigate' || url.pathname.endsWith('/') || url.pathname.endsWith('/index.html');
  if (isPage) { e.respondWith(pageResponse()); return; }
  e.respondWith(caches.match(req).then(r => r || fetch(req)));
});

// 앱이 "새 버전 있는지 봐줘"라고 물으면 확인해서, 바뀌었으면 알린다
self.addEventListener('message', e => {
  if (!e.data || e.data.type !== 'check-update') return;
  e.waitUntil(pageUnchanged().then(same => {
    if (same) return null;
    return fetchPage().then(got => { if (got.changed) return tellClients(); });
  }).catch(() => {}));
});
