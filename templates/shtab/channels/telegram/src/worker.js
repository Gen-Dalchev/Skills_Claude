// Cloudflare Worker: вебхук Telegram → issue `входящее` в репо штаба.
// Решает inbox.js; здесь — только сеть.
import { decide, acceptedText, sameSecret } from './inbox.js';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Разовая привязка вебхука: владелец открывает /setup?key=<WEBHOOK_SECRET>.
    if (request.method === 'GET' && url.pathname === '/setup') {
      if (!sameSecret(url.searchParams.get('key') ?? '', env.WEBHOOK_SECRET)) return new Response('нет', { status: 403 });
      const res = await telegram(env, 'setWebhook', {
        url: `${url.origin}/`,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ['message'],
        drop_pending_updates: true,
      });
      return new Response(res.ok ? 'Вебхук привязан' : `Telegram ответил ${res.status}`, { status: res.ok ? 200 : 502 });
    }

    if (request.method !== 'POST' || url.pathname !== '/') return new Response('нет', { status: 404 });
    if (!sameSecret(request.headers.get('X-Telegram-Bot-Api-Secret-Token') ?? '', env.WEBHOOK_SECRET)) {
      return new Response('нет', { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response('ok');
    }
    const action = decide(update, env.OWNER_ID);
    // Telegram всегда получает 200: иначе он шлёт то же сообщение снова и задача задвоится.
    if (action.kind === 'ignore') return new Response('ok');

    const chatId = update.message.chat.id;
    const replyTo = update.message.message_id;
    let text;
    if (action.kind === 'reply') {
      text = action.text;
    } else if (action.kind === 'issue') {
      const res = await github(env, `/repos/${env.REPO}/issues`, { title: action.title, body: action.body, labels: [env.LABEL] });
      text = res.ok ? acceptedText(res.data.number, res.data.html_url) : `Не записал: GitHub ответил ${res.status}`;
    } else {
      const res = await github(env, `/repos/${env.REPO}/issues/${action.issue}/comments`, { body: action.body });
      text = res.ok ? `#${action.issue} — комментарий добавлен` : `Не записал к #${action.issue}: GitHub ответил ${res.status}`;
    }
    if (action.note) text += `\n${action.note}`;
    await telegram(env, 'sendMessage', {
      chat_id: chatId,
      text,
      reply_parameters: { message_id: replyTo },
      link_preview_options: { is_disabled: true },
    });
    return new Response('ok');
  },
};

async function github(env, path, payload) {
  const res = await fetch(`https://api.github.com${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'shtab-telegram',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  // Тело ответа при ошибке не пересылается — только код (может содержать лишнее).
  return { ok: res.ok, status: res.status, data: res.ok ? await res.json() : null };
}

async function telegram(env, method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return { ok: res.ok, status: res.status };
}
