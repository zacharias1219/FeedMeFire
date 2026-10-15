import { $, h } from './dom.js';
import { fillHero, renderBody, quoteChoices, INTENSITY_LABEL } from './render.js';
import { drawCard, downloadCanvas } from './card.js';

const data = await (await fetch('/static/demo.json')).json();
const { report, evidence, intensity, items } = data;
$('#demo-items').append(...items.map((t) => h('li', {}, t)));
fillHero(report, intensity);
renderBody($('#report-body'), report, evidence);

const pick = $('#quote-pick');
quoteChoices(report).forEach((q, i) => pick.append(h('option', { value: String(i) }, q)));
const quotes = quoteChoices(report);
const redraw = () => drawCard($('#card'), { persona: report.persona.label, quote: quotes[+pick.value], intensityLabel: INTENSITY_LABEL[intensity] });
pick.addEventListener('change', redraw);
redraw();
$('#download').addEventListener('click', async () => {
  try { await downloadCanvas($('#card')); $('#card-err').hidden = true; }
  catch { const e = $('#card-err'); e.textContent = 'Could not export the image here. The full roast is still readable above.'; e.hidden = false; }
});
