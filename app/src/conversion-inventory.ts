import type { ConversionCheck } from "./api";

function failure(code: string): Error {
  return Object.assign(new Error(code), { code });
}

/** 진단은 읽기만 기다리며 이동이나 팀 예약을 호출하지 않는다. */
export async function waitForInventory(topic: string, begin: () => Promise<ConversionCheck>,
                                      status: (ticket: string) => Promise<ConversionCheck>,
                                      sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms)),
                                      now = Date.now, timeout = 240_000): Promise<ConversionCheck> {
  const deadline = now() + timeout;
  let check = await begin();
  while (check.inventory_state === "pending") {
    if (!check.inventory_ticket || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(check.inventory_ticket))
      throw failure("conversion_inventory_invalid");
    if (now() >= deadline) throw failure("conversion_inventory_pending");
    await sleep(1000);
    check = await status(check.inventory_ticket);
  }
  if (check.topic && check.topic !== topic) throw failure("conversion_inventory_invalid");
  if (!Array.isArray(check.blockers) || typeof check.ready !== "boolean")
    throw failure("conversion_inventory_invalid");
  return check;
}
