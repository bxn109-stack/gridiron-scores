// Tests for web/ledger-core.js. Run: node --test web/
const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('./ledger-core.js');

const close = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test('American and decimal odds convert both ways', () => {
  close(C.amToDec(-110), 1 + 100/110);
  close(C.amToDec(150), 2.5);
  assert.equal(C.amToDec(50), null);
  assert.equal(C.amToDec(-99), null);
  assert.equal(C.amToDec(null), null);
  assert.equal(C.decToAm(2.5), 150);
  assert.equal(C.decToAm(1 + 100/110), -110);
  assert.equal(C.decToAm(1), null);
  assert.equal(C.decToAm(2), 100);
});

test('implied and fair prices', () => {
  close(C.impliedProb(-110), 110/210);
  close(C.impliedProb(+150), 0.4);
  assert.equal(C.fairAm(0.5), 100);
  assert.equal(C.fairAm(0.6), -150);
  assert.equal(C.fairAm(0.25), 300);
  assert.equal(C.fairAm(1), null);
});

test('price text parsing never turns a percentage into a sportsbook price', () => {
  assert.deepEqual(C.parsePrice('-110'), { am: -110, book: null });
  assert.deepEqual(C.parsePrice('+130'), { am: 130, book: null });
  assert.deepEqual(C.parsePrice('−125'), { am: -125, book: null });
  assert.deepEqual(C.parsePrice('-130 Lucky Rebel'), { am: -130, book: 'Lucky Rebel' });
  assert.deepEqual(C.parsePrice('-115 (Caesars)'), { am: -115, book: 'Caesars' });
  assert.deepEqual(C.parsePrice('EVEN'), { am: 100, book: null });
  assert.deepEqual(C.parsePrice('24% (Kalshi)'), { prob: 0.24, book: 'Kalshi' });
  for (const bad of ['', null, 'n/a', '1.91', '-50']) assert.equal(C.parsePrice(bad), null, String(bad));
});

test('removing the margin from a two-way market', () => {
  const d = C.devig2(-110, -110);
  close(d.pA, 0.5); close(d.pB, 0.5); close(d.margin, 2*110/210 - 1);
  const f = C.devig2(-200, +170);
  close(f.pA + f.pB, 1);
  assert.ok(f.pA > 0.64 && f.pA < 0.66);
  assert.equal(C.devig2(-110, null), null);
});

test('expected value handles pushes and break-even', () => {
  close(C.ev(0.5, C.amToDec(-110)), 0.5*(100/110) - 0.5);
  close(C.ev(0.524, C.amToDec(-110)), 0.524*(100/110) - 0.476);
  close(C.ev(0.45, 2, 0.1), 0.45 - 0.45);           // 45% win, 10% push, 45% loss at even money = 0
  assert.equal(C.ev(0.7, 2, 0.4), null);             // impossible probabilities
  close(C.breakEven(C.amToDec(-110)), 110/210);
});

test('Wilson interval and the incomplete beta function', () => {
  const [lo, hi] = C.wilson(116, 225);
  assert.ok(lo > 0.44 && lo < 0.46 && hi > 0.57 && hi < 0.59);
  assert.deepEqual(C.wilson(0, 0), [null, null]);
  close(C.betaCdf(0.5, 2, 2), 0.5, 1e-9);
  close(C.betaCdf(0.3, 2, 5), 0.579825, 1e-6);
  close(C.betaCdf(0.9, 1, 1), 0.9, 1e-9);
  assert.equal(C.betaCdf(0, 3, 3), 0);
  assert.equal(C.betaCdf(1, 3, 3), 1);
});

test('skeptical posterior: small records barely move the market probability', () => {
  const p = C.posterior(0.5, 6, 3, 110/210);          // stars 6-3
  assert.ok(p.mean > 0.51 && p.mean < 0.52);
  assert.ok(p.pBeat < 0.5);
  const big = C.posterior(0.5, 600, 400, 110/210);    // a long, strong record
  assert.ok(big.mean > 0.58 && big.pBeat > 0.99);
});

