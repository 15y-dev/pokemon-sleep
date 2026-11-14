/* report_card.js — カードの描画。report_html.py が report.html に埋め込む(DATA・AX・META を使う)。 */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const hms = s => { if (!s) return '-'; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return (h ? h + '時間' : '') + (m || h ? m + '分' : '') + x + '秒'; };
const norm = s => String(s).normalize('NFKC').toLowerCase();
const PURPOSE_NAME = META.purposeShort;

function moons(n, cls = '') {
  return `<span class="moons ${cls}" role="img" aria-label="${n} / 5">` +
    [0, 1, 2, 3, 4].map(j => `<i class="${j < n ? 'on' : ''}"></i>`).join('') + '</span>';
}

function card(d, focus) {
  // focus: いま並べ替えに使っている角度の番号(なければ -1)。その角度を目立たせる
  const subs = d.subs.map(([n, on], i) => {
    const hit = n && AX.some((k, a) => d.hits[k].includes(n) && (focus < 0 || a === focus));
    return `<li class="${on ? 'on' : 'off'} ${hit ? 'hit' : ''}"><span class="lv">Lv${META.unlock[i]}</span><span class="nm">${esc(n || '-')}</span></li>`;
  }).join('');
  const angles = AX.map((k, a) => `<li class="${a === focus ? 'rel' : ''}" title="${esc(META.axisNote[k])}"><span class="an">${esc(k)}</span>` +
    `${moons(d.ax[a][0], 'sm')}<span class="rk">${d.ax[a][2]}位</span></li>`).join('');
  const helpDiff = d.helpNow - d.help, carryDiff = d.carry - d.carryNow;
  const notes = [];
  if (d.dessert) notes.push(`<span class="good">新デザート食材: ${esc(d.dessert)}</span>`);
  if (d.wiki) notes.push(`<span class="wiki">Wiki候補: ${esc(d.wiki)}</span>`);
  d.purposes.forEach(p => notes.push(`<span class="purp">${esc(PURPOSE_NAME[p])}</span>`));
  const imgT = d.img[0] ? `<button class="shot" data-open="${d.id}" aria-label="全体の画像を開く"><img loading="lazy" alt="No.${d.id} の上部" src="report_img/${d.id}_t.jpg"></button>` : '';
  const imgS = d.img[1] ? `<button class="skillshot" data-open="${d.id}" aria-label="全体の画像を開く"><img loading="lazy" alt="No.${d.id} のスキル部" src="report_img/${d.id}_s.jpg"></button>` : '';
  const helpNote = helpDiff > 0 ? `いま ${hms(d.helpNow)}(未解放のスキルで−${hms(helpDiff)})` : 'いまの値のまま';
  const carryNote = carryDiff > 0 ? `いま ${d.carryNow}個(未解放のスキルで+${carryDiff})` : 'いまの値のまま';
  return `<article class="card" style="--tc:${d.color}" data-id="${d.id}">
<div class="top"><div class="info">
<div class="rank">${moons(d.m)}<span class="pts">${d.pt}<small>pt</small></span><span class="no">No.${d.id}${d.fav ? ' ♥' : ''}</span></div>
<div class="name">${esc(d.name)}${d.nick ? `<small>「${esc(d.nick)}」</small>` : ''}</div>
<div class="meta"><span class="chip lv">Lv.${d.lv}</span><span class="chip type">${esc(d.type || '?')}</span><span class="chip">${esc(d.sp || '?')}とくい</span><span class="chip">${esc(d.berry || '?')}</span></div>
<dl class="facts">
<div class="kv"><dt>メインスキル</dt><dd>${esc(d.skill || '-')}</dd></div>
<div class="kv"><dt>せいかく</dt><dd>${esc(d.nature || '-')}<small>${esc(d.nat)}</small></dd></div>
<div class="kv"><dt>食材</dt><dd>${esc(d.ings.join(' / ') || '-')}</dd></div>
<div class="kv" title="せいかく・サブスキル(未解放のおてつだいスピードも含む)を反映した時間"><dt>おてつだい</dt><dd>${esc(d.helpText || '-')}<small>${helpNote}</small></dd></div>
<div class="kv" title="未解放の「最大所持数アップ」も含めた値"><dt>最大所持数</dt><dd>${d.carry} <small class="inline">個</small><small>${carryNote}</small></dd></div>
</dl></div>${imgT}</div>
<ul class="tl" aria-label="サブスキル">${subs}</ul>
<ul class="ang" aria-label="角度別の評価">${angles}</ul>
${notes.length ? `<div class="note">${notes.join('')}</div>` : ''}${imgS}</article>`;
}
