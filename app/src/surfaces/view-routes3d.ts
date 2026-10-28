import * as THREE from "three";
import { movedRoute, pointOnRoute, routeLength, type ViewRoute, type RoutePoint } from "../view-route";

type Paint = { color: string; width: number; arrow: boolean; particles: boolean;
  particleColor: string; visible: boolean; targetRadius: number; detail?: number;
  /** 파선 — 범례의 갈래가 고른다(방향 없는 관계·다리). 굵기 튜브에는 없다. */
  dashed?: boolean };
/** 파선 간격(월드 단위). 노드 간격(격자 한 칸 ≈ 46)보다 훨씬 짧아 한 구간 안에 여러 번 끊긴다. */
const DASH = 5, GAP = 3.5;
const UP = new THREE.Vector3(0, 1, 0);

/** A link remains one pickable ForceGraph object. Its geometry, arrow and
 * particles all use the same routed sections, including live endpoint drags. */
export class RoutedLinkObject extends THREE.Group {
  private line = new THREE.Line(new THREE.BufferGeometry(),
    new THREE.LineDashedMaterial({ transparent: true, opacity: .85, dashSize: DASH, gapSize: 0 }));
  private arrow = new THREE.Mesh(new THREE.ConeGeometry(.8, 2.6, 8), new THREE.MeshBasicMaterial({ transparent: true, opacity: .85 }));
  private photons = Array.from({ length: 2 }, () => new THREE.Mesh(new THREE.SphereGeometry(.55, 8, 6), new THREE.MeshBasicMaterial({ transparent: true })));
  private cylinders: THREE.Mesh[] = [];
  private points: RoutePoint[] = [];
  private length = 0;
  private signature = "";
  constructor(private route: ViewRoute) {
    super(); this.add(this.line, this.arrow, ...this.photons);
    const raycast = this.line.raycast.bind(this.line);
    this.line.raycast = (ray, hits) => { if (this.line.visible) raycast(ray, hits); };
  }
  update(source: RoutePoint, target: RoutePoint, scale: number, paint: Paint, now: number): void {
    this.visible = paint.visible;
    if (!paint.visible) return;
    const signature = `${source.x},${source.y},${source.z},${target.x},${target.y},${target.z},${scale}`;
    if (signature !== this.signature) {
      this.signature = signature;
      this.points = movedRoute(this.route.points,
        { x: source.x / scale, y: source.y / scale, z: source.z / scale },
        { x: target.x / scale, y: target.y / scale, z: target.z / scale })
        .map(p => ({ x: p.x * scale, y: p.y * scale, z: p.z * scale }));
      this.length = routeLength(this.points);
      this.line.geometry.dispose();
      this.line.geometry = new THREE.BufferGeometry().setFromPoints(this.points.map(p => new THREE.Vector3(p.x, p.y, p.z)));
      this.line.computeLineDistances();   // 파선 재질은 꼭짓점마다 누적 거리가 있어야 끊는다
      for (const cylinder of this.cylinders) { this.remove(cylinder); cylinder.geometry.dispose(); (cylinder.material as THREE.Material).dispose(); }
      this.cylinders = [];
    }
    if (paint.width > 0 && !this.cylinders.length) for (let i = 1; i < this.points.length; i++) {
      const a = this.points[i - 1], b = this.points[i];
      const direction = new THREE.Vector3(b.x - a.x, b.y - a.y, b.z - a.z), length = direction.length();
      if (!length) continue;
      const cylinder = new THREE.Mesh(new THREE.CylinderGeometry(.5, .5, length, 6),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: .85 }));
      cylinder.position.set((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
      cylinder.quaternion.setFromUnitVectors(UP, direction.normalize());
      const raycast = cylinder.raycast.bind(cylinder);
      cylinder.raycast = (ray, hits) => { if (cylinder.visible) raycast(ray, hits); };
      this.cylinders.push(cylinder); this.add(cylinder);
    }
    const detail = paint.detail ?? 1;
    const alpha = (paint.color.length === 9 ? parseInt(paint.color.slice(7), 16) / 255 : 1) * detail;
    this.line.material.color.set(paint.color.slice(0, 7));
    this.line.material.gapSize = paint.dashed ? GAP : 0;
    const lineVisible = alpha > 0 && !paint.width;
    if (this.line.visible && !lineVisible) this.line.geometry.dispose();
    this.line.visible = lineVisible;
    this.line.material.opacity = .85 * alpha;
    for (const c of this.cylinders) {
      const visible = alpha > 0 && paint.width > 0;
      if (c.visible && !visible) c.geometry.dispose();
      c.visible = visible;
      c.scale.set(paint.width, 1, paint.width);
      const material = c.material as THREE.MeshBasicMaterial;
      material.color.set(paint.color.slice(0, 7)); material.opacity = .85 * alpha;
    }
    this.arrow.visible = paint.arrow && alpha > 0 && this.length > paint.targetRadius + 2.6;
    if (this.arrow.visible) {
      const distance = this.length - paint.targetRadius - 1;
      const tip = pointOnRoute(this.points, distance), before = pointOnRoute(this.points, distance - .01);
      const direction = new THREE.Vector3(tip.x - before.x, tip.y - before.y, tip.z - before.z).normalize();
      this.arrow.quaternion.setFromUnitVectors(UP, direction);
      this.arrow.position.set(tip.x - direction.x * 1.3, tip.y - direction.y * 1.3, tip.z - direction.z * 1.3);
      this.arrow.material.color.set(paint.color.slice(0, 7));
      this.arrow.material.opacity = .85 * alpha;
    }
    this.photons.forEach((p, i) => {
      p.visible = paint.particles && detail > 0 && this.length > 0;
      if (!p.visible) return;
      const at = pointOnRoute(this.points, ((now * .00036 + i / 2) % 1) * this.length);
      p.position.set(at.x, at.y, at.z); p.material.color.set(paint.particleColor);
      p.material.opacity = detail;
    });
  }
}