const DEC = C.amToDec(-110);
test('verdicts: a model fair price or a stale quote is never actionable', () => {
  const fair = C.evaluate({ pMkt: 0.6, dec: 2, priceKind: 'fair', fresh: true });
  assert.equal(fair.verdict, 'unavailable'); assert.equal(fair.ev, null);
  const stale = C.evaluate({ pMkt: 0.6, dec: 2, priceKind: 'quote', fresh: false });
  assert.equal(stale.verdict, 'unavailable');
  const nop = C.evaluate({ pMkt: null, dec: DEC, priceKind: 'quote', fresh: true });
  assert.equal(nop.verdict, 'unavailable');
});

test('verdicts: the market alone is no bet; strong evidence is value; thin evidence is only possible', () => {
  const mkt = C.evaluate({ pMkt: 0.5, dec: DEC, priceKind: 'quote', fresh: true });
  assert.equal(mkt.verdict, 'none'); assert.ok(mkt.ev < 0);
  const strong = C.evaluate({ pMkt: 0.5, dec: DEC, priceKind: 'quote', fresh: true, evidence: { w: 600, l: 400 } });
  assert.equal(strong.verdict, 'value'); assert.ok(strong.ev > 0);
  const thin = C.evaluate({ pMkt: 0.5, dec: 2.2, priceKind: 'quote', fresh: true, evidence: { w: 6, l: 3 } });
  assert.equal(thin.verdict, 'possible');
  const news = C.evaluate({ pMkt: 0.5, dec: DEC, priceKind: 'quote', fresh: true, evidence: { w: 600, l: 400 }, needsNews: true });
  assert.equal(news.verdict, 'possible');
  // a good-looking price with no record behind it is still not value
  const plus = C.evaluate({ pMkt: 0.5, dec: 2.1, priceKind: 'quote', fresh: true });
  assert.equal(plus.verdict, 'possible');
});

test('verdict labels stay separate', () => {
  const r = C.evaluate({ pMkt: 0.8, dec: 1.2, am: -500, priceKind: 'quote', fresh: true });
  assert.ok(r.labels.includes('high-chance'));
  assert.ok(!r.labels.includes('value'));
  const ls = C.evaluate({ pMkt: 0.2, dec: 5, am: 400, priceKind: 'quote', fresh: true });
  assert.ok(ls.labels.includes('long-shot'));
});

const BOOKS = {
  DraftKings: { ts: '2026-11-04T12:00:00Z', src: 'espn', ml: { A: 150, H: -170 }, spread: { A: [3.5, -110], H: [-3.5, -110] }, total: { O: [44.5, -110], U: [44.5, -110] } },
  FanDuel:    { ts: '2026-11-04T13:00:00Z', src: 'oddsapi', ml: { A: 155, H: -180 }, spread: { A: [3, -120], H: [-3, 100] }, total: { O: [44.5, -105], U: [44.5, -115] } },
};
test('best quote respects the line and picks the best price', () => {
  assert.equal(C.bestQuote(BOOKS, 'ml', 'A').book, 'FanDuel');
  assert.equal(C.bestQuote(BOOKS, 'spread', 'H', -3.5).book, 'DraftKings');
  assert.equal(C.bestQuote(BOOKS, 'spread', 'H', -3).am, 100);
  assert.equal(C.bestQuote(BOOKS, 'spread', 'H', -2.5), null);
  assert.equal(C.bestQuote(BOOKS, 'total', 'O', 44.5).am, -105);
});

test('market probability uses only matching two-sided quotes', () => {
  close(C.marketProb(BOOKS, 'spread', 'H', -3.5), 0.5);
  const pU = C.marketProb(BOOKS, 'total', 'U', 44.5);
  assert.ok(pU > 0.5 && pU < 0.51);                    // median of 0.5 and FanDuel's 0.5116
  const pA = C.marketProb(BOOKS, 'ml', 'A');
  assert.ok(pA > 0.37 && pA < 0.39);
  assert.equal(C.marketProb(BOOKS, 'spread', 'H', -2.5), null);
  assert.equal(C.marketProb({ X: { spread: { A: [3.5, -110], H: [-3, -110] } } }, 'spread', 'A'), null);  // not mirror lines
});

