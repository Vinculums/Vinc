/** 조사를 낱말의 끝소리에 맞춰 고른다(VIN-259). 두 꼴을 괄호로 함께 적으면 기계가 쓴
 *  말로 읽힌다([[vinc.spec.ui-copy-ko]] §5 가 빚으로 적어 둔 그 꼴이다). 끝 글자가 한글이면
 *  받침으로 정확히 고르고, 숫자는 읽는 소리로, 영문은 끝 글자로 어림한다.
 *  ponytail: 영문은 끝 글자 어림이라 chat·map·book 처럼 받침으로 읽히는 파열음 끝은 틀린다.
 *  틀리는 일이 잦아지면 낱말 끝 발음표를 둔다. */
type Josa = "은" | "이" | "을" | "과" | "으로";
const JOSA: Record<Exclude<Josa, "으로">, [string, string]> = {
  은: ["은", "는"], 이: ["이", "가"], 을: ["을", "를"], 과: ["과", "와"],
};
function coda(w: string): "none" | "rieul" | "other" {
  const t = w.replace(/[\s'"’”)\]}>.,·]+$/u, "");
  const c = t.slice(-1), code = c.charCodeAt(0);
  if (code >= 0xac00 && code <= 0xd7a3) {
    const jong = (code - 0xac00) % 28;
    return jong === 0 ? "none" : jong === 8 ? "rieul" : "other";
  }
  if (/\d/.test(c)) return "178".includes(c) ? "rieul" : "036".includes(c) ? "other" : "none";
  const word = (t.match(/[A-Za-z]+$/) || [""])[0];
  if (word.length > 1 && word === word.toUpperCase()) {   // 약어는 글자 이름으로 읽는다(MCP 엠씨피)
    const L = word.slice(-1);
    return L === "L" || L === "R" ? "rieul" : L === "M" || L === "N" ? "other" : "none";
  }
  const lw = word.toLowerCase();
  if (/(m|n|ng)$/.test(lw)) return "other";
  if (/le?$/.test(lw)) return "rieul";
  return "none";
}
/** `w` 뒤에 붙일 조사. `j("토큰", "을")` → "을", `j("Dark mode", "을")` → "를". */
export function j(w: string, p: Josa): string {
  const c = coda(w);
  if (p === "으로") return c === "other" ? "으로" : "로";
  return JOSA[p][c === "none" ? 1 : 0];
}
