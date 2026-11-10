// Offline test of the Telegram worker: fake fetch, no network. Run: node worker/test.mjs
import assert from "node:assert/strict";
import worker from "./index.js";

const { fmtSlot, checkScheduled, replyFor, handleUpdate, postDueComments } = worker.internals;
const NOW = new Date("2026-11-10T12:00:00Z");
const sent = [];
let ytError = null;
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
  if (url.includes("api.telegram.org")) { sent.push(JSON.parse(opts.body)); return json({ ok: true }); }
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
    if (url.includes("/channels")) return json({ items: [{ snippet: { title: "Money Channel" }, statistics: { subscriberCount: "1234", viewCount: "56789", videoCount: "7" } }] });
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
assert.match(stats, /Money Channel/); assert.match(stats, /1\.234 abone/); assert.match(stats, /1\.500/);
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
console.log("worker tests passed");
