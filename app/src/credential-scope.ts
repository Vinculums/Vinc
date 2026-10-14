/** A team credential belongs to one confirmed account and one explicit graph. */
export type CredentialIdentity = { subject: string; credential_team: string | null;
  key_scope: string; teams: Array<{ id: string; status: string }> };
export type CredentialStorage = {
  personal(): Promise<string>;
  team(subject: string, team: string): Promise<string>;
  save(subject: string, team: string, key: string): Promise<void>;
  inspect(key: string): Promise<CredentialIdentity>;
};
export class ScopedCredentials {
  private generation = 0;
  private key = "";
  private subject = "";
  private teams = new Map<string, string>();
  constructor(private storage: CredentialStorage) {}
  forget(): void { this.generation++; this.key = ""; this.subject = ""; this.teams.clear(); }
  private current(generation: number): void {
    if (generation !== this.generation) throw new Error("credential_changed");
  }
  async personal(): Promise<string> {
    const generation = this.generation;
    if (!this.key) {
      const key = await this.storage.personal();
      this.current(generation); this.key = key;
    }
    return this.key;
  }
  private async owner(): Promise<string> {
    const generation = this.generation;
    if (!this.subject) {
      const key = await this.personal();
      if (!key) throw new Error("personal_key_required");
      const proof = await this.storage.inspect(key);
      this.current(generation);
      if (!/^user:[0-9a-f-]{36}$/i.test(proof?.subject || "")
          || proof.credential_team !== null || proof.key_scope !== "personal")
        throw new Error("credential_identity_unavailable");
      this.subject = proof.subject;
    }
    return this.subject;
  }
  async forTeam(team: string): Promise<string> {
    const generation = this.generation;
    const subject = await this.owner();
    this.current(generation);
    if (this.teams.has(team)) return this.teams.get(team)!;
    const key = await this.storage.team(subject, team);
    this.current(generation);
    if (!key) return "";
    await this.check(subject, team, key);
    this.current(generation); this.teams.set(team, key);
    return key;
  }
  private async check(subject: string, team: string, key: string): Promise<void> {
    const proof = await this.storage.inspect(key);
    if (proof?.subject !== subject || proof.credential_team !== team || proof.key_scope !== team
        || !Array.isArray(proof.teams) || !proof.teams.some(t => t.id === team && t.status === "active"))
      throw new Error("key_graph_scope_mismatch");
  }
  async bind(team: string, key: string): Promise<void> {
    const generation = this.generation;
    if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(team) || !key.trim())
      throw new Error("key_graph_scope_mismatch");
    const subject = await this.owner();
    await this.check(subject, team, key.trim());
    this.current(generation);
    await this.storage.save(subject, team, key.trim());
    this.current(generation); this.teams.set(team, key.trim());
  }
}
