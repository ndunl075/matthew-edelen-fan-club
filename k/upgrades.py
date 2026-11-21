from __future__ import annotations
from .params import Params
WEAPONS = ('gun', 'mortar', 'laser', 'lightning')

def classify(upgrade: dict) -> str:
    cats = []
    for eff in upgrade.get('effects', []):
        path = eff.get('path', '')
        parts = path.split('.')
        if parts[0] == 'weapons' and len(parts) > 1:
            cats.append(parts[1])
        elif path == 'mech.hp':
            cats.append('max_hp')
        elif path == 'mech.armor':
            cats.append('armor')
        elif path in ('mech.regen_per_s', 'mech.regen_delay'):
            cats.append('regen')
        elif path in ('mech.near_range_bonus', 'mech.vision_bonus', 'mech.hearing'):
            cats.append('sensors')
        elif path in ('mech.walk_speed', 'mech.boost_speed'):
            cats.append('speed')
        elif path in ('mech.energy', 'mech.energy_regen', 'mech.boost_drain', 'mech.jet_drain'):
            cats.append('energy')
        elif path in ('mech.turn_rate', 'mech.pitch_rate'):
            cats.append('turn')
        else:
            cats.append('rest')
    return cats[0] if cats else 'rest'

class UpgradePicker:

    def __init__(self, upgrade_defs: list, params: Params):
        self.p = params
        self.cat = {u['id']: classify(u) for u in upgrade_defs}

    @property
    def order(self) -> list:
        return list(self.p.upgrades.category_order)

    def rank(self, upgrade_id: str, held: set) -> int:
        cat = self.cat.get(upgrade_id, 'rest')
        if cat in WEAPONS and cat not in held:
            cat = 'rest'
        order = self.order
        return order.index(cat) if cat in order else len(order)

    def choose(self, offer: dict, held: set) -> int:
        choices = offer.get('choices', [])
        if not choices:
            return 0
        ranks = [self.rank(c['id'], held) for c in choices]
        return min(range(len(choices)), key=lambda i: (ranks[i], i))
