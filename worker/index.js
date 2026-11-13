// Telegram bot for the Shorts autopilot, running as a Cloudflare Worker (free plan).
// Telegram calls this Worker the moment you send a message, so replies arrive in 1-2 seconds.
//
// It needs NO GitHub API: history and status are plain files of the public repository
// (state/history.json, state/status.json), fetched from raw.githubusercontent.com.
//
// Secrets (Worker > Settings > Variables and Secrets):
//   TELEGRAM_BOT_TOKEN   token from @BotFather
//   TELEGRAM_CHAT_ID     the only chat that may use the bot
//   WEBHOOK_SECRET       any long random string (Telegram sends it back with every call)
//   YOUTUBE_TOKENS_JSON  the same JSON as the GitHub secret of that name
// Plain variables (optional): REPO (default saahinyunus/shorts-autopilot), NOTIFY_TZ (default Europe/Istanbul)

const CHANNEL_TZ = { money: "America/New_York", psychology: "America/New_York" };
const CITY = { "America/New_York": "NY", "Europe/Istanbul": "TR" };
const CHANNELS = ["money", "psychology"];
const tokenCache = new Map(); // channel -> {token, expires}

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const num = (v) => (v === undefined || v === null || isNaN(Number(v)) ? "-" : Number(v).toLocaleString("de-DE"));

function parts(date, tz) {
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return Object.fromEntries(f.formatToParts(date).map((p) => [p.type, p.value]));
}

function fmtSlot(iso, channelTz, notifyTz) {
  if (!iso) return "-";
  const d = new Date(iso);
  const a = parts(d, channelTz), b = parts(d, notifyTz);
  const hh = (p) => `${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
  return `${a.day}.${a.month} ${hh(a)} ${CITY[channelTz] || channelTz} · ${hh(b)} ${CITY[notifyTz] || notifyTz}`;
}

function ago(then, now) {
  const m = Math.floor((now - then) / 60000);
  if (m < 90) return `${Math.max(m, 0)} dk önce`;
  if (m < 48 * 60) return `${Math.floor(m / 60)} sa önce`;
  return `${Math.floor(m / 1440)} gün önce`;
}

function checkScheduled(item, publishAt) {
  if (!item) return [false, "video YouTube'da bulunamadı (silinmiş olabilir)"];
  const st = item.status || {};
  if (st.privacyStatus === "public") return [true, "yayında"];
  if (st.privacyStatus === "private" && st.publishAt) {
    const wanted = publishAt ? new Date(publishAt) : new Date(st.publishAt);
    if (Math.abs(new Date(st.publishAt) - wanted) < 120000) return [true, "zamanlanmış"];
    return [false, `zamanlama farklı: ${st.publishAt}`];
  }
  return [false, `zamanlanmamış, durum: ${st.privacyStatus || "?"} (kilitlenmiş olabilir)`];
}

// ---------------------------------------------------------------------------------- data
async function rawJson(env, path) {
  const repo = env.REPO || "saahinyunus/shorts-autopilot";
  const r = await fetch(`https://raw.githubusercontent.com/${repo}/main/${path}?t=${Date.now()}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function accessToken(env, channel) {
  const hit = tokenCache.get(channel);
  if (hit && hit.expires > Date.now() + 60000) return hit.token;
  let tokens;
  try { tokens = JSON.parse(env.YOUTUBE_TOKENS_JSON || "{}"); } catch { throw new Error("YOUTUBE_TOKENS_JSON geçersiz"); }
  let info = tokens[channel];
  if (typeof info === "string") info = JSON.parse(info);
  if (!info) throw new Error("bu kanal için YouTube girişi yok");
  const r = await fetch(info.token_uri || "https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: info.client_id, client_secret: info.client_secret,
      refresh_token: info.refresh_token, grant_type: "refresh_token",
    }),
  });
  if (!r.ok) throw new Error(`giriş yenilenemedi (HTTP ${r.status})`);
  const data = await r.json();
  tokenCache.set(channel, { token: data.access_token, expires: Date.now() + (data.expires_in || 3000) * 1000 });
  return data.access_token;
}

