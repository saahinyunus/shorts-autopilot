// Offline test of the Telegram worker: fake fetch, no network. Run: node worker/test.mjs
import assert from "node:assert/strict";
import worker from "./index.js";

const { fmtSlot, checkScheduled, replyFor, handleUpdate, postDueComments, dailySummary, trDate, localToUtcMs, slotsBetween, findGaps, watchdog, tokenCache } = worker.internals;
const NOW = new Date("2026-11-10T12:00:00Z");
const sent = [];
let ytError = null;
let channelViews = 56789;
let commentFail = false;
const posted = [];
const existingComments = {};

const HISTORY = { entries: [
  { plan_id: "m1", channel: "money", topic: "t1", title: "Money <One>", video_id: "v1", url: "https://youtu.be/v1", publish_at: "2026-11-10T13:00:00Z" },
  { plan_id: "m2", channel: "money", topic: "t2", title: "Money Two", video_id: "v2", url: "https://youtu.be/v2", publish_at: "2026-11-10T18:00:00Z" },
  { plan_id: "m0", channel: "money", topic: "t0", title: "Old one", video_id: "v0", url: "https://youtu.be/v0", publish_at: "2026-11-09T13:00:00Z" },
  { plan_id: "p1", channel: "psychology", topic: "tp", title: "Psych", video_id: "vp", url: "https://youtu.be/vp", publish_at: "2026-11-10T18:00:00Z" },
] };
const STATUS = { updated_at: "2026-11-10T04:30:00Z", run_url: "https://run", plan_count: 3, produce_result: "success", reserve: { money: 8, psychology: 2 }, llms: ["Gemini", "Groq"] };
const VIDEOS = {
  v1: { status: { privacyStatus: "private", publishAt: "2026-11-10T13:00:00Z" }, statistics: {} },
  v2: { status: { privacyStatus: "private" }, statistics: {} },
  v0: { status: { privacyStatus: "public" }, statistics: { viewCount: "1500", likeCount: "30", commentCount: "2" } },
  vp: { status: { privacyStatus: "private", publishAt: "2026-11-10T18:00:00Z" }, statistics: {} },
};

globalThis.fetch = async (url, opts = {}) => {
  url = String(url);
  const json = (b, status = 200) => new Response(JSON.stringify(b), { status });
  if (url.includes("raw.githubusercontent.com")) return url.includes("history.json") ? json(HISTORY) : json(STATUS);
  if (url.includes("oauth2.googleapis.com")) return json({ access_token: "ya29.fake", expires_in: 3600 });
  if (url.includes("api.telegram.org")) { sent.push({ _method: url.split("/").pop(), ...JSON.parse(opts.body) }); return json({ ok: true }); }
  if (url.includes("youtube/v3/commentThreads")) {
    if (opts.method === "POST") {
      if (commentFail) return new Response("{}", { status: 403 });
      const body = JSON.parse(opts.body); posted.push(body.snippet); return json({ id: "thread1" });
    }
    const vid = new URL(url).searchParams.get("videoId");
    return json({ items: (existingComments[vid] || []).map((t) => ({ snippet: { topLevelComment: { snippet: { textOriginal: t } } } })) });
  }
  if (url.includes("youtube/v3")) {
    if (ytError) return new Response(ytError, { status: 403 });
    if (url.includes("/channels")) return json({ items: [{ snippet: { title: "Money Channel" }, statistics: { subscriberCount: "1234", viewCount: String(channelViews), videoCount: "7" } }] });
    const ids = new URL(url).searchParams.get("id").split(",");
    return json({ items: ids.filter((i) => VIDEOS[i]).map((i) => ({ id: i, ...VIDEOS[i] })) });
  }
  throw new Error("unexpected fetch " + url);
};

const env = { TELEGRAM_BOT_TOKEN: "T", TELEGRAM_CHAT_ID: "42", WEBHOOK_SECRET: "s3cret",
  YOUTUBE_TOKENS_JSON: JSON.stringify({ money: { client_id: "a", client_secret: "b", refresh_token: "c" }, psychology: { client_id: "a", client_secret: "b", refresh_token: "c" } }) };

