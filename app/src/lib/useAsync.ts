import { useCallback, useRef, useState } from "react";
import type { FourState } from "../components/primitives";

// 4-State 규약([[vinc.d.index#§3]])을 그대로 표현하는 훅.
// idle → loading → (success | error). 화면은 state 로 분기만 하면 된다.

export type AsyncResult<T> = {
  state: FourState;
  data: T | null;
  error: string | null;
  run: (...args: unknown[]) => Promise<void>;
  reset: () => void;
};

export function useAsync<T>(
  fn: (...args: any[]) => Promise<T>,
  opts: { initial?: FourState } = {},
): AsyncResult<T> {
  const [state, setState] = useState<FourState>(opts.initial ?? "idle");
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 최신 호출만 반영(경쟁 조건 가드).
  const seq = useRef(0);

  const run = useCallback(
    async (...args: unknown[]) => {
      const myseq = ++seq.current;
      setState("loading");
      setError(null);
      try {
        const result = await fn(...args);
        if (myseq !== seq.current) return;
        setData(result);
        setState("success");
      } catch (e) {
        if (myseq !== seq.current) return;
        setError(e instanceof Error ? e.message : String(e));
        setState("error");
      }
    },
    [fn],
  );

  const reset = useCallback(() => {
    seq.current++;
    setState("idle");
    setData(null);
    setError(null);
  }, []);

  return { state, data, error, run, reset };
}
