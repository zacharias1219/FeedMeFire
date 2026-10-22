// RoastMyFeed app flow: gate -> input -> review -> approve/prefs -> generate -> results.
// State lives on the server (sample, job, report) so a reload resumes where the user left off.
import { $, $$, h, clear, show } from './dom.js';
import { api, ApiError, setCsrf, newKey } from './api.js';
import { fillHero, renderBody, quoteChoices, INTENSITY_LABEL } from './render.js';
import { drawCard, downloadCanvas } from './card.js';
import { parseYouTubePaste } from './parse.js';

const STEPS = ['gate', 'input', 'review', 'prefs', 'generating', 'results', 'error', 'deleted'];
const PROGRESS = { input: 0, review: 1, prefs: 2, generating: 3, results: 4 };
const state = { cfg: null, session: null, sample: null, jobId: null, report: null, pendingKey: null, errBack: 'input', errRetry: null, polling: 0 };

const live = (msg) => { $('#live').textContent = ''; setTimeout(() => { $('#live').textContent = msg; }, 30); };
const setErr = (id, msg) => { const el = $(id); el.textContent = msg || ''; el.hidden = !msg; };

function go(step, { focus = true } = {}) {
  STEPS.forEach((s) => show($(`#step-${s}`), s === step));
  $$('#progress li').forEach((li) => {
    const idx = PROGRESS[li.dataset.step]; const cur = PROGRESS[step];
    li.classList.toggle('done', cur !== undefined && idx < cur);
    if (idx === cur) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
  });
  show($('#progress'), step in PROGRESS);
  if (focus) { const hd = $(`#step-${step} h1`); if (hd) { hd.focus({ preventScroll: false }); } }
  window.scrollTo({ top: 0 });
}

function friendly(e) {
  if (e instanceof ApiError) return e.message;
  return 'Something went wrong. Please try again.';
}

async function guard(fn, errId) {
  try { return await fn(); }
  catch (e) {
    if (e instanceof ApiError && (e.code === 'SESSION_EXPIRED' || e.code === 'DATA_DELETED')) { sessionEnded(); return undefined; }
    if (errId) setErr(errId, friendly(e)); else showError(friendly(e));
    return undefined;
  }
}

function sessionEnded() {
  state.session = null; state.sample = null; state.report = null; state.jobId = null; setCsrf(null);
  show($('#gate-expired'), true);
  go('gate');
}

function showError(msg, { back = 'input', retry = null } = {}) {
  $('#err-msg').textContent = msg;
  state.errBack = back; state.errRetry = retry;
  show($('#err-retry'), !!retry);
  go('error');
}

// ------------------------------------------------------------ gate
function wireGate() {
  const upd = () => { $('#gate-go').disabled = !($('#chk-age').checked && $('#chk-terms').checked); };
  $('#chk-age').addEventListener('change', upd); $('#chk-terms').addEventListener('change', upd);
  $('#gate-go').addEventListener('click', () => guard(async () => {
    setErr('#gate-err', '');
    state.session = await api('POST', '/api/v1/session', { age_attested: true });
    setCsrf(state.session.csrf_token);
    await api('POST', '/api/v1/consents', { consent_type: 'terms_privacy' });
    state.session.consents.push('terms_privacy');
    show($('#gate-expired'), false);
    go('input');
  }, '#gate-err'));
}

// ------------------------------------------------------------ input
const TYPE_LABEL = { video_title: 'Video', channel_name: 'Channel', interest: 'Interest' };

async function addItems(list) {
  setErr('#input-err', '');
  const cleaned = list.filter((i) => i.text.trim());
  if (!cleaned.length) return;
  const res = await guard(() => (state.sample
    ? api('PATCH', `/api/v1/samples/${state.sample.id}`, { add: cleaned })
    : api('POST', '/api/v1/samples', { source: 'manual', items: cleaned })), '#input-err');
  if (!res) return;
  state.sample = res;
  const notes = [];
  if (res.rejected && res.rejected.length) notes.push(`${res.rejected.length} not added: ${res.rejected[0].reason}`);
  if (res.duplicates_skipped) notes.push(`${res.duplicates_skipped} duplicate${res.duplicates_skipped > 1 ? 's' : ''} skipped.`);
  if (notes.length) setErr('#input-err', notes.join(' '));
  renderInput();
}