test('quote freshness', () => {
  const now = Date.parse('2026-11-04T18:00:00Z');
  assert.equal(C.fresh('2026-11-04T17:00:00Z', now, '2026-11-08T18:00:00Z').ok, true);
  assert.equal(C.fresh('2026-11-04T08:00:00Z', now, '2026-11-08T18:00:00Z').ok, false);
  assert.equal(C.fresh('2026-11-04T17:59:00Z', now, '2026-11-04T17:30:00Z').ok, false);   // already kicked off
  assert.equal(C.fresh(null, now, '2026-11-08T18:00:00Z').ok, false);
});

test('straight bet settlement: win, loss, push and void', () => {
  close(C.settleStraight(5, 2.5, 'W').ret, 12.5);
  assert.equal(C.settleStraight(5, 2.5, 'L').profit, -5);
  assert.equal(C.settleStraight(5, 2.5, 'P').ret, 5);
  assert.equal(C.settleStraight(5, 2.5, 'V').profit, 0);
  assert.equal(C.settleStraight(5, 2.5, null).state, null);
});

test('parlay settlement: pushes and voids drop out of the payout', () => {
  const d = C.amToDec(-110);
  const all = C.settleParlay(10, [{ dec: d, res: 'W' }, { dec: d, res: 'W' }, { dec: d, res: 'W' }]);
  close(all.ret, 10*d**3);
  const pushed = C.settleParlay(10, [{ dec: d, res: 'W' }, { dec: d, res: 'P' }, { dec: d, res: 'W' }]);
  close(pushed.ret, 10*d**2); assert.equal(pushed.state, 'W*');
  const voided = C.settleParlay(10, [{ dec: d, res: 'V' }, { dec: 2.5, res: 'W' }]);
  close(voided.ret, 25);
  assert.equal(C.settleParlay(10, [{ dec: d, res: 'P' }, { dec: d, res: 'V' }]).ret, 10);
  assert.equal(C.settleParlay(10, [{ dec: d, res: 'L' }, { dec: d, res: null }]).state, 'L');   // a loss settles it early
  assert.equal(C.settleParlay(10, [{ dec: d, res: 'W' }, { dec: d, res: null }]).state, null);
});

test('PrizePicks power play settlement uses the entered payouts', () => {
  const m = { 2: 3, 3: 6, 4: 10 };
  assert.equal(C.settlePowerPlay(5, m, ['W', 'W', 'W']).ret, 30);
  assert.equal(C.settlePowerPlay(5, m, ['W', 'P', 'W']).ret, 15);   // drops to a 2-pick entry
  assert.equal(C.settlePowerPlay(5, m, ['W', 'V']).ret, 5);         // one pick left: refund
  assert.equal(C.settlePowerPlay(5, m, ['W', 'L', 'W']).state, 'L');
  assert.equal(C.settlePowerPlay(5, { 2: 3 }, ['W', 'W', 'W']).ret, null);
});

test('parlay expected value only when it can be computed honestly', () => {
  const d = C.amToDec(-110);
  const sg = C.parlayEval([{ gid: 'g1', priceKind: 'quote', dec: d, p: 0.5 }, { gid: 'g1', priceKind: 'quote', dec: d, p: 0.5 }]);
  assert.equal(sg.ev, null); assert.match(sg.reason, /Same-game/);
  const fair = C.parlayEval([{ gid: 'g1', priceKind: 'fair', dec: d, p: 0.5 }, { gid: 'g2', priceKind: 'quote', dec: d, p: 0.5 }]);
  assert.equal(fair.ev, null);
  const ok = C.parlayEval([{ gid: 'g1', priceKind: 'quote', dec: d, p: 0.5 }, { gid: 'g2', priceKind: 'quote', dec: d, p: 0.5 }]);
  close(ok.ev, 0.25*d*d - 1); assert.match(ok.reason, /independent/);
});

test('units and drawdown', () => {
  close(C.units({ W: 116, L: 109 }), 116*(100/110) - 109);
  assert.equal(C.maxDrawdown([1, -1, -1, -1, 2, -3]), 4);
  assert.equal(C.maxDrawdown([]), 0);
});
