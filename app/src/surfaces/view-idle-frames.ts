type IdleFrameHooks = {
  now: () => number;
  defer: (fn: () => void) => void;
  deferResume: (fn: () => void) => void;
  deferSettle: (fn: () => void) => void;
  pause: () => void;
  resume: () => void;
  continuous: () => boolean;
  hasUnpaintedChanges: () => boolean;
};

/** 유휴 시한이 지났어도 아직 화면에 반영하지 않은 변경은 한 번 더 그린다. */
export class ViewIdleFrames {
  private awakeUntil: number;
  private paused = false;
  private resumePending = false;
  private disposed = false;
  private revision = 0;
  private settledRevision = 0;
  private paintedRevision = 0;

  constructor(private readonly hooks: IdleFrameHooks) {
    this.awakeUntil = hooks.now() + 2500;
  }

  wake(ms = 300): void {
    if (this.disposed) return;
    this.awakeUntil = Math.max(this.awakeUntil, this.hooks.now() + ms);
    const revision = ++this.revision;
    // SDK 세터의 비동기 digest 뒤에 그린 프레임이 있어야 잠든다.
    this.hooks.deferSettle(() => {
      if (!this.disposed && revision === this.revision) this.settledRevision = revision;
    });
    if (!this.paused) return;
    this.paused = false;
    this.resumePending = true;
    // 호출측의 카메라 명령을 먼저 시작한다. resume은 첫 렌더를 동기로 실행한다.
    this.hooks.deferResume(() => {
      this.resumePending = false;
      if (!this.disposed) this.hooks.resume();
    });
  }

  check(): void {
    if (this.disposed || this.paused || this.resumePending || this.hooks.continuous()
        || this.paintedRevision !== this.revision || this.hooks.hasUnpaintedChanges()
        || this.hooks.now() <= this.awakeUntil) return;
    this.paused = true;
    this.hooks.pause();
  }

  afterRender(): void {
    this.paintedRevision = this.settledRevision;
    if (this.disposed || this.paused || this.hooks.continuous()
        || this.hooks.now() <= this.awakeUntil) return;
    // 라이브러리가 후처리와 다음 rAF 등록을 마친 뒤 그 요청을 취소한다.
    this.hooks.defer(() => this.check());
  }

  dispose(): void { this.disposed = true; }
}
