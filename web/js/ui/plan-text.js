// Short labels for StepPlan checks and CheckResult values, shared by S4, S7–S9 and the drawer.

// Plain object names for the learner (NFR-65): the plan's own names, never prompt or code identifiers.
const short = (name) => name || '?';
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);

export function checkLabel(c) {
  const A = short(c.a), B = short(c.b);
  switch (c.type) {
    case 'present': return cap(`${A} in view`);
    case 'count': return cap(`${c.n} × ${A}`);
    case 'inside': return cap(`${A} in ${B}`);
    case 'adjacent': return cap(`${A} ${c.side} of ${B}`);
    case 'left_of': return cap(`${A} left of ${B}`);
    case 'above': return cap(`${A} above ${B}`);
    case 'area_ratio': return cap(`${A} size vs ${B}`);
    default: return c.text;
  }
}

export function formatValue(c, r) {
  if (!r) return '';
  if (r.kind === 'judged') return r.pass ? 'match' : 'no match';
  const v = r.value;
  if (typeof v !== 'number') return '';
  if (c.type === 'inside') return `${Math.round(v * 100)}%`;
  if (c.type === 'count') return String(v);
  if (c.type === 'left_of' || c.type === 'above') return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
  return v.toFixed(2);
}

/** Object names that first appear in step n (1-based). */
export function newObjects(steps, n) {
  const seen = new Set(steps.slice(0, n - 1).flatMap((s) => s.objects.map((o) => o.name)));
  return new Set(steps[n - 1].objects.map((o) => o.name).filter((name) => !seen.has(name)));
}
