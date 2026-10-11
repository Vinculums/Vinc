import * as THREE from "three";
import type { RoutePoint } from "../view-route";

/** ForceGraph의 곡선·해상도·재질을 유지하고 버퍼만 재사용한다.
 * 기본 튜브는 끝점이 고정되어도 layout tick마다 지오메트리를 재생성한다. */
export class CurvedLinkObject extends THREE.Group {
  private line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial());
  private tube?: THREE.Mesh<THREE.BufferGeometry, THREE.MeshLambertMaterial>;
  private signature = "";
  private centers?: Float64Array;
  private paintedWidth = -1;
  constructor() {
    super(); this.add(this.line);
    const raycast = this.line.raycast.bind(this.line);
    this.line.raycast = (ray, hits) => { if (this.line.visible) raycast(ray, hits); };
  }
  update(start: RoutePoint, end: RoutePoint, curve: THREE.Curve<THREE.Vector3> | null,
    width: number, color: string): void {
    const signature = `${start.x},${start.y},${start.z},${end.x},${end.y},${end.z}`;
    if (signature !== this.signature) {
      this.signature = signature;
      this.line.geometry.setFromPoints(curve?.getPoints(30) ??
        [start, end].map(p => new THREE.Vector3(p.x, p.y, p.z)));
      this.line.geometry.computeBoundingSphere();
      if (this.tube) { this.tube.geometry.dispose(); this.tube.geometry = new THREE.BufferGeometry(); }
      this.centers = undefined; this.paintedWidth = -1;
    }
    width = Math.ceil(width * 10) / 10;
    if (width && !this.tube) {
      this.tube = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshLambertMaterial());
      const raycast = this.tube.raycast.bind(this.tube);
      this.tube.raycast = (ray, hits) => { if (this.tube!.visible) raycast(ray, hits); };
      this.add(this.tube);
    }
    if (width && this.tube && !this.centers) {
      const path = curve ?? new THREE.LineCurve3(new THREE.Vector3(start.x, start.y, start.z),
        new THREE.Vector3(end.x, end.y, end.z));
      const centers = this.centers = new Float64Array(31 * 7 * 3);
      // TubeGeometry가 이미 계산하는 중심을 보관한다. 곡선을 다시 평가하지 않는다.
      const geometryPath = Object.create(path) as THREE.Curve<THREE.Vector3>;
      geometryPath.computeFrenetFrames = path.computeFrenetFrames.bind(path);
      geometryPath.getPointAt = (u, target) => {
        const center = path.getPointAt(u, target), ring = Math.round(u * 30);
        for (let radial = 0; radial <= 6; radial++)
          centers.set([center.x, center.y, center.z], (ring * 7 + radial) * 3);
        return center;
      };
      const geometry = new THREE.TubeGeometry(geometryPath, 30, 1, 6, false);
      geometryPath.getPointAt = path.getPointAt.bind(path);
      this.tube.geometry.dispose(); this.tube.geometry = geometry;
      this.paintedWidth = -1;
    }
    if (width && this.tube && this.centers && width !== this.paintedWidth) {
      const position = this.tube.geometry.getAttribute("position"), normal = this.tube.geometry.getAttribute("normal");
      for (let i = 0; i < position.count * 3; i++) position.array[i] = this.centers[i] + normal.array[i] * width / 2;
      position.needsUpdate = true;
      this.tube.geometry.computeBoundingSphere();
      this.paintedWidth = width;
    }
    // CPU 버퍼는 굵기 왕복에 재사용하고, 숨은 형태의 GPU 버퍼는 해제한다.
    // Three는 같은 geometry가 다시 보이면 해당 버퍼를 재업로드한다.
    if (this.line.visible && width) this.line.geometry.dispose();
    if (this.tube?.visible && !width) this.tube.geometry.dispose();
    this.line.visible = !width;
    if (this.tube) this.tube.visible = !!width;
    const alpha = color.length === 9 ? parseInt(color.slice(7), 16) / 255 : 1;
    for (const material of [this.line.material, this.tube?.material]) if (material) {
      material.color.set(color.slice(0, 7));
      material.opacity = .85 * alpha;
      material.transparent = material.opacity < 1;
      material.depthWrite = material.opacity >= 1;
    }
  }
}
