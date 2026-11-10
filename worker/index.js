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
  "<b>Komutlar</b>\n/status: otopilotun genel durumu\n/queue: yayını bekleyen videolar (YouTube'dan doğrulanır)\n" +
  "/stats: kanal ve video istatistikleri\n/health: sistem sağlık kontrolü\n/help: bu liste";

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

async function cmdStats(env, now) {
  const h = (await rawJson(env, "state/history.json")).entries;
  const lines = ["<b>📈 İstatistikler</b>"];
  for (const cid of CHANNELS) {
    try {
      const ch = (await youtube(env, cid, "channels", { part: "snippet,statistics", mine: "true" })).items[0];
      const s = ch.statistics;
      lines.push(`\n<b>${esc(ch.snippet.title)}</b> (${esc(cid)})\n👥 ${num(s.subscriberCount)} abone · 👁 ${num(s.viewCount)} toplam izlenme · 🎞 ${num(s.videoCount)} video`);
      const published = h.filter((e) => e.channel === cid && e.publish_at && new Date(e.publish_at) <= now);
      if (!published.length) { lines.push("(henüz yayına girmiş video yok)"); continue; }
      const info = await videoInfo(env, cid, published.slice(-8).map((e) => e.video_id));
      for (const e of published.slice(-5).reverse()) {
        const v = (info[e.video_id] || {}).statistics || {};
        lines.push(`• ${esc((e.title || e.topic).slice(0, 48))}: 👁 ${num(v.viewCount)} · 👍 ${num(v.likeCount)} · 💬 ${num(v.commentCount)}`);
      }
    } catch (e) {
      lines.push(`\n<b>${esc(cid)}</b>: ${esc(e.message)}`);
    }
  }
  return lines.join("\n");
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

async function send(env, chatId, text) {
  for (let i = 0; i < text.length; i += 3900) {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: text.slice(i, i + 3900), parse_mode: "HTML", disable_web_page_preview: true }),
    });
  }
}

async function handleUpdate(update, env) {
  const msg = update.message || {};
  if (String((msg.chat || {}).id ?? "") !== String(env.TELEGRAM_CHAT_ID)) return; // strangers get no answer
  const answer = await replyFor(msg.text, env);
  if (answer) await send(env, msg.chat.id, answer);
}

export default {
  internals: { fmtSlot, ago, checkScheduled, replyFor, handleUpdate }, // for worker/test.mjs
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
