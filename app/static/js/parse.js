// Turns text a user copied from their own YouTube pages (Liked videos, playlists, History, Subscriptions)
// into sample items. Pure and client-side: we never contact YouTube; the user pastes, reviews, then approves.

const DURATION = /^(?:\d{1,2}:)?\d{1,2}:\d{2}$/;
const COUNT = /^\d[\d.,]*\s*[kKmMbB]?(?:\s*(?:views?|watching|subscribers?|likes?|comments?|व्यू\w*|बार देखा गया))?$/i;
const REL_TIME = /^(?:streamed |premiered )?\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago$/i;
const NO_VIEWS = /^no views$/i;
const INLINE_VIEWS = /\d[\d.,]*\s*[kKmMbB]?\s+(?:views?|watching|subscribers?)\b/i;
const DATE_HEADING = /^(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?$/i;
const NOISE = new Set(['shorts', 'now playing', 'live', 'new', 'mix', 'watch later', 'save to playlist', 'share', 'subscribe',
  'subscribed', 'verified', 'remove from watch history', 'search', 'home', 'history', 'today', 'yesterday', 'this week',
  'this month', 'older', 'playlists', 'videos', 'all', 'unwatched', 'watched', 'cc', '4k', 'hd', 'autoplay', 'up next',
  'more', 'show more', 'show less', 'sort by', 'filters', 'manage all history', 'pause watch history',
  'clear all watch history', 'history type', 'comments', 'posts', 'shopping', '•', '·', '...', '…', 'you', 'explore',
  'subscriptions', 'library', 'liked videos', 'sign in', 'trending', 'music', 'gaming', 'news']);
const MAX = 200;

function kind(line) {
  if (DURATION.test(line)) return 'duration';
  if (COUNT.test(line) || REL_TIME.test(line) || NO_VIEWS.test(line)) return 'meta';
  if (line.length <= 50 && INLINE_VIEWS.test(line)) return 'meta';
  if (NOISE.has(line.toLowerCase()) || DATE_HEADING.test(line)) return 'noise';
  return 'text';
}

function dedupe(list) {
  const seen = new Set(); const out = [];
  for (const t of list) { const k = t.toLowerCase(); if (!seen.has(k)) { seen.add(k); out.push(t); } }
  return out;
}

/**
 * mode: 'videos'   liked videos / playlists / history (title + channel blocks) -> titles AND channels
 *       'channels' subscriptions page -> channel names
 *       'plain'    one item per line, typed by `plainType`
 * Returns { items: [{item_type, text}], stats: { videos, channels, others, skipped } }.
 */
export function parseYouTubePaste(raw, mode = 'videos', plainType = 'interest') {
  // "Channel • 289 views • 3 weeks ago" is split into separate segments so metadata never swallows a name.
  const lines = String(raw || '').replace(/\r/g, '').split('\n')
    .flatMap((l) => l.split(/\s[•·]\s/))
    .map((s) => s.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const kinds = lines.map(kind);
  const clip = (s) => s.slice(0, MAX).trim();
  const stats = { videos: 0, channels: 0, others: 0, skipped: 0 };
  const items = [];

  if (mode === 'plain') {
    // Even a plain list never wants "6:30", "289" or "3 weeks ago" as an item.
    for (const l of dedupe(lines.filter((_, i) => kinds[i] === 'text').map(clip))) items.push({ item_type: plainType, text: l });
    stats.others = items.length; stats.skipped = lines.length - items.length;
    return { items, stats };
  }

  if (mode === 'channels') {
    const names = lines.filter((l, i) => kinds[i] === 'text' && !l.startsWith('@') && l.length <= 80 && !/subscribers?$/i.test(l));
    for (const n of dedupe(names.map(clip))) items.push({ item_type: 'channel_name', text: n });
    stats.channels = items.length; stats.skipped = lines.length - items.length;
    return { items, stats };
  }

  // videos: if there is no duration/view metadata the paste is just a list of titles
  const structured = kinds.some((k) => k === 'duration' || k === 'meta');
  if (!structured) {
    for (const t of dedupe(lines.filter((_, i) => kinds[i] === 'text').map(clip))) items.push({ item_type: 'video_title', text: t });
    stats.videos = items.length; stats.skipped = lines.length - items.length;
    return { items, stats };
  }

  // Structured page text repeats: title, channel, views / age, duration. Text runs are split by metadata
  // lines, so a run of 2 is [title, channel]; a trailing run of 1 (cut-off selection) is just a title.
  const titles = []; const channels = [];
  let run = [];
  const flush = () => {
    for (let i = 0; i < run.length; i += 2) {
      titles.push(run[i]);
      if (run[i + 1] !== undefined) channels.push(run[i + 1]);
    }
    run = [];
  };
  lines.forEach((l, i) => {
    if (kinds[i] === 'text') run.push(clip(l));
    else if (kinds[i] !== 'noise') flush();
  });
  flush();
  const t = dedupe(titles); const c = dedupe(channels);
  t.forEach((x) => items.push({ item_type: 'video_title', text: x }));
  c.forEach((x) => items.push({ item_type: 'channel_name', text: x }));
  stats.videos = t.length; stats.channels = c.length; stats.skipped = lines.length - titles.length - channels.length;
  return { items, stats };
}
