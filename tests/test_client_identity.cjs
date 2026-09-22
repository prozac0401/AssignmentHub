'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const http = require('node:http'), net = require('node:net');
let playwright, expect;
try { playwright = require('playwright'); ({expect} = require('playwright/test')); }
catch {
  const installed = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  playwright = require(installed); ({expect} = require(path.join(installed, 'test')));
}

// The browser's connections use distinct real socket source addresses. Header
// spoofing alone must never be able to select a different rate-limit bucket.
async function sourceProxy(localAddress) {
  const sockets = new Set();
  const server = http.createServer((request, response) => {
    const target = new URL(request.url);
    const upstream = http.request(target, {method: request.method, headers: request.headers, localAddress}, result => {
      response.writeHead(result.statusCode, result.headers); result.pipe(response);
    });
    upstream.on('error', () => { response.writeHead(502); response.end(); });
    request.pipe(upstream);
  });
  const tunnel = (request, socket, head, connect) => {
    const target = new URL(connect ? 'http://' + request.url : request.url);
    const upstream = net.connect({host: target.hostname, port: target.port || 80, localAddress}, () => {
      if (connect) socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      else upstream.write(`${request.method} ${target.pathname}${target.search} HTTP/${request.httpVersion}\r\n` +
        Object.entries(request.headers).map(([key, value]) => `${key}: ${value}`).join('\r\n') + '\r\n\r\n');
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    });
    upstream.on('error', () => socket.destroy());
    socket.on('close', () => upstream.destroy());
  };
  server.on('connect', (request, socket, head) => tunnel(request, socket, head, true));
  server.on('upgrade', (request, socket, head) => tunnel(request, socket, head, false));
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {url: `http://127.0.0.1:${server.address().port}`, close: () => {
    for (const socket of sockets) socket.destroy(); server.close();
  }};
}

(async () => {
  const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const blockedProxy = await sourceProxy('127.0.0.2'), healthyProxy = await sourceProxy('127.0.0.3');
  const browser = await playwright.chromium.launch({headless: true,
    args: ['--proxy-bypass-list=<-loopback>'],
    ...(os.platform() === 'win32' ? {executablePath: process.env.AH_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'} : {})});
  try {
    for (const [proxy, blocked] of [[blockedProxy, true], [healthyProxy, false]]) {
      const context = await browser.newContext({proxy: {server: proxy.url, bypass: '<-loopback>'},
        extraHTTPHeaders: {'X-AH-Client-IP': '192.0.2.99', 'X-AH-Gateway-Key': 'fake', 'X-Forwarded-For': '192.0.2.88'}});
      const page = await context.newPage();
      await page.goto(fixture.url);
      await page.getByRole('textbox', {name: '로그인 ID', exact: true}).fill('admin');
      await page.getByRole('textbox', {name: '비밀번호', exact: true}).fill(fixture.password);
      await page.getByRole('button', {name: '로그인', exact: true}).click();
      if (blocked) await expect(page.getByText('로그인 시도가 너무 많습니다. 15분 뒤 다시 시도하세요.', {exact: true})).toBeVisible();
      else await expect(page.getByRole('button', {name: '로그아웃', exact: true})).toBeVisible();
      assert.equal(await page.locator('[data-testid="stException"]').count(), 0);
      await context.close();
    }
    console.log('PASS: real gateway/Streamlit/API client isolation and forged browser headers');
  } finally {
    await browser.close(); blockedProxy.close(); healthyProxy.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