function renderInput() {
  const s = state.sample;
  const total = s ? s.submitted_item_count : 0;
  const eligible = s ? s.eligible_item_count : 0;
  const need = Math.max(0, state.cfg.min_items - eligible);
  $('#input-count').textContent = total
    ? `${total} added, ${eligible} eligible. ${need ? `Add ${need} more eligible item${need > 1 ? 's' : ''} to continue.` : 'You have enough. Add more or review.'}`
    : `Nothing added yet. You need ${state.cfg.min_items}.`;
  const have = new Set((s ? s.items : []).map((i) => i.item_type));
  const missing = ['video_title', 'channel_name', 'interest'].filter((t) => !have.has(t)).map((t) => TYPE_LABEL[t].toLowerCase());
  $('#suggest').textContent = need && missing.length
    ? `Tip: try adding some ${missing.join(' and ')} entries for better contradictions.`
    : 'Tip: mix types. A few videos, a few channels and a few interests give the funniest contradictions.';
  const chips = clear($('#chips'));
  (s ? s.items : []).forEach((i) => chips.append(h('li', { class: i.is_sensitive_flagged ? 'flagged' : '' },
    h('span', { class: 'tag' }, TYPE_LABEL[i.item_type]), ' ', i.text, ' ',
    h('button', { type: 'button', class: 'x', 'aria-label': `Remove ${i.text}`, onclick: () => removeItem(i.id) }, '×'))));
  $('#to-review').disabled = !total;
}

async function removeItem(id) {
  const res = await guard(() => api('PATCH', `/api/v1/samples/${state.sample.id}`, { remove: [id] }), '#input-err');
  if (res) { state.sample = res; renderInput(); if (!$('#step-review').hidden) renderReview(); }
}

let pending = null;  // parsed items awaiting the user's confirmation

function smartAdd(text) {
  $('#bulk-result').hidden = true; setErr('#input-err', '');
  const mode = $('input[name=paste-mode]:checked').value;
  const { items, stats } = parseYouTubePaste(text, mode, $('#add-type').value);
  if (!items.length) { setErr('#input-err', 'We could not find any items in that paste. Try Ctrl+A on the YouTube page, or switch the paste type.'); return; }
  pending = { items: items.slice(), stats };
  renderPreview();
}

function renderPreview() {
  const box = $('#bulk-preview');
  if (!pending || !pending.items.length) { box.hidden = true; pending = null; return; }
  const v = pending.items.filter((i) => i.item_type === 'video_title').length;
  const c = pending.items.filter((i) => i.item_type === 'channel_name').length;
  const o = pending.items.length - v - c;
  const parts = [];
  if (v) parts.push(`${v} video${v > 1 ? 's' : ''}`);
  if (c) parts.push(`${c} channel${c > 1 ? 's' : ''}`);
  if (o) parts.push(`${o} item${o > 1 ? 's' : ''}`);
  $('#bulk-summary').textContent = `Found ${parts.join(', ')}. We ignored ${pending.stats.skipped} lines of views, durations and dates.`;
  const list = clear($('#preview-chips'));
  pending.items.forEach((it, idx) => list.append(h('li', {},
    h('span', { class: 'tag' }, TYPE_LABEL[it.item_type]), ' ', it.text, ' ',
    h('button', { type: 'button', class: 'x', 'aria-label': `Don't add ${it.text}`, onclick: () => { pending.items.splice(idx, 1); renderPreview(); } }, '×'))));
  $('#bulk-confirm').firstElementChild.textContent = `Add ${pending.items.length}`;
  box.hidden = false;
}

async function confirmPreview() {
  if (!pending) return;
  const room = Math.max(0, state.cfg.max_submitted_items - (state.sample ? state.sample.submitted_item_count : 0));
  const kept = pending.items.slice(0, room);
  const cut = pending.items.length - kept.length;
  pending = null; renderPreview();
  await addItems(kept);
  const out = $('#bulk-result');
  out.textContent = `Added ${kept.length} item${kept.length === 1 ? '' : 's'}.${cut ? ` ${cut} didn't fit (limit ${state.cfg.max_submitted_items}).` : ''} Check them on the next screen.`;
  out.hidden = false; $('#bulk-text').value = '';
}

