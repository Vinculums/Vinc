import type { ConversionCheck, ConversionRun, ConventionsPolicy } from "./api";

export type Checkpoint = { id: string; name: string; topic: string; fingerprint: string; conventions_policy?: ConventionsPolicy };
type ConversionClient = {
  preflight(topic: string, policy: ConventionsPolicy): Promise<ConversionCheck>;
  reserve(body: Checkpoint): Promise<ConversionRun>;
  status(id: string): Promise<ConversionRun>;
  resume(id: string): Promise<ConversionRun>;
  cancel(id: string): Promise<ConversionRun>;
};
/** A closed sheet does not cancel an uncertain write. Only a server-confirmed
 * cancellation clears the recovery checkpoint. No method retries a write. */
export class TopicConversion {
  topic = ""; name = ""; check: ConversionCheck | null = null;
  run: ConversionRun | null = null; checkpoint: Checkpoint | null;
  busy = false; error = "";
  conventionsPolicy: ConventionsPolicy = "retain";
  private revision = 0;
  constructor(private client: ConversionClient, private save: (value: Checkpoint | null) => void,
              checkpoint: Checkpoint | null = null, private uuid = () => crypto.randomUUID()) {
    this.checkpoint = checkpoint;
    if (checkpoint) { this.topic = checkpoint.topic; this.name = checkpoint.name;
      this.conventionsPolicy = checkpoint.conventions_policy ?? "retain"; }
  }
  edit(topic: string, name: string) {
    if (this.checkpoint) return;
    this.topic = topic; this.name = name; this.check = null; this.revision++;
  }
  chooseConventions(policy: ConventionsPolicy) {
    if (this.checkpoint || this.busy) return;
    this.conventionsPolicy = policy; this.check = null; this.revision++;
  }
  private async action(fn: () => Promise<void>) {
    if (this.busy) return;
    this.busy = true; this.error = "";
    try { await fn(); }
    catch (e: any) { this.error = String(e?.code || "conversion_response_uncertain"); }
    finally { this.busy = false; }
  }
  async preflight() {
    const revision = this.revision, topic = this.topic, policy = this.conventionsPolicy;
    await this.action(async () => {
      this.check = null;
      const check = await this.client.preflight(topic, policy);
      if ((policy === "move_original" || check.conventions !== undefined) && check.conventions?.policy !== policy)
        throw { code: "conversion_response_uncertain" };
      if (revision === this.revision) this.check = check;
    });
  }
  async reserve(consent: boolean) {
    if (!consent || !this.check?.ready || !this.check.fingerprint || !this.name.trim()) return;
    await this.action(async () => {
      this.checkpoint ??= { id: this.uuid(), name: this.name.trim(), topic: this.topic, fingerprint: this.check!.fingerprint! };
      if (this.conventionsPolicy === "move_original") this.checkpoint.conventions_policy = this.conventionsPolicy;
      this.save(this.checkpoint);
      this.run = await this.reservation();
    });
  }
  async status() {
    if (this.checkpoint) await this.action(async () => { this.run = await this.client.status(this.checkpoint!.id); });
  }
  async resume() {
    if (this.checkpoint) await this.action(async () => {
      // A lost reservation reply is resolved using the SAME id and scope.
      if (!this.run) this.run = await this.reservation();
      this.run = await this.client.resume(this.checkpoint!.id);
      if (this.run.state === "cancelled") this.clear();
    });
  }
  async cancel() {
    if (this.checkpoint) await this.action(async () => {
      this.run = await this.client.cancel(this.checkpoint!.id);
      if (this.run.state === "cancelled") this.clear();
    });
  }
  private clear() { this.checkpoint = null; this.check = null; this.save(null); }
  private async reservation() {
    try { return await this.client.reserve(this.checkpoint!); }
    catch (e: any) {
      // These replies occur before any reservation is attempted. An uncertain
      // transport or DB outcome must keep the same recovery identity.
      if (["preflight_changed", "invalid_conversion_request", "topic_conversion_unavailable", "team_space_limit"].includes(e?.code)) this.clear();
      throw e;
    }
  }
}