// time formatting
assert.equal(fmtSlot("2026-11-10T13:00:00Z", "America/New_York", "Europe/Istanbul"), "10.11 08:00 NY · 16:00 TR");
assert.equal(fmtSlot("2026-11-11T00:00:00Z", "America/New_York", "Europe/Istanbul"), "10.11 19:00 NY · 03:00 TR");
// scheduled check
assert.deepEqual(checkScheduled(VIDEOS.v1, "2026-11-10T13:00:00Z"), [true, "zamanlanmış"]);
assert.equal(checkScheduled(VIDEOS.v2, "2026-11-10T18:00:00Z")[0], false);
assert.equal(checkScheduled(null, "x")[0], false);

// commands
assert.match(await replyFor("/help", env, NOW), /\/queue/);
assert.equal(await replyFor("hello", env, NOW), null);
assert.match(await replyFor("/nope", env, NOW), /Bilinmeyen/);
const status = await replyFor("/status", env, NOW);
assert.match(status, /Son çalıştırma: success/); assert.match(status, /money<\/b>: 3 video yüklendi, 2 yayın bekliyor/); assert.match(status, /money 8, psychology 2/);
const queue = await replyFor("/queue@my_bot", env, NOW);
assert.match(queue, /3 video/); assert.match(queue, /✅/); assert.match(queue, /zamanlanmamış/); assert.match(queue, /Money &lt;One&gt;/); assert.doesNotMatch(queue, /Old one/);
const stats = await replyFor("/stats", env, NOW);
assert.match(stats, /Tüm kanallar/); assert.match(stats, /Money Channel<\/b>: 👥 1\.234 · 👁 56\.789 · 🎬 7 · 📈 -/); assert.match(stats, /Toplam:<\/b> 👥 2\.468/); assert.match(stats, /KV bağlı değil/); assert.match(stats, /TSİ/);
const health = await replyFor("/health", env, NOW);
assert.match(health, /Yedek senaryo \(psychology\): 2/); assert.match(health, /⚠️/); assert.match(health, /YouTube girişi \(money\): geçerli/);

// missing permission is explained, not crashed
ytError = "insufficientPermissions";
assert.match(await replyFor("/stats", env, NOW), /yeniden giriş/);
assert.match(await replyFor("/queue", env, NOW), /izni eksik/);
ytError = null;

