"""Slot-based containers, item metadata, stack identity and atomic transfers."""

import json
from copy import deepcopy

from .diagnostics import Unsupported


class Inventory:
    def __init__(self, world):
        self.world = world
        self.slots = {"player": [None] * 36}

    def item_info(self, identifier):
        catalog = self.world.catalog
        if identifier not in catalog.items and identifier not in catalog.blocks:
            raise Unsupported(f"Item definition not supplied: {identifier}")
        components = catalog.items.get(identifier, {}).get("components", {})
        size = components.get("minecraft:max_stack_size", 64)
        if isinstance(size, dict):
            size = size.get("value", 64)
        if type(size) is not int or not 1 <= size <= 64:
            raise ValueError("Stack size must be 1–64")
        return {
            "maxAmount": size,
            "tags": components.get("minecraft:tags", {}).get("tags", []),
            "durability": components.get("minecraft:durability", {}).get("max_durability"),
            "food": components.get("minecraft:food"),
        }

    def validate(self, stack, allow_bulk=False):
        if stack is None:
            return None
        stack = deepcopy(stack)
        info = self.item_info(stack["typeId"])
        amount = stack.get("amount", 1)
        if type(amount) is not int or not 1 <= amount <= (2304 if allow_bulk else info["maxAmount"]):
            raise ValueError("Invalid item amount")
        if len(json.dumps(stack, allow_nan=False)) > 32768:
            raise ValueError("Item metadata limit")
        damage = stack.get("damage", 0)
        if type(damage) is not int or damage < 0 or damage > (info["durability"] or 0):
            raise ValueError("Invalid durability damage")
        stack.update(
            amount=amount, lore=stack.get("lore", []), dynamic=stack.get("dynamic", {}), damage=damage
        )
        return stack

    def container(self, owner):
        if owner != "player":
            self.world.entity_data(owner)
        if owner not in self.slots:
            entity = self.world.entity_data(owner)
            component = entity.get("components", {}).get("minecraft:inventory")
            if component is None:
                raise ValueError("Entity has no inventory component")
            size = component.get("inventory_size", 5)
            if type(size) is not int or not 1 <= size <= 54:
                raise Unsupported("Container size must be 1–54")
            self.slots[owner] = [None] * size
        return self.slots[owner]

    def index(self, owner, slot):
        slots = self.container(owner)
        if type(slot) is not int or not 0 <= slot < len(slots):
            raise ValueError("Inventory slot outside container")
        return slots

    @staticmethod
    def identity(stack):
        return {k: v for k, v in stack.items() if k != "amount"}

    def add(self, owner, stack):
        stack = self.validate(stack, True)
        slots = self.container(owner)
        limit = self.item_info(stack["typeId"])["maxAmount"]
        for existing in slots:
            if existing and self.identity(existing) == self.identity(stack):
                count = min(stack["amount"], limit - existing["amount"])
                existing["amount"] += count
                stack["amount"] -= count
        for i, existing in enumerate(slots):
            if stack["amount"] and existing is None:
                count = min(limit, stack["amount"])
                slots[i] = {**deepcopy(stack), "amount": count}
                stack["amount"] -= count
        self.sync()
        return stack if stack["amount"] else None

    def consume(self, owner, identifier, amount):
        slots = self.container(owner)
        if type(amount) is not int or amount < 0:
            raise ValueError("Consumption must be a nonnegative integer")
        if sum(s["amount"] for s in slots if s and s["typeId"] == identifier) < amount:
            raise ValueError("Inventory lacks required items")
        for i, stack in enumerate(slots):
            if stack and stack["typeId"] == identifier:
                take = min(stack["amount"], amount)
                stack["amount"] -= take
                amount -= take
                if not stack["amount"]:
                    slots[i] = None
        self.sync()

    def sync(self):
        player = self.world.player
        # Retain zero counts for inspectable consumption assertions.
        counts = dict.fromkeys(player.get("inventory", {}), 0)
        for item in self.slots["player"]:
            if item:
                counts[item["typeId"]] = counts.get(item["typeId"], 0) + item["amount"]
        player["inventory"] = counts
        player["slots"] = deepcopy(self.slots["player"])
        player["hotbar"] = [s["typeId"] if s else None for s in self.slots["player"][:9]]

    def api(self, op, owner, *args):
        slots = self.container(owner)
        if op == "size":
            return len(slots)
        if op == "empty":
            return slots.count(None)
        if op == "get":
            self.index(owner, args[0])
            return deepcopy(slots[args[0]])
        if op == "set":
            self.index(owner, args[0])
            slots[args[0]] = self.validate(args[1])
        elif op == "add":
            return self.add(owner, args[0])
        elif op == "clear":
            slots[:] = [None] * len(slots)
        elif op == "swap":
            slot, other_owner, other_slot = args
            self.index(owner, slot)
            other = self.index(other_owner, other_slot)
            slots[slot], other[other_slot] = other[other_slot], slots[slot]
        elif op == "transfer":
            slot, other_owner = args
            self.index(owner, slot)
            if owner == other_owner:
                raise ValueError("Transfer requires another container")
            self.container(other_owner)
            if slots[slot] is None:
                return None
            remainder = self.add(other_owner, slots[slot])
            slots[slot] = remainder
            self.sync()
            return deepcopy(remainder)
        else:
            raise Unsupported(f"Container operation: {op}")
        self.sync()
        return None
