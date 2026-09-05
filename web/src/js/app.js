/* ==========================================================================
   wyrmhoard — dashboard

   No framework, no build step, no dependencies. Two reasons: this has to keep
   working in five years without anyone running `npm install`, and a design
   swap should not require re-implementing anything.

   The contract with the markup is small:
     [data-bind="path.into.state"]  — filled with a formatted scalar
     [data-format="money|money2|pct|weeks|int|date|text|link"]
     [data-list="name"]             — a renderer fills this container
   ========================================================================== */

const API = '/api';

const state = {};
let RULES = [];

/* ---------- helpers ---------------------------------------------------- */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function money(v, dp = 0) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const s = Math.abs(n).toLocaleString('en-NZ', {
    minimumFractionDigits: dp, maximumFractionDigits: dp,
  });
  return (n < 0 ? '-$' : '$') + s;
}

const FORMATTERS = {
  money:  v => money(v, 0),
  money2: v => money(v, 2),
  pct:    v => (v === null || v === undefined ? '—' : `${Number(v).toFixed(0)}%`),
  weeks:  v => (v === null || v === undefined ? '—' : `${Number(v).toFixed(0)} weeks`),
  int:    v => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-NZ')),
  date:   v => (v ? new Date(v).toLocaleDateString('en-NZ',
                  { day: 'numeric', month: 'short', year: 'numeric' }) : '—'),
  text:   v => (v === null || v === undefined || v === '' ? '—' : String(v)),
  link:   v => v || '',
};

function pick(path, root = state) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), root);
}

/** Fill every [data-bind] on the page from the current state. */
function applyBindings() {
  $$('[data-bind]').forEach(el => {
    const raw = pick(el.dataset.bind);
    const fmt = FORMATTERS[el.dataset.format || 'text'] || FORMATTERS.text;
    if (el.dataset.format === 'link') {
      el.innerHTML = raw
        ? `IRD's own calculator is the authority: <a href="${esc(raw)}" target="_blank" rel="noopener">${esc(raw)}</a>`
        : '';
      return;
    }
    el.textContent = fmt(raw);
    // Colour the headline numbers by sign, but only where it carries meaning.
    if (el.classList.contains('v') && typeof raw === 'number' &&
        el.dataset.bind.includes('net')) {
      el.classList.toggle('good', raw >= 0);
      el.classList.toggle('bad', raw < 0);
    }
  });
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

/* ---------- derived values -------------------------------------------- */

function deriveHeadline() {
  const t = state.summary?.typical_month;
  if (!t?.available) {
    return { state: 'unknown', number: '—', label: 'Not enough data yet',
             sub: t?.reason || 'Import bank exports to see the picture.' };
  }
  const net = t.net_median;
  if (net < 0) {
    return { state: 'behind', number: money(Math.abs(net)), label: 'short each month',
             sub: `That is ${money(Math.abs(net) * 12)} over a year.` };
  }
  return { state: 'ahead', number: money(net), label: 'left over each month',
           sub: `That is ${money(net * 12)} over a year, if it gets allocated on purpose.` };
}

/* ---------- list renderers -------------------------------------------- */

const GROUP_LABELS = {
  essential:     'Essentials — food, power, fuel, health',
  commitment:    'Commitments — mortgage, insurance, KiwiSaver',
  sinking:       'Lumpy bills — rates, rego, Christmas',
  discretionary: 'Choices — takeaways, subscriptions, shopping',
  unknown:       'Not yet categorised',
};

function groupRows() {
  const t = state.summary?.typical_month;
  if (!t?.available) return [];
  const entries = Object.entries(t.by_group).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0) || 1;
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([key, amount]) => ({
      key, amount, share: +(100 * amount / total).toFixed(1),
      label: GROUP_LABELS[key] || key,
    }));
}

