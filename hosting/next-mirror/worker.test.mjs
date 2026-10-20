import test from 'node:test'; import assert from 'node:assert/strict'; import worker from './worker.mjs';
const chunk = 'runtime/encoder-stream/417811d879ac9ef5a8714f38a453571d86e7168047e3d22b7d809b74905bf468/part-091.onnx';
function env(site = async () => new Response(new Uint8Array([1, 2, 3]), { headers: { 'Content-Type': 'application/octet-stream' } })) {
  const objects = new Map(), asked = [];
  const object = (key, bytes, meta) => ({ size: bytes.byteLength, httpEtag: '"e"', httpMetadata: meta, body: new Response(bytes).body });
  return { objects, asked, MIRROR: {
    head: async key => objects.has(key) ? object(key, ...objects.get(key)) : null,
    get: async key => objects.has(key) ? object(key, ...objects.get(key)) : null,
    put: async (key, bytes, { httpMetadata }) => { objects.set(key, [new Uint8Array(bytes), httpMetadata]); return object(key, new Uint8Array(bytes), httpMetadata); }
  }, SITE: { fetch: async request => { asked.push(request.url); return site(request); } } };
}
const get = (path, method = 'GET') => new Request('https://models.facemorph.me/' + path, { method });
test('a file not yet held is copied from the site once, then served from R2 with CORS', async () => {
  const e = env();
  const first = await worker.fetch(get(chunk), e);
  assert.equal(first.status, 200); assert.deepEqual([...new Uint8Array(await first.arrayBuffer())], [1, 2, 3]);
  assert.equal(first.headers.get('Access-Control-Allow-Origin'), '*'); assert.equal(first.headers.get('Cross-Origin-Resource-Policy'), 'cross-origin');
  assert.deepEqual(e.asked, ['https://next.facemorph.me/' + chunk]);
  const again = await worker.fetch(get(chunk), e);
  assert.deepEqual([...new Uint8Array(await again.arrayBuffer())], [1, 2, 3]); assert.equal(e.asked.length, 1);
});
test('HEAD warms the copy without a body', async () => {
  const e = env(), r = await worker.fetch(get(chunk, 'HEAD'), e);
  assert.equal(r.status, 200); assert.equal(r.headers.get('Content-Length'), '3'); assert.ok(e.objects.has(chunk));
});
test('mutable or unsafe paths are never copied, and a site failure is not stored', async () => {
  const e = env();
  for (const path of ['runtime/manifest.json', 'index.html', 'runtime/chunks/../manifest.json', 'runtime/chunks//x'])
    assert.equal((await worker.fetch(get(path), e)).status, 404);
  assert.equal(e.asked.length, 0);
  const failing = env(async () => new Response('fault', { status: 500 }));
  assert.equal((await worker.fetch(get(chunk), failing)).status, 502); assert.equal(failing.objects.size, 0);
  assert.equal((await worker.fetch(get(chunk), env(async () => new Response('', { status: 404 })))).status, 404);
  assert.equal((await worker.fetch(get(chunk, 'POST'), e)).status, 405);
});
