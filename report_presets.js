/* report_presets.js — プリセット(条件をまとめて設定)。report_filter.js の後に埋め込む。
   組み込み(META.presets)と、自分で保存したもの(localStorage)を同じ形で扱う。
   形: {name, note, state}。state のキーは report_html.py の PRESETS の説明を参照。 */
const STORE_KEY = 'pokesleep-presets-v1';

function readMine() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch (e) { return []; }
}
function writeMine(list) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
}

// いまの画面の条件を、state にまとめる(空の条件は入れない)
function captureState() {
  const f = filters(), s = {};
  ['type', 'sp', 'berry', 'skill', 'sub', 'ing', 'natUp', 'natDown'].forEach(g => { const v = checked(g); if (v.length) s[g] = v; });
  if ($('#q').value.trim()) s.q = $('#q').value.trim();
  if (f.mT) s.mT = f.mT;
  if (f.m.some(Boolean)) s.m = f.m;
  ['helpMax', 'carryMin', 'lvMin', 'lvMax'].forEach(k => { if (f[k] != null) s[k] = f[k]; });
  if (f.fav) s.fav = true;
  if (f.dessert) s.dessert = true;
  if (f.wiki) s.wiki = true;
  s.sort = $('#sort').value;
  return s;
}

// state を画面に反映する。いまの条件は、いったんすべて消す
function restoreState(s) {
  $$('#panel input[type=checkbox]').forEach(i => i.checked = false);
  $$('#panel input[type=number]').forEach(i => i.value = '');
  $$('#panel select').forEach(e => e.value = '0');
  $('#q').value = s.q || '';
  ['type', 'sp', 'berry', 'skill', 'sub', 'ing', 'natUp', 'natDown'].forEach(g => {
    const want = new Set(s[g] || []);
    $$(`input[data-g="${g}"]`).forEach(i => i.checked = want.has(g === 'sp' ? i.value.replace(/とくい$/, '') : i.value));
  });
  $('#mT').value = s.mT || 0;
  AX.forEach((_, a) => $('#m' + a).value = (s.m && s.m[a]) || 0);
  [['helpMax', '#helpMax'], ['carryMin', '#carryMin'], ['lvMin', '#lvMin'], ['lvMax', '#lvMax']].forEach(([k, id]) => $(id).value = s[k] ?? '');
  $('#favOnly').checked = !!s.fav;
  $('#dessertOnly').checked = !!s.dessert;
  $('#wikiOnly').checked = !!s.wiki;
  $('#sort').value = SORTS[s.sort] ? s.sort : 'total';
  // 絞り込みの欄は、条件が入っているものを開いておく
  $$('details.grp').forEach(d => { if ($$('input:checked', d).length) d.open = true; });
  apply();
}

function renderPresets() {
  const mine = readMine();
  const btn = (p, i, own) => `<span class="pchip ${own ? 'own' : ''}"><button type="button" class="pbtn" data-p="${own ? 'm' : 'b'}${i}" title="${esc(p.note || '')}">${esc(p.name)}</button>` +
    (own ? `<button type="button" class="pdel" data-del="${i}" aria-label="「${esc(p.name)}」を削除" title="削除">×</button>` : '') + '</span>';
  $('#presetList').innerHTML = META.presets.map((p, i) => btn(p, i, false)).join('') + mine.map((p, i) => btn(p, i, true)).join('') +
    '<button class="btn psave" id="presetSave" type="button" title="いまの条件と並び順に名前をつけて、このブラウザに保存します">＋ いまの条件を保存</button>';
}

$('#presetList').addEventListener('click', e => {
  if (e.target.closest('#presetSave')) return saveCurrent();
  const del = e.target.closest('[data-del]');
  if (del) {
    const list = readMine(), i = +del.dataset.del;
    if (confirm(`プリセット「${list[i].name}」を削除しますか?`)) { list.splice(i, 1); writeMine(list); renderPresets(); }
    return;
  }
  const b = e.target.closest('[data-p]');
  if (!b) return;
  const src = b.dataset.p[0] === 'b' ? META.presets : readMine();
  const p = src[+b.dataset.p.slice(1)];
  restoreState(p.state);
  $$('.pbtn').forEach(x => x.classList.toggle('cur', x === b));
  window.scrollTo({top: 0, behavior: 'smooth'});
});

function saveCurrent() {
  const name = (prompt('プリセットの名前を入力してください(このブラウザに保存されます)') || '').trim();
  if (!name) return;
  const list = readMine(), at = list.findIndex(p => p.name === name);
  if (META.presets.some(p => p.name === name)) { alert('その名前は、組み込みのプリセットで使われています。別の名前にしてください。'); return; }
  if (at >= 0 && !confirm(`「${name}」はすでにあります。上書きしますか?`)) return;
  const p = {name, note: '自分で保存した条件', state: captureState()};
  if (at >= 0) list[at] = p; else list.push(p);
  if (!writeMine(list)) { alert('保存できませんでした(このブラウザでは、保存が許可されていません)。'); return; }
  renderPresets();
}

// 条件を手で変えたら、プリセットの選択表示を外す
$('#panel').addEventListener('input', () => $$('.pbtn.cur').forEach(x => x.classList.remove('cur')));
$('#q').addEventListener('input', () => $$('.pbtn.cur').forEach(x => x.classList.remove('cur')));
renderPresets();
