// Разбор сообщения Telegram в действие над ящиком — чистые функции, без сети.
// Worker (worker.js) только исполняет то, что вернул `decide`.

export const ACCEPTED = 'принято';
export const TITLE_MAX = 70;

const ATTACHMENTS = ['photo', 'document', 'voice', 'audio', 'video', 'video_note', 'animation', 'sticker'];

const HELP =
  'Пиши или пересылай сюда — каждое сообщение станет задачей во входящих Штаба. ' +
  'Ответ на «#N принято» — комментарий к #N. Вложения не переношу — только текст и подпись.';

// Что делать с update. Возвращает одно из:
// { kind: 'ignore' } — чужое или пустое, молчим;
// { kind: 'reply', text } — только ответить;
// { kind: 'issue', title, body, note } — завести задачу;
// { kind: 'comment', issue, body, note } — комментарий к задаче.
// `note` — добавка к ответу бота (про вложение), может быть пустой.
export function decide(update, ownerId) {
  const msg = update && update.message;
  if (!msg || !msg.from || !msg.chat) return { kind: 'ignore' };
  if (String(msg.from.id) !== String(ownerId) || msg.chat.type !== 'private') return { kind: 'ignore' };

  const text = (msg.text ?? msg.caption ?? '').trim();
  const hasAttachment = ATTACHMENTS.some((k) => msg[k] !== undefined);
  const note = hasAttachment ? 'Вложение не взято — только текст.' : '';

  if (!text) {
    return hasAttachment ? { kind: 'reply', text: 'Вложения не переношу — напиши словами.' } : { kind: 'ignore' };
  }
  if (/^\/(start|help)(@\w+)?$/.test(text)) return { kind: 'reply', text: HELP };

  const body = header(msg) + '\n\n' + text + (hasAttachment ? '\n\n_Было вложение — не перенесено._' : '');

  const issue = issueFromReply(msg.reply_to_message);
  if (issue) return { kind: 'comment', issue, body, note };

  return { kind: 'issue', title: titleOf(text), body, note };
}

// Шапка входящего — та, что ждёт agents/specifier.md.
export function header(msg) {
  const origin = forwardedFrom(msg);
  const author = origin ?? 'владелец';
  const trust = origin ? 'третье лицо' : 'владелец';
  return ['Канал: telegram', `Автор: ${author}`, `Доверие: ${trust}`].join('\n');
}

// Имя отправителя пересланного сообщения или null, если сообщение своё.
export function forwardedFrom(msg) {
  const o = msg.forward_origin;
  if (o) {
    if (o.type === 'user' && o.sender_user) return personName(o.sender_user);
    if (o.type === 'hidden_user') return o.sender_user_name || 'скрытый отправитель';
    if (o.type === 'chat' && o.sender_chat) return o.sender_chat.title || 'чат';
    if (o.type === 'channel' && o.chat) return o.chat.title || 'канал';
    return 'неизвестный отправитель';
  }
  // Старые поля Bot API — на случай, если forward_origin не пришёл.
  if (msg.forward_from) return personName(msg.forward_from);
  if (msg.forward_sender_name) return msg.forward_sender_name;
  if (msg.forward_from_chat) return msg.forward_from_chat.title || 'канал';
  return null;
}

function personName(u) {
  const name = [u.first_name, u.last_name].filter(Boolean).join(' ');
  return u.username ? `${name} (@${u.username})` : name || 'без имени';
}

export function titleOf(text) {
  const line = text.split('\n').find((l) => l.trim()) ?? '';
  const flat = line.trim().replace(/\s+/g, ' ');
  return flat.length <= TITLE_MAX ? flat : flat.slice(0, TITLE_MAX - 1).trimEnd() + '…';
}

// Текст ответа бота; номер из него читает issueFromReply — хранилища нет.
export function acceptedText(number, url) {
  return `#${number} ${ACCEPTED}\n${url}`;
}

// Номер задачи из сообщения бота, на которое ответил владелец, или null.
export function issueFromReply(reply) {
  if (!reply || !reply.from || !reply.from.is_bot) return null;
  const m = /^#(\d+) /.exec(reply.text ?? '');
  return m ? Number(m[1]) : null;
}

// Сверка секрета вебхука без раннего выхода на первом несовпадении.
export function sameSecret(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || !b) return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) diff |= (a.charCodeAt(i) || 0) ^ b.charCodeAt(i);
  return diff === 0;
}