const RENDER = {

  groupbar: el => {
    el.innerHTML = groupRows()
      .map(g => `<span class="seg-${esc(g.key)}" style="width:${g.share}%" title="${esc(g.label)}"></span>`)
      .join('');
  },

  grouplegend: el => {
    el.innerHTML = groupRows().map(g => `
      <div class="row">
        <span class="dot seg-${esc(g.key)}"></span>
        <span>${esc(g.label)}</span>
        <span class="amt">${money(g.amount)}</span>
        <span class="shr">${g.share}%</span>
      </div>`).join('');
  },

  monthchart: el => {
    const rows = (state.summary?.monthly || []).slice(-14);
    if (!rows.length) { el.innerHTML = '<p class="muted">No data yet.</p>'; return; }
    const max = Math.max(...rows.map(r => Math.max(r.income, r.spend))) || 1;
    // The final month is nearly always mid-flight; fade it so nobody reads a
    // half-finished month as a collapse in income.
    const lastIdx = rows.length - 1;
    el.innerHTML = rows.map((r, i) => `
      <div class="col ${i === lastIdx ? 'partial' : ''}" title="${esc(r.month)} — in ${money(r.income)}, out ${money(r.spend)}">
        <div class="stack">
          <div class="in"  style="height:${(r.income / max * 68).toFixed(1)}%"></div>
          <div class="out" style="height:${(r.spend  / max * 68).toFixed(1)}%"></div>
        </div>
        <div class="lab">${esc(r.month.slice(2))}</div>
      </div>`).join('');
  },

  categories: el => {
    const rows = state.summary?.by_category || [];
    if (!rows.length) { el.innerHTML = '<tr><td colspan="6" class="muted">No data yet.</td></tr>'; return; }
    el.innerHTML = rows.map(c => `
      <tr>
        <td>${esc(c.label)} ${c.flagged ? '<span class="tag warn">worth discussing</span>' : ''}</td>
        <td><span class="tag">${esc(c.group)}</span></td>
        <td class="n">${money(c.per_month)}</td>
        <td class="n">${money(c.per_year)}</td>
        <td class="n">${c.share_pct}%</td>
        <td class="n">${c.transactions}</td>
      </tr>`).join('');
  },

  findings: el => renderFindings(el, state.coach?.findings || []),

  topfindings: el => renderFindings(
    el, (state.coach?.findings || []).filter(f => f.severity !== 'low').slice(0, 3)),

  plan: el => {
    el.innerHTML = (state.coach?.plan || []).map(s => {
      const cls = s.status === 'done' ? 'done' : (s.status === 'in progress' ? 'now' : '');
      const meter = s.progress_pct
        ? `<div class="meter"><i style="width:${Math.min(100, s.progress_pct)}%"></i></div>
           <div class="d">${s.progress_pct}% there</div>` : '';
      return `<li class="${cls}">
          <div class="t">${esc(s.title)}</div>
          <div class="w">${esc(s.why)}</div>
          ${meter}
          <div class="d">Done when: ${esc(s.done_when)}</div>
        </li>`;
    }).join('');
  },

  recurring: el => {
    const rows = state.recurring?.items || [];
    if (!rows.length) { el.innerHTML = '<tr><td colspan="6" class="muted">Nothing detected yet.</td></tr>'; return; }
    el.innerHTML = rows.map(r => `
      <tr>
        <td>${esc(r.merchant)} ${r.possibly_cancelled ? '<span class="tag warn">may have stopped</span>' : ''}</td>
        <td><span class="tag">${esc(r.label)}</span></td>
        <td>${esc(r.cadence)}</td>
        <td class="n">${money(r.typical_amount, 2)}</td>
        <td class="n">${money(r.annual_cost)}</td>
        <td class="muted">${esc(r.last_seen)}</td>
      </tr>`).join('');
  },

  entbreakdown: el => {
    const e = state.entitlements?.estimate;
    if (!e?.available) {
      el.innerHTML = `<tr><td class="muted">${esc(e?.reason || 'Not enough information yet.')}</td></tr>`;
      return;
    }
    const rows = [
      ['Family Tax Credit',   e.family_tax_credit_annual],
      ['In-Work Tax Credit',  e.in_work_tax_credit_annual],
      ['Less abatement',      e.abatement_applied ? -e.abatement_applied : 0],
      ['Best Start',          e.best_start_estimate],
    ];
    el.innerHTML = rows.map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td class="n">${money(v)}</td></tr>`).join('') +
      `<tr><td><b>Estimated total</b></td><td class="n"><b>${money(e.total_estimate_annual)}</b></td></tr>`;
  },

  checklist: el => {
    const rows = state.entitlements?.checklist || [];
    el.innerHTML = rows.map(i => `
      <tr>
        <td><b>${esc(i.name)}</b></td>
        <td>${esc(i.applies_to)}</td>
        <td>${i.source ? `<a href="${esc(i.source)}" target="_blank" rel="noopener">Check →</a>` : ''}</td>
      </tr>`).join('');
  },

  snapshots: el => {
    const rows = state.snapshots || [];
    if (!rows.length) {
      el.innerHTML = '<tr><td colspan="6" class="muted">No snapshots yet. Take one after your first family meeting.</td></tr>';
      return;
    }
    el.innerHTML = rows.slice().reverse().map(s => `
      <tr>
        <td>${esc(s.taken_on)}</td>
        <td class="n">${money(s.metrics.net_median)}</td>
        <td class="n">${money(s.metrics.spend_median)}</td>
        <td class="n">${money(s.metrics.cash)}</td>
        <td class="n">${s.metrics.runway_weeks ?? '—'}</td>
        <td class="muted">${esc(s.note || '')}</td>
      </tr>`).join('');
  },

  loans: el => {
    const rows = state.loans || [];
    if (!rows.length) {
      el.innerHTML = '<p class="muted">No loan accounts found. If you have one, mark it as a loan on the Data tab.</p>';
      return;
    }
    el.innerHTML = rows.map(l => {
      const p = (l.projection && l.projection.available) ? l.projection.base : null;
      const best = (l.projection && l.projection.available)
        ? l.projection.scenarios.filter(s => s.extra_per_period === 50)[0] : null;

      const change = l.upcoming_change ? `
        <div class="note ${l.upcoming_change.to > l.upcoming_change.from ? 'warn' : 'info'}">
          <b>Repayment changes on ${esc(l.upcoming_change.due)}:</b>
          ${money(l.upcoming_change.from)} → ${money(l.upcoming_change.to)}
          ${l.upcoming_change.to > l.upcoming_change.from
            ? '— make sure the funding transfer covers it.'
            : '— the difference is only saved if something claims it.'}
        </div>` : '';

      const offset = l.is_offset && l.offset_benefit ? `
        <div class="note good">
          <b>Offset saved ${money(l.offset_benefit)}</b> over the last ${l.months} months —
          the balances in your other accounts reduced the interest charged here.
        </div>` : '';

      return `<div class="card" style="margin-bottom:14px">
        <h3>…${esc(String(l.account).slice(-2))} · ${money(l.balance)} owing</h3>
        <div class="grid g4" style="margin:12px 0">
          <div class="stat"><div class="v">${l.rate_pct ?? '—'}%</div><div class="k">interest rate${l.rate_varied ? ', varied' : ''}</div></div>
          <div class="stat"><div class="v">${money(l.repayment)}</div><div class="k">${esc(l.cadence || 'repayment')}</div></div>
          <div class="stat"><div class="v">${money(l.interest_gross)}</div><div class="k">interest, last ${l.months} months</div></div>
          <div class="stat"><div class="v">${p ? esc(String(p.payoff_date).slice(0, 4)) : '—'}</div><div class="k">clears at this rate</div></div>
        </div>
        ${change}${offset}
        ${best ? `<p class="muted">An extra ${money(best.extra_per_period)} per repayment would clear it
          ${best.years_saved.toFixed(1)} years sooner and save ${money(best.interest_saved)} —
          worth doing only once a cash buffer exists.</p>` : ''}
        <p class="muted" style="font-size:12.5px">
          Derived from ${l.interest_periods} interest charges (${esc(l.confidence)} confidence).
        </p>
      </div>`;
    }).join('');
  },

  accounts: el => {
    const rows = state.accounts || [];
    if (!rows.length) {
      el.innerHTML = '<tr><td colspan="5" class="muted">No accounts yet — import a bank export.</td></tr>';
      return;
    }
    const ROLES = [
      ['everyday', 'Everyday spending'],
      ['savings', 'Savings / sinking fund'],
      ['liability', 'Loan or mortgage'],
      ['ignore', 'Ignore this account'],
    ];
    el.innerHTML = rows.map((a, i) => {
      const opts = ROLES.map(([v, lbl]) =>
        `<option value="${v}"${a.role === v ? ' selected' : ''}>${esc(lbl)}</option>`).join('');
      const tag = a.confidence === 'confirmed'
        ? '<span class="tag good">confirmed</span>'
        : `<span class="tag">${esc(a.confidence || 'guess')}</span>`;
      return `<tr>
          <td>${esc(a.account)} ${tag}</td>
          <td class="n">${a.transactions ?? '—'}</td>
          <td class="n">${a.last_balance === null || a.last_balance === undefined ? '—' : money(a.last_balance)}</td>
          <td class="muted">${esc(a.evidence || '')}</td>
          <td><select data-role="${i}">${opts}</select></td>
        </tr>`;
    }).join('');

    $$('select[data-role]', el).forEach(sel => {
      sel.addEventListener('change', async ev => {
        // Everything read from the element happens BEFORE the first await.
        // After it, refresh() has rebuilt this table and any node captured
        // here is detached — the same trap as reading currentTarget late.
        const el = ev.currentTarget;
        const account = rows[Number(el.dataset.role)].account;
        const role = el.value;
        el.disabled = true;
        try {
          await api('/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ account, role }),
          });
        } finally {
          // Re-render either way: on success it shows the new role, on failure
          // it snaps back to what is actually stored rather than leaving an
          // unsaved choice on screen looking saved.
          await refresh();
        }
      });
    });
  },

  unknowns: el => {
    const rows = state.unknowns || [];
    if (!rows.length) {
      el.innerHTML = '<tr><td colspan="4" class="muted">Nothing uncategorised. Good.</td></tr>';
      return;
    }
    const options = RULES.map(r =>
      `<option value="${esc(r.key)}">${esc(r.label)}</option>`).join('');
    el.innerHTML = rows.map((u, i) => `
      <tr>
        <td>${esc(u.memo)}</td>
        <td class="n">${u.count}</td>
        <td class="n">${money(u.total)}</td>
        <td>
          <select data-fix="${i}">
            <option value="">Choose…</option>${options}
          </select>
        </td>
      </tr>`).join('');

    $$('select[data-fix]', el).forEach(sel => {
      sel.addEventListener('change', async () => {
        if (!sel.value) return;
        const item = rows[Number(sel.dataset.fix)];
        sel.disabled = true;
        await api('/categorise', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fingerprints: item.fingerprints, category: sel.value }),
        });
        await refresh();
      });
    });
  },
};

function renderFindings(el, findings) {
  if (!findings.length) { el.innerHTML = '<p class="muted">Nothing to report yet.</p>'; return; }
  el.innerHTML = findings.map(f => `
    <div class="finding ${esc(f.severity)}">
      ${f.amount ? `<div class="pill">${money(f.amount)} ${esc(f.unit)}</div>` : ''}
      <h3>${esc(f.title)}</h3>
      <p>${esc(f.body)}</p>
      ${f.action ? `<div class="do"><b>Do this:</b> ${esc(f.action)}</div>` : ''}
      ${f.evidence ? `<div class="ev">${esc(f.evidence)}</div>` : ''}
    </div>`).join('');
}

function renderLists() {
  $$('[data-list]').forEach(el => {
    const fn = RENDER[el.dataset.list];
    if (fn) { try { fn(el); } catch (e) { console.error(el.dataset.list, e); } }
  });
}

/* ---------- banners ---------------------------------------------------- */

function renderBanners() {
  const out = [];
  const setup = state.setup || {};
  const cov = setup.coverage || {};

  (setup.todo || []).forEach(t => {
    out.push(`<div class="note warn"><b>${esc(t.label)}</b> — ${esc(t.why)}</div>`);
  });

  if (!setup.transactions) {
    out.push(`<div class="note info">
      <b>No data yet.</b> Export 12 months of transactions from Kiwibank internet
      banking as CSV, then drop them on the <b>Data</b> tab. Everything else here
      is derived from that one file.</div>`);
  } else if (cov.trustworthy) {
    out.push(`<div class="note good"><b>${cov.categorised_pct}% of spending categorised</b>
      across ${cov.transaction_count} transactions — the numbers below are worth trusting.</div>`);
  }

  $('#banners').innerHTML = out.join('');

  const e = state.entitlements?.estimate;
  const banner = $('#ent-banner');
  if (!banner) return;

  if (e?.headline) {
    const tone = e.severity === 'high' ? 'bad' : (e.severity === 'medium' ? 'warn' : 'info');
    banner.innerHTML = `<div class="note ${tone}"><b>${esc(e.headline)}</b></div>` +
      (e.rates_verified ? '' :
        `<div class="note warn">The NZ rate constants in <code>config/nz_rates.yml</code>
         have not been verified for this tax year, so the estimate is a rough signal only.
         Verifying them takes about ten minutes and makes this page trustworthy.</div>`);
  } else {
    // The page must never be silently blank. Somebody who has not filled in
    // their household yet should be told what to add and what it buys them,
    // rather than being left looking at an empty tab wondering if it broke.
    const reason = e?.reason
      || 'Add your household details to see whether you are claiming everything you are entitled to.';
    const next = e?.how_to_add
      || 'Add each child (with a birth date) under <code>people:</code> in '
         + '<code>config/household.yml</code>, then press "Reload config" on the Data tab. '
         + 'Entitlements are usually the largest single number this tool can find, so it '
         + 'is worth the two minutes.';
    banner.innerHTML =
      `<div class="note info"><b>Nothing to estimate yet.</b> ${esc(reason)}</div>` +
      `<div class="note info">${next}</div>`;
  }
}

/* ---------- data ------------------------------------------------------- */

async function refresh() {
  const [setup, summary, coach, recurring, entitlements, mortgage,
         snapshots, unknowns, rules, accounts, loans] = await Promise.all([
    api('/setup'), api('/summary'), api('/coach'), api('/recurring'),
    api('/entitlements'), api('/mortgage'), api('/snapshots'),
    api('/uncategorised?limit=25'), api('/rules'), api('/accounts'), api('/loans'),
  ]);

  Object.assign(state, {
    setup, summary, coach, recurring, entitlements, mortgage, snapshots, unknowns,
    accounts, loans,
    household: { name: setup.household_name },
    headline: null,
  });
  state.headline = deriveHeadline();
  state.mortgageNote = mortgage.available
    ? `clears ${String(mortgage.base.payoff_date).slice(0, 4)} on current payments`
    : 'add rate + repayment to household.yml';
  // Net worth here counts money, not property. Saying so matters: a family
  // seeing a large negative number should know the house is not in it.
  const debtAccounts = (summary.debt && summary.debt.accounts) || [];
  state.netWorthNote = debtAccounts.length
    ? ` across ${debtAccounts.length} loan${debtAccounts.length > 1 ? 's' : ''} · excludes property value`
    : '';
  RULES = rules;

  applyBindings();
  renderLists();
  renderBanners();
}

/* ---------- interactions ----------------------------------------------- */

function showTab(name) {
  $$('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== name; });
  $$('nav.tabs button').forEach(b =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  window.scrollTo({ top: 0, behavior: 'smooth' });
  try { localStorage.setItem('wyrmhoard.tab', name); } catch { /* private mode */ }
}

async function uploadFiles(files) {
  const out = $('#import-result');
  out.innerHTML = '<div class="note info"><span class="spinner"></span> Importing…</div>';
  const blocks = [];
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch(`${API}/import`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await res.text());
      const { report } = await res.json();
      const tone = report.confidence === 'high' ? 'good'
                 : report.confidence === 'medium' ? 'info' : 'bad';
      blocks.push(`<div class="note ${tone}">
        <b>${esc(report.filename)}</b> — parsed ${report.rows_parsed} of ${report.rows_seen} rows,
        confidence <b>${esc(report.confidence)}</b>.
        Dates ${esc(report.date_range[0])} → ${esc(report.date_range[1])}.
        ${report.warnings.map(w => `<br>⚠ ${esc(w)}`).join('')}
      </div>`);
    } catch (err) {
      blocks.push(`<div class="note bad"><b>${esc(file.name)}</b> — ${esc(err.message)}</div>`);
    }
  }
  out.innerHTML = blocks.join('');
  await refresh();
}

function wire() {
  $$('nav.tabs button').forEach(b =>
    b.addEventListener('click', () => showTab(b.dataset.tab)));
  $$('[data-goto]').forEach(b =>
    b.addEventListener('click', () => showTab(b.dataset.goto)));

  const drop = $('#drop'), input = $('#file');
  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', () => input.files.length && uploadFiles(input.files));
  ['dragenter', 'dragover'].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => {
    const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.csv'));
    if (files.length) uploadFiles(files);
  });

  $('#btn-report').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    $('#report-status').innerHTML = '<span class="spinner"></span> Building…';
    try {
      const r = await api('/report', { method: 'POST' });
      $('#report-status').textContent = `Written to reports/${r.filename}`;
      $('#link-report').hidden = false;
    } catch (err) {
      $('#report-status').textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  // NOTE: capture the button BEFORE the first await. `event.currentTarget` is
  // nulled once the event finishes dispatching, so reading it after an await
  // throws — which silently skipped the refresh() that follows and left the
  // page showing stale data. Caught by the browser tests.
  $('#btn-snapshot').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await api('/snapshots', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: $('#snap-note').value || null }),
      });
      $('#snap-note').value = '';
      await refresh();
    } finally {
      btn.disabled = false;
    }
  });

  $('#btn-reload').addEventListener('click', async e => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await api('/reload', { method: 'POST' });
      await refresh();
    } finally {
      btn.disabled = false;
    }
  });
}

/* ---------- boot ------------------------------------------------------- */

(async function boot() {
  wire();
  try {
    await refresh();
    let tab = 'overview';
    try { tab = localStorage.getItem('wyrmhoard.tab') || 'overview'; } catch { /* ignore */ }
    if ($(`[data-panel="${tab}"]`)) showTab(tab);
  } catch (err) {
    $('#banners').innerHTML =
      `<div class="note bad"><b>Cannot reach the API.</b> ${esc(err.message)}.
       Check the container is running: <code>docker compose ps</code></div>`;
  }
})();
