import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decide, titleOf, acceptedText, issueFromReply, sameSecret, TITLE_MAX } from '../src/inbox.js';

const OWNER = 111;
const msg = (extra) => ({ message: { message_id: 7, from: { id: OWNER }, chat: { id: OWNER, type: 'private' }, ...extra } });

test('своё сообщение — задача с шапкой владельца', () => {
  const a = decide(msg({ text: 'Сделать фильтр\nподробности' }), OWNER);
  assert.equal(a.kind, 'issue');
  assert.equal(a.title, 'Сделать фильтр');
  assert.equal(a.body, 'Канал: telegram\nАвтор: владелец\nДоверие: владелец\n\nСделать фильтр\nподробности');
  assert.equal(a.note, '');
});

test('id владельца строкой из секрета — тоже владелец', () => {
  assert.equal(decide(msg({ text: 'x' }), String(OWNER)).kind, 'issue');
});

test('чужой отправитель, группа, пустое, не сообщение — молчим', () => {
  assert.equal(decide({ message: { from: { id: 222 }, chat: { id: 222, type: 'private' }, text: 'x' } }, OWNER).kind, 'ignore');
  assert.equal(decide({ message: { from: { id: OWNER }, chat: { id: -5, type: 'group' }, text: 'x' } }, OWNER).kind, 'ignore');
  assert.equal(decide(msg({ text: '   ' }), OWNER).kind, 'ignore');
  assert.equal(decide({ edited_message: {} }, OWNER).kind, 'ignore');
  assert.equal(decide(null, OWNER).kind, 'ignore');
  assert.equal(decide(msg({ text: 'x' }), undefined).kind, 'ignore');
});

test('пересланное от человека — третье лицо с именем', () => {
  const a = decide(msg({ text: 'хочу кнопку', forward_origin: { type: 'user', sender_user: { first_name: 'Аня', last_name: 'К', username: 'anya' } } }), OWNER);
  assert.match(a.body, /^Канал: telegram\nАвтор: Аня К \(@anya\)\nДоверие: третье лицо\n/);
});

test('пересланное: скрытый отправитель, канал, старые поля', () => {
  const hidden = decide(msg({ text: 'x', forward_origin: { type: 'hidden_user', sender_user_name: 'Петя' } }), OWNER);
  assert.match(hidden.body, /Автор: Петя\nДоверие: третье лицо/);
  const channel = decide(msg({ text: 'x', forward_origin: { type: 'channel', chat: { title: 'Новости' } } }), OWNER);
  assert.match(channel.body, /Автор: Новости\nДоверие: третье лицо/);
  const old = decide(msg({ text: 'x', forward_from: { first_name: 'Оля' } }), OWNER);
  assert.match(old.body, /Автор: Оля\nДоверие: третье лицо/);
});

test('ответ на «#N принято» — комментарий к N', () => {
  const reply = { from: { is_bot: true }, text: acceptedText(42, 'https://github.com/x/y/issues/42') };
  const a = decide(msg({ text: 'и ещё', reply_to_message: reply }), OWNER);
  assert.equal(a.kind, 'comment');
  assert.equal(a.issue, 42);
});

test('ответ на «комментарий добавлен» — туда же', () => {
  const a = decide(msg({ text: 'и ещё', reply_to_message: { from: { is_bot: true }, text: '#42 — комментарий добавлен' } }), OWNER);
  assert.equal(a.issue, 42);
});

test('ответ на своё сообщение или не на номер — новая задача', () => {
  assert.equal(decide(msg({ text: 'x', reply_to_message: { from: { id: OWNER, is_bot: false }, text: '#5 принято' } }), OWNER).kind, 'issue');
  assert.equal(decide(msg({ text: 'x', reply_to_message: { from: { is_bot: true }, text: 'Пиши сюда' } }), OWNER).kind, 'issue');
});

test('фото с подписью — задача без вложения, с пометкой', () => {
  const a = decide(msg({ photo: [{}], caption: 'Сломан экран' }), OWNER);
  assert.equal(a.kind, 'issue');
  assert.match(a.body, /Сломан экран\n\n_Было вложение — не перенесено._$/);
  assert.equal(a.note, 'Вложение не взято — только текст.');
});

test('вложение без текста — только ответ', () => {
  const a = decide(msg({ voice: {} }), OWNER);
  assert.equal(a.kind, 'reply');
});

test('/start и /help — подсказка, не задача', () => {
  assert.equal(decide(msg({ text: '/start' }), OWNER).kind, 'reply');
  assert.equal(decide(msg({ text: '/help@shtab_bot' }), OWNER).kind, 'reply');
  assert.equal(decide(msg({ text: '/старт' }), OWNER).kind, 'issue');
});

test('заголовок — первая непустая строка, длинная — обрезана', () => {
  assert.equal(titleOf('\n\n  первая   строка  \nвторая'), 'первая строка');
  const long = titleOf('а'.repeat(200));
  assert.equal(long.length, TITLE_MAX);
  assert.ok(long.endsWith('…'));
});

test('номер из ответа бота читается обратно', () => {
  assert.equal(issueFromReply({ from: { is_bot: true }, text: acceptedText(7, 'u') }), 7);
  assert.equal(issueFromReply(undefined), null);
});

test('секрет вебхука', () => {
  assert.equal(sameSecret('abc', 'abc'), true);
  assert.equal(sameSecret('abd', 'abc'), false);
  assert.equal(sameSecret('abcd', 'abc'), false);
  assert.equal(sameSecret('ab', 'abc'), false);
  assert.equal(sameSecret('', ''), false);
  assert.equal(sameSecret('x', undefined), false);
});
