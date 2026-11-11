import { system, world } from '@minecraft/server';

system.beforeEvents.startup.subscribe((event) => {
  event.blockComponentRegistry.registerCustomComponent('workshop:toggle', {
    onPlayerInteract(event) {
      const permutation = event.block.permutation;
      const lit = permutation.getState('workshop:lit');
      event.block.setPermutation(permutation.withState('workshop:lit', !lit));
    }
  });
});

system.runInterval(() => {
  const count = world.getDynamicProperty('workshop:heartbeat') || 0;
  world.setDynamicProperty('workshop:heartbeat', count + 1);
}, 5);
