import { ICONS, type IconName } from "../lib/icons";

type IconProps = {
  name: IconName;
  size?: number;
  /** 접근성 라벨. 장식용이면 생략(aria-hidden). */
  label?: string;
  className?: string;
};

/**
 * Notation 아이콘 — 원본 SVG 를 인라인 주입해 `currentColor` 가 상속되게 한다.
 * 색은 소비 측 CSS `color`(잉크 토큰)로 지정 — 아이콘 자체는 색을 갖지 않는다(모노크롬).
 */
export function Icon({ name, size = 20, label, className }: IconProps) {
  const raw = ICONS[name];
  if (!raw) {
    if (import.meta.env.DEV) console.warn(`[Icon] 미등록 아이콘: ${name}`);
    return null;
  }
  // 루트 <svg> 의 width/height 를 요청 크기로 치환(원본은 24 고정). viewBox 유지.
  const sized = raw
    .replace(/\swidth="[^"]*"/, ` width="${size}"`)
    .replace(/\sheight="[^"]*"/, ` height="${size}"`);
  return (
    <span
      className={className}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ display: "inline-flex", lineHeight: 0, color: "inherit" }}
      dangerouslySetInnerHTML={{ __html: sized }}
    />
  );
}