function wireInput() {
  $('#add-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const text = $('#add-text').value;
    await addItems([{ item_type: $('#add-type').value, text }]);
    $('#add-text').value = ''; $('#add-text').focus();
  });
  $('#bulk-add').addEventListener('click', () => smartAdd($('#bulk-text').value));
  $('#bulk-confirm').addEventListener('click', confirmPreview);
  $('#bulk-cancel').addEventListener('click', () => { pending = null; renderPreview(); });
  // A multi-line paste into the single-item box would otherwise collapse into one giant item.
  $('#add-text').addEventListener('paste', (ev) => {
    const txt = (ev.clipboardData || window.clipboardData).getData('text');
    if (txt && txt.includes('\n')) { ev.preventDefault(); $('#bulk-text').value = txt; smartAdd(txt); }
  });
  $('#use-demo').addEventListener('click', async () => {
    if (state.sample && state.sample.submitted_item_count && !confirm('Replace your current items with the fictional demo sample?')) return;
    const res = await guard(() => api('POST', '/api/v1/samples', { source: 'demo', items: [] }), '#input-err');
    if (res) { state.sample = res; renderInput(); }
  });
  $('#to-review').addEventListener('click', () => { renderReview(); go('review'); });
}

// ------------------------------------------------------------ review
function renderReview() {
  const s = state.sample;
  $('#review-summary').textContent = `${s.submitted_item_count} submitted · ${s.eligible_item_count} eligible · ${s.selected_count} will be analyzed`
    + (s.needed_more ? ` · add ${s.needed_more} more to continue` : '');
  const list = clear($('#items'));
  s.items.forEach((i) => {
    const input = h('input', { value: i.text, maxlength: 200, 'aria-label': `Edit ${TYPE_LABEL[i.item_type]} item` });
    input.addEventListener('change', async () => {
      const res = await guard(() => api('PATCH', `/api/v1/samples/${s.id}`, { update: [{ id: i.id, text: input.value }] }), '#review-err');
      if (res) { state.sample = res; setErr('#review-err', ''); renderReview(); renderInput(); } else { input.value = i.text; }
    });
    const status = i.is_sensitive_flagged
      ? h('span', { class: 'badge warn' }, `Not sent: looks like ${i.sensitive_category || 'sensitive'} content`)
      : i.is_selected ? h('span', { class: 'badge ok' }, 'Sent to the AI') : h('span', { class: 'badge' }, 'Not sent: over the item limit');
    list.append(h('li', { class: i.is_sensitive_flagged ? 'flagged' : '' },
      h('span', { class: 'tag' }, TYPE_LABEL[i.item_type]), input, status,
      h('button', { type: 'button', class: 'x', 'aria-label': `Remove ${i.text}`, onclick: () => removeItem(i.id) }, '×')));
  });
  $('#bound-count').textContent = s.model_bound.length;
  $('#bound-note').textContent = s.truncated
    ? `You added more than we send. A fixed, type-balanced selection of ${s.model_bound.length} is shown. Edit your items to change it.` : '';
  clear($('#bound')).append(...s.model_bound.map((m) => h('li', {}, h('span', { class: 'tag' }, TYPE_LABEL[m.item_type]), ' ', m.text)));
  $('#approve').disabled = s.needed_more > 0;
}

function wireReview() {
  $('#back-input').addEventListener('click', () => { renderInput(); go('input'); });
  $('#clear-all').addEventListener('click', async () => {
    if (!confirm('Remove every item?')) return;
    const res = await guard(() => api('PATCH', `/api/v1/samples/${state.sample.id}`, { clear: true }), '#review-err');
    if (res) { state.sample = res; renderInput(); go('input'); }
  });
  $('#approve').addEventListener('click', async () => {
    const res = await guard(() => api('POST', `/api/v1/samples/${state.sample.id}/approve`), '#review-err');
    if (res) { state.sample = res; resetPrefs(); go('prefs'); }
  });
}

// ------------------------------------------------------------ prefs
function resetPrefs() { ['#c-own', '#c-provider', '#c-fun'].forEach((id) => { $(id).checked = false; }); updPrefs(); }
function updPrefs() { $('#start').disabled = !($('#c-own').checked && $('#c-provider').checked && $('#c-fun').checked); }

async function ensureConsents() {
  const have = new Set(state.session.consents);
  for (const c of ['terms_privacy', 'own_sample', 'provider_send', 'entertainment']) {
    if (!have.has(c)) { await api('POST', '/api/v1/consents', { consent_type: c }); state.session.consents.push(c); }
  }
}