async function youtube(env, channel, path, query) {
  const token = await accessToken(env, channel);
  const r = await fetch(`https://www.googleapis.com/youtube/v3/${path}?${new URLSearchParams(query)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (r.status === 403) {
    const text = await r.text();
    if (/insufficient/i.test(text)) throw new Error("YouTube izni eksik: bu kanalda yeniden giriş yapılmalı (youtube.readonly izni)");
    throw new Error("YouTube erişimi reddedildi (kota ya da izin)");
  }
  if (!r.ok) throw new Error(`YouTube HTTP ${r.status}`);
  return r.json();
}

async function videoInfo(env, channel, ids) {
  const found = {};
  for (let i = 0; i < ids.length; i += 50) {
    const data = await youtube(env, channel, "videos", { part: "snippet,statistics,status", id: ids.slice(i, i + 50).join(",") });
    for (const item of data.items || []) found[item.id] = item;
  }
  return found;
}

// -------------------------------------------------------------------------------- commands
const HELP =
  "<b>Komutlar</b>\n/menu: düğmeli menü\n/status: otopilotun genel durumu\n/queue: yayını bekleyen videolar (YouTube'dan doğrulanır)\n" +
  "/stats: kanal istatistikleri ve bugünkü artış\n/health: sistem sağlık kontrolü\n/help: bu liste\n\n" +
  "Her gece 00:00'da (TSİ) günlük özet otomatik gelir.";

async function cmdStatus(env, now) {
  const tz = env.NOTIFY_TZ || "Europe/Istanbul";
  const h = (await rawJson(env, "state/history.json")).entries;
  let st = null;
  try { st = await rawJson(env, "state/status.json"); } catch { /* not written yet */ }
  const lines = ["<b>📊 Otopilot durumu</b>"];
  if (st) {
    const icon = st.produce_result === "success" ? "✅" : st.produce_result === "failure" ? "❌" : "⏳";
    lines.push(`${icon} Son çalıştırma: ${esc(st.produce_result || "?")} (${ago(new Date(st.updated_at), now)}), ${st.plan_count} video planlandı` +
      (st.run_url ? ` <a href="${esc(st.run_url)}">detay</a>` : ""));
  }
  for (const cid of CHANNELS) {
    const own = h.filter((e) => e.channel === cid);
    const waiting = own.filter((e) => e.publish_at && new Date(e.publish_at) > now).sort((a, b) => a.publish_at.localeCompare(b.publish_at));
    let line = `\n<b>${esc(cid)}</b>: ${own.length} video yüklendi, ${waiting.length} yayın bekliyor`;
    if (waiting[0]) line += `\n   ⏭ Sıradaki: ${fmtSlot(waiting[0].publish_at, CHANNEL_TZ[cid], tz)}`;
    lines.push(line);
  }
  if (st && st.reserve) lines.push(`\n🛟 Yedek senaryo: ${Object.entries(st.reserve).map(([k, v]) => `${k} ${v}`).join(", ")}`);
  return lines.join("\n");
}

async function cmdQueue(env, now) {
  const tz = env.NOTIFY_TZ || "Europe/Istanbul";
  const h = (await rawJson(env, "state/history.json")).entries;
  const upcoming = h.filter((e) => e.publish_at && new Date(e.publish_at) > now).sort((a, b) => a.publish_at.localeCompare(b.publish_at));
  if (!upcoming.length) return "📭 Yayın bekleyen video yok.";
  const lines = [`<b>🗓 Yayın bekleyen ${upcoming.length} video</b>`];
  for (const cid of CHANNELS) {
    const group = upcoming.filter((e) => e.channel === cid);
    if (!group.length) continue;
    let info = {}, note = "";
    try { info = await videoInfo(env, cid, group.map((e) => e.video_id)); } catch (e) { note = e.message; }
    lines.push(`\n<b>${esc(cid)}</b>${note ? ` <i>(${esc(note)})</i>` : ""}`);
    for (const e of group) {
      let when = fmtSlot(e.publish_at, CHANNEL_TZ[cid], tz), mark = "•";
      if (!note) {
        const [ok, why] = checkScheduled(info[e.video_id], e.publish_at);
        mark = ok ? "✅" : "⚠️";
        if (!ok) when += ` (${why})`;
      }
      lines.push(`${mark} ${when}\n   <a href="${esc(e.url)}">${esc(e.title || e.topic)}</a>`);
    }
  }
  return lines.join("\n");
}

// ------------------------------------------------------------------ statistics + daily snapshots
// Totals are read live from YouTube. "Today's gain" needs a baseline, so a snapshot of every channel's
// totals is kept in Workers KV (binding `KV`), taken at 00:00 Istanbul time. Without KV the gains show "-".
const TR_TZ = "Europe/Istanbul";
const ICON = { money: "💰", psychology: "🧠" };

function trDate(d) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TR_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

function clock(d) {
  const p = parts(d, TR_TZ);
  return `${p.hour === "24" ? "00" : p.hour}:${p.minute} TSİ`;
}

const signed = (n) => (n === null || n === undefined || isNaN(n) ? "-" : `${n < 0 ? "-" : "+"}${num(Math.abs(n))}`);

async function channelTotals(env) {
  const totals = {};
  for (const cid of CHANNELS) {
    try {
      const ch = (await youtube(env, cid, "channels", { part: "snippet,statistics", mine: "true" })).items[0];
      const s = ch.statistics;
      totals[cid] = { title: ch.snippet.title, subs: Number(s.subscriberCount || 0), views: Number(s.viewCount || 0), videos: Number(s.videoCount || 0) };
    } catch (e) {
      totals[cid] = { error: e.message };
    }
  }
  return totals;
}

const kvGet = async (env, key) => (env.KV ? env.KV.get(key, "json") : null);
const kvPut = async (env, key, value) => (env.KV ? env.KV.put(key, JSON.stringify(value), { expirationTtl: 90 * 86400 }) : null);

function snapshotOf(totals, now) {
  const snap = { at: now.toISOString() };
  for (const [cid, t] of Object.entries(totals)) if (!t.error) snap[cid] = { subs: t.subs, views: t.views, videos: t.videos };
  return snap;
}

async function cmdStats(env, now) {
  const totals = await channelTotals(env);
  const today = trDate(now);
  let base = await kvGet(env, `snap:${today}`);
  if (!base && env.KV && Object.values(totals).some((t) => !t.error)) { // first look today: start counting now
    base = snapshotOf(totals, now);
    await kvPut(env, `snap:${today}`, base);
  }
  const lines = ["📊 <b>Tüm kanallar</b>\n"];
  let subs = 0, views = 0, videos = 0, gain = 0, gainKnown = false;
  for (const cid of CHANNELS) {
    const t = totals[cid];
    if (t.error) { lines.push(`${ICON[cid] || "📺"} ${esc(cid)}: ⚠️ ${esc(t.error)}`); continue; }
    const b = base && base[cid];
    const g = b ? t.views - b.views : null;
    if (g !== null) { gain += g; gainKnown = true; }
    subs += t.subs; views += t.views; videos += t.videos;
    lines.push(`${ICON[cid] || "📺"} <b>${esc(t.title)}</b>: 👥 ${num(t.subs)} · 👁 ${num(t.views)} · 🎬 ${num(t.videos)} · 📈 ${signed(g)}`);
  }
  lines.push(`\n<b>Toplam:</b> 👥 ${num(subs)} · 👁 ${num(views)} · 🎬 ${num(videos)}`);
  lines.push(`Bugün: ${gainKnown ? signed(gain) : "-"} izlenme` + (env.KV ? "" : " <i>(KV bağlı değil: günlük artış hesaplanamıyor)</i>"));
  lines.push(`\n🕒 ${clock(now)}`);
  return lines.join("\n");
}

// Sent every night at 00:00 Istanbul time (cron "0 21 * * *"): what the day that just ended brought.
async function dailySummary(env, now = new Date()) {
  const today = trDate(now);
  const ended = trDate(new Date(now.getTime() - 12 * HOUR)); // the Istanbul day that just finished
  if (env.KV && (await env.KV.get(`summary:${today}`))) return null; // already sent
  const totals = await channelTotals(env);
  const base = await kvGet(env, `snap:${ended}`);
  const h = (await rawJson(env, "state/history.json")).entries;
  const lines = [`🌙 <b>Günlük özet</b> · ${ended.split("-").reverse().join(".")}\n`];
  let dViews = 0, dSubs = 0, dVideos = 0, known = false;
  for (const cid of CHANNELS) {
    const t = totals[cid];
    if (t.error) { lines.push(`${ICON[cid] || "📺"} ${esc(cid)}: ⚠️ ${esc(t.error)}`); continue; }
    const b = base && base[cid];
    const published = h.filter((e) => e.channel === cid && e.publish_at && trDate(new Date(e.publish_at)) === ended).length;
    dVideos += published;
    if (b) { dViews += t.views - b.views; dSubs += t.subs - b.subs; known = true; }
    lines.push(`${ICON[cid] || "📺"} <b>${esc(t.title)}</b>: 👁 ${b ? signed(t.views - b.views) : "-"} · 👥 ${b ? signed(t.subs - b.subs) : "-"} · 🎬 ${published} yayınlandı`);
  }
  lines.push(`\n<b>Toplam:</b> 👁 ${known ? signed(dViews) : "-"} izlenme · 👥 ${known ? signed(dSubs) : "-"} abone · 🎬 ${dVideos} video`);
  if (!known) lines.push("<i>(Karşılaştırma için dünkü kayıt yok; yarından itibaren artışlar görünür.)</i>");
  // best recent videos
  try {
    const recent = h.filter((e) => e.publish_at && new Date(e.publish_at) <= now && now - new Date(e.publish_at) < 7 * 24 * HOUR);
    const scored = [];
    for (const cid of CHANNELS) {
      const mine = recent.filter((e) => e.channel === cid);
      if (!mine.length || totals[cid].error) continue;
      const info = await videoInfo(env, cid, mine.map((e) => e.video_id));
      for (const e of mine) scored.push({ e, views: Number(((info[e.video_id] || {}).statistics || {}).viewCount || 0) });
    }
    scored.sort((a, b) => b.views - a.views);
    if (scored.length) {
      lines.push("\n🏆 <b>Son 7 günün en çok izlenenleri</b>");
      for (const s of scored.slice(0, 3)) lines.push(`• <a href="${esc(s.e.url)}">${esc((s.e.title || s.e.topic).slice(0, 50))}</a>: 👁 ${num(s.views)}`);
    }
  } catch { /* the summary must go out even without the ranking */ }
  const waiting = h.filter((e) => e.publish_at && new Date(e.publish_at) > now).length;
  lines.push(`\n🗓 Yayın bekleyen: ${waiting} video`);
  try {
    const st = await rawJson(env, "state/status.json");
    const ok = st.produce_result !== "failure" && (now - new Date(st.updated_at)) / HOUR < 30;
    lines.push(ok ? "✅ Sistem sorunsuz" : "⚠️ Son otomatik çalıştırmada sorun var, /health ile bakın");
  } catch { /* no status file yet */ }
  const ok = Object.values(totals).some((t) => !t.error);
  if (env.KV && ok) { // today's baseline = the totals right now (00:00)
    await kvPut(env, `snap:${today}`, snapshotOf(totals, now));
    await kvPut(env, `summary:${today}`, { sent: now.toISOString() });
  }
  const text = lines.join("\n");
  await send(env, env.TELEGRAM_CHAT_ID, text, MENU_ONLY);
  return text;
}

async function cmdHealth(env, now) {
  const problems = [];
  const lines = ["<b>🩺 Sağlık kontrolü</b>"];
  let st = null;
  try { st = await rawJson(env, "state/status.json"); } catch { /* ignore */ }
  if (st) {
    const hours = (now - new Date(st.updated_at)) / 3600000;
    const ok = st.produce_result !== "failure" && hours < 30;
    lines.push(`${ok ? "✅" : "❌"} Günlük çalıştırma: son ${ago(new Date(st.updated_at), now)}, sonuç ${esc(st.produce_result || "?")}`);
    if (!ok) problems.push("günlük çalıştırma");
  } else lines.push("⚠️ Henüz durum dosyası yok (ilk otomatik çalıştırmadan sonra oluşur)");
  try {
    const gaps = findGaps((await rawJson(env, "state/history.json")).entries, now);
    if (gaps.length) {
      lines.push(`❌ Slot kapsamı: ${gaps.map((g) => `${esc(g.channel)} ${fmtSlot(g.at.toISOString(), CHANNEL_TZ[g.channel], env.NOTIFY_TZ || "Europe/Istanbul")}`).join(", ")} için video planlı değil`);
      problems.push("boş slot");
    } else lines.push("✅ Önümüzdeki 6 saatteki tüm slotlar için video planlı");
  } catch { /* repo unreachable: the other checks still report */ }
  for (const cid of CHANNELS) {
    try { await accessToken(env, cid); lines.push(`✅ YouTube girişi (${cid}): geçerli`); }
    catch (e) { lines.push(`❌ YouTube girişi (${cid}): ${esc(e.message)}`); problems.push(`${cid} girişi`); }
    if (st && st.reserve) {
      const n = st.reserve[cid] ?? 0;
      lines.push(`${n >= 3 ? "✅" : "⚠️"} Yedek senaryo (${cid}): ${n}`);
    }
  }
  if (st && st.llms) lines.push(`${st.llms.length >= 2 ? "✅" : "⚠️"} Yapay zekâ anahtarları: ${st.llms.join(", ") || "yok"}`);
  lines.push("\n" + (problems.length ? "🔴 Dikkat: " + problems.join(", ") : "🟢 Her şey yolunda"));
  return lines.join("\n");
}

const COMMANDS = { status: cmdStatus, queue: cmdQueue, stats: cmdStats, health: cmdHealth };

async function replyFor(text, env, now = new Date()) {
  const word = (text || "").trim().split(/\s+/)[0].toLowerCase();
  if (!word.startsWith("/")) return null;
  const name = word.slice(1).split("@")[0];
  if (name === "start" || name === "help") return HELP;
  const handler = COMMANDS[name];
  if (!handler) return "Bilinmeyen komut. /help yazın.";
  try { return await handler(env, now); } catch (e) { return `⚠️ /${esc(name)} çalışmadı: ${esc(e.message)}`; }
}

// ------------------------------------------------------------------ Telegram I/O and buttons
const MENU_ONLY = { inline_keyboard: [[{ text: "⬅️ Menü", callback_data: "menu" }]] };
const MENU_TEXT = "<b>📋 Menü</b>\nBir seçim yapın:";
const MENU_KEYBOARD = {
  inline_keyboard: [
    [{ text: "📊 İstatistik", callback_data: "cmd:stats" }, { text: "🗓 Yayın kuyruğu", callback_data: "cmd:queue" }],
    [{ text: "📋 Durum", callback_data: "cmd:status" }, { text: "🩺 Sağlık", callback_data: "cmd:health" }],
  ],
};

const refreshKeyboard = (name) => ({
  inline_keyboard: [[{ text: "🔄 Yenile", callback_data: `cmd:${name}` }, { text: "⬅️ Menü", callback_data: "menu" }]],
});

async function telegram(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r;
}

async function send(env, chatId, text, markup) {
  const chunks = [];
  for (let i = 0; i < text.length; i += 3900) chunks.push(text.slice(i, i + 3900));
  for (let i = 0; i < chunks.length; i++) {
    const body = { chat_id: chatId, text: chunks[i], parse_mode: "HTML", disable_web_page_preview: true };
    if (markup && i === chunks.length - 1) body.reply_markup = markup;
    await telegram(env, "sendMessage", body);
  }
}

async function handleUpdate(update, env) {
  const cb = update.callback_query;
  if (cb) { // a button was pressed: edit the message in place
    const chat = String(((cb.message || {}).chat || {}).id ?? "");
    if (chat !== String(env.TELEGRAM_CHAT_ID)) return;
    await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
    const data = cb.data || "";
    let text, markup;
    if (data === "menu") { text = MENU_TEXT; markup = MENU_KEYBOARD; }
    else if (data.startsWith("cmd:")) { const name = data.slice(4); text = await replyFor(`/${name}`, env); markup = refreshKeyboard(name); }
    else return;
    if (text) await telegram(env, "editMessageText", { chat_id: chat, message_id: cb.message.message_id, text: text.slice(0, 3900), parse_mode: "HTML", disable_web_page_preview: true, reply_markup: markup });
    return; // "message is not modified" errors are harmless
  }
  const msg = update.message || {};
  if (String((msg.chat || {}).id ?? "") !== String(env.TELEGRAM_CHAT_ID)) return; // strangers get no answer
  const word = (msg.text || "").trim().split(/\s+/)[0].toLowerCase().replace(/^\//, "").split("@")[0];
  if (word === "menu") { await send(env, msg.chat.id, MENU_TEXT, MENU_KEYBOARD); return; }
  const answer = await replyFor(msg.text, env);
  if (answer) await send(env, msg.chat.id, answer, COMMANDS[word] ? refreshKeyboard(word) : undefined);
}

// ------------------------------------------------------------------ first comments (timer)
// YouTube refuses comments on videos that are not public yet, so the Worker's cron trigger (every 10
// minutes) posts each video's first comment once the video has gone public. It never posts twice: it
// looks for its own comment first. state/history.json carries the text (comment_text).
const HOUR = 3600000;
const handled = new Set(); // video ids finished in this isolate (the YouTube check below is the real guard)
const attempts = new Map();

async function ownComment(env, e) {
  const data = await youtube(env, e.channel, "commentThreads", { part: "snippet", videoId: e.video_id, maxResults: "50", order: "time" });
  return (data.items || []).some((i) => i.snippet.topLevelComment.snippet.textOriginal === e.comment_text);
}

async function insertComment(env, e) {
  const token = await accessToken(env, e.channel);
  const r = await fetch("https://www.googleapis.com/youtube/v3/commentThreads?part=snippet", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ snippet: { videoId: e.video_id, topLevelComment: { snippet: { textOriginal: e.comment_text } } } }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

async function postDueComments(env, now = new Date()) {
  const h = (await rawJson(env, "state/history.json")).entries;
  const due = h.filter((e) => {
    const age = now - new Date(e.publish_at);
    return e.comment_text && !e.comment_id && e.publish_at && age >= 0 && age < 12 * HOUR && !handled.has(e.video_id);
  });
  const results = [];
  for (const e of due) {
    try {
      if (!(await ownComment(env, e))) {
        await insertComment(env, e);
        results.push(e);
        await send(env, env.TELEGRAM_CHAT_ID,
          `💬 <b>İlk yorum yazıldı</b>
🎬 <a href="${esc(e.url)}">${esc(e.title || e.topic)}</a>
“${esc(e.comment_text)}”
` +
          "📌 Sabitlemek için: YouTube Studio uygulaması → Yorumlar → yorumun ⋮ menüsü → Sabitle");
      }
      handled.add(e.video_id);
    } catch (err) {
      const n = (attempts.get(e.video_id) || 0) + 1;
      attempts.set(e.video_id, n);
      if (n >= 6) { // about an hour of trying: give up and say so once
        handled.add(e.video_id);
        await send(env, env.TELEGRAM_CHAT_ID, `⚠️ İlk yorum yazılamadı: ${esc(e.title || e.topic)} (${esc(err.message)})`);
      }
    }
  }
  return results;
}

// ------------------------------------------------------------------ watchdog (independent of GitHub)
// If GitHub Actions stops running (a skipped schedule, an outage, a disabled workflow) nothing inside GitHub can
// say so. This check runs on Cloudflare every 10 minutes and alerts through Telegram, each problem once a day.
const CHANNEL_SLOTS = { money: ["08:00", "19:00"], psychology: ["08:00", "19:00"] }; // same as channels/*.json (tested)
const alertMemory = new Set();

function zoned(ms, tz) {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", hourCycle: "h23",
  });
  return Object.fromEntries(f.formatToParts(new Date(ms)).filter((p) => p.type !== "literal").map((p) => [p.type, Number(p.value)]));
}

// The UTC instant at which the wall clock of `tz` shows y-mo-d hh:mm (daylight saving aware).
function localToUtcMs(y, mo, d, hh, mm, tz) {
  const wanted = Date.UTC(y, mo - 1, d, hh, mm);
  let guess = wanted;
  for (let i = 0; i < 3; i++) {
    const z = zoned(guess, tz);
    guess -= Date.UTC(z.year, z.month - 1, z.day, z.hour, z.minute) - wanted;
  }
  return guess;
}

function slotsBetween(channel, fromMs, toMs) {
  const tz = CHANNEL_TZ[channel];
  const start = zoned(fromMs - 36 * HOUR, tz);
  const found = [];
  for (let k = 0; k < 6; k++) {
    const day = new Date(Date.UTC(start.year, start.month - 1, start.day + k));
    for (const slot of CHANNEL_SLOTS[channel]) {
      const [hh, mm] = slot.split(":").map(Number);
      const ms = localToUtcMs(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), hh, mm, tz);
      if (ms >= fromMs && ms < toMs) found.push(ms);
    }
  }
  return found.sort((a, b) => a - b);
}

// Slots in the next `hours` hours that have no video in the history (the pipeline plans ~24 h ahead, so any gap
// this close is a real failure of the autopilot).
function findGaps(history, now, hours = 6) {
  const gaps = [];
  for (const channel of CHANNELS) {
    for (const ms of slotsBetween(channel, now.getTime(), now.getTime() + hours * HOUR)) {
      const planned = history.some((e) => e.channel === channel && Math.abs(Date.parse(e.publish_at) - ms) < 60000);
      if (!planned) gaps.push({ channel, at: new Date(ms) });
    }
  }
  return gaps;
}

async function once(env, key, ttlSeconds) { // true the first time within the TTL
  if (env.KV) {
    if (await env.KV.get(key)) return false;
    await env.KV.put(key, "1", { expirationTtl: Math.max(60, ttlSeconds) });
    return true;
  }
  if (alertMemory.has(key)) return false;
  alertMemory.add(key);
  return true;
}

async function watchdog(env, now = new Date()) {
  const tz = env.NOTIFY_TZ || "Europe/Istanbul";
  const alerts = [];
  const hourly = now.getUTCMinutes() < 10; // the cron fires every 10 minutes: run the heavier checks once an hour
  let history = null;
  try { history = (await rawJson(env, "state/history.json")).entries; } catch { /* repo unreachable: skip repo-based checks */ }

  if (history) {
    for (const g of findGaps(history, now)) {
      if (await once(env, `alert:gap:${g.channel}:${g.at.toISOString()}`, 86400)) {
        alerts.push(`⚠️ ${esc(g.channel)}: ${fmtSlot(g.at.toISOString(), CHANNEL_TZ[g.channel], tz)} slotu için video planlanmamış. Otopilot çalışmıyor olabilir: GitHub → Actions sayfasına bakın.`);
      }
    }
    if (hourly) { // videos whose publish time has passed must really be public (YouTube may lock or hold them)
      const overdue = history.filter((e) => { const age = now - new Date(e.publish_at); return age > 25 * 60000 && age < 24 * HOUR; });
      for (const channel of CHANNELS) {
        const mine = overdue.filter((e) => e.channel === channel);
        if (!mine.length) continue;
        try {
          const info = await videoInfo(env, channel, mine.map((e) => e.video_id));
          for (const e of mine) {
            const v = info[e.video_id];
            const privacy = v && v.status && v.status.privacyStatus;
            if (privacy !== "public" && (await once(env, `alert:private:${e.video_id}`, 172800))) {
              alerts.push(`⚠️ ${esc(channel)}: “${esc((e.title || e.topic).slice(0, 60))}” yayın saati geçti ama video herkese açık değil (durum: ${esc(privacy || "bulunamadı")}). YouTube Studio'ya bakın.`);
            }
          }
        } catch (err) {
          if (await once(env, `alert:yt:${channel}`, 86400)) alerts.push(`⚠️ ${esc(channel)}: YouTube durumu okunamadı (${esc(err.message)}).`);
        }
      }
    }
  }

  if (hourly) {
    for (const channel of CHANNELS) {
      try { await accessToken(env, channel); } catch (err) {
        if (await once(env, `alert:token:${channel}`, 86400)) alerts.push(`⚠️ ${esc(channel)}: YouTube girişi çalışmıyor (${esc(err.message)}). Yeniden giriş gerekebilir.`);
      }
    }
  }

  try {
    const st = await rawJson(env, "state/status.json");
    const hours = (now - new Date(st.updated_at)) / HOUR;
    if (hours > 30 && (await once(env, "alert:stale", 21600))) alerts.push(`⚠️ Otopilotun son kalp atışı ${Math.round(hours)} saat önce. GitHub Actions çalışmıyor olabilir.`);
  } catch { /* no status file yet */ }

  if (alerts.length) await send(env, env.TELEGRAM_CHAT_ID, "🚨 <b>Otopilot uyarısı</b>\n" + alerts.join("\n"));
  return alerts;
}

export default {
  internals: { fmtSlot, ago, checkScheduled, replyFor, handleUpdate, postDueComments, dailySummary, trDate, localToUtcMs, slotsBetween, findGaps, watchdog, tokenCache }, // for worker/test.mjs
  async scheduled(event, env, ctx) {
    // "0 21 * * *" (= 00:00 Istanbul) sends the daily summary; every other trigger posts due first comments
    if (event.cron === "0 21 * * *") ctx.waitUntil(dailySummary(env).catch(() => {}));
    else ctx.waitUntil(Promise.all([postDueComments(env).catch(() => {}), watchdog(env).catch(() => {})]));
  },
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("Shorts autopilot bot is running.");
    if (!env.WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
    ctx.waitUntil(handleUpdate(update, env).catch(() => {})); // answer after replying 200 to Telegram
    return new Response("ok");
  },
};
