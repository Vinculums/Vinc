/** Display evidence metadata separately from searchable content. */
export function temporalEntries(row: any): [string, string][] {
  const t = row.temporal || row;
  const entries: [string, string][] = [
    ...["created_at", "updated_at", "valid_from", "valid_until", "superseded_by"].map(
      (key): [string, string] => [key, t[key] ? String(t[key]) : "unknown"]),
    ["validity", String(t.validity || "unknown")],
  ];
  if (t.claim_status && t.claim_status !== "unscoped") entries.push(["claim_status", t.claim_status]);
  if (t.conflicts?.length) entries.push(["conflicts", t.conflicts.join(", ")]);
  if (t.supersession?.length) entries.push(["supersession", JSON.stringify(t.supersession)]);
  return entries;
}
