/**
 * Independent negotiation oracle.
 *
 * This is a SECOND, from-scratch implementation of the negotiation rules,
 * deliberately written in a different style (regex/split based, flat numeric
 * scores instead of typed match objects). It does NOT import any production
 * code from src/. It is used two ways:
 *
 *   1. Hand-authored vectors: exact expected winner / failure code written by
 *      a human, never produced by either implementation.
 *   2. Differential fuzzing: hundreds of randomly generated legal headers and
 *      variant sets fed to BOTH this oracle and the production core; the two
 *      independent implementations must agree on winner id or failure code.
 *
 * If they disagree, one of the rule implementations (or the hand reading of
 * the spec) is wrong; the run fails loudly.
 */

// ---- Independent Accept parser ------------------------------------------------

function parseAcceptOracle(header) {
  if (header === null) return [{ t: '*', s: '*', spec: 0, q: 1, params: {} }];
  return header.split(',').map((el, index) => {
    const parts = el.split(';').map((p) => p.trim());
    const mime = parts[0];
    const slash = mime.indexOf('/');
    const type = mime.slice(0, slash).toLowerCase();
    const subtype = mime.slice(slash + 1).toLowerCase();
    const params = {};
    let q = 1;
    for (const p of parts.slice(1)) {
      const eq = p.indexOf('=');
      const name = p.slice(0, eq).trim().toLowerCase();
      let value = p.slice(eq + 1).trim();
      if (value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
      if (name === 'q') q = Number(value);
      else params[name] = value.toLowerCase();
    }
    const spec = type === '*' ? 0 : subtype === '*' ? 1 : 2;
    return { t: type, s: subtype, spec, q, params, index };
  });
}

function parseLanguageOracle(header) {
  if (header === null) return [{ tag: null, spec: 0, q: 1 }];
  return header.split(',').map((el, index) => {
    const [head, qpart] = el.split(';').map((p) => p.trim());
    const tag = head.toLowerCase();
    if (tag === '*') return { tag: null, spec: 0, q: qpart ? Number(qpart.split('=')[1]) : 1, index };
    return { tag, spec: tag.split('-').length, q: qpart ? Number(qpart.split('=')[1]) : 1, index };
  });
}

// ---- Independent scoring ------------------------------------------------------

function mediaQualityFor(rep, ranges) {
  let best = null;
  for (const r of ranges) {
    const typeOk = r.t === '*' || r.t === rep.mediaType.type;
    if (!typeOk) continue;
    const subOk = r.s === '*' || r.s === rep.mediaType.subtype;
    if (!subOk) continue;
    let paramsOk = true;
    for (const [k, v] of Object.entries(r.params)) {
      if (rep.mediaType.parameters.get(k) !== v) paramsOk = false;
    }
    if (!paramsOk) continue;
    // Most specific wins; header order (first) breaks equal specificity.
    if (best === null || r.spec > best.spec) best = { q: r.q, spec: r.spec };
  }
  return best; // {q, spec} or null
}

function langTier(rangeTag, rangeSubtags, servedTag) {
  if (rangeTag === null) return { tier: 0, sub: 0 };
  if (rangeTag === servedTag) return { tier: 3, sub: rangeSubtags };
  if (servedTag.startsWith(rangeTag + '-')) return { tier: 2, sub: rangeSubtags };
  if (rangeTag.startsWith(servedTag + '-')) return { tier: 1, sub: -rangeSubtags }; // truncation fallback
  return null;
}

function languageQualityFor(rep, ranges) {
  let best = null;
  for (const r of ranges) {
    const tier = langTier(r.tag, r.spec, rep.language);
    if (tier === null) continue;
    const rank = tier.tier * 1000 + tier.sub;
    if (best === null || rank > best.rank) best = { q: r.q, tier: tier.tier, rank };
  }
  return best; // {q, tier, rank} or null
}

/** Independent verdict: { winner: id } or { failure: code }. */
export function oracleDecide(resource, acceptHeader, langHeader) {
  const mr = parseAcceptOracle(acceptHeader);
  const lr = parseLanguageOracle(langHeader);
  const scored = resource.representations.map((rep, order) => {
    const m = mediaQualityFor(rep, mr);
    const l = languageQualityFor(rep, lr);
    const mq = m === null ? null : m.q;
    const lq = l === null ? null : l.q;
    let combined = null;
    if (mq !== null && lq !== null && mq > 0 && lq > 0) combined = mq * lq;
    else if (mq === 0 || lq === 0) combined = 0;
    return { id: rep.id, order, mq, lq, combined, mspec: m ? m.spec : -1, ltier: l ? l.tier : -1 };
  });

  if (scored.every((s) => s.mq === null)) return { failure: 'UNACCEPTABLE_MEDIA_TYPE' };
  if (scored.filter((s) => s.mq !== null).every((s) => s.mq === 0)) return { failure: 'UNACCEPTABLE_MEDIA_TYPE' };
  if (scored.every((s) => s.lq === null)) return { failure: 'UNACCEPTABLE_LANGUAGE' };
  if (scored.filter((s) => s.lq !== null).every((s) => s.lq === 0)) return { failure: 'UNACCEPTABLE_LANGUAGE' };
  const live = scored.filter((s) => s.combined !== null && s.combined > 0);
  if (live.length === 0) return { failure: 'NO_VARIANT_FOR_COMBINATION' };

  // Tie-break mirrors the documented contract exactly:
  // combined desc -> media specificity desc -> language tier desc ->
  // declaration order asc -> id asc.
  live.sort(
    (a, b) =>
      b.combined - a.combined ||
      b.mspec - a.mspec ||
      b.ltier - a.ltier ||
      a.order - b.order ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  return { winner: live[0].id };
}

export { parseAcceptOracle, parseLanguageOracle };
