import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.js';

const env = { TELEGRAM_TOKEN: 'tg', GITHUB_TOKEN: 'gh', WEBHOOK_SECRET: 's3cret', OWNER_ID: '111', REPO: 'o/hq', LABEL: 'входящее' };
let calls;
let githubStatus;

beforeEach(() => {
  calls = [];
  githubStatus = 201;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(init.body), headers: init.headers });
    if (String(url).startsWith('https://api.github.com')) {
      const ok = githubStatus < 300;
      return { ok, status: githubStatus, json: async () => ({ number: 9, html_url: 'https://github.com/o/hq/issues/9' }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
});

const post = (update, secret = 's3cret') =>
  worker.fetch(
    new Request('https://w.example/', { method: 'POST', headers: { 'X-Telegram-Bot-Api-Secret-Token': secret }, body: JSON.stringify(update) }),
    env,
  );
const fromOwner = (extra) => ({ message: { message_id: 5, from: { id: 111 }, chat: { id: 111, type: 'private' }, ...extra } });

test('неверный или пустой секрет — 403, в сеть не ходит', async () => {
  assert.equal((await post(fromOwner({ text: 'x' }), 'wrong')).status, 403);
  assert.equal((await post(fromOwner({ text: 'x' }), '')).status, 403);
  assert.equal(calls.length, 0);
});

test('сообщение владельца — issue с меткой и ответ «#9 принято»', async () => {
  const res = await post(fromOwner({ text: 'Новая мысль' }));
  assert.equal(res.status, 200);
  assert.equal(calls[0].url, 'https://api.github.com/repos/o/hq/issues');
  assert.deepEqual(calls[0].body.labels, ['входящее']);
  assert.equal(calls[0].headers.Authorization, 'Bearer gh');
  assert.equal(calls[1].url, 'https://api.telegram.org/bottg/sendMessage');
  assert.equal(calls[1].body.text, '#9 принято\nhttps://github.com/o/hq/issues/9');
  assert.equal(calls[1].body.reply_parameters.message_id, 5);
});

test('ответ на «#9 принято» — комментарий к 9', async () => {
  await post(fromOwner({ text: 'добавка', reply_to_message: { from: { is_bot: true }, text: '#9 принято\nu' } }));
  assert.equal(calls[0].url, 'https://api.github.com/repos/o/hq/issues/9/comments');
  assert.equal(calls[1].body.text, '#9 — комментарий добавлен');
});

test('GitHub отказал — владелец узнаёт код, Telegram получает 200', async () => {
  githubStatus = 401;
  const res = await post(fromOwner({ text: 'x' }));
  assert.equal(res.status, 200);
  assert.equal(calls[1].body.text, 'Не записал: GitHub ответил 401');
});

test('чужой — 200 и тишина', async () => {
  const res = await post({ message: { message_id: 1, from: { id: 222 }, chat: { id: 222, type: 'private' }, text: 'x' } });
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0);
});

test('битый JSON — 200 и тишина', async () => {
  const res = await worker.fetch(new Request('https://w.example/', { method: 'POST', headers: { 'X-Telegram-Bot-Api-Secret-Token': 's3cret' }, body: '{' }), env);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0);
});

test('/setup: без ключа — 403; с ключом — setWebhook на свой адрес с секретом', async () => {
  assert.equal((await worker.fetch(new Request('https://w.example/setup?key=no'), env)).status, 403);
  const res = await worker.fetch(new Request('https://w.example/setup?key=s3cret'), env);
  assert.equal(res.status, 200);
  assert.equal(calls[0].url, 'https://api.telegram.org/bottg/setWebhook');
  assert.equal(calls[0].body.url, 'https://w.example/');
  assert.equal(calls[0].body.secret_token, 's3cret');
});

test('другие пути и методы — 404', async () => {
  assert.equal((await worker.fetch(new Request('https://w.example/'), env)).status, 404);
  assert.equal((await worker.fetch(new Request('https://w.example/x', { method: 'POST' }), env)).status, 404);
});
