/* Gridiron Ledger core: odds, probabilities, expected value, settlement and value verdicts.
 *
 * Pure functions, no DOM. The page inlines this file between its BEGIN/END ledger-core markers; Node loads it with
 * require() for the tests in web/core.test.js. Nothing here invents a sportsbook price: a price either comes from a
 * provider quote (priceKind "quote") or is a model fair price (priceKind "fair"), and only quotes can be actionable.
 */
(function (root) {
  'use strict';

  /* ---------- prices ---------- */
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  // American -> decimal; null for anything that is not a valid American price (|am| >= 100)
  function amToDec(am){ if (!isNum(am) || (am > -100 && am < 100)) return null; return am > 0 ? 1 + am/100 : 1 + 100/(-am); }
  function decToAm(dec){ if (!isNum(dec) || dec <= 1) return null; return dec >= 2 ? Math.round((dec-1)*100) : Math.round(-100/(dec-1)); }
  // break-even probability of a price (includes the book's margin)
  function impliedProb(am){ const d = amToDec(am); return d ? 1/d : null; }
  // fair American price for a probability
  function fairAm(p){ if (!isNum(p) || p <= 0 || p >= 1) return null; return decToAm(1/p); }
  /* Parse a price from text: "-110", "+130", "−125", "EVEN", "-130 Lucky Rebel", "-115 (Caesars)". Returns {am, book} or null.
     Percent strings ("24% (Kalshi)") are a prediction-market price, returned as {prob, book} and never as a sportsbook price. */
  function parsePrice(s){
    if (isNum(s)) return amToDec(s) ? { am: Math.round(s), book: null } : null;
    const t = String(s ?? '').trim().replace(/−/g, '-');
    if (!t) return null;
    if (/^even\b/i.test(t)) return { am: 100, book: t.slice(4).replace(/[()]/g,'').trim() || null };
    let m = t.match(/^([+-]\d{3,5})\b\s*(.*)$/);
    if (m && amToDec(+m[1])) return { am: +m[1], book: m[2].replace(/[()]/g,'').trim() || null };
    m = t.match(/^(\d+(?:\.\d+)?)\s*%\s*(.*)$/);
    if (m){ const p = +m[1]/100; return p > 0 && p < 1 ? { prob: p, book: m[2].replace(/[()]/g,'').trim() || null } : null; }
    return null;
  }

  /* ---------- removing the margin ---------- */
  // Two-way market: proportional (multiplicative) normalization of the two implied probabilities.
  function devig2(amA, amB){
    const a = impliedProb(amA), b = impliedProb(amB);
    if (a == null || b == null) return null;
    const over = a + b;
    if (over <= 0) return null;
    return { pA: a/over, pB: b/over, overround: over, margin: over - 1 };
  }

  /* ---------- expected value ---------- */
  // Expected profit per 1 unit staked. pPush returns the stake (zero profit). Probabilities must sum to <= 1.
  function ev(pWin, dec, pPush = 0){
    if (!isNum(pWin) || !isNum(dec) || dec <= 1) return null;
    const pLose = 1 - pWin - (pPush || 0);
    if (pLose < -1e-9) return null;
    return pWin*(dec - 1) - Math.max(0, pLose);
  }
  // win probability needed to break even, among bets that do not push
  const breakEven = dec => (isNum(dec) && dec > 1 ? 1/dec : null);

  /* ---------- uncertainty ---------- */
  function wilson(w, n, z = 1.96){
    if (!n) return [null, null];
    const p = w/n, den = 1 + z*z/n, c = (p + z*z/(2*n))/den, h = z*Math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den;
    return [Math.max(0, c - h), Math.min(1, c + h)];
  }
  // log-gamma (Lanczos) and the regularized incomplete beta function I_x(a, b) (continued fraction, Numerical Recipes)
  function lgamma(x){
    const c = [76.18009172947146,-86.50532032941677,24.01409824083091,-1.231739572450155,0.1208650973866179e-2,-0.5395239384953e-5];
    let y = x, t = x + 5.5; t -= (x + 0.5)*Math.log(t);
    let s = 1.000000000190015; for (const k of c) s += k/++y;
    return -t + Math.log(2.5066282746310005*s/x);
  }
  function betacf(x, a, b){
    const MAXIT = 300, EPS = 3e-14, FPMIN = 1e-300;
    let qab = a + b, qap = a + 1, qam = a - 1, c = 1, d = 1 - qab*x/qap;
    if (Math.abs(d) < FPMIN) d = FPMIN; d = 1/d; let h = d;
    for (let m = 1; m <= MAXIT; m++){
      const m2 = 2*m; let aa = m*(b - m)*x/((qam + m2)*(a + m2));
      d = 1 + aa*d; if (Math.abs(d) < FPMIN) d = FPMIN; c = 1 + aa/c; if (Math.abs(c) < FPMIN) c = FPMIN; d = 1/d; h *= d*c;
      aa = -(a + m)*(qab + m)*x/((a + m2)*(qap + m2));
      d = 1 + aa*d; if (Math.abs(d) < FPMIN) d = FPMIN; c = 1 + aa/c; if (Math.abs(c) < FPMIN) c = FPMIN; d = 1/d;
      const del = d*c; h *= del; if (Math.abs(del - 1) < EPS) break;
    }
    return h;
  }
  function betaCdf(x, a, b){
    if (x <= 0) return 0; if (x >= 1) return 1;
    const bt = Math.exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a*Math.log(x) + b*Math.log(1 - x));
    return x < (a + 1)/(a + b + 2) ? bt*betacf(x, a, b)/a : 1 - bt*betacf(1 - x, b, a)/b;
  }

  /* Skeptical update of a market probability with a measured record for the same kind of pick.
     Prior: Beta centred on the market's no-vig probability with weight `k` games (default 100), i.e. we assume no edge
     until the record says otherwise. Returns the posterior mean and the chance the true rate beats `be`. */
  function posterior(pMkt, w, l, be, k = 100){
    const a = k*pMkt + (w||0), b = k*(1 - pMkt) + (l||0);
    return { mean: a/(a + b), pBeat: be == null ? null : 1 - betaCdf(be, a, b), n: (w||0) + (l||0), a, b };
  }

  /* ---------- value verdicts ----------
     input: { pMkt (no-vig market probability among bets that do not push), dec (price), priceKind ('quote' | 'fair' | 'none'),
              fresh (bool), evidence: {w, l, label} | null, needsNews (bool), pPush (chance of landing on the number) }
     verdict: 'value'      positive expected value from an actual, fresh quote AND a measured record behind it
                           (posterior chance of beating break-even >= minProb, at least minN graded picks)
              'possible'   positive estimate but the evidence is too thin to call it value
              'none'       no edge: expected value is the book's margin or worse
              'unavailable' no actual quote, stale quote, or no probability to judge it with
     The labels for chance, value, evidence and long shots are kept separate on purpose. */
  const RULES = { minProb: 0.80, minN: 50, maxQuoteAgeMin: 360, k: 100, highChance: 0.70, longShotAm: 300 };
  function evaluate(x, rules = RULES){
    const out = { verdict: 'unavailable', ev: null, p: null, be: null, pBeat: null, n: 0, reasons: [], labels: [] };
    const dec = x.dec, be = breakEven(dec);
    out.be = be;
    if (x.priceKind !== 'quote' || !dec){ out.reasons.push(x.priceKind === 'fair' ? 'No posted price: the number shown is a model fair price, not an offer.' : 'No posted price.'); }
    else if (!x.fresh){ out.reasons.push('The price is stale or the game has started, so it is not actionable.'); }
    if (!isNum(x.pMkt)){ out.reasons.push('No two-sided market price to remove the margin from.'); return out; }
    let p = x.pMkt;
    if (x.evidence && (x.evidence.w + x.evidence.l) > 0){
      const post = posterior(x.pMkt, x.evidence.w, x.evidence.l, be, rules.k);
      p = post.mean; out.pBeat = post.pBeat; out.n = post.n;
    }
    out.p = p;
    if (p >= rules.highChance) out.labels.push('high-chance');
    if (x.am != null && x.am >= rules.longShotAm) out.labels.push('long-shot');
    if (x.priceKind !== 'quote' || !dec || !x.fresh) return out;
    const pPush = isNum(x.pPush) && x.pPush > 0 && x.pPush < 1 ? x.pPush : 0;
    out.pPush = pPush;
    out.ev = ev(p*(1 - pPush), dec, pPush);   // p is the chance among bets that do not push; a push returns the stake
    if (x.needsNews) out.reasons.push('Depends on unconfirmed player news.');
    if (out.ev > 0 && out.pBeat != null && out.pBeat >= rules.minProb && out.n >= rules.minN && !x.needsNews){ out.verdict = 'value'; out.labels.push('value'); }
    else if (out.ev > 0){ out.verdict = 'possible'; out.reasons.push(out.n ? `Only ${out.n} graded picks behind this kind of bet (${Math.round((out.pBeat||0)*100)}% chance it beats break-even).` : 'No measured record behind it.'); }
    else { out.verdict = 'none'; out.reasons.push('No measured edge: the expected value is the book’s margin.'); }
    return out;
  }

  /* ---------- quotes ---------- */
  // Best quote across books for one selection. With `line`, only quotes at exactly that line count.
  function bestQuote(books, market, sel, line){
    let best = null;
    for (const [book, q] of Object.entries(books || {})){
      const v = q?.[market]?.[sel]; if (v == null) continue;
      const ln = market === 'ml' ? null : v[0], am = market === 'ml' ? v : v[1];
      if (!isNum(am)) continue;
      if (line != null && market !== 'ml' && (!isNum(ln) || Math.abs(ln - line) > 1e-9)) continue;
      const dec = amToDec(am); if (!dec) continue;
      if (!best || dec > best.dec) best = { book, line: ln, am, dec, ts: q.ts || null, src: q.src || null };
    }
    return best;
  }
  // No-vig probability of `sel` at `line`: median over books that quote both sides at the same line.
  function marketProb(books, market, sel, line){
    const other = { A:'H', H:'A', O:'U', U:'O' }[sel];
    const ps = [];
    for (const q of Object.values(books || {})){
      const a = q?.[market]?.[sel], b = q?.[market]?.[other]; if (a == null || b == null) continue;
      if (market !== 'ml'){
        if (!isNum(a[0]) || !isNum(b[0])) continue;
        if (line != null && Math.abs(a[0] - line) > 1e-9) continue;
        if (market === 'spread' && Math.abs(a[0] + b[0]) > 1e-9) continue;
        if (market === 'total' && Math.abs(a[0] - b[0]) > 1e-9) continue;
      }
      const d = devig2(market === 'ml' ? a : a[1], market === 'ml' ? b : b[1]); if (d) ps.push(d.pA);
    }
    if (!ps.length) return null;
    ps.sort((x, y) => x - y);
    const m = ps.length >> 1;
    return ps.length % 2 ? ps[m] : (ps[m-1] + ps[m])/2;
  }
  // A quote is actionable only before kickoff and when the feed confirmed it recently.
  function fresh(checkedAt, now, ko, maxAgeMin = RULES.maxQuoteAgeMin){
    const c = Date.parse(checkedAt || ''), k = Date.parse(ko || '');
    if (!Number.isFinite(c) || !Number.isFinite(now)) return { ok:false, ageMin:null, why:'no time on the quote' };
    const age = (now - c)/60000;
    if (Number.isFinite(k) && now >= k) return { ok:false, ageMin:age, why:'game has started' };
    if (age > maxAgeMin) return { ok:false, ageMin:age, why:`last confirmed ${Math.round(age/60)} h ago` };
    return { ok:true, ageMin:age, why:'' };
  }

  /* ---------- settlement ---------- */
  // results: 'W' win, 'L' loss, 'P' push, 'V' void (cancelled game, player did not play), null pending
  function settleStraight(stake, dec, res){
    if (res === 'W') return { state:'W', ret: stake*dec, profit: stake*(dec - 1) };
    if (res === 'L') return { state:'L', ret: 0, profit: -stake };
    if (res === 'P' || res === 'V') return { state: res, ret: stake, profit: 0 };
    return { state: null, ret: null, profit: null };
  }
  // Standard parlay: any loss loses; pushes and voids drop out (their odds become 1.00); all pushed/voided = refund.
  function settleParlay(stake, legs){
    if (legs.some(l => l.res === 'L')) return { state:'L', ret:0, profit:-stake, dec: null };
    if (legs.some(l => !l.res)) return { state:null, ret:null, profit:null, dec:null };
    const live = legs.filter(l => l.res === 'W');
    if (!live.length) return { state:'P', ret:stake, profit:0, dec:1 };
    const dec = live.reduce((s, l) => s*l.dec, 1);
    return { state: live.length < legs.length ? 'W*' : 'W', ret: stake*dec, profit: stake*(dec - 1), dec };
  }
  // PrizePicks Power Play: all picks must hit; a push or DNP drops that pick and the payout moves to the smaller entry size;
  // fewer than 2 picks left = refund. mults: {2: 3, 3: 6, ...} as entered by the user (payouts change; never assumed).
  function settlePowerPlay(stake, mults, results){
    if (results.some(r => r === 'L')) return { state:'L', ret:0, profit:-stake };
    if (results.some(r => !r)) return { state:null, ret:null, profit:null };
    const n = results.filter(r => r === 'W').length;
    if (n < 2) return { state:'P', ret:stake, profit:0 };
    const m = mults?.[n];
    if (!isNum(m)) return { state:'W', ret:null, profit:null, note:`No payout entered for ${n} picks` };
    return { state: n < results.length ? 'W*' : 'W', ret: stake*m, profit: stake*(m - 1) };
  }

  /* ---------- parlays ---------- */
  // Expected value of a parlay, only when it can honestly be computed.
  function parlayEval(legs){
    const ids = legs.map(l => l.gid), sameGame = new Set(ids).size < ids.length;
    if (sameGame) return { ev:null, p:null, dec:null, reason:'Same-game legs are correlated and the book prices them differently; the joint chance is not modeled here.' };
    if (legs.some(l => l.priceKind !== 'quote' || !isNum(l.dec))) return { ev:null, p:null, dec:null, reason:'Every leg needs a posted price to price the parlay.' };
    const dec = legs.reduce((s, l) => s*l.dec, 1);
    if (legs.some(l => !isNum(l.p))) return { ev:null, p:null, dec, reason:'At least one leg has no probability to judge it with.' };
    const p = legs.reduce((s, l) => s*l.p, 1);
    return { ev: p*dec - 1, p, dec, reason:'Assumes the games are independent of each other.' };
  }

  /* ---------- the number a game lands on (Phase 2) ----------
     A pmf slice from the robot: {n0, p:[...], lo, hi}: P(outcome = n) for whole numbers n0.. plus the mass below and
     above. The outcome is the home margin (spreads) or the total (totals), given the market's closing number. */
  function pmfSplit(pmf, x){
    if (!pmf || !Array.isArray(pmf.p) || !isNum(pmf.n0) || !isNum(x)) return null;
    let above = pmf.hi || 0, below = pmf.lo || 0, at = 0;
    pmf.p.forEach((q, i) => { const n = pmf.n0 + i; if (n > x + 1e-9) above += q; else if (n < x - 1e-9) below += q; else at += q; });
    const tot = above + below + at;
    return tot > 0 ? { above: above/tot, at: at/tot, below: below/tot } : null;
  }
  // Win / push / lose for one selection at its own line. Spread: sel 'H' or 'A' with that side's line (e.g. H -3.5);
  // the home side covers when margin > -line. Total: sel 'O' or 'U' at the total.
  function lineSplit(pmf, market, sel, line){
    if (!isNum(line)) return null;
    if (market === 'spread'){
      const homeLine = sel === 'H' ? line : -line, s = pmfSplit(pmf, -homeLine); if (!s) return null;
      return sel === 'H' ? { win: s.above, push: s.at, lose: s.below } : { win: s.below, push: s.at, lose: s.above };
    }
    if (market === 'total'){
      const s = pmfSplit(pmf, line); if (!s) return null;
      return sel === 'O' ? { win: s.above, push: s.at, lose: s.below } : { win: s.below, push: s.at, lose: s.above };
    }
    return null;
  }
  // Chance among non-pushes at `line`, anchored to the no-vig price at the market's own line (the pmf gives the shape,
  // the price gives the level). Used for alternate lines and to read a pick's chance from the closing market.
  function chanceAt(pmf, market, sel, line, anchorLine, anchorP){
    const at = lineSplit(pmf, market, sel, line); if (!at || at.win + at.lose <= 0) return null;
    let p = at.win/(at.win + at.lose);
    if (isNum(anchorLine) && isNum(anchorP)){
      const an = lineSplit(pmf, market, sel, anchorLine);
      if (an && an.win + an.lose > 0) p += anchorP - an.win/(an.win + an.lose);
    }
    return { p: Math.min(0.995, Math.max(0.005, p)), push: at.push };
  }

  /* ---------- model blend (Phases 3 and 4) ----------
     The gates come from the Ledger Model backtest. A failed gate leaves the market untouched. */
  const sig = z => 1/(1 + Math.exp(-Math.max(-35, Math.min(35, z))));
  const logit = p => { const q = Math.min(1 - 1e-9, Math.max(1e-9, p)); return Math.log(q/(1 - q)); };
  // edgePts: model minus market, in points, from the selected side (home margin edge for H, -edge for A; total edge for O)
  function blendCover(pMkt, edgePts, gate){
    if (!gate?.pass || !isNum(pMkt) || !isNum(edgePts) || !isNum(gate.b) || !isNum(gate.scale) || gate.scale <= 0) return pMkt;
    return sig(logit(pMkt) + (gate.a || 0) + gate.b*edgePts/gate.scale);
  }
  function blendMl(pMkt, pModel, gate){
    if (!gate?.pass || !isNum(pMkt) || !isNum(pModel) || !Array.isArray(gate.w)) return pMkt;
    return sig(gate.w[0] + gate.w[1]*logit(pMkt) + gate.w[2]*logit(pModel));
  }
  // model margin -> chance the home side wins outright (normal around the margin)
  function erf(x){ const t = 1/(1 + 0.3275911*Math.abs(x)); const y = 1 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t - 0.284496736)*t + 0.254829592)*t*Math.exp(-x*x); return x >= 0 ? y : -y; }
  const normCdf = z => 0.5*(1 + erf(z/Math.SQRT2));
  /* Calibration gate for model chances (props): rows [[p, y, base]], y = 1 win / 0 loss, base = the chance to beat
     (price-implied when there was a price, else 0.5). Passes with at least minN rows and a Brier-score gain with t >= minT. */
  function calibrationGate(rows, minN = 300, minT = 2){
    const xs = (rows || []).filter(r => isNum(r[0]) && (r[1] === 0 || r[1] === 1));
    const n = xs.length;
    if (!n) return { n:0, pass:false, gain:null, t:null, brier:null, base:null };
    const d = xs.map(([p, y, b]) => ((isNum(b) ? b : 0.5) - y)**2 - (p - y)**2);
    const mean = d.reduce((s, v) => s + v, 0)/n, sd = Math.sqrt(d.reduce((s, v) => s + (v - mean)**2, 0)/n);
    const t = sd > 0 ? mean/(sd/Math.sqrt(n)) : null;
    const brier = xs.reduce((s, [p, y]) => s + (p - y)**2, 0)/n, base = xs.reduce((s, [p, y, b]) => s + ((isNum(b) ? b : 0.5) - y)**2, 0)/n;
    return { n, gain: mean, t, brier, base, pass: n >= minN && mean > 0 && t != null && t >= minT };
  }

  /* ---------- bankroll (Phase 5) ---------- */
  /* Fractional Kelly with pushes, capped at one unit. p = chance among non-pushes. Returns a stake rounded down to `step`;
     0 with a reason when there is no edge or the stake would be under `min`. */
  function kellyStake({ p, dec, pPush = 0, bankroll, unit, frac = 0.25, min = 1, step = 0.5 }){
    if (!isNum(p) || !isNum(dec) || dec <= 1 || !isNum(bankroll) || bankroll <= 0) return { stake:0, f:0, reason:'No chance or price to size it with.' };
    const b = dec - 1, pw = p*(1 - (pPush || 0)), pl = (1 - p)*(1 - (pPush || 0));
    const f = (pw*b - pl)/b;
    if (!(f > 0)) return { stake:0, f, reason:'No edge, so no stake.' };
    let raw = frac*f*bankroll, capped = false;
    if (isNum(unit) && unit > 0 && raw > unit){ raw = unit; capped = true; }
    const stake = Math.floor(raw/step + 1e-9)*step;
    if (stake < min) return { stake:0, f, reason:`The edge supports less than $${min}.` };
    return { stake, f, capped, reason: capped ? 'Capped at one unit.' : `${Math.round(frac*100)}% of the Kelly stake.` };
  }
  // Bankroll path from settled profits in time order: current, peak and drawdown from the peak.
  function bankrollPath(start, profits){
    let cur = start, peak = start, dd = 0, ddPct = 0;
    for (const p of profits){ cur += p; peak = Math.max(peak, cur); if (peak - cur > dd){ dd = peak - cur; ddPct = peak > 0 ? dd/peak : 0; } }
    return { cur, peak, dd: peak - cur, ddPct: peak > 0 ? (peak - cur)/peak : 0, maxDd: dd, maxDdPct: ddPct };
  }
  // Wins, losses, pushes, amount staked and profit for a list of settled items {state, stake, profit}
  function summarize(items){
    const o = { n:0, W:0, L:0, P:0, staked:0, profit:0, open:0 };
    for (const it of items || []){
      const st = it.state ? String(it.state).replace('*', '') : null;
      if (st === 'W' || st === 'L' || st === 'P' || st === 'V'){ o.n++; o[st === 'V' ? 'P' : st]++; o.staked += it.stake || 0; o.profit += it.profit || 0; }
      else o.open++;
    }
    o.roi = o.staked ? o.profit/o.staked : null;
    return o;
  }

  /* ---------- records ---------- */
  function units(rec, am = -110){
    const d = amToDec(am); return rec.W*(d - 1) - rec.L;
  }
  // Maximum drawdown in units over a sequence of profits.
  function maxDrawdown(profits){
    let peak = 0, cum = 0, dd = 0;
    for (const p of profits){ cum += p; peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum); }
    return dd;
  }

  const api = { isNum, amToDec, decToAm, impliedProb, fairAm, parsePrice, devig2, ev, breakEven, wilson, betaCdf, posterior,
    RULES, evaluate, bestQuote, marketProb, fresh, settleStraight, settleParlay, settlePowerPlay, parlayEval, units, maxDrawdown, lgamma,
    pmfSplit, lineSplit, chanceAt, sig, logit, blendCover, blendMl, normCdf, calibrationGate, kellyStake, bankrollPath, summarize };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LedgerCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