// webhook security: wrong secret, wrong method, strangers
const ctx = { waitUntil: (p) => p };
assert.equal((await worker.fetch(new Request("https://w", { method: "POST", body: "{}" }), env, ctx)).status, 403);
assert.equal((await worker.fetch(new Request("https://w", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "bad" }, body: "{}" }), env, ctx)).status, 403);
assert.equal((await worker.fetch(new Request("https://w"), env, ctx)).status, 200);
sent.length = 0;
await handleUpdate({ message: { chat: { id: 999 }, text: "/status" } }, env);
assert.equal(sent.length, 0); // a stranger gets nothing
const ok = await worker.fetch(new Request("https://w", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret" }, body: JSON.stringify({ message: { chat: { id: 42 }, text: "/help" } }) }), env, { waitUntil: (p) => p.then(() => {}) });
assert.equal(ok.status, 200);
await new Promise((r) => setTimeout(r, 20));
assert.equal(sent.length, 1); assert.equal(sent[0].chat_id, 42); assert.match(sent[0].text, /Komutlar/);


// ---- stats with a KV baseline (gains since 00:00 Istanbul) ----
const kvStore = new Map();
env.KV = { get: async (k) => (kvStore.has(k) ? JSON.parse(kvStore.get(k)) : null), put: async (k, v) => { kvStore.set(k, v); } };
HISTORY.entries = [
  { plan_id: "m1", channel: "money", topic: "t1", title: "Published today", video_id: "v0", url: "https://youtu.be/v0", publish_at: "2026-11-10T13:00:00Z" },
  { plan_id: "p1", channel: "psychology", topic: "t", title: "Psych today", video_id: "vp", url: "https://youtu.be/vp", publish_at: "2026-11-10T18:00:00Z" },
  { plan_id: "m9", channel: "money", topic: "t", title: "Other day", video_id: "v1", url: "u", publish_at: "2026-11-08T13:00:00Z" },
];
channelViews = 1000;
const first = await replyFor("/stats", env, NOW);
assert.match(first, /📈 \+0/); assert.match(first, /Bugün: \+0 izlenme/);               // baseline created now
assert.ok(kvStore.has(`snap:${trDate(NOW)}`));
channelViews = 1044;
const later = await replyFor("/stats", env, NOW);
assert.match(later, /📈 \+44/); assert.match(later, /Bugün: \+88 izlenme/);              // two channels with +44 each

// ---- the nightly summary (cron 0 21 * * * = 00:00 Istanbul) ----
const MIDNIGHT = new Date("2026-11-10T21:00:00Z");                                   // 00:00 on 11 Nov in Istanbul
assert.equal(trDate(MIDNIGHT), "2026-11-11");
kvStore.set("snap:2026-11-10", JSON.stringify({ money: { subs: 1200, views: 900, videos: 6 }, psychology: { subs: 1230, views: 1000, videos: 6 } }));
channelViews = 1100; sent.length = 0;
const summary = await dailySummary(env, MIDNIGHT);
assert.match(summary, /Günlük özet<\/b> · 10\.11\.2026/);
assert.match(summary, /👁 \+200 · 👥 \+34 · 🎬 1 yayınlandı/);                     // money: 1100-900, 1234-1200, one video that day
assert.match(summary, /👁 \+100 · 👥 \+4 · 🎬 1 yayınlandı/);
assert.match(summary, /Toplam:<\/b> 👁 \+300 izlenme · 👥 \+38 abone · 🎬 2 video/);
assert.match(summary, /en çok izlenenleri/);
assert.equal(sent.length, 1); assert.equal(sent[0]._method, "sendMessage"); assert.ok(sent[0].reply_markup.inline_keyboard[0][0].text.includes("Menü"));
assert.ok(kvStore.has("snap:2026-11-11") && kvStore.has("summary:2026-11-11")); // tomorrow's baseline saved
assert.equal(await dailySummary(env, MIDNIGHT), null); assert.equal(sent.length, 1); // never sent twice
// without yesterday's snapshot it says so instead of inventing numbers
kvStore.clear(); sent.length = 0;
const noBase = await dailySummary(env, MIDNIGHT);
assert.match(noBase, /yarından itibaren/); assert.match(noBase, /👁 - /);

// ---- timer routing ----
kvStore.clear(); sent.length = 0;
await worker.scheduled({ cron: "0 21 * * *" }, env, { waitUntil: (p) => p });
await new Promise((r) => setTimeout(r, 30));
assert.ok(sent.some((m) => /Günlük özet/.test(m.text)));
sent.length = 0;
await worker.scheduled({ cron: "*/10 * * * *" }, env, { waitUntil: (p) => p });
await new Promise((r) => setTimeout(r, 30));
assert.ok(!sent.some((m) => /Günlük özet/.test(m.text)));

// ---- buttons ----
sent.length = 0;
await handleUpdate({ message: { chat: { id: 42 }, text: "/stats" } }, env);
const kb = sent[0].reply_markup.inline_keyboard[0];
assert.deepEqual(kb.map((b) => b.text), ["🔄 Yenile", "⬅️ Menü"]); assert.equal(kb[0].callback_data, "cmd:stats");
sent.length = 0;
await handleUpdate({ message: { chat: { id: 42 }, text: "/menu" } }, env);
assert.equal(sent[0].reply_markup.inline_keyboard.flat().length, 4);
sent.length = 0;
await handleUpdate({ callback_query: { id: "cb1", data: "cmd:stats", message: { chat: { id: 42 }, message_id: 77 } } }, env);
assert.deepEqual(sent.map((m) => m._method), ["answerCallbackQuery", "editMessageText"]);
assert.equal(sent[1].message_id, 77); assert.match(sent[1].text, /Tüm kanallar/); assert.equal(sent[1].reply_markup.inline_keyboard[0][0].callback_data, "cmd:stats");
sent.length = 0;
await handleUpdate({ callback_query: { id: "cb2", data: "menu", message: { chat: { id: 42 }, message_id: 78 } } }, env);
assert.match(sent[1].text, /Menü/); assert.equal(sent[1].reply_markup.inline_keyboard.flat().length, 4);
sent.length = 0;
await handleUpdate({ callback_query: { id: "cb3", data: "cmd:stats", message: { chat: { id: 999 }, message_id: 1 } } }, env);
assert.equal(sent.length, 0);                                                          // a stranger's button does nothing

// first comments at publish time
const C_HIST = [
  { plan_id: "c1", channel: "money", topic: "t", title: "Due video", video_id: "due1", url: "https://youtu.be/due1", publish_at: "2026-11-10T11:30:00Z", comment_text: "Which one gets you?" },
  { plan_id: "c2", channel: "money", topic: "t", title: "Future video", video_id: "fut1", url: "u", publish_at: "2026-11-10T13:00:00Z", comment_text: "Not yet?" },
  { plan_id: "c3", channel: "psychology", topic: "t", title: "Already commented", video_id: "had1", url: "u", publish_at: "2026-11-10T10:00:00Z", comment_text: "Existing question?" },
  { plan_id: "c4", channel: "money", topic: "t", title: "Old video, no feature", video_id: "old1", url: "u", publish_at: "2026-11-10T09:00:00Z" },
  { plan_id: "c5", channel: "money", topic: "t", title: "Too old", video_id: "ancient", url: "u", publish_at: "2026-11-09T09:00:00Z", comment_text: "Stale?" },
];
HISTORY.entries = C_HIST;
existingComments.had1 = ["Existing question?"];
sent.length = 0;
const done = await postDueComments(env, NOW);
assert.deepEqual(done.map((e) => e.video_id), ["due1"]);                         // only the due one
assert.equal(posted.length, 1); assert.equal(posted[0].videoId, "due1"); assert.equal(posted[0].topLevelComment.snippet.textOriginal, "Which one gets you?");
assert.equal(sent.length, 1); assert.match(sent[0].text, /Sabitle/); assert.match(sent[0].text, /Due video/);
assert.deepEqual(await postDueComments(env, NOW), []);                           // never twice
assert.equal(posted.length, 1);

// a failing comment is retried, and reported once after six tries
HISTORY.entries = [{ plan_id: "f", channel: "money", topic: "t", title: "Flaky", video_id: "flaky1", url: "u", publish_at: "2026-11-10T11:55:00Z", comment_text: "Flaky q?" }];
commentFail = true; sent.length = 0;
for (let i = 0; i < 5; i++) await postDueComments(env, NOW);
assert.equal(sent.length, 0);
await postDueComments(env, NOW);
assert.equal(sent.length, 1); assert.match(sent[0].text, /yazılamadı/);
await postDueComments(env, NOW);
assert.equal(sent.length, 1);                                                    // gave up quietly after the alert

// the timer entry point exists
assert.equal(typeof worker.scheduled, "function");
// ---- watchdog: alerts that do not depend on GitHub ----
{
  const iso = (ms) => new Date(ms).toISOString();
  const at = (s) => new Date(s).getTime();
  // time zone maths, daylight saving aware
  assert.equal(iso(localToUtcMs(2026, 11, 13, 8, 0, "America/New_York")), "2026-11-13T13:00:00.000Z"); // winter (UTC-5)
  assert.equal(iso(localToUtcMs(2026, 7, 1, 8, 0, "America/New_York")), "2026-07-01T12:00:00.000Z"); // summer (UTC-4)
  assert.equal(iso(localToUtcMs(2027, 3, 14, 19, 0, "America/New_York")), "2027-03-14T23:00:00.000Z"); // the day DST starts
  assert.equal(iso(localToUtcMs(2026, 11, 1, 19, 0, "America/New_York")), "2026-11-02T00:00:00.000Z"); // the day DST ends
  assert.deepEqual(slotsBetween("money", at("2026-11-13T02:44:00Z"), at("2026-11-13T08:44:00Z")), []);
  assert.deepEqual(slotsBetween("money", at("2026-11-13T08:00:00Z"), at("2026-11-13T14:00:00Z")).map(iso), ["2026-11-13T13:00:00.000Z"]);
  assert.deepEqual(slotsBetween("psychology", at("2026-11-13T14:00:00Z"), at("2026-11-14T02:00:00Z")).map(iso), ["2026-11-14T00:00:00.000Z"]);

  const NOWW = new Date("2026-11-13T08:05:00Z");
  const entry = (channel, id, publishAt, title) => ({ plan_id: `${channel}-${publishAt}`, channel, topic: "t", title, video_id: id, url: `https://youtu.be/${id}`, publish_at: publishAt });
  const healthy = () => [
    entry("money", "ok1", "2026-11-13T00:00:00Z", "Money yesterday"), entry("psychology", "ok2", "2026-11-13T00:00:00Z", "Psych yesterday"),
    entry("money", "f1", "2026-11-13T13:00:00Z", "Money next"), entry("psychology", "f2", "2026-11-13T13:00:00Z", "Psych next"),
  ];
  Object.assign(VIDEOS, { ok1: { status: { privacyStatus: "public" }, statistics: {} }, ok2: { status: { privacyStatus: "public" }, statistics: {} } });
  const fresh = () => ({ ...env, KV: (() => { const m = new Map(); return { get: async (k) => (m.has(k) ? m.get(k) : null), put: async (k, v) => { m.set(k, v); } }; })() });
  const heartbeat = (hoursAgo) => { STATUS.updated_at = new Date(NOWW - hoursAgo * 3600000).toISOString(); };

  // gap detection
  const gaps = findGaps([entry("money", "f1", "2026-11-13T13:00:00Z", "x")], new Date("2026-11-13T08:05:00Z"));
  assert.deepEqual(gaps.map((g) => g.channel), ["psychology"]);
  assert.deepEqual(findGaps(healthy(), NOWW), []);

  // healthy system: silence
  HISTORY.entries = healthy(); heartbeat(3); sent.length = 0;
  assert.deepEqual(await watchdog(fresh(), NOWW), []); assert.equal(sent.length, 0);

  // a slot within 6 hours without a video: one alert, never repeated
  HISTORY.entries = healthy().filter((e) => e.video_id !== "f2"); sent.length = 0;
  const w1 = fresh();
  const first = await watchdog(w1, NOWW);
  assert.equal(first.length, 1); assert.match(first[0], /psychology/); assert.match(first[0], /planlanmamış/);
  assert.equal(sent.length, 1); assert.match(sent[0].text, /Otopilot uyarısı/);
  assert.deepEqual(await watchdog(w1, new Date("2026-11-13T08:15:00Z")), []); assert.equal(sent.length, 1); // same problem, no second message

  // a video whose publish time passed but is still private (locked / held by YouTube)
  HISTORY.entries = healthy(); VIDEOS.ok1 = { status: { privacyStatus: "private" }, statistics: {} }; sent.length = 0;
  const priv = await watchdog(fresh(), NOWW);
  assert.equal(priv.length, 1); assert.match(priv[0], /Money yesterday/); assert.match(priv[0], /herkese açık değil/);
  VIDEOS.ok1 = { status: { privacyStatus: "public" }, statistics: {} };
  // the heavier checks run once an hour only (minute < 10)
  VIDEOS.ok2 = { status: { privacyStatus: "private" }, statistics: {} };
  assert.deepEqual(await watchdog(fresh(), new Date("2026-11-13T08:25:00Z")), []);
  VIDEOS.ok2 = { status: { privacyStatus: "public" }, statistics: {} };

  // broken YouTube login
  sent.length = 0;
  tokenCache.clear(); // cached access tokens stay valid for up to an hour, then the refresh fails and this fires
  const noLogin = await watchdog({ ...fresh(), YOUTUBE_TOKENS_JSON: "" }, NOWW);
  assert.ok(noLogin.some((a) => /YouTube girişi çalışmıyor/.test(a)));

  // stale heartbeat (GitHub not running for over 30 hours)
  heartbeat(40); sent.length = 0;
  const stale = await watchdog(fresh(), NOWW);
  assert.equal(stale.length, 1); assert.match(stale[0], /kalp atışı 40 saat önce/);
  heartbeat(3);

  // the scheduled handler runs the watchdog next to the comment poster and never throws
  HISTORY.entries = healthy();
  await worker.scheduled({ cron: "*/10 * * * *" }, fresh(), { waitUntil: (p) => p });
  await new Promise((r) => setTimeout(r, 40));
}

console.log("worker tests passed");
