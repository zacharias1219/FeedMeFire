// Share-card export, rendered locally in the browser. Only persona + one quote + intensity are drawn:
// source items never reach the canvas (PRD §7.9).
const W = 1080, H = 1350;

function wrap(ctx, text, maxWidth) {
  const words = text.split(/\s+/); const lines = []; let line = '';
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (ctx.measureText(t).width > maxWidth && line) { lines.push(line); line = w; } else line = t;
  }
  if (line) lines.push(line);
  return lines;
}

export async function drawCard(canvas, { persona, quote, intensityLabel }) {
  try { await Promise.all([document.fonts.load('900 64px Poppins'), document.fonts.load('500 32px Poppins')]); } catch { /* fall back */ }
  const ctx = canvas.getContext('2d');
  canvas.width = W; canvas.height = H;
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, '#04060c'); bg.addColorStop(0.6, '#07101c'); bg.addColorStop(1, '#0a2433');
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
  const glow = ctx.createRadialGradient(W / 2, H, 40, W / 2, H, 760);
  glow.addColorStop(0, 'rgba(62,200,228,.45)'); glow.addColorStop(1, 'rgba(62,200,228,0)');
  ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);

  const sans = 'Poppins, "Noto Sans Devanagari", "Noto Sans Tamil", "Noto Sans Telugu", "Noto Sans Bengali", "Nirmala UI", system-ui, sans-serif';
  ctx.fillStyle = '#fff'; ctx.textBaseline = 'alphabetic';
  ctx.font = `900 44px ${sans}`; ctx.fillText('ROASTMYFEED', 80, 130);
  ctx.font = `500 28px ${sans}`; ctx.fillStyle = '#8fdcee'; ctx.fillText('Your taste. On trial.', 80, 176);

  ctx.fillStyle = '#a9aeb5'; ctx.font = `600 30px ${sans}`; ctx.fillText('MY VIEWING PERSONA', 80, 330);
  ctx.fillStyle = '#fff'; ctx.font = `900 72px ${sans}`;
  let y = 420;
  for (const l of wrap(ctx, persona.toUpperCase(), W - 160).slice(0, 3)) { ctx.fillText(l, 80, y); y += 84; }

  // Fit the quote: shrink until it fits in the box.
  let size = 66, lines;
  const maxLines = 7, top = y + 70, boxH = 560;
  for (; size >= 34; size -= 4) {
    ctx.font = `600 ${size}px ${sans}`;
    lines = wrap(ctx, `“${quote}”`, W - 160);
    if (lines.length * size * 1.28 <= boxH && lines.length <= maxLines) break;
  }
  ctx.fillStyle = '#fff';
  lines.forEach((l, i) => ctx.fillText(l, 80, top + i * size * 1.28));

  ctx.fillStyle = 'rgba(255,255,255,.12)'; ctx.fillRect(80, H - 190, W - 160, 2);
  ctx.fillStyle = '#c8eef7'; ctx.font = `600 30px ${sans}`; ctx.fillText(`${intensityLabel} roast`, 80, H - 130);
  ctx.fillStyle = '#a9aeb5'; ctx.font = `500 26px ${sans}`;
  ctx.fillText('AI-generated entertainment. Not a record of real viewing.', 80, H - 80);
}

export function downloadCanvas(canvas, filename = 'roastmyfeed-card.png') {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => {
        if (!blob) return reject(new Error('export failed'));
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = filename;
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000); resolve();
      }, 'image/png');
    } catch (e) { reject(e); }
  });
}
