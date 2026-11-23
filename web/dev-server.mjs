/*
 * Run the site locally without a Vercel account: static files from public/ and the api/ routes.
 *
 *   PREDICT_API_KEY=... node dev-server.mjs          # against the deployed model API
 *   MODEL_API_URL=http://localhost:8001 node dev-server.mjs   # against a local `uvicorn model.api:...`
 *
 * `vercel dev` does the same once the project is linked.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = fileURLToPath(new URL('.', import.meta.url));
const PUBLIC = join(ROOT, 'public');
const PORT = Number(process.env.PORT || 3000);
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml' };

createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  try {
    const route = url.pathname.match(/^\/api\/([a-z-]+)$/);
    if (route) {
      const mod = await import(pathToFileURL(join(ROOT, 'api', `${route[1]}.js`)).href).catch(() => null);
      if (!mod?.GET || req.method !== 'GET') return send(res, 404, 'text/plain', 'Not found');
      const response = await mod.GET(new Request(url, { headers: req.headers }));
      res.writeHead(response.status, Object.fromEntries(response.headers));
      return res.end(Buffer.from(await response.arrayBuffer()));
    }
    const path = normalize(join(PUBLIC, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname)));
    if (!path.startsWith(PUBLIC + sep)) return send(res, 403, 'text/plain', 'Forbidden');
    send(res, 200, TYPES[extname(path)] || 'application/octet-stream', await readFile(path));
  } catch (error) {
    if (error.code === 'ENOENT') return send(res, 404, 'text/plain', 'Not found');
    console.error(error);
    send(res, 500, 'text/plain', 'Server error');
  }
}).listen(PORT, () => console.log(`http://localhost:${PORT}`));

function send(res, status, type, body) {
  res.writeHead(status, { 'Content-Type': type });
  res.end(body);
}
