import { world, system, ItemStack, BlockPermutation } from '@minecraft/server';
import { ModalFormData } from '@minecraft/server-ui';

// An original, small integration fixture. Expectations live in emulator-scenario.json.
class WorkshopSettings {
  async open(player) {
    const response = await new ModalFormData()
      .title('Workshop controls')
      .toggle('Light the lamp', { defaultValue: false })
      .slider('Score', 0, 10, { valueStep: 2, defaultValue: 2 })
      .dropdown('Label', ['Blue', 'Warm'])
      .textField('Robot name', 'Enter a name', { defaultValue: 'Workshop robot' })
      .submitButton('Apply settings')
      .show(player);
    if (response.canceled) return;
    const [lit, score, label, name] = response.formValues;
    const dimension = player.dimension;
    dimension.getBlock({ x: 0, y: 0, z: 0 }).setPermutation(
      BlockPermutation.resolve('workshop:lamp', { 'workshop:lit': lit })
    );
    const robot = dimension.spawnEntity('workshop:robot', { x: 2, y: 0, z: 0 });
    robot.nameTag = name;
    robot.addTag('configured');
    const objective = world.scoreboard.getObjective('workshop') || world.scoreboard.addObjective('workshop');
    objective.setScore(robot, score);
    const stack = new ItemStack('workshop:shard', 1);
    stack.nameTag = label === 0 ? 'Blue shard' : 'Warm shard';
    player.getComponent('inventory').container.setItem(1, stack);
    world.setDynamicProperty('workshop:configured', true);
    player.onScreenDisplay.setActionBar('Settings applied');
  }
}

world.afterEvents.itemUse.subscribe(event => {
  if (event.itemStack?.typeId === 'workshop:shard') {
    system.run(() => new WorkshopSettings().open(event.source));
  }
});