async function startRoast() {
  setErr('#prefs-err', '');
  const intensity = $('input[name=intensity]:checked').value;
  const job = await guard(async () => {
    await ensureConsents();
    state.pendingKey = state.pendingKey || newKey();  // reused if the request is retried, so it can't double-spend
    return api('POST', '/api/v1/reports', { sample_id: state.sample.id, approved_sample_hash: state.sample.approved_sample_hash, intensity, language: $('#lang').value },
      { 'Idempotency-Key': state.pendingKey });
  }, '#prefs-err');
  if (!job) return;
  state.pendingKey = null;
  beginPolling(job.job_id, { regenerate: false });
}

function wirePrefs() {
  ['#c-own', '#c-provider', '#c-fun'].forEach((id) => $(id).addEventListener('change', updPrefs));
  $('#back-review').addEventListener('click', () => { renderReview(); go('review'); });
  $('#start').addEventListener('click', startRoast);
}

// ------------------------------------------------------------ generating
const ORDER = ['queued', 'validating', 'generating', 'checking'];
function renderStage(status) {
  const idx = ORDER.indexOf(status);
  $$('#stages li').forEach((li, i) => {
    li.classList.toggle('done', i < idx); li.classList.toggle('current', i === idx);
    if (i === idx) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
  });
}

async function beginPolling(jobId, { regenerate }) {
  state.jobId = jobId; const token = ++state.polling;
  renderStage('queued');
  if (!regenerate) go('generating'); else { $('#regen').disabled = true; }
  let delay = 800, last = '';
  while (token === state.polling) {
    let job;
    try { job = await api('GET', `/api/v1/jobs/${jobId}`); }
    catch (e) {
      if (e instanceof ApiError && ['SESSION_EXPIRED', 'DATA_DELETED', 'NOT_FOUND'].includes(e.code)) { if (token === state.polling) sessionEnded(); return; }
      await new Promise((r) => setTimeout(r, 2500)); continue;  // transient: keep waiting
    }
    if (job.status !== last) { last = job.status; renderStage(job.status); live(job.stage_label); }
    if (job.status === 'ready') { state.jobId = null; await loadReport(job.report_id); return; }
    if (job.status === 'failed') {
      state.jobId = null; $('#regen').disabled = false;
      const retry = job.error && job.error.retryable ? () => (regenerate ? go('results') : retryGeneration()) : null;
      if (regenerate) { setErr('#regen-err', job.error ? job.error.message : 'Generation failed.'); go('results', { focus: false }); }
      else showError(job.error ? job.error.message : 'Generation failed.', { back: 'prefs', retry });
      return;
    }
    if (job.status === 'cancelled') { state.jobId = null; $('#regen').disabled = false; if (!regenerate) go('prefs'); return; }
    await new Promise((r) => setTimeout(r, delay)); delay = Math.min(delay * 1.25, 2500);
  }
}

function retryGeneration() { go('prefs'); }

function wireGenerating() {
  $('#cancel').addEventListener('click', async () => {
    if (!state.jobId) return;
    state.polling++;
    await guard(() => api('POST', `/api/v1/jobs/${state.jobId}/cancel`));
    state.jobId = null; go('prefs');
  });
}

// ------------------------------------------------------------ results
async function loadReport(id) {
  const r = await guard(() => api('GET', `/api/v1/reports/${id}`));
  if (!r) return;
  state.report = r;
  const { report, evidence } = r;
  fillHero(report, r.intensity);
  renderBody($('#report-body'), report, evidence);
  $('#res-source').textContent = r.provider === 'fake' ? 'Written by the built-in demo generator (canned template, not an AI model).' : `Written by ${state.cfg.provider_name} · ${r.model}`;
  $('#report-body').lang = r.language || 'en'; $('#res-h').lang = r.language || 'en'; $('#res-persona').lang = r.language || 'en';
  $('#res-expires').textContent = `at ${new Date(r.expires_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`;
  const pick = clear($('#quote-pick'));
  quoteChoices(report).forEach((q, i) => pick.append(h('option', { value: String(i) }, q)));
  $('#fb-ok').hidden = true; $('#fb-form').reset();
  show($('#fb-form'), true);
  const rg = $('#regen-panel');
  show(rg, r.can_regenerate);
  if (r.can_regenerate) {
    $('#attempts-left').textContent = String(r.attempts_remaining);
    clear($('#regen-int')).append(...['gentle', 'spicy', 'savage'].filter((x) => x !== r.intensity)
      .map((x) => h('option', { value: x }, INTENSITY_LABEL[x])));
  }
  $('#regen').disabled = false; setErr('#regen-err', '');
  go('results'); live('Your roast is ready.');
  redrawCard();
}

