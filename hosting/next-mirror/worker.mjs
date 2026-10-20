// models.facemorph.me: a second copy of next.facemorph.me's immutable runtime files, held in R2.
// On 20 October one Cloudflare edge answered HTTP 500 from static-asset storage for single encoder
// chunks for ten minutes at a time; the page now retries such a chunk here, on separate storage.
// A file not yet held is copied from the site through the service binding on first request (the
// deploy warms every runtime path with HEAD), and only under the immutable, content-addressed
// prefixes: the page checks every byte against its pin, so a stale or wrong copy can only fail.
export const MIRRORED = ['runtime/assets/', 'runtime/chunks/', 'runtime/manifests/', 'runtime/photo/', 'runtime/encoder-stream/'];
const SITE = 'https://next.facemorph.me/';
const SHARED = { 'Access-Control-Allow-Origin': '*', 'Cross-Origin-Resource-Policy': 'cross-origin', 'X-Content-Type-Options': 'nosniff' };
const empty = (status, extra = {}) => new Response(null, { status, headers: { ...SHARED, 'Cache-Control': 'no-store', ...extra } });
function reply(object, body) {
  return new Response(body, { status: 200, headers: { ...SHARED,
    'Content-Type': object.httpMetadata?.contentType || 'application/octet-stream',
    'Content-Length': String(object.size), ETag: object.httpEtag,
    'Cache-Control': 'public, max-age=31536000, immutable' } });
}
export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return empty(204, { 'Access-Control-Allow-Methods': 'GET, HEAD', 'Access-Control-Max-Age': '86400' });
    if (request.method !== 'GET' && request.method !== 'HEAD') return empty(405, { Allow: 'GET, HEAD' });
    const key = decodeURIComponent(new URL(request.url).pathname.slice(1));
    if (!MIRRORED.some(prefix => key.startsWith(prefix)) || key.split('/').some(part => part === '..' || part === '')) return empty(404);
    const held = request.method === 'HEAD' ? await env.MIRROR.head(key) : await env.MIRROR.get(key);
    if (held) return reply(held, request.method === 'HEAD' ? null : held.body);
    const origin = await env.SITE.fetch(new Request(SITE + key));
    if (origin.status !== 200) { void origin.body?.cancel(); return empty(origin.status === 404 ? 404 : 502); }
    // Runtime files are at most 25 MiB (the static-asset limit), so buffering one is cheap.
    const bytes = await origin.arrayBuffer();
    const saved = await env.MIRROR.put(key, bytes, { httpMetadata: { contentType: origin.headers.get('Content-Type') || 'application/octet-stream' } });
    return reply(saved, request.method === 'HEAD' ? null : bytes);
  }
};
