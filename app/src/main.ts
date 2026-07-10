import "./app.css";
import { COPY } from "./copy";
import { renderIntake } from "./surfaces/intake";
import { renderRecords } from "./surfaces/records";
import { renderAtlas } from "./surfaces/atlas";
import { renderSearch } from "./surfaces/search";
import { renderPacks } from "./surfaces/packs";
import { renderSettings } from "./surfaces/settings";

type Surface = "intake" | "records" | "atlas" | "search" | "packs";
const SURFACES: Record<Surface, (el: HTMLElement) => void | Promise<void>> = {
  intake: renderIntake,
  records: renderRecords,
  atlas: renderAtlas,
  search: renderSearch,
  packs: renderPacks,
};

const surfaceEl = document.getElementById("surface")!;
const nav = document.getElementById("nav")!;

function current(): Surface {
  const h = location.hash.replace(/^#/, "").split("/")[0];
  return (h in SURFACES ? h : "intake") as Surface;
}

async function route() {
  const s = current();
  nav.querySelectorAll("a").forEach((a) =>
    a.classList.toggle("active", a.dataset.surface === s));
  surfaceEl.innerHTML = "";
  await SURFACES[s](surfaceEl);
}

window.addEventListener("hashchange", route);
document.title = `Vinc — ${COPY.tagline}`;

const popover = document.getElementById("settings-popover")!;
document.getElementById("settings-btn")!.addEventListener("click", () => {
  popover.hidden = !popover.hidden;
  if (!popover.hidden) renderSettings(popover);
});

route();
