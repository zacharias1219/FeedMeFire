import { h, clear, $ } from './dom.js';

const TYPE_LABEL = { video_title: 'Video', channel_name: 'Channel', interest: 'Interest' };
export const INTENSITY_LABEL = { gentle: 'Gentle', spicy: 'Spicy', savage: 'Savage' };

function evidenceList(ids, evidence) {
  return h('ul', { class: 'evidence' }, ids.map((id) => {
    const e = evidence[id];
    return h('li', {}, h('span', { class: 'tag' }, e ? TYPE_LABEL[e.item_type] : 'Item'), ' ', e ? e.text : id);
  }));
}

function why(ids, evidence) {
  return h('details', { class: 'why' }, h('summary', {}, 'Why this joke?'),
    h('p', { class: 'hint' }, 'Only these items from your sample were used for it:'), evidenceList(ids, evidence));
}

/** Header block shared by the live result and the demo. */
export function fillHero(report, intensity) {
  $('#res-intensity').textContent = `${INTENSITY_LABEL[intensity] || ''} roast`;
  $('#res-h').textContent = report.headline;
  $('#res-persona').textContent = report.persona.label;
  $('#res-persona-note').textContent = report.persona.description;
  $('#res-disclaimer').textContent = report.disclaimer;
}

/** Opening roast, observations, contradictions and closer. All text is set via textContent. */
export function renderBody(container, report, evidence) {
  clear(container);
  container.append(
    h('div', { class: 'panel' }, h('p', { class: 'opening' }, report.opening_roast)),
    h('div', { class: 'cards' }, report.observations.map((o) =>
      h('article', { class: 'card-obs' }, h('h3', {}, o.title), h('p', {}, o.joke), why(o.evidence_item_ids, evidence)))),
    h('div', { class: 'panel' }, h('h2', {}, 'Two playful contradictions'),
      report.contradictions.map((c) => h('div', { class: 'contra' }, h('p', {}, c.joke), why(c.evidence_item_ids, evidence)))),
    h('div', { class: 'panel closer' }, h('p', {}, report.affectionate_closer)),
  );
}

export function quoteChoices(report) {
  const out = [report.share_quote];
  for (const c of report.contradictions) if (c.joke.length <= 160) out.push(c.joke);
  return [...new Set(out)];
}
