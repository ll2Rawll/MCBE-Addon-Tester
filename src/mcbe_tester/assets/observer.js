// Development-only observer. Never include this pack in a shipped add-on.
import { world, system } from "@minecraft/server";
const runId = __RUN_ID__;
const emit = (kind, data = {}) => console.warn("BEDROCKLAB " + JSON.stringify({
  runId, kind, tick: system.currentTick, ...data
}));
const attempt = (fn) => { try { return fn(); } catch { return null; } };
const entity = (e) => ({ id: e.id, typeId: e.typeId, location: attempt(() => e.location),
  health: attempt(() => e.getComponent("minecraft:health").currentValue) });
const events = ["itemUse", "playerSpawn", "playerBreakBlock", "playerPlaceBlock", "entityHurt", "entityDie"];
system.run(() => {
  emit("ready");
  for (const name of events) {
    try {
      world.afterEvents[name].subscribe(e => emit("event", { event: name,
        player: e.player?.name ?? e.source?.name, item: e.itemStack?.typeId,
        entity: e.hurtEntity ? entity(e.hurtEntity) : undefined, damage: e.damage }));
    } catch (e) { emit("unsupported_event", { event: name, message: String(e) }); }
  }
});
system.runInterval(() => {
  try {
    const players = world.getAllPlayers().map(p => {
      const inventory = attempt(() => p.getComponent("minecraft:inventory").container);
      const items = [];
      if (inventory) for (let slot = 0; slot < inventory.size; slot++) {
        const item = inventory.getItem(slot);
        if (item) items.push({ slot, typeId: item.typeId, amount: item.amount });
      }
      return { ...entity(p), name: p.name, dimension: p.dimension.id,
        rotation: attempt(() => p.getRotation()), selectedSlot: p.selectedSlotIndex, items,
        nearby: attempt(() => p.dimension.getEntities({ location: p.location, maxDistance: 32 })
          .slice(0, 64).map(entity)) };
    });
    emit("snapshot", { players });
  } catch (e) { emit("observer_error", { message: String(e) }); }
}, 20);
system.afterEvents.scriptEventReceive.subscribe(e => {
  if (e.id !== "bedrocklab:probe") return;
  try {
    const query = JSON.parse(e.message);
    const block = world.getDimension(query.dimension ?? "overworld").getBlock(query.location);
    emit("block", { nonce: query.nonce, location: query.location, typeId: block?.typeId,
      states: block?.permutation.getAllStates() });
  } catch (e) { emit("observer_error", { message: String(e) }); }
});
