/* report_filter.js — 絞り込み・並べ替え・表示。report_card.js の後に埋め込む。 */
const checked = g => $$(`input[data-g="${g}"]:checked`).map(e => e.value);
const num = id => { const v = $(id).value; return v === '' ? null : +v; };
const GROUPS = ['type', 'sp', 'berry', 'skill', 'sub', 'ing', 'natUp', 'natDown'];
const SORTS = {
  total: (a, b) => a.rank - b.rank,
  no: (a, b) => a.id.localeCompare(b.id),
  lvHigh: (a, b) => b.lv - a.lv || a.rank - b.rank,
  lvLow: (a, b) => a.lv - b.lv || a.rank - b.rank,
  helpFast: (a, b) => a.help - b.help || a.rank - b.rank,
  carryMany: (a, b) => b.carry - a.carry || a.rank - b.rank,
};
AX.forEach((k, a) => SORTS['ax' + a] = (x, y) => x.ax[a][2] - y.ax[a][2] || x.rank - y.rank);

let shown = [], page = 0;
const PAGE = 60;

// 検索用の文字列(全部の項目を連結)
DATA.forEach(d => {
  d.text = norm([d.id, d.name, d.nick, d.type, d.sp + 'とくい', d.berry, d.skill, d.nature, d.ings.join(' '),
    d.subs.map(s => s[0]).join(' '), d.dessert, d.wiki].join(' '));
});

function filters() {
  return {
    words: norm($('#q').value).split(/\s+/).filter(Boolean),
    type: checked('type'), sp: checked('sp').map(v => v.replace(/とくい$/, '')),
    berry: checked('berry'), skill: checked('skill'), sub: checked('sub'), ing: checked('ing'),
    natUp: checked('natUp'), natDown: checked('natDown'),
    mT: +$('#mT').value, m: AX.map((_, a) => +$('#m' + a).value),
    helpMax: num('#helpMax'), carryMin: num('#carryMin'), lvMin: num('#lvMin'), lvMax: num('#lvMax'),
    fav: $('#favOnly').checked, dessert: $('#dessertOnly').checked, wiki: $('#wikiOnly').checked,
  };
}

function matches(d, f) {
  if (f.words.length && !f.words.every(w => d.text.includes(w))) return false;
  if (f.type.length && !f.type.includes(d.type)) return false;
  if (f.sp.length && !f.sp.includes(d.sp)) return false;
  if (f.berry.length && !f.berry.includes(d.berry)) return false;
  if (f.skill.length && !f.skill.includes(d.skill)) return false;
  if (f.natUp.length && !f.natUp.includes(d.natUp)) return false;
  if (f.natDown.length && !f.natDown.includes(d.natDown)) return false;
  if (f.sub.length && !f.sub.every(s => d.subs.some(([n]) => n === s))) return false;
  if (f.ing.length && !f.ing.every(s => d.ings.includes(s))) return false;
  if (f.mT && d.m < f.mT) return false;
  for (let a = 0; a < AX.length; a++) if (f.m[a] && d.ax[a][0] < f.m[a]) return false;
  if (f.helpMax != null && !(d.help <= f.helpMax * 60)) return false;
  if (f.carryMin != null && !(d.carry >= f.carryMin)) return false;
  if (f.lvMin != null && d.lv < f.lvMin) return false;
  if (f.lvMax != null && d.lv > f.lvMax) return false;
  if (f.fav && !d.fav) return false;
  if (f.dessert && !d.dessert) return false;
  if (f.wiki && !d.wiki) return false;
  return true;
}

function summary(f) {
  const chips = [];
  ['type', 'sp', 'berry', 'skill', 'sub', 'ing', 'natUp', 'natDown'].forEach(g => checked(g).forEach(v => chips.push(v)));
  if (f.mT) chips.push('総合 月' + f.mT + '以上');
  AX.forEach((k, a) => f.m[a] && chips.push(k + ' 月' + f.m[a] + '以上'));
  if (f.helpMax != null) chips.push('おてつだい ' + f.helpMax + '分以下');
  if (f.carryMin != null) chips.push('所持数 ' + f.carryMin + '個以上');
  if (f.lvMin != null || f.lvMax != null) chips.push(`Lv ${f.lvMin ?? ''}〜${f.lvMax ?? ''}`);
  if (f.fav) chips.push('お気に入り');
  if (f.dessert) chips.push('新デザート食材');
  if (f.wiki) chips.push('Wiki候補');
  $('#chips').innerHTML = chips.map(c => `<span>${esc(c)}</span>`).join('');
}

function apply() {
  const f = filters(), key = $('#sort').value;
  shown = DATA.filter(d => matches(d, f)).sort(SORTS[key]);
  page = 0;
  $('#list').innerHTML = '';
  more();
  $('#shown').textContent = shown.length;
  $('#total').textContent = DATA.length;
  $('#none').hidden = shown.length > 0;
  GROUPS.forEach(g => { const n = checked(g).length, el = $(`[data-cnt="${g}"]`); if (el) el.textContent = n ? n + '件' : ''; });
  const active = $$('#panel input[type=checkbox]:checked').length + $$('#panel select').filter(s => +s.value).length +
    $$('#panel input[type=number]').filter(i => i.value !== '').length + ($('#q').value ? 1 : 0);
  $('#reset').disabled = !active;
  summary(f);
}

function more() {
  const key = $('#sort').value, focus = key.startsWith('ax') ? +key.slice(2) : -1;
  $('#list').insertAdjacentHTML('beforeend', shown.slice(page * PAGE, (page + 1) * PAGE).map(d => card(d, focus)).join(''));
  page++;
  const rest = Math.max(0, shown.length - page * PAGE);
  $('#more').hidden = rest === 0;
  $('#more').textContent = `さらに表示(残り ${rest}匹)`;
}

// 全体画像(撮影したパノラマ)を開く
const dlg = $('#viewer');
document.addEventListener('click', e => {
  const b = e.target.closest('[data-open]');
  if (!b) return;
  const d = DATA.find(x => x.id === b.dataset.open);
  $('img', dlg).src = 'panoramas/' + d.id + '.png';
  $('.cap', dlg).textContent = `No.${d.id} ${d.name}`;
  dlg.showModal();
});
dlg.addEventListener('click', () => dlg.close());

// 操作
$('#panel').addEventListener('input', apply);
$('#panel').addEventListener('change', apply);
$('#q').addEventListener('input', apply);
$('#sort').addEventListener('change', apply);
$('#more').addEventListener('click', more);
$('#reset').addEventListener('click', () => {
  $$('#panel input[type=checkbox]').forEach(i => i.checked = false);
  $$('#panel input[type=number]').forEach(i => i.value = '');
  $$('#panel select').forEach(s => s.value = '0');
  $('#q').value = '';
  apply();
});
$('#panelToggle').addEventListener('click', () => document.body.classList.toggle('panel-open'));
new IntersectionObserver(es => es[0].isIntersecting && !$('#more').hidden && more(), {rootMargin: '800px'}).observe($('#sentinel'));
apply();
