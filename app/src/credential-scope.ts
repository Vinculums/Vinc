/** 팀 키는 확인된 계정 하나와 팀 그래프 하나에 묶인다.
 *  팀에 묶인 키가 없으면 개인 키로 팀을 연다. 개인 키가 멤버십으로 팀에 닿는 것이 채택된 동작이다.
 *  근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
 *  신원 답의 `personal_key_teams` 가 거짓이면(계정의 엄격 모드) 팀 키를 받아야 하고, 신원 필드가 없는
 *  계정은 키를 팀에 묶지 않는 계정으로 읽어 개인 키로 연다. */
export type CredentialIdentity = { subject: string; credential_team: string | null;
  key_scope: string; teams: Array<{ id: string; status: string }>; personal_key_teams?: boolean };
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
  /** 개인 키가 팀 그래프에 닿는가. 신원을 읽은 뒤에 정해진다. */
  private personalReachesTeams = true;
  /** 계정이 키를 팀에 묶지 않는다고 답했다. 다시 묻지 않는다(`forget` 이 지운다). */
  private unbound = false;
  private teams = new Map<string, string>();
  constructor(private storage: CredentialStorage) {}
  forget(): void { this.generation++; this.key = ""; this.subject = ""; this.personalReachesTeams = true; this.unbound = false; this.teams.clear(); }
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
    if (this.unbound) throw new Error("credential_binding_unsupported");
    if (!this.subject) {
      const key = await this.personal();
      if (!key) throw new Error("personal_key_required");
      const proof = await this.storage.inspect(key);
      this.current(generation);
      // 신원 필드를 하나도 싣지 않는 계정은 키를 팀에 묶지 않는다: 팀 키는 없고 개인 키가 멤버십으로 팀에 닿는다.
      if (proof && typeof proof === "object" && !("subject" in proof) && !("credential_team" in proof)) {
        this.unbound = true;
        throw new Error("credential_binding_unsupported");
      }
      if (!/^user:[0-9a-f-]{36}$/i.test(proof?.subject || "")
          || proof.credential_team !== null || proof.key_scope !== "personal")
        throw new Error("credential_identity_unavailable");
      this.subject = proof.subject;
      this.personalReachesTeams = proof.personal_key_teams !== false;
    }
    return this.subject;
  }
  /** 이 팀을 여는 키. 묶인 팀 키가 먼저고, 없으면 계정이 허락할 때 개인 키다. 둘 다 아니면 "" 다. */
  async forTeam(team: string): Promise<string> {
    const bound = await this.boundTo(team).catch(error => {
      if (error?.message === "credential_binding_unsupported") return "";
      throw error;
    });
    if (bound || !this.personalReachesTeams) return bound;
    return this.personal();
  }
  /** 이 팀에 묶여 이 기기에 저장된 팀 키. 없으면 "" 다. */
  async boundTo(team: string): Promise<string> {
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