function redrawCard() {
  const r = state.report; if (!r) return;
  const q = quoteChoices(r.report)[+$('#quote-pick').value || 0];
  drawCard($('#card'), { persona: r.report.persona.label, quote: q, intensityLabel: INTENSITY_LABEL[r.intensity] });
}

function wireResults() {
  $('#quote-pick').addEventListener('change', redrawCard);
  $('#download').addEventListener('click', async () => {
    try {
      await downloadCanvas($('#card')); setErr('#card-err', '');
      api('POST', '/api/v1/events', { name: 'export_downloaded' }).catch(() => {});
    } catch { setErr('#card-err', 'Could not export the image in this browser. Your full roast is still on this page, and you can screenshot the preview.'); }
  });
  $('#regen').addEventListener('click', async () => {
    setErr('#regen-err', '');
    const r = state.report;
    const job = await guard(() => api('POST', `/api/v1/reports/${r.id}/regenerate`, { intensity: $('#regen-int').value },
      { 'Idempotency-Key': newKey() }), '#regen-err');
    if (job) beginPolling(job.job_id, { regenerate: true });
  });
  $('#fb-form').addEventListener('submit', async (ev) => {
    ev.preventDefault(); setErr('#fb-err', '');
    const v = (n) => { const el = $(`input[name=${n}]:checked`); return el ? el.value === '1' : null; };
    const res = await guard(() => api('POST', `/api/v1/reports/${state.report.id}/feedback`,
      { is_funny: v('funny'), is_specific: v('specific'), is_too_harsh: v('harsh'), comment: $('#fb-comment').value || null }), '#fb-err');
    if (res) { $('#fb-ok').textContent = `Thanks! ${res.note}`; $('#fb-ok').hidden = false; show($('#fb-form'), false); }
  });
  $('#delete').addEventListener('click', () => $('#confirm-delete').showModal());
  $('#confirm-delete').addEventListener('close', async () => {
    if ($('#confirm-delete').returnValue !== 'confirm') return;
    state.polling++;
    const ok = await guard(() => api('DELETE', '/api/v1/session/data'));
    if (ok) { state.session = state.sample = state.report = null; setCsrf(null); go('deleted'); }
  });
  $('#again').addEventListener('click', async () => {
    state.sample = null; renderInput(); go('input');
  });
  $('#err-back').addEventListener('click', () => { if (state.errBack === 'prefs') go('prefs'); else { renderInput(); go('input'); } });
  $('#err-retry').addEventListener('click', () => { if (state.errRetry) state.errRetry(); });
}

// ------------------------------------------------------------ boot
async function boot() {
  state.cfg = await api('GET', '/api/v1/config');
  $$('[data-bind=min]').forEach((e) => { e.textContent = state.cfg.min_items; });
  show($('#demo-banner'), !!state.cfg.is_demo_generator);
  clear($('#lang')).append(...state.cfg.languages.map((l) => h('option', { value: l.code }, l.code === 'en' ? 'English' : `${l.native} (${l.name})`)));
  $('#lang-note').textContent += state.cfg.provider === 'fake' ? ' Note: the built-in demo generator always writes English; connect Sarvam AI for other languages.' : '';
  $$('[data-bind=provider]').forEach((e) => { e.textContent = state.cfg.provider_name; });
  $$('[data-bind=feedback_days]').forEach((e) => { e.textContent = '30'; });
  wireGate(); wireInput(); wireReview(); wirePrefs(); wireGenerating(); wireResults();

  try { state.session = await api('GET', '/api/v1/session'); setCsrf(state.session.csrf_token); }
  catch { go('gate', { focus: false }); return; }  // no valid session: start at the gate

  const s = state.session;
  if (s.active_job_id) { beginPolling(s.active_job_id, { regenerate: false }); return; }
  if (s.latest_report_id) { await loadReport(s.latest_report_id); return; }
  if (s.sample_id) {
    const sample = await guard(() => api('GET', `/api/v1/samples/${s.sample_id}`));
    if (sample) {
      state.sample = sample; renderInput();
      if (sample.status === 'approved') { resetPrefs(); go('prefs', { focus: false }); }
      else go('input', { focus: false });
      return;
    }
  }
  renderInput(); go('input', { focus: false });
}

boot();
